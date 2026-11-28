"""User-owned site bindings and command-scoped login consent."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..configuration.auth_origins import AuthOriginError, OriginMatcher, expand_auth_origins
from ..configuration.endpoint_origins import OriginPattern
from ..configuration.host_groups import EMPTY_HOST_GROUPS, HostGroupError
from ..configuration.models import PluginAuthAccess, SiteAuthAccess
from ..diagnostics import configuration_error, host_group_diagnostic
from ..exceptions import LoginApprovalRequiredError


@dataclass(frozen=True, slots=True)
class ResolvedAuthAccess:
    plugin_id: str
    site_id: str
    site: SiteAuthAccess
    matcher: OriginMatcher = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "matcher", OriginMatcher.compile(self.site.origins))

    @property
    def session_key(self) -> tuple[object, ...]:
        return (
            self.plugin_id,
            self.site_id,
            tuple(sorted(self.site.origins)),
            tuple(sorted(self.site.secrets.items())),
            self.site.mode,
        )

    @property
    def key(self) -> tuple[object, ...]:
        return (
            self.plugin_id,
            self.site_id,
            tuple(sorted(self.site.origins)),
            tuple(sorted(self.site.secrets.items())),
            self.site.provides_credentials,
            self.site.login_policy,
        )


def compile_auth_access(
    plugin_id: str, policy: PluginAuthAccess, host_groups: Mapping[str, tuple[str, ...]] = EMPTY_HOST_GROUPS
) -> PluginAuthAccess:
    """Validate effective permissions after layers/operation overrides are merged."""
    if policy.host_groups is not None:
        host_groups = policy.host_groups
    sites: dict[str, SiteAuthAccess] = {}
    seen: list[OriginMatcher] = []
    for name, site in policy.sites.items():
        try:
            origins = expand_auth_origins(site.origins, host_groups)
            matcher = OriginMatcher.compile(origins)
            if any(matcher.overlaps(previous) for previous in seen):
                raise AuthOriginError("overlap")
        except HostGroupError as exc:
            error = host_group_diagnostic(
                exc,
                plugin_id=plugin_id,
                reference_field="auth_access.sites.<site>.origins",
                auth_namespace=policy.host_groups is not None,
            )
            error._core_configuration_path = ("plugin_settings", plugin_id, "auth_access", "sites", name, "origins")
            raise error from exc
        except AuthOriginError as exc:
            error = configuration_error(
                str(exc),
                "auth_origins_" + exc.kind,
                plugin_id=plugin_id,
                field="plugin_settings." + plugin_id + ".auth_access.sites.<site>.origins",
            )
            error._core_configuration_path = ("plugin_settings", plugin_id, "auth_access", "sites", name, "origins")
            raise error from exc
        sites[name] = site.model_copy(update={"origins": origins})
        seen.append(matcher)
    return policy.model_copy(update={"sites": MappingProxyType(sites)})


def resolve_auth_access(
    plugin_id: str,
    url: str,
    policy: PluginAuthAccess,
    *,
    host_groups: Mapping[str, tuple[str, ...]] = EMPTY_HOST_GROUPS,
) -> ResolvedAuthAccess:
    from ..transport.metadata import _request_origin

    origin = OriginPattern.parse(_request_origin(url), allow_wildcard=False).origin
    compiled = compile_auth_access(plugin_id, policy, host_groups)
    for name, site in compiled.sites.items():
        if OriginMatcher.compile(site.origins).matches(origin):
            if "mode" not in site.model_fields_set:
                site = site.model_copy(update={"mode": policy.mode})
            return ResolvedAuthAccess(plugin_id, name, site)
    return ResolvedAuthAccess(plugin_id, origin, SiteAuthAccess(origins=(origin,), mode=policy.mode))


@dataclass(frozen=True, slots=True)
class LoginApprovalRequest:
    plugin_id: str
    site_id: str
    origins: tuple[str, ...]
    provides_credentials: bool
    reason: str


LoginApprover = Callable[[LoginApprovalRequest], Awaitable[bool]]


async def console_login_approver(request: LoginApprovalRequest) -> bool:
    if not sys.stdin.isatty():
        raise LoginApprovalRequiredError(LoginApprovalRequiredError.reason)
    # Do not print control characters or accept terminal instructions from plugin text.
    reason = "".join(c for c in request.reason if c.isprintable())[:300]
    prompt = (
        f"Allow login/renewal for {request.plugin_id} / {request.site_id}?\n"
        f"Allowed origin patterns: {', '.join(request.origins)}\n"
        f"Credentials provided: {'yes' if request.provides_credentials else 'no'}\n"
        f"Reason: {reason}\nAllow for this command? [y/N] "
    )
    try:
        print(prompt, end="", file=sys.stderr, flush=True)
        return input().strip().lower() in {"y", "yes"}
    except EOFError:
        return False


class CommandAuthAccess:
    """Own consent for one service/command, including all workflow attempts."""

    def __init__(self, approver: LoginApprover | None = None) -> None:
        self._approver = approver if approver is not None else console_login_approver
        self._decisions: dict[tuple[object, ...], bool] = {}
        self._lock = asyncio.Lock()

    async def approve(self, access: ResolvedAuthAccess, reason: str) -> bool:
        if access.site.login_policy != "ask":
            return access.site.login_policy == "allow"
        async with self._lock:
            if access.key not in self._decisions:
                decision = await self._approver(
                    LoginApprovalRequest(
                        access.plugin_id,
                        access.site_id,
                        access.site.origins,
                        access.site.provides_credentials,
                        reason,
                    )
                )
                if type(decision) is not bool:
                    raise TypeError("login approver must return bool")
                self._decisions[access.key] = decision
            return self._decisions[access.key]

    def clear(self) -> None:
        self._decisions.clear()
