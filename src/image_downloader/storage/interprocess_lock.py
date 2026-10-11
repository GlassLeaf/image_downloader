"""Portable, timeout-bound inter-process file locking."""

from __future__ import annotations

import asyncio
import builtins
import ctypes
import errno
import importlib
import math
import os
import stat
import sys
import tempfile
import time
from functools import cache
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Protocol, cast

from ..exceptions import InterProcessLockError


def _warn_cleanup_failure(resource: str) -> None:
    try:
        sys.stderr.write(f"warning: inter-process lock cleanup failed ({resource})\n")
    except BaseException:
        pass


def _is_link_or_reparse(state: os.stat_result) -> bool:
    return stat.S_ISLNK(state.st_mode) or bool(
        getattr(state, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _directory_state(path: Path) -> os.stat_result:
    state = path.lstat()
    if _is_link_or_reparse(state) or not stat.S_ISDIR(state.st_mode):
        raise OSError(errno.EINVAL, "unsafe inter-process lock directory")
    return state


def _check_directory(path: Path, expected: os.stat_result) -> None:
    if not _same_file(_directory_state(path), expected):
        raise OSError(errno.EIO, "inter-process lock directory changed")


def _check_regular(state: os.stat_result) -> None:
    if _is_link_or_reparse(state) or not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
        raise OSError(errno.EINVAL, "unsafe inter-process lock file")


def _file_state(path: Path) -> os.stat_result | None:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return None
    _check_regular(state)
    return state


def _write_lock_byte(stream: BinaryIO) -> None:
    if stream.write(b"\0") != 1:
        raise OSError(errno.EIO, "incomplete lock initialization write")
    stream.flush()


@cache
def _windows_crt() -> ctypes.CDLL | None:
    if os.name != "nt":
        return None
    try:
        library = ctypes.CDLL("ucrtbase.dll", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        library._get_doserrno.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        library._get_doserrno.restype = ctypes.c_int
        library._set_doserrno.argtypes = [ctypes.c_ulong]
        library._set_doserrno.restype = ctypes.c_int
    except (OSError, AttributeError):
        return None
    return library


def _windows_open_error(library: ctypes.CDLL) -> int | None:
    code = ctypes.c_ulong()
    if library._get_doserrno(ctypes.byref(code)) != 0:
        return None
    return code.value


def _open_existing_append(path: str, flags: int) -> int:
    # Creation belongs to the completed-file publisher, never to this open.
    flags &= ~os.O_CREAT
    library = _windows_crt()
    if library is not None:
        library._set_doserrno(0)
    try:
        return os.open(path, flags)
    except OSError as error:
        if error.errno != errno.EACCES or library is None or _windows_open_error(library) != 32:
            raise
        primary = error
    # Publication-time sharing conflicts may be transient. Retry only a returned
    # sharing violation, once, without sleeps or creation.
    try:
        return os.open(path, flags)
    except OSError:
        pass
    raise primary


def _remove_owned_temporary(path: Path, parent_state: os.stat_result, owned: os.stat_result | None) -> None:
    try:
        _check_directory(path.parent, parent_state)
        current = _file_state(path)
        if current is None:
            return
        if owned is None or not _same_file(current, owned):
            raise OSError(errno.EIO, "temporary lock file changed")
        path.unlink()
    except BaseException:
        _warn_cleanup_failure("temporary")


def _publish_windows_lock_file(path: Path, parent_state: os.stat_result) -> None:
    descriptor: int | None = None
    stream: BinaryIO | None = None
    temporary: Path | None = None
    owned: os.stat_result | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".id-lock-", dir=path.parent)
        temporary = Path(name)
        owned = os.fstat(descriptor)
        stream = os.fdopen(descriptor, "wb", buffering=0)
        descriptor = None
        _write_lock_byte(stream)
        stream.close()
        stream = None
        _check_directory(path.parent, parent_state)
        staged = _file_state(temporary)
        if staged is None or not _same_file(staged, owned):
            raise OSError(errno.EIO, "temporary lock file changed before publication")
        if _file_state(path) is not None:
            return
        try:
            # Windows rename refuses an existing destination. Do not use replace
            # or call this publisher on POSIX, whose rename may overwrite it.
            os.rename(temporary, path)
        except FileExistsError:
            if _file_state(path) is None:
                raise FileNotFoundError(errno.ENOENT, "published lock file disappeared") from None
        else:
            temporary = None
        _check_directory(path.parent, parent_state)
    finally:
        if stream is not None:
            try:
                stream.close()
            except BaseException:
                _warn_cleanup_failure("stream")
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                _warn_cleanup_failure("descriptor")
        if temporary is not None:
            _remove_owned_temporary(temporary, parent_state, owned)


class _LockBackend(Protocol):
    def try_acquire(self, stream: BinaryIO) -> bool: ...

    def release(self, stream: BinaryIO) -> None: ...


class _FcntlModule(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, file_descriptor: int, operation: int) -> None: ...


class _MsvcrtModule(Protocol):
    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, file_descriptor: int, operation: int, byte_count: int) -> None: ...


class _WindowsLockBackend:
    def try_acquire(self, stream: BinaryIO) -> bool:
        msvcrt = cast(_MsvcrtModule, importlib.import_module("msvcrt"))

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    def release(self, stream: BinaryIO) -> None:
        msvcrt = cast(_MsvcrtModule, importlib.import_module("msvcrt"))

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class _PosixLockBackend:
    def try_acquire(self, stream: BinaryIO) -> bool:
        fcntl = cast(_FcntlModule, importlib.import_module("fcntl"))

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        return True

    def release(self, stream: BinaryIO) -> None:
        fcntl = cast(_FcntlModule, importlib.import_module("fcntl"))

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class InterProcessFileLock:
    """Exclusive file lock with identical timeout semantics on Windows and POSIX."""

    DEFAULT_TIMEOUT_SECONDS = 30.0
    POLL_INTERVAL_SECONDS = 0.1

    def __init__(self, path: Path, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("lock timeout must be finite and nonnegative")
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._backend: _LockBackend = _WindowsLockBackend() if os.name == "nt" else _PosixLockBackend()
        self._stream: BinaryIO | None = None

    def acquire(self) -> None:
        if self._stream is not None:
            raise InterProcessLockError("inter-process file lock is already acquired")
        stream: BinaryIO | None = None
        acquired = False
        try:
            stream = self._open_stream()
            deadline = time.monotonic() + self.timeout_seconds
            while not self._backend.try_acquire(stream):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InterProcessLockError(f"timed out acquiring inter-process file lock: {self.path}")
                time.sleep(min(self.POLL_INTERVAL_SECONDS, remaining))
            acquired = True
            self._ensure_lock_byte(stream)
            self._stream = stream
        except BaseException as exc:
            if stream is not None:
                self._abort_acquisition(stream, acquired=acquired)
            if isinstance(exc, OSError):
                raise InterProcessLockError(f"cannot acquire inter-process file lock: {self.path}") from exc
            raise

    async def acquire_async(self) -> None:
        """Poll a nonblocking OS lock without blocking the event loop."""
        if self._stream is not None:
            raise InterProcessLockError("inter-process file lock is already acquired")
        stream: BinaryIO | None = None
        acquired = False
        try:
            stream = self._open_stream()
            deadline = time.monotonic() + self.timeout_seconds
            while not self._backend.try_acquire(stream):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InterProcessLockError(f"timed out acquiring inter-process file lock: {self.path}")
                await asyncio.sleep(min(self.POLL_INTERVAL_SECONDS, remaining))
            acquired = True
            self._ensure_lock_byte(stream)
            self._stream = stream
        except BaseException as exc:
            if stream is not None:
                self._abort_acquisition(stream, acquired=acquired)
            if isinstance(exc, OSError):
                raise InterProcessLockError(f"cannot acquire inter-process file lock: {self.path}") from exc
            raise

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            self._backend.release(stream)
        except BaseException as exc:
            try:
                stream.close()
            except BaseException:
                _warn_cleanup_failure("stream")
            if isinstance(exc, OSError):
                raise InterProcessLockError(f"cannot release inter-process file lock: {self.path}") from exc
            raise
        else:
            stream.close()

    def _open_stream(self) -> BinaryIO:
        if os.name == "nt":
            return self._open_windows_stream()
        return self.path.open("a+b", buffering=0)

    def _open_windows_stream(self) -> BinaryIO:
        path = self.path.absolute()
        parent_state = _directory_state(path.parent)
        expected = _file_state(path)
        if expected is None:
            _publish_windows_lock_file(path, parent_state)
            expected = _file_state(path)
        if expected is None:
            raise FileNotFoundError(errno.ENOENT, "published lock file disappeared")
        _check_directory(path.parent, parent_state)
        stream = builtins.open(path, "a+b", buffering=0, opener=_open_existing_append)
        try:
            opened = os.fstat(stream.fileno())
            _check_regular(opened)
            current = _file_state(path)
            _check_directory(path.parent, parent_state)
            if current is None or not _same_file(current, opened) or not _same_file(expected, opened):
                raise OSError(errno.EIO, "inter-process lock file changed during open")
        except BaseException:
            try:
                stream.close()
            except BaseException:
                _warn_cleanup_failure("stream")
            raise
        return stream

    def _abort_acquisition(self, stream: BinaryIO, *, acquired: bool) -> None:
        if acquired:
            try:
                self._backend.release(stream)
            except BaseException:
                _warn_cleanup_failure("unlock")
        try:
            stream.close()
        except BaseException:
            _warn_cleanup_failure("stream")

    def __enter__(self) -> InterProcessFileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    @staticmethod
    def _ensure_lock_byte(stream: BinaryIO) -> None:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            _write_lock_byte(stream)
        stream.seek(0)
