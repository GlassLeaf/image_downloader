"""Machine-readable success/error contract coverage for every CLI family."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import image_downloader.cli as cli
import image_downloader.commands.cookie as cookie_command
import image_downloader.commands.dispatch as dispatch
import image_downloader.commands.plugin as plugin_command
from image_downloader.config import AppConfig


def test_config_initialization_commands_emit_fixed_json_objects(tmp_path: Path, capsys) -> None:
    parser = cli.build_parser()
    main = (tmp_path / "config" / "app.yaml").resolve()

    assert asyncio.run(cli.run(parser.parse_args(["config", "init", str(main), "--json"]))) == cli.EXIT_SUCCESS
    assert json.loads(capsys.readouterr().out) == {"created_config": str(main)}

    assert (
        asyncio.run(cli.run(parser.parse_args(["config", "profile", "init", "work", "--config", str(main), "--json"])))
        == cli.EXIT_SUCCESS
    )
    assert json.loads(capsys.readouterr().out) == {
        "main_config": str(main),
        "created_main_config": False,
        "profile_config": str((main.parent / "profiles" / "work" / "app.yaml").resolve()),
    }


def test_profile_initialization_json_reports_when_it_created_main_config(tmp_path: Path, capsys) -> None:
    main = (tmp_path / "config" / "app.yaml").resolve()
    args = cli.build_parser().parse_args(["config", "profile", "init", "work", "--config", str(main), "--json"])

    assert asyncio.run(cli.run(args)) == cli.EXIT_SUCCESS
    assert json.loads(capsys.readouterr().out) == {
        "main_config": str(main),
        "created_main_config": True,
        "profile_config": str((main.parent / "profiles" / "work" / "app.yaml").resolve()),
    }


def test_cookie_json_never_exposes_passphrases_or_cookie_records(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[tuple[str, str, str | None]] = []

    class Store:
        def __init__(self, _filesystem: object) -> None:
            pass

        def export_file(self, path: Path, passphrase: str) -> None:
            calls.append(("export", str(path), passphrase))

        def import_file(self, path: Path, passphrase: str) -> None:
            calls.append(("import", str(path), passphrase))

        def import_browser(self, domain: str) -> None:
            calls.append(("browser-import", domain, None))

    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )
    monkeypatch.setattr(cookie_command, "CookieStore", Store)
    monkeypatch.setattr(cookie_command, "_config_for", lambda *_args, **_kwargs: (config, tmp_path, tmp_path, "test"))
    monkeypatch.setattr(cookie_command, "_persist_initial_user_config", lambda *_args: None)
    monkeypatch.setattr(cookie_command.getpass, "getpass", lambda *_args: "never-print-this-passphrase")

    destination = tmp_path / "cookies.export"
    args = cli.build_parser().parse_args(["cookie", "export", str(destination), "--json"])
    assert asyncio.run(cli.run(args)) == cli.EXIT_SUCCESS

    assert calls == [("export", str(destination), "never-print-this-passphrase")]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"operation": "cookie", "action": "export", "target": str(destination)}
    assert "passphrase" not in json.dumps(payload)


def test_json_error_omits_unavailable_optional_fields(capsys) -> None:
    assert dispatch.main(["config", "unknown", "--json"]) == cli.EXIT_CONFIGURATION

    error = json.loads(capsys.readouterr().out)["error"]
    assert {"operation", "exception", "code", "reason", "message"} <= set(error)
    assert "response_url" not in error
    assert "http_status" not in error
    assert "output_path" not in error


def test_plugin_list_applies_ephemeral_verification_override_only_to_diagnostics(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config = AppConfig.model_validate({"plugins": {"root": str((tmp_path / "plugins").resolve())}})
    observed: list[str] = []

    class Runtime:
        diagnostics = ()

        def __init__(self, _config: AppConfig, _root: Path, *, mode: str) -> None:
            observed.append(mode)

    monkeypatch.setattr(plugin_command, "_config_for", lambda *_args, **_kwargs: (config, tmp_path, tmp_path, "test"))
    monkeypatch.setattr(plugin_command, "PluginRuntime", Runtime)

    args = cli.build_parser().parse_args(["plugin", "list", "--plugin-verification-override", "bypass-all", "--json"])
    assert plugin_command.plugin_command(args) == cli.EXIT_SUCCESS

    payload = json.loads(capsys.readouterr().out)
    assert observed == ["bypass-all"]
    assert payload["plugins"] == []
    assert config.security.plugin_verification == "strict"
