"""Public cookie reads preserve stored scopes and response boundaries."""

from __future__ import annotations

import asyncio
import time
from dataclasses import fields
from http.cookiejar import Cookie
from inspect import signature

import httpx
import pytest

from image_downloader import CookieProvider, PluginExecutionContext, RequestResponse, RequestSpec
from image_downloader.config import AppConfig
from image_downloader.exceptions import (
    HttpStatusError,
    HttpTransportError,
    SecretNotFoundError,
    UnsupportedSiteFeatureError,
)
from image_downloader.transport.gateway import RequestGateway


class _Secrets:
    def get(self, name: str) -> str:
        raise SecretNotFoundError("no test secret")


def _context(gateway: RequestGateway, *, with_provider: bool = True) -> PluginExecutionContext:
    return PluginExecutionContext(
        {}, {}, {}, None, _Secrets(), gateway, cookie_provider=gateway if with_provider else None
    )


def _cookie(name, value, domain, path="/", *, expires=None, secure=False, rest=None):
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        domain.startswith("."),
        domain.startswith("."),
        path,
        True,
        secure,
        expires,
        False,
        None,
        None,
        rest or {},
        False,
    )


def test_context_cookie_scope_expiry_and_detached_results():
    async def scenario():
        gateway = RequestGateway(AppConfig())
        try:
            assert isinstance(gateway, CookieProvider)
            context = _context(gateway)
            assert context.get_cookies() == {}
            jar = gateway.client.cookies.jar
            for cookie in (
                _cookie("session", "host", "example.test"),
                _cookie("session", "domain", ".example.test"),
                _cookie("session", "images", ".example.test", "/images", secure=True, rest={"HttpOnly": None}),
                _cookie("expired", "secret", ".example.test", expires=int(time.time()) - 1),
                _cookie("empty", None, "example.test"),
            ):
                jar.set_cookie(cookie)
            expected = {
                "example.test": {"/": {"session": "host", "empty": ""}},
                ".example.test": {"/": {"session": "domain"}, "/images": {"session": "images"}},
            }
            assert context.get_cookies() == expected
            assert context.get_cookies(domain=".example.test") == expected[".example.test"]
            assert context.get_cookies(path="/images") == {".example.test": {"session": "images"}}
            assert context.get_cookies(domain="example.test", path="/") == {"session": "host", "empty": ""}
            assert context.get_cookies(domain="sub.example.test") == {}
            assert context.get_cookies(path="/images/child") == {}
            assert context.get_cookies(domain=".example.test", path="/missing") == {}
            assert context.get_cookies(domain="EXAMPLE.TEST") == {}
            context.get_cookies()["example.test"]["/"]["session"] = "changed"
            context.get_cookies(domain=".example.test")["/"].clear()
            context.get_cookies(path="/images")[".example.test"].clear()
            context.get_cookies(domain="example.test", path="/").clear()
            assert context.get_cookies() == expected
            # Reads do not prune expired entries or otherwise mutate the jar.
            assert len(list(jar)) == 5
            with pytest.raises(UnsupportedSiteFeatureError):
                _context(gateway, with_provider=False).get_cookies()
            with pytest.raises(TypeError, match="domain"):
                context.get_cookies(domain=1)
            with pytest.raises(TypeError, match="path"):
                context.get_cookies(path=1)
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ((), {}),
        (("a=one; Path=/; HttpOnly", "b=two; Expires=Wed, 09 Jun 2038 10:18:14 GMT"), {"a": "one", "b": "two"}),
        (("a=first", "a=last; Path=/other"), {"a": "last"}),
        (('quoted="a,b"; Secure', "gone=; Max-Age=0"), {"quoted": "a,b", "gone": ""}),
        (("not-a-cookie", "bad[name]=no", "good=yes"), {"good": "yes"}),
    ],
)
def test_response_set_cookies(raw, expected):
    response = RequestResponse("https://example.test/", 200, {}, b"", set_cookie_headers=raw)
    assert response.set_cookies() == expected
    response.set_cookies().clear()
    assert response.set_cookies() == expected
    assert "set_cookie_headers" not in repr(response)


def test_response_constructor_compatibility_and_header_fallback():
    response = RequestResponse("https://example.test/", 200, {"Set-Cookie": "a=one; Path=/"}, b"")
    assert response.set_cookies() == {"a": "one"}
    assert RequestResponse.__match_args__ == ("url", "status", "headers", "body")
    assert fields(RequestResponse)[-1].kw_only
    assert signature(PluginExecutionContext).parameters["cookie_provider"].kind.name == "KEYWORD_ONLY"
    assert (
        RequestResponse("https://example.test/", 200, response.headers, b"", set_cookie_headers=()).set_cookies() == {}
    )
    with pytest.raises(TypeError, match="set_cookie_headers"):
        RequestResponse("https://example.test/", 200, {}, b"", set_cookie_headers=["a=one"])


def test_transport_cookie_updates_redirect_response_and_request_only_values():
    async def scenario():
        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/final", "set-cookie": "hop=one; Path=/"})
            if request.url.path == "/final":
                assert "hop=one" in request.headers["cookie"]
                return httpx.Response(200)
            if request.url.path == "/login":
                return httpx.Response(
                    200,
                    headers=[
                        ("set-cookie", "session=first; Path=/; HttpOnly"),
                        ("set-cookie", "other=two; Expires=Wed, 09 Jun 2038 10:18:14 GMT; Path=/"),
                    ],
                )
            if request.url.path == "/update":
                return httpx.Response(200, headers={"set-cookie": "session=updated; Path=/"})
            if request.url.path == "/delete":
                return httpx.Response(200, headers={"set-cookie": "session=; Max-Age=0; Path=/"})
            return httpx.Response(400, headers={"set-cookie": "failure=received; Path=/"})

        gateway = RequestGateway(AppConfig(), transport_factory=lambda: httpx.MockTransport(handler))
        context = _context(gateway)
        try:
            response = await context.requests.execute(RequestSpec("https://example.test/start"))
            assert response.set_cookies() == {}
            assert context.get_cookies(domain="example.test", path="/") == {"hop": "one"}
            response = await context.requests.execute(
                RequestSpec("https://example.test/login", cookies={"manual": "only"})
            )
            assert response.set_cookies() == {"session": "first", "other": "two"}
            assert len(response.set_cookie_headers) == 2
            assert "manual" not in context.get_cookies(domain="example.test", path="/")
            await context.requests.execute(RequestSpec("https://example.test/update"))
            assert context.get_cookies(domain="example.test", path="/")["session"] == "updated"
            # A previously returned response remains independent of the current jar.
            assert response.set_cookies()["session"] == "first"
            deleted = await context.requests.execute(RequestSpec("https://example.test/delete"))
            assert deleted.set_cookies() == {"session": ""}
            assert "session" not in context.get_cookies(domain="example.test", path="/")
            with pytest.raises(HttpStatusError):
                await context.requests.execute(RequestSpec("https://example.test/error"))
            assert context.get_cookies(domain="example.test", path="/")["failure"] == "received"
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_retry_and_concurrent_requests_share_current_cookie_values():
    async def scenario():
        calls = 0
        retry_started = asyncio.Event()
        other_done = asyncio.Event()

        async def handler(request):
            nonlocal calls
            if request.url.path == "/retry":
                calls += 1
                if calls == 1:
                    retry_started.set()
                    return httpx.Response(503, headers={"set-cookie": "retry=first; Path=/"})
                await other_done.wait()
                assert "retry=first" in request.headers["cookie"]
                return httpx.Response(200, headers={"set-cookie": "retry=last; Path=/"})
            await retry_started.wait()
            return httpx.Response(200, headers={"set-cookie": "parallel=other; Path=/"})

        config = AppConfig.model_validate({"network": {"max_attempts": 2, "retry_max_delay_seconds": 0}})
        gateway = RequestGateway(config, transport_factory=lambda: httpx.MockTransport(handler))
        context = _context(gateway)
        try:

            async def other():
                response = await context.requests.execute(RequestSpec("https://example.test/other"))
                other_done.set()
                return response

            retry, parallel = await asyncio.wait_for(
                asyncio.gather(context.requests.execute(RequestSpec("https://example.test/retry")), other()), timeout=5
            )
            assert retry.set_cookies() == {"retry": "last"}
            assert parallel.set_cookies() == {"parallel": "other"}
            assert context.get_cookies(domain="example.test", path="/") == {"retry": "last", "parallel": "other"}
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_auth_refresh_can_read_cookies_from_failed_response():
    async def scenario():
        gateway = RequestGateway(AppConfig(), transport_factory=lambda: httpx.MockTransport(handler))
        context = _context(gateway)

        class Flow:
            async def apply(self, request):
                return request

            def is_auth_failure(self, request, response):
                return False

            async def refresh(self, request, response):
                assert response.set_cookies() == {"session": "challenge"}
                assert context.get_cookies(domain="example.test", path="/") == {"session": "challenge"}
                return request

        session = gateway.operation(
            plugin_id="example.plugin", operation_url="https://example.test/", auth_flow_factory=lambda _: Flow()
        )
        try:
            response = await session.execute(RequestSpec("https://example.test/login"))
            assert response.set_cookies() == {"session": "authenticated"}
            assert context.get_cookies(domain="example.test", path="/") == {"session": "authenticated"}
        finally:
            await gateway.close()

    def handler(request):
        # Presence of the challenge cookie distinguishes the retried request.
        if "session=challenge" in request.headers.get("cookie", ""):
            return httpx.Response(200, headers={"set-cookie": "session=authenticated; Path=/"})
        return httpx.Response(401, headers={"set-cookie": "session=challenge; Path=/"})

    asyncio.run(scenario())


def test_cookie_update_survives_response_body_failure():
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadError("artificial body failure")

    async def scenario():
        def handler(request):
            return httpx.Response(200, headers={"set-cookie": "received=yes; Path=/"}, stream=BrokenStream())

        config = AppConfig.model_validate({"network": {"max_attempts": 1}})
        gateway = RequestGateway(config, transport_factory=lambda: httpx.MockTransport(handler))
        try:
            context = _context(gateway)
            with pytest.raises(HttpTransportError):
                await context.requests.execute(RequestSpec("https://example.test/broken"))
            assert context.get_cookies(domain="example.test", path="/") == {"received": "yes"}
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_service_hooks_share_cookies_and_inspection_uses_isolated_jar(tmp_path, monkeypatch):
    from support.vault import MemoryKeys
    from support.workflow import A, Gallery, attach_transport, compose

    from image_downloader import DownloadRequest, InspectRequest
    from image_downloader.credentials.keyring import MasterKeyStore

    keys = MemoryKeys()
    monkeypatch.setattr(MasterKeyStore, "get", lambda self, vault_id, key_id: keys.get(vault_id, key_id))
    monkeypatch.setattr(
        MasterKeyStore, "create", lambda self, vault_id, key_id, key: keys.create(vault_id, key_id, key)
    )

    class CookieGallery(Gallery):
        async def inspect(self, url, context):
            assert context.get_cookies(domain="example.test", path="/") == {"session": "imported"}
            response = await context.requests.execute(RequestSpec("https://example.test/login"))
            assert response.set_cookies() == {"session": "received"}
            assert context.get_cookies(domain="example.test", path="/") == {"session": "received"}
            return await super().inspect(url, context)

        async def create_image_request(self, image, context):
            assert context.get_cookies(domain="example.test", path="/") == {"session": "received"}
            return await super().create_image_request(image, context)

    async def scenario():
        async with compose(tmp_path, CookieGallery()) as service:
            # The real factory is also used by inspection's temporary gateway.
            def handler(request):
                return httpx.Response(200, headers={"set-cookie": "session=received; Path=/"})

            service.gateway.transport_factory = lambda: httpx.MockTransport(handler)
            service.gateway.client.cookies.set("session", "imported", domain="example.test", path="/")
            await service.inspect(InspectRequest(url=A.url))
            assert service.gateway.get_cookie_snapshot()["example.test"]["/"] == {"session": "imported"}
            await attach_transport(service)
            # Use the same login route while preserving the image fixture transport.
            image_transport = service.gateway.client._transport

            async def download_handler(request):
                if request.url.path == "/login":
                    return handler(request)
                return await image_transport.handle_async_request(request)

            service.gateway.client.cookies.set("session", "imported", domain="example.test", path="/")
            service.gateway.client._transport = httpx.MockTransport(download_handler)
            result = await service.download(DownloadRequest(url=A.url))
            assert not result.failures
            assert service.gateway.get_cookie_snapshot()["example.test"]["/"] == {"session": "received"}

    asyncio.run(scenario())
