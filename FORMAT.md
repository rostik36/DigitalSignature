# Signature file format

This document describes exactly what a saved signature file contains: the
on-disk container, the inner data structure, field meanings and units, and what
the encryption does and does not hide.

There are two file types, distinguished by extension and by a leading magic
header:

| Extension | Encrypted | Leading bytes | Purpose |
|-----------|-----------|---------------|---------|
| `*.sigx` | Yes — AES-256-GCM from the passphrase (+ optional machine binding / Hello) | `53 49 47 58 33 0A` (`SIGX3\n`) | **Default.** Portable unless tied to a PC. See §2. |
| `*.sigx` (legacy) | Yes — passphrase + Windows account | `53 49 47 58 32 0A` (`SIGX2\n`) | Older files; DPAPI-wrapped, always machine-bound. Still loads. |
| `*.sigx` (legacy) | Yes — Windows account only | `53 49 47 58 31 0A` (`SIGX1\n`) | Oldest files; DPAPI only, no passphrase. Still loads. |
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

## 2. The encrypted container (`*.sigx`, format SIGX3)

The current default. Byte layout:

```
+----------------+------------------------------+---+------------------------+
| magic (6 bytes)| header: one-line JSON (utf-8)|\n | body (binary)          |
| 53 49 47 58 33 | {"v":3,"kdf":...}            |0A | nonce(12) || AES-GCM    |
| 0A  "SIGX3\n"  |                              |   | ciphertext || tag(16)  |
+----------------+------------------------------+---+------------------------+
```

If the file is tied to a computer (`"machine":"dpapi"`), that whole body is then
wrapped once more with `CryptProtectData`, so the bytes on disk are a DPAPI blob
containing the AES-GCM output.

- **Magic** — `SIGX3\n`. `load` reads this to pick the path; plain JSON (`{`) and
  legacy `SIGX2\n` / `SIGX1\n` are handled separately.
- **Header** — a single line of cleartext JSON. It holds **no secrets**, only
  the public parameters needed to derive the key:

  | Field | Meaning |
  |-------|---------|
  | `v` | Container version (`2`). |
  | `kdf` | Key-derivation function (`"pbkdf2-sha256"`). |
  | `salt` | base64 random 16-byte salt for the KDF. |
  | `iters` | PBKDF2 iteration count (default `200000`). |
  | `auth` | *Optional.* `"none"` marks a file saved **without a passphrase**; absent means a passphrase is required. |
  | `hello` | *Optional.* Present only if Windows Hello unlock was enabled (see below). |

  If present, `hello` is `{ "key": <credential name>, "challenge": <base64 32
  bytes>, "sealed": <base64> }`.

  When `auth` is `"none"`, the KDF input is the fixed public string
  `DigitalSignature/v2/no-passphrase` instead of a user secret. This is **not** a
  hidden password — it is in the source — so such a file is protected by DPAPI
  alone, matching the legacy `SIGX1` guarantee. Readers must check this field
  and skip the passphrase prompt (`vault.needs_passphrase`).

- **Body** — Windows `CryptProtectData` output over the UTF-8 JSON payload, using
  extra entropy = `PBKDF2(passphrase, salt, iters)`. Opaque Microsoft structure
  beginning with a version `DWORD` and the well-known DPAPI provider GUID
  `df9d8cd0-1501-…` (`01 00 00 00 d0 8c 9d df 01 …`), including an integrity MAC.

### The layered key — what you need to decrypt

By default there is **exactly one** factor, and it travels with you:

1. **Your passphrase** — stretched with PBKDF2-HMAC-SHA256 (200k iterations)
   over the header's random 16-byte `salt` to give a 32-byte AES key. The
   iteration count slows brute-force guessing against a stolen file.

The header line is passed to AES-GCM as **associated data**, so it is
authenticated even though it is not encrypted: editing `salt`, `iters`, or
`machine` invalidates the tag instead of silently changing how the file is read.
A wrong passphrase fails the same way — the GCM tag simply doesn't verify.

Because no machine identity is involved, **the file opens on any computer** that
has the passphrase. That is the intended default.

**If `"machine":"dpapi"` is present**, a second factor is added:

2. **Your Windows account** — DPAPI's own key, derived by Windows from your
   logon. The AES-GCM body is wrapped with `CryptProtectData` (secondary entropy
   = the same passphrase-derived key). Now an attacker **already inside your
   unlocked Windows session** still cannot open the file without the passphrase —
   and the file is unreadable on any other PC or account, which is precisely the
   trade being made.

A fixed app constant `b"DigitalSignature/v1/dpapi-entropy"` is also prepended to
the DPAPI entropy (binds the blob to this app). Changing it breaks existing
machine-bound files.

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

Someone who copies a `*.sigx` file, without the passphrase:

- **Cannot** read the strokes, timing or any geometry — the payload is
  encrypted and tampering is caught by the GCM tag.
- **Can** still infer, from the extension and `SIGX3` header, that it is a
  signature file from this app, and estimate rough complexity from the **file
  size** (not padded). To hide even that, keep it inside an encrypted volume.
- **Can** attempt an **offline brute-force of the passphrase** on a default
  (portable) file. This is the honest cost of portability: everything needed to
  test a guess travels in the file. PBKDF2 at 200k iterations makes each guess
  expensive, but a weak passphrase is still the weak link — **choose a strong
  one for files you intend to carry between machines.**
- **Cannot** attack a file saved with **"tie to this computer"** off-machine at
  all: DPAPI needs the creating user's key, so guesses can only be made from
  inside that logged-in account.

### Legacy SIGX2 and SIGX1

`SIGX2\n` files wrap the payload directly in DPAPI with the passphrase as
secondary entropy; `SIGX1\n` files use DPAPI with only the fixed app entropy and
no passphrase. Both are **always machine-bound** and both still load. Re-saving
one upgrades it to `SIGX3` — and, unless you tick "tie to this computer", makes
it portable in the process.

---

## 3. Versioning

- **Payload**: the `format` integer (currently `1`). New fields should bump this.
- **Container**: the magic line (currently `SIGX3\n`). `vault.classify()` branches
  on it, so a new layout adds `SIGX4\n` and a matching branch while older readers
  keep working.
