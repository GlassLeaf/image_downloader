from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from image_downloader.cli import build_parser, main
from image_downloader.commands import workflow as command
from image_downloader.exceptions import ConfigurationError, StorageSafetyError, error_info_for
from image_downloader.models import (
    Chapter,
    ChapterResult,
    DownloadManifest,
    DownloadResult,
    ImageOutcome,
    ImageOutcomeKind,
    ImageResource,
    UpdateCandidate,
    UpdateSnapshot,
    WorkflowItemResult,
    WorkflowResult,
)

URL = "https://example.test/feed"
TARGET = "https://example.test/a?token=secret"


@pytest.mark.parametrize("before", [False, True])
def test_dry_run_supported_before_and_after_command(before):
    args = build_parser().parse_args(["--dry-run", "workflow", URL] if before else ["workflow", URL, "--dry-run"])
    assert args.dry_run and args.command_handler == "workflow"


@pytest.mark.parametrize("arguments", [["download", URL], [URL], ["inspect", URL], ["doctor"], ["config", "path"]])
def test_dry_run_rejected_by_other_commands(arguments, capsys):
    assert main(["--dry-run", *arguments, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "configuration_error"


@pytest.mark.parametrize("failure", [None, "check", "cleanup", "cancel"])
def test_dry_run_cli_is_sinkless_and_emits_one_safe_document(tmp_path, monkeypatch, capsys, failure):
    from image_downloader import AppConfig, PluginError, RuntimeComposer

    config = AppConfig.model_validate(
        {"storage": {"data_root": str(tmp_path / "data")}, "plugins": {"root": str(tmp_path / "plugins")}}
    )
    services = []
    seen = []

    def configure(args, host, **kwargs):
        assert kwargs == {"rewrite_user_layers": False}
        return config, tmp_path, tmp_path / "never-created.yaml", "defaults"

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must not persist configuration or invoke downloads")

    async def check(url, context):
        print("plugin stdout must not enter JSON")
        seen.append("check")
        if failure == "check":
            raise PluginError("token=secret private failure")
        if failure == "cancel":
            raise asyncio.CancelledError()
        return UpdateSnapshot(url, (UpdateCandidate(TARGET, "a", "1"),), datetime.now(UTC))

    def cleanup():
        seen.append("cleanup")
        if failure == "cleanup":
            raise PluginError("private cleanup failure")

    def composer(config, **kwargs):
        real = RuntimeComposer(config, **kwargs)
        service = real._compose_for_workflow_plan()
        record = service.registry.records["core.generic-html"]
        plugin = SimpleNamespace(check_updates=check, auth_flow=lambda context: None, cleanup_after_use=cleanup)
        service.registry.resolve = lambda *args, **kwargs: (record, plugin)
        service.run = forbidden
        service.workflow = forbidden
        services.append(service)
        return SimpleNamespace(_compose_for_workflow_plan=lambda: service)

    monkeypatch.setattr(command, "_config_for", configure)
    monkeypatch.setattr(command, "_persist_initial_user_config", forbidden)
    monkeypatch.setattr(command, "RuntimeComposer", composer)
    status = main(["workflow", URL, "--dry-run", "--workflow-retries", "99", "--workflow-retry-delay", "600", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["operation"] == "workflow" and payload["dry_run"] is True
    assert "secret" not in captured.out
    assert "plugin stdout" in captured.err
    assert seen == ["check", "cleanup"]
    assert status == (130 if failure == "cancel" else 4 if failure else 0)
    if failure == "cleanup":
        assert len(payload["selected_urls"]) == 1
    if failure is None:
        assert payload["first_run"] is True and payload["summary"]["selected_urls"] == 1
    assert services[0]._closed
    assert all(p.suffix == ".lock" for p in tmp_path.rglob("*") if p.is_file())


def test_invalid_dry_run_options_return_plan_error_document(capsys):
    assert main(["workflow", URL, "--dry-run", "--workflow-retry-timeout", "0", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] and payload["stop_error"]["code"] == "configuration_error"


@pytest.mark.parametrize("before", [False, True])
def test_retry_options_supported_before_and_after_command(before):
    options = ["--workflow-retries", "2", "--workflow-retry-delay", "0", "--workflow-retry-timeout", "30"]
    args = build_parser().parse_args([*options, "workflow", URL] if before else ["workflow", URL, *options])
    assert (args.workflow_retries, args.workflow_retry_delay, args.workflow_retry_timeout) == (2, 0, 30)


@pytest.mark.parametrize(
    "option,value",
    [
        ("--workflow-retries", "-1"),
        ("--workflow-retry-delay", "nan"),
        ("--workflow-retry-delay", "-1"),
        ("--workflow-retry-timeout", "inf"),
        ("--workflow-retry-timeout", "0"),
    ],
)
def test_invalid_retry_options_rejected_before_runtime(option, value, capsys):
    assert main(["workflow", URL, option, value, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "configuration_error"


@pytest.mark.parametrize("option", ["--workflow-retries", "--workflow-retry-delay", "--workflow-retry-timeout"])
@pytest.mark.parametrize("command", [["download", URL], ["inspect", URL], ["doctor"], ["config", "path"]])
def test_retry_options_rejected_for_other_commands(option, command, capsys):
    assert main([option, "1", *command, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "configuration_error"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--download-scope", "all", "workflow", URL],
        ["workflow", URL, "--download-scope", "all"],
    ],
)
def test_scope_supported_before_and_after_command(arguments):
    args = build_parser().parse_args(arguments)
    assert args.command_handler == "workflow"
    assert args.download_scope == "all"


@pytest.mark.parametrize(
    "arguments",
    [
        ["download", URL],
        [URL],
        ["inspect", URL],
        ["doctor"],
        ["config", "path"],
        ["plugin", "list"],
    ],
)
def test_scope_rejected_for_other_commands(arguments, capsys):
    assert main(["--download-scope", "all", *arguments, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "configuration_error"


@pytest.mark.parametrize(
    "option",
    [
        "--list-updated-urls",
        "--inspect-only",
        "--manifest-only",
        "--export-cookies",
        "--host",
        "--selection-priority",
        "--inspection-data",
    ],
)
def test_workflow_rejects_incompatible_global_options(option, capsys, tmp_path):
    values = {
        "--export-cookies": str(tmp_path / "cookies"),
        "--host": "example.test",
        "--selection-priority": "3",
        "--inspection-data": "all",
    }
    options = [option] + ([values[option]] if option in values else [])
    assert main([*options, "workflow", URL, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["operation"] == "workflow"


def result(root: Path, *, failed=False):
    chapter = Chapter(1, "chapter")
    download = DownloadResult(
        TARGET,
        DownloadManifest("title", (chapter,)),
        (
            ChapterResult(
                chapter, (ImageOutcome(ImageResource(TARGET), ImageOutcomeKind.SAVED, str(root / "saved.png")),)
            ),
        ),
    )
    items = [WorkflowItemResult(TARGET, ("added",), "success", download)]
    if failed:
        items.append(WorkflowItemResult("https://example.test/b", ("unfinished",), "failed"))
    return WorkflowResult(
        URL, "updated", UpdateSnapshot(URL, (UpdateCandidate(TARGET),), datetime.now(UTC)), (), tuple(items)
    )


@pytest.mark.parametrize("stop", [None, "storage", "cancel", "configuration", "close"])
def test_workflow_json_is_single_safe_document_and_keeps_partial_results(tmp_path, monkeypatch, capsys, stop):
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps({"storage": {"data_root": str(tmp_path / "data")}, "plugins": {"root": str(tmp_path / "plugins")}})
    )
    captured = {}
    closed = []

    async def execute(url, **kwargs):
        captured.update(kwargs)
        print("plugin diagnostic")
        outcome = result(tmp_path)
        if stop and stop != "close":
            error = {
                "storage": StorageSafetyError(),
                "configuration": ConfigurationError(),
                "cancel": asyncio.CancelledError(),
            }[stop]
            error.workflow_result = outcome
            raise error
        return outcome

    async def close():
        closed.append(True)
        print("cleanup diagnostic")
        if stop == "close":
            raise StorageSafetyError()

    def composer(config, **kwargs):
        captured["config"] = config
        captured["composer"] = kwargs
        return SimpleNamespace(compose=lambda: SimpleNamespace(workflow=execute, close=close))

    monkeypatch.setattr(command, "RuntimeComposer", composer)
    arguments = [
        "workflow",
        URL,
        "--config",
        str(config_path),
        "--output-dir",
        str(tmp_path / "out"),
        "--existing-file",
        "skip",
        "--image-format",
        "PNG",
        "--directory-format",
        "%CONTENT_TITLE%",
        "--json",
    ]
    assert main(arguments) == {None: 0, "storage": 1, "configuration": 2, "cancel": 130, "close": 1}[stop]
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert "secret" not in output.out
    assert "diagnostic" in output.err
    assert payload["items"][0]["download"]["saved"]
    assert captured["download_scope"] == "updated"
    assert captured["config"].output.existing_file == "skip"
    assert captured["config"].output.image_format == "PNG"
    assert captured["config"].output.directory_format == "%CONTENT_TITLE%"
    assert captured["composer"]["output_root"] == tmp_path / "out"
    assert closed == [True]


def test_workflow_status_and_stop_error_serialization(tmp_path):
    assert command._status(result(tmp_path, failed=True)) == 5
    failed = WorkflowResult(
        URL, "updated", None, (), (WorkflowItemResult(TARGET, (), "failed"),), error_info_for(StorageSafetyError())
    )
    assert command._status(failed) == 1
    assert command.workflow_payload(failed, tmp_path)["stop_error"]["code"] == "storage_safety_error"


@pytest.mark.parametrize(
    "before,after",
    [
        (["--image-format", "PNG"], ["--force-image-format", "WEBP"]),
        (["--force-image-format", "PNG"], ["--image-format", "WEBP"]),
        (["--plugin", "one"], ["--force-plugin", "two"]),
        (["--force-plugin", "one"], ["--plugin", "two"]),
    ],
)
def test_exclusions_across_global_and_command_options(before, after, capsys):
    assert main([*before, "workflow", URL, *after, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "configuration_error"
