from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from image_downloader.config import AppConfig
from image_downloader.exceptions import AuthenticationError, DownloaderError
from image_downloader.models import RequestResponse, RequestSpec
from image_downloader.transport.gateway import RequestGateway


class _HeaderFlow:
    def __init__(self, *origins: str) -> None:
        self.allowed_origins = origins

    async def apply(self, request: RequestSpec) -> RequestSpec:
        return replace(request, headers={**request.headers, "X-Secret": "credential"})

    async def refresh(self, request: RequestSpec, response: RequestResponse) -> RequestSpec | None:
        return None

    def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
        return False


async def _gateway(config: AppConfig, handler) -> RequestGateway:
    gateway = RequestGateway(config)
    await gateway.client.aclose()
    gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return gateway


def test_authenticated_redirect_to_unlisted_origin_never_sends_secret() -> None:
    async def scenario() -> None:
        seen: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((str(request.url), request.headers.get("X-Secret")))
            return httpx.Response(302, headers={"Location": "https://other.test/target"}, request=request)

        gateway = await _gateway(AppConfig(), handler)
        session = gateway.operation(
            plugin_id="com.example.site",
            operation_url="https://origin.test/gallery",
            auth_flow_factory=lambda _: _HeaderFlow(),
        )
        try:
            with pytest.raises(AuthenticationError, match="outside the configured origins"):
                await session.execute(RequestSpec("https://origin.test/start"))
            assert seen == [("https://origin.test/start", "credential")]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_allowlisted_authenticated_redirect_and_same_origin_hops_keep_auth() -> None:
    async def scenario() -> None:
        seen: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((str(request.url), request.headers.get("X-Secret")))
            if request.url.path == "/start":
                return httpx.Response(302, headers={"Location": "/middle"}, request=request)
            if request.url.path == "/middle":
                return httpx.Response(302, headers={"Location": "https://cdn.test/image"}, request=request)
            return httpx.Response(200, content=b"image", request=request)

        gateway = await _gateway(AppConfig(), handler)
        session = gateway.operation(
            plugin_id="com.example.site",
            operation_url="https://origin.test/gallery",
            auth_flow_factory=lambda _: _HeaderFlow("https://cdn.test"),
        )
        try:
            response = await session.execute(RequestSpec("https://origin.test/start"))
            assert response.body == b"image"
            assert seen == [
                ("https://origin.test/start", "credential"),
                ("https://origin.test/middle", "credential"),
                ("https://cdn.test/image", "credential"),
            ]
            assert set(gateway._hosts) == {"https://origin.test:443", "https://cdn.test:443"}
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_authenticated_https_to_http_downgrade_is_rejected_even_if_allowlisted() -> None:
    async def scenario() -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://cdn.test/image"}, request=request)

        gateway = await _gateway(AppConfig(), handler)
        session = gateway.operation(
            plugin_id="com.example.site",
            operation_url="https://origin.test/gallery",
            auth_flow_factory=lambda _: _HeaderFlow("http://cdn.test"),
        )
        try:
            with pytest.raises(AuthenticationError, match="downgrade"):
                await session.execute(RequestSpec("https://origin.test/start"))
            assert seen == ["https://origin.test/start"]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_anonymous_cross_origin_redirect_strips_all_caller_and_client_headers() -> None:
    async def scenario() -> None:
        seen: list[tuple[str, dict[str, str]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((str(request.url), dict(request.headers)))
            if request.url.host == "origin.test":
                return httpx.Response(302, headers={"Location": "https://cdn.test/image"}, request=request)
            return httpx.Response(200, content=b"image", request=request)

        config = AppConfig.model_validate({"network": {"headers": {"X-Global-Secret": "global"}}})
        gateway = await _gateway(config, handler)
        gateway.client.headers["X-Global-Secret"] = "global"
        try:
            response = await gateway.execute(
                RequestSpec(
                    "https://origin.test/start",
                    headers={"X-Secret": "manual"},
                    cookies={"session": "cookie-secret"},
                    referer="https://origin.test/private?token=secret",
                    auth_required=False,
                )
            )
            assert response.body == b"image"
            assert seen[0][1]["x-secret"] == "manual"
            assert seen[1][0] == "https://cdn.test/image"
            assert set(seen[1][1]) == {"host", "accept"}
            assert seen[1][1]["accept"] == "*/*"
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "expected_method"),
    [(301, "GET"), (302, "GET"), (303, "GET"), (307, "POST"), (308, "POST")],
)
def test_same_origin_redirect_keeps_httpx_method_semantics(status: int, expected_method: str) -> None:
    async def scenario() -> None:
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            if request.url.path == "/start":
                return httpx.Response(status, headers={"Location": "/done"}, request=request)
            return httpx.Response(200, content=b"ok", request=request)

        gateway = await _gateway(AppConfig(), handler)
        try:
            result = await gateway.execute(RequestSpec("https://origin.test/start", method="POST", form={"a": "b"}))
            assert result.body == b"ok"
            assert methods == ["POST", expected_method]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_anonymous_cross_origin_redirect_cannot_forward_post_body() -> None:
    async def scenario() -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(307, headers={"Location": "https://cdn.test/target"}, request=request)

        gateway = await _gateway(AppConfig(), handler)
        try:
            with pytest.raises(DownloaderError, match="request body"):
                await gateway.execute(RequestSpec("https://origin.test/start", method="POST", form={"token": "secret"}))
            assert seen == ["https://origin.test/start"]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_follow_redirects_false_returns_initial_response() -> None:
    async def scenario() -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://cdn.test/target"}, request=request)

        gateway = await _gateway(AppConfig.model_validate({"network": {"follow_redirects": False}}), handler)
        try:
            response = await gateway.execute(RequestSpec("https://origin.test/start", auth_required=False))
            assert response.status == 302
            assert seen == ["https://origin.test/start"]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_redirect_loop_stops_at_configured_client_limit() -> None:
    async def scenario() -> None:
        seen = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen
            seen += 1
            return httpx.Response(302, headers={"Location": "/again"}, request=request)

        gateway = await _gateway(AppConfig(), handler)
        try:
            with pytest.raises(DownloaderError, match="redirect limit"):
                await gateway.execute(RequestSpec("https://origin.test/start"))
            assert seen == gateway.client.max_redirects + 1
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_each_redirect_hop_checks_the_auth_origin() -> None:
    async def scenario() -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            target = "https://cdn.test/middle" if request.url.host == "origin.test" else "https://other.test/end"
            return httpx.Response(302, headers={"Location": target}, request=request)

        gateway = await _gateway(AppConfig(), handler)
        session = gateway.operation(
            plugin_id="com.example.site",
            operation_url="https://origin.test/gallery",
            auth_flow_factory=lambda _: _HeaderFlow("https://cdn.test"),
        )
        try:
            with pytest.raises(AuthenticationError, match="outside the configured origins"):
                await session.execute(RequestSpec("https://origin.test/start"))
            assert seen == ["https://origin.test/start", "https://cdn.test/middle"]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_redirected_response_still_obeys_the_byte_limit() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "origin.test":
                return httpx.Response(302, headers={"Location": "https://cdn.test/image"}, request=request)
            return httpx.Response(200, content=b"12345", request=request)

        gateway = await _gateway(AppConfig.model_validate({"network": {"max_response_bytes": 4}}), handler)
        try:
            with pytest.raises(DownloaderError, match="byte limit"):
                await gateway.execute(RequestSpec("https://origin.test/start", auth_required=False))
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_redirected_host_uses_its_own_concurrency_limit() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        target_calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal target_calls
            if request.url.host == "origin.test":
                return httpx.Response(302, headers={"Location": "https://cdn.test/image"}, request=request)
            target_calls += 1
            if target_calls == 1:
                entered.set()
                await release.wait()
            return httpx.Response(200, content=b"ok", request=request)

        config = AppConfig.model_validate(
            {"network": {"origin_request_concurrency": 1, "request_concurrency": 4}}
        )
        gateway = await _gateway(config, handler)
        first = asyncio.create_task(gateway.execute(RequestSpec("https://origin.test/one", auth_required=False)))
        second = asyncio.create_task(gateway.execute(RequestSpec("https://origin.test/two", auth_required=False)))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(0.05)
            assert target_calls == 1
        finally:
            release.set()
            await asyncio.gather(first, second)
            await gateway.close()
        assert target_calls == 2

    asyncio.run(scenario())
