"""Offline host normalization and site overlay names."""

from __future__ import annotations

import ipaddress
import re

from ..exceptions import ConfigurationError

try:  # The package ships its PSL data; no network lookup is ever performed.
    from publicsuffix2 import get_sld as _public_suffix_get_sld
except ImportError:  # Source-only development remains deterministic.
    _public_suffix_get_sld = None


# Bundled PSL rules. The resolver implements exact matching locally and never
# downloads suffix data while loading configuration.
_PSL_RULES = frozenset(
    (
        "ac ae app au be biz br ca ch cn co co.jp co.kr co.nz co.uk com "
        "com.au com.br com.cn de dev edu es fr gov gr hk id in info io it "
        "jp kr me mil mx net net.au net.br net.cn nl no org org.au org.br "
        "org.cn org.uk pl ru se sg tech uk us xyz za github.io"
    ).split()
)
_HOST_NAME = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)


def normalize_host(host: str) -> tuple[str, bool]:
    candidate = host.strip().rstrip(".")
    if not candidate:
        raise ConfigurationError("site host is empty")
    try:
        return ipaddress.ip_address(candidate).compressed.lower(), True
    except ValueError:
        pass
    try:
        value = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ConfigurationError("site host is not valid IDNA") from exc
    if not _HOST_NAME.fullmatch(value):
        raise ConfigurationError("site host is not a DNS hostname")
    return value, False


def registrable_domain(host: str) -> str:
    if _public_suffix_get_sld is not None:
        value = _public_suffix_get_sld(host, strict=False)
        if value:
            return str(value).lower()
    labels = host.split(".")
    public_count = max(
        (len(rule.split(".")) for rule in _PSL_RULES if labels[-len(rule.split(".")) :] == rule.split(".")), default=1
    )
    return host if len(labels) <= public_count else ".".join(labels[-(public_count + 1) :])


def site_file_name(host: str) -> str:
    normalized, is_ip = normalize_host(host)
    if not is_ip:
        return f"{normalized}.yaml"
    address = ipaddress.ip_address(normalized)
    return f"ipv6-{address.exploded.replace(':', '')}.yaml" if address.version == 6 else f"{normalized}.yaml"
