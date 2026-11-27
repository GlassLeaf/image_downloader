"""User-owned DNS host collections and safe variable-resolution failures."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from ..exceptions import ConfigurationError
from .hosts import normalize_host

HOST_GROUP_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
HOST_GROUP_REFERENCE = re.compile(r"host_groups\.([a-z][a-z0-9_]{0,63})")
EMPTY_HOST_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType({})


class HostGroupError(ValueError):
    """Publish only a fixed explanation and a validated group identifier."""

    def __init__(self, kind: str, group: str | None = None) -> None:
        self.kind = kind
        self.group = group if group is not None and HOST_GROUP_NAME.fullmatch(group) else None
        super().__init__(
            {
                "legacy": "allowed_hosts was removed; define host_groups.<name> and reference it explicitly",
                "mapping": "host_groups must be a mapping of names to DNS hostname arrays",
                "name": "host group names must match [a-z][a-z0-9_]{0,63}",
                "array": "each host group must be a DNS hostname array",
                "host": "host groups must contain only concrete DNS hostnames",
                "undefined": "referenced host group is not defined",
                "empty": "referenced host group must not be empty",
                "reference": "host group references must use host_groups.<name>",
                "arrays": "an origin template may contain at most one array reference",
                "expansion": "host group expansion must produce a valid origin",
            }[kind]
        )


def normalize_host_groups(value: object) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise HostGroupError("mapping")
    result: dict[str, tuple[str, ...]] = {}
    for group, hosts in value.items():
        if not isinstance(group, str) or not HOST_GROUP_NAME.fullmatch(group):
            raise HostGroupError("name")
        if not isinstance(hosts, (list, tuple)) or any(not isinstance(host, str) for host in hosts):
            raise HostGroupError("array", group)
        normalized: list[str] = []
        for host in hosts:
            try:
                if any(char.isspace() for char in host) or any(char in host for char in ":/\\*@?#"):
                    raise ValueError
                name, is_ip = normalize_host(host)
                if is_ip:
                    raise ValueError
            except (ValueError, ConfigurationError) as exc:
                raise HostGroupError("host", group) from exc
            normalized.append(name)
        result[group] = tuple(dict.fromkeys(normalized))
    return MappingProxyType(result)


def resolve_host_group(group: str, groups: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    if group not in groups:
        raise HostGroupError("undefined", group)
    if not groups[group]:
        raise HostGroupError("empty", group)
    return groups[group]
