from __future__ import annotations

from app.core.config import Settings
from app.core.exceptions import SafetyViolation
from app.engines.base import ResolvedTarget
from app.engines.registry import EngineRegistry
from app.schemas.experiment import ExperimentDocument
from app.services.probe_service import check_probe_url


def resolve_target(settings: Settings, doc: ExperimentDocument) -> ResolvedTarget:
    t = doc.spec.target
    env = settings.environments.get(t.environment)
    if env is None:
        raise SafetyViolation(f"environment '{t.environment}' is not configured/allowlisted")
    svc = env.services.get(t.service)
    if svc is None:
        from pathlib import Path
        import json
        p_base = Path(settings.artifact_dir) / "pipelines"
        if p_base.exists():
            dirs = sorted(p_base.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            for p_dir in dirs:
                p_file = p_dir / "pipeline.json"
                if p_file.exists():
                    try:
                        with open(p_file, "r") as f:
                            p_data = json.load(f)
                            slug = p_data.get("project_name", "")
                            pid = p_data.get("id", "")
                            # Check primary container or any discovered service container in multi-service project
                            target_container = None
                            if f"pipe-{slug}-{pid[-4:]}" == t.service:
                                target_container = p_data.get("container", {}).get("name")
                            
                            if not target_container and "services" in p_data:
                                for s_item in p_data["services"]:
                                    s_name = s_item.get("name", "")
                                    s_cname = s_item.get("container_name", "")
                                    if t.service in (f"pipe-{slug}-{s_name}-{pid[-4:]}", f"pipe-{s_name}-{pid[-4:]}", s_cname):
                                        target_container = s_cname
                                        break
                                    # Fallback if t.service matched base slug
                                    if f"pipe-{slug}-{pid[-4:]}" == t.service:
                                        target_container = s_cname
                                        break
                            
                            if target_container:
                                proxies = [f"pipe-{pid[-6:]}"] if p_data.get("config", {}).get("fault_engine") == "toxiproxy" else []
                                return ResolvedTarget(
                                    environment=t.environment,
                                    service=t.service,
                                    containers=[target_container],
                                    proxies=proxies,
                                    k8s_namespace="default",
                                    k8s_labels={"pipeline": pid}
                                )
                    except Exception:
                        pass
        raise SafetyViolation(f"service '{t.service}' is not an approved target in '{t.environment}'")
    return ResolvedTarget(environment=t.environment, service=t.service, containers=list(svc.containers),
                          proxies=list(svc.toxiproxy_proxies), k8s_namespace=svc.k8s_namespace,
                          k8s_labels=dict(svc.k8s_labels))


def check_definition(settings: Settings, registry: EngineRegistry, doc: ExperimentDocument) -> ResolvedTarget:
    """Full safety policy. Raises SafetyViolation/ValidationRejected before anything touches infrastructure."""
    s = doc.spec
    target = resolve_target(settings, doc)
    env = settings.environments[s.target.environment]
    allowed_engines = {e.replace("-", "_") for e in env.engines} | {e.replace("_", "-") for e in env.engines} | set(env.engines)
    if s.fault.engine not in allowed_engines:
        raise SafetyViolation(f"engine '{s.fault.engine}' is not permitted in '{s.target.environment}'")
    limit = min(env.max_duration_seconds, settings.global_max_duration_seconds, s.safety.max_duration_seconds)
    if s.fault.duration_seconds > limit:
        raise SafetyViolation(f"duration {s.fault.duration_seconds}s exceeds the effective limit of {limit}s")
    if len(target.containers) + len(target.proxies) > env.max_targets and s.fault.engine in ("pumba",):
        raise SafetyViolation(f"target count exceeds max_targets={env.max_targets}")
    engine = registry.get(s.fault.engine)
    engine.validate_target(target)
    engine.validate_parameters(s.fault.type, s.fault.parameters, target)
    for p in s.probes:
        if p.type == "http":
            check_probe_url(settings, p.url or "")
        elif p.container not in target.containers:
            raise SafetyViolation(f"probe '{p.name}' container is not in the target allowlist")
    return target
