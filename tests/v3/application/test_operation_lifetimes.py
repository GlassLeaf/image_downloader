"""Selected plugins and detached sessions have one owner on every exit path."""

from __future__ import annotations

import asyncio

import pytest
from support.synchronization import wait_until_started
from support.workflow import FEED, A, Gallery, compose

from image_downloader import (
    InspectRequest,
    PluginError,
    WorkflowCancelledError,
    WorkflowExecutionError,
    WorkflowPlanCancelledError,
    WorkflowPlanError,
    WorkflowPlanRequest,
    WorkflowRequest,
)
from image_downloader.application import inspection
from image_downloader.exceptions import InterProcessLockError, UpdateStateError
from image_downloader.storage.workflow import WorkflowState
from image_downloader.transport.gateway import RequestGateway


@pytest.mark.parametrize("planning", [False, True])
@pytest.mark.parametrize("stage", ["lock_creation", "lock_acquisition", "state_validation"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_preparation_failure_cleans_selected_plugin_once_and_keeps_primary(
    tmp_path, monkeypatch, planning, stage, cleanup_fails
):
    async def scenario():
        class Selected(Gallery):
            def cleanup_after_use(self):
                super().cleanup_after_use()
                if cleanup_fails:
                    raise PluginError("secondary cleanup failure")

        gallery = Selected()
        primary = UpdateStateError() if stage == "state_validation" else InterProcessLockError()

        class Lock:
            async def acquire_async(self):
                if stage == "lock_acquisition":
                    raise primary

            def release(self):
                if stage == "state_validation":
                    assert gallery.cleanups == 1

        def feed_lock(*_args):
            if stage == "lock_creation":
                raise primary
            return Lock()

        monkeypatch.setattr(WorkflowState, "feed_lock", feed_lock)
        if stage == "state_validation":

            def validate(_self):
                raise primary

            monkeypatch.setattr(WorkflowState, "validate", validate)
        async with compose(tmp_path, gallery) as service:
            with pytest.raises(WorkflowPlanError if planning else WorkflowExecutionError) as caught:
                if planning:
                    await service.plan_workflow(WorkflowPlanRequest(url=FEED))
                else:
                    await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert caught.value.cause is primary
            assert gallery.checks == 0
            assert gallery.cleanups == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("planning", [False, True])
def test_cancellation_while_acquiring_feed_lock_cleans_selected_plugin(tmp_path, monkeypatch, planning):
    async def scenario():
        gallery = Gallery()
        started = asyncio.Event()

        class Lock:
            async def acquire_async(self):
                started.set()
                await asyncio.Event().wait()

            def release(self):
                raise AssertionError("a lock which was never acquired must not be released")

        monkeypatch.setattr(WorkflowState, "feed_lock", lambda *_args: Lock())
        async with compose(tmp_path, gallery) as service:
            operation = (
                service.plan_workflow(WorkflowPlanRequest(url=FEED))
                if planning
                else service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            )
            task = asyncio.create_task(operation)
            try:
                await wait_until_started(started, task)
                task.cancel()
                with pytest.raises(WorkflowPlanCancelledError if planning else WorkflowCancelledError):
                    await task
                assert gallery.cleanups == 1
                assert gallery.checks == 0
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["plugin", "cancel", "interrupt", "success"])
def test_inspection_session_close_preserves_primary_and_closes_client(tmp_path, monkeypatch, failure):
    async def scenario():
        primary = {
            "plugin": PluginError("primary plugin failure"),
            "cancel": asyncio.CancelledError(),
            "interrupt": KeyboardInterrupt(),
            "success": None,
        }[failure]
        secondary = RuntimeError("secondary session close failure")
        sessions = []

        class Selected(Gallery):
            async def inspect(self, url, context):
                if primary is not None:
                    raise primary
                return await super().inspect(url, context)

        class Session(RequestGateway):
            async def close(self):
                sessions.append(self)
                await super().close()
                raise secondary

        monkeypatch.setattr(inspection, "RequestGateway", Session)
        gallery = Selected()
        async with compose(tmp_path, gallery) as service:
            with pytest.raises(type(primary) if primary is not None else RuntimeError) as caught:
                await service.inspect(InspectRequest(url=A.url, resolve_image_requests=False))
            assert caught.value is (primary if primary is not None else secondary)
            assert gallery.cleanups == 1
            assert len(sessions) == 1 and sessions[0].client.is_closed

    asyncio.run(scenario())
