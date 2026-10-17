"""Log records and concrete output sinks."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from ..privacy.log_safety import (
    mask_log_text,
)
from ..privacy.log_safety import safe_log_text as safe_log_text
from ..storage.filesystem import FileSystem


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
    transport: str | None = None


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
        self._lock = asyncio.Lock()
        if filesystem is not None and relative_path is not None:
            self._stream = filesystem.open_text_append(relative_path, encoding="utf-8", newline="")
        elif path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("a", encoding="utf-8", newline="")
        else:
            raise ValueError("ChapterFileSink requires path or filesystem with relative_path")

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
