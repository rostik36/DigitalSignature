"""Save-with-passphrase / load-with-passphrase round trips for the SIGX2 vault.

Two groups of tests live here:

* Real-DPAPI tests (``TestSameMachine``) exercise :mod:`app.vault` exactly as the
  app uses it. They only run on Windows, since DPAPI is a Windows API.
* Simulated-DPAPI tests (``TestPortability``) swap :func:`app.secure.protect` /
  :func:`app.secure.unprotect` for a fake that reproduces DPAPI's defining
  property -- a blob is bound to the account that created it. These run
  everywhere and pin down what happens when a ``.sigx`` file is copied to a
  second PC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys

import pytest

from app import secure, vault
from app.model import Point, Signature

PASSPHRASE = "correct horse battery staple"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def make_signature() -> Signature:
    """A small but realistic capture: two strokes, monotonic timestamps."""
    return Signature(
        strokes=[
            [Point(10.0, 20.0, 0.000, 1.0), Point(12.5, 22.5, 0.016, 1.0),
             Point(15.0, 21.0, 0.032, 0.8)],
            [Point(30.0, 25.0, 0.100, 1.0), Point(33.0, 27.5, 0.116, 0.9)],
        ],
        source_width=420.0,
        source_height=150.0,
    )


class FakeDPAPI:
    """Stand-in for DPAPI that binds each blob to an 'account' identity.

    This mirrors the property that matters for this bug: ``CryptProtectData``
    keys off the logged-on user's secrets, so only that same account on that
    same machine can call ``CryptUnprotectData`` successfully. The extra
    ``entropy`` argument must match too.
    """

    def __init__(self, account: str = "PC-A\\user") -> None:
        self.account = account

    def protect(self, data: bytes, entropy: bytes = b"") -> bytes:
        key = hashlib.sha256(self.account.encode() + b"|" + entropy).digest()
        tag = hmac.new(key, data, hashlib.sha256).digest()
        # Not real encryption -- the test only cares about the binding.
        return tag + data

    def unprotect(self, blob: bytes, entropy: bytes = b"") -> bytes:
        tag, data = blob[:32], blob[32:]
        key = hashlib.sha256(self.account.encode() + b"|" + entropy).digest()
        if not hmac.compare_digest(tag, hmac.new(key, data, hashlib.sha256).digest()):
            raise OSError(13, "CryptUnprotectData failed (wrong account/machine or entropy)")
        return data


@pytest.fixture
def fake_dpapi(monkeypatch):
    """Install the fake DPAPI and hand back the object so tests can move 'PC'."""
    fake = FakeDPAPI()
    monkeypatch.setattr(secure, "protect", fake.protect)
    monkeypatch.setattr(secure, "unprotect", fake.unprotect)
    return fake


needs_windows = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="DPAPI is Windows-only"
)


# --------------------------------------------------------------------------
# real DPAPI: what happens on a single PC
# --------------------------------------------------------------------------

@needs_windows
class TestSameMachine:
    def test_save_then_load_with_passphrase(self):
        """The core round trip: encrypt with a passphrase, decrypt with it."""
        sig = make_signature()
        raw = vault.encrypt(sig.to_json_bytes(), PASSPHRASE)

        loaded = Signature.from_json_bytes(vault.decrypt_with_passphrase(raw, PASSPHRASE))

        assert loaded.strokes == sig.strokes

    def test_wrong_passphrase_is_rejected(self):
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw, "not the passphrase")

    @pytest.mark.parametrize(
        "passphrase",
        [
            "short",
            "with spaces and PUNCTUATION!@#$%^&*()",
            "ünïcodé-pässphrase-Ω≈ç",
            "emoji-🖊️-signature",
            "a" * 512,
        ],
        ids=["short", "punctuation", "unicode", "emoji", "very-long"],
    )
    def test_round_trip_across_passphrase_shapes(self, passphrase):
        """Non-ASCII passphrases must survive; they are UTF-8 encoded in the KDF."""
        sig = make_signature()
        raw = vault.encrypt(sig.to_json_bytes(), passphrase)

        loaded = Signature.from_json_bytes(vault.decrypt_with_passphrase(raw, passphrase))

        assert loaded.strokes == sig.strokes

    def test_trailing_whitespace_matters(self):
        """A trailing space is a different passphrase -- a real user-typo trap."""
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw, PASSPHRASE + " ")

    def test_each_save_uses_a_fresh_salt(self):
        payload = make_signature().to_json_bytes()

        first = vault.encrypt(payload, PASSPHRASE)
        second = vault.encrypt(payload, PASSPHRASE)

        assert _header(first)["salt"] != _header(second)["salt"]
        assert first != second
        # Both still open with the same passphrase.
        for raw in (first, second):
            assert vault.decrypt_with_passphrase(raw, PASSPHRASE) == payload

    def test_plaintext_does_not_leak_into_the_file(self):
        sig = make_signature()
        raw = vault.encrypt(sig.to_json_bytes(), PASSPHRASE)

        # A distinctive coordinate from the capture must not appear in the clear.
        assert b"22.5" not in raw
        assert b"strokes" not in raw

    def test_iteration_count_is_honoured_from_the_header(self):
        """Files record their own KDF cost, so old files stay readable."""
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)
        header = _header(raw)

        assert header["iters"] == secure.PBKDF2_ITERATIONS
        assert header["kdf"] == "pbkdf2-sha256"


# --------------------------------------------------------------------------
# format-level checks that need no DPAPI at all
# --------------------------------------------------------------------------

class TestFormat:
    def test_classify(self):
        assert vault.classify(b"SIGX3\n{}\nbody") == "sigx3"
        assert vault.classify(b"SIGX2\n{}\nbody") == "sigx2"
        assert vault.classify(b"SIGX1\nbody") == "sigx1"
        assert vault.classify(b'{"strokes": []}') == "plain"

    def test_header_is_cleartext_json_without_secrets(self):
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)
        header = _header(raw)

        assert header["v"] == 3
        assert set(header) == {"v", "kdf", "salt", "iters", "cipher"}
        assert PASSPHRASE not in json.dumps(header)

    def test_no_hello_section_unless_requested(self):
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        assert not vault.header_has_hello(raw)

    def test_header_is_authenticated(self):
        """Editing the header must break the tag, not silently change behaviour."""
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)
        tampered = raw.replace(b'"cipher":"aes-256-gcm"', b'"cipher":"aes-256-gcM"')
        assert tampered != raw

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(tampered, PASSPHRASE)

    def test_truncated_file_is_rejected(self):
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw[:-1], PASSPHRASE)


# --------------------------------------------------------------------------
# saving without a passphrase
# --------------------------------------------------------------------------

class TestNoPassphraseMode:
    def test_round_trip_without_prompting(self, fake_dpapi):
        payload = make_signature().to_json_bytes()
        raw = vault.encrypt(payload, None, tie_to_machine=True)

        assert vault.needs_passphrase(raw) is False
        assert vault.decrypt(raw) == payload

    def test_header_marks_the_file(self, fake_dpapi):
        raw = vault.encrypt(b'{"strokes": []}', None, tie_to_machine=True)

        assert _header(raw)["auth"] == "none"

    def test_passphrase_files_still_require_one(self):
        raw = vault.encrypt(b'{"strokes": []}', PASSPHRASE)

        assert vault.needs_passphrase(raw) is True
        assert "auth" not in _header(raw)
        with pytest.raises(vault.BadPassphrase):
            vault.decrypt(raw)  # refuses rather than silently trying

    def test_empty_string_is_rejected_as_ambiguous(self):
        """'' must not silently mean 'no passphrase' -- that would be a footgun."""
        with pytest.raises(ValueError):
            vault.encrypt(b'{"strokes": []}', "")

    def test_refuses_to_write_an_unprotected_container(self):
        """No passphrase AND no machine binding would protect nothing at all."""
        with pytest.raises(ValueError, match="no protection at all"):
            vault.encrypt(b'{"strokes": []}', None, tie_to_machine=False)

    @needs_windows
    def test_still_encrypted_on_disk(self):
        """No passphrase must not mean no encryption: the payload stays hidden."""
        sig = make_signature()
        raw = vault.encrypt(sig.to_json_bytes(), None, tie_to_machine=True)

        assert b"strokes" not in raw
        assert b"22.5" not in raw

    def test_still_bound_to_the_creating_account(self, fake_dpapi):
        """The DPAPI factor is the *only* one left, so it must still apply."""
        fake_dpapi.account = "PC-A\\user"
        raw = vault.encrypt(make_signature().to_json_bytes(), None, tie_to_machine=True)

        fake_dpapi.account = "PC-B\\user"

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt(raw)

    def test_needs_passphrase_is_false_for_plain_files(self):
        assert vault.needs_passphrase(b'{"strokes": []}') is False


# --------------------------------------------------------------------------
# moving a file to a second PC
#
# The original complaint was that a file made on one PC would not open on
# another. Files are now portable by default, and machine binding is opt-in;
# these tests hold both halves of that contract in place.
# --------------------------------------------------------------------------

class TestPortability:
    def test_default_save_is_not_machine_bound(self):
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        assert vault.is_machine_bound(raw) is False
        assert b"dpapi" not in raw.split(b"\n", 2)[1]

    def test_default_file_opens_on_a_different_pc(self, fake_dpapi):
        """The fix for the reported bug: correct passphrase, different machine, opens.

        Moving the fake DPAPI 'account' models carrying the file to another PC.
        A portable file never touches DPAPI, so the move is irrelevant to it.
        """
        payload = make_signature().to_json_bytes()
        fake_dpapi.account = "PC-A\\user"
        raw = vault.encrypt(payload, PASSPHRASE)

        fake_dpapi.account = "PC-B\\user"  # same person, different machine

        assert vault.decrypt_with_passphrase(raw, PASSPHRASE) == payload

    def test_portable_file_still_needs_the_right_passphrase(self, fake_dpapi):
        """Portable must not mean unprotected."""
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        fake_dpapi.account = "PC-B\\user"

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw, "not the passphrase")

    def test_opting_in_ties_the_file_to_this_pc(self, fake_dpapi):
        """The checkbox has to actually do something."""
        fake_dpapi.account = "PC-A\\user"
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE,
                            tie_to_machine=True)

        assert vault.is_machine_bound(raw) is True
        fake_dpapi.account = "PC-B\\user"

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw, PASSPHRASE)

    def test_tied_file_also_refuses_another_account_on_the_same_pc(self, fake_dpapi):
        fake_dpapi.account = "PC-A\\alice"
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE,
                            tie_to_machine=True)

        fake_dpapi.account = "PC-A\\bob"

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw, PASSPHRASE)

    def test_hello_requires_machine_binding(self):
        """Hello keys are machine-bound, so the combination must be rejected."""
        with pytest.raises(ValueError, match="tie_to_machine"):
            vault.encrypt(b'{"strokes": []}', PASSPHRASE, enable_hello=True)


class TestLegacyFiles:
    """Old SIGX2 files were always DPAPI-wrapped; they must still open."""

    def test_v2_is_reported_as_machine_bound(self, fake_dpapi):
        v2 = _make_v2(b'{"strokes": []}', PASSPHRASE)

        assert vault.classify(v2) == "sigx2"
        assert vault.is_machine_bound(v2) is True
        assert vault.needs_passphrase(v2) is True

    def test_v2_round_trip(self, fake_dpapi):
        payload = b'{"strokes": [[[1.0, 2.0, 0.0, 1.0]]]}'
        v2 = _make_v2(payload, PASSPHRASE)

        assert vault.decrypt_with_passphrase(v2, PASSPHRASE) == payload

    def test_v2_wrong_passphrase(self, fake_dpapi):
        v2 = _make_v2(b'{"strokes": []}', PASSPHRASE)

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(v2, "wrong")


def _make_v2(payload: bytes, passphrase: str) -> bytes:
    """Build a legacy SIGX2 file the way the old encrypt() did."""
    import base64 as _b64mod
    import os as _os

    salt = _os.urandom(16)
    iters = secure.PBKDF2_ITERATIONS
    file_key = secure.derive_key(passphrase, salt, iters)
    body = secure.protect(payload, entropy=file_key)
    header = json.dumps(
        {"v": 2, "kdf": "pbkdf2-sha256",
         "salt": _b64mod.b64encode(salt).decode("ascii"), "iters": iters},
        separators=(",", ":"),
    ).encode("utf-8")
    return vault.MAGIC_V2 + header + b"\n" + body


# --------------------------------------------------------------------------
# small parsing helpers (mirror vault._split_v2 without reaching into privates)
# --------------------------------------------------------------------------

def _split(raw: bytes):
    magic = vault.MAGIC_V3 if raw.startswith(vault.MAGIC_V3) else vault.MAGIC_V2
    rest = raw[len(magic):]
    nl = rest.index(b"\n")
    return json.loads(rest[:nl].decode("utf-8")), rest[nl + 1:]


def _header(raw: bytes) -> dict:
    return _split(raw)[0]
