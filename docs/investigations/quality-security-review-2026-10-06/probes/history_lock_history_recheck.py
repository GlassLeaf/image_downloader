"""Run independent history tests against pinned sources, under outer deadlines.

Uses the already installed pytest and psutil. Each subprocess records the module
path and SHA-256 before pytest starts. Only descendants of an owned test process
are terminated on timeout. Protection settings are never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil

TEST = "tests/v3/application/test_workflow_history.py::test_independent_processes_merge_under_shared_history_lock"


def run_worker(source: Path) -> int:
    source = source.resolve()
    sys.path.insert(0, str(source))
    module = importlib.import_module("image_downloader.storage.interprocess_lock")
    path = Path(module.__file__).resolve()
    if not path.is_relative_to(source):
        raise RuntimeError("history test imported a different source")
    print(json.dumps({"module": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}), flush=True)
    import pytest

    return pytest.main(["-q", "-o", "pythonpath=" + str(source), TEST])


def bounded_test(source: Path, log: Path) -> dict:
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(source)],
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        watchdog = False
        try:
            code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            watchdog = True
            try:
                children = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
            process.wait(timeout=5)
            psutil.wait_procs(children, timeout=5)
            for child in children:
                if child.is_running():
                    child.kill()
            code = process.returncode
    return {
        "exitcode": code,
        "watchdog": watchdog,
        "seconds": time.monotonic() - started,
        "log": log.read_text(encoding="utf-8"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--source", action="append", help="label=src directory")
    parser.add_argument("--protection", help="user-reported condition; does not toggle protection")
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker is not None:
        return run_worker(args.worker)
    if args.output is None or not args.source or not args.protection or args.repeats < 1:
        parser.error("output, source, protection and a positive repeat count are required")
    sources = [(label, Path(path).resolve()) for label, path in (value.split("=", 1) for value in args.source)]
    if len({label.casefold() for label, _ in sources}) != len(sources) or any(
        not label.replace("-", "").isalnum() for label, _ in sources
    ):
        parser.error("source labels must be unique alphanumeric names with optional hyphens")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for repeat in range(args.repeats):
        order = sources[repeat % len(sources) :] + sources[: repeat % len(sources)]
        if repeat % 2:
            order = list(reversed(order))
        for label, source in order:
            row = bounded_test(source, output / f"{repeat + 1:02d}-{label}.log")
            row.update({"source": label, "repeat": repeat + 1, "protection": args.protection})
            results.append(row)
            (output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps({key: value for key, value in row.items() if key != "log"}), flush=True)
    return 0 if all(row["exitcode"] == 0 and not row["watchdog"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
