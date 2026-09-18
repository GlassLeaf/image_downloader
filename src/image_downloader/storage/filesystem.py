from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path, PurePath
from typing import TextIO

from ..exceptions import StorageSafetyError

_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"}


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    stem = value.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES or (stem[:3] in {"COM", "LPT"} and stem[3:].isdigit()):
        value = f"_{value}"
    return value or "download"


def safe_component(value: str, *, max_length: int | None = None) -> str:
    """Return one safe path component without adding a suffix absent a real collision."""
    result = safe_name(str(value))
    if max_length is None or len(result) <= max_length:
        return result
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()[:8]
    return result[: max_length - 9].rstrip(" .") + "_" + digest


class FileSystem:
    """A root-confined filesystem boundary for application-managed files.

    The caller supplies a trusted root. All subsequent operations require a relative
    path and reject symlinks and Windows reparse points beneath that root.
    """

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("FileSystem root must be absolute")
        self.root = root

    def ensure_directory(self, relative: str | Path = Path(".")) -> Path:
        parts = self._parts(relative, allow_empty=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self._assert_directory(self.root)
        current = self.root
        for part in parts:
            current = current / part
            state = self._lstat(current)
            if state is None:
                current.mkdir(exist_ok=True)
            self._assert_directory(current)
        return current

    def path(self, relative: str | Path) -> Path:
        parts = self._parts(relative)
        return self.root.joinpath(*parts)

    def exists(self, relative: str | Path) -> bool:
        if self._lstat(self.root) is None:
            return False
        path = self._prepare_file(relative, create_parent=False)
        state = self._lstat(path)
        if state is None:
            return False
        self._assert_regular(path, state)
        return True

    def collision_key(self, relative: str | Path) -> str:
        """Return a portable, case-insensitive key for an application path."""
        parts = self._parts(relative, allow_empty=True)
        return "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts) or "."

    def child_paths(self, relative: str | Path = Path(".")) -> tuple[Path, ...]:
        """Return direct child spellings without following entries below a safe directory."""
        parts = self._parts(relative, allow_empty=True)
        directory = self.root.joinpath(*parts)
        self._assert_safe_ancestors(directory)
        parent = Path(*parts)
        return tuple(parent / item.name for item in sorted(directory.iterdir(), key=lambda item: item.name))

    def read_text(self, relative: str | Path, *, encoding: str = "utf-8") -> str:
        path = self._prepare_file(relative, create_parent=False)
        before = self._require_regular_file(path)
        value = path.read_text(encoding=encoding)
        self._assert_same(path, before)
        return value

    def write_bytes_atomic(self, relative: str | Path, data: bytes) -> Path:
        path = self._prepare_file(relative, create_parent=True)
        existing = self._lstat(path)
        if existing is not None:
            self._assert_regular(path, existing)
        parent_state = self._lstat(path.parent)
        if parent_state is None:
            raise StorageSafetyError("storage parent disappeared during write")
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._assert_same(path.parent, parent_state)
            self._assert_safe_ancestors(path.parent)
            current = self._lstat(path)
            if current is not None:
                self._assert_regular(path, current)
            os.replace(temporary, path)
            return path
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def open_text_append(self, relative: str | Path, *, encoding: str = "utf-8", newline: str = "") -> TextIO:
        path = self._prepare_file(relative, create_parent=True)
        existing = self._lstat(path)
        if existing is not None:
            self._assert_regular(path, existing)
        parent_state = self._lstat(path.parent)
        if parent_state is None:
            raise StorageSafetyError("storage parent disappeared during append")
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            self._assert_same(path.parent, parent_state)
            opened = os.fstat(descriptor)
            current = self._lstat(path)
            if current is None or not self._same_state(opened, current) or self._is_unsafe(current):
                raise StorageSafetyError("storage file changed during append")
            return os.fdopen(descriptor, "a", encoding=encoding, newline=newline)
        except Exception:
            os.close(descriptor)
            raise

    def _prepare_file(self, relative: str | Path, *, create_parent: bool) -> Path:
        parts = self._parts(relative)
        parent = Path(*parts[:-1]) if len(parts) > 1 else Path(".")
        if create_parent:
            self.ensure_directory(parent)
        else:
            self._assert_safe_ancestors(self.root.joinpath(*parts[:-1]))
        return self.root.joinpath(*parts)

    def _parts(self, relative: str | Path, *, allow_empty: bool = False) -> tuple[str, ...]:
        candidate = PurePath(relative)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            if not (allow_empty and candidate.parts in {(), (".",)}):
                raise StorageSafetyError("storage path must be a non-empty relative path")
        parts = tuple(part for part in candidate.parts if part != ".")
        if not parts and not allow_empty:
            raise StorageSafetyError("storage path must be a non-empty relative path")
        for part in parts:
            if safe_name(part) != part:
                raise StorageSafetyError("storage path contains an unsafe component")
        return parts

    def _assert_safe_ancestors(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise StorageSafetyError("storage path escapes root") from exc
        self._assert_directory(self.root)
        current = self.root
        for part in relative.parts:
            current = current / part
            self._assert_directory(current)

    def _require_regular_file(self, path: Path) -> os.stat_result:
        state = self._lstat(path)
        if state is None:
            raise FileNotFoundError(path)
        self._assert_regular(path, state)
        return state

    def _assert_directory(self, path: Path) -> None:
        state = self._lstat(path)
        if state is None or self._is_unsafe(state) or not stat.S_ISDIR(state.st_mode):
            raise StorageSafetyError(f"storage directory is unsafe: {path}")

    def _assert_regular(self, path: Path, state: os.stat_result) -> None:
        if self._is_unsafe(state) or not stat.S_ISREG(state.st_mode) or getattr(state, "st_nlink", 1) != 1:
            raise StorageSafetyError(f"storage file is unsafe: {path}")

    @staticmethod
    def _lstat(path: Path) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None

    @staticmethod
    def _is_unsafe(state: os.stat_result) -> bool:
        return stat.S_ISLNK(state.st_mode) or bool(
            getattr(state, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    def _assert_same(self, path: Path, expected: os.stat_result) -> None:
        current = self._lstat(path)
        if current is None or not self._same_state(current, expected) or self._is_unsafe(current):
            raise StorageSafetyError("storage path changed during operation")

    @staticmethod
    def _same_state(left: os.stat_result, right: os.stat_result) -> bool:
        return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
