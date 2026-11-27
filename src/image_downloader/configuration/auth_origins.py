"""User-owned origin templates and the shared authentication boundary matcher."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http.cookiejar import Cookie

from .endpoint_origins import OriginPattern, expand_origins, template_tokens
from .host_groups import HOST_GROUP_REFERENCE, HostGroupError


class AuthOriginError(ValueError):
    """A fixed, safe explanation that never includes a supplied input value."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(
            {
                "reference": "auth origins may reference only host_groups.<name>",
                "array": "an origin template may contain only one host group reference",
                "invalid": "auth origin must be an HTTP(S) origin without userinfo, path, query or fragment",
                "wildcard": (
                    "auth wildcard must be HTTPS with one complete leftmost wildcard label and a registrable base"
                ),
                "overlap": "authentication site ranges must not overlap",
            }[kind]
        )


def _auth_pattern(value: str) -> OriginPattern:
    try:
        pattern = OriginPattern.parse(value)
    except (ValueError, TypeError) as exc:
        raise AuthOriginError("wildcard" if "*" in value else "invalid") from exc
    if pattern.wildcard and pattern.scheme != "https":
        raise AuthOriginError("wildcard")
    return pattern


def validate_auth_origin(value: str) -> str:
    try:
        tokens = template_tokens(value)
    except HostGroupError:
        raise
    except ValueError as exc:
        raise AuthOriginError("reference") from exc
    if any(not HOST_GROUP_REFERENCE.fullmatch(token.group(1)) for token in tokens):
        raise AuthOriginError("reference")
    if len(tokens) > 1:
        raise AuthOriginError("array")
    if tokens:
        _auth_pattern(value[: tokens[0].start()] + "example.com" + value[tokens[0].end() :])
        return value
    return _auth_pattern(value).origin


def expand_auth_origins(origins: tuple[str, ...], host_groups: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    for origin in origins:
        validate_auth_origin(origin)
    try:
        expanded = expand_origins(origins, {}, None, host_groups=host_groups)
    except HostGroupError:
        raise
    except ValueError as exc:
        raise AuthOriginError("invalid") from exc
    return tuple(dict.fromkeys(_auth_pattern(value).origin for value in expanded))


def _overlaps(first: OriginPattern, second: OriginPattern) -> bool:
    if (first.scheme, first.port) != (second.scheme, second.port):
        return False
    if first.wildcard and second.wildcard:
        return first.host == second.host
    return first.matches(second) if first.wildcard else second.matches(first) if second.wildcard else first == second


@dataclass(frozen=True, slots=True)
class OriginMatcher:
    patterns: tuple[OriginPattern, ...]

    @classmethod
    def compile(cls, origins: tuple[str, ...]) -> OriginMatcher:
        return cls(tuple(OriginPattern.parse(origin) for origin in origins))

    def matches(self, origin: str) -> bool:
        target = OriginPattern.parse(origin, allow_wildcard=False)
        return any(pattern.matches(target) for pattern in self.patterns)

    def overlaps(self, other: OriginMatcher) -> bool:
        return any(_overlaps(first, second) for first in self.patterns for second in other.patterns)

    def intersection(self, other: OriginMatcher) -> OriginMatcher:
        patterns = tuple(
            dict.fromkeys(
                second if first.wildcard and not second.wildcard else first
                for first in self.patterns
                for second in other.patterns
                if _overlaps(first, second)
            )
        )
        return OriginMatcher(patterns)

    def accepts_cookie(self, cookie: Cookie) -> bool:
        """Whether a cookie can apply to a concrete origin in this range.

        Paths remain in the snapshot for the caller's path selection. Actual
        sends still use the HTTP client's normal cookie selection rules.
        """
        if cookie.is_expired():
            return False
        domain = cookie.domain.lstrip(".").lower()
        for pattern in self.patterns:
            if cookie.secure and pattern.scheme != "https":
                continue
            if not pattern.wildcard:
                if pattern.host == domain or (cookie.domain_specified and pattern.host.endswith("." + domain)):
                    return True
            elif cookie.domain_specified:
                if pattern.host == domain or pattern.host.endswith("." + domain):
                    return True
                if pattern.matches(OriginPattern(pattern.scheme, domain, pattern.port)):
                    return True
            elif pattern.matches(OriginPattern(pattern.scheme, domain, pattern.port)):
                return True
        return False
