from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFound
from app.models import Artifact, ExperimentRun, FaultInstance, ProbeResult, RunEvent


def build_report(db: Session, run_id: str) -> dict:
    """Assembled strictly from persisted evidence; nothing is synthesized."""
    run = db.get(ExperimentRun, run_id)
    if run is None:
        raise NotFound(f"run {run_id} not found")
    probes = list(db.scalars(select(ProbeResult).where(ProbeResult.run_id == run_id).order_by(ProbeResult.id)))
    faults = list(db.scalars(select(FaultInstance).where(FaultInstance.run_id == run_id)))
    events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)))
    arts = list(db.scalars(select(Artifact).where(Artifact.run_id == run_id)))

    def phase(name: str) -> list[dict]:
        return [{"probe": p.probe_name, "measurement": p.measurement, "tolerance": p.tolerance, "status": p.status,
                 "detail": p.detail, "evidence": p.evidence_ref} for p in probes if p.phase == name]

    return {
        "run": {"id": run.id, "correlation_id": run.correlation_id, "status": run.status, "outcome": run.outcome,
                "cleanup_status": run.cleanup_status, "dry_run": run.dry_run, "requested_by": run.requested_by,
                "started_at": run.started_at, "finished_at": run.finished_at, "error_summary": run.error_summary,
                "requires_attention": run.requires_attention},
        "definition_snapshot": run.definition_snapshot,
        "baseline": phase("baseline"),
        "during_fault": phase("during"),
        "recovery": phase("recovery"),
        "faults": [{"id": f.id, "engine": f.engine, "native_id": f.native_id, "type": f.fault_type,
                    "parameters": f.parameters, "state": f.state, "cleanup_error": f.cleanup_error,
                    "expires_at": f.expires_at} for f in faults],
        "events": [{"at": e.created_at, "type": e.event_type, "message": e.message, "details": e.details} for e in events],
        "artifacts": [{"id": a.id, "name": a.name, "size_bytes": a.size_bytes, "sha256": a.sha256} for a in arts],
        "notes": "Outcome is stored separately from raw evidence; empty sections mean nothing was measured.",
    }
