"""Small shared window-placement helpers.

Tk places new toplevels wherever the window manager decides, which on a
multi-monitor Windows desktop often means an edge or a different screen than the
one the user is looking at. Every dialog in this app routes through here so
prompts appear where the user's attention already is.
"""

from __future__ import annotations

import tkinter as tk


def _measure(win: tk.Misc) -> tuple:
    """Return the window's (width, height), forcing layout if it is not mapped.

    A freshly built Toplevel reports 1x1 until Tk has processed its geometry, so
    the requested size is the only reliable number before the window is shown.
    """
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    if w <= 1 or h <= 1:
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    return w, h


def center_on_screen(win: tk.Misc) -> None:
    """Center ``win`` on the monitor it currently sits on."""
    w, h = _measure(win)
    # winfo_screen* is the screen containing the window, which keeps the dialog
    # on the monitor the app is already on rather than always the primary.
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    # Bias slightly above dead center: it reads better and keeps tall dialogs
    # clear of the taskbar.
    y = max(0, int(y * 0.85))
    win.geometry(f"+{x}+{y}")


def center_on_parent(win: tk.Misc, parent: tk.Misc) -> None:
    """Center ``win`` over ``parent``, falling back to the screen if unusable."""
    try:
        if not parent.winfo_viewable():
            raise tk.TclError("parent not viewable")
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
    except tk.TclError:
        center_on_screen(win)
        return

    if pw <= 1 or ph <= 1:
        center_on_screen(win)
        return

    w, h = _measure(win)
    x = px + (pw - w) // 2
    y = py + (ph - h) // 2

    # Keep the dialog fully on-screen even if the parent hugs an edge.
    x = max(0, min(x, win.winfo_screenwidth() - w))
    y = max(0, min(y, win.winfo_screenheight() - h))
    win.geometry(f"+{x}+{y}")
