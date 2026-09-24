from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import image_downloader.cli as cli
import image_downloader.commands.dispatch as cli_dispatch
import image_downloader.commands.download as cli_download
import image_downloader.commands.setup as cli_setup
from image_downloader.cli import build_parser
from image_downloader.config import AppConfig, apply_overrides
from image_downloader.exceptions import ConfigurationError
from image_downloader.models import FailureKind, ImageFailure, UpdateChangeKind


def test_legacy_url_and_explicit_download_use_the_same_handler() -> None:
    parser = build_parser()

    legacy = parser.parse_args(["https://example.test/gallery", "--json"])
    explicit = parser.parse_args(["download", "https://example.test/gallery", "--json"])

    assert legacy.command == explicit.command == "download"
    assert legacy.command_handler == explicit.command_handler == "download"
    assert legacy.url == explicit.url == "https://example.test/gallery"
    assert legacy.json_output is explicit.json_output is True


def test_each_top_level_subparser_selects_its_own_handler(tmp_path: Path) -> None:
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command_handler == "doctor"
    assert parser.parse_args(["config", "init", str((tmp_path / "app.yaml").resolve())]).command_handler == "config"
    assert parser.parse_args(["plugin", "list"]).command_handler == "plugin"
    cookie = parser.parse_args(["cookie", "export", str(tmp_path / "cookies.export")])
    assert cookie.command_handler == "cookie"
    assert cookie.cookie_action == "export"


def test_command_specific_options_are_rejected_outside_their_command() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["plugin", "list", "--host", "example.test"])

    args = parser.parse_args(["--host", "example.test", "plugin", "list"])
    with pytest.raises(ConfigurationError, match="not valid for the plugin command"):
        asyncio.run(cli.run(args))


def test_run_dispatches_explicit_and_legacy_cookie_commands(monkeypatch, tmp_path: Path) -> None:
    handled: list[str] = []

    class Handler:
        async def handle(self, args) -> int:
            handled.append(getattr(args, "command_handler", "legacy"))
            return 37

    monkeypatch.setitem(cli_dispatch._COMMAND_HANDLERS, "cookie", Handler())
    parser = build_parser()
    explicit = parser.parse_args(["cookie", "export", str(tmp_path / "cookies.export")])
    legacy = parser.parse_args(["--export-cookies", str(tmp_path / "cookies.export")])

    assert asyncio.run(cli.run(explicit)) == 37
    assert asyncio.run(cli.run(legacy)) == 37
    assert handled == ["cookie", "legacy"]


def test_config_and_plugin_argument_validation_remains_in_their_handlers() -> None:
    parser = build_parser()

    with pytest.raises(ConfigurationError, match="config command"):
        asyncio.run(cli.run(parser.parse_args(["config", "unknown"])))
    with pytest.raises(ConfigurationError, match="plugin command"):
        asyncio.run(cli.run(parser.parse_args(["plugin"])))


@pytest.mark.parametrize(
    ("saved", "skipped", "failures", "expected"),
    (
        (("saved.jpg",), (), (), cli.EXIT_SUCCESS),
        (("saved.jpg",), (), (object(),), cli.EXIT_PARTIAL),
        ((), (), (object(),), cli.EXIT_FAILURE),
    ),
)
def test_download_handler_preserves_result_exit_codes(
    monkeypatch,
    tmp_path: Path,
    saved: tuple[str, ...],
    skipped: tuple[str, ...],
    failures: tuple[object, ...],
    expected: int,
) -> None:
    closed = False

    class Service:
        async def run(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(saved_files=saved, skipped_files=skipped, failures=failures)

        async def close(self) -> None:
            nonlocal closed
            closed = True

    class Composer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def compose(self) -> Service:
            return Service()

    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )
    monkeypatch.setattr(
        cli_download,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "test"),
    )
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
    args = build_parser().parse_args(["download", "https://example.test/item"])

    assert asyncio.run(cli.run(args)) == expected
    assert closed


@pytest.mark.parametrize("list_updates", [False, True])
def test_download_json_keeps_stdout_machine_readable(monkeypatch, tmp_path: Path, capsys, list_updates: bool) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )

    def configured(args, *_unused, **_kwargs):
        return apply_overrides(config, cli_setup._app_override(args)), tmp_path, tmp_path, "test"

    class Service:
        def __init__(self, active_config: AppConfig) -> None:
            self.active_config = active_config

        async def run(self, *_args: object, **_kwargs: object) -> object:
            assert not self.active_config.logging.console.enabled
            print("plugin output")
            return SimpleNamespace(saved_files=("saved.jpg",), skipped_files=(), failures=())

        async def check_updates(self, *_args: object, **_kwargs: object) -> object:
            assert not self.active_config.logging.console.enabled
            print("plugin output")
            return SimpleNamespace(
                changes=(
                    SimpleNamespace(kind=UpdateChangeKind.ADDED, url="https://example.test/new"),
                    SimpleNamespace(kind=UpdateChangeKind.REMOVED, url="https://example.test/old"),
                )
            )

        async def close(self) -> None:
            print("plugin cleanup")

    class Composer:
        def __init__(self, active_config: AppConfig, **_kwargs: object) -> None:
            self.active_config = active_config

        def compose(self) -> Service:
            return Service(self.active_config)

    monkeypatch.setattr(cli_download, "_config_for", configured)
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
    arguments = ["download", "https://example.test/item", "--json"]
    if list_updates:
        arguments.append("--list-updated-urls")

    assert asyncio.run(cli.run(build_parser().parse_args(arguments))) == cli.EXIT_SUCCESS
    output = capsys.readouterr()
    assert "plugin output" in output.err
    assert "plugin cleanup" in output.err
    assert json.loads(output.out) == (
        {"updated_urls": ["https://example.test/new"], "removed": 1}
        if list_updates
        else {"saved": ["saved.jpg"], "skipped": [], "failures": []}
    )


def test_download_json_includes_structured_existing_file_conflict(monkeypatch, tmp_path: Path, capsys) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )
    failure = ImageFailure(
        FailureKind.SAVE,
        "ExistingFileConflictError",
        "output file already exists and existing-file=error prevents overwrite",
        code="existing_file_conflict",
        reason="output file already exists and existing-file=error prevents overwrite",
        output_path=str(tmp_path / "data" / "chapter" / "0001.jpeg"),
    )

    class Service:
        async def run(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(saved_files=("saved.jpg",), skipped_files=(), failures=(failure,))

        async def close(self) -> None:
            return None

    class Composer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def compose(self) -> Service:
            return Service()

    monkeypatch.setattr(
        cli_download,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "test"),
    )
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)

    status = asyncio.run(cli.run(build_parser().parse_args(["download", "https://example.test/item", "--json"])))
    output = json.loads(capsys.readouterr().out)

    assert status == cli.EXIT_PARTIAL
    assert output["failures"] == [
        {
            "kind": "save",
            "exception": "ExistingFileConflictError",
            "message": "output file already exists and existing-file=error prevents overwrite",
            "code": "existing_file_conflict",
            "reason": "output file already exists and existing-file=error prevents overwrite",
            "output_path": "[REDACTED]",
            "response_url": None,
            "http_status": None,
            "transport": None,
        }
    ]
