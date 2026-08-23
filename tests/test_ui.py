"""Window placement helpers.

Dialogs that open against a screen edge are easy to miss, especially the signing
countdown, so placement is worth pinning down.
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from app.ui import center_on_parent, center_on_screen  # noqa: E402


@pytest.fixture(scope="module")
def tk_root():
    """One Tk root for the whole module.

    Creating and tearing down a root per test is flaky on Windows -- Tk
    intermittently refuses the next Tk() -- so the root is shared and each test
    gets a clean slate via the ``root`` fixture below.
    """
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    yield r
    r.destroy()


@pytest.fixture
def root(tk_root):
    tk_root.deiconify()
    tk_root.geometry("300x200+0+0")
    tk_root.update()
    yield tk_root
    for child in tk_root.winfo_children():
        child.destroy()
    tk_root.update()


def _geometry(win) -> tuple:
    win.update_idletasks()
    win.update()
    return win.winfo_x(), win.winfo_y(), win.winfo_width(), win.winfo_height()


def test_center_on_screen_is_horizontally_centered(root):
    win = tk.Toplevel(root)
    tk.Label(win, text="x" * 40).pack()
    center_on_screen(win)

    x, _y, w, _h = _geometry(win)
    screen_w = win.winfo_screenwidth()

    assert abs((x + w // 2) - screen_w // 2) <= 30


def test_center_on_screen_stays_on_screen(root):
    win = tk.Toplevel(root)
    tk.Label(win, text="small").pack()
    center_on_screen(win)

    x, y, w, h = _geometry(win)

    assert x >= 0 and y >= 0
    assert x + w <= win.winfo_screenwidth()
    assert y + h <= win.winfo_screenheight()


def test_center_on_screen_is_not_at_the_top_edge(root):
    """The countdown used to be pinned near y=40; it belongs mid-screen."""
    win = tk.Toplevel(root)
    tk.Label(win, text="Signing in 3…").pack()
    center_on_screen(win)

    _x, y, _w, _h = _geometry(win)

    assert y > win.winfo_screenheight() * 0.2


def test_center_on_parent_centers_over_the_parent(root):
    root.geometry("600x400+200+150")
    root.update()

    win = tk.Toplevel(root)
    tk.Label(win, text="child").pack()
    center_on_parent(win, root)

    x, y, w, h = _geometry(win)
    parent_cx = root.winfo_rootx() + root.winfo_width() // 2
    parent_cy = root.winfo_rooty() + root.winfo_height() // 2

    assert abs((x + w // 2) - parent_cx) <= 30
    assert abs((y + h // 2) - parent_cy) <= 30


def test_center_on_parent_keeps_dialog_on_screen(root):
    """A parent hugging the corner must not push the dialog off-screen."""
    root.geometry("600x400+0+0")
    root.update()

    win = tk.Toplevel(root)
    tk.Label(win, text="x" * 80).pack()
    center_on_parent(win, root)

    x, y, _w, _h = _geometry(win)

    assert x >= 0 and y >= 0


def test_center_on_parent_falls_back_when_parent_is_hidden(root):
    """Withdrawn parents have no usable geometry; fall back to the screen."""
    root.withdraw()
    root.update()

    win = tk.Toplevel(root)
    tk.Label(win, text="orphan").pack()
    center_on_parent(win, root)  # must not raise

    win.update_idletasks()
    assert win.winfo_reqwidth() > 0
