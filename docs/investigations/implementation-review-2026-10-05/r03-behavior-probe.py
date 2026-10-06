"""Offline R03 impact probes; no product source changes.

The injected rejection is after _run_with_additional returns. It checks existing
downstream error handling, not the proposed early validation/hook ordering.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/v3/application"))

from test_additional_files import install, service  # noqa: E402
from test_generic_workflow import Page, compose  # noqa: E402

from image_downloader import (  # noqa: E402
    AdditionalFileHookPoint,
    AdditionalFileSpec,
    Chapter,
    DownloadManifest,
    PluginError,
    UpdateSnapshot,
    WorkflowStateService,
)
from image_downloader.application.service import DownloadService  # noqa: E402
from image_downloader.application.workflow_reporting import workflow_status  # noqa: E402
from image_downloader.application.workflow_verification import verify_workflow  # noqa: E402
from image_downloader.observability.events import EventName  # noqa: E402
from image_downloader.plugins.builtin import GenericHtmlPlugin  # noqa: E402

URL = "https://example.test/"


def reject_empty_images(monkeypatch):
    original = DownloadService._run_with_additional

    async def injected(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if result.manifest.chapters and not any(chapter.images for chapter in result.manifest.chapters):
            raise PluginError("probe: manifest has no images")
        return result

    monkeypatch.setattr(DownloadService, "_run_with_additional", injected)


@pytest.mark.parametrize("allow_empty", [False, True])
def test_current_empty_success_but_verify_failure(tmp_path, monkeypatch, allow_empty):
    page = Page(monkeypatch)
    page.html = "<title>Login</title>"

    async def scenario():
        async with compose(tmp_path, allow_empty=allow_empty) as runtime:
            events = []
            runtime.events.on(EventName.DOWNLOAD_SUCCESS, events.append)
            regular = await runtime.run(URL)
            assert not regular.saved_files and not regular.failures and len(events) == 1
            workflow = await runtime.workflow(URL, workflow_retries=0)
            assert workflow_status(workflow) == 0 and workflow.items[0].status == "success"
            state = WorkflowStateService(runtime.config)
            assert state.get_workflow(URL)[0].items[0].completed
            verified = verify_workflow(state.get_run(workflow.run_id), runtime.outputs.root)
            assert verified["status"] == "failed"
            assert verified["items"][0]["errors"][0]["code"] == "no_images"
            assert not (await runtime.workflow(URL, workflow_retries=0)).selected_urls

    asyncio.run(scenario())


def test_zero_image_chapter_can_save_additional_file(tmp_path, monkeypatch):
    async def inspect(self, url, context):
        return DownloadManifest("metadata", (Chapter(1, "empty"),))

    async def provider(self, hook, context):
        return (AdditionalFileSpec("metadata", "meta/info.json", data=b'{"ok":true}'),)

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", inspect)
    install(monkeypatch, (AdditionalFileHookPoint.AFTER_DOWNLOAD,), provider)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            download = await runtime.run(URL)
            assert not download.saved_files and not download.failures
            saved = [item for item in download.additional_files if item.phase == "save" and item.status == "saved"]
            assert len(saved) == 1
            assert (runtime.outputs.root / saved[0].path).read_bytes() == b'{"ok":true}'
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_injected_rejection_retry_events_and_old_completed_state(tmp_path, monkeypatch):
    page = Page(monkeypatch)
    page.html = "<title>Login</title>"

    async def scenario():
        async with compose(tmp_path) as runtime:
            await runtime.workflow(URL, workflow_retries=0)
            reject_empty_images(monkeypatch)
            # Merely changing future downloads does not invalidate previous completion.
            assert not (await runtime.workflow(URL, workflow_retries=0)).selected_urls
            assert not (await runtime.inspect(URL)).manifest.chapters[0].images
            failures, successes = [], []
            runtime.events.on(EventName.PLUGIN_FAILED, failures.append)
            runtime.events.on(EventName.DOWNLOAD_SUCCESS, successes.append)
            rejected = await runtime.workflow(URL, download_scope="all", workflow_retries=1, workflow_retry_delay=0)
            assert workflow_status(rejected) == 1
            assert [attempt.status for attempt in rejected.items[0].attempts] == ["failed", "failed"]
            assert len(failures) == 2 and not successes
            assert not WorkflowStateService(runtime.config).get_workflow(URL)[0].items[0].completed
            # The now unfinished candidate is selected by a subsequent updated run.
            assert (await runtime.workflow(URL, workflow_retries=0)).selected_urls == (URL,)
            with pytest.raises(PluginError):
                await runtime.run(URL)

    asyncio.run(scenario())


def test_injected_rule_preserves_skip_and_mixed_empty_chapters(tmp_path, monkeypatch):
    reject_empty_images(monkeypatch)
    original = GenericHtmlPlugin.inspect

    async def mixed(self, url, context):
        manifest = await original(self, url, context)
        return replace(manifest, chapters=(Chapter(0, "empty"), *manifest.chapters))

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", mixed)

    async def scenario():
        runtime = await service(tmp_path)
        try:
            first = await runtime.run("https://example.test/gallery")
            assert len(first.saved_files) == 1 and not first.failures
            second = await runtime.run("https://example.test/gallery")
            assert not second.saved_files and len(second.skipped_files) == 1 and not second.failures
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_injected_rule_preserves_no_update_candidates(tmp_path, monkeypatch):
    Page(monkeypatch)
    reject_empty_images(monkeypatch)

    async def no_candidates(self, url, context):
        return UpdateSnapshot(url, (), datetime.now(UTC))

    monkeypatch.setattr(GenericHtmlPlugin, "check_updates", no_candidates)

    async def scenario():
        async with compose(tmp_path) as runtime:
            result = await runtime.workflow(URL, workflow_retries=0)
            assert not result.items and not result.selected_urls and workflow_status(result) == 0

    asyncio.run(scenario())
