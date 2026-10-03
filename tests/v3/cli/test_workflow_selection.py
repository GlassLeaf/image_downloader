from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from image_downloader import (
    UpdateCandidate,
    UpdateChange,
    UpdateChangeKind,
    UpdateSnapshot,
    WorkflowItemResult,
    WorkflowResult,
    WorkflowRoundResult,
    WorkflowRunRecord,
    WorkflowStateView,
)
from image_downloader.application.workflow_reporting import history_details
from image_downloader.application.workflow_selection import selection_explanation, workflow_selection
from image_downloader.commands.state import state_payload
from image_downloader.commands.state_formatting import print_state
from image_downloader.commands.workflow import _print_workflow, workflow_payload
from image_downloader.privacy.log_safety import safe_url
from image_downloader.storage.workflow_history import encode_records, run_payload

URL = "https://example.test/feed?token=secret"
A = "https://example.test/a"
B = "https://example.test/b"


def result(*, urls=(A,), selected=(), changes=(), scope="updated"):
    snapshot = UpdateSnapshot(URL, tuple(UpdateCandidate(u) for u in urls), datetime.now(UTC))
    differences = tuple(UpdateChange(UpdateChangeKind(kind), url) for kind, url in changes)
    return WorkflowResult(
        URL,
        scope,
        snapshot,
        differences,
        tuple(WorkflowItemResult(u, ("added",), "success") for u in selected),
        rounds=(WorkflowRoundResult(0, snapshot, differences, selected),),
    )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, "unchanged and previously completed; no downloads selected"),
        ({"urls": ()}, "plugin returned no candidates; no downloads selected"),
        ({"scope": "all", "selected": (A,)}, "all current URLs selected by all scope"),
        ({"selected": (A,), "changes": (("added", A),)}, "absent from workflow comparison history selected"),
        ({"selected": (A,), "changes": (("changed", A),)}, "with changed URL or revision selected"),
        ({"selected": (A,)}, "previously unfinished candidates selected"),
        ({"changes": (("removed", B),)}, "removed_candidates=1"),
        ({"urls": (), "changes": (("removed", A),)}, "removed_candidates=1"),
        ({"urls": (A, A), "selected": (A,)}, "candidates=2 selected_urls=1"),
    ],
)
def test_selection_explains_existing_initial_round_facts(kwargs, expected):
    execution = result(**kwargs)
    live = workflow_selection(execution)
    assert live == selection_explanation(history_details(execution, Path.cwd()))
    assert expected in "\n".join(live)


def test_multiple_reasons_and_deletions_are_reported_separately():
    execution = result(
        urls=(A, B, "https://example.test/c"),
        selected=(A, B, "https://example.test/c"),
        changes=(("added", A), ("changed", B), ("removed", "https://example.test/old")),
    )
    lines = workflow_selection(execution)
    assert "absent from workflow comparison history" in lines[1]
    assert "changed URL or revision" in lines[1]
    assert "previously unfinished" in lines[1]
    assert lines[2].startswith("Initial changes: removed_candidates=1")


def test_retry_changes_and_merged_reasons_do_not_rewrite_initial_selection():
    initial = result(selected=(A,), changes=(("added", A),))
    retry = WorkflowRoundResult(1, initial.snapshot, (UpdateChange(UpdateChangeKind.CHANGED, A),), (A,))
    recovered = replace(
        initial,
        changes=retry.changes,
        rounds=(*initial.rounds, retry),
        items=(WorkflowItemResult(A, ("added", "changed", "unfinished"), "success"),),
    )
    assert workflow_selection(recovered) == workflow_selection(initial)
    assert "changed URL or revision" not in "\n".join(workflow_selection(recovered))


def test_multiple_candidates_sharing_url_preserve_known_initial_reasons():
    execution = result(urls=(A, A), selected=(A,), changes=(("added", A),))
    execution = replace(execution, items=(WorkflowItemResult(A, ("added", "unfinished"), "success"),))
    explanation = "\n".join(workflow_selection(execution))
    assert "candidates=2 selected_urls=1" in explanation
    assert "absent from workflow comparison history" in explanation and "previously unfinished" in explanation


def test_unknown_and_unestablished_selection_are_not_reported_as_empty_candidates():
    execution = result()
    old = replace(execution, rounds=())
    stopped = replace(execution, snapshot=None, rounds=(WorkflowRoundResult(0, None, status="stopped"),))
    assert workflow_selection(old) == ("Selection: initial selection details were not recorded",)
    assert workflow_selection(stopped) == ("Selection: update check and target selection were not established",)
    during_download = replace(execution, rounds=(replace(execution.rounds[0], status="stopped"),))
    assert workflow_selection(during_download) == workflow_selection(execution)


@pytest.mark.parametrize("action", ["workflow", "list", "show", "history", "run"])
def test_selection_is_shared_across_outputs_without_changing_json_or_history(action, capsys):
    execution = result()
    now = datetime.now(UTC)
    details = history_details(execution, Path.cwd())
    record = WorkflowRunRecord(str(uuid4()), "core.generic-html", "a" * 64, safe_url(URL), now, now, 0, details)
    before_history = encode_records([record])
    data = workflow_payload(execution, Path.cwd())
    before_json = json.dumps(data)
    if action == "workflow":
        _print_workflow(execution, data)
    else:
        payload = {"action": action}
        if action in {"list", "show"}:
            view = WorkflowStateView("core.generic-html", "a" * 64, safe_url(URL), None, (), record)
            payload["workflows"] = [state_payload(view)]
        elif action == "history":
            payload["runs"] = [run_payload(record)]
        else:
            payload["run"] = run_payload(record)
        print_state(payload)
    output = capsys.readouterr()
    text = output.out + output.err
    assert "candidates=1 selected_urls=0" in text
    assert "unchanged and previously completed; no downloads selected" in text
    assert "First-run status is not recorded" in text
    assert "secret" not in text
    assert encode_records([record]) == before_history
    assert json.dumps(data) == before_json
    assert data["summary"] == dict.fromkeys(("success", "partial", "failed", "unprocessed", "removed"), 0)
