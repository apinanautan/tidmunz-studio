# -*- coding: utf-8 -*-
"""SnapGen prop page.

This module owns the widgets, state, and callbacks for this page only.
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()
from snapgen_page_builder import (
    make_log_box as _builder_make_log_box,
    append_log as _builder_append_log,
    set_selection_lock as _builder_set_selection_lock,
    set_selection_locks as _builder_set_selection_locks,
    remove_selection_lock as _builder_remove_selection_lock,
)
from snapgen_button_styles import STYLE


_PROP_MODULE_DIR = Path(__file__).resolve().parent
_PROP_PROJECT_ROOT = _PROP_MODULE_DIR.parent

def install(g: dict, root: tk.Misc) -> tk.Misc:
    """Build this page and return its root frame."""
    globals().update(g)
    lock_g = {"_selection_locks": {}, "_selection_lock_vars": []}
    export_prop_dir = g.get("EXPORT_PROP", BASE / "prop")
    prop_page = tk.Frame(root, bg="#FAFAF7")
    g["prop_page"] = prop_page
    prop_name_var = tk.StringVar(value="")
    g["prop_name_var"] = prop_name_var
    prop_job_running = [False]
    prop_make_btn = [None]
    auto_prop_btn = [None]
    auto_prop_running = [False]
    auto_prop_stop = [False]
    prop_ref_image = [None]
    prop_ref_preview_photo = [None]
    prop_ref_paste_busy = [False]
    prop_ref_last_paste = {"sig": None, "time": 0.0}
    prop_3d_asset_type = [""]
    prop_ref_name_hint = [""]
    main_hunyuan_running = [False]
    main_hunyuan_btn = [None]
    main_hunyuan_status = tk.StringVar(value="Hunyuan 1 ว่าง")
    hunyuan_quota_text = tk.StringVar(value="เครดิต —")
    hunyuan_quota_loading = [False]
    texture_ref_image = [None]
    texture_ref_photo = [None]
    texture_base_image = [None]
    texture_running = [False]
    texture_type_var = tk.StringVar(value="ผ้า")
    texture_detail_var = tk.StringVar(value="")
    texture_tiling_var = tk.StringVar(value="30")
    extra_hunyuan_slots = [
        {
            "path": "", "name": "", "asset_type": "", "running": False,
            "photo": None, "thumb": None, "info": None, "status": None,
            "button": None,
        }
        for _ in range(1)
    ]
    triposplat_running = [False]
    triposplat_btn = [None]
    triposplat_progress_var = tk.DoubleVar(value=0)
    triposplat_progress_text = tk.StringVar(value="3D พร้อม")
    trellis2_running = [False]
    trellis2_btn = [None]
    trellis2_last_glb = [None]
    trellis2_progress_var = triposplat_progress_var
    trellis2_progress_text = triposplat_progress_text
    progress_log_last = {"TripoSplat": None, "TRELLIS.2": None}

    def _configured_3d_resolution(key, default):
        try:
            cfg = g.get("load_config", lambda: {})() or {}
            value = int(cfg.get(key, default))
            return value if value in (512, 768, 1024) else default
        except Exception:
            return default

    def _notify_done():
        notify = g.get("_snapgen_notify_done")
        if callable(notify):
            try:
                notify()
            except Exception:
                pass

    prop_box = tk.LabelFrame(prop_page, text="📦 Prop", bg="#FAFAF7", fg="#1A1A1A", padx=10, pady=8)
    prop_box.pack(fill="x", padx=10, pady=10)
    prop_row = tk.Frame(prop_box, bg="#FAFAF7")
    prop_row.pack(fill="x")
    tk.Label(prop_row, text="ชื่อ:", bg="#FAFAF7", fg="#333").pack(side="left")
    prop_entry_wrap = tk.Frame(prop_row, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D1D5DB")
    prop_entry_wrap.pack(side="left", fill="x", expand=True, padx=6)
    prop_entry = tk.Entry(prop_entry_wrap, textvariable=prop_name_var, relief="flat", bg="#FFFFFF", fg="#111")
    prop_entry.pack(fill="x", padx=8, pady=6)
    prop_placeholder = tk.Label(prop_entry_wrap, text="ใส่ชื่อ", bg="#FFFFFF", fg="#B0B0B0", font=(SNAPGEN_UI_FONT, 9))
    prop_placeholder.place(x=10, y=6)

    # Category dropdown (5 types — affects cat_hint injected into prompt)
    prop_category_var = tk.StringVar(value="อัตโนมัติ")
    g["prop_category_var"] = prop_category_var
    _PROP_CATEGORIES = ("อัตโนมัติ", "เสื้อผ้า", "อาหาร", "ทั่วไป", "สัตว์")
    _PROP_CAT_HINTS = {
        "อัตโนมัติ": "",
        "เสื้อผ้า": "Clothing / garment prop. Show fabric texture, stitching detail, fold and drape. ",
        "อาหาร": "Food prop. Show fresh texture, natural color, appetizing presentation. ",
        "ทั่วไป": "General everyday object prop. Show material detail, surface texture. ",
        "สัตว์": "Animal prop. Show natural fur/skin/feather texture, lifelike pose. ",
    }
    g["_PROP_CAT_HINTS"] = _PROP_CAT_HINTS
    tk.Label(prop_row, text="หมวด:", bg="#FAFAF7", fg="#333").pack(side="left", padx=(8, 0))
    prop_cat_menu = tk.OptionMenu(prop_row, prop_category_var, *_PROP_CATEGORIES)
    prop_cat_menu.config(relief="flat", bg="#FFFFFF", fg="#111", font=(SNAPGEN_UI_FONT, 9), highlightthickness=1, highlightbackground="#D1D5DB")
    prop_cat_menu.pack(side="left", padx=4)
    g["prop_cat_menu"] = prop_cat_menu
    g["_prop_selected_context"] = {}
    def _sync_prop_placeholder(*_):
        try:
            if prop_name_var.get().strip(): prop_placeholder.place_forget()
            else: prop_placeholder.place(x=10, y=6)
        except Exception:
            pass
    prop_name_var.trace_add("write", _sync_prop_placeholder)
    prop_entry.bind("<FocusIn>", lambda _e: _sync_prop_placeholder(), add="+")

    # Prop Select — opens character/prop list from prompt_ref_context.json
    def _prop_item_name(item):
        if isinstance(item, dict):
            for key in ("name", "ชื่อ", "item", "prop", "object"):
                val = str(item.get(key, "")).strip()
                if val:
                    return val
            return ""
        return str(item).strip()

    def _prop_item_detail(item):
        if isinstance(item, dict):
            parts = []
            for key in ("description", "รายละเอียด", "note", "ลักษณะ", "material", "วัสดุ", "color", "สี", "usage", "การใช้งาน"):
                val = str(item.get(key, "")).strip()
                if val and val.lower() != "ไม่ระบุ":
                    parts.append(f"{key}: {val}")
            return "; ".join(parts)
        return str(item).strip()

    def _extract_context_props(text):
        try:
            import json as _json
            ctx = _json.loads(text)
            props = ctx.get("props", []) if isinstance(ctx, dict) else []
        except Exception:
            props = []
        out = []
        seen = set()
        for item in props:
            name = _prop_item_name(item)
            if not name or name.lower() == "ไม่ระบุ" or name in seen:
                continue
            seen.add(name)
            out.append((name, item))
        return out

    def _apply_prop_character(item, selector=None):
        # item comes from props[] only
        if isinstance(item, dict):
            name = _prop_item_name(item)
        else:
            name = str(item).strip()
        prop_name_var.set(name)
        if name:
            g["_prop_selected_context"][name] = _prop_item_detail(item)
        _prop_log(_builder_set_selection_lock(lock_g, "prop", name))
        if selector is not None:
            selector.destroy()

    def _open_prop_selector():
        # Pull from `props` array in prompt_ref_context.json
        try:
            import json as _json
            context_text = _load_ref_context()
            ctx = _json.loads(context_text)
        except Exception:
            ctx = {}
        items = _extract_context_props(context_text)
        if not items:
            _prop_log("[Select] ไม่พบรายการ prop ใน prompt_ref_context.json (props[] ว่าง)")
            return
        selector = tk.Toplevel(root)
        selector.title("เลือก Prop — จาก Context")
        selector.configure(bg="#FAFAF7")
        selector.resizable(False, False)
        selector.transient(root)
        tk.Label(selector, text=f"เลือก Prop ({len(items)} รายการ)", bg="#FAFAF7", fg="#111", font=(SNAPGEN_UI_FONT, 11, "bold")).pack(fill="x", padx=14, pady=(12, 6))
        for name, item in items:
            detail = _prop_item_detail(item)
            label = name + (f"  —  {detail[:80]}" if detail and detail != name else "")
            tk.Button(selector, text=label, anchor="w", command=lambda c=item: _apply_prop_character(c, selector), bg="#FFFFFF", fg="#111", activebackground="#E0E7FF", activeforeground="#111", relief="flat", bd=0, padx=12, pady=8).pack(fill="x", padx=12, pady=3)
        selector.grab_set()

    prop_select_btn = tk.Button(prop_row, text="Select", command=_open_prop_selector, bg="#2563EB", fg="white", activebackground="#1D4ED8", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    g["prop_select_btn"] = prop_select_btn

    # ── Optional visual reference attachment ───────────────────────────────
    # ใช้เวลาต้องการให้ Prop/3D อิงจากรูปตัวอย่าง เช่น แคปจอแล้ววางเลย
    prop_ref_header = tk.Frame(prop_box, bg="#FAFAF7")
    tk.Label(
        prop_ref_header, text="รูปแนบตัวอย่าง", bg="#FAFAF7", fg="#1A1A1A",
        font=(SNAPGEN_UI_FONT, 9),
    ).pack(side="left")
    hunyuan_quota_label = tk.Label(
        prop_ref_header, textvariable=hunyuan_quota_text,
        bg="#FAFAF7", fg="#9CA3AF", cursor="hand2",
        font=(SNAPGEN_UI_FONT, 9), padx=8,
    )
    hunyuan_quota_label.pack(side="right")
    prop_ref_box = tk.LabelFrame(
        prop_box, labelwidget=prop_ref_header,
        bg="#FAFAF7", fg="#1A1A1A", padx=8, pady=6,
    )
    prop_ref_box.pack(fill="x", pady=(8, 0))

    def _fit_prop_ref_header(_event=None):
        # LabelFrame does not stretch labelwidget automatically on Windows.
        # Match the inner frame width so quota stays at the far-right corner,
        # still inside the border and never over either Hunyuan slot.
        try:
            prop_ref_header.configure(width=max(prop_ref_box.winfo_width() - 24, 1), height=22)
            prop_ref_header.pack_propagate(False)
        except Exception:
            pass

    prop_ref_box.bind("<Configure>", _fit_prop_ref_header, add="+")

    def _refresh_hunyuan_quota():
        if hunyuan_quota_loading[0]:
            return
        hunyuan_quota_loading[0] = True
        hunyuan_quota_text.set("เครดิต …")

        def worker():
            text = "เครดิต —"
            try:
                from snapgen_modules.hunyuan3d import Hunyuan3DClient
                cookie = str(_PROP_PROJECT_ROOT / "snapgen_data" / "hunyuan_cookies.txt")
                quota = Hunyuan3DClient(cookie_file=cookie).daily_quota()
                remain = int(quota.get("remainQuota", 0))
                text = f"เครดิต {remain}"
            except Exception:
                text = "เครดิต —"

            def finish():
                hunyuan_quota_loading[0] = False
                hunyuan_quota_text.set(text)
            try:
                root.after(0, finish)
            except Exception:
                pass

    def _ensure_3d_model(model_name):
        """Install missing per-machine weights before running selected 3D tool."""
        import snapgen_3d_model_manager as manager

        info = manager.model_info(model_name)
        if info["installed"]:
            return True

        def report(text):
            root.after(0, lambda value=str(text): _prop_log(f"[{model_name}] {value}"))

        report("เครื่องนี้ยังไม่มีโมเดลครบ — กำลังติดตั้งอัตโนมัติ")
        manager.install_model(model_name, report)
        if not manager.model_info(model_name)["installed"]:
            raise RuntimeError(f"ติดตั้ง {model_name} แล้วยังพบไฟล์ไม่ครบ")
        report("ติดตั้งเสร็จ — เริ่มสร้าง 3D ต่อ")
        return True

        threading.Thread(target=worker, daemon=True).start()

    hunyuan_quota_label.bind("<Button-1>", lambda _event: _refresh_hunyuan_quota())
    g["refresh_hunyuan_quota"] = _refresh_hunyuan_quota
    root.after(250, _refresh_hunyuan_quota)
    prop_ref_row = tk.Frame(prop_ref_box, bg="#FAFAF7")
    prop_ref_row.pack(fill="x")
    # UI rule: Prop cards must keep equal width, height, preview size, and
    # button rows. Add new controls inside these rows; never grow one card alone.
    for slot_column in range(3):
        prop_ref_row.grid_columnconfigure(slot_column, weight=1, uniform="prop_ref_slots")
    prop_ref_row.grid_rowconfigure(0, weight=1)
    main_slot_frame = tk.Frame(
        prop_ref_row, bg="#FFFFFF",
        highlightthickness=1, highlightbackground="#E5E7EB",
        height=140,
    )
    main_slot_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
    main_slot_frame.grid_propagate(False)
    main_slot_content = tk.Frame(main_slot_frame, bg="#FFFFFF")
    main_slot_content.pack(fill="x", padx=6, pady=4)
    prop_ref_thumb = tk.Label(
        main_slot_content,
        text="วางรูป / เลือกไฟล์",
        bg="#FFFFFF",
        fg="#6B7280",
        width=18,
        height=6,
        relief="solid",
        bd=1,
        anchor="center",
    )
    prop_ref_thumb.pack(side="left", anchor="n", padx=(0, 8), pady=2)
    prop_ref_info = tk.StringVar(value="ยังไม่มีรูปแนบ")
    prop_ref_controls = tk.Frame(main_slot_content, bg="#FFFFFF")
    prop_ref_controls.pack(side="left", fill="x", expand=True)
    main_status_row = tk.Frame(prop_ref_controls, bg="#FFFFFF")
    main_status_row.pack(fill="x", pady=(0, 4))
    tk.Label(
        main_status_row, textvariable=main_hunyuan_status,
        bg="#FFFFFF", fg="#6D28D9", anchor="w",
        font=(SNAPGEN_UI_FONT, 8, "bold"),
    ).pack(side="left")
    tk.Label(
        main_status_row, text=" · ", bg="#FFFFFF", fg="#9CA3AF",
        font=(SNAPGEN_UI_FONT, 8),
    ).pack(side="left")
    tk.Label(
        main_status_row, textvariable=prop_ref_info,
        bg="#FFFFFF", fg="#374151", anchor="w",
        font=(SNAPGEN_UI_FONT, 8),
    ).pack(side="left", fill="x", expand=True)
    main_paste_row = tk.Frame(prop_ref_controls, bg="#FFFFFF")
    main_paste_row.pack(fill="x", pady=(0, 3))
    for attach_column in range(2):
        main_paste_row.grid_columnconfigure(attach_column, weight=1, uniform="hunyuan_main_attach")
    main_action_row = tk.Frame(prop_ref_controls, bg="#FFFFFF")
    main_action_row.pack(fill="x")
    for action_column in range(2):
        main_action_row.grid_columnconfigure(action_column, weight=1, uniform="hunyuan_main_actions")

    def _set_prop_ref_image(path, garment_type="", name_hint=""):
        p = Path(str(path or "")).expanduser()
        if not p.exists() or not p.is_file():
            _prop_log(f"[แนบรูป] ไม่พบไฟล์: {p}")
            return
        old_name = Path(prop_ref_image[0]).name if prop_ref_image[0] else ""
        if old_name:
            _builder_remove_selection_lock(lock_g, "reference", old_name)
        prop_ref_image[0] = str(p)
        prop_3d_asset_type[0] = str(garment_type or "").strip()
        prop_ref_name_hint[0] = str(name_hint or p.stem).strip()
        prop_ref_info.set(p.name)
        main_hunyuan_status.set("Hunyuan 1 พร้อม")
        # A newly selected source is a new job. Clear the previous model's
        # completed progress immediately instead of leaving a stale 100% bar.
        triposplat_progress_var.set(0)
        triposplat_progress_text.set("3D พร้อม — 0%")
        progress_log_last["TripoSplat"] = None
        progress_log_last["TRELLIS.2"] = None
        try:
            from PIL import Image, ImageTk
            im = Image.open(p)
            im.thumbnail((150, 110), Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
            prop_ref_preview_photo[0] = photo
            prop_ref_thumb.config(image=photo, text="", width=150, height=110)
        except Exception:
            prop_ref_thumb.config(image="", text=p.name[:28], width=18, height=6)
        _prop_log(_builder_set_selection_lock(lock_g, "reference", p.name, append=True))

    def _set_extra_hunyuan_slot(slot_index, path, name_hint="", garment_type=""):
        slot = extra_hunyuan_slots[slot_index]
        p = Path(str(path or "")).expanduser()
        if not p.is_file():
            _prop_log(f"[Hunyuan สล็อต {slot_index + 2}] ไม่พบไฟล์: {p}")
            return False
        slot["path"] = str(p)
        slot["name"] = str(name_hint or p.stem).strip()
        slot["asset_type"] = str(garment_type or "").strip()
        slot["status"].set(f"Hunyuan {slot_index + 2} พร้อม")
        slot["info"].set(f"{p.name}" + (f" → {slot['asset_type']}" if slot["asset_type"] else ""))
        try:
            from PIL import Image, ImageTk
            image = Image.open(p)
            image.thumbnail((150, 110), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            slot["photo"] = photo
            slot["thumb"].config(image=photo, text="", width=150, height=110)
        except Exception:
            slot["photo"] = None
            slot["thumb"].config(image="", text=p.name[:28], width=18, height=6)
        return True

    def _receive_story_prop(path, name="", garment_type=""):
        """Receive one Story asset image, then show Prop for optional 3D."""
        p = Path(str(path or ""))
        if not p.is_file():
            _prop_log(f"[นิทาน → Prop] ไม่พบไฟล์: {p}")
            return False
        if not garment_type:
            sidecar = Path(str(p) + ".garment.json")
            if sidecar.is_file():
                try:
                    garment_type = str(
                        json.loads(sidecar.read_text(encoding="utf-8")).get("garment_type") or ""
                    ).strip()
                except (OSError, json.JSONDecodeError):
                    garment_type = ""
        incoming_name = str(name or p.stem).strip()
        prop_name_var.set(incoming_name)
        # Story outfits keep the clothing category; generic Story scene assets
        # arrive without garment_type and should stay ordinary props for 3D.
        prop_category_var.set("เสื้อผ้า" if garment_type else "ทั่วไป")
        if not prop_ref_image[0]:
            _set_prop_ref_image(p, garment_type, incoming_name)
            slot_number = 1
        else:
            slot_index = next(
                (index for index, slot in enumerate(extra_hunyuan_slots) if not slot["path"]),
                None,
            )
            if slot_index is None:
                _prop_log("[นิทาน → Prop] สล็อต Hunyuan เต็ม 2 ช่อง — ล้างช่องที่เสร็จแล้วก่อน")
                return False
            _set_extra_hunyuan_slot(slot_index, p, incoming_name, garment_type)
            slot_number = slot_index + 2
        show = g.get("show_prop_mode")
        if callable(show):
            root.after(0, show)
        type_text = f" → {garment_type}" if garment_type else ""
        _prop_log(f"[นิทาน → Prop] Hunyuan สล็อต {slot_number}: {p.name}{type_text}")
        return True

    g["receive_story_prop"] = _receive_story_prop

    def _paste_prop_ref_image():
        if prop_ref_paste_busy[0]:
            return
        prop_ref_paste_busy[0] = True
        try:
            from PIL import ImageGrab, Image
            clip = ImageGrab.grabclipboard()
            if clip is None:
                _prop_log("[แนบรูป] clipboard ไม่มีรูป")
                return
            attach_dir = Path(export_prop_dir) / "_attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            now = time.time()
            if isinstance(clip, Image.Image):
                sample = clip.convert("RGB").resize((16, 16))
                sig = ("image", clip.size, clip.mode, sample.tobytes())
                if prop_ref_last_paste["sig"] == sig and now - prop_ref_last_paste["time"] < 1.0:
                    return
                prop_ref_last_paste.update({"sig": sig, "time": now})
                out = attach_dir / f"prop_ref_clip_{time.strftime('%Y%m%d-%H%M%S')}.png"
                clip.convert("RGBA").save(out)
                _set_prop_ref_image(out)
                return
            if isinstance(clip, (list, tuple)):
                for item in clip:
                    src = Path(str(item))
                    if src.exists() and src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                        sig = ("file", str(src.resolve()), src.stat().st_mtime_ns, src.stat().st_size)
                        if prop_ref_last_paste["sig"] == sig and now - prop_ref_last_paste["time"] < 1.0:
                            return
                        prop_ref_last_paste.update({"sig": sig, "time": now})
                        out = attach_dir / src.name
                        if src.resolve() != out.resolve():
                            shutil.copy2(src, out)
                        _set_prop_ref_image(out)
                        return
            _prop_log("[แนบรูป] clipboard ไม่ใช่รูปที่ใช้ได้")
        except Exception as e:
            _prop_log(f"[แนบรูป] วางรูปไม่สำเร็จ: {e}")
        finally:
            prop_ref_paste_busy[0] = False

    def _choose_prop_ref_image():
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                title="เลือกรูปแนบตัวอย่าง Prop",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")]
            )
            if path:
                _set_prop_ref_image(path)
        except Exception as e:
            _prop_log(f"[แนบรูป] เลือกไฟล์ไม่สำเร็จ: {e}")

    def _clear_prop_ref_image():
        if main_hunyuan_running[0]:
            _prop_log("[Hunyuan สล็อต 1] กำลังสร้าง — ยังล้างไม่ได้")
            return
        old_name = Path(prop_ref_image[0]).name if prop_ref_image[0] else ""
        prop_ref_image[0] = None
        prop_3d_asset_type[0] = ""
        prop_ref_name_hint[0] = ""
        prop_ref_preview_photo[0] = None
        prop_ref_info.set("ยังไม่มีรูปแนบ")
        main_hunyuan_status.set("Hunyuan 1 ว่าง")
        prop_ref_thumb.config(image="", text="วางรูป / เลือกไฟล์", width=18, height=6)
        _builder_remove_selection_lock(lock_g, "reference", old_name)
        _prop_log("[แนบรูป] ล้างรูปแนบแล้ว")

    def _run_3d_from_prop_ref():
        if main_hunyuan_running[0]:
            _prop_log("[Hunyuan สล็อต 1] กำลังสร้างอยู่")
            return
        if not prop_ref_image[0]:
            _prop_log("[3D] ยังไม่มีรูปแนบ")
            return
        name_hint = prop_ref_name_hint[0] or prop_name_var.get().strip() or prop_ref_image[0]
        asset_type = prop_3d_asset_type[0]
        source = prop_ref_image[0]
        main_hunyuan_running[0] = True
        main_hunyuan_status.set("Hunyuan 1 กำลังสร้าง...")
        if main_hunyuan_btn[0]:
            main_hunyuan_btn[0].config(text="กำลังสร้าง...", state=tk.DISABLED)

        def worker():
            result = None
            try:
                _prop_log(f"[Hunyuan สล็อต 1] เริ่มส่ง: {Path(source).name}")
                result = _run_prop_3d(source, name_hint=name_hint, asset_type=asset_type)
            finally:
                main_hunyuan_running[0] = False
                def finish():
                    main_hunyuan_status.set("Hunyuan 1 เสร็จ" if result else "Hunyuan 1 ผิดพลาด")
                    if main_hunyuan_btn[0]:
                        main_hunyuan_btn[0].config(text="Hunyuan", state=tk.NORMAL)
                    _refresh_hunyuan_quota()
                root.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def _run_extra_hunyuan_slot(slot_index):
        slot = extra_hunyuan_slots[slot_index]
        if slot["running"]:
            return
        if not slot["path"]:
            _prop_log(f"[Hunyuan สล็อต {slot_index + 2}] ยังไม่มีรูป")
            return
        source, name_hint, asset_type = slot["path"], slot["name"], slot["asset_type"]
        slot["running"] = True
        slot["status"].set(f"Hunyuan {slot_index + 2} กำลังสร้าง...")
        slot["button"].config(text="กำลังสร้าง...", state=tk.DISABLED)

        def worker():
            result = None
            try:
                _prop_log(f"[Hunyuan สล็อต {slot_index + 2}] เริ่มส่ง: {Path(source).name}")
                result = _run_prop_3d(source, name_hint=name_hint, asset_type=asset_type)
            finally:
                slot["running"] = False
                def finish():
                    slot["status"].set(
                        f"Hunyuan {slot_index + 2} " + ("เสร็จ" if result else "ผิดพลาด")
                    )
                    slot["button"].config(text="Hunyuan", state=tk.NORMAL)
                    _refresh_hunyuan_quota()
                root.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def _clear_extra_hunyuan_slot(slot_index):
        slot = extra_hunyuan_slots[slot_index]
        if slot["running"]:
            _prop_log(f"[Hunyuan สล็อต {slot_index + 2}] กำลังสร้าง — ยังล้างไม่ได้")
            return
        slot.update({"path": "", "name": "", "asset_type": "", "photo": None})
        slot["info"].set("ยังไม่มีรูป")
        slot["status"].set(f"Hunyuan {slot_index + 2} ว่าง")
        slot["thumb"].config(image="", text="วางรูป / เลือกไฟล์", width=18, height=6)

    def _choose_extra_hunyuan_slot(slot_index):
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                title=f"เลือกรูป Hunyuan สล็อต {slot_index + 2}",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")],
            )
            if path:
                _set_extra_hunyuan_slot(slot_index, path, Path(path).stem, "")
        except Exception as exc:
            _prop_log(f"[Hunyuan สล็อต {slot_index + 2}] เลือกไฟล์ไม่สำเร็จ: {exc}")

    def _paste_extra_hunyuan_slot(slot_index):
        try:
            from PIL import ImageGrab, Image
            clip = ImageGrab.grabclipboard()
            attach_dir = Path(export_prop_dir) / "_attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            if isinstance(clip, Image.Image):
                out = attach_dir / f"prop_ref_slot{slot_index + 2}_{time.strftime('%Y%m%d-%H%M%S')}.png"
                clip.convert("RGBA").save(out)
                _set_extra_hunyuan_slot(slot_index, out, out.stem, "")
                return
            if isinstance(clip, (list, tuple)):
                source = next((
                    Path(str(item)) for item in clip
                    if Path(str(item)).is_file()
                    and Path(str(item)).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
                ), None)
                if source:
                    _set_extra_hunyuan_slot(slot_index, source, source.stem, "")
                    return
            _prop_log(f"[Hunyuan สล็อต {slot_index + 2}] clipboard ไม่มีรูป")
        except Exception as exc:
            _prop_log(f"[Hunyuan สล็อต {slot_index + 2}] วางรูปไม่สำเร็จ: {exc}")

    def _set_texture_ref(path):
        p = Path(str(path or "")).expanduser()
        if not p.is_file():
            _prop_log(f"[Texture] ไม่พบรูป: {p}")
            return False
        texture_ref_image[0] = str(p)
        texture_ref_info.set(p.name)
        try:
            from PIL import Image, ImageTk
            image = Image.open(p)
            image.thumbnail((150, 110), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            texture_ref_photo[0] = photo
            texture_ref_thumb.config(image=photo, text="", width=150, height=110)
        except Exception:
            texture_ref_photo[0] = None
            texture_ref_thumb.config(image="", text=p.name[:22], width=18, height=6)
        return True

    def _paste_texture_ref():
        try:
            from PIL import ImageGrab, Image
            clip = ImageGrab.grabclipboard()
            attach_dir = Path(export_prop_dir) / "_attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            if isinstance(clip, Image.Image):
                out = attach_dir / f"texture_ref_{time.strftime('%Y%m%d-%H%M%S')}.png"
                clip.convert("RGB").save(out)
                _set_texture_ref(out)
                return
            if isinstance(clip, (list, tuple)):
                source = next((Path(str(item)) for item in clip if Path(str(item)).is_file()), None)
                if source and _set_texture_ref(source):
                    return
            _prop_log("[Texture] clipboard ไม่มีรูป")
        except Exception as exc:
            _prop_log(f"[Texture] วางรูปไม่สำเร็จ: {exc}")

    def _choose_texture_ref():
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                title="เลือกรูปอ้างอิง Texture",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")],
            )
            if path:
                _set_texture_ref(path)
        except Exception as exc:
            _prop_log(f"[Texture] เลือกไฟล์ไม่สำเร็จ: {exc}")

    def _clear_texture_ref():
        texture_ref_image[0] = None
        texture_ref_photo[0] = None
        texture_base_image[0] = None
        texture_ref_info.set("Texture · ยังไม่มีรูป")
        texture_ref_thumb.config(
            image="", text="Texture\nวางรูป / เลือกไฟล์", width=18, height=6
        )
        _prop_log("[Texture] ล้างรูปแล้ว")

    def _texture_prompt():
        kind = texture_type_var.get().strip() or "กำหนดเอง"
        detail = texture_detail_var.get().strip() or f"พื้นผิว{kind}สมจริง"
        return (
            f"Create one production-ready seamless tileable PBR base-color texture. Material category: {kind}. "
            f"Requested surface: {detail}. Perfectly flat orthographic surface scan, square 1:1, evenly lit, "
            "high-frequency material detail, no perspective, no object silhouette, no folds caused by an object shape, "
            "no border, no frame, no text, no watermark, no directional shadow, no highlight hotspot. "
            "The left edge must continue exactly into the right edge and the top edge exactly into the bottom edge. "
            "It must tile repeatedly in all directions without visible seams or dark lines. Base Color only; do not output "
            "a normal-map purple image, metallic map, roughness map, bump map, or multi-panel contact sheet."
        )

    def _create_pbr_maps(base_path):
        base = Path(base_path)
        kind = texture_type_var.get().strip()
        out_dir = base.parent / f"{base.stem}_PBR"
        out_dir.mkdir(parents=True, exist_ok=True)
        tool_dir = _PROP_PROJECT_ROOT / "snapgen_data" / "tools" / "stable_normal"
        python_exe = tool_dir / ".venv" / "Scripts" / "python.exe"
        runner = _PROP_MODULE_DIR / "snapgen_stable_normal_pbr.py"
        source_dir = tool_dir / "source"
        if not (source_dir / "hubconf.py").is_file():
            _prop_log("[PBR AI] กำลังดาวน์โหลด StableNormal source")
            source_dir.parent.mkdir(parents=True, exist_ok=True)
            clone = subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/Stable-X/StableNormal.git", str(source_dir)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if clone.returncode != 0 or not (source_dir / "hubconf.py").is_file():
                raise RuntimeError("ดาวน์โหลด StableNormal source ไม่สำเร็จ")
        if not python_exe.is_file():
            _prop_log("[PBR AI] ติดตั้งครั้งแรก — กำลังสร้างระบบ StableNormal แยก")
            tool_dir.mkdir(parents=True, exist_ok=True)
            create = subprocess.run(
                [sys.executable, "-m", "venv", str(tool_dir / ".venv")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if create.returncode != 0:
                raise RuntimeError("สร้างระบบ StableNormal ไม่สำเร็จ")
        ready = tool_dir / ".ready"
        expected_ready = "StableNormal-turbo-v2"
        try:
            installed_ready = ready.read_text(encoding="utf-8-sig").strip()
        except OSError:
            installed_ready = ""
        if installed_ready != expected_ready:
            _prop_log("[PBR AI] กำลังติดตั้ง PyTorch GPU และ StableNormal — ทำครั้งเดียว")
            packages = (
                [str(python_exe), "-m", "pip", "install", "--upgrade", "pip"],
                [str(python_exe), "-m", "pip", "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu128"],
                [str(python_exe), "-m", "pip", "install", "diffusers==0.30.3", "transformers==4.44.2", "huggingface-hub==0.24.7", "accelerate==0.34.2", "einops", "scipy", "safetensors", "Pillow", "numpy"],
            )
            for command in packages:
                install = subprocess.run(
                    command, cwd=str(tool_dir), capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if install.returncode != 0:
                    raise RuntimeError("ติดตั้ง StableNormal ไม่สำเร็จ: " + (install.stdout + install.stderr)[-500:])
            # Dependency installs can silently replace CUDA torch with CPU torch.
            cuda_check = subprocess.run(
                [str(python_exe), "-c", "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)"],
                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if cuda_check.returncode != 0:
                raise RuntimeError("StableNormal ติดตั้งแล้วแต่ไม่พบ NVIDIA GPU/CUDA")
            ready.write_text(expected_ready + "\n", encoding="utf-8")
        command = [
            str(python_exe), "-B", str(runner), "--input", str(base),
            "--output-dir", str(out_dir), "--kind", kind,
            "--tool-dir", str(tool_dir),
        ]
        process = subprocess.Popen(
            command, cwd=str(_PROP_PROJECT_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        results = []
        for line in iter(process.stdout.readline, ""):
            line = line.strip()
            if line.startswith("PROGRESS|"):
                _tag, percent, detail = line.split("|", 2)
                _prop_log(f"[PBR AI] {percent}% — {detail}")
            elif line.startswith("RESULT|"):
                results.append(Path(line.split("|", 1)[1]))
            elif line:
                _prop_log("[PBR AI] " + line[-300:])
        code = process.wait()
        if code != 0 or len(results) != 4 or not all(path.is_file() for path in results):
            raise RuntimeError(f"StableNormal สร้าง PBR ไม่สำเร็จ (code {code})")
        return results

    def _make_seamless_base(source_path):
        """Build a deterministic 2x2 mirrored tile; opposite edges match."""
        from PIL import Image, ImageOps
        source = Path(source_path)
        image = Image.open(source).convert("RGB")
        side = min(image.size)
        left = (image.width - side) // 2
        top = (image.height - side) // 2
        tile = image.crop((left, top, left + side, top + side)).resize((512, 512), Image.LANCZOS)
        small = tile.resize((256, 256), Image.LANCZOS)
        seamless = Image.new("RGB", (512, 512))
        seamless.paste(small, (0, 0))
        seamless.paste(ImageOps.mirror(small), (256, 0))
        seamless.paste(ImageOps.flip(small), (0, 256))
        seamless.paste(ImageOps.flip(ImageOps.mirror(small)), (256, 256))
        out = source.with_name(f"{source.stem}_seamless.png")
        seamless.save(out)
        return out

    def _generate_texture():
        if texture_running[0]:
            return
        texture_running[0] = True
        texture_generate_btn.config(state=tk.DISABLED, text="กำลังสร้าง...")

        def worker():
            try:
                prompt = _texture_prompt()
                payload = {
                    "model": "auto", "prompt": prompt, "n": 1,
                    "aspect_ratio": "1:1", "history_and_training_disabled": True,
                    "_temporary_chat": True,
                }
                ref = texture_ref_image[0]
                if ref and Path(ref).is_file():
                    encoder = g.get("_encode_image_b64") or globals().get("_encode_image_b64")
                    if callable(encoder):
                        payload["images"] = [encoder(ref)]
                name = texture_detail_var.get().strip() or texture_type_var.get().strip() or "texture"
                out_dir = Path(export_prop_dir) / "textures"
                out = g["_do_image_request"](
                    payload, is_edit=bool(payload.get("images")), prompt=prompt,
                    name_hint=name, raw_prompt=prompt, output_dir=str(out_dir),
                )
                seamless = _make_seamless_base(out)
                texture_base_image[0] = str(seamless)
                root.after(0, lambda out=seamless: (_prop_log(f"[Texture] ✓ Seamless Base Color: {out}"), _prop_gallery_add(str(out), True, is_texture=True), _notify_done()))
            except Exception as exc:
                root.after(0, lambda exc=exc: _prop_log(f"[Texture] ERROR: {exc}"))
            finally:
                texture_running[0] = False
                root.after(0, lambda: texture_generate_btn.config(state=tk.NORMAL, text="สร้าง Texture"))
        threading.Thread(target=worker, daemon=True).start()

    def _generate_texture_map(map_name):
        if not texture_base_image[0] or not Path(texture_base_image[0]).is_file():
            _prop_log("[Texture] สร้าง Base Color ก่อน")
            return
        try:
            outputs = _create_pbr_maps(texture_base_image[0])
            selected = {"Normal/Bump": outputs[1], "Metallic": outputs[2], "Roughness": outputs[3]}[map_name]
            _prop_gallery_add(str(selected), True, is_texture=True)
            _prop_log(f"[Texture] ✓ {map_name}: {selected}")
        except Exception as exc:
            _prop_log(f"[Texture] สร้าง {map_name} ไม่สำเร็จ: {exc}")

    def _generate_all_texture_maps(source_path=None):
        selected_source = source_path or texture_base_image[0]
        if not selected_source or not Path(selected_source).is_file():
            _prop_log("[Texture] สร้าง Base Color ก่อน")
            return
        def worker():
            try:
                outputs = _create_pbr_maps(selected_source)
                root.after(0, lambda outputs=outputs: (
                    [_prop_gallery_add(str(output), True, is_texture=True) for output in outputs[1:]],
                    _prop_log(f"[Texture] ✓ PBR AI ครบ: {outputs[1].parent}"), _notify_done(),
                ))
            except Exception as exc:
                root.after(0, lambda exc=exc: _prop_log(f"[Texture] สร้าง PBR AI ไม่สำเร็จ: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _convert_texture_tiling(source_path=None, repeat_value=None):
        """Bake selected repeat count into one texture image; never run 3D."""
        selected_source = source_path or texture_base_image[0]
        if not selected_source or not Path(selected_source).is_file():
            _prop_log("[Texture] สร้าง Texture ก่อน")
            return
        try:
            from PIL import Image
            repeat = int(repeat_value or texture_tiling_var.get())
            source = Path(selected_source)
            image = Image.open(source).convert("RGB")
            output_size = 2048
            tile_size = max(1, (output_size + repeat - 1) // repeat)
            tile = image.resize((tile_size, tile_size), Image.LANCZOS)
            output = Image.new("RGB", (output_size, output_size))
            for y in range(0, output_size, tile_size):
                for x in range(0, output_size, tile_size):
                    output.paste(tile, (x, y))
            output_path = source.with_name(f"{source.stem}_tiling{repeat}.png")
            output.save(output_path)
            _prop_gallery_add(str(output_path), True, is_texture=True)
            _prop_log(f"[Texture] ✓ Tiling {repeat}: {output_path}")
        except Exception as exc:
            _prop_log(f"[Texture] แปลง Tiling ไม่สำเร็จ: {exc}")

    def _run_triposplat_glb_from_prop_ref():
        if triposplat_running[0] or trellis2_running[0]:
            _prop_log("[3D] กำลังทำงานอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        if not prop_ref_image[0]:
            _prop_log("[TripoSplat] ยังไม่มีรูปแนบ")
            return
        name_hint = prop_ref_name_hint[0] or prop_name_var.get().strip() or prop_ref_image[0]
        triposplat_running[0] = True
        triposplat_progress_var.set(0)
        triposplat_progress_text.set("TripoSplat 0%")
        progress_log_last["TripoSplat"] = None
        _draw_3d_progress()
        root.update_idletasks()
        if triposplat_btn[0]:
            triposplat_btn[0].config(text="กำลังทำ...", state=tk.DISABLED)
        if trellis2_btn[0]:
            trellis2_btn[0].config(state=tk.DISABLED)
        def worker():
            result = None
            try:
                _ensure_3d_model("TripoSplat")
                result = _run_triposplat_glb(
                    prop_ref_image[0], name_hint=name_hint,
                    asset_type=prop_3d_asset_type[0],
                )
            except Exception as exc:
                _prop_log(f"[TripoSplat] ติดตั้งอัตโนมัติ/สร้าง 3D ไม่สำเร็จ: {exc}")
            finally:
                triposplat_running[0] = False
                def finish_ui():
                    if triposplat_btn[0]:
                        triposplat_btn[0].config(text="TripoSplat", state=tk.NORMAL)
                    if trellis2_btn[0]:
                        trellis2_btn[0].config(state=tk.NORMAL)
                    if not result:
                        triposplat_progress_text.set(
                            f"ไม่สำเร็จ — หยุดที่ {int(triposplat_progress_var.get())}%"
                        )
                root.after(0, finish_ui)
        threading.Thread(target=worker, daemon=True).start()

    def _run_trellis2_glb(image_path, name_hint="", asset_type=""):
        tool_dir = _PROP_PROJECT_ROOT / "snapgen_data" / "tools" / "trellis2"
        runner = _PROP_MODULE_DIR / "snapgen_trellis2_direct.py"
        source = Path(str(image_path or "")).resolve()
        if not source.is_file():
            _prop_log(f"[TRELLIS.2] ไม่พบรูป: {source}")
            return None
        name = _clean_prop_3d_name(name_hint or source)
        out_dir = export_prop_dir / "3d" / name / "trellis2"
        out_dir.mkdir(parents=True, exist_ok=True)
        trellis_python = next((path for path in (
            tool_dir / "code" / "venv" / "Scripts" / "python.exe",
            tool_dir / "python" / "python.exe",
        ) if path.is_file()), tool_dir / "python" / "python.exe")
        if not trellis_python.is_file():
            _prop_log("[TRELLIS.2] ยังไม่ได้ติดตั้ง Python ของ TRELLIS.2")
            return None
        command = [
            str(trellis_python), "-B", str(runner), "--input", str(source),
            "--output", str(out_dir), "--tool-dir", str(tool_dir),
            "--resolution", str(_configured_3d_resolution("trellis2_resolution", 1024)),
        ]
        if asset_type:
            command.extend(["--asset-name", asset_type])
        try:
            process = subprocess.Popen(
                command, cwd=str(_PROP_PROJECT_ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            results = []
            error_detail = ""
            for line in iter(process.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("PROGRESS|"):
                    _tag, percent, text = line.split("|", 2)
                    _set_trellis2_progress(percent, text)
                elif line.startswith("RESULT|"):
                    results.append(Path(line.split("|", 1)[1]))
                else:
                    if line.startswith("ERROR:"):
                        error_detail = line.split(":", 1)[1].strip()
                    _prop_log(f"[TRELLIS.2] {line}")
            code = process.wait()
            if code != 0 or not results:
                reason = f": {error_detail}" if error_detail else ""
                _prop_log(f"[TRELLIS.2] ERROR: สร้าง 3D ไม่สำเร็จ{reason} (code {code})")
                return None
            glb = next((path for path in results if path.suffix.lower() == ".glb"), None)
            fbx = next((path for path in results if path.suffix.lower() == ".fbx"), None)
            if glb:
                trellis2_last_glb[0] = str(glb)
                _prop_log(f"[TRELLIS.2] ✓ GLB: {glb}")
            if fbx:
                _prop_log(f"[TRELLIS.2] ✓ FBX สำหรับ iClone: {fbx}")
            selected = fbx or glb
            subprocess.Popen(["explorer", "/select,", str(selected)])
            _notify_done()
            return str(selected)
        except Exception as e:
            _set_trellis2_progress(0, "ไม่สำเร็จ")
            _prop_log(f"[TRELLIS.2] ERROR: {e}")
            return None

    def _run_trellis2_glb_from_prop_ref():
        if trellis2_running[0] or triposplat_running[0]:
            _prop_log("[3D] กำลังทำงานอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        if not prop_ref_image[0]:
            _prop_log("[Trellis2] ยังไม่มีรูปแนบ")
            return
        trellis2_running[0] = True
        trellis2_progress_var.set(0)
        trellis2_progress_text.set("TRELLIS.2 0%")
        progress_log_last["TRELLIS.2"] = None
        _draw_3d_progress()
        root.update_idletasks()
        if trellis2_btn[0]:
            trellis2_btn[0].config(text="กำลังทำ...", state=tk.DISABLED)
        if triposplat_btn[0]:
            triposplat_btn[0].config(state=tk.DISABLED)

        def worker():
            try:
                _ensure_3d_model("TRELLIS.2")
                name_hint = prop_ref_name_hint[0] or prop_name_var.get().strip() or prop_ref_image[0]
                _run_trellis2_glb(
                    prop_ref_image[0], name_hint=name_hint,
                    asset_type=prop_3d_asset_type[0],
                )
            except Exception as exc:
                _prop_log(f"[TRELLIS.2] ติดตั้งอัตโนมัติ/สร้าง 3D ไม่สำเร็จ: {exc}")
            finally:
                trellis2_running[0] = False
                def finish_ui():
                    if trellis2_btn[0]:
                        trellis2_btn[0].config(text="TRELLIS.2", state=tk.NORMAL)
                    if triposplat_btn[0]:
                        triposplat_btn[0].config(state=tk.NORMAL)
                root.after(0, finish_ui)

        threading.Thread(target=worker, daemon=True).start()

    tk.Button(main_paste_row, text="📋 วางรูป", command=_paste_prop_ref_image, bg="#475569", fg="white", relief="flat", pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).grid(row=0, column=0, sticky="ew", padx=(0, 2))
    tk.Button(main_paste_row, text="📎 เลือกไฟล์", command=_choose_prop_ref_image, bg="#475569", fg="white", relief="flat", pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).grid(row=0, column=1, sticky="ew", padx=(2, 0))
    main_hunyuan_btn[0] = tk.Button(main_action_row, text="Hunyuan", command=_run_3d_from_prop_ref, bg="#059669", fg="white", activebackground="#10B981", relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"))
    main_hunyuan_btn[0].grid(row=0, column=0, sticky="ew", padx=(0, 2))
    tk.Button(
        main_action_row, text="ล้าง", command=_clear_prop_ref_image,
        bg="#DC2626", fg="white", activebackground="#B91C1C",
        activeforeground="white", disabledforeground="white",
        relief="flat", bd=0, padx=12, pady=6,
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).grid(row=0, column=1, sticky="ew", padx=(2, 0))

    extra_slots_box = tk.Frame(prop_ref_row, bg="#FAFAF7", height=140)
    extra_slots_box.grid(row=0, column=1, sticky="nsew", padx=3)
    extra_slots_box.grid_propagate(False)
    for slot_index, slot in enumerate(extra_hunyuan_slots):
        row = tk.Frame(
            extra_slots_box, bg="#FFFFFF",
            highlightthickness=1, highlightbackground="#E5E7EB",
            height=140,
        )
        row.pack(fill="both", expand=True)
        row.pack_propagate(False)
        slot_content = tk.Frame(row, bg="#FFFFFF")
        slot_content.pack(fill="x", padx=6, pady=4)
        slot["thumb"] = tk.Label(
            slot_content, text="วางรูป / เลือกไฟล์", bg="#FFFFFF", fg="#6B7280",
            width=18, height=6, relief="solid", bd=1,
        )
        slot["thumb"].pack(side="left", anchor="n", padx=(0, 8), pady=2)
        text_box = tk.Frame(slot_content, bg="#FFFFFF")
        text_box.pack(side="left", fill="x", expand=True)
        slot["info"] = tk.StringVar(value="ยังไม่มีรูป")
        slot["status"] = tk.StringVar(value=f"Hunyuan {slot_index + 2} ว่าง")
        status_row = tk.Frame(text_box, bg="#FFFFFF")
        status_row.pack(fill="x", pady=(0, 4))
        tk.Label(
            status_row, textvariable=slot["status"], bg="#FFFFFF", fg="#6D28D9",
            anchor="w", font=(SNAPGEN_UI_FONT, 8, "bold"),
        ).pack(side="left")
        tk.Label(
            status_row, text=" · ", bg="#FFFFFF", fg="#9CA3AF",
            font=(SNAPGEN_UI_FONT, 8),
        ).pack(side="left")
        tk.Label(
            status_row, textvariable=slot["info"], bg="#FFFFFF", fg="#374151",
            anchor="w", font=(SNAPGEN_UI_FONT, 8),
        ).pack(side="left", fill="x", expand=True)
        paste_row = tk.Frame(text_box, bg="#FFFFFF")
        paste_row.pack(fill="x", pady=(0, 3))
        for attach_column in range(2):
            paste_row.grid_columnconfigure(attach_column, weight=1, uniform="hunyuan_extra_attach")
        action_row = tk.Frame(text_box, bg="#FFFFFF")
        action_row.pack(fill="x")
        for action_column in range(2):
            action_row.grid_columnconfigure(action_column, weight=1, uniform="hunyuan_extra_actions")
        tk.Button(
            paste_row, text="📋 วางรูป", command=lambda i=slot_index: _paste_extra_hunyuan_slot(i),
            bg="#475569", fg="white", relief="flat", padx=12, pady=6,
            font=(SNAPGEN_UI_FONT, 9, "bold"),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        tk.Button(
            paste_row, text="📎 เลือกไฟล์", command=lambda i=slot_index: _choose_extra_hunyuan_slot(i),
            bg="#475569", fg="white", relief="flat", padx=12, pady=6,
            font=(SNAPGEN_UI_FONT, 9, "bold"),
        ).grid(row=0, column=1, sticky="ew", padx=(2, 0))
        slot["button"] = tk.Button(
            action_row, text="Hunyuan", command=lambda i=slot_index: _run_extra_hunyuan_slot(i),
            bg="#059669", activebackground="#10B981", fg="white",
            relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"),
        )
        slot["button"].grid(row=0, column=0, sticky="ew", padx=(0, 2))
        tk.Button(
            action_row, text="ล้าง", command=lambda i=slot_index: _clear_extra_hunyuan_slot(i),
            bg="#DC2626", activebackground="#B91C1C", fg="white",
            relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"),
        ).grid(row=0, column=1, sticky="ew", padx=(2, 0))

    texture_slot = tk.Frame(
        prop_ref_row, bg="#FFFFFF",
        highlightthickness=1, highlightbackground="#E5E7EB",
        height=140,
    )
    texture_slot.grid(row=0, column=2, sticky="nsew", padx=(3, 0))
    texture_slot.grid_propagate(False)
    texture_content = tk.Frame(texture_slot, bg="#FFFFFF")
    texture_content.pack(fill="x", padx=6, pady=4)
    texture_ref_thumb = tk.Label(
        texture_content, text="Texture\nวางรูป / เลือกไฟล์",
        bg="#FFFFFF", fg="#6B7280", width=18, height=6,
        relief="solid", bd=1, justify="center",
    )
    texture_ref_thumb.pack(side="left", anchor="n", padx=(0, 8), pady=2)
    texture_controls = tk.Frame(texture_content, bg="#FFFFFF")
    texture_controls.pack(side="left", fill="x", expand=True)
    texture_ref_info = tk.StringVar(value="Texture · ยังไม่มีรูป")
    tk.Label(
        texture_controls, textvariable=texture_ref_info,
        bg="#FFFFFF", fg="#374151", anchor="w",
        font=(SNAPGEN_UI_FONT, 8, "bold"),
    ).pack(fill="x", pady=(0, 3))
    texture_options = tk.Frame(texture_controls, bg="#FFFFFF")
    texture_options.pack(fill="x", pady=(0, 3))
    texture_types = ("ผ้า", "ไม้", "โลหะ", "หิน", "หนัง", "พลาสติก", "ธรรมชาติ", "กำหนดเอง")
    texture_menu = tk.OptionMenu(texture_options, texture_type_var, *texture_types)
    texture_menu.config(relief="flat", bg="#F3F4F6", fg="#111827", font=(SNAPGEN_UI_FONT, 8), highlightthickness=0, width=7)
    texture_menu.pack(side="left", padx=(0, 3))
    tk.Entry(texture_options, textvariable=texture_detail_var, relief="solid", bd=1, font=(SNAPGEN_UI_FONT, 8)).pack(side="left", fill="x", expand=True)
    texture_attach_row = tk.Frame(texture_controls, bg="#FFFFFF")
    texture_attach_row.pack(fill="x", pady=(0, 3))
    for attach_column in range(2):
        texture_attach_row.grid_columnconfigure(attach_column, weight=1, uniform="texture_attach")
    tk.Button(texture_attach_row, text="📋 วางรูป", command=_paste_texture_ref, bg="#475569", fg="white", relief="flat", pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).grid(row=0, column=0, sticky="ew", padx=(0, 2))
    tk.Button(texture_attach_row, text="📎 เลือกไฟล์", command=_choose_texture_ref, bg="#475569", fg="white", relief="flat", pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).grid(row=0, column=1, sticky="ew", padx=(2, 0))
    texture_action_row = tk.Frame(texture_controls, bg="#FFFFFF")
    texture_action_row.pack(fill="x")
    for action_column in range(2):
        texture_action_row.grid_columnconfigure(action_column, weight=1, uniform="texture_main")
    texture_generate_btn = tk.Button(texture_action_row, text="สร้าง Texture", command=_generate_texture, bg="#059669", fg="white", relief="flat", pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"))
    texture_generate_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))
    tk.Button(texture_action_row, text="ล้าง", command=_clear_texture_ref, bg="#DC2626", activebackground="#B91C1C", fg="white", relief="flat", pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).grid(row=0, column=1, sticky="ew", padx=(2, 0))

    def _install_prop_image_drop(widget, setter, slot_name):
        try:
            vendor = _PROP_PROJECT_ROOT / "vendor"
            if str(vendor) not in sys.path:
                sys.path.insert(0, str(vendor))
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD.require(root)
            allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

            def dropped(event):
                files = root.tk.splitlist(event.data)
                source = next((Path(item) for item in files if Path(item).suffix.lower() in allowed), None)
                widget.configure(highlightthickness=0)
                if source is None or not source.is_file():
                    _prop_log("[ลากรูป] รับเฉพาะไฟล์รูป PNG/JPG/WEBP/BMP")
                else:
                    setter(source)
                    _prop_log(f"[ลากรูป] ใส่{slot_name}: {source.name}")
                return event.action

            widget.drop_target_register(DND_FILES)
            widget.dnd_bind(
                "<<DropEnter>>",
                lambda event: (widget.configure(highlightthickness=2, highlightbackground="#2563EB"), event.action)[1],
            )
            widget.dnd_bind(
                "<<DropLeave>>",
                lambda event: (widget.configure(highlightthickness=0), event.action)[1],
            )
            widget.dnd_bind("<<Drop>>", dropped)
        except Exception as exc:
            _prop_log(f"[ลากรูป] เปิดใช้ {slot_name} ไม่ได้: {exc}")

    _install_prop_image_drop(
        prop_ref_thumb,
        lambda path: _set_prop_ref_image(path, name_hint=Path(path).stem),
        "สล็อต 1",
    )
    for drop_slot_index, drop_slot in enumerate(extra_hunyuan_slots):
        _install_prop_image_drop(
            drop_slot["thumb"],
            lambda path, index=drop_slot_index: _set_extra_hunyuan_slot(index, path, Path(path).stem, ""),
            f"สล็อต {drop_slot_index + 2}",
        )
    _install_prop_image_drop(texture_ref_thumb, _set_texture_ref, "สล็อต Texture")

    local_tools_row = tk.Frame(prop_ref_box, bg="#FAFAF7")
    local_tools_row.pack(fill="x", pady=(6, 0))
    tk.Label(
        local_tools_row, text="3D ในเครื่อง:", bg="#FAFAF7", fg="#374151",
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(0, 6))
    triposplat_btn[0] = tk.Button(local_tools_row, text="TripoSplat", command=_run_triposplat_glb_from_prop_ref, bg="#059669", fg="white", activebackground="#10B981", relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"))
    triposplat_btn[0].pack(side="left", padx=4)
    trellis2_btn[0] = tk.Button(local_tools_row, text="TRELLIS.2", command=_run_trellis2_glb_from_prop_ref, bg="#059669", fg="white", activebackground="#10B981", relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"))
    trellis2_btn[0].pack(side="left", padx=4)
    triposplat_progress_row = tk.Frame(prop_ref_box, bg="#FAFAF7")
    triposplat_progress_row.pack(fill="x", pady=(6, 0))
    # ttk on Windows ignores custom progress colors under some native themes.
    # Canvas keeps the trough unchanged and guarantees the moving fill is purple.
    triposplat_progress_bar = tk.Canvas(
        triposplat_progress_row,
        height=20,
        bg="#BDB9B0",
        highlightthickness=1,
        highlightbackground="#A8A49C",
        bd=0,
    )
    triposplat_progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def _draw_3d_progress(*_):
        triposplat_progress_bar.delete("all")
        width = max(0, triposplat_progress_bar.winfo_width() - 2)
        height = max(2, triposplat_progress_bar.winfo_height() - 2)
        triposplat_progress_bar.create_rectangle(
            1, 1, width + 1, height + 1,
            fill="#BDB9B0", outline="", tags="trough",
        )
        fill_width = width * max(0.0, min(100.0, triposplat_progress_var.get())) / 100.0
        if fill_width > 0:
            triposplat_progress_bar.create_rectangle(
                1, 1, fill_width + 1, height + 1,
                fill="#6D28D9", outline="", tags="fill",
            )

    triposplat_progress_bar.bind("<Configure>", _draw_3d_progress)
    triposplat_progress_var.trace_add("write", _draw_3d_progress)
    root.after_idle(_draw_3d_progress)
    tk.Label(
        triposplat_progress_row,
        textvariable=triposplat_progress_text,
        bg="#FAFAF7",
        fg="#5B21B6",
        width=30,
        anchor="w",
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left")
    g["prop_ref_image"] = prop_ref_image

    def _set_triposplat_progress(percent, text):
        def apply():
            value = max(0, min(100, int(float(percent))))
            triposplat_progress_var.set(value)
            triposplat_progress_text.set(f"TripoSplat {value}%")
            current = (value, text)
            if progress_log_last["TripoSplat"] != current:
                progress_log_last["TripoSplat"] = current
                _prop_log(f"[TripoSplat] {value}% — {text}")
        root.after(0, apply)

    def _set_trellis2_progress(percent, text):
        def apply():
            value = max(0, min(100, int(float(percent))))
            trellis2_progress_var.set(value)
            trellis2_progress_text.set(f"TRELLIS.2 {value}%")
            current = (value, text)
            if progress_log_last["TRELLIS.2"] != current:
                progress_log_last["TRELLIS.2"] = current
                _prop_log(f"[TRELLIS.2] {value}% — {text}")
        root.after(0, apply)

    prop_log_widget = _builder_make_log_box(prop_box)
    prop_log_widget.pack(fill="x", pady=(8, 0))
    g["prop_log_box"] = prop_log_widget
    def _prop_log(msg):
        _builder_append_log(prop_log_widget, msg)
    g["_prop_log"] = _prop_log

    prop_gallery = tk.LabelFrame(prop_page, text="แกลเลอรี", bg="#FAFAF7", fg="#1A1A1A", padx=8, pady=6)
    prop_gallery.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    prop_gallery_canvas = tk.Canvas(prop_gallery, bg="#FAFAF7", highlightthickness=0)
    prop_gallery_scroll = tk.Scrollbar(prop_gallery, orient="vertical", command=prop_gallery_canvas.yview)
    prop_gallery_canvas.configure(yscrollcommand=prop_gallery_scroll.set)
    prop_gallery_inner = tk.Frame(prop_gallery_canvas, bg="#FAFAF7")
    prop_gallery_inner.bind("<Configure>", lambda e: prop_gallery_canvas.configure(scrollregion=prop_gallery_canvas.bbox("all")))
    _pg_window = prop_gallery_canvas.create_window((0, 0), window=prop_gallery_inner, anchor="nw")
    prop_gallery_canvas.pack(side="left", fill="both", expand=True)
    prop_gallery_scroll.pack(side="right", fill="y")
    g["prop_gallery_inner"] = prop_gallery_inner
    prop_gallery_images = []
    g["prop_gallery_images"] = prop_gallery_images
    prop_gallery_state_path = Path(BASE) / "meta" / "prop_gallery.json"
    prop_gallery_paths = []
    prop_gallery_cards = []

    def _save_prop_gallery():
        prop_gallery_state_path.parent.mkdir(parents=True, exist_ok=True)
        prop_gallery_state_path.write_text(
            json.dumps({"paths": prop_gallery_paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _relayout_prop_gallery():
        for item in prop_gallery_cards:
            try:
                item.pack_forget()
                item.pack(fill="x", pady=2)
            except Exception:
                pass
        try:
            prop_gallery_canvas.yview_moveto(0)
            root.after_idle(_pg_sync)
        except Exception:
            pass

    def _pg_sync(_e=None):
        prop_gallery_canvas.configure(scrollregion=prop_gallery_canvas.bbox("all") or (0, 0, 0, 0))
        try:
            prop_gallery_canvas.itemconfigure(_pg_window, width=max(prop_gallery_canvas.winfo_width() - 4, 1))
        except Exception:
            pass

    def _pg_on_mousewheel(event):
        try:
            if not prop_gallery_canvas.winfo_exists():
                return
            first, last = prop_gallery_canvas.yview()
            if float(last) - float(first) >= 0.999:
                return
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return
            prop_gallery_canvas.yview_scroll(int(-1 * (delta / 120)), "units")
            return "break"
        except Exception:
            return

    def _pg_bind_wheel(widget):
        try:
            widget.bind("<Enter>", lambda _e: prop_gallery_canvas.bind_all("<MouseWheel>", _pg_on_mousewheel), add="+")
            widget.bind("<Leave>", lambda _e: prop_gallery_canvas.unbind_all("<MouseWheel>"), add="+")
            widget.bind("<MouseWheel>", _pg_on_mousewheel, add="+")
        except Exception:
            pass

    prop_gallery_canvas.bind("<Configure>", _pg_sync)
    for _w in (prop_gallery, prop_gallery_canvas, prop_gallery_inner, prop_gallery_scroll):
        _pg_bind_wheel(_w)

    # ── Prop 3D: Hunyuan 3D integration (image → 3D mesh) ──────────────
    prop_3d_dir = export_prop_dir / "3d"
    prop_3d_dir.mkdir(parents=True, exist_ok=True)
    g["prop_3d_dir"] = prop_3d_dir

    def _clean_prop_3d_name(text):
        import re
        name = os.path.splitext(os.path.basename(str(text or "")))[0]
        name = re.sub(r"^\d{8}[-_]\d{6}[-_]*", "", name)
        name = re.sub(r"[_-]slow2x$", "", name, flags=re.I)
        name = re.sub(r"[^0-9A-Za-zก-๙ ]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
        return (name[:40].strip() or "simple object")

    def _build_safe_hunyuan_prop_prompt(name_hint):
        name = _clean_prop_3d_name(name_hint)
        return (
            f"a single {name} object, isolated centered product photo, "
            "plain white background, full object visible, simple realistic material, no text"
        )

    def _run_prop_3d(image_path, name_hint="", asset_type=""):
        """Send image to Hunyuan 3D → download GLB + preview into prop_3d/<name>/."""
        try:
            # Import by the full module path so this page can never pick up a
            # stale hunyuan3d.pyc or another same-named module from sys.path.
            from snapgen_modules.hunyuan3d import Hunyuan3DClient, Hunyuan3DError
            # BASE from the recovered app already points at snapgen_data.
            # Resolve from this module instead so the path cannot become
            # snapgen_data/snapgen_data when the runtime environment changes.
            cookie = str(_PROP_PROJECT_ROOT / "snapgen_data" / "hunyuan_cookies.txt")
            client = Hunyuan3DClient(cookie_file=cookie)
            source = str(image_path or "").strip()
            out_name = _clean_prop_3d_name(name_hint or image_path)
            out_dir = prop_3d_dir / out_name
            out_dir.mkdir(parents=True, exist_ok=True)
            tool_dir = _PROP_PROJECT_ROOT / "snapgen_data" / "tools" / "rmbg2"
            remover = _PROP_MODULE_DIR / "snapgen_rmbg2_remove.py"
            from snapgen_rmbg2_runtime import ensure_runtime
            python_exe, model_dir = ensure_runtime(
                lambda text: _prop_log(str(text)[-500:])
            )
            foreground = out_dir / "background_removed.png"
            _prop_log("[Hunyuan] กำลังลบพื้นหลังด้วย RMBG 2.0")
            remove = subprocess.Popen(
                [str(python_exe), "-B", str(remover), "--input", source,
                 "--output", str(foreground), "--model-dir", str(model_dir)],
                cwd=str(tool_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in iter(remove.stdout.readline, ""):
                line = line.strip()
                if line.startswith("PROGRESS|"):
                    _tag, percent, text = line.split("|", 2)
                    _prop_log(f"[Hunyuan] {percent}% — {text}")
                elif line.startswith("ERROR|"):
                    _prop_log(f"[Hunyuan] {line.split('|', 1)[1]}")
            if remove.wait() != 0 or not foreground.is_file():
                raise RuntimeError("RMBG 2.0 ลบพื้นหลังไม่สำเร็จ")
            _prop_log(f"[Hunyuan] ส่งรูปโปร่งใส: {foreground.name}")
            # hunyuan3d.py owns the complete local-file/URL flow so every
            # caller behaves the same and this page does not duplicate it.
            task_id = client.image_to_3d(str(foreground))
            _prop_log(f"[3D] task_id: {task_id} — รอประมวลผล...")
            result = client.wait_for_task(task_id, timeout=600)
            saved = client.download_result(result, out_dir=str(out_dir), formats=["fbx"])
            if asset_type and (saved.get("glb") or saved.get("fbx")):
                blender = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe")
                converter = _PROP_MODULE_DIR / "snapgen_blender_glb_to_fbx.py"
                source_3d = Path(saved.get("glb") or saved["fbx"])
                old_fbx = Path(saved["fbx"]) if saved.get("fbx") else None
                named_fbx = out_dir / f"{asset_type}.fbx"
                converted = subprocess.run(
                    [str(blender), "--background", "--python", str(converter), "--",
                     str(source_3d), str(named_fbx), asset_type],
                    capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if converted.returncode != 0 or not named_fbx.is_file():
                    raise RuntimeError(f"ตั้งชื่อ 3D เป็น {asset_type} ไม่สำเร็จ")
                if source_3d.suffix.lower() == ".glb":
                    named_glb = out_dir / f"{asset_type}.glb"
                    if source_3d != named_glb:
                        if named_glb.exists():
                            named_glb.unlink()
                        source_3d.replace(named_glb)
                    saved["glb"] = str(named_glb)
                if old_fbx and old_fbx != named_fbx and old_fbx.exists():
                    old_fbx.unlink()
                saved["fbx"] = str(named_fbx)
            _prop_log(f"[3D] ✓ สำเร็จ: {saved}")
            # Open output folder
            subprocess.Popen(["explorer", str(out_dir)])
            return saved
        except Exception as e:
            _prop_log(f"[3D] ERROR: {e}")
            return None
    g["_run_prop_3d"] = _run_prop_3d

    def _run_triposplat_glb(image_path, name_hint="", asset_type=""):
        """Run isolated TripoSplat tool; never route through Hunyuan or ComfyUI UI."""
        tool_dir = _PROP_PROJECT_ROOT / "snapgen_data" / "tools" / "triposplat"
        python_exe = tool_dir / ".venv" / "Scripts" / "python.exe"
        runner = _PROP_MODULE_DIR / "snapgen_triposplat_glb.py"
        model_dir = tool_dir / "ckpts"
        converter = tool_dir / "source" / "snapgen_splat_to_glb.py"
        bg_model = tool_dir / "bg_models" / "RMBG-2.0" / "model.safetensors"
        bg_downloader = _PROP_MODULE_DIR / "snapgen_download_rmbg2.py"
        required_models = (
            model_dir / "diffusion_models" / "triposplat_fp16.safetensors",
            model_dir / "vae" / "triposplat_vae_decoder_fp16.safetensors",
            model_dir / "clip_vision" / "dino_v3_vit_h.safetensors",
            model_dir / "vae" / "flux2-vae.safetensors",
            model_dir / "background_removal" / "birefnet.safetensors",
        )
        missing = []
        if not python_exe.is_file():
            missing.append("Python แยกของ TripoSplat")
        if not runner.is_file():
            missing.append("ตัวทำงาน TripoSplat → GLB")
        if not converter.is_file():
            missing.append("ตัวแปลง Splat → GLB")
        missing_models = [p.name for p in required_models if not p.is_file()]
        if missing_models:
            missing.append(f"โมเดล {len(missing_models)} ไฟล์")
        if missing:
            _prop_log("[TripoSplat] ยังไม่พร้อม: " + ", ".join(missing))
            _prop_log(f"[TripoSplat] ตำแหน่งติดตั้ง: {tool_dir}")
            return None

        if not bg_downloader.is_file():
            _prop_log("[TripoSplat] ไม่พบตัวดาวน์โหลด RMBG 2.0")
            return None
        if not bg_model.is_file():
            _prop_log("[TripoSplat] เครื่องนี้ยังไม่มี RMBG 2.0 — เริ่มดาวน์โหลดครั้งแรก")
        else:
            _prop_log("[TripoSplat] กำลังตรวจ RMBG 2.0")
        try:
            download_process = subprocess.Popen(
                [str(python_exe), "-B", str(bg_downloader), str(bg_model)],
                cwd=str(tool_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in iter(download_process.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("DOWNLOAD|"):
                    try:
                        _tag, percent, text = line.split("|", 2)
                        _set_triposplat_progress(percent, text)
                    except ValueError:
                        _prop_log(f"[TripoSplat] {line}")
                elif line.startswith("ERROR|"):
                    _prop_log("[TripoSplat] " + line.split("|", 1)[1])
                else:
                    _prop_log(f"[TripoSplat] {line}")
            if download_process.wait() != 0 or not bg_model.is_file():
                _prop_log("[TripoSplat] ERROR: ติดตั้ง RMBG 2.0 ไม่สำเร็จ")
                return None
            _set_triposplat_progress(0, "ดาวน์โหลดเสร็จ — กำลังเริ่มสร้าง 3D")
        except Exception as e:
            _prop_log(f"[TripoSplat] ERROR: ตรวจ/ดาวน์โหลด RMBG 2.0 ไม่สำเร็จ: {e}")
            return None

        source = Path(str(image_path or "")).resolve()
        if not source.is_file():
            _prop_log(f"[TripoSplat] ไม่พบรูป: {source}")
            return None
        out_name = _clean_prop_3d_name(name_hint or source)
        out_dir = prop_3d_dir / out_name / "triposplat"
        out_dir.mkdir(parents=True, exist_ok=True)
        _prop_log(f"[TripoSplat] เริ่มสร้าง GLB: {out_name}")
        command = [
            str(python_exe), "-B", str(runner),
            "--input", str(source),
            "--output", str(out_dir),
            "--mesh-resolution", str(_configured_3d_resolution("triposplat_resolution", 768)),
        ]
        if asset_type:
            command.extend(["--asset-name", asset_type])
        try:
            process = subprocess.Popen(
                command,
                cwd=str(tool_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in iter(process.stdout.readline, ""):
                line = line.strip()
                if line:
                    if line.startswith("PROGRESS|"):
                        try:
                            _tag, percent, text = line.split("|", 2)
                            _set_triposplat_progress(percent, text)
                        except ValueError:
                            _prop_log(f"[TripoSplat] {line}")
                    else:
                        _prop_log(f"[TripoSplat] {line}")
            code = process.wait()
            glbs = sorted(out_dir.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True)
            if code != 0 or not glbs:
                _prop_log(f"[TripoSplat] ERROR: สร้าง GLB ไม่สำเร็จ (code {code})")
                return None
            fbxs = sorted(out_dir.glob("*.fbx"), key=lambda p: p.stat().st_mtime, reverse=True)
            splats = sorted(out_dir.glob("*.splat"), key=lambda p: p.stat().st_mtime, reverse=True)
            plys = sorted(out_dir.glob("*.ply"), key=lambda p: p.stat().st_mtime, reverse=True)
            if splats:
                _prop_log(f"[TripoSplat] ✓ Native Splat คุณภาพต้นฉบับ: {splats[0]}")
            if plys:
                _prop_log(f"[TripoSplat] ✓ Native PLY คุณภาพต้นฉบับ: {plys[0]}")
            _prop_log(f"[TripoSplat] ✓ GLB: {glbs[0]}")
            _prop_log("[TripoSplat] หมายเหตุ: GLB/FBX เป็น Mesh ที่แปลงจาก Splat รายละเอียดอาจต่ำกว่า viewer เว็บ")
            if fbxs:
                _prop_log(f"[TripoSplat] ✓ FBX สำหรับ iClone: {fbxs[0]}")
            _set_triposplat_progress(100, "เสร็จแล้ว")
            selected = fbxs[0] if fbxs else glbs[0]
            subprocess.Popen(["explorer", "/select,", str(selected)])
            _notify_done()
            return str(selected)
        except Exception as e:
            _prop_log(f"[TripoSplat] ERROR: {e}")
            return None
    g["_run_triposplat_glb"] = _run_triposplat_glb

    def _prop_gallery_add(path, prepend=True, is_texture=False, persist=True):
        path = str(Path(path))
        if persist and path not in prop_gallery_paths:
            prop_gallery_paths.insert(0 if prepend else len(prop_gallery_paths), path)
            _save_prop_gallery()
        card = tk.Frame(prop_gallery_inner, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E5E7EB")
        thumb_box = tk.Frame(card, bg="#FFFFFF", width=96, height=96)
        thumb_box.pack(side="left", padx=6, pady=6)
        thumb_box.pack_propagate(False)
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((96, 96), Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
            prop_gallery_images.append(photo)
            tk.Label(thumb_box, image=photo, bg="#FFFFFF").pack(expand=True)
        except Exception:
            tk.Label(thumb_box, text="ไม่มี preview", bg="#FFFFFF", fg="#9CA3AF", wraplength=80).pack(expand=True)
        detail = tk.Frame(card, bg="#FFFFFF")
        detail.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        tk.Label(detail, text=os.path.basename(path), bg="#FFFFFF", fg="#111", anchor="w").pack(fill="x")
        comment_row = tk.Frame(detail, bg="#FFFFFF")
        comment_row.pack(fill="x", pady=(6, 0))
        comment_var = tk.StringVar()
        comment_entry = tk.Entry(
            comment_row, textvariable=comment_var, relief="solid", bd=1,
            font=(SNAPGEN_UI_FONT, 9),
        )
        comment_entry.pack(side="left", fill="x", expand=True)

        def _edit_from_comment(p=path, texture_output=is_texture):
            comment = comment_var.get().strip()
            if not comment:
                _prop_log("[แก้รูป] พิมพ์สิ่งที่ต้องการแก้ก่อน")
                return
            if not Path(p).is_file():
                _prop_log(f"[แก้รูป] ไม่พบรูป: {p}")
                return
            comment_entry.config(state=tk.DISABLED)
            edit_btn.config(state=tk.DISABLED, text="กำลังแก้...")

            def worker():
                try:
                    encoder = g.get("_encode_image_b64") or globals().get("_encode_image_b64")
                    if not callable(encoder):
                        raise RuntimeError("ไม่พบตัวเข้ารหัสรูป")
                    prompt = (
                        "แก้ไขรูปที่แนบมาโดยยึดวัตถุเดิม รูปร่างเดิม มุมกล้องเดิม "
                        "พื้นหลังเดิม และรายละเอียดอื่นทั้งหมดไว้ เปลี่ยนเฉพาะตามคำสั่งนี้: "
                        + comment
                    )
                    payload = {
                        "model": "auto", "prompt": prompt, "n": 1,
                        "aspect_ratio": "1:1",
                        "history_and_training_disabled": bool(texture_output),
                        "images": [encoder(p)],
                    }
                    if texture_output:
                        payload["_temporary_chat"] = True
                    else:
                        payload["_use_prop_history"] = True
                    out_dir = Path(export_prop_dir) / ("textures" if texture_output else "")
                    out = g["_do_image_request"](
                        payload, is_edit=True, prompt=prompt,
                        name_hint=f"{Path(p).stem}_แก้ไข", raw_prompt=comment,
                        output_dir=str(out_dir),
                    )
                    root.after(0, lambda out=out: (
                        _prop_gallery_add(str(out), True, is_texture=texture_output),
                        _prop_log(f"[แก้รูป] ✓ {out}"), _notify_done(),
                    ))
                except Exception as exc:
                    root.after(0, lambda exc=exc: _prop_log(f"[แก้รูป] ERROR: {exc}"))
                finally:
                    root.after(0, lambda: (
                        comment_entry.config(state=tk.NORMAL),
                        edit_btn.config(state=tk.NORMAL, text="ส่งแก้ไข"),
                    ))

            threading.Thread(target=worker, daemon=True).start()

        edit_btn = tk.Button(
            comment_row, text="ส่งแก้ไข", command=_edit_from_comment,
            bg="#059669", activebackground="#10B981", fg="white",
            relief="flat", font=(SNAPGEN_UI_FONT, 8, "bold"),
        )
        edit_btn.pack(side="left", padx=(4, 0))
        comment_entry.bind("<Return>", lambda _event: _edit_from_comment(), add="+")
        tools = tk.Frame(card, bg="#FFFFFF")
        tools.pack(side="right", padx=4, pady=4)
        tk.Button(
            tools, text="📂 เปิด",
            command=lambda p=path: subprocess.Popen(["explorer", "/select,", p]),
        ).pack(side="right", padx=(4, 0))
        if is_texture:
            # Texture post-process tools belong to each finished gallery item,
            # never to the input slot. Convert this exact output, not stale state.
            card_tiling_var = tk.StringVar(value="30")
            tk.Button(
                tools, text="สร้าง PBR",
                command=lambda p=path: _generate_all_texture_maps(p),
                bg="#6D28D9", activebackground="#7C3AED", fg="white",
                relief="flat", pady=3, font=(SNAPGEN_UI_FONT, 8, "bold"),
            ).pack(side="right", padx=(4, 0))
            tk.Button(
                tools, text="แปลง",
                command=lambda p=path, v=card_tiling_var: _convert_texture_tiling(p, v.get()),
                bg="#2563EB", activebackground="#1D4ED8", fg="white",
                relief="flat", pady=3, font=(SNAPGEN_UI_FONT, 8, "bold"),
            ).pack(side="right", padx=(4, 0))
            tiling_menu = tk.OptionMenu(tools, card_tiling_var, "5", "30", "50")
            tiling_menu.config(
                relief="flat", bg="#F3F4F6", fg="#111827", width=3,
                highlightthickness=0, font=(SNAPGEN_UI_FONT, 8),
            )
            tiling_menu.pack(side="right")
            tk.Label(
                tools, text="Tiling", bg="#FFFFFF", fg="#6B7280",
                font=(SNAPGEN_UI_FONT, 8),
            ).pack(side="right", padx=(0, 4))
        else:
            # Gallery never starts a 3D job. Fill the first empty source slot
            # without replacing an image the user already placed there.
            def _send_to_3d_slot(p=path):
                name_hint = os.path.splitext(os.path.basename(p))[0]
                if not prop_ref_image[0]:
                    _set_prop_ref_image(p, name_hint=name_hint)
                    slot_number = 1
                else:
                    slot_index = next(
                        (index for index, slot in enumerate(extra_hunyuan_slots) if not slot["path"]),
                        None,
                    )
                    if slot_index is None:
                        _prop_log("[3D] สล็อตเต็มทั้ง 2 ช่อง — ล้างสล็อตที่ไม่ใช้ก่อน")
                        return
                    _set_extra_hunyuan_slot(slot_index, p, name_hint=name_hint)
                    slot_number = slot_index + 2
                _prop_log(
                    f"[3D] ส่งเข้าสล็อต {slot_number}: {os.path.basename(p)} — "
                    "เลือกปุ่มสร้างจากสล็อตนั้น"
                )
                try:
                    prop_gallery_canvas.yview_moveto(0.0)
                except Exception:
                    pass
            tk.Button(
                tools, text="🧊 ส่งไปสล็อต 3D", command=_send_to_3d_slot,
                bg="#2563EB", fg="white", activebackground="#1D4ED8",
                activeforeground="white", relief="flat", bd=0, padx=8, pady=4,
                font=(SNAPGEN_UI_FONT, 8, "bold"),
            ).pack(side="right", padx=4, pady=4)
        prop_gallery_cards.insert(0 if prepend else len(prop_gallery_cards), card)
        _relayout_prop_gallery()
    g["_prop_gallery_add"] = _prop_gallery_add

    # Prop prompt builder — single clean object render for 3D, not multi-view sheet
    def _build_prop_prompt(name):
        cat = prop_category_var.get() if "prop_category_var" in g else "อัตโนมัติ"
        cat_hint = _PROP_CAT_HINTS.get(cat, "")
        return (f"Single prop/object render: {name}. {cat_hint}"
                "Show one complete object only, centered, isolated, full object visible, three-quarter front view. "
                "Clean silhouette, clear shape, readable material, realistic scale, no cropping, no duplicate views, no multi-angle sheet. "
                "Plain white or very light gray studio background with soft shadow under the object. "
                "No humans, no characters, no hands, no label strip, no text labels on the object itself. "
                "No environment, no room, no location, no scenes. "
                "Photorealistic product-style studio lighting, sharp focus, high detail, suitable as a source image for 3D generation. No watermark.")

    def _set_prop_action_buttons_running(running, auto=False):
        prop_job_running[0] = bool(running)
        try:
            if prop_make_btn[0]:
                prop_make_btn[0].config(state=(tk.DISABLED if running else tk.NORMAL))
            if auto_prop_btn[0] and not auto:
                auto_prop_btn[0].config(state=(tk.DISABLED if running else tk.NORMAL))
        except Exception:
            pass

    def _set_auto_prop_button_running(running):
        btn = auto_prop_btn[0]
        if not btn:
            return
        try:
            _set_prop_action_buttons_running(running, auto=True)
            if running:
                btn.config(text="⏹ หยุด", command=stop_auto_prop, state=tk.NORMAL, bg="#DC2626", activebackground="#B91C1C")
            else:
                btn.config(text="⚡ Auto Prop", command=generate_auto_prop, state=tk.NORMAL, bg="#0EA5E9", activebackground="#0284C7")
        except Exception:
            pass

    def stop_auto_prop():
        auto_prop_stop[0] = True
        _prop_log("[auto-prop] ขอหยุด — จะหยุดหลังรูปที่กำลังสร้างเสร็จ")

    def _run_prop_jobs(names, auto=False):
        names = [n.strip() for n in names if n and n.strip()]
        if not names:
            _prop_log("ไม่มีชื่อสำหรับสร้าง Prop")
            return
        if prop_job_running[0]:
            _prop_log("กำลังสร้างอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        if auto:
            auto_prop_stop[0] = False
            auto_prop_running[0] = True
            root.after(0, lambda: _set_auto_prop_button_running(True))
        else:
            root.after(0, lambda: _set_prop_action_buttons_running(True, auto=False))
        def worker():
            try:
                lock = globals().get("_bridge_queue_lock")
                def run_one(idx, name):
                    prompt = _build_prop_prompt(name)
                    refine = g.get("_refine_prompt_via_ai") or globals().get("_refine_prompt_via_ai")
                    if callable(refine):
                        _prop_log(f"[refine] ส่ง GPT แปลง prompt Prop: {name}")
                        refined_prompt = refine(prompt, kind="prop")
                        if refined_prompt and refined_prompt != prompt:
                            _prop_log(f"[refine] ได้ prompt ใหม่ ({len(refined_prompt)} chars)")
                            prompt = refined_prompt
                    else:
                        _prop_log("[refine] ไม่เจอตัวแปลง prompt — ใช้ prompt เดิม")
                    if "_wait_bridge_free" in globals():
                        globals()["_wait_bridge_free"](log_fn=_prop_log)
                    if auto and auto_prop_stop[0]:
                        _prop_log(f"[auto-prop] หยุดแล้ว — ข้าม {name}")
                        return
                    _prop_log(f"[auto-prop] {idx}/{len(names)} — เริ่มสร้าง: {name}")
                    payload = {"model":"auto", "prompt":prompt, "n":1, "aspect_ratio":"1:1", "history_and_training_disabled":False, "_use_prop_history":True}
                    ref_path = prop_ref_image[0]
                    if ref_path and os.path.exists(ref_path):
                        enc = g.get("_encode_image_b64") or globals().get("_encode_image_b64")
                        if callable(enc):
                            payload["images"] = [enc(ref_path)]
                            _prop_log(f"[แนบรูป] ส่งไฟล์แนบไปด้วย: {os.path.basename(ref_path)}")
                        else:
                            _prop_log("[แนบรูป] ไม่พบตัวเข้ารหัสรูป — สร้างแบบไม่มีไฟล์แนบ")
                    out = g["_do_image_request"](payload, is_edit=bool(payload.get("images")), prompt=prompt, name_hint=name, raw_prompt=prompt, output_dir=str(export_prop_dir))
                    root.after(0, lambda out=out: (_prop_log(f"✓ {out}"), _prop_gallery_add(out, True), _notify_done()))
                def run_all():
                    for idx, name in enumerate(names, 1):
                        if auto and auto_prop_stop[0]:
                            _prop_log("[auto-prop] หยุดตามคำสั่ง")
                            break
                        run_one(idx, name)
                if lock:
                    with lock: run_all()
                else:
                    run_all()
            except Exception as e:
                root.after(0, lambda e=e: _prop_log(f"ERROR: {e}"))
            finally:
                if auto:
                    auto_prop_running[0] = False
                    root.after(0, lambda: _set_auto_prop_button_running(False))
                else:
                    root.after(0, lambda: _set_prop_action_buttons_running(False, auto=False))
        threading.Thread(target=worker, daemon=True).start()

    def generate_prop():
        name = prop_name_var.get().strip()
        if not name:
            _prop_log("ใส่ชื่อก่อน")
            return
        _prop_log(_builder_set_selection_lock(lock_g, "prop", name))
        _run_prop_jobs([name])
    g["generate_prop"] = generate_prop

    def generate_auto_prop():
        ctx = _load_ref_context()
        if not ctx.strip():
            _prop_log("ไม่เจอ prompt_ref_context.json")
            return
        items = _extract_context_props(ctx)
        if not items:
            _prop_log("ไม่เจอ props[] ใน Prompt Context")
            return
        for name, item in items:
            g["_prop_selected_context"][name] = _prop_item_detail(item)
        names = [name for name, _item in items]
        _prop_log(_builder_set_selection_locks(lock_g, "prop", names))
        _prop_log("[auto-prop] จะสร้างทีละรูป: " + ", ".join(names))
        _run_prop_jobs(names, auto=True)
    g["generate_auto_prop"] = generate_auto_prop

    def clear_prop_gallery():
        for child in prop_gallery_inner.winfo_children():
            child.destroy()
        prop_gallery_images.clear()
        prop_gallery_cards.clear()
        prop_gallery_paths.clear()
        _save_prop_gallery()
        _prop_log("ล้างรูป Prop gallery แล้ว")

    def _restore_prop_gallery():
        try:
            payload = json.loads(prop_gallery_state_path.read_text(encoding="utf-8"))
            saved = payload.get("paths") if isinstance(payload, dict) else []
        except FileNotFoundError:
            saved = [
                str(path) for path in sorted(
                    export_prop_dir.glob("*"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
        except (json.JSONDecodeError, OSError):
            saved = []
        prop_gallery_paths[:] = [str(Path(path)) for path in saved if Path(path).is_file()]
        _save_prop_gallery()
        for path in reversed(prop_gallery_paths):
            _prop_gallery_add(path, prepend=True, persist=False)

    root.after(50, _restore_prop_gallery)

    def _start_new_prop_history():
        try:
            # Reset the module instance the image requests actually use.
            imgmod = g.get("_imgmod")
            if imgmod is None:
                import snapgen_image_gen as imgmod
            imgmod.reset_prop_conversation()
            _prop_log("[ประวัติ Prop] เริ่มประวัติใหม่แล้ว — งานถัดไปจะเปิดแชทใหม่")
        except Exception as exc:
            _prop_log(f"[ประวัติ Prop] เริ่มใหม่ไม่สำเร็จ: {exc}")

    make_prop_btn = tk.Button(prop_row, text="📦 สร้าง Prop", command=generate_prop, bg="#059669", fg="white", activebackground="#10B981", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    make_prop_btn.pack(side="left", padx=4)
    prop_make_btn[0] = make_prop_btn

    # Pack Select AFTER สร้าง Prop (so สร้าง Prop appears first on the left)
    prop_select_btn.pack(side="left", padx=(0, 4))
    new_history_btn = tk.Button(prop_row, text="เริ่มประวัติใหม่", command=_start_new_prop_history, bg=STYLE.HISTORY.bg, fg=STYLE.HISTORY.fg, activebackground=STYLE.HISTORY.active_bg, activeforeground=STYLE.HISTORY.active_fg, relief="flat", bd=0, highlightthickness=1, highlightbackground="#BFDBFE", highlightcolor="#93C5FD", padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    new_history_btn.pack(side="left", padx=4)
    tk.Button(prop_row, text="Clear", command=lambda: (prop_name_var.set(""), prop_log_widget.delete("1.0", tk.END)), bg="#DC2626", fg="white", activebackground="#B91C1C", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)
    tk.Button(prop_row, text="🧹 ล้างรูป", command=clear_prop_gallery, bg="#DC2626", fg="white", activebackground="#B91C1C", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)
    g["prop_page"] = prop_page
    return prop_page
