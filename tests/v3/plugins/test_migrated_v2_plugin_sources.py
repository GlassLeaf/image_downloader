"""Strict-loading coverage for the six v2-pattern v3 source units."""

from __future__ import annotations

from pathlib import Path

from image_downloader.config import AppConfig
from image_downloader.security import PluginRuntime, install_plugin

UNITS = {
    "chaptered-catalog": ("https://catalog.example.test/works/1", "local.image-downloader.chaptered-catalog"),
    "csrf-login-gallery": ("https://csrf-login.example.test/works/1", "local.image-downloader.csrf-login-gallery"),
    "cursor-api-gallery": ("https://cursor-api.example.test/galleries/1", "local.image-downloader.cursor-api-gallery"),
    "oauth-media-api": ("https://oauth-media.example.test/works/1", "local.image-downloader.oauth-media-api"),
    "public-gallery": ("https://public-gallery.example.test/galleries/1", "local.image-downloader.public-gallery"),
    "signed-cdn-gallery": ("https://signed-cdn.example.test/works/1", "local.image-downloader.signed-cdn-gallery"),
}


def test_migrated_v2_units_install_strictly_and_win_their_hosts(tmp_path: Path, plugin_sources: Path) -> None:
    root = (tmp_path / "plugin-root").resolve()
    for name in UNITS:
        install_plugin(root, (plugin_sources / name).resolve())
    config = AppConfig.model_validate({"plugins": {"root": str(root)}, "security": {"plugin_verification": "strict"}})
    registry = PluginRuntime(config, root, mode="strict")
    from image_downloader.plugins.builtin import GenericHtmlPlugin

    registry.register_builtin(GenericHtmlPlugin)
    registry.prepare()
    assert not [item for item in registry.diagnostics if not item.loaded and not item.warning]
    for url, expected_id in UNITS.values():
        record, _ = registry.resolve(url, fallback_enabled=True)
        assert record.id == expected_id
