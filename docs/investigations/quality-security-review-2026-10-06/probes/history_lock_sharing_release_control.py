"""Artificial control: release a real deny-sharing handle after the first error.

This verifies one retry's behavior, not a spontaneous COMODO reproduction. The
existing one-byte test file is owned by this probe; protection is never changed.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from history_lock_remediation import load_lock


def trial(source: Path, path: Path, asynchronous: bool) -> dict:
    for name in list(sys.modules):
        if name == "image_downloader" or name.startswith("image_downloader."):
            del sys.modules[name]
    module = load_lock(source)
    assert Path(module.__file__).resolve().is_relative_to(source)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    original_opener, original_os_open = module._open_existing_append, os.open
    errors, attempts = [], []

    def observe_open(value, flags):
        if Path(value) == path:
            attempts.append(flags)
        return original_os_open(value, flags)

    def release_after_error(value, flags):
        nonlocal handle
        try:
            return original_opener(value, flags)
        except OSError as error:
            errors.append({"errno": error.errno, "winerror": getattr(error, "winerror", None),
                           "verified_sharing_violation": getattr(error, "_lock_sharing_violation", False)})
            if handle is not None:
                assert kernel.CloseHandle(handle)
                handle = None
            raise

    module._open_existing_append, os.open = release_after_error, observe_open
    lock, acquired, returned_error = module.InterProcessFileLock(path), False, None
    started = time.monotonic()
    try:
        try:
            if asynchronous:
                asyncio.run(lock.acquire_async())
            else:
                lock.acquire()
            acquired = True
            lock.release()
        except Exception as error:
            returned_error = type(error).__name__
    finally:
        module._open_existing_append, os.open = original_opener, original_os_open
        if handle is not None:
            assert kernel.CloseHandle(handle)
    return {
        "module": module.__file__, "sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        "async": asynchronous, "acquired": acquired, "returned_error": returned_error,
        "native_open_flags": attempts, "initial_errors": errors, "seconds": time.monotonic() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", action="append", required=True, help="label=src directory")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows is required")
    if len(args.source) != 2 or {value.split("=", 1)[0] for value in args.source} != {"final", "no-retry"}:
        parser.error("provide final and no-retry sources exactly once")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    path = output / "shared.lock"
    path.write_bytes(b"\0")
    results = []
    for value in args.source:
        label, folder = value.split("=", 1)
        for asynchronous in (False, True):
            result = trial(Path(folder).resolve(), path, asynchronous)
            result["source"] = label
            results.append(result)
            (output / "evidence.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result), flush=True)
    assert path.read_bytes() == b"\0"
    return 0 if all(
        len(row["initial_errors"]) == 1 and row["initial_errors"][0]["verified_sharing_violation"]
        and all(not flags & os.O_CREAT for flags in row["native_open_flags"])
        and row["acquired"] == (row["source"] == "final")
        and row["returned_error"] == (None if row["source"] == "final" else "InterProcessLockError")
        and len(row["native_open_flags"]) == (2 if row["source"] == "final" else 1)
        for row in results
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
