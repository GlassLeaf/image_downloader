from __future__ import annotations

import asyncio
import ctypes
import errno
import io
import multiprocessing
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

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


def test_existing_append_opener_removes_only_creation_flag(tmp_path: Path, monkeypatch) -> None:
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    calls = []
    monkeypatch.setattr(lock_module.os, "open", lambda path, mode: calls.append((path, mode)) or 17)
    assert lock_module._open_existing_append(str(tmp_path / "flags.lock"), flags) == 17
    assert calls == [(str(tmp_path / "flags.lock"), flags & ~os.O_CREAT)]


def test_existing_append_opener_does_not_recreate_disappeared_target(tmp_path: Path) -> None:
    path = tmp_path / "missing.lock"
    with pytest.raises(FileNotFoundError):
        lock_module.builtins.open(path, "a+b", buffering=0, opener=lock_module._open_existing_append)
    assert not path.exists()


@pytest.mark.parametrize("code", [5, 33, 0, None])
def test_existing_append_opener_does_not_retry_other_errors(tmp_path: Path, monkeypatch, code) -> None:
    primary = PermissionError(errno.EACCES, "secret denied")
    attempts = []
    reader = SimpleNamespace(_set_doserrno=lambda value: None)

    def fail(path, flags):
        attempts.append(flags)
        raise primary

    monkeypatch.setattr(lock_module, "_windows_crt", lambda: reader)
    monkeypatch.setattr(lock_module, "_windows_open_error", lambda library: code)
    monkeypatch.setattr(lock_module.os, "open", fail)
    with pytest.raises(PermissionError) as caught:
        lock_module._open_existing_append(str(tmp_path / "denied.lock"), os.O_RDWR | os.O_APPEND | os.O_CREAT)
    assert caught.value is primary
    assert attempts == [os.O_RDWR | os.O_APPEND]


def test_existing_append_opener_marks_only_confirmed_sharing(tmp_path: Path, monkeypatch) -> None:
    cause = RuntimeError("original cause")
    primary = PermissionError(errno.EACCES, "secret sharing error")
    primary.__cause__ = cause
    attempts = []
    reader = SimpleNamespace(_set_doserrno=lambda value: None)

    def open_once_more(path, flags):
        attempts.append(flags)
        raise primary

    monkeypatch.setattr(lock_module, "_windows_crt", lambda: reader)
    monkeypatch.setattr(lock_module, "_windows_open_error", lambda library: 32)
    monkeypatch.setattr(lock_module.os, "open", open_once_more)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
    with pytest.raises(lock_module._LockOpenError) as caught:
        lock_module._open_existing_append(str(tmp_path / "shared.lock"), flags)
    assert caught.value.cause is primary
    assert caught.value.retry is None
    assert primary.__cause__ is cause
    assert primary.__context__ is None
    assert attempts == [flags & ~os.O_CREAT]


def test_existing_append_opener_without_crt_keeps_original_failure(tmp_path: Path, monkeypatch) -> None:
    primary = PermissionError(errno.EACCES, "secret without extended error")
    attempts = []

    def fail(path, flags):
        attempts.append(flags)
        raise primary

    monkeypatch.setattr(lock_module, "_windows_crt", lambda: None)
    monkeypatch.setattr(lock_module.os, "open", fail)
    with pytest.raises(PermissionError) as caught:
        lock_module._open_existing_append(str(tmp_path / "unavailable.lock"), os.O_RDWR)
    assert caught.value is primary
    assert attempts == [os.O_RDWR]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("retry_succeeds", [False, True])
def test_acquisition_open_waits_once_and_preserves_primary(tmp_path, monkeypatch, asynchronous, retry_succeeds) -> None:
    lock = InterProcessFileLock(tmp_path / "sharing.lock")
    primary = PermissionError(errno.EACCES, "secret primary")
    cause = RuntimeError("original cause")
    primary.__cause__ = cause
    attempts, waits = [], []
    stream = io.BytesIO()

    def retry():
        attempts.append("retry")
        if retry_succeeds:
            return stream
        raise OSError(errno.EIO, "secret secondary")

    def first():
        attempts.append("first")
        raise lock_module._LockOpenError(primary, retry)

    async def wait(delay):
        waits.append(delay)

    monkeypatch.setattr(lock, "_open_stream", first)
    monkeypatch.setattr(lock_module.time, "sleep", waits.append)
    monkeypatch.setattr(lock_module.asyncio, "sleep", wait)
    try:
        if retry_succeeds:
            value = asyncio.run(lock._open_for_acquisition_async()) if asynchronous else lock._open_for_acquisition()
            assert value is stream
        else:
            with pytest.raises(PermissionError) as caught:
                if asynchronous:
                    asyncio.run(lock._open_for_acquisition_async())
                else:
                    lock._open_for_acquisition()
            assert caught.value is primary
            assert primary.__cause__ is cause
            assert primary.__context__ is None
        assert attempts == ["first", "retry"]
        assert waits == [0.1]
    finally:
        stream.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_sharing_wait_can_be_interrupted_before_retry(tmp_path, monkeypatch, asynchronous) -> None:
    lock = InterProcessFileLock(tmp_path / "interrupted-sharing.lock")
    primary = PermissionError(errno.EACCES, "sharing")
    retries = []

    def first():
        raise lock_module._LockOpenError(primary, lambda: retries.append(True))

    monkeypatch.setattr(lock, "_open_stream", first)
    if asynchronous:

        async def scenario():
            task = asyncio.create_task(lock.acquire_async())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
    else:
        interrupt = KeyboardInterrupt()

        def stop(delay):
            raise interrupt

        monkeypatch.setattr(lock_module.time, "sleep", stop)
        with pytest.raises(KeyboardInterrupt) as caught:
            lock.acquire()
        assert caught.value is interrupt
    assert retries == []
    assert lock._stream is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", ["target", "parent", "missing"])
def test_sharing_retry_keeps_original_path_identities(tmp_path, monkeypatch, asynchronous, change) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    path = parent / "identity.lock"
    path.write_bytes(b"\0")
    primary = PermissionError(errno.EACCES, "sharing")
    opens = []
    original_open = lock_module.builtins.open

    def first(value, *args, **kwargs):
        if Path(value) == path:
            opens.append(value)
            raise lock_module._LockOpenError(primary)
        return original_open(value, *args, **kwargs)

    def replace(delay):
        assert delay == 0.1
        if change == "parent":
            parent.rename(tmp_path / "old-parent")
            parent.mkdir()
        else:
            path.rename(parent / "old.lock")
        if change != "missing":
            path.write_bytes(b"replacement")

    async def async_replace(delay):
        replace(delay)

    lock = InterProcessFileLock(path)
    monkeypatch.setattr(lock, "_open_stream", lock._open_windows_stream)
    monkeypatch.setattr(lock_module.builtins, "open", first)
    monkeypatch.setattr(lock_module.time, "sleep", replace)
    monkeypatch.setattr(lock_module.asyncio, "sleep", async_replace)
    with pytest.raises(InterProcessLockError) as caught:
        if asynchronous:
            asyncio.run(lock.acquire_async())
        else:
            lock.acquire()
    assert caught.value.__cause__ is primary
    assert len(opens) == 1
    assert lock._stream is None
    if change == "missing":
        assert not path.exists()
    else:
        assert path.read_bytes() == b"replacement"


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT and handle sharing")
@pytest.mark.parametrize("denial", ["sharing", "readonly"])
def test_windows_extended_error_distinguishes_sharing_from_access_denial(tmp_path: Path, monkeypatch, denial) -> None:
    path = tmp_path / "native-denial.lock"
    path.write_bytes(b"\0")
    original_open, original_reader = lock_module.os.open, lock_module._windows_open_error
    attempts, codes = [], []

    def observe_open(value, flags):
        attempts.append(flags)
        return original_open(value, flags)

    def observe_error(library):
        code = original_reader(library)
        codes.append(code)
        return code

    monkeypatch.setattr(lock_module.os, "open", observe_open)
    monkeypatch.setattr(lock_module, "_windows_open_error", observe_error)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = None
    try:
        if denial == "sharing":
            handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
            assert handle != ctypes.c_void_p(-1).value
        else:
            path.chmod(stat.S_IREAD)
        with pytest.raises(InterProcessLockError) as caught:
            InterProcessFileLock(path).acquire()
        assert isinstance(caught.value.__cause__, PermissionError)
        assert caught.value.__cause__.errno == errno.EACCES
        assert codes == ([32, 32] if denial == "sharing" else [5])
        assert len(attempts) == (2 if denial == "sharing" else 1)
        assert all(not flags & os.O_CREAT for flags in attempts)
    finally:
        if handle is not None:
            assert kernel.CloseHandle(handle)
        else:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
    with InterProcessFileLock(path):
        pass
    assert path.read_bytes() == b"\0"


@pytest.mark.skipif(os.name != "nt", reason="Windows rename refuses an existing destination")
def test_windows_publishes_completed_byte_and_leaves_no_temporary(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "publication.lock"
    rename = lock_module.os.rename
    observed = []

    def observe_publication(source, destination):
        assert not Path(destination).exists()
        assert Path(source).read_bytes() == b"\0"
        # A closed staging handle is required for Windows rename.
        observed.append(Path(source).stat().st_ino)
        rename(source, destination)

    monkeypatch.setattr(lock_module.os, "rename", observe_publication)
    with InterProcessFileLock(path):
        pass
    assert observed == [path.stat().st_ino]
    assert path.read_bytes() == b"\0"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["publication.lock"]


@pytest.mark.skipif(os.name != "nt", reason="Windows rename refuses an existing destination")
def test_windows_publication_loser_preserves_held_winner(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "winner.lock"
    path.write_bytes(b"winner")
    before = path.stat()

    def unexpected_rename(*args):
        raise AssertionError("an already visible winner must not be renamed over")

    monkeypatch.setattr(lock_module.os, "rename", unexpected_rename)
    with InterProcessFileLock(path):
        lock_module._publish_windows_lock_file(path, lock_module._directory_state(tmp_path))
    after = path.stat()
    assert path.read_bytes() == b"winner"
    assert (before.st_dev, before.st_ino, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_mtime_ns)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["winner.lock"]


def test_publication_uses_winner_created_between_inspection_and_rename(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "raced.lock"
    winner = []

    def publish_competitor(source, destination):
        assert not path.exists()
        path.write_bytes(b"winner")
        winner.append(path.stat())
        raise FileExistsError(errno.EEXIST, "competitor published first")

    monkeypatch.setattr(lock_module.os, "rename", publish_competitor)
    lock_module._publish_windows_lock_file(path, lock_module._directory_state(tmp_path))
    assert len(winner) == 1
    assert path.read_bytes() == b"winner"
    assert path.stat().st_ino == winner[0].st_ino
    assert sorted(p.name for p in tmp_path.iterdir()) == ["raced.lock"]


@pytest.mark.parametrize("failure", ["write", "flush", "close", "rename", "fdopen"])
def test_failed_publication_closes_owned_resources_and_preserves_error(tmp_path: Path, monkeypatch, failure) -> None:
    path = tmp_path / "failure.lock"
    primary = PermissionError(errno.EACCES, "secret publication error")
    fdopen, rename = lock_module.os.fdopen, lock_module.os.rename
    descriptors = []
    streams = []

    class Stream:
        def __init__(self, raw):
            self.raw = raw

        def write(self, value):
            if failure == "write":
                raise primary
            return self.raw.write(value)

        def flush(self):
            if failure == "flush":
                raise primary
            self.raw.flush()

        def close(self):
            self.raw.close()
            if failure == "close":
                raise primary

    def wrap(descriptor, *args, **kwargs):
        descriptors.append(descriptor)
        if failure == "fdopen":
            raise primary
        stream = Stream(fdopen(descriptor, *args, **kwargs))
        streams.append(stream)
        return stream

    def fail_rename(source, destination):
        if failure == "rename":
            raise primary
        rename(source, destination)

    monkeypatch.setattr(lock_module.os, "fdopen", wrap)
    monkeypatch.setattr(lock_module.os, "rename", fail_rename)
    with pytest.raises(PermissionError) as caught:
        lock_module._publish_windows_lock_file(path, lock_module._directory_state(tmp_path))
    assert caught.value is primary
    assert not path.exists()
    assert not list(tmp_path.iterdir())
    assert all(stream.raw.closed for stream in streams)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_publication_cleanup_failure_keeps_primary_and_warns_without_secrets(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    primary = OSError(errno.EIO, "secret publish failure")

    def fail_rename(source, destination):
        raise primary

    def fail_unlink(path, *args, **kwargs):
        raise OSError("secret cleanup failure")

    monkeypatch.setattr(lock_module.os, "rename", fail_rename)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError) as caught:
        lock_module._publish_windows_lock_file(tmp_path / "failed.lock", lock_module._directory_state(tmp_path))
    assert caught.value is primary
    assert capsys.readouterr().err == "warning: inter-process lock cleanup failed (temporary)\n"


def test_windows_open_refuses_target_removed_after_inspection(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "disappeared.lock"
    path.write_bytes(b"\0")
    original_open = lock_module.builtins.open

    def remove_then_open(value, *args, **kwargs):
        if Path(value) == path:
            path.unlink()
        return original_open(value, *args, **kwargs)

    monkeypatch.setattr(lock_module.builtins, "open", remove_then_open)
    with pytest.raises(FileNotFoundError):
        InterProcessFileLock(path)._open_windows_stream()
    assert not path.exists()


def test_windows_open_closes_handle_when_target_identity_changes(tmp_path: Path, monkeypatch) -> None:
    path, other = tmp_path / "changed.lock", tmp_path / "other.lock"
    path.write_bytes(b"\0")
    other.write_bytes(b"other")
    original_state, original_open = lock_module._file_state, lock_module.builtins.open
    observations, handles = [], []

    def changed_state(value):
        if value == path:
            observations.append(value)
            if len(observations) == 3:
                return other.stat()
        return original_state(value)

    def observe_open(value, *args, **kwargs):
        stream = original_open(value, *args, **kwargs)
        if Path(value) == path:
            handles.append(stream)
        return stream

    monkeypatch.setattr(lock_module, "_file_state", changed_state)
    monkeypatch.setattr(lock_module.builtins, "open", observe_open)
    with pytest.raises(OSError, match="changed during open"):
        InterProcessFileLock(path)._open_windows_stream()
    assert len(handles) == 1
    assert handles[0].closed
    assert path.read_bytes() == b"\0"


def test_publication_rejects_parent_replacement_without_deleting_other_files(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    moved = tmp_path / "moved"
    check = lock_module._check_directory
    changed = []

    def replace_parent(path, state):
        if not changed:
            parent.rename(moved)
            parent.mkdir()
            (parent / "unrelated").write_bytes(b"keep")
            changed.append(True)
        check(path, state)

    monkeypatch.setattr(lock_module, "_check_directory", replace_parent)
    with pytest.raises(OSError, match="directory changed"):
        lock_module._publish_windows_lock_file(parent / "parent.lock", lock_module._directory_state(parent))
    assert (parent / "unrelated").read_bytes() == b"keep"
    assert not (parent / "parent.lock").exists()
    assert len(list(moved.iterdir())) == 1
    assert capsys.readouterr().err == "warning: inter-process lock cleanup failed (temporary)\n"


@pytest.mark.parametrize("reparse", [False, True])
def test_windows_open_rejects_nonregular_or_reparse_target(tmp_path: Path, monkeypatch, reparse: bool) -> None:
    path = tmp_path / "unsafe.lock"
    path.mkdir()
    if reparse:
        lstat = Path.lstat
        unsafe = SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_file_attributes=0x400)
        monkeypatch.setattr(Path, "lstat", lambda value: unsafe if value == path else lstat(value))
    with pytest.raises(OSError, match="unsafe inter-process lock file"):
        InterProcessFileLock(path)._open_windows_stream()


def test_windows_open_rejects_hardlinked_target(tmp_path: Path) -> None:
    path, alias = tmp_path / "hardlink.lock", tmp_path / "alias.lock"
    path.write_bytes(b"\0")
    os.link(path, alias)
    with pytest.raises(OSError, match="unsafe inter-process lock file"):
        InterProcessFileLock(path)._open_windows_stream()
    assert path.read_bytes() == alias.read_bytes() == b"\0"


def test_owned_temporary_cleanup_does_not_delete_substituted_file(tmp_path: Path, capsys) -> None:
    parent = lock_module._directory_state(tmp_path)
    staged = tmp_path / ".id-lock-test"
    staged.write_bytes(b"\0")
    owned = staged.stat()
    substitute = tmp_path / "substitute"
    substitute.write_bytes(b"keep")
    os.replace(substitute, staged)
    lock_module._remove_owned_temporary(staged, parent, owned)
    assert staged.read_bytes() == b"keep"
    assert capsys.readouterr().err == "warning: inter-process lock cleanup failed (temporary)\n"
