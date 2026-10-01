# -*- coding: utf-8 -*-
"""Stable Thai UI font selection for every SnapGen workstation.

Windows Tahoma is used for the live UI because its Thai combining-mark
rendering is stable in Tk/GDI across the supported machines. Noto Sans Thai
stays bundled under SIL OFL as an optional asset, but is not forced into the
main UI where it can make glyphs look broken.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

# Tk/GDI on some Windows builds renders Thai combining marks incorrectly with
# the bundled Noto face. Tahoma is a standard Windows Thai-capable face and
# keeps glyph shaping stable across supported workstations.
UI_FONT = "Tahoma"
FALLBACK_FONT = "Noto Sans Thai"
FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansThai.ttf"
FR_PRIVATE = 0x10

_registered = False


def register() -> str:
    """Prepare the UI font and return the usable family name."""
    global _registered
    if _registered:
        return UI_FONT
    if os.name != "nt" or not FONT_PATH.is_file():
        return UI_FONT
    try:
        added = ctypes.windll.gdi32.AddFontResourceExW(
            str(FONT_PATH), FR_PRIVATE, 0
        )
        if int(added) > 0:
            _registered = True
            return UI_FONT
    except Exception:
        pass
    # Tahoma remains the safest Windows choice even when the optional private
    # font registration is unavailable. Noto is retained in assets but is not
    # forced into Tk by default.
    return UI_FONT


def font_family() -> str:
    """Return the selected family without triggering a second registration."""
    return UI_FONT


def apply_to_widget_tree(root) -> None:
    """Replace only known legacy Thai fonts in already-created widgets.

    Do not rewrite every widget font. Tk uses different fonts intentionally
    for code/log boxes, emoji, menus, and platform controls. Rewriting those
    repeatedly changes their metrics and can break the recovered page layout.
    """
    try:
        import tkinter.font as tkfont
    except Exception:
        return
    family = font_family()
    widget_classes = {
        "Button", "Label", "Entry", "Text", "Listbox", "Checkbutton",
        "Radiobutton", "Menubutton", "Scale", "Spinbox", "Message",
        "TButton", "TLabel", "TEntry", "TCombobox", "TCheckbutton",
        "TRadiobutton", "TSpinbox", "TLabelframe",
    }
    legacy_families = {"leelawadee ui", "leelawadee"}

    def walk(widget):
        try:
            cls = widget.winfo_class()
            if cls in widget_classes:
                raw = widget.cget("font")
                actual = tkfont.Font(widget, font=raw).actual() if raw else {}
                old_family = str(actual.get("family") or "")
                # Only repair the old hardcoded family. Preserve Consolas,
                # TkFixedFont, Segoe UI, emoji, and all other intentional
                # choices. This is the key layout-safety boundary.
                if old_family.casefold() in legacy_families:
                    size = int(actual.get("size") or 10)
                    weight = str(actual.get("weight") or "normal")
                    slant = str(actual.get("slant") or "roman")
                    widget.configure(font=(family, size, weight, slant))
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                walk(child)
        except Exception:
            pass

    walk(root)
