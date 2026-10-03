"""Persist outcomes for the opt-in Mailpit run, including failed assertions."""

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    case = item.funcargs.get("mail_case")
    if case is not None and report.when in {"call", "teardown"}:
        case.record_outcome(report)
