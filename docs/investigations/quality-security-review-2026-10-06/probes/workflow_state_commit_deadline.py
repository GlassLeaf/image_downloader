"""Repeat the state-commit deadline regression, optionally slowing retry preparation."""

import time

import pytest

from image_downloader.storage.workflow_logging import WorkflowLogWriter


def pytest_addoption(parser):
    parser.addoption("--repeat-state-commit", type=int, default=1)
    parser.addoption("--state-commit-preparation-delay", type=float, default=0)


@pytest.fixture(autouse=True)
def state_commit_iteration():
    """Provide a separate fixture scope and temporary directory for each repetition."""


def pytest_generate_tests(metafunc):
    if metafunc.function.__name__ == "test_deadline_during_state_commit_finishes_transaction":
        metafunc.parametrize(
            "state_commit_iteration", range(metafunc.config.getoption("--repeat-state-commit")), indirect=True
        )


@pytest.fixture(autouse=True)
def slow_state_commit_preparation(request, monkeypatch):
    if request.node.originalname != "test_deadline_during_state_commit_finishes_transaction":
        yield
        return
    original = WorkflowLogWriter.plan
    delayed = False

    def plan(writer, round_number, document):
        nonlocal delayed
        if round_number == 1 and not delayed:
            delayed = True
            time.sleep(request.config.getoption("--state-commit-preparation-delay"))
        return original(writer, round_number, document)

    monkeypatch.setattr(WorkflowLogWriter, "plan", plan)
    yield
