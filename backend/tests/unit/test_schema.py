import pytest

from app.core.exceptions import ValidationRejected
from app.schemas.fault import validate_fault_parameters
from app.services.experiment_service import parse_document


def test_valid_example_parses(doc_dict):
    d = parse_document(doc_dict)
    assert d.spec.fault.duration_seconds == 30


def test_unknown_engine_fault_combo_rejected():
    with pytest.raises(ValidationRejected):
        validate_fault_parameters("toxiproxy", "packet-loss", {})  # Toxiproxy has no packet-loss toxic


def test_duration_over_safety_limit(doc_dict):
    doc_dict["spec"]["fault"]["durationSeconds"] = 120
    with pytest.raises(ValidationRejected):
        parse_document(doc_dict)


def test_missing_required_parameter(doc_dict):
    del doc_dict["spec"]["fault"]["parameters"]["latencyMs"]
    with pytest.raises(ValidationRejected):
        parse_document(doc_dict)


def test_invalid_numeric_range(doc_dict):
    doc_dict["spec"]["fault"]["parameters"]["latencyMs"] = -5
    with pytest.raises(ValidationRejected):
        parse_document(doc_dict)


def test_secret_like_key_rejected(doc_dict):
    doc_dict["spec"]["fault"]["parameters"]["password"] = "x"
    with pytest.raises(ValidationRejected):
        parse_document(doc_dict)


def test_cleanup_must_remove_faults(doc_dict):
    doc_dict["spec"]["cleanup"]["removeFaults"] = False
    with pytest.raises(ValidationRejected):
        parse_document(doc_dict)


def test_environment_must_be_in_allowlist(doc_dict):
    doc_dict["spec"]["safety"]["environmentAllowlist"] = ["staging"]
    with pytest.raises(ValidationRejected):
        parse_document(doc_dict)


def test_unknown_field_rejected(doc_dict):
    doc_dict["spec"]["fault"]["shell"] = "rm -rf /"
    with pytest.raises(ValidationRejected):
        parse_document(doc_dict)
