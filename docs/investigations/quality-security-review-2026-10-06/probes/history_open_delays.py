"""Compare pre-open, handle-held, and post-close delays in disposable files.

Default Path.open behavior is preserved. Optional --native-helper uses an
opener that captures _wopen's return and error fields before Python resumes.
Returned Python exceptions and nonreturning opens are recorded separately.
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

BASE = Path(__file__).with_name("history_open_size_modes.py")
spec = importlib.util.spec_from_file_location("size_modes_base", BASE)
assert spec is not None and spec.loader is not None
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
public = base.public
public.PHASES += ("pre_open_wait", "post_open_wait", "post_close_wait")
TIMINGS = {"zero": (0, 0, 0), "pre-10": (10, 0, 0), "pre-100": (100, 0, 0),
           "held-10": (0, 10, 0), "held-100": (0, 100, 0),
           "closed-10": (0, 0, 10), "closed-100": (0, 0, 100)}


class OpenResult(ctypes.Structure):
    _fields_ = [("result", ctypes.c_int), ("last_error", ctypes.c_uint32),
                ("crt_errno", ctypes.c_int), ("doserrno", ctypes.c_ulong)]


def error_reader():
    kernel = ctypes.WinDLL("kernel32", use_last_error=False)
    kernel.GetLastError.restype = ctypes.c_uint32
    crt = ctypes.CDLL("ucrtbase", use_last_error=False)
    crt._get_doserrno.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
    crt._get_doserrno.restype = ctypes.c_int
    crt._set_doserrno.argtypes = [ctypes.c_ulong]
    dos = ctypes.c_ulong()

    def read():
        last = kernel.GetLastError()
        status = crt._get_doserrno(ctypes.byref(dos))
        return {"thread_last_error_after_python_exception": last,
                "doserrno_after_python_exception": dos.value, "doserrno_read_status": status,
                "scope": "late diagnostic; not guaranteed original Win32 call result"}

    return read, lambda: crt._set_doserrno(0)


def native_opener(path):
    dll = ctypes.CDLL(str(Path(path).resolve()))
    dll.capture_wopen.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(OpenResult)]
    dll.capture_wopen.restype = None
    observations = []

    def opener(filename, flags):
        result = OpenResult()
        dll.capture_wopen(os.fspath(filename), flags, ctypes.byref(result))
        value = {name: getattr(result, name) for name, _ in OpenResult._fields_}
        value["flags"] = flags
        if result.result < 0:
            observations.append(value)
            raise OSError(result.crt_errno, os.strerror(result.crt_errno), filename)
        return result.result

    return opener, observations


def wait_phase(profile, key, phase, worker_id, iteration, states, samples):
    if profile[key]:
        public.set_phase(states, worker_id, phase, iteration)
        began = time.perf_counter()
        time.sleep(profile[key] / 1000)
        samples.append(time.perf_counter() - began)


def operations(folder, profile, worker_id, barrier, states, error_file):
    path = folder / "shared.lock"
    reader, clear_dos = error_reader()
    opener, native_errors = native_opener(profile["native_helper"]) if profile["native_helper"] else (None, [])
    times, close_times, errors = [], [], []
    delays = {"pre_open_ms": [], "post_open_ms": [], "post_close_ms": []}
    rounds, successes, iteration, barrier_error = 0, 0, -1, None
    try:
        for iteration in range(profile["rounds"]):
            public.set_phase(states, worker_id, "barrier", iteration)
            barrier.wait(profile["barrier"])
            wait_phase(profile, "pre_open_ms", "pre_open_wait", worker_id, iteration, states, delays["pre_open_ms"])
            clear_dos()
            public.set_phase(states, worker_id, "open", iteration)
            stage, began = "open", time.perf_counter()
            native_start = len(native_errors)
            try:
                stream = (open(path, profile["mode"], opener=opener)
                          if opener else path.open(profile["mode"]))
                times.append(time.perf_counter() - began)
                stage = "post_open_wait"
                wait_phase(profile, "post_open_ms", stage, worker_id, iteration, states, delays["post_open_ms"])
                stage = "close"
                public.set_phase(states, worker_id, stage, iteration)
                began = time.perf_counter()
                stream.close()
                close_times.append(time.perf_counter() - began)
                successes += 1
            except Exception as exc:
                diagnostic = reader()
                event = {"stage": stage, "iteration": iteration, "type": type(exc).__name__,
                         "errno": getattr(exc, "errno", None), "winerror": getattr(exc, "winerror", None),
                         "diagnostic": diagnostic, "native_boundary": native_errors[native_start:],
                         "traceback": traceback.format_exc()}
                errors.append(event)
                error_file.write(json.dumps(event) + "\n")
                error_file.flush()
            rounds += 1
            wait_phase(profile, "post_close_ms", "post_close_wait", worker_id, iteration,
                       states, delays["post_close_ms"])
            public.set_phase(states, worker_id, "barrier", iteration)
            barrier.wait(profile["barrier"])
    except Exception:
        barrier_error = traceback.format_exc()
    finally:
        public.set_phase(states, worker_id, "finished", iteration)
    return {"pid": os.getpid(), "worker": worker_id, "rounds": rounds, "successes": successes,
            "errors": len(errors), "errors_by_kind": {"returned_open_or_close_error": len(errors)} if errors else {},
            "samples": errors[:10], "barrier_error": barrier_error, "open_latency": public.distribution(times),
            "close_latency": public.distribution(close_times), "native_boundary_errors": native_errors,
            "actual_delay": {key: public.distribution(values) for key, values in delays.items()}}


def delay_worker(source, folder_name, variant, profile, worker_id, barrier, counter, states):
    del source, variant, counter
    folder = Path(folder_name)
    before, guard = base.product_modules(), base.loaded_guard()
    public.write_json(folder / f"startup-{worker_id}.json", {"pid": os.getpid(), "product_modules": before,
                                                           "guard64": guard})
    with ((folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack,
          (folder / f"errors-{worker_id}.jsonl").open("w", encoding="utf-8") as error_file):
        faulthandler.enable(file=stack)
        result = operations(folder, profile, worker_id, barrier, states, error_file)
    result.update({"product_modules_before": before, "product_modules_after": base.product_modules(),
                   "guard64": guard, "periodic_dump_seconds": 0})
    public.write_json(folder / f"worker-{worker_id}.json", result)


def run_case(output, label, profile, timeout):
    command = [sys.executable, str(Path(__file__).resolve()), str(output), "--child-label", label,
               "--child-profile", json.dumps(profile)]
    execution = {"command": command, "started_at": public.now(), "timed_out": False}
    print(json.dumps({"started": label, "profile": profile}), flush=True)
    with (output / f"{label}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=base.ROOT, stdout=log, stderr=subprocess.STDOUT)
        execution["pid"] = process.pid
        try:
            execution["exitcode"] = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            execution["timed_out"] = True
            base.stop_owned(process)
            execution["exitcode"] = process.returncode
        except BaseException:
            base.stop_owned(process)
            raise
    execution["finished_at"] = public.now()
    path = output / label / "summary.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "label": label, "profile": profile, "protocol_completed": False, "statistics_available": False,
    }
    events = []
    for path in (output / label).glob("errors-*.jsonl"):
        events += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    result["durable_returned_error_events"] = events
    print(json.dumps({"completed": label, "complete": result["protocol_completed"],
                      "rounds": result.get("rounds"), "returned_errors": len(events),
                      "pending": result.get("long_pending_events"), "outer_timeout": execution["timed_out"]}),
          flush=True)
    return {"execution": execution, "observation": result}


def run_matrix(args, output):
    output.mkdir(parents=True, exist_ok=False)
    evidence = {"started_at": public.now(), "probe_sha256": public.digest(Path(__file__)),
                "base_sha256": public.digest(BASE), "python": sys.version, "trials": []}
    cases = base.profiles(args)
    for repetition in range(1, args.repeats + 1):
        timings = args.timings if repetition % 2 else list(reversed(args.timings))
        for timing in timings:
            for original in cases if repetition % 2 else list(reversed(cases)):
                if args.conditions and original["name"] not in args.conditions:
                    continue
                pre, held, closed = TIMINGS[timing]
                profile = {**original, "barrier": args.barrier, "timing": timing, "pre_open_ms": pre,
                           "post_open_ms": held, "post_close_ms": closed,
                           "native_helper": str(args.native_helper.resolve()) if args.native_helper else None}
                label = f"repeat-{repetition:02}-{timing}-{profile['name']}"
                evidence["trials"].append(run_case(output, label, profile, args.case_timeout))
                public.write_json(output / "evidence.json", evidence)
    evidence["finished_at"] = public.now()
    public.write_json(output / "evidence.json", evidence)
    return 0 if all(t["execution"]["exitcode"] == 0 for t in evidence["trials"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--barrier", type=float, default=10)
    parser.add_argument("--case-timeout", type=float, default=40)
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--timings", nargs="+", choices=tuple(TIMINGS), default=tuple(TIMINGS))
    parser.add_argument("--conditions", nargs="+")
    parser.add_argument("--native-helper", type=Path)
    parser.add_argument("--child-label", help=argparse.SUPPRESS)
    parser.add_argument("--child-profile", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (sys.platform != "win32" or args.rounds <= 0 or args.repeats <= 0
            or not all(math.isfinite(v) and v > 0 for v in (args.barrier, args.case_timeout))):
        parser.error("requires Windows and positive finite counts/deadlines")
    if args.child_label:
        if re.fullmatch(r"[a-z0-9-]+", args.child_label) is None:
            parser.error("invalid child label")
        base.open_worker = delay_worker
        return base.child_case(args, args.output.resolve())
    return run_matrix(args, args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
