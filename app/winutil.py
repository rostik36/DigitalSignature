"""Windows-specific helpers for DPI awareness and virtual-screen geometry.

Getting the cursor to land where we expect requires that this process use the
same coordinate space as the real mouse. We therefore mark the process
per-monitor DPI aware so tkinter reports physical pixels, which is the same unit
pynput uses when it sets the cursor position. On non-Windows platforms these
functions degrade to sensible no-ops.
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple

# System-metric indices for the bounding box of all monitors combined.
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def set_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware. Must run before any Tk window."""
    if not sys.platform.startswith("win"):
        return
    import ctypes

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def cursor_pos() -> Optional[Tuple[int, int]]:
    """Return the current cursor position in physical screen pixels.

    Used by the capture window to poll the pen position at high frequency,
    independent of (and denser than) tkinter's own motion events. Returns None
    off Windows or if the call fails, so callers can fall back to Tk.
    """
    if not sys.platform.startswith("win"):
        return None
    import ctypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    try:
        pt = _POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
    except Exception:
        pass
    return None


def set_timer_resolution(period_ms: int = 1) -> bool:
    """Raise the system timer resolution so short ``after`` polls fire on time.

    Windows' default timer granularity is ~15.6 ms, which would throttle our
    high-frequency sampler. ``timeBeginPeriod`` lowers it (to 1 ms here). Always
    pair a True result with :func:`clear_timer_resolution`.
    """
    if not sys.platform.startswith("win"):
        return False
    import ctypes

    try:
        return ctypes.windll.winmm.timeBeginPeriod(int(period_ms)) == 0  # TIMERR_NOERROR
    except Exception:
        return False


def clear_timer_resolution(period_ms: int = 1) -> None:
    """Undo a matching :func:`set_timer_resolution` call."""
    if not sys.platform.startswith("win"):
        return
    import ctypes

    try:
        ctypes.windll.winmm.timeEndPeriod(int(period_ms))
    except Exception:
        pass


def virtual_screen() -> Tuple[int, int, int, int]:
    """Return (left, top, width, height) covering every monitor.

    On multi-monitor setups the left/top may be negative (monitors placed to the
    left of or above the primary display). Falls back to a 1080p primary screen
    if the metrics can't be read.
    """
    if not sys.platform.startswith("win"):
        return (0, 0, 1920, 1080)
    import ctypes

    try:
        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return (x, y, w, h)
    except Exception:
        pass
    return (0, 0, 1920, 1080)
