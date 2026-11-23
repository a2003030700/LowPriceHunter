"""Windows DPAPI（目前使用者範圍）加密工具。

只有同一台電腦、同一個 Windows 使用者能解密；換使用者或把檔案拿到別台電腦
都無法解開。請注意：同一個使用者權限下執行的惡意程式仍可呼叫 DPAPI 解密，
這是 DPAPI 的設計限制，不是本模組的漏洞。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

ENTROPY = b"LowPriceHunter-bot-email-v1"
_UI_FORBIDDEN = 0x1


class DpapiError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


if sys.platform == "win32":
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
else:
    _crypt32 = None
    _kernel32 = None


def available() -> bool:
    return _crypt32 is not None


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def protect(data: bytes, entropy: bytes = ENTROPY) -> bytes:
    """加密 bytes，回傳 DPAPI blob。"""
    if _crypt32 is None:
        raise DpapiError("DPAPI 僅支援 Windows")
    in_blob, _in_buf = _blob(data)
    ent_blob, _ent_buf = _blob(entropy)
    out = _DataBlob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "LowPriceHunter",
        ctypes.byref(ent_blob),
        None,
        None,
        _UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise DpapiError(f"CryptProtectData 失敗（{ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def unprotect(blob_data: bytes, entropy: bytes = ENTROPY) -> bytes:
    """解密 DPAPI blob。"""
    if _crypt32 is None:
        raise DpapiError("DPAPI 僅支援 Windows")
    in_blob, _in_buf = _blob(blob_data)
    ent_blob, _ent_buf = _blob(entropy)
    out = _DataBlob()
    desc = ctypes.c_wchar_p()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(desc),
        ctypes.byref(ent_blob),
        None,
        None,
        _UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if desc:
        _kernel32.LocalFree(ctypes.cast(desc, ctypes.c_void_p))
    if not ok:
        raise DpapiError(f"CryptUnprotectData 失敗（{ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
