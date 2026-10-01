"""Literal forbidden-word highlighter for Video Slot prompts.

The word list is data-driven.  This module only highlights matching text; it
never edits a prompt and never blocks video generation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


TAG_NAME = "snapgen_video_forbidden"
TAG_BACKGROUND = "#DC2626"
TAG_FOREGROUND = "#FFFFFF"


def load_word_data(path):
    """Return (enabled, match_case, words) from the JSON data file."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    raw_words = data.get("words") if isinstance(data, dict) else None
    if not isinstance(raw_words, list):
        raise ValueError("video_forbidden_words.json ต้องมีรายการ words")
    words = []
    seen = set()
    for value in raw_words:
        word = str(value or "").strip()
        key = word.casefold()
        if word and key not in seen:
            seen.add(key)
            words.append(word)
    # Longer phrases win visually when one phrase contains another.
    words.sort(key=len, reverse=True)
    return bool(data.get("enabled", True)), bool(data.get("match_case", False)), words


def find_matches(text, words, match_case=False):
    """Return literal (start, end, word) matches without changing *text*."""
    flags = 0 if match_case else re.IGNORECASE
    matches = []
    for word in words:
        for found in re.finditer(re.escape(word), str(text or ""), flags):
            matches.append((found.start(), found.end(), word))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    return matches


def install(g, root, data_path):
    """Attach lightweight, debounced highlighting to Video Slot Text widgets."""
    state = {
        "mtime": None,
        "enabled": True,
        "match_case": False,
        "words": [],
        "after": {},
    }
    data_path = Path(data_path)

    def refresh_data():
        try:
            mtime = data_path.stat().st_mtime_ns
            if state["mtime"] != mtime:
                enabled, match_case, words = load_word_data(data_path)
                state.update(
                    mtime=mtime,
                    enabled=enabled,
                    match_case=match_case,
                    words=words,
                )
        except Exception as exc:
            # A broken custom list must not break typing or video generation.
            state.update(enabled=False, words=[])
            print(f"[SnapGen] video forbidden-word data error: {exc}")

    def highlight(widget):
        try:
            if not widget.winfo_exists():
                return []
            refresh_data()
            widget.tag_remove(TAG_NAME, "1.0", "end")
            widget.tag_configure(
                TAG_NAME,
                background=TAG_BACKGROUND,
                foreground=TAG_FOREGROUND,
            )
            if not state["enabled"] or not state["words"]:
                return []
            text = widget.get("1.0", "end-1c")
            matches = find_matches(text, state["words"], state["match_case"])
            for start, end, _word in matches:
                widget.tag_add(
                    TAG_NAME,
                    f"1.0+{start}c",
                    f"1.0+{end}c",
                )
            widget.tag_raise(TAG_NAME)
            return matches
        except Exception:
            return []

    def schedule(widget, delay=80):
        key = str(widget)
        previous = state["after"].pop(key, None)
        if previous is not None:
            try:
                root.after_cancel(previous)
            except Exception:
                pass
        try:
            state["after"][key] = root.after(
                delay,
                lambda w=widget, k=key: (
                    state["after"].pop(k, None),
                    highlight(w),
                ),
            )
        except Exception:
            pass

    def on_modified(widget):
        try:
            widget.edit_modified(False)
        except Exception:
            pass
        schedule(widget)

    installed = 0
    for widget in g.get("slot_prompts") or []:
        try:
            if getattr(widget, "_snapgen_forbidden_guard", False):
                continue
            widget._snapgen_forbidden_guard = True
            widget.tag_configure(
                TAG_NAME,
                background=TAG_BACKGROUND,
                foreground=TAG_FOREGROUND,
            )
            widget.bind(
                "<<Modified>>",
                lambda _event, w=widget: on_modified(w),
                add="+",
            )
            widget.bind(
                "<<Paste>>",
                lambda _event, w=widget: schedule(w, 120),
                add="+",
            )
            widget.bind(
                "<<Cut>>",
                lambda _event, w=widget: schedule(w, 120),
                add="+",
            )
            try:
                widget.edit_modified(False)
            except Exception:
                pass
            schedule(widget, 0)
            installed += 1
        except Exception:
            pass

    g["highlight_video_forbidden_words"] = highlight
    g["reload_video_forbidden_words"] = lambda: (
        state.update(mtime=None),
        [schedule(widget, 0) for widget in (g.get("slot_prompts") or [])],
    )
    return installed

