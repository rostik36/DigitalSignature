"""Entry point for the Signature Mouse Signer.

Run with:  python run.py   (equivalently:  python -m app)

The real startup sequence lives in :mod:`app.__main__` so that every way of
launching the app -- this script, ``python -m app``, or running ``app/app.py``
directly -- goes through the same path.
"""

import sys

from app.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
