# Signature Mouse Signer

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
- `pynput` (`pip install -r requirements.txt`). The GUI uses `tkinter`, which is
  bundled with Python on Windows.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

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

Two on-disk formats, chosen by the extension you save with:

- **`*.sigx` — encrypted (recommended, the default).** Protected by **two
  independent factors**: your **Windows account** (DPAPI) **and** a **passphrase**
  you set (PBKDF2-HMAC-SHA256, 200k iterations). Both are required to open it, so
  even someone on your *already-unlocked* Windows session can't read it without
  the passphrase. Optionally, **Windows Hello** (face/fingerprint) can be enabled
  as a convenient unlock — then the file opens with the passphrase **or** a live
  biometric (the passphrase stays as the master/fallback). Copy the file to
  another account/PC and it's unreadable noise; nothing touches the network.
- **`*.sig.json` — plain JSON (not encrypted).** Human-readable; use it only for
  interop (e.g. importing a capture produced by an external tool that writes
  the same `[x, y, t, p]` shape).

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
| `run.py` | Entry point; sets DPI awareness, launches the app |
| `app/model.py` | Signature data, JSON save/load, map-to-box math |
| `app/capture.py` | Canvas window for drawing & recording |
| `app/overlay.py` | Fullscreen drag-select of the target rectangle |
| `app/replay.py` | Engine that performs the mouse drag |
| `app/winput.py` | Windows `SendInput` mouse backend (ordered drag stream) |
| `app/secure.py` | DPAPI primitives + PBKDF2 key derivation |
| `app/vault.py` | `SIGX2` container: passphrase (+ optional Hello) |
| `app/hello.py` | Windows Hello unlock via `KeyCredentialManager` |
| `app/fileio.py` | Passphrase/Hello dialogs + save/load routing |
| `app/app.py` | Control window wiring it all together |
| `app/winutil.py` | DPI awareness + virtual-screen geometry |

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
