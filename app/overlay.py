"""Fullscreen overlay for selecting a target rectangle anywhere on screen.

Usage::

    selector = BoxSelector(root)
    root.wait_window(selector)
    box = selector.result   # (left, top, right, bottom) in screen px, or None

The overlay is a translucent always-on-top window covering the whole virtual
desktop. The user drags out a rectangle (e.g. over a form's signature field);
the returned box is in absolute screen pixels, ready to hand to the replay
engine. Esc or right-click cancels.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional, Tuple

from .winutil import virtual_screen

Box = Tuple[int, int, int, int]


class BoxSelector(tk.Toplevel):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.result: Optional[Box] = None

        self._vx, self._vy, self._vw, self._vh = virtual_screen()

        # Borderless, translucent, covering the full virtual desktop.
        self.overrideredirect(True)
        self.geometry(f"{self._vw}x{self._vh}+{self._vx}+{self._vy}")
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.30)
        except Exception:
            pass
        self.configure(bg="#1b3a5b", cursor="crosshair")

        self.canvas = tk.Canvas(
            self,
            width=self._vw,
            height=self._vh,
            bg="#1b3a5b",
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._hint = self.canvas.create_text(
            self._vw // 2,
            40,
            text="Drag a box over the signature field.   Esc / right-click to cancel.",
            fill="white",
            font=("Segoe UI", 14, "bold"),
        )

        self._start: Optional[Tuple[int, int]] = None  # canvas coords
        self._rect = None
        self._dim_label = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.canvas.bind("<ButtonPress-3>", lambda _e: self._cancel())

        self.grab_set()
        self.focus_force()

    # ------------------------------------------------------------- events
    def _on_press(self, event: tk.Event) -> None:
        self._start = (event.x, event.y)
        if self._rect is not None:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ffd34d", width=2, fill="#ffe9a8"
        )
        # Make the selection fill stand out from the dim background a little.
        self.canvas.itemconfig(self._rect, stipple="gray25")

    def _on_motion(self, event: tk.Event) -> None:
        if self._start is None or self._rect is None:
            return
        x0, y0 = self._start
        self.canvas.coords(self._rect, x0, y0, event.x, event.y)
        self._update_dim_label(x0, y0, event.x, event.y)

    def _on_release(self, event: tk.Event) -> None:
        if self._start is None:
            return
        x0, y0 = self._start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))

        # Ignore accidental tiny selections.
        if (right - left) < 8 or (bottom - top) < 8:
            self._cancel()
            return

        # Translate canvas coords into absolute screen coords.
        self.result = (
            left + self._vx,
            top + self._vy,
            right + self._vx,
            bottom + self._vy,
        )
        self.grab_release()
        self.destroy()

    def _update_dim_label(self, x0: int, y0: int, x1: int, y1: int) -> None:
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        text = f"{w} x {h} px"
        cx = (x0 + x1) // 2
        cy = min(y0, y1) - 14
        if self._dim_label is None:
            self._dim_label = self.canvas.create_text(
                cx, cy, text=text, fill="white", font=("Segoe UI", 11, "bold")
            )
        else:
            self.canvas.coords(self._dim_label, cx, cy)
            self.canvas.itemconfig(self._dim_label, text=text)

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()
