from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
from examples.plugins.sample_site import SampleSiteDownloader

from image_downloader import BaseDownloader, Downloader, PluginRegistry
from image_downloader.config import load_application_config


class TypeErrorPlugin(BaseDownloader):
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url == "type-error://example"

    async def parse(self, url: str, context: object | None = None):
        raise TypeError("plugin implementation bug")


def test_library_package_is_not_present() -> None:
    assert importlib.util.find_spec("library") is None


def test_sample_plugin_requires_explicit_registration() -> None:
    registry = PluginRegistry()
    with pytest.raises(Exception, match="no plugin can handle URL"):
        registry.resolve("sample://example")
    registry.register(SampleSiteDownloader)
    assert isinstance(registry.resolve("sample://example"), SampleSiteDownloader)


def test_plugin_type_error_is_not_retried_or_hidden(tmp_path: Path) -> None:
    registry = PluginRegistry()
    registry.register(TypeErrorPlugin)

    async def scenario() -> None:
        downloader = Downloader("type-error://example", config={"_output_dir": str(tmp_path)}, registry=registry)
        try:
            with pytest.raises(TypeError, match="plugin implementation bug"):
                await downloader.download()
        finally:
            await downloader.close()

    asyncio.run(scenario())


def test_configuration_precedence_matches_documentation(tmp_path: Path) -> None:
    (tmp_path / "app.yaml").write_text("network:\n  max_concurrency: 1\n", encoding="utf-8")
    profile_conf = tmp_path / "profiles" / "default" / "conf"
    profile_conf.mkdir(parents=True)
    (profile_conf / "app.yaml").write_text("network:\n  max_concurrency: 2\n", encoding="utf-8")
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "site_global.yaml").write_text("network:\n  max_concurrency: 3\n", encoding="utf-8")
    (profile_conf / "site_global.yaml").write_text("network:\n  max_concurrency: 4\n", encoding="utf-8")
    site_dir = sites / "example_test"
    site_dir.mkdir()
    (site_dir / "example_test.yaml").write_text("network:\n  max_concurrency: 5\n", encoding="utf-8")
    (profile_conf / "example_test.yaml").write_text("network:\n  max_concurrency: 6\n", encoding="utf-8")

    config = load_application_config(tmp_path / "app.yaml", site="example.test")
    assert config["network"]["max_concurrency"] == 6
