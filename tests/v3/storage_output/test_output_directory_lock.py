from __future__ import annotations

import asyncio
import io
import multiprocessing
import os
import unicodedata
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from image_downloader.config import AppConfig
from image_downloader.exceptions import InterProcessLockError
from image_downloader.models import Chapter, ImageResource
from image_downloader.observability.logging import DownloadLogger
from image_downloader.output.output_allocator import OutputAllocator
from image_downloader.output.output_lock import OutputDirectoryLocks
from image_downloader.runtime import DownloadService, RuntimeComposer
from image_downloader.storage import FileSystem
from image_downloader.storage.interprocess_lock import InterProcessFileLock


def _worker(root: str, state_root: str, mode: str, ready, start, result, label: str) -> None:
    async def run() -> None:
        outputs = FileSystem(Path(root))
        state = FileSystem(Path(state_root))
        config = AppConfig.model_validate({"output": {"existing_file": mode}})
        allocator = OutputAllocator(outputs, config)
        directory = Path("chapter")
        outputs.ensure_directory(directory)
        locks = OutputDirectoryLocks(state, timeout_seconds=5, logger=DownloadLogger())
        ready.put(label)
        if not start.wait(10):
            raise RuntimeError("start signal timed out")
        try:
            async with locks.hold(outputs.path(directory)):
                await allocator.refresh_directory(directory)
                allocation = await allocator.allocate(
                    directory, ImageResource("https://example.test/1"), Chapter(1, "x"), ".jpeg"
                )
                if allocation.should_write:
                    outputs.write_bytes_atomic(allocation.relative_path, label.encode())
                    await allocation.commit()
                result.put((label, "saved" if allocation.should_write else "skipped", allocation.relative_path.name))
        except FileExistsError:
            result.put((label, "error", ""))

    asyncio.run(run())


def _abort_holder(root: str, state_root: str, acquired, release) -> None:
    async def run() -> None:
        outputs = FileSystem(Path(root))
        directory = Path("chapter")
        outputs.ensure_directory(directory)
        locks = OutputDirectoryLocks(FileSystem(Path(state_root)), timeout_seconds=5, logger=DownloadLogger())
        allocator = OutputAllocator(outputs, AppConfig.model_validate({"output": {"existing_file": "skip"}}))
        async with locks.hold(outputs.path(directory)):
            await allocator.refresh_directory(directory)
            allocation = await allocator.allocate(
                directory, ImageResource("https://example.test/1"), Chapter(1, "x"), ".jpeg"
            )
            await allocation.abort()
            acquired.set()
            if not release.wait(10):
                raise RuntimeError("release signal timed out")

    asyncio.run(run())


def _crash_holder(state_root: str, directory: str, acquired) -> None:
    locks = OutputDirectoryLocks(FileSystem(Path(state_root)), timeout_seconds=5, logger=DownloadLogger())

    async def run() -> None:
        async with locks.hold(Path(directory)):
            acquired.set()
            os._exit(7)

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["overwrite", "skip", "rename", "error"])
def test_two_processes_apply_existing_file_policy(tmp_path: Path, mode: str) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    result = context.Queue()
    start = context.Event()
    root = (tmp_path / "downloads").resolve()
    state_root = (tmp_path / "state").resolve()
    processes = [
        context.Process(target=_worker, args=(str(root), str(state_root), mode, ready, start, result, label))
        for label in ("first", "second")
    ]
    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=15) for _ in processes} == {"first", "second"}
        start.set()
        outcomes = [result.get(timeout=15) for _ in processes]
    finally:
        start.set()
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
    assert all(process.exitcode == 0 for process in processes)
    statuses = sorted(item[1] for item in outcomes)
    if mode == "overwrite":
        assert statuses == ["saved", "saved"]
        assert {item[2] for item in outcomes} == {"0001.jpeg"}
    elif mode == "skip":
        assert statuses == ["saved", "skipped"]
    elif mode == "rename":
        assert statuses == ["saved", "saved"]
        assert {item[2] for item in outcomes} == {"0001.jpeg", "0001_1.jpeg"}
    else:
        assert statuses == ["error", "saved"]
    assert (root / "chapter" / "0001.jpeg").is_file()


def test_same_operation_overwrite_renames_after_first_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        outputs = FileSystem((tmp_path / "downloads").resolve())
        state = FileSystem((tmp_path / "state").resolve())
        config = AppConfig.model_validate({"output": {"existing_file": "overwrite"}})
        allocator = OutputAllocator(outputs, config)
        locks = OutputDirectoryLocks(state, timeout_seconds=1, logger=DownloadLogger())
        directory = Path("chapter")
        outputs.ensure_directory(directory)
        paths = []
        for data in (b"one", b"two"):
            async with locks.hold(outputs.path(directory)):
                await allocator.refresh_directory(directory)
                allocation = await allocator.allocate(
                    directory, ImageResource("https://example.test/1"), Chapter(1, "x"), ".jpeg"
                )
                paths.append(allocation.relative_path)
                outputs.write_bytes_atomic(allocation.relative_path, data)
                await allocation.commit()
        assert {path.name for path in paths} == {"0001.jpeg", "0001_1.jpeg"}
        assert {outputs.path(path).read_bytes() for path in paths} == {b"one", b"two"}

    asyncio.run(scenario())


def test_skip_takes_original_name_after_another_process_aborts(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    root = (tmp_path / "downloads").resolve()
    state_root = (tmp_path / "state").resolve()
    acquired = context.Event()
    release = context.Event()
    ready = context.Queue()
    start = context.Event()
    result = context.Queue()
    first = context.Process(target=_abort_holder, args=(str(root), str(state_root), acquired, release))
    second = context.Process(target=_worker, args=(str(root), str(state_root), "skip", ready, start, result, "later"))
    first.start()
    try:
        assert acquired.wait(15)
        second.start()
        assert ready.get(timeout=15) == "later"
        start.set()
        release.set()
        assert result.get(timeout=15) == ("later", "saved", "0001.jpeg")
    finally:
        release.set()
        start.set()
        for process in (first, second):
            if process.pid is None:
                continue
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
    assert first.exitcode == second.exitcode == 0


def test_crash_releases_lock_but_keeps_sidecar(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    state = FileSystem((tmp_path / "state").resolve())
    directory = (tmp_path / "downloads" / "chapter").resolve()
    locks = OutputDirectoryLocks(state, timeout_seconds=1, logger=DownloadLogger())
    acquired = context.Event()
    child = context.Process(target=_crash_holder, args=(str(state.root), str(directory), acquired))
    child.start()
    try:
        assert acquired.wait(15)
        child.join(10)
        assert child.exitcode == 7
        async def reacquire() -> None:
            async with locks.hold(directory):
                pass
        asyncio.run(reacquire())
        assert locks.lock_path(directory).exists()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_refresh_detects_new_case_and_unicode_collision(tmp_path: Path) -> None:
    async def scenario() -> None:
        outputs = FileSystem((tmp_path / "downloads").resolve())
        directory = Path("chapter")
        outputs.ensure_directory(directory)
        config = AppConfig.model_validate({"output": {"existing_file": "rename", "filename_format": "%TITLE%.%EXT%"}})
        allocator = OutputAllocator(outputs, config)
        chapter = Chapter(1, unicodedata.normalize("NFD", "CAFÉ"))
        await allocator.refresh_directory(directory)
        outputs.write_bytes_atomic(directory / "Café.jpeg", b"external")
        await allocator.refresh_directory(directory)
        allocation = await allocator.allocate(directory, ImageResource("https://example.test/1"), chapter, ".jpeg")
        assert allocation.relative_path.name.endswith("_1.jpeg")
        await allocation.abort()

    asyncio.run(scenario())


def test_wait_timeout_and_cancel_leave_lock_reusable(tmp_path: Path) -> None:
    async def scenario() -> None:
        state = FileSystem((tmp_path / "state").resolve())
        directory = (tmp_path / "downloads" / "chapter").resolve()
        locks = OutputDirectoryLocks(state, timeout_seconds=0, logger=DownloadLogger())
        held = InterProcessFileLock(locks.lock_path(directory))
        held.acquire()
        try:
            with pytest.raises(InterProcessLockError, match="timed out"):
                async with locks.hold(directory):
                    raise AssertionError("lock must not be acquired")
            waiting_locks = OutputDirectoryLocks(state, timeout_seconds=10, logger=DownloadLogger())
            waiting = asyncio.create_task(waiting_locks.hold(directory).__aenter__())
            await asyncio.sleep(0.15)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        finally:
            held.release()
        async with locks.hold(directory):
            pass

    asyncio.run(scenario())


def test_cancellation_waits_for_reservation_cleanup_before_unlock(tmp_path: Path) -> None:
    async def scenario() -> None:
        outputs = FileSystem((tmp_path / "downloads").resolve())
        directory = Path("chapter")
        outputs.ensure_directory(directory)
        allocator = OutputAllocator(outputs, AppConfig())
        locks = OutputDirectoryLocks(
            FileSystem((tmp_path / "state").resolve()), timeout_seconds=0, logger=DownloadLogger()
        )
        entered = asyncio.Event()
        proceed = asyncio.Event()
        original_finish = allocator._finish

        async def slow_finish(*args, **kwargs) -> None:
            entered.set()
            await proceed.wait()
            await original_finish(*args, **kwargs)

        allocator._finish = slow_finish  # type: ignore[method-assign]
        async with locks.hold(outputs.path(directory)):
            allocation = await allocator.allocate(
                directory, ImageResource("https://example.test/1"), Chapter(1, "x"), ".jpeg"
            )
            cleanup = asyncio.create_task(DownloadService._settle_allocation(allocation, success=False))
            await entered.wait()
            cleanup.cancel()
            await asyncio.sleep(0)
            assert not cleanup.done()
            proceed.set()
            with pytest.raises(asyncio.CancelledError):
                await cleanup
            assert not allocator._reserved
        async with locks.hold(outputs.path(directory)):
            replacement = await allocator.allocate(
                directory, ImageResource("https://example.test/2"), Chapter(1, "x"), ".jpeg"
            )
            await replacement.abort()

    asyncio.run(scenario())


def test_release_diagnostic_does_not_replace_primary_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        state = FileSystem((tmp_path / "state").resolve())
        directory = (tmp_path / "downloads" / "chapter").resolve()
        locks = OutputDirectoryLocks(state, timeout_seconds=0, logger=DownloadLogger())
        original_release = InterProcessFileLock.release

        def release_then_fail(lock: InterProcessFileLock) -> None:
            original_release(lock)
            raise InterProcessLockError("release diagnostic")

        monkeypatch.setattr(InterProcessFileLock, "release", release_then_fail)
        with pytest.raises(ValueError, match="primary"):
            async with locks.hold(directory):
                raise ValueError("primary")

    asyncio.run(scenario())


def test_lock_timeout_fails_entire_download_operation(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = (tmp_path / "data").resolve()
        plugin_root = (tmp_path / "plugins").resolve()
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str(data_root)},
                "plugins": {"root": str(plugin_root)},
                "security": {"plugin_verification": "off"},
                "output": {"lock_timeout_seconds": 0},
                "logging": {"console": {"enabled": False}},
                "notification": {"enabled": False},
                "download": {"continue_on_image_error": True},
            }
        )
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()
        await service.gateway.client.aclose()
        image_buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(image_buffer, format="PNG")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/gallery":
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    content=b"<title>Gallery</title><img src='/one.png'>",
                    request=request,
                )
            return httpx.Response(
                200, headers={"content-type": "image/png"}, content=image_buffer.getvalue(), request=request
            )

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        directory = service.outputs.path(Path("0001_Gallery"))
        held = InterProcessFileLock(service.output_locks.lock_path(directory))
        held.acquire()
        try:
            with pytest.raises(InterProcessLockError, match="timed out"):
                await service.run("https://example.test/gallery")
        finally:
            held.release()
            await service.close()

    asyncio.run(scenario())


def test_lock_key_is_normalized_and_distinct_chapters_are_independent(tmp_path: Path) -> None:
    async def scenario() -> None:
        state = FileSystem((tmp_path / "state").resolve())
        locks = OutputDirectoryLocks(state, timeout_seconds=0, logger=DownloadLogger())
        root = (tmp_path / "downloads").resolve()
        first = root / "Café"
        equivalent = root / unicodedata.normalize("NFD", "CAFÉ")
        other = root / "other"
        assert locks.lock_path(first) == locks.lock_path(equivalent)
        assert locks.lock_path(first) != locks.lock_path(other)
        async with locks.hold(first):
            async with locks.hold(other):
                pass
        assert locks.lock_path(first).is_file()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf")])
def test_output_lock_timeout_rejects_invalid_values(value: float) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"output": {"lock_timeout_seconds": value}})


def test_simultaneous_directory_creation(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem((tmp_path / "downloads").resolve())
        await asyncio.gather(*(asyncio.to_thread(filesystem.ensure_directory, "chapter/nested") for _ in range(10)))
        assert filesystem.path("chapter/nested").is_dir()

    asyncio.run(scenario())
