"""Encrypted container for signatures: passphrase + optional Windows Hello.

File layout for the passphrase format (version 2)::

    b"SIGX2\n"  <one-line JSON header>  b"\n"  <DPAPI body bytes>

The header is small, cleartext JSON (it holds no secrets -- only public KDF
parameters and, if Hello is enabled, a random challenge and a Hello-sealed copy
of the file key)::

    {
      "v": 2,
      "kdf": "pbkdf2-sha256",
      "salt": "<base64>",
      "iters": 200000,
      "auth": "none",                  # present only in no-passphrase mode
      "hello": {                       # present only if Hello was enabled
        "key": "DigitalSignature_user",
        "challenge": "<base64>",
        "sealed": "<base64>"           # file key, sealed under the Hello secret
      }
    }

How the layers combine:

- The payload (JSON signature) is DPAPI-protected with extra entropy =
  ``PBKDF2(passphrase, salt)``. Reading it back needs the **Windows account**
  (DPAPI) *and* the **passphrase**. A wrong passphrase makes DPAPI fail, which is
  how we detect it.
- If Hello is enabled, that same passphrase-derived file key is *also* sealed
  under a Hello-derived secret and stored in ``hello.sealed``. A live Hello
  gesture recovers the file key without typing the passphrase. So unlocking
  needs the passphrase **or** a live biometric -- the passphrase always remains
  as the master/fallback.
- In **no-passphrase mode** (``"auth": "none"``) the KDF input is a fixed public
  constant instead of a user secret, so the only real factor left is DPAPI. The
  file is still ciphertext on disk and still unreadable on another account or
  PC, but it is *not* protected from someone using your unlocked session. Use it
  when the threat you care about is a copied file, not a shared desktop.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional, Tuple

from . import hello, secure

MAGIC_V2 = b"SIGX2\n"
MAGIC_V1 = b"SIGX1\n"  # legacy: DPAPI only, no passphrase
DEFAULT_HELLO_KEY = "DigitalSignature_user"

# Used as the KDF input when the user saves without a passphrase. It is a fixed
# constant in public source, so it is **not a secret** and adds no strength of
# its own -- a no-passphrase file is protected by DPAPI (the Windows account)
# alone, exactly like the legacy SIGX1 format. Its purpose is to keep one code
# path for both modes and to keep the payload out of plain sight on disk.
# Changing this string makes existing no-passphrase files unreadable.
_NO_PASSPHRASE_KDF_INPUT = "DigitalSignature/v2/no-passphrase"


class BadPassphrase(Exception):
    """The supplied passphrase did not decrypt the file."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def classify(raw: bytes) -> str:
    """Return 'sigx2', 'sigx1' or 'plain' for a file's leading bytes."""
    if raw.startswith(MAGIC_V2):
        return "sigx2"
    if raw.startswith(MAGIC_V1):
        return "sigx1"
    return "plain"


def _split_v2(raw: bytes) -> Tuple[dict, bytes]:
    rest = raw[len(MAGIC_V2):]
    nl = rest.index(b"\n")  # header is a single line; body is binary after it
    header = json.loads(rest[:nl].decode("utf-8"))
    body = rest[nl + 1:]
    return header, body


def header_has_hello(raw: bytes) -> bool:
    if classify(raw) != "sigx2":
        return False
    header, _ = _split_v2(raw)
    return "hello" in header


def encrypt(
    payload: bytes,
    passphrase: Optional[str] = None,
    enable_hello: bool = False,
    hello_key: str = DEFAULT_HELLO_KEY,
) -> bytes:
    """Build a SIGX2 file from ``payload``.

    ``passphrase`` of ``None`` selects **no-passphrase mode**: the file is still
    encrypted and still bound to the current Windows account (DPAPI), but opens
    without prompting. The header records ``"auth": "none"`` so readers know not
    to ask. This drops the second factor -- anyone with access to your unlocked
    Windows session can open the file -- so it is a convenience mode, not an
    equal-strength one.

    If ``enable_hello`` is True, a Hello gesture is requested now to seal a
    recovery copy of the file key (raises :class:`hello.HelloError` on failure).
    """
    if passphrase == "":
        raise ValueError("Pass passphrase=None for no-passphrase mode, not an empty string.")

    salt = os.urandom(16)
    iters = secure.PBKDF2_ITERATIONS
    kdf_input = _NO_PASSPHRASE_KDF_INPUT if passphrase is None else passphrase
    file_key = secure.derive_key(kdf_input, salt, iters)
    body = secure.protect(payload, entropy=file_key)

    header = {
        "v": 2,
        "kdf": "pbkdf2-sha256",
        "salt": _b64(salt),
        "iters": iters,
    }
    if passphrase is None:
        header["auth"] = "none"

    if enable_hello:
        challenge = os.urandom(32)
        hello_secret = hello.derive_secret(hello_key, challenge, create_if_missing=True)
        sealed = secure.protect(file_key, entropy=hello_secret)
        header["hello"] = {
            "key": hello_key,
            "challenge": _b64(challenge),
            "sealed": _b64(sealed),
        }

    return MAGIC_V2 + json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n" + body


def needs_passphrase(raw: bytes) -> bool:
    """True if opening this file requires the user to type a passphrase.

    False for files saved in no-passphrase mode, which :func:`decrypt` opens
    without prompting.
    """
    if classify(raw) != "sigx2":
        return False
    header, _ = _split_v2(raw)
    return header.get("auth") != "none"


def decrypt_with_passphrase(raw: bytes, passphrase: Optional[str] = None) -> bytes:
    """Decrypt a SIGX2 file. Raises :class:`BadPassphrase` if it does not open.

    ``passphrase=None`` uses the fixed no-passphrase key; pass it only for files
    where :func:`needs_passphrase` returned False.
    """
    header, body = _split_v2(raw)
    salt = _unb64(header["salt"])
    iters = int(header.get("iters", secure.PBKDF2_ITERATIONS))
    kdf_input = _NO_PASSPHRASE_KDF_INPUT if passphrase is None else passphrase
    file_key = secure.derive_key(kdf_input, salt, iters)
    try:
        return secure.unprotect(body, entropy=file_key)
    except OSError as exc:
        if passphrase is None:
            raise BadPassphrase(
                "This file didn't open. It was saved without a passphrase, so it "
                "can only be read by the Windows account that created it."
            ) from exc
        raise BadPassphrase("Wrong passphrase (or wrong Windows account).") from exc


def decrypt(raw: bytes) -> bytes:
    """Open a no-passphrase SIGX2 file. Raises if it needs a passphrase."""
    if needs_passphrase(raw):
        raise BadPassphrase("This file is passphrase-protected.")
    return decrypt_with_passphrase(raw, None)


def decrypt_with_hello(raw: bytes) -> bytes:
    """Decrypt a SIGX2 file via a live Windows Hello gesture.

    Raises :class:`hello.HelloError` if the file has no Hello unlock, Hello is
    unavailable, or the gesture fails/cancels.
    """
    header, body = _split_v2(raw)
    h = header.get("hello")
    if not h:
        raise hello.HelloError("This file has no Windows Hello unlock.")
    if not hello.available():
        raise hello.HelloError("Windows Hello is not available on this account.")
    hello_secret = hello.derive_secret(h["key"], _unb64(h["challenge"]), create_if_missing=False)
    try:
        file_key = secure.unprotect(_unb64(h["sealed"]), entropy=hello_secret)
    except OSError as exc:
        raise hello.HelloError("Hello unlock failed to recover the file key.") from exc
    # file_key recovered; body should now decrypt (wrong here would be corruption).
    return secure.unprotect(body, entropy=file_key)


def decrypt_legacy_v1(raw: bytes) -> bytes:
    """Decrypt a legacy SIGX1 file (DPAPI only, no passphrase)."""
    return secure.unprotect(raw[len(MAGIC_V1):])
