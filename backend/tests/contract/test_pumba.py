import pytest

from app.core.exceptions import SafetyViolation
from app.engines.base import FaultSpec, ResolvedTarget
from app.engines.pumba import build_argv


def spec(ftype, params=None, d=20):
    return FaultSpec(run_id="r" * 8, fault_id="f" * 8, fault_type=ftype, parameters=params or {}, duration_seconds=d,
                     target=ResolvedTarget(environment="e", service="s", containers=["web"]))


def test_kill_argv(settings):
    a = build_argv(settings, spec("container-kill"), ["web"])
    assert a == ["pumba", "--log-level", "info", "kill", "--signal", "SIGKILL", "web"]


def test_netem_delay_argv(settings):
    a = build_argv(settings, spec("network-delay", {"timeMs": 300, "jitterMs": 20}), ["web"])
    assert a[3:] == ["netem", "--duration", "20s", "delay", "--time", "300", "--jitter", "20", "web"]


def test_all_args_are_plain_strings_no_shell(settings):
    a = build_argv(settings, spec("container-pause"), ["web"])
    assert all(isinstance(x, str) for x in a) and a[0] == "pumba"


def test_rejects_unsafe_container_name(settings):
    with pytest.raises(SafetyViolation):
        build_argv(settings, spec("container-kill"), ["web; rm -rf /"])


def test_invalid_signal_rejected(settings):
    from app.core.exceptions import ValidationRejected

    with pytest.raises(ValidationRejected):
        build_argv(settings, spec("container-kill", {"signal": "SIGKILL; ls"}), ["web"])
