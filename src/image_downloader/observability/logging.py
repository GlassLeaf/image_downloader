from __future__ import annotations

import asyncio
import contextvars
import logging
import queue
import re
import sys
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TextIO, cast

from ..privacy.log_safety import (
    _safe_text,
    mask_log_text,
    safe_exception_name,
    safe_relative_path,
    safe_url,
)
from ..privacy.log_safety import safe_log_text as safe_log_text
from .diagnostic_safety import warn_diagnostic_failure

if TYPE_CHECKING:
    from ..storage import FileSystem

_SAFE_MODULES = {"auth", "download", "http", "image", "notification", "plugin", "processor", "runtime", "storage"}
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
    "response_received",
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


@dataclass(frozen=True)
class LogRecord:
    message: str
    chapter_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    trusted: bool = False

    def formatted(self) -> str:
        return self.message if self.trusted else mask_log_text(self.message)


@dataclass(frozen=True, slots=True)
class ChapterFailureRecord:
    """Already-classified failure data rendered by the chapter logger."""

    url: str
    response_url: str | None
    path: str | Path | None
    stage: str
    image_index: int
    http_status: int | None
    code: str
    reason: str
    exception_type: str
    message: str


class LogSink:
    async def write(self, record: LogRecord) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class ChapterFileSink(LogSink):
    def __init__(
        self, path: Path | None = None, *, filesystem: FileSystem | None = None, relative_path: Path | None = None
    ) -> None:
        self.path = path
        if filesystem is not None and relative_path is not None:
            self._stream = filesystem.open_text_append(relative_path, encoding="utf-8", newline="")
        elif path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("a", encoding="utf-8", newline="")
        else:
            raise ValueError("ChapterFileSink requires path or filesystem with relative_path")
        self._lock = asyncio.Lock()

    async def write(self, record: LogRecord) -> None:
        async with self._lock:
            self._stream.write(record.formatted())
            if not record.message.endswith("\n"):
                self._stream.write("\n")
            self._stream.flush()

    async def close(self) -> None:
        async with self._lock:
            if not self._stream.closed:
                self._stream.close()


class DebugFileSink(ChapterFileSink):
    """Detailed, file-only diagnostics sink."""

    async def write(self, record: LogRecord) -> None:
        detail = datetime.now(UTC).isoformat(timespec="milliseconds")
        detail += f" [{record.chapter_id or '-'}]"
        detail += " " + record.metadata.get("module", "library")
        detailed = LogRecord(f"{detail} {record.message}", record.chapter_id, record.metadata, record.trusted)
        await super().write(detailed)


class ConsoleSink(LogSink):
    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout
        self._lock = asyncio.Lock()

    async def write(self, record: LogRecord) -> None:
        async with self._lock:
            self.stream.write(record.formatted())
            if not record.message.endswith("\n"):
                self.stream.write("\n")
            self.stream.flush()


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
            parts.append(
                "url="
                + safe_url(
                    url,
                    safe_query_parameters=self._safe_query_parameters,
                    safe_fragment_parameters=self._safe_fragment_parameters,
                )
            )
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

    async def chapter_header(self, chapter_id: str, *, url: str, title: str, subtitle: str | None) -> None:
        lines = ["---", f"url: {self.safe_url(url)}", f"title: {_safe_text(mask_log_text(title), 1024)}"]
        if subtitle:
            lines.append(f"subtitle: {_safe_text(mask_log_text(subtitle), 1024)}")
        lines.append("---")
        await self._write_chapter(chapter_id, "\n".join(lines))

    async def chapter_download(self, chapter_id: str, url: str) -> None:
        await self._write_chapter(chapter_id, "download: " + self.safe_url(url))

    async def chapter_save(self, chapter_id: str, path: str | Path) -> None:
        output_root = self._chapter_output_roots.get(chapter_id, self._output_root)
        await self._write_chapter(chapter_id, "save: " + safe_relative_path(path, output_root))

    async def chapter_error_group(self, chapter_id: str, category: str, records: list[ChapterFailureRecord]) -> None:
        lines = [f"error: {_safe_text(mask_log_text(category), 128)} (count={len(records)})"]
        output_root = self._chapter_output_roots.get(chapter_id, self._output_root)
        for record in records:
            lines.append("image_url: " + self.safe_url(record.url))
            lines.append(f"image_index: {record.image_index}")
            lines.append("stage: " + _safe_text(mask_log_text(record.stage), 64))
            if record.response_url is not None:
                lines.append("response_url: " + self.safe_url(record.response_url))
            if record.http_status is not None:
                lines.append(f"http_status: {record.http_status}")
                if record.stage in {"image_processing", "image_save"}:
                    lines.append("transport: completed")
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
    ) -> None:
        """Write a masked error body to chapter and debug files, never to console/events."""
        parts = [f"error={safe_exception_name(error)}"]
        if url is not None:
            parts.append("url=" + self.safe_url(url))
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
