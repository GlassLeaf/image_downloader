from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from ..exceptions import PUBLIC_EXCEPTION_NAMES
from .sensitive_values import SENSITIVE_TERM_PATTERN

_SENSITIVE_TERM = SENSITIVE_TERM_PATTERN
_SENSITIVE_FIELD_NAME = rf"[A-Za-z0-9_.-]*(?:{_SENSITIVE_TERM})[A-Za-z0-9_.-]*"
_SENSITIVE_HEADER = re.compile(rf"(?im)(?P<key>\b(?:{_SENSITIVE_TERM})\b)(?P<separator>\s*:\s*)(?P<value>[^\r\n]*)")
_QUOTED_SENSITIVE_FIELD = re.compile(
    rf"(?i)(?P<quote>[\"'])(?P<key>{_SENSITIVE_FIELD_NAME})(?P=quote)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s,;&}]+)"
)
_UNQUOTED_SENSITIVE_FIELD = re.compile(
    rf"(?i)(?P<key>\b{_SENSITIVE_FIELD_NAME}\b)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[^\s,;&}]+)"
)
_SENSITIVE_QUERY = re.compile(rf"(?i)([?&](?:[^=&\s]*(?:{_SENSITIVE_TERM}|key)[^=&\s]*)=)[^&\s]+")
_SENSITIVE_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_SENSITIVE_BEARER = re.compile(r"(?i)(\bbearer\s+)[^\s]+")
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SAFE_PARAMETERS = {
    "page",
    "offset",
    "limit",
    "id",
    "chapter",
    "image",
    "width",
    "height",
    "format",
    "lang",
    "view",
}
_SAFE_EXCEPTION_NAMES = PUBLIC_EXCEPTION_NAMES


def mask_log_text(
    value: str,
    *,
    safe_query_parameters: set[str] | None = None,
    safe_fragment_parameters: set[str] | None = None,
) -> str:
    """Mask secrets in arbitrary log text while retaining useful diagnostics."""

    def replace_url(match: re.Match[str]) -> str:
        return safe_url(
            match.group(0),
            safe_query_parameters=safe_query_parameters,
            safe_fragment_parameters=safe_fragment_parameters,
        )

    value = _URL_IN_TEXT.sub(replace_url, value)
    value = _SENSITIVE_HEADER.sub(r"\g<key>\g<separator>[REDACTED]", value)
    value = _QUOTED_SENSITIVE_FIELD.sub(
        r"\g<quote>\g<key>\g<quote>\g<separator>[REDACTED]",
        value,
    )
    value = _UNQUOTED_SENSITIVE_FIELD.sub(
        r"\g<key>\g<separator>[REDACTED]",
        value,
    )
    value = _SENSITIVE_QUERY.sub(r"\1[REDACTED]", value)
    value = _SENSITIVE_USERINFO.sub(r"\1[REDACTED]@", value)
    return _SENSITIVE_BEARER.sub(r"\1[REDACTED]", value)


def safe_url(
    value: str,
    *,
    safe_query_parameters: set[str] | None = None,
    safe_fragment_parameters: set[str] | None = None,
) -> str:
    """Render a URL without credentials while retaining approved diagnostics."""
    try:
        parsed = urlsplit(value)
        scheme = _safe_text(parsed.scheme, 32)
        host = parsed.hostname or "[REDACTED]"
        try:
            port = f":{parsed.port}" if parsed.port is not None else ""
        except ValueError:
            port = ""
        authority = f"userinfo=[REDACTED]@{host}{port}" if parsed.username is not None else f"{host}{port}"
        rendered = f"{scheme}://{authority}{_safe_text(parsed.path or '/', 1024)}"
        rendered += _safe_parameters(
            parsed.query,
            _SAFE_PARAMETERS | (safe_query_parameters or set()),
            "?",
        )
        if parsed.fragment:
            rendered += _safe_parameters(
                parsed.fragment,
                _SAFE_PARAMETERS | (safe_fragment_parameters or set()),
                "#",
            )
        return rendered
    except Exception:
        return "[REDACTED]"


def safe_relative_path(value: str | Path, output_root: Path | None) -> str:
    if output_root is None:
        return "[REDACTED]"
    try:
        relative = Path(value).resolve().relative_to(output_root.resolve())
        return _safe_text(mask_log_text(relative.as_posix()), 1024)
    except (OSError, ValueError):
        return "[REDACTED]"


def safe_exception_name(value: object) -> str:
    name = value if isinstance(value, str) else type(value).__name__
    return name if name in _SAFE_EXCEPTION_NAMES else "UnknownError"


def _safe_parameters(value: str, allowed: set[str], prefix: str) -> str:
    if not value:
        return ""
    if "=" not in value:
        return f"{prefix}fragment=[REDACTED]" if prefix == "#" else f"{prefix}[REDACTED]"
    pairs = parse_qsl(value, keep_blank_values=True)
    if not pairs:
        return f"{prefix}[REDACTED]"
    return prefix + "&".join(
        f"{_safe_text(key, 64)}={_safe_text(item, 256) if key.lower() in allowed else '[REDACTED]'}"
        for key, item in pairs
    )


def _safe_text(value: str, maximum: int) -> str:
    clean = "".join(character for character in str(value) if character >= " " and character != "\x7f")
    return clean[:maximum] + ("…" if len(clean) > maximum else "")


def safe_log_text(value: str, *, maximum: int = 4096) -> str:
    """Mask secrets and collapse control characters for one structured field."""
    return _safe_text(mask_log_text(value), maximum)
