import pytest

from app.core.exceptions import Unauthorized
from app.core.security import Role, authenticate, redact, redact_text


def test_authenticate(settings):
    p = authenticate("admin-key", settings)
    assert p.user == "alice" and p.has(Role.OPERATOR)
    v = authenticate("view-key", settings)
    assert v.has(Role.VIEWER) and not v.has(Role.OPERATOR)


def test_bad_or_missing_key(settings):
    with pytest.raises(Unauthorized):
        authenticate("nope", settings)
    with pytest.raises(Unauthorized):
        authenticate(None, settings)


def test_redaction():
    assert redact({"password": "x", "ok": 1}) == {"password": "[REDACTED]", "ok": 1}
    assert "hunter2" not in redact_text("token=hunter2 and more")
    assert "s3cret" not in redact_text("postgres://u:s3cret@db/x")
    assert "abc123" not in redact_text("Authorization: Bearer abc123")
