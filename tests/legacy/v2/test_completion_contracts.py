from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from image_downloader import (
    BaseDownloader,
    Chapter,
    Downloader,
    ImageDownloaderError,
    ImageResource,
    ParseResult,
    PluginContext,
    PluginRegistry,
)
from image_downloader.cli import EXIT_CONFIGURATION, EXIT_SUCCESS, build_parser, run
from image_downloader.config import DEFAULT_CONFIG, resolve_paths, validate_config
from image_downloader.exceptions import ConfigurationError, PluginError
from image_downloader.plugins import registry as registry_module


class FirstPlugin(BaseDownloader):
    name = "first"
    priority = 10

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith("test://")


class SecondPlugin(FirstPlugin):
    name = "second"


class BrokenEntryPoint:
    name = "broken"

    def load(self) -> type[BaseDownloader]:
        raise RuntimeError("broken entry point")


class EntryPoints:
    def select(self, *, group: str):
        assert group == "image_downloader.plugins"
        return [BrokenEntryPoint()]


def test_configuration_rejects_unknown_values_and_invalid_email() -> None:
    with pytest.raises(ConfigurationError, match="Extra inputs"):
        validate_config({"network": {"unexpected": True}})
    with pytest.raises(ConfigurationError, match="greater than or equal to 1"):
        validate_config({"network": {"max_chapter_concurrency": 0}})
    with pytest.raises(ConfigurationError, match="email notifications require"):
        validate_config({"notification": {"methods": ["email"]}})
    with pytest.raises(ConfigurationError, match="unsupported notification events"):
        validate_config({"notification": {"routes": {"unknown": ["desktop"]}}})


def test_profile_override_selects_runtime_paths_and_rejects_escape(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["profile"] = dict(DEFAULT_CONFIG["profile"])
    config["profile"]["default"] = "work"
    assert resolve_paths(config, base_dir=tmp_path)["profile"] == (tmp_path / "profiles" / "work").resolve()
    config["profile"]["root"] = "../escape"
    with pytest.raises(ConfigurationError, match="profile root"):
        resolve_paths(config, base_dir=tmp_path)


def test_doctor_warns_for_missing_default_and_errors_for_explicit_config(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    default_args = build_parser().parse_args(["doctor"])
    assert asyncio.run(run(default_args)) == EXIT_SUCCESS
    assert "warning: default configuration file not found" in capsys.readouterr().out
    explicit_args = build_parser().parse_args(["doctor", "--config", "missing.yaml"])
    assert asyncio.run(run(explicit_args)) == EXIT_CONFIGURATION


def test_plugin_loading_is_isolated_and_conflicts_are_explicit(monkeypatch) -> None:
    monkeypatch.setattr(registry_module, "entry_points", lambda: EntryPoints())
    registry = PluginRegistry()
    registry.load_entry_points()
    assert registry.diagnostics[0].loaded is False
    registry.register(FirstPlugin)
    with pytest.raises(PluginError, match="duplicate plugin name"):
        registry.register(FirstPlugin)
    registry.register(SecondPlugin)
    with pytest.raises(PluginError, match="priority conflict"):
        registry.resolve("test://target")


def test_chapter_worker_cap_and_result_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        downloader = Downloader(
            "test://chapters", config={"_output_dir": str(tmp_path), "network": {"max_chapter_concurrency": 3}}
        )
        active = 0
        maximum = 0

        async def download_chapter(_plugin: object, _parsed: ParseResult, chapter: Chapter):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01 * (5 - chapter.number))
            active -= 1
            return [str(chapter.number)], []

        downloader.chapter_service.download = download_chapter  # type: ignore[method-assign]
        parsed = ParseResult("chapters", [Chapter(number, str(number)) for number in range(1, 6)])
        try:
            results = await downloader._run_chapters(object(), parsed)
        finally:
            await downloader.close()
        assert maximum <= 3
        assert [result[0][0] for result in results if not isinstance(result, Exception)] == ["1", "2", "3", "4", "5"]

    asyncio.run(scenario())


class ContextCapturingPlugin(BaseDownloader):
    captured: list[PluginContext] = []

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith("context://")

    async def parse(self, url: str, context: PluginContext | None = None) -> ParseResult:
        return ParseResult("context", [])

    async def build_image_request(self, image: ImageResource, context: PluginContext | None = None):
        assert context is not None
        type(self).captured.append(context)
        raise RuntimeError("stop after context capture")


def test_chapter_service_passes_public_plugin_context_and_close_delegates(tmp_path: Path) -> None:
    async def scenario() -> None:
        ContextCapturingPlugin.captured.clear()
        downloader = Downloader("context://page", config={"_output_dir": str(tmp_path)})
        original_close = downloader.session.close
        close_calls = 0

        async def close_session() -> None:
            nonlocal close_calls
            close_calls += 1
            await original_close()

        downloader.session.close = close_session  # type: ignore[method-assign]
        chapter = Chapter(1, "context", images=[ImageResource("https://example.test/image")])
        try:
            with pytest.raises(ImageDownloaderError, match="stop after context capture"):
                await downloader.chapter_service.download(
                    ContextCapturingPlugin(), ParseResult("context", [chapter]), chapter
                )
        finally:
            await downloader.close()

        assert close_calls == 1
        assert ContextCapturingPlugin.captured == [downloader]
        assert isinstance(downloader, PluginContext)
        assert ContextCapturingPlugin.captured[0].url == "context://page"
        assert ContextCapturingPlugin.captured[0].config is downloader.config

    asyncio.run(scenario())
