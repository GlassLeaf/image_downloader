from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from image_downloader.application.workflow_reporting import history_details
from image_downloader.cli import main
from image_downloader.configuration.models import AppConfig
from image_downloader.configuration.paths import resolve_paths
from image_downloader.models import UpdateSnapshot, WorkflowResult, WorkflowRoundResult, WorkflowRunRecord
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.workflow_history import WorkflowHistoryStore

HEADER = "---\nurl: https://example.test/gallery\ntitle: test\n---\n"


@pytest.mark.parametrize("placement", ["before", "within", "after"])
def test_logs_json_options_and_no_side_effects(tmp_path, capsys, monkeypatch, placement):
    (tmp_path / "log.log").write_text(HEADER + "done\n", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("must not resolve config or compose runtime")

    monkeypatch.setattr("image_downloader.commands.verify._config_for", forbidden)
    monkeypatch.setattr("image_downloader.runtime.RuntimeComposer.compose", forbidden)
    args = ["verify", "logs", str(tmp_path)]
    args.insert({"before": 0, "within": 1, "after": 3}[placement], "--json")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "verify" and payload["resource"] == "logs"
    assert payload["status"] == "passed"
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_error_indeterminate_precedence_and_human_display(tmp_path, capsys):
    path = tmp_path / "log.log"
    path.write_text(HEADER + "error: image_save_failed (count=1)\ndone\n", encoding="utf-8")
    assert main(["verify", "logs", str(path)]) == 1
    assert "log.log:5" in capsys.readouterr().out
    path.write_text(HEADER + "error: image_save_failed (count=1)\n", encoding="utf-8")
    assert main(["verify", "logs", str(path), "--json"]) == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["logs"]["summary"]["errors"] == 1
    assert payload["logs"]["summary"]["indeterminate"] == 1


@pytest.mark.parametrize("extra", [["--profile", "test"], ["--existing-file", "skip"], ["--config", "absent"]])
def test_logs_rejects_unrelated_options(tmp_path, capsys, extra):
    assert main(["verify", "logs", str(tmp_path), *extra, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["details"]["kind"] == "option_scope"


def test_workflow_requires_run_and_output_root(tmp_path, capsys):
    assert main(["verify", "workflow", str(uuid4()), "--json"]) == 2
    json.loads(capsys.readouterr().out)
    assert main(["verify", "workflow", "missing", "--output-dir", str(tmp_path), "--json"]) == 2
    json.loads(capsys.readouterr().out)
    assert main(["verify", "logs", "relative", "--json"]) == 2
    json.loads(capsys.readouterr().out)


def test_workflow_offline_history_without_rewrite(tmp_path, capsys, monkeypatch):
    root = tmp_path / "output"
    root.mkdir()
    (root / "log.log").write_text(HEADER + "done\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    data = tmp_path / "data"
    config_path.write_text(f"storage:\n  data_root: '{data}'\n", encoding="utf-8")
    config = AppConfig.model_validate({"storage": {"data_root": str(data)}})
    now = datetime.now(UTC)
    url = "https://example.test/feed"
    snapshot = UpdateSnapshot(url, (), now)
    result = WorkflowResult(url, "updated", snapshot, (), (), rounds=(WorkflowRoundResult(0, snapshot),))
    record = WorkflowRunRecord(str(uuid4()), "test", "a" * 64, url, now, now, 0, history_details(result, root))
    store = WorkflowHistoryStore(FileSystem(resolve_paths(config)["state"]), config.workflow_history)
    store.save(record)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and not p.name.endswith(".lock")}

    def forbidden(*args, **kwargs):
        raise AssertionError("must not compose runtime")

    monkeypatch.setattr("image_downloader.runtime.RuntimeComposer.compose", forbidden)
    args = ["--config", str(config_path), "verify", "workflow", record.run_id, "--output-dir", str(root), "--json"]
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["no_targets"]
    assert payload["workflow"]["summary"]["selected"] == 0
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and not p.name.endswith(".lock")} == before
    args[args.index(record.run_id)] = str(uuid4())
    assert main(args) == 5
    assert json.loads(capsys.readouterr().out)["workflow"]["status"] == "indeterminate"


def test_workflow_corrupt_history_is_indeterminate(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    data = tmp_path / "data"
    config_path.write_text(f"storage:\n  data_root: '{data}'\n", encoding="utf-8")
    config = AppConfig.model_validate({"storage": {"data_root": str(data)}})
    fs = FileSystem(resolve_paths(config)["state"])
    fs.write_bytes_atomic("workflow-history.json", b"invalid")
    assert (
        main(
            ["verify", "workflow", str(uuid4()), "--config", str(config_path), "--output-dir", str(tmp_path), "--json"]
        )
        == 5
    )
    result = json.loads(capsys.readouterr().out)
    assert result["workflow"]["indeterminate"][0]["code"] == "workflow_history_unreadable"
