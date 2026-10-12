"""Diagnostic companion: record the open layer, resource and flags in shared memory.

Uses the comparison CLI unchanged. No additional disk writes occur at each open.
The extra Python/shared-memory operations may affect timing; successful controls
must not replace the uninstrumented comparison's failures.
The recorded layer is the last entered layer, not necessarily the pending call.
Use history_lock_open_active_layer_control.py to restore layers on inner returns.
"""

from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path

import history_lock_remediation as comparison

_original_context = comparison.multiprocessing.get_context
_original_case = comparison.case
_original_worker = comparison.worker
_original_observe = comparison.observe_calls
_case_counts = None
_worker_counts = None


class RecordingContext:
    def __init__(self, context):
        self.context = context

    def __getattr__(self, name):
        return getattr(self.context, name)

    def Array(self, typecode, length, **kwargs):
        global _case_counts
        if typecode == "i" and length == 9:
            _case_counts = self.context.Array(typecode, 18, **kwargs)
            return _case_counts
        return self.context.Array(typecode, length, **kwargs)


def get_context(method=None):
    return RecordingContext(_original_context(method))


def observe(module, phases, worker_id):
    _original_observe(module, phases, worker_id)

    def wrap(function, layer):
        def observed(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                name = os.fsdecode(os.path.basename(os.fspath(path)))
                resource = 2 if name.startswith(".id-lock-") else (1 if name.endswith(".lock") else 0)
                if resource and _worker_counts is not None:
                    offset = 9 + worker_id * 3
                    _worker_counts[offset] = resource
                    _worker_counts[offset + 1] = layer
                    _worker_counts[offset + 2] = args[0] if layer == 3 and args else -1
            return function(path, *args, **kwargs)

        return observed

    builtins.open = wrap(builtins.open, 1)
    io.open = wrap(io.open, 2)
    os.open = wrap(os.open, 3)


def worker(source, output, condition, rounds, worker_id, barrier, counter, phases, counts):
    global _worker_counts
    assert len(counts) == 18
    _worker_counts = counts
    return _original_worker(source, output, condition, rounds, worker_id, barrier, counter, phases, counts)


def case(source, output, condition, rounds):
    _original_case(source, output, condition, rounds)
    assert _case_counts is not None
    path = Path(output) / "summary.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["open_phase_instrumentation"] = True
    for stopped in value["stopped_workers"]:
        if stopped["phase"] != "native_open" or _case_counts is None:
            continue
        offset = 9 + stopped["worker"] * 3
        resource, layer, flags = _case_counts[offset : offset + 3]
        stopped["open_resource"] = {1: "target", 2: "staging"}.get(resource, "unknown")
        stopped["open_layer"] = {1: "builtins.open", 2: "io.open", 3: "os.open"}.get(layer, "unknown")
        stopped["open_flags"] = flags if layer == 3 else None
        stopped["O_CREAT"] = bool(flags & os.O_CREAT) if layer == 3 else None
        stopped["O_EXCL"] = bool(flags & os.O_EXCL) if layer == 3 else None
    comparison.write_json(path, value)


comparison.multiprocessing.get_context = get_context
comparison.observe_calls = observe
comparison.worker = worker
comparison.case = case

if __name__ == "__main__":
    raise SystemExit(comparison.main())
