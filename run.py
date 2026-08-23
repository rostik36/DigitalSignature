"""Entry point for the Signature Mouse Signer.

Run with:  python run.py

DPI awareness is set before any Tk window is created so that on-screen
coordinates match the real cursor, which is what makes the replay land in the
right place.
"""

from app.winutil import set_dpi_awareness


def main() -> None:
    set_dpi_awareness()
    # Import after DPI awareness is configured.
    from app.app import App

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
