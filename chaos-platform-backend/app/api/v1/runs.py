from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.exceptions import Conflict, DependencyUnavailable, NotFound
from app.core.security import Principal, Role, require
from app.db.session import get_db
from app.models import Artifact, ExperimentRun, ProbeResult, RunEvent
from app.models.base import utcnow
from app.repositories import runs as repo
from app.schemas.run import (
    ACTIVE_STATES,
    TERMINAL,
    ArtifactOut,
    CleanupStatus,
    EventOut,
    Outcome,
    ProbeOut,
    RunOut,
    RunStatus,
)
from app.services.report_service import build_report
from app.workers.jobs import enqueue_retry_cleanup

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class RunSubmitResponse(BaseModel):
    run_id: str
    status: str
    dry_run: bool


class ActionResponse(BaseModel):
    run_id: str
    status: str
    message: str


def _run(db: Session, run_id: str) -> ExperimentRun:
    r = db.get(ExperimentRun, run_id)
    if r is None:
        raise NotFound(f"run {run_id} not found")
    return r


@router.get("", response_model=list[RunOut])
def list_runs(status: str | None = None, experiment_id: str | None = None, limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0), db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    q = select(ExperimentRun).order_by(ExperimentRun.created_at.desc())
    if status:
        q = q.where(ExperimentRun.status == status)
    if experiment_id:
        q = q.where(ExperimentRun.experiment_id == experiment_id)
    return list(db.scalars(q.limit(limit).offset(offset)))


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    return _run(db, run_id)


@router.post("/{run_id}/cancel", response_model=ActionResponse, status_code=202)
def cancel(run_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(Role.OPERATOR))):
    run = _run(db, run_id)
    if RunStatus(run.status) in TERMINAL:
        raise Conflict(f"run already {run.status}")
    # Not yet claimed: abort atomically (a worker claim is a compare-and-swap on the same column).
    res = db.execute(update(ExperimentRun).where(
        ExperimentRun.id == run_id, ExperimentRun.status == RunStatus.QUEUED.value).values(
        status=RunStatus.ABORTED.value, outcome=Outcome.INCONCLUSIVE.value, cancel_requested=True,
        cleanup_status=CleanupStatus.NOT_REQUIRED.value, finished_at=utcnow()))
    if res.rowcount == 1:
        from sqlalchemy import delete

        from app.models import TargetLease

        db.execute(delete(TargetLease).where(TargetLease.run_id == run_id))
        repo.add_event(db, run_id, "cancelled", f"cancelled while queued by {p.user}")
        repo.audit(db, p.user, "run.cancel", run_id, "success", {"phase": "queued"})
        db.commit()
        return ActionResponse(run_id=run_id, status="ABORTED", message="cancelled before execution; nothing was injected")
    db.refresh(run)
    if RunStatus(run.status) not in ACTIVE_STATES:
        raise Conflict(f"run is {run.status}; cannot cancel")
    run.cancel_requested = True
    repo.add_event(db, run_id, "cancel_requested", f"cancel requested by {p.user}")
    repo.audit(db, p.user, "run.cancel", run_id, "success", {"phase": run.status})
    db.commit()
    return ActionResponse(run_id=run_id, status=run.status,
                          message="cancellation requested; the worker will stop and clean up. "
                                  "Cleanup is NOT confirmed until cleanup_status is SUCCEEDED.")


@router.post("/{run_id}/retry-cleanup", response_model=ActionResponse, status_code=202)
def retry_cleanup(run_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(Role.OPERATOR))):
    run = _run(db, run_id)
    if run.status != RunStatus.CLEANUP_FAILED.value:
        raise Conflict("retry-cleanup is only valid for runs in CLEANUP_FAILED")
    repo.audit(db, p.user, "run.retry_cleanup", run_id, "success")
    db.commit()
    try:
        enqueue_retry_cleanup(run_id)
    except Exception as exc:  # noqa: BLE001
        raise DependencyUnavailable("job queue unavailable; retry later") from exc
    return ActionResponse(run_id=run_id, status=run.status, message="cleanup retry enqueued; poll the run for the outcome")


@router.get("/{run_id}/events", response_model=list[EventOut])
def events(run_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    _run(db, run_id)
    return list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)))


@router.get("/{run_id}/probes", response_model=list[ProbeOut])
def probes(run_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    _run(db, run_id)
    return list(db.scalars(select(ProbeResult).where(ProbeResult.run_id == run_id).order_by(ProbeResult.id)))


@router.get("/{run_id}/artifacts", response_model=list[ArtifactOut])
def artifacts(run_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    _run(db, run_id)
    return list(db.scalars(select(Artifact).where(Artifact.run_id == run_id)))


@router.get("/{run_id}/report")
def report(run_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(Role.VIEWER))):
    return build_report(db, run_id)
