"""Acceptance checks for immutable requests and explicit partial-result failures."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from support.synchronization import wait_until_started
from support.workflow import FEED, Gallery, compose

from image_downloader import (
    DownloadRequest,
    DownloadService,
    InspectRequest,
    UpdateCheckRequest,
    WorkflowCancelledError,
    WorkflowExecutionError,
    WorkflowPlanCancelledError,
    WorkflowPlanError,
    WorkflowPlanRequest,
    WorkflowRequest,
)
from image_downloader.exceptions import UpdateStateError, error_info_for


@pytest.mark.parametrize(
    "request_type", (DownloadRequest, InspectRequest, UpdateCheckRequest, WorkflowPlanRequest, WorkflowRequest)
)
def test_requests_detach_and_freeze_nested_caller_options(request_type):
    options = {"example.site": {"nested": {"items": [1, 2]}}}
    request = request_type(url=FEED, plugin_overrides=options)
    options["example.site"]["nested"]["items"].append(3)
    assert request.plugin_overrides["example.site"]["nested"]["items"] == (1, 2)
    with pytest.raises(TypeError):
        request.plugin_overrides["example.site"]["nested"]["new"] = 1
    with pytest.raises(FrozenInstanceError):
        request.url = "https://different.test/feed"
    with pytest.raises(TypeError):
        request_type(FEED)


@pytest.mark.parametrize("planning", (False, True))
def test_workflow_failure_exposes_settled_result_and_original_cause(tmp_path, planning):
    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            service.state.filesystem.write_bytes_atomic("workflow.json", b"invalid JSON")
            expected = WorkflowPlanError if planning else WorkflowExecutionError
            with pytest.raises(expected) as caught:
                if planning:
                    await service.plan_workflow(WorkflowPlanRequest(url=FEED))
                else:
                    await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            error = caught.value
            assert isinstance(error.cause, UpdateStateError)
            assert error.__cause__ is error.cause
            assert error.result.stop_error.code == "update_state_error"
            assert error_info_for(error) == error_info_for(error.cause)
            assert not hasattr(error.cause, "workflow_result")
            assert not hasattr(error.cause, "workflow_plan_result")
            if not planning:
                assert error.result.history_saved is True

    asyncio.run(scenario())


def test_cancellation_types_preserve_asyncio_cancellation_and_old_download_entry_is_absent():
    assert issubclass(WorkflowCancelledError, asyncio.CancelledError)
    assert issubclass(WorkflowPlanCancelledError, asyncio.CancelledError)
    assert not issubclass(WorkflowCancelledError, Exception)
    assert not hasattr(DownloadService, "run")


def test_api_retry_count_preserves_arbitrary_integer_range():
    retries = 10**400
    assert WorkflowRequest(url=FEED, workflow_retries=retries).workflow_retries == retries


@pytest.mark.parametrize("planning", (False, True))
def test_started_workflow_cancellation_exposes_the_correct_typed_result(tmp_path, planning):
    async def scenario():
        started = asyncio.Event()

        class Blocked(Gallery):
            async def check_updates(self, url, context):
                started.set()
                await asyncio.Event().wait()

        async with compose(tmp_path, Blocked()) as service:
            operation = (
                service.plan_workflow(WorkflowPlanRequest(url=FEED))
                if planning
                else service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            )
            task = asyncio.create_task(operation)
            try:
                await wait_until_started(started, task)
                task.cancel()
                with pytest.raises(WorkflowPlanCancelledError if planning else WorkflowCancelledError) as caught:
                    await task
                assert caught.value.result.cancelled is True
                assert caught.value.result.snapshot is None
                if not planning:
                    assert caught.value.result.history_saved is True
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
