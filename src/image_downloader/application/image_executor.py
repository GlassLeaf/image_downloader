"""Fetch, process and save one image with explicit operation inputs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any, NoReturn

from ..configuration.models import AppConfig
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    ErrorInfo,
    ExistingFileConflictError,
    ImageDownloaderError,
    ImageFailureContext,
    ImageProcessingError,
    InterProcessLockError,
    PluginError,
    RequestError,
    StorageError,
    StorageSafetyError,
    error_info_for,
)
from ..models import (
    AdditionalFileHookPoint,
    Chapter,
    DownloadManifest,
    FailureKind,
    ImageArtifact,
    ImageFailure,
    ImageOutcome,
    ImageOutcomeKind,
    ImageResource,
    ImageTransportMetadata,
    RequestResponse,
)
from ..observability.chapter_reporter import ChapterReporter
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventBus, EventName, EventPayload
from ..observability.logging import DownloadLogger
from ..output.original_filename import resolve_original_filename
from ..output.output_allocator import OutputAllocation, OutputFormatContext
from ..output.output_lock import OutputDirectoryLocks
from ..storage import FileSystem
from ..storage.transactions import finish_task
from ..transport.metadata import ImageFetchResult
from .additional_files import AdditionalFiles
from .download_context import DownloadContext
from .workflow_retry import ImageLedger

_IMAGE_FAILURE_EVENTS = {
    FailureKind.FETCH: EventName.FETCH_FAILED,
    FailureKind.PROCESS: EventName.IMAGE_PROCESS_FAILED,
    FailureKind.SAVE: EventName.SAVE_FAILED,
}
_IMAGE_FAILURE_ACTIONS = {
    FailureKind.FETCH: "image_fetch",
    FailureKind.PROCESS: "image_process",
    FailureKind.SAVE: "image_save",
}
_IMAGE_FAILURE_STAGES = {
    FailureKind.FETCH: "image_fetch",
    FailureKind.PROCESS: "image_processing",
    FailureKind.SAVE: "image_save",
}


async def _additional_image(
    additional: AdditionalFiles | None,
    point: AdditionalFileHookPoint,
    manifest: DownloadManifest,
    chapter: Chapter,
    image: ImageResource,
    **values: Any,
) -> None:
    if additional is not None:
        await additional.run(point, manifest=manifest, chapter=chapter, image=image, **values)


def _remember_image(
    ledger: ImageLedger | None, chapter_position: int, image_position: int, outcome: ImageOutcome
) -> None:
    if ledger is not None:
        ledger.record(chapter_position, image_position, outcome)


def _start_workflow_image(ledger: ImageLedger | None, chapter_position: int, image_position: int) -> None:
    if ledger is not None:
        ledger.mark_attempted(chapter_position, image_position)


class _ImageJobError(Exception):
    def __init__(self, kind: FailureKind, cause: Exception, response: RequestResponse | None = None) -> None:
        classified = _classify_image_error(kind, cause)
        super().__init__(str(classified))
        self.kind, self.cause, self.response = kind, classified, response
        self.original = cause
        self.unexpected = not isinstance(cause, ImageDownloaderError) and not (
            kind is FailureKind.SAVE and isinstance(cause, OSError)
        )


def _classify_image_error(kind: FailureKind, cause: Exception) -> ImageDownloaderError:
    if isinstance(cause, ImageDownloaderError):
        return cause
    error: ImageDownloaderError
    if kind is FailureKind.FETCH:
        error = RequestError("unexpected image fetch failure")
    elif kind is FailureKind.PROCESS:
        error = ImageProcessingError("unexpected image processing failure")
    else:
        error = StorageError("unexpected image save failure")
    error.__cause__ = cause
    return error


def _failure_code(error: Exception) -> str:
    return error_info_for(error).code


def _failure_output_path(error: Exception, filesystem: FileSystem) -> Path | None:
    """Return the absolute target path carried by an expected output conflict."""

    if isinstance(error, ExistingFileConflictError):
        return filesystem.path(error.relative_path)
    return None


def _failure_info(
    error: Exception,
    response: RequestResponse | None,
    filesystem: FileSystem,
) -> tuple[ErrorInfo, str | None, int | None, Path | None]:
    info = error_info_for(error)
    response_url = response.url if response is not None else info.response_url
    http_status = response.status if response is not None else info.http_status
    output_path = _failure_output_path(error, filesystem)
    return info, response_url, http_status, output_path


def _failure_transport(kind: FailureKind, error: Exception, response: RequestResponse | None) -> str:
    if response is not None or kind in {FailureKind.PROCESS, FailureKind.SAVE}:
        return "completed"
    code = _failure_code(error)
    return {
        "http_status_error": "response_received",
        "response_size_limit_error": "response_limit_exceeded",
        "redirect_policy_error": "redirect_rejected",
    }.get(code, "failed")


class ImageOperationError(Exception):
    """Internal propagation of an unexpected, already reported workflow image failure."""

    def __init__(self, cause: Exception, context: ImageFailureContext) -> None:
        super().__init__("image operation failed")
        self.cause, self.context = cause, context


def _workflow_image_error(error: _ImageJobError, ledger: ImageLedger | None) -> Exception:
    return error.original if ledger is not None and error.unexpected else error.cause


def _raise_image_error(error: _ImageJobError, ledger: ImageLedger | None, context: ImageFailureContext) -> NoReturn:
    if ledger is not None and error.unexpected:
        raise ImageOperationError(error.original, context) from None
    error.cause.image_failure = context
    raise error.cause from error.cause.__cause__


def _is_fatal_image_error(
    error: _ImageJobError,
    continue_on_image_error: bool,
    ledger: ImageLedger | None = None,
) -> bool:
    return (
        (ledger is not None and error.unexpected)
        or not continue_on_image_error
        or isinstance(
            error.cause,
            (AuthenticationError, PluginError, ConfigurationError, StorageSafetyError, InterProcessLockError),
        )
    )


class ImageExecutor:
    """Run image stages in order and record each settled outcome."""

    def __init__(
        self,
        operation: DownloadContext,
        chapter: Chapter,
        chapter_position: int,
        directory: Path,
        reporter: ChapterReporter,
        config: AppConfig,
        events: EventBus,
        logger: DownloadLogger,
        output_locks: OutputDirectoryLocks,
    ) -> None:
        self.operation, self.chapter, self.chapter_position = operation, chapter, chapter_position
        self.directory, self.reporter = directory, reporter
        self.config, self.events, self.logger, self.output_locks = config, events, logger, output_locks
        self.chapter_id = str(chapter.number)

    async def execute(self, position: int, image: ImageResource) -> ImageOutcome:
        ledger = self.operation.ledger
        retained = ledger.retained_outcome(self.chapter_position, position) if ledger is not None else None
        if retained is not None:
            outcome = retained
            await self.reporter.record(position, outcome)
            return outcome
        _start_workflow_image(ledger, self.chapter_position, position)
        extra = partial(
            _additional_image,
            self.operation.additional,
            manifest=self.operation.manifest,
            chapter=self.chapter,
            image=image,
        )
        try:
            fetched = await self._fetch(image, extra)
            processed = await self._process(image, fetched.response, fetched.transport_metadata)
            outcome = await self._save(position, image, fetched.response, processed, extra)
        except asyncio.CancelledError:
            raise
        except _ImageJobError as error:
            return await self._report_failure(position, image, error)
        _remember_image(ledger, self.chapter_position, position, outcome)
        await self.reporter.record(position, outcome)
        return outcome

    async def _fetch(self, image: ImageResource, extra: Callable[..., Awaitable[None]]) -> ImageFetchResult:
        try:
            await extra(AdditionalFileHookPoint.BEFORE_IMAGE_REQUEST)
            await self.events.emit(EventName.BEFORE_FETCH, EventPayload(url=image.url))
            await best_effort_diagnostic(
                self.logger.core,
                "request_started",
                module="download",
                chapter_id=self.chapter_id,
                url=image.url,
                count=image.index,
                plugin_id=self.operation.site.record.id,
                action="image_fetch",
                url_is_locator=True,
                debug=True,
            )
            fetched = await self.operation.site.gateway.execute_image_with_metadata(
                self.operation.site.plugin, image, self.operation.site.context
            )
            response = fetched.response
            await best_effort_diagnostic(
                self.logger.core,
                "download_finished",
                module="download",
                chapter_id=self.chapter_id,
                url=response.url,
                bytes_count=len(response.body),
                count=image.index,
                plugin_id=self.operation.site.record.id,
                action="image_fetch",
                debug=True,
            )
            await self.events.emit(EventName.FETCH_SUCCESS, EventPayload(url=image.url))
            await extra(AdditionalFileHookPoint.AFTER_IMAGE_REQUEST, response=response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _ImageJobError(FailureKind.FETCH, exc) from exc

        return fetched

    async def _process(
        self, image: ImageResource, response: RequestResponse, transport_metadata: ImageTransportMetadata
    ) -> ImageArtifact:
        artifact = ImageArtifact(response.body, response.headers.get("content-type", ""), image.url, image.image_id)
        try:
            await self.events.emit(EventName.IMAGE_PROCESS_STARTED, EventPayload(url=image.url))
            processed = await self.operation.pipeline.process(
                self.operation.site.plugin,
                artifact,
                image,
                self.operation.manifest,
                self.chapter,
                transport_metadata=transport_metadata,
            )
            await self.events.emit(EventName.IMAGE_PROCESS_SUCCESS, EventPayload(url=image.url))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _ImageJobError(FailureKind.PROCESS, exc, response) from exc

        return processed

    async def _save(
        self,
        position: int,
        image: ImageResource,
        response: RequestResponse,
        processed: ImageArtifact,
        extra: Callable[..., Awaitable[None]],
    ) -> ImageOutcome:
        try:
            await extra(AdditionalFileHookPoint.BEFORE_IMAGE_SAVE, response=response, artifact=processed)
            await self.events.emit(EventName.BEFORE_SAVE, EventPayload(url=image.url))
            async with self.output_locks.hold(self.operation.allocator.filesystem.path(self.directory)):
                await self.operation.allocator.refresh_directory(self.directory)
                allocation = await self.operation.allocator.allocate(
                    self.directory,
                    OutputFormatContext(
                        self.operation.manifest,
                        self.chapter,
                        self.operation.source_url,
                        self.operation.site.record.id,
                        image,
                        processed.extension or ".jpeg",
                        resolve_original_filename(image, response),
                        self.operation.plugin_values,
                    ),
                )
                path = self.operation.allocator.filesystem.path(allocation.relative_path)
                if allocation.should_write:
                    try:
                        path = self.operation.allocator.filesystem.write_bytes_atomic(
                            allocation.relative_path, processed.data
                        )
                    except BaseException:
                        await self._settle_allocation(allocation, success=False)
                        raise
                    await self._commit_image_allocation(
                        allocation, self.operation.ledger, self.chapter_position, position, image, path
                    )
                _remember_image(
                    self.operation.ledger,
                    self.chapter_position,
                    position,
                    ImageOutcome(
                        image,
                        ImageOutcomeKind.SAVED if allocation.should_write else ImageOutcomeKind.SKIPPED,
                        str(path),
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _ImageJobError(FailureKind.SAVE, exc, response) from exc
        if allocation.should_write:
            await self.events.emit(EventName.SAVE_SUCCESS, EventPayload(url=image.url, path=str(path)))
            await best_effort_diagnostic(
                self.logger.core,
                "file_saved",
                module="storage",
                chapter_id=self.chapter_id,
                url=image.url,
                path=path,
                bytes_count=len(processed.data),
                count=image.index,
                plugin_id=self.operation.site.record.id,
                url_is_locator=True,
                debug=True,
            )
        outcome = ImageOutcome(
            image, ImageOutcomeKind.SAVED if allocation.should_write else ImageOutcomeKind.SKIPPED, str(path)
        )
        await extra(
            AdditionalFileHookPoint.AFTER_IMAGE_SAVE,
            response=response,
            artifact=processed,
            image_outcome=outcome,
        )

        return outcome

    async def _report_failure(self, position: int, image: ImageResource, exc: _ImageJobError) -> ImageOutcome:
        info, response_url, http_status, failure_path = _failure_info(
            _workflow_image_error(exc, self.operation.ledger),
            exc.response,
            self.operation.allocator.filesystem,
        )
        transport = _failure_transport(exc.kind, exc.cause, exc.response)
        await self.events.emit(
            _IMAGE_FAILURE_EVENTS[exc.kind],
            EventPayload(
                url=image.url,
                response_url=response_url,
                path=str(failure_path) if failure_path is not None else None,
                http_status=http_status,
                stage=_IMAGE_FAILURE_STAGES[exc.kind],
                chapter_id=self.chapter_id,
                image_index=image.index,
                error_code=info.code,
                error_reason=info.reason,
                error_class=info.exception,
                operation="download",
                transport=transport,
            ),
        )
        await best_effort_diagnostic(
            self.logger.core,
            "download_failed",
            module="download",
            chapter_id=self.chapter_id,
            url=image.url,
            count=image.index,
            error=exc.cause,
            plugin_id=self.operation.site.record.id,
            action=_IMAGE_FAILURE_ACTIONS[exc.kind],
            url_is_locator=True,
            debug=True,
        )
        await best_effort_diagnostic(
            self.logger.error_detail,
            exc.cause,
            chapter_id=self.chapter_id,
            url=image.url,
            path=failure_path,
            module="image",
            url_is_locator=True,
        )
        outcome = ImageOutcome(
            image,
            ImageOutcomeKind.FAILED,
            path=str(failure_path) if failure_path is not None else None,
            failure=ImageFailure(
                exc.kind,
                info.exception,
                info.message,
                code=info.code,
                reason=info.reason,
                response_url=response_url,
                http_status=http_status,
                output_path=info.output_path,
                transport=transport,
            ),
        )
        _remember_image(self.operation.ledger, self.chapter_position, position, outcome)
        await self.reporter.record(position, outcome)
        if _is_fatal_image_error(exc, self.config.download.continue_on_image_error, self.operation.ledger):
            context = ImageFailureContext(
                _IMAGE_FAILURE_STAGES[exc.kind],
                transport,
                image.url,
                response_url,
                http_status,
                info.output_path,
            )
            _raise_image_error(exc, self.operation.ledger, context)
        return outcome

    @staticmethod
    async def _commit_image_allocation(
        allocation: OutputAllocation,
        ledger: ImageLedger | None,
        chapter_position: int,
        image_position: int,
        image: ImageResource,
        path: Path,
    ) -> None:
        try:
            await ImageExecutor._settle_allocation(allocation, success=True)
        except asyncio.CancelledError:
            _remember_image(
                ledger, chapter_position, image_position, ImageOutcome(image, ImageOutcomeKind.SAVED, str(path))
            )
            raise

    @staticmethod
    async def _settle_allocation(allocation: OutputAllocation, *, success: bool) -> None:
        await finish_task(allocation.commit() if success else allocation.abort())
