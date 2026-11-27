"""Origin patterns and deliberately limited endpoint template expansion."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from ..exceptions import ConfigurationError
from .host_groups import EMPTY_HOST_GROUPS, HOST_GROUP_REFERENCE, HostGroupError, resolve_host_group
from .hosts import normalize_host

try:
    from publicsuffix2 import PublicSuffixList
except ImportError:
    PublicSuffixList = None

_TOKEN = re.compile(r"\$\{([^{}]+)\}")
_CONFIG_REFERENCE = re.compile(r"config\.[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*")
_OPERATION_NAMES = frozenset({"origin", "host", "site_domain", "scheme", "port"})


@lru_cache(maxsize=1)
def _suffix_list() -> Any:
    if PublicSuffixList is None:
        return None
    try:
        with (
            files("image_downloader.configuration").joinpath("public_suffix_list.dat").open(encoding="utf-8") as stream
        ):
            return PublicSuffixList(stream)
    except (OSError, UnicodeError, ValueError):
        return None


def site_domain(host: str) -> str:
    """Resolve an authentication boundary with the packaged ICANN and PRIVATE PSL."""
    normalized, is_ip = normalize_host(host)
    psl = _suffix_list()
    if is_ip or psl is None:
        raise ValueError("site_domain requires a DNS host and the packaged Public Suffix List")
    suffix = psl.get_tld(normalized, strict=True)
    domain = psl.get_sld(normalized, strict=True)
    if not suffix or not domain or domain == suffix:
        raise ValueError("site_domain cannot determine a registrable domain from the packaged Public Suffix List")
    return str(domain)


@dataclass(frozen=True, slots=True)
class OriginPattern:
    scheme: str
    host: str
    port: int
    wildcard: bool = False

    @classmethod
    def parse(cls, value: str, *, allow_wildcard: bool = True) -> OriginPattern:
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
            raise ValueError("endpoint origin must not contain whitespace, controls or backslashes")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or "?" in value
            or "#" in value
        ):
            raise ValueError("endpoint origin must be an HTTP(S) origin without userinfo, path, query or fragment")
        host = parsed.hostname
        wildcard = host.startswith("*.")
        if "*" in host:
            if not allow_wildcard or not wildcard or host.count("*") != 1:
                raise ValueError("endpoint wildcard must be one complete leftmost host label")
            host = host[2:]
        try:
            host, is_ip = normalize_host(host)
        except ConfigurationError as exc:
            raise ValueError("endpoint origin contains an invalid host") from exc
        if wildcard:
            if is_ip:
                raise ValueError("endpoint wildcard cannot target an IP address")
            site_domain(host)
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        return cls(parsed.scheme, host, port, wildcard)

    @property
    def origin(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{'*.' if self.wildcard else ''}{host}:{self.port}"

    def matches(self, target: OriginPattern) -> bool:
        if target.wildcard or self.scheme != target.scheme or self.port != target.port:
            return False
        if not self.wildcard:
            return self.host == target.host
        label, separator, suffix = target.host.partition(".")
        return bool(label and separator and suffix == self.host)


def template_tokens(value: str) -> tuple[re.Match[str], ...]:
    matches = tuple(_TOKEN.finditer(value))
    if "${" in _TOKEN.sub("", value):
        raise ValueError("malformed endpoint variable reference")
    for match in matches:
        reference = match.group(1)
        if reference == "allowed_hosts":
            raise HostGroupError("legacy")
        if HOST_GROUP_REFERENCE.fullmatch(reference):
            continue
        if reference == "host_groups" or reference.startswith("host_groups."):
            raise HostGroupError("reference")
        if reference.startswith("operation.") and reference[10:] in _OPERATION_NAMES:
            continue
        if not _CONFIG_REFERENCE.fullmatch(reference):
            raise ValueError(f"unsupported endpoint variable: {reference}")
    return matches


def validate_origin(value: str) -> str:
    """Validate literals immediately; operation-dependent values are checked before use."""
    if template_tokens(value):
        return value
    return OriginPattern.parse(value).origin


def _config_value(config: Mapping[str, object], reference: str) -> str | tuple[str, ...]:
    current: object = config
    for key in reference.split(".")[1:]:
        if not isinstance(current, Mapping) or key not in current:
            raise ValueError(f"undefined endpoint variable: {reference}")
        current = current[key]
    if isinstance(current, str):
        values = (current,)
    elif isinstance(current, (list, tuple)) and current and all(isinstance(item, str) for item in current):
        values = tuple(current)
    else:
        raise ValueError(f"endpoint variable {reference} must be a string or a non-empty string array")
    if any(not item or "${" in item for item in values):
        raise ValueError(f"endpoint variable {reference} must be non-empty and cannot contain variable references")
    return values[0] if isinstance(current, str) else values


def expand_origins(
    value: str | tuple[str, ...],
    config: Mapping[str, object],
    operation_url: str | None,
    *,
    host_groups: Mapping[str, tuple[str, ...]] | None = EMPTY_HOST_GROUPS,
) -> tuple[str, ...]:
    """Expand arrays once; absent operation/deferred groups retain declaration templates."""
    operation = OriginPattern.parse(_url_origin(operation_url), allow_wildcard=False) if operation_url else None
    result: list[str] = []
    for template in (value,) if isinstance(value, str) else value:
        candidates = [""]
        position = arrays = 0
        tokens = template_tokens(template)
        groups = [
            matched_group.group(1)
            for match in tokens
            if (matched_group := HOST_GROUP_REFERENCE.fullmatch(match.group(1)))
        ]
        for match in tokens:
            reference = match.group(1)
            replacement: str | tuple[str, ...]
            group_reference = HOST_GROUP_REFERENCE.fullmatch(reference)
            if group_reference is not None:
                group = group_reference.group(1)
                if host_groups is None:
                    replacement = match.group(0)
                else:
                    replacement = resolve_host_group(group, host_groups)
            elif reference.startswith("config."):
                replacement = _config_value(config, reference)
            elif operation is None:
                replacement = match.group(0)
            else:
                replacement = _operation_value(operation, reference[10:])
            if isinstance(replacement, tuple):
                arrays += 1
                if arrays > 1:
                    raise HostGroupError("arrays")
                replacements = replacement
            else:
                replacements = (replacement,)
            candidates = [
                prefix + template[position : match.start()] + item for prefix in candidates for item in replacements
            ]
            position = match.end()
        for candidate in candidates:
            expanded = candidate + template[position:]
            try:
                result.append(validate_origin(expanded))
            except ValueError as exc:
                if groups:
                    raise HostGroupError("expansion", groups[0]) from exc
                raise
    return tuple(dict.fromkeys(result))


def _url_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("operation URL must be absolute HTTP(S) without userinfo")
    return f"{parsed.scheme}://{parsed.netloc}"


def _operation_value(operation: OriginPattern, name: str) -> str:
    if name == "site_domain":
        return site_domain(operation.host)
    if name == "origin":
        return operation.origin
    if name == "host":
        return f"[{operation.host}]" if ":" in operation.host else operation.host
    if name == "port":
        return str(operation.port)
    return operation.scheme


def referenced_host_groups(value: str | tuple[str, ...]) -> frozenset[str]:
    return frozenset(
        group.group(1)
        for template in ((value,) if isinstance(value, str) else value)
        for token in template_tokens(template)
        if (group := HOST_GROUP_REFERENCE.fullmatch(token.group(1))) is not None
    )
