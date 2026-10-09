from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import Conflict, PlatformError
from app.core.logging import get_logger
from app.core.security import redact, redact_text
from app.models import Artifact, AuditLog, ExperimentRun, ProbeResult, RunEvent, TargetLease
from app.models.base import utcnow
from app.schemas.run import TERMINAL, RunStatus, can_transition

log = get_logger(__name__)


def add_event(db: Session, run_id: str, event_type: str, message: str, details: dict | None = None) -> None:
    db.add(RunEvent(run_id=run_id, event_type=event_type, message=redact_text(message)[:2000],
                    details=redact(details or {})))


def audit(db: Session, actor: str, action: str, target: str, outcome: str, meta: dict | None = None) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, outcome=outcome, meta=redact(meta or {})))


def transition(db: Session, run: ExperimentRun, dst: RunStatus, message: str = "", details: dict | None = None) -> None:
    src = RunStatus(run.status)
    if src == dst:
        return
    if not can_transition(src, dst):
        raise PlatformError(f"illegal state transition {src} -> {dst}")
    run.status = dst.value
    if dst in TERMINAL:
        run.finished_at = utcnow()
        if dst != RunStatus.CLEANUP_FAILED:  # keep the target leased while faults may still be live
            db.execute(delete(TargetLease).where(TargetLease.run_id == run.id))
    add_event(db, run.id, "state_transition", message or f"{src.value} -> {dst.value}",
              {"from": src.value, "to": dst.value, **(details or {})})
    db.flush()


def acquire_leases(db: Session, run_id: str, keys: list[str], ttl_seconds: int) -> None:
    now = utcnow()
    db.execute(delete(TargetLease).where(TargetLease.expires_at < now))
    db.flush()
    for key in keys:
        holder = db.get(TargetLease, key)
        if holder is not None:
            raise Conflict(f"target '{key}' is leased by run {holder.run_id}")
        db.add(TargetLease(target_key=key, run_id=run_id, expires_at=now + timedelta(seconds=ttl_seconds)))
    try:
        db.flush()
    except Exception as exc:  # unique violation from a concurrent acquirer
        db.rollback()
        raise Conflict("target lease was acquired concurrently by another run") from exc


def add_probe_result(db: Session, run_id: str, name: str, phase: str, measurement: float | None,
                     tolerance: float | None, status: str, detail: dict | None = None,
                     evidence_ref: str | None = None) -> None:
    db.add(ProbeResult(run_id=run_id, probe_name=name, phase=phase, measurement=measurement, tolerance=tolerance,
                       status=status, detail=detail or {}, evidence_ref=evidence_ref))


def store_artifact(db: Session, settings: Settings, run_id: str, name: str, data: bytes) -> Artifact:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100]
    data = redact_text(data[: settings.max_output_bytes * 4].decode(errors="replace")).encode()
    folder = Path(settings.artifact_dir) / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe
    path.write_bytes(data)
    art = Artifact(run_id=run_id, name=safe, path=str(path), size_bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest())
    db.add(art)
    db.flush()
    return art


def events_for(db: Session, run_id: str) -> list[RunEvent]:
    return list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)))
