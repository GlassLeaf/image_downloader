"""Shared repository locations for the current v3 test suite."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def plugin_sources(repository_root: Path) -> Path:
    return repository_root / "plugin-sources"


@pytest.fixture(scope="session")
def local_plugin_fixtures() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "local_plugins"
