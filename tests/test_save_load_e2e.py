"""End-to-end save -> disk -> load, through the same functions the GUI calls.

The dialogs are stubbed so the flow runs headless, but everything below them --
protection choice, extension retargeting, container format, routing on load --
is the real code. This is the layer where a SIGX3 file was being handed to the
plain-JSON reader, producing "'utf-8' codec can't decode byte 0xdc in position
109"; a unit test of vault alone could not see that.
"""

from __future__ import annotations

import pytest

from app import fileio, vault
from app.model import Point, Signature

PASSPHRASE = "correct horse battery staple"


def make_signature(strokes: int = 2) -> Signature:
    out, t = [], 0.0
    for s in range(strokes):
        pts = []
        for i in range(4):
            t += 0.016
            pts.append(Point(10.0 + s * 30 + i * 2.5, 20.0 + i * 1.5, round(t, 3), 1.0 - i * 0.05))
        out.append(pts)
    return Signature(strokes=out, source_width=420.0, source_height=150.0)


class FakeParent:
    """Stands in for the Tk widget passed to save/load. Only wait_window is used."""

    def wait_window(self, _dlg) -> None:
        return None


@pytest.fixture
def gui(monkeypatch):
    """Drive the save dialog and unlock prompt without a display.

    Returns a controller whose ``answer``/``unlock_with`` set what the stubbed
    dialogs will return.
    """

    class Controller:
        save_answer = None
        unlock_answers: list = []
        errors: list = []

        def answer(self, **kw):
            self.save_answer = kw

        def unlock_with(self, *passphrases):
            self.unlock_answers = list(passphrases)

    ctl = Controller()

    class FakeSaveDialog:
        def __init__(self, *_a, **_kw):
            self.result = ctl.save_answer

    class FakeUnlockDialog:
        def __init__(self, *_a, **_kw):
            if ctl.unlock_answers:
                pp = ctl.unlock_answers.pop(0)
                self.result = None if pp is None else {"action": "passphrase", "passphrase": pp}
            else:
                self.result = None  # cancelled

    monkeypatch.setattr(fileio, "SaveOptionsDialog", FakeSaveDialog)
    monkeypatch.setattr(fileio, "PassphraseDialog", FakeUnlockDialog)
    monkeypatch.setattr(fileio.hello, "available", lambda: False)
    monkeypatch.setattr(fileio.messagebox, "showerror",
                        lambda *a, **k: ctl.errors.append(a))
    monkeypatch.setattr(fileio.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(fileio.messagebox, "askyesno", lambda *a, **k: True)
    return ctl


def save(gui, sig, path, **answer) -> dict:
    gui.answer(**answer)
    result = fileio.save_signature(FakeParent(), sig, str(path))
    assert result is not None, "save was unexpectedly cancelled"
    return result


# --------------------------------------------------------------------------

class TestPassphraseRoundTrip:
    """The headline case: save with a passcode, reopen with it, contents match."""

    @pytest.mark.parametrize(
        "passphrase",
        [
            "hunter2",
            "my dog has fleas 42",
            "P@$$w0rd!#%&*()_+-=[]{}|;:",
            "пароль-Ω≈ç-日本語",
            "sign🖊️here✅",
            "x" * 300,
        ],
        ids=["simple", "spaces", "symbols", "unicode", "emoji", "long"],
    )
    def test_save_then_load(self, gui, tmp_path, passphrase):
        sig = make_signature()
        saved = save(gui, sig, tmp_path / "sig.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=passphrase,
                     tie_to_machine=False, enable_hello=False)

        gui.unlock_with(passphrase)
        loaded = fileio.load_signature(FakeParent(), saved["path"])

        assert loaded is not None, f"failed to load: {gui.errors}"
        assert loaded.strokes == sig.strokes
        assert loaded.source_width == sig.source_width
        assert loaded.source_height == sig.source_height

    def test_every_sample_field_survives(self, gui, tmp_path):
        """x, y, timing and pressure must all come back bit-for-bit."""
        sig = make_signature()
        saved = save(gui, sig, tmp_path / "s.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=False, enable_hello=False)

        gui.unlock_with(PASSPHRASE)
        loaded = fileio.load_signature(FakeParent(), saved["path"])

        original = [p for stroke in sig.strokes for p in stroke]
        restored = [p for stroke in loaded.strokes for p in stroke]
        assert len(restored) == len(original)
        for a, b in zip(original, restored):
            assert (a.x, a.y, a.t, a.p) == (b.x, b.y, b.t, b.p)

    def test_file_on_disk_is_a_recognised_container(self, gui, tmp_path):
        """Regression: the saved bytes must classify as a container, not 'plain'."""
        saved = save(gui, make_signature(), tmp_path / "s.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=False, enable_hello=False)

        raw = open(saved["path"], "rb").read()
        assert vault.classify(raw) == "sigx3"
        assert raw.startswith(b"SIGX3\n")

    def test_wrong_passphrase_then_right_one(self, gui, tmp_path):
        """The unlock prompt retries; a later correct entry must still work."""
        sig = make_signature()
        saved = save(gui, sig, tmp_path / "s.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=False, enable_hello=False)

        gui.unlock_with("wrong", "also wrong", PASSPHRASE)
        loaded = fileio.load_signature(FakeParent(), saved["path"])

        assert loaded is not None
        assert loaded.strokes == sig.strokes
        assert len(gui.errors) == 2  # one complaint per bad attempt

    def test_cancelling_the_prompt_returns_none(self, gui, tmp_path):
        saved = save(gui, make_signature(), tmp_path / "s.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=False, enable_hello=False)

        gui.unlock_with(None)  # user hit Cancel
        assert fileio.load_signature(FakeParent(), saved["path"]) is None


class TestOtherModes:
    def test_tied_to_this_pc_round_trip(self, gui, tmp_path):
        sig = make_signature()
        saved = save(gui, sig, tmp_path / "s.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=True, enable_hello=False)

        assert saved["tied"] is True
        gui.unlock_with(PASSPHRASE)
        loaded = fileio.load_signature(FakeParent(), saved["path"])

        assert loaded is not None and loaded.strokes == sig.strokes

    def test_no_passphrase_opens_without_prompting(self, gui, tmp_path):
        sig = make_signature()
        saved = save(gui, sig, tmp_path / "s.sigx", mode=fileio.MODE_NO_PASSPHRASE)

        # No unlock answers queued at all: if it prompts, it gets None and fails.
        gui.unlock_with()
        loaded = fileio.load_signature(FakeParent(), saved["path"])

        assert loaded is not None and loaded.strokes == sig.strokes

    def test_plain_round_trip_and_extension(self, gui, tmp_path):
        sig = make_signature()
        saved = save(gui, sig, tmp_path / "s.sigx", mode=fileio.MODE_PLAIN)

        assert saved["path"].endswith(".sig.json")  # retargeted away from .sigx
        loaded = fileio.load_signature(FakeParent(), saved["path"])

        assert loaded is not None and loaded.strokes == sig.strokes

    def test_encrypted_choice_retargets_a_json_filename(self, gui, tmp_path):
        saved = save(gui, make_signature(), tmp_path / "s.sig.json",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=False, enable_hello=False)

        assert saved["path"].endswith(".sigx")


class TestPortabilityEndToEnd:
    def test_default_save_carries_no_machine_binding(self, gui, tmp_path):
        saved = save(gui, make_signature(), tmp_path / "s.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=False, enable_hello=False)

        assert saved["tied"] is False
        raw = open(saved["path"], "rb").read()
        assert vault.is_machine_bound(raw) is False

    def test_portable_file_opens_with_no_dpapi(self, gui, tmp_path, monkeypatch):
        """Stands in for opening the file on a PC that isn't this one."""
        sig = make_signature()
        saved = save(gui, sig, tmp_path / "s.sigx",
                     mode=fileio.MODE_PASSPHRASE, passphrase=PASSPHRASE,
                     tie_to_machine=False, enable_hello=False)

        raw = open(saved["path"], "rb").read()
        monkeypatch.setattr(vault.secure, "_AVAILABLE", False)

        recovered = Signature.from_json_bytes(
            vault.decrypt_with_passphrase(raw, PASSPHRASE)
        )
        assert recovered.strokes == sig.strokes
