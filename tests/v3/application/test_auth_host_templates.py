from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from application.test_auth_access_sessions import service
from support.vault import MemoryKeys

from image_downloader import AuthMode, AuthScopeError, RequestSpec
from image_downloader.credentials.keyring import MasterKeyStore
from image_downloader.diagnostics import diagnostic_for
from image_downloader.exceptions import ConfigurationError
from image_downloader.storage.cookies import CookieStore

HOSTS = "$" + "{host_groups.default}"


@pytest.fixture(autouse=True)
def memory_keys(monkeypatch):
    keys = MemoryKeys()
    monkeypatch.setattr(MasterKeyStore, "get", lambda self, v, k: keys.get(v, k))
    monkeypatch.setattr(MasterKeyStore, "create", lambda self, v, k, value: keys.create(v, k, value))


def declare(runtime, endpoints, *, config=None):
    original, plugin = runtime.registry.resolve("https://example.com")
    record = replace(original, connection_defaults={"endpoints": endpoints}, author_defaults=config or {})
    runtime.registry.resolve = lambda *a, **kw: (record, plugin)


def test_wildcard_session_login_redirect_preview_refresh_and_cross_domain_reuse(tmp_path, monkeypatch):
    async def scenario():
        observed = []
        logins = 0
        expire = False

        def handler(request):
            nonlocal logins, expire
            observed.append(request)
            base = "example.co.jp" if request.url.host.endswith("example.co.jp") else "example.com"
            if request.url.path == "/login":
                logins += 1
                assert request.content == b"token=canary"
                headers = {"set-cookie": f"session={base}; Domain={base}; Path=/; Secure"}
                if request.url.host == "example.com":
                    headers["location"] = "https://viewer.example.com/welcome"
                    return httpx.Response(302, headers=headers)
                return httpx.Response(200, headers=headers)
            if request.url.path == "/welcome":
                assert request.headers.get("cookie") == "session=example.com"
            if expire:
                expire = False
                return httpx.Response(401)
            return httpx.Response(200 if request.headers.get("cookie") == f"session={base}" else 401)

        monkeypatch.setattr(CookieStore, "load", lambda self: pytest.fail("anonymous cookie load"))
        monkeypatch.setattr(CookieStore, "persist_delta", lambda *a: pytest.fail("anonymous cookie save"))
        approver = AsyncMock(return_value=True)
        runtime = service(
            tmp_path,
            monkeypatch,
            {
                "shared": {
                    "origins": ["https://" + HOSTS, "https://*." + HOSTS],
                    "mode": "anonymous",
                    "secrets": {"token": "A"},
                }
            },
            handler,
            approver,
            host_groups={"default": ["example.com", "example.co.jp"]},
        )
        declare(runtime, {"viewer": {"origin": "https://viewer." + HOSTS, "auth": "session"}})
        runtime._operations.credentials.set_plugin("core.generic-html", "A", "canary")
        async with runtime._operations.open("https://example.com/work") as operation:
            await operation.context.requests.execute(RequestSpec("https://example.com/work"))
            gateway = operation.gateway._shared
            preview = await operation.gateway.preview(RequestSpec("https://viewer.example.com/image"))
            # A preview includes cookie names but keeps their values redacted.
            assert "session" in str(preview) and "canary" not in str(preview)
            expire = True
            await operation.context.requests.execute(RequestSpec("https://viewer.example.com/work"))
            assert logins == 2
        async with runtime._operations.open("https://viewer.example.co.jp/work") as operation:
            assert operation.gateway._shared is gateway
            await operation.context.requests.execute(RequestSpec("https://viewer.example.co.jp/work"))
            assert set(operation.context.get_cookies()) == {".example.com", ".example.co.jp"}
        assert logins == 3 and approver.await_count == 1
        other_first = next(request for request in observed if request.url.host == "viewer.example.co.jp")
        assert "cookie" not in other_first.headers
        assert "*.example.com" in str(approver.call_args.args[0].origins)
        await runtime.close()
        assert not list(gateway.client.cookies.jar)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "target,declared,kind",
    [
        ("https://api.example.com", "https://viewer.example.com", "author_origin_undeclared"),
        ("https://viewer.other.com", "https://viewer.other.com", "user_origin_denied"),
        ("https://a.viewer.example.com", "https://a.viewer.example.com", "user_origin_denied"),
    ],
)
def test_author_and_user_ranges_are_both_required_before_sending(tmp_path, monkeypatch, target, declared, kind):
    async def scenario():
        runtime = service(
            tmp_path,
            monkeypatch,
            {"shared": {"origins": ["https://example.com", "https://*.example.com"], "mode": "anonymous"}},
            lambda request: pytest.fail("denied request reached transport"),
        )
        declare(runtime, {"remote": {"origin": declared, "auth": "session"}})
        async with runtime._operations.open("https://example.com") as operation:
            with pytest.raises(AuthScopeError) as captured:
                await operation.context.requests.execute(RequestSpec(target))
            diagnostic = diagnostic_for(captured.value)
            assert diagnostic.details["kind"] == kind
            assert diagnostic.details["plugin_id"] == "core.generic-html"
        await runtime.close()

    asyncio.run(scenario())


def test_auth_none_uses_the_same_wildcard_cookie_filter_and_normal_cookie_attributes(tmp_path, monkeypatch):
    async def scenario():
        observed = []

        def handler(request):
            observed.append(request)
            return httpx.Response(200)

        runtime = service(
            tmp_path,
            monkeypatch,
            {"shared": {"origins": ["https://example.com", "https://*.example.com"], "mode": "anonymous"}},
            handler,
        )
        declare(runtime, {"viewer": {"origin": "https://viewer.example.com", "auth": "session"}})
        async with runtime._operations.open("https://example.com") as operation:
            jar = operation.gateway._shared.client.cookies
            jar.set("domain_cookie", "shared", domain=".example.com", path="/")
            jar.set("viewer", "only", domain="viewer.example.com", path="/")
            jar.set("unrelated", "hidden", domain=".other.com", path="/")
            for url in (
                "https://viewer.example.com",
                "https://a.viewer.example.com",
                "http://viewer.example.com",
                "https://viewer.other.com",
            ):
                await operation.context.requests.execute(RequestSpec(url, auth=AuthMode.NONE))
            assert "domain_cookie=shared" in observed[0].headers["cookie"]
            assert all("cookie" not in request.headers for request in observed[1:])
            assert set(operation.context.get_cookies()) == {".example.com", "viewer.example.com"}
        await runtime.close()

    asyncio.run(scenario())


def test_auth_access_overrides_expand_only_the_selected_user_hosts(tmp_path, monkeypatch):
    async def scenario():
        runtime = service(
            tmp_path, monkeypatch, {}, lambda r: httpx.Response(200), host_groups={"default": ["example.com"]}
        )
        overrides = {"core.generic-html": {"sites": {"added": {"origins": "https://" + HOSTS, "mode": "anonymous"}}}}
        async with runtime._operations.open("https://example.com", auth_access_overrides=overrides) as operation:
            assert operation.gateway.auth.access.site_id == "added"
            assert operation.gateway.auth.access.site.origins == ("https://example.com:443",)
        with pytest.raises(ConfigurationError):
            async with runtime._operations.open(
                "https://example.com", auth_access_overrides={"core.generic-html": {"host_groups": ["other.com"]}}
            ):
                pytest.fail("host_groups was accepted as an auth_access override")
        with pytest.raises(ConfigurationError) as invalid:
            async with runtime._operations.open(
                "https://example.com",
                auth_access_overrides={
                    "core.generic-html": {"sites": {"bad": {"origins": "https://" + "$" + "{env.PRIVATE_CANARY}"}}}
                },
            ):
                pytest.fail("unsupported variable reached an operation")
        diagnostic = diagnostic_for(invalid.value)
        assert diagnostic.details["kind"] == "auth_origins_reference"
        assert diagnostic.details["plugin_id"] == "core.generic-html"
        assert "CANARY" not in diagnostic.message
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("detached", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_author_config_cannot_supply_user_hosts_for_endpoints(tmp_path, monkeypatch, detached, legacy):
    async def scenario():
        monkeypatch.setattr(CookieStore, "load", lambda self: pytest.fail("vault read before configuration validation"))
        runtime = service(tmp_path, monkeypatch, {}, lambda r: pytest.fail("network sent"))
        declare(
            runtime,
            {
                "viewer": {
                    "origin": "https://viewer." + ("$" + "{allowed_hosts}" if legacy else HOSTS),
                    "auth": "session",
                }
            },
            config={"host_groups": {"default": ["example.com"]}, "supported_hosts": ["example.com"]},
        )
        with pytest.raises(ConfigurationError) as captured:
            async with runtime._operations.open("https://example.com", detached=detached):
                pytest.fail("author hosts were used as user permission")
        assert diagnostic_for(captured.value).details["kind"] == (
            "host_groups_legacy" if legacy else "host_groups_undefined"
        )
        assert diagnostic_for(captured.value).details["field"] == "plugin_settings.core.generic-html." + (
            "allowed_hosts" if legacy else "host_groups.default"
        )
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("declared,permitted", [(True, False), (False, True), (True, True)])
def test_cookie_send_snapshot_redirect_and_preview_require_both_ranges(tmp_path, monkeypatch, declared, permitted):
    async def scenario():
        observed = []

        def handler(request):
            observed.append(request)
            if request.url.path == "/redirect":
                return httpx.Response(302, headers={"location": "https://viewer.example.com/final"})
            return httpx.Response(200)

        origins = ["https://example.com"] + (["https://*.example.com"] if permitted else [])
        runtime = service(tmp_path, monkeypatch, {"shared": {"origins": origins, "mode": "anonymous"}}, handler)
        if declared:
            declare(runtime, {"viewer": {"origin": "https://viewer.example.com", "auth": "session"}})
        async with runtime._operations.open("https://example.com") as operation:
            operation.gateway._shared.client.cookies.set("viewer_secret", "hidden", domain="viewer.example.com")
            request = RequestSpec("https://viewer.example.com/direct", auth=AuthMode.NONE)
            preview = await operation.gateway.preview(request)
            expected = declared and permitted
            assert ("viewer_secret" in str(preview)) is expected
            assert ("viewer.example.com" in operation.context.get_cookies()) is expected
            await operation.context.requests.execute(request)
            assert ("viewer_secret=hidden" in observed[-1].headers.get("cookie", "")) is expected
            await operation.context.requests.execute(RequestSpec("https://example.com/redirect", auth=AuthMode.NONE))
            assert ("viewer_secret=hidden" in observed[-1].headers.get("cookie", "")) is expected
        await runtime.close()

    asyncio.run(scenario())


def test_invalid_author_endpoint_group_expansion_is_safe_and_precedes_cookie_reads(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(CookieStore, "load", lambda self: pytest.fail("cookie read before validation"))
        runtime = service(
            tmp_path, monkeypatch, {}, lambda r: pytest.fail("network sent"), host_groups={"default": ["example.com"]}
        )
        declare(runtime, {"viewer": {"origin": "https://viewer." + HOSTS + ":PRIVATE_PORT_CANARY", "auth": "session"}})
        with pytest.raises(ConfigurationError) as captured:
            async with runtime._operations.open("https://example.com"):
                pytest.fail("invalid expanded origin reached operation")
        diagnostic = diagnostic_for(captured.value)
        assert diagnostic.details["kind"] == "host_groups_expansion"
        assert diagnostic.details["plugin_id"] == "core.generic-html"
        assert "PRIVATE_PORT_CANARY" not in str(diagnostic)
        await runtime.close()

    asyncio.run(scenario())
