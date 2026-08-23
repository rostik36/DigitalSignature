"""Encrypted container for signatures: passphrase + optional machine binding.

File layout for the current format (version 3)::

    b"SIGX3\n"  <one-line JSON header>  b"\n"  <nonce || AES-GCM ciphertext>

The header is small, cleartext JSON (it holds no secrets -- only public KDF
parameters and, if Hello is enabled, a random challenge and a Hello-sealed copy
of the file key)::

    {
      "v": 3,
      "kdf": "pbkdf2-sha256",
      "salt": "<base64>",
      "iters": 200000,
      "cipher": "aes-256-gcm",
      "auth": "none",                  # present only in no-passphrase mode
      "machine": "dpapi",              # present only if tied to this computer
      "hello": {                       # present only if Hello was enabled
        "key": "DigitalSignature_user",
        "challenge": "<base64>",
        "sealed": "<base64>"           # file key, sealed under the Hello secret
      }
    }

How the layers combine:

- ``file_key = PBKDF2(passphrase, salt, iters)``, and the payload is encrypted
  with **AES-256-GCM** under that key. Nothing about the computer is involved,
  so by default a file **opens on any PC** given the passphrase. The header is
  passed as GCM associated data, so editing it (e.g. deleting ``"machine"``)
  breaks authentication instead of changing behaviour.
- If **tie_to_machine** is set, the AES-GCM ciphertext is additionally wrapped
  with DPAPI. That adds the **Windows account** as a second required factor and
  makes the file unreadable on any other PC or account -- the trade-off is
  exactly that loss of portability. Opt in only when you want it.
- In **no-passphrase mode** (``"auth": "none"``) the KDF input is a fixed public
  constant instead of a user secret, so DPAPI is the only real factor left. It
  is therefore only allowed together with ``tie_to_machine``.
- If Hello is enabled, the file key is *also* sealed under a Hello-derived
  secret in ``hello.sealed``, so a live gesture opens the file without typing
  the passphrase. Hello keys are machine-bound, so this implies
  ``tie_to_machine``.

Legacy ``SIGX2``/``SIGX1`` files stay readable: they were DPAPI-wrapped by
construction and are always machine-bound.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional, Tuple

from . import hello, secure

MAGIC_V3 = b"SIGX3\n"  # current: AES-256-GCM, portable unless machine-bound
MAGIC_V2 = b"SIGX2\n"  # legacy: DPAPI-wrapped, always machine-bound
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
    """Return 'sigx3', 'sigx2', 'sigx1', 'sigx-unknown' or 'plain'.

    ``sigx-unknown`` means the file is one of ours but from a **newer** build.
    Detecting that explicitly matters: without it the binary body reaches the
    plain-JSON reader and fails as an opaque ``UnicodeDecodeError`` instead of
    telling the user to update.
    """
    if raw.startswith(MAGIC_V3):
        return "sigx3"
    if raw.startswith(MAGIC_V2):
        return "sigx2"
    if raw.startswith(MAGIC_V1):
        return "sigx1"
    if raw.startswith(b"SIGX"):
        return "sigx-unknown"
    return "plain"


def _split(raw: bytes, magic: bytes) -> Tuple[dict, bytes, bytes]:
    """Return ``(header, header_bytes, body)``.

    ``header_bytes`` is the exact on-disk header line, which SIGX3 authenticates
    as AES-GCM associated data -- so it must be the raw bytes, not a re-encoding.
    """
    rest = raw[len(magic):]
    nl = rest.index(b"\n")  # header is a single line; body is binary after it
    header_bytes = rest[:nl]
    return json.loads(header_bytes.decode("utf-8")), header_bytes, rest[nl + 1:]


def header_has_hello(raw: bytes) -> bool:
    if classify(raw) not in ("sigx2", "sigx3"):
        return False
    header, _, _ = _read_header(raw)
    return "hello" in header


def encrypt(
    payload: bytes,
    passphrase: Optional[str] = None,
    tie_to_machine: bool = False,
    enable_hello: bool = False,
    hello_key: str = DEFAULT_HELLO_KEY,
) -> bytes:
    """Build a SIGX3 file from ``payload``.

    ``passphrase``
        The secret that opens the file. ``None`` selects **no-passphrase mode**,
        where the KDF input is a fixed public constant; that only makes sense
        together with ``tie_to_machine``, since otherwise nothing secret is left
        (see :func:`encrypt`'s validation below).

    ``tie_to_machine``
        When False (the default) the file is **portable**: AES-256-GCM keyed
        purely from the passphrase, so it opens on any computer. When True the
        ciphertext is additionally wrapped with DPAPI, adding your Windows
        account as a second required factor -- at the cost of the file no longer
        opening anywhere else.

    ``enable_hello``
        Seal a recovery copy of the file key under Windows Hello. Hello keys are
        machine-bound, so this implies ``tie_to_machine``.
    """
    if passphrase == "":
        raise ValueError("Pass passphrase=None for no-passphrase mode, not an empty string.")
    if passphrase is None and not tie_to_machine:
        raise ValueError(
            "A file with no passphrase and no machine binding has no protection at all; "
            "save it as plain JSON instead."
        )
    if enable_hello and not tie_to_machine:
        raise ValueError("Windows Hello unlock requires tie_to_machine=True.")

    salt = os.urandom(16)
    iters = secure.PBKDF2_ITERATIONS
    kdf_input = _NO_PASSPHRASE_KDF_INPUT if passphrase is None else passphrase
    file_key = secure.derive_key(kdf_input, salt, iters)

    header = {
        "v": 3,
        "kdf": "pbkdf2-sha256",
        "salt": _b64(salt),
        "iters": iters,
        "cipher": "aes-256-gcm",
    }
    if passphrase is None:
        header["auth"] = "none"
    if tie_to_machine:
        header["machine"] = "dpapi"

    if enable_hello:
        challenge = os.urandom(32)
        hello_secret = hello.derive_secret(hello_key, challenge, create_if_missing=True)
        header["hello"] = {
            "key": hello_key,
            "challenge": _b64(challenge),
            "sealed": _b64(secure.protect(file_key, entropy=hello_secret)),
        }

    # The header is authenticated (not encrypted) so flipping e.g. "machine"
    # or the salt invalidates the tag instead of silently changing behaviour.
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    body = secure.aead_encrypt(file_key, payload, associated_data=header_bytes)
    if tie_to_machine:
        body = secure.protect(body, entropy=file_key)

    return MAGIC_V3 + header_bytes + b"\n" + body


def _read_header(raw: bytes) -> Tuple[dict, bytes, bytes]:
    """Parse any SIGX2/SIGX3 file into ``(header, header_bytes, body)``."""
    kind = classify(raw)
    if kind == "sigx3":
        return _split(raw, MAGIC_V3)
    if kind == "sigx2":
        return _split(raw, MAGIC_V2)
    raise ValueError(f"Not an encrypted signature container ({kind}).")


def needs_passphrase(raw: bytes) -> bool:
    """True if opening this file requires the user to type a passphrase.

    False for files saved in no-passphrase mode, which :func:`decrypt` opens
    without prompting.
    """
    if classify(raw) not in ("sigx2", "sigx3"):
        return False
    header, _, _ = _read_header(raw)
    return header.get("auth") != "none"


def is_machine_bound(raw: bytes) -> bool:
    """True if this file can only be opened on the PC/account that wrote it.

    Always True for the legacy SIGX2/SIGX1 formats, which were DPAPI-wrapped by
    construction. For SIGX3 it reflects the "tie to this computer" choice.
    """
    kind = classify(raw)
    if kind in ("sigx1", "sigx2"):
        return True
    if kind != "sigx3":
        return False
    header, _, _ = _read_header(raw)
    return "machine" in header


def decrypt_with_passphrase(raw: bytes, passphrase: Optional[str] = None) -> bytes:
    """Decrypt a SIGX2/SIGX3 file. Raises :class:`BadPassphrase` if it won't open.

    ``passphrase=None`` uses the fixed no-passphrase key; pass it only for files
    where :func:`needs_passphrase` returned False.
    """
    header, header_bytes, body = _read_header(raw)
    salt = _unb64(header["salt"])
    iters = int(header.get("iters", secure.PBKDF2_ITERATIONS))
    kdf_input = _NO_PASSPHRASE_KDF_INPUT if passphrase is None else passphrase
    file_key = secure.derive_key(kdf_input, salt, iters)
    machine_bound = is_machine_bound(raw)

    try:
        if machine_bound:
            # DPAPI layer first: this is the factor that pins the file to this PC.
            try:
                body = secure.unprotect(body, entropy=file_key)
            except OSError as exc:
                raise BadPassphrase(_wrong_key_message(passphrase, True)) from exc
            except RuntimeError as exc:  # not on Windows at all
                raise BadPassphrase(
                    "This file is tied to a Windows PC and cannot be opened on this system."
                ) from exc

        if header.get("v") == 2:
            return body  # legacy: DPAPI *was* the encryption, nothing further
        return secure.aead_decrypt(file_key, body, associated_data=header_bytes)
    except secure.DecryptionError as exc:
        raise BadPassphrase(_wrong_key_message(passphrase, machine_bound)) from exc


def _wrong_key_message(passphrase: Optional[str], machine_bound: bool) -> str:
    if passphrase is None:
        return ("This file was saved without a passphrase, so it can only be read by "
                "the Windows account that created it.")
    if machine_bound:
        return "Wrong passphrase, or this file is tied to a different PC/Windows account."
    return "Wrong passphrase (or the file has been modified)."


def decrypt(raw: bytes) -> bytes:
    """Open a no-passphrase container. Raises if it needs a passphrase."""
    if needs_passphrase(raw):
        raise BadPassphrase("This file is passphrase-protected.")
    return decrypt_with_passphrase(raw, None)


def decrypt_with_hello(raw: bytes) -> bytes:
    """Decrypt a SIGX2/SIGX3 file via a live Windows Hello gesture.

    Raises :class:`hello.HelloError` if the file has no Hello unlock, Hello is
    unavailable, or the gesture fails/cancels.
    """
    header, header_bytes, body = _read_header(raw)
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

    # file_key recovered; anything failing past here means corruption, not a
    # wrong secret. Hello implies the file is machine-bound, so unwrap DPAPI.
    if is_machine_bound(raw):
        body = secure.unprotect(body, entropy=file_key)
    if header.get("v") == 2:
        return body
    return secure.aead_decrypt(file_key, body, associated_data=header_bytes)


def decrypt_legacy_v1(raw: bytes) -> bytes:
    """Decrypt a legacy SIGX1 file (DPAPI only, no passphrase)."""
    return secure.unprotect(raw[len(MAGIC_V1):])
