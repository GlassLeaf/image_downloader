"""chapter reporter runtime responsibilities."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from image_downloader.observability.log_sinks import ChapterFailureRecord
from image_downloader.observability.logging import DownloadLogger

from ..models import (
    Chapter,
    DownloadManifest,
    FailureKind,
    ImageOutcome,
    ImageOutcomeKind,
)
from ..observability.diagnostic_safety import best_effort_diagnostic, best_effort_diagnostic_sync
from ..storage import FileSystem
from ..storage.transactions import finish_task


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
        self._emitted: set[int] = set()
        self._finished = False
        self._source_url = manifest.metadata.get("source_url", "")
        self._manifest = manifest

    async def start(self) -> None:
        self.filesystem.ensure_directory(self.chapter_directory)
        best_effort_diagnostic_sync(
            self.logger.register_chapter,
            self.reporter_id,
            filesystem=self.filesystem,
            relative_path=self.chapter_directory / "log.log",
        )
        fields = {
            "content_id": self._manifest.content_id or "",
            "chapter_number": str(self.chapter.number),
            "chapter_id": self.chapter.chapter_id or "",
            "content_published_at": self._manifest.published_at or "",
            "content_publication_date": self._manifest.publication_date or "",
            "chapter_published_at": self.chapter.published_at or "",
            "chapter_publication_date": self.chapter.publication_date or "",
            **{f"content.{key}": value for key, value in self._manifest.log_fields.items()},
            **{f"chapter.{key}": value for key, value in self.chapter.log_fields.items()},
        }
        await best_effort_diagnostic(
            self.logger.chapter_header,
            self.reporter_id,
            url=self._source_url,
            title=self.chapter.title,
            subtitle=self.chapter.subtitle or None,
            content_title=self._manifest.title,
            author=self._manifest.author,
            extra_fields=fields,
        )

    async def record(self, position: int, outcome: ImageOutcome) -> None:
        self._register(position, outcome)
        await finish_task(self._flush())

    def _register(self, position: int, outcome: ImageOutcome) -> None:
        """Register on the event-loop thread without a cancellation point."""
        self._items[position] = outcome

    async def _flush(self, *, final: bool = False) -> None:
        async with self._lock:
            ready: list[int] = []
            if final:
                ready = [position for position in self._positions if position in self._items]
            else:
                while self._next < len(self._positions) and self._positions[self._next] in self._items:
                    ready.append(self._positions[self._next])
                    self._next += 1
            for position in ready:
                if position in self._emitted:
                    continue
                item = self._items[position]
                if item.kind is not ImageOutcomeKind.FAILED:
                    await best_effort_diagnostic(self.logger.chapter_download, self.reporter_id, item.image.url)
                    await best_effort_diagnostic(self.logger.chapter_save, self.reporter_id, item.path or "")
                self._emitted.add(position)

    async def finish(self, outcomes: tuple[ImageOutcome, ...] | None = None) -> None:
        """Render every recorded outcome exactly once, even after fail-fast exits."""
        await finish_task(self._finish(outcomes))

    async def _finish(self, outcomes: tuple[ImageOutcome, ...] | None) -> None:
        async with self._lock:
            if self._finished:
                return
            if outcomes is not None:
                for position, outcome in enumerate(outcomes):
                    self._items.setdefault(position, outcome)
            self._finished = True
            outcomes = tuple(sorted(self._items.values(), key=lambda item: item.image.index))
        await self._flush(final=True)
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
