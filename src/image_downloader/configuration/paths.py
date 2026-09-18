"""Configured filesystem roots and profile path resolution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory
from .models import AppConfig


def _absolute_directory(value: str | None, name: str) -> Path:
    if not value:
        raise ConfigurationError(f"{name} must be configured as an absolute path")
    return existing_directory(Path(value), name)


def resolve_paths(config: AppConfig) -> Mapping[str, Path]:
    """Resolve profile data without using the config file or current directory."""
    root = canonical_path(
        _absolute_directory(config.storage.data_root, "storage.data_root") / "profiles" / config.profile.default,
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
    return _absolute_directory(config.plugins.root, "plugins.root")
