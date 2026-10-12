"""Run variability cases with a separate owned-process deadline for every case.

Periodic traceback dumps are disabled by default because this investigation
observed native crashes during dumps on Python 3.11.4. Fatal reporting and the
shared-state sampler remain enabled. This changes no product source or settings.
"""

from __future__ import annotations

import argparse
import faulthandler
import importlib.util
import json
import math
import re
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PUBLIC = Path(__file__).with_name("history_lock_variability.py")
spec = importlib.util.spec_from_file_location("variability_probe", PUBLIC)
assert spec is not None and spec.loader is not None
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


def delegated_worker(source, folder_name, variant, profile, worker_id, barrier, counter, states):
    folder = Path(folder_name)
    with (folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        period = profile["dump_period_seconds"]
        if period:
            faulthandler.dump_traceback_later(period, repeat=True, file=stack)
        public.set_phase(states, worker_id, "starting", -1)
        try:
            public.worker_operations(source, folder, variant, profile, worker_id, barrier, counter, states)
        except BaseException:
            public.write_json(folder / f"worker-{worker_id}-fatal.json", {"traceback": traceback.format_exc()})
            raise
        finally:
            if period:
                faulthandler.cancel_dump_traceback_later()


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


def run_case(output, source, label, variant, profile, timeout):
    command = [sys.executable, str(Path(__file__).resolve()), str(output), "--child-label", label,
               "--child-source", str(source), "--child-variant", variant, "--child-profile", json.dumps(profile),
               "--control-rounds", str(profile["rounds"])]
    execution = {"command": command, "started_at": public.now(), "timed_out": False}
    with (output / f"{label}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
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
        "label": label, "variant": variant, "profile": profile, "protocol_completed": False,
        "statistics_available": False, "reason": "Case did not return a summary; missing data is not zero errors",
        "stacks": {p.name: p.read_text(encoding="utf-8") for p in (output / label).glob("*.stack.txt")},
    }
    print(json.dumps({"case": label, "complete": observation["protocol_completed"],
                      "timed_out": execution["timed_out"]}), flush=True)
    return {"execution": execution, "observation": observation}


def child_case(args, output):
    if re.fullmatch(r"[a-z0-9-]+", args.child_label) is None:
        raise ValueError("invalid child label")
    source = args.child_source.resolve()
    if source not in {(ROOT / "src").resolve(), (output / "no-initialization-source/src").resolve()}:
        raise ValueError("child source must be the repository or its owned isolated copy")
    profile = json.loads(args.child_profile)
    allowed = [dict(p) for p in public.PROFILES]
    allowed += [{"name": c["name"], "rounds": args.control_rounds, "workers": 3,
                 "hold": 0.0, "lifecycle": c["lifecycle"], "barrier": 60} for c in public.CONTROLS]
    period = profile.pop("dump_period_seconds")
    if profile not in allowed or not math.isfinite(period) or period < 0:
        raise ValueError("invalid child profile")
    profile["dump_period_seconds"] = period
    public.worker = delegated_worker
    result = public.trial(output, source, args.child_variant, profile, args.child_label)
    return 0 if result["protocol_completed"] else 1


def run_matrix(args, output):
    output.mkdir(parents=True, exist_ok=False)
    isolated = public.isolated_source(ROOT, output)
    evidence = {"started_at": public.now(), "probe_sha256": public.digest(PUBLIC),
                "dump_period_seconds": args.dump_period, "case_timeout_seconds": args.case_timeout,
                "source_sha256_before": public.digest(ROOT / "src/image_downloader/storage/interprocess_lock.py"),
                "trials": []}
    for repetition in range(1, args.repeats + 1):
        profiles = public.PROFILES if repetition % 2 else tuple(reversed(public.PROFILES))
        for profile in profiles:
            if profile["name"] not in args.profiles:
                continue
            order = ("current", "no-initialization") if repetition % 2 else ("no-initialization", "current")
            for variant in order:
                label = f"repeat-{repetition:02}-{profile['name']}-{variant}"
                source = ROOT / "src" if variant == "current" else isolated
                selected = {**profile, "dump_period_seconds": args.dump_period}
                evidence["trials"].append(run_case(output, source, label, variant, selected, args.case_timeout))
                public.write_json(output / "evidence.json", evidence)
        if "controls" in args.profiles:
            for control in public.CONTROLS:
                profile = {"name": control["name"], "rounds": args.control_rounds, "workers": 3,
                           "hold": 0.0, "lifecycle": control["lifecycle"], "barrier": 60,
                           "dump_period_seconds": args.dump_period}
                label = f"repeat-{repetition:02}-{control['name']}"
                evidence["trials"].append(run_case(
                    output, ROOT / "src", label, control["variant"], profile, args.case_timeout))
                public.write_json(output / "evidence.json", evidence)
    evidence["finished_at"] = public.now()
    evidence["source_sha256_after"] = public.digest(ROOT / "src/image_downloader/storage/interprocess_lock.py")
    public.write_json(output / "evidence.json", evidence)
    return 0 if all(t["observation"]["protocol_completed"] for t in evidence["trials"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new disposable output directory")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--control-rounds", type=int, default=2000)
    parser.add_argument("--case-timeout", type=float, default=240)
    parser.add_argument("--dump-period", type=float, default=0)
    parser.add_argument("--profiles", nargs="+", choices=(*[p["name"] for p in public.PROFILES], "controls"),
                        default=(*[p["name"] for p in public.PROFILES], "controls"))
    parser.add_argument("--child-label", help=argparse.SUPPRESS)
    parser.add_argument("--child-source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-variant", help=argparse.SUPPRESS)
    parser.add_argument("--child-profile", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (sys.platform != "win32" or args.repeats <= 0 or args.control_rounds <= 0
            or not math.isfinite(args.case_timeout) or args.case_timeout <= 0
            or not math.isfinite(args.dump_period) or args.dump_period < 0):
        parser.error("requires Windows, positive counts/deadline, and a finite nonnegative dump period")
    output = args.output.resolve()
    return child_case(args, output) if args.child_label else run_matrix(args, output)


if __name__ == "__main__":
    raise SystemExit(main())
