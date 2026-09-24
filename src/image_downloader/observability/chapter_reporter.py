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
        self._finished = False
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

    async def finish(self, outcomes: tuple[ImageOutcome, ...] | None = None) -> None:
        """Render every recorded outcome exactly once, even after fail-fast exits."""
        async with self._lock:
            if self._finished:
                return
            if outcomes is not None:
                for position, outcome in enumerate(outcomes):
                    self._items.setdefault(position, outcome)
            self._finished = True
            outcomes = tuple(sorted(self._items.values(), key=lambda item: item.image.index))
        failures = [item for item in outcomes if item.failure is not None]
        if failures:
            grouped: dict[FailureKind, list[ChapterFailureRecord]] = defaultdict(list)
            for item in failures:
                assert item.failure is not None
                grouped[item.failure.kind].append(
                    ChapterFailureRecord(
                        url=item.image.url,
                        response_url=item.failure.response_url,
                        path=item.path,
                        stage={
                            FailureKind.FETCH: "image_fetch",
                            FailureKind.PROCESS: "image_processing",
                            FailureKind.SAVE: "image_save",
                        }[item.failure.kind],
                        image_index=item.image.index,
                        http_status=item.failure.http_status,
                        code=item.failure.code,
                        reason=item.failure.reason,
                        exception_type=item.failure.exception_type,
                        message=item.failure.message,
                        transport=item.failure.transport,
                    )
                )
            for kind, items in grouped.items():
                await best_effort_diagnostic(
                    self.logger.chapter_error_group,
                    self.reporter_id,
                    {
                        FailureKind.FETCH: "image_fetch_failed",
                        FailureKind.PROCESS: "image_processing_failed",
                        FailureKind.SAVE: "image_save_failed",
                    }[kind],
                    items,
                )
        await best_effort_diagnostic(self.logger.chapter_done, self.reporter_id)
