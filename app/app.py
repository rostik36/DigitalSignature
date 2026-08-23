"""Main control window: tie capture, storage, box-selection and replay together."""

from __future__ import annotations

if __name__ == "__main__" and __package__ in (None, ""):
    # Run as a plain script (e.g. the IDE's run button). The relative imports
    # below need a parent package, and DPI awareness has to be configured
    # before Tk loads, so hand off to the real entry point instead.
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from app.__main__ import main

    raise SystemExit(main())

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
from . import theme as th
from .ui import center_on_parent, center_on_screen

PREVIEW_W = 420
PREVIEW_H = 150


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Digital Signature")
        self.resizable(False, False)
        self.configure(bg=th.BG)

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
        # Center the main window too: tkinter places message boxes over their
        # parent, so a centered parent keeps every prompt near the middle.
        center_on_screen(self)
        self._poll_events()

    # --------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        """Lay the window out as the two steps the task actually has:
        get a signature, then place it on a form."""
        self.configure(bg=th.BG)

        self._build_header()
        self._build_status_bar()   # packed to the bottom before the body fills
        self._build_footer()

        body = tk.Frame(self, bg=th.BG)
        body.pack(fill="both", expand=True, padx=th.GUTTER)

        self._build_signature_section(body)
        self._build_sign_section(body)

        self._refresh_buttons()

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=th.BG)
        head.pack(fill="x", padx=th.GUTTER, pady=(16, 10))

        tk.Label(head, text="Digital Signature", bg=th.BG, fg=th.TEXT,
                 font=th.TITLE, anchor="w").pack(fill="x")
        tk.Label(head,
                 text="Capture your signature once, then have the mouse re-draw it "
                      "inside any box you pick on screen.",
                 bg=th.BG, fg=th.TEXT_MUTED, font=th.BODY_SMALL,
                 anchor="w", justify="left", wraplength=PREVIEW_W + 40
                 ).pack(fill="x", pady=(3, 0))

    def _build_signature_section(self, parent: tk.Frame) -> None:
        th.section_label(parent, "1 · Your signature").pack(fill="x", pady=(2, 6))

        card = th.card(parent)
        card.pack(fill="x")

        self.preview = tk.Canvas(card, width=PREVIEW_W, height=PREVIEW_H,
                                 bg=th.SURFACE, highlightthickness=0, bd=0)
        self.preview.pack(padx=1, pady=(1, 0))
        self._render_preview()

        th.separator(card).pack(fill="x")

        # Actions live inside the card, directly under what they act on.
        tools = tk.Frame(card, bg=th.SURFACE)
        tools.pack(fill="x", padx=10, pady=9)

        self.draw_btn = th.Button(tools, "Draw signature…", self._draw, padx=12)
        self.draw_btn.pack(side="left")
        th.Button(tools, "Load…", self._load, padx=12).pack(side="left", padx=(th.GAP, 0))
        self.save_btn = th.Button(tools, "Save…", self._save, padx=12)
        self.save_btn.pack(side="left", padx=(th.GAP, 0))

        self.sig_info = tk.Label(tools, text="", bg=th.SURFACE, fg=th.TEXT_FAINT,
                                 font=th.HINT, anchor="e")
        self.sig_info.pack(side="right")

    def _build_sign_section(self, parent: tk.Frame) -> None:
        th.section_label(parent, "2 · Sign a form").pack(fill="x", pady=(th.GAP_LG, 6))

        # The primary action is the only filled button in the window, so where
        # to click next is never ambiguous.
        self.sign_btn = th.Button(parent, "Select box & sign", self._sign_new,
                                  kind="primary", pady=10)
        self.sign_btn.pack(fill="x")

        self.again_btn = th.Button(parent, "Sign again in the last box", self._sign_again)
        self.again_btn.pack(fill="x", pady=(th.GAP, 0))

        self.stretch_var = tk.BooleanVar(value=self.options.stretch_to_fill)
        tk.Checkbutton(
            parent, text="Stretch to fill the whole box (distorts aspect ratio)",
            variable=self.stretch_var, command=self._on_stretch_toggle,
            bg=th.BG, fg=th.TEXT_MUTED, activebackground=th.BG,
            activeforeground=th.TEXT, selectcolor=th.SURFACE,
            font=th.BODY_SMALL, anchor="w", padx=0, highlightthickness=0,
        ).pack(fill="x", pady=(th.GAP, 0))

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=th.BG)
        footer.pack(fill="x", side="bottom", padx=th.GUTTER, pady=(12, 14))

        th.Button(footer, "Settings…", self._open_settings, kind="ghost",
                  padx=10, pady=5).pack(side="left")
        tk.Label(footer, text="Press Esc while signing to stop the mouse.",
                 bg=th.BG, fg=th.TEXT_FAINT, font=th.HINT_ITALIC
                 ).pack(side="right", pady=4)

    def _build_status_bar(self) -> None:
        self.status = tk.Label(
            self, text="Ready — draw or load a signature to begin.",
            bg="#e2e8f1", fg=th.TEXT_MUTED, anchor="w",
            font=th.BODY_SMALL, padx=th.GUTTER, pady=7,
        )
        self.status.pack(fill="x", side="bottom")

    def _set_status(self, text: str) -> None:
        self.status.config(text=text)

    def _on_stretch_toggle(self) -> None:
        self.options.stretch_to_fill = bool(self.stretch_var.get())
        mode = "stretch-to-fill" if self.options.stretch_to_fill else "keep aspect ratio"
        self._set_status(f"Fit mode: {mode}.")

    def _refresh_buttons(self) -> None:
        has_sig = self.signature is not None and not self.signature.is_empty()
        self.sign_btn.set_state(has_sig and not self._busy)
        self.again_btn.set_state(has_sig and self._last_box is not None and not self._busy)
        self.save_btn.set_state(has_sig and not self._busy)
        self.draw_btn.set_state(not self._busy)

        # Summarise the loaded signature next to the buttons that act on it.
        if has_sig:
            pts = sum(len(s) for s in self.signature.strokes)
            n = len(self.signature.strokes)
            self.sig_info.config(text=f"{n} stroke{'s' if n != 1 else ''} · {pts} points")
        else:
            self.sig_info.config(text="")

    # ------------------------------------------------------------ preview
    def _render_preview(self) -> None:
        self.preview.delete("all")
        if self.signature is None or self.signature.is_empty():
            # Dashed placeholder reads as "a signature goes here" rather than
            # as an empty white box the user might mistake for a bug.
            self.preview.create_rectangle(
                14, 14, PREVIEW_W - 14, PREVIEW_H - 14,
                outline=th.BORDER_STRONG, dash=(4, 4),
            )
            self.preview.create_text(
                PREVIEW_W // 2, PREVIEW_H // 2,
                text="No signature yet — click “Draw signature…”",
                fill=th.TEXT_FAINT, font=(th.FAMILY, 10),
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
            # The chosen mode may have changed the extension, so report what
            # was actually written rather than what was typed.
            self._set_status(
                f"Saved {os.path.basename(saved['path'])} — "
                f"{fileio.describe(saved['mode'], saved['tied'])}."
            )

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
        # Re-center on every tick: the text width changes with the digit, and
        # the countdown belongs in the middle of the screen where the user is
        # looking, not tucked against the top edge.
        self._cd_lbl.config(text=f"Signing in {n}…  (Esc to cancel)")
        center_on_screen(self._cd_win)

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
        self.configure(bg=th.BG)
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
        center_on_parent(self, app)

    def _label(self, grid: tk.Frame, row: int, text: str) -> None:
        tk.Label(
            grid, text=text, bg=th.BG, fg=th.TEXT, font=th.BODY, anchor="w"
        ).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 16))

    def _spin(self, grid, row, label, var, frm, to, inc):
        self._label(grid, row, label)
        tk.Spinbox(
            grid, from_=frm, to=to, increment=inc, textvariable=var, width=8,
            justify="right", font=th.BODY_SMALL, relief="flat",
            bg=th.SURFACE, highlightthickness=1, highlightbackground=th.BORDER,
        ).grid(row=row, column=1, sticky="e", pady=6)

    def _check(self, grid, row, label, var):
        self._label(grid, row, label)
        tk.Checkbutton(grid, variable=var, bg=th.BG, activebackground=th.BG,
                       selectcolor=th.SURFACE, highlightthickness=0).grid(
            row=row, column=1, sticky="e", pady=6
        )

    def _build(self) -> None:
        tk.Label(self, text="Replay settings", bg=th.BG, fg=th.TEXT,
                 font=(th.FAMILY, 13, "bold"), anchor="w"
                 ).pack(fill="x", padx=th.GUTTER, pady=(14, 8))

        # A two-column grid keeps every label/control pair on its own aligned row.
        grid = tk.Frame(self, bg=th.BG)
        grid.pack(fill="x", padx=th.GUTTER, pady=2)
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
            bg=th.BG,
            fg=th.TEXT_FAINT,
            font=th.HINT_ITALIC,
            wraplength=360,
            justify="left",
        ).pack(fill="x", padx=th.GUTTER, pady=(6, 2))

        bar = tk.Frame(self, bg=th.BG)
        bar.pack(fill="x", padx=th.GUTTER, pady=(10, 14))
        th.Button(bar, "Save", self._save, kind="primary").pack(side="right")
        th.Button(bar, "Cancel", self.destroy).pack(side="right", padx=(0, th.GAP))

    def _save(self) -> None:
        opt = self.app.options
        opt.use_timing = bool(self._use_timing.get())
        opt.speed = max(0.1, float(self._speed.get()))
        opt.padding_frac = max(0.0, min(0.4, float(self._padding.get()) / 100.0))
        opt.min_step_px = max(1.0, float(self._min_step.get()))
        opt.step_delay = max(0.0, float(self._step_delay.get()) / 1000.0)
        self.app.countdown_seconds = max(0, int(self._countdown.get()))
        self.destroy()
