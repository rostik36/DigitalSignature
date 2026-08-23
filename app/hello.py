"""Windows Hello (biometric) integration via the WinRT ``KeyCredentialManager``.

Used as an *optional* unlock for encrypted signatures. The trick that turns
Hello into a usable key source: a Hello-protected key signs a fixed challenge,
and ``KeyCredential`` uses RSASSA-PKCS1-v1.5, which is **deterministic** -- the
same key signing the same challenge yields the same bytes every time. We hash
that signature into a stable 32-byte secret. Producing it requires a live Hello
gesture (face / fingerprint / PIN), so the secret is unavailable to an attacker
who merely shares an unlocked Windows session.

Everything here is guarded: if ``winsdk`` is missing or Hello is not enrolled,
:func:`available` returns False and callers fall back to the passphrase. This
module has not been exercised against a live Hello credential in this project's
environment (none enrolled), so treat the biometric path as implemented-to-spec
but verify on a machine with Hello set up.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Optional

_SECRET_PREFIX = b"DigitalSignature/hello/v1/"

try:
    from winsdk.windows.security.credentials import (
        KeyCredentialCreationOption,
        KeyCredentialManager,
        KeyCredentialStatus,
    )
    from winsdk.windows.storage.streams import CryptographicBuffer

    _IMPORTED = True
except Exception:  # winsdk not installed / not Windows
    _IMPORTED = False


class HelloError(RuntimeError):
    """Hello was expected but could not be used (not enrolled, cancelled, etc.)."""


def _run(coro):
    return asyncio.run(coro)


def available() -> bool:
    """True only if winsdk is importable *and* a Hello credential is enrolled."""
    if not _IMPORTED:
        return False
    try:
        return bool(_run(KeyCredentialManager.is_supported_async()))
    except Exception:
        return False


async def _get_credential(key_name: str, create_if_missing: bool):
    res = await KeyCredentialManager.open_async(key_name)
    if res.status == KeyCredentialStatus.SUCCESS:
        return res.credential
    if not create_if_missing:
        raise HelloError(f"Hello key '{key_name}' not found (status {res.status}).")
    res = await KeyCredentialManager.request_create_async(
        key_name, KeyCredentialCreationOption.REPLACE_EXISTING
    )
    if res.status == KeyCredentialStatus.SUCCESS:
        return res.credential
    raise HelloError(f"Could not create Hello key (status {res.status}).")


async def _derive(key_name: str, challenge: bytes, create_if_missing: bool) -> bytes:
    cred = await _get_credential(key_name, create_if_missing)
    buf = CryptographicBuffer.create_from_byte_array(list(challenge))
    sign = await cred.request_sign_async(buf)  # <-- triggers the Hello prompt
    if sign.status != KeyCredentialStatus.SUCCESS:
        raise HelloError(f"Hello signing failed/cancelled (status {sign.status}).")
    sig = bytes(CryptographicBuffer.copy_to_byte_array(sign.result))
    return hashlib.sha256(_SECRET_PREFIX + sig).digest()


def derive_secret(key_name: str, challenge: bytes, create_if_missing: bool = False) -> bytes:
    """Return a stable 32-byte secret bound to a live Hello gesture.

    ``create_if_missing`` is True when first enabling Hello for a file (creates
    the credential), False when unlocking (the credential must already exist).
    Raises :class:`HelloError` on any failure or cancellation.
    """
    if not _IMPORTED:
        raise HelloError("winsdk is not available.")
    try:
        return _run(_derive(key_name, challenge, create_if_missing))
    except HelloError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HelloError(str(exc)) from exc


def delete_key(key_name: str) -> None:
    """Best-effort removal of a Hello key created by this app."""
    if not _IMPORTED:
        return
    try:
        _run(KeyCredentialManager.delete_async(key_name))
    except Exception:
        pass
