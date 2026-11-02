from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest

from image_downloader.exceptions import PluginError
from image_downloader.media import TransformExecutor


@pytest.mark.parametrize("workers", [1, 2, 3, 4])
def test_admission_is_bounded_and_pending_work_can_be_cancelled(workers):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        lock = threading.Lock()
        count = 0
        thread_ids = set()

        def block():
            nonlocal count
            with lock:
                count += 1
                thread_ids.add(threading.get_ident())
                if count == workers:
                    loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)

        async with TransformExecutor(workers) as executor:
            active = [asyncio.create_task(executor.run(block)) for _ in range(workers)]
            try:
                await asyncio.wait_for(entered.wait(), 5)
                queued = asyncio.create_task(executor.run(lambda: pytest.fail("cancelled job was submitted")))
                await asyncio.sleep(0)
                queued.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await queued
                assert count == workers
                assert len(thread_ids) == workers
                assert threading.get_ident() not in thread_ids
            finally:
                release.set()
                await asyncio.gather(*active)

    asyncio.run(scenario())


def test_cancelled_caller_waits_for_started_work_and_close_is_idempotent():
    async def scenario():
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        finished = []
        executor = TransformExecutor(1)

        def block():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            finished.append(True)

        task = asyncio.create_task(executor.run(block))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            closing = asyncio.create_task(executor.close())
            await asyncio.sleep(0)
            with pytest.raises(PluginError, match="closed"):
                await executor.run(lambda: None)
            assert not closing.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await executor.close()
        assert finished == [True]
        await closing
        await executor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [ValueError("test"), SystemExit(19), KeyboardInterrupt()])
def test_worker_failures_are_collected_and_admission_is_released(error):
    async def scenario():
        async with TransformExecutor(1) as executor:

            def fail():
                raise error

            with pytest.raises(type(error)) as caught:
                await executor.run(fail)
            assert caught.value is error
            assert await executor.run(lambda: 42) == 42

    asyncio.run(scenario())


def test_context_is_copied_per_submission():
    marker = contextvars.ContextVar("transform_test")

    async def scenario():
        async with TransformExecutor(1) as executor:
            marker.set("first")
            assert await executor.run(marker.get) == "first"
            await executor.run(lambda: marker.set("worker-only"))
            assert marker.get() == "first"
            marker.set("second")
            assert await executor.run(marker.get) == "second"

    asyncio.run(scenario())


def test_cross_loop_reentry_is_rejected_without_deadlocking():
    async def scenario():
        async with TransformExecutor(1) as executor:
            with pytest.raises(PluginError, match="owning event loop"):
                await executor.run(lambda: asyncio.run(executor.run(lambda: None)))
            assert await executor.run(lambda: 42) == 42

    asyncio.run(scenario())


@pytest.mark.parametrize("workers", [0, 5, True, 1.5, "2"])
def test_invalid_worker_counts_are_rejected(workers):
    with pytest.raises(ValueError):
        TransformExecutor(workers)

    from pydantic import ValidationError

    from image_downloader.config import AppConfig

    with pytest.raises(ValidationError):
        AppConfig.model_validate({"media": {"transform_workers": workers}})


def test_legacy_coroutine_hooks_are_rejected_at_load_and_awaitable_returns_at_call(monkeypatch):
    from types import SimpleNamespace

    from image_downloader.models import ImageArtifact
    from image_downloader.plugins.builtin import GenericHtmlPlugin
    from image_downloader.plugins.lifecycle import PluginClassLoader
    from image_downloader.plugins.plugin_invoker import PluginInvoker

    class Legacy(GenericHtmlPlugin):
        async def transform_image(self, artifact, context):
            return artifact

    loader = PluginClassLoader()
    monkeypatch.setattr(loader, "construct", lambda _: Legacy())
    with pytest.raises(PluginError, match="replace async def with def"):
        loader.site_instance(SimpleNamespace(kind="site_plugin"))
    loader.close()

    class Disguised:
        def transform_image(self, artifact, context):
            return Legacy().transform_image(artifact, context)

    async def scenario():
        async with TransformExecutor() as executor:
            with pytest.raises(PluginError, match="must be synchronous"):
                await PluginInvoker("test.legacy").transform_image(
                    Disguised(),
                    ImageArtifact(b"test", "image/png", "image:1"),
                    None,
                    chapter_id="1",
                    executor=executor,
                )

    asyncio.run(scenario())
