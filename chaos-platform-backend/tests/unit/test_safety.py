import pytest

from app.core.exceptions import SafetyViolation
from app.engines.registry import EngineRegistry
from app.services.experiment_service import parse_document
from app.services.probe_service import check_probe_url, p95, summarize
from app.services.safety_service import check_definition


def test_valid_definition_resolves_target(settings, doc_dict):
    t = check_definition(settings, EngineRegistry.from_settings(settings), parse_document(doc_dict))
    assert t.key == "docker-test:payment-service" and t.proxies == ["payment-db"]


def test_unapproved_service_rejected(settings, doc_dict):
    doc_dict["spec"]["target"]["service"] = "billing"
    with pytest.raises(SafetyViolation):
        check_definition(settings, EngineRegistry.from_settings(settings), parse_document(doc_dict))


def test_proxy_not_allowlisted(settings, doc_dict):
    doc_dict["spec"]["fault"]["parameters"]["proxy"] = "other-db"
    with pytest.raises(SafetyViolation):
        check_definition(settings, EngineRegistry.from_settings(settings), parse_document(doc_dict))


def test_engine_not_permitted_in_environment(settings, doc_dict):
    settings.environments["docker-test"].engines = ["pumba"]
    with pytest.raises(SafetyViolation):
        check_definition(settings, EngineRegistry.from_settings(settings), parse_document(doc_dict))


def test_environment_duration_cap(settings, doc_dict):
    settings.environments["docker-test"].max_duration_seconds = 10
    with pytest.raises(SafetyViolation):
        check_definition(settings, EngineRegistry.from_settings(settings), parse_document(doc_dict))


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest", "http://metadata.google.internal/",
                                 "http://evil.example/x", "http://user:pw@payment-service/", "file:///etc/passwd"])
def test_probe_url_blocked(settings, url):
    with pytest.raises(SafetyViolation):
        check_probe_url(settings, url)


def test_probe_url_allowed(settings):
    assert check_probe_url(settings, "http://payment-service:8080/health") == "payment-service"


def test_p95_and_summary():
    assert p95([1, 2, 3, 4, 100]) == 100
    assert p95([]) is None
    from app.schemas.probe import ProbeSample

    s = summarize("x", "during", [ProbeSample(ok=True, latency_ms=10), ProbeSample(ok=False, latency_ms=0)])
    assert s.error_rate == 0.5 and s.errors == 1
