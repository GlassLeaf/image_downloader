from __future__ import annotations

import tomllib
from pathlib import Path


def test_legacy_tests_are_explicitly_excluded(repository_root: Path) -> None:
    with (repository_root / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream)

    pytest_config = config["tool"]["pytest"]["ini_options"]
    ruff_config = config["tool"]["ruff"]
    assert pytest_config["testpaths"] == ["tests/v3"]
    assert "tests/legacy" in pytest_config["norecursedirs"]
    assert "tests/legacy" in ruff_config["extend-exclude"]
    assert ".runtime" in ruff_config["extend-exclude"]
    assert ruff_config["line-length"] == 120
    assert "E501" not in config["tool"]["ruff"]["lint"]["ignore"]
    assert config["tool"]["ruff"]["lint"]["per-file-ignores"] == {
        "plugin-sources/generic-css-selector/plugin.py": ["E501"],
        "plugin-sources/paginated-catalog/plugin.py": ["E501"],
    }
    assert config["tool"]["ruff"]["format"]["exclude"] == ["plugin-sources/**/*.py"]


def test_repository_ruff_gate_includes_the_whole_tree(repository_root: Path) -> None:
    workflow = (repository_root / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    archive = (repository_root / "tests" / "legacy" / "README.md").read_text(encoding="utf-8")
    assert "python -m ruff check ." in workflow
    assert "Status: archived." in archive
    assert "not part of the current test suite" in archive
