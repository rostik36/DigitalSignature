"""Shared visual language: colours, type, spacing and widget factories.

Tk has no stylesheet, so without a single source of truth every window invents
its own hex codes and paddings and the app drifts out of alignment. Everything
visual lives here; windows compose from these helpers rather than passing raw
colours around.
"""

from __future__ import annotations

import tkinter as tk

# --------------------------------------------------------------------------
# Palette
#
# One neutral ramp plus a single blue accent. Restraint is deliberate: the
# signature preview is the thing worth looking at, so the chrome stays quiet.
# --------------------------------------------------------------------------

BG = "#eef1f6"          # window background
SURFACE = "#ffffff"     # cards sitting on the background
BORDER = "#d7dee8"      # hairlines
BORDER_STRONG = "#c2ccda"

TEXT = "#16202e"        # primary copy
TEXT_MUTED = "#5b6a7d"  # secondary copy
TEXT_FAINT = "#8d9aab"  # hints, tips

ACCENT = "#2d6cdf"      # primary action
ACCENT_HOVER = "#245ec9"
ACCENT_ACTIVE = "#1d4fab"

NEUTRAL = "#e3e9f2"     # secondary button
NEUTRAL_HOVER = "#d5dee9"
NEUTRAL_ACTIVE = "#c7d2e0"

DANGER = "#c53030"
SUCCESS = "#2f855a"
WARN = "#b7791f"

DISABLED_FG = "#a6b1c0"
DISABLED_BG = "#dfe5ee"        # muted fill for a disabled secondary button
DISABLED_ACCENT_BG = "#b9c9e8"  # a drained accent, so "off" reads as off

# --------------------------------------------------------------------------
# Type scale
# --------------------------------------------------------------------------

FAMILY = "Segoe UI"

TITLE = (FAMILY, 17, "bold")
SECTION = (FAMILY, 9, "bold")
BODY = (FAMILY, 10)
BODY_SMALL = (FAMILY, 9)
HINT = (FAMILY, 8)
HINT_ITALIC = (FAMILY, 8, "italic")
BUTTON = (FAMILY, 10)
BUTTON_STRONG = (FAMILY, 10, "bold")

# --------------------------------------------------------------------------
# Spacing (multiples of 4 keep vertical rhythm consistent)
# --------------------------------------------------------------------------

GUTTER = 18   # window side margin
GAP = 8       # between related controls
GAP_LG = 16   # between sections


class Button(tk.Button):
    """Flat button with hover feedback that survives being disabled.

    Tk's ``activebackground`` only applies while the mouse is *pressed*, so
    hover has to be wired manually -- and it must not repaint a disabled
    button, which would make dead controls look clickable.
    """

    def __init__(self, parent, text, command, kind="secondary", **kw):
        if kind == "primary":
            rest, hover, active, fg, font = (
                ACCENT, ACCENT_HOVER, ACCENT_ACTIVE, "white", BUTTON_STRONG)
            off = DISABLED_ACCENT_BG
        elif kind == "ghost":
            rest, hover, active, fg, font = (BG, NEUTRAL, NEUTRAL_HOVER, TEXT_MUTED, BUTTON)
            off = BG
        else:
            rest, hover, active, fg, font = (
                NEUTRAL, NEUTRAL_HOVER, NEUTRAL_ACTIVE, TEXT, BUTTON)
            off = DISABLED_BG

        self._rest = rest
        self._hover = hover
        # A disabled button must not keep the full accent fill, or it still
        # looks like the thing to click.
        self._off = off

        kw.setdefault("padx", 14)
        kw.setdefault("pady", 7)
        super().__init__(
            parent, text=text, command=command, font=font,
            bg=rest, fg=fg, activebackground=active, activeforeground=fg,
            disabledforeground=DISABLED_FG, relief="flat", borderwidth=0,
            highlightthickness=0, cursor="hand2", **kw,
        )
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, _e=None) -> None:
        if str(self["state"]) != "disabled":
            self.config(bg=self._hover)

    def _on_leave(self, _e=None) -> None:
        if str(self["state"]) != "disabled":
            self.config(bg=self._rest)

    def set_state(self, enabled: bool) -> None:
        """Enable/disable, clearing any hover tint and applying the off fill."""
        self.config(state="normal" if enabled else "disabled",
                    bg=self._rest if enabled else self._off,
                    cursor="hand2" if enabled else "arrow")


def card(parent, **kw) -> tk.Frame:
    """A white panel with a hairline border, for grouping related content."""
    return tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                    highlightbackground=BORDER, **kw)


def section_label(parent, text: str) -> tk.Label:
    """Small uppercase heading that introduces a group of controls."""
    return tk.Label(parent, text=text.upper(), bg=BG, fg=TEXT_FAINT,
                    font=SECTION, anchor="w")


def separator(parent) -> tk.Frame:
    return tk.Frame(parent, bg=BORDER, height=1)
