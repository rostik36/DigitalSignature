"""At-rest encryption for saved signatures, using Windows DPAPI.

The signature is the most sensitive thing this app stores, so the encrypted
format binds the ciphertext to the **current Windows user account**: Windows
derives the key from the user's logon secrets via ``CryptProtectData`` and only
the same user (by default on the same machine) can call ``CryptUnprotectData``
to read it back. There is no password for us to store and nothing travels over
the network -- if the file is copied to another account or PC it is just noise.

An additional app-specific *entropy* value is mixed in so the blob is also tied
to this application's context, not merely the user.

This is the closest practical match to "encrypt with my Windows identity"
without extra dependencies. A biometric (Windows Hello) prompt on each open is a
stronger but heavier option -- see the project README.
"""

from __future__ import annotations

import hashlib
import sys

# Mixed into every blob as secondary entropy. Changing this makes all previously
# encrypted files unreadable, so treat it as a fixed part of the format.
_APP_ENTROPY = b"DigitalSignature/v1/dpapi-entropy"

# Default PBKDF2 work factor for passphrase-derived keys. High enough to make
# on-session brute force of the passphrase slow.
PBKDF2_ITERATIONS = 200_000


def derive_key(passphrase: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Stretch a passphrase into a 32-byte key with PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)

_AVAILABLE = sys.platform.startswith("win")

if _AVAILABLE:
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1  # never show UI; fail instead of prompting

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    _CryptProtectData = _crypt32.CryptProtectData
    _CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _CryptProtectData.restype = wintypes.BOOL

    _CryptUnprotectData = _crypt32.CryptUnprotectData
    _CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _CryptUnprotectData.restype = wintypes.BOOL

    def _make_blob(data: bytes) -> _DATA_BLOB:
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _read_blob(blob: _DATA_BLOB) -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)

    def _free(blob: _DATA_BLOB) -> None:
        if blob.pbData:
            _kernel32.LocalFree(blob.pbData)


def available() -> bool:
    """True if DPAPI-based protect/unprotect can be used (Windows only)."""
    return _AVAILABLE


def protect(data: bytes, entropy: bytes = b"") -> bytes:
    """Encrypt ``data`` for the current Windows user. Returns the opaque blob.

    ``entropy`` is extra secondary entropy appended to the fixed app entropy --
    e.g. a passphrase-derived key. The exact same ``entropy`` must be supplied to
    :func:`unprotect`, so it acts as an additional required secret on top of the
    Windows-account binding.
    """
    if not _AVAILABLE:
        raise RuntimeError("Encrypted storage requires Windows (DPAPI).")
    in_blob = _make_blob(data)
    ent_blob = _make_blob(_APP_ENTROPY + entropy)
    out_blob = _DATA_BLOB()
    ok = _CryptProtectData(
        ctypes.byref(in_blob), "DigitalSignature", ctypes.byref(ent_blob),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return _read_blob(out_blob)
    finally:
        _free(out_blob)


def unprotect(blob: bytes, entropy: bytes = b"") -> bytes:
    """Decrypt a blob produced by :func:`protect`.

    Fails (raising ``OSError``) if the Windows user/machine differs or the
    ``entropy`` (e.g. the passphrase-derived key) is wrong -- which is exactly how
    a wrong passphrase is detected.
    """
    if not _AVAILABLE:
        raise RuntimeError("Encrypted storage requires Windows (DPAPI).")
    in_blob = _make_blob(blob)
    ent_blob = _make_blob(_APP_ENTROPY + entropy)
    out_blob = _DATA_BLOB()
    ok = _CryptUnprotectData(
        ctypes.byref(in_blob), None, ctypes.byref(ent_blob),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(
            ctypes.get_last_error(),
            "CryptUnprotectData failed (wrong Windows user/machine, passphrase, or corrupt file)",
        )
    try:
        return _read_blob(out_blob)
    finally:
        _free(out_blob)
