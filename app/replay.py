"""Replay engine: redraw a captured signature with the real OS mouse.

This is what solves the "form only records a single dot" problem. Instead of a
click, we synthesize a genuine drag at the operating-system level:

    move to the first point  ->  press the left button  ->  emit many small,
    interpolated move events  ->  release the button

Because these are real OS input events (pynput uses ``SendInput`` on Windows),
the browser / form sees an authentic mouse drag and records the full stroke.

The work runs on a background thread so the UI stays responsive and an Esc key
listener can abort it. All mouse state is released in a ``finally`` block so an
abort never leaves the button stuck down.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from .model import Box, Point, Signature
from . import winput

StatusCb = Optional[Callable[[str], None]]


class _PynputBackend:
    """Fallback backend (non-Windows). Note: SetCursorPos-based moves make for a
    weaker drag than the Windows :class:`~app.winput.SendInputBackend`;
    used only where SendInput is unavailable."""

    def __init__(self) -> None:
        from pynput.mouse import Button, Controller

        self._button = Button.left
        self._mouse = Controller()

    def move(self, x: int, y: int) -> None:
        self._mouse.position = (int(x), int(y))

    def press(self) -> None:
        self._mouse.press(self._button)

    def release(self) -> None:
        self._mouse.release(self._button)


def _make_backend():
    if winput.available():
        return winput.SendInputBackend()
    return _PynputBackend()


@dataclass
class ReplayOptions:
    """Tunables for how the signature is drawn."""

    padding_frac: float = 0.08      # margin kept inside the target box
    stretch_to_fill: bool = False   # fill the box, distorting aspect ratio
    speed: float = 1.0              # >1 faster, <1 slower (only when use_timing)
    use_timing: bool = False        # reproduce the natural rhythm (slower, can coalesce)
    min_step_px: float = 6.0        # max gap between emitted move events
    step_delay: float = 0.012       # fixed per-step delay when use_timing is False
    # Absolute floor on the time the cursor dwells at each emitted point. This
    # is the key anti-coalescing knob: Windows only synthesizes a move message
    # for the target when its message loop next runs, so each point must persist
    # long enough (a few ms) to be sampled. Too small -> the form receives only
    # the start of each stroke and draws a fragment, not the whole line.
    floor_step_delay: float = 0.008
    min_step_delay: float = 0.008   # clamp for per-step delay when use_timing
    max_step_delay: float = 0.05    # clamp for per-step delay when use_timing
    settle_before_press: float = 0.10   # pause after moving onto the start point
    settle_after_press: float = 0.08    # pause after pressing, before moving
    settle_after_release: float = 0.06  # pause after releasing a stroke
    stroke_pause: float = 0.14      # pause between separate strokes


class Replayer:
    """Drives the mouse to draw a signature inside a screen box."""

    def __init__(self, options: Optional[ReplayOptions] = None) -> None:
        self.options = options or ReplayOptions()
        self._backend = _make_backend()

    # -------------------------------------------------------------- threaded
    def sign_async(
        self,
        signature: Signature,
        box: Box,
        abort: threading.Event,
        on_status: StatusCb = None,
        on_done: Optional[Callable[[bool], None]] = None,
    ) -> threading.Thread:
        """Run :meth:`sign` on a daemon thread; returns the thread.

        ``on_done(completed)`` is called from the worker thread with ``True`` if
        the whole signature was drawn, ``False`` if aborted.
        """

        def worker() -> None:
            completed = self.sign(signature, box, abort, on_status)
            if on_done is not None:
                on_done(completed)

        thread = threading.Thread(target=worker, name="signature-replay", daemon=True)
        thread.start()
        return thread

    # ---------------------------------------------------------------- core
    def sign(
        self,
        signature: Signature,
        box: Box,
        abort: threading.Event,
        on_status: StatusCb = None,
    ) -> bool:
        """Draw ``signature`` inside ``box``. Returns False if aborted."""
        strokes = signature.map_to_box(
            box, self.options.padding_frac, self.options.stretch_to_fill
        )
        if not strokes:
            return True

        pressed = False
        try:
            for idx, stroke in enumerate(strokes):
                if abort.is_set():
                    return False
                if len(stroke) < 2:
                    continue

                if on_status:
                    on_status(f"Signing… stroke {idx + 1}/{len(strokes)}")

                # Move onto the first sample before pressing.
                self._move_to(stroke[0])
                self._sleep(self.options.settle_before_press, abort)

                self._backend.press()
                pressed = True
                self._sleep(self.options.settle_after_press, abort)

                # Walk the stroke, interpolating so move events stay dense.
                for i in range(1, len(stroke)):
                    if abort.is_set():
                        return False
                    self._draw_segment(stroke[i - 1], stroke[i], abort)

                self._backend.release()
                pressed = False
                self._sleep(self.options.settle_after_release, abort)
                self._sleep(self.options.stroke_pause, abort)

            return not abort.is_set()
        finally:
            # Never leave the button stuck down, whatever happens.
            if pressed:
                try:
                    self._backend.release()
                except Exception:
                    pass

    # ------------------------------------------------------------- helpers
    def _move_to(self, pt: Point) -> None:
        self._backend.move(int(round(pt.x)), int(round(pt.y)))

    def _draw_segment(self, a: Point, b: Point, abort: threading.Event) -> None:
        """Emit interpolated move events from a to b with appropriate pacing."""
        dist = math.hypot(b.x - a.x, b.y - a.y)
        steps = max(1, int(math.ceil(dist / self.options.min_step_px)))

        if self.options.use_timing:
            segment_time = max(0.0, (b.t - a.t)) / max(self.options.speed, 1e-6)
            per_step = segment_time / steps if steps else 0.0
            per_step = min(
                self.options.max_step_delay,
                max(self.options.min_step_delay, per_step),
            )
        else:
            per_step = self.options.step_delay

        # Never dwell less than the floor, or the target's message loop won't
        # sample every move and the stroke arrives truncated.
        per_step = max(per_step, self.options.floor_step_delay)

        for s in range(1, steps + 1):
            if abort.is_set():
                return
            frac = s / steps
            x = a.x + (b.x - a.x) * frac
            y = a.y + (b.y - a.y) * frac
            self._backend.move(int(round(x)), int(round(y)))
            time.sleep(per_step)

    @staticmethod
    def _sleep(seconds: float, abort: threading.Event) -> None:
        """Sleep, but wake early and often so aborts feel responsive."""
        if seconds <= 0:
            return
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            if abort.is_set():
                return
            time.sleep(min(0.01, max(0.0, end - time.perf_counter())))
