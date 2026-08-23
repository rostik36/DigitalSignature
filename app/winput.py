"""Low-level Windows mouse injection via a single ordered ``SendInput`` stream.

Why this exists: a reliable synthetic *drag* requires the button-down, every
move, and the button-up to enter the system input queue **in order**, as one
coherent stream. ``pynput`` presses the button with ``SendInput`` but moves the
cursor with ``SetCursorPos`` -- two different delivery paths -- so the target can
see the click and the motion out of order and never registers a held drag. Here
every action (down, move, up) is one ``SendInput`` mouse event, so the target
sees ``down -> move -> move -> ... -> up`` with the button held throughout.

Moves use absolute coordinates normalized to the whole virtual desktop, so this
works across multiple monitors and DPI scaling (the process is marked DPI aware
in :mod:`app.winutil`).
"""

from __future__ import annotations

import sys

from .winutil import virtual_screen

_AVAILABLE = sys.platform.startswith("win")

if _AVAILABLE:
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = wintypes.WPARAM  # pointer-sized unsigned integer

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _INPUT_MOUSE = 0
    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _MOUSEEVENTF_ABSOLUTE = 0x8000
    _MOUSEEVENTF_VIRTUALDESK = 0x4000

    _SendInput = ctypes.windll.user32.SendInput
    _SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    _SendInput.restype = wintypes.UINT

    def _send(flags: int, dx: int = 0, dy: int = 0) -> None:
        mi = _MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        inp = _INPUT(_INPUT_MOUSE, _INPUTUNION(mi=mi))
        _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def _to_absolute(x: int, y: int) -> tuple:
        """Map a virtual-desktop pixel to the 0..65535 absolute range."""
        vx, vy, vw, vh = virtual_screen()
        nx = int(round((x - vx) * 65535.0 / max(vw - 1, 1)))
        ny = int(round((y - vy) * 65535.0 / max(vh - 1, 1)))
        nx = min(65535, max(0, nx))
        ny = min(65535, max(0, ny))
        return nx, ny


def available() -> bool:
    return _AVAILABLE


class SendInputBackend:
    """Mouse backend that injects an ordered down/move*/up stream via SendInput."""

    def move(self, x: int, y: int) -> None:
        nx, ny = _to_absolute(int(x), int(y))
        _send(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK, nx, ny)

    def press(self) -> None:
        _send(_MOUSEEVENTF_LEFTDOWN)

    def release(self) -> None:
        _send(_MOUSEEVENTF_LEFTUP)
