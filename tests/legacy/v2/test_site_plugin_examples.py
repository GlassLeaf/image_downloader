"""Offline acceptance coverage for the six documented v2 plugin patterns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from examples.plugins.chaptered_catalog import ChapteredCatalogPlugin
from examples.plugins.csrf_login_gallery import CsrfLoginGalleryPlugin
from examples.plugins.cursor_api_gallery import CursorApiGalleryPlugin
from examples.plugins.oauth_media_api import OAuthMediaApiPlugin
from examples.plugins.public_gallery import PublicGalleryPlugin
from examples.plugins.signed_cdn_gallery import SignedCdnGalleryPlugin

from image_downloader import PluginExecutionContext, RequestResponse, RequestSpec

FIXTURES = Path(__file__).parents[2] / "examples" / "fixtures"


class Secrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> str:
        return self.values[name]


class Requests:
    def __init__(self, handler: Callable[[RequestSpec], Awaitable[RequestResponse]]) -> None:
        self.handler = handler
        self.seen: list[RequestSpec] = []

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        self.seen.append(spec)
        return await self.handler(spec)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def response(url: str, body: bytes, status: int = 200) -> RequestResponse:
    return RequestResponse(url, status, {"content-type": "application/json"}, body)


def context(
    handler: Callable[[RequestSpec], Awaitable[RequestResponse]], *, secrets: dict[str, str] | None = None
) -> tuple[PluginExecutionContext, Requests]:
    requests = Requests(handler)
    return PluginExecutionContext({}, Secrets(secrets or {}), requests), requests


def test_public_html_and_chaptered_catalog_examples_use_fixed_fixtures() -> None:
    async def public_handler(spec: RequestSpec) -> RequestResponse:
        return RequestResponse(spec.url, 200, {"content-type": "text/html"}, fixture("public_gallery.html"))

    async def catalog_handler(spec: RequestSpec) -> RequestResponse:
        if spec.url.endswith("/updates"):
            return response(spec.url, fixture("chaptered_catalog_updates.json"))
        return response(spec.url, fixture("chaptered_catalog.json"))

    async def scenario() -> None:
        public_context, _ = context(public_handler)
        public = await PublicGalleryPlugin().inspect("https://public-gallery.example.test/galleries/1", public_context)
        request = await PublicGalleryPlugin().create_image_request(public.chapters[0].images[0], public_context)
        assert public.chapters[0].images[0].image_id == "public-1"
        assert request.referer == "https://public-gallery.example.test/galleries/1"
        assert request.headers["Origin"] == "https://public-gallery.example.test"

        catalog_context, _ = context(catalog_handler)
        catalog = ChapteredCatalogPlugin()
        manifest = await catalog.inspect("https://catalog.example.test/works/1", catalog_context)
        updates = await catalog.check_updates("https://catalog.example.test/works/1", catalog_context)
        assert manifest.content_id == "catalog-1" and manifest.chapters[0].chapter_id == "chapter-1"
        assert updates.candidates[0].revision == "8"

    asyncio.run(scenario())


def test_cursor_csrf_oauth_and_signed_cdn_examples_use_only_request_port() -> None:
    async def cursor_handler(spec: RequestSpec) -> RequestResponse:
        return response(
            spec.url,
            fixture("cursor_api_page_1.json") if spec.json["cursor"] is None else fixture("cursor_api_page_2.json"),
        )

    async def csrf_handler(spec: RequestSpec) -> RequestResponse:
        if spec.url.endswith("/login"):
            return RequestResponse(spec.url, 200, {}, fixture("csrf_login_form.html"))
        if spec.url.endswith("/session"):
            assert spec.form == {"username": "fixture-user", "password": "fixture-password", "csrf": "fixture-csrf"}
            return response(spec.url, b"{}")
        return RequestResponse(spec.url, 200, {}, fixture("csrf_members_gallery.html"))

    async def oauth_handler(spec: RequestSpec) -> RequestResponse:
        if spec.url.endswith("/oauth/token"):
            return response(spec.url, fixture("oauth_token.json"))
        return response(spec.url, fixture("oauth_media.json"))

    signed_calls = 0

    async def signed_handler(spec: RequestSpec) -> RequestResponse:
        nonlocal signed_calls
        if spec.url.endswith("/images"):
            return response(spec.url, fixture("signed_cdn_images.json"))
        signed_calls += 1
        return response(spec.url, fixture(f"signed_cdn_url_{signed_calls}.json"))

    async def scenario() -> None:
        cursor_context, cursor_requests = context(cursor_handler, secrets={"api_token": "fixture-api-token"})
        cursor = await CursorApiGalleryPlugin().inspect("https://cursor-api.example.test/galleries/1", cursor_context)
        assert [image.image_id for image in cursor.chapters[0].images] == ["cursor-image-1", "cursor-image-2"]
        assert cursor_requests.seen[0].headers["X-Api-Key"] == "fixture-api-token"

        csrf_context, _ = context(csrf_handler, secrets={"username": "fixture-user", "password": "fixture-password"})
        csrf = CsrfLoginGalleryPlugin()
        manifest = await csrf.inspect("https://csrf-login.example.test/works/1", csrf_context)
        refreshed = await csrf.auth_flow(csrf_context).refresh(
            RequestSpec("https://csrf-login.example.test/works/1"),
            response("https://csrf-login.example.test/works/1", b"{}", 401),
        )
        assert manifest.chapters[0].images[0].image_id == "member-1" and refreshed is not None

        oauth_context, _ = context(oauth_handler, secrets={"access_token": "old", "refresh_token": "fixture-refresh"})
        oauth = OAuthMediaApiPlugin()
        assert (
            await oauth.inspect("https://oauth-media.example.test/works/1", oauth_context)
        ).content_id == "oauth-media-1"
        flow = oauth.auth_flow(oauth_context)
        assert flow.is_auth_failure(
            RequestSpec("https://oauth-media.example.test/works/1"),
            response("https://oauth-media.example.test/works/1", b'{"error":"expired_token"}'),
        )
        refreshed = await flow.refresh(
            RequestSpec("https://oauth-media.example.test/works/1"),
            response("https://oauth-media.example.test/works/1", b"{}"),
        )
        assert refreshed is not None
        assert "fixture-access" in (await flow.apply(refreshed)).headers["Authorization"]

        signed_context, _ = context(signed_handler)
        signed = SignedCdnGalleryPlugin()
        image = (await signed.inspect("https://signed-cdn.example.test/works/1", signed_context)).chapters[0].images[0]
        first = await signed.create_image_request(image, signed_context)
        replacement = await signed.recover_image_request(image, first, response(first.url, b"{}", 403), signed_context)
        assert (
            first.url.endswith("signed-1.png") and replacement is not None and replacement.url.endswith("signed-2.png")
        )

    asyncio.run(scenario())
