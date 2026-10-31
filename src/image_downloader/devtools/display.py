"""Console selection and rendering, independent of lossless diagnostic storage."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, TextIO

LEVELS = {name: getattr(logging, name) for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")}
SOURCES = ("event", "python", "stdout", "stderr")
_INFO_EVENTS = {
    "session_opened", "simulation_started", "simulation_finished", "method_started", "method_returned",
    "runtime_log", "runtime_operation_started", "runtime_operation_finished",
    "runtime_download_started", "runtime_download_finished",
}
_WARNING_EVENTS = {
    "http_error", "runtime_request_retry", "runtime_workflow_retry_wait", "runtime_workflow_retry_started",
    "runtime_download_cancelled",
}
_SUMMARY_FIELDS = {
    "sequence", "event", "source", "level", "method", "url", "status", "logger", "plugin_id",
    "chapter_id", "module", "action", "attempt", "count", "message", "mode", "path", "category",
}


def nonempty_pattern(value: str) -> str:
    if not value.strip():
        raise ValueError("log filter patterns must not be empty")
    return value


def event_level(event: str, values: Mapping[str, Any]) -> str:
    if event in _WARNING_EVENTS:
        return "WARNING"
    if event == "http_response" and values.get("status", 0) >= 400:
        return "WARNING"
    if (
        values.get("error") is not None
        or event.endswith(("_failed", "_error"))
        or event == "chapter_error_group"
    ):
        return "ERROR"
    if event in {
        "runtime_operation_started", "runtime_operation_finished",
        "runtime_download_started", "runtime_download_finished",
    } and (values.get("chapter_id") is not None or values.get("action") == "image_fetch"):
        return "DEBUG"
    return "INFO" if event in _INFO_EVENTS else "DEBUG"


@dataclass(frozen=True)
class DisplayPolicy:
    view: str = "summary"
    level: str | None = None
    sources: tuple[str, ...] | None = None
    events: tuple[str, ...] = ()
    excluded_events: tuple[str, ...] = ()
    loggers: tuple[str, ...] = ()
    excluded_loggers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.view not in {"summary", "all"}:
            raise ValueError(f"invalid log view: {self.view}")
        if self.level is not None and self.level not in LEVELS:
            raise ValueError(f"invalid log level: {self.level}")
        if self.sources is not None and any(source not in SOURCES for source in self.sources):
            raise ValueError("invalid log source")
        for pattern in self.events + self.excluded_events + self.loggers + self.excluded_loggers:
            nonempty_pattern(pattern)

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> DisplayPolicy:
        return cls(
            view=options.get("log_view", "summary"),
            level=options.get("log_level"),
            sources=tuple(options["log_sources"]) if options.get("log_sources") is not None else None,
            events=tuple(options.get("events") or ()),
            excluded_events=tuple(options.get("excluded_events") or ()),
            loggers=tuple(options.get("loggers") or ()),
            excluded_loggers=tuple(options.get("excluded_loggers") or ()),
        )

    def matches(self, source: str, event: str, *, level: int | None = None, logger: str | None = None) -> bool:
        sources = self.sources if self.sources is not None else (SOURCES if self.view == "all" else SOURCES[:2])
        threshold = LEVELS[self.level or ("DEBUG" if self.view == "all" else "INFO")]
        return (
            source in sources
            and (level is None or level >= threshold)
            and (not self.events or any(fnmatchcase(event, pattern) for pattern in self.events))
            and not any(fnmatchcase(event, pattern) for pattern in self.excluded_events)
            and (
                not self.loggers
                or source == "python" and logger is not None and any(fnmatchcase(logger, p) for p in self.loggers)
            )
            and not (
                source == "python" and logger is not None
                and any(fnmatchcase(logger, p) for p in self.excluded_loggers)
            )
        )


class ConsoleOutput:
    """Called under RawOutput's lock, including from native stream pump threads."""

    def __init__(self, stream: TextIO, policy: DisplayPolicy) -> None:
        self.stream, self.policy = stream, policy
        self.hidden_events = 0
        self.hidden_bytes = {"stdout": 0, "stderr": 0}
        self._finished = False
        self._line_open = False

    def event(self, payload: dict[str, Any], level: int) -> None:
        if not self.policy.matches(payload["source"], payload["event"], level=level, logger=payload.get("logger")):
            self.hidden_events += 1
            return
        if self.policy.view == "summary":
            summary = {key: value for key, value in payload.items() if key in _SUMMARY_FIELDS and value is not None}
            if isinstance(payload.get("error"), dict):
                summary["error"] = {key: payload["error"][key] for key in ("type", "message")}
            payload = summary
        self._json(payload)

    def write_stream(self, name: str, data: bytes) -> None:
        if not self.policy.matches(name, name):
            self.hidden_bytes[name] += len(data)
            return
        if not data:
            return
        self.stream.flush()
        buffer = getattr(self.stream, "buffer", None)
        if buffer is not None:
            buffer.write(data)
            buffer.flush()
        else:
            self.stream.write(data.decode("utf-8", errors="backslashreplace"))
            self.stream.flush()
        self._line_open = not data.endswith(b"\n")

    def finish(self, directory: str) -> None:
        if not self._finished:
            self._finished = True
            self._json({
                "event": "diagnostics_summary", "hidden_events": self.hidden_events,
                "hidden_stream_bytes": self.hidden_bytes, "run_directory": directory,
            })

    def _json(self, payload: dict[str, Any]) -> None:
        if self._line_open:
            self.stream.write("\n")
            self._line_open = False
        self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.stream.flush()
