from __future__ import annotations

import tomllib
from pathlib import Path
from shlex import split

import yaml

from image_downloader.config import AppConfig


def _assert_checkout_identity_follows_python_setup(steps: list[dict]) -> None:
    setup = next(index for index, step in enumerate(steps) if step.get("uses", "").startswith("actions/setup-python@"))
    identity = next(index for index, step in enumerate(steps) if step.get("name") == "Record checkout identity")
    assert identity > setup  # Use the selected interpreter on every OS, including macOS.
    assert steps[identity]["shell"] == "python"


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
    _assert_checkout_identity_follows_python_setup(workflow["jobs"]["test"]["steps"])

    commands = [step["run"] for step in workflow["jobs"]["test"]["steps"] if "run" in step]
    assert "python -m pip check" in commands
    assert "python -m ruff check ." in commands
    assert "python -m mypy src/image_downloader" in commands
    pytest_commands = [split(command) for command in commands if split(command)[:3] == ["python", "-m", "pytest"]]
    assert len(pytest_commands) == 1
    arguments = pytest_commands[0][3:]
    assert {"-q", "-ra", "--cov=image_downloader"} <= set(arguments)
    assert "--cov-report=term-missing:skip-covered" in arguments
    assert "--cov-report=xml:.runtime/ci/coverage.xml" in arguments
    assert "--junitxml=.runtime/ci/junit.xml" in arguments
    test_step = next(step for step in workflow["jobs"]["test"]["steps"] if "pytest" in step.get("run", ""))
    assert test_step.get("continue-on-error", "false") == "false"


def test_ci_reports_are_preserved_on_failure_without_uploading_the_workspace(repository_root: Path) -> None:
    workflow = yaml.load(
        (repository_root / ".github/workflows/quality.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    steps = workflow["jobs"]["test"]["steps"]
    uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["if"] == "always()"
    settings = upload["with"]
    assert settings["path"].splitlines() == [".runtime/ci/junit.xml", ".runtime/ci/coverage.xml"]
    assert settings["include-hidden-files"] == "true"
    assert settings["if-no-files-found"] == "warn"
    assert settings["retention-days"] == "14"
    for component in ("matrix.os", "matrix.python-version", "github.run_attempt"):
        assert "${{ " + component + " }}" in settings["name"]


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
    _assert_checkout_identity_follows_python_setup(job["steps"])
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert "python -m build --sdist --wheel" in commands
    assert "python tools/release_smoke.py" in commands


def test_bundled_and_model_user_agent_match_beta_version(repository_root: Path) -> None:
    bundled = yaml.safe_load((repository_root / "src" / "image_downloader" / "app.yaml").read_text(encoding="utf-8"))
    assert bundled["network"]["headers"]["User-Agent"] == "image-downloader/0.0.0.1b0"
    assert AppConfig().network.headers["User-Agent"] == bundled["network"]["headers"]["User-Agent"]
