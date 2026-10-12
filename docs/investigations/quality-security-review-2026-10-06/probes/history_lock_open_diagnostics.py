"""Windows diagnostic control using the comparison probe's existing CLI.

Replace only the existing-file opener with one direct os.open, without retries.
Record CRT doserrno immediately after Python returns OSError; this is not an
in-native-call GetLastError capture. Deliberately omit per-open clearing to
reduce timing perturbation; stale values remain a limitation of this control.
Do not use this control as a regression pass/fail test of the product's retry.
The regular comparison probe does not apply this instrumentation.
"""

from __future__ import annotations

import ctypes
import os

import history_lock_remediation as comparison

_load_lock = comparison.load_lock


def load_with_diagnostics(source):
    module = _load_lock(source)
    if not hasattr(module, "_open_existing_append"):
        raise RuntimeError("diagnostic control requires the Windows publication opener")
    library = ctypes.CDLL("ucrtbase.dll", winmode=0x800)
    library._get_doserrno.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
    library._get_doserrno.restype = ctypes.c_int
    library._set_doserrno.argtypes = [ctypes.c_ulong]
    library._set_doserrno(0)

    def opener(path, flags):
        try:
            return os.open(path, flags & ~os.O_CREAT)
        except OSError as error:
            code = ctypes.c_ulong()
            if library._get_doserrno(ctypes.byref(code)) == 0:
                error._diagnostic_doserrno = code.value
            raise

    module._open_existing_append = opener
    return module


comparison.load_lock = load_with_diagnostics


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("This diagnostic control requires Windows.")
    raise SystemExit(comparison.main())
