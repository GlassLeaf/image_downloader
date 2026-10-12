"""Bounded delay/repetition probe for the workflow interruption logging test.

Add this directory to PYTHONPATH and run pytest with -p workflow_logging_timeout.
The default leaves timing unchanged; --delay-retry-plan 0.3 reproduces the old race.
"""

from __future__ import annotations

import json
import math
import time

import pytest

from image_downloader.storage.workflow_logging import WorkflowLogWriter

_TEST = "test_cancel_or_timeout_distinguishes_interrupted_and_not_started"


def pytest_addoption(parser):
    parser.addoption("--repeat-workflow-interruption", type=int, default=1)
    parser.addoption("--delay-retry-plan", type=float, default=0)


def pytest_configure(config):
    repeats = config.getoption("--repeat-workflow-interruption")
    delay = config.getoption("--delay-retry-plan")
    if not 1 <= repeats <= 100 or not math.isfinite(delay) or not 0 <= delay <= 3:
        raise pytest.UsageError("repeat must be 1..100; delay must be finite and 0..3 seconds")


@pytest.fixture(autouse=True)
def workflow_interruption_repeat():
    pass


def pytest_generate_tests(metafunc):
    if metafunc.function.__name__ == _TEST:
        metafunc.parametrize(
            "workflow_interruption_repeat",
            range(metafunc.config.getoption("--repeat-workflow-interruption")),
            indirect=True,
        )


@pytest.fixture(autouse=True)
def slow_retry_plan(request, monkeypatch, tmp_path):
    delay = request.config.getoption("--delay-retry-plan")
    if request.node.originalname != _TEST or not request.node.callspec.params["timeout"] or not delay:
        yield
        return
    original = WorkflowLogWriter.plan

    def plan(writer, round_number, document):
        if round_number == 1:
            time.sleep(delay)
        return original(writer, round_number, document)

    monkeypatch.setattr(WorkflowLogWriter, "plan", plan)
    yield
    for path in tmp_path.rglob("*.jsonl"):
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        print("workflow events:", [(record["event"], record.get("url")) for record in records])
        print("final flags:", {key: records[-1].get(key) for key in ("timed_out", "cancelled", "exit_code")})
