# -*- coding: utf-8 -*-
"""SnapGen Button Style System — single source of truth for all button colors.

Usage:
    from snapgen_button_styles import STYLE, make_button

    btn = make_button(parent, STYLE.PRIMARY, "🎭 สร้าง", command=do_create)
    btn.pack(side="left", padx=4)

Never hardcode bg/fg/activebackground again. Add new styles here.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Callable
from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()


@dataclass(frozen=True)
class ButtonStyle:
    bg: str
    fg: str
    active_bg: str
    active_fg: str = "white"


class STYLE:
    """Semantic button styles. Pick by function, not by color."""

    # ── Mode buttons (top navigation) ──────────────────────────────────
    MODE_IDLE = ButtonStyle("#FAFAF7", "#1A1A1A", "#F3F4F6", "#1A1A1A")
    MODE_ACTIVE = ButtonStyle("#6B7280", "#FFFFFF", "#4B5563", "#FFFFFF")

    # ── Action buttons ─────────────────────────────────────────────────
    PRIMARY = ButtonStyle("#059669", "white", "#10B981")       # สร้าง, แปลง, generate
    SECONDARY = ButtonStyle("#2563EB", "white", "#1D4ED8")     # Select, Copy
    DANGER = ButtonStyle("#DC2626", "white", "#B91C1C")        # Clear, ล้าง, delete
    AUTO = ButtonStyle("#0EA5E9", "white", "#0284C7")          # ⚡ Auto
    WARNING = ButtonStyle("#F97316", "white", "#EA580C")       # เติมที่ขาด, special
    NEUTRAL = ButtonStyle("#64748B", "white", "#475569")       # Diff, misc
    SUCCESS = ButtonStyle("#4CAF50", "white", "#388E3C")       # Save
    CONTEXT = ButtonStyle("#795548", "white", "#5D4037")       # Context, สรุปบท
    # Reserved only for starting/changing a GPT history on every page.
    # Do not reuse this light blue for Select, Auto, attach, or other actions.
    HISTORY = ButtonStyle("#EFF6FF", "#315A75", "#DBEAFE", "#315A75")

    # ── Aliases for backward compatibility ────────────────────────────
    PURPLE = ButtonStyle("#6D28D9", "white", "#7C3AED")
    BLUE = SECONDARY
    RED = DANGER
    CYAN = AUTO
    ORANGE = WARNING
    SLATE = NEUTRAL
    GREEN = SUCCESS
    BROWN = CONTEXT


# ── Shared button geometry ──────────────────────────────────────────────
# PAGE ACTION CONTRACT: every main action button on every SnapGen page uses
# these values. Do not add page-local width/padding unless the control is an
# intentionally compact utility (X, mic, Slot, arrow, or gallery-card button).
DEFAULT_PADX = 14
DEFAULT_PADY = 7
DEFAULT_FONT = (SNAPGEN_UI_FONT, 9, "bold")
DEFAULT_WIDTH = 14
DEFAULT_HEIGHT = 1
DEFAULT_RELIEF = "flat"
DEFAULT_BD = 0

# TOP MODE TAB CONTRACT: every page tab uses identical geometry and spacing.
# Text length must never decide one tab's size. New pages use this same style.
MODE_WIDTH = 13
MODE_HEIGHT = 1
MODE_PADX = 10
MODE_PADY = 8
MODE_PACK_PADX = 4


def make_button(
    parent: tk.Misc,
    style: ButtonStyle,
    text: str,
    command: Callable | None = None,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    font: tuple = DEFAULT_FONT,
    padx: int = DEFAULT_PADX,
    pady: int = DEFAULT_PADY,
    relief: str = DEFAULT_RELIEF,
    bd: int = DEFAULT_BD,
    **extra,
) -> tk.Button:
    """Create a consistently-styled tk.Button.

    Args:
        parent: parent widget
        style: one of STYLE.PRIMARY / SECONDARY / DANGER / AUTO / WARNING / NEUTRAL / SUCCESS / CONTEXT
        text: button label
        command: callback
        width, height, font, padx, pady, relief, bd: overridable geometry
        **extra: passed directly to tk.Button (e.g. state, cursor)

    Returns:
        tk.Button (not yet packed)
    """
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=style.bg,
        fg=style.fg,
        activebackground=style.active_bg,
        activeforeground=style.active_fg,
        relief=relief,
        bd=bd,
        padx=padx,
        pady=pady,
        width=width,
        height=height,
        font=font,
        **extra,
    )


def style_mode_button(btn: tk.Button, active: bool = False) -> None:
    """Apply MODE_IDLE or MODE_ACTIVE style to an existing mode button."""
    s = STYLE.MODE_ACTIVE if active else STYLE.MODE_IDLE
    try:
        btn.config(
            bg=s.bg, fg=s.fg,
            activebackground=s.active_bg, activeforeground=s.active_fg,
            relief="flat", bd=0, borderwidth=0,
            width=MODE_WIDTH, height=MODE_HEIGHT,
            padx=MODE_PADX, pady=MODE_PADY,
            font=(SNAPGEN_UI_FONT, 10, "bold"),
            cursor="hand2", highlightthickness=0,
            overrelief="flat",
        )
        if btn.winfo_manager() == "pack":
            btn.pack_configure(padx=MODE_PACK_PADX)
    except Exception:
        pass
