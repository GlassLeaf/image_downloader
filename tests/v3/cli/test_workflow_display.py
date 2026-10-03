from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from image_downloader import (
    UpdateCandidate,
    UpdateCheckUnsupportedError,
    UpdateSnapshot,
    WorkflowAttemptResult,
    WorkflowItemResult,
    WorkflowResult,
    WorkflowRoundResult,
    WorkflowRunRecord,
    WorkflowStateItem,
    WorkflowStateView,
)
from image_downloader.application.workflow_reporting import history_details, history_outcome
from image_downloader.commands.state import state_payload
from image_downloader.commands.state_formatting import print_state
from image_downloader.commands.workflow import _print_workflow, _stopped_status, workflow_payload
from image_downloader.exceptions import PluginError, error_info_for
from image_downloader.privacy.log_safety import safe_url
from image_downloader.storage.workflow_history import run_payload

URL = "https://example.test/feed?token=secret"


def workflow(*, status="success", stopped=False, timed_out=False, cancelled=False):
    error = error_info_for(UpdateCheckUnsupportedError()) if stopped else None
    items = () if stopped else (WorkflowItemResult(URL, ("added",), status),)
    if items:
        item_error = error_info_for(PluginError("private secret")) if status == "failed" else None
        attempt = WorkflowAttemptResult(0, status, error=item_error)
        items = (WorkflowItemResult(URL, ("added",), status, error=item_error, attempts=(attempt,)),)
    snapshot = None if stopped else UpdateSnapshot(URL, (UpdateCandidate(URL),), datetime.now(UTC))
    round_result = WorkflowRoundResult(0, snapshot, status="stopped" if stopped else "complete", error=error)
    return WorkflowResult(
        URL, "updated", snapshot, (), items, error, cancelled, rounds=(round_result,), timed_out=timed_out
    )


def record(result):
    now = datetime.now(UTC)
    return WorkflowRunRecord(
        str(uuid4()),
        "core.generic-html",
        "a" * 64,
        safe_url(URL),
        now,
        now,
        4 if result.stop_error else 0,
        history_details(result, Path.cwd()),
    )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, "success"),
        ({"status": "failed"}, "incomplete"),
        ({"status": "partial"}, "incomplete"),
        ({"stopped": True}, "stopped"),
        ({"stopped": True, "timed_out": True}, "timed_out"),
        ({"cancelled": True}, "cancelled"),
    ],
)
def test_outcome_is_separate_from_url_counters(kwargs, expected, capsys):
    result = workflow(**kwargs)
    payload = workflow_payload(result, Path.cwd())
    assert payload["outcome"] == expected
    assert history_outcome(history_details(result, Path.cwd())) == expected
    _print_workflow(result, payload)
    captured = capsys.readouterr()
    assert expected in captured.err and "URL results:" in captured.err
    assert "secret" not in captured.out + captured.err
    assert "{'" not in captured.err
    if kwargs.get("stopped"):
        assert "update check result: not established" in captured.err
        assert payload["summary"]["failed"] == 0
        assert "selected plugin does not support update checks" in captured.err
        assert _stopped_status(UpdateCheckUnsupportedError()) == 4


@pytest.mark.parametrize("action", ["list", "show"])
def test_history_only_feed_displays_unavailable_state_without_zero_current_counts(action, capsys):
    view = WorkflowStateView("core.generic-html", "a" * 64, safe_url(URL), None, (), record(workflow(stopped=True)))
    data = state_payload(view)
    assert data["state_available"] is False
    assert data["checked_at"] is None and data["summary"]["candidates"] == 0
    print_state({"action": action, "workflows": [data]})
    out = capsys.readouterr().out
    assert "current state: not available" in out and "(exit 4, stopped)" in out
    assert "current state (None)" not in out and "None" not in out
    assert "{'" not in out and "secret" not in out
    if action == "list":
        assert "URL results:" not in out
        assert "latest saved run per feed" in out
        assert "py -m image_downloader state workflow history [URL]" in out
    else:
        assert "past execution result:" in out
        assert "View earlier saved runs:" not in out
        assert "update check result: not established" in out


def test_initialized_empty_state_is_distinguished_from_unavailable_state(capsys):
    data = state_payload(WorkflowStateView("core.generic-html", "a" * 64, None, datetime.now(UTC), ()))
    assert data["state_available"]
    print_state({"action": "list", "workflows": [data]})
    out = capsys.readouterr().out
    assert "candidates=0 completed=0 unfinished=0" in out
    assert "not available" not in out


@pytest.mark.parametrize("action", ["list", "show"])
def test_list_is_summary_and_show_has_current_candidates(action, capsys):
    item = WorkflowStateItem(UpdateCandidate("https://example.test/current", "content", "revision"), True)
    view = WorkflowStateView("core.generic-html", "a" * 64, None, datetime.now(UTC), (item,))
    print_state({"action": action, "workflows": [state_payload(view)]})
    out = capsys.readouterr().out
    assert "completed=1" in out
    assert ("https://example.test/current" in out) == (action == "show")
    if action == "show":
        assert "content_id: content; revision: revision" in out


def test_run_formats_attempts_rounds_errors_and_history_keeps_old_error(capsys):
    run = run_payload(record(workflow(status="failed")))
    print_state({"action": "run", "run": run})
    out = capsys.readouterr().out
    assert "attempt round 0: failed" in out and "round 0: complete" in out
    assert "plugin_error" in out and "update_check_unsupported" not in out
    assert "None" not in out and "{'" not in out and "secret" not in out
    print_state({"action": "history", "runs": [run]})
    out = capsys.readouterr().out
    assert "run " in out and "incomplete" in out
    assert "attempt round" not in out


def test_prune_normal_output_is_human_readable(capsys):
    print_state(
        {
            "action": "prune",
            "prune": {
                "dry_run": True,
                "deleted_run_ids": ("old-run",),
                "expired_count": 1,
                "before_bytes": 100,
                "after_bytes": 32,
                "over_limit": False,
            },
        }
    )
    out = capsys.readouterr().out
    assert "history prune: preview" in out and "would delete: old-run" in out
    assert "UTF-8 bytes: 100 -> 32" in out and "{'" not in out


@pytest.mark.parametrize("dry_run", [False, True])
def test_unsupported_updates_cli_returns_one_safe_document_and_exit_four(tmp_path, monkeypatch, capsys, dry_run):
    import json

    from image_downloader import AppConfig, RuntimeComposer
    from image_downloader.cli import main
    from image_downloader.commands import workflow as command

    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(tmp_path / "plugins")},
            "logging": {"console": {"enabled": False}},
        }
    )

    def composer(config, **kwargs):
        runtime = RuntimeComposer(config, **kwargs)
        service = runtime._compose_for_workflow_plan() if dry_run else runtime.compose()
        record = service.registry.records["core.generic-html"]
        plugin = SimpleNamespace(auth_flow=lambda context: None)
        service.registry.resolve = lambda *args, **kwargs: (record, plugin)
        return SimpleNamespace(compose=lambda: service, _compose_for_workflow_plan=lambda: service)

    monkeypatch.setattr(command, "RuntimeComposer", composer)
    monkeypatch.setattr(command, "_config_for", lambda *args, **kwargs: (config, tmp_path, None, "defaults"))
    monkeypatch.setattr(command, "_persist_initial_user_config", lambda *args: None)
    arguments = ["workflow", URL, "--json"] + (["--dry-run"] if dry_run else [])
    assert main(arguments) == 4
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["stop_error"]["code"] == "update_check_unsupported"
    assert payload["stop_error"]["reason"] == "selected plugin does not support update checks"
    assert "secret" not in output.out
    if not dry_run:
        assert payload["outcome"] == "stopped" and payload["checked_at"] is None
        assert payload["summary"]["failed"] == 0
