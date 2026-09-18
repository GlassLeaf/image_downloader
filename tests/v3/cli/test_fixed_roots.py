from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path

import pytest
import yaml

import image_downloader.cli as cli
import image_downloader.commands.download as cli_download
import image_downloader.commands.plugin as cli_plugin
import image_downloader.commands.setup as cli_setup
from image_downloader.cli import EXIT_CONFIGURATION, EXIT_SUCCESS, build_parser, doctor
from image_downloader.config import AppConfig, apply_overrides, load_application_config
from image_downloader.exceptions import ConfigurationError, StorageSafetyError
from image_downloader.storage import FileSystem


def _user_config(path: Path, *, verification: str = "strict") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        (
            "storage:",
            f"  data_root: {(path.parent / 'data').resolve()}",
            "plugins:",
            f"  root: {(path.parent / 'plugins').resolve()}",
            "security:",
            f"  plugin_verification: '{verification}'",
            "",
        )
    )
    path.write_text(
        content,
        encoding="utf-8",
    )


class _InteractiveInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_fixed_user_config_ignores_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "fixed" / "conf" / "app.yaml"
    _user_config(config)
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: config)
    reports: list[dict[str, object]] = []
    for cwd in (tmp_path / "one", tmp_path / "two"):
        cwd.mkdir()
        (cwd / "app.yaml").write_text("storage: {data_root: C:\\\\ignored}\n", encoding="utf-8")
        monkeypatch.chdir(cwd)
        args = build_parser().parse_args(["doctor", "--json"])
        assert asyncio.run(doctor(args)) == EXIT_SUCCESS
        reports.append(json.loads(capsys.readouterr().out))

    assert reports[0]["configuration"]["config_file"] == str(config.resolve())
    assert reports[0]["configuration"]["source"] == "user"
    assert reports[0]["paths"] == reports[1]["paths"]
    assert reports[0]["plugin_root"] == reports[1]["plugin_root"]


def test_bundled_fallback_uses_cli_roots_without_creating_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    data_root = (tmp_path / "temporary-data").resolve()
    plugin_root = (tmp_path / "temporary-plugins").resolve()
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)

    args = build_parser().parse_args(
        ["doctor", "--data-root", str(data_root), "--plugin-root", str(plugin_root), "--json"]
    )

    assert asyncio.run(doctor(args)) == EXIT_SUCCESS
    report = json.loads(capsys.readouterr().out)
    assert report["configuration"]["source"] == "bundled"
    assert report["configuration"]["config_file"] == str(cli_setup._bundled_config_path().resolve())
    assert report["paths"]["profile"].startswith(str(data_root))
    assert report["plugin_root"] == str(plugin_root)
    assert not user_config.exists()


def test_url_and_plugin_commands_accept_bundled_fallback_roots_without_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    data_root = (tmp_path / "temporary-data").resolve()
    plugin_root = (tmp_path / "temporary-plugins").resolve()
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    sources: list[str] = []
    original_config_for = cli_setup._config_for

    def record_config_source(*args: object, **kwargs: object) -> tuple[AppConfig, Path, Path, str]:
        result = original_config_for(*args, **kwargs)
        sources.append(result[3])
        return result

    class FakeService:
        async def run(self, url: str, **_: object) -> object:
            return type("Result", (), {"saved_files": (), "skipped_files": (), "failures": ()})()

        async def close(self) -> None:
            return None

    class FakeComposer:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def compose(self) -> FakeService:
            return FakeService()

    monkeypatch.setattr(cli_download, "_config_for", record_config_source)
    monkeypatch.setattr(cli_plugin, "_config_for", record_config_source)
    monkeypatch.setattr(cli_download, "RuntimeComposer", FakeComposer)
    command = ["--data-root", str(data_root), "--plugin-root", str(plugin_root)]

    assert asyncio.run(cli.run(build_parser().parse_args(["https://example.test/item", *command]))) == EXIT_SUCCESS
    assert asyncio.run(cli.run(build_parser().parse_args(["plugin", "list", *command]))) == EXIT_SUCCESS
    assert sources == ["bundled", "bundled"]
    assert not user_config.exists()


def test_interactive_root_setup_creates_user_config_and_honors_partial_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    data_root = (tmp_path / "chosen-data").resolve()
    plugins_root = (tmp_path / "chosen-plugins").resolve()
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput(f"{plugins_root}\nyes\n"))

    args = build_parser().parse_args(["doctor", "--data-root", str(data_root)])

    assert asyncio.run(doctor(args)) == EXIT_SUCCESS
    value = load_application_config(user_config)
    assert value.storage.data_root == str(data_root)
    assert value.plugins.root == str(plugins_root)


def test_interactive_root_setup_repairs_user_config_without_losing_other_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        "storage: {data_root: relative-data}\nplugins: {root: relative-plugins}\nnetwork: {max_retries: 9}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    data_root = (tmp_path / "chosen-data").resolve()
    plugins_root = (tmp_path / "chosen-plugins").resolve()
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput(f"{data_root}\n{plugins_root}\ny\n"))

    assert asyncio.run(doctor(build_parser().parse_args(["doctor"]))) == EXIT_SUCCESS
    value = load_application_config(user_config)
    assert value.storage.data_root == str(data_root)
    assert value.plugins.root == str(plugins_root)
    assert value.network.max_retries == 9


def test_interactive_root_setup_retries_invalid_input_and_keeps_roots_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    data_root = (tmp_path / "chosen-data").resolve()
    plugins_root = (tmp_path / "chosen-plugins").resolve()
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(
        cli_setup.sys,
        "stdin",
        _InteractiveInput(f"relative-data\n{data_root}\n{plugins_root}\nn\n"),
    )

    assert asyncio.run(doctor(build_parser().parse_args(["doctor"]))) == EXIT_SUCCESS
    assert "Invalid Data root" in capsys.readouterr().err
    assert not user_config.exists()


def test_profile_init_keeps_root_settings_temporary_when_save_is_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "user" / "conf" / "app.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("storage: {data_root: null}\nplugins: {root: null}\n", encoding="utf-8")
    data_root = (tmp_path / "temporary-data").resolve()
    plugins_root = (tmp_path / "temporary-plugins").resolve()
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: config)
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput(f"{data_root}\n{plugins_root}\nn\n"))

    assert asyncio.run(cli.run(build_parser().parse_args(["config", "profile", "init", "work"]))) == EXIT_SUCCESS
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["storage"]["data_root"] is None
    assert (config.parent / "profiles" / "work" / "app.yaml").is_file()


def test_profile_init_saves_interactive_roots_to_explicit_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = (tmp_path / "custom" / "app.yaml").resolve()
    config.parent.mkdir()
    config.write_text("storage: {data_root: null}\nplugins: {root: null}\n", encoding="utf-8")
    data_root = (tmp_path / "chosen-data").resolve()
    plugins_root = (tmp_path / "chosen-plugins").resolve()
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput(f"{data_root}\n{plugins_root}\ny\n"))

    args = build_parser().parse_args(["config", "profile", "init", "work", "--config", str(config)])

    assert asyncio.run(cli.run(args)) == EXIT_SUCCESS
    value = load_application_config(config)
    assert value.storage.data_root == str(data_root)
    assert value.plugins.root == str(plugins_root)
    assert (config.parent / "profiles" / "work" / "app.yaml").is_file()


def test_root_save_decline_uses_temporary_settings_and_noninteractive_cases_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)

    data_root = (tmp_path / "temporary-data").resolve()
    plugins_root = (tmp_path / "temporary-plugins").resolve()
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput(f"{data_root}\n{plugins_root}\nn\n"))
    assert asyncio.run(doctor(build_parser().parse_args(["doctor"]))) == EXIT_SUCCESS
    assert not user_config.exists()

    monkeypatch.setattr(cli_setup.sys, "stdin", io.StringIO())
    assert asyncio.run(doctor(build_parser().parse_args(["doctor"]))) == EXIT_CONFIGURATION
    assert not user_config.exists()

    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput("y\n"))

    assert asyncio.run(doctor(build_parser().parse_args(["doctor", "--json"]))) == EXIT_CONFIGURATION
    assert not user_config.exists()

    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("storage: {data_root: null}\nplugins: {root: null}\n", encoding="utf-8")
    assert asyncio.run(doctor(build_parser().parse_args(["doctor", "--config", str(explicit)]))) == EXIT_CONFIGURATION
    assert yaml.safe_load(explicit.read_text(encoding="utf-8"))["storage"]["data_root"] is None

    data_root = (tmp_path / "explicit-data").resolve()
    plugin_root = (tmp_path / "explicit-plugins").resolve()
    args = build_parser().parse_args(
        ["doctor", "--config", str(explicit), "--data-root", str(data_root), "--plugin-root", str(plugin_root)]
    )
    assert asyncio.run(doctor(args)) == EXIT_SUCCESS


def test_unsafe_absolute_root_is_not_replaced_by_interactive_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    user_config.parent.mkdir(parents=True)
    unsafe = tmp_path / "not-a-directory"
    unsafe.write_text("not a directory", encoding="utf-8")
    user_config.write_text(
        f"storage:\n  data_root: {unsafe}\nplugins:\n  root: null\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput("y\n"))

    assert asyncio.run(doctor(build_parser().parse_args(["doctor"]))) == EXIT_CONFIGURATION
    assert yaml.safe_load(user_config.read_text(encoding="utf-8"))["plugins"]["root"] is None


def test_config_profile_init_without_a_saved_base_stops_after_root_save_decline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    data_root = (tmp_path / "temporary-data").resolve()
    plugins_root = (tmp_path / "temporary-plugins").resolve()
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput(f"{data_root}\n{plugins_root}\nn\n"))

    args = build_parser().parse_args(["config", "profile", "init", "work"])

    with pytest.raises(ConfigurationError, match="without a saved user app.yaml"):
        asyncio.run(cli.run(args))
    assert not user_config.exists()
    assert not (user_config.parent / "profiles" / "work" / "app.yaml").exists()


def test_config_and_plugin_override_paths_must_be_absolute(tmp_path: Path) -> None:
    relative = build_parser().parse_args(["doctor", "--config", "app.yaml"])
    assert asyncio.run(doctor(relative)) == EXIT_CONFIGURATION

    config = tmp_path / "app.yaml"
    _user_config(config)
    relative_plugin = build_parser().parse_args(["doctor", "--config", str(config), "--plugin-root", "plugins"])
    assert asyncio.run(doctor(relative_plugin)) == EXIT_CONFIGURATION


def test_bootstrap_settings_are_rejected_in_overlays(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    _user_config(main)
    overlay = tmp_path / "profiles" / "default" / "app.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("storage:\n  data_root: C:\\\\other\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="bootstrap settings"):
        load_application_config(main)


def test_security_is_allowed_in_profile_app_but_not_site_overlay(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    _user_config(main)
    profile = tmp_path / "profiles" / "default" / "app.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("security: {plugin_verification: warn}\n", encoding="utf-8")
    assert load_application_config(main).security.plugin_verification == "warn"

    site = tmp_path / "sites" / "global.yaml"
    site.parent.mkdir()
    site.write_text("security: {plugin_verification: off}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="bootstrap settings"):
        load_application_config(main)


def test_config_link_is_not_treated_as_a_missing_or_valid_configuration(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    _user_config(target)
    link = tmp_path / "app.yaml"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(ConfigurationError, match="symbolic link or reparse point"):
        load_application_config(link, require_config=True)


def test_profile_cannot_be_changed_by_post_selection_overrides(tmp_path: Path) -> None:
    main = tmp_path / "app.yaml"
    _user_config(main)

    with pytest.raises(ConfigurationError, match="profile cannot be overridden"):
        load_application_config(main, runtime_override={"profile": {}})
    with pytest.raises(ConfigurationError, match="profile cannot be overridden"):
        apply_overrides(AppConfig(), {"profile": {"default": "work"}})

    assert load_application_config(main, runtime_override={"network": {"max_retries": 7}}).network.max_retries == 7


def test_plugin_config_file_requires_an_absolute_regular_file(tmp_path: Path) -> None:
    config = tmp_path / "plugin-config.json"
    config.write_text(
        '{"plugin_id":"com.example.gallery","config":{"source":"file","nested":{"one":1}}}',
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            "--plugin-config-file",
            str(config),
            "--plugin-config",
            'com.example.gallery={"nested":{"two":2}}',
        ]
    )
    assert cli_setup._runtime_overrides(args) == {
        "com.example.gallery": {"source": "file", "nested": {"one": 1, "two": 2}}
    }

    for unsafe in (Path("relative.json"), tmp_path / "missing.json", tmp_path):
        unsafe_args = build_parser().parse_args(["--plugin-config-file", str(unsafe)])
        with pytest.raises(ConfigurationError):
            cli_setup._runtime_overrides(unsafe_args)


def test_plugin_config_file_rejects_link_leaf_and_ancestor(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"plugin_id":"com.example.gallery","config":{}}', encoding="utf-8")
    leaf_link = tmp_path / "leaf-link.json"
    ancestor_link = tmp_path / "linked-directory"
    try:
        leaf_link.symlink_to(source)
        ancestor_link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    for unsafe in (leaf_link, ancestor_link / source.name):
        args = build_parser().parse_args(["--plugin-config-file", str(unsafe)])
        with pytest.raises(ConfigurationError, match="symbolic link or reparse point"):
            cli_setup._runtime_overrides(args)


def test_filesystem_rejects_an_existing_hard_link(tmp_path: Path) -> None:
    filesystem = FileSystem(tmp_path / "root")
    filesystem.write_bytes_atomic("log.log", b"safe")
    linked = tmp_path / "root" / "linked.log"
    try:
        os.link(tmp_path / "root" / "log.log", linked)
    except OSError:
        pytest.skip("hard links are unavailable in this environment")

    with pytest.raises(StorageSafetyError):
        filesystem.exists("log.log")
    with pytest.raises(StorageSafetyError):
        filesystem.open_text_append("linked.log")


def test_off_requires_per_run_acknowledgement(tmp_path: Path) -> None:
    config = tmp_path / "app.yaml"
    _user_config(config, verification="off")
    args = build_parser().parse_args(["doctor", "--config", str(config)])
    assert asyncio.run(doctor(args)) == EXIT_CONFIGURATION

    allowed = build_parser().parse_args(["doctor", "--config", str(config), "--allow-unverified-plugins"])
    assert asyncio.run(doctor(allowed)) == EXIT_SUCCESS


def test_config_init_creates_explicit_absolute_roots_with_yes(tmp_path: Path) -> None:
    config = (tmp_path / "conf" / "app.yaml").resolve()
    data = (tmp_path / "data").resolve()
    plugins = (tmp_path / "plugins").resolve()
    args = build_parser().parse_args(
        ["config", "init", str(config), "--data-root", str(data), "--plugin-root", str(plugins), "--yes"]
    )

    assert asyncio.run(cli.run(args)) == EXIT_SUCCESS
    value = load_application_config(config)
    assert value.storage.data_root == str(data)
    assert value.plugins.root == str(plugins)
    assert set(yaml.safe_load(config.read_text(encoding="utf-8"))) == set(
        AppConfig().model_dump(by_alias=True, warnings=False)
    )


def test_config_init_prompts_for_missing_roots_and_prints_yaml_when_not_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = (tmp_path / "conf" / "app.yaml").resolve()
    data_root = (tmp_path / "chosen-data").resolve()
    plugins_root = (tmp_path / "chosen-plugins").resolve()
    monkeypatch.setattr(cli_setup.sys, "stdin", _InteractiveInput(f"{data_root}\n{plugins_root}\nn\n"))

    assert asyncio.run(cli.run(build_parser().parse_args(["config", "init", str(config)]))) == EXIT_SUCCESS
    rendered = yaml.safe_load(capsys.readouterr().out)
    assert rendered["storage"]["data_root"] == str(data_root)
    assert rendered["plugins"]["root"] == str(plugins_root)
    assert not config.exists()


def test_config_init_with_complete_options_prints_yaml_without_yes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = (tmp_path / "conf" / "app.yaml").resolve()
    data_root = (tmp_path / "chosen-data").resolve()
    plugins_root = (tmp_path / "chosen-plugins").resolve()
    args = build_parser().parse_args(
        ["config", "init", str(config), "--data-root", str(data_root), "--plugin-root", str(plugins_root)]
    )

    assert asyncio.run(cli.run(args)) == EXIT_SUCCESS
    assert yaml.safe_load(capsys.readouterr().out)["storage"]["data_root"] == str(data_root)
    assert not config.exists()


def test_config_profile_init_creates_a_safe_empty_overlay(tmp_path: Path) -> None:
    config = (tmp_path / "conf" / "app.yaml").resolve()
    _user_config(config)
    args = build_parser().parse_args(["config", "profile", "init", "work", "--config", str(config)])

    assert asyncio.run(cli.run(args)) == EXIT_SUCCESS
    overlay = config.parent / "profiles" / "work" / "app.yaml"
    assert overlay.read_text(encoding="utf-8").endswith("{}\n")
    assert load_application_config(config, profile="work").profile.default == "work"


def test_bundled_baseline_is_available() -> None:
    assert cli_setup._bundled_config_path().is_file()


def test_doctor_never_creates_a_missing_explicit_configuration(tmp_path: Path) -> None:
    config = (tmp_path / "conf" / "app.yaml").resolve()
    declined = build_parser().parse_args(["doctor", "--config", str(config)])
    assert asyncio.run(doctor(declined)) == EXIT_CONFIGURATION
    assert not config.exists()

    accepted = build_parser().parse_args(["doctor", "--config", str(config), "--yes"])
    assert asyncio.run(doctor(accepted)) == EXIT_CONFIGURATION
    assert not config.exists()
