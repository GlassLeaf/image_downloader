"""Repeat paired Windows lock observations with fresh processes and alternating order.

Each pair runs two original history tests before and after a contention probe.
The contention probe observes current initialization and an isolated copy without
initialization, each with three workers and the same number of rounds.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
import winreg
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE_FILES = (
    "src/image_downloader/storage/interprocess_lock.py",
    "src/image_downloader/storage/workflow_history.py",
    "tests/v3/application/test_workflow_history.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_command(command: list[str], root: Path, environment: dict[str, str], log: Path, label: str) -> dict:
    print(json.dumps({"phase": label, "status": "started"}), flush=True)
    started = time.monotonic()
    previous = ""
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=root, env=environment, stdout=stream, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                time.sleep(3)
                content = log.read_text(encoding="utf-8")
                last = content.strip().splitlines()[-1] if content.strip() else ""
                if last and last != previous:
                    previous = last
                    if last.startswith('{"variant":'):
                        observation = json.loads(last)
                        print(
                            json.dumps(
                                {
                                    "phase": label,
                                    **{
                                        k: observation[k]
                                        for k in ("variant", "errors", "successful_acquisitions", "counter", "rounds")
                                    },
                                }
                            ),
                            flush=True,
                        )
                if time.monotonic() - started > 240:
                    raise TimeoutError(f"diagnostic command exceeded 240 seconds: {label}")
        except BaseException:
            import psutil

            if process.poll() is None:
                descendants = psutil.Process(process.pid).children(recursive=True)
                for child in descendants:
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(descendants, timeout=5)
                for child in alive:
                    child.kill()
                process.terminate()
                process.wait(10)
            raise
    result = {
        "label": label,
        "command": command,
        "exitcode": process.returncode,
        "seconds": round(time.monotonic() - started, 3),
        "log": str(log),
        "log_sha256": digest(log),
    }
    print(json.dumps({"phase": label, "status": "completed", "exitcode": process.returncode}), flush=True)
    return result


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def environment_record(requirements: Path) -> dict:
    expected = {
        canonical(name): version
        for line in requirements.read_text(encoding="utf-8-sig").splitlines()
        if "==" in line
        for name, version in [line.split("==", 1)]
    }
    installed = {canonical(d.metadata["Name"]): d.version for d in metadata.distributions()}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
        windows = {
            name: winreg.QueryValueEx(key, name)[0]
            for name in ("EditionID", "DisplayVersion", "CurrentBuildNumber", "UBR")
        }
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "windows": windows,
        "requirements_path": str(requirements),
        "requirements_sha256": digest(requirements),
        "requirements_count": len(expected),
        "differences": {
            name: {"expected": version, "installed": installed.get(name)}
            for name, version in expected.items()
            if installed.get(name) != version
        },
        "additional_packages": {name: version for name, version in installed.items() if name not in expected},
        "pytest_plugin_autoload": False,
    }


def history_observation(root: Path, probes: Path, output: Path, environment: dict[str, str], label: str) -> dict:
    folder = output / label
    command = [sys.executable, str(probes / "history_process_stacks.py"), str(folder), "--no-autoload"]
    execution = run_command(command, root, environment, output / f"{label}.log", label)
    observation = read_json(folder / "summary.json")
    observation["pytest_summary"] = (folder / "pytest.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    observation["pytest_log_sha256"] = digest(folder / "pytest.log")
    return {**execution, "observation": observation}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for all observations")
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=200)
    parser.add_argument("--requirements", type=Path, default=Path.home() / "Downloads/requirements.txt")
    args = parser.parse_args()
    if sys.platform != "win32" or args.pairs <= 0 or args.rounds <= 0:
        parser.error("requires Windows, positive pairs and positive rounds")
    probes = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parents[4]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    evidence = {
        "recorded_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "environment": environment_record(args.requirements),
        "protocol": {
            "pairs": args.pairs,
            "rounds_per_condition": args.rounds,
            "workers": 3,
            "history_tests_before_each_pair": 2,
            "history_tests_after_each_pair": 2,
            "sequential": True,
            "order": "alternating; current first on odd pairs",
            "protected_delay_seconds": 0.001,
            "record_all_errors": True,
            "execution_environment": "outside Codex sandbox",
        },
        "source_sha256_before": {p: digest(root / p) for p in SOURCE_FILES},
        "probe_sha256": {
            p.name: digest(p)
            for p in (Path(__file__), probes / "history_lock_contention.py", probes / "history_process_stacks.py")
        },
        "history_tests": [],
        "pairs": [],
    }
    if evidence["environment"]["differences"]:
        raise RuntimeError("installed requirements differ from the expected environment")
    destination = output / "evidence.json"
    for pair in range(1, args.pairs + 1):
        for iteration in (1, 2):
            label = f"pair-{pair:02}-history-before-{iteration}"
            evidence["history_tests"].append(history_observation(root, probes, output, environment, label))
            destination.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        order = ("missing", "no-initialization") if pair % 2 else ("no-initialization", "missing")
        label = f"pair-{pair:02}-contention"
        folder = output / label
        command = [
            sys.executable,
            str(probes / "history_lock_contention.py"),
            str(folder),
            "--rounds",
            str(args.rounds),
            "--record-all-errors",
            "--variants",
            *order,
        ]
        execution = run_command(command, root, environment, output / f"{label}.log", label)
        evidence["pairs"].append(
            {"pair": pair, "order": order, **execution, "observations": read_json(folder / "evidence.json")}
        )
        destination.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        for iteration in (1, 2):
            label = f"pair-{pair:02}-history-after-{iteration}"
            evidence["history_tests"].append(history_observation(root, probes, output, environment, label))
            destination.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    evidence["source_sha256_after"] = {p: digest(root / p) for p in SOURCE_FILES}
    evidence["source_unchanged"] = evidence["source_sha256_before"] == evidence["source_sha256_after"]
    evidence["completed_at"] = datetime.now(timezone(timedelta(hours=9))).isoformat()
    destination.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    complete = evidence["source_unchanged"] and all(r["exitcode"] == 0 for r in evidence["history_tests"])
    complete = complete and all(r["exitcode"] == 0 for r in evidence["pairs"])
    print(json.dumps({"evidence": str(destination), "protocol_completed": complete}), flush=True)
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
