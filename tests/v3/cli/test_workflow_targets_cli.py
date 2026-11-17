from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from support.synchronization import managed_task, wait_until_started
from support.workflow import FEED, A, B, Gallery, snapshot

from image_downloader import (
    AppConfig,
    PluginError,
    RuntimeComposer,
    UpdateCandidate,
    WorkflowStateService,
    WorkflowTargetSelector,
)
from image_downloader.cli import main
from image_downloader.commands import workflow_targets as command
from image_downloader.commands.dispatch import _execute
from image_downloader.commands.parser import build_parser
from image_downloader.configuration.paths import resolve_paths
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.workflow import WorkflowState

C = UpdateCandidate("https://example.test/c", "c", "1")


def setup_cli(tmp_path, *candidates):
    config_path = tmp_path / "app.yaml"
    config_path.write_text(f"storage:\n  data_root: '{tmp_path / 'data'}'\n", encoding="utf-8")
    config = AppConfig.model_validate({"storage": {"data_root": str(tmp_path / "data")}})
    state = WorkflowState(FileSystem(resolve_paths(config)["state"]))
    if candidates:
        state.prepare("p", FEED, snapshot(*candidates), "updated")
    return config_path, config, state


def run(config, *args):
    return main(["--config", str(config), "--json", "state", "workflow", *args])


@pytest.mark.parametrize(
    "answers,completed,prompts",
    [
        ("yes\nno\nyes\n", {A.url, C.url}, 3),
        ("no\nall-yes\n", {B.url, C.url}, 2),
        ("yes\nall-no\n", {A.url}, 2),
        ("all-no\n", set(), 1),
        ("\nwrong\nall-yes\n", {A.url, B.url, C.url}, 3),
    ],
)
def test_confirmation_choices_and_json_output(tmp_path, monkeypatch, capsys, answers, completed, prompts):
    path, config, _ = setup_cli(tmp_path, A, B, C)
    monkeypatch.setattr(sys, "stdin", io.StringIO(answers))
    assert run(path, "set", "--all", "--status", "completed") == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)["execution"]["result"]
    assert captured.err.count("Apply?") == prompts
    rows = WorkflowStateService(config).list_targets(WorkflowTargetSelector(all_targets=True))
    assert {row.candidate.url for row in rows if row.completed} == completed
    assert payload["summary"]["changed_records"] == len(completed)
    assert payload["summary"]["skipped_records"] == 3 - len(completed)


@pytest.mark.parametrize("interruption", ["eof", "ctrl-c"])
def test_input_interruption_discards_earlier_approvals(tmp_path, monkeypatch, capsys, interruption):
    path, _, state = setup_cli(tmp_path, A, B)
    before = state.filesystem.read_text("workflow.json")
    calls = 0

    def answer():
        nonlocal calls
        calls += 1
        if calls == 1:
            return "yes"
        raise EOFError() if interruption == "eof" else KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", answer)
    assert run(path, "set", "--all", "--status", "completed") == 130
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["execution"]["result"]["summary"]["changed_records"] == 0
    assert envelope["execution"]["status"] == "cancelled"
    assert state.filesystem.read_text("workflow.json") == before


def test_duplicate_urls_prompt_once_and_unchanged_dry_run_never_prompt(tmp_path, monkeypatch, capsys):
    path, _, state = setup_cli(tmp_path, A, B)
    state.prepare("q", FEED, snapshot(A), "updated")
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))
    assert run(path, "set", A.url, "--status", "completed") == 0
    captured = capsys.readouterr()
    assert captured.err.count("Apply?") == 1
    assert json.loads(captured.out)["execution"]["result"]["summary"]["changed_records"] == 2
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("unexpected confirmation"))
    assert run(path, "set", A.url, "--status", "completed") == 0
    capsys.readouterr()
    before = state.filesystem.read_text("workflow.json")
    assert run(path, "reset", "--all", "--dry-run") == 0
    payload = json.loads(capsys.readouterr().out)["execution"]["result"]
    assert payload["summary"]["dry_run"] and payload["summary"]["planned_records"] == 2
    assert state.filesystem.read_text("workflow.json") == before


def test_confirmation_releases_locks_and_detects_concurrent_change(tmp_path, monkeypatch, capsys):
    path, _, state = setup_cli(tmp_path, A)

    def answer():
        with state.feed_lock("p", FEED):
            state.set_status("p", FEED, A.url, "cancelled")
        return "yes"

    monkeypatch.setattr("builtins.input", answer)
    assert run(path, "set", A.url, "--status", "completed") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["error"]["code"] == "workflow_state_conflict_error"


@pytest.mark.parametrize("content_id", [None, "a"])
def test_regex_matches_raw_urls_but_output_and_prompts_mask_secrets(tmp_path, monkeypatch, capsys, content_id):
    secret = UpdateCandidate(A.url + "?token=hidden-value", content_id, "1")
    path, _, _ = setup_cli(tmp_path, secret)
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))
    assert run(path, "set", "--url-regex", "token=hidden-value$", "--status", "completed") == 0
    captured = capsys.readouterr()
    assert "hidden-value" not in captured.out + captured.err
    assert json.loads(captured.out)["execution"]["result"]["summary"]["changed_records"] == 1


@pytest.mark.parametrize(
    "args",
    [
        ["set", "--status", "completed"],
        ["set", "--all"],
        ["reset", A.url, "--all"],
        ["reset", "--url-regex", "["],
        ["reset", "--all", "--refresh"],
        ["targets", "--download-scope", "all"],
        ["targets", "--source", "invalid"],
        ["set", "--all", "--status", "completed", "--yes"],
        ["reset", "--all", "--status", "pending"],
        ["targets", "--dry-run"],
        ["targets", "not-a-url"],
    ],
)
def test_invalid_arguments_fail_before_confirmation_or_storage(tmp_path, monkeypatch, capsys, args):
    path, _, _ = setup_cli(tmp_path)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("unexpected confirmation"))
    assert run(path, *args) == 2
    assert json.loads(capsys.readouterr().out)["execution"]["error"]["code"] == "argument_error"
    assert not any(p.name == "workflow.json" for p in tmp_path.rglob("*"))


def test_empty_targets_missing_mutation_and_normal_text_rendering(tmp_path, capsys):
    path, _, _ = setup_cli(tmp_path)
    assert run(path, "targets") == 0
    assert json.loads(capsys.readouterr().out)["execution"]["result"]["targets"] == []
    assert run(path, "reset", A.url) == 1
    assert json.loads(capsys.readouterr().out)["execution"]["result"]["not_found"]
    assert main(["--config", str(path), "state", "workflow", "targets"]) == 0
    assert "no workflow targets found" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["list", "dry-run", "apply", "check-error", "close-error"])
def test_refresh_cli_without_image_downloads_and_only_approved_writes(tmp_path, monkeypatch, capsys, mode):
    path, config, state = setup_cli(tmp_path)
    gallery = Gallery()

    def composer(config, **kwargs):
        service = RuntimeComposer(config, **kwargs)._compose_for_workflow_plan()
        record = service.registry.records["core.generic-html"]
        service.registry.resolve = lambda *args, **kw: (record, gallery)

        async def forbidden(*args, **kwargs):
            pytest.fail("image download must not run")

        service._downloads.execute = forbidden
        if mode == "check-error":

            async def check(*args):
                raise PluginError("private-token")

            gallery.check_updates = check
        if mode == "close-error":
            close = service.close

            async def fail_close():
                await close()
                raise PluginError("private-token")

            service.close = fail_close
        return SimpleNamespace(_compose_for_workflow_plan=lambda: service)

    monkeypatch.setattr(command, "RuntimeComposer", composer)
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\nno\n"))
    args = ["targets"] if mode == "list" else ["set", "--all", "--status", "completed"]
    args += ["--source", FEED, "--refresh"]
    if mode == "dry-run":
        args += ["--dry-run"]
    status = run(path, *args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "private-token" not in captured.out + captured.err
    assert not gallery.seen
    assert not state.filesystem.exists("updates.json")
    assert not state.filesystem.exists("workflow-history.json")
    if mode in {"check-error", "close-error"}:
        assert status == 4
        assert "Apply?" not in captured.err
        if mode == "close-error":
            assert payload["execution"]["exit_code"] == 0
            assert payload["execution"]["error"] is None
    else:
        assert status == 0
    if mode == "apply":
        rows = WorkflowStateService(config).list_targets(WorkflowTargetSelector(all_targets=True))
        assert [row.candidate.url for row in rows] == [A.url]
        assert rows[0].completed
        assert payload["execution"]["result"]["summary"]["changed_records"] == 1
    else:
        assert not state.filesystem.exists("workflow.json")


def test_cancellation_after_save_starts_waits_and_reports_committed_changes(tmp_path, monkeypatch):
    path, config, _ = setup_cli(tmp_path, A)
    monkeypatch.setattr(sys, "stdin", io.StringIO("all-yes\n"))
    original = WorkflowStateService.apply_changes

    async def scenario():
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def blocked_apply(service, *args):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(30)
            return original(service, *args)

        monkeypatch.setattr(WorkflowStateService, "apply_changes", blocked_apply)
        args = build_parser().parse_args(
            [
                "--config",
                str(path),
                "--json",
                "state",
                "workflow",
                "set",
                "--all",
                "--status",
                "completed",
            ]
        )
        async with managed_task(_execute(args)) as task:
            try:
                await wait_until_started(started, task)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                release.set()
                outcome = await task
                assert outcome.exit_code == 130
                result = outcome.payload()["execution"]["result"]
                assert result["cancelled"]
                assert result["summary"]["changed_records"] == 1
            finally:
                release.set()

    asyncio.run(scenario())
    assert WorkflowStateService(config).list_targets(WorkflowTargetSelector(url=A.url))[0].completed
