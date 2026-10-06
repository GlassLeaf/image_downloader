"""Capture repeatable audit checks without changing production files."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = HERE / "raw"


def main() -> None:
    RAW.mkdir(exist_ok=True)
    inventory = []
    for path in sorted((ROOT / "src/image_downloader").rglob("*.py")):
        content = path.read_bytes()
        inventory.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "lines": len(content.splitlines()),
            }
        )
    metadata = {
        "started_utc": datetime.now(UTC).isoformat(),
        "user_date": "2026-10-06",
        "user_timezone": "Asia/Tokyo",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "initial_status_before_audit_files": "?? docs/investigations/implementation-review-2026-10-05/",
        "status_at_runner_start": subprocess.check_output(
            ["git", "status", "--short"],
            cwd=ROOT,
            text=True,
        ),
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "tools": {name: importlib.metadata.version(name) for name in ("ruff", "mypy", "pytest", "pytest-cov")},
        "inventory": inventory,
        "checks": [],
    }
    checks = [
        ("ruff-project", ["-m", "ruff", "check", "src/image_downloader", "--output-format", "json"]),
        (
            "ruff-security",
            [
                "-m",
                "ruff",
                "check",
                "src/image_downloader",
                "--select",
                "S",
                "--ignore-noqa",
                "--output-format",
                "json",
            ],
        ),
        (
            "ruff-pep8",
            [
                "-m",
                "ruff",
                "check",
                "src/image_downloader",
                "--select",
                "E,W,N",
                "--line-length",
                "79",
                "--ignore-noqa",
                "--config",
                "lint.ignore=[]",
                "--output-format",
                "json",
            ],
        ),
        ("mypy", ["-m", "mypy", "src/image_downloader"]),
        (
            "pytest-v3",
            [
                "-m",
                "pytest",
                "tests/v3",
                "-q",
                "--cov=image_downloader",
                "--cov-branch",
                "--cov-report=term-missing",
                f"--cov-report=json:{RAW / 'coverage.json'}",
            ],
        ),
    ]
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", COVERAGE_FILE=str(RAW / ".coverage"))
    # Mailpit integration is opt-in and is not part of this offline audit.
    environment.pop("IMAGE_DOWNLOADER_TEST_MAILPIT", None)
    for name, arguments in checks:
        started = time.monotonic()
        command = [sys.executable, *arguments]
        with (RAW / f"{name}.stdout").open("w", encoding="utf-8") as stdout:
            with (RAW / f"{name}.stderr").open("w", encoding="utf-8") as stderr:
                completed = subprocess.run(
                    command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr, check=False
                )
        record = {
            "name": name,
            "command": command,
            "exit_code": completed.returncode,
            "seconds": round(time.monotonic() - started, 3),
        }
        metadata["checks"].append(record)
        (RAW / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False), flush=True)
    metadata["finished_utc"] = datetime.now(UTC).isoformat()
    (RAW / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
