"""Run the historical lock module in a fresh, bounded Windows contention case.

Package initializers are excluded so the lock and its exception definitions can
be compared without unrelated historical application imports. The exact checked
out module is used; its contents, open mode, initializer and backend are unchanged.
Each worker result records the loaded module path. No periodic stack dump is used.
"""

from __future__ import annotations

import argparse
import faulthandler
import hashlib
import importlib
import json
import multiprocessing
import sys
import time
import traceback
import types
from collections import Counter
from pathlib import Path


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_lock(source: Path):
    for name, directory in (("image_downloader", source / "image_downloader"),
                            ("image_downloader.storage", source / "image_downloader/storage")):
        package = types.ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package
    return importlib.import_module("image_downloader.storage.interprocess_lock")


def worker(source: str, folder: str, rounds: int, worker_id: int, barrier, counter) -> None:
    sys.dont_write_bytecode = True
    output = Path(folder)
    with (output / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        module = load_lock(Path(source))
        successes, completed, errors, samples = 0, 0, Counter(), []
        protocol_error = None
        try:
            for iteration in range(rounds):
                barrier.wait(20)
                try:
                    with module.InterProcessFileLock(output / "shared.lock", timeout_seconds=5):
                        value = counter.value
                        time.sleep(0.001)
                        counter.value = value + 1
                    successes += 1
                except Exception:
                    trace = traceback.format_exc()
                    kind = ("initialization_flush_PermissionError"
                            if "in _ensure_lock_byte" in trace and "stream.flush()" in trace
                            and "PermissionError: [Errno 13]" in trace else "other")
                    errors[kind] += 1
                    if sum(s["kind"] == kind for s in samples) < 3:
                        samples.append({"round": iteration, "kind": kind, "traceback": trace})
                completed += 1
                barrier.wait(20)
        except BaseException:
            protocol_error = traceback.format_exc()
        finally:
            write(output / f"worker-{worker_id}.json", {
                "worker": worker_id, "rounds": completed, "successes": successes,
                "errors": dict(errors), "samples": samples, "protocol_error": protocol_error,
                "module": module.__file__,
                "module_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
            })


def run(source: Path, output: Path, rounds: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    context = multiprocessing.get_context("spawn")
    barrier, counter = context.Barrier(4), context.Value("i", 0, lock=False)
    processes = [context.Process(target=worker, args=(str(source), str(output), rounds, i, barrier, counter))
                 for i in range(3)]
    started, completed, protocol_error = time.monotonic(), 0, None
    try:
        for process in processes:
            process.start()
        for _ in range(rounds):
            (output / "shared.lock").unlink(missing_ok=True)
            barrier.wait(20)
            barrier.wait(20)
            completed += 1
    except BaseException:
        protocol_error = traceback.format_exc()
    finally:
        for process in processes:
            if process.pid is None:
                continue
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
    workers = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(output.glob("worker-*.json"))]
    errors = Counter()
    for result in workers:
        errors.update(result["errors"])
    successful = sum(r["successes"] for r in workers)
    complete = (completed == rounds and len(workers) == 3 and protocol_error is None
                and all(r["rounds"] == rounds and r["protocol_error"] is None for r in workers)
                and all(p.exitcode == 0 for p in processes) and counter.value == successful
                and successful + sum(errors.values()) == rounds * 3)
    result = {"rounds_requested": rounds, "rounds_completed": completed, "attempts_requested": rounds * 3,
              "seconds": time.monotonic() - started, "counter": counter.value, "workers": workers,
              "errors": dict(errors), "successes": successful, "protocol_completed": complete,
              "protocol_error": protocol_error, "exitcodes": [p.exitcode for p in processes],
              "stacks": {p.name: p.read_text(encoding="utf-8") for p in output.glob("*.stack.txt")}}
    write(output / "summary.json", result)
    print(json.dumps({k: result[k] for k in ("rounds_completed", "seconds", "errors", "protocol_completed")}),
          flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rounds", type=int, default=30)
    args = parser.parse_args()
    if sys.platform != "win32" or args.rounds <= 0:
        parser.error("requires Windows and positive rounds")
    result = run(args.source.resolve(), args.output.resolve(), args.rounds)
    return 0 if result["protocol_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
