"""Main control window: tie capture, storage, box-selection and replay together."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Optional

from pynput import keyboard

from . import fileio
from .capture import CaptureWindow
from .model import Box, Signature
from .overlay import BoxSelector
from .replay import Replayer, ReplayOptions

PREVIEW_W = 420
PREVIEW_H = 150


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Signature Mouse Signer")
        self.resizable(False, False)
        self.configure(bg="#f4f6f9")

        self.signature: Optional[Signature] = None
        self.options = ReplayOptions()
        self.countdown_seconds = 3
        self._last_box: Optional[Box] = None

        # Replay coordination (worker thread -> UI via this queue).
        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._abort = threading.Event()
        self._esc_listener: Optional[keyboard.Listener] = None
        self._busy = False

        self._build_ui()
        self._poll_events()

    # --------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        header = tk.Label(
            self,
            text="Signature Mouse Signer",
            bg="#f4f6f9",
            fg="#1f2d3d",
            font=("Segoe UI", 16, "bold"),
            pady=10,
        )
        header.pack(fill="x")

        sub = tk.Label(
            self,
            text="Capture your signature once, then have the mouse re-draw it inside any\n"
            "box you select on screen — e.g. a form's signature field.",
            bg="#f4f6f9",
            fg="#52606d",
            font=("Segoe UI", 10),
            justify="center",
        )
        sub.pack(fill="x", padx=16)

        self.preview = tk.Canvas(
            self,
            width=PREVIEW_W,
            height=PREVIEW_H,
            bg="white",
            highlightthickness=1,
            highlightbackground="#b8c4d4",
        )
        self.preview.pack(padx=16, pady=12)
        self._render_preview()

        row1 = tk.Frame(self, bg="#f4f6f9")
        row1.pack(fill="x", padx=16)
        self._btn(row1, "Draw signature…", self._draw, accent=True).pack(side="left", expand=True, fill="x", padx=4)
        self._btn(row1, "Load…", self._load).pack(side="left", expand=True, fill="x", padx=4)
        self._btn(row1, "Save…", self._save).pack(side="left", expand=True, fill="x", padx=4)

        row2 = tk.Frame(self, bg="#f4f6f9")
        row2.pack(fill="x", padx=16, pady=(8, 4))
        self.sign_btn = self._btn(row2, "Select box & sign", self._sign_new, accent=True)
        self.sign_btn.pack(side="left", expand=True, fill="x", padx=4)
        self.again_btn = self._btn(row2, "Sign again (last box)", self._sign_again)
        self.again_btn.pack(side="left", expand=True, fill="x", padx=4)

        fit_row = tk.Frame(self, bg="#f4f6f9")
        fit_row.pack(fill="x", padx=20, pady=(2, 0))
        self.stretch_var = tk.BooleanVar(value=self.options.stretch_to_fill)
        tk.Checkbutton(
            fit_row,
            text="Stretch to fill the whole box (distorts aspect ratio)",
            variable=self.stretch_var,
            command=self._on_stretch_toggle,
            bg="#f4f6f9",
            fg="#33475b",
            activebackground="#f4f6f9",
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left")

        row3 = tk.Frame(self, bg="#f4f6f9")
        row3.pack(fill="x", padx=16, pady=(4, 4))
        self._btn(row3, "Settings…", self._open_settings).pack(side="left", padx=4)
        tk.Label(
            row3,
            text="Tip: press Esc any time during signing to stop the mouse.",
            bg="#f4f6f9",
            fg="#7b8794",
            font=("Segoe UI", 9, "italic"),
        ).pack(side="right")

        self.status = tk.Label(
            self,
            text="Ready. Draw or load a signature to begin.",
            bg="#e7ecf3",
            fg="#1f2d3d",
            anchor="w",
            font=("Segoe UI", 9),
            padx=10,
            pady=6,
        )
        self.status.pack(fill="x", side="bottom")

        self._refresh_buttons()

    def _btn(self, parent, text, cmd, accent=False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=("Segoe UI", 10),
            padx=12,
            pady=6,
            bg="#2d6cdf" if accent else "#e7ecf3",
            fg="white" if accent else "#1f2d3d",
            activebackground="#1f57c0" if accent else "#d4dce6",
            disabledforeground="#aab4c0",
            relief="flat",
            cursor="hand2",
        )

    def _set_status(self, text: str) -> None:
        self.status.config(text=text)

    def _on_stretch_toggle(self) -> None:
        self.options.stretch_to_fill = bool(self.stretch_var.get())
        mode = "stretch-to-fill" if self.options.stretch_to_fill else "keep aspect ratio"
        self._set_status(f"Fit mode: {mode}.")

    def _refresh_buttons(self) -> None:
        has_sig = self.signature is not None and not self.signature.is_empty()
        state = "normal" if (has_sig and not self._busy) else "disabled"
        self.sign_btn.config(state=state)
        self.again_btn.config(
            state="normal" if (has_sig and self._last_box is not None and not self._busy) else "disabled"
        )

    # ------------------------------------------------------------ preview
    def _render_preview(self) -> None:
        self.preview.delete("all")
        if self.signature is None or self.signature.is_empty():
            self.preview.create_text(
                PREVIEW_W // 2,
                PREVIEW_H // 2,
                text="(no signature yet)",
                fill="#aab4c0",
                font=("Segoe UI", 11, "italic"),
            )
            return
        strokes = self.signature.map_to_box((0, 0, PREVIEW_W, PREVIEW_H), padding_frac=0.10)
        for stroke in strokes:
            for i in range(1, len(stroke)):
                a, b = stroke[i - 1], stroke[i]
                self.preview.create_line(
                    a.x, a.y, b.x, b.y, fill="#10243e", width=2, capstyle="round", smooth=True
                )

    # ------------------------------------------------------------ actions
    def _draw(self) -> None:
        win = CaptureWindow(self, existing=self.signature)
        self.wait_window(win)
        if win.result is not None:
            self.signature = win.result
            self._render_preview()
            self._set_status(
                f"Captured {len(self.signature.strokes)} stroke(s), "
                f"{self.signature.point_count()} points."
            )
            self._refresh_buttons()

    def _load(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Load signature",
            filetypes=[
                ("Signature files", "*.sigx *.sig.json *.json"),
                ("Encrypted signature", "*.sigx"),
                ("Plain JSON", "*.sig.json *.json"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            sig = fileio.load_signature(self, path)
        except Exception as exc:  # noqa: BLE001 - surface any load error to the user
            messagebox.showerror("Load failed", f"Could not load file:\n{exc}", parent=self)
            return
        if sig is None:
            self._set_status("Load cancelled.")
            return
        self.signature = sig
        self._render_preview()
        self._set_status(f"Loaded {os.path.basename(path)}.")
        self._refresh_buttons()

    def _save(self) -> None:
        if self.signature is None or self.signature.is_empty():
            messagebox.showinfo("Nothing to save", "Draw or load a signature first.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save signature",
            defaultextension=".sigx",
            filetypes=[
                ("Encrypted signature (recommended)", "*.sigx"),
                ("Plain JSON (not encrypted)", "*.sig.json"),
            ],
        )
        if path:
            try:
                saved = fileio.save_signature(self, self.signature, path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Save failed", f"Could not save file:\n{exc}", parent=self)
                return
            if not saved:
                self._set_status("Save cancelled.")
                return
            kind = "encrypted" if path.lower().endswith(".sigx") else "plain (not encrypted)"
            self._set_status(f"Saved {os.path.basename(path)} — {kind}.")

    # --------------------------------------------------------------- signing
    def _sign_new(self) -> None:
        if not self._ensure_signature():
            return
        selector = BoxSelector(self)
        self.wait_window(selector)
        if selector.result is None:
            self._set_status("Box selection cancelled.")
            return
        self._last_box = selector.result
        self._refresh_buttons()
        self._begin_sign(selector.result)

    def _sign_again(self) -> None:
        if not self._ensure_signature() or self._last_box is None:
            return
        self._begin_sign(self._last_box)

    def _ensure_signature(self) -> bool:
        if self.signature is None or self.signature.is_empty():
            messagebox.showinfo("No signature", "Draw or load a signature first.", parent=self)
            return False
        return True

    def _begin_sign(self, box: Box) -> None:
        if self._busy:
            return
        self._busy = True
        self._abort.clear()
        self._refresh_buttons()
        # Get our own window out of the way so mouse events reach the target.
        self.withdraw()
        self._countdown(self.countdown_seconds, box)

    def _countdown(self, remaining: int, box: Box) -> None:
        if remaining <= 0:
            self._start_replay(box)
            return
        self._show_countdown(remaining)
        self.after(1000, lambda: self._countdown(remaining - 1, box))

    def _show_countdown(self, n: int) -> None:
        if getattr(self, "_cd_win", None) is None or not self._cd_win.winfo_exists():
            self._cd_win = tk.Toplevel(self)
            self._cd_win.overrideredirect(True)
            self._cd_win.attributes("-topmost", True)
            try:
                self._cd_win.attributes("-alpha", 0.92)
            except Exception:
                pass
            self._cd_win.configure(bg="#1f2d3d")
            self._cd_lbl = tk.Label(
                self._cd_win, bg="#1f2d3d", fg="white", font=("Segoe UI", 20, "bold"), padx=30, pady=18
            )
            self._cd_lbl.pack()
            self._cd_win.update_idletasks()
            sw = self._cd_win.winfo_screenwidth()
            self._cd_win.geometry(f"+{sw // 2 - 130}+40")
        self._cd_lbl.config(text=f"Signing in {n}…  (Esc to cancel)")

    def _hide_countdown(self) -> None:
        win = getattr(self, "_cd_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        self._cd_win = None

    def _start_replay(self, box: Box) -> None:
        self._hide_countdown()
        self._set_status("Signing…")

        # Global Esc listener so the user can always stop the mouse.
        def on_press(key):
            if key == keyboard.Key.esc:
                self._abort.set()

        self._esc_listener = keyboard.Listener(on_press=on_press)
        self._esc_listener.start()

        replayer = Replayer(self.options)
        replayer.sign_async(
            self.signature,
            box,
            self._abort,
            on_status=lambda s: self._events.put(("status", s)),
            on_done=lambda completed: self._events.put(("done", completed)),
        )

    def _poll_events(self) -> None:
        """Drain worker-thread events on the UI thread."""
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "status":
                    self._set_status(payload)
                elif kind == "done":
                    self._on_replay_done(bool(payload))
        except queue.Empty:
            pass
        self.after(50, self._poll_events)

    def _on_replay_done(self, completed: bool) -> None:
        if self._esc_listener is not None:
            self._esc_listener.stop()
            self._esc_listener = None
        self._busy = False
        self.deiconify()
        self.lift()
        self._refresh_buttons()
        if completed:
            self._set_status("Done — signature drawn. Check the form, then submit.")
        else:
            self._set_status("Stopped before finishing (Esc).")

    # -------------------------------------------------------------- settings
    def _open_settings(self) -> None:
        SettingsDialog(self)


class SettingsDialog(tk.Toplevel):
    """Small dialog to tune replay behavior."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("Settings")
        self.resizable(False, False)
        self.configure(bg="#f4f6f9")
        self.transient(app)
        self.grab_set()

        opt = app.options
        self._use_timing = tk.BooleanVar(value=opt.use_timing)
        self._speed = tk.DoubleVar(value=opt.speed)
        self._padding = tk.DoubleVar(value=opt.padding_frac * 100.0)
        self._countdown = tk.IntVar(value=app.countdown_seconds)
        self._min_step = tk.DoubleVar(value=opt.min_step_px)
        self._step_delay = tk.DoubleVar(value=opt.step_delay * 1000.0)

        self._build()

    def _label(self, grid: tk.Frame, row: int, text: str) -> None:
        tk.Label(
            grid, text=text, bg="#f4f6f9", fg="#33475b", font=("Segoe UI", 10), anchor="w"
        ).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 16))

    def _spin(self, grid, row, label, var, frm, to, inc):
        self._label(grid, row, label)
        tk.Spinbox(
            grid, from_=frm, to=to, increment=inc, textvariable=var, width=8, justify="right"
        ).grid(row=row, column=1, sticky="e", pady=5)

    def _check(self, grid, row, label, var):
        self._label(grid, row, label)
        tk.Checkbutton(grid, variable=var, bg="#f4f6f9", activebackground="#f4f6f9").grid(
            row=row, column=1, sticky="e", pady=5
        )

    def _build(self) -> None:
        tk.Label(
            self, text="Replay settings", bg="#f4f6f9", fg="#1f2d3d", font=("Segoe UI", 13, "bold"), pady=8
        ).pack(fill="x")

        # A two-column grid keeps every label/control pair on its own aligned row.
        grid = tk.Frame(self, bg="#f4f6f9")
        grid.pack(fill="x", padx=18, pady=2)
        grid.columnconfigure(0, weight=1)  # labels take the slack; controls hug right

        self._check(grid, 0, "Reproduce natural speed", self._use_timing)
        self._spin(grid, 1, "Speed multiplier (×)", self._speed, 0.25, 5.0, 0.25)
        self._spin(grid, 2, "Padding inside box (%)", self._padding, 0, 40, 1)
        self._spin(grid, 3, "Countdown (seconds)", self._countdown, 0, 10, 1)
        self._spin(grid, 4, "Smoothness step (px)", self._min_step, 1, 20, 1)
        self._spin(grid, 5, "Fixed step delay (ms)", self._step_delay, 0, 50, 1)

        tk.Label(
            self,
            text="“Fixed step delay” is used only when natural speed is off.",
            bg="#f4f6f9",
            fg="#7b8794",
            font=("Segoe UI", 8, "italic"),
            wraplength=360,
            justify="left",
        ).pack(fill="x", padx=18, pady=(2, 4))

        bar = tk.Frame(self, bg="#f4f6f9")
        bar.pack(fill="x", padx=14, pady=10)
        tk.Button(bar, text="Cancel", command=self.destroy, relief="flat", bg="#e7ecf3", padx=12, pady=4).pack(side="right", padx=4)
        tk.Button(bar, text="Save", command=self._save, relief="flat", bg="#2d6cdf", fg="white", padx=12, pady=4).pack(side="right", padx=4)

    def _save(self) -> None:
        opt = self.app.options
        opt.use_timing = bool(self._use_timing.get())
        opt.speed = max(0.1, float(self._speed.get()))
        opt.padding_frac = max(0.0, min(0.4, float(self._padding.get()) / 100.0))
        opt.min_step_px = max(1.0, float(self._min_step.get()))
        opt.step_delay = max(0.0, float(self._step_delay.get()) / 1000.0)
        self.app.countdown_seconds = max(0, int(self._countdown.get()))
        self.destroy()
