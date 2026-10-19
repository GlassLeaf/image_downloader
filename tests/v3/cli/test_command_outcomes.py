"""Completion is published once, after cleanup, without losing either phase."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from support.synchronization import managed_task, wait_until_started
from support.workflow import Gallery, compose

from image_downloader import AuthenticationError, WorkflowCancelledError, WorkflowExecutionError
from image_downloader.application.workflow_state import WorkflowStateService
from image_downloader.commands import dispatch, download, inspect, workflow
from image_downloader.commands.parser import build_parser
from image_downloader.models import (
    Chapter,
    ChapterResult,
    DownloadManifest,
    DownloadResult,
    FailureKind,
    ImageFailure,
    ImageOutcome,
    ImageOutcomeKind,
    ImageResource,
    ManifestInspectionResult,
    UpdateResult,
    WorkflowPlanResult,
    WorkflowResult,
)

URL = "https://example.test/gallery"
ROUTES = ["download", "updates", "inspect", "alias", "workflow", "plan"]


def install_service(
    tmp_path, monkeypatch, route, *, primary=None, secondary=None, close_hook=None, operation_result=None
):
    config = tmp_path / "app.yaml"
    config.write_text(
        json.dumps(
            {
                "storage": {"data_root": str(tmp_path / "data")},
                "plugins": {"root": str(tmp_path / "plugins")},
            }
        ),
        encoding="utf-8",
    )
    manifest = DownloadManifest("gallery", ())
    results = {
        "download": DownloadResult(URL, manifest, ()),
        "updates": UpdateResult(URL, "test.plugin", (), datetime.now(UTC)),
        "inspect": ManifestInspectionResult(URL, "test.plugin", manifest, False),
        "alias": ManifestInspectionResult(URL, "test.plugin", manifest, False),
        "workflow": WorkflowResult(URL, "updated", None, (), ()),
        "plan": WorkflowPlanResult(URL, "updated"),
    }
    if operation_result is not None:
        results[route] = operation_result
    closed = []

    async def execute(_request):
        print("operation diagnostic")
        if primary is not None:
            raise primary
        return results[route]

    async def close():
        print("cleanup diagnostic")
        if close_hook is not None:
            await close_hook()
        closed.append(True)
        if secondary is not None:
            raise secondary

    service = SimpleNamespace(
        download=execute,
        check_updates=execute,
        inspect=execute,
        workflow=execute,
        plan_workflow=execute,
        close=close,
    )
    composer = SimpleNamespace(
        compose=lambda: service,
        _compose_for_inspection=lambda: service,
        _compose_for_workflow_plan=lambda: service,
    )
    module = download if route in {"download", "updates"} else inspect if route in {"inspect", "alias"} else workflow
    monkeypatch.setattr(module, "RuntimeComposer", lambda *_args, **_kwargs: composer)
    command = "inspect" if route == "inspect" else "workflow" if route in {"workflow", "plan"} else "download"
    arguments = [command, URL, "--config", str(config), "--json"]
    if route == "updates":
        arguments.append("--list-updated-urls")
    if route == "alias":
        arguments.append("--inspect-only")
    if route == "plan":
        arguments.append("--dry-run")
    return arguments, closed


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("body_failed", [False, True])
@pytest.mark.parametrize("close_failed", [False, True])
def test_each_phase_survives_success_and_failure(tmp_path, monkeypatch, capsys, route, body_failed, close_failed):
    arguments, closed = install_service(
        tmp_path,
        monkeypatch,
        route,
        primary=AuthenticationError("secret-body") if body_failed else None,
        secondary=RuntimeError("secret-close") if close_failed else None,
    )
    expected = 3 if body_failed else 1 if close_failed else 0
    assert dispatch.main(arguments) == expected
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert set(payload) == {"operation", "exit_code", "execution", "shutdown"}
    assert payload["exit_code"] == expected
    execution, shutdown = payload["execution"], payload["shutdown"]
    assert execution["status"] == ("failed" if body_failed else "success")
    assert execution["exit_code"] == (3 if body_failed else 0)
    assert shutdown["status"] == ("failed" if close_failed else "success")
    assert shutdown["exit_code"] == (1 if close_failed else 0)
    assert (execution["error"] is not None) == body_failed
    assert (shutdown["error"] is not None) == close_failed
    assert (execution["result"] is not None) == (not body_failed or route == "plan")
    if route in {"workflow", "plan"} and not body_failed:
        assert execution["result"]["stop_error"] is None
        assert execution["result"]["cancelled"] is False
    assert closed == [True]
    assert "diagnostic" in captured.err
    assert "secret" not in captured.out + captured.err


@pytest.mark.parametrize("planning", [False, True])
def test_default_inspection_and_argument_failure_use_the_same_envelope(capsys, planning):
    arguments = ["download", "--inspect-only"] if planning else ["inspect"]
    assert dispatch.main(arguments) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["error"]["code"] == "argument_error"
    assert payload["shutdown"]["status"] == "not_started"


@pytest.mark.parametrize("close_failed", [False, True])
def test_close_is_collected_after_waiter_cancellation(tmp_path, monkeypatch, capsys, close_failed):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def block_close():
            started.set()
            await release.wait()

        arguments, closed = install_service(
            tmp_path,
            monkeypatch,
            "download",
            close_hook=block_close,
            secondary=RuntimeError("secret") if close_failed else None,
        )
        async with managed_task(dispatch._execute(build_parser().parse_args(arguments))) as task:
            try:
                await wait_until_started(started, task)
                assert not capsys.readouterr().out
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
            outcome = await task
        assert closed == [True]
        assert outcome.finish() == (1 if close_failed else 130)
        payload = json.loads(capsys.readouterr().out)
        assert payload["execution"]["status"] == "success"
        assert payload["shutdown"]["status"] == ("failed" if close_failed else "cancelled")
        assert payload["shutdown"]["error"] is not None if close_failed else payload["shutdown"]["error"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_workflow_wrapper_keeps_partial_result_and_cause(tmp_path, monkeypatch, capsys, cancelled):
    cause = asyncio.CancelledError() if cancelled else AuthenticationError("secret")
    partial = WorkflowResult(URL, "updated", None, (), ())
    primary = WorkflowCancelledError(partial) if cancelled else WorkflowExecutionError(partial, cause)
    primary.__cause__ = cause
    arguments, closed = install_service(tmp_path, monkeypatch, "workflow", primary=primary, secondary=RuntimeError())

    async def scenario():
        outcome = await dispatch._execute(build_parser().parse_args(arguments))
        assert outcome.primary is primary
        assert outcome.primary.__cause__ is cause
        assert outcome.finish(propagate=True) == (130 if cancelled else 3)

    asyncio.run(scenario())
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["result"] is not None
    assert payload["execution"]["status"] == ("cancelled" if cancelled else "failed")
    assert payload["shutdown"]["status"] == "failed"
    assert closed == [True]


def test_output_write_failure_does_not_write_an_error_document(monkeypatch):
    writes = []

    def broken_write(_value):
        writes.append(True)
        raise BrokenPipeError()

    monkeypatch.setattr(sys, "stdout", SimpleNamespace(write=broken_write))
    with pytest.raises(BrokenPipeError):
        dispatch.main(["config", "path", "--json"])
    assert writes == [True]


@pytest.mark.parametrize("route", ["inspect", "alias"])
def test_inspection_without_json_flag_is_also_wrapped(tmp_path, monkeypatch, capsys, route):
    arguments, _ = install_service(tmp_path, monkeypatch, route, secondary=RuntimeError())
    arguments.remove("--json")
    assert dispatch.main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["result"]["inspection_data"] == "url"
    assert payload["execution"]["status"] == "success"
    assert payload["shutdown"]["status"] == "failed"


def test_partial_download_result_keeps_its_own_exit_code(tmp_path, monkeypatch, capsys):
    image = ImageResource(URL + ".png")
    chapter = Chapter(1, "chapter", images=(image,))
    result = DownloadResult(
        URL,
        DownloadManifest("gallery", (chapter,)),
        (
            ChapterResult(
                chapter,
                (
                    ImageOutcome(image, ImageOutcomeKind.SAVED, path=str(tmp_path / "saved.png")),
                    ImageOutcome(
                        image, ImageOutcomeKind.FAILED, failure=ImageFailure(FailureKind.FETCH, "ValueError", "secret")
                    ),
                ),
            ),
        ),
    )
    arguments, _ = install_service(tmp_path, monkeypatch, "download", operation_result=result, secondary=RuntimeError())
    assert dispatch.main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["exit_code"] == 5
    assert payload["execution"]["error"] is None
    assert len(payload["execution"]["result"]["saved"]) == 1
    assert len(payload["execution"]["result"]["failures"]) == 1
    assert payload["shutdown"]["exit_code"] == 1


def test_result_formatting_failure_precedes_close_failure(tmp_path, monkeypatch, capsys):
    from image_downloader import ConfigurationError

    arguments, closed = install_service(tmp_path, monkeypatch, "download", secondary=RuntimeError())

    def broken_payload(*_args):
        raise ConfigurationError("secret")

    monkeypatch.setattr(download, "download_payload", broken_payload)
    assert dispatch.main(arguments) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["error"]["code"] == "configuration_error"
    assert payload["shutdown"]["error"]["code"] == "unexpected_runtime_error"
    assert closed == [True]


@pytest.mark.parametrize("body_failed", [False, True])
def test_public_run_still_raises_the_original_unhandled_error(tmp_path, monkeypatch, capsys, body_failed):
    body_error, close_error = AuthenticationError(), RuntimeError()
    arguments, closed = install_service(
        tmp_path,
        monkeypatch,
        "download",
        primary=body_error if body_failed else None,
        secondary=close_error,
    )
    with pytest.raises(type(body_error if body_failed else close_error)) as caught:
        asyncio.run(dispatch.run(build_parser().parse_args(arguments)))
    assert caught.value is (body_error if body_failed else close_error)
    assert closed == [True]
    assert not capsys.readouterr().out


@pytest.mark.parametrize(
    "words,shutdown",
    [
        (["config", "path"], "not_required"),
        (["config", "explain"], "not_required"),
        (["doctor"], "success"),
        (["plugin", "list"], "success"),
        (["state", "workflow", "list"], "not_required"),
    ],
)
def test_offline_command_envelopes(tmp_path, capsys, words, shutdown):
    config = tmp_path / "app.yaml"
    config.write_text(
        json.dumps(
            {
                "storage": {"data_root": str(tmp_path / "data")},
                "plugins": {"root": str(tmp_path / "plugins")},
            }
        ),
        encoding="utf-8",
    )
    config_options = [] if words == ["config", "path"] else ["--config", str(config)]
    assert dispatch.main([*words, *config_options, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"]["result"] is not None
    assert payload["shutdown"]["status"] == shutdown
    assert payload["shutdown"]["exit_code"] == (0 if shutdown == "success" else None)


def test_cancellation_during_history_correction_preserves_close_failure(tmp_path, monkeypatch, capsys):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = ()
        service = compose(tmp_path, gallery)
        started, release = asyncio.Event(), asyncio.Event()
        original_close, original_revision = service.close, workflow.revise_after_close

        async def close():
            await original_close()
            raise RuntimeError("secret")

        async def revise(*args):
            started.set()
            await release.wait()
            return await original_revision(*args)

        monkeypatch.setattr(service, "close", close)
        monkeypatch.setattr(
            workflow, "RuntimeComposer", lambda *_args, **_kwargs: SimpleNamespace(compose=lambda: service)
        )
        monkeypatch.setattr(workflow, "_config_for", lambda *_args, **_kwargs: (service.config, tmp_path, {}, None))
        monkeypatch.setattr(workflow, "_persist_initial_user_config", lambda _source: None)
        monkeypatch.setattr(workflow, "revise_after_close", revise)
        args = build_parser().parse_args(["workflow", URL, "--json"])
        async with managed_task(dispatch._execute(args)) as task:
            try:
                await wait_until_started(started, task)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
            outcome = await task
        assert outcome.finish() == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["execution"]["result"]["stop_error"] is None
        assert payload["execution"]["result"]["cancelled"] is False
        assert payload["shutdown"]["result"]["history_saved"] is True
        run = WorkflowStateService(service.config).list_runs()[0]
        assert run.exit_code == 1 and not run.details["cancelled"]
        assert not service._operation_lock.locked()
        assert service.gateway.client.is_closed and service.image_processor._closed

    asyncio.run(scenario())


def test_failed_history_correction_does_not_rewrite_the_execution_snapshot(tmp_path, monkeypatch, capsys):
    from image_downloader.storage.workflow_history import WorkflowHistoryStore

    async def scenario():
        gallery = Gallery()
        gallery.candidates = ()
        service = compose(tmp_path, gallery)
        original_close = service.close

        async def close():
            await original_close()
            raise RuntimeError("secret-close")

        def failed_revision(*_args, **_kwargs):
            raise OSError("secret-history")

        monkeypatch.setattr(service, "close", close)
        monkeypatch.setattr(
            workflow, "RuntimeComposer", lambda *_args, **_kwargs: SimpleNamespace(compose=lambda: service)
        )
        monkeypatch.setattr(workflow, "_config_for", lambda *_args, **_kwargs: (service.config, tmp_path, {}, None))
        monkeypatch.setattr(workflow, "_persist_initial_user_config", lambda _source: None)
        monkeypatch.setattr(WorkflowHistoryStore, "revise", failed_revision)
        outcome = await dispatch._execute(build_parser().parse_args(["workflow", URL, "--json"]))
        assert outcome.finish() == 1
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["execution"]["result"]["history_saved"] is True
        assert payload["execution"]["result"]["stop_error"] is None
        assert payload["shutdown"]["result"]["history_saved"] is False
        assert payload["shutdown"]["error"]["code"] == "unexpected_runtime_error"
        assert "secret" not in captured.out + captured.err
        assert "workflow history could not be saved" in captured.err
        assert WorkflowStateService(service.config).list_runs()[0].exit_code == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["interrupt", "exit"])
@pytest.mark.parametrize("mode", ["body", "close", "secondary"])
@pytest.mark.parametrize("route", ["download", "workflow", "plan"])
def test_fatal_cli_failures_are_not_json_errors(tmp_path, kind, mode, route):
    probe = """
import asyncio, json, sys
from types import SimpleNamespace
from image_downloader import AuthenticationError
from image_downloader.commands import dispatch
from image_downloader.commands import download, workflow
from image_downloader.models import DownloadManifest, DownloadResult, WorkflowResult, WorkflowPlanResult
kind, mode, route, root = sys.argv[1:]
fatal = KeyboardInterrupt("secret") if kind == "interrupt" else SystemExit(7)
closed = []
async def execute(request):
    if mode == "body": raise fatal
    if mode == "secondary": raise AuthenticationError("secret")
    if route == "download": return DownloadResult(request.url, DownloadManifest("gallery", ()), ())
    if route == "plan": return WorkflowPlanResult(request.url, "updated")
    return WorkflowResult(request.url, "updated", None, (), ())
async def close():
    closed.append(True)
    if mode != "body": raise fatal
    raise RuntimeError("secret")
service = SimpleNamespace(download=execute, workflow=execute, plan_workflow=execute, close=close)
module = download if route == "download" else workflow
module.RuntimeComposer = lambda *args, **kwargs: SimpleNamespace(
    compose=lambda: service, _compose_for_workflow_plan=lambda: service)
arguments = ["download" if route == "download" else "workflow", "https://example.test/a", "--config", root, "--json"]
if route == "plan": arguments.append("--dry-run")
try:
    status = dispatch.main(arguments)
except SystemExit as error:
    assert kind == "exit" and error.code == 7 and mode != "secondary"
else:
    assert status == (3 if mode == "secondary" else 130)
assert closed == [True]
"""
    config = tmp_path / "app.yaml"
    config.write_text(
        json.dumps(
            {
                "storage": {"data_root": str(tmp_path / "data")},
                "plugins": {"root": str(tmp_path / "plugins")},
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.run(
        [sys.executable, "-c", probe, kind, mode, route, str(config)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    if mode == "secondary":
        payload = json.loads(process.stdout)
        assert payload["execution"]["error"]["code"] == "authentication_error"
        assert payload["shutdown"] == {"status": "interrupted", "exit_code": None, "result": None, "error": None}
    else:
        assert process.stdout == ""
    assert "secret" not in process.stdout + process.stderr
