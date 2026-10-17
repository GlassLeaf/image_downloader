"""Chapter scheduling and ordered chapter reporting."""

from __future__ import annotations

import asyncio
import uuid
from functools import partial

from ..configuration.models import AppConfig
from ..models import (
    Chapter,
    ChapterResult,
)
from ..observability.chapter_reporter import ChapterReporter
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventBus
from ..observability.logging import DownloadLogger
from ..output.output_allocator import OutputFormatContext
from ..output.output_lock import OutputDirectoryLocks
from .concurrency import bounded
from .download_context import DownloadContext
from .image_executor import ImageExecutor


class ChapterExecutor:
    """Schedule chapters and images and finalize each chapter reporter."""

    def __init__(self, config: AppConfig, events: EventBus, logger: DownloadLogger, output_locks: OutputDirectoryLocks):
        self.config, self.events, self.logger, self.output_locks = config, events, logger, output_locks

    async def execute(self, operation: DownloadContext) -> list[ChapterResult]:
        operation_id = uuid.uuid4().hex
        factories = [
            partial(self._chapter, operation, position, chapter, f"{operation_id}-chapter-{position}")
            for position, chapter in enumerate(operation.manifest.chapters)
        ]
        try:
            return await bounded(factories, operation.site.policy.chapter_concurrency)
        finally:
            await asyncio.gather(
                *(
                    best_effort_diagnostic(self.logger.close_chapter, f"{operation_id}-chapter-{position}")
                    for position in range(len(factories))
                )
            )

    async def _chapter(
        self,
        operation: DownloadContext,
        chapter_position: int,
        chapter: Chapter,
        reporter_id: str,
    ) -> ChapterResult:
        directory = operation.allocator.chapter_directory(
            OutputFormatContext(
                operation.manifest,
                chapter,
                operation.source_url,
                operation.site.record.id,
                plugin_values=operation.plugin_values,
            )
        )
        reporter = ChapterReporter(
            operation.allocator.filesystem,
            directory,
            operation.manifest,
            chapter,
            self.logger,
            reporter_id,
        )
        await reporter.start()
        await best_effort_diagnostic(
            self.logger.core,
            "chapter_started",
            module="download",
            chapter_id=str(chapter.number),
            url=operation.manifest.metadata.get("source_url"),
            count=len(chapter.images),
            plugin_id=operation.site.record.id,
            debug=True,
        )
        executor = ImageExecutor(
            operation,
            chapter,
            chapter_position,
            directory,
            reporter,
            self.config,
            self.events,
            self.logger,
            self.output_locks,
        )
        factories = [partial(executor.execute, position, image) for position, image in enumerate(chapter.images)]
        try:
            outcomes = tuple(await bounded(factories, operation.site.policy.image_concurrency_per_chapter))
        finally:
            await reporter.finish()
        await best_effort_diagnostic(
            self.logger.core,
            "chapter_finished",
            module="download",
            chapter_id=str(chapter.number),
            url=operation.manifest.metadata.get("source_url"),
            count=len(outcomes),
            plugin_id=operation.site.record.id,
            debug=True,
        )
        return ChapterResult(chapter, outcomes)
