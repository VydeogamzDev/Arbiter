"""Windows security helpers (spec §18.8): current-user SID, user-only security
descriptors for named pipes and files. Stdlib (ctypes) only; import only on win32."""

from __future__ import annotations

import ctypes
import functools
import sys
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":  # pragma: no cover - guarded by callers
    raise ImportError("winsec is Windows-only")

_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.LocalFree.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
_adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
_adv.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
_adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
_adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

SE_FILE_OBJECT = 1
SE_KERNEL_OBJECT = 6
DACL_SECURITY_INFORMATION = 0x4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SDDL_REVISION_1 = 1


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


@functools.lru_cache(maxsize=1)
def current_user_sid() -> str:
    token = wintypes.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x8, ctypes.byref(token)):  # TOKEN_QUERY
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        _adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        buf = ctypes.create_string_buffer(size.value)
        if not _adv.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        out = wintypes.LPWSTR()
        if not _adv.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = out.value or ""
        _k32.LocalFree(ctypes.cast(out, ctypes.c_void_p))
        return sid
    finally:
        _k32.CloseHandle(token)


def user_only_sddl() -> str:
    """Protected DACL granting full access to the current user only."""
    return f"D:P(A;;GA;;;{current_user_sid()})"


class UserOnlySecurityAttributes:
    """Owns a SECURITY_ATTRIBUTES + descriptor; keep alive while the handle is created."""

    def __init__(self) -> None:
        self._psd = ctypes.c_void_p()
        if not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                user_only_sddl(), SDDL_REVISION_1, ctypes.byref(self._psd), None):
            raise ctypes.WinError(ctypes.get_last_error())
        self.sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), self._psd, False)

    @property
    def address(self) -> int:
        return ctypes.addressof(self.sa)

    def __del__(self) -> None:
        if self._psd:
            _k32.LocalFree(self._psd)
            self._psd = ctypes.c_void_p()


def handle_dacl_sddl(handle: int) -> str:
    """Return the DACL of a kernel object handle as SDDL (used by tests and doctor)."""
    pdacl, psd = ctypes.c_void_p(), ctypes.c_void_p()
    err = _adv.GetSecurityInfo(handle, SE_KERNEL_OBJECT, DACL_SECURITY_INFORMATION, None, None,
                               ctypes.byref(pdacl), None, ctypes.byref(psd))
    if err:
        raise ctypes.WinError(err)
    out = wintypes.LPWSTR()
    _adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(psd, SDDL_REVISION_1, DACL_SECURITY_INFORMATION,
                                                              ctypes.byref(out), None)
    value = out.value or ""
    _k32.LocalFree(ctypes.cast(out, ctypes.c_void_p))
    _k32.LocalFree(psd)
    return value


def restrict_file_to_user(path: Path) -> None:
    """Replace a file's DACL with a protected current-user-only DACL (best effort)."""
    psd = ctypes.c_void_p()
    if not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;;FA;;;{current_user_sid()})", SDDL_REVISION_1, ctypes.byref(psd), None):
        return
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        pdacl = ctypes.c_void_p()
        if not _adv.GetSecurityDescriptorDacl(psd, ctypes.byref(present), ctypes.byref(pdacl), ctypes.byref(defaulted)):
            return
        _adv.SetNamedSecurityInfoW(str(path), SE_FILE_OBJECT,
                                   DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                                   None, None, pdacl, None)
    finally:
        _k32.LocalFree(psd)
