from __future__ import annotations

import asyncio
import contextvars
import logging
import queue
import re
from collections.abc import Awaitable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..privacy.log_safety import (
    _safe_text,
    mask_log_text,
    safe_exception_name,
    safe_locator,
    safe_relative_path,
    safe_url,
)
from ..privacy.log_safety import safe_log_text as safe_log_text
from .chapter_header import header_key_order, is_extra_header_key
from .diagnostic_safety import warn_diagnostic_failure
from .log_sinks import ChapterFailureRecord as ChapterFailureRecord
from .log_sinks import ChapterFileSink as ChapterFileSink
from .log_sinks import ConsoleSink as ConsoleSink
from .log_sinks import DebugFileSink as DebugFileSink
from .log_sinks import LogRecord as LogRecord
from .log_sinks import LogSink as LogSink

__all__ = [
    "LogRecord",
    "ChapterFailureRecord",
    "LogSink",
    "ChapterFileSink",
    "DebugFileSink",
    "ConsoleSink",
    "DownloadLogger",
    "safe_log_text",
    "mask_log_text",
    "safe_exception_name",
    "safe_locator",
    "safe_relative_path",
    "safe_url",
]

if TYPE_CHECKING:
    from ..storage import FileSystem

_SAFE_MODULES = {
    "auth",
    "download",
    "http",
    "image",
    "notification",
    "plugin",
    "processor",
    "runtime",
    "storage",
    "workflow",
}
_SAFE_EVENTS = {
    "auth_apply_started",
    "auth_apply_finished",
    "auth_refresh_started",
    "auth_refresh_finished",
    "chapter_started",
    "chapter_finished",
    "download_started",
    "request_started",
    "request_retry",
    "workflow_retry_wait",
    "workflow_retry_started",
    "response_received",
    "site_operation_close_failed",
    "image_processed",
    "file_saved",
    "download_failed",
    "download_finished",
    "download_cancelled",
    "operation_failed",
    "operation_started",
    "plugin_call_started",
    "plugin_call_finished",
    "plugin_selected",
    "python_log",
    "notification_failed",
    "notification_sent",
    "event_observer_failed",
    "cookie_store_access",
}
_CAPTURE_OWNER: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "image_downloader_log_capture_owner", default=None
)


class _CapturedPythonLogHandler(logging.Handler):
    """Collect standard-library log records without letting plugin code write files."""

    def __init__(self, records: queue.Queue[LogRecord], owner: object) -> None:
        super().__init__()
        self.records = records
        self.owner = owner

    def emit(self, record: logging.LogRecord) -> None:
        if _CAPTURE_OWNER.get() is not self.owner:
            return
        try:
            message = record.getMessage()
            self.records.put_nowait(
                LogRecord(
                    " ".join(
                        (
                            "event=python_log",
                            f"level={record.levelname}",
                            f"logger={record.name}",
                            f"message={message[:4096]}",
                        )
                    ),
                    metadata={"module": "plugin"},
                )
            )
        except Exception:
            # Logging is diagnostic-only. Never propagate an observer failure.
            return


class DownloadLogger:
    def __init__(self, sinks: list[LogSink] | None = None) -> None:
        self.sinks = list(sinks or [])
        self._chapter_sinks: dict[str, ChapterFileSink] = {}
        self._chapter_output_roots: dict[str, Path | None] = {}
        self._lock = asyncio.Lock()
        self._safe_query_parameters: set[str] = set()
        self._safe_fragment_parameters: set[str] = set()
        self._output_root: Path | None = None
        self._chapter_summary_console = False
        self._python_log_records: queue.Queue[LogRecord] = queue.Queue(maxsize=10_000)
        self._python_log_handler: _CapturedPythonLogHandler | None = None
        self._python_loggers: tuple[logging.Logger, ...] = ()
        self._capture_owner = object()
        self._capture_token: contextvars.Token[object | None] | None = None

    def configure_safety(self, logging: dict[str, object], output_root: str | Path | None = None) -> None:
        query = logging.get("safe_query_parameters", [])
        fragment = logging.get("safe_fragment_parameters", [])
        self._safe_query_parameters = {
            str(value).lower()
            for value in cast(Iterable[object], query)
            if isinstance(query, (list, tuple, set, frozenset))
        }
        self._safe_fragment_parameters = {
            str(value).lower()
            for value in cast(Iterable[object], fragment)
            if isinstance(fragment, (list, tuple, set, frozenset))
        }
        self._output_root = Path(output_root) if output_root is not None else None

    def safe_url(self, value: str) -> str:
        return safe_url(
            value,
            safe_query_parameters=self._safe_query_parameters,
            safe_fragment_parameters=self._safe_fragment_parameters,
        )

    def safe_locator(self, value: str) -> str:
        return safe_locator(
            value,
            safe_query_parameters=self._safe_query_parameters,
            safe_fragment_parameters=self._safe_fragment_parameters,
        )

    def set_chapter_summary_console(self, enabled: bool) -> None:
        """Mirror chapter ``log.log`` records to ConsoleSink during a download run."""
        self._chapter_summary_console = enabled

    def begin_python_log_capture(self, namespaces: Iterable[str]) -> None:
        """Capture standard Python log records for one service operation.

        Plugins keep their capability-reduced context, but may use
        ``logging.getLogger(__name__)`` for diagnostics. The records are written
        only to the masked debug sink when :meth:`flush_python_log_capture` runs.
        """
        if self._python_log_handler is not None:
            raise RuntimeError("Python log capture is already active")
        names = tuple(dict.fromkeys(name for name in namespaces if name))
        if not names:
            return
        self._capture_token = _CAPTURE_OWNER.set(self._capture_owner)
        self._python_log_handler = _CapturedPythonLogHandler(self._python_log_records, self._capture_owner)
        self._python_loggers = tuple(logging.getLogger(name) for name in names)
        for plugin_logger in self._python_loggers:
            plugin_logger.addHandler(self._python_log_handler)

    async def flush_python_log_capture(self) -> None:
        """Write all currently captured standard-library log records to debug.log."""
        records: list[LogRecord] = []
        while True:
            try:
                records.append(self._python_log_records.get_nowait())
            except queue.Empty:
                break
        if records:
            await self._deliver(
                *(sink.write(record) for sink in self.sinks if isinstance(sink, DebugFileSink) for record in records)
            )

    def end_python_log_capture(self) -> None:
        if self._python_log_handler is not None:
            for plugin_logger in self._python_loggers:
                plugin_logger.removeHandler(self._python_log_handler)
            self._python_log_handler = None
            self._python_loggers = ()
        if self._capture_token is not None:
            _CAPTURE_OWNER.reset(self._capture_token)
            self._capture_token = None

    async def core(
        self,
        event: str,
        *,
        module: str,
        chapter_id: str | None = None,
        url: str | None = None,
        path: str | Path | None = None,
        method: str | None = None,
        status: int | None = None,
        bytes_count: int | None = None,
        count: int | None = None,
        plugin_id: str | None = None,
        attempt: int | None = None,
        action: str | None = None,
        error: Exception | None = None,
        url_is_locator: bool = False,
        debug: bool = False,
        include_chapter: bool = False,
    ) -> None:
        """Log a core event from a strict allowlist; never render caller-provided messages."""
        parts = [
            f"event={event if event in _SAFE_EVENTS else 'unknown'}",
            f"module={module if module in _SAFE_MODULES else 'library'}",
        ]
        if chapter_id is not None:
            parts.append(f"chapter={chapter_id if chapter_id.isdigit() else '[REDACTED]'}")
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            parts.append(f"method={method}")
        if status is not None and 100 <= status <= 599:
            parts.append(f"status={status}")
        for key, value in (("bytes", bytes_count), ("count", count)):
            if value is not None and value >= 0:
                parts.append(f"{key}={value}")
        if attempt is not None and attempt >= 1:
            parts.append(f"attempt={attempt}")
        if plugin_id is not None:
            parts.append("plugin=" + _safe_text(mask_log_text(plugin_id), 256))
        if action is not None and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", action):
            parts.append(f"action={action}")
        if url is not None:
            parts.append("url=" + (self.safe_locator(url) if url_is_locator else self.safe_url(url)))
        if path is not None:
            parts.append("path=" + safe_relative_path(path, self._output_root))
        if error is not None:
            parts.append("error=" + safe_exception_name(error))
        await self._write(
            LogRecord(" ".join(parts), chapter_id, {"module": module}, trusted=True),
            debug_only=debug,
            include_chapter=include_chapter,
        )

    def register_chapter(
        self,
        chapter_id: str,
        path: Path | None = None,
        *,
        filesystem: FileSystem | None = None,
        relative_path: Path | None = None,
    ) -> None:
        if chapter_id in self._chapter_sinks:
            raise RuntimeError(f"chapter logger is already registered: {chapter_id}")
        self._chapter_sinks[chapter_id] = ChapterFileSink(path, filesystem=filesystem, relative_path=relative_path)
        self._chapter_output_roots[chapter_id] = filesystem.root if filesystem is not None else self._output_root

    async def log(self, message: str, *, chapter_id: str | None = None, module: str = "library") -> None:
        await self._write(LogRecord(message, chapter_id, {"module": module}))

    async def chapter_header(
        self,
        chapter_id: str,
        *,
        url: str,
        title: str,
        subtitle: str | None,
        content_title: str | None = None,
        author: str | None = None,
        extra_fields: Mapping[str, str] | None = None,
    ) -> None:
        fields = dict(extra_fields or {})
        if any(not isinstance(key, str) or not is_extra_header_key(key) for key in fields):
            raise ValueError("invalid chapter header field")
        if any(not isinstance(value, str) for value in fields.values()):
            raise TypeError("chapter header fields must contain strings")
        lines = ["---", f"url: {self.safe_url(url)}"]
        if content_title is not None:
            lines.append(f"content_title: {_safe_text(mask_log_text(content_title), 1024)}")
        if author:
            lines.append(f"author: {_safe_text(mask_log_text(author), 1024)}")
        lines.append(f"title: {_safe_text(mask_log_text(title), 1024)}")
        if subtitle:
            lines.append(f"subtitle: {_safe_text(mask_log_text(subtitle), 1024)}")
        for key in sorted(fields, key=header_key_order):
            if fields[key]:
                value = mask_log_text(f"{key}: {fields[key]}").partition(": ")[2]
                lines.append(f"{key}: {_safe_text(value, 1024)}")
        lines.append("---")
        await self._write_chapter(chapter_id, "\n".join(lines))

    async def chapter_download(self, chapter_id: str, url: str) -> None:
        await self._write_chapter(chapter_id, "download: " + self.safe_locator(url))

    async def chapter_save(self, chapter_id: str, path: str | Path) -> None:
        output_root = self._chapter_output_roots.get(chapter_id, self._output_root)
        await self._write_chapter(chapter_id, "save: " + safe_relative_path(path, output_root))

    async def chapter_error_group(self, chapter_id: str, category: str, records: list[ChapterFailureRecord]) -> None:
        lines = [f"error: {_safe_text(mask_log_text(category), 128)} (count={len(records)})"]
        output_root = self._chapter_output_roots.get(chapter_id, self._output_root)
        for record in records:
            lines.append("image_url: " + self.safe_locator(record.url))
            lines.append(f"image_index: {record.image_index}")
            lines.append("stage: " + _safe_text(mask_log_text(record.stage), 64))
            if record.response_url is not None:
                lines.append("response_url: " + self.safe_url(record.response_url))
            if record.http_status is not None:
                lines.append(f"http_status: {record.http_status}")
            transport = record.transport
            if (
                transport is None
                and record.stage in {"image_processing", "image_save"}
                and record.http_status is not None
            ):
                transport = "completed"
            if transport is not None:
                lines.append("transport: " + _safe_text(mask_log_text(transport), 64))
            if record.path is not None:
                lines.append("save: " + safe_relative_path(record.path, output_root))
            lines.append("reason_code: " + _safe_text(mask_log_text(record.code), 128))
            lines.append("reason: " + _safe_text(mask_log_text(record.reason), 1024))
            exception_type = (
                record.exception_type
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,255}", record.exception_type)
                else "[REDACTED]"
            )
            lines.append("exception: " + exception_type)
            detail = mask_log_text(
                record.message,
                safe_query_parameters=self._safe_query_parameters,
                safe_fragment_parameters=self._safe_fragment_parameters,
            )
            if detail:
                lines.append("detail: " + _safe_text(detail, 4096))
        await self._write_chapter(chapter_id, "\n".join(lines))

    async def chapter_done(self, chapter_id: str) -> None:
        await self._write_chapter(chapter_id, "done")
        await self.close_chapter(chapter_id)

    async def close_chapter(self, chapter_id: str) -> None:
        """Release one chapter sink; safe to call after normal completion."""
        sink = self._chapter_sinks.pop(chapter_id, None)
        self._chapter_output_roots.pop(chapter_id, None)
        if sink is not None:
            await sink.close()

    async def error_detail(
        self,
        error: Exception,
        *,
        chapter_id: str | None = None,
        url: str | None = None,
        path: str | Path | None = None,
        module: str = "download",
        url_is_locator: bool = False,
    ) -> None:
        """Write a masked error body to chapter and debug files, never to console/events."""
        parts = [f"error={safe_exception_name(error)}"]
        if url is not None:
            parts.append("url=" + (self.safe_locator(url) if url_is_locator else self.safe_url(url)))
        if path is not None:
            parts.append("path=" + safe_relative_path(path, self._output_root))
        detail = mask_log_text(
            str(error),
            safe_query_parameters=self._safe_query_parameters,
            safe_fragment_parameters=self._safe_fragment_parameters,
        )
        if detail:
            parts.append("detail=" + _safe_text(detail, 4096))
        record = LogRecord(" ".join(parts), chapter_id, {"module": module}, trusted=True)
        targets: list[LogSink] = []
        if chapter_id is not None and chapter_id in self._chapter_sinks:
            targets.append(self._chapter_sinks[chapter_id])
        targets.extend(sink for sink in self.sinks if isinstance(sink, DebugFileSink))
        await self._deliver(*(sink.write(record) for sink in targets))

    async def _write(self, record: LogRecord, *, debug_only: bool = False, include_chapter: bool = True) -> None:
        if debug_only:
            await self._deliver(*(sink.write(record) for sink in self.sinks if isinstance(sink, DebugFileSink)))
            return
        targets = [
            sink
            for sink in self.sinks
            if not isinstance(sink, DebugFileSink)
            and not (self._chapter_summary_console and isinstance(sink, ConsoleSink))
        ]
        if include_chapter and record.chapter_id and record.chapter_id in self._chapter_sinks:
            targets.append(self._chapter_sinks[record.chapter_id])
        debug_targets = [sink for sink in self.sinks if isinstance(sink, DebugFileSink)]
        await self._deliver(*(sink.write(record) for sink in targets + debug_targets))

    async def _write_chapter(self, chapter_id: str, message: str) -> None:
        sink = self._chapter_sinks.get(chapter_id)
        record = LogRecord(message, chapter_id, {"module": "download"}, trusted=True)
        targets: list[LogSink] = [sink] if sink is not None else []
        if self._chapter_summary_console:
            targets.extend(item for item in self.sinks if isinstance(item, ConsoleSink))
        await self._deliver(*(target.write(record) for target in targets))

    @staticmethod
    async def _deliver(*writes: Awaitable[object]) -> None:
        results = await asyncio.gather(*writes, return_exceptions=True)
        failed = False
        for result in results:
            if isinstance(result, Exception):
                failed = True
            elif isinstance(result, BaseException):
                raise result
        if failed:
            warn_diagnostic_failure()

    async def debug(
        self, message: str, *, chapter_id: str | None = None, module: str = "library", **metadata: str
    ) -> None:
        record = LogRecord(message, chapter_id, {"module": module, **metadata})
        await asyncio.gather(*(sink.write(record) for sink in self.sinks if isinstance(sink, DebugFileSink)))

    async def close(self) -> None:
        try:
            self.end_python_log_capture()
        except Exception:
            warn_diagnostic_failure()
        try:
            await self.flush_python_log_capture()
        except Exception:
            warn_diagnostic_failure()
        sinks = (*self.sinks, *self._chapter_sinks.values())
        self._chapter_sinks.clear()
        self._chapter_output_roots.clear()
        results = await asyncio.gather(*(sink.close() for sink in sinks), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                warn_diagnostic_failure()
            elif isinstance(result, BaseException):
                raise result
