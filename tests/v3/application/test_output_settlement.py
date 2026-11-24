"""Committed results survive cancellation, observer failures, and reporting gaps."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from support.additional_files import URL, install, service
from support.synchronization import managed_task, wait_until_started
from support.workflow import FEED, A, compose
from support.workflow_retry import RetryGallery, manifest, transport

from image_downloader import DownloadRequest, ImageResource, WorkflowRequest
from image_downloader.application.workflow_retry import ImageLedger
from image_downloader.configuration.models import AppConfig
from image_downloader.models import (
    AdditionalFileHookPoint as Point,
)
from image_downloader.models import AdditionalFileSpec, Chapter, DownloadManifest, ImageOutcome, ImageOutcomeKind
from image_downloader.observability.chapter_reporter import ChapterReporter
from image_downloader.observability.events import EventName
from image_downloader.observability.logging import DownloadLogger
from image_downloader.output.output_allocator import OutputAllocation, OutputAllocator, OutputFormatContext
from image_downloader.output.output_lock import OutputDirectoryLocks
from image_downloader.output.output_writer import OutputWriter
from image_downloader.storage import FileSystem


@pytest.mark.parametrize("workflow", [False, True])
@pytest.mark.parametrize("stage", ["commit", "event", "hook"])
def test_cancellation_keeps_committed_image_in_chapter_log_and_workflow(tmp_path, monkeypatch, workflow, stage):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def block(*args, **kwargs):
            started.set()
            await release.wait()

        original_commit = OutputAllocation.commit
        if stage == "commit":

            async def commit(allocation):
                await block()
                await original_commit(allocation)

            monkeypatch.setattr(OutputAllocation, "commit", commit)
        if workflow:
            gallery = RetryGallery()
            gallery.manifests[1, A.url] = manifest(ImageResource(A.url + "/good.png"))
            if stage == "hook":
                monkeypatch.setattr(
                    RetryGallery,
                    "additional_file_hook_points",
                    lambda self, context: (Point.AFTER_IMAGE_SAVE,),
                    raising=False,
                )
                monkeypatch.setattr(RetryGallery, "additional_files", block, raising=False)
            runtime = compose(tmp_path, gallery)
            await transport(runtime, gallery)
            operation = runtime.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
        else:
            if stage == "hook":
                install(monkeypatch, (Point.AFTER_IMAGE_SAVE,), block)
            runtime = await service(tmp_path)
            operation = runtime.download(DownloadRequest(url=URL))
        if stage == "event":
            runtime.events.on(EventName.SAVE_SUCCESS, block)
        try:
            async with managed_task(operation) as task:
                await wait_until_started(started, task)
                task.cancel()
                if stage == "commit":
                    await asyncio.sleep(0)
                    release.set()
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
            logs = list((tmp_path / "data").rglob("log.log"))
            assert len(logs) == 1
            detail = logs[0].read_text(encoding="utf-8")
            assert detail.count("save: ") == 1
            assert detail.rstrip().endswith("done")
            assert len(list((tmp_path / "data").rglob("*.png"))) == 1
            if workflow:
                assert len(caught.value.result.items[0].download.saved_files) == 1
        finally:
            release.set()
            await runtime.close()

    asyncio.run(scenario())


def test_reporter_flushes_results_past_unprocessed_positions_once(tmp_path):
    async def scenario():
        logger = DownloadLogger([])
        logger.chapter_download = AsyncMock()
        logger.chapter_save = AsyncMock()
        images = tuple(ImageResource(f"https://example.test/{n}", index=n) for n in range(3))
        chapter = Chapter(1, "chapter", images=images)
        reporter = ChapterReporter(
            FileSystem(tmp_path), Path("chapter"), DownloadManifest("book", (chapter,)), chapter, logger, "test"
        )
        first = ImageOutcome(images[0], ImageOutcomeKind.SAVED, "first.png")
        last = ImageOutcome(images[2], ImageOutcomeKind.SKIPPED, "last.png")
        await reporter.record(0, first)
        reporter._register(2, last)
        await reporter.finish()
        await reporter.finish()
        assert logger.chapter_save.await_args_list[0].args == ("test", "first.png")
        assert logger.chapter_save.await_args_list[1].args == ("test", "last.png")
        assert logger.chapter_save.await_count == 2
        await logger.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_commit_waiter_interruption_preserves_artifact_ledger_log_and_lock(tmp_path, monkeypatch, kind):
    async def scenario():
        filesystem = FileSystem(tmp_path / "output")
        allocator = OutputAllocator(filesystem, AppConfig())
        logger = DownloadLogger([])
        locks = OutputDirectoryLocks(FileSystem(tmp_path / "state"), timeout_seconds=0, logger=logger)
        image = ImageResource(URL + "/image.png", index=1)
        chapter = Chapter(1, "chapter", images=(image,))
        manifest = DownloadManifest("book", (chapter,))
        directory = Path("chapter")
        reporter = ChapterReporter(filesystem, directory, manifest, chapter, logger, "test.site")
        ledger = ImageLedger(URL)
        ledger.begin(manifest, "test.site")
        started, release = asyncio.Event(), asyncio.Event()
        interruption = kind("commit waiter interrupted")
        original_commit, original_shield = OutputAllocation.commit, asyncio.shield
        injected = False

        async def commit(allocation):
            started.set()
            await release.wait()
            await original_commit(allocation)

        async def shield(task):
            nonlocal injected
            if not injected:
                injected = True
                await started.wait()
                release.set()
                raise interruption
            return await original_shield(task)

        def register(receipt):
            outcome = ImageOutcome(image, ImageOutcomeKind.SAVED, receipt.relative_path.as_posix())
            ledger.record(0, 0, outcome)
            reporter._register(0, outcome)

        try:
            await reporter.start()
            monkeypatch.setattr(OutputAllocation, "commit", commit)
            monkeypatch.setattr(asyncio, "shield", shield)
            with pytest.raises(kind) as caught:
                await OutputWriter(allocator, locks).image(
                    directory,
                    OutputFormatContext(manifest, chapter, URL, "test.site", image, extension=".png"),
                    b"committed image",
                    register,
                )
            assert caught.value is interruption
            outcome = ledger.outcomes[0, 0]
            assert filesystem.path(Path(outcome.path)).read_bytes() == b"committed image"
            await reporter.finish()
            await reporter.finish()
            assert filesystem.path(directory / "log.log").read_text(encoding="utf-8").count("save: ") == 1
            async with locks.hold(filesystem.path(directory)):
                pass
        finally:
            release.set()
            await logger.close()

    asyncio.run(scenario())


def test_additional_file_commit_cancellation_registers_before_propagation(tmp_path, monkeypatch):
    async def scenario():
        async def provider(*args):
            return (AdditionalFileSpec("meta", "meta/file.txt", data=b"metadata"),)

        install(monkeypatch, (Point.AFTER_IMAGE_SAVE,), provider)
        started, release = asyncio.Event(), asyncio.Event()
        original = OutputAllocation.commit

        async def commit(allocation):
            if allocation.relative_path.suffix == ".txt":
                started.set()
                await release.wait()
            await original(allocation)

        monkeypatch.setattr(OutputAllocation, "commit", commit)
        from image_downloader.application.additional_files import AdditionalFiles

        registered = []
        register = AdditionalFiles._register

        def observe(additional, *args, **kwargs):
            register(additional, *args, **kwargs)
            registered[:] = additional.outcomes

        monkeypatch.setattr(AdditionalFiles, "_register", observe)
        runtime = await service(tmp_path)
        try:
            async with managed_task(runtime.download(DownloadRequest(url=URL))) as task:
                await wait_until_started(started, task)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert any(outcome.file_id == "meta" and outcome.status == "saved" for outcome in registered)
            assert next((tmp_path / "data").rglob("file.txt")).read_bytes() == b"metadata"
        finally:
            release.set()
            await runtime.close()

    asyncio.run(scenario())


def test_writer_aborts_failed_write_and_releases_directory_lock(tmp_path, monkeypatch):
    async def scenario():
        filesystem = FileSystem(tmp_path / "output")
        allocator = OutputAllocator(filesystem, AppConfig())
        logger = DownloadLogger([])
        locks = OutputDirectoryLocks(FileSystem(tmp_path / "state"), timeout_seconds=0, logger=logger)
        writer = OutputWriter(allocator, locks)
        original = FileSystem.write_bytes_atomic
        primary = OSError("artificial write failure")
        monkeypatch.setattr(FileSystem, "write_bytes_atomic", lambda *args: (_ for _ in ()).throw(primary))
        receipts = []
        with pytest.raises(OSError) as caught:
            await writer.additional(Path("chapter"), Path("chapter/meta.txt"), b"data", receipts.append)
        assert caught.value is primary
        assert receipts == []
        monkeypatch.setattr(FileSystem, "write_bytes_atomic", original)
        receipt = await writer.additional(Path("chapter"), Path("chapter/meta.txt"), b"retry", receipts.append)
        assert receipt.status == "saved"
        assert filesystem.read_bytes_bounded(receipt.relative_path, 5) == b"retry"
        await logger.close()

    asyncio.run(scenario())
