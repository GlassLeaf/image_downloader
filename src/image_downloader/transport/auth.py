"""Operation-local authentication state, leases and shared authentication tasks."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

from ..configuration.auth_origins import OriginMatcher
from ..configuration.models import PluginAuthAccess, PluginConnections, PluginSettings
from ..credentials.access import CommandAuthAccess, ResolvedAuthAccess, resolve_auth_access
from ..diagnostics import auth_scope_error
from ..exceptions import (
    AuthReentryError,
    AuthScopeError,
    AuthStateUnknownError,
    CredentialAccessDeniedError,
    LoginDeniedError,
    PluginError,
)
from ..models import (
    AuthDecision,
    AuthLease,
    AuthMode,
    AuthReceipt,
    AuthSnapshot,
    AuthStatus,
    RequestResponse,
    RequestSpec,
)
from ..plugins.plugin_invoker import PluginInvoker
from ..ports import AuthContext, AuthFlow, Endpoint, RequestPort, SecretProvider, is_auth_flow
from .endpoint_rules import EndpointRules, ResolvedEndpoint
from .metadata import _request_origin

_AUTH_HOOKS: ContextVar[frozenset[str]] = ContextVar("operation_auth_hooks", default=frozenset())


@dataclass(slots=True)
class _AuthPhase:
    name: str
    active: bool = True


class OperationAuthSession:
    """One credential generation shared by all requests in an operation."""

    def __init__(
        self,
        operation_url: str,
        settings: PluginSettings | PluginConnections,
        invoker: PluginInvoker,
        *,
        plugin_config: Mapping[str, object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        access: ResolvedAuthAccess | None = None,
        consent: CommandAuthAccess | None = None,
        host_groups: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        config = (
            plugin_config
            if plugin_config is not None
            else settings.config
            if isinstance(settings, PluginSettings)
            else {}
        )
        user_access = settings.auth_access if isinstance(settings, PluginSettings) else PluginAuthAccess()
        user_hosts = settings.host_groups if isinstance(settings, PluginSettings) else host_groups or {}
        settings = PluginConnections.model_validate(
            {"authentication": settings.authentication, "endpoints": settings.endpoints}
        )
        self.operation_id = uuid4().hex
        self.access = access or resolve_auth_access(
            invoker.plugin_id, operation_url, user_access, host_groups=user_hosts
        )
        self.consent = consent or CommandAuthAccess()
        self._phase: ContextVar[_AuthPhase | None] = ContextVar("auth_phase", default=None)
        self.policy = settings.authentication
        self._verify_on_start_pending = self.policy.verify_on_start
        self._origin = _request_origin(operation_url)
        self.rules = EndpointRules(settings, config, operation_url, host_groups=user_hosts)
        self.cookie_origins = self.access.matcher.intersection(
            OriginMatcher.compile(
                (self._origin, *(origin for endpoint in self.rules.endpoints.values() for origin in endpoint.origins))
            )
        )
        if "operation" in self.rules.endpoints and self.rules.endpoints["operation"].origins != (self._origin,):
            raise AuthScopeError("endpoints.operation is reserved for the operation origin")
        self._invoker, self._clock = invoker, clock
        self.flow: AuthFlow | None = None
        self.context: AuthContext | None = None
        self.endpoints = dict(self.rules.endpoints)
        self.endpoints.setdefault("operation", Endpoint(self._origin))
        self.generation = 0
        self._initialized = self._dirty = self._closed = False
        self._ready = False
        self._decision_lock = asyncio.Lock()
        self._login_serial = 0
        self._expires: float | None = None
        self._verified_at: float | None = None
        self._verification_serial = 0
        self._failure: BaseException | None = None
        self._public_data: Mapping[str, object] = {}
        self._last_verified_status = AuthStatus.UNKNOWN
        self._task: asyncio.Task[None] | None = None

    def snapshot(self) -> AuthSnapshot:
        """Read current state without running authentication hooks or sending requests."""
        now = self._clock()
        remaining = max(0.0, self._expires - now) if self._expires is not None else None
        renewal_due = self._expires is not None and now >= self._expires - self.policy.refresh_skew_seconds
        ready = bool(
            self.flow is not None
            and self._ready
            and not (self._dirty or self._closed or self._failure is not None or renewal_due)
        )
        return AuthSnapshot(
            flow_configured=self.flow is not None,
            credentials_ready=ready,
            last_verified_status=self._last_verified_status,
            generation=self.generation,
            remaining_seconds=remaining,
            public_data=self._public_data if ready else {},
        )

    def guard(self) -> None:
        if self.operation_id in _AUTH_HOOKS.get():
            raise AuthReentryError("authentication hooks must use AuthContext.requests, not context.requests/auth")
        if self._closed:
            raise PluginError("authentication operation is closed")

    def configure(self, flow: object | None) -> None:
        if flow is not None and not is_auth_flow(flow):
            raise PluginError(
                "auth_flow must return the new AuthFlow contract or None: initialize, synchronous apply, "
                "classify_response, verify, refresh(auth_context); remove is_auth_failure/allowed_origins"
            )
        self.flow = flow
        if self.rules.requires_session and flow is None:
            raise AuthScopeError("endpoint declares session authentication but auth_flow returned None")

    def bind(self, config: Mapping[str, object], secrets: SecretProvider, requests: RequestPort) -> None:
        self.context = AuthContext(config, secrets, self.endpoints, requests)

    def check_credentials(self) -> None:
        phase = self._phase.get()
        if (
            self._closed
            or phase is None
            or not phase.active
            or phase.name not in {"initialize", "login", "refresh", "verify"}
        ):
            raise AuthReentryError("credentials are available only inside asynchronous authentication hooks")

    def check_auth_request(self) -> None:
        phase = self._phase.get()
        if self._closed or phase is None or not phase.active or phase.name not in {"login", "refresh", "verify"}:
            raise AuthReentryError("initialize must not send requests; use login/refresh or verify")

    def lookup(self, origin: str) -> ResolvedEndpoint | None:
        item = self.rules.lookup(origin)
        if item is None and origin == self._origin:
            return ResolvedEndpoint(origin, "session" if self.flow is not None else "none", self.policy)
        return item

    def allows_authenticated_origin(self, origin: str) -> bool:
        item = self.lookup(origin)
        return item is not None and item.auth == "session" and self.access.matcher.matches(origin)

    def allows_declared_origin(self, origin: str) -> bool:
        return self.lookup(origin) is not None and self.access.matcher.matches(origin)

    def scope_error(self, kind: str) -> AuthScopeError:
        user = kind == "user_origin_denied"
        return auth_scope_error(
            "request origin is outside the user's auth_access site group"
            if user
            else "request origin is undeclared or not declared for session authentication",
            kind,
            plugin_id=self.access.plugin_id,
            field="auth_access.sites.<site>.origins" if user else "endpoints.<endpoint>.origin/auth",
        )

    def require_declared_origin(self, origin: str) -> bool:
        if self.lookup(origin) is None:
            raise self.scope_error("author_origin_undeclared")
        if not self.access.matcher.matches(origin):
            raise self.scope_error("user_origin_denied")
        return True

    def require_authenticated_origin(self, origin: str) -> bool:
        item = self.lookup(origin)
        if item is None or item.auth != "session":
            raise self.scope_error("author_origin_undeclared")
        return self.require_declared_origin(origin)

    def resolve(self, spec: RequestSpec) -> ResolvedEndpoint | None:
        self.guard()
        if spec.auth is AuthMode.NONE:
            return None
        if self.flow is None:
            if spec.auth is AuthMode.REQUIRED:
                raise AuthScopeError("REQUIRED request has no authentication flow")
            return None
        item = self.lookup(_request_origin(spec.url))
        if item is None:
            raise self.scope_error("author_origin_undeclared")
        if item.auth == "none":
            if spec.auth is AuthMode.REQUIRED:
                raise AuthScopeError("REQUIRED request targets an endpoint declared auth: none")
            return None
        if not self.access.matcher.matches(_request_origin(spec.url)):
            raise self.scope_error("user_origin_denied")
        return item

    async def prepare(self, endpoint: ResolvedEndpoint, *, required: bool = False) -> None:
        policy = endpoint.policy
        interval = policy.verify_interval_seconds
        check = policy.verify_before_request or (
            interval is not None and (self._verified_at is None or self._clock() - self._verified_at >= interval)
        )
        await self.ensure_ready(check=check, required=required)

    async def ensure_ready(self, *, check: bool = False, required: bool = True) -> None:
        self.guard()
        serial = self._verification_serial
        async with self._decision_lock:
            await self._ensure_ready(check=check, required=required, serial=serial)

    async def _ensure_ready(self, *, check: bool, required: bool, serial: int) -> None:
        self.guard()
        if self.flow is None:
            if check:
                raise AuthScopeError("authentication verification requires an AuthFlow")
            return
        while self._task is not None and not self._task.done():
            await asyncio.shield(self._task)
        if self._failure is not None:
            raise self._failure
        if not self._initialized:
            await self._start("initialize", check or self.policy.verify_on_start)
        check = check and serial == self._verification_serial
        expired = self._expires is not None and self._clock() >= self._expires - self.policy.refresh_skew_seconds
        action = "refresh" if self._ready else "login"
        if self._dirty or expired or (required and not self._ready):
            await self._start(action, check)
            return
        if self._ready and check:
            await self._start("verify", True)

    async def request_login(self, reason: str) -> bool:
        self.guard()
        serial = self._login_serial
        if not isinstance(reason, str):
            raise TypeError("login reason must be a string")
        if not await self.consent.approve(self.access, reason):
            return False
        if self.flow is None:
            raise AuthScopeError("login requires an AuthFlow")
        await self.ensure_ready(required=False)
        async with self._decision_lock:
            while self._task is not None and not self._task.done():
                await asyncio.shield(self._task)
            if self._failure is not None:
                raise self._failure
            if self._login_serial == serial:
                await self._start("login", False)
        return True

    async def _start(self, action: str, check: bool) -> None:
        self._task = asyncio.create_task(self._run(action, check))
        self._task.add_done_callback(self._observe_task)
        await asyncio.shield(self._task)

    @staticmethod
    def _observe_task(task: asyncio.Task[None]) -> None:
        # The only waiter can be cancelled; still retrieve an eventual failure.
        if not task.cancelled():
            task.exception()

    def _lease(self, lease: AuthLease) -> None:
        self._expires = None if lease.expires_in_seconds is None else self._clock() + lease.expires_in_seconds

    async def _renew(self, *, action: str = "refresh") -> None:
        assert self.flow is not None and self.context is not None
        if action != "initialize" and not await self.consent.approve(self.access, f"authentication {action} required"):
            raise LoginDeniedError(LoginDeniedError.reason)
        hook = {
            "initialize": self._invoker.initialize_auth,
            "login": self._invoker.login_auth,
            "refresh": self._invoker.refresh_auth,
        }[action]
        capability = _AuthPhase(action)
        phase = self._phase.set(capability)
        try:
            try:
                lease = await hook(self.flow, self.context)
            except CredentialAccessDeniedError:
                if action != "initialize":
                    raise
                lease = None
        finally:
            capability.active = False
            self._phase.reset(phase)
        self._initialized = True
        if lease is None:
            self._ready = False
            return
        self._lease(lease)
        self._public_data = lease.public_data if lease.public_data is not None else {}
        self._last_verified_status = AuthStatus.UNKNOWN
        self.generation += 1
        if action != "initialize":
            self._login_serial += 1
        self._ready, self._dirty = True, False
        self._verified_at = None

    async def _verify(self) -> bool:
        assert self.flow is not None and self.context is not None
        capability = _AuthPhase("verify")
        phase = self._phase.set(capability)
        try:
            result = await self._invoker.verify_auth(self.flow, self.context)
        finally:
            capability.active = False
            self._phase.reset(phase)
        self._verification_serial += 1
        self._last_verified_status = result.status
        if result.status is AuthStatus.UNKNOWN:
            raise AuthStateUnknownError("verify returned UNKNOWN; implement a verifier or adjust the site policy")
        if result.status is AuthStatus.INVALID:
            self._dirty = True
            return False
        self._verified_at = self._clock()
        if result.lease is not None:
            self._lease(result.lease)
            if result.lease.public_data is not None:
                self._public_data = result.lease.public_data
        return True

    async def _run(self, action: str, check: bool) -> None:
        token = _AUTH_HOOKS.set(_AUTH_HOOKS.get() | {self.operation_id})
        try:
            if action not in {"verify", "check_response"}:
                await self._renew(action=action)
            check = check or (self._ready and self._verify_on_start_pending)
            if check and (self._ready or action == "check_response"):
                if self._ready:
                    self._verify_on_start_pending = False
                if not await self._verify() and action != "check_response":
                    await self._renew(action="refresh" if self._ready else "login")
        except BaseException as exc:
            if not isinstance(exc, asyncio.CancelledError):
                self._failure = exc
            raise
        finally:
            _AUTH_HOOKS.reset(token)

    def apply(self, spec: RequestSpec) -> tuple[RequestSpec, AuthReceipt]:
        assert self.flow is not None
        if not self._ready:
            return spec, AuthReceipt(self.operation_id, self.generation)
        token = _AUTH_HOOKS.set(_AUTH_HOOKS.get() | {self.operation_id})
        try:
            result = self._invoker.apply_auth(self.flow, spec)
        finally:
            _AUTH_HOOKS.reset(token)
        if _request_origin(result.url) != _request_origin(spec.url) or result.auth is not spec.auth:
            raise AuthScopeError("AuthFlow.apply must not change the request origin or authentication mode")
        return result, AuthReceipt(self.operation_id, self.generation)

    def classify(self, request: RequestSpec, response: RequestResponse) -> AuthDecision:
        assert self.flow is not None
        token = _AUTH_HOOKS.set(_AUTH_HOOKS.get() | {self.operation_id})
        try:
            return self._invoker.classify_auth(self.flow, request, response)
        finally:
            _AUTH_HOOKS.reset(token)

    def invalidate(self, response: RequestResponse) -> bool:
        self.guard()
        receipt = response.auth_receipt
        if receipt is None or receipt.operation_id != self.operation_id:
            raise AuthScopeError("response has no authentication receipt for this operation")
        if receipt.generation != self.generation:
            return False
        self._dirty = True
        return True

    async def response_failed(self, request: RequestSpec, response: RequestResponse) -> bool:
        decision = self.classify(request, response)
        if decision is AuthDecision.NOT_AUTH_FAILURE:
            return False
        receipt = response.auth_receipt
        assert receipt is not None
        if receipt.generation != self.generation:
            return True
        if decision is AuthDecision.UNKNOWN:
            while self._task is not None and not self._task.done():
                serial = self._verification_serial
                await asyncio.shield(self._task)
                if self.generation != receipt.generation or self._dirty:
                    return True
                if serial != self._verification_serial:
                    return False
            if self._failure is not None:
                raise self._failure
            if self._dirty:
                return True
            self._task = asyncio.create_task(self._run("check_response", True))
            self._task.add_done_callback(self._observe_task)
            await asyncio.shield(self._task)
            return self._dirty or self.generation != receipt.generation
        self.invalidate(response)
        return True

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            if not self._task.done():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.flow = self.context = None
        self._public_data = {}
        self._failure = None
        self._task = None
        self._ready = False
