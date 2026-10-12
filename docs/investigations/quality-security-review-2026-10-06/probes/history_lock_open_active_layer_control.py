"""Diagnostic control that restores the active open layer when nested calls return.

The last native flags are kept separately from the active layer. A builtins.open
stall with saved native flags can therefore occur after os.open has returned.
Extra observation changes timing; keep this separate from normal comparisons.
"""

from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path

import history_lock_open_phase_control as phase

comparison = phase.comparison


def observe(module, phases, worker_id):
    phase._original_observe(module, phases, worker_id)

    def wrap(function, layer):
        def observed(path, *args, **kwargs):
            counts = phase._worker_counts
            resource = 0
            if isinstance(path, (str, bytes, os.PathLike)):
                name = os.fsdecode(os.path.basename(os.fspath(path)))
                resource = 2 if name.startswith(".id-lock-") else (1 if name.endswith(".lock") else 0)
            if not resource or counts is None:
                return function(path, *args, **kwargs)
            offset = 9 + worker_id * 3
            previous = counts[offset : offset + 2]
            if previous[1] == 0:
                counts[offset + 2] = -1
            counts[offset], counts[offset + 1] = resource, layer
            if layer == 3 and args:
                counts[offset + 2] = args[0]
            try:
                return function(path, *args, **kwargs)
            finally:
                counts[offset], counts[offset + 1] = previous

        return observed

    builtins.open = wrap(builtins.open, 1)
    io.open = wrap(io.open, 2)
    os.open = wrap(os.open, 3)


def case(source, output, condition, rounds):
    phase._original_case(source, output, condition, rounds)
    assert phase._case_counts is not None
    path = Path(output) / "summary.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["active_open_layer_instrumentation"] = True
    for stopped in value["stopped_workers"]:
        if stopped["phase"] != "native_open":
            continue
        offset = 9 + stopped["worker"] * 3
        resource, layer, flags = phase._case_counts[offset : offset + 3]
        stopped["open_resource"] = {1: "target", 2: "staging"}.get(resource, "unknown")
        stopped["active_open_layer"] = {1: "builtins.open", 2: "io.open", 3: "os.open"}.get(layer, "unknown")
        stopped["last_native_flags"] = flags if flags >= 0 else None
        stopped["O_CREAT"] = bool(flags & os.O_CREAT) if flags >= 0 else None
        stopped["O_EXCL"] = bool(flags & os.O_EXCL) if flags >= 0 else None
    comparison.write_json(path, value)


comparison.observe_calls = observe
comparison.case = case

if __name__ == "__main__":
    raise SystemExit(comparison.main())
