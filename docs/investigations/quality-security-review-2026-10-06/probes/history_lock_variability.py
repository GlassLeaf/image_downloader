"""Measure Windows open latency across paired lock and open-only trials.

Only disposable probe files and child-local observation wrappers are used.
The no-initialization variant removes exactly two calls in an isolated copy.
Failed or incomplete trials remain in evidence; they are not retried away.
"""

from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import math
import multiprocessing
import os
import shutil
import statistics
import sys
import threading
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

PHASES = ("starting", "barrier", "open", "initialization", "lock_wait", "held", "close", "finished")
PROFILES = (
    {"name": "short", "rounds": 30, "workers": 3, "hold": 0.001, "lifecycle": "missing", "barrier": 20},
    {"name": "long", "rounds": 300, "workers": 3, "hold": 0.001, "lifecycle": "missing", "barrier": 60},
    {"name": "wide", "rounds": 80, "workers": 8, "hold": 0.001, "lifecycle": "missing", "barrier": 60},
    {"name": "held", "rounds": 80, "workers": 3, "hold": 0.025, "lifecycle": "missing", "barrier": 60},
    {"name": "stable", "rounds": 300, "workers": 3, "hold": 0.001, "lifecycle": "stable-empty", "barrier": 60},
    {"name": "burst", "rounds": 800, "workers": 3, "hold": 0.0, "lifecycle": "missing", "barrier": 60},
)
CONTROLS = (
    {"name": "append-created", "variant": "open-only-append", "lifecycle": "missing"},
    {"name": "append-stable", "variant": "open-only-append", "lifecycle": "stable-seeded"},
    {"name": "readwrite-created", "variant": "open-only-readwrite", "lifecycle": "seeded"},
    {"name": "readwrite-stable", "variant": "open-only-readwrite", "lifecycle": "stable-seeded"},
)


def now() -> str:
    return datetime.now().astimezone().isoformat()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "mean_seconds": statistics.fmean(ordered) if ordered else None,
        "p50_seconds": ordered[math.ceil(len(ordered) * 0.50) - 1] if ordered else None,
        "p95_seconds": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
        "p99_seconds": ordered[math.ceil(len(ordered) * 0.99) - 1] if ordered else None,
        "max_seconds": ordered[-1] if ordered else None,
        "counts_at_least_seconds": {str(t): sum(v >= t for v in ordered) for t in (0.01, 0.1, 1.0, 2.0)},
    }


def set_phase(states, worker_id: int, phase: str, iteration: int) -> None:
    offset = worker_id * 4
    states[offset + 1] = time.monotonic()
    states[offset + 2] = iteration
    states[offset + 3] = os.getpid()
    states[offset] = PHASES.index(phase)


def error_kind(text: str, stage: str) -> str:
    if "line 165, in _ensure_lock_byte" in text and "PermissionError: [Errno 13]" in text:
        return "initialization_flush_PermissionError"
    if "timed out acquiring inter-process file lock" in text:
        return "lock_timeout"
    return f"{stage}_other_error"


def worker(source: str, folder_name: str, variant: str, profile: dict, worker_id: int, barrier, counter, states):
    folder = Path(folder_name)
    with (folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        faulthandler.dump_traceback_later(8, repeat=True, file=stack)
        set_phase(states, worker_id, "starting", -1)
        try:
            worker_operations(source, folder, variant, profile, worker_id, barrier, counter, states)
        except BaseException:
            write_json(folder / f"worker-{worker_id}-fatal.json", {"traceback": traceback.format_exc()})
            raise
        finally:
            faulthandler.cancel_dump_traceback_later()


def worker_operations(source, folder, variant, profile, worker_id, barrier, counter, states):
    sys.path.insert(0, source)
    from image_downloader.storage.interprocess_lock import InterProcessFileLock

    lock_path = folder / "shared.lock"
    original_open = Path.open
    original_initialize = InterProcessFileLock._ensure_lock_byte
    open_times, initialization_times, acquisition_times = [], [], []
    errors, samples = Counter(), []
    completed, successes = 0, 0
    iteration = -1
    stage = "starting"

    def observed_open(path, *args, **kwargs):
        nonlocal stage
        if path != lock_path:
            return original_open(path, *args, **kwargs)
        stage = "open"
        set_phase(states, worker_id, stage, iteration)
        started = time.perf_counter()
        try:
            stream = original_open(path, *args, **kwargs)
        finally:
            open_times.append(time.perf_counter() - started)
        stage = "lock_wait"
        set_phase(states, worker_id, stage, iteration)
        return stream

    def observed_initialize(stream):
        nonlocal stage
        stage = "initialization"
        set_phase(states, worker_id, stage, iteration)
        started = time.perf_counter()
        try:
            original_initialize(stream)
        finally:
            initialization_times.append(time.perf_counter() - started)
        stage = "lock_wait"
        set_phase(states, worker_id, stage, iteration)

    Path.open = observed_open
    InterProcessFileLock._ensure_lock_byte = staticmethod(observed_initialize)
    parent_barrier_error = None
    try:
        for iteration in range(profile["rounds"]):
            set_phase(states, worker_id, "barrier", iteration)
            barrier.wait(profile["barrier"])
            started = time.perf_counter()
            try:
                if variant.startswith("open-only"):
                    mode = "a+b" if variant == "open-only-append" else "r+b"
                    stream = lock_path.open(mode)
                    set_phase(states, worker_id, "close", iteration)
                    stream.close()
                else:
                    with InterProcessFileLock(lock_path, timeout_seconds=5):
                        set_phase(states, worker_id, "held", iteration)
                        current = counter.value
                        if profile["hold"]:
                            time.sleep(profile["hold"])
                        counter.value = current + 1
                        set_phase(states, worker_id, "close", iteration)
                successes += 1
            except Exception:
                text = traceback.format_exc()
                kind = error_kind(text, stage)
                errors[kind] += 1
                if sum(s["kind"] == kind for s in samples) < 5:
                    samples.append({"iteration": iteration, "kind": kind, "traceback": text})
            finally:
                acquisition_times.append(time.perf_counter() - started)
            completed += 1
            set_phase(states, worker_id, "barrier", iteration)
            barrier.wait(profile["barrier"])
    except Exception:
        parent_barrier_error = traceback.format_exc()
    finally:
        Path.open = original_open
        InterProcessFileLock._ensure_lock_byte = staticmethod(original_initialize)
        set_phase(states, worker_id, "finished", iteration)
        write_json(folder / f"worker-{worker_id}.json", {
            "pid": os.getpid(), "worker": worker_id, "rounds": completed, "successes": successes,
            "errors_by_kind": dict(errors), "errors": sum(errors.values()), "samples": samples,
            "barrier_error": parent_barrier_error, "open_latency": distribution(open_times),
            "initialization_latency": distribution(initialization_times),
            "operation_latency": distribution(acquisition_times),
            "source_module": sys.modules["image_downloader.storage.interprocess_lock"].__file__,
            "has_initialization": "_ensure_lock_byte" in InterProcessFileLock.acquire.__code__.co_names,
        })


def monitor(states, processes, stop, events):
    seen = {}
    while not stop.wait(0.25):
        current = time.monotonic()
        for worker_id, process in enumerate(processes):
            offset = worker_id * 4
            phase = PHASES[int(states[offset])]
            began, iteration, pid = states[offset + 1], int(states[offset + 2]), int(states[offset + 3])
            elapsed = current - began
            if phase not in {"open", "initialization", "starting"} or elapsed < 2:
                continue
            key = worker_id, phase, iteration
            if key in seen:
                events[seen[key]]["last_observed_pending_seconds"] = elapsed
                continue
            seen[key] = len(events)
            events.append({"observed_at": now(), "worker": worker_id, "phase": phase,
                           "iteration": iteration, "pid": pid or process.pid,
                           "first_observed_pending_seconds": elapsed, "last_observed_pending_seconds": elapsed})


def reset_lock(path: Path, lifecycle: str, iteration: int):
    if lifecycle.startswith("stable") and iteration > 0:
        return
    if lifecycle == "missing":
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(b"\0" if lifecycle.endswith("seeded") else b"")


def trial(output: Path, source: Path, variant: str, profile: dict, label: str) -> dict:
    folder = output / label
    folder.mkdir()
    context = multiprocessing.get_context("spawn")
    count = profile["workers"]
    barrier, counter = context.Barrier(count + 1), context.Value("i", 0, lock=False)
    states = context.Array("d", count * 4, lock=False)
    for worker_id in range(count):
        set_phase(states, worker_id, "starting", -1)
        states[worker_id * 4 + 3] = 0
    processes = [context.Process(target=worker, args=(
        str(source), str(folder), variant, profile, worker_id, barrier, counter, states,
    )) for worker_id in range(count)]
    stop, events = threading.Event(), []
    observer = threading.Thread(target=monitor, args=(states, processes, stop, events), daemon=True)
    started_at, started = now(), time.monotonic()
    completed, parent_error = 0, None
    print(json.dumps({"started": label, "profile": profile, "variant": variant, "at": started_at}), flush=True)
    observer.start()
    try:
        for process in processes:
            process.start()
        for iteration in range(profile["rounds"]):
            reset_lock(folder / "shared.lock", profile["lifecycle"], iteration)
            barrier.wait(profile["barrier"])
            barrier.wait(profile["barrier"])
            completed += 1
    except Exception:
        parent_error = traceback.format_exc()
    finally:
        for process in processes:
            if process.pid is None:
                continue
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
        stop.set()
        observer.join(1)
    workers = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("worker-*.json"))
               if not path.name.endswith("-fatal.json")]
    successes = sum(w["successes"] for w in workers)
    errors = Counter()
    for result in workers:
        errors.update(result["errors_by_kind"])
    complete = completed == profile["rounds"] and parent_error is None and len(workers) == count
    complete = complete and all(p.exitcode == 0 for p in processes)
    complete = complete and all(w["rounds"] == profile["rounds"] and w["barrier_error"] is None for w in workers)
    complete = complete and (variant.startswith("open-only") or counter.value == successes)
    complete = complete and successes + sum(errors.values()) == profile["rounds"] * count
    result = {
        "label": label, "profile": profile, "variant": variant, "started_at": started_at, "finished_at": now(),
        "seconds": time.monotonic() - started, "rounds": completed, "planned_attempts": profile["rounds"] * count,
        "successful_operations": successes, "errors": sum(errors.values()), "errors_by_kind": dict(errors),
        "protocol_completed": complete, "parent_error": parent_error, "counter": counter.value,
        "process_exitcodes": [p.exitcode for p in processes], "workers": workers,
        "long_pending_events": events,
        "final_states": [{"worker": i, "phase": PHASES[int(states[i * 4])],
                          "iteration": int(states[i * 4 + 2]), "pid": int(states[i * 4 + 3])} for i in range(count)],
        "stacks": {path.name: path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.stack.txt"))},
    }
    write_json(folder / "summary.json", result)
    print(json.dumps({"completed": label, "complete": complete, "rounds": completed, "errors": sum(errors.values()),
                      "seconds": round(result["seconds"], 3), "pending_events": events}), flush=True)
    return result


def isolated_source(repository: Path, output: Path) -> Path:
    source = output / "no-initialization-source/src"
    shutil.copytree(repository / "src", source, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    lock = source / "image_downloader/storage/interprocess_lock.py"
    text = lock.read_text(encoding="utf-8")
    needle = "            self._ensure_lock_byte(stream)\n"
    if text.count(needle) != 2:
        raise RuntimeError("expected exactly two initialization calls")
    lock.write_text(text.replace(needle, ""), encoding="utf-8")
    return source


def run_matrix(repository: Path, output: Path, repeats: int, control_rounds: int, selected: set[str]) -> dict:
    isolated = isolated_source(repository, output)
    lock_file = repository / "src/image_downloader/storage/interprocess_lock.py"
    record = {"started_at": now(), "source_sha256_before": digest(lock_file), "trials": [],
              "instrumentation": "Child-local open/initializer wrappers; perf_counter; state sampler every 0.25s",
              "pending_threshold_seconds": 2, "lock_timeout_seconds": 5,
              "no_initialization_change": "Exactly two initialization calls removed from isolated source"}
    for repetition in range(1, repeats + 1):
        profiles = list(PROFILES)
        if repetition % 2 == 0:
            profiles.reverse()
        if repetition > 2:
            profiles = profiles[2:] + profiles[:2]
        for profile in profiles:
            if profile["name"] not in selected:
                continue
            variants = ("current", "no-initialization") if repetition % 2 else ("no-initialization", "current")
            for variant in variants:
                label = f"repeat-{repetition:02}-{profile['name']}-{variant}"
                source = repository / "src" if variant == "current" else isolated
                record["trials"].append(trial(output, source, variant, dict(profile), label))
                write_json(output / "evidence.json", record)
        if "controls" in selected:
            controls = CONTROLS if repetition % 2 else tuple(reversed(CONTROLS))
            for control in controls:
                profile = {"name": control["name"], "rounds": control_rounds, "workers": 3,
                           "hold": 0.0, "lifecycle": control["lifecycle"], "barrier": 60}
                label = f"repeat-{repetition:02}-{control['name']}"
                record["trials"].append(trial(output, repository / "src", control["variant"], profile, label))
                write_json(output / "evidence.json", record)
    record["finished_at"] = now()
    record["source_sha256_after"] = digest(lock_file)
    record["source_unchanged"] = record["source_sha256_before"] == record["source_sha256_after"]
    record["isolated_source_sha256"] = digest(isolated / "image_downloader/storage/interprocess_lock.py")
    write_json(output / "evidence.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new disposable directory")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--control-rounds", type=int, default=2000)
    parser.add_argument("--profiles", nargs="+", choices=(*[p["name"] for p in PROFILES], "controls"),
                        default=(*[p["name"] for p in PROFILES], "controls"))
    args = parser.parse_args()
    if sys.platform != "win32" or args.repeats <= 0 or args.control_rounds <= 0:
        parser.error("requires Windows and positive counts")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    repository = Path(__file__).resolve().parents[4]
    record = run_matrix(repository, output, args.repeats, args.control_rounds, set(args.profiles))
    return 0 if record["source_unchanged"] and all(t["protocol_completed"] for t in record["trials"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
