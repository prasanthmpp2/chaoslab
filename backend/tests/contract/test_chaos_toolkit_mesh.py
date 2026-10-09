import pytest

from app.core.exceptions import SafetyViolation
from app.engines.base import FaultSpec, ResolvedTarget
from app.engines.chaos_mesh import build_manifest
from app.engines.chaos_toolkit import check_native_safety, interpret_journal

HTTP_PROBE = {"type": "probe", "name": "p", "provider": {"type": "http", "url": "http://x"}}


def test_process_provider_rejected(settings):
    doc = {"method": [{"type": "action", "name": "a", "provider": {"type": "process", "path": "rm"}}]}
    with pytest.raises(SafetyViolation):
        check_native_safety(doc, settings)


def test_unlisted_python_module_rejected(settings):
    doc = {"method": [{"type": "action", "name": "a", "provider": {"type": "python", "module": "os", "func": "system"}}]}
    with pytest.raises(SafetyViolation):
        check_native_safety(doc, settings)


def test_secrets_block_rejected(settings):
    with pytest.raises(SafetyViolation):
        check_native_safety({"secrets": {"a": {"b": "c"}}, "method": [HTTP_PROBE]}, settings)


def test_http_provider_allowed(settings):
    check_native_safety({"method": [HTTP_PROBE]}, settings)


def test_journal_mapping():
    assert interpret_journal({"status": "completed", "deviated": False}, 0).passed is True
    assert interpret_journal({"status": "completed", "deviated": True}, 0).passed is False
    assert interpret_journal({"status": "failed"}, 1).passed is None
    assert interpret_journal({"status": "completed", "rollbacks": [{"status": "failed"}]}, 0).rollback_failed


def mesh_spec(ftype, params, ns="test", labels=None):
    return FaultSpec(run_id="run-1234", fault_id="fault-5678", fault_type=ftype, parameters=params, duration_seconds=30,
                     target=ResolvedTarget(environment="k", service="s", k8s_namespace=ns,
                                           k8s_labels={"app": "web"} if labels is None else labels))


def test_mesh_podkill_manifest():
    m = build_manifest(mesh_spec("pod-kill", {}))
    assert m["kind"] == "PodChaos" and m["spec"]["action"] == "pod-kill"
    assert m["spec"]["selector"] == {"namespaces": ["test"], "labelSelectors": {"app": "web"}}
    assert m["metadata"]["labels"]["chaos-platform/run-id"] == "run-1234"


def test_mesh_network_delay_manifest():
    m = build_manifest(mesh_spec("network-delay", {"latencyMs": 200, "jitterMs": 10}))
    assert m["kind"] == "NetworkChaos" and m["spec"]["delay"]["latency"] == "200ms"


def test_mesh_requires_label_selector():
    with pytest.raises(SafetyViolation):
        build_manifest(mesh_spec("pod-kill", {}, labels={}))
