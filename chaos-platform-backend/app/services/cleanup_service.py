"""Durable fault cleanup shared by the orchestrator, the reconciler and manual retry."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.engines.base import FaultHandle, FaultSpec, ResolvedTarget
from app.engines.registry import EngineRegistry
from app.models import ExperimentRun, FaultInstance
from app.models.base import utcnow
from app.repositories import runs as repo
from app.schemas.experiment import ExperimentDocument
from app.schemas.run import ACTIVE_STATES, CleanupStatus, FaultState, Outcome, RunStatus
from app.services import metrics
from app.services.safety_service import resolve_target

log = get_logger(__name__)
OPEN_FAULT_STATES = (FaultState.PENDING.value, FaultState.ACTIVE.value, FaultState.REMOVAL_FAILED.value)


def spec_from_row(settings: Settings, run: ExperimentRun, f: FaultInstance) -> tuple[FaultSpec, FaultHandle | None, str]:
    doc = ExperimentDocument.model_validate(run.definition_snapshot)
    target: ResolvedTarget = resolve_target(settings, doc)
    spec = FaultSpec(run_id=run.id, fault_id=f.id, fault_type=f.fault_type, parameters=f.parameters,
                     duration_seconds=int(f.native_details.get("duration_seconds", doc.spec.fault.duration_seconds)),
                     target=target)
    handle = FaultHandle(native_id=f.native_id, details=f.native_details.get("handle", {})) if f.native_id else None
    return spec, handle, f.engine


async def cleanup_fault(settings: Settings, registry: EngineRegistry, fault_id: str) -> FaultState:
    """Remove then independently verify one fault. Idempotent; safe to call repeatedly."""
    with session_scope() as db:
        f = db.get(FaultInstance, fault_id)
        run = db.get(ExperimentRun, f.run_id)
        spec, handle, engine_name = spec_from_row(settings, run, f)
        f.cleanup_attempts += 1
        f.last_cleanup_attempt = utcnow()
    adapter = registry.get(engine_name)
    error: str | None = None
    state = FaultState.REMOVAL_FAILED
    try:
        for attempt in range(3):  # remove_fault is idempotent, so retrying is safe
            try:
                await asyncio.wait_for(adapter.remove_fault(spec, handle), settings.cleanup_attempt_timeout_seconds)
                error = None
                break
            except Exception as exc:  # noqa: BLE001
                error = f"remove attempt {attempt + 1}: {exc!r}"[:500]
                await asyncio.sleep(1 + attempt)
        if error is None:
            rec = await asyncio.wait_for(adapter.verify_recovery(spec, handle), settings.cleanup_attempt_timeout_seconds)
            if rec.recovered:
                state = FaultState.REMOVED if rec.verifiable else FaultState.UNVERIFIED
                error = None if rec.verifiable else rec.detail
            else:
                error = f"verification failed: {rec.detail}"
    except Exception as exc:  # noqa: BLE001
        error = f"verification error: {exc!r}"[:500]
    with session_scope() as db:
        f = db.get(FaultInstance, fault_id)
        f.state = state.value
        f.cleanup_error = error
        f.requires_attention = state == FaultState.REMOVAL_FAILED
        repo.add_event(db, f.run_id, "fault_cleanup", f"fault {fault_id[:8]} -> {state.value}",
                       {"engine": engine_name, "error": error, "native_id": f.native_id})
    if state == FaultState.REMOVAL_FAILED:
        metrics.CLEANUP_FAILURES.labels(engine=engine_name).inc()
        log.error("ALERT: fault cleanup failed; manual remediation required",
                  extra={"event": "cleanup_failed", "error_category": "cleanup"})
    return state


def final_status(run: ExperimentRun, cleanup_ok: bool, worker_lost: bool = False) -> RunStatus:
    if not cleanup_ok:
        return RunStatus.CLEANUP_FAILED
    if run.cancel_requested:
        return RunStatus.ABORTED
    if worker_lost or run.outcome == Outcome.ERROR.value:
        return RunStatus.FAILED
    return RunStatus.SUCCEEDED


async def finalize_run_cleanup(settings: Settings, registry: EngineRegistry, run_id: str, reason: str) -> RunStatus:
    """Used for orphaned runs (worker lost) and manual retry-cleanup."""
    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        st = RunStatus(run.status)
        if st in (RunStatus.RUNNING_BASELINE, RunStatus.INJECTING, RunStatus.OBSERVING):
            repo.transition(db, run, RunStatus.ABORTING, f"{reason}: aborting")
        if RunStatus(run.status) in (RunStatus.ABORTING, RunStatus.CLEANUP_FAILED):
            repo.transition(db, run, RunStatus.CLEANING_UP, f"{reason}: cleaning up")
        run.cleanup_status = CleanupStatus.NOT_STARTED.value
        run.heartbeat_at = utcnow()
        fault_ids = list(db.scalars(select(FaultInstance.id).where(
            FaultInstance.run_id == run_id, FaultInstance.state != FaultState.REMOVED.value)))
    results = [await cleanup_fault(settings, registry, fid) for fid in fault_ids]
    ok = all(r != FaultState.REMOVAL_FAILED for r in results)
    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        worker_lost = reason == "worker lost"
        if ok:
            repo.transition(db, run, RunStatus.VERIFYING_RECOVERY, "faults removed")
            run.cleanup_status = CleanupStatus.SUCCEEDED.value
            run.requires_attention = False
            if worker_lost:
                run.error_summary = (run.error_summary or "") + " Worker lost; faults reconciled."
                run.outcome = Outcome.ERROR.value
        else:
            run.cleanup_status = CleanupStatus.FAILED.value
            run.requires_attention = True
        dst = final_status(run, ok, worker_lost)
        repo.transition(db, run, dst, f"{reason}: finished as {dst.value}")
        return dst


async def reconcile_once(settings: Settings, registry: EngineRegistry) -> dict[str, int]:
    """Find expired/orphaned faults and stale runs; clean them up. Safe to run concurrently with workers."""
    now = utcnow()
    stats = {"stale_runs": 0, "expired_faults": 0}
    with session_scope() as db:
        stale_cut = now - timedelta(seconds=settings.heartbeat_stale_seconds)
        stale = [r.id for r in db.scalars(select(ExperimentRun).where(
            ExperimentRun.status.in_([s.value for s in ACTIVE_STATES])))
            if (r.heartbeat_at or r.started_at or r.created_at) < stale_cut]
        expired = [f.id for f in db.scalars(select(FaultInstance).where(
            FaultInstance.state.in_(OPEN_FAULT_STATES), FaultInstance.expires_at < now))]
        # faults of runs that are already terminal but still open (e.g. crash between steps)
        terminal_open = [f.id for f in db.scalars(select(FaultInstance).join(ExperimentRun).where(
            FaultInstance.state.in_([FaultState.PENDING.value, FaultState.ACTIVE.value]),
            ExperimentRun.status.in_(["SUCCEEDED", "FAILED", "ABORTED"])))]
    for rid in stale:
        log.warning("stale run detected", extra={"event": "stale_run", "error_category": "worker_lost"})
        await finalize_run_cleanup(settings, registry, rid, "worker lost")
        stats["stale_runs"] += 1
    handled = set()
    for fid in [*expired, *terminal_open]:
        if fid in handled:
            continue
        handled.add(fid)
        with session_scope() as db:
            f = db.get(FaultInstance, fid)
            if f.state not in OPEN_FAULT_STATES:
                continue
            # Past expiry the owning run has overrun its own deadline, so clean up even if it looks alive.
            repo.add_event(db, f.run_id, "fault_expired", f"fault {fid[:8]} expired; reconciling")
        await cleanup_fault(settings, registry, fid)
        stats["expired_faults"] += 1
    with session_scope() as db:
        open_n = db.scalar(select(func.count()).select_from(FaultInstance).where(
            FaultInstance.state.in_([FaultState.PENDING.value, FaultState.ACTIVE.value])))
        attn = db.scalar(select(func.count()).select_from(FaultInstance).where(
            FaultInstance.requires_attention.is_(True)))
        metrics.ACTIVE_FAULTS.set(open_n or 0)
        metrics.FAULTS_NEEDING_ATTENTION.set(attn or 0)
    return stats
