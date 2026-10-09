
from app.engines.base import ResolvedTarget
from app.engines.registry import EngineRegistry
from app.schemas.probe import ProbeSample
from app.schemas.run import Outcome
from app.services.experiment_service import parse_document
from app.services.orchestrator import Ctx, Orchestrator


def mk(settings, doc_dict):
    o = Orchestrator(settings, EngineRegistry.from_settings(settings))
    c = Ctx(run_id="r", doc=parse_document(doc_dict), target=ResolvedTarget(environment="e", service="s"))
    return o, c


def ok(ms=10):
    return ProbeSample(ok=True, latency_ms=ms)


def test_pass(settings, doc_dict):
    o, c = mk(settings, doc_dict)
    c.during = {"p": [ok()] * 10}
    o._decide(c, recovered=True)
    assert c.outcome == Outcome.PASSED


def test_no_measurements_is_inconclusive_not_pass(settings, doc_dict):
    o, c = mk(settings, doc_dict)
    o._decide(c, recovered=True)
    assert c.outcome == Outcome.INCONCLUSIVE


def test_error_rate_fails_hypothesis(settings, doc_dict):
    o, c = mk(settings, doc_dict)
    c.during = {"p": [ok()] * 5 + [ProbeSample(ok=False, latency_ms=0)] * 5}
    o._decide(c, recovered=True)
    assert c.outcome == Outcome.FAILED_HYPOTHESIS


def test_latency_fails_hypothesis(settings, doc_dict):
    o, c = mk(settings, doc_dict)
    c.during = {"p": [ok(5000)] * 10}
    o._decide(c, recovered=True)
    assert c.outcome == Outcome.FAILED_HYPOTHESIS


def test_no_recovery_fails(settings, doc_dict):
    o, c = mk(settings, doc_dict)
    c.during = {"p": [ok()] * 10}
    c.recovery = {"p": [ProbeSample(ok=False, latency_ms=0)] * 3}
    o._decide(c, recovered=False)
    assert c.outcome == Outcome.FAILED_HYPOTHESIS


def test_cancel_is_inconclusive(settings, doc_dict):
    o, c = mk(settings, doc_dict)
    c.cancelled = True
    o._decide(c, recovered=True)
    assert c.outcome == Outcome.INCONCLUSIVE
