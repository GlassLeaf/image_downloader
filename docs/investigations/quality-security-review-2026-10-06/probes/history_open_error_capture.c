/* Diagnostic-only wrappers. Capture errors before returning to Python. */
#include <windows.h>
#include <errno.h>
#include <fcntl.h>
#include <io.h>
#include <stdlib.h>

typedef struct {
    int result;
    DWORD last_error;
    int crt_errno;
    unsigned long doserrno;
} OpenResult;

__declspec(dllexport) void capture_wopen(const wchar_t *path, int flags, OpenResult *out) {
    _set_errno(0);
    _set_doserrno(0);
    SetLastError(0);
    out->result = _wopen(path, flags, 0666);
    out->last_error = GetLastError();
    _get_errno(&out->crt_errno);
    _get_doserrno(&out->doserrno);
}

__declspec(dllexport) void capture_close(int fd, OpenResult *out) {
    _set_errno(0);
    _set_doserrno(0);
    SetLastError(0);
    out->result = _close(fd);
    out->last_error = GetLastError();
    _get_errno(&out->crt_errno);
    _get_doserrno(&out->doserrno);
}

__declspec(dllexport) void *capture_createfile(const wchar_t *path, DWORD access,
                                            DWORD share, DWORD disposition, DWORD *error) {
    HANDLE handle;
    SetLastError(0);
    handle = CreateFileW(path, access, share, NULL, disposition, FILE_ATTRIBUTE_NORMAL, NULL);
    *error = GetLastError();
    return handle;
}
