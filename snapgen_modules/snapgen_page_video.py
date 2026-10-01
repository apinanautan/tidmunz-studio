# -*- coding: utf-8 -*-
"""Adapter for SnapGen's recovered Video page.

The Video page is tightly coupled to the recovered bytecode's slot widgets and
state arrays.  This module gives that page an isolated controller without
rebuilding or replacing those widgets.
"""
from __future__ import annotations

from typing import Any


class VideoPageController:
    """Own visibility operations for the original, working Video page."""

    def __init__(self, g: dict[str, Any]):
        self.g = g
        self.slots = g.get("slots")
        self.footer = g.get("footer")
        self._slots_pack = self._remember_pack(self.slots, {"fill": "both", "expand": True})
        self._footer_pack = self._remember_pack(self.footer, {"side": "bottom", "fill": "x"})

    @staticmethod
    def _remember_pack(widget: Any, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            info = dict(widget.pack_info())
            info.pop("in", None)
            return info or fallback
        except Exception:
            return fallback

    def show(self) -> None:
        if self.footer is not None:
            self.footer.pack(**self._footer_pack)
        if self.slots is not None:
            self.slots.pack(**self._slots_pack)

    def hide(self) -> None:
        if self.slots is not None:
            self.slots.pack_forget()
        if self.footer is not None:
            self.footer.pack_forget()


def install(g: dict, root: Any = None) -> VideoPageController:
    """Adopt the recovered Video widgets without changing their state contract."""
    controller = VideoPageController(g)
    g["video_page_controller"] = controller
    return controller
