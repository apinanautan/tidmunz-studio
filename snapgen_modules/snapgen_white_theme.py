# -*- coding: utf-8 -*-
"""White minimal surface theme for SnapGen.

This module deliberately never configures Button, TButton, Menubutton, or
OptionMenu widgets. Button colors remain owned by their page/style modules.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()


WHITE = "#FFFFFF"
TEXT = "#171717"
MUTED = "#6B7280"
BORDER = "#E5E7EB"
FOCUS = "#CBD5E1"


def apply(root: tk.Misc) -> None:
    """Apply white surfaces recursively without changing any button colors."""
    try:
        root.configure(bg=WHITE)
    except Exception:
        pass

    try:
        style = ttk.Style(root)
        style.configure("TFrame", background=WHITE)
        style.configure("TLabelframe", background=WHITE, bordercolor=BORDER,
                        relief="flat", borderwidth=1)
        style.configure("TLabelframe.Label", background=WHITE, foreground=TEXT)
        style.configure("TLabel", background=WHITE, foreground=TEXT)
        style.configure("TEntry", fieldbackground=WHITE, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        # Intentionally do not configure TButton or button-derived styles.
    except Exception:
        pass

    def skin(widget: tk.Misc) -> None:
        try:
            cls = widget.winfo_class()
        except Exception:
            return

        # Hard boundary: no button-like widget is changed by this theme.
        if cls in {"Button", "TButton", "Menubutton", "TMenubutton"}:
            return

        try:
            if cls in {"Frame", "TFrame"}:
                widget.configure(bg=WHITE)
            elif cls == "Labelframe":
                widget.configure(bg=WHITE, fg=TEXT, relief="flat", bd=0,
                                 highlightthickness=1,
                                 highlightbackground=BORDER,
                                 highlightcolor=BORDER)
            elif cls == "Label":
                current_fg = str(widget.cget("fg"))
                # Preserve semantic status colors; only normalize common text colors.
                common = {"black", "#000", "#000000", "#111", "#1A1A1A",
                          "#333", "#555", "#666", "#6B7280", "#999"}
                fg = TEXT if current_fg in common else current_fg
                widget.configure(bg=WHITE, fg=fg)
            elif cls in {"Text", "Entry", "Listbox"}:
                widget.configure(bg=WHITE, relief="flat", bd=0,
                                 highlightthickness=1,
                                 highlightbackground=BORDER,
                                 highlightcolor=FOCUS)
            elif cls in {"Checkbutton", "Radiobutton"}:
                widget.configure(bg=WHITE, activebackground=WHITE,
                                 selectcolor=WHITE, highlightthickness=0, bd=0)
            elif cls == "Canvas":
                widget.configure(bg=WHITE, highlightthickness=0)
        except Exception:
            pass

        try:
            for child in widget.winfo_children():
                skin(child)
        except Exception:
            pass

    skin(root)


def apply_settings_dialog(window: tk.Toplevel) -> None:
    """Match Settings to the app while preserving every existing button color."""
    apply(window)

    def polish(widget: tk.Misc) -> None:
        try:
            cls = widget.winfo_class()
            if cls == "Button":
                # Geometry only. Deliberately do not pass bg/fg/active colors.
                widget.configure(relief="flat", bd=0, borderwidth=0,
                                 highlightthickness=0, padx=12, pady=6,
                                 font=(SNAPGEN_UI_FONT, 9, "bold"), cursor="hand2")
            elif cls == "Labelframe":
                widget.configure(padx=12, pady=10)
            elif cls == "Label":
                widget.configure(font=(SNAPGEN_UI_FONT, 10))
            elif cls == "Entry":
                widget.configure(font=(SNAPGEN_UI_FONT, 10), padx=8, pady=6)
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                polish(child)
        except Exception:
            pass

    polish(window)

    try:
        window.update_idletasks()
        req_w = max(760, min(window.winfo_reqwidth() + 32, 920))
        req_h = max(480, min(window.winfo_reqheight() + 36, 620))
        parent = window.master
        if parent is not None and parent.winfo_exists():
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - req_w) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - req_h) // 2)
            window.geometry(f"{req_w}x{req_h}+{x}+{y}")
        else:
            window.geometry(f"{req_w}x{req_h}")
        window.minsize(700, 440)
    except Exception:
        pass
