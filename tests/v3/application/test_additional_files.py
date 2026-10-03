from __future__ import annotations

import asyncio
import io
import json
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from PIL import Image

from image_downloader import (
    AdditionalFileHookPoint as Point,
)
from image_downloader import (
    AdditionalFileSpec as Spec,
)
from image_downloader import (
    AppConfig,
    Chapter,
    DownloadManifest,
    ImageFetchRequest,
    ImageResource,
    RequestSpec,
    RuntimeComposer,
)
from image_downloader.application.additional_files import additional_file_payload
from image_downloader.exceptions import AuthenticationError
from image_downloader.plugins.builtin import GenericHtmlPlugin
from image_downloader.storage import FileSystem

URL = "https://example.test/gallery"


def png():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, "PNG")
    return stream.getvalue()


async def service(root, handler=None, **overrides):
    config = {
        "storage": {"data_root": str(root / "data")},
        "plugins": {"root": str(root / "plugins")},
        "logging": {"console": {"enabled": False}},
        "output": {"directory_format": "%CHAPTER_NUMBER%", "existing_file": "skip"},
        "network": {"max_attempts": 1},
    }
    config.update(overrides)
    result = RuntimeComposer(AppConfig.model_validate(config), config_root=root, plugin_root=root / "plugins").compose()
    await result.gateway.client.aclose()
    data = png()

    def default(request):
        if request.url.path == "/gallery":
            return httpx.Response(200, text="<img src='/image.png'>", request=request)
        return httpx.Response(200, content=data, headers={"content-type": "image/png"}, request=request)

    result.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler or default))
    return result


def install(monkeypatch, points, provider, received=None, saved=None):
    monkeypatch.setattr(GenericHtmlPlugin, "additional_file_hook_points", lambda self, context: points, raising=False)
    monkeypatch.setattr(GenericHtmlPlugin, "additional_files", provider, raising=False)
    if received:
        monkeypatch.setattr(GenericHtmlPlugin, "additional_file_received", received, raising=False)
    if saved:
        monkeypatch.setattr(GenericHtmlPlugin, "additional_file_saved", saved, raising=False)


def test_all_stages_raw_data_and_cleanup_order(tmp_path, monkeypatch):
    seen, received, saved = [], [], []

    async def provider(self, hook, context):
        seen.append((hook.point, hook.chapter.number if hook.chapter else None))
        return (Spec("raw", f"metadata/{hook.point}.json", data=b'{ "raw": 1 }'),)

    async def receive(self, result, context):
        received.append(result)

    async def save(self, result, context):
        saved.append(result)

    async def manifest(self, url, context):
        assert len(received) == 1 and saved == []
        return DownloadManifest(
            "test",
            tuple(
                Chapter(i, "chapter", images=(ImageResource(f"https://example.test/{i}.png", image_id=str(i)),))
                for i in (1, 2)
            ),
        )

    async def cleanup(self):
        assert len([p for p, _ in seen if p == Point.AFTER_DOWNLOAD]) == 2

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", manifest)
    monkeypatch.setattr(GenericHtmlPlugin, "cleanup_after_use", cleanup, raising=False)
    install(monkeypatch, tuple(reversed(tuple(Point))), provider, receive, save)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            result = await runtime.run(URL)
            assert len(result.saved_files) == 2
            assert not result.failures
            assert len(received) == 13 and len(saved) == 14
            assert seen[0] == (Point.BEFORE_MANIFEST, None)
            assert seen[1:3] == [(Point.AFTER_MANIFEST, 1), (Point.AFTER_MANIFEST, 2)]
            assert all(x.status == "saved" and x.data == b'{ "raw": 1 }' for x in saved)
            before = [x for x in saved if x.hook.point == Point.BEFORE_MANIFEST]
            assert [x.hook.chapter.number for x in before] == [1, 2]
            for chapter in (1, 2):
                image_points = [p for p, c in seen if c == chapter]
                assert image_points == list(Point)[1:]
            for item in saved:
                assert Path(item.path).read_bytes() == item.data
            assert all("raw" not in str(asdict(x)) or "data" not in asdict(x) for x in result.additional_files)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_received_key_used_for_request_and_transform(tmp_path, monkeypatch):
    async def provider(self, hook, context):
        return (Spec("key", "metadata/key.json", request=RequestSpec("https://example.test/key")),)

    async def receive(self, result, context):
        assert result.response.status == 200
        self.keys = getattr(self, "keys", {})
        self.keys[result.hook.image.url] = json.loads(result.data)["key"]

    async def request(self, image, context):
        key = self.keys[image.url]
        return ImageFetchRequest(RequestSpec(image.url, headers={"X-Key": key}), {"key": key})

    async def transform(self, artifact, context):
        assert context.transport_metadata.plugin_data["key"] == "private-test-key"
        return artifact

    data = png()

    def handler(request):
        if request.url.path == "/gallery":
            return httpx.Response(200, text="<img src='/image.png'>", request=request)
        if request.url.path == "/key":
            return httpx.Response(200, json={"key": "private-test-key"}, request=request)
        assert request.headers["X-Key"] == "private-test-key"
        return httpx.Response(200, content=data, headers={"content-type": "image/png"}, request=request)

    install(monkeypatch, (Point.BEFORE_IMAGE_REQUEST,), provider, receive)
    monkeypatch.setattr(GenericHtmlPlugin, "create_image_request", request)
    monkeypatch.setattr(GenericHtmlPlugin, "transform_image", transform)

    async def scenario():
        runtime = await service(tmp_path, handler)
        try:
            result = await runtime.run(URL)
            assert len(result.saved_files) == 1
            assert "private-test-key" not in json.dumps(additional_file_payload(result, runtime.outputs.root))
            assert "private-test-key" not in next((tmp_path / "data").rglob("debug.log")).read_text()
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_skip_callback_observes_existing_content(tmp_path, monkeypatch):
    received, saved = [], []

    async def provider(self, hook, context):
        return (Spec("item", "metadata/data.dat", data=b"new"),)

    async def receive(self, result, context):
        received.append(result.data)

    async def save(self, result, context):
        saved.append(result)

    install(monkeypatch, (Point.AFTER_IMAGE_SAVE,), provider, receive, save)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            first = await runtime.run(URL)
            Path(saved[0].path).write_bytes(b"old")
            second = await runtime.run(URL)
            assert len(first.saved_files) == 1 and len(second.skipped_files) == 1
            assert received == [b"new", b"new"]
            assert saved[1].status == "skipped" and saved[1].data == b"old"
            assert saved[1].hook.image_outcome.kind == "skipped"
            assert any(x.phase == "read" and x.status == "completed" for x in second.additional_files)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def inject_file_failure(monkeypatch, kind):
    if kind == "save":
        original = FileSystem.write_bytes_atomic

        def write(self, path, data):
            if Path(path).name == "x":
                raise OSError("secret-file-error")
            return original(self, path, data)

        monkeypatch.setattr(FileSystem, "write_bytes_atomic", write)
    if kind == "read":
        monkeypatch.setattr(FileSystem, "read_bytes_bounded", lambda *a: (_ for _ in ()).throw(OSError("read failed")))


@pytest.mark.parametrize("kind", ["receive", "save", "read", "received_callback", "saved_callback", "path", "return"])
def test_optional_failures_preserve_images(tmp_path, monkeypatch, kind):
    callbacks = []

    async def provider(self, hook, context):
        if kind == "return":
            return [Spec("file", "meta/x", data=b"x")]
        return (
            Spec(
                "file",
                "../escape" if kind == "path" else "meta/x",
                request=RequestSpec("https://example.test/missing") if kind == "receive" else None,
                data=None if kind == "receive" else b"x",
            ),
        )

    async def receive(self, result, context):
        callbacks.append(result)
        if kind == "received_callback":
            raise RuntimeError("secret-callback-text")

    async def saved(self, result, context):
        callbacks.append(result)
        if kind == "saved_callback":
            return "invalid"

    install(monkeypatch, (Point.AFTER_IMAGE_SAVE,), provider, receive, saved)
    inject_file_failure(monkeypatch, kind)
    data = png()

    def handler(request):
        if request.url.path == "/gallery":
            return httpx.Response(200, text="<img src='/image.png'>", request=request)
        return httpx.Response(
            404 if request.url.path == "/missing" else 200,
            content=data,
            headers={"content-type": "image/png"},
            request=request,
        )

    async def scenario():
        runtime = await service(tmp_path, handler)
        try:
            await runtime.run(URL)
            result = await runtime.run(URL)
            assert not result.failures and len(result.skipped_files) == 1
            phase = "hook" if kind in {"path", "return"} else kind
            assert any(x.phase == phase and x.status == "failed" for x in result.additional_files)
            payload = json.dumps(additional_file_payload(result, runtime.outputs.root))
            assert "secret-" not in payload
            if kind == "read":
                assert callbacks[-1].status == "skipped" and callbacks[-1].data is None
                assert callbacks[-1].read_error is not None
            if kind == "received_callback":
                assert any(x.phase == "save" and x.status == "skipped" for x in result.additional_files)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_workflow_retains_image_results_and_history(tmp_path, monkeypatch):
    received = []
    counts = {}

    async def provider(self, hook, context):
        return (Spec("file", f"meta/{hook.image.index}.json", data=b"private-extra-payload"),)

    async def receive(self, result, context):
        received.append((result.hook.image.index, result.hook.attempt_number))

    install(monkeypatch, (Point.BEFORE_IMAGE_REQUEST,), provider, receive)
    data = png()

    def handler(request):
        name = request.url.path
        counts[name] = counts.get(name, 0) + 1
        if name == "/gallery":
            return httpx.Response(200, text="<img src='/good.png'><img src='/retry.png'>", request=request)
        status = 503 if name == "/retry.png" and counts[name] == 1 else 200
        return httpx.Response(status, content=data, headers={"content-type": "image/png"}, request=request)

    async def scenario():
        from image_downloader.configuration.paths import resolve_paths
        from image_downloader.storage.workflow_history import WorkflowHistoryStore

        runtime = await service(tmp_path, handler)
        try:
            result = await runtime.workflow(URL, workflow_retries=1, workflow_retry_delay=0)
            assert result.items[0].status == "success"
            assert sorted(received) == [(1, 1), (2, 1), (2, 2)]
            assert counts["/good.png"] == 1 and counts["/retry.png"] == 2
            outputs = result.items[0].download.additional_files
            assert any(x.image_index == 1 and x.attempt_number == 1 for x in outputs)
            assert not any(x.image_index == 2 and x.attempt_number == 1 for x in outputs)
            store = WorkflowHistoryStore(
                FileSystem(resolve_paths(runtime.config)["state"]), runtime.config.workflow_history
            )
            record = next(r for r in store.visible() if r.run_id == result.run_id)
            details = dict(record.details)
            assert details["items"][0]["additional_files"]
            assert "private-extra-payload" not in json.dumps(details, default=str)
            # Old histories omit the new fields and remain readable with empty lists.
            from image_downloader.storage.workflow_history_schema import HistoryDetails

            legacy = json.loads(json.dumps(details, default=lambda x: dict(x)))
            for item in legacy["items"]:
                item.pop("additional_files")
                for attempt in item["attempts"]:
                    attempt.pop("additional_files")
            validated = HistoryDetails.model_validate(legacy)
            assert validated.items[0].additional_files == []
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path", ["../escape", "C:/escape", "/escape", "\\\\server\\share\\x", "meta/x:ads", "meta/./x"]
)
def test_unsafe_paths_rejected_before_request(tmp_path, monkeypatch, path):
    async def provider(self, hook, context):
        return (Spec("id", path, request=RequestSpec("https://unexpected.test/file")),)

    install(monkeypatch, (Point.BEFORE_MANIFEST,), provider)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            result = await runtime.run(URL)
            assert len(result.saved_files) == 1
            assert result.additional_files[0].phase == "hook"
            assert result.additional_files[0].status == "failed"
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_byte_limit_and_missing_required_key(tmp_path, monkeypatch):
    received = []

    async def provider(self, hook, context):
        return (Spec("id", "meta/key", data=b"x" * 1000),)

    async def receive(self, result, context):
        received.append(result)

    async def image_request(self, image, context):
        raise AuthenticationError("required key is unavailable")

    install(monkeypatch, (Point.BEFORE_IMAGE_REQUEST,), provider, receive)
    monkeypatch.setattr(GenericHtmlPlugin, "create_image_request", image_request)

    async def scenario():
        runtime = await service(tmp_path, network={"max_attempts": 1, "max_response_bytes": 512})
        try:
            with pytest.raises(AuthenticationError):
                await runtime.run(URL)
            assert len(received) == 1 and received[0].status == "failed"
            assert received[0].data is None
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_cli_serializes_only_safe_outcomes(tmp_path, monkeypatch, capsys):
    from image_downloader.cli import main

    async def provider(self, hook, context):
        return (Spec("id", "meta/data", data=b"private-cli-payload"),)

    install(monkeypatch, (Point.AFTER_DOWNLOAD,), provider)
    original = httpx.AsyncClient

    def client(**kwargs):
        def handler(request):
            if request.url.path == "/gallery":
                return httpx.Response(200, text="<img src='/image.png'>", request=request)
            return httpx.Response(200, content=png(), headers={"content-type": "image/png"}, request=request)

        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    config = tmp_path / "app.yaml"
    config.write_text(
        json.dumps({"storage": {"data_root": str(tmp_path / "data")}, "plugins": {"root": str(tmp_path / "plugins")}})
    )
    assert main(["download", URL, "--config", str(config), "--json"]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert any(x["status"] == "saved" for x in result["additional_files"])
    assert "private-cli-payload" not in output.out + output.err


def test_cancelled_callback_propagates_and_cleans_temporary(tmp_path, monkeypatch):
    roots = []
    import image_downloader.application.additional_files as module

    original = module.tempfile.TemporaryDirectory

    def temporary(*a, **kw):
        value = original(*a, **kw)
        roots.append(Path(value.name))
        return value

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", temporary)

    async def provider(self, hook, context):
        return (Spec("first", "meta/x", data=b"x"), Spec("second", "meta/y", data=b"y"))

    async def receive(self, result, context):
        if result.file_id == "second":
            raise asyncio.CancelledError

    install(monkeypatch, (Point.BEFORE_MANIFEST,), provider, receive)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            with pytest.raises(asyncio.CancelledError):
                await runtime.run(URL)
            assert roots and all(not p.exists() for p in roots)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_inspection_and_update_do_not_call_provider(tmp_path, monkeypatch):
    async def provider(*args):
        raise AssertionError("must not execute")

    def declaration(*args):
        raise AssertionError("must not declare")

    install(monkeypatch, tuple(Point), provider)
    monkeypatch.setattr(GenericHtmlPlugin, "additional_file_hook_points", declaration)
    # Inspection/planning intentionally construct detached gateways of their own.
    original_client = httpx.AsyncClient

    def client(**kwargs):
        def response(request):
            return httpx.Response(200, text="<img src='/image.png'>", request=request)

        kwargs.setdefault("transport", httpx.MockTransport(response))
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            await runtime.inspect(URL)
            await runtime.check_updates(URL)
            await runtime.plan_workflow(URL)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_after_request_data_is_available_to_parallel_transforms(tmp_path, monkeypatch):
    waiting = asyncio.Event()
    seen = []

    async def provider(self, hook, context):
        assert hook.response is not None and hook.artifact is None
        seen.append(hook.invocation_id)
        if len(seen) == 2:
            waiting.set()
        await asyncio.wait_for(waiting.wait(), 5)
        return (Spec("metadata", f"meta/{hook.image.index}", data=str(hook.image.index).encode()),)

    async def receive(self, result, context):
        self.extra = getattr(self, "extra", {})
        self.extra[(result.hook.chapter.number, result.hook.image.index)] = result.data
        await asyncio.sleep(0)

    async def transform(self, artifact, context):
        assert self.extra[(context.chapter.number, context.index)] == str(context.index).encode()
        return artifact

    async def manifest(self, url, context):
        return DownloadManifest(
            "parallel",
            (Chapter(1, "one", images=tuple(ImageResource(f"https://example.test/{i}.png", index=i) for i in (1, 2))),),
        )

    install(monkeypatch, (Point.AFTER_IMAGE_REQUEST,), provider, receive)
    monkeypatch.setattr(GenericHtmlPlugin, "inspect", manifest)
    monkeypatch.setattr(GenericHtmlPlugin, "transform_image", transform)

    async def scenario():
        runtime = await service(tmp_path, download={"image_concurrency_per_chapter": 2})
        try:
            result = await runtime.run(URL)
            assert len(result.saved_files) == 2 and not result.failures
            assert len(set(seen)) == 2
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "declaration", [(Point.BEFORE_MANIFEST, Point.BEFORE_MANIFEST), [Point.BEFORE_MANIFEST], ("before_manifest",)]
)
def test_invalid_declaration_is_optional(tmp_path, monkeypatch, declaration):
    async def provider(*args):
        raise AssertionError("invalid declaration must disable provider")

    install(monkeypatch, declaration, provider)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            result = await runtime.run(URL)
            assert len(result.saved_files) == 1
            assert [(x.phase, x.status) for x in result.additional_files] == [("declaration", "failed")]
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_before_manifest_with_no_chapters_has_no_save_callback(tmp_path, monkeypatch):
    received = []

    async def provider(self, hook, context):
        return (Spec("file", "meta/x", data=b"x"),)

    async def receive(self, result, context):
        received.append(result)

    async def save(*args):
        raise AssertionError("no chapter means no save callback")

    async def manifest(*args):
        assert len(received) == 1
        return DownloadManifest("empty", ())

    install(monkeypatch, (Point.BEFORE_MANIFEST,), provider, receive, save)
    monkeypatch.setattr(GenericHtmlPlugin, "inspect", manifest)

    async def scenario():
        runtime = await service(tmp_path, download={"allow_empty_chapter_manifest": True})
        try:
            result = await runtime.run(URL)
            assert not result.saved_files and not result.failures
            assert result.additional_files[-1].status == "no_target"
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_additional_ledger_matches_stable_identity_not_numbers():
    from image_downloader import AdditionalFileHookContext, AdditionalFileOutcome, ImageOutcome, ImageOutcomeKind
    from image_downloader.application.workflow_retry import ImageLedger

    unchanged = ImageResource("https://example.test/good", image_id="good")
    changed = ImageResource("https://example.test/old", image_id="change")
    first = DownloadManifest(
        "book",
        (
            Chapter(1, "one", chapter_id="one", images=(unchanged,)),
            Chapter(1, "two", chapter_id="two", images=(changed,)),
        ),
    )
    ledger = ImageLedger(URL)
    ledger.start_attempt()
    ledger.begin(first, "site")
    for cp, image in enumerate((unchanged, changed)):
        ledger.record(cp, 0, ImageOutcome(image, ImageOutcomeKind.SAVED, "image.png"))
        hook = AdditionalFileHookContext(
            Point.AFTER_IMAGE_SAVE, ledger.operation_id, 1, URL, manifest=first, chapter=first.chapters[cp], image=image
        )
        ledger.record_additional(
            AdditionalFileOutcome(str(cp), hook.point, hook.operation_id, "call", 1, 1, 1, "save", "saved"), hook
        )
    ledger.start_attempt()
    second = DownloadManifest(
        "book",
        (
            Chapter(8, "one", chapter_id="one", images=(unchanged,)),
            Chapter(8, "two", chapter_id="two", images=(ImageResource("https://example.test/new", image_id="change"),)),
        ),
    )
    ledger.begin(second, "site")
    assert [(x.file_id, x.chapter_number) for x in ledger.additional_files] == [("0", 8)]


@pytest.mark.parametrize("source", ["bytes", "http", "existing"])
def test_all_additional_byte_sources_are_bounded(tmp_path, monkeypatch, source):
    saved = []

    async def provider(self, hook, context):
        if source == "http":
            return (Spec("id", "meta/x", request=RequestSpec("https://example.test/large")),)
        return (Spec("id", "meta/x", data=b"x" * (600 if source == "bytes" else 1)),)

    async def save(self, result, context):
        saved.append(result)

    def handler(request):
        if request.url.path == "/gallery":
            return httpx.Response(200, text="<img src='/image.png'>", request=request)
        data = b"x" * 600 if request.url.path == "/large" else png()
        return httpx.Response(200, content=data, headers={"content-type": "image/png"}, request=request)

    install(monkeypatch, (Point.AFTER_IMAGE_SAVE,), provider, saved=save)

    async def scenario():
        runtime = await service(tmp_path, handler, network={"max_attempts": 1, "max_response_bytes": 512})
        try:
            result = await runtime.run(URL)
            assert len(result.saved_files) == 1 and not result.failures
            if source == "existing":
                Path(saved[0].path).write_bytes(b"x" * 600)
                result = await runtime.run(URL)
                assert not result.failures and len(result.skipped_files) == 1
                assert saved[-1].status == "skipped" and saved[-1].data is None
                assert saved[-1].read_error is not None
                assert Path(saved[-1].path).stat().st_size == 600
                phase = "read"
            else:
                assert saved == []
                phase = "receive"
            assert any(x.phase == phase and x.status == "failed" for x in result.additional_files)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_after_download_runs_for_returned_image_failures(tmp_path, monkeypatch):
    seen = []

    async def provider(self, hook, context):
        assert sum(x.failure is not None for x in hook.chapter_result.outcomes) == 1
        seen.append(hook.point)
        return (Spec("summary", "meta/result", data=b"failed image"),)

    def handler(request):
        return (
            httpx.Response(200, text="<img src='/missing'>", request=request)
            if request.url.path == "/gallery"
            else httpx.Response(404, request=request)
        )

    install(monkeypatch, (Point.AFTER_DOWNLOAD,), provider)

    async def scenario():
        runtime = await service(tmp_path, handler)
        try:
            result = await runtime.run(URL)
            assert len(result.failures) == 1 and not result.saved_files
            assert seen == [Point.AFTER_DOWNLOAD]
            assert any(x.status == "saved" for x in result.additional_files)
        finally:
            await runtime.close()

    asyncio.run(scenario())
