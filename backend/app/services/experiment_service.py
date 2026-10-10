from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import NotFound, PlatformError, ValidationRejected
from app.engines.registry import EngineRegistry
from app.models import Experiment, ExperimentRun, ExperimentVersion
from app.models.base import utcnow
from app.repositories import runs as repo
from app.schemas.experiment import ExperimentDocument
from app.schemas.run import CleanupStatus, RunStatus
from app.services import metrics
from app.services.safety_service import check_definition
from app.services.scorecard_service import not_scored


def parse_document(raw: dict[str, Any] | str) -> ExperimentDocument:
    try:
        data = yaml.safe_load(raw) if isinstance(raw, str) else raw
        return ExperimentDocument.model_validate(data)
    except (ValidationError, yaml.YAMLError) as exc:
        errs = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()] if isinstance(exc, ValidationError) \
            else [{"msg": str(exc)}]
        raise ValidationRejected("experiment definition is invalid", {"errors": errs}) from exc


class ExperimentService:
    def __init__(self, db: Session, settings: Settings, registry: EngineRegistry) -> None:
        self.db, self.s, self.registry = db, settings, registry

    def _get(self, experiment_id: str) -> Experiment:
        e = self.db.get(Experiment, experiment_id)
        if e is None or e.archived:
            raise NotFound(f"experiment {experiment_id} not found")
        return e

    def validate(self, raw: dict[str, Any] | str) -> dict[str, Any]:
        doc = parse_document(raw)
        target = check_definition(self.s, self.registry, doc)
        warnings = []
        if doc.spec.fault.engine == "chaos_toolkit":
            warnings.append("`chaos validate` against the installed CLI runs in the worker at execution time")
        if not doc.spec.probes:
            warnings.append("no probes defined: the run can only be INCONCLUSIVE (nothing is measured)")
        return {"valid": True, "target": target.model_dump(), "warnings": warnings}

    def create(self, raw: dict[str, Any] | str, actor: str) -> Experiment:
        doc = parse_document(raw)
        check_definition(self.s, self.registry, doc)
        definition = doc.model_dump(by_alias=True)
        existing = self.db.scalar(select(Experiment).where(Experiment.name == doc.metadata.name))
        if existing:
            if existing.archived:
                existing.archived = False
            return self.update(existing.id, raw, actor)
        e = Experiment(name=doc.metadata.name, description=doc.metadata.description, definition=definition,
                       owner=actor, current_version=1, approved_version=None)
        self.db.add(e)
        self.db.flush()
        self.db.add(ExperimentVersion(experiment_id=e.id, version=1, definition=definition, created_by=actor))
        repo.audit(self.db, actor, "experiment.create", e.id, "success", {"name": e.name})
        self.db.commit()
        return e

    def update(self, experiment_id: str, raw: dict[str, Any] | str, actor: str) -> Experiment:
        e = self._get(experiment_id)
        doc = parse_document(raw)
        if doc.metadata.name != e.name:
            raise ValidationRejected("metadata.name is immutable")
        check_definition(self.s, self.registry, doc)
        e.current_version += 1  # new immutable version; earlier runs keep their snapshots
        e.definition = doc.model_dump(by_alias=True)
        e.description = doc.metadata.description
        e.approved_version = None
        self.db.add(ExperimentVersion(experiment_id=e.id, version=e.current_version, definition=e.definition,
                                      created_by=actor))
        repo.audit(self.db, actor, "experiment.update", e.id, "success", {"version": e.current_version})
        self.db.commit()
        return e

    def archive(self, experiment_id: str, actor: str) -> None:
        e = self._get(experiment_id)
        e.archived = True
        repo.audit(self.db, actor, "experiment.archive", e.id, "success")
        self.db.commit()

    def start_run(self, experiment_id: str, actor: str, enqueue) -> ExperimentRun:
        e = self._get(experiment_id)
        ver = self.db.scalar(select(ExperimentVersion).where(
            ExperimentVersion.experiment_id == e.id, ExperimentVersion.version == e.current_version))
        doc = parse_document(ver.definition)
        run = ExperimentRun(experiment_id=e.id, experiment_version=e.current_version, definition_snapshot=ver.definition,
                            requested_by=actor, dry_run=not self.s.execution_enabled,
                            scorecard=not_scored("run has not collected measurements yet"),
                            status=RunStatus.VALIDATING.value)
        self.db.add(run)
        self.db.flush()
        repo.add_event(self.db, run.id, "submitted", f"submitted by {actor}", {"dry_run": run.dry_run})
        try:
            target = check_definition(self.s, self.registry, doc)
            repo.acquire_leases(self.db, run.id, [target.key],
                                doc.spec.limits.run_timeout_seconds + self.s.run_grace_seconds
                                + self.s.fault_expiry_grace_seconds)
        except PlatformError as exc:
            self.db.rollback()
            run = self._persist_rejected(e, ver, actor, exc)
            raise type(exc)(exc.message, {**exc.details, "run_id": run.id}) from exc
        repo.transition(self.db, run, RunStatus.QUEUED, "queued")
        run.queued_at = utcnow()
        repo.audit(self.db, actor, "run.submit", run.id, "success", {"experiment": e.id, "dry_run": run.dry_run})
        self.db.commit()
        metrics.RUNS_SUBMITTED.inc()
        try:
            enqueue(run.id, doc.spec.limits.run_timeout_seconds + self.s.run_grace_seconds * 4)
        except Exception as exc:  # noqa: BLE001  (Redis outage)
            run.cleanup_status = CleanupStatus.NOT_REQUIRED.value
            repo.transition(self.db, run, RunStatus.FAILED, "queue unavailable")
            run.error_summary = f"could not enqueue: {type(exc).__name__}"
            self.db.commit()
            from app.core.exceptions import DependencyUnavailable

            raise DependencyUnavailable("job queue unavailable; run marked FAILED, nothing was executed") from exc
        return run

    def _persist_rejected(self, e: Experiment, ver: ExperimentVersion, actor: str, exc: PlatformError) -> ExperimentRun:
        run = ExperimentRun(experiment_id=e.id, experiment_version=e.current_version, definition_snapshot=ver.definition,
                            requested_by=actor, status=RunStatus.REJECTED.value, outcome="ERROR",
                            cleanup_status=CleanupStatus.NOT_REQUIRED.value,
                            scorecard=not_scored("run was rejected before measurements were collected"),
                            finished_at=utcnow(),
                            error_summary=exc.message[:500])
        self.db.add(run)
        self.db.flush()
        repo.add_event(self.db, run.id, "rejected", exc.message, exc.details)
        repo.audit(self.db, actor, "run.submit", run.id, "rejected", {"reason": exc.message[:200]})
        self.db.commit()
        return run
