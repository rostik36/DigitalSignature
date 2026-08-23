"""Which reader a file gets routed to, based on its magic bytes.

Regression cover for a real failure: SIGX3 files were falling through to the
plain-JSON reader, which tried to UTF-8 decode the binary ciphertext and died
with "'utf-8' codec can't decode byte 0x8a in position 110" -- an error that
tells the user nothing about the real problem.
"""

from __future__ import annotations

import json

import pytest

from app import vault
from app.model import Point, Signature


def make_signature() -> Signature:
    return Signature(
        strokes=[[Point(10.0, 20.0, 0.0, 1.0), Point(15.0, 25.0, 0.05, 0.8)]],
        source_width=420.0,
        source_height=150.0,
    )


class TestClassify:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (b"SIGX3\n{}\nbody", "sigx3"),
            (b"SIGX2\n{}\nbody", "sigx2"),
            (b"SIGX1\nbody", "sigx1"),
            (b"SIGX9\n{}\nbody", "sigx-unknown"),
            (b'{"strokes": []}', "plain"),
        ],
        ids=["v3", "v2", "v1", "future", "plain"],
    )
    def test_magic_bytes(self, raw, expected):
        assert vault.classify(raw) == expected

    def test_future_versions_are_not_treated_as_plain_json(self):
        """The whole point: a newer container must never reach the JSON reader."""
        assert vault.classify(b"SIGX7\n{}\n\x8a\xff") != "plain"


class TestPlainReaderRefusesContainers:
    """Signature.load() handles plain JSON and legacy SIGX1 only."""

    def _write(self, tmp_path, name, data: bytes) -> str:
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)

    def test_sigx3_raises_a_useful_message(self, tmp_path):
        raw = vault.encrypt(make_signature().to_json_bytes(), "pw")
        path = self._write(tmp_path, "s.sigx", raw)

        with pytest.raises(ValueError) as err:
            Signature.load(path)

        # Must name the problem, not surface a codec error.
        assert "encrypted" in str(err.value).lower()

    def test_unknown_container_mentions_updating(self, tmp_path):
        path = self._write(tmp_path, "s.sigx", b"SIGX9\n{}\n\x8a\xff\x00binary")

        with pytest.raises(ValueError) as err:
            Signature.load(path)

        assert "update" in str(err.value).lower()

    def test_no_unicode_decode_error_escapes(self, tmp_path):
        """The original bug surfaced as UnicodeDecodeError; it must not recur."""
        for magic in (b"SIGX2\n", b"SIGX3\n", b"SIGX9\n"):
            path = self._write(tmp_path, "x.sigx", magic + b'{"v":9}\n\x8a\x9b\xff')
            with pytest.raises(ValueError):  # not UnicodeDecodeError
                Signature.load(path)

    def test_plain_json_still_loads(self, tmp_path):
        sig = make_signature()
        path = self._write(tmp_path, "s.sig.json", sig.to_json_bytes())

        assert Signature.load(path).strokes == sig.strokes


class TestRoundTripThroughTheSaveFormat:
    """A file written by encrypt() must be readable by the matching decrypt."""

    @pytest.mark.parametrize("tied", [False, True], ids=["portable", "tied"])
    def test_encrypt_then_decrypt(self, tied):
        payload = make_signature().to_json_bytes()
        raw = vault.encrypt(payload, "pw", tie_to_machine=tied)

        assert vault.classify(raw) == "sigx3"
        assert vault.decrypt_with_passphrase(raw, "pw") == payload

    def test_saved_file_is_recognised_as_a_container(self, tmp_path):
        """Guards the routing check in fileio.load_signature."""
        raw = vault.encrypt(make_signature().to_json_bytes(), "pw")

        assert vault.classify(raw) in ("sigx2", "sigx3")
        assert vault.needs_passphrase(raw) is True
