"""Full acquisition control for the comparison's persistent nonempty files.

Uses the existing comparison supervisor and CLI. Unlike its open-only stable
worker, this worker acquires/releases the real product lock and updates a shared
counter under exclusion. No live files are reset and no protection is changed.
"""

from __future__ import annotations

import faulthandler
import hashlib
import json
import sys
import time
from pathlib import Path

import history_lock_remediation as comparison

_original_case = comparison.case


def worker(source, output, condition, rounds, worker_id, barrier, counter, phases, counts):
    sys.dont_write_bytecode = True
    if not condition.startswith("stable"):
        raise ValueError("persistent acquisition requires a stable condition")
    folder = Path(output)
    with (folder / f"worker-{worker_id}.stack.txt").open("w", encoding="utf-8") as stack:
        faulthandler.enable(file=stack)
        module = comparison.load_lock(Path(source))
        comparison.observe_calls(module, phases, worker_id)
        errors, latencies = [], []
        protocol_error = None
        try:
            for iteration in range(rounds):
                phases[worker_id] = 7
                barrier.wait(20)
                started = time.perf_counter()
                try:
                    lock = module.InterProcessFileLock(folder / "stable.lock", timeout_seconds=5)
                    with lock:
                        phases[worker_id] = 5
                        value = counter.value
                        time.sleep(0.001)
                        counter.value = value + 1
                    counts[worker_id * 3] += 1
                    latencies.append((time.perf_counter() - started) * 1000)
                except Exception as error:
                    detail = comparison.exception_details(error, iteration)
                    errors.append(detail)
                    counts[worker_id * 3 + 1] += 1
                    with (folder / f"worker-{worker_id}.errors.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps(detail) + "\n")
                counts[worker_id * 3 + 2] += 1
                phases[worker_id] = 7
                barrier.wait(20)
        except BaseException as error:
            protocol_error = comparison.exception_details(error, counts[worker_id * 3 + 2])
        finally:
            phases[worker_id] = 8
            comparison.write_json(
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


def case(source, output, condition, rounds):
    _original_case(source, output, condition, rounds)
    path = Path(output) / "summary.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["operation"] = "persistent_acquire"
    value["counter_matches_successes"] = value["counter"] == value["successes"]
    value["nonempty_file_metadata_preserved"] = value["before"] == value["after"]
    preserved = value["counter_matches_successes"] and value["nonempty_file_metadata_preserved"]
    value["protocol_completed"] = value["protocol_completed"] and preserved
    value["all_operations_succeeded"] = value["all_operations_succeeded"] and preserved
    comparison.write_json(path, value)


comparison.worker = worker
comparison.case = case

if __name__ == "__main__":
    raise SystemExit(comparison.main())
