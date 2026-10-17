"""A failed startup guard must not trap service.close behind its own test task."""

from __future__ import annotations

import asyncio

import pytest
from support.synchronization import managed_task, wait_until_started
from support.workflow import FEED, Gallery, compose

from image_downloader import WorkflowRequest


@pytest.mark.parametrize("failure", ["startup_timeout", "early_error", "early_return"])
def test_startup_failure_settles_task_before_service_close(tmp_path, monkeypatch, failure):
    async def scenario():
        started, entered, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        error = RuntimeError("operation stopped before its milestone")

        async def execute(_request):
            entered.set()
            try:
                if failure == "early_error":
                    raise error
                if failure == "early_return":
                    return None
                await asyncio.Event().wait()
            finally:
                released.set()

        service = compose(tmp_path, Gallery())
        monkeypatch.setattr(service._workflows, "execute", execute)
        with pytest.raises(RuntimeError if failure == "early_error" else AssertionError) as caught:
            async with service:
                async with managed_task(service.workflow(WorkflowRequest(url=FEED))) as task:
                    await entered.wait()
                    await wait_until_started(started, task, timeout=0.01)
        if failure == "early_error":
            assert caught.value is error
        assert task.done() and released.is_set()
        assert service.gateway.client.is_closed
        assert not service._operation_lock.locked()

    async def guarded():
        await asyncio.wait_for(scenario(), 30)

    asyncio.run(guarded())
