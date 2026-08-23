"""Interactive save/load with passphrase + optional Windows Hello prompts.

Shared by the main window and the capture window. Keeps all the dialog and
format-routing logic in one place:

- ``.sigx``  -> passphrase-encrypted (SIGX2), optionally Hello-unlockable.
- ``.sigx1`` legacy / plain ``.json`` -> handled by ``Signature.load`` directly.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Optional

from . import hello, vault
from .model import Signature


#: Protection modes offered when saving. Values are stored in the result dict.
MODE_PASSPHRASE = "passphrase"   # SIGX2, passphrase + Windows account
MODE_NO_PASSPHRASE = "nopass"    # SIGX2, Windows account only, no prompt
MODE_PLAIN = "plain"             # unencrypted JSON, portable


class PassphraseDialog(tk.Toplevel):
    """Prompt for a passphrase, in 'set' (with confirm) or 'enter' mode.

    Result (``self.result``) is one of:
      * ``None`` -- cancelled
      * ``{"action": "passphrase", "passphrase": str, "enable_hello": bool}``
      * ``{"action": "hello"}`` -- user chose the Windows Hello button
    """

    def __init__(self, parent: tk.Misc, mode: str, hello_available: bool = False) -> None:
        super().__init__(parent)
        self.mode = mode  # "set" | "enter"
        self.hello_available = hello_available
        self.result: Optional[dict] = None

        self.title("Encrypted signature")
        self.resizable(False, False)
        self.configure(bg="#f4f6f9")
        self.transient(parent if isinstance(parent, (tk.Tk, tk.Toplevel)) else None)
        self.grab_set()

        self._pp = tk.StringVar()
        self._pp2 = tk.StringVar()
        self._show = tk.BooleanVar(value=False)
        self._enable_hello = tk.BooleanVar(value=hello_available)
        self._build()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self._entry.focus_set()

    def _build(self) -> None:
        title = "Set a passphrase to encrypt this signature" if self.mode == "set" \
            else "Enter the passphrase to unlock this signature"
        tk.Label(self, text=title, bg="#f4f6f9", fg="#1f2d3d",
                 font=("Segoe UI", 11, "bold"), wraplength=360, justify="left", pady=8
                 ).pack(fill="x", padx=16, pady=(10, 2))

        body = tk.Frame(self, bg="#f4f6f9")
        body.pack(fill="x", padx=16, pady=4)

        tk.Label(body, text="Passphrase", bg="#f4f6f9", fg="#33475b", font=("Segoe UI", 10)
                 ).grid(row=0, column=0, sticky="w", pady=4)
        self._entry = tk.Entry(body, textvariable=self._pp, show="•", width=30)
        self._entry.grid(row=0, column=1, sticky="ew", pady=4, padx=(10, 0))

        if self.mode == "set":
            tk.Label(body, text="Confirm", bg="#f4f6f9", fg="#33475b", font=("Segoe UI", 10)
                     ).grid(row=1, column=0, sticky="w", pady=4)
            self._entry2 = tk.Entry(body, textvariable=self._pp2, show="•", width=30)
            self._entry2.grid(row=1, column=1, sticky="ew", pady=4, padx=(10, 0))
        body.columnconfigure(1, weight=1)

        tk.Checkbutton(self, text="Show passphrase", variable=self._show, command=self._toggle_show,
                       bg="#f4f6f9", activebackground="#f4f6f9", fg="#52606d", font=("Segoe UI", 9)
                       ).pack(anchor="w", padx=16)

        if self.mode == "set" and self.hello_available:
            tk.Checkbutton(self, text="Also allow Windows Hello (face/fingerprint) unlock",
                           variable=self._enable_hello, bg="#f4f6f9", activebackground="#f4f6f9",
                           fg="#52606d", font=("Segoe UI", 9)).pack(anchor="w", padx=16)

        if self.mode == "set":
            tk.Label(self, text="The passphrase is required (in addition to your Windows account) "
                                "to open this file. There is no way to recover it if forgotten.",
                     bg="#f4f6f9", fg="#7b8794", font=("Segoe UI", 8, "italic"),
                     wraplength=360, justify="left").pack(fill="x", padx=16, pady=(2, 4))

        bar = tk.Frame(self, bg="#f4f6f9")
        bar.pack(fill="x", padx=14, pady=10)
        tk.Button(bar, text="Cancel", command=self._cancel, relief="flat", bg="#e7ecf3",
                  padx=12, pady=4).pack(side="right", padx=4)
        ok_text = "Encrypt & save" if self.mode == "set" else "Unlock"
        tk.Button(bar, text=ok_text, command=self._ok, relief="flat", bg="#2d6cdf", fg="white",
                  padx=12, pady=4).pack(side="right", padx=4)
        if self.mode == "enter" and self.hello_available:
            tk.Button(bar, text="Use Windows Hello", command=self._use_hello, relief="flat",
                      bg="#3aa76d", fg="white", padx=12, pady=4).pack(side="left", padx=4)

    def _toggle_show(self) -> None:
        ch = "" if self._show.get() else "•"
        self._entry.config(show=ch)
        if self.mode == "set":
            self._entry2.config(show=ch)

    def _ok(self) -> None:
        pp = self._pp.get()
        if not pp:
            messagebox.showinfo("Passphrase", "Please enter a passphrase.", parent=self)
            return
        if self.mode == "set":
            if len(pp) < 4:
                messagebox.showinfo("Passphrase", "Use at least 4 characters.", parent=self)
                return
            if pp != self._pp2.get():
                messagebox.showinfo("Passphrase", "The two passphrases don't match.", parent=self)
                return
        self.result = {"action": "passphrase", "passphrase": pp,
                       "enable_hello": bool(self._enable_hello.get()) if self.mode == "set" else False}
        self.grab_release()
        self.destroy()

    def _use_hello(self) -> None:
        self.result = {"action": "hello"}
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()


class SaveOptionsDialog(tk.Toplevel):
    """Choose how a signature is protected on disk.

    Result (``self.result``) is ``None`` if cancelled, otherwise::

        {"mode": MODE_PASSPHRASE, "passphrase": str, "enable_hello": bool}
        {"mode": MODE_NO_PASSPHRASE}
        {"mode": MODE_PLAIN}
    """

    def __init__(self, parent: tk.Misc, initial_mode: str = MODE_PASSPHRASE,
                 hello_available: bool = False) -> None:
        super().__init__(parent)
        self.hello_available = hello_available
        self.result: Optional[dict] = None

        self.title("Save signature")
        self.resizable(False, False)
        self.configure(bg="#f4f6f9")
        self.transient(parent if isinstance(parent, (tk.Tk, tk.Toplevel)) else None)
        self.grab_set()

        self._mode = tk.StringVar(value=initial_mode)
        self._pp = tk.StringVar()
        self._pp2 = tk.StringVar()
        self._show = tk.BooleanVar(value=False)
        self._enable_hello = tk.BooleanVar(value=hello_available)
        self._build()
        self._sync_enabled()
        self.bind("<Escape>", lambda _e: self._cancel())
        self._entry.focus_set()

    # -- layout ---------------------------------------------------------
    def _option(self, parent: tk.Misc, mode: str, title: str, badge: str,
                badge_fg: str, detail: str) -> tk.Frame:
        row = tk.Frame(parent, bg="#f4f6f9")
        row.pack(fill="x", pady=(6, 0))
        head = tk.Frame(row, bg="#f4f6f9")
        head.pack(fill="x")
        tk.Radiobutton(head, text=title, value=mode, variable=self._mode,
                       command=self._sync_enabled, bg="#f4f6f9", activebackground="#f4f6f9",
                       fg="#1f2d3d", font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(head, text=badge, bg="#f4f6f9", fg=badge_fg,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(row, text=detail, bg="#f4f6f9", fg="#7b8794", font=("Segoe UI", 8),
                 wraplength=380, justify="left").pack(anchor="w", padx=(24, 0))
        return row

    def _build(self) -> None:
        tk.Label(self, text="How should this signature be protected?", bg="#f4f6f9",
                 fg="#1f2d3d", font=("Segoe UI", 11, "bold"), wraplength=400,
                 justify="left").pack(fill="x", padx=16, pady=(12, 2))

        wrap = tk.Frame(self, bg="#f4f6f9")
        wrap.pack(fill="x", padx=16)

        # 1. passphrase
        self._option(wrap, MODE_PASSPHRASE, "Passphrase", "strongest", "#2f855a",
                     "Needs the passphrase AND this Windows account to open. "
                     "Cannot be recovered if forgotten.")
        fields = tk.Frame(wrap, bg="#f4f6f9")
        fields.pack(fill="x", padx=(24, 0), pady=(2, 0))
        tk.Label(fields, text="Passphrase", bg="#f4f6f9", fg="#33475b",
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=2)
        self._entry = tk.Entry(fields, textvariable=self._pp, show="•", width=26)
        self._entry.grid(row=0, column=1, sticky="ew", pady=2, padx=(8, 0))
        tk.Label(fields, text="Confirm", bg="#f4f6f9", fg="#33475b",
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=2)
        self._entry2 = tk.Entry(fields, textvariable=self._pp2, show="•", width=26)
        self._entry2.grid(row=1, column=1, sticky="ew", pady=2, padx=(8, 0))
        fields.columnconfigure(1, weight=1)

        self._show_cb = tk.Checkbutton(wrap, text="Show passphrase", variable=self._show,
                                       command=self._toggle_show, bg="#f4f6f9",
                                       activebackground="#f4f6f9", fg="#52606d",
                                       font=("Segoe UI", 8))
        self._show_cb.pack(anchor="w", padx=(24, 0))

        self._hello_cb = tk.Checkbutton(
            wrap, text="Also allow Windows Hello (face/fingerprint) unlock",
            variable=self._enable_hello, bg="#f4f6f9", activebackground="#f4f6f9",
            fg="#52606d", font=("Segoe UI", 8))
        if self.hello_available:
            self._hello_cb.pack(anchor="w", padx=(24, 0))

        tk.Frame(self, bg="#dde3ec", height=1).pack(fill="x", padx=16, pady=(10, 0))

        # 2. no passphrase, DPAPI only
        self._option(wrap, MODE_NO_PASSPHRASE, "No passphrase", "this PC only", "#b7791f",
                     "Saves immediately with no prompt. Still encrypted and unreadable "
                     "on another PC or account — but anyone using your unlocked "
                     "Windows session can open it.")

        # 3. plain
        self._option(wrap, MODE_PLAIN, "No protection", "portable", "#c53030",
                     "⚠ Saved as readable JSON. Anyone who gets this file can replay "
                     "your signature. Use it to move a signature to another PC, then "
                     "re-save it there with a passphrase and delete the copy.")

        bar = tk.Frame(self, bg="#f4f6f9")
        bar.pack(fill="x", padx=14, pady=12)
        tk.Button(bar, text="Cancel", command=self._cancel, relief="flat", bg="#e7ecf3",
                  padx=12, pady=4).pack(side="right", padx=4)
        tk.Button(bar, text="Save", command=self._ok, relief="flat", bg="#2d6cdf",
                  fg="white", padx=14, pady=4).pack(side="right", padx=4)

    # -- behaviour ------------------------------------------------------
    def _sync_enabled(self) -> None:
        """Grey out the passphrase fields unless that mode is selected."""
        on = self._mode.get() == MODE_PASSPHRASE
        state = "normal" if on else "disabled"
        for w in (self._entry, self._entry2, self._show_cb, self._hello_cb):
            w.config(state=state)

    def _toggle_show(self) -> None:
        ch = "" if self._show.get() else "•"
        self._entry.config(show=ch)
        self._entry2.config(show=ch)

    def _ok(self) -> None:
        mode = self._mode.get()
        if mode == MODE_PASSPHRASE:
            pp = self._pp.get()
            if len(pp) < 4:
                messagebox.showinfo("Passphrase", "Use at least 4 characters.", parent=self)
                return
            if pp != self._pp2.get():
                messagebox.showinfo("Passphrase", "The two passphrases don't match.", parent=self)
                return
            self.result = {"mode": mode, "passphrase": pp,
                           "enable_hello": bool(self._enable_hello.get())}
        elif mode == MODE_PLAIN:
            if not messagebox.askyesno(
                "Save without protection",
                "This file will NOT be encrypted. Anyone who opens it can copy and "
                "replay your handwritten signature.\n\nSave it anyway?",
                icon="warning", default="no", parent=self,
            ):
                return
            self.result = {"mode": mode}
        else:
            self.result = {"mode": mode}
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()


def _retarget(path: str, encrypted: bool) -> str:
    """Return ``path`` with the extension matching the chosen protection mode."""
    low = path.lower()
    for ext in (".sig.json", ".sigx", ".json"):
        if low.endswith(ext):
            path = path[: -len(ext)]
            break
    return path + (".sigx" if encrypted else ".sig.json")


#: Human-readable status text per mode, for the caller's status bar.
MODE_LABELS = {
    MODE_PASSPHRASE: "encrypted with a passphrase",
    MODE_NO_PASSPHRASE: "encrypted, this PC only",
    MODE_PLAIN: "NOT encrypted",
}


def save_signature(parent: tk.Misc, sig: Signature, path: str) -> Optional[dict]:
    """Save ``sig``, asking how it should be protected.

    The chosen mode decides the real extension, so picking "No protection" for a
    ``.sigx`` filename writes ``.sig.json`` instead (and vice versa). Returns
    ``None`` if cancelled, else ``{"path": <written path>, "mode": <MODE_*>}``.
    """
    initial = MODE_PLAIN if not path.lower().endswith(".sigx") else MODE_PASSPHRASE
    dlg = SaveOptionsDialog(parent, initial_mode=initial, hello_available=hello.available())
    parent.wait_window(dlg)
    if not dlg.result:
        return None

    mode = dlg.result["mode"]
    if mode == MODE_PLAIN:
        path = _retarget(path, encrypted=False)
        sig.save(path)
        return {"path": path, "mode": mode}

    path = _retarget(path, encrypted=True)
    if mode == MODE_NO_PASSPHRASE:
        raw = vault.encrypt(sig.to_json_bytes(), None)
    else:
        pp = dlg.result["passphrase"]
        want_hello = dlg.result.get("enable_hello", False)
        try:
            raw = vault.encrypt(sig.to_json_bytes(), pp, enable_hello=want_hello)
        except hello.HelloError as exc:
            if not messagebox.askyesno(
                "Windows Hello",
                f"Couldn't enable Windows Hello:\n{exc}\n\nSave with the passphrase only?",
                parent=parent,
            ):
                return None
            raw = vault.encrypt(sig.to_json_bytes(), pp, enable_hello=False)

    with open(path, "wb") as fh:
        fh.write(raw)
    return {"path": path, "mode": mode}


def load_signature(parent: tk.Misc, path: str) -> Optional[Signature]:
    """Load a signature, prompting for passphrase/Hello on ``.sigx`` files.

    Returns None if the user cancels. Raises on unreadable/corrupt files."""
    with open(path, "rb") as fh:
        raw = fh.read()

    if vault.classify(raw) != "sigx2":
        return Signature.load(path)  # plain JSON or legacy SIGX1

    if not vault.needs_passphrase(raw):
        # Saved without a passphrase: DPAPI alone gates it, so open silently.
        try:
            return Signature.from_json_bytes(vault.decrypt(raw))
        except vault.BadPassphrase as exc:
            messagebox.showerror("Can't open signature", str(exc), parent=parent)
            return None

    has_hello = vault.header_has_hello(raw)
    hello_av = has_hello and hello.available()

    for _ in range(5):
        dlg = PassphraseDialog(parent, "enter", hello_available=hello_av)
        parent.wait_window(dlg)
        if not dlg.result:
            return None
        if dlg.result.get("action") == "hello":
            try:
                return Signature.from_json_bytes(vault.decrypt_with_hello(raw))
            except hello.HelloError as exc:
                messagebox.showerror("Windows Hello", str(exc), parent=parent)
                continue
        try:
            return Signature.from_json_bytes(
                vault.decrypt_with_passphrase(raw, dlg.result["passphrase"])
            )
        except vault.BadPassphrase:
            messagebox.showerror("Wrong passphrase",
                                 "That passphrase didn't work. Try again.", parent=parent)
            continue
    return None
