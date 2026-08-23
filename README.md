# Digital Signature

Capture your handwritten signature on a canvas (with a pen or the mouse), store
it to a file, and have the application **re-draw it by moving the real mouse
cursor** inside any rectangle you select on screen.

It exists to solve one specific annoyance: some digital forms have a signature
field that only records a **single dot** when you try to sign by hand — the page
sees a click but not the drag in between. This tool injects a genuine
operating-system mouse drag (press → many moves → release), which those fields
*do* register, so your signature comes out as a real stroke.

## Why this approach

- A **browser extension can't help** — page scripts can't move the OS cursor or
  fix how another site handles pointer input. Driving the real mouse from a
  small desktop app is what actually works, and it works in *any* app, not just
  one website.
- Synthetic mouse input via `pynput` (`SendInput` on Windows) is delivered as
  real OS input, so the form can't tell it apart from a hand-drawn drag.

## Requirements

- Windows (built and tested on Windows 11)
- Python 3.9+
- `pynput` and `cryptography` (`pip install -r requirements.txt`). The GUI uses
  `tkinter`, which is bundled with Python on Windows.

Only one copy runs at a time. A second launch shows a notice and exits, because
two instances would both install global Esc hotkeys and both could drive the
mouse — a replay from one would fight the other.

## Run

The launcher scripts create a `.venv`, install `requirements.txt` into it, and
start the app. They can be run from any working directory, and re-runs reuse the
existing environment (dependencies are reinstalled only when
`requirements.txt` changes).

```powershell
.\run.ps1              # Windows / PowerShell
```

```bash
./run.sh               # Linux, macOS, Git Bash / WSL
```

Both accept `--recreate` (rebuild the venv from scratch) and `--skip-install`
(launch without touching dependencies); in PowerShell these are `-Recreate` and
`-SkipInstall`.

To manage the environment yourself instead:

```powershell
pip install -r requirements.txt
python run.py           # equivalently: python -m app
```

## Tests

```powershell
pip install -r requirements-dev.txt
pytest
```

[`tests/test_vault.py`](tests/test_vault.py) covers the passphrase save/load
round trip, wrong-passphrase rejection, non-ASCII passphrases, per-save salt
freshness, header tamper detection, the no-passphrase mode, legacy `SIGX2`
files, and both halves of the portability contract — that a default save opens
on another machine, and that ticking "tie to this computer" stops it from
doing so. The DPAPI-backed tests are skipped automatically off Windows.

## How to use

1. **Draw signature…** — write your signature in the white canvas with your pen
   or mouse. Multiple strokes are fine. Click **Use this**. (Optionally **Save…**
   it to a `.sig.json` file so you can **Load…** it next time.)
2. **Select box & sign** — the screen dims; **drag a rectangle over the form's
   signature field**. A short countdown appears, then the mouse draws your
   signature inside that box. The signature is scaled to fit, keeping its aspect
   ratio.
3. If you need to redo it, **Sign again (last box)** reuses the same rectangle.

Press **Esc** at any moment during signing to stop the mouse immediately.

### Fit modes

- **Keep aspect ratio** (default, checkbox off) — the signature is scaled by a
  single ratio so its proportions are preserved, then centered in the box. It
  fills whichever axis runs out first and leaves a margin on the other.
- **Stretch to fill the whole box** (checkbox on) — width and height are scaled
  *independently* so the signature fills the entire box, distorting its
  proportions to match the box's shape (e.g. a wide, short field makes a wide,
  short signature).

## Settings

- **Reproduce natural speed** — replays using the actual rhythm you wrote with.
  Turn off for a steady, fixed-speed draw.
- **Speed multiplier** — faster/slower when natural speed is on.
- **Padding inside box** — margin kept between the signature and the box edge.
- **Countdown** — seconds before the mouse starts moving.
- **Smoothness step / Fixed step delay** — how densely move events are emitted
  and how fast, which affects whether stubborn fields register the stroke.

## File format & encryption

A signature is a list of strokes, each a list of `[x, y, t, p]` samples (canvas
pixels, seconds since start, pressure). Coordinates are only mapped to the real
screen at signing time, so one capture works in any box of any size.

When you save, the app asks how the signature should be protected. Your choice
sets the real file extension, so you don't have to know the formats in advance:

| Choice | What you get | Opens on another PC? |
|--------|--------------|----------------------|
| **Passphrase** *(default)* | `.sigx` — AES-256-GCM keyed from your passphrase | **Yes**, with the passphrase |
| **Passphrase** + *tie to this computer* | `.sigx` — the above, also wrapped with your Windows account | No |
| **No passphrase** | `.sigx` — encrypted, opens with no prompt | No |
| **No protection** | `.sig.json` — plain, readable JSON | Yes, to anyone |

**Files are portable by default.** The passphrase alone is the key, so a `.sigx`
you save today opens on any computer that runs this app and knows the
passphrase. Nothing about your PC is mixed in.

Tick **"Also tie this file to this computer"** if you'd rather it *couldn't*
leave: that adds your Windows account (DPAPI) as a second required factor, so
the file will not open on another PC or another user account even with the right
passphrase. It's a deliberate trade — stronger at rest, useless on a second
machine.

"No passphrase" leaves the Windows account as the only lock, so it's always
tied to this computer. "No protection" stores your signature in the clear:
anyone with that file can replay your real signature.

The two on-disk formats in detail:

- **`*.sigx` — encrypted (recommended, the default).** The payload is encrypted
  with **AES-256-GCM** under a key stretched from your passphrase with
  **PBKDF2-HMAC-SHA256 (200k iterations)**. GCM authenticates the file, so a
  wrong passphrase and a tampered file are both caught rather than producing
  garbage. Nothing about your computer is part of the key, so the file travels.

  Two optional extras, both off unless you ask for them:

  - **Tie to this computer** — additionally wraps the ciphertext with Windows
    **DPAPI**, adding your Windows account as a second required factor. Now even
    someone on your *already-unlocked* session needs the passphrase, and the file
    is unreadable noise on any other PC or account.
  - **Windows Hello** (face/fingerprint) as a convenient unlock, so the file
    opens with the passphrase **or** a live biometric. Hello keys live on one
    machine, so this implies "tie to this computer".

  Nothing touches the network in any mode.
- **`*.sig.json` — plain JSON (not encrypted).** Human-readable. Written when you
  pick "No protection", and useful for moving a signature to another PC or for
  interop with anything that writes the same `[x, y, t, p]` shape. **Treat it as
  sensitive:** it is your signature in the clear.

See **[FORMAT.md](FORMAT.md)** for the full byte layout, the JSON schema, field
units, and exactly what the encryption does and does not hide.

**Windows Hello** support needs the `winsdk` package and a Hello credential
**enrolled** on your account (Settings → Accounts → Sign-in options). If either
is missing, the app silently falls back to passphrase-only — nothing breaks. A
**Google/OAuth token is not suitable** as an encryption key — it rotates and
expires, which would make old files undecryptable.

## Project layout

| File | Role |
|------|------|
| `run.py` | Thin wrapper; real startup lives in `app/__main__.py` |
| `app/__main__.py` | Entry point: DPI awareness, single-instance lock, launch |
| `app/single_instance.py` | Named-mutex lock so only one copy runs |
| `app/model.py` | Signature data, JSON save/load, map-to-box math |
| `app/capture.py` | Canvas window for drawing & recording |
| `app/overlay.py` | Fullscreen drag-select of the target rectangle |
| `app/replay.py` | Engine that performs the mouse drag |
| `app/winput.py` | Windows `SendInput` mouse backend (ordered drag stream) |
| `app/secure.py` | AES-256-GCM + PBKDF2, and the DPAPI primitives |
| `app/vault.py` | `SIGX3` container: portable by default, optional PC binding |
| `app/hello.py` | Windows Hello unlock via `KeyCredentialManager` |
| `app/fileio.py` | Save-protection dialog, unlock prompts, save/load routing |
| `app/app.py` | Control window wiring it all together |
| `app/winutil.py` | DPI awareness + virtual-screen geometry |
| `app/ui.py` | Centering helpers so dialogs open in the middle of the screen |

## Troubleshooting

- **The form only drew the *start* of each stroke (a fragment/dash).** The cursor
  is moving faster than the target samples it: Windows coalesces rapid cursor
  moves, so the form receives a press, one move, then the release. Raise
  **Settings → Fixed step delay** (e.g. 16–20 ms) and keep **Reproduce natural
  speed** off. The defaults (steady speed, 12 ms/step, 8 ms floor) already avoid
  this for most fields; stubborn pads may need a larger delay.
- **Captured strokes look straight / cut corners when you draw fast.** That's
  capture density — lower `SAMPLE_INTERVAL_MS` in `capture.py` toward 2.

## Notes & limits

- Designed for Windows. The replay path is cross-platform via `pynput`, but DPI
  handling and the screen-geometry helper are Windows-specific.
- The mouse genuinely moves during replay — don't touch it until it finishes (or
  press Esc to abort).
- Use it to sign with **your own** signature on forms you're authorized to sign.
