"""Opt-in empty-result rejection without changing legacy download semantics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from inspect import Parameter, signature
from types import SimpleNamespace

import pytest
from support.workflow import FEED, A, Gallery, attach_transport, compose

import image_downloader.cli as cli
import image_downloader.commands.download as download_command
from image_downloader import (
    Chapter,
    DownloadManifest,
    DownloadRequest,
    ImageResource,
    InspectRequest,
    PluginError,
    WorkflowRequest,
    WorkflowStateService,
)
from image_downloader.application.workflow_verification import verify_workflow
from image_downloader.commands.inspect import _inspection_json
from image_downloader.observability.events import EventName
from image_downloader.plugins.plugin_invoker import PluginInvoker


class ManifestGallery(Gallery):
    candidates = (A,)

    def __init__(self, manifest):
        super().__init__()
        self.manifest = manifest
        self.image_requests = []

    async def inspect(self, url, context):
        self.seen.append(url)
        return self.manifest

    async def create_image_request(self, image, context):
        self.image_requests.append(image)
        return await super().create_image_request(image, context)


def observe_success(service):
    events, messages = [], []
    service.events.on(EventName.DOWNLOAD_SUCCESS, events.append)
    service.notifications.config = service.notifications.config.model_copy(
        update={"enabled": True, "notify_on": ("download_success",)}
    )

    async def send(title, message):
        messages.append(message)
        return True

    service.notifications.senders = {"desktop": SimpleNamespace(send=send)}
    return events, messages


@pytest.mark.parametrize("setting", [False, True])
@pytest.mark.parametrize("permission", [None, False, True])
def test_zero_chapters_setting_remains_authoritative(tmp_path, setting, permission):
    gallery = ManifestGallery(DownloadManifest("empty", (), allow_empty_chapters=permission))
    allowed = setting and permission is not False

    async def scenario():
        async with compose(tmp_path, gallery, download={"allow_empty_chapter_manifest": setting}) as service:
            events, messages = observe_success(service)
            if allowed:
                assert not (await service.download(DownloadRequest(url=A.url))).saved_files
            else:
                with pytest.raises(PluginError, match="^plugin returned an empty manifest$"):
                    await service.download(DownloadRequest(url=A.url))
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert result.items[0].status == ("success" if allowed else "failed")
            state = WorkflowStateService(service.config)
            assert state.get_workflow(FEED)[0].items[0].completed is allowed
            if allowed:
                verified = verify_workflow(state.get_run(result.run_id), service.outputs.root)
                assert verified["items"][0]["errors"][0]["code"] == "no_images"
                assert verified["items"][0]["status"] == "not_acquired"
            else:
                assert result.items[0].error.code == "plugin_error"
            assert len(events) == len(messages) == (2 if allowed else 0)
            assert gallery.image_requests == []
            assert gallery.cleanups == 2 + gallery.checks

    asyncio.run(scenario())


@pytest.mark.parametrize("permission", [None, False, True])
@pytest.mark.parametrize("with_image", [False, True])
@pytest.mark.parametrize("empty_first", [False, True])
def test_empty_images_are_decided_per_chapter_before_any_image_job(tmp_path, permission, with_image, empty_first):
    empty = Chapter(1, "empty", allow_empty_images=permission)
    full = Chapter(2, "full", images=(ImageResource(A.url + ".png"),), allow_empty_images=False)
    chapters = ((empty, full) if empty_first else (full, empty)) if with_image else (empty,)
    gallery = ManifestGallery(DownloadManifest("book", chapters, allow_empty_chapters=False))

    async def scenario():
        async with compose(tmp_path, gallery, existing_file="skip") as service:
            await attach_transport(service)
            events, messages = observe_success(service)
            if permission is False:
                with pytest.raises(PluginError, match="^plugin returned an empty image list$"):
                    await service.download(DownloadRequest(url=A.url))
                assert gallery.image_requests == []
                assert not tuple(service.outputs.root.rglob("*.png"))
            else:
                regular = await service.download(DownloadRequest(url=A.url))
                assert len(regular.saved_files) == int(with_image)
                assert not regular.failures
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            allowed = permission is not False
            assert result.items[0].status == ("success" if allowed else "failed")
            state = WorkflowStateService(service.config)
            assert state.get_workflow(FEED)[0].items[0].completed is allowed
            assert gallery.cleanups == 2 + gallery.checks
            assert len(events) == len(messages) == (2 if allowed else 0)
            if not allowed:
                assert gallery.image_requests == []
                assert not tuple(service.outputs.root.rglob("*.png"))
            else:
                assert len(result.items[0].download.skipped_files) == int(with_image)
                verified = verify_workflow(state.get_run(result.run_id), service.outputs.root)
                assert verified["status"] == ("passed" if with_image else "failed")
                if not with_image:
                    assert verified["items"][0]["errors"][0]["code"] == "no_images"

    asyncio.run(scenario())


def test_retry_rechecks_policy_and_preserves_existing_final_notification(tmp_path, monkeypatch):
    refused = DownloadManifest("empty", (Chapter(1, "empty", allow_empty_images=False),))
    allowed = replace(refused, chapters=(replace(refused.chapters[0], allow_empty_images=True),))
    gallery = ManifestGallery(refused)
    manifests = iter((refused, allowed))

    async def inspect(url, context):
        return next(manifests)

    gallery.inspect = inspect

    async def scenario():
        async with compose(tmp_path, gallery) as service:
            calls = []
            validate = service._downloads._validate_empty_manifest_policy

            def record(manifest):
                calls.append(manifest)
                validate(manifest)

            monkeypatch.setattr(service._downloads, "_validate_empty_manifest_policy", record)
            events, messages = observe_success(service)
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=1, workflow_retry_delay=0))
            assert [attempt.status for attempt in result.items[0].attempts] == ["failed", "success"]
            assert result.items[0].attempts[0].error.code == "plugin_error"
            assert calls == [replace(value, metadata={"source_url": A.url}) for value in (refused, allowed)]
            assert gallery.cleanups == len(calls) + gallery.checks
            assert len(events) == len(messages) == 1
            assert WorkflowStateService(service.config).get_workflow(FEED)[0].items[0].completed

    asyncio.run(scenario())


@pytest.mark.parametrize("manifest_permission", [None, False, True])
@pytest.mark.parametrize("chapter_permission", [None, False, True])
def test_inspect_accepts_empty_and_only_serializes_explicit_permissions(
    tmp_path, manifest_permission, chapter_permission
):
    chapter = Chapter(1, "empty", allow_empty_images=chapter_permission)
    manifest = DownloadManifest("book", (chapter,), allow_empty_chapters=manifest_permission)

    async def scenario():
        async with compose(tmp_path, ManifestGallery(manifest)) as service:
            result = await service.inspect(InspectRequest(url=A.url))
            assert result.manifest.allow_empty_chapters is manifest_permission
            assert result.manifest.chapters[0].allow_empty_images is chapter_permission
            payload = _inspection_json(result, "all")["manifest"]
            manifest_keys = {"title", "content_id", "author", "access", "revision", "metadata", "chapters"}
            chapter_keys = {"number", "title", "subtitle", "chapter_id", "images"}
            if manifest_permission is not None:
                manifest_keys.add("allow_empty_chapters")
                assert payload["allow_empty_chapters"] is manifest_permission
            if chapter_permission is not None:
                chapter_keys.add("allow_empty_images")
                assert payload["chapters"][0]["allow_empty_images"] is chapter_permission
            assert set(payload) == manifest_keys
            assert set(payload["chapters"][0]) == chapter_keys
            for level in ("url", "http"):
                summary = _inspection_json(result, level)["manifest"]
                assert "allow_empty_chapters" not in summary
            assert not result.failures

    asyncio.run(scenario())


def test_new_permissions_preserve_positional_calls_repr_patterns_and_replace():
    chapter = Chapter(1, "chapter", "subtitle", (), "id")
    manifest = DownloadManifest("book", (chapter,), "id", "author", "access", "revision", {"key": "value"})
    assert Chapter.__match_args__ == ("number", "title", "subtitle", "images", "chapter_id")
    assert DownloadManifest.__match_args__ == (
        "title",
        "chapters",
        "content_id",
        "author",
        "access",
        "revision",
        "metadata",
    )
    assert signature(Chapter).parameters["allow_empty_images"].kind is Parameter.KEYWORD_ONLY
    assert signature(DownloadManifest).parameters["allow_empty_chapters"].kind is Parameter.KEYWORD_ONLY
    assert repr(chapter) == "Chapter(number=1, title='chapter', subtitle='subtitle', images=(), chapter_id='id')"
    assert repr(replace(chapter, allow_empty_images=False)) == repr(chapter)
    assert repr(replace(manifest, allow_empty_chapters=False)) == repr(manifest)
    assert replace(chapter, allow_empty_images=False) != chapter
    assert replace(manifest, allow_empty_chapters=False) != manifest
    pinned = replace(manifest, allow_empty_chapters=False)
    assert replace(pinned, metadata={"source_url": A.url}).allow_empty_chapters is False


def test_inspect_zero_chapters_does_not_enforce_download_rejection(tmp_path):
    manifest = DownloadManifest("empty", (), allow_empty_chapters=False)

    async def scenario():
        async with compose(tmp_path, ManifestGallery(manifest)) as service:
            result = await service.inspect(InspectRequest(url=A.url))
            assert result.manifest.chapters == ()
            assert result.manifest.allow_empty_chapters is False
            assert not result.failures
            assert _inspection_json(result, "all")["manifest"]["allow_empty_chapters"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("permission", [None, False])
def test_additional_files_keep_existing_order_and_prior_saves_on_rejection(tmp_path, monkeypatch, permission):
    from support.additional_files import URL, install, service

    from image_downloader import AdditionalFileHookPoint as Point
    from image_downloader import AdditionalFileSpec as Spec
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    seen = []

    async def provider(self, hook, context):
        seen.append((hook.point, hook.chapter.number if hook.chapter else None))
        return (Spec("metadata", f"metadata/{hook.point}.txt", data=b"retained"),)

    async def inspect(self, url, context):
        assert seen == [(Point.BEFORE_MANIFEST, None)]
        return DownloadManifest(
            "book",
            (
                Chapter(1, "full", images=(ImageResource(URL + ".png"),)),
                Chapter(2, "empty", allow_empty_images=permission),
            ),
        )

    install(
        monkeypatch,
        (Point.BEFORE_MANIFEST, Point.AFTER_MANIFEST, Point.BEFORE_IMAGE_REQUEST, Point.AFTER_DOWNLOAD),
        provider,
    )
    monkeypatch.setattr(GenericHtmlPlugin, "inspect", inspect)

    async def scenario():
        async with await service(tmp_path) as runtime:
            if permission is False:
                with pytest.raises(PluginError, match="^plugin returned an empty image list$"):
                    await runtime.download(DownloadRequest(url=URL))
                assert not tuple(runtime.outputs.root.rglob("*.png"))
                assert len(tuple(runtime.outputs.root.rglob("*.txt"))) == 4
            else:
                assert len((await runtime.download(DownloadRequest(url=URL))).saved_files) == 1
            assert seen[:3] == [(Point.BEFORE_MANIFEST, None), (Point.AFTER_MANIFEST, 1), (Point.AFTER_MANIFEST, 2)]
            assert seen[3:] == (
                []
                if permission is False
                else [(Point.BEFORE_IMAGE_REQUEST, 1), (Point.AFTER_DOWNLOAD, 1), (Point.AFTER_DOWNLOAD, 2)]
            )
            assert all(path.read_bytes() == b"retained" for path in runtime.outputs.root.rglob("*.txt"))

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [0, 1, 0.0, "false", [], {}, object()])
@pytest.mark.parametrize("target", ["chapter", "manifest"])
def test_constructor_rejects_invalid_permission_without_echoing_value(target, invalid):
    if target == "chapter":
        with pytest.raises(TypeError, match="^Chapter.allow_empty_images must be bool or None$"):
            Chapter(1, "empty", allow_empty_images=invalid)
    else:
        with pytest.raises(TypeError, match="^DownloadManifest.allow_empty_chapters must be bool or None$"):
            DownloadManifest("empty", (), allow_empty_chapters=invalid)


@pytest.mark.parametrize("invalid", [0, "secret-invalid-policy", []])
@pytest.mark.parametrize("target", ["chapter", "manifest"])
def test_invoker_validates_permissions_even_if_constructor_was_bypassed(target, invalid):
    chapter = Chapter(1, "empty")
    manifest = DownloadManifest("book", (chapter,))
    field = "allow_empty_images" if target == "chapter" else "allow_empty_chapters"
    object.__setattr__(chapter if target == "chapter" else manifest, field, invalid)

    async def scenario():
        with pytest.raises(PluginError, match="invalid " + field) as caught:
            await PluginInvoker("com.example.empty-policy").inspect(ManifestGallery(manifest), A.url, object())
        assert "secret-invalid-policy" not in str(caught.value)

    asyncio.run(scenario())


def test_constructor_failure_inside_inspect_uses_existing_plugin_error_boundary():
    async def inspect(url, context):
        return DownloadManifest("empty", (), allow_empty_chapters="secret-invalid-policy")

    async def scenario():
        with pytest.raises(PluginError) as caught:
            await PluginInvoker("com.example.empty-policy").inspect(SimpleNamespace(inspect=inspect), A.url, object())
        assert isinstance(caught.value.__cause__, TypeError)
        assert "secret-invalid-policy" not in str(caught.value)

    asyncio.run(scenario())


def test_download_cli_uses_existing_plugin_failure_exit_and_closes_service(tmp_path, monkeypatch, capsys):
    gallery = ManifestGallery(DownloadManifest("empty", (Chapter(1, "empty", allow_empty_images=False),)))
    service = compose(tmp_path, gallery)
    monkeypatch.setattr(download_command, "RuntimeComposer", lambda *a, **kw: SimpleNamespace(compose=lambda: service))
    monkeypatch.setattr(download_command, "_config_for", lambda *a, **kw: (service.config, tmp_path, None, "test"))
    monkeypatch.setattr(download_command, "_persist_initial_user_config", lambda *a: None)
    try:
        assert cli.main([A.url, "--json"]) == cli.EXIT_PLUGIN
        assert '"code": "plugin_error"' in capsys.readouterr().out
        assert service._closed
        assert gallery.image_requests == []
    finally:
        asyncio.run(service.close())
