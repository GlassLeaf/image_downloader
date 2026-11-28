"""Querying uses explicit plugin settings; offline operations do not inspect them."""

from __future__ import annotations

import io
import json
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from support.configuration import write_config
from support.plugins import make_plugin
from support.workflow import FEED, A, B, snapshot

from image_downloader import AppConfig, WorkflowResult, WorkflowRoundResult, WorkflowRunRecord, WorkflowStateService
from image_downloader.application.workflow_reporting import history_details
from image_downloader.cli import main
from image_downloader.commands import setup
from image_downloader.commands.parser import build_parser
from image_downloader.commands.validation import _option_has_effect, validate_arguments
from image_downloader.configuration.paths import resolve_paths
from image_downloader.plugins.management import trust_plugin
from image_downloader.plugins.runtime import PluginRuntime
from image_downloader.storage.cookies import CookieStore
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.source_identity import source_key
from image_downloader.storage.workflow import WorkflowState
from image_downloader.storage.workflow_history import WorkflowHistoryStore

PLUGIN = "com.example.gallery"
MODES = [None, "bypass-all", "bypass-catalog", "bypass-signature"]


def target_words(action):
    return ["state", "workflow", action, "--all", *(["--status", "completed"] if action == "set" else [])]


@pytest.mark.parametrize("action", ["targets", "set", "reset"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("with_root", [False, True])
@pytest.mark.parametrize("placement", ["before", "within", "after"])
def test_query_plugin_options_are_prepared(tmp_path, action, mode, with_root, placement):
    words = [*target_words(action), "--query-targets-from-workflow-url", FEED]
    options = ["--plugin-root", str(tmp_path / "plugins")] if with_root else []
    if mode:
        options += ["--plugin-verification-override", mode]
    split = {"before": 0, "within": 2, "after": len(words)}[placement]
    command = validate_arguments(build_parser().parse_args([*words[:split], *options, *words[split:]]))
    assert command.arguments.plugin_root == (tmp_path / "plugins" if with_root else None)
    assert command.arguments.plugin_verification_override == mode


@pytest.fixture
def query_plugin(tmp_path):
    root = tmp_path / "development-plugins"
    directory = make_plugin(root, update_provider=True)
    value = {
        "storage": {"data_root": str(tmp_path / "data")},
        "plugins": {"root": str(tmp_path / "wrong-plugins")},
        "plugin_settings": {PLUGIN: {}},
    }
    config = AppConfig.model_validate(value)
    path = tmp_path / "app.yaml"
    write_config(path, value)
    return path, config, root, directory


@pytest.mark.parametrize("action", ["targets", "set", "reset"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("with_root", [False, True])
def test_query_loads_selected_plugin_and_applies_verification(
    query_plugin, monkeypatch, capsys, action, mode, with_root
):
    path, config, root, directory = query_plugin
    if mode in {None, "bypass-signature"}:
        trust_plugin(root, directory)
    if not with_root:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        value["plugins"]["root"] = str(root)
        write_config(path, value)
    state = WorkflowState(FileSystem(resolve_paths(config)["state"]))
    if action == "reset":
        state.prepare(PLUGIN, FEED, snapshot(A, B), "updated")
        for candidate in (A, B):
            state.complete(PLUGIN, FEED, candidate.url)
    original_config = path.read_bytes()
    original_catalog = (root / "catalog.json").read_bytes() if (root / "catalog.json").exists() else None
    observed = []
    original_init = PluginRuntime.__init__

    def record_mode(self, config, plugin_root, **kwargs):
        observed.append((plugin_root, kwargs["mode"]))
        original_init(self, config, plugin_root, **kwargs)

    monkeypatch.setattr(PluginRuntime, "__init__", record_mode)
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\nno\n"))
    options = ["--plugin-root", str(root)] if with_root else []
    if mode:
        options += ["--plugin-verification-override", mode]
    assert (
        main(
            [
                *options,
                "--config",
                str(path),
                "--json",
                *target_words(action),
                "--query-targets-from-workflow-url",
                FEED,
                "--download-scope",
                "all",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)["execution"]["result"]
    assert observed == [(root, mode or "strict")]
    if action == "targets":
        assert [row["url"] for row in payload["targets"]] == [A.url, B.url]
        assert all(row["plugin_id"] == PLUGIN for row in payload["targets"])
        assert "Apply?" not in output.err
        assert not state.filesystem.exists("workflow.json")
    else:
        assert payload["summary"]["changed_records"] == 1
        rows = WorkflowStateService(config).get_workflow(FEED, plugin_id=PLUGIN)[0].items
        assert rows[0].status == ("completed" if action == "set" else "pending")
        assert len(rows) == (1 if action == "set" else 2)
        if action == "reset":
            assert rows[1].status == "completed"
    assert path.read_bytes() == original_config
    if original_catalog is None:
        assert not (root / "catalog.json").exists()
    else:
        assert (root / "catalog.json").read_bytes() == original_catalog
    paths = resolve_paths(config)
    assert not paths["downloads"].exists() and not paths["logs"].exists() and not paths["cookie"].exists()
    assert not state.filesystem.exists("updates.json")
    assert not state.filesystem.exists("workflow-history.json")


@pytest.mark.parametrize("options", ["root-only", "override-only"])
def test_query_does_not_silently_replace_missing_plugin_settings(query_plugin, capsys, options):
    path, _, root, _ = query_plugin
    selected = (
        ["--plugin-root", str(root)] if options == "root-only" else ["--plugin-verification-override", "bypass-all"]
    )
    assert (
        main(
            [
                "--config",
                str(path),
                "--json",
                *selected,
                "state",
                "workflow",
                "targets",
                "--query-targets-from-workflow-url",
                FEED,
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["execution"]["error"]["code"] == "configuration_error"


def test_query_dry_run_with_plugin_options_never_prompts_or_saves(query_plugin, monkeypatch, capsys):
    path, config, root, _ = query_plugin
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("dry-run must not prompt"))
    assert (
        main(
            [
                "--config",
                str(path),
                "--json",
                "--plugin-root",
                str(root),
                "--plugin-verification-override",
                "bypass-all",
                *target_words("set"),
                "--query-targets-from-workflow-url",
                FEED,
                "--dry-run",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)["execution"]["result"]["summary"]
    assert summary["dry_run"] and summary["planned_records"] == 2 and summary["changed_records"] == 0
    assert not (resolve_paths(config)["state"] / "workflow.json").exists()


@pytest.mark.parametrize(
    "words",
    [
        ["download", FEED],
        ["download", FEED, "--list-updated-urls"],
        ["download", FEED, "--inspect-only"],
        ["inspect", FEED],
        ["workflow", FEED],
        ["workflow", FEED, "--dry-run"],
        ["doctor"],
        ["plugin", "list"],
        ["state", "workflow", "targets", "--query-targets-from-workflow-url", FEED],
    ],
)
def test_plugin_consumers_still_reject_unusable_configured_root(tmp_path, capsys, words):
    root = tmp_path / "file-as-root"
    root.write_text("not a directory", encoding="utf-8")
    path = tmp_path / "app.yaml"
    path.write_text(
        json.dumps(
            {
                "config_version": 2,
                "storage": {"data_root": str(tmp_path / "data")},
                "plugins": {"root": str(root)},
            }
        ),
        encoding="utf-8",
    )
    assert main(["--config", str(path), *words, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["execution"]["error"]["code"] == "configuration_error"


@pytest.mark.parametrize("value", ["relative/plugins", 123])
def test_offline_configuration_still_validates_root_schema(tmp_path, capsys, value):
    path = tmp_path / "app.yaml"
    path.write_text(
        json.dumps(
            {
                "config_version": 2,
                "storage": {"data_root": str(tmp_path / "data")},
                "plugins": {"root": value},
            }
        ),
        encoding="utf-8",
    )
    assert main(["--config", str(path), "state", "workflow", "targets", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["execution"]["error"]
    assert error["code"] == "configuration_error" and error["details"]["field"] == "plugins.root"


@pytest.mark.parametrize("action", ["targets", "set", "reset"])
@pytest.mark.parametrize("filtered", [False, True])
@pytest.mark.parametrize(
    "option,value",
    [
        ("--plugin-root", "relative-root"),
        ("--plugin-verification-override", "bypass-all"),
        ("--plugin-verification-override", "invalid-mode"),
        ("--download-scope", "all"),
    ],
)
def test_plugin_options_without_query_fail_before_configuration(monkeypatch, capsys, action, option, value, filtered):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid arguments must not resolve configuration or prompt")

    monkeypatch.setattr(setup, "_resolved_config_for", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)
    selector = ["--filter-workflow-url", FEED] if filtered else []
    assert main([*target_words(action), *selector, option, value, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["execution"]["error"]
    assert error["code"] == "argument_error" and error["details"]["kind"] == "option_scope"
    assert error["details"]["option"] == option
    assert "--query-targets-from-workflow-url" in error["message"]


@pytest.mark.parametrize(
    "words",
    [
        ["state", "workflow", "list"],
        ["state", "workflow", "show", FEED],
        ["state", "workflow", "history"],
        ["state", "workflow", "run", str(uuid4())],
        ["state", "workflow", "prune"],
        ["verify", "workflow", str(uuid4())],
        ["verify", "logs", "relative"],
    ],
)
@pytest.mark.parametrize(
    "option,value",
    [
        ("--plugin-root", "relative-root"),
        ("--plugin-verification-override", "bypass-all"),
    ],
)
def test_offline_option_scope_is_preserved(monkeypatch, capsys, words, option, value):
    monkeypatch.setattr(setup, "_resolved_config_for", lambda *a, **kw: pytest.fail("unexpected configuration"))
    assert main([*words, option, value, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["execution"]["error"]
    assert error["details"]["kind"] == "option_scope" and error["details"]["option"] == option


@pytest.mark.parametrize("invalid", ["mode", "relative-root", "file-root"])
def test_invalid_query_options_fail_before_configuration(tmp_path, monkeypatch, capsys, invalid):
    root_file = tmp_path / "file"
    root_file.write_text("not a directory", encoding="utf-8")
    option = "--plugin-verification-override" if invalid == "mode" else "--plugin-root"
    value = {"mode": "invalid", "relative-root": "relative", "file-root": str(root_file)}[invalid]
    monkeypatch.setattr(setup, "_resolved_config_for", lambda *a, **kw: pytest.fail("unexpected configuration"))
    assert (
        main(
            [
                "state",
                "workflow",
                "targets",
                "--query-targets-from-workflow-url",
                FEED,
                option,
                value,
                "--json",
            ]
        )
        == 2
    )
    error = json.loads(capsys.readouterr().out)["execution"]["error"]
    assert error["details"]["kind"] == "invalid_value" and error["details"]["option"] == option


@pytest.fixture(params=["missing", "file", "reparse", "inaccessible"])
def offline_config(tmp_path, monkeypatch, request):
    root = tmp_path / "unusable-plugins"
    if request.param == "file":
        root.write_text("not a directory", encoding="utf-8")
    touched = []
    original_lstat = Path.lstat

    def inspect_path(path, *args, **kwargs):
        if path == root:
            touched.append(path)
            if request.param == "inaccessible":
                raise PermissionError("artificial unavailable root")
            if request.param == "reparse":
                return SimpleNamespace(
                    st_mode=stat.S_IFDIR, st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inspect_path)
    value = {
        "storage": {"data_root": str(tmp_path / "data")},
        "plugins": {"root": str(root)},
        "plugin_settings": {PLUGIN: {}},
    }
    config = AppConfig.model_validate(value)
    path = tmp_path / "app.yaml"
    write_config(path, value)
    state = WorkflowState(FileSystem(resolve_paths(config)["state"]))
    state.prepare(PLUGIN, FEED, snapshot(A, B), "updated")
    state.complete(PLUGIN, FEED, A.url)
    now, run_id = datetime.now(UTC), str(uuid4())
    empty = snapshot()
    result = WorkflowResult(FEED, "updated", empty, (), (), rounds=(WorkflowRoundResult(0, empty),))
    output = tmp_path / "output"
    output.mkdir()
    record = WorkflowRunRecord(run_id, PLUGIN, source_key(FEED), FEED, now, now, 0, history_details(result, output))
    WorkflowHistoryStore(state.filesystem, config.workflow_history).save(record)
    (output / "log.log").write_text(
        "---\nurl: https://example.test/gallery\ntitle: test\n---\ndone\n", encoding="utf-8"
    )

    def forbidden(*args, **kwargs):
        pytest.fail("offline commands must not inspect or initialize plugins")

    monkeypatch.setattr(setup, "_plugin_root", forbidden)
    monkeypatch.setattr(PluginRuntime, "__init__", forbidden)
    # Exercise the automatic-user-config diagnostic display too.
    monkeypatch.setattr(setup, "_user_config_path", lambda: path)
    yield path, config, run_id
    assert touched == []


@pytest.mark.parametrize("action", ["list", "show", "history", "run", "prune", "targets", "set", "reset", "verify"])
def test_saved_workflow_operations_do_not_inspect_plugin_root(offline_config, tmp_path, monkeypatch, capsys, action):
    path, config, run_id = offline_config
    before = path.read_bytes()
    words = ["state", "workflow", action]
    if action == "show":
        words += [FEED]
    elif action == "run":
        words += [run_id]
    elif action in {"set", "reset"}:
        words += ["--all", *(["--status", "completed"] if action == "set" else [])]
        monkeypatch.setattr(sys, "stdin", io.StringIO("all-yes\n"))
    elif action == "verify":
        words = ["verify", "workflow", run_id, "--output-dir", str(tmp_path / "output")]
    assert main([*words, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)["execution"]["result"]
    if action in {"list", "show"}:
        assert len(payload["workflows"]) == 1
    elif action in {"history", "run"}:
        assert (payload["runs"][0] if action == "history" else payload["run"])["run_id"] == run_id
    elif action == "targets":
        assert len(payload["targets"]) == 2
    elif action in {"set", "reset"}:
        assert payload["summary"]["changed_records"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "words",
    [
        ["config", "explain"],
        ["credential", "plugin", "list"],
        ["credential", "smtp", "list"],
        ["vault", "status"],
        ["cookie", "export", "unused"],
        ["cookie", "import", "unused"],
        ["cookie", "browser-import", "example.test"],
    ],
)
def test_other_offline_operations_do_not_inspect_plugin_root(offline_config, monkeypatch, capsys, words):
    path, config, _ = offline_config
    before = path.read_bytes()
    calls = []

    def cookie_operation(self, value, *args):
        calls.append(value)

    for method in ("export_file", "import_file", "import_browser"):
        monkeypatch.setattr(CookieStore, method, cookie_operation)
    monkeypatch.setattr("getpass.getpass", lambda *args: "artificial-passphrase")
    assert main([*words, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["execution"]["result"]
    if words == ["config", "explain"]:
        assert result["effective"]["plugins"]["root"] == config.plugins.root
    assert len(calls) == (1 if words[0] == "cookie" else 0)
    assert path.read_bytes() == before


@pytest.mark.parametrize("action", ["revoke", "uninstall"])
def test_catalog_id_mutations_mark_verification_as_ineffective(action):
    args = build_parser().parse_args(["plugin", action, PLUGIN, "--plugin-verification-override", "bypass-all"])
    validate_arguments(args)
    assert not _option_has_effect("plugin-verification-override", args, "plugin " + action)


@pytest.mark.parametrize("option", ["--filter-workflow-url", "--query-targets-from-workflow-url"])
@pytest.mark.parametrize("placement", [0, 2, -1])
def test_workflow_url_options_are_prepared_independently(option, placement):
    words = ["state", "workflow", "targets"]
    split = len(words) if placement == -1 else placement
    prepared = validate_arguments(build_parser().parse_args([*words[:split], option, FEED, *words[split:]]))
    assert prepared.arguments.filter_workflow_url == (FEED if option == "--filter-workflow-url" else None)
    assert prepared.arguments.query_targets_from_workflow_url == (
        FEED if option == "--query-targets-from-workflow-url" else None
    )
    assert not hasattr(prepared.arguments, "source_url") and not hasattr(prepared.arguments, "refresh")


@pytest.mark.parametrize("workflow_input", [FEED, A.url])
@pytest.mark.parametrize("selector", [[], [A.url], ["--url-regex", "/b$"], [FEED]])
def test_query_matches_returned_urls_independently_of_workflow_input(query_plugin, capsys, workflow_input, selector):
    path, config, root, _ = query_plugin
    assert (
        main(
            [
                "--config",
                str(path),
                "--json",
                "--plugin-root",
                str(root),
                "--plugin-verification-override",
                "bypass-all",
                "state",
                "workflow",
                "targets",
                "--query-targets-from-workflow-url",
                workflow_input,
                *selector,
            ]
        )
        == 0
    )
    targets = json.loads(capsys.readouterr().out)["execution"]["result"]["targets"]
    expected = [A.url, B.url] if not selector else [A.url] if selector == [A.url] else [B.url]
    if selector == [FEED]:
        expected = []
    assert [row["url"] for row in targets] == expected
    assert not (resolve_paths(config)["state"] / "workflow.json").exists()


@pytest.mark.parametrize("scope", [None, "all"])
def test_query_keeps_updated_default_even_with_all_url_selector(query_plugin, capsys, scope):
    path, config, root, _ = query_plugin
    state = WorkflowState(FileSystem(resolve_paths(config)["state"]))
    state.prepare(PLUGIN, FEED, snapshot(A, B), "updated")
    state.complete(PLUGIN, FEED, A.url)
    before = state.filesystem.read_text("workflow.json")
    assert (
        main(
            [
                "--config",
                str(path),
                "--json",
                "--plugin-root",
                str(root),
                "--plugin-verification-override",
                "bypass-all",
                "state",
                "workflow",
                "targets",
                "--all",
                "--query-targets-from-workflow-url",
                FEED,
                *(["--download-scope", scope] if scope else []),
            ]
        )
        == 0
    )
    targets = json.loads(capsys.readouterr().out)["execution"]["result"]["targets"]
    assert [row["url"] for row in targets] == ([A.url, B.url] if scope else [B.url])
    assert state.filesystem.read_text("workflow.json") == before
