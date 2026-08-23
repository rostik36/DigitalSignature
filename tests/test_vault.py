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
        assert vault.classify(b"SIGX2\n{}\nbody") == "sigx2"
        assert vault.classify(b"SIGX1\nbody") == "sigx1"
        assert vault.classify(b'{"strokes": []}') == "plain"

    def test_header_is_cleartext_json_without_secrets(self, fake_dpapi):
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)
        header = _header(raw)

        assert header["v"] == 2
        assert set(header) == {"v", "kdf", "salt", "iters"}
        assert PASSPHRASE not in json.dumps(header)

    def test_no_hello_section_unless_requested(self, fake_dpapi):
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        assert not vault.header_has_hello(raw)


# --------------------------------------------------------------------------
# saving without a passphrase
# --------------------------------------------------------------------------

class TestNoPassphraseMode:
    def test_round_trip_without_prompting(self, fake_dpapi):
        payload = make_signature().to_json_bytes()
        raw = vault.encrypt(payload, None)

        assert vault.needs_passphrase(raw) is False
        assert vault.decrypt(raw) == payload

    def test_header_marks_the_file(self, fake_dpapi):
        raw = vault.encrypt(b'{"strokes": []}', None)

        assert _header(raw)["auth"] == "none"

    def test_passphrase_files_still_require_one(self, fake_dpapi):
        raw = vault.encrypt(b'{"strokes": []}', PASSPHRASE)

        assert vault.needs_passphrase(raw) is True
        assert "auth" not in _header(raw)
        with pytest.raises(vault.BadPassphrase):
            vault.decrypt(raw)  # refuses rather than silently trying

    def test_empty_string_is_rejected_as_ambiguous(self, fake_dpapi):
        """'' must not silently mean 'no passphrase' -- that would be a footgun."""
        with pytest.raises(ValueError):
            vault.encrypt(b'{"strokes": []}', "")

    @needs_windows
    def test_still_encrypted_on_disk(self):
        """No passphrase must not mean no encryption: the payload stays hidden.

        Uses real DPAPI -- FakeDPAPI models only the account binding and keeps
        the plaintext, so it cannot answer a confidentiality question.
        """
        sig = make_signature()
        raw = vault.encrypt(sig.to_json_bytes(), None)

        assert b"strokes" not in raw
        assert b"22.5" not in raw

    def test_still_bound_to_the_creating_account(self, fake_dpapi):
        """The DPAPI factor is the *only* one left, so it must still apply."""
        fake_dpapi.account = "PC-A\\user"
        raw = vault.encrypt(make_signature().to_json_bytes(), None)

        fake_dpapi.account = "PC-B\\user"

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt(raw)

    def test_needs_passphrase_is_false_for_non_sigx2(self):
        assert vault.needs_passphrase(b'{"strokes": []}') is False


# --------------------------------------------------------------------------
# the reported bug: move the file to a second PC
# --------------------------------------------------------------------------

class TestPortability:
    def test_same_pc_same_passphrase_opens(self, fake_dpapi):
        """Baseline for the two tests below: nothing moved, so it opens."""
        payload = make_signature().to_json_bytes()
        raw = vault.encrypt(payload, PASSPHRASE)

        assert vault.decrypt_with_passphrase(raw, PASSPHRASE) == payload

    def test_correct_passphrase_fails_on_a_different_pc(self, fake_dpapi):
        """A .sigx written on PC A does NOT open on PC B, passphrase notwithstanding.

        This reproduces the reported symptom. The passphrase is correct and the
        file is byte-identical; the DPAPI layer underneath is what refuses,
        because the blob is bound to the Windows account that created it.
        """
        fake_dpapi.account = "PC-A\\user"
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        fake_dpapi.account = "PC-B\\user"  # same person, different machine

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw, PASSPHRASE)

    def test_also_fails_for_a_different_account_on_the_same_pc(self, fake_dpapi):
        fake_dpapi.account = "PC-A\\alice"
        raw = vault.encrypt(make_signature().to_json_bytes(), PASSPHRASE)

        fake_dpapi.account = "PC-A\\bob"

        with pytest.raises(vault.BadPassphrase):
            vault.decrypt_with_passphrase(raw, PASSPHRASE)

    def test_passphrase_alone_does_not_determine_the_key(self, fake_dpapi):
        """Shows the design intent: passphrase is necessary but not sufficient."""
        fake_dpapi.account = "PC-A\\user"
        from_a = vault.encrypt(b'{"strokes": []}', PASSPHRASE)

        fake_dpapi.account = "PC-B\\user"
        from_b = vault.encrypt(b'{"strokes": []}', PASSPHRASE)

        # Same passphrase, same payload, but the bodies are not interchangeable.
        header_a, body_a = _split(from_a)
        header_b, body_b = _split(from_b)
        with pytest.raises(OSError):
            secure.unprotect(body_a, entropy=secure.derive_key(
                PASSPHRASE, _b64d(header_a["salt"]), header_a["iters"]))
        # ...while the file made on this PC opens fine.
        assert secure.unprotect(body_b, entropy=secure.derive_key(
            PASSPHRASE, _b64d(header_b["salt"]), header_b["iters"])) == b'{"strokes": []}'


# --------------------------------------------------------------------------
# small parsing helpers (mirror vault._split_v2 without reaching into privates)
# --------------------------------------------------------------------------

def _split(raw: bytes):
    rest = raw[len(vault.MAGIC_V2):]
    nl = rest.index(b"\n")
    return json.loads(rest[:nl].decode("utf-8")), rest[nl + 1:]


def _header(raw: bytes) -> dict:
    return _split(raw)[0]


def _b64d(text: str) -> bytes:
    import base64
    return base64.b64decode(text.encode("ascii"))
