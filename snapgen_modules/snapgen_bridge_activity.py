"""Show Bridge work in the footer light: orange while a picture is being made, then done/failed.

Every page makes pictures through ``snapgen_image_gen.generate_image``; ``install`` wraps it once
so the footer light and text tell the user that something is happening and when it finished.
"""
from __future__ import annotations

import threading

BUSY, DONE, FAILED = "#F59E0B", "#22C55E", "#EF4444"
_state = {"running": 0, "idle_text": None}
_lock = threading.Lock()


def _root(g):
    import tkinter
    return g.get("root") or tkinter._default_root


def _show(g, colour, text):
    root = _root(g)
    light, item, var = g.get("snap_light"), g.get("snap_light_item"), g.get("snap_status_var")

    def apply():
        try:
            if light is not None and item is not None and light.winfo_exists():
                light.itemconfig(item, fill=colour)
            if var is not None and text is not None:
                var.set(text)
        except Exception:
            pass
    try:
        (root.after(0, apply) if root is not None else apply())
    except Exception:
        pass


def _idle_text(g):
    var = g.get("snap_status_var")
    try:
        text = str(var.get()) if var is not None else ""
    except Exception:
        text = ""
    return text if text and not text.startswith("Bridge: กำลัง") and "สร้างรูปเสร็จ" not in text \
        and "สร้างรูปไม่สำเร็จ" not in text else (_state["idle_text"] or "Bridge: พร้อม")


def start(g, what="สร้างรูป"):
    with _lock:
        if _state["running"] == 0:
            _state["idle_text"] = _idle_text(g)
        _state["running"] += 1
        count = _state["running"]
        g["_bridge_busy"] = count
    _show(g, BUSY, f"Bridge: กำลัง{what}..." + (f" ({count} งาน)" if count > 1 else ""))


def finish(g, ok=True, what="สร้างรูป"):
    with _lock:
        _state["running"] = max(0, _state["running"] - 1)
        left = _state["running"]
        g["_bridge_busy"] = left
        idle = _state["idle_text"] or "Bridge: พร้อม"
    if left:
        _show(g, BUSY, f"Bridge: กำลัง{what}... ({left} งาน)")
        return
    _show(g, DONE if ok else FAILED, f"✓ {what}เสร็จ" if ok else f"✗ {what}ไม่สำเร็จ")
    root = _root(g)
    try:
        # Back to the normal status after a few seconds.
        root.after(6000, lambda: _state["running"] == 0 and _show(g, DONE, idle))
    except Exception:
        pass


def install(g, imgmod) -> None:
    """Wrap imgmod.generate_image once so every page shows the activity light."""
    if imgmod is None or getattr(imgmod.generate_image, "_snapgen_activity", False):
        return
    original = imgmod.generate_image

    def generate_image(*args, **kwargs):
        start(g)
        ok = False
        try:
            result = original(*args, **kwargs)
            ok = True
            return result
        finally:
            finish(g, ok)
    generate_image._snapgen_activity = True
    imgmod.generate_image = generate_image
