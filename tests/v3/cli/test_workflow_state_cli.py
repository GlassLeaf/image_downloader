from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from image_downloader import AppConfig, UpdateCandidate, UpdateSnapshot, UpdateStateError, WorkflowStateService
from image_downloader.cli import main
from image_downloader.configuration.paths import resolve_paths
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.workflow import WorkflowState

URL = "https://example.test/feed?token=secret"


def configuration(tmp_path):
    config_path = tmp_path / "app.yaml"
    config = AppConfig.model_validate({"storage": {"data_root": str(tmp_path / "data")}})
    config_path.write_text(f"storage:\n  data_root: '{tmp_path / 'data'}'\n", encoding="utf-8")
    return config_path, config


def test_offline_show_and_plugin_filter_without_config_rewrite(tmp_path, capsys, monkeypatch):
    config_path, config = configuration(tmp_path)
    state = WorkflowState(FileSystem(resolve_paths(config)["state"]))
    for plugin in ("test.a", "test.b"):
        state.prepare(
            plugin,
            URL,
            UpdateSnapshot(
                URL, (UpdateCandidate("https://example.test/image?key=secret", "id", "1"),), datetime.now(UTC)
            ),
            "updated",
        )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and not p.name.endswith(".lock")}

    def forbidden(*args, **kwargs):
        raise AssertionError("runtime must not be constructed")

    monkeypatch.setattr("image_downloader.runtime.RuntimeComposer.compose", forbidden)
    assert main(["state", "workflow", "show", URL, "--config", str(config_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["workflows"]) == 2
    assert payload["workflows"][0]["source_url"] is None
    assert payload["workflows"][0]["summary"]["unfinished"] == 1
    assert "secret" not in json.dumps(payload)
    assert main(["--config", str(config_path), "--json", "state", "workflow", "show", URL, "--plugin", "test.a"]) == 0
    assert len(json.loads(capsys.readouterr().out)["workflows"]) == 1
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and not p.name.endswith(".lock")}
    assert after == before


@pytest.mark.parametrize("action", [[], ["list"], ["history"], ["history", "--limit", "1"], ["prune", "--dry-run"]])
def test_empty_state_is_success_and_only_locks_are_created(tmp_path, capsys, action):
    config_path, _ = configuration(tmp_path)
    assert main(["--config", str(config_path), "state", "workflow", *action, "--json"]) == 0
    json.loads(capsys.readouterr().out)
    assert all(p == config_path or p.name.endswith(".lock") for p in tmp_path.rglob("*") if p.is_file())


@pytest.mark.parametrize("action", [["show", URL], ["history", URL], ["run", "missing"]])
def test_explicit_missing_feed_or_run_returns_one(tmp_path, capsys, action):
    config_path, _ = configuration(tmp_path)
    assert main(["state", "workflow", *action, "--config", str(config_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["not_found"]


@pytest.mark.parametrize(
    "action",
    [
        ["list", "--dry-run"],
        ["history", "--limit", "0"],
        ["list", "--limit", "2"],
        ["prune", "--plugin", "x"],
        ["list", "--workflow-retries", "0"],
        ["list", "--existing-file", "skip"],
        ["list", "--yes"],
        ["list", "--host", "example.test"],
        ["list", "--force-plugin", "x"],
        ["show"],
    ],
)
def test_invalid_state_options_return_configuration_error(tmp_path, capsys, action):
    config_path, _ = configuration(tmp_path)
    assert main(["--config", str(config_path), "state", "workflow", *action, "--json"]) == 2
    json.loads(capsys.readouterr().out)


def test_corrupt_history_returns_one_and_is_untouched(tmp_path, capsys):
    config_path, config = configuration(tmp_path)
    path = resolve_paths(config)["state"] / "workflow-history.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":2,"runs":[]}', encoding="utf-8")
    before = path.read_bytes()
    assert main(["--config", str(config_path), "state", "workflow", "history", "--json"]) == 1
    json.loads(capsys.readouterr().out)
    assert path.read_bytes() == before
    with pytest.raises(UpdateStateError):
        WorkflowStateService(config).prune_history()


@pytest.mark.parametrize("option,value", [("--selection-priority", "0"), ("--fallback-generic", "auto")])
def test_explicit_default_execution_option_is_rejected(tmp_path, capsys, option, value):
    config_path, _ = configuration(tmp_path)
    assert main([option, value, "--config", str(config_path), "state", "workflow", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"


@pytest.mark.parametrize("failure", ["storage", "cancel", "config"])
def test_cli_close_updates_same_history_run_and_preserves_exit_code(tmp_path, capsys, monkeypatch, failure):
    import asyncio
    from types import SimpleNamespace

    from image_downloader import ConfigurationError, RuntimeComposer, StorageSafetyError
    from image_downloader.commands import workflow as command

    config_path, config = configuration(tmp_path)
    run_ids = []

    async def check(url, context):
        return UpdateSnapshot(url, (), datetime.now(UTC))

    def composer(config, **kwargs):
        service = RuntimeComposer(config, **kwargs).compose()
        record = service.registry.records["core.generic-html"]
        plugin = SimpleNamespace(check_updates=check, auth_flow=lambda context: None, cleanup_after_use=lambda: None)
        service.registry.resolve = lambda *args, **kwargs: (record, plugin)
        original_close = service.close

        async def close():
            run_ids.append(WorkflowStateService(config).list_runs()[0].run_id)
            await original_close()
            raise {"storage": StorageSafetyError(), "cancel": asyncio.CancelledError(), "config": ConfigurationError()}[
                failure
            ]

        service.close = close
        return SimpleNamespace(compose=lambda: service)

    monkeypatch.setattr(command, "RuntimeComposer", composer)
    status = main(["workflow", URL, "--config", str(config_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert status == {"storage": 1, "cancel": 130, "config": 2}[failure]
    assert payload["history_saved"]
    runs = WorkflowStateService(config).list_runs()
    assert len(runs) == 1
    assert runs[0].run_id == run_ids[0] == payload["run_id"]
    assert runs[0].exit_code == status
    assert runs[0].details["cancelled"] == (failure == "cancel")
    assert "secret" not in json.dumps(payload)
    assert main(["state", "workflow", "run", runs[0].run_id, "--config", str(config_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["run"]["exit_code"] == status
