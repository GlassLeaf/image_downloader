"""chapter reporter runtime responsibilities."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from ..models import (
    Chapter,
    DownloadManifest,
    FailureKind,
    ImageOutcome,
    ImageOutcomeKind,
)
from ..observability.diagnostic_safety import best_effort_diagnostic, best_effort_diagnostic_sync
from ..observability.logging import (
    ChapterFailureRecord,
    DownloadLogger,
)
from ..storage import FileSystem


class ChapterReporter:
    def __init__(
        self,
        filesystem: FileSystem,
        chapter_directory: Path,
        manifest: DownloadManifest,
        chapter: Chapter,
        logger: DownloadLogger,
        reporter_id: str,
    ) -> None:
        self.filesystem, self.chapter_directory, self.chapter, self.logger, self.reporter_id = (
            filesystem,
            chapter_directory,
            chapter,
            logger,
            reporter_id,
        )
        self._lock = asyncio.Lock()
        self._items: dict[int, ImageOutcome] = {}
        self._positions = sorted(range(len(chapter.images)), key=lambda index: (chapter.images[index].index, index))
        self._next = 0
        self._source_url = manifest.metadata.get("source_url", "")

    async def start(self) -> None:
        self.filesystem.ensure_directory(self.chapter_directory)
        best_effort_diagnostic_sync(
            self.logger.register_chapter,
            self.reporter_id,
            filesystem=self.filesystem,
            relative_path=self.chapter_directory / "log.log",
        )
        await best_effort_diagnostic(
            self.logger.chapter_header,
            self.reporter_id,
            url=self._source_url,
            title=self.chapter.title,
            subtitle=self.chapter.subtitle or None,
        )

    async def record(self, position: int, outcome: ImageOutcome) -> None:
        async with self._lock:
            self._items[position] = outcome
            ready: list[ImageOutcome] = []
            while self._next < len(self._positions) and self._positions[self._next] in self._items:
                item = self._items[self._positions[self._next]]
                if item.kind is not ImageOutcomeKind.FAILED:
                    ready.append(item)
                self._next += 1
            for item in ready:
                await best_effort_diagnostic(self.logger.chapter_download, self.reporter_id, item.image.url)
                await best_effort_diagnostic(self.logger.chapter_save, self.reporter_id, item.path or "")

    async def finish(self, outcomes: tuple[ImageOutcome, ...]) -> None:
        failures = [item for item in outcomes if item.failure is not None]
        if failures:
            grouped: dict[FailureKind, list[ChapterFailureRecord]] = defaultdict(list)
            for item in failures:
                assert item.failure is not None
                grouped[item.failure.kind].append(
                    ChapterFailureRecord(
                        url=item.image.url,
                        path=item.path,
                        exception_type=item.failure.exception_type,
                        message=item.failure.message,
                    )
                )
            for kind, items in grouped.items():
                await best_effort_diagnostic(
                    self.logger.chapter_error_group, self.reporter_id, f"image {kind.value} error", items
                )
        await best_effort_diagnostic(self.logger.chapter_done, self.reporter_id)
