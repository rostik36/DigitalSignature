"""Data model for captured signatures plus persistence and geometry helpers.

A :class:`Signature` is an ordered list of *strokes*; each stroke is an ordered
list of :class:`Point` samples. A point carries the pen/mouse position, the
time (seconds since capture started) and an optional pressure value. Timing is
kept so replay can reproduce the natural speed of the original writing.

Coordinates are stored in the capture canvas' own pixel space. They are only
mapped into real screen coordinates at replay time, via :meth:`Signature.map_to_box`,
so the same signature can be re-drawn into any target rectangle of any size.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

FORMAT_VERSION = 1

# Leading bytes that mark a DPAPI-encrypted signature file (see Signature.save).
ENC_MAGIC = b"SIGX1\n"

# A rectangle in screen pixels: (left, top, right, bottom).
Box = Tuple[int, int, int, int]


@dataclass
class Point:
    """One sample along a stroke."""

    x: float
    y: float
    t: float = 0.0  # seconds since capture start
    p: float = 1.0  # pressure 0..1 (1.0 when the device reports none)


@dataclass
class Signature:
    """An ordered collection of strokes describing a handwritten mark."""

    strokes: List[List[Point]] = field(default_factory=list)
    # Size of the canvas the signature was drawn on (informational only).
    source_width: float = 0.0
    source_height: float = 0.0

    # ------------------------------------------------------------------ state
    def is_empty(self) -> bool:
        return not any(self.strokes)

    def point_count(self) -> int:
        return sum(len(s) for s in self.strokes)

    def duration(self) -> float:
        """Total elapsed time from the first sample to the last, in seconds."""
        first: Optional[float] = None
        last: Optional[float] = None
        for stroke in self.strokes:
            for pt in stroke:
                if first is None:
                    first = pt.t
                last = pt.t
        if first is None or last is None:
            return 0.0
        return max(0.0, last - first)

    def bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Axis-aligned bounding box of every point: (minx, miny, maxx, maxy)."""
        xs: List[float] = []
        ys: List[float] = []
        for stroke in self.strokes:
            for pt in stroke:
                xs.append(pt.x)
                ys.append(pt.y)
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    # ------------------------------------------------------------- geometry
    def map_to_box(
        self, box: Box, padding_frac: float = 0.08, stretch: bool = False
    ) -> List[List[Point]]:
        """Return strokes with x/y scaled to fit ``box`` (screen pixels).

        With ``stretch`` False (default) the signature's aspect ratio is
        preserved: both axes use one uniform scale and the mark is centered in
        the box. With ``stretch`` True width and height are scaled independently
        so the signature fills the whole box, distorting its proportions.

        A margin of ``padding_frac`` of the box size is kept on every side so the
        mark never touches the very edge of the field. Time and pressure are
        passed through unchanged.
        """
        bounds = self.bounds()
        if bounds is None:
            return []

        minx, miny, maxx, maxy = bounds
        src_w = max(maxx - minx, 1e-6)
        src_h = max(maxy - miny, 1e-6)

        left, top, right, bottom = box
        if right < left:
            left, right = right, left
        if bottom < top:
            top, bottom = bottom, top

        box_w = max(right - left, 1.0)
        box_h = max(bottom - top, 1.0)

        pad_x = box_w * padding_frac
        pad_y = box_h * padding_frac
        inner_w = max(box_w - 2 * pad_x, 1.0)
        inner_h = max(box_h - 2 * pad_y, 1.0)

        if stretch:
            # Independent scales fill the padded box exactly (aspect distorted).
            scale_x = inner_w / src_w
            scale_y = inner_h / src_h
            off_x = left + pad_x
            off_y = top + pad_y
        else:
            # One uniform scale preserves aspect ratio; center the result.
            scale_x = scale_y = min(inner_w / src_w, inner_h / src_h)
            off_x = left + pad_x + (inner_w - src_w * scale_x) / 2.0
            off_y = top + pad_y + (inner_h - src_h * scale_y) / 2.0

        mapped: List[List[Point]] = []
        for stroke in self.strokes:
            out: List[Point] = []
            for pt in stroke:
                sx = off_x + (pt.x - minx) * scale_x
                sy = off_y + (pt.y - miny) * scale_y
                out.append(Point(sx, sy, pt.t, pt.p))
            mapped.append(out)
        return mapped

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        # Each point is stored as a compact [x, y, t, p] array to keep files small.
        return {
            "format": FORMAT_VERSION,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "strokes": [
                [[pt.x, pt.y, pt.t, pt.p] for pt in stroke] for stroke in self.strokes
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> "Signature":
        strokes: List[List[Point]] = []
        for raw_stroke in data.get("strokes", []):
            stroke: List[Point] = []
            for raw_pt in raw_stroke:
                x = raw_pt[0]
                y = raw_pt[1]
                t = raw_pt[2] if len(raw_pt) > 2 else 0.0
                p = raw_pt[3] if len(raw_pt) > 3 else 1.0
                stroke.append(Point(x, y, t, p))
            strokes.append(stroke)
        return Signature(
            strokes=strokes,
            source_width=data.get("source_width", 0.0),
            source_height=data.get("source_height", 0.0),
        )

    # --- JSON bytes (the payload that encryption wraps) ---
    def to_json_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), indent=2).encode("utf-8")

    @staticmethod
    def from_json_bytes(raw: bytes) -> "Signature":
        # utf-8-sig tolerates a BOM on plain JSON exported by other tools.
        return Signature.from_dict(json.loads(raw.decode("utf-8-sig")))

    def save(self, path: str) -> None:
        """Save as **plain** JSON. Encrypted formats go through ``vault`` (the
        app handles the passphrase/Hello prompts), since they need a secret."""
        with open(path, "wb") as fh:
            fh.write(self.to_json_bytes())

    @staticmethod
    def load(path: str) -> "Signature":
        """Load a **plain** JSON or legacy DPAPI-only (SIGX1) file.

        Passphrase-protected SIGX2 files require a secret and are handled by the
        app via :mod:`app.vault`; loading one here raises.
        """
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.startswith(b"SIGX2\n"):
            raise ValueError("This file is passphrase-encrypted; open it through the app.")
        if raw.startswith(ENC_MAGIC):  # legacy SIGX1: DPAPI only
            from .secure import unprotect

            raw = unprotect(raw[len(ENC_MAGIC):])
        return Signature.from_json_bytes(raw)


def distance(a: Point, b: Point) -> float:
    """Euclidean distance between two points (ignoring time/pressure)."""
    return math.hypot(b.x - a.x, b.y - a.y)
