from __future__ import annotations

import pytest

from image_downloader.commands.reporting import _doctor_redact
from image_downloader.config import Logging
from image_downloader.privacy.log_safety import mask_log_text
from image_downloader.privacy.sensitive_values import REDACTED, redact_sensitive_values


def test_idr_034_structured_redaction_covers_the_shared_sensitive_vocabulary() -> None:
    value = {
        "session_id": "session-value",
        "refresh_value": "refresh-value",
        "csrf": "csrf-value",
        "signature": "signature-value",
        "private_material": "private-value",
        "nested": {"passwd": "password-value", "mode": "visible"},
        "headers": {"User-Agent": "agent", "Authorization": "bearer"},
        "secrets": {"api_key": "REFERENCE"},
        "credential_service": "image-downloader.smtp",
    }

    redacted = redact_sensitive_values(value)

    assert redacted == {
        "session_id": REDACTED,
        "refresh_value": REDACTED,
        "csrf": REDACTED,
        "signature": REDACTED,
        "private_material": REDACTED,
        "nested": {"passwd": REDACTED, "mode": "visible"},
        "headers": {"User-Agent": REDACTED, "Authorization": REDACTED},
        "secrets": {"api_key": REDACTED},
        "credential_service": "image-downloader.smtp",
    }
    assert _doctor_redact(value) == redacted


@pytest.mark.parametrize("name", ("session_id", "refresh_token", "csrf", "signature"))
def test_idr_034_logging_and_safe_parameter_validation_share_sensitive_terms(name: str) -> None:
    rendered = mask_log_text(f"{name}=sensitive-value")

    assert "sensitive-value" not in rendered
    with pytest.raises(ValueError, match="safe parameter names are invalid"):
        Logging(safe_query_parameters=(name,))
