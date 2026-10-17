"""Fatal job failures remain catchable after owned work has settled."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
import threading

import pytest
from support.synchronization import managed_task, wait_until_started

from image_downloader.application.workflow_recording import _settle
from image_downloader.storage.transactions import finish_transaction

PROBE = textwrap.dedent(
    """
    import asyncio
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from image_downloader import AppConfig, RuntimeComposer
    from image_downloader.application.concurrency import bounded
    from image_downloader.application.image_executor import ImageExecutor
    from image_downloader.application.workflow_recording import _settle
    from image_downloader.storage.transactions import finish_transaction

    kind, mode, root = sys.argv[1:]
    failure = {"exit": SystemExit, "interrupt": KeyboardInterrupt, "cancel": asyncio.CancelledError}[kind]("fatal job")
    events = []

    def fail_sync(*args):
        raise failure

    async def fail_async():
        raise failure

    async def sibling():
        await asyncio.sleep(0)
        events.append("sibling")

    async def queued():
        events.append("queued")

    async def main():
        primary = ValueError("operation failed") if mode.endswith("_primary") else failure
        service = None
        try:
            if mode.startswith("service"):
                root = Path(sys.argv[3])
                config = AppConfig.model_validate({
                    "storage": {"data_root": str(root / "data")},
                    "plugins": {"root": str(root / "plugins")},
                    "security": {"plugin_verification": "off"},
                    "logging": {"console": {"enabled": False}},
                })
                service = RuntimeComposer(config, config_root=root, plugin_root=root / "plugins").compose()
                if mode.startswith("service_logger"):
                    original_close = service.logger.close

                    async def close_logger():
                        await original_close()
                        raise failure

                    service.logger.close = close_logger
                else:
                    service.cookie_store.persist_delta = fail_sync
                if mode.endswith("_primary"):
                    async with service:
                        raise primary
                else:
                    await service.close()
            elif mode == "transaction":
                await finish_transaction(fail_sync)
            elif mode == "history":
                await _settle(fail_sync)
            elif mode == "allocation":
                await ImageExecutor._settle_allocation(SimpleNamespace(commit=fail_async), success=True)
            else:
                factory = fail_sync if mode == "factory" else fail_async
                await bounded([factory, sibling, queued], 2)
        except BaseException as caught:
            assert caught is primary, (type(caught).__name__, type(primary).__name__)
            if service is not None:
                assert service.gateway.client.is_closed
                assert service.image_processor._closed
                assert not service._operation_lock.locked()
            if mode in {"bounded", "factory"}:
                assert events == ["sibling"], events
            print(json.dumps({"caught": type(caught).__name__}))
            return
        raise AssertionError("fatal failure was lost")

    asyncio.run(main())
    """
)


@pytest.mark.parametrize("kind", ["exit", "interrupt", "cancel"])
@pytest.mark.parametrize(
    "mode",
    ["transaction", "history", "allocation", "bounded", "factory", "service", "service_primary",
     "service_logger", "service_logger_primary"],
)
def test_fatal_failures_do_not_escape_the_owners_event_loop(tmp_path, kind, mode):
    # A regression can stop asyncio.run itself. Isolate it from pytest so the
    # suite reports a failure instead of receiving KeyboardInterrupt/SystemExit.
    result = subprocess.run(
        [sys.executable, "-c", PROBE, kind, mode, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "ValueError" if mode.endswith("_primary") else {
        "exit": "SystemExit", "interrupt": "KeyboardInterrupt", "cancel": "CancelledError"
    }[kind]
    assert json.loads(result.stdout) == {"caught": expected}


@pytest.mark.parametrize("history", [False, True])
def test_thread_error_and_waiter_cancellation_keep_the_callers_priority(history):
    async def scenario():
        started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        failure = ValueError("write failed")

        def write():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(30)
            finished.set()
            raise failure

        async with managed_task(_settle(write) if history else finish_transaction(write)) as task:
            try:
                await wait_until_started(started, task)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError if history else ValueError) as caught:
                await task
            if not history:
                assert caught.value is failure
            assert finished.is_set()

    asyncio.run(scenario())
