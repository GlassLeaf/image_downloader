from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import image_downloader.cli as cli
import image_downloader.commands.dispatch as cli_dispatch
import image_downloader.commands.download as cli_download
import image_downloader.commands.inspect as cli_inspect
import image_downloader.commands.setup as cli_setup
from image_downloader.cli import build_parser
from image_downloader.config import AppConfig, apply_overrides
from image_downloader.exceptions import ConfigurationError
from image_downloader.models import (
    Chapter,
    DownloadManifest,
    EffectiveRequestPreview,
    FailureKind,
    ImageFailure,
    ImageRequestResolution,
    ImageRequestResolutionFailure,
    ImageRequestResolutionStatus,
    ImageResource,
    ManifestInspectionResult,
    RequestSpec,
    TransportCookie,
    TransportHeader,
    UpdateChangeKind,
)


def test_legacy_url_and_explicit_download_use_the_same_handler() -> None:
    parser = build_parser()

    legacy = parser.parse_args(["https://example.test/gallery", "--json"])
    explicit = parser.parse_args(["download", "https://example.test/gallery", "--json"])

    assert legacy.command == explicit.command == "download"
    assert legacy.command_handler == explicit.command_handler == "download"
    assert legacy.url == explicit.url == "https://example.test/gallery"
    assert legacy.json_output is explicit.json_output is True


def test_output_options_support_explicit_and_bare_url_downloads(tmp_path: Path) -> None:
    parser = build_parser()
    output_dir = (tmp_path / "output").resolve()

    explicit = parser.parse_args(
        [
            "download",
            "https://example.test/gallery",
            "--output-dir",
            str(output_dir),
            "--directory-format",
            "destination_%CHAPTER_NUMBER%",
        ]
    )
    bare = parser.parse_args(
        [
            "--output-dir",
            str(output_dir),
            "--directory-format",
            "destination_%CHAPTER_NUMBER%",
            "https://example.test/gallery",
        ]
    )

    assert explicit.command_handler == bare.command_handler == "download"
    assert explicit.url == bare.url == "https://example.test/gallery"
    assert explicit.output_dir == bare.output_dir == output_dir
    assert explicit.directory_format == bare.directory_format == "destination_%CHAPTER_NUMBER%"


def test_directory_format_is_a_nonpersistent_runtime_override(tmp_path: Path) -> None:
    config = AppConfig.model_validate({"output": {"directory_format": "from_yaml"}})
    args = build_parser().parse_args(
        ["download", "https://example.test/gallery", "--directory-format", "destination_%CHAPTER_NUMBER%"]
    )

    overridden = apply_overrides(config, cli_setup._app_override(args))

    assert overridden.output.directory_format == "destination_%CHAPTER_NUMBER%"
    assert config.output.directory_format == "from_yaml"

    invalid = build_parser().parse_args(
        ["download", "https://example.test/gallery", "--directory-format", "%IMAGE_INDEX%"]
    )
    with pytest.raises(ConfigurationError, match="image-only tokens"):
        apply_overrides(config, cli_setup._app_override(invalid))


def test_output_dir_requires_an_absolute_safe_directory() -> None:
    args = build_parser().parse_args(["download", "https://example.test/gallery", "--output-dir", "relative-output"])

    with pytest.raises(ConfigurationError, match="--output-dir must be an absolute path"):
        cli_setup._output_root(args)


@pytest.mark.parametrize(
    "arguments",
    (
        ("inspect", "https://example.test/gallery"),
        ("doctor",),
        ("config", "path"),
        ("plugin", "list"),
        ("cookie", "export", "cookies.export"),
    ),
)
def test_output_options_are_rejected_outside_download(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    args = build_parser().parse_args(
        ["--output-dir", str((tmp_path / "output").resolve()), "--directory-format", "destination", *arguments]
    )

    with pytest.raises(ConfigurationError, match="output-dir|directory-format"):
        asyncio.run(cli.run(args))


def test_output_options_are_ignored_for_non_saving_download_modes(monkeypatch, tmp_path: Path) -> None:
    parser = build_parser()
    ignored = parser.parse_args(
        [
            "download",
            "https://example.test/gallery",
            "--inspect-only",
            "--output-dir",
            "relative-output-is-ignored",
            "--directory-format",
            "%IMAGE_INDEX%",
        ]
    )
    monkeypatch.setattr(cli_download, "inspect_command", lambda _args: asyncio.sleep(0, result=cli.EXIT_SUCCESS))

    assert asyncio.run(cli.run(ignored)) == cli.EXIT_SUCCESS
    assert "directory_format" not in cli_setup._app_override(ignored).get("output", {})

    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )
    composer_calls: list[dict[str, object]] = []

    class Service:
        async def check_updates(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(changes=())

        async def close(self) -> None:
            return None

    class Composer:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            composer_calls.append(dict(kwargs))

        def compose(self) -> Service:
            return Service()

    monkeypatch.setattr(
        cli_download,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "test"),
    )
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
    updates = parser.parse_args(
        [
            "download",
            "https://example.test/gallery",
            "--list-updated-urls",
            "--output-dir",
            "relative-output-is-ignored",
            "--directory-format",
            "%IMAGE_INDEX%",
        ]
    )

    assert asyncio.run(cli.run(updates)) == cli.EXIT_SUCCESS
    assert composer_calls == [
        {
            "config_root": tmp_path.resolve(),
            "plugin_root": (tmp_path / "plugins").resolve(),
            "output_root": None,
            "plugin_verification_override": None,
        }
    ]


def test_image_format_options_support_original_and_force_is_download_only(monkeypatch, tmp_path: Path) -> None:
    parser = build_parser()
    preferred = parser.parse_args(["download", "https://example.test/gallery", "--image-format", "ORIGINAL"])
    forced = parser.parse_args(["https://example.test/gallery", "--force-image-format", "PNG"])

    assert preferred.image_format == "ORIGINAL"
    assert preferred.force_image_format is None
    assert forced.command_handler == "download"
    assert forced.force_image_format == "PNG"
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["download", "https://example.test/gallery", "--image-format", "PNG", "--force-image-format", "JPEG"]
        )

    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )
    calls: list[dict[str, object]] = []

    class Service:
        async def run(self, *_args: object, **kwargs: object) -> object:
            calls.append(dict(kwargs))
            return SimpleNamespace(saved_files=(), skipped_files=(), failures=())

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
    args = parser.parse_args(["download", "https://example.test/gallery", "--force-image-format", "PNG"])

    assert asyncio.run(cli.run(args)) == cli.EXIT_SUCCESS
    assert calls == [
        {
            "plugin_overrides": {},
            "fallback_override": None,
            "plugin_id": None,
            "force_plugin": False,
            "plugin_download_policy_overrides": {},
            "force_image_format": "PNG",
        }
    ]

    inspect_args = parser.parse_args(
        ["download", "https://example.test/gallery", "--inspect-only", "--force-image-format", "PNG"]
    )
    with pytest.raises(ConfigurationError, match="force-image-format.*inspect"):
        asyncio.run(cli.run(inspect_args))

    update_args = parser.parse_args(
        ["download", "https://example.test/gallery", "--list-updated-urls", "--force-image-format", "PNG"]
    )
    with pytest.raises(ConfigurationError, match="force-image-format.*list-updated-urls"):
        asyncio.run(cli.run(update_args))


def test_each_top_level_subparser_selects_its_own_handler(tmp_path: Path) -> None:
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command_handler == "doctor"
    assert parser.parse_args(["config", "init", str((tmp_path / "app.yaml").resolve())]).command_handler == "config"
    assert parser.parse_args(["plugin", "list"]).command_handler == "plugin"
    cookie = parser.parse_args(["cookie", "export", str(tmp_path / "cookies.export")])
    assert cookie.command_handler == "cookie"
    assert cookie.cookie_action == "export"
    assert parser.parse_args(["inspect", "https://example.test/gallery"]).command_handler == "inspect"


def test_inspection_cli_selects_request_data_and_aliases_download(monkeypatch, tmp_path: Path, capsys) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )
    result = ManifestInspectionResult(
        "https://example.test/gallery",
        "com.example.gallery",
        DownloadManifest(
            "Book",
            (
                Chapter(
                    1,
                    "One",
                    images=(
                        ImageResource(
                            "image:1",
                            referer="https://example.test/source?token=raw",
                            headers={"X-Image": "raw"},
                            image_id="image-id",
                            metadata={"page": "1"},
                            original_filename="plugin-name.webp",
                        ),
                    ),
                ),
            ),
            metadata={"source_url": "https://example.test/gallery"},
        ),
        True,
        (
            ImageRequestResolution(
                1,
                1,
                ImageRequestResolutionStatus.RESOLVED,
                RequestSpec(
                    "https://cdn.example.test/1?signature=raw",
                    method="POST",
                    headers={"Authorization": "raw"},
                    cookies={"session": "raw"},
                    referer="https://example.test/source?token=raw",
                    query={"token": "raw"},
                    json={"nested": "raw"},
                    auth_required=False,
                    retry_non_idempotent=True,
                ),
                {"plugin": "raw"},
                True,
                effective_request=EffectiveRequestPreview(
                    "POST",
                    "https://cdn.example.test/1?signature=effective",
                    (
                        TransportHeader("authorization", "effective"),
                        TransportHeader("cookie", "session=effective"),
                        TransportHeader("content-type", "application/json"),
                    ),
                    (TransportCookie("session", "effective"),),
                    b'{"nested":"raw"}',
                ),
            ),
            ImageRequestResolution(
                1,
                2,
                ImageRequestResolutionStatus.FAILED,
                failure=ImageRequestResolutionFailure("plugin_error", "plugin error", "PluginError"),
            ),
        ),
    )

    class Service:
        async def inspect(self, *_args: object, **kwargs: object) -> ManifestInspectionResult:
            assert kwargs["resolve_image_requests"] is True
            print("plugin output")
            return result

        async def close(self) -> None:
            print("plugin cleanup")

    class Composer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _compose_for_inspection(self) -> Service:
            print("plugin import output")
            return Service()

    config_calls: list[dict[str, object]] = []

    def config_for(*_args: object, **kwargs: object):
        config_calls.append(kwargs)
        return config, tmp_path.resolve(), tmp_path.resolve(), "test"

    monkeypatch.setattr(cli_inspect, "_config_for", config_for)
    monkeypatch.setattr(cli_inspect, "RuntimeComposer", Composer)

    args = build_parser().parse_args(["inspect", "https://example.test/gallery", "--json"])
    assert asyncio.run(cli.run(args)) == cli.EXIT_PARTIAL
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["inspection_data"] == "url"
    assert payload["manifest"] == {"chapter_count": 1, "image_count": 1}
    assert payload["image_requests"][0]["effective_url"] == "https://cdn.example.test/1?signature=effective"
    assert "request" not in payload["image_requests"][0]
    assert "plugin_data" not in payload["image_requests"][0]
    assert payload["image_requests"][1]["failure"]["code"] == "plugin_error"
    assert payload["image_requests"][1]["failure"]["phase"] == "create_image_request"
    assert "plugin output" in output.err
    assert "plugin cleanup" in output.err
    assert "plugin import output" in output.err
    assert config_calls == [{"allow_root_setup": False, "rewrite_user_layers": False}]

    http_args = build_parser().parse_args(
        ["inspect", "https://example.test/gallery", "--inspection-data", "http", "--json"]
    )
    assert asyncio.run(cli.run(http_args)) == cli.EXIT_PARTIAL
    http_payload = json.loads(capsys.readouterr().out)
    http_request = http_payload["image_requests"][0]
    assert http_payload["inspection_data"] == "http"
    assert http_request["request"]["headers"] == {"Authorization": "raw"}
    assert http_request["request"]["cookies"] == {"session": "raw"}
    assert http_request["effective_request"]["url"] == "https://cdn.example.test/1?signature=effective"
    assert http_request["effective_request"]["headers"][0] == {"name": "authorization", "value": "effective"}
    assert http_request["effective_request"]["cookies"] == [{"name": "session", "value": "effective"}]
    assert http_request["effective_request"]["body"] == {
        "encoding": "base64",
        "size_bytes": 16,
        "data": "eyJuZXN0ZWQiOiJyYXcifQ==",
    }
    assert "plugin_data" not in http_request
    assert "chapters" not in http_payload["manifest"]

    all_args = build_parser().parse_args(
        ["inspect", "https://example.test/gallery", "--inspection-data", "all", "--json"]
    )
    assert asyncio.run(cli.run(all_args)) == cli.EXIT_PARTIAL
    all_payload = json.loads(capsys.readouterr().out)
    all_image = all_payload["manifest"]["chapters"][0]["images"][0]
    assert all_payload["inspection_data"] == "all"
    assert all_image["url"] == "image:1"
    assert all_image["referer"] == "https://example.test/source?token=raw"
    assert all_image["headers"] == {"X-Image": "raw"}
    assert all_image["metadata"] == {"page": "1"}
    assert all_image["original_filename"] == "plugin-name.webp"
    assert all_payload["image_requests"][0]["plugin_data"] == {"plugin": "raw"}

    called: list[bool] = []

    async def alias(args: object) -> int:
        called.append(args.manifest_only)  # type: ignore[attr-defined]
        return cli.EXIT_SUCCESS

    monkeypatch.setattr(cli_download, "inspect_command", alias)
    alias_args = build_parser().parse_args(
        ["download", "https://example.test/gallery", "--inspect-only", "--manifest-only"]
    )
    assert asyncio.run(cli.run(alias_args)) == cli.EXIT_SUCCESS
    assert called == [True]


@pytest.mark.parametrize(
    "words",
    (
        ("--existing-file", "skip"),
        ("--no-console-log",),
        ("--plugin-download-policy", 'com.example.gallery={"request_concurrency": 1}'),
    ),
)
def test_inspection_alias_rejects_download_only_options(words: tuple[str, ...]) -> None:
    args = build_parser().parse_args(["download", "https://example.test/gallery", "--inspect-only", *words])

    with pytest.raises(ConfigurationError, match="not valid for .*inspect"):
        asyncio.run(cli.run(args))


def test_inspection_alias_is_exclusive_with_update_listing() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["download", "https://example.test/gallery", "--inspect-only", "--list-updated-urls"])


def test_inspection_data_option_defaults_to_url_and_is_rejected_for_download() -> None:
    parser = build_parser()

    inspect_args = parser.parse_args(["inspect", "https://example.test/gallery"])
    assert getattr(inspect_args, "inspection_data", "url") == "url"
    alias_args = parser.parse_args(
        ["download", "https://example.test/gallery", "--inspect-only", "--inspection-data", "all"]
    )
    assert alias_args.inspection_data == "all"
    with pytest.raises(SystemExit):
        parser.parse_args(["inspect", "https://example.test/gallery", "--inspection-data", "private"])

    download_args = parser.parse_args(["download", "https://example.test/gallery", "--inspection-data", "url"])
    with pytest.raises(ConfigurationError, match="not valid for download"):
        asyncio.run(cli.run(download_args))


@pytest.mark.parametrize(
    "words",
    (
        ("download", "https://example.test/gallery"),
        ("inspect", "https://example.test/gallery"),
        ("doctor",),
        ("config", "path"),
        ("plugin", "list"),
        ("cookie", "export", "cookies.export"),
    ),
)
def test_verification_override_is_available_to_every_top_level_command(words: tuple[str, ...]) -> None:
    args = build_parser().parse_args([*words, "--plugin-verification-override", "bypass-signature"])

    assert args.plugin_verification_override == "bypass-signature"


def test_legacy_allow_unverified_plugins_flag_is_removed() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["doctor", "--allow-unverified-plugins"])


def test_command_specific_options_are_rejected_outside_their_command() -> None:
    parser = build_parser()

    for words in (["plugin", "list", "--host", "example.test"], ["--host", "example.test", "plugin", "list"]):
        args = parser.parse_args(words)
        with pytest.raises(ConfigurationError, match="not valid for plugin list"):
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


def test_config_and_plugin_syntax_is_validated_by_nested_parsers() -> None:
    parser = build_parser()

    for words in (["config", "unknown"], ["plugin"]):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(words)
        assert error.value.code == 2


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
            return SimpleNamespace(saved_files=("saved.jpg",), skipped_files=(), failures=(), additional_files=())

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
        else {"saved": ["saved.jpg"], "skipped": [], "failures": [], "additional_files": []}
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
            return SimpleNamespace(
                saved_files=("saved.jpg",), skipped_files=(), failures=(failure,), additional_files=()
            )

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


def test_download_json_makes_custom_output_failure_paths_relative(monkeypatch, tmp_path: Path, capsys) -> None:
    output_root = (tmp_path / "custom-output").resolve()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
        }
    )
    failure = ImageFailure(
        FailureKind.SAVE,
        "StorageError",
        "save failed",
        output_path=str(output_root / "chapter" / "0001.jpeg"),
    )
    composer_calls: list[dict[str, object]] = []

    class Service:
        async def run(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(saved_files=(), skipped_files=(), failures=(failure,), additional_files=())

        async def close(self) -> None:
            return None

    class Composer:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            composer_calls.append(dict(kwargs))

        def compose(self) -> Service:
            return Service()

    monkeypatch.setattr(
        cli_download,
        "_config_for",
        lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "test"),
    )
    monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)

    status = asyncio.run(
        cli.run(
            build_parser().parse_args(
                ["download", "https://example.test/item", "--json", "--output-dir", str(output_root)]
            )
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert status == cli.EXIT_FAILURE
    assert payload["failures"][0]["output_path"] == "chapter/0001.jpeg"
    assert composer_calls[0]["output_root"] == output_root
