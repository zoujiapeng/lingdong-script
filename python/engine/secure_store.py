"""Windows DPAPI helpers for secrets persisted by the legacy execution store."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import os
from ctypes import wintypes


PREFIX = "dpapi:v1:"
_DESCRIPTION = "Lingdong Script local vault"
_ENTROPY = hashlib.sha256(b"lingdong-script-vault-v1").digest()
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("The local vault requires Windows DPAPI")


def is_protected(value: str) -> bool:
    return value.startswith(PREFIX)


def protect_text(value: str) -> str:
    _require_windows()
    raw_blob, raw_buffer = _blob(value.encode("utf-8"))
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(raw_blob),
        _DESCRIPTION,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    del raw_buffer, entropy_buffer
    if not ok:
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect_text(value: str) -> str:
    _require_windows()
    if not is_protected(value):
        raise ValueError("Vault value is not DPAPI protected")
    encrypted = base64.b64decode(value[len(PREFIX):], validate=True)
    encrypted_blob, encrypted_buffer = _blob(encrypted)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(encrypted_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    del encrypted_buffer, entropy_buffer
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
