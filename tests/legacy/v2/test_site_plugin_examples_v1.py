from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from examples.plugins.chaptered_catalog import ChapteredCatalogDownloader
from examples.plugins.csrf_login_gallery import CsrfLoginGalleryDownloader
from examples.plugins.cursor_api_gallery import CursorApiGalleryDownloader
from examples.plugins.oauth_media_api import OAuthMediaApiDownloader
from examples.plugins.public_gallery import PublicGalleryDownloader
from examples.plugins.signed_cdn_gallery import SignedCdnGalleryDownloader

from image_downloader import RequestResponse, RequestSpec


class Secrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> str:
        return self.values[name]


class FixtureContext:
    def __init__(
        self,
        url: str,
        handler: Callable[[RequestSpec], Awaitable[RequestResponse]],
        *,
        plugin_config: dict[str, object] | None = None,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self.url, self.config, self.plugin_config = url, {}, plugin_config or {}
        self.secrets, self.handler = Secrets(secrets or {}), handler

    async def fetch(self, spec: RequestSpec) -> RequestResponse:
        return await self.handler(spec)


def response(url: str, body: object, status: int = 200) -> RequestResponse:
    value = body if isinstance(body, bytes) else json.dumps(body).encode()
    return RequestResponse(url, status, {"content-type": "application/json"}, value)


def test_public_gallery_parses_html_and_builds_referer_request() -> None:
    async def handler(spec: RequestSpec) -> RequestResponse:
        return RequestResponse(spec.url, 200, {}, b"<title>Public</title><img data-image-id='one' src='/one.png'>")

    async def scenario() -> None:
        url = "https://gallery.example.test/work/1"
        plugin = PublicGalleryDownloader()
        parsed = await plugin.parse(url, FixtureContext(url, handler))  # type: ignore[arg-type]
        request = await plugin.build_image_request(parsed.chapters[0].images[0])
        assert parsed.content_id is None and parsed.chapters[0].images[0].image_id == "one"
        assert request.referer == url and request.headers["Origin"] == "https://gallery.example.test"

    asyncio.run(scenario())


def test_catalog_and_cursor_examples_produce_stable_ordered_resources() -> None:
    async def catalog_handler(spec: RequestSpec) -> RequestResponse:
        if spec.url.endswith("/updates"):
            return response(spec.url, {"works": [{"id": "work-1", "url": "https://catalog.example.test/new", "revision": "2"}]})
        return response(spec.url, {"work": {"id": "work-1", "title": "Catalog", "revision": "1", "chapters": [{"id": "ch-1", "images": [{"id": "im-1", "url": "https://cdn/1"}]}]}})

    async def cursor_handler(spec: RequestSpec) -> RequestResponse:
        cursor = spec.json["cursor"]  # type: ignore[index]
        page = {"id": "gallery-1", "title": "Cursor", "images": [{"id": "a", "url": "https://cdn/a"}], "next_cursor": "next" if cursor is None else None}
        if cursor == "next":
            page["images"] = [{"id": "b", "url": "https://cdn/b"}]
        return response(spec.url, {"gallery": page})

    async def scenario() -> None:
        catalog_url = "https://catalog.example.test/work/1"
        catalog = ChapteredCatalogDownloader()
        parsed = await catalog.parse(catalog_url, FixtureContext(catalog_url, catalog_handler))  # type: ignore[arg-type]
        updates = await catalog.get_updated_urls(catalog_url, FixtureContext(catalog_url, catalog_handler))  # type: ignore[arg-type]
        assert parsed.content_id == "work-1" and parsed.chapters[0].chapter_id == "ch-1"
        assert updates.updated_urls[0].revision == "2"
        api_url = "https://api-gallery.example.test/work/1"
        cursor = await CursorApiGalleryDownloader().parse(api_url, FixtureContext(api_url, cursor_handler, secrets={"api_token": "fixture"}))  # type: ignore[arg-type]
        assert [image.image_id for image in cursor.chapters[0].images] == ["a", "b"]

    asyncio.run(scenario())


def test_csrf_and_oauth_examples_use_secret_references_and_auth_hooks() -> None:
    async def csrf_handler(spec: RequestSpec) -> RequestResponse:
        if spec.url.endswith("/login"):
            return RequestResponse(spec.url, 200, {}, b"<input name='csrf' value='fixture-csrf'>")
        if spec.url.endswith("/session"):
            assert spec.form and spec.form["csrf"] == "fixture-csrf"
            return response(spec.url, {"ok": True})
        return RequestResponse(spec.url, 200, {}, b"<title>Members</title><img data-id='m1' src='/m1.png'>")

    async def oauth_handler(spec: RequestSpec) -> RequestResponse:
        if spec.url.endswith("/oauth/token"):
            return response(spec.url, {"access_token": "fresh", "expires_in": 60})
        return response(spec.url, {"media": {"id": "media-1", "title": "OAuth", "images": [{"id": "o1", "url": "https://cdn/o1"}]}})

    async def scenario() -> None:
        csrf_url = "https://members.example.test/work/1"
        csrf_context = FixtureContext(csrf_url, csrf_handler, secrets={"username": "user", "password": "pass"})
        csrf_plugin = CsrfLoginGalleryDownloader()
        assert (await csrf_plugin.parse(csrf_url, csrf_context)).chapters[0].images[0].image_id == "m1"  # type: ignore[arg-type]
        refreshed = await csrf_plugin.get_auth_strategy(csrf_context).refresh_request(RequestSpec(csrf_url), response(csrf_url, {}, 401))  # type: ignore[arg-type]
        assert refreshed.url == csrf_url
        oauth_url = "https://oauth-media.example.test/work/1"
        oauth_context = FixtureContext(oauth_url, oauth_handler, secrets={"access_token": "old", "refresh_token": "refresh"})
        oauth_plugin = OAuthMediaApiDownloader()
        strategy = oauth_plugin.get_auth_strategy(oauth_context)  # type: ignore[arg-type]
        assert strategy.is_auth_failure(RequestSpec(oauth_url), response(oauth_url, {"error": "expired_token"}))
        assert "Bearer fresh" in (await strategy.refresh_request(RequestSpec(oauth_url), response(oauth_url, {}))).headers["Authorization"]

    asyncio.run(scenario())


def test_signed_cdn_example_mints_initial_and_replacement_requests() -> None:
    calls = 0

    async def handler(spec: RequestSpec) -> RequestResponse:
        nonlocal calls
        if spec.url.endswith("/images"):
            return response(spec.url, {"id": "signed-work", "images": [{"id": "image-1"}]})
        calls += 1
        return response(spec.url, {"url": f"https://cdn.example.test/signed-{calls}.png"})

    async def scenario() -> None:
        url = "https://signed.example.test/work/1"
        context = FixtureContext(url, handler)
        plugin = SignedCdnGalleryDownloader()
        image = (await plugin.parse(url, context)).chapters[0].images[0]  # type: ignore[arg-type]
        first = await plugin.build_image_request(image, context)  # type: ignore[arg-type]
        replacement = await plugin.refresh_image_request(image, first, response(first.url, {}, 403), context)  # type: ignore[arg-type]
        assert first.url.endswith("signed-1.png") and replacement and replacement.url.endswith("signed-2.png")

    asyncio.run(scenario())
