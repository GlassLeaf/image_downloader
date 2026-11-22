from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from image_downloader import AuthenticationError, HttpStatusError, PluginError, PluginExecutionContext, RequestSpec
from image_downloader.config import AppConfig
from image_downloader.configuration.models import PluginConnections
from image_downloader.devtools.loading import resolve_target
from image_downloader.plugins.lifecycle import PluginClassLoader
from image_downloader.transport.gateway import RequestGateway

SOURCE = Path(__file__).resolve().parents[3] / "plugin-implementation-samples/plugin-a"
DOMAIN = "https://www.example.co.jp"
WORK = "/manga/official/1/"
CHAPTER = WORK + "1/"
API = "/manga/official/viewer.json"
IMAGE = "https://cdn-image.example.co.jp/1.webp"
ENCODED_TOKEN = "token%2B%2F%3D"
TOKEN = "token+/="


class _Secrets:
    def __init__(self):
        self.reads = []

    def get(self, name):
        self.reads.append(name)
        return {"email": "reader@example.test", "password": "test-password"}[name]


class _Site:
    def __init__(self):
        self.calls = []
        self.login_posts = 0
        self.expire_next_work = False
        self.login_succeeds = True
        self.login_html = '<input name="_token" value="login-token">'
        self.xsrf_cookie = f"XSRF-TOKEN={ENCODED_TOKEN}; Domain=.example.co.jp; Path=/; Secure"
        self.page_response = {
            "manga": {"title": "Work"},
            "episode": {"title": "Episode"},
            "page": {"images": [{"url": IMAGE}], "placeholder": ""},
        }

    def __call__(self, request):
        self.calls.append(request)
        if request.url.host not in {"www.example.co.jp", "cdn-image.example.co.jp"} or request.url.scheme != "https":
            return httpx.Response(302, headers={"Location": DOMAIN + request.url.path})
        authenticated = "session=active" in request.headers.get("Cookie", "")
        path = request.url.path
        if path == "/login":
            return self._login(request)
        if path == "/mypage":
            return httpx.Response(200) if authenticated else httpx.Response(302, headers={"Location": "/login"})
        if path == WORK:
            if self.expire_next_work:
                self.expire_next_work = False
                return httpx.Response(
                    302,
                    headers={"Location": "/login", "Set-Cookie": "session=; Domain=.example.co.jp; Path=/; Max-Age=0"},
                )
            return httpx.Response(200, text=self._toc(authenticated))
        if path == "/manga/official/search":
            today = datetime.now(timezone(timedelta(hours=9)))
            return httpx.Response(
                200,
                text=f'<a href="{WORK}"><div class="last-update"><span>{today:%m/%d}</span></div></a>',
            )
        if not authenticated:
            return httpx.Response(401)
        if path == CHAPTER:
            # XSRF is issued at login and deliberately not reissued here.
            return httpx.Response(200, text="viewer")
        if path == API:
            if request.headers.get("X-XSRF-TOKEN") != TOKEN:
                return httpx.Response(419)
            assert json.loads(request.content) == {
                "episode_no": 1,
                "manga_sele_id": 1,
                "preview": False,
                "hide_page": False,
                "resolution": "full_hd",
            }
            return httpx.Response(200, json=self.page_response)
        if str(request.url) == IMAGE:
            return httpx.Response(200, content=b"image-bytes", headers={"Content-Type": "image/webp"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def _login(self, request):
        if request.method == "GET":
            return httpx.Response(200, text=self.login_html)
        self.login_posts += 1
        assert json.loads(request.content) == {
            "_token": "login-token",
            "email": "reader@example.test",
            "password": "test-password",
        }
        if not self.login_succeeds:
            return httpx.Response(200, text=self.login_html)
        headers = [
            ("Location", "/mypage"),
            ("Set-Cookie", "session=active; Domain=.example.co.jp; Path=/; Secure"),
        ]
        if self.xsrf_cookie:
            headers.append(("Set-Cookie", self.xsrf_cookie))
        return httpx.Response(302, headers=headers)

    @staticmethod
    def _toc(authenticated):
        today = datetime.now(timezone(timedelta(hours=9)))
        episodes = [
            {
                "episodeNo": 1,
                "rental": {"isFree": False, "isOnRental": authenticated},
                "url": CHAPTER,
                "upTime": today.strftime("%Y.%m.%d") + "更新",
                "isPublic": True,
                "earlyRelease": False,
            }
        ]
        return (
            '<div id="app-official-manga-toc"><script type="application/json">'
            + json.dumps({"episodes": episodes})
            + "</script></div>"
        )


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
    async def create(site, *, url=DOMAIN + WORK, authentication=None):
        gateway = RequestGateway(AppConfig(), transport_factory=lambda: httpx.MockTransport(site))
        plugin = plugin_class()
        secrets = _Secrets()
        context = None

        def factory(session):
            nonlocal context
            session.bind_auth_context({}, secrets)
            context = PluginExecutionContext(
                {}, {}, plugin_record.manifest.value, None, secrets, session, cookie_provider=gateway
            )
            return plugin.auth_flow(context)

        settings = PluginConnections.model_validate(
            {
                **plugin_record.connection_defaults,
                "authentication": authentication or {},
            }
        )
        session = gateway.operation(
            plugin_id=plugin_record.id, operation_url=url, auth_flow_factory=factory, settings=settings
        )
        try:
            yield plugin, context, gateway, session, secrets
        finally:
            plugin.cleanup_after_use()
            await gateway.close()

    return create


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("host", ["example.com", "www.example.com", "example.co.jp", "www.example.co.jp"])
def test_initial_login_inspects_rented_chapter_and_fetches_cdn_for_every_supported_origin(operation, scheme, host):
    async def scenario():
        site = _Site()
        url = f"{scheme}://{host}{WORK}"
        async with operation(site, url=url) as (plugin, context, _gateway, _session, secrets):
            assert plugin.matches(url)
            manifest = await plugin.inspect(url, context)
            assert manifest.title == "Work"
            assert len(manifest.chapters) == 1
            chapter = manifest.chapters[0]
            assert (chapter.title, chapter.chapter_id) == ("Episode", "1")
            assert [image.url for image in chapter.images] == [IMAGE]
            response = await context.requests.execute(await plugin.create_image_request(chapter.images[0], context))
            assert response.body == b"image-bytes"
            assert site.login_posts == 1
            assert secrets.reads == ["email", "password"]
            assert site.calls[0].url.path == "/mypage"

    asyncio.run(scenario())


def test_existing_session_is_reused_without_reading_credentials(operation):
    async def scenario():
        site = _Site()
        async with operation(site) as (plugin, context, gateway, _session, secrets):
            gateway.client.cookies.set("session", "active", domain=".example.co.jp", path="/")
            gateway.client.cookies.set("XSRF-TOKEN", ENCODED_TOKEN, domain=".example.co.jp", path="/")
            assert len((await plugin.inspect(DOMAIN + WORK, context)).chapters) == 1
            assert site.login_posts == 0
            assert secrets.reads == []

    asyncio.run(scenario())


def test_expired_session_is_refreshed_through_auth_context(operation):
    async def scenario():
        site = _Site()
        site.expire_next_work = True
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            assert len((await plugin.inspect(DOMAIN + WORK, context)).chapters) == 1
            assert site.login_posts == 2

    asyncio.run(scenario())


def test_unauthorized_response_refreshes_session(operation):
    async def scenario():
        site = _Site()
        async with operation(site) as (_plugin, _context, gateway, session, _secrets):
            await session.execute(RequestSpec(DOMAIN + WORK))
            gateway.client.cookies.delete("session", domain=".example.co.jp", path="/")
            response = await session.execute(RequestSpec(DOMAIN + CHAPTER))
            assert response.body == b"viewer"
            assert site.login_posts == 2

    asyncio.run(scenario())


def test_forbidden_response_with_valid_session_does_not_repeat_login(operation):
    async def scenario():
        site = _Site()

        def handler(request):
            if request.url.path == "/restricted":
                return httpx.Response(403)
            return site(request)

        async with operation(handler) as (_plugin, _context, _gateway, session, _secrets):
            with pytest.raises(HttpStatusError) as error:
                await session.execute(RequestSpec(DOMAIN + "/restricted"))
            assert error.value.status == 403
            assert site.login_posts == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("authentication", [{"verify_on_start": True}, {"verify_before_request": True}])
def test_session_verification_uses_auth_context(operation, authentication):
    async def scenario():
        site = _Site()
        async with operation(site, authentication=authentication) as (plugin, context, _gateway, _session, _secrets):
            assert len((await plugin.inspect(DOMAIN + WORK, context)).chapters) == 1
            assert site.login_posts == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("login_html", ["<form></form>", '<input name="_token">', '<input name="_token" value="">'])
def test_missing_login_token_is_an_authentication_error(operation, login_html):
    async def scenario():
        site = _Site()
        site.login_html = login_html
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(AuthenticationError, match="login session request failed"):
                await plugin.inspect(DOMAIN + WORK, context)
            assert site.login_posts == 0

    asyncio.run(scenario())


def test_failed_login_is_an_authentication_error(operation):
    async def scenario():
        site = _Site()
        site.login_succeeds = False
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(AuthenticationError, match="login session request failed"):
                await plugin.inspect(DOMAIN + WORK, context)
            assert site.login_posts == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "domain,path",
    [
        ("www.example.co.jp", "/"),
        (".www.example.co.jp", "/"),
        (".example.co.jp", "/"),
        (".example.co.jp", "/manga/official"),
    ],
)
def test_saved_xsrf_token_matches_cookie_domain_and_api_path(operation, domain, path):
    async def scenario():
        site = _Site()
        site.xsrf_cookie = None
        async with operation(site) as (plugin, context, gateway, _session, _secrets):
            gateway.client.cookies.set("XSRF-TOKEN", ENCODED_TOKEN, domain=domain, path=path)
            assert len((await plugin.inspect(DOMAIN + WORK, context)).chapters) == 1
            api_request = next(request for request in site.calls if request.url.path == API)
            assert api_request.headers["X-XSRF-TOKEN"] == TOKEN

    asyncio.run(scenario())


def test_reissued_xsrf_cookie_replaces_stale_token(operation):
    async def scenario():
        site = _Site()
        site.xsrf_cookie = None

        def handler(request):
            response = site(request)
            if request.url.path == CHAPTER:
                response.headers["Set-Cookie"] = f"XSRF-TOKEN={ENCODED_TOKEN}; Domain=.example.co.jp; Path=/; Secure"
            return response

        async with operation(handler) as (plugin, context, gateway, _session, _secrets):
            gateway.client.cookies.set("XSRF-TOKEN", "stale", domain=".example.co.jp", path="/")
            assert len((await plugin.inspect(DOMAIN + WORK, context)).chapters) == 1

    asyncio.run(scenario())


def test_api_path_cookie_takes_precedence_over_root_cookie(operation):
    async def scenario():
        site = _Site()
        site.xsrf_cookie = "XSRF-TOKEN=stale; Domain=.example.co.jp; Path=/; Secure"
        async with operation(site) as (plugin, context, gateway, _session, _secrets):
            gateway.client.cookies.set("XSRF-TOKEN", ENCODED_TOKEN, domain=".example.co.jp", path="/manga/official")
            assert len((await plugin.inspect(DOMAIN + WORK, context)).chapters) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("domain,path", [("other.test", "/"), (".example.co.jp", "/manga/official/1/")])
def test_unrelated_xsrf_cookie_does_not_mask_missing_token(operation, domain, path):
    async def scenario():
        site = _Site()
        site.xsrf_cookie = None
        async with operation(site) as (plugin, context, gateway, _session, _secrets):
            gateway.client.cookies.set("XSRF-TOKEN", ENCODED_TOKEN, domain=domain, path=path)
            with pytest.raises(PluginError, match="XSRF-TOKEN cookie is missing"):
                await plugin.inspect(DOMAIN + WORK, context)
            assert not any(request.url.path == API for request in site.calls)

    asyncio.run(scenario())


def test_missing_xsrf_cookie_is_a_plugin_error(operation):
    async def scenario():
        site = _Site()
        site.xsrf_cookie = None
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(PluginError, match="XSRF-TOKEN cookie is missing"):
                await plugin.inspect(DOMAIN + WORK, context)

    asyncio.run(scenario())


@pytest.mark.parametrize("body", [None, {}, {"manga": None}])
def test_invalid_api_response_is_a_plugin_error(operation, body):
    async def scenario():
        site = _Site()
        site.page_response = body
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(PluginError, match="invalid plugin-a chapter page response"):
                await plugin.inspect(DOMAIN + WORK, context)

    asyncio.run(scenario())


def test_invalid_chapter_url_is_rejected_before_any_request(operation):
    async def scenario():
        site = _Site()
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            with pytest.raises(PluginError, match="invalid plugin-a chapter URL"):
                await plugin.get_chapter_page_info(DOMAIN + WORK, context)
            assert site.calls == []

    asyncio.run(scenario())


def test_update_check_uses_initialized_session(operation):
    async def scenario():
        site = _Site()
        async with operation(site) as (plugin, context, _gateway, _session, _secrets):
            snapshot = await plugin.check_updates(DOMAIN + WORK, context)
            assert snapshot.source_url == DOMAIN + WORK
            assert [candidate.url for candidate in snapshot.candidates] == [DOMAIN + CHAPTER]
            assert site.login_posts == 1

    asyncio.run(scenario())
