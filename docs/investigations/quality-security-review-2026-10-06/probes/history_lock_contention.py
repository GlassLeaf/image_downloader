"""Observe simultaneous lock initialization on Windows without changing product code.

Each round starts three spawn workers together. The parent resets only the
probe-owned lock file after all workers have released it. The no-initialization
variant uses an isolated source copy with the two initialization calls removed.
Recorded acquisition errors are observations; the probe does not repair them.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import multiprocessing
import shutil
import sys
import time
import traceback
from pathlib import Path


def worker(
    source: str,
    folder_name: str,
    rounds: int,
    barrier,
    counter,
    worker_id: int,
    result,
    record_all_errors: bool = False,
) -> None:
    sys.path.insert(0, source)
    from image_downloader.storage.interprocess_lock import InterProcessFileLock

    folder = Path(folder_name)
    with (folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        faulthandler.dump_traceback_later(8, repeat=True, file=stack)
        errors, successes, samples = 0, 0, []
        try:
            for iteration in range(rounds):
                barrier.wait(20)
                try:
                    with InterProcessFileLock(folder / "shared.lock", timeout_seconds=5):
                        current = counter.value
                        time.sleep(0.001)
                        counter.value = current + 1
                    successes += 1
                except Exception:
                    errors += 1
                    if record_all_errors or len(samples) < 3:
                        samples.append({"iteration": iteration, "traceback": traceback.format_exc()})
                barrier.wait(20)
        except Exception:
            samples.append({"barrier_error": traceback.format_exc()})
        finally:
            faulthandler.cancel_dump_traceback_later()
        result.put(
            {
                "worker": worker_id,
                "successes": successes,
                "errors": errors,
                "samples": samples,
                "module": sys.modules["image_downloader.storage.interprocess_lock"].__file__,
                "has_initialization": "_ensure_lock_byte" in InterProcessFileLock.acquire.__code__.co_names,
            }
        )


def scenario(output: Path, source: Path, variant: str, rounds: int, *, record_all_errors: bool = False) -> dict:
    folder = output / variant
    folder.mkdir()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    counter = context.Value("i", 0, lock=False)
    results = context.Queue()
    processes = [
        context.Process(
            target=worker, args=(str(source), str(folder), rounds, barrier, counter, i, results, record_all_errors)
        )
        for i in range(3)
    ]
    started = time.monotonic()
    parent_error = None
    completed = 0
    try:
        for process in processes:
            process.start()
        for _ in range(rounds):
            lock_path = folder / "shared.lock"
            if variant in ("missing", "no-initialization"):
                lock_path.unlink(missing_ok=True)
            else:
                lock_path.write_bytes(b"\0" if variant == "seeded" else b"")
            barrier.wait(20)
            barrier.wait(20)
            completed += 1
        workers = [results.get(timeout=10) for _ in processes]
    except Exception:
        parent_error = traceback.format_exc()
        workers = []
    finally:
        for process in processes:
            if process.pid is None:
                continue
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
    result = {
        "variant": variant,
        "rounds": completed,
        "worker_count": 3,
        "workers": workers,
        "counter": counter.value,
        "successful_acquisitions": sum(w["successes"] for w in workers),
        "errors": sum(w["errors"] for w in workers),
        "record_all_errors": record_all_errors,
        "parent_error": parent_error,
        "process_exitcodes": [p.exitcode for p in processes],
        "seconds": round(time.monotonic() - started, 3),
        "final_lock_size": (folder / "shared.lock").stat().st_size if (folder / "shared.lock").exists() else None,
        "sampled_lock_stacks": [
            p.name for p in folder.glob("*.stack.txt") if "interprocess_lock.py" in p.read_text(encoding="utf-8")
        ],
    }
    (folder / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "workers"}), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for diagnostic files")
    parser.add_argument("--rounds", type=int, default=200)
    parser.add_argument(
        "--record-all-errors", action="store_true", help="retain a traceback for every acquisition error"
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=("missing", "empty", "seeded", "no-initialization"),
        default=("missing", "empty", "seeded", "no-initialization"),
    )
    args = parser.parse_args()
    if sys.platform != "win32" or args.rounds <= 0 or len(args.variants) != len(set(args.variants)):
        parser.error("requires Windows, positive rounds, and unique variants")
    repository = Path(__file__).resolve().parents[4]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = repository / "src"
    isolated = output / "no-initialization-source" / "src"
    if "no-initialization" in args.variants:
        shutil.copytree(source, isolated, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        lock_source = isolated / "image_downloader/storage/interprocess_lock.py"
        text = lock_source.read_text(encoding="utf-8")
        needle = "            self._ensure_lock_byte(stream)\n"
        if text.count(needle) != 2:
            raise RuntimeError("expected exactly two initialization calls in current source")
        lock_source.write_text(text.replace(needle, ""), encoding="utf-8")
    results = []
    for variant in args.variants:
        selected_source = isolated if variant == "no-initialization" else source
        results.append(
            scenario(output, selected_source, variant, args.rounds, record_all_errors=args.record_all_errors)
        )
        (output / "evidence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    complete = all(
        r["rounds"] == args.rounds
        and r["parent_error"] is None
        and r["process_exitcodes"] == [0, 0, 0]
        and r["counter"] == r["successful_acquisitions"]
        for r in results
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
