from __future__ import annotations

import asyncio
from dataclasses import replace
from http.cookiejar import CookieJar
from unittest.mock import AsyncMock

import httpx
import pytest
from support.vault import MemoryKeys

from image_downloader import (
    AppConfig,
    AuthLease,
    AuthMode,
    AuthReentryError,
    AuthScopeError,
    AuthStatus,
    AuthVerification,
    BaseAuthFlow,
    LoginDeniedError,
    RequestSpec,
    RuntimeComposer,
)
from image_downloader.credentials.keyring import MasterKeyStore
from image_downloader.storage.cookies import CookieStore


@pytest.fixture(autouse=True)
def memory_keys(monkeypatch):
    keys = MemoryKeys()
    monkeypatch.setattr(MasterKeyStore, "get", lambda self, v, k: keys.get(v, k))
    monkeypatch.setattr(MasterKeyStore, "create", lambda self, v, k, value: keys.create(v, k, value))


def service(tmp_path, monkeypatch, sites, handler, approver=None, flow_class=None, host_groups=None):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir(exist_ok=True)
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(plugin_root)},
            "notification": {"enabled": False},
            "logging": {"console": {"enabled": False}},
            "network": {"max_attempts": 1},
            "plugin_settings": {
                "core.generic-html": {"host_groups": host_groups or {}, "auth_access": {"sites": sites}}
            },
        }
    )
    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **kw: client(*a, **{**kw, "transport": httpx.MockTransport(handler)})
    )
    runtime = RuntimeComposer(config, config_root=tmp_path, plugin_root=plugin_root, login_approver=approver).compose()

    class LoginFlow(BaseAuthFlow):
        async def login(self, context):
            value = context.credentials.get("token")
            response = await context.requests.execute(
                RequestSpec(
                    context.endpoints["operation"].origin + "/login",
                    method="POST",
                    form={"token": value},
                )
            )
            assert response.status == 200
            return AuthLease()

    class Plugin:
        def auth_flow(self, context):
            assert not hasattr(context, "secrets")
            return (flow_class or LoginFlow)()

    record = runtime.registry.records["core.generic-html"]
    runtime.registry.resolve = lambda *a, **kw: (record, Plugin())
    return runtime


def test_anonymous_login_cookie_redirect_and_reuse_never_touch_saved_cookies(tmp_path, monkeypatch):
    async def scenario():
        observed = []

        def handler(request):
            observed.append(request)
            if request.url.path == "/login":
                assert request.content == b"token=canary"
                return httpx.Response(302, headers={"set-cookie": "session=yes; Path=/", "location": "/welcome"})
            return httpx.Response(200 if request.headers.get("cookie") == "session=yes" else 401)

        sites = {"a": {"origins": ["https://a.test"], "mode": "anonymous", "secrets": {"token": "A"}}}
        approver = AsyncMock(return_value=True)
        monkeypatch.setattr(CookieStore, "load", lambda self: pytest.fail("anonymous loaded saved cookies"))
        monkeypatch.setattr(CookieStore, "persist_delta", lambda *a: pytest.fail("anonymous persisted cookies"))
        runtime = service(tmp_path, monkeypatch, sites, handler, approver)
        runtime._operations.credentials.set_plugin("core.generic-html", "A", "canary")
        for _ in range(2):
            async with runtime._operations.open("https://a.test/work") as site:
                assert (await site.context.requests.execute(RequestSpec("https://a.test/work"))).status == 200
                assert site.context.get_cookies(domain="a.test", path="/") == {"session": "yes"}
        assert [r.url.path for r in observed] == ["/work", "/login", "/welcome", "/work", "/work"]
        assert approver.await_count == 1
        gateway = next(iter(runtime._operations._anonymous.values()))
        await runtime.close()
        assert not list(gateway.client.cookies.jar)
        assert not (tmp_path / "data/profiles/default/cookie/cookies.v2.enc").exists()
        runtime = service(tmp_path, monkeypatch, sites, handler, approver)
        async with runtime._operations.open("https://a.test/work") as site:
            await site.context.requests.execute(RequestSpec("https://a.test/work"))
        await runtime.close()
        assert approver.await_count == 2

    asyncio.run(scenario())


def test_normal_and_anonymous_changes_are_separate_and_limits_are_shared(tmp_path, monkeypatch):
    async def scenario():
        persisted = []
        monkeypatch.setattr(CookieStore, "load", lambda self: CookieJar())
        monkeypatch.setattr(
            CookieStore, "persist_delta", lambda self, baseline, jar: persisted.extend(c.value for c in jar)
        )
        runtime = service(
            tmp_path,
            monkeypatch,
            {
                "a": {"origins": ["https://a.test"], "mode": "anonymous"},
                "b": {"origins": ["https://b.test"]},
            },
            lambda r: httpx.Response(200, headers={"set-cookie": f"session={r.url.host}; Path=/"}),
        )
        async with runtime._operations.open("https://a.test") as a:
            await a.context.requests.execute(RequestSpec("https://a.test"))
            assert a.gateway._shared._global is runtime.gateway._global
            assert a.gateway._shared._connections is runtime.gateway._connections
        async with runtime._operations.open("https://b.test") as b:
            await b.context.requests.execute(RequestSpec("https://b.test"))
            assert b.context.get_cookies() == {"b.test": {"/": {"session": "b.test"}}}
        await runtime.close()
        assert persisted == ["b.test"]

    asyncio.run(scenario())


@pytest.mark.parametrize("templates", [False, True])
def test_site_credentials_and_consents_do_not_cross_bindings(tmp_path, monkeypatch, templates):
    async def scenario():
        def handler(r):
            if r.url.path == "/login":
                assert r.content == (b"token=A-canary" if r.url.host == "a.test" else b"token=B-canary")
                return httpx.Response(200, headers={"set-cookie": "session=yes; Path=/"})
            return httpx.Response(200 if "session=yes" in r.headers.get("cookie", "") else 401)

        approver = AsyncMock(return_value=True)
        runtime = service(
            tmp_path,
            monkeypatch,
            {
                name: {
                    "origins": [
                        ("https://" + "$" + "{host_groups." + name + "}") if templates else f"https://{name}.test"
                    ],
                    "mode": "anonymous",
                    "secrets": {"token": name.upper()},
                }
                for name in ("a", "b")
            },
            handler,
            approver,
            host_groups={name: [name + ".test"] for name in ("a", "b")} if templates else None,
        )
        for ref in ("A", "B"):
            runtime._operations.credentials.set_plugin("core.generic-html", ref, ref + "-canary")

        async def execute(name):
            async with runtime._operations.open(f"https://{name}.test") as site:
                await site.context.requests.execute(RequestSpec(f"https://{name}.test"))

        await asyncio.gather(execute("a"), execute("b"))
        assert approver.await_count == 2
        await runtime.close()

    asyncio.run(scenario())


def test_delivery_denial_never_reads_vault_and_login_denial_never_runs_hook(tmp_path, monkeypatch):
    async def scenario():
        class Flow(BaseAuthFlow):
            async def initialize(self, context):
                context.credentials.get("token")

            async def login(self, context):
                pytest.fail("denied login executed")

        runtime = service(
            tmp_path,
            monkeypatch,
            {
                "a": {"origins": ["https://a.test"], "mode": "anonymous", "credential_access": "deny", "login": "deny"},
            },
            lambda r: httpx.Response(401),
            flow_class=Flow,
        )
        monkeypatch.setattr(runtime._operations.credentials, "get_plugin", lambda *a: pytest.fail("denied vault read"))
        async with runtime._operations.open("https://a.test") as site:
            assert not await site.context.auth.request_login("optional")
            with pytest.raises(LoginDeniedError):
                await site.context.requests.execute(RequestSpec("https://a.test"))
        await runtime.close()

    asyncio.run(scenario())


def test_initialize_cannot_send_network_requests(tmp_path, monkeypatch):
    async def scenario():
        class Flow(BaseAuthFlow):
            async def initialize(self, context):
                await context.requests.execute(RequestSpec("https://a.test/login"))

        runtime = service(
            tmp_path,
            monkeypatch,
            {"a": {"origins": ["https://a.test"], "mode": "anonymous"}},
            lambda r: pytest.fail("initialization reached transport"),
            flow_class=Flow,
        )
        async with runtime._operations.open("https://a.test") as site:
            with pytest.raises(AuthReentryError):
                await site.context.requests.execute(RequestSpec("https://a.test"))
        await runtime.close()

    asyncio.run(scenario())


def test_saved_token_can_be_applied_without_login_or_renewal_permission(tmp_path, monkeypatch):
    async def scenario():
        class Flow(BaseAuthFlow):
            async def initialize(self, context):
                self.credentials = context.credentials
                self.token = context.credentials.get("token")
                return AuthLease()

            def apply(self, request):
                return replace(request, headers={"Authorization": "Bearer " + self.token})

        def handler(r):
            assert r.headers["authorization"] == "Bearer canary"
            return httpx.Response(200)

        runtime = service(
            tmp_path,
            monkeypatch,
            {
                "a": {
                    "origins": ["https://a.test"],
                    "mode": "anonymous",
                    "secrets": {"token": "A"},
                    "login": "deny",
                }
            },
            handler,
            flow_class=Flow,
        )
        runtime._operations.credentials.set_plugin("core.generic-html", "A", "canary")
        async with runtime._operations.open("https://a.test") as site:
            await site.context.requests.execute(RequestSpec("https://a.test", auth=AuthMode.REQUIRED))
            with pytest.raises(AuthReentryError):
                site.gateway.auth.flow.credentials.get("token")
            with pytest.raises(AuthScopeError):
                await site.context.requests.execute(RequestSpec("https://other.test"))
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", [RuntimeError, asyncio.CancelledError])
def test_anonymous_failure_and_cancellation_discard_cookies_and_preserve_saved_file(
    tmp_path, monkeypatch, interruption
):
    async def scenario():
        runtime = service(
            tmp_path,
            monkeypatch,
            {
                "a": {"origins": ["https://a.test"], "mode": "anonymous"},
            },
            lambda request: httpx.Response(200, headers={"set-cookie": "temporary=command; Path=/"}),
        )
        stored = httpx.Cookies()
        stored.set("saved", "original", domain="a.test", path="/")
        runtime.cookie_store.save(stored.jar)
        encrypted = runtime.cookie_store.filesystem.path(runtime.cookie_store.relative)
        before = encrypted.read_bytes()
        monkeypatch.setattr(CookieStore, "load", lambda self: pytest.fail("anonymous read saved cookies"))
        with pytest.raises(interruption):
            async with runtime._operations.open("https://a.test") as site:
                assert site.context.get_cookies() == {}
                await site.context.requests.execute(RequestSpec("https://a.test"))
                gateway = site.gateway._shared
                assert gateway.client.cookies.get("temporary") == "command"
                raise interruption()
        await runtime.close()
        assert gateway.client.is_closed and not list(gateway.client.cookies.jar)
        assert encrypted.read_bytes() == before

    asyncio.run(scenario())


def test_anonymous_cookie_session_survives_permission_changes_in_one_command(tmp_path, monkeypatch):
    async def scenario():
        runtime = service(
            tmp_path,
            monkeypatch,
            {
                "a": {"origins": ["https://a.test"], "mode": "anonymous", "login": "ask"},
            },
            lambda request: httpx.Response(200, headers={"set-cookie": "session=existing; Path=/"}),
        )
        async with runtime._operations.open("https://a.test") as first:
            await first.context.requests.execute(RequestSpec("https://a.test"))
            gateway = first.gateway._shared
        async with runtime._operations.open(
            "https://a.test",
            auth_access_overrides={
                "core.generic-html": {"sites": {"a": {"login": "deny"}}},
            },
        ) as second:
            assert second.gateway._shared is gateway
            assert second.context.get_cookies(domain="a.test", path="/") == {"session": "existing"}
        await runtime.close()

    asyncio.run(scenario())


def test_anonymous_client_shares_pool_without_sharing_cookies_or_closing_owner():
    from image_downloader.transport.gateway import RequestGateway

    class Pool(httpx.MockTransport):
        closed = 0

        async def aclose(self):
            self.closed += 1

    async def scenario():
        root = RequestGateway(AppConfig())
        await root.client.aclose()
        pool = Pool(lambda request: httpx.Response(200, headers={"set-cookie": "session=temporary; Path=/"}))
        root.client = httpx.AsyncClient(transport=pool)
        temporary = root._fork()
        await temporary.execute(RequestSpec("https://a.test"))
        assert temporary.client.cookies.get("session") == "temporary"
        assert not list(root.client.cookies.jar)
        await temporary.close()
        assert pool.closed == 0
        await root.close()
        assert pool.closed == 1

    asyncio.run(scenario())


def test_verification_can_read_permitted_saved_credentials_while_login_is_denied(tmp_path, monkeypatch):
    class Flow(BaseAuthFlow):
        async def initialize(self, context):
            return AuthLease()

        async def verify(self, context):
            token = context.credentials.get("token")
            response = await context.requests.execute(
                context.endpoints["operation"].request("/session", headers={"Authorization": "Bearer " + token})
            )
            return AuthVerification(AuthStatus.VALID if response.status == 200 else AuthStatus.INVALID)

    async def scenario():
        def handler(request):
            assert request.url.path == "/session"
            assert request.headers["authorization"] == "Bearer canary"
            return httpx.Response(200)

        runtime = service(
            tmp_path,
            monkeypatch,
            {
                "a": {
                    "origins": ["https://a.test"],
                    "mode": "anonymous",
                    "secrets": {"token": "A"},
                    "login": "deny",
                }
            },
            handler,
            flow_class=Flow,
        )
        runtime._operations.credentials.set_plugin("core.generic-html", "A", "canary")
        async with runtime._operations.open("https://a.test") as operation:
            await operation.context.auth.ensure_ready(check=True)
            assert operation.context.auth.snapshot().last_verified_status is AuthStatus.VALID
        await runtime.close()

    asyncio.run(scenario())


def test_anonymous_refresh_uses_the_same_cookie_jar_and_command_approval(tmp_path, monkeypatch):
    async def scenario():
        logins = 0
        protected_requests = 0

        def handler(request):
            nonlocal logins, protected_requests
            if request.url.path == "/login":
                logins += 1
                return httpx.Response(200, headers={"set-cookie": f"session={logins}; Path=/"})
            protected_requests += 1
            cookie = request.headers.get("cookie")
            if protected_requests == 2:
                assert cookie == "session=1"
                return httpx.Response(401)
            assert cookie == f"session={logins}"
            return httpx.Response(200)

        approver = AsyncMock(return_value=True)
        runtime = service(
            tmp_path,
            monkeypatch,
            {"a": {"origins": ["https://a.test"], "mode": "anonymous", "secrets": {"token": "A"}}},
            handler,
            approver,
        )
        runtime._operations.credentials.set_plugin("core.generic-html", "A", "canary")
        async with runtime._operations.open("https://a.test") as operation:
            await operation.context.requests.execute(RequestSpec("https://a.test/work", auth=AuthMode.REQUIRED))
            await operation.context.requests.execute(RequestSpec("https://a.test/work"))
            assert operation.context.get_cookies(domain="a.test", path="/") == {"session": "2"}
        assert logins == 2 and approver.await_count == 1
        await runtime.close()

    asyncio.run(scenario())


def test_anonymous_workflow_retry_preserves_cookie_and_saves_results_history_logs_and_state(tmp_path, monkeypatch):
    import io
    from pathlib import Path

    from PIL import Image
    from support.workflow import FEED, A, Gallery

    from image_downloader import PluginError, WorkflowRequest
    from image_downloader.config import resolve_paths

    class Flow(BaseAuthFlow):
        async def initialize(self, context):
            return AuthLease()

        async def login(self, context):
            token = context.credentials.get("token")
            response = await context.requests.execute(
                context.endpoints["operation"].request("/login", method="POST", form={"token": token})
            )
            assert response.status == 200
            return AuthLease()

    class RetryGallery(Gallery):
        candidates = (A,)

        def auth_flow(self, context):
            return Flow()

        async def check_updates(self, url, context):
            await context.requests.execute(RequestSpec(url))
            return await super().check_updates(url, context)

        async def inspect(self, url, context):
            await context.requests.execute(RequestSpec(url))
            if not self.seen:
                self.seen.append(url)
                raise PluginError("retry the operation")
            return await super().inspect(url, context)

    async def scenario():
        data = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(data, "PNG")
        observed = []

        def handler(request):
            observed.append(request)
            if request.url.path == "/login":
                assert request.content == b"token=workflow-canary"
                return httpx.Response(200, headers={"set-cookie": "session=ephemeral; Path=/"})
            if request.headers.get("cookie") != "session=ephemeral":
                assert request.url.path == "/feed" and len(observed) == 1
                return httpx.Response(401)
            return httpx.Response(200, content=data.getvalue(), headers={"content-type": "image/png"})

        monkeypatch.setattr(CookieStore, "load", lambda self: pytest.fail("anonymous loaded saved cookies"))
        monkeypatch.setattr(CookieStore, "persist_delta", lambda *a: pytest.fail("anonymous persisted cookies"))
        approver = AsyncMock(return_value=True)
        runtime = service(
            tmp_path,
            monkeypatch,
            {"a": {"origins": ["https://example.test"], "secrets": {"token": "A"}}},
            handler,
            approver,
        )
        runtime._operations.credentials.set_plugin("core.generic-html", "A", "workflow-canary")
        record = runtime.registry.records["core.generic-html"]
        gallery = RetryGallery()
        runtime.registry.resolve = lambda *a, **kw: (record, gallery)
        async with runtime:
            result = await runtime.workflow(
                WorkflowRequest(
                    url=FEED,
                    workflow_retries=1,
                    workflow_retry_delay=0,
                    plugin_auth_access_overrides={"core.generic-html": {"mode": "anonymous"}},
                )
            )
        assert result.items[0].status == "success"
        assert [attempt.status for attempt in result.items[0].attempts] == ["failed", "success"]
        assert approver.await_count == 1
        assert sum(request.url.path == "/login" for request in observed) == 1
        assert all(Path(file).is_file() for file in result.items[0].download.saved_files)
        assert result.items[0].download.saved_files
        paths = resolve_paths(runtime.config)
        for name in ("workflow.json", "workflow-history.json", "updates.json"):
            assert (paths["state"] / name).is_file()
        logs = list(paths["logs"].rglob("*.log")) + list(paths["logs"].rglob("*.jsonl"))
        assert logs
        assert all("workflow-canary" not in file.read_text(encoding="utf-8") for file in logs)
        assert not list(paths["cookie"].glob("*.enc"))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("policy", "code"),
    [
        ({"credential_access": "deny", "login": "allow"}, "credential_access_denied"),
        ({"login": "deny"}, "login_denied"),
        ({"login": "ask"}, "login_approval_required"),
    ],
)
def test_workflow_does_not_retry_credential_denial_login_denial_or_missing_approval(
    tmp_path, monkeypatch, policy, code
):
    from support.workflow import FEED, A, Gallery

    from image_downloader import LoginApprovalRequiredError, WorkflowRequest

    class Flow(BaseAuthFlow):
        async def login(self, context):
            assert code == "credential_access_denied", "denied login hook executed"
            context.credentials.get("token")
            pytest.fail("denied credentials were supplied")

    class ProtectedGallery(Gallery):
        candidates = (A,)

        def auth_flow(self, context):
            return Flow()

        async def inspect(self, url, context):
            await context.requests.execute(RequestSpec(url, auth=AuthMode.REQUIRED))
            pytest.fail("denied request was sent")

    async def scenario():
        approver = AsyncMock(side_effect=LoginApprovalRequiredError())
        runtime = service(
            tmp_path,
            monkeypatch,
            {"a": {"origins": ["https://example.test"], "mode": "anonymous", "secrets": {"token": "A"}, **policy}},
            lambda request: pytest.fail("denied authentication reached transport"),
            approver,
        )
        monkeypatch.setattr(runtime._operations.credentials, "get_plugin", lambda *a: pytest.fail("denied vault read"))
        record = runtime.registry.records["core.generic-html"]
        gallery = ProtectedGallery()
        runtime.registry.resolve = lambda *a, **kw: (record, gallery)
        async with runtime:
            result = await runtime.workflow(WorkflowRequest(url=FEED, workflow_retries=2, workflow_retry_delay=0))
        assert len(result.rounds) == 1
        assert len(result.items[0].attempts) == 1
        assert result.items[0].error.code == code
        assert approver.await_count == (1 if policy.get("login") == "ask" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize("request_kind", ["regular", "authentication"])
def test_operation_close_cancels_and_reaps_outstanding_host_requests(tmp_path, monkeypatch, request_kind):
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        tasks = []

        async def handler(request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        class Flow(BaseAuthFlow):
            async def login(self, context):
                tasks.append(asyncio.create_task(context.requests.execute(RequestSpec("https://a.test/login"))))
                await asyncio.wait_for(started.wait(), timeout=5)
                return AuthLease()

        runtime = service(
            tmp_path,
            monkeypatch,
            {"a": {"origins": ["https://a.test"], "mode": "anonymous", "login": "allow"}},
            handler,
            flow_class=Flow,
        )
        async with runtime._operations.open("https://a.test") as operation:
            if request_kind == "regular":
                tasks.append(
                    asyncio.create_task(
                        operation.context.requests.execute(RequestSpec("https://a.test", auth=AuthMode.NONE))
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=5)
            else:
                await operation.context.auth.request_login("test background authentication request")
        assert cancelled.is_set()
        assert len(tasks) == 1 and tasks[0].done() and tasks[0].cancelled()
        assert not operation.gateway._active_requests
        assert operation.gateway.auth.flow is None and operation.gateway.auth.context is None
        await runtime.close()

    asyncio.run(scenario())


def test_cookie_snapshot_obeys_site_origin_scheme_and_host_only_scope(tmp_path, monkeypatch):
    async def scenario():
        def handler(request):
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "host_only=exact; Path=/"),
                    ("set-cookie", "domain_cookie=shared; Domain=example.test; Path=/account"),
                    ("set-cookie", "secure_cookie=tls; Domain=example.test; Path=/; Secure"),
                ],
            )

        runtime = service(
            tmp_path,
            monkeypatch,
            {"a": {"origins": ["http://sub.example.test"], "mode": "anonymous"}},
            handler,
        )
        async with runtime._operations.open("http://sub.example.test") as operation:
            await operation.context.requests.execute(RequestSpec("http://sub.example.test", auth=AuthMode.NONE))
            snapshot = operation.context.get_cookies()
            assert snapshot["sub.example.test"]["/"] == {"host_only": "exact"}
            assert snapshot[".example.test"]["/account"] == {"domain_cookie": "shared"}
            assert not any("secure_cookie" in cookies for paths in snapshot.values() for cookies in paths.values())
            jar = operation.gateway._shared.client.cookies.jar
            for cookie in list(jar):
                if cookie.name == "host_only":
                    cookie.domain = "example.test"
                    jar.clear()
                    jar.set_cookie(cookie)
                    break
            assert operation.context.get_cookies() == {}
        await runtime.close()

    asyncio.run(scenario())
