from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import Conflict, NotFound
from app.models import Artifact, ExperimentRun, FaultInstance, ProbeCall, ProbeResult, RunEvent
from app.services.scorecard_service import not_scored


def build_report(db: Session, run_id: str) -> dict:
    """Assembled strictly from persisted evidence; nothing is synthesized."""
    run = db.get(ExperimentRun, run_id)
    if run is None:
        raise NotFound(f"run {run_id} not found")
    probes = list(db.scalars(select(ProbeResult).where(ProbeResult.run_id == run_id).order_by(ProbeResult.id)))
    calls_count = db.scalar(select(ProbeCall.id).where(ProbeCall.run_id == run_id).limit(1))
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
                "requires_attention": run.requires_attention,
                "recovery_duration_seconds": run.recovery_duration_seconds,
                "scorecard": run.scorecard or not_scored(
                    "Score unavailable: this historical run has no recovery-duration evidence")},
        "definition_snapshot": run.definition_snapshot,
        "baseline": phase("baseline"),
        "during_fault": phase("during"),
        "recovery": phase("recovery"),
        "faults": [{"id": f.id, "engine": f.engine, "native_id": f.native_id, "target_id": f.target_id, "type": f.fault_type,
                    "parameters": f.parameters, "state": f.state, "cleanup_error": f.cleanup_error,
                    "expires_at": f.expires_at} for f in faults],
        "events": [{"at": e.created_at, "type": e.event_type, "message": e.message, "details": e.details} for e in events],
        "artifacts": [{"id": a.id, "name": a.name, "size_bytes": a.size_bytes, "sha256": a.sha256} for a in arts],
        "probe_call_capture": {"source": "synthetic_http_probes", "available": calls_count is not None},
        "notes": "Outcome is stored separately from raw evidence; empty sections mean nothing was measured.",
    }


def list_probe_calls(db: Session, run_id: str, phase: str | None = None,
                     limit: int = 100, after_id: int = 0) -> list[dict]:
    run = db.get(ExperimentRun, run_id)
    if run is None:
        raise NotFound(f"run {run_id} not found")
    query = select(ProbeCall).where(ProbeCall.run_id == run_id, ProbeCall.id > after_id)
    if phase:
        query = query.where(ProbeCall.phase == phase)
    rows = db.scalars(query.order_by(ProbeCall.id).limit(limit))
    return [{"id": row.id, "probe_name": row.probe_name, "phase": row.phase,
             "captured_at": row.captured_at, "duration_ms": row.duration_ms, "ok": row.ok,
             "request": row.request, "response": row.response} for row in rows]


def build_run_comparison(db: Session, baseline_id: str, candidate_id: str) -> dict:
    baseline, candidate = db.get(ExperimentRun, baseline_id), db.get(ExperimentRun, candidate_id)
    if baseline is None:
        raise NotFound(f"baseline run {baseline_id} not found")
    if candidate is None:
        raise NotFound(f"candidate run {candidate_id} not found")
    if baseline.id == candidate.id:
        raise Conflict("Choose two different runs to compare")

    runs = [baseline, candidate]
    all_calls = list(db.scalars(select(ProbeCall).where(
        ProbeCall.run_id.in_([baseline.id, candidate.id])).order_by(ProbeCall.captured_at)))
    summaries: dict[str, dict[tuple[str, str], list[ProbeCall]]] = {baseline.id: {}, candidate.id: {}}
    for call in all_calls:
        summaries[call.run_id].setdefault((call.probe_name, call.phase), []).append(call)
    keys = sorted(set(summaries[baseline.id]) | set(summaries[candidate.id]))

    def metrics(calls: list[ProbeCall]) -> dict:
        latencies = sorted(c.duration_ms for c in calls if c.ok)
        def percentile(p: float) -> float | None:
            return round(latencies[max(0, int((len(latencies) * p + 0.999999)) - 1)], 2) if latencies else None
        n = len(calls)
        return {"request_count": n, "errors": sum(not c.ok for c in calls),
                "error_rate": round(sum(not c.ok for c in calls) / n, 4) if n else None,
                "status_codes": {str(code): sum(1 for c in calls if c.response.get("status_code") == code)
                                 for code in sorted({c.response.get("status_code") for c in calls
                                                     if c.response.get("status_code") is not None})},
                "p50_ms": percentile(.50), "p95_ms": percentile(.95), "p99_ms": percentile(.99)}

    comparisons = []
    for probe, phase in keys:
        left, right = summaries[baseline.id].get((probe, phase), []), summaries[candidate.id].get((probe, phase), [])
        a, b = metrics(left), metrics(right)
        comparisons.append({"probe": probe, "phase": phase, "baseline": a, "candidate": b,
                            "delta": {key: round(b[key] - a[key], 4) if isinstance(a[key], (int, float))
                                      and isinstance(b[key], (int, float)) else None
                                      for key in ("request_count", "errors", "error_rate", "p50_ms", "p95_ms", "p99_ms")}})

    compatible = (baseline.experiment_id == candidate.experiment_id
                  and baseline.experiment_version == candidate.experiment_version
                  and baseline.definition_snapshot == candidate.definition_snapshot)
    return {
        "compatible": compatible,
        "compatibility_note": "Same experiment version and definition" if compatible else
            "Runs use different experiment versions or definitions; compare cautiously",
        "capture_scope": "HTTP requests generated by configured synthetic probes; application traffic is not captured",
        "runs": [{"id": run.id, "experiment_id": run.experiment_id,
                  "experiment_version": run.experiment_version, "created_at": run.created_at,
                  "status": run.status, "outcome": run.outcome, "cleanup_status": run.cleanup_status,
                  "recovery_duration_seconds": run.recovery_duration_seconds,
                  "scorecard": run.scorecard,
                  "target": run.definition_snapshot.get("spec", {}).get("target", {}),
                  "fault": run.definition_snapshot.get("spec", {}).get("fault", {})} for run in runs],
        "probe_metrics": comparisons,
    }


def build_recovery_scorecard_report(db: Session, experiment_id: str, limit: int = 200) -> dict:
    """Return per-run scorecards, supporting evidence, and a trend for one experiment."""
    from app.models import Experiment

    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise NotFound(f"experiment {experiment_id} not found")
    runs = list(db.scalars(
        select(ExperimentRun)
        .where(ExperimentRun.experiment_id == experiment_id)
        .order_by(ExperimentRun.created_at.desc())
        .limit(limit)
    ))
    runs.reverse()
    run_ids = [run.id for run in runs]
    probes = list(db.scalars(select(ProbeResult).where(ProbeResult.run_id.in_(run_ids)).order_by(ProbeResult.id))) if run_ids else []
    faults = list(db.scalars(select(FaultInstance).where(FaultInstance.run_id.in_(run_ids)))) if run_ids else []
    events = list(db.scalars(select(RunEvent).where(RunEvent.run_id.in_(run_ids)).order_by(RunEvent.id))) if run_ids else []
    artifacts = list(db.scalars(select(Artifact).where(Artifact.run_id.in_(run_ids)))) if run_ids else []

    by_run_probes: dict[str, list[ProbeResult]] = {}
    by_run_faults: dict[str, list[FaultInstance]] = {}
    by_run_events: dict[str, list[RunEvent]] = {}
    by_run_artifacts: dict[str, list[Artifact]] = {}
    for row in probes:
        by_run_probes.setdefault(row.run_id, []).append(row)
    for row in faults:
        by_run_faults.setdefault(row.run_id, []).append(row)
    for row in events:
        by_run_events.setdefault(row.run_id, []).append(row)
    for row in artifacts:
        by_run_artifacts.setdefault(row.run_id, []).append(row)

    items = []
    for run in runs:
        items.append({
            "run_id": run.id,
            "created_at": run.created_at,
            "experiment_version": run.experiment_version,
            "target": run.definition_snapshot.get("spec", {}).get("target", {}),
            "engine": run.definition_snapshot.get("spec", {}).get("fault", {}).get("engine"),
            "fault": run.definition_snapshot.get("spec", {}).get("fault", {}),
            "hypothesis": run.definition_snapshot.get("spec", {}).get("hypothesis", {}),
            "execution_status": run.status,
            "verdict": run.outcome,
            "cleanup_status": run.cleanup_status,
            "recovery_duration_seconds": run.recovery_duration_seconds,
            "scorecard": run.scorecard or not_scored(
                "Score unavailable: this historical run has no recovery-duration evidence"),
            "evidence": {
                "probes": [{
                    "name": p.probe_name, "phase": p.phase, "measurement": p.measurement,
                    "tolerance": p.tolerance, "status": p.status, "detail": p.detail,
                    "evidence_ref": p.evidence_ref,
                } for p in by_run_probes.get(run.id, [])],
                "faults": [{"engine": f.engine, "type": f.fault_type, "state": f.state,
                            "cleanup_error": f.cleanup_error} for f in by_run_faults.get(run.id, [])],
                "events": [{"at": e.created_at, "type": e.event_type, "message": e.message}
                           for e in by_run_events.get(run.id, [])],
                "artifacts": [{"id": a.id, "name": a.name, "size_bytes": a.size_bytes, "sha256": a.sha256}
                              for a in by_run_artifacts.get(run.id, [])],
            },
        })

    scored = [item["scorecard"]["score"] for item in items
              if item["scorecard"].get("status") == "SCORED" and item["scorecard"].get("score") is not None]
    trend = {
        "run_count": len(items),
        "scored_run_count": len(scored),
        "average_score": round(sum(scored) / len(scored), 2) if scored else None,
        "best_score": max(scored) if scored else None,
        "lowest_score": min(scored) if scored else None,
        "series": [{"run_id": item["run_id"], "created_at": item["created_at"],
                    "score": item["scorecard"].get("score"),
                    "status": item["scorecard"].get("status")} for item in items],
    }
    return {"experiment": {"id": experiment.id, "name": experiment.name}, "trend": trend, "runs": items}
