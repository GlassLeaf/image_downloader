"""Shared sensitive-name policy and structured diagnostic redaction."""

from __future__ import annotations

import re
from collections.abc import Mapping

REDACTED = "<redacted>"
SENSITIVE_NAME_TERMS = (
    "authorization",
    "token",
    "key",
    "secret",
    "signature",
    "credential",
    "auth",
    "cookie",
    "password",
    "csrf",
    "sig",
    "bearer",
    "session",
    "refresh",
    "private",
    "passwd",
    "pwd",
)
SENSITIVE_TERM_PATTERN = "|".join(re.escape(term) for term in SENSITIVE_NAME_TERMS)

# These names contain a broad sensitive term but their values are documented,
# non-secret application settings.  Keep this exception narrow: diagnostics
# must still redact arbitrary plugin fields such as ``refresh_token``.
_SAFE_REFERENCE_NAMES = frozenset(("auth_refresh_attempts", "credential_service"))
_SECRET_MAPPINGS = frozenset(("headers", "secrets"))


def is_sensitive_field_name(value: object) -> bool:
    """Return whether a field name may identify credential material."""
    lowered = str(value).lower()
    return any(term in lowered for term in SENSITIVE_NAME_TERMS)


def redact_sensitive_values(value: object) -> object:
    """Return a JSON-shaped copy with sensitive structured fields redacted."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return redact_sensitive_values(model_dump(by_alias=True, warnings=False))
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_name, item in value.items():
            name = str(raw_name)
            lowered = name.lower()
            if lowered in _SECRET_MAPPINGS and isinstance(item, Mapping):
                result[name] = {
                    str(child_name): REDACTED if child_value is not None else None
                    for child_name, child_value in item.items()
                }
            elif lowered not in _SAFE_REFERENCE_NAMES and is_sensitive_field_name(lowered):
                result[name] = REDACTED if item is not None else None
            else:
                result[name] = redact_sensitive_values(item)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_sensitive_values(item) for item in value]
    return value
