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


def save_signature(parent: tk.Misc, sig: Signature, path: str) -> bool:
    """Save ``sig`` to ``path``. ``.sigx`` prompts for a passphrase (and optional
    Hello). Returns True on success, False if cancelled."""
    if not path.lower().endswith(".sigx"):
        sig.save(path)  # plain JSON
        return True

    dlg = PassphraseDialog(parent, "set", hello_available=hello.available())
    parent.wait_window(dlg)
    if not dlg.result:
        return False
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
            return False
        raw = vault.encrypt(sig.to_json_bytes(), pp, enable_hello=False)
    with open(path, "wb") as fh:
        fh.write(raw)
    return True


def load_signature(parent: tk.Misc, path: str) -> Optional[Signature]:
    """Load a signature, prompting for passphrase/Hello on ``.sigx`` files.

    Returns None if the user cancels. Raises on unreadable/corrupt files."""
    with open(path, "rb") as fh:
        raw = fh.read()

    if vault.classify(raw) != "sigx2":
        return Signature.load(path)  # plain JSON or legacy SIGX1

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
