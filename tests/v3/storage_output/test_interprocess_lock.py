from __future__ import annotations

import asyncio
import errno
import io
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


def test_windows_backend_locks_one_byte_and_handles_contention(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.positions: list[int] = []

        def seek(self, position: int) -> None:
            self.positions.append(position)

        def fileno(self) -> int:
            return 17

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int]] = []
            self.error: int | None = None

        def locking(self, descriptor: int, operation: int, byte_count: int) -> None:
            self.calls.append((descriptor, operation, byte_count))
            if self.error is not None:
                raise OSError(self.error, "lock failed")

    fake = FakeMsvcrt()
    imported: list[str] = []

    def import_module(name: str) -> FakeMsvcrt:
        imported.append(name)
        return fake

    monkeypatch.setattr(lock_module.importlib, "import_module", import_module)
    backend = lock_module._WindowsLockBackend()
    stream = FakeStream()
    assert backend.try_acquire(stream) is True  # type: ignore[arg-type]
    backend.release(stream)  # type: ignore[arg-type]
    assert fake.calls == [(17, 1, 1), (17, 2, 1)]
    assert stream.positions == [0, 0]
    assert imported == ["msvcrt", "msvcrt"]
    for error in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
        fake.error = error
        assert backend.try_acquire(stream) is False  # type: ignore[arg-type]
    fake.error = errno.EIO
    with pytest.raises(OSError) as caught:
        backend.try_acquire(stream)  # type: ignore[arg-type]
    assert caught.value.errno == errno.EIO


def test_interprocess_lock_contract_uses_fixed_polling_and_default_timeout(tmp_path: Path) -> None:
    lock = InterProcessFileLock(tmp_path / "contract.lock")

    assert lock.timeout_seconds == 30.0
    assert lock.POLL_INTERVAL_SECONDS == 0.1
    with pytest.raises(ValueError, match="finite and nonnegative"):
        InterProcessFileLock(tmp_path / "invalid.lock", timeout_seconds=-0.1)


@pytest.mark.parametrize("initial", [None, b"", b"\0", b"existing bytes"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_initialization_keeps_existing_nonempty_file(tmp_path: Path, initial, asynchronous: bool) -> None:
    path = tmp_path / "initialization.lock"
    if initial is not None:
        path.write_bytes(initial)
    before = path.stat() if initial is not None else None
    lock = InterProcessFileLock(path, timeout_seconds=0)
    if asynchronous:
        asyncio.run(lock.acquire_async())
    else:
        lock.acquire()
    assert isinstance(lock._stream, io.FileIO)
    lock.release()
    after = path.stat()
    assert path.read_bytes() == (initial or b"\0")
    if before is not None:
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        if initial:
            assert after.st_mtime_ns == before.st_mtime_ns


def _hold_empty_lock(path: str, acquired, release) -> None:
    lock = InterProcessFileLock(Path(path))
    with Path(path).open("a+b", buffering=0) as stream:
        assert lock._backend.try_acquire(stream)
        try:
            acquired.set()
            assert release.wait(10)
        finally:
            lock._backend.release(stream)


def test_empty_file_is_locked_before_initialization(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired, release = context.Event(), context.Event()
    path = tmp_path / "empty.lock"
    path.touch()
    process = context.Process(target=_hold_empty_lock, args=(str(path), acquired, release))
    process.start()
    try:
        assert acquired.wait(10)
        with pytest.raises(InterProcessLockError, match="timed out"):
            InterProcessFileLock(path, timeout_seconds=0).acquire()
        assert path.stat().st_size == 0
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    with InterProcessFileLock(path, timeout_seconds=0):
        pass
    assert path.read_bytes() == b"\0"


def _increment_under_lock(path: str, start, counter) -> None:
    assert start.wait(10)
    for _ in range(12):
        with InterProcessFileLock(Path(path), timeout_seconds=5):
            value = counter.value
            time.sleep(0.001)
            counter.value = value + 1


@pytest.mark.parametrize("existing_empty", [False, True])
def test_simultaneous_initialization_serializes_updates(tmp_path: Path, existing_empty: bool) -> None:
    path = tmp_path / "shared-start.lock"
    if existing_empty:
        path.touch()
    context = multiprocessing.get_context("spawn")
    start, counter = context.Event(), context.Value("i", 0, lock=False)
    processes = [context.Process(target=_increment_under_lock, args=(str(path), start, counter)) for _ in range(3)]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(15)
        assert [process.exitcode for process in processes] == [0, 0, 0]
        assert counter.value == 36
        assert path.read_bytes() == b"\0"
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["write", "flush"])
@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_initialization_preserves_error_and_cleans_up(
    tmp_path: Path, monkeypatch, capsys, asynchronous: bool, failure: str, error_type, cleanup_fails: bool
) -> None:
    primary = error_type("secret primary error")
    primary.__cause__ = ValueError("secret original cause")
    calls = []
    state = {"owned": False}

    class Stream(io.BytesIO):
        def write(self, value):
            assert state["owned"], "initialization must own the OS lock"
            calls.append("write")
            if failure == "write":
                raise primary
            return super().write(value)

        def flush(self):
            assert state["owned"]
            calls.append("flush")
            if failure == "flush":
                raise primary

        def close(self):
            calls.append("close")
            super().close()
            if cleanup_fails:
                raise OSError("secret close error")

    class Backend:
        def try_acquire(self, stream):
            calls.append("acquire")
            state["owned"] = True
            return True

        def release(self, stream):
            calls.append("release")
            state["owned"] = False
            if cleanup_fails:
                raise OSError("secret release error")

    stream = Stream()
    lock = InterProcessFileLock(tmp_path / "failure.lock")
    lock._backend = Backend()
    monkeypatch.setattr(InterProcessFileLock, "_open_stream", lambda self: stream)
    expected = InterProcessLockError if isinstance(primary, OSError) else error_type
    with pytest.raises(expected) as caught:
        if asynchronous:
            asyncio.run(lock.acquire_async())
        else:
            lock.acquire()
    assert (caught.value.__cause__ if isinstance(primary, OSError) else caught.value) is primary
    assert isinstance(primary.__cause__, ValueError)
    assert calls == (
        ["acquire", "write", "release", "close"]
        if failure == "write"
        else ["acquire", "write", "flush", "release", "close"]
    )
    assert stream.closed
    assert lock._stream is None
    assert not state["owned"]
    diagnostic = capsys.readouterr().err
    assert "secret" not in diagnostic
    assert diagnostic == (
        "warning: inter-process lock cleanup failed (unlock)\nwarning: inter-process lock cleanup failed (stream)\n"
        if cleanup_fails
        else ""
    )


@pytest.mark.parametrize("count", [0, None])
def test_short_initialization_write_is_an_acquisition_error(tmp_path: Path, monkeypatch, count) -> None:
    class Stream(io.BytesIO):
        def write(self, value):
            return count

    class Backend:
        def try_acquire(self, stream):
            return True

        def release(self, stream):
            pass

    stream = Stream()
    lock = InterProcessFileLock(tmp_path / "short.lock")
    lock._backend = Backend()
    monkeypatch.setattr(InterProcessFileLock, "_open_stream", lambda self: stream)
    with pytest.raises(InterProcessLockError) as caught:
        lock.acquire()
    assert caught.value.__cause__.errno == errno.EIO
    assert stream.closed


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_lock_is_reusable_after_initialization_failure(tmp_path: Path, monkeypatch, asynchronous: bool) -> None:
    path = tmp_path / "retry.lock"
    lock = InterProcessFileLock(path, timeout_seconds=0)
    primary = OSError(errno.EIO, "injected initialization failure")

    def fail(stream):
        raise primary

    with monkeypatch.context() as patch:
        patch.setattr(InterProcessFileLock, "_ensure_lock_byte", staticmethod(fail))
        with pytest.raises(InterProcessLockError) as caught:
            if asynchronous:
                asyncio.run(lock.acquire_async())
            else:
                lock.acquire()
        assert caught.value.__cause__ is primary
    with lock:
        pass
    assert path.read_bytes() == b"\0"


def test_release_failure_does_not_get_replaced_by_close_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    primary = OSError(errno.EIO, "secret release error")

    class Stream(io.BytesIO):
        def close(self):
            super().close()
            raise RuntimeError("secret close error")

    class Backend:
        def release(self, stream):
            raise primary

    stream = Stream(b"\0")
    lock = InterProcessFileLock(tmp_path / "release.lock")
    lock._stream, lock._backend = stream, Backend()
    with pytest.raises(InterProcessLockError) as caught:
        lock.release()
    assert caught.value.__cause__ is primary
    assert lock._stream is None
    assert stream.closed
    assert capsys.readouterr().err == "warning: inter-process lock cleanup failed (stream)\n"


def test_synchronous_wait_interruption_closes_unowned_stream(tmp_path: Path, monkeypatch) -> None:
    stream = io.BytesIO()

    class Backend:
        def try_acquire(self, stream):
            return False

        def release(self, stream):
            pytest.fail("an unowned lock must not be explicitly unlocked")

    def interrupt(delay):
        raise KeyboardInterrupt()

    lock = InterProcessFileLock(tmp_path / "wait.lock")
    lock._backend = Backend()
    monkeypatch.setattr(InterProcessFileLock, "_open_stream", lambda self: stream)
    monkeypatch.setattr(lock_module.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        lock.acquire()
    assert stream.closed


def test_asynchronous_wait_cancellation_closes_unowned_stream(tmp_path: Path, monkeypatch) -> None:
    stream = io.BytesIO()

    class Backend:
        def try_acquire(self, stream):
            return False

        def release(self, stream):
            pytest.fail("an unowned lock must not be explicitly unlocked")

    async def scenario():
        lock = InterProcessFileLock(tmp_path / "wait.lock")
        lock._backend = Backend()
        monkeypatch.setattr(InterProcessFileLock, "_open_stream", lambda self: stream)
        task = asyncio.create_task(lock.acquire_async())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed

    asyncio.run(scenario())
