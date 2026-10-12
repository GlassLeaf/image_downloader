"""Audit saved open prerequisites and run bounded, single-process mode controls.

Each smoke case uses a fresh disposable path and a child deadline. This is
not a repetition of the concurrent hang benchmark. Prior evidence is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REVIEW = Path(__file__).resolve().parents[1]
ROOT = REVIEW.parents[2]
ARCHIVES = (
    "workflow-history-open-size-modes-evidence-2026-10-11.json",
    "workflow-history-open-delay-errors-evidence-2026-10-11.json",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(path: Path) -> dict:
    try:
        info = path.stat()
        return {"exists": True, "regular": stat.S_ISREG(info.st_mode), "size": info.st_size}
    except FileNotFoundError:
        return {"exists": False}


def child(path: Path, mode: str) -> dict:
    result = {"before_open": snapshot(path), "mode": mode}
    try:
        with path.open(mode) as stream:
            result["success"] = True
            result["open_size"] = stream.seek(0, 2)
    except OSError as exc:
        result.update(
            success=False, error_type=type(exc).__name__, errno=exc.errno, winerror=getattr(exc, "winerror", None)
        )
    result["after_close"] = snapshot(path)
    return result


def audit_archives() -> tuple[list, list, list]:
    archive_records, rows, source_checks = [], [], []
    for name in ARCHIVES:
        path = REVIEW / "raw" / name
        before = digest(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        groups = (
            {"size-modes": data["matrix"]}
            if "matrix" in data
            else {key: value["evidence"] for key, value in data["comparison_groups"].items()}
        )
        for group, evidence in groups.items():
            for trial in evidence["trials"]:
                observed = trial["observation"]
                profile = observed["profile"]
                first = next(s for s in observed["preparation"]["snapshots"] if s["when"] == "before_first_round")
                expected_exists = profile["lifecycle"] != "missing"
                expected_size = 0 if profile["lifecycle"] == "stable-empty" else 1
                preparation_ok = first["exists"] == expected_exists and (
                    not expected_exists or first.get("size") == expected_size
                )
                mode_ok = profile["mode"] in ("w+b", "a+b") or (profile["mode"] == "r+b" and first["exists"])
                rows.append(
                    {
                        "archive": name,
                        "group": group,
                        "label": observed["label"],
                        "mode": profile["mode"],
                        "lifecycle": profile["lifecycle"],
                        "initial_snapshot": first,
                        "preparation_matches_profile": preparation_ok,
                        "mode_allows_initial_state": mode_ok,
                        "protocol_completed": observed["protocol_completed"],
                        "returned_errors_in_worker_reports": observed["errors"],
                    }
                )
        sources = data["sources"]
        entries = ({"path": key, **value} for key, value in sources.items()) if isinstance(sources, dict) else sources
        for entry in entries:
            source = ROOT / entry["path"]
            current = digest(source) if source.is_file() else None
            source_checks.append(
                {
                    "archive": name,
                    "path": entry["path"],
                    "expected_sha256": entry["sha256"],
                    "current_sha256": current,
                    "matches": current == entry["sha256"],
                }
            )
        archive_records.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256_before": before,
                "sha256_after": digest(path),
                "unchanged": before == digest(path),
            }
        )
    return archive_records, rows, source_checks


def run(output: Path, evidence_path: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    archives, rows, source_checks = audit_archives()
    cases = []
    for state in ("missing", "empty", "one-byte"):
        for mode in ("r+b", "w+b", "a+b", "x+b"):
            path = output / f"{state}-{mode[0]}.lock"
            if state != "missing":
                path.write_bytes(b"" if state == "empty" else b"\0")
            case = {"state": state, "mode": mode, "prepared": snapshot(path)}
            try:
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--child", str(path), "--mode", mode],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                case["exitcode"] = completed.returncode
                case["stderr"] = completed.stderr
                case["observed"] = json.loads(completed.stdout) if completed.returncode == 0 else None
            except subprocess.TimeoutExpired:
                case["timed_out"] = True
                case["observed"] = None
            observed = case["observed"]
            expected_error = (
                "FileNotFoundError"
                if mode == "r+b" and state == "missing"
                else ("FileExistsError" if mode == "x+b" and state != "missing" else None)
            )
            expected_size = 0 if state != "one-byte" or mode[0] == "w" else 1
            case["expected_error"] = expected_error
            case["matches_expected"] = bool(
                observed
                and observed["before_open"] == case["prepared"]
                and (
                    observed.get("error_type") == expected_error and observed["after_close"] == case["prepared"]
                    if expected_error
                    else observed["success"] and observed["after_close"].get("size") == expected_size
                )
            )
            cases.append(case)
            print(json.dumps({"state": state, "mode": mode, "matches_expected": case["matches_expected"]}), flush=True)
    counts = Counter((row["group"], row["lifecycle"]) for row in rows)
    record = {
        "recorded_at": datetime.now().astimezone().isoformat(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "execution_scope": "current command environment; sequential child cases; no concurrent workers",
        "child_deadline_seconds": 10,
        "probe_sha256": digest(Path(__file__)),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "archives": archives,
        "archive_source_checks": source_checks,
        "archived_trials": rows,
        "archived_counts": [
            {"group": group, "lifecycle": lifecycle, "count": count}
            for (group, lifecycle), count in sorted(counts.items())
        ],
        "archived_all_preparation_matches": all(r["preparation_matches_profile"] for r in rows),
        "archived_all_modes_match": all(r["mode_allows_initial_state"] for r in rows),
        "smoke_cases": cases,
        "smoke_all_expected": all(c["matches_expected"] for c in cases),
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", type=Path)
    parser.add_argument("--mode", choices=("r+b", "w+b", "a+b", "x+b"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if args.child is not None:
        if args.mode is None:
            parser.error("--child requires --mode")
        print(json.dumps(child(args.child, args.mode)))
        return 0
    if args.output is None or args.evidence is None:
        parser.error("--output and --evidence are required")
    record = run(args.output.resolve(), args.evidence.resolve())
    return (
        0
        if record["smoke_all_expected"]
        and record["archived_all_modes_match"]
        and record["archived_all_preparation_matches"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
