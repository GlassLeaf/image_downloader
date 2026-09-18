from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace

import pytest

from image_downloader import (
    AppConfig,
    Chapter,
    ChapterResult,
    DownloadManifest,
    DownloadResult,
    FailureKind,
    ImageArtifact,
    ImageFailure,
    ImageOutcome,
    ImageOutcomeKind,
    ImageResource,
    RequestResponse,
    UpdateChange,
    UpdateChangeKind,
    UpdateResult,
)
from image_downloader.builtin import GenericHtmlPlugin
from image_downloader.cli import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS, build_parser, run
from image_downloader.config import apply_overrides, deep_merge, load_application_config, resolve_paths, validate_config
from image_downloader.cookies import CookieStore, _cookie
from image_downloader.exceptions import ConfigurationError
from image_downloader.ports import PluginExecutionContext


def test_config_layers_validation_and_safe_paths(tmp_path: Path) -> None:
    app = tmp_path / "app.yaml"
    app.write_text("profile:\n  default: work\nnetwork:\n  max_retries: 2\n", encoding="utf-8")
    profile = tmp_path / "profiles" / "work" / "conf"
    profile.mkdir(parents=True)
    (profile / "app.yaml").write_text("output:\n  image_format: PNG\n", encoding="utf-8")
    loaded = load_application_config(app)
    assert loaded.network.max_retries == 2
    assert loaded.output.image_format == "PNG"
    assert resolve_paths(loaded, base_dir=tmp_path)["downloads"].name == "downloads"
    assert apply_overrides(loaded, {"allow_empty_manifest": True}).allow_empty_manifest
    assert deep_merge({"one": {"two": 1}}, {"one": {"three": 2}}) == {"one": {"two": 1, "three": 2}}
    with pytest.raises(ConfigurationError):
        validate_config({"network": {"max_retries": 0}})
    with pytest.raises(ConfigurationError):
        resolve_paths(AppConfig.model_validate({"profile": {"root": "../bad"}}), base_dir=tmp_path)


def test_encrypted_cookie_round_trip_and_export(tmp_path: Path, monkeypatch) -> None:
    values: dict[tuple[str, str], str] = {}
    keyring = SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, value: values.__setitem__((service, account), value),
    )
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    store = CookieStore(tmp_path / "cookies.enc")
    jar = CookieJar()
    jar.set_cookie(_cookie("session", "secret", "example.test", domain_specified=True))
    store.save(jar)
    assert b"secret" not in (tmp_path / "cookies.enc").read_bytes()
    assert next(iter(store.load())).value == "secret"
    exported = tmp_path / "cookies.export"
    store.export_file(exported, "passphrase")
    target = CookieStore(tmp_path / "other.enc")
    target.import_file(exported, "passphrase")
    assert next(iter(target.load())).name == "session"


def test_generic_html_plugin_uses_restricted_request_port() -> None:
    class Requests:
        async def execute(self, spec):
            return RequestResponse(spec.url, 200, {}, b"<title>Gallery</title><img src='/one.png'>")

    class Secrets:
        def get(self, name):
            raise AssertionError(name)

    async def check() -> None:
        context = PluginExecutionContext({}, Secrets(), Requests())
        plugin = GenericHtmlPlugin()
        manifest = await plugin.inspect("https://example.test/gallery", context)
        assert manifest.title == "Gallery"
        request = await plugin.create_image_request(manifest.chapters[0].images[0], context)
        assert request.referer == "https://example.test/gallery"
        artifact = ImageArtifact(b"data", "application/octet-stream", request.url)
        assert await plugin.transform_image(artifact, SimpleNamespace()) is artifact

    asyncio.run(check())


def test_cli_exit_results_and_update_output(monkeypatch, capsys, tmp_path: Path) -> None:
    config = AppConfig.model_validate({"security": {"plugin_verification": "off"}})

    class Service:
        async def run(self, url):
            image = ImageResource(url)
            if url.endswith("failure"):
                failed = ImageOutcome(
                    image,
                    ImageOutcomeKind.FAILED,
                    failure=ImageFailure(FailureKind.FETCH, "TestError", "masked"),
                )
                return DownloadResult(
                    url,
                    DownloadManifest("x", ()),
                    (ChapterResult(Chapter(1, "x"), (failed,)),),
                )
            return DownloadResult(url, DownloadManifest("x", ()), ())

        async def check_updates(self, url):
            return UpdateResult(
                url,
                "x",
                (
                    UpdateChange(UpdateChangeKind.ADDED, "https://example.test/new"),
                    UpdateChange(UpdateChangeKind.REMOVED, "https://example.test/old"),
                ),
                datetime.now(UTC),
            )

        async def close(self):
            return None

    service = Service()
    monkeypatch.setattr("image_downloader.cli.load_application_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(
        "image_downloader.cli.RuntimeComposer", lambda *args, **kwargs: SimpleNamespace(compose=lambda: service)
    )

    async def check() -> None:
        parser = build_parser()
        assert await run(parser.parse_args(["https://example.test/ok"])) == EXIT_SUCCESS
        assert await run(parser.parse_args(["https://example.test/ok", "--list-updated-urls"])) == EXIT_SUCCESS
        assert await run(parser.parse_args(["https://example.test/failure"])) == EXIT_FAILURE

    asyncio.run(check())
    captured = capsys.readouterr()
    assert "https://example.test/new" in captured.out
    assert "removed: 1" in captured.err


def test_cli_partial_exit_is_distinct() -> None:
    assert EXIT_PARTIAL == 5
