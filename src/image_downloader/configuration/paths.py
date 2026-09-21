"""Configured filesystem roots and profile path resolution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from platformdirs import PlatformDirs

from ..storage.path_safety import canonical_path, existing_directory
from .models import AppConfig


def platform_dirs() -> PlatformDirs:
    """Return the one platform-specific location policy used by every CLI entry point."""
    return PlatformDirs("image-downloader", appauthor=False)


def default_user_config_path() -> Path:
    return platform_dirs().user_config_path / "conf" / "app.yaml"


def default_data_root() -> Path:
    return platform_dirs().user_data_path


def default_plugin_root() -> Path:
    return default_data_root() / "plugins"


def _absolute_directory(value: str | None, name: str, automatic: Path) -> Path:
    return existing_directory(Path(value) if value is not None else automatic, name)


def resolve_paths(config: AppConfig) -> Mapping[str, Path]:
    """Resolve profile data without using the config file or current directory."""
    root = canonical_path(
        _absolute_directory(config.storage.data_root, "storage.data_root", default_data_root())
        / "profiles"
        / config.profile.default,
        "profile data root",
    )
    downloads = root / "downloads"
    return MappingProxyType(
        {
            "profile": root,
            "downloads": downloads,
            "cookie": root / "cookie",
            "logs": root / "logs",
            "state": root / "state",
        }
    )


def plugin_root(config: AppConfig) -> Path:
    """Return the configured plugin root after absolute-path validation."""
    return _absolute_directory(config.plugins.root, "plugins.root", default_plugin_root())
