"""Private files and bounded atomic ciphertext writes."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from ..exceptions import VaultError
from ..storage.filesystem import FileSystem
from ..storage.path_safety import existing_directory, existing_regular_file


def restrict(path: Path, *, directory: bool) -> None:
    if directory:
        existing_directory(path, "private directory", required=True)
    else:
        existing_regular_file(path, "private file", required=True)
    if sys.platform != "win32":
        path.chmod(0o700 if directory else 0o600)
        return
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True, winmode=0x800)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    token = wintypes.HANDLE()
    sid_text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise VaultError("access")
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise VaultError("access")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise VaultError("access")
        flags = "OICI" if directory else ""
        sddl = f"D:P(A;{flags};FA;;;{sid_text.value})(A;{flags};FA;;;SY)(A;{flags};FA;;;BA)"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise VaultError("access")
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise VaultError("access")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


def private_directory(filesystem: FileSystem, relative: Path = Path(".")) -> None:
    existing_directory(filesystem.root, "private storage root")
    filesystem.ensure_directory(relative)
    directory = filesystem.root
    restrict(directory, directory=True)
    for part in relative.parts:
        directory /= part
        restrict(directory, directory=True)


def write_private(filesystem: FileSystem, relative: Path, data: bytes, limit: int) -> None:
    if len(data) > limit:
        raise VaultError("size")
    private_directory(filesystem, relative.parent)
    filesystem.write_bytes_atomic(relative, data)
    restrict(filesystem.path(relative), directory=False)
