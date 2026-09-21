from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import image_downloader.cli as cli
import image_downloader.commands.cookie as cli_cookie
import image_downloader.commands.download as cli_download
import image_downloader.commands.plugin as cli_plugin
import image_downloader.commands.setup as cli_setup
from image_downloader.cli import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS, build_parser
from image_downloader.config import AppConfig, resolve_application_config
from image_downloader.exceptions import ConfigurationError
from image_downloader.immutable import thaw_json


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )


def _snapshot() -> dict[str, object]:
    resolved = resolve_application_config(None, source="defaults")
    value = thaw_json(resolved.config.model_dump(by_alias=True, warnings=False))
    assert isinstance(value, dict)
    return value


def test_persist_initial_user_config_writes_complete_bundled_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)

    cli_setup._persist_initial_user_config("defaults")

    assert yaml.safe_load(user_config.read_text(encoding="utf-8")) == _snapshot()
    assert "Generated from the bundled app.yaml" in user_config.read_text(encoding="utf-8")
    assert "Created user configuration" in capsys.readouterr().err


def test_initial_persistence_never_overwrites_or_persists_non_default_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)

    cli_setup._persist_initial_user_config("user")
    assert not user_config.exists()
    user_config.parent.mkdir(parents=True)
    user_config.write_text("sentinel: keep\n", encoding="utf-8")
    cli_setup._persist_initial_user_config("defaults")

    assert user_config.read_text(encoding="utf-8") == "sentinel: keep\n"


def test_initial_persistence_treats_a_create_race_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)

    def concurrent_writer(destination: Path, *_: object) -> None:
        destination.parent.mkdir(parents=True)
        destination.write_text("winner: true\n", encoding="utf-8")
        raise ConfigurationError("configuration file already exists")

    monkeypatch.setattr(cli_setup, "_write_initial_user_config", concurrent_writer)
    cli_setup._persist_initial_user_config("defaults")

    assert yaml.safe_load(user_config.read_text(encoding="utf-8")) == {"winner": True}
    assert "warning:" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("saved", "failures", "expected"),
    (
        (("saved.jpg",), (), EXIT_SUCCESS),
        (("saved.jpg",), (object(),), EXIT_PARTIAL),
        ((), (object(),), EXIT_FAILURE),
    ),
)
def test_download_persists_before_main_operation_regardless_of_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    saved: tuple[str, ...],
    failures: tuple[object, ...],
    expected: int,
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    config = _config(tmp_path)
    closed = False

    class Service:
        async def run(self, *_: object, **__: object) -> object:
            return SimpleNamespace(saved_files=saved, skipped_files=(), failures=failures)

        async def close(self) -> None:
            nonlocal closed
            assert user_config.exists()
            closed = True

    class Composer:
        def __init__(self, *_: object, **__: object) -> None:
            assert user_config.exists()

        def compose(self) -> Service:
            return Service()

    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(
        cli_download,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "defaults"),
    )
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)

    result = asyncio.run(cli.run(build_parser().parse_args(["download", "https://example.test/item"])))

    assert result == expected
    assert closed
    assert user_config.exists()
    assert yaml.safe_load(user_config.read_text(encoding="utf-8")) == _snapshot()


def test_download_keeps_success_and_json_when_initial_config_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    config = _config(tmp_path)

    class Service:
        async def run(self, *_: object, **__: object) -> object:
            return SimpleNamespace(saved_files=("saved.jpg",), skipped_files=(), failures=())

        async def close(self) -> None:
            return None

    class Composer:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def compose(self) -> Service:
            return Service()

    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(
        cli_download,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "defaults"),
    )
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
    monkeypatch.setattr(
        cli_setup,
        "_write_initial_user_config",
        lambda *_args: (_ for _ in ()).throw(OSError("permission denied")),
    )

    args = build_parser().parse_args(["download", "https://example.test/item", "--json"])
    assert asyncio.run(cli.run(args)) == EXIT_SUCCESS
    output = capsys.readouterr()
    assert json.loads(output.out) == {"saved": ["saved.jpg"], "skipped": [], "failures": []}
    assert "warning: could not create initial user configuration: permission denied" in output.err
    assert not user_config.exists()


def test_download_exception_keeps_initial_user_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    config = _config(tmp_path)

    class Service:
        async def run(self, *_: object, **__: object) -> object:
            raise RuntimeError("request failed")

        async def close(self) -> None:
            return None

    class Composer:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def compose(self) -> Service:
            return Service()

    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(
        cli_download,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "defaults"),
    )
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)

    args = build_parser().parse_args(["download", "https://example.test/item"])
    with pytest.raises(RuntimeError, match="request failed"):
        asyncio.run(cli.run(args))
    assert yaml.safe_load(user_config.read_text(encoding="utf-8")) == _snapshot()


def test_cookie_export_creates_initial_user_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    data_root = (tmp_path / "data").resolve()
    plugin_root = (tmp_path / "plugins").resolve()
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(cli_cookie.getpass, "getpass", lambda _prompt: "passphrase")

    def export_file(*_args: object) -> None:
        assert user_config.exists()

    monkeypatch.setattr(cli_cookie.CookieStore, "export_file", export_file)

    args = build_parser().parse_args(
        [
            "cookie",
            "export",
            str(tmp_path / "cookies.export"),
            "--data-root",
            str(data_root),
            "--plugin-root",
            str(plugin_root),
        ]
    )

    assert asyncio.run(cli.run(args)) == EXIT_SUCCESS
    snapshot = yaml.safe_load(user_config.read_text(encoding="utf-8"))
    assert snapshot == _snapshot()
    assert snapshot["storage"]["data_root"] != str(data_root)
    assert snapshot["plugins"]["root"] != str(plugin_root)


@pytest.mark.parametrize("command", ("install", "trust", "revoke"))
def test_plugin_mutations_create_initial_user_configuration(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    config = _config(tmp_path)
    entry = SimpleNamespace(id="com.example.gallery", version="1.0.0")
    entry.as_json = lambda: {"id": entry.id, "version": entry.version}
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(
        cli_plugin,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "defaults"),
    )
    monkeypatch.setattr(cli_plugin, "_plugin_root", lambda *_args: (tmp_path / "plugins").resolve())

    def mutate_plugin(*_args: object, **_kwargs: object) -> object:
        assert user_config.exists()
        return entry

    monkeypatch.setattr(cli_plugin, "install_plugin", mutate_plugin)
    monkeypatch.setattr(cli_plugin, "trust_plugin", mutate_plugin)
    monkeypatch.setattr(cli_plugin, "revoke_plugin", mutate_plugin)

    if command in {"install", "trust"}:
        manifest = SimpleNamespace(
            id=entry.id,
            kind="site_plugin",
            value={"key_id": "key", "publisher": "publisher", "version": entry.version, "file_tree_sha256": "tree"},
        )
        monkeypatch.setattr(cli_plugin, "read_manifest", lambda _path: manifest)
        words = ["plugin", command, str((tmp_path / "source").resolve()), "--yes"]
    else:
        words = ["plugin", "revoke", entry.id, "--yes"]

    assert asyncio.run(cli.run(build_parser().parse_args(words))) == EXIT_SUCCESS
    assert yaml.safe_load(user_config.read_text(encoding="utf-8")) == _snapshot()


def test_plugin_list_and_missing_explicit_config_do_not_create_user_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    data_root = (tmp_path / "data").resolve()
    plugin_root = (tmp_path / "plugins").resolve()
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)

    list_args = build_parser().parse_args(
        ["plugin", "list", "--data-root", str(data_root), "--plugin-root", str(plugin_root)]
    )
    assert asyncio.run(cli.run(list_args)) == EXIT_SUCCESS
    assert not user_config.exists()
    explicit = (tmp_path / "explicit" / "app.yaml").resolve()
    download_args = build_parser().parse_args(["download", "https://example.test/item", "--config", str(explicit)])
    with pytest.raises(ConfigurationError, match="configuration file not found"):
        asyncio.run(cli.run(download_args))
    assert not explicit.exists()


def test_unconfirmed_plugin_mutation_does_not_create_initial_user_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_config = tmp_path / "user" / "conf" / "app.yaml"
    monkeypatch.setattr(cli_setup, "_user_config_path", lambda: user_config)
    monkeypatch.setattr(cli_plugin.sys, "stdin", type("Input", (), {"isatty": lambda _self: False})())

    args = build_parser().parse_args(["plugin", "revoke", "com.example.gallery"])
    with pytest.raises(ConfigurationError, match="not confirmed"):
        asyncio.run(cli.run(args))

    assert not user_config.exists()
