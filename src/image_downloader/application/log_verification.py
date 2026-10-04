"""Offline, streaming inspection of the application's existing log formats."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from ..exceptions import ConfigurationError, StorageSafetyError
from ..privacy.log_safety import safe_log_text
from ..storage.filesystem import FileSystem
from ..storage.path_safety import canonical_path

_DEBUG = re.compile(r"^\d{4}-\d\d-\d\dT\S+ \[[^\]\r\n]+\] [a-z_]+ (.*)$")
_FAILURE_EVENTS = frozenset(
    {
        "download_failed",
        "operation_failed",
        "notification_failed",
        "event_observer_failed",
        "site_operation_close_failed",
    }
)
_ERROR = re.compile(r"^error=[A-Za-z_][A-Za-z0-9_.]*(?:\s|$)")
_GROUP = re.compile(r"^error: [a-z_]+ \(count=\d+\)$")


class VerificationFileChangedError(StorageSafetyError):
    """A file disappeared or changed after verification started."""


def issue(code: str, *, path: str | None = None, line: int | None = None, detail: str = "") -> dict[str, Any]:
    return {
        "code": code,
        "path": safe_log_text(path) if path else None,
        "line": line,
        "message": safe_log_text(detail or code),
    }


def _snapshot(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


@contextmanager
def stable_file(filesystem: FileSystem, relative: Path) -> Iterator[BinaryIO]:
    """Reject unsafe files and changes before, during, or after a streamed read."""
    path = filesystem._prepare_file(relative, create_parent=False)
    before = filesystem._require_regular_file(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if _snapshot(os.fstat(stream.fileno())) != _snapshot(before):
            raise VerificationFileChangedError("file changed before verification")
        filesystem._assert_regular(path, os.fstat(stream.fileno()))
        yield stream
        filesystem._assert_safe_ancestors(path.parent)
        try:
            after = filesystem._require_regular_file(path)
        except FileNotFoundError as exc:
            raise VerificationFileChangedError("file disappeared during verification") from exc
        if _snapshot(os.fstat(stream.fileno())) != _snapshot(before) or _snapshot(after) != _snapshot(before):
            raise VerificationFileChangedError("file changed during verification")


class _LogParser:
    def __init__(self, relative: Path) -> None:
        self.relative = relative.as_posix()
        self.debug = relative.name == "debug.log"
        self.errors: list[dict[str, Any]] = []
        self.unknown: list[dict[str, Any]] = []
        self.recognized = False
        self.nonempty = False
        self.session: int | None = None
        self.header: list[tuple[int, str]] = []

    def problem(self, code: str, line: int | None = None) -> None:
        self.unknown.append(issue(code, path=self.relative, line=line))

    def feed(self, text: str, number: int) -> None:
        self.nonempty = self.nonempty or bool(text.strip())
        if self.debug:
            self.debug_record(text, number)
        else:
            self.chapter_record(text, number)

    def debug_record(self, text: str, number: int) -> None:
        if not text.strip():
            return
        match = _DEBUG.fullmatch(text)
        if match is None:
            self.problem("unrecognized_record", number)
            return
        body = match[1]
        if not body.startswith("event=") and not _ERROR.match(body):
            self.problem("unrecognized_record", number)
            return
        self.recognized = True
        event = body.split(" ", 1)[0].removeprefix("event=")
        python_error = re.match(r"^event=python_log level=(?:ERROR|CRITICAL)(?:\s|$)", body)
        if _ERROR.match(body) or event in _FAILURE_EVENTS or python_error:
            self.errors.append(issue("log_error", path=self.relative, line=number, detail=body))

    def chapter_record(self, text: str, number: int) -> None:
        if self.header:
            self.header.append((number, text))
            if text == "---" or len(self.header) > 5:
                self.finish_header()
            return
        if text == "---":
            self.header = [(number, text)]
        elif text == "done":
            if self.session is None:
                self.problem("unexpected_done", number)
            self.session = None
        elif _GROUP.fullmatch(text) or _ERROR.match(text):
            self.errors.append(issue("log_error", path=self.relative, line=number, detail=text))
        elif text.strip() and (
            self.session is None
            or not text.startswith(
                (
                    "download: ",
                    "save: ",
                    "image_url: ",
                    "image_index: ",
                    "stage: ",
                    "response_url: ",
                    "http_status: ",
                    "transport: ",
                    "reason_code: ",
                    "reason: ",
                    "exception: ",
                    "detail: ",
                )
            )
        ):
            self.problem("unrecognized_record", number)

    def finish_header(self) -> None:
        values = [value for _, value in self.header]
        valid = (
            len(values) in {4, 5}
            and values[-1] == "---"
            and values[1].startswith("url: ")
            and values[2].startswith("title: ")
            and (len(values) == 4 or values[3].startswith("subtitle: "))
        )
        if valid:
            self.recognized = True
            if self.session is not None:
                self.problem("incomplete_chapter", self.session)
            self.session = self.header[0][0]
        else:
            self.problem("invalid_header", self.header[0][0])
        self.header = []

    def finish(self) -> None:
        if self.session is not None or self.header:
            self.problem("incomplete_chapter", self.session or self.header[0][0])
        if not self.nonempty or not self.recognized:
            self.problem("empty_log" if not self.nonempty else "unrecognized_log")


def _scan(filesystem: FileSystem, relative: Path) -> dict[str, Any]:
    parser = _LogParser(relative)
    try:
        with stable_file(filesystem, relative) as stream:
            for number, raw in enumerate(stream, 1):
                text = raw.decode("utf-8-sig" if number == 1 else "utf-8").rstrip("\r\n")
                parser.feed(text, number)
        parser.finish()
    except (OSError, UnicodeError, StorageSafetyError):
        parser.problem("unreadable_or_changed_log")
    return {"path": safe_log_text(relative.as_posix()), "errors": parser.errors, "indeterminate": parser.unknown}


def _discover(filesystem: FileSystem, unknown: list[dict[str, Any]]) -> list[Path]:
    files: list[Path] = []
    pending = [Path(".")]
    while pending:
        parent = pending.pop()
        try:
            for relative in filesystem.child_paths(parent):
                state = filesystem.path(relative).lstat()
                if filesystem._is_unsafe(state):
                    unknown.append(issue("unsafe_path", path=relative.as_posix()))
                elif stat.S_ISDIR(state.st_mode):
                    pending.append(relative)
                elif relative.name == "log.log":
                    files.append(relative)
        except (OSError, StorageSafetyError):
            unknown.append(issue("unreadable_directory", path=str(parent)))
    return sorted(files, key=lambda p: p.as_posix())


def verify_logs(target: Path) -> dict[str, Any]:
    unknown: list[dict[str, Any]] = []
    files: list[Path] = []
    filesystem = FileSystem(target.parent)
    try:
        canonical_path(target, "verification target")
        state = target.lstat()
        if filesystem._is_unsafe(state):
            unknown.append(issue("unsafe_path", path=target.name))
        elif stat.S_ISDIR(state.st_mode):
            filesystem = FileSystem(target)
            files = _discover(filesystem, unknown)
        elif target.name in {"log.log", "debug.log"}:
            files = [Path(target.name)]
        else:
            unknown.append(issue("unsupported_log_file", path=target.name))
    except ConfigurationError:
        unknown.append(issue("unsafe_or_unreadable_target", path=target.name))
    except OSError:
        unknown.append(issue("log_target_unavailable", path=target.name))
    results = [_scan(filesystem, path) for path in files]
    if not files and not unknown:
        unknown.append(issue("logs_not_found"))
    errors = sum(len(file["errors"]) for file in results)
    count = len(unknown) + sum(len(file["indeterminate"]) for file in results)
    return {
        "status": "indeterminate" if count else "failed" if errors else "passed",
        "summary": {"files": len(results), "errors": errors, "indeterminate": count},
        "files": results,
        "indeterminate": unknown,
    }
