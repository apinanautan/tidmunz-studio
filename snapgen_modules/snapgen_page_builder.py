# -*- coding: utf-8 -*-
"""SnapGen Page Builder — single source of truth for creating mode pages.

Usage in snapgen_gui_v2.py:

    from snapgen_page_builder import build_page, register_mode, make_action_row

    # Create a page
    karaoke_page, karaoke_box = build_page(root, "🔤 คาราโอเกะ — แปลงชื่อไทยเป็นคำอ่าน")

    # Register as a mode (button + show/hide + color sync)
    karaoke_mode_btn, show_karaoke = register_mode(
        mode_frame, mode_buttons, "karaoke", "🔤 คาราโอเกะ",
        karaoke_page, slots, img_page, ref_page, prop_page, new_page,
        footer, current_mode, _set_mode_active, _sync_ref_mode_buttons
    )

    # Add action buttons (consistent style via snapgen_button_styles)
    row = make_action_row(karaoke_box)
    make_btn = make_styled_button
    make_btn(row, "PRIMARY", "🔤 แปลง", command=convert).pack(side="left", padx=4)
    make_btn(row, "SECONDARY", "📋 Copy", command=copy).pack(side="left", padx=4)
    make_btn(row, "DANGER", "🧹 Clear", command=clear).pack(side="left", padx=4)

All pages look identical. All buttons look identical. Never hardcode colors again.
"""
from __future__ import annotations

from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()

import tkinter as tk
from tkinter import ttk
from typing import Callable

from snapgen_button_styles import STYLE, ButtonStyle, make_button, style_mode_button


# ── Page background / frame defaults ─────────────────────────────────────
PAGE_BG = "#FFFFFF"
PAGE_PADX = 16
PAGE_PADY = 16
LABELFRAME_PADX = 12
LABELFRAME_PADY = 12
LABELFRAME_FONT = (SNAPGEN_UI_FONT, 11, "bold")
LABEL_FG = "#1A1A1A"
LABEL_SUB_FG = "#555"
LABEL_FONT = (SNAPGEN_UI_FONT, 10)
ENTRY_FONT = (SNAPGEN_UI_FONT, 12)
STATUS_FONT = (SNAPGEN_UI_FONT, 9)
LOG_HEIGHT = 2

_LOCK_LABELS = {
    "character": "ตัวละคร",
    "location": "สถานที่",
    "prop": "Prop",
    "prompt": "Prompt",
    "reference": "ไฟล์แนบ",
    "image": "รูป",
    "ref": "Ref",
}


def _lock_state(g: dict) -> dict:
    state = g.get("_selection_locks")
    if not isinstance(state, dict):
        state = {}
        g["_selection_locks"] = state
    return state


def selection_lock_text(g: dict) -> str:
    """Return the shared, human-readable selection lock status."""
    state = _lock_state(g)
    parts = []
    for kind in ("character", "location", "prop", "ref", "prompt", "reference", "image"):
        values = state.get(kind) or []
        if isinstance(values, str):
            values = [values]
        values = [str(value).strip() for value in values if str(value).strip()]
        if values:
            shown = ", ".join(values[:4])
            if len(values) > 4:
                shown += f" +{len(values) - 4}"
            parts.append(f"{_LOCK_LABELS.get(kind, kind)}: {shown}")
    return "🔒 ล็อกแล้ว: " + " | ".join(parts) if parts else "🔓 ยังไม่ได้เลือกข้อมูลล็อก"


def _refresh_selection_lock_vars(g: dict) -> str:
    text = selection_lock_text(g)
    live = []
    for var in g.get("_selection_lock_vars", []) or []:
        try:
            var.set(text)
            live.append(var)
        except Exception:
            pass
    g["_selection_lock_vars"] = live
    return text


def set_selection_lock(g: dict, kind: str, value: object, *, append: bool = False) -> str:
    """Set one shared lock and refresh every registered work-page display."""
    name = str(value or "").strip()
    if not name:
        return _refresh_selection_lock_vars(g)
    state = _lock_state(g)
    current = state.get(kind) or []
    if isinstance(current, str):
        current = [current]
    if append:
        current = [str(item).strip() for item in current if str(item).strip()]
        if name not in current:
            current.append(name)
        state[kind] = current[-10:]
    else:
        state[kind] = [name]
    return _refresh_selection_lock_vars(g)


def set_selection_locks(g: dict, kind: str, values) -> str:
    """Replace a lock category with a de-duplicated list of selected values."""
    unique = []
    for value in values or []:
        name = str(value or "").strip()
        if name and name not in unique:
            unique.append(name)
    _lock_state(g)[kind] = unique[:10]
    return _refresh_selection_lock_vars(g)


def clear_selection_lock(g: dict, kind: str | None = None) -> str:
    state = _lock_state(g)
    if kind is None:
        state.clear()
    else:
        state.pop(kind, None)
    return _refresh_selection_lock_vars(g)


def remove_selection_lock(g: dict, kind: str, value: object) -> str:
    """Remove only one selected value without disturbing other processes."""
    name = str(value or "").strip()
    state = _lock_state(g)
    current = state.get(kind) or []
    if isinstance(current, str):
        current = [current]
    state[kind] = [item for item in current if str(item).strip() != name]
    if not state[kind]:
        state.pop(kind, None)
    return _refresh_selection_lock_vars(g)


def make_selection_lock_bar(parent: tk.Misc, g: dict, *, bg: str = PAGE_BG) -> tk.Label:
    """Create a persistent one-line view of locks shared by every process."""
    var = tk.StringVar(master=parent, value=selection_lock_text(g))
    g.setdefault("_selection_lock_vars", []).append(var)
    label = tk.Label(
        parent, textvariable=var, bg=bg, fg="#475569", anchor="w",
        font=(SNAPGEN_UI_FONT, 9), padx=8, pady=4,
        highlightthickness=1, highlightbackground="#E2E8F0",
    )
    return label


def install_selection_lock_api(g: dict) -> None:
    """Expose the shared API to legacy/recovered parts of the application."""
    g["set_selection_lock"] = lambda kind, value, append=False: set_selection_lock(
        g, kind, value, append=append
    )
    g["set_selection_locks"] = lambda kind, values: set_selection_locks(g, kind, values)
    g["clear_selection_lock"] = lambda kind=None: clear_selection_lock(g, kind)
    g["selection_lock_text"] = lambda: selection_lock_text(g)


def build_page(parent: tk.Misc, title: str) -> tuple[tk.Frame, tk.LabelFrame]:
    """Create a standard mode page with a LabelFrame container.

    Returns:
        (page_frame, inner_labelframe)
    """
    page = tk.Frame(parent, bg=PAGE_BG)
    box = tk.LabelFrame(
        page,
        text=title,
        bg=PAGE_BG,
        fg=LABEL_FG,
        font=LABELFRAME_FONT,
        padx=LABELFRAME_PADX,
        pady=LABELFRAME_PADY,
    )
    box.pack(fill="both", expand=True, padx=PAGE_PADX, pady=PAGE_PADY)
    return page, box


def make_action_row(parent: tk.Misc, bg: str = PAGE_BG) -> tk.Frame:
    """Create a standard action button row inside a page."""
    return tk.Frame(parent, bg=bg)


def make_styled_button(parent: tk.Misc, style_name: str, text: str,
                       command: Callable | None = None, **kw) -> tk.Button:
    """Create a button using a STYLE name string.

    Args:
        style_name: one of "PRIMARY", "SECONDARY", "DANGER", "AUTO",
                    "WARNING", "NEUTRAL", "SUCCESS", "CONTEXT"
        text: button label
        command: callback
    """
    s = getattr(STYLE, style_name.upper(), STYLE.PRIMARY)
    return make_button(parent, s, text, command=command, **kw)


def make_label(parent: tk.Misc, text: str, *, sub: bool = False) -> tk.Label:
    """Create a standard label."""
    return tk.Label(
        parent,
        text=text,
        bg=PAGE_BG,
        fg=LABEL_SUB_FG if sub else LABEL_FG,
        font=LABEL_FONT,
    )


def make_entry(parent: tk.Misc, textvariable: tk.StringVar | None = None,
               width: int = 30, readonly: bool = False) -> tk.Entry:
    """Create a standard entry field."""
    kw = dict(font=ENTRY_FONT, width=width, relief="solid", bd=1)
    if readonly:
        kw["state"] = "readonly"
        kw["readonlybackground"] = "#F0F0F0"
    if textvariable is not None:
        kw["textvariable"] = textvariable
    return tk.Entry(parent, **kw)


def make_status_label(parent: tk.Misc, textvariable: tk.StringVar | None = None) -> tk.Label:
    """Create a small status label at bottom of page."""
    kw = dict(bg=PAGE_BG, fg=LABEL_SUB_FG, font=STATUS_FONT, anchor="w")
    if textvariable is not None:
        kw["textvariable"] = textvariable
    return tk.Label(parent, **kw)


def make_log_box(parent: tk.Misc, *, bg: str = "#FFFFFF") -> tk.Text:
    """Create the same compact two-line log used on every work page."""
    return tk.Text(
        parent,
        height=LOG_HEIGHT,
        wrap="word",
        bg=bg,
        fg="#111827",
        relief="solid",
        bd=1,
        font=(SNAPGEN_UI_FONT, 9),
        padx=8,
        pady=5,
        spacing1=1,
        spacing3=1,
    )


def append_log(box: tk.Text, message: object, *, max_lines: int = 200) -> None:
    """Append one event and keep the newest text visible automatically."""
    text = " ".join(str(message or "").split()).strip()
    if not text:
        return
    try:
        import snapgen_error_reporter
        snapgen_error_reporter.report_log(text, "page log")
    except Exception:
        pass
    try:
        box.configure(state="normal")
        box.insert(tk.END, text + "\n")
        line_count = int(box.index("end-1c").split(".", 1)[0])
        if line_count > max_lines:
            box.delete("1.0", f"{line_count - max_lines}.0")
        box.see(tk.END)
    except Exception:
        pass


# ── Mode registration ────────────────────────────────────────────────────

def register_mode(
    mode_frame: tk.Misc,
    mode_buttons: dict,
    key: str,
    label: str,
    page: tk.Frame,
    other_pages: list[tk.Widget],
    footer: tk.Widget | None,
    current_mode: tk.StringVar | None,
    on_set_active: Callable | None = None,
    on_sync: Callable | None = None,
) -> tuple[tk.Button, Callable]:
    """Register a page as a mode with a top-bar button and show/hide logic.

    Args:
        mode_frame: the top bar Frame holding mode buttons
        mode_buttons: dict to store button in (e.g. {"video": btn, ...})
        key: mode key (e.g. "karaoke")
        label: button text (e.g. "🔤 คาราโอเกะ")
        page: the page Frame to show/hide
        other_pages: list of other page widgets to pack_forget
        footer: optional footer widget to re-pack
        current_mode: optional StringVar to set mode name
        on_set_active: callback to sync pyc's _mode_btn_map (optional)
        on_sync: callback to sync _sync_ref_mode_buttons (optional)

    Returns:
        (mode_button, show_mode_function)
    """
    btn = tk.Button(mode_frame, text=label, command=lambda: _show())
    style_mode_button(btn)
    btn.pack(side="left", padx=5)
    mode_buttons[key] = btn

    def _show():
        try:
            for p in other_pages:
                try:
                    p.pack_forget()
                except Exception:
                    pass
            page.pack(fill="both", expand=True)
            if footer:
                try:
                    footer.pack(side="bottom", fill="x")
                except Exception:
                    pass
            if current_mode:
                current_mode.set(key)
            if on_set_active:
                on_set_active(key)
            if on_sync:
                on_sync(key)
        except Exception:
            pass

    return btn, _show


def sync_all_mode_buttons(mode_buttons: dict, active_key: str) -> None:
    """Sync all mode button colors. Call after skin/UI changes."""
    for key, btn in mode_buttons.items():
        try:
            style_mode_button(btn, active=(key == active_key))
        except Exception:
            pass
