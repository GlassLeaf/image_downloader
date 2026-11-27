from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from support.additional_files import URL, install, png, service

from image_downloader import (
    PLUGIN_API_FEATURES,
    AdditionalFileHookPoint,
    AdditionalFileSpec,
    AuthLease,
    Chapter,
    DownloadManifest,
    DownloadRequest,
    ImageResource,
    InspectRequest,
    PluginDataView,
    PluginError,
    PluginExecutionContext,
    RequestSpec,
    TransformContext,
)
from image_downloader.commands.inspect import _inspection_json
from image_downloader.plugins.builtin import GenericHtmlPlugin
from image_downloader.plugins.plugin_invoker import PluginInvoker


@pytest.mark.parametrize(
    "factory",
    [
        lambda data: ImageResource("image:1", plugin_data=data),
        lambda data: Chapter(1, "chapter", plugin_data=data),
        lambda data: DownloadManifest("work", (), plugin_data=data),
        lambda data: AuthLease(public_data=data),
        lambda data: PluginDataView(work=data),
    ],
)
def test_structured_values_are_detached_and_deeply_immutable(factory):
    data = {"nested": {"array": [None, True, 3, 1.5, "text"]}}
    dto = factory(data)
    frozen = (
        dto.work
        if isinstance(dto, PluginDataView)
        else (dto.public_data if isinstance(dto, AuthLease) else dto.plugin_data)
    )
    data["nested"]["array"].append("later")
    assert frozen["nested"]["array"] == (None, True, 3, 1.5, "text")
    with pytest.raises(TypeError):
        frozen["nested"]["changed"] = True
    assert replace(dto) == dto


@pytest.mark.parametrize(
    "data",
    [
        [],
        {1: "value"},
        {"nested": {1: "value"}},
        {"x": b"bytes"},
        {"x": object()},
        {"x": {1, 2}},
        {"x": float("inf")},
        {"x": float("nan")},
    ],
)
def test_non_json_values_are_rejected_without_echoing_values(data):
    with pytest.raises((TypeError, ValueError), match="plugin data"):
        ImageResource("image:1", plugin_data=data)


def test_cycles_are_rejected_but_shared_subtrees_are_allowed():
    items = []
    items.append(items)
    with pytest.raises(ValueError, match="cycles"):
        Chapter(1, "chapter", plugin_data={"items": items})
    shared = [1, 2]
    assert Chapter(1, "chapter", plugin_data={"a": shared, "b": shared}).plugin_data == {
        "a": (1, 2),
        "b": (1, 2),
    }


def test_keyword_only_compatibility_views_and_contexts():
    assert {"structured_plugin_data", "auth_snapshot"} <= PLUGIN_API_FEATURES
    image = ImageResource("image:1", plugin_data={"same": "image"})
    chapter = Chapter(1, "chapter", images=(image,), plugin_data={"same": "chapter"})
    work = DownloadManifest("work", (chapter,), plugin_data={"same": "work"})
    for cls in (ImageResource, Chapter, DownloadManifest):
        assert inspect.signature(cls).parameters["plugin_data"].kind is inspect.Parameter.KEYWORD_ONLY
        assert "plugin_data" not in cls.__match_args__
    assert inspect.signature(AuthLease).parameters["public_data"].kind is inspect.Parameter.KEYWORD_ONLY
    view = PluginDataView.from_resources(work, chapter, image)
    assert view.work is work.plugin_data and view.chapter is chapter.plugin_data and view.image is image.plugin_data
    context = PluginExecutionContext({}, {}, {"id": "example.plugin"}, None, object())
    bound = context._with_plugin_data(view)
    assert context.plugin_data == PluginDataView()
    assert bound.requests is context.requests and bound.plugin_manifest is context.manifest
    assert bound.plugin_data is view
    transformed = TransformContext(image, {}, {}, {}, None, work, chapter)
    assert transformed.plugin_data == view
    assert not hasattr(transformed, "auth")


def test_invoker_rejects_tampered_plugin_data():
    work = DownloadManifest("work", ())
    object.__setattr__(work, "plugin_data", {"x": b"not-json"})

    class Plugin:
        async def inspect(self, url, context):
            return work

    with pytest.raises(PluginError, match="structured plugin data"):
        asyncio.run(PluginInvoker("test.structured").inspect(Plugin(), URL, None))


def test_parallel_download_recovery_and_additional_callbacks_receive_scoped_views(tmp_path, monkeypatch):
    seen = {"request": [], "recover": [], "transform": [], "additional": [], "received": [], "saved": []}
    images = tuple(
        ImageResource(f"https://example.test/start/{i}", index=i % 2 + 1, plugin_data={"same": i}) for i in range(4)
    )
    chapters = tuple(
        Chapter(i + 1, f"chapter {i}", images=images[i * 2 : i * 2 + 2], plugin_data={"same": i}) for i in range(2)
    )
    work = DownloadManifest("work", chapters, plugin_data={"same": "work"})

    def check(context, image=None, chapter=None):
        view = context.plugin_data
        assert view.work == {"same": "work"}
        if image is not None:
            number = image.plugin_data["same"]
            assert view.image == {"same": number}
            assert view.chapter == {"same": number // 2}
        elif chapter is not None:
            assert view.chapter is chapter.plugin_data and not view.image
        return view

    async def manifest(self, url, context):
        assert context.plugin_data == PluginDataView()
        return work

    async def request(self, image, context):
        before = check(context, image)
        await asyncio.sleep(0)
        assert check(context, image) is before
        seen["request"].append(before)
        return RequestSpec(image.url)

    async def recover(self, image, failed, response, context):
        seen["recover"].append(check(context, image))
        return RequestSpec(image.url.replace("/start/", "/final/"))

    def transform(self, artifact, context):
        assert context.plugin_data.chapter["same"] == context.plugin_data.image["same"] // 2
        seen["transform"].append(context.plugin_data)
        return artifact

    async def provider(self, hook, context):
        if hook.manifest is None:
            assert context.plugin_data == PluginDataView()
        else:
            check(context, hook.image, hook.chapter)
        seen["additional"].append(context.plugin_data)
        return (AdditionalFileSpec("data", f"{hook.point}-{hook.image.index if hook.image else 0}.txt", data=b"data"),)

    async def receive(self, result, context):
        if result.hook.manifest:
            check(context, result.hook.image, result.hook.chapter)
        seen["received"].append(context.plugin_data)

    async def save(self, result, context):
        check(context, result.hook.image, result.hook.chapter)
        seen["saved"].append(context.plugin_data)

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", manifest)
    monkeypatch.setattr(GenericHtmlPlugin, "create_image_request", request)
    monkeypatch.setattr(GenericHtmlPlugin, "recover_image_request", recover)
    monkeypatch.setattr(GenericHtmlPlugin, "transform_image", transform)
    install(monkeypatch, tuple(AdditionalFileHookPoint), provider, receive, save)
    body = png()

    def handler(request):
        return httpx.Response(
            404 if "/start/" in request.url.path else 200, content=body, headers={"content-type": "image/png"}
        )

    async def scenario():
        runtime = await service(
            tmp_path, handler, download={"chapter_concurrency": 2, "image_concurrency_per_chapter": 2}
        )
        try:
            result = await runtime.download(DownloadRequest(url=URL))
            assert len(result.saved_files) == 4 and not result.failures
            assert len(seen["request"]) == len(seen["recover"]) == len(seen["transform"]) == 4
            assert seen["received"] and seen["saved"] and not [x for x in result.additional_files if x.error]
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_inspection_scoped_views_and_optional_json_fields(tmp_path, monkeypatch):
    image = ImageResource("https://example.test/image", plugin_data={"n": 7, "nested": [1, 2]})
    chapter = Chapter(1, "chapter", images=(image,), plugin_data={"n": "chapter"})
    work = DownloadManifest("work", (chapter,), plugin_data={"n": "work"})

    async def manifest(self, url, context):
        return work

    async def request(self, value, context):
        assert context.plugin_data == PluginDataView.from_resources(work, chapter, image)
        return RequestSpec(value.url)

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", manifest)
    monkeypatch.setattr(GenericHtmlPlugin, "create_image_request", request)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            result = await runtime.inspect(InspectRequest(url=URL, resolve_image_requests=True))
            assert not result.failures
            full = _inspection_json(result, "all")["manifest"]
            assert full["plugin_data"] == {"n": "work"}
            assert full["chapters"][0]["plugin_data"] == {"n": "chapter"}
            assert full["chapters"][0]["images"][0]["plugin_data"] == {"n": 7, "nested": [1, 2]}
        finally:
            await runtime.close()
        empty = SimpleNamespace(
            manifest=DownloadManifest("old", (Chapter(1, "old", images=(ImageResource("old"),)),)),
            source_url=URL,
            plugin_id="old.plugin",
            request_resolution_performed=False,
            image_requests=(),
        )
        old = _inspection_json(empty, "all")["manifest"]
        assert "plugin_data" not in old and "plugin_data" not in old["chapters"][0]
        assert "plugin_data" not in old["chapters"][0]["images"][0]

    asyncio.run(scenario())
