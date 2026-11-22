"""Operation-local authentication state, leases and shared authentication tasks."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from uuid import uuid4

from ..configuration.models import AuthenticationPolicy, EndpointSettings, PluginConnections, PluginSettings
from ..exceptions import AuthReentryError, AuthScopeError, AuthStateUnknownError, PluginError
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
from .metadata import _request_origin

_AUTH_HOOKS: ContextVar[frozenset[str]] = ContextVar("operation_auth_hooks", default=frozenset())


class OperationAuthSession:
    """One credential generation shared by all requests in an operation."""

    def __init__(
        self,
        operation_url: str,
        settings: PluginSettings | PluginConnections,
        invoker: PluginInvoker,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        settings = PluginConnections.model_validate(
            {"authentication": settings.authentication, "endpoints": settings.endpoints}
        )
        self.operation_id = uuid4().hex
        self.policy = settings.authentication
        self._declared = dict(settings.endpoints)
        self._origin = _request_origin(operation_url)
        if "operation" in self._declared and self._declared["operation"].origin != self._origin:
            raise AuthScopeError("endpoints.operation is reserved for the operation origin")
        self._invoker, self._clock = invoker, clock
        self._policies: dict[str, AuthenticationPolicy] = {}
        self.flow: AuthFlow | None = None
        self.context: AuthContext | None = None
        self.origins: dict[str, EndpointSettings] = {}
        self.endpoints = {name: Endpoint(item.origin) for name, item in self._declared.items()}
        self.endpoints.setdefault("operation", Endpoint(self._origin))
        self.generation = 0
        self._initialized = self._dirty = self._closed = False
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
            and self._initialized
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
        for item in self._declared.values():
            previous = self.origins.get(item.origin)
            if previous is not None and (
                previous.auth != item.auth or self.request_policy(previous) != self.request_policy(item)
            ):
                raise AuthScopeError(f"conflicting authentication declarations for {item.origin}")
            if item.auth == "session" and flow is None:
                raise AuthScopeError(f"{item.origin} declares session authentication but auth_flow returned None")
            self.origins[item.origin] = item
        self.origins.setdefault(
            self._origin,
            EndpointSettings(origin=self._origin, auth="session" if flow is not None else "none"),
        )

    def bind(self, config: Mapping[str, object], secrets: SecretProvider, requests: RequestPort) -> None:
        self.context = AuthContext(config, secrets, self.endpoints, requests)

    @property
    def authenticated_origins(self) -> frozenset[str]:
        return frozenset(origin for origin, item in self.origins.items() if item.auth == "session")

    def resolve(self, spec: RequestSpec) -> EndpointSettings | None:
        self.guard()
        if spec.auth is AuthMode.NONE:
            return None
        if self.flow is None:
            if spec.auth is AuthMode.REQUIRED:
                raise AuthScopeError("REQUIRED request has no authentication flow")
            return None
        item = self.origins.get(_request_origin(spec.url))
        if item is None:
            raise AuthScopeError("request origin is undeclared; add endpoints.<name>.origin/auth or use AuthMode.NONE")
        if item.auth == "none":
            if spec.auth is AuthMode.REQUIRED:
                raise AuthScopeError("REQUIRED request targets an endpoint declared auth: none")
            return None
        return item

    def request_policy(self, endpoint: EndpointSettings) -> AuthenticationPolicy:
        if endpoint.origin in self._policies:
            return self._policies[endpoint.origin]
        overrides = endpoint.verification.model_dump(exclude_unset=True)
        values = self.policy.model_dump()
        if "before_request" in overrides:
            values["verify_before_request"] = overrides["before_request"]
        if "interval_seconds" in overrides:
            values["verify_interval_seconds"] = overrides["interval_seconds"]
        policy = AuthenticationPolicy.model_validate(values)
        self._policies[endpoint.origin] = policy
        return policy

    async def prepare(self, endpoint: EndpointSettings) -> None:
        policy = self.request_policy(endpoint)
        interval = policy.verify_interval_seconds
        check = policy.verify_before_request or (
            interval is not None and (self._verified_at is None or self._clock() - self._verified_at >= interval)
        )
        await self.ensure_ready(check=check)

    async def ensure_ready(self, *, check: bool = False) -> None:
        self.guard()
        if self.flow is None:
            if check:
                raise AuthScopeError("authentication verification requires an AuthFlow")
            return
        serial = self._verification_serial
        while self._task is not None and not self._task.done():
            await asyncio.shield(self._task)
        if self._failure is not None:
            raise self._failure
        check = check and serial == self._verification_serial
        expired = self._expires is not None and self._clock() >= self._expires - self.policy.refresh_skew_seconds
        action = "initialize" if not self._initialized else "refresh" if self._dirty or expired else "verify"
        if action == "verify" and not check:
            return
        check = check or (action == "initialize" and self.policy.verify_on_start)
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

    async def _renew(self, *, initial: bool = False) -> None:
        assert self.flow is not None and self.context is not None
        hook = self._invoker.initialize_auth if initial else self._invoker.refresh_auth
        lease = await hook(self.flow, self.context)
        self._lease(lease)
        self._public_data = lease.public_data if lease.public_data is not None else {}
        self._last_verified_status = AuthStatus.UNKNOWN
        self.generation += 1
        self._initialized, self._dirty = True, False
        self._verified_at = None

    async def _verify(self) -> bool:
        assert self.flow is not None and self.context is not None
        result = await self._invoker.verify_auth(self.flow, self.context)
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
                await self._renew(initial=action == "initialize")
            if check and not await self._verify() and action != "check_response":
                await self._renew()
        except BaseException as exc:
            if not isinstance(exc, asyncio.CancelledError):
                self._failure = exc
            raise
        finally:
            _AUTH_HOOKS.reset(token)

    def apply(self, spec: RequestSpec) -> tuple[RequestSpec, AuthReceipt]:
        assert self.flow is not None
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
