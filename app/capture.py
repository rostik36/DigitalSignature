"""Capture window: draw a signature on a canvas and record it with timing.

Open it as a modal child of the main window::

    win = CaptureWindow(root, existing=current_signature)
    root.wait_window(win)
    if win.result is not None:
        current_signature = win.result

The window records every press/drag/release as strokes of timed points. A pen
behaves exactly like the mouse here, so a stylus works out of the box; the
captured path (not pressure) is what matters for replay.

To keep fast, curvy strokes faithful, the pen position is sampled two ways at
once: tkinter's motion events *and* a high-frequency timer poll that reads the
OS cursor every few milliseconds. The union of both (de-duplicated to whole
pixels) is recorded with no smoothing, so the natural shake and rounded corners
of real handwriting are preserved instead of being collapsed into straight
shortcuts between sparse points.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import List, Optional, Tuple

from . import fileio
from . import theme as th
from .model import Point, Signature
from .ui import center_on_parent
from .winutil import clear_timer_resolution, cursor_pos, set_timer_resolution

CANVAS_WIDTH = 900
CANVAS_HEIGHT = 360
LINE_COLOR = "#10243e"
LINE_WIDTH = 2
GUIDE_COLOR = "#cfd8e3"

# How often the high-frequency poll reads the cursor while drawing (ms).
# ~4 ms aims for ~250 Hz; the real ceiling is the device's report rate.
SAMPLE_INTERVAL_MS = 4


class CaptureWindow(tk.Toplevel):
    """A modal Toplevel that lets the user draw and returns a :class:`Signature`."""

    def __init__(self, master: tk.Misc, existing: Optional[Signature] = None) -> None:
        super().__init__(master)
        self.title("Draw your signature")
        self.resizable(False, False)
        self.configure(bg=th.BG)

        # result stays None unless the user explicitly accepts via "Use this".
        self.result: Optional[Signature] = None

        self._t0 = time.perf_counter()
        self._strokes: List[List[Point]] = []
        self._current: Optional[List[Point]] = None
        self._last_xy: Optional[Tuple[int, int]] = None  # last recorded pixel

        # High-frequency sampling state.
        self._drawing = False
        self._poll_id: Optional[str] = None
        self._timer_raised = False

        self._build_ui()
        self.bind("<Destroy>", self._on_destroy)

        if existing is not None and not existing.is_empty():
            # Clone the incoming strokes so editing here doesn't mutate the original.
            self._strokes = [list(stroke) for stroke in existing.strokes]
            self._redraw()

        # Center over the parent and grab focus (modal behavior).
        self.transient(master if isinstance(master, (tk.Tk, tk.Toplevel)) else None)
        self.grab_set()
        self.update_idletasks()
        self._center_on_parent(master)

    # --------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        hint = tk.Label(
            self,
            text="Sign or write below using your pen or mouse. Multiple strokes are fine.",
            bg=th.BG,
            fg=th.TEXT,
            font=th.BODY,
            pady=8,
        )
        hint.pack(fill="x")

        self.canvas = tk.Canvas(
            self,
            width=CANVAS_WIDTH,
            height=CANVAS_HEIGHT,
            bg=th.SURFACE,
            highlightthickness=1,
            highlightbackground=th.BORDER_STRONG,
            cursor="pencil",
        )
        self.canvas.pack(padx=12, pady=4)
        self._draw_baseline()

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        bar = tk.Frame(self, bg=th.BG)
        bar.pack(fill="x", padx=12, pady=10)

        def add_btn(text, cmd, side="left", kind="secondary"):
            btn = th.Button(bar, text, cmd, kind=kind)
            btn.pack(side=side, padx=(0, th.GAP) if side == "left" else (th.GAP, 0))
            return btn

        add_btn("Clear", self._clear)
        add_btn("Undo stroke", self._undo)
        add_btn("Save to file…", self._save)
        # Right-hand side packs outward, so "Use this" ends up rightmost.
        add_btn("Use this", self._use, side="right", kind="primary")
        add_btn("Cancel", self._cancel, side="right")

        self.bind("<Escape>", lambda _e: self._cancel())

    def _draw_baseline(self) -> None:
        """A faint signature line, for visual guidance only (not recorded)."""
        y = int(CANVAS_HEIGHT * 0.72)
        self.canvas.create_line(
            30, y, CANVAS_WIDTH - 30, y, fill=GUIDE_COLOR, width=1, tags="guide"
        )

    def _center_on_parent(self, master: tk.Misc) -> None:
        try:
            center_on_parent(self, master)
        except Exception:
            pass

    # ----------------------------------------------------------- drawing
    def _now(self) -> float:
        return time.perf_counter() - self._t0

    def _add_sample(self, x: int, y: int) -> None:
        """Record one sample (deduped to whole pixels) and draw the segment.

        Shared by the motion-event handler and the high-frequency poll, so the
        recording is the union of both at up to one point per pixel of travel.
        No smoothing or corner-cutting is applied -- the raw path is kept.
        """
        if self._current is None:
            return
        if self._last_xy is not None and (x, y) == self._last_xy:
            return  # same pixel -> nothing new to record
        self._current.append(Point(x, y, self._now()))
        if self._last_xy is not None:
            lx, ly = self._last_xy
            self.canvas.create_line(
                lx, ly, x, y,
                fill=LINE_COLOR, width=LINE_WIDTH, capstyle="round", tags="ink",
            )
        self._last_xy = (x, y)

    def _canvas_cursor_xy(self) -> Optional[Tuple[int, int]]:
        """Current cursor position in canvas pixels, via the OS (high rate)."""
        pos = cursor_pos()
        if pos is None:
            return None
        return (pos[0] - self.canvas.winfo_rootx(), pos[1] - self.canvas.winfo_rooty())

    def _poll(self) -> None:
        if not self._drawing:
            return
        xy = self._canvas_cursor_xy()
        if xy is not None:
            self._add_sample(int(xy[0]), int(xy[1]))
        self._poll_id = self.after(SAMPLE_INTERVAL_MS, self._poll)

    def _on_press(self, event: tk.Event) -> None:
        self._current = [Point(event.x, event.y, self._now())]
        self._strokes.append(self._current)
        self._last_xy = (event.x, event.y)
        self._drawing = True
        # Sharpen the OS timer so our short poll interval is actually honored.
        if not self._timer_raised:
            self._timer_raised = set_timer_resolution(1)
        if self._poll_id is None:
            self._poll_id = self.after(SAMPLE_INTERVAL_MS, self._poll)

    def _on_motion(self, event: tk.Event) -> None:
        # Motion events catch movement between polls; the poll catches movement
        # between (possibly coalesced) motion events. Together they lose nothing.
        if self._current is not None:
            self._add_sample(event.x, event.y)

    def _on_release(self, _event: tk.Event) -> None:
        self._drawing = False
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if self._timer_raised:
            clear_timer_resolution(1)
            self._timer_raised = False
        # Drop degenerate single-sample strokes (a pure click with no movement).
        if self._current is not None and len(self._current) < 2:
            self._strokes.pop()
        self._current = None
        self._last_xy = None

    def _on_destroy(self, event: tk.Event) -> None:
        # Make sure a half-finished stroke never leaves the timer resolution
        # raised (e.g. window closed via the title bar while still drawing).
        if event.widget is self and self._timer_raised:
            clear_timer_resolution(1)
            self._timer_raised = False

    def _redraw(self) -> None:
        self.canvas.delete("ink")
        for stroke in self._strokes:
            for i in range(1, len(stroke)):
                a = stroke[i - 1]
                b = stroke[i]
                self.canvas.create_line(
                    a.x, a.y, b.x, b.y,
                    fill=LINE_COLOR, width=LINE_WIDTH, capstyle="round", tags="ink",
                )

    # ------------------------------------------------------------ actions
    def _clear(self) -> None:
        self._strokes = []
        self._current = None
        self._last_xy = None
        self.canvas.delete("ink")

    def _undo(self) -> None:
        if self._strokes:
            self._strokes.pop()
            self._redraw()

    def _build_signature(self) -> Signature:
        return Signature(
            strokes=[list(stroke) for stroke in self._strokes if len(stroke) >= 2],
            source_width=CANVAS_WIDTH,
            source_height=CANVAS_HEIGHT,
        )

    def _save(self) -> None:
        sig = self._build_signature()
        if sig.is_empty():
            messagebox.showinfo("Nothing to save", "Draw something first.", parent=self)
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
                saved = fileio.save_signature(self, sig, path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Save failed", f"Could not save file:\n{exc}", parent=self)
                return
            if not saved:
                return
            # The chosen mode may have changed the extension, so report what
            # was actually written rather than what was typed.
            messagebox.showinfo(
                "Saved",
                f"Saved ({fileio.describe(saved['mode'], saved['tied'])}):\n{saved['path']}",
                parent=self,
            )

    def _use(self) -> None:
        sig = self._build_signature()
        if sig.is_empty():
            messagebox.showinfo("Nothing to use", "Draw something first.", parent=self)
            return
        self.result = sig
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()
