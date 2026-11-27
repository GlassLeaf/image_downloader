from __future__ import annotations

import asyncio
import io
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image
from support.auth import approved_operation

from image_downloader import (
    AuthenticationError,
    AuthStatus,
    Chapter,
    DownloadManifest,
    HttpStatusError,
    ImageArtifact,
    ImageResource,
    PluginError,
    PluginExecutionContext,
    RequestSpec,
    TransformContext,
)
from image_downloader.config import AppConfig
from image_downloader.configuration.models import PluginConnections
from image_downloader.devtools.loading import resolve_target
from image_downloader.exceptions import AuthScopeError, SecretNotFoundError, VaultError
from image_downloader.plugins.lifecycle import PluginClassLoader
from image_downloader.plugins.plugin_invoker import PluginInvoker
from image_downloader.transport.gateway import RequestGateway

SOURCE = Path(__file__).resolve().parents[3] / "plugin-implementation-samples/plugin-c"
DOMAIN = "https://example.com"
EPISODE = "/episodes/abc123"
CREDENTIALS = {"id": "reader", "password": "test-password"}
TABLE = [4, 0, 12, 8, 5, 1, 13, 9, 6, 2, 14, 10, 7, 3, 15, 11]
ORIGINAL_GRID = [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11], [12, 13, 14, 15]]
RESTORED_GRID = [[1, 5, 9, 13], [0, 4, 8, 12], [3, 7, 11, 15], [2, 6, 10, 14]]
DEFAULT_SESSION = object()


def _tile_png(width=8, height=8, grid=ORIGINAL_GRID):
    with Image.new("RGB", (width, height), (249, 248, 247)) as image, io.BytesIO() as output:
        for row, cells in enumerate(grid):
            for column, value in enumerate(cells):
                color = (value * 13, value * 7, 255 - value * 11)
                left, top = column * (width // 4), row * (height // 4)
                image.paste(color, (left, top, left + width // 4, top + height // 4))
        image.save(output, format="PNG")
        return output.getvalue()


class _Secrets:
    def __init__(self, values):
        self.values = values or {}
        self.reads = []

    def get(self, name):
        self.reads.append(name)
        if name not in self.values:
            raise SecretNotFoundError("required plugin secret is not configured")
        value = self.values[name]
        if isinstance(value, Exception):
            raise value
        return value


class _Site:
    def __init__(self):
        self.calls = []
        self.login_posts = 0
        self.login_succeeds = True
        self.login_status = 200
        self.csrf_payload = {"csrfToken": "csrf-token"}
        self.session_response = DEFAULT_SESSION
        self.session_body = None
        self.html = '<title>HTML title</title><div cms-viewer-id="42"></div>'
        self.api_path = "/api"
        self.book_title = "API title"
        self.legacy_statuses = {}
        self.episode_details = {"episode": {"content": [{"viewerId": 42}]}}
        self.expire_next_contents = False
        self.expected_user = None

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        authenticated = "session=active" in request.headers.get("Cookie", "")
        if path == "/api/auth/session":
            return self._session(authenticated)
        if path == "/api/auth/csrf":
            return httpx.Response(200, json=self.csrf_payload)
        if path == "/api/auth/callback/credentials":
            self.login_posts += 1
            assert request.method == "POST"
            assert request.headers["Content-Type"] == "application/json"
            assert json.loads(request.content) == {
                "id": CREDENTIALS["id"],
                "password": CREDENTIALS["password"],
                "csrfToken": "csrf-token",
                "json": True,
                "redirect": False,
            }
            headers = {}
            if self.login_succeeds and self.login_status == 200:
                headers["Set-Cookie"] = "session=active; Path=/; Secure; HttpOnly"
            return httpx.Response(self.login_status, json={}, headers=headers)
        if path.startswith("/episodes/"):
            return httpx.Response(200, text=self.html)
        if path == "/api/episodes/abc123":
            return httpx.Response(200, json=self.episode_details)
        if path.startswith(self.api_path + "/book/"):
            assert request.headers["Referer"].endswith(EPISODE)
            assert request.url.params["cms-viewer-id"] == "42"
            if path.endswith("/Info"):
                return httpx.Response(
                    self.legacy_statuses.get("Info", 200), json={"result": {"title": self.book_title}}
                )
            if path.endswith("/episodeInfo"):
                assert request.url.params["isPreview"] == "false"
                return httpx.Response(
                    self.legacy_statuses.get("episodeInfo", 200), json={"result": [{"id": 42, "name": "Episode title"}]}
                )
            if path.endswith("/contentsInfo"):
                if self.expire_next_contents:
                    self.expire_next_contents = False
                    return httpx.Response(401, headers={"Set-Cookie": "session=; Path=/; Max-Age=0"})
                user = self.expected_user if self.expected_user is not None else 7 if authenticated else 0
                assert request.url.params["user-id"] == str(user)
                assert request.url.params["page-from"] == "0"
                pages = int(request.url.params["page-to"])
                assert pages in {1, 2}
                images = [
                    {"sort": 0, "imageUrl": DOMAIN + "/images/1.png", "scramble": json.dumps(TABLE)},
                    {"sort": 1, "imageUrl": DOMAIN + "/images/2.png", "scramble": json.dumps(list(range(16)))},
                ]
                return httpx.Response(200, json={"totalPages": 2, "result": images[:pages]})
        if path.startswith("/images/"):
            assert request.headers["Referer"] == DOMAIN
            return httpx.Response(200, content=_tile_png(), headers={"Content-Type": "image/png"})
        if path == "/private":
            return httpx.Response(200 if authenticated else 401, content=b"private")
        if path == "/forbidden":
            return httpx.Response(403)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def _session(self, authenticated):
        if self.session_body is not None:
            return httpx.Response(200, content=self.session_body)
        if self.session_response is not DEFAULT_SESSION:
            return httpx.Response(200, json=self.session_response)
        session = {}
        if authenticated:
            session = {
                "user": 7,
                "idToken": "session-token",
                "expires": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        return httpx.Response(200, json=session)


@pytest.fixture(scope="module")
def plugin_record():
    return resolve_target(SOURCE)


@pytest.fixture(scope="module")
def plugin_class(plugin_record):
    loader = PluginClassLoader()
    try:
        yield loader.load_class(plugin_record)
    finally:
        loader.close()


@pytest.fixture
def operation(plugin_record, plugin_class):
    @asynccontextmanager
    async def create(site, *, credentials=None, url=DOMAIN + EPISODE, authentication=None, max_attempts=3):
        config = AppConfig.model_validate({"network": {"max_attempts": max_attempts}})
        gateway = RequestGateway(config, transport_factory=lambda: httpx.MockTransport(site))
        plugin = plugin_class()
        secrets = _Secrets(credentials)
        context = None

        def factory(session):
            nonlocal context
            session.bind_auth_context(plugin_record.author_defaults, secrets)
            context = PluginExecutionContext(
                plugin_record.author_defaults,
                {},
                plugin_record.manifest.value,
                None,
                session,
                cookie_provider=session,
                auth=session.auth,
                endpoints=session.endpoints,
            )
            return plugin.auth_flow(context)

        settings = PluginConnections.model_validate(
            {**plugin_record.connection_defaults, "authentication": authentication or {}}
        )
        try:
            session = approved_operation(
                gateway,
                origins=(),
                login="allow" if credentials and set(CREDENTIALS).issubset(credentials) else "deny",
                plugin_id=plugin_record.id,
                operation_url=url,
                auth_flow_factory=factory,
                settings=settings,
            )
            yield plugin, context, gateway, session, secrets
        finally:
            await gateway.close()

    return create


def test_development_loader_and_configured_host_selection(plugin_record, plugin_class):
    assert plugin_record.id == "com.example.c"
    assert plugin_record.connection_defaults["endpoints"] == {}
    plugin = plugin_class()
    invoker = PluginInvoker(plugin_record.id)
    invoker.validate_config(plugin, plugin_record.author_defaults, {})
    for url, expected in (
        (DOMAIN + EPISODE, True),
        ("https://EXAMPLE.COM/episodes/abc123", True),
        ("https://example.co.jp/episodes/abc123", False),
        ("http://example.com/episodes/abc123", True),
        ("https://sub.example.com/episodes/abc123", False),
        ("https://example.com.attacker.test/episodes/abc123", False),
        ("ftp://example.com/episodes/abc123", False),
        ("https://[broken", False),
    ):
        assert invoker.matches(plugin, url, config=plugin_record.author_defaults, app_settings={}) == (
            expected,
            "configured",
        )
    assert plugin.matches_with_config("https://custom.test/episodes/1", {"supported_hosts": ["custom.test"]}, {})
    assert not plugin.matches_with_config(DOMAIN + EPISODE, {"supported_hosts": ["custom.test"]}, {})
    assert invoker.matches(
        plugin,
        "https://EXAMPLE.CO.JP/episodes/abc123",
        config={"supported_hosts": ["example.co.jp"]},
        app_settings={},
    ) == (True, "configured")


@pytest.mark.parametrize("hosts", ["example.com", None, [1], [""]])
def test_invalid_host_configuration_is_rejected(plugin_class, hosts):
    with pytest.raises(ValueError, match="supported_hosts"):
        plugin_class().validate_config({"supported_hosts": hosts}, {})


@pytest.mark.parametrize("attribute", ["cms-viewer-id", "data-cms-viewer-id", None])
def test_anonymous_inspection_extracts_viewer_and_all_images(operation, attribute):
    async def scenario():
        site = _Site()
        site.html = f'<div {attribute}="42"></div>' if attribute else "<html></html>"
        async with operation(site) as (plugin, context, _gateway, session, _secrets):
            manifest = await PluginInvoker("com.example.c").inspect(plugin, DOMAIN + EPISODE, context)
            assert session.auth.flow is not None
            assert (plugin.viewerId, plugin.userId) == ("42", 0)
            assert (manifest.title, manifest.chapters[0].title) == ("API title", "Episode title")
            assert [image.index for image in manifest.chapters[0].images] == [1, 2]
            assert all(image.referer == DOMAIN for image in manifest.chapters[0].images)
            assert [image.plugin_data["table"] for image in manifest.chapters[0].images] == [
                tuple(TABLE),
                tuple(range(16)),
            ]
            assert site.login_posts == 0
            paths = [call.url.path for call in site.calls]
            assert paths.count(EPISODE) == 1
            assert paths.count("/api/episodes/abc123") == (attribute is None)
            assert "/api/auth/csrf" not in paths
            assert [call.url.params["page-to"] for call in site.calls if call.url.path.endswith("/contentsInfo")] == [
                "1",
                "2",
            ]

    asyncio.run(scenario())


def test_html_user_id_takes_precedence_over_session(operation):
    async def scenario():
        site = _Site()
        site.html += '<span id="login_user_id"> 19 </span>'
        site.expected_user = 19
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            await plugin.inspect(DOMAIN + EPISODE, context)
            assert (plugin.viewerId, plugin.userId) == ("42", 19)
            assert not any(call.url.path == "/api/auth/session" for call in site.calls)

    asyncio.run(scenario())


def test_custom_api_path_uses_page_origin_and_fixed_episode_fallback(operation):
    async def scenario():
        site = _Site()
        site.html = '<div data-api-domain="https://unused.test/custom-api/"></div>'
        site.api_path = "/custom-api"
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            await plugin.inspect(DOMAIN + EPISODE, context)
            assert plugin.apiPath == "/custom-api"
            assert all(call.url.host == "example.com" for call in site.calls)
            assert any(call.url.path == "/api/episodes/abc123" for call in site.calls)
            site.html = '<div data-cms-viewer-id="84"></div>'
            with pytest.raises(AuthScopeError):
                await plugin.get_params("https://example.co.jp/episodes/second", context)
            async with operation(site, url="https://example.co.jp/episodes/second") as (_, other_context, *_):
                await plugin.get_params("https://example.co.jp/episodes/second", other_context)
            assert (plugin.host, plugin.apiPath, plugin.viewerId, plugin.userId) == (
                "https://example.co.jp",
                "/api",
                "84",
                0,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("credentials", [{"id": "reader"}, {"password": "password"}, {"id": ""}])
def test_incomplete_credentials_keep_anonymous_access(operation, credentials):
    async def scenario():
        site = _Site()
        async with operation(site, credentials=credentials) as (plugin, context, _gateway, session, _secrets):
            await plugin.inspect(DOMAIN + EPISODE, context)
            assert session.auth.flow is not None
            assert site.login_posts == 0

    asyncio.run(scenario())


def test_vault_failure_is_not_treated_as_anonymous_access(operation):
    async def scenario():
        site = _Site()
        with pytest.raises(VaultError):
            async with operation(site, credentials={"id": VaultError("backend"), "password": "unused"}) as (
                plugin,
                context,
                *_,
            ):
                await plugin.inspect(DOMAIN + EPISODE, context)
        assert site.login_posts == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("existing_cookie", [False, True])
def test_credentials_prepare_session_before_page_and_use_session_user_id(operation, existing_cookie):
    async def scenario():
        site = _Site()
        async with operation(site, credentials=CREDENTIALS) as (plugin, context, gateway, _session, _secrets):
            if existing_cookie:
                gateway.client.cookies.set("session", "active", domain="example.com", path="/")
            await plugin.inspect(DOMAIN + EPISODE, context)
            assert plugin.userId == 7
            assert site.login_posts == (0 if existing_cookie else 1)
            paths = [call.url.path for call in site.calls]
            assert paths[0] == EPISODE
            assert paths.count("/api/auth/session") == (1 if existing_cookie else 3)
            if not existing_cookie:
                assert paths.index(EPISODE) < paths.index("/api/auth/callback/credentials")

    asyncio.run(scenario())


def test_existing_cookie_without_credentials_still_supplies_user_id(operation):
    async def scenario():
        site = _Site()
        async with operation(site) as (plugin, context, gateway, session, _secrets):
            gateway.client.cookies.set("session", "active", domain="example.com", path="/")
            await plugin.inspect(DOMAIN + EPISODE, context)
            assert session.auth.flow is not None
            assert plugin.userId == 7
            assert site.login_posts == 0

    asyncio.run(scenario())


def test_unauthorized_image_info_refreshes_session_through_auth_context(operation):
    async def scenario():
        site = _Site()
        site.expire_next_contents = True
        async with operation(site, credentials=CREDENTIALS) as (plugin, context, _gateway, _session, _secrets):
            manifest = await plugin.inspect(DOMAIN + EPISODE, context)
            assert len(manifest.chapters[0].images) == 2
            assert site.login_posts == 2

    asyncio.run(scenario())


def test_forbidden_with_valid_session_does_not_repeat_login(operation):
    async def scenario():
        site = _Site()
        async with operation(site, credentials=CREDENTIALS) as (_plugin, context, _gateway, _session, _secrets):
            with pytest.raises(HttpStatusError) as error:
                await context.requests.execute(RequestSpec(DOMAIN + "/forbidden"))
            assert error.value.status == 403
            assert site.login_posts == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("authentication", [{"verify_on_start": True}, {"verify_before_request": True}])
def test_authentication_verification_policy_uses_auth_context(operation, authentication):
    async def scenario():
        site = _Site()
        async with operation(site, credentials=CREDENTIALS, authentication=authentication) as values:
            plugin, context, _gateway, _session, _secrets = values
            await plugin.inspect(DOMAIN + EPISODE, context)
            assert site.login_posts == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [200, 401, 403])
def test_failed_login_is_not_reported_as_success(operation, status):
    async def scenario():
        site = _Site()
        site.login_succeeds = False
        site.login_status = status
        async with operation(site, credentials=CREDENTIALS) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(AuthenticationError, match="login session request failed"):
                await plugin.inspect(DOMAIN + EPISODE, context)
            assert site.login_posts == 1
            assert not any(call.url.path.endswith("/contentsInfo") for call in site.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("csrf", [{}, {"csrfToken": None}, {"csrfToken": ""}, []])
def test_missing_csrf_token_stops_before_post(operation, csrf):
    async def scenario():
        site = _Site()
        site.csrf_payload = csrf
        async with operation(site, credentials=CREDENTIALS) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(AuthenticationError, match="login session request failed"):
                await plugin.inspect(DOMAIN + EPISODE, context)
            assert site.login_posts == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "session_data, expected",
    [
        ({}, AuthStatus.INVALID),
        ([], AuthStatus.INVALID),
        ({"user": 7, "idToken": "token"}, AuthStatus.INVALID),
        ({"user": 7, "idToken": "token", "expires": None}, AuthStatus.INVALID),
        ({"user": 7, "idToken": "token", "expires": "invalid"}, AuthStatus.INVALID),
        ({"user": 7, "idToken": "token", "expires": "2999-01-01T00:00:00"}, AuthStatus.INVALID),
        ({"user": 7, "idToken": "token", "expires": "2000-01-01T00:00:00Z"}, AuthStatus.INVALID),
        ({"user": None, "idToken": "token", "expires": "2999-01-01T00:00:00Z"}, AuthStatus.INVALID),
        ({"user": {}, "idToken": "token", "expires": "2999-01-01T00:00:00Z"}, AuthStatus.INVALID),
        ({"user": True, "idToken": "token", "expires": "2999-01-01T00:00:00Z"}, AuthStatus.INVALID),
        ({"user": 7, "expires": "2999-01-01T00:00:00Z"}, AuthStatus.INVALID),
        ({"user": "7", "idToken": "token", "expires": "2999-01-01T09:00:00+09:00"}, AuthStatus.VALID),
        ({"user": 7, "idToken": "token", "expires": "2999-01-01T00:00:00Z"}, AuthStatus.VALID),
    ],
)
def test_session_verification_handles_empty_and_malformed_expiry(operation, session_data, expected):
    async def scenario():
        site = _Site()
        site.session_response = session_data
        async with operation(site, credentials=CREDENTIALS) as (_plugin, _context, _gateway, session, _secrets):
            verification = await session.auth.flow.verify(replace(session.auth.context, requests=_gateway))
            assert verification.status is expected

    asyncio.run(scenario())


def test_non_json_session_response_is_an_authentication_error(operation):
    async def scenario():
        site = _Site()
        site.session_body = b"<html>not JSON</html>"
        async with operation(site, credentials=CREDENTIALS) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(AuthenticationError, match="session response did not contain valid JSON"):
                await _session.auth.ensure_ready(check=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("url", [DOMAIN + "/works/123", DOMAIN + "/episodes/", "https://[broken"])
def test_invalid_episode_url_is_rejected_before_requests(operation, url):
    async def scenario():
        site = _Site()
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(PluginError, match="invalid episode URL"):
                await plugin.inspect(url, context)
            assert not site.calls

    asyncio.run(scenario())


def test_missing_viewer_in_fallback_is_a_plugin_error(operation):
    async def scenario():
        site = _Site()
        site.html = "<html></html>"
        site.episode_details = {"episode": {"content": []}}
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(PluginError, match="episode response did not contain a viewerId"):
                await plugin.inspect(DOMAIN + EPISODE, context)

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [("Info",), ("episodeInfo",), ("Info", "episodeInfo")])
def test_missing_legacy_metadata_preserves_available_titles_and_images(operation, missing):
    async def scenario():
        site = _Site()
        site.html += '<span class="main-h-text">HTML episode title</span>'
        site.legacy_statuses = dict.fromkeys(missing, 404)
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            manifest = await PluginInvoker("com.example.c").inspect(plugin, DOMAIN + EPISODE, context)
            assert manifest.title == ("HTML title" if "Info" in missing else "API title")
            assert manifest.chapters[0].title == ("HTML episode title" if "episodeInfo" in missing else "Episode title")
            assert [image.index for image in manifest.chapters[0].images] == [1, 2]
            paths = [call.url.path for call in site.calls]
            assert paths.count("/api/book/Info") == 1
            assert paths.count("/api/book/episodeInfo") == 1
            assert paths.count("/api/book/contentsInfo") == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("endpoint", ["Info", "episodeInfo"])
@pytest.mark.parametrize("status", [403, 500])
def test_other_legacy_metadata_http_errors_are_not_suppressed(operation, endpoint, status):
    async def scenario():
        site = _Site()
        site.legacy_statuses[endpoint] = status
        async with operation(site, max_attempts=1) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(HttpStatusError) as error:
                await PluginInvoker("com.example.c").inspect(plugin, DOMAIN + EPISODE, context)
            assert error.value.status == status
            paths = [call.url.path for call in site.calls]
            assert paths.count(f"/api/book/{endpoint}") == 1
            assert "/api/book/contentsInfo" not in paths

    asyncio.run(scenario())


@pytest.mark.parametrize("width,height", [(8, 8), (9, 11)])
def test_inspection_to_image_transform_preserves_column_order_and_per_image_data(operation, width, height):
    async def scenario():
        site = _Site()
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            manifest = await plugin.inspect(DOMAIN + EPISODE, context)
            chapter = manifest.chapters[0]
            for image, expected_grid in zip(chapter.images, (RESTORED_GRID, ORIGINAL_GRID), strict=True):
                request = await plugin.create_image_request(image, context)
                assert request.url == image.url
                assert request.referer == DOMAIN
                response = await context.requests.execute(request)
                artifact = ImageArtifact(
                    response.body if (width, height) == (8, 8) else _tile_png(width, height),
                    "image/png",
                    image.url,
                    history=("download",),
                )
                transform_context = TransformContext(image, {}, {}, {}, None, manifest, chapter)
                result = plugin.transform_image(artifact, transform_context)
                assert (result.content_type, result.extension) == ("image/webp", ".webp")
                assert result.history == ("download", "plugin-c.descramble")
                assert result.source_url == image.url
                with Image.open(io.BytesIO(result.data)) as restored:
                    with Image.open(io.BytesIO(_tile_png(width, height, expected_grid))) as expected:
                        assert restored.size == expected.size
                        assert restored.convert("RGB").tobytes() == expected.tobytes()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"table": None},
        {"table": "0123456789abcdef"},
        {"table": list(range(15))},
        {"table": [*range(15), 16]},
        {"table": [-1, *range(15)]},
        {"table": [True, *range(1, 16)]},
        {"table": [0.0, *range(1, 16)]},
    ],
)
def test_invalid_scramble_table_is_rejected_before_decoding_image(plugin_class, data):
    image = ImageResource(DOMAIN + "/images/1.png", plugin_data=data)
    chapter = Chapter(1, "chapter", images=(image,))
    manifest = DownloadManifest("work", (chapter,))
    context = TransformContext(image, {}, {}, {}, None, manifest, chapter)
    with pytest.raises(PluginError, match="scramble table must contain 16 integers"):
        plugin_class().transform_image(ImageArtifact(b"not an image", "image/png", image.url), context)
