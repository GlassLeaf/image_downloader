"""Compare precreated, retained 0/1-byte files with r+b, w+b, and a+b.

Workers only open/close the target: no write, explicit flush, or byte lock.
The parent closes its preparation handle before releasing the start barrier.
Periodic faulthandler dumps are disabled. Each case has an external deadline.
psutil is used only by the outer supervisor when terminating its own children.
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

PUBLIC = Path(__file__).with_name("history_lock_variability.py")
spec = importlib.util.spec_from_file_location("size_mode_orchestration", PUBLIC)
assert spec is not None and spec.loader is not None
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)
ROOT = Path(__file__).resolve().parents[4]
MODES = {"readwrite": "r+b", "truncate": "w+b", "append": "a+b"}


def product_modules():
    return sorted(n for n in sys.modules if n == "image_downloader" or n.startswith("image_downloader."))


def loaded_guard():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    handle = kernel.GetModuleHandleW("guard64.dll")
    name = ctypes.create_unicode_buffer(32768)
    if handle:
        kernel.GetModuleFileNameW(handle, name, len(name))
    return {"loaded": bool(handle), "path": name.value if handle else None}


def snapshot(path):
    try:
        value = path.stat()
        return {"exists": True, "size": value.st_size, "device": value.st_dev,
                "inode": value.st_ino, "mtime_ns": value.st_mtime_ns}
    except FileNotFoundError:
        return {"exists": False}


def open_worker(source, folder_name, variant, profile, worker_id, barrier, counter, states):
    del source, variant, counter
    folder = Path(folder_name)
    lock_path = folder / "shared.lock"
    before, guard = product_modules(), loaded_guard()
    times, errors = [], []
    rounds, successes, iteration = 0, 0, -1
    barrier_error = None
    public.write_json(folder / f"startup-{worker_id}.json", {
        "pid": os.getpid(), "product_modules": before, "guard64": guard,
    })
    with (folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        try:
            for iteration in range(profile["rounds"]):
                public.set_phase(states, worker_id, "barrier", iteration)
                barrier.wait(profile["barrier"])
                public.set_phase(states, worker_id, "open", iteration)
                began = time.perf_counter()
                stage = "open"
                try:
                    stream = lock_path.open(profile["mode"])
                    times.append(time.perf_counter() - began)
                    stage = "close"
                    public.set_phase(states, worker_id, stage, iteration)
                    stream.close()
                    successes += 1
                except Exception:
                    errors.append({"stage": stage, "iteration": iteration, "traceback": traceback.format_exc()})
                rounds += 1
                public.set_phase(states, worker_id, "barrier", iteration)
                barrier.wait(profile["barrier"])
        except Exception:
            barrier_error = traceback.format_exc()
        finally:
            public.set_phase(states, worker_id, "finished", iteration)
            public.write_json(folder / f"worker-{worker_id}.json", {
                "pid": os.getpid(), "worker": worker_id, "rounds": rounds, "successes": successes,
                "errors": len(errors), "errors_by_kind": {"open_or_close_error": len(errors)} if errors else {},
                "samples": errors[:5], "barrier_error": barrier_error,
                "open_latency": public.distribution(times), "product_modules_before": before,
                "product_modules_after": product_modules(), "guard64": guard, "periodic_dump_seconds": 0,
            })


def child_case(args, output):
    if re.fullmatch(r"[a-z0-9-]+", args.child_label) is None:
        raise ValueError("invalid child label")
    profile = json.loads(args.child_profile)
    if profile["mode"] not in MODES.values() or profile["lifecycle"] not in {
        "stable-empty", "stable-seeded", "missing", "seeded",
    }:
        raise ValueError("invalid child profile")
    preparation = {"snapshots": []}
    original_reset = public.reset_lock

    def observe_preparation(path, lifecycle, iteration):
        if iteration == 1:
            preparation["snapshots"].append({"when": "after_first_round", **snapshot(path)})
        original_reset(path, lifecycle, iteration)
        if iteration == 0:
            preparation["snapshots"].append({"when": "before_first_round", **snapshot(path)})
        if iteration <= 1:
            public.write_json(path.parent / "preparation.json", preparation)

    public.reset_lock = observe_preparation
    public.worker = open_worker
    result = public.trial(output, ROOT, "open-only-size-mode", profile, args.child_label)
    preparation["snapshots"].append({"when": "after_trial", **snapshot(output / args.child_label / "shared.lock")})
    result.update({"preparation": preparation, "parent_product_modules": product_modules(),
                   "parent_guard64": loaded_guard()})
    public.write_json(output / args.child_label / "summary.json", result)
    return 0 if result["protocol_completed"] and result["errors"] == 0 else 1


def stop_owned(process):
    import psutil

    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
    for child in descendants:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(descendants, timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=10)


def run_case(output, label, profile, timeout):
    command = [sys.executable, str(Path(__file__).resolve()), str(output), "--child-label", label,
               "--child-profile", json.dumps(profile)]
    execution = {"command": command, "started_at": public.now(), "timed_out": False}
    print(json.dumps({"started": label, "profile": profile, "at": execution["started_at"]}), flush=True)
    with (output / f"{label}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        execution["pid"] = process.pid
        try:
            execution["exitcode"] = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            execution["timed_out"] = True
            stop_owned(process)
            execution["exitcode"] = process.returncode
        except BaseException:
            stop_owned(process)
            raise
    execution["finished_at"] = public.now()
    summary = output / label / "summary.json"
    observation = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else {
        "label": label, "profile": profile, "protocol_completed": False,
        "statistics_available": False, "reason": "Missing summary is not zero errors",
    }
    result = {"execution": execution, "observation": observation}
    public.write_json(output / f"{label}-execution.json", execution)
    print(json.dumps({"completed": label, "complete": observation["protocol_completed"],
                      "rounds": observation.get("rounds"), "timed_out": execution["timed_out"],
                      "pending": observation.get("long_pending_events")}), flush=True)
    return result


def profiles(args):
    base = {"rounds": args.rounds, "workers": 3, "hold": 0.0, "barrier": 60}
    cases = []
    for size in (0, 1):
        for name, mode in MODES.items():
            cases.append({**base, "name": f"stable-{size}byte-{name}", "mode": mode,
                          "initial_size": size, "lifecycle": "stable-seeded" if size else "stable-empty"})
    if args.controls:
        cases += [{**base, "name": "control-append-created", "mode": "a+b", "initial_size": None,
                   "lifecycle": "missing"},
                  {**base, "name": "control-readwrite-rewritten", "mode": "r+b", "initial_size": 1,
                   "lifecycle": "seeded"}]
    return cases


def run_matrix(args, output):
    output.mkdir(parents=True, exist_ok=False)
    evidence = {"started_at": public.now(), "python": sys.version, "executable": sys.executable,
                "probe_sha256": public.digest(Path(__file__)), "orchestration_sha256": public.digest(PUBLIC),
                "case_timeout_seconds": args.case_timeout, "periodic_dump_seconds": 0, "trials": []}
    cases = profiles(args)
    for repetition in range(1, args.repeats + 1):
        order = cases if repetition % 2 else list(reversed(cases))
        if repetition > 2:
            order = order[2:] + order[:2]
        for profile in order:
            label = f"repeat-{repetition:02}-{profile['name']}"
            evidence["trials"].append(run_case(output, label, profile, args.case_timeout))
            public.write_json(output / "evidence.json", evidence)
    evidence["finished_at"] = public.now()
    public.write_json(output / "evidence.json", evidence)
    return 0 if all(t["execution"]["exitcode"] == 0 for t in evidence["trials"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new disposable output directory")
    parser.add_argument("--rounds", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case-timeout", type=float, default=120)
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--child-label", help=argparse.SUPPRESS)
    parser.add_argument("--child-profile", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (sys.platform != "win32" or args.rounds <= 0 or args.repeats <= 0
            or not math.isfinite(args.case_timeout) or args.case_timeout <= 0):
        parser.error("requires Windows, positive counts, and a finite positive deadline")
    output = args.output.resolve()
    return child_case(args, output) if args.child_label else run_matrix(args, output)


if __name__ == "__main__":
    raise SystemExit(main())
