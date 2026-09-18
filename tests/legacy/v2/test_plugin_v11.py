from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from image_downloader import UnsupportedSiteFeature
from image_downloader.application.updates import UpdateState, filter_updated
from image_downloader.auth import AuthProvider
from image_downloader.config import validate_config
from image_downloader.models import RequestResponse, RequestSpec, UpdatedUrl, UpdateResult
from image_downloader.networking import HttpClient, RequestScheduler
from image_downloader.plugins.secrets import PluginSecrets


class BodyAuthStrategy:
    def is_auth_failure(self, _request: RequestSpec, response: RequestResponse) -> bool:
        return response.status == 200 and response.body == b'{"error":"expired"}'

    async def refresh_request(self, request: RequestSpec, _response: RequestResponse) -> RequestSpec:
        return replace(request, headers={**request.headers, "Authorization": "Bearer renewed"})


def _client(auth: AuthProvider | None = None) -> HttpClient:
    return HttpClient({"network": {"max_retries": 0, "max_auth_retries": 1}}, RequestScheduler(1, 0), auth)


def test_json_body_and_http_200_auth_failure_are_supported() -> None:
    async def scenario() -> None:
        auth = AuthProvider(strategy=BodyAuthStrategy())
        client = _client(auth)

        async def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content) == {"query": "images"}
            if request.headers.get("Authorization") != "Bearer renewed":
                return httpx.Response(200, content=b'{"error":"expired"}', request=request)
            return httpx.Response(200, json={"images": []}, request=request)

        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            response = await client.request(RequestSpec("https://api.example.test/graphql", method="POST", json={"query": "images"}))
            assert response.body == b'{"images":[]}'
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_failed_response_can_replace_a_signed_image_request_once() -> None:
    async def scenario() -> None:
        client = _client()
        attempts: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(str(request.url))
            return httpx.Response(403 if request.url.path == "/expired" else 200, content=b"image", request=request)

        async def refresh(_request: RequestSpec, response: RequestResponse) -> RequestSpec | None:
            assert response.status == 403
            return RequestSpec("https://cdn.example.test/fresh")

        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert (await client.request(RequestSpec("https://cdn.example.test/expired"), on_response_failure=refresh)).body == b"image"
            assert attempts == ["https://cdn.example.test/expired", "https://cdn.example.test/fresh"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_plugin_secret_uses_reference_not_plaintext(monkeypatch) -> None:
    monkeypatch.setenv("IMAGE_DOWNLOADER_PLUGIN_EXAMPLE_SITE_API_TOKEN", "secret-value")
    assert PluginSecrets("example-site", {"token": "api-token"}).get("token") == "secret-value"
    config = validate_config({"plugins": {"example-site": {"config": {"page_size": 100}, "secrets": {"token": "api-token"}}}})
    assert config["plugins"]["example-site"]["config"]["page_size"] == 100


def test_update_state_uses_stable_content_id_and_revision(tmp_path: Path) -> None:
    state = UpdateState(tmp_path / "state.json")
    first = UpdateResult("source", "example", [UpdatedUrl("https://old", content_id="work-1", revision="1")], datetime.now(UTC))
    assert len(filter_updated(first, state).updated_urls) == 1
    moved = UpdateResult("source", "example", [UpdatedUrl("https://new", content_id="work-1", revision="1")], datetime.now(UTC))
    assert len(filter_updated(moved, state).updated_urls) == 1
    assert len(filter_updated(moved, state).updated_urls) == 0


def test_unsupported_request_protocols_and_multiple_signed_refreshes_are_explicit() -> None:
    async def scenario() -> None:
        client = _client()
        try:
            try:
                await client.request(RequestSpec("wss://example.test/feed"))
            except UnsupportedSiteFeature as exc:
                assert "WebSocket" in str(exc)
            else:
                raise AssertionError("WebSocket request was accepted")

            try:
                await client.request(RequestSpec("https://example.test/api", files={"upload": b"x"}))
            except UnsupportedSiteFeature as exc:
                assert "multipart" in str(exc)
            else:
                raise AssertionError("multipart request was accepted")

            async def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(403, request=request)

            async def refresh(_request: RequestSpec, _response: RequestResponse) -> RequestSpec:
                return RequestSpec("https://cdn.example.test/retry")

            await client.client.aclose()
            client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                await client.request(RequestSpec("https://cdn.example.test/expired"), on_response_failure=refresh)
            except UnsupportedSiteFeature as exc:
                assert "more than one" in str(exc)
            else:
                raise AssertionError("second signed refresh was accepted")
        finally:
            await client.aclose()

    asyncio.run(scenario())
