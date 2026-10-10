from __future__ import annotations

import hashlib
import json
import re

from app.core.config import Settings
from app.core.exceptions import SafetyViolation
from app.db.session import session_scope
from app.engines.base import ResolvedTarget
from app.engines.registry import EngineRegistry
from app.models import ExperimentRun, PipelineExecution, TargetLease
from app.models.base import utcnow
from app.schemas.experiment import ExperimentDocument
from app.services.probe_service import check_probe_url


def _pipeline_state_for(settings: Settings, doc: ExperimentDocument) -> dict | None:
    if doc.spec.target.environment != "docker-test" or not doc.metadata.name.startswith("pipe-exp-"):
        return None
    suffix = doc.metadata.name.removeprefix("pipe-exp-")
    if not re.fullmatch(r"[a-f0-9]{8}", suffix):
        return None
    fingerprint = hashlib.sha256(
        json.dumps(doc.model_dump(by_alias=True), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with session_scope() as db:
        row = db.get(PipelineExecution, f"pipe-{suffix}")
        if row is None:
            return None
        state = row.state or {}
        running_now = row.status == "RUNNING" and state.get("status") == "RUNNING"
        retained_after_run = (
            row.status in {"PASSED", "FAILED", "DRY_RUN"}
            and state.get("status") in {"PASSED", "FAILED", "DRY_RUN"}
            and not state.get("config", {}).get("auto_cleanup", True)
        )
        if not (running_now or retained_after_run) or (state.get("container") or {}).get("status") != "running":
            return None
        if (
            doc.spec.target.service == state.get("target_service_key")
            and (state.get("confirmed_by") or state.get("approved_by"))
            and fingerprint == state.get("experiment_definition_sha256")
        ):
            return state
    return None


def list_retained_pipeline_targets(settings: Settings) -> list[dict]:
    """List pipeline targets whose state records them as deliberately retained."""
    with session_scope() as db:
        rows = list(db.query(PipelineExecution).order_by(PipelineExecution.created_at.desc()).all())
        active_leases = {
            lease.target_key for lease in db.query(TargetLease).filter(TargetLease.expires_at > utcnow()).all()
        }
        blocked_pipeline_ids = set()
        active_run_statuses = {"QUEUED", "RUNNING_BASELINE", "INJECTING", "OBSERVING", "ABORTING",
                               "CLEANING_UP", "VERIFYING_RECOVERY", "CLEANUP_FAILED"}
        for row in rows:
            run_id = (row.state or {}).get("experiment_run_id")
            run = db.get(ExperimentRun, run_id) if run_id else None
            if run and (run.status in active_run_statuses or run.cleanup_status == "FAILED"):
                blocked_pipeline_ids.add(row.id)
    output = []
    for row in rows:
        state = row.state or {}
        config = state.get("config", {})
        container = state.get("container", {})
        if (
            row.status not in {"RUNNING", "PASSED", "FAILED", "DRY_RUN"}
            or state.get("status") not in {"RUNNING", "PASSED", "FAILED", "DRY_RUN"}
            or row.id in blocked_pipeline_ids
            or config.get("auto_cleanup", True)
            or container.get("status") != "running"
            or not state.get("experiment_definition_sha256")
            or not state.get("experiment_id")
            or not state.get("target_service_key")
            or f"docker-test:{state.get('target_service_key')}" in active_leases
        ):
            continue
        primary = next((service for service in state.get("services", []) if service.get("primary")), {})
        output.append({
            "pipeline_id": state.get("id"),
            "project_name": state.get("project_name"),
            "experiment_id": state.get("experiment_id"),
            "status": state.get("status"),
            "service": state.get("target_service_key"),
            "service_name": primary.get("name") or state.get("project_name"),
            "container": container.get("name"),
        })
    return output


def resolve_target(settings: Settings, doc: ExperimentDocument) -> ResolvedTarget:
    t = doc.spec.target
    pipeline_state = _pipeline_state_for(settings, doc)
    if pipeline_state is not None:
        container = (pipeline_state.get("container") or {}).get("name")
        if not container:
            raise SafetyViolation("pipeline target container is missing")
        pipeline_id = pipeline_state.get("id", "")
        proxies = [f"pipe-{pipeline_id[-6:]}"] if pipeline_state.get("config", {}).get("fault_engine") == "toxiproxy" else []
        return ResolvedTarget(
            environment=t.environment,
            service=t.service,
            containers=[container],
            proxies=proxies,
            k8s_namespace="default",
            k8s_labels={"pipeline": pipeline_id},
        )

    if t.environment == "docker-test" and doc.metadata.name.startswith("pipe-exp-"):
        raise SafetyViolation(
            "pipeline-built target is stale, was cleaned up, or no longer matches its original definition; retarget it to an allowlisted service"
        )

    env = settings.environments.get(t.environment)
    if env is None:
        raise SafetyViolation(f"environment '{t.environment}' is not configured/allowlisted")
    svc = env.services.get(t.service)
    if svc is None:
        raise SafetyViolation(f"service '{t.service}' is not an approved target in '{t.environment}'")
    return ResolvedTarget(environment=t.environment, service=t.service, containers=list(svc.containers),
                          proxies=list(svc.toxiproxy_proxies), k8s_namespace=svc.k8s_namespace,
                          k8s_labels=dict(svc.k8s_labels))


def check_definition(settings: Settings, registry: EngineRegistry, doc: ExperimentDocument,
                     verify_pipeline_container: bool = False) -> ResolvedTarget:
    """Full safety policy. Raises SafetyViolation/ValidationRejected before anything touches infrastructure."""
    s = doc.spec
    target = resolve_target(settings, doc)
    pipeline_state = _pipeline_state_for(settings, doc)
    if pipeline_state is not None and verify_pipeline_container:
        container_name = (pipeline_state.get("container") or {}).get("name")
        pipeline_id = pipeline_state.get("id")
        try:
            import docker

            container = docker.from_env(timeout=5).containers.get(container_name)
            container.reload()
            labels = container.attrs.get("Config", {}).get("Labels") or {}
            if container.status != "running" or labels.get("chaoslab.pipeline") != pipeline_id:
                raise SafetyViolation("retained pipeline target is no longer running")
        except SafetyViolation:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SafetyViolation("retained pipeline target could not be verified as running") from exc
    env = settings.environments.get(s.target.environment)
    if pipeline_state is not None:
        config = pipeline_state.get("config", {})
        approved_engine = config.get("fault_engine")
        if s.fault.engine != approved_engine:
            raise SafetyViolation("pipeline experiment engine differs from the approved pipeline configuration")
        max_duration = min(
            int(config.get("fault_duration", 0)) + 60,
            settings.global_max_duration_seconds,
            s.safety.max_duration_seconds,
        )
        allowed_engines = {approved_engine}
        max_targets = 1
    elif env is None:
        raise SafetyViolation(f"environment '{s.target.environment}' is not configured/allowlisted")
    else:
        allowed_engines = {e.replace("-", "_") for e in env.engines} | {e.replace("_", "-") for e in env.engines} | set(env.engines)
        max_duration = min(env.max_duration_seconds, settings.global_max_duration_seconds, s.safety.max_duration_seconds)
        max_targets = env.max_targets
    if s.fault.engine not in allowed_engines:
        raise SafetyViolation(f"engine '{s.fault.engine}' is not permitted in '{s.target.environment}'")
    if s.fault.duration_seconds > max_duration:
        raise SafetyViolation(f"duration {s.fault.duration_seconds}s exceeds the effective limit of {max_duration}s")
    if len(target.containers) + len(target.proxies) > max_targets and s.fault.engine in ("pumba",):
        raise SafetyViolation(f"target count exceeds max_targets={max_targets}")
    engine = registry.get(s.fault.engine)
    engine.validate_target(target)
    engine.validate_parameters(s.fault.type, s.fault.parameters, target)
    probe_settings = settings
    if pipeline_state is not None:
        probe_settings = settings.model_copy(deep=True)
        known_hosts = {
            (pipeline_state.get("container") or {}).get("name"),
            pipeline_state.get("target_service_key"),
        }
        if pipeline_state.get("config", {}).get("fault_engine") == "toxiproxy":
            known_hosts.add("toxiproxy")
        probe_settings.probe_allowed_hosts.extend(
            host for host in known_hosts if host and host not in probe_settings.probe_allowed_hosts
        )
    for p in s.probes:
        if p.type == "http":
            check_probe_url(probe_settings, p.url or "")
        elif p.container not in target.containers:
            raise SafetyViolation(f"probe '{p.name}' container is not in the target allowlist")
    return target
