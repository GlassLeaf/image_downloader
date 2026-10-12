"""Run open-only controls without importing any image_downloader module.

The existing variability probe supplies only standard-library orchestration,
file preparation, shared-state sampling, and statistics. No file lock or
initializer is constructed. Periodic traceback dumping is disabled.
"""

from __future__ import annotations

import argparse
import faulthandler
import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path

PUBLIC = Path(__file__).with_name("history_lock_variability.py")
spec = importlib.util.spec_from_file_location("open_only_orchestration", PUBLIC)
assert spec is not None and spec.loader is not None
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


def product_modules() -> list[str]:
    return sorted(name for name in sys.modules if name == "image_downloader" or name.startswith("image_downloader."))


def stdlib_worker(source, folder_name, variant, profile, worker_id, barrier, counter, states):
    del source, counter
    folder = Path(folder_name)
    lock_path = folder / "shared.lock"
    mode = "a+b" if variant == "open-only-append" else "r+b"
    before = product_modules()
    open_times, errors = [], []
    rounds, successes, iteration = 0, 0, -1
    barrier_error = None
    with (folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        try:
            for iteration in range(profile["rounds"]):
                public.set_phase(states, worker_id, "barrier", iteration)
                barrier.wait(profile["barrier"])
                public.set_phase(states, worker_id, "open", iteration)
                began = time.perf_counter()
                try:
                    stream = lock_path.open(mode)
                    open_times.append(time.perf_counter() - began)
                    public.set_phase(states, worker_id, "close", iteration)
                    stream.close()
                    successes += 1
                except Exception:
                    errors.append(traceback.format_exc())
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
                "open_latency": public.distribution(open_times),
                "product_modules_before": before, "product_modules_after": product_modules(),
                "periodic_dump_seconds": 0,
            })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new disposable directory")
    parser.add_argument("--rounds", type=int, default=2000)
    args = parser.parse_args()
    if sys.platform != "win32" or args.rounds <= 0:
        parser.error("requires Windows and positive rounds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    public.worker = stdlib_worker
    evidence = {"started_at": public.now(), "probe_sha256": public.digest(Path(__file__)),
                "orchestration_sha256": public.digest(PUBLIC), "periodic_dump_seconds": 0,
                "parent_product_modules_before": product_modules(), "trials": []}
    for control in public.CONTROLS:
        profile = {"name": control["name"], "rounds": args.rounds, "workers": 3,
                   "hold": 0.0, "lifecycle": control["lifecycle"], "barrier": 60}
        result = public.trial(output, Path(__file__).parent, control["variant"], profile, control["name"])
        evidence["trials"].append(result)
        public.write_json(output / "evidence.json", evidence)
    evidence["finished_at"] = public.now()
    evidence["parent_product_modules_after"] = product_modules()
    public.write_json(output / "evidence.json", evidence)
    return 0 if all(t["protocol_completed"] for t in evidence["trials"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
