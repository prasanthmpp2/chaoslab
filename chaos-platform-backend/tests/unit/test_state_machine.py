from app.schemas.run import ALLOWED, TERMINAL, RunStatus, can_transition


def test_happy_path():
    path = ["VALIDATING", "QUEUED", "RUNNING_BASELINE", "INJECTING", "OBSERVING", "CLEANING_UP",
            "VERIFYING_RECOVERY", "SUCCEEDED"]
    for a, b in zip(path, path[1:], strict=False):
        assert can_transition(RunStatus(a), RunStatus(b)), (a, b)


def test_cannot_skip_cleanup_after_injection():
    assert not can_transition(RunStatus.OBSERVING, RunStatus.SUCCEEDED)
    assert not can_transition(RunStatus.INJECTING, RunStatus.SUCCEEDED)


def test_terminal_states_have_no_exits_except_cleanup_retry():
    for t in TERMINAL:
        outs = ALLOWED.get(t, set())
        assert outs <= {RunStatus.CLEANING_UP}
    assert can_transition(RunStatus.CLEANUP_FAILED, RunStatus.CLEANING_UP)
    assert not can_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING_BASELINE)


def test_cleanup_failure_is_reachable():
    assert can_transition(RunStatus.CLEANING_UP, RunStatus.CLEANUP_FAILED)
