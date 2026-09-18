"""Portable, timeout-bound inter-process file locking."""

from __future__ import annotations

import asyncio
import errno
import importlib
import math
import os
import time
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Protocol, cast

from ..exceptions import InterProcessLockError


class _LockBackend(Protocol):
    def try_acquire(self, stream: BinaryIO) -> bool: ...

    def release(self, stream: BinaryIO) -> None: ...


class _FcntlModule(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, file_descriptor: int, operation: int) -> None: ...


class _WindowsLockBackend:
    def try_acquire(self, stream: BinaryIO) -> bool:
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    def release(self, stream: BinaryIO) -> None:
        import msvcrt

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
        try:
            stream = self.path.open("a+b")
            self._ensure_lock_byte(stream)
            deadline = time.monotonic() + self.timeout_seconds
            while not self._backend.try_acquire(stream):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InterProcessLockError(f"timed out acquiring inter-process file lock: {self.path}")
                time.sleep(min(self.POLL_INTERVAL_SECONDS, remaining))
            self._stream = stream
        except InterProcessLockError:
            if stream is not None:
                stream.close()
            raise
        except OSError as exc:
            if stream is not None:
                stream.close()
            raise InterProcessLockError(f"cannot acquire inter-process file lock: {self.path}") from exc

    async def acquire_async(self) -> None:
        """Poll a nonblocking OS lock without blocking the event loop."""
        if self._stream is not None:
            raise InterProcessLockError("inter-process file lock is already acquired")
        stream: BinaryIO | None = None
        try:
            stream = self.path.open("a+b")
            self._ensure_lock_byte(stream)
            deadline = time.monotonic() + self.timeout_seconds
            while not self._backend.try_acquire(stream):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InterProcessLockError(f"timed out acquiring inter-process file lock: {self.path}")
                await asyncio.sleep(min(self.POLL_INTERVAL_SECONDS, remaining))
            self._stream = stream
        except BaseException as exc:
            if stream is not None:
                stream.close()
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
        except OSError as exc:
            raise InterProcessLockError(f"cannot release inter-process file lock: {self.path}") from exc
        finally:
            stream.close()

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
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
