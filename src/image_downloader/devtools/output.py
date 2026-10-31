"""Lossless development values and independently collected raw diagnostics."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import logging
import os
import sys
import threading
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TextIO


def read_document(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


class RawOutput:
    def __init__(self, directory: Path, *, binary: str = "file", stream: TextIO | None = None) -> None:
        self.directory, self.binary = directory, binary
        self.stream = stream if stream is not None else sys.stderr
        directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sequence = 0
        self.recorded_http: list[dict[str, Any]] = []

    def value(self, value: Any, seen: set[int] | None = None) -> Any:
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, Enum):
            return self.value(value.value)
        if isinstance(value, (datetime, date, Path)):
            return str(value)
        if isinstance(value, bytes):
            digest = hashlib.sha256(value).hexdigest()
            path = self.directory / "bodies" / digest
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(value)
            result = {"$file": str(path), "size": len(value), "sha256": digest}
            if self.binary == "base64":
                result["base64"] = base64.b64encode(value).decode("ascii")
            elif self.binary == "hex":
                result["hex"] = value.hex()
            return result
        if isinstance(value, BaseException):
            return {
                "type": type(value).__name__,
                "message": str(value),
                "traceback": "".join(traceback.format_exception(value)),
            }
        visited = set() if seen is None else set(seen)
        if id(value) in visited:
            return {"$cycle": type(value).__name__}
        visited.add(id(value))
        if is_dataclass(value) and not isinstance(value, type):
            return {
                "$type": type(value).__name__,
                **{field.name: self.value(getattr(value, field.name), visited) for field in fields(value)},
            }
        if isinstance(value, Mapping):
            return {str(key): self.value(item, visited) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.value(item, visited) for item in value]
        # Expose actual context properties, without dumping request clients or vaults.
        if type(value).__name__ in {"PluginExecutionContext", "TransformContext", "PluginFormatContext"}:
            names = [name for name, prop in inspect.getmembers(type(value)) if isinstance(prop, property)]
            return {
                "$type": type(value).__name__,
                **{
                    name: self.value(getattr(value, name), visited)
                    for name in names
                    if name not in {"requests", "secrets"}
                },
            }
        return {"$type": type(value).__name__, "repr": repr(value)}

    def event(self, event: str, **values: Any) -> None:
        with self._lock:
            self._sequence += 1
            payload = {
                "sequence": self._sequence,
                "event": event,
                **{name: self.value(value) for name, value in values.items()},
            }
            rendered = json.dumps(payload, ensure_ascii=False, default=str)
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as file:
                file.write(rendered + "\n")
            self.stream.write(rendered + "\n")
            self.stream.flush()

    def result(self, value: Any) -> Any:
        result = self.value(value)
        (self.directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


class RawLogHandler(logging.Handler):
    def __init__(self, output: RawOutput) -> None:
        super().__init__()
        self.output = output

    def emit(self, record: logging.LogRecord) -> None:
        self.output.event(
            "python_log",
            logger=record.name,
            level=record.levelname,
            message=record.getMessage(),
            error=record.exc_info[1] if record.exc_info else None,
        )


class CapturedStream:
    """Tee plugin streams without ever writing into the structured result channel."""

    def __init__(self, output: RawOutput, name: str, destination: TextIO) -> None:
        self.output, self.name, self.destination = output, name, destination
        self.encoding = "utf-8"
        self.buffer = BinaryCapturedStream(output, name, destination)

    def write(self, text: str) -> int:
        with self.output._lock:
            with (self.output.directory / (self.name + ".log")).open("a", encoding="utf-8") as file:
                file.write(text)
            self.destination.write(text)
            self.destination.flush()
        return len(text)

    def flush(self) -> None:
        self.destination.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return 1 if self.name == "stdout" else 2


class BinaryCapturedStream:
    def __init__(self, output: RawOutput, name: str, destination: TextIO) -> None:
        self.output, self.name, self.destination = output, name, destination

    def write(self, data: bytes) -> int:
        with self.output._lock:
            with (self.output.directory / (self.name + ".log")).open("ab") as file:
                file.write(data)
            self.destination.flush()
            self.destination.buffer.write(data)
            self.destination.buffer.flush()
        return len(data)

    def flush(self) -> None:
        self.destination.flush()

    def fileno(self) -> int:
        return 1 if self.name == "stdout" else 2


@contextmanager
def capture_native_streams(output: RawOutput, diagnostics: TextIO) -> Iterator[None]:
    """Keep os.write, native extensions and plugin subprocesses off the IPC channel."""
    streams: list[tuple[int, int, threading.Thread]] = []

    def pump(reader: int, name: str) -> None:
        try:
            while data := os.read(reader, 64 * 1024):
                with output._lock:
                    with (output.directory / (name + ".log")).open("ab") as file:
                        file.write(data)
                    diagnostics.flush()
                    remaining = memoryview(data)
                    while remaining:
                        remaining = remaining[os.write(diagnostics.fileno(), remaining) :]
        finally:
            os.close(reader)

    try:
        for descriptor, name in ((1, "stdout"), (2, "stderr")):
            saved = os.dup(descriptor)
            reader, writer = os.pipe()
            os.dup2(writer, descriptor)
            os.close(writer)
            thread = threading.Thread(target=pump, args=(reader, name), daemon=True)
            streams.append((descriptor, saved, thread))
            thread.start()
        yield
    finally:
        for descriptor, saved, _ in streams:
            os.dup2(saved, descriptor)
            os.close(saved)
        for _, _, thread in streams:
            thread.join(timeout=1)
