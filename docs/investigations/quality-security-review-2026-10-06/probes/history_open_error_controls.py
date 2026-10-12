"""Validate errno/doserrno/GetLastError capture using deterministic failures.

Build history_open_error_capture.c with the dynamic CRT (/MD) first. Tests
use missing paths, a directory, and a temporary no-sharing handle. No ACLs,
security settings, or existing files are changed. Run with an outer deadline.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path

source = Path(__file__).with_name("history_open_delays.py")
spec = importlib.util.spec_from_file_location("delay_probe", source)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def flags(mode):
    value = os.O_RDWR | os.O_BINARY | os.O_NOINHERIT
    if mode == "w+b":
        value |= os.O_CREAT | os.O_TRUNC
    elif mode == "a+b":
        value |= os.O_CREAT | os.O_APPEND
    return value


def native_api(helper):
    dll = ctypes.CDLL(str(helper.resolve()))
    dll.capture_wopen.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(probe.OpenResult)]
    dll.capture_wopen.restype = None
    dll.capture_close.argtypes = [ctypes.c_int, ctypes.POINTER(probe.OpenResult)]
    dll.capture_close.restype = None
    dll.capture_createfile.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    dll.capture_createfile.restype = ctypes.c_void_p
    return dll


def observe(path, mode, dll):
    reader, clear_dos = probe.error_reader()
    clear_dos()
    python = {"success": False}
    try:
        with path.open(mode):
            python["success"] = True
    except OSError as exc:
        diagnostic = reader()
        python.update({"type": type(exc).__name__, "errno": exc.errno,
                       "winerror": getattr(exc, "winerror", None), "late_diagnostic": diagnostic})
    result = probe.OpenResult()
    dll.capture_wopen(str(path), flags(mode), ctypes.byref(result))
    native = {name: getattr(result, name) for name, _ in probe.OpenResult._fields_}
    native["success"] = result.result >= 0
    if result.result >= 0:
        closed = probe.OpenResult()
        dll.capture_close(result.result, ctypes.byref(closed))
        native["close_result"] = closed.result
    error = ctypes.c_uint32()
    disposition = {"r+b": 3, "w+b": 2, "a+b": 4}[mode]
    handle = dll.capture_createfile(str(path), 0xC0000000, 3, disposition, ctypes.byref(error))
    invalid = ctypes.c_void_p(-1).value
    win32 = {"success": handle != invalid, "last_error": error.value}
    if handle != invalid:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        assert kernel.CloseHandle(handle)
    return {"path": str(path), "mode": mode, "python": python, "native_wopen": native,
            "direct_CreateFileW": win32}


def run(output, helper):
    output.mkdir(parents=True, exist_ok=False)
    dll = native_api(helper)
    directory = output / "directory"
    directory.mkdir()
    shared = output / "shared.lock"
    shared.write_bytes(b"\0")
    error = ctypes.c_uint32()
    handle = dll.capture_createfile(str(shared), 0xC0000000, 0, 3, ctypes.byref(error))
    assert handle != ctypes.c_void_p(-1).value, error.value
    record = {"guard64": probe.base.loaded_guard(), "results": [],
              "helper_sha256": probe.public.digest(helper)}
    try:
        for case in ("missing-file", "missing-parent", "directory", "sharing-conflict"):
            for mode in ("r+b", "w+b", "a+b"):
                path = {"missing-file": output / f"absent-{mode[0]}.lock",
                        "missing-parent": output / "absent-directory" / "file.lock",
                        "directory": directory, "sharing-conflict": shared}[case]
                result = {"case": case, **observe(path, mode, dll)}
                record["results"].append(result)
                probe.public.write_json(output / "evidence.json", record)
                print(json.dumps(result), flush=True)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        assert kernel.CloseHandle(handle)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("helper", type=Path)
    args = parser.parse_args()
    run(args.output.resolve(), args.helper.resolve())


if __name__ == "__main__":
    main()
