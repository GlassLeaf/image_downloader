from __future__ import annotations

import asyncio
import io
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from image_downloader import (
    AppConfig,
    DownloadManifest,
    PluginError,
    RuntimeComposer,
    UpdateCheckUnsupportedError,
    WorkflowStateService,
)
from image_downloader.plugins.builtin import GenericHtmlPlugin

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


def compose(tmp_path, *, allow_empty=False):
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(tmp_path / "plugins")},
            "logging": {"console": {"enabled": False}},
            "network": {"max_attempts": 1},
            "download": {"allow_empty_chapter_manifest": allow_empty},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path, plugin_root=tmp_path / "plugins").compose()


def revision(html):
    async def execute(spec):
        return SimpleNamespace(url=spec.url, body=html.encode())

    context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
    return asyncio.run(GenericHtmlPlugin().check_updates(URL, context))


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<img src="a.png">', "https://example.test/new/gallery/a.png"),
        ('<base href="../images/"><img src="a.png">', "https://example.test/new/images/a.png"),
        ('<base href="//cdn.example.test/assets/"><img src="a.png">', "https://cdn.example.test/assets/a.png"),
        ('<img src="a.png"><base href="/assets/">', "https://example.test/assets/a.png"),
        ('<base href="/first/"><base href="/second/"><img src="a.png">', "https://example.test/first/a.png"),
        ('<base href=""><base href="/second/"><img src="a.png">', "https://example.test/new/gallery/a.png"),
        ('<base href="file:///images/"><img src="a.png">', "https://example.test/new/gallery/a.png"),
        ('<base href="https://["><img src="a.png">', "https://example.test/new/gallery/a.png"),
        ('<base href="/assets/"><img src="https://other.test/a.png">', "https://other.test/a.png"),
    ],
)
def test_generic_resolves_images_against_final_response_and_first_base(html, expected):
    final_url = "https://example.test/new/gallery/"

    async def execute(spec):
        assert spec.url == URL
        return SimpleNamespace(url=final_url, body=html.encode())

    context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
    manifest = asyncio.run(GenericHtmlPlugin().inspect(URL, context))
    image = manifest.chapters[0].images[0]
    assert image.url == expected
    assert image.referer == final_url
    request = asyncio.run(GenericHtmlPlugin().create_image_request(image, context))
    assert request.url == expected and request.referer == final_url


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
