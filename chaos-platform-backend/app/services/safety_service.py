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
        raise SafetyViolation(f"service '{t.service}' is not an approved target in '{t.environment}'")
    return ResolvedTarget(environment=t.environment, service=t.service, containers=list(svc.containers),
                          proxies=list(svc.toxiproxy_proxies), k8s_namespace=svc.k8s_namespace,
                          k8s_labels=dict(svc.k8s_labels))


def check_definition(settings: Settings, registry: EngineRegistry, doc: ExperimentDocument) -> ResolvedTarget:
    """Full safety policy. Raises SafetyViolation/ValidationRejected before anything touches infrastructure."""
    s = doc.spec
    target = resolve_target(settings, doc)
    env = settings.environments[s.target.environment]
    if s.fault.engine not in env.engines:
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
