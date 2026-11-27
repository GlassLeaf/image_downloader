"""Compiled, operation-local authentication rules shared by every request path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from ..configuration.endpoint_origins import OriginPattern, expand_origins
from ..configuration.host_groups import HostGroupError
from ..configuration.models import AuthenticationPolicy, EndpointSettings, PluginConnections
from ..diagnostics import host_group_diagnostic
from ..exceptions import ConfigurationError
from ..ports import Endpoint


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    origin: str
    auth: Literal["session", "none"]
    policy: AuthenticationPolicy


def endpoint_policy(item: EndpointSettings, base: AuthenticationPolicy) -> AuthenticationPolicy:
    overrides = item.verification.model_dump(exclude_unset=True)
    values = base.model_dump()
    if "before_request" in overrides:
        values["verify_before_request"] = overrides["before_request"]
    if "interval_seconds" in overrides:
        values["verify_interval_seconds"] = overrides["interval_seconds"]
    return AuthenticationPolicy.model_validate(values)


def expand_connections(
    connections: PluginConnections,
    config: Mapping[str, object],
    operation_url: str | None = None,
    *,
    host_groups: Mapping[str, tuple[str, ...]] | None = None,
) -> PluginConnections:
    expanded = {}
    for name, item in connections.endpoints.items():
        try:
            origins = expand_origins(item.origin, config, operation_url, host_groups=host_groups)
        except HostGroupError as exc:
            raise host_group_diagnostic(exc, reference_field="endpoints.<endpoint>.origin") from exc
        except ValueError as exc:
            raise ConfigurationError(f"endpoints.{name}.origin: {exc}") from exc
        expanded[name] = item.model_copy(update={"origin": origins})
    try:
        return PluginConnections.model_validate({"authentication": connections.authentication, "endpoints": expanded})
    except ValueError as exc:
        raise ConfigurationError("conflicting expanded endpoint authentication declarations") from exc


class EndpointRules:
    def __init__(
        self,
        connections: PluginConnections,
        config: Mapping[str, object],
        operation_url: str,
        *,
        host_groups: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        expanded = expand_connections(connections, config, operation_url, host_groups=host_groups or {})
        self.endpoints: dict[str, Endpoint] = {}
        self.requires_session = False
        self._exact: dict[str, ResolvedEndpoint] = {}
        self._wildcards: list[tuple[OriginPattern, ResolvedEndpoint]] = []
        for name, item in expanded.endpoints.items():
            self.endpoints[name] = Endpoint(item.origin)
            self.requires_session |= item.auth == "session"
            policy = endpoint_policy(item, connections.authentication)
            for origin in self.endpoints[name].origins:
                pattern = OriginPattern.parse(origin)
                resolved = ResolvedEndpoint(origin, item.auth, policy)
                if pattern.wildcard:
                    self._wildcards.append((pattern, resolved))
                else:
                    self._exact[origin] = resolved

    def lookup(self, origin: str) -> ResolvedEndpoint | None:
        if origin in self._exact:
            return self._exact[origin]
        target = OriginPattern.parse(origin, allow_wildcard=False)
        if target.origin in self._exact:
            return self._exact[target.origin]
        for pattern, item in self._wildcards:
            if pattern.matches(target):
                return ResolvedEndpoint(target.origin, item.auth, item.policy)
        return None
