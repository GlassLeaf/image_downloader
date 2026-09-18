from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

from image_downloader.config import AppConfig


def test_quality_workflow_covers_supported_python_and_operating_system_matrix(repository_root: Path) -> None:
    workflow_path = repository_root / ".github" / "workflows" / "quality.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    strategy = workflow["jobs"]["test"]["strategy"]
    matrix = strategy["matrix"]

    assert strategy["fail-fast"] == "false"
    assert matrix["os"] == ["ubuntu-latest", "windows-latest", "macos-latest"]
    assert matrix["python-version"] == ["3.11", "3.12", "3.13", "3.14"]
    assert len(matrix["os"]) * len(matrix["python-version"]) == 12

    commands = [step["run"] for step in workflow["jobs"]["test"]["steps"] if "run" in step]
    assert "python -m pip check" in commands
    assert "python -m ruff check ." in commands
    assert "python -m mypy src/image_downloader" in commands
    assert "python -m pytest -q --cov=image_downloader" in commands


def test_package_metadata_matches_the_ci_support_range(repository_root: Path) -> None:
    metadata = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    classifiers = set(metadata["classifiers"])

    assert metadata["requires-python"] == ">=3.11"
    assert metadata["version"] == "0.0.0.1b0"
    assert "Development Status :: 4 - Beta" in classifiers
    expected_python = {
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    }
    expected_operating_systems = {
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS",
    }
    assert {item for item in classifiers if item.startswith("Programming Language :: Python :: 3.")} == (
        expected_python
    )
    assert {item for item in classifiers if item.startswith("Operating System ::")} == expected_operating_systems


def test_release_smoke_builds_both_artifacts_on_each_operating_system(repository_root: Path) -> None:
    workflow_path = repository_root / ".github" / "workflows" / "quality.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["package-smoke"]

    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest", "macos-latest"]
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert "python -m build --sdist --wheel" in commands
    assert "python tools/release_smoke.py" in commands


def test_bundled_and_model_user_agent_match_beta_version(repository_root: Path) -> None:
    bundled = yaml.safe_load((repository_root / "src" / "image_downloader" / "app.yaml").read_text(encoding="utf-8"))
    assert bundled["network"]["headers"]["User-Agent"] == "image-downloader/0.0.0.1b0"
    assert AppConfig().network.headers["User-Agent"] == bundled["network"]["headers"]["User-Agent"]
