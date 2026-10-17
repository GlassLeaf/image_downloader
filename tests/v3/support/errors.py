"""Assertions for partial-result workflow failures and their original causes."""

from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager

import pytest

from image_downloader import WorkflowExecutionError, WorkflowPlanError


@contextmanager
def expect_workflow_error(expected, *, match=None):
    """Assert the new wrapper contract while retaining each original cause check."""
    with pytest.raises((WorkflowExecutionError, WorkflowPlanError, asyncio.CancelledError)) as caught:
        yield caught
    error = caught.value
    cause = error.cause if isinstance(error, (WorkflowExecutionError, WorkflowPlanError)) else error
    assert isinstance(cause, expected)
    if match is not None:
        assert re.search(match, str(cause))
