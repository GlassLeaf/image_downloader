"""Native component budgets and narrowly scoped path-error diagnostics."""

from __future__ import annotations

import errno
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..exceptions import StorageError


@dataclass(frozen=True)
class _ComponentLimit:
    maximum: int
    windows: bool

    def measure(self, value: str) -> int:
        return len(value.encode("utf-16-le", errors="surrogatepass")) // 2 if self.windows else len(os.fsencode(value))

    def shorten(self, value: str, *, tail: str = "") -> str:
        if self.measure(value) <= self.maximum:
            return value
        marker = "_" + hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()[:8]
        available = self.maximum - self.measure(marker + tail)
        if available < 1 or (tail and not value.endswith(tail)):
            error = StorageError("output component cannot retain its required suffix")
            error.__dict__["_path_length_diagnostic"] = "component"
            raise error
        prefix = value[: -len(tail)] if tail else value
        used = 0
        end = 0
        for character in prefix:
            size = self.measure(character)
            if used + size > available:
                break
            used += size
            end += 1
        return prefix[:end].rstrip(" .") + marker + tail


def _nearest_parent(path: Path) -> Path:
    current = path
    while True:
        try:
            if current.is_dir():
                return current
        except (OSError, ValueError):
            pass
        if current.parent == current:
            return current
        current = current.parent


def _windows_component_limit(parent: Path) -> int:
    import ctypes
    from ctypes import wintypes

    loader = getattr(ctypes, "WinDLL", None)
    last_error = getattr(ctypes, "get_last_error", None)
    if loader is None or last_error is None:
        raise AttributeError("Windows volume APIs are unavailable")
    kernel = loader("kernel32", use_last_error=True)
    volume_path = ctypes.create_unicode_buffer(32768)
    get_path = kernel.GetVolumePathNameW
    get_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_path.restype = wintypes.BOOL
    if not get_path(str(parent), volume_path, len(volume_path)):
        raise OSError(int(last_error()), "cannot query output volume")
    maximum = wintypes.DWORD()
    get_info = kernel.GetVolumeInformationW
    get_info.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_info.restype = wintypes.BOOL
    if not get_info(volume_path.value, None, 0, None, ctypes.byref(maximum), None, None, 0):
        raise OSError(int(last_error()), "cannot query output component limit")
    return maximum.value


def _component_limit(parent: Path) -> _ComponentLimit:
    windows = os.name == "nt"
    try:
        nearest = _nearest_parent(parent)
        if windows:
            maximum = _windows_component_limit(nearest)
        else:
            pathconf = getattr(os, "pathconf", None)
            if pathconf is None:
                raise AttributeError("pathconf is unavailable")
            maximum = int(pathconf(nearest, "PC_NAME_MAX"))
    except (OSError, ValueError, AttributeError):
        maximum = 255
    return _ComponentLimit(maximum if maximum > 0 else 255, windows)


def _path_diagnostic(path: Path, error: OSError | ValueError, *, creating: bool) -> StorageError | None:
    windows = os.name == "nt"
    kind: str | None = None
    if isinstance(error, OSError) and (error.errno == errno.ENAMETOOLONG or getattr(error, "winerror", None) == 206):
        budget = _component_limit(path.parent)
        kind = "component" if budget.measure(path.name) > budget.maximum else "path"
    elif (
        windows
        and isinstance(error, ValueError)
        and any(
            text in str(error)
            for text in ("path too long for Windows", "src too long for Windows", "dst too long for Windows")
        )
    ):
        kind = "path"
    elif isinstance(error, OSError) and (error.errno == errno.EINVAL or getattr(error, "winerror", None) == 123):
        budget = _component_limit(path.parent)
        if budget.measure(path.name) > budget.maximum:
            kind = "component"
    elif windows and creating and isinstance(error, FileNotFoundError):
        # Missing files are normal in exists()/allocation. Only a failed creation
        # beneath a still-existing parent can suggest this Windows API ambiguity.
        if _ComponentLimit(255, True).measure(str(path)) > 260:
            try:
                if path.parent.is_dir():
                    kind = "possible"
            except (OSError, ValueError):
                pass
    if kind is None:
        return None
    result = StorageError("storage path length failure")
    result.__dict__["_path_length_diagnostic"] = kind
    return result


@contextmanager
def _path_errors(path: Path, *, creating: bool = False) -> Iterator[None]:
    try:
        yield
    except (OSError, ValueError) as error:
        diagnosed = _path_diagnostic(path, error, creating=creating)
        if diagnosed is not None:
            raise diagnosed from error
        raise
