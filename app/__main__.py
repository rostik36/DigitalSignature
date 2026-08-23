"""Package entry point:  python -m app

DPI awareness is set before any Tk window is created so that on-screen
coordinates match the real cursor, which is what makes the replay land in the
right place.

Only one copy may run at a time: two instances would both install global Esc
hotkeys and both could drive the mouse, so a replay from one would fight the
other. See :mod:`app.single_instance`.
"""

import sys

from app.single_instance import AlreadyRunning, SingleInstance
from app.winutil import set_dpi_awareness


def _complain(message: str) -> None:
    """Report a startup failure through a dialog, falling back to stderr."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("Signature Mouse Signer", message)
        root.destroy()
    except Exception:  # noqa: BLE001 - headless or no display; stderr will do
        print(message, file=sys.stderr)


def main() -> int:
    set_dpi_awareness()

    lock = SingleInstance()
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        _complain(
            f"{exc}\n\nLook for the existing window — it may be behind another "
            "app or on a different monitor."
        )
        return 1

    try:
        # Import after DPI awareness is configured.
        from app.app import App

        App().mainloop()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
