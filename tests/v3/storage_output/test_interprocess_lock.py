from __future__ import annotations

import errno
import multiprocessing
import time
from pathlib import Path

import pytest

import image_downloader.storage.interprocess_lock as lock_module
from image_downloader import InterProcessLockError
from image_downloader.storage.interprocess_lock import InterProcessFileLock


def _hold_lock(path: str, acquired, release) -> None:
    with InterProcessFileLock(Path(path), timeout_seconds=5):
        acquired.set()
        if not release.wait(10):
            raise RuntimeError("lock test release signal timed out")


def _raise_inside_lock(path: str) -> None:
    try:
        with InterProcessFileLock(Path(path)):
            raise RuntimeError("worker failed")
    except RuntimeError:
        pass


def test_interprocess_lock_excludes_another_process_and_times_out(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    lock_path = tmp_path / "shared.lock"
    process = context.Process(target=_hold_lock, args=(str(lock_path), acquired, release))
    process.start()
    try:
        assert acquired.wait(10), "child process did not acquire the lock"
        started = time.monotonic()
        with pytest.raises(InterProcessLockError, match="timed out"):
            with InterProcessFileLock(lock_path, timeout_seconds=0.25):
                raise AssertionError("contended lock must not be acquired")
        assert time.monotonic() - started >= 0.2
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0

    with InterProcessFileLock(lock_path, timeout_seconds=1):
        pass


def test_interprocess_lock_is_released_when_the_protected_block_fails(tmp_path: Path) -> None:
    lock_path = tmp_path / "exception.lock"

    with pytest.raises(RuntimeError, match="protected operation failed"):
        with InterProcessFileLock(lock_path):
            raise RuntimeError("protected operation failed")

    with InterProcessFileLock(lock_path, timeout_seconds=0):
        pass


def test_interprocess_lock_is_released_after_a_child_process_exception(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    lock_path = tmp_path / "child-exception.lock"
    process = context.Process(target=_raise_inside_lock, args=(str(lock_path),))

    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
    assert process.exitcode == 0
    with InterProcessFileLock(lock_path, timeout_seconds=1):
        pass


def test_posix_backend_uses_nonblocking_flock_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        def fileno(self) -> int:
            return 17

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []
            self.busy = False

        def flock(self, descriptor: int, operation: int) -> None:
            self.calls.append((descriptor, operation))
            if self.busy:
                raise BlockingIOError(errno.EAGAIN, "busy")

    fake = FakeFcntl()
    monkeypatch.setattr(lock_module.importlib, "import_module", lambda name: fake)
    backend = lock_module._PosixLockBackend()
    stream = FakeStream()

    assert backend.try_acquire(stream) is True  # type: ignore[arg-type]
    backend.release(stream)  # type: ignore[arg-type]
    assert fake.calls == [(17, 3), (17, 4)]
    fake.busy = True
    assert backend.try_acquire(stream) is False  # type: ignore[arg-type]


def test_interprocess_lock_contract_uses_fixed_polling_and_default_timeout(tmp_path: Path) -> None:
    lock = InterProcessFileLock(tmp_path / "contract.lock")

    assert lock.timeout_seconds == 30.0
    assert lock.POLL_INTERVAL_SECONDS == 0.1
    with pytest.raises(ValueError, match="finite and nonnegative"):
        InterProcessFileLock(tmp_path / "invalid.lock", timeout_seconds=-0.1)
