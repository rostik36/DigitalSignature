# Signature file format

This document describes exactly what a saved signature file contains: the
on-disk container, the inner data structure, field meanings and units, and what
the encryption does and does not hide.

There are two file types, distinguished by extension and by a leading magic
header:

| Extension | Encrypted | Leading bytes | Purpose |
|-----------|-----------|---------------|---------|
| `*.sigx` | Yes — passphrase + Windows account (+ optional Hello) | `53 49 47 58 32 0A` (`SIGX2\n`) | **Default.** See §2. |
| `*.sigx` (legacy) | Yes — Windows account only | `53 49 47 58 31 0A` (`SIGX1\n`) | Older files; DPAPI only, no passphrase. Still loads. |
| `*.sig.json` / `*.json` | No | `{` (raw JSON) | Interop / human-readable export. |

Both wrap the **same inner JSON payload** — described first below — and the
encrypted form simply protects that JSON.

---

## 1. The inner payload (the actual signature data)

The payload is a single UTF-8 JSON object. This is stored verbatim in a
`*.sig.json` file, and is the plaintext that gets encrypted inside a `*.sigx`
file.

### Top-level object

| Field | Type | Meaning |
|-------|------|---------|
| `format` | integer | Payload schema version. Currently `1`. |
| `source_width` | number | Width (px) of the capture canvas the signature was drawn on. Informational — describes the coordinate space the points live in. Default capture canvas is `900`. |
| `source_height` | number | Height (px) of the capture canvas. Default `360`. |
| `strokes` | array | The signature itself: an ordered list of strokes (see below). |

### `strokes`

`strokes` is an **ordered** array. Each element is one *stroke* — a continuous
pen-down → pen-up gesture. Order matters: strokes are replayed in the order they
were drawn (e.g. the body of a letter, then later the dot on an `i` or the cross
on a `t`).

Each stroke is itself an **ordered** array of *points* (samples taken as the pen
moved). A stroke always has at least 2 points (single-point "click" strokes are
dropped at capture time).

### A point: `[x, y, t, p]`

Every point is a compact 4-element array (arrays, not objects, to keep files
small):

| Index | Name | Type | Units / range | Meaning |
|-------|------|------|---------------|---------|
| 0 | `x` | number | canvas pixels, origin top-left | Horizontal position. |
| 1 | `y` | number | canvas pixels, origin top-left | Vertical position (down is positive). |
| 2 | `t` | number | seconds | Time since the **first** sample of the capture session (not per-stroke). Used to reproduce natural writing speed on replay. |
| 3 | `p` | number | 0.0–1.0 | Pen pressure. `1.0` when the device reports no pressure (the tkinter capture path always records `1.0`; captures from a pressure-bearing device may vary). |

Notes:

- Coordinates are in the **capture canvas' own pixel space**, *not* screen
  pixels. They are mapped onto the real target rectangle only at signing time
  (preserving or distorting aspect ratio per the "stretch to fill" option), so
  one capture can be drawn into any box of any size.
- Coordinates are typically integers (the capture samples at whole pixels) but
  the schema permits floats.
- The payload deliberately stores **only geometry and timing** — no name, no
  image, no machine identifiers, no account info.

### Worked example

```json
{
  "format": 1,
  "source_width": 900,
  "source_height": 360,
  "strokes": [
    [
      [120, 80, 0.0,   1.0],
      [122, 70, 0.016, 1.0],
      [130, 95, 0.032, 1.0]
    ],
    [
      [150, 60, 0.21,  1.0],
      [190, 62, 0.24,  1.0]
    ]
  ]
}
```

This signature has two strokes. The first is three samples over the first 32 ms;
the second stroke starts at t = 0.21 s (a ~178 ms pen-up gap between strokes,
which replay reproduces as a pause).

---

## 2. The encrypted container (`*.sigx`, format SIGX2)

The current default. Byte layout:

```
+----------------+------------------------------+---+------------------------+
| magic (6 bytes)| header: one-line JSON (utf-8)|\n | DPAPI body (binary)    |
| 53 49 47 58 32 | {"v":2,"kdf":...}            |0A | CryptProtectData output|
| 0A  "SIGX2\n"  |                              |   | over the JSON payload   |
+----------------+------------------------------+---+------------------------+
```

- **Magic** — `SIGX2\n`. `load` reads this to pick the path; plain JSON (`{`) and
  legacy `SIGX1\n` are handled separately.
- **Header** — a single line of cleartext JSON. It holds **no secrets**, only
  the public parameters needed to derive the key:

  | Field | Meaning |
  |-------|---------|
  | `v` | Container version (`2`). |
  | `kdf` | Key-derivation function (`"pbkdf2-sha256"`). |
  | `salt` | base64 random 16-byte salt for the KDF. |
  | `iters` | PBKDF2 iteration count (default `200000`). |
  | `hello` | *Optional.* Present only if Windows Hello unlock was enabled (see below). |

  If present, `hello` is `{ "key": <credential name>, "challenge": <base64 32
  bytes>, "sealed": <base64> }`.

- **Body** — Windows `CryptProtectData` output over the UTF-8 JSON payload, using
  extra entropy = `PBKDF2(passphrase, salt, iters)`. Opaque Microsoft structure
  beginning with a version `DWORD` and the well-known DPAPI provider GUID
  `df9d8cd0-1501-…` (`01 00 00 00 d0 8c 9d df 01 …`), including an integrity MAC.

### The layered key — what you need to decrypt

The payload key combines two independent factors, so **both** are required:

1. **Your Windows account** — DPAPI's own key, derived by Windows from your
   logon. Decryptable only as the same user (by default, same machine).
2. **Your passphrase** — stretched with PBKDF2-HMAC-SHA256 (200k iterations) and
   passed as DPAPI secondary entropy. A wrong passphrase → wrong entropy →
   `CryptUnprotectData` fails (that's how a wrong passphrase is detected). The
   iteration count slows brute-force guessing on an unlocked session.

A fixed app constant `b"DigitalSignature/v1/dpapi-entropy"` is also prepended to
the entropy (binds the blob to this app). Changing it breaks all existing files.

This is the key point of the design: even an attacker **already inside your
unlocked Windows session** (remote control, or you walked away) cannot open the
file — DPAPI alone isn't enough; they'd also need the passphrase, which logging
in does not provide.

### Optional Windows Hello unlock (the `hello` header block)

If enabled, the same passphrase-derived file key is **also** sealed under a
secret that only a live Hello gesture can produce, and stored in `hello.sealed`:

- A Hello-protected key (`KeyCredentialManager`) signs the random `challenge`.
  `KeyCredential` uses deterministic RSASSA-PKCS1-v1.5, so the same key+challenge
  always yields the same signature; we SHA-256 it into a stable 32-byte secret.
- `sealed = CryptProtectData(file_key, entropy = hello_secret)`.
- To unlock: a live face/fingerprint/PIN gesture reproduces `hello_secret`,
  which unseals `file_key`, which decrypts the body — **without typing the
  passphrase**.

So a Hello-enabled file opens with the **passphrase OR a live biometric**. The
passphrase always remains as the master/fallback. A remote intruder on your
session has neither your passphrase nor your face/finger. (Implemented in
[`hello.py`](app/hello.py) / [`vault.py`](app/vault.py);
requires Hello to be enrolled and the `winsdk` package.)

### What an attacker can and cannot learn

Without your Windows logon **and** passphrase (or biometric), someone who copies
a `*.sigx` file:

- **Cannot** read the strokes, timing or any geometry — the payload is
  encrypted and tampering is detected by the MAC.
- **Can** still infer, from the extension and `SIGX2` header, that it is a
  signature file from this app, and estimate rough complexity from the **file
  size** (not padded). To hide even that, keep it inside an encrypted volume.
- **Cannot** brute-force the passphrase off your machine at all (DPAPI needs
  your user key); on your unlocked session, PBKDF2's cost slows each guess.

### Legacy SIGX1

Older `*.sigx` files use `SIGX1\n` + a single DPAPI blob with only the fixed app
entropy (no passphrase). They still load. Re-save to upgrade them to SIGX2.

---

## 3. Versioning

- **Payload**: the `format` integer (currently `1`). New fields should bump this.
- **Container**: the `SIGX1` magic (currently version `1`). A new container
  layout would use `SIGX2\n`, and `load` would branch on the header.
