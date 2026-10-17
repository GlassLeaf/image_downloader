"""Stable, root-confined streaming reads and artifact existence checks."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Literal

from ..exceptions import StorageSafetyError
from ..storage.filesystem import FileSystem


class VerificationFileChangedError(StorageSafetyError):
    """A file disappeared or changed after verification started."""


def classify_path(path: Path) -> Literal["unsafe", "directory", "other"]:
    """Inspect a node without following links, including verification targets."""
    state = path.lstat()
    if FileSystem._is_unsafe(state):
        return "unsafe"
    return "directory" if stat.S_ISDIR(state.st_mode) else "other"


def _snapshot(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def has_missing_ancestor(filesystem: FileSystem, relative: Path) -> bool:
    """Distinguish an absent artifact from an unsafe or inaccessible path."""
    current = filesystem.root
    try:
        filesystem.validate_relative(relative)
        for component in relative.parts:
            current /= component
            if filesystem._is_unsafe(current.lstat()):
                return False
    except FileNotFoundError:
        return True
    except (OSError, StorageSafetyError):
        pass
    return False


@contextmanager
def stable_file(filesystem: FileSystem, relative: Path) -> Iterator[BinaryIO]:
    """Reject unsafe files and changes before, during, or after a streamed read."""
    path = filesystem._prepare_file(relative, create_parent=False)
    before = filesystem._require_regular_file(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        # Windows can report creation time via lstat and change time via fstat
        # in st_ctime_ns (CPython issue #157671). Compare the common fields here,
        # then retain each API's full snapshot for the checks after reading.
        if _snapshot(opened)[:-1] != _snapshot(before)[:-1]:
            raise VerificationFileChangedError("file changed before verification")
        filesystem._assert_regular(path, opened)
        yield stream
        filesystem._assert_safe_ancestors(path.parent)
        try:
            after = filesystem._require_regular_file(path)
        except FileNotFoundError as exc:
            raise VerificationFileChangedError("file disappeared during verification") from exc
        if _snapshot(os.fstat(stream.fileno())) != _snapshot(opened) or _snapshot(after) != _snapshot(before):
            raise VerificationFileChangedError("file changed during verification")
