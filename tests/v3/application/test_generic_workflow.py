from __future__ import annotations

import asyncio
import io
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from image_downloader import (
    AppConfig,
    AuthenticationError,
    DownloadManifest,
    ImageResource,
    PluginError,
    RuntimeComposer,
    UpdateCheckUnsupportedError,
    WorkflowStateService,
)
from image_downloader.observability.logging import DebugFileSink
from image_downloader.plugins.builtin import GenericHtmlPlugin, _automatic_referer, _ImageParser
from image_downloader.transport.gateway import RequestGateway

URL = "https://example.test/"
HTML = '<title>gallery</title><img src="/a.png" data-image-id="a"><img src="/b.png" data-image-id="b">'


class Page:
    def __init__(self, monkeypatch):
        self.html = HTML
        self.requests = []
        self.failed = set()
        self.once_failed = set()
        data = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(data, "PNG")
        client_class = httpx.AsyncClient

        def respond(request):
            self.requests.append(str(request.url))
            if request.url.path == "/":
                return httpx.Response(200, content=self.html.encode(), headers={"content-type": "text/html"})
            path = request.url.path
            failed = path in self.failed or path in self.once_failed
            self.once_failed.discard(path)
            return httpx.Response(
                503 if failed else 200, content=data.getvalue(), headers={"content-type": "image/png"}
            )

        def client(*args, **kwargs):
            return client_class(*args, **dict(kwargs, transport=httpx.MockTransport(respond)))

        monkeypatch.setattr(httpx, "AsyncClient", client)


def compose(tmp_path, *, allow_empty=False, network=None):
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(tmp_path / "plugins")},
            "logging": {"console": {"enabled": False}},
            "network": {"max_attempts": 1, **(network or {})},
            "download": {"allow_empty_chapter_manifest": allow_empty},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path, plugin_root=tmp_path / "plugins").compose()


def revision(html):
    async def execute(spec):
        return SimpleNamespace(url=spec.url, body=html.encode(), headers={})

    context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
    return asyncio.run(GenericHtmlPlugin().check_updates(URL, context))


@pytest.mark.parametrize(
    ("html", "expected", "expected_referer"),
    [
        ('<img src="a.png">', "https://example.test/new/gallery/a.png", URL + "new/gallery/"),
        ('<base href="../images/"><img src="a.png">', "https://example.test/new/images/a.png", URL + "new/gallery/"),
        ('<base href="//cdn.example.test/assets/"><img src="a.png">', "https://cdn.example.test/assets/a.png", URL),
        ('<img src="a.png"><base href="/assets/">', "https://example.test/assets/a.png", URL + "new/gallery/"),
        ('<base href="/first/"><base href="/second/"><img src="a.png">', URL + "first/a.png", URL + "new/gallery/"),
        ('<base href=""><base href="/second/"><img src="a.png">', URL + "new/gallery/a.png", URL + "new/gallery/"),
        ('<base href="file:///images/"><img src="a.png">', URL + "new/gallery/a.png", URL + "new/gallery/"),
        ('<base href="https://["><img src="a.png">', URL + "new/gallery/a.png", URL + "new/gallery/"),
        ('<base href="/assets/"><img src="https://other.test/a.png">', "https://other.test/a.png", URL),
    ],
)
def test_generic_resolves_images_against_final_response_and_first_base(html, expected, expected_referer):
    final_url = "https://example.test/new/gallery/"

    async def execute(spec):
        assert spec.url == URL
        return SimpleNamespace(url=final_url, body=html.encode(), headers={})

    context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
    manifest = asyncio.run(GenericHtmlPlugin().inspect(URL, context))
    image = manifest.chapters[0].images[0]
    assert image.url == expected
    assert image.referer == expected_referer
    request = asyncio.run(GenericHtmlPlugin().create_image_request(image, context))
    assert request.url == expected and request.referer == expected_referer


def test_generic_download_follows_redirect_and_fetches_base_relative_image(tmp_path, monkeypatch):
    client_class = httpx.AsyncClient
    fetched = []
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, "PNG")

    def respond(request):
        fetched.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/new/gallery/"})
        if request.url.path == "/new/gallery/":
            return httpx.Response(200, text='<base href="../images/"><img src="a.png">')
        assert request.url.path == "/new/images/a.png"
        assert request.headers["referer"] == "https://example.test/new/gallery/"
        return httpx.Response(200, content=data.getvalue(), headers={"content-type": "image/png"})

    def client(*args, **kwargs):
        return client_class(*args, **dict(kwargs, transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def scenario():
        async with compose(tmp_path) as service:
            result = await service.run(URL)
            assert len(result.saved_files) == 1
            assert not result.failures

    asyncio.run(scenario())
    assert fetched == [URL, URL + "new/gallery/", URL + "new/images/a.png"]


def test_revision_is_stable_and_ignores_title_body_and_img_attributes():
    first = revision(HTML)
    second = revision(HTML.replace("gallery", "different title") + "<p>different body</p>")
    assert first.candidates == second.candidates
    assert first.candidates[0].url == URL and first.candidates[0].content_id is None
    assert first.candidates[0].revision.startswith("generic-html-image-list-v1:")
    assert revision(HTML.replace('src="/a.png"', 'alt="changed" src="/a.png"')).candidates == first.candidates
    assert first.checked_at.tzinfo is not None


@pytest.mark.parametrize(
    "changed",
    [
        HTML.replace("/a.png", "/a.png?key=new"),
        HTML.replace('data-image-id="a"', 'data-image-id="new"'),
        '<img src="/b.png" data-image-id="b"><img src="/a.png" data-image-id="a">',
        HTML + '<img src="/c.png">',
        '<img src="/a.png" data-image-id="a">',
        HTML + '<img src="/a.png" data-image-id="a">',
    ],
)
def test_revision_detects_image_url_id_order_addition_deletion_and_duplicates(changed):
    assert revision(changed).candidates != revision(HTML).candidates


def test_real_builtin_regular_workflow_updated_and_all_match(tmp_path, monkeypatch):
    page = Page(monkeypatch)

    async def scenario():
        async with compose(tmp_path) as service:
            regular = await service.run(URL)
            page.requests.clear()
            first = await service.workflow(URL, workflow_retries=0)
            assert first.selected_urls == (URL,)
            assert first.items[0].download.saved_files == regular.saved_files
            assert page.requests.count(URL) == 2  # Check and fresh target manifest.
            assert len(page.requests) == 4
            page.requests.clear()
            page.html = HTML.replace("gallery", "new title") + "<p>changed body</p>"
            assert (await service.workflow(URL, workflow_retries=0)).selected_urls == ()
            assert page.requests == [URL]
            page.requests.clear()
            all_result = await service.workflow(URL, download_scope="all", workflow_retries=0)
            assert all_result.selected_urls == (URL,) and len(page.requests) == 4
            page.html += '<img src="/c.png">'
            changed = await service.workflow(URL, workflow_retries=0)
            assert changed.items[0].reasons == ("changed",)
            assert len(changed.items[0].download.saved_files) == 3
            assert all(i.completed for i in WorkflowStateService(service.config).get_workflow(URL)[0].items)

    asyncio.run(scenario())


def test_native_shortened_title_preserves_inspect_revision_original_bytes_and_workflow(tmp_path, monkeypatch):
    page = Page(monkeypatch)
    title = "😀" * 300
    page.html = HTML.replace("gallery", title)
    original_revision = revision(page.html).candidates
    assert original_revision == revision(HTML).candidates

    async def scenario():
        async with compose(tmp_path) as service:
            inspection = await service.inspect(URL)
            assert inspection.manifest.title == title
            regular = await service.run(URL)
            assert len(regular.saved_files) == 2
            paths = tuple(Path(value) for value in regular.saved_files)
            contents = {path.name: path.read_bytes() for path in paths}
            data = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(data, "PNG")
            assert set(contents.values()) == {data.getvalue()}
            assert all(path.parent.name != "0001_" + title + "_" + title for path in paths)
            first = await service.workflow(URL, workflow_retries=0)
            assert first.items[0].status == "success"
            assert first.items[0].download.saved_files == regular.saved_files
            assert (await service.workflow(URL, workflow_retries=0)).selected_urls == ()
            all_result = await service.workflow(URL, download_scope="all", workflow_retries=0)
            assert all_result.items[0].status == "success"
            assert {path.name: path.read_bytes() for path in paths} == contents
            assert all(item.completed for item in WorkflowStateService(service.config).get_workflow(URL)[0].items)

    asyncio.run(scenario())


@pytest.mark.parametrize("allow_empty", [False, True])
def test_empty_page_uses_regular_manifest_policy(tmp_path, monkeypatch, allow_empty):
    page = Page(monkeypatch)
    page.html = "<title>empty</title>"

    async def scenario():
        async with compose(tmp_path, allow_empty=allow_empty) as service:
            result = await service.workflow(URL, workflow_retries=0)
            assert result.selected_urls == (URL,)
            assert result.items[0].status == "success"
            assert not result.items[0].download.saved_files
            regular = await service.run(URL)
            assert not regular.saved_files

    asyncio.run(scenario())


def test_partial_retry_retains_success_and_next_invocation_retries_unfinished(tmp_path, monkeypatch):
    page = Page(monkeypatch)
    page.once_failed = {"/b.png"}

    async def scenario():
        async with compose(tmp_path) as service:
            result = await service.workflow(URL, workflow_retry_delay=0)
            assert [a.status for a in result.items[0].attempts] == ["partial", "success"]
            assert Counter(page.requests)[URL + "a.png"] == 1
            assert Counter(page.requests)[URL + "b.png"] == 2
            page.failed = {"/b.png"}
            unfinished = await service.workflow(URL, download_scope="all", workflow_retries=0)
            assert unfinished.items[0].status == "partial"
            page.failed.clear()
            retried = await service.workflow(URL, workflow_retries=0)
            assert retried.items[0].reasons == ("unfinished",)
            assert retried.items[0].status == "success"

    asyncio.run(scenario())


def test_generic_dry_run_checks_only_feed_and_leaves_history_unchanged(tmp_path, monkeypatch):
    page = Page(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("target manifest must not be inspected by dry-run")

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", forbidden)

    async def scenario():
        async with compose(tmp_path) as service:
            before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p.suffix != ".lock"}
            first = await service.plan_workflow(URL)
            second = await service.plan_workflow(URL)
            assert first.selected_urls == second.selected_urls == (URL,)
            assert page.requests == [URL, URL]
            after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p.suffix != ".lock"}
            assert after == before
            assert WorkflowStateService(service.config).list_runs() == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["check_updates", "workflow", "plan_workflow"])
def test_non_update_plugin_returns_specific_safe_error(tmp_path, operation):
    class DownloadOnly:
        def auth_flow(self, context):
            return None

    async def scenario():
        async with compose(tmp_path) as service:
            record = service.registry.records["core.generic-html"]
            service.registry.resolve = lambda *args, **kwargs: (record, DownloadOnly())
            with pytest.raises(UpdateCheckUnsupportedError) as error:
                await getattr(service, operation)(URL)
            assert error.value.code == "update_check_unsupported"
            if operation == "workflow":
                result = error.value.workflow_result
                assert result.stop_error.reason == "selected plugin does not support update checks"
                assert WorkflowStateService(service.config).get_run(result.run_id).exit_code == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("allow_empty", [False, True])
def test_zero_chapter_manifest_obeys_allow_empty_setting(tmp_path, monkeypatch, allow_empty):
    Page(monkeypatch)

    async def inspect(self, url, context):
        return DownloadManifest("empty", ())

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", inspect)

    async def scenario():
        async with compose(tmp_path, allow_empty=allow_empty) as service:
            result = await service.workflow(URL, workflow_retries=0)
            assert result.items[0].status == ("success" if allow_empty else "failed")
            if allow_empty:
                assert not (await service.run(URL)).saved_files
            else:
                with pytest.raises(PluginError):
                    await service.run(URL)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ("https://user:pass@example.test/p?x=a%2Fb&x=c+d#fragment", URL + "a.png", URL + "p?x=a%2Fb&x=c+d"),
        ("https://user:pass@example.test/p?token=private#fragment", "https://cdn.test/a.png", URL),
        ("https://example.test/p", "https://cdn.example.test/a.png", URL),
        ("https://example.test/p", "http://example.test/a.png", None),
        ("https://example.test/p", "http://localhost/a.png", None),
        ("http://example.test/p?token=private#fragment", "https://example.test/a.png", "http://example.test/"),
        ("http://example.test/p?token=private#fragment", "http://cdn.test/a.png", "http://example.test/"),
        ("http://example.test:80/p?x=1", "http://EXAMPLE.test/a.png", "http://example.test/p?x=1"),
        ("https://EXAMPLE.test:443/p", URL + "a.png", URL + "p"),
        ("https://example.test:8443/p", "https://example.test:8443/a.png", "https://example.test:8443/p"),
        ("https://example.test:8443/p", URL + "a.png", "https://example.test:8443/"),
        ("https://example.test:0/p", URL + "a.png", "https://example.test:0/"),
        ("https://example.test:0/p", "https://example.test:0/a.png", "https://example.test:0/p"),
        ("https://[2001:db8::1]:8443/p?q=x#f", "https://[2001:db8::1]:8443/a.png", "https://[2001:db8::1]:8443/p?q=x"),
        ("https://[2001:db8::1]/p", "https://[2001:0db8:0:0:0:0:0:1]/a.png", "https://[2001:db8::1]/p"),
        ("https://[2001:db8::1]:8443/p", URL + "a.png", "https://[2001:db8::1]:8443/"),
        ("https://bücher.test/p?q=x#f", "https://xn--bcher-kva.test/a.png", "https://xn--bcher-kva.test/p?q=x"),
        ("https://bücher.test/p?q=x#f", "https://cdn.test/a.png", "https://xn--bcher-kva.test/"),
    ],
)
def test_automatic_referer_limits_disclosure_without_rewriting_queries(source, target, expected):
    assert _automatic_referer(source, target) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        "/relative",
        "file:///p",
        "https:///p",
        "https://[",
        "https://host.test:abc/p",
        "https://host.test:-1/p",
        "https://host.test:65536/p",
        "https://host.test/a\nb",
    ],
)
@pytest.mark.parametrize("side", ["source", "target"])
def test_unusable_urls_do_not_generate_automatic_referers(invalid, side):
    source, target = (invalid, URL + "a.png") if side == "source" else (URL, invalid)
    assert _automatic_referer(source, target) is None


def _mock_http(monkeypatch, handler):
    client_class = httpx.AsyncClient

    def client(*args, **kwargs):
        return client_class(*args, **dict(kwargs, transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(httpx, "AsyncClient", client)


@pytest.fixture
def tiny_png():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    return output.getvalue()


SECRET_PAGE = (
    "https://audit-user:audit-pass@gallery.test/works/page?token=audit-token&tag=a%2Fb&tag=c+d#private=audit-fragment"
)
EXPLICIT_REFERER = "https://explicit-user:explicit-pass@configured.test/p?token=explicit-token#explicit-fragment"


@pytest.mark.parametrize(
    ("target", "default_referer", "automatic", "sent"),
    [
        (
            "https://gallery.test/a.png",
            None,
            "https://gallery.test/works/page?token=audit-token&tag=a%2Fb&tag=c+d",
            "https://gallery.test/works/page?token=audit-token&tag=a%2Fb&tag=c+d",
        ),
        ("https://asset.test/a.png", None, "https://gallery.test/", "https://gallery.test/"),
        ("http://asset.test/a.png", None, None, None),
        ("http://asset.test/a.png", EXPLICIT_REFERER, None, EXPLICIT_REFERER),
    ],
)
def test_generic_inspection_preview_and_sent_headers_follow_automatic_policy(
    tmp_path, monkeypatch, tiny_png, target, default_referer, automatic, sent
):
    fetched = []

    def respond(request):
        if request.url.path == "/works/page":
            return httpx.Response(
                200,
                text=f'<meta name="referrer" content="unsafe-url"><img src="{target}" referrerpolicy="unsafe-url">',
                headers={"Referrer-Policy": "unsafe-url"},
            )
        fetched.append((str(request.url), request.headers.get("referer")))
        return httpx.Response(200, content=tiny_png, headers={"content-type": "image/png"})

    _mock_http(monkeypatch, respond)

    async def scenario():
        network = {"headers": {"Referer": default_referer}} if default_referer else None
        async with compose(tmp_path, network=network) as service:
            debug_path = tmp_path / "debug.log"
            service.logger.sinks.append(DebugFileSink(debug_path))
            inspection = await service.inspect(SECRET_PAGE)
            image = inspection.manifest.chapters[0].images[0]
            resolution = inspection.image_requests[0]
            assert image.url == target and image.referer == automatic
            assert resolution.request.url == target and resolution.request.referer == automatic
            preview_headers = {h.name.lower(): h.value for h in resolution.effective_request.headers}
            assert preview_headers.get("referer") == sent
            assert fetched == []  # Resolving the image request only previews it.
            result = await service.run(SECRET_PAGE)
            assert len(result.saved_files) == 1 and not result.failures
            assert Path(result.saved_files[0]).read_bytes() == tiny_png
        logs = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.log"))
        assert "operation_started" in logs
        assert all(secret not in logs for secret in ("audit-user", "audit-pass", "audit-token", "audit-fragment"))

    asyncio.run(scenario())
    assert fetched == [(target, sent)]


@pytest.mark.parametrize("explicit", ["header", "field", "default"])
def test_explicit_referers_retain_their_values_and_header_precedence(monkeypatch, explicit):
    seen = []

    def respond(request):
        seen.append(request.headers.get("referer"))
        return httpx.Response(200, content=b"ok")

    _mock_http(monkeypatch, respond)

    async def scenario():
        gateway = RequestGateway(AppConfig.model_validate({"network": {"headers": {"Referer": EXPLICIT_REFERER}}}))
        try:
            image = ImageResource(
                "https://asset.test/a.png",
                referer=SECRET_PAGE if explicit in {"header", "field"} else None,
                headers={"rEfErEr": EXPLICIT_REFERER} if explicit == "header" else {},
            )
            request = await GenericHtmlPlugin().create_image_request(image, None)
            assert request.referer == image.referer and request.headers == image.headers
            expected = SECRET_PAGE if explicit == "field" else EXPLICIT_REFERER
            operation = gateway.operation(plugin_id=None, operation_url=request.url)
            preview = await operation.preview(request)
            assert {h.name.lower(): h.value for h in preview.headers}["referer"] == expected
            await operation.execute(request)
            assert seen == [expected]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_generic_subclasses_and_shared_parser_retain_legacy_referers():
    class IndependentPlugin(GenericHtmlPlugin):
        pass

    html = '<base href="https://asset.test/"><img src="a.png">'

    async def execute(spec):
        return SimpleNamespace(url=SECRET_PAGE, body=html.encode(), headers={})

    context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
    plugin = IndependentPlugin()
    manifest = asyncio.run(plugin.inspect(URL, context))
    image = manifest.chapters[0].images[0]
    assert image.url == "https://asset.test/a.png" and image.referer == SECRET_PAGE
    assert asyncio.run(plugin.create_image_request(image, context)).referer == SECRET_PAGE
    parser = _ImageParser(SECRET_PAGE)
    parser.feed(html)
    parser.close()
    assert parser.images == [image]


@pytest.mark.parametrize("base_first", [True, False])
def test_page_redirect_and_cross_origin_base_keep_the_final_page_as_referrer(
    tmp_path, monkeypatch, tiny_png, base_first
):
    seen = []

    def respond(request):
        seen.append((str(request.url), request.headers.get("referer")))
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/new/gallery/?token=private#fragment"})
        if request.url.path == "/new/gallery/":
            base, image = '<base href="https://asset.test/images/">', '<img src="a.png">'
            return httpx.Response(200, text=base + image if base_first else image + base)
        if request.url.path == "/images/a.png":
            return httpx.Response(302, headers={"location": "http://final.test/a.png"})
        return httpx.Response(200, content=tiny_png, headers={"content-type": "image/png"})

    _mock_http(monkeypatch, respond)

    async def scenario():
        async with compose(tmp_path) as service:
            result = await service.run(URL)
            assert len(result.saved_files) == 1 and not result.failures

    asyncio.run(scenario())
    assert len(seen) == 4
    assert seen[2] == ("https://asset.test/images/a.png", URL)
    assert seen[3] == ("http://final.test/a.png", None)


def test_cdn_requiring_full_referer_fails_without_an_unsafe_fallback(tmp_path, monkeypatch):
    seen = []

    def respond(request):
        if request.url.host == "gallery.test":
            return httpx.Response(200, text='<img src="https://asset.test/a.png">')
        seen.append(request.headers.get("referer"))
        return httpx.Response(403, text="full page referrer required")

    _mock_http(monkeypatch, respond)

    async def scenario():
        async with compose(tmp_path) as service:
            with pytest.raises(AuthenticationError, match="authentication is required"):
                await service.run(SECRET_PAGE)

    asyncio.run(scenario())
    assert seen == ["https://gallery.test/"]


@pytest.mark.parametrize(("host", "retained_a_requests"), [("asset.test", 1), ("example.test", 2)])
def test_retry_compares_effective_referers_when_page_query_changes(
    tmp_path, monkeypatch, tiny_png, host, retained_a_requests
):
    fetched = Counter()
    page_calls = 0

    def respond(request):
        nonlocal page_calls
        if request.url.path == "/":
            page_calls += 1
            token = "first" if page_calls <= 2 else "second"
            return httpx.Response(302, headers={"location": f"/gallery?token={token}"})
        if request.url.path == "/gallery":
            return httpx.Response(200, text=f'<img src="https://{host}/a.png"><img src="https://{host}/b.png">')
        fetched[request.url.path] += 1
        if request.url.path == "/b.png" and fetched[request.url.path] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=tiny_png, headers={"content-type": "image/png"})

    _mock_http(monkeypatch, respond)

    async def scenario():
        async with compose(tmp_path) as service:
            result = await service.workflow(URL, workflow_retry_delay=0)
            assert [a.status for a in result.items[0].attempts] == ["partial", "success"]
            assert fetched == {"/a.png": retained_a_requests, "/b.png": 2}

    asyncio.run(scenario())


def test_referer_changes_do_not_change_the_generic_image_list_revision():
    async def scenario():
        async def execute(spec):
            return SimpleNamespace(url=spec.url, body=b'<img src="https://asset.test/a.png">', headers={})

        context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
        builtin, legacy = GenericHtmlPlugin(), type("IndependentPlugin", (GenericHtmlPlugin,), {})()
        current_manifest = await builtin.inspect(SECRET_PAGE, context)
        legacy_manifest = await legacy.inspect(SECRET_PAGE, context)
        assert current_manifest.chapters[0].images[0].referer == "https://gallery.test/"
        assert legacy_manifest.chapters[0].images[0].referer == SECRET_PAGE
        assert (await builtin.check_updates(SECRET_PAGE, context)).candidates == (
            await legacy.check_updates(SECRET_PAGE, context)
        ).candidates

    asyncio.run(scenario())
