from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from support.auth import approved_access

from image_downloader import (
    AuthContext,
    AuthDecision,
    AuthenticationError,
    AuthLease,
    AuthMode,
    AuthReentryError,
    AuthScopeError,
    AuthStateUnknownError,
    AuthStatus,
    AuthVerification,
    BaseAuthFlow,
    HttpStatusError,
    RequestSpec,
)
from image_downloader.application.workflow_retry import retryable
from image_downloader.config import AppConfig
from image_downloader.configuration.models import PluginSettings
from image_downloader.transport.gateway import RequestGateway


class CountingFlow(BaseAuthFlow):
    def __init__(self):
        self.initialized = self.refreshed = self.verified = 0
        self.status = AuthStatus.VALID
        self.lease = AuthLease()

    async def initialize(self, auth_context):
        self.initialized += 1
        return self.lease

    async def refresh(self, auth_context):
        self.refreshed += 1
        return self.lease

    async def verify(self, auth_context):
        self.verified += 1
        return AuthVerification(self.status)

    def apply(self, request):
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.refreshed}"})


def operation(flow, *, handler=None, settings=None, attempts=1):
    handler = handler or (lambda request: httpx.Response(200))
    gateway = RequestGateway(
        AppConfig.model_validate({"network": {"auth_refresh_attempts": attempts}}),
        transport_factory=lambda: httpx.MockTransport(handler),
    )
    session = gateway.operation(
        plugin_id="com.example.auth",
        operation_url="https://site.test/gallery",
        auth_flow_factory=lambda _: flow,
        settings=PluginSettings.model_validate(settings or {}),
        access=approved_access(
            origins=tuple(
                item["origin"]
                for item in (settings or {}).get("endpoints", {}).values()
                if isinstance(item["origin"], str)
            )
        ),
    )
    return gateway, session


@pytest.mark.parametrize("status", [401, 403])
def test_public_site_does_not_run_authentication(status):
    async def scenario():
        gateway, session = operation(None, handler=lambda request: httpx.Response(status))
        try:
            with pytest.raises(HttpStatusError) as error:
                await session.execute(RequestSpec("https://site.test/file"))
            assert error.value.status == status
            with pytest.raises(AuthScopeError):
                await session.execute(RequestSpec("https://site.test/file", auth=AuthMode.REQUIRED))
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("trigger", ["required", "explicit"])
def test_verify_on_start_is_deferred_until_login_when_initialize_has_no_lease(trigger):
    class Flow(BaseAuthFlow):
        initialized = logged_in = verified = 0

        async def initialize(self, context):
            self.initialized += 1
            return None

        async def login(self, context):
            self.logged_in += 1
            return AuthLease()

        async def verify(self, context):
            self.verified += 1
            return AuthVerification(AuthStatus.VALID)

    async def scenario():
        flow = Flow()
        gateway, session = operation(flow, settings={"authentication": {"verify_on_start": True}})
        try:
            if trigger == "explicit":
                assert await session.auth.request_login("protected content")
            else:
                await session.execute(RequestSpec("https://site.test/work", auth=AuthMode.REQUIRED))
            await session.auth.ensure_ready()
            assert (flow.initialized, flow.logged_in, flow.verified) == (1, 1, 1)
            assert session.auth.snapshot().last_verified_status is AuthStatus.VALID
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "policy,verified",
    [({}, 0), ({"verify_on_start": True}, 1), ({"verify_before_request": True}, 3)],
)
def test_default_initialization_and_declared_verification_frequency(policy, verified):
    async def scenario():
        flow = CountingFlow()
        gateway, session = operation(flow, settings={"authentication": policy})
        try:
            for _ in range(3):
                await session.execute(RequestSpec("https://site.test/file"))
            assert (flow.initialized, flow.refreshed, flow.verified) == (1, 0, verified)
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_interval_endpoint_override_expiry_and_explicit_verification():
    async def scenario():
        flow = CountingFlow()
        flow.lease = AuthLease(100)
        gateway, session = operation(
            flow,
            settings={
                "authentication": {"verify_interval_seconds": 10},
                "endpoints": {
                    "api": {"origin": "https://api.test", "auth": "session", "verification": {"before_request": True}}
                },
            },
        )
        now = [0.0]
        session.auth._clock = lambda: now[0]
        try:
            await session.execute(RequestSpec("https://site.test/file"))
            now[0] = 9
            await session.execute(RequestSpec("https://site.test/file"))
            assert flow.verified == 1
            now[0] = 10
            await session.execute(RequestSpec("https://site.test/file"))
            await session.execute(session.endpoints["api"].request("/one"))
            await session.execute(session.endpoints["api"].request("/two"))
            await session.auth.ensure_ready(check=True)
            assert flow.verified == 5
            now[0] = 100
            await session.execute(RequestSpec("https://site.test/file"))
            assert flow.refreshed == 1
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [AuthStatus.VALID, AuthStatus.INVALID, AuthStatus.UNKNOWN])
def test_unknown_response_requires_fresh_verification(status):
    class Flow(CountingFlow):
        def classify_response(self, request, response):
            return AuthDecision.UNKNOWN if not self.refreshed else AuthDecision.NOT_AUTH_FAILURE

    async def scenario():
        flow = Flow()
        flow.status = status
        gateway, session = operation(flow)
        try:
            if status is AuthStatus.UNKNOWN:
                with pytest.raises(AuthStateUnknownError):
                    await session.execute(RequestSpec("https://site.test/file"))
            else:
                await session.execute(RequestSpec("https://site.test/file"))
                if status is AuthStatus.VALID:
                    await session.execute(RequestSpec("https://site.test/file"))
                    assert flow.verified == 2
            assert flow.refreshed == (1 if status is AuthStatus.INVALID else 0)
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_unknown_response_cannot_refresh_past_the_request_budget():
    class Flow(CountingFlow):
        def classify_response(self, request, response):
            return AuthDecision.UNKNOWN

    async def scenario():
        flow = Flow()
        flow.status = AuthStatus.INVALID
        gateway, session = operation(flow, attempts=0)
        try:
            with pytest.raises(AuthenticationError, match="configured refresh attempts"):
                await session.execute(RequestSpec("https://site.test/file"))
            assert flow.refreshed == 0
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_permanent_auth_failure_is_bounded_and_original_headers_are_rebuilt():
    async def scenario():
        seen = []

        def handler(request):
            seen.append(request.headers["Authorization"])
            return httpx.Response(401)

        flow = CountingFlow()
        gateway, session = operation(flow, handler=handler, attempts=2)
        try:
            with pytest.raises(AuthenticationError, match="configured refresh attempts"):
                await session.execute(RequestSpec("https://site.test/file"))
            assert seen == ["Bearer 0", "Bearer 1", "Bearer 2"]
            assert flow.refreshed == 2
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("allow", [False, True])
def test_non_idempotent_replay_requires_opt_in(allow):
    async def scenario():
        flow = CountingFlow()
        gateway, session = operation(
            flow,
            handler=lambda request: httpx.Response(200 if flow.refreshed else 401),
        )
        try:
            request = RequestSpec("https://site.test/action", method="POST", retry_non_idempotent=allow)
            if allow:
                assert (await session.execute(request)).status == 200
            else:
                with pytest.raises(AuthenticationError, match="retry_non_idempotent"):
                    await session.execute(request)
            assert flow.refreshed == int(allow)
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_dedicated_auth_requests_return_errors_and_share_cookies_without_reentry():
    class Flow(CountingFlow):
        async def initialize(self, auth_context):
            return None

        async def login(self, auth_context: AuthContext):
            response = await auth_context.requests.execute(RequestSpec("https://site.test/login"))
            assert response.status == 401
            assert response.body == b"denied"
            return await super().initialize(auth_context)

    async def scenario():
        def handler(request):
            if request.url.path == "/login":
                assert "authorization" not in request.headers
                return httpx.Response(401, content=b"denied", headers={"set-cookie": "csrf=value; Path=/"})
            assert request.headers["Cookie"] == "csrf=value"
            return httpx.Response(200)

        flow = Flow()
        gateway, session = operation(flow, handler=handler)
        try:
            await asyncio.wait_for(session.execute(RequestSpec("https://site.test/file", auth=AuthMode.REQUIRED)), 2)
            assert flow.refreshed == 0
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("hook", ["initialize", "verify", "refresh"])
def test_normal_requests_inside_auth_hooks_fail_fast(hook):
    async def scenario():
        flow = CountingFlow()
        gateway, session = operation(flow)

        async def reenter(auth_context):
            await session.execute(RequestSpec("https://site.test/login", auth=AuthMode.NONE))

        setattr(flow, hook, reenter)
        try:
            if hook != "initialize":
                response = await session.execute(RequestSpec("https://site.test/file"))
                if hook == "refresh":
                    session.auth.invalidate(response)
            with pytest.raises(AuthReentryError):
                await asyncio.wait_for(session.auth.ensure_ready(check=hook == "verify"), 2)
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_concurrent_initialization_and_refresh_are_shared_and_receipts_reject_stale_invalidations():
    class Flow(CountingFlow):
        async def initialize(self, auth_context):
            await asyncio.sleep(0)
            return await super().initialize(auth_context)

        async def refresh(self, auth_context):
            await asyncio.sleep(0)
            return await super().refresh(auth_context)

    async def scenario():
        flow = Flow()
        gateway, session = operation(flow)
        try:
            responses = await asyncio.gather(
                *(session.execute(RequestSpec("https://site.test/file")) for _ in range(12))
            )
            assert flow.initialized == 1
            assert session.auth.invalidate(responses[0]) is True
            await asyncio.gather(*(session.execute(RequestSpec("https://site.test/file")) for _ in range(12)))
            assert flow.refreshed == 1
            assert session.auth.invalidate(responses[-1]) is False
            other = gateway.operation(plugin_id=None, operation_url="https://site.test/other")
            with pytest.raises(AuthScopeError):
                other.auth.invalidate(responses[0])
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_cancelling_a_waiter_keeps_initialization_alive_and_close_collects_it():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Flow(CountingFlow):
            async def initialize(self, auth_context):
                entered.set()
                await release.wait()
                return await super().initialize(auth_context)

        flow = Flow()
        gateway, session = operation(flow)
        try:
            first = asyncio.create_task(session.execute(RequestSpec("https://site.test/file")))
            await entered.wait()
            second = asyncio.create_task(session.execute(RequestSpec("https://site.test/file")))
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            await asyncio.wait_for(second, 2)
            assert flow.initialized == 1
            session.auth.invalidate(await session.execute(RequestSpec("https://site.test/file")))
            task = asyncio.create_task(session.auth.ensure_ready())
            await task
        finally:
            await gateway.close()
        assert session.auth._task is None

    asyncio.run(scenario())


def test_failed_initialization_is_shared_without_repeated_logins():
    class Flow(CountingFlow):
        async def initialize(self, auth_context):
            self.initialized += 1
            await asyncio.sleep(0)
            raise AuthenticationError("login rejected")

    async def scenario():
        flow = Flow()
        gateway, session = operation(flow)
        try:
            errors = await asyncio.gather(
                *(session.execute(RequestSpec("https://site.test/file")) for _ in range(8)),
                return_exceptions=True,
            )
            assert all(isinstance(error, AuthenticationError) for error in errors)
            with pytest.raises(AuthenticationError):
                await session.execute(RequestSpec("https://site.test/file"))
            assert flow.initialized == 1
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_endpoint_scope_and_public_endpoint_do_not_leak_bearer_tokens():
    async def scenario():
        seen = []

        def handler(request):
            seen.append((request.url.host, request.headers.get("Authorization")))
            return httpx.Response(200)

        flow = CountingFlow()
        gateway, session = operation(
            flow,
            handler=handler,
            settings={
                "endpoints": {
                    "api": {"origin": "https://api.test", "auth": "session"},
                    "cdn": {"origin": "https://cdn.test", "auth": "none"},
                }
            },
        )
        try:
            await session.execute(session.endpoints["api"].request("/one"))
            await session.execute(session.endpoints["cdn"].request("/two"))
            assert seen == [("api.test", "Bearer 0"), ("cdn.test", None)]
            with pytest.raises(AuthScopeError):
                await session.execute(RequestSpec("https://unknown.test/file"))
            with pytest.raises(AuthReentryError):
                await session.auth.context.requests.execute(RequestSpec("https://unknown.test/login"))
            with pytest.raises(ValueError):
                session.endpoints["api"].request("//unknown.test/file")
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["auth_scope_error", "auth_reentry_error", "auth_state_unknown"])
def test_authentication_contract_errors_are_not_workflow_retryable(code):
    assert retryable(code) is False


def test_inflight_verification_is_shared_but_later_requests_verify_again():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Flow(CountingFlow):
            async def verify(self, auth_context):
                entered.set()
                await release.wait()
                return await super().verify(auth_context)

        flow = Flow()
        gateway, session = operation(flow, settings={"authentication": {"verify_before_request": True}})
        try:
            first = asyncio.create_task(session.execute(RequestSpec("https://site.test/one")))
            await entered.wait()
            second = asyncio.create_task(session.execute(RequestSpec("https://site.test/two")))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(first, second)
            assert flow.verified == 1
            await session.execute(RequestSpec("https://site.test/three"))
            assert flow.verified == 2
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_close_cancels_a_pending_authentication_task():
    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        class Flow(CountingFlow):
            async def initialize(self, auth_context):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        gateway, session = operation(Flow())
        waiter = asyncio.create_task(session.execute(RequestSpec("https://site.test/file")))
        await entered.wait()
        await gateway.close()
        assert cancelled.is_set()
        with pytest.raises(asyncio.CancelledError):
            await waiter

    asyncio.run(scenario())


def test_non_authentication_403_reaches_image_recovery():
    class Flow(CountingFlow):
        def classify_response(self, request, response):
            return AuthDecision.NOT_AUTH_FAILURE

    async def scenario():
        flow = Flow()
        gateway, session = operation(
            flow,
            handler=lambda request: httpx.Response(403 if request.url.path == "/expired" else 200),
        )

        async def recover(request, response):
            assert response.status == 403
            return RequestSpec("https://site.test/fresh")

        try:
            result = await session._execute(RequestSpec("https://site.test/expired"), recover=recover)
            assert result.url == "https://site.test/fresh"
            assert flow.refreshed == flow.verified == 0
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_declared_authenticated_redirect_forwards_bearer_but_blocks_further_origins():
    async def scenario():
        seen = []

        def handler(request):
            seen.append((request.url.host, request.headers.get("Authorization")))
            target = "https://api.test/file" if request.url.host == "site.test" else "https://unknown.test/file"
            return httpx.Response(302, headers={"location": target})

        gateway, session = operation(
            CountingFlow(),
            handler=handler,
            settings={
                "endpoints": {
                    "api": {"origin": "https://api.test", "auth": "session"},
                }
            },
        )
        try:
            with pytest.raises(AuthScopeError):
                await session.execute(RequestSpec("https://site.test/file"))
            assert seen == [("site.test", "Bearer 0"), ("api.test", "Bearer 0")]
        finally:
            await gateway.close()

    asyncio.run(scenario())
