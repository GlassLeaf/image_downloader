from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

from image_downloader.models import (
    Chapter,
    DownloadManifest,
    FailureKind,
    ImageFailure,
    ImageOutcome,
    ImageOutcomeKind,
    ImageResource,
)
from image_downloader.observability.logging import ConsoleSink, DebugFileSink, DownloadLogger
from image_downloader.runtime import ChapterReporter
from image_downloader.storage import FileSystem


def test_chapter_reporter_uses_logger_for_ordering_masking_and_sink_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem(tmp_path.resolve())
        console = io.StringIO()
        logger = DownloadLogger([ConsoleSink(console)])
        logger.configure_safety({}, filesystem.root)
        logger.set_chapter_summary_console(True)
        images = (
            ImageResource("https://images.test/second?token=second-secret", index=2),
            ImageResource("https://images.test/first?token=first-secret", index=1),
            ImageResource("https://images.test/failed?token=failed-secret", index=3),
        )
        chapter = Chapter(1, "Chapter token=title-secret", images=images)
        manifest = DownloadManifest(
            "Book",
            (chapter,),
            metadata={"source_url": "https://source.test/book?token=source-secret"},
        )
        reporter = ChapterReporter(filesystem, Path("chapter"), manifest, chapter, logger, "report-1")

        await reporter.start()
        await reporter.record(
            0,
            ImageOutcome(images[0], ImageOutcomeKind.SAVED, str(filesystem.root / "chapter" / "02.jpg")),
        )
        await reporter.record(
            1,
            ImageOutcome(images[1], ImageOutcomeKind.SAVED, str(filesystem.root / "chapter" / "01.jpg")),
        )
        failure = ImageOutcome(
            images[2],
            ImageOutcomeKind.FAILED,
            failure=ImageFailure(FailureKind.FETCH, "RuntimeError", "token=failure-detail-secret"),
        )
        await reporter.record(2, failure)
        await reporter.finish(
            (
                ImageOutcome(images[0], ImageOutcomeKind.SAVED, str(filesystem.root / "chapter" / "02.jpg")),
                ImageOutcome(images[1], ImageOutcomeKind.SAVED, str(filesystem.root / "chapter" / "01.jpg")),
                failure,
            )
        )

        detail = (filesystem.root / "chapter" / "log.log").read_text(encoding="utf-8")
        assert detail == console.getvalue()
        assert detail.index("first?token=[REDACTED]") < detail.index("second?token=[REDACTED]")
        assert "save: chapter/01.jpg" in detail
        assert "error: image fetch error (1)" in detail
        assert "exception: RuntimeError" in detail
        assert detail.rstrip().endswith("done")
        assert "secret" not in detail
        assert logger._chapter_sinks == {}
        await logger.close()

    asyncio.run(scenario())


def test_plugin_capture_does_not_modify_root_logger_and_ignores_other_namespaces(tmp_path: Path) -> None:
    async def scenario() -> None:
        debug_path = tmp_path / "debug.log"
        logger = DownloadLogger([DebugFileSink(debug_path)])
        root = logging.getLogger()
        root_handlers = tuple(root.handlers)
        namespace = "_image_downloader_plugins.com_example_selected_1234"
        plugin_logger = logging.getLogger(f"{namespace}.entry")
        unrelated_logger = logging.getLogger("unrelated.library")

        logger.begin_python_log_capture([namespace])
        assert tuple(root.handlers) == root_handlers
        plugin_logger.warning("selected-marker token=private-value")
        unrelated_logger.warning("unrelated-marker")
        await logger.flush_python_log_capture()
        logger.end_python_log_capture()
        assert tuple(root.handlers) == root_handlers
        await logger.close()

        detail = debug_path.read_text(encoding="utf-8")
        assert "selected-marker" in detail
        assert "private-value" not in detail
        assert "unrelated-marker" not in detail

    asyncio.run(scenario())


def test_concurrent_service_captures_are_isolated_by_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_path = tmp_path / "first.log"
        second_path = tmp_path / "second.log"
        first = DownloadLogger([DebugFileSink(first_path)])
        second = DownloadLogger([DebugFileSink(second_path)])
        first_namespace = "_image_downloader_plugins.com_example_first_1234"
        second_namespace = "_image_downloader_plugins.com_example_second_5678"
        ready = 0
        both_ready = asyncio.Event()
        root_handlers = tuple(logging.getLogger().handlers)

        async def capture(
            owner: DownloadLogger,
            namespace: str,
            other_namespace: str,
            marker: str,
        ) -> None:
            nonlocal ready
            owner.begin_python_log_capture([namespace])
            ready += 1
            if ready == 2:
                both_ready.set()
            await both_ready.wait()
            logging.getLogger(f"{namespace}.entry").warning(marker)
            logging.getLogger(f"{other_namespace}.entry").warning(f"cross-{marker}")
            await owner.flush_python_log_capture()
            owner.end_python_log_capture()
            await owner.close()

        await asyncio.gather(
            capture(first, first_namespace, second_namespace, "first-marker"),
            capture(second, second_namespace, first_namespace, "second-marker"),
        )

        assert tuple(logging.getLogger().handlers) == root_handlers
        first_detail = first_path.read_text(encoding="utf-8")
        second_detail = second_path.read_text(encoding="utf-8")
        assert "first-marker" in first_detail
        assert "second-marker" not in first_detail
        assert "second-marker" in second_detail
        assert "first-marker" not in second_detail

    asyncio.run(scenario())
