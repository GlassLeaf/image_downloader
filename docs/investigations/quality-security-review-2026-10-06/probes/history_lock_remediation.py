"""Bounded comparisons of frozen lock sources; never reset a live lock file.

Cold cases exercise full acquisition. Stable cases exercise open/close only,
matching the earlier 2,000-round open-only controls. Product timeouts do not
interrupt native open: a separate supervisor bounds every case to 120 seconds.
No periodic faulthandler dump, protection setting changes, or package initializer
imports are used. Use a fresh output directory and describe protection externally.
"""

from __future__ import annotations

import argparse
import builtins
import csv
import faulthandler
import hashlib
import importlib
import io
import json
import multiprocessing
import os
import sys
import time
import traceback
import types
from collections import Counter
from pathlib import Path

PHASES = {
    0: "startup",
    1: "native_open",
    2: "open_preparation",
    3: "byte_lock",
    4: "initialization",
    5: "protected_operation",
    6: "release",
    7: "barrier",
    8: "finished",
}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_lock(source: Path):
    for name, folder in (
        ("image_downloader", source / "image_downloader"),
        ("image_downloader.storage", source / "image_downloader/storage"),
    ):
        package = types.ModuleType(name)
        package.__path__ = [str(folder)]
        sys.modules[name] = package
    return importlib.import_module("image_downloader.storage.interprocess_lock")


def observe_calls(module, phases, worker_id):
    def wrap(function, phase):
        def observed(*args, **kwargs):
            previous = phases[worker_id]
            phases[worker_id] = phase
            try:
                return function(*args, **kwargs)
            finally:
                phases[worker_id] = previous

        return observed

    def wrap_open(function):
        def observed(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                name = os.fsdecode(os.path.basename(os.fspath(path)))
                if name.endswith(".lock") or name.startswith(".id-lock-"):
                    return wrap(function, 1)(path, *args, **kwargs)
            return function(path, *args, **kwargs)

        return observed

    builtins.open, io.open, os.open = (wrap_open(builtins.open), wrap_open(io.open), wrap_open(os.open))
    lock = module.InterProcessFileLock
    if hasattr(lock, "_open_for_acquisition"):
        lock._open_for_acquisition = wrap(lock._open_for_acquisition, 2)
    if hasattr(lock, "_open_stream"):
        lock._open_stream = wrap(lock._open_stream, 2)
    lock._ensure_lock_byte = staticmethod(wrap(lock._ensure_lock_byte, 4))
    for name in ("_WindowsLockBackend", "_PosixLockBackend"):
        backend = getattr(module, name)
        backend.try_acquire = wrap(backend.try_acquire, 3)
    lock.release = wrap(lock.release, 6)


def exception_details(error: BaseException, iteration: int) -> dict:
    chain, frames = [], []
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "errno": getattr(current, "errno", None),
                "winerror": getattr(current, "winerror", None),
                "diagnostic_doserrno": getattr(current, "_diagnostic_doserrno", None),
            }
        )
        frames.extend(traceback.extract_tb(current.__traceback__))
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    lines = "\n".join(frame.line or "" for frame in frames)
    names = {frame.name for frame in frames}
    if "stream.write(" in lines:
        kind = "initialization_write"
    elif "stream.flush(" in lines:
        kind = "initialization_flush"
    elif "_publish_windows_lock_file" in names:
        kind = "publication"
    elif "release" in names:
        kind = "release"
    elif "try_acquire" in names:
        kind = "byte_lock"
    elif "_open_windows_stream" in names or "_open_stream" in names:
        kind = "open"
    else:
        kind = "other"
    return {"round": iteration, "kind": kind, "chain": chain, "traceback": "".join(traceback.format_exception(error))}


def worker(source, output, condition, rounds, worker_id, barrier, counter, phases, counts):
    sys.dont_write_bytecode = True
    folder = Path(output)
    with (folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        module = load_lock(Path(source))
        observe_calls(module, phases, worker_id)
        errors, latencies = [], []
        protocol_error = None
        try:
            for iteration in range(rounds):
                phases[worker_id] = 7
                barrier.wait(20)
                path = folder / (f"cold-{iteration}.lock" if condition.startswith("cold") else "stable.lock")
                started = time.perf_counter()
                try:
                    phases[worker_id] = 1
                    lock = module.InterProcessFileLock(path, timeout_seconds=5)
                    if condition.startswith("stable"):
                        phases[worker_id] = 2
                        stream = lock._open_stream() if hasattr(lock, "_open_stream") else path.open("a+b")
                        stream.close()
                    else:
                        with lock:
                            phases[worker_id] = 5
                            value = counter.value
                            time.sleep(0.001)
                            counter.value = value + 1
                    counts[worker_id * 3] += 1
                    latencies.append((time.perf_counter() - started) * 1000)
                except Exception as error:
                    detail = exception_details(error, iteration)
                    errors.append(detail)
                    counts[worker_id * 3 + 1] += 1
                    with (folder / f"worker-{worker_id}.errors.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps(detail) + "\n")
                counts[worker_id * 3 + 2] += 1
                phases[worker_id] = 7
                barrier.wait(20)
        except BaseException as error:
            protocol_error = exception_details(error, counts[worker_id * 3 + 2])
        finally:
            phases[worker_id] = 8
            write_json(
                folder / f"worker-{worker_id}.json",
                {
                    "worker": worker_id,
                    "successes": counts[worker_id * 3],
                    "errors": errors,
                    "rounds": counts[worker_id * 3 + 2],
                    "protocol_error": protocol_error,
                    "latency_ms": {"max": max(latencies, default=None), "count": len(latencies)},
                    "module": module.__file__,
                    "sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
                },
            )


def snapshot(path: Path) -> dict | None:
    try:
        state = path.stat()
    except FileNotFoundError:
        return None
    return {"size": state.st_size, "device": state.st_dev, "inode": state.st_ino, "mtime_ns": state.st_mtime_ns}


def case(source, output, condition, rounds):
    folder = Path(output)
    context = multiprocessing.get_context("spawn")
    barrier, counter = context.Barrier(4), context.Value("i", 0, lock=False)
    phases, counts = context.Array("i", 3, lock=False), context.Array("i", 9, lock=False)
    stable = folder / "stable.lock"
    if condition.startswith("stable"):
        stable.write_bytes(b"\0" if condition == "stable-one" else b"existing bytes")
    before = snapshot(stable)
    processes = [
        context.Process(target=worker, args=(source, output, condition, rounds, i, barrier, counter, phases, counts))
        for i in range(3)
    ]
    started, completed, protocol_error, stopped = time.monotonic(), 0, None, []
    try:
        for process in processes:
            process.start()
        for iteration in range(rounds):
            if condition == "cold-empty":
                (folder / f"cold-{iteration}.lock").write_bytes(b"")
            barrier.wait(20)
            barrier.wait(20)
            completed += 1
    except BaseException as error:
        protocol_error = exception_details(error, completed)
    finally:
        for i, process in enumerate(processes):
            if process.pid is None:
                continue
            process.join(5)
            if process.is_alive():
                stopped.append({"worker": i, "pid": process.pid, "phase": PHASES.get(phases[i], "unknown")})
                process.terminate()
                process.join(5)
        workers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("worker-*.json"))]
        errors = [
            json.loads(line)
            for p in folder.glob("*.errors.jsonl")
            for line in p.read_text(encoding="utf-8").splitlines()
        ]
        successes = sum(counts[i * 3] for i in range(3))
        failures = sum(counts[i * 3 + 1] for i in range(3))
        protocol = (
            completed == rounds
            and len(workers) == 3
            and protocol_error is None
            and all(p.exitcode == 0 for p in processes)
            and all(w["rounds"] == rounds and w["protocol_error"] is None for w in workers)
            and successes + failures == rounds * 3
        )
        if condition.startswith("cold"):
            protocol = protocol and counter.value == successes
        result = {
            "condition": condition,
            "operation": "open_only" if condition.startswith("stable") else "acquire",
            "rounds_requested": rounds,
            "rounds_completed": completed,
            "successes": successes,
            "errors": dict(Counter(e["kind"] for e in errors)),
            "failure_count": failures,
            "protocol_completed": protocol,
            "all_operations_succeeded": protocol and failures == 0,
            "protocol_error": protocol_error,
            "stopped_workers": stopped,
            "missing_workers": 3 - len(workers),
            "seconds": time.monotonic() - started,
            "counter": counter.value,
            "workers": workers,
            "exitcodes": [p.exitcode for p in processes],
            "before": before,
            "after": snapshot(stable),
        }
        write_json(folder / "summary.json", result)


def bounded_case(source, folder, condition, rounds):
    folder.mkdir(parents=True, exist_ok=False)
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=case, args=(str(source), str(folder), condition, rounds))
    process.start()
    process.join(120)
    if process.is_alive():
        import psutil

        owner = psutil.Process(process.pid)
        children = owner.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        process.join(5)
        psutil.wait_procs(children, timeout=5)
        for child in children:
            try:
                if child.is_running():
                    child.kill()
            except psutil.NoSuchProcess:
                pass
        return {
            "condition": condition,
            "watchdog": True,
            "all_operations_succeeded": False,
            "folder": str(folder),
            "missing_workers": 3 - len(list(folder.glob("worker-*.json"))),
        }
    summary = folder / "summary.json"
    if not summary.exists():
        return {
            "condition": condition,
            "watchdog": False,
            "protocol_completed": False,
            "all_operations_succeeded": False,
            "folder": str(folder),
            "supervisor_exitcode": process.exitcode,
            "missing_workers": 3 - len(list(folder.glob("worker-*.json"))),
            "fatal_case_exit": True,
        }
    result = json.loads(summary.read_text(encoding="utf-8"))
    result.update({"watchdog": False, "folder": str(folder), "supervisor_exitcode": process.exitcode})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", action="append", required=True, help="label=src directory; repeat for snapshots")
    parser.add_argument("--protection", required=True, help="user-reported condition; does not toggle protection")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cold-rounds", type=int, default=200)
    parser.add_argument("--stable-rounds", type=int, default=2000)
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=["cold-missing", "cold-empty", "stable-one", "stable-many"],
        default=["cold-missing", "cold-empty", "stable-one", "stable-many"],
    )
    args = parser.parse_args()
    if min(args.repeats, args.cold_rounds, args.stable_rounds) <= 0:
        parser.error("rounds and repeats must be positive")
    sources = [(label, Path(value).resolve()) for label, value in (item.split("=", 1) for item in args.source)]
    if len({label for label, _ in sources}) != len(sources) or any(
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
        for condition in args.conditions:
            rounds = args.cold_rounds if condition.startswith("cold") else args.stable_rounds
            for label, source in order:
                result = bounded_case(
                    source, output / f"repeat-{repeat + 1:02d}-{condition}-{label}", condition, rounds
                )
                result.update(
                    {
                        "source": label,
                        "repeat": repeat + 1,
                        "protection": args.protection,
                        "source_sha256": hashlib.sha256(
                            (source / "image_downloader/storage/interprocess_lock.py").read_bytes()
                        ).hexdigest(),
                    }
                )
                results.append(result)
                write_json(output / "evidence.json", results)
                print(
                    json.dumps(
                        {
                            key: result.get(key)
                            for key in (
                                "source",
                                "condition",
                                "repeat",
                                "successes",
                                "failure_count",
                                "errors",
                                "stopped_workers",
                                "all_operations_succeeded",
                                "watchdog",
                                "seconds",
                            )
                        }
                    ),
                    flush=True,
                )
    columns = [
        "source",
        "condition",
        "operation",
        "repeat",
        "protection",
        "rounds_requested",
        "rounds_completed",
        "successes",
        "failure_count",
        "protocol_completed",
        "all_operations_succeeded",
        "missing_workers",
        "watchdog",
        "seconds",
        "source_sha256",
        "errors",
        "stopped_workers",
    ]
    with (output / "runs.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    **result,
                    "errors": json.dumps(result.get("errors")),
                    "stopped_workers": json.dumps(result.get("stopped_workers")),
                }
            )
    return 0 if all(result["all_operations_succeeded"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
