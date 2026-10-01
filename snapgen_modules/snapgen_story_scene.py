# -*- coding: utf-8 -*-
"""Story scene designer: text design -> white-background reference -> 3D assets."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from snapgen_fonts import font_family as _snapgen_font_family


SNAPGEN_UI_FONT = _snapgen_font_family()
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _location_view_capabilities(location):
    """Return exterior/interior modes that make sense for one story location."""
    if not isinstance(location, dict):
        location = {"name": str(location or "")}
    text = " ".join(
        str(location.get(key) or "")
        for key in ("name", "type", "parent_location", "visual_description", "story_fact")
    ).casefold()
    location_type = str(location.get("type") or "").casefold()

    interior_words = (
        "ห้อง", "ภายใน", "ท้องพระโรง", "โถง", "ห้องครัว", "ห้องนอน", "ห้องบรรทม",
        "room", "interior", "bedroom", "kitchen", "hall", "unit",
    )
    exterior_words = (
        "ป่า", "ถนน", "ท่าเรือ", "ลาน", "สวน", "ริม", "แม่น้ำ", "ตลาด", "หมู่บ้าน", "เมือง",
        "กลางแจ้ง", "forest", "street", "harbor", "pier", "garden", "outdoor", "exterior",
    )
    building_words = (
        "บ้าน", "เรือน", "อาคาร", "พระราชวัง", "วัง", "โรงเรียน", "โรงพยาบาล", "ร้าน",
        "โรงแรม", "building", "house", "home", "palace", "temple",
    )
    if location_type in {"room", "unit", "interior"} or any(word in text for word in interior_words):
        return ("interior",)
    if location_type in {"building", "house", "home", "palace"} or any(word in text for word in building_words):
        return ("exterior", "interior")
    if location_type in {"forest", "street", "city", "outdoor", "exterior"} or any(word in text for word in exterior_words):
        return ("exterior",)
    return ("exterior", "interior")


def _safe_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _build_master_scene_prompt(location, view, design_text=""):
    location = location if isinstance(location, dict) else {"name": str(location or "")}
    name = str(location.get("name") or "สถานที่ในเรื่อง").strip()
    mode = "ภายใน" if view == "interior" else "ภายนอก"
    details = []
    for label, key in (
        ("ข้อเท็จจริงจากเรื่อง", "story_fact"),
        ("รูปลักษณ์", "visual_description"),
        ("บรรยากาศ", "atmosphere"),
        ("วัสดุ", "materials"),
    ):
        value = str(location.get(key) or "").strip()
        if value:
            details.append(f"{label}: {value}")
    must = _safe_list(location.get("must_include"))
    must_not = _safe_list(location.get("must_not_include"))
    if must:
        details.append("ต้องมี: " + ", ".join(must[:10]))
    if must_not:
        details.append("ห้ามมี: " + ", ".join(must_not[:10]))
    context = ". ".join(details)
    design = str(design_text or "").strip()
    return (
        f"สร้างภาพ SCENE DESIGN REFERENCE — {mode} ของ {name}. "
        f"{context}. "
        + (f"TEXT DESIGN ที่อนุมัติแล้ว: {design}. " if design else "")
        + "ภาพนี้ไม่ใช่ภาพฉากสำเร็จและห้ามจัดองค์ประกอบทุกชิ้นของสถานที่ให้ครบในภาพเดียว. "
        "ถ้า TEXT DESIGN มีบรรทัดรายการชิ้นสำหรับ 3D ให้ถือเป็น inventory สำหรับขั้นตอนถัดไปเท่านั้น ห้ามพยายามวาดรายการนั้นให้ครบทั้งหมดในภาพต้นแบบ. "
        "ให้เป็นภาพออกแบบบนพื้นหลังขาวหรือเทาอ่อนแบบ studio concept sheet ที่สะอาดมาก มีเพียงโครงสร้างหลักหรือ visual motif สำคัญ 1-3 กลุ่ม "
        "เพื่อกำหนดภาษารูปทรง สัดส่วน วัสดุ สี และงานตกแต่งของสถานที่. เว้นพื้นที่ว่างมาก อ่าน silhouette ง่าย ไม่ทำเป็นห้องหรือ environment เต็ม. "
        "รายการชิ้น 3D อื่น ๆ จะถูกสร้างแยกทีละชิ้นภายหลัง จึงไม่ต้องนำทุกประตู หน้าต่าง เสา เฟอร์นิเจอร์ หรือพร็อพมารวมในภาพนี้. "
        "ไม่มีคน ไม่มีตัวละคร ไม่มีรถ ไม่มีต้นไม้หรือฉากหลังธรรมชาติที่ไม่จำเป็น ไม่มีข้อความ ไม่มีป้าย ไม่มี watermark. "
        "neutral studio daylight, photorealistic architectural/production design reference, clean readable forms, realistic scale, sharp focus. "
        "OUTPUT 16:9 landscape."
    )


def _build_part_prompt(part, location_name=""):
    if isinstance(part, dict):
        name = str(part.get("name") or "ชิ้นส่วน").strip()
        description = str(part.get("description") or "").strip()
    else:
        name = str(part or "ชิ้นส่วน").strip()
        description = ""
    source = f" จากภาพต้นแบบฉากของ {location_name}" if location_name else " จากภาพต้นแบบฉาก"
    return (
        f"ใช้ภาพแนบเป็น visual style/material reference หลัก{source}. สร้างเฉพาะวัตถุชิ้นนี้: {name}. "
        + (f"รายละเอียดที่ต้องรักษา: {description}. " if description else "")
        + "รักษาภาษารูปทรง สัดส่วน วัสดุ สี อายุพื้นผิว และดีไซน์ให้เข้าชุดกับภาพอ้างอิง. "
        "ถ้าวัตถุนี้ไม่ได้ปรากฏครบทั้งชิ้นในภาพอ้างอิง ห้าม crop หรือคัดลอกวัตถุอื่นมาแทน ให้สร้างชิ้นเต็มจากรายละเอียดข้อความโดยคง style เดียวกัน. "
        "สร้างหนึ่งวัตถุเท่านั้น เห็นครบทั้งชิ้น มุมสามในสี่ด้านหน้า centered, clean silhouette, no cropping, "
        "no duplicate views, no multi-angle sheet. พื้นหลังขาวหรือเทาอ่อนเรียบ มีเงาอ่อนใต้ชิ้น. "
        "ไม่มีคน ไม่มีมือ ไม่มีตัวละคร ไม่มีฉากแวดล้อม ไม่มีห้อง ไม่มีต้นไม้หรือของชิ้นอื่นที่ไม่ใช่ส่วนหนึ่งของวัตถุ "
        "ไม่มีข้อความ ไม่มีป้าย ไม่มี watermark. Photorealistic product-style studio lighting, sharp focus, high detail, "
        "suitable as a source image for AI-to-3D generation. OUTPUT 1:1 square."
    )


def _build_scene_design_fallback(location, view):
    """Build an editable text-first scene design and 3D asset list without image generation."""
    location = location if isinstance(location, dict) else {"name": str(location or "")}
    name = str(location.get("name") or "สถานที่ในเรื่อง").strip()
    mode = "ภายใน" if view == "interior" else "ภายนอก"
    visual = str(location.get("visual_description") or location.get("story_fact") or "").strip()
    materials = str(location.get("materials") or "").strip()
    atmosphere = str(location.get("atmosphere") or "").strip()
    parts = _fallback_parts(location, view)
    lines = [
        f"ออกแบบ {mode} — {name}",
        "แนวคิด: ใช้ภาพต้นแบบพื้นหลังขาวเพื่อกำหนดภาษารูปทรงและวัสดุ ไม่ทำเป็นฉากสำเร็จทั้งพื้นที่",
    ]
    if visual:
        lines.append("รูปลักษณ์หลัก: " + visual)
    if materials:
        lines.append("วัสดุหลัก: " + materials)
    if atmosphere:
        lines.append("อารมณ์/บรรยากาศที่ต้องสะท้อนผ่านดีไซน์: " + atmosphere)
    lines.append("ชิ้นที่จะทำภาพเดี่ยวสำหรับ 3D: " + ", ".join(item["name"] for item in parts))
    return "\n".join(lines), parts


def _parse_scene_design_response(raw):
    """Parse text-first design JSON: design_text + 3-6 separate 3D parts."""
    text = str(raw or "").lstrip("\ufeff").strip()
    candidates = [text]
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I))
    data = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                data = value
                break
        except Exception:
            start = candidate.find("{")
            if start >= 0:
                try:
                    value, _ = json.JSONDecoder().raw_decode(candidate[start:])
                    if isinstance(value, dict):
                        data = value
                        break
                except Exception:
                    pass
    if not isinstance(data, dict):
        raise RuntimeError("GPT ไม่ได้คืน JSON แบบฉาก")
    design_text = str(data.get("design_text") or data.get("design") or "").strip()
    parts = _parse_scene_parts_response(json.dumps({"parts": data.get("parts") or []}, ensure_ascii=False))
    if not design_text:
        raise RuntimeError("GPT ไม่ได้คืน design_text")
    return design_text, parts


def _parts_from_design_text(design_text, location, view):
    """Read the editable 3D asset list from scene-design text, with a safe fallback."""
    text = str(design_text or "").strip()
    names = []
    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        if "3d" not in normalized.casefold() and "ชิ้นที่จะทำ" not in normalized:
            continue
        value = normalized.split(":", 1)[1] if ":" in normalized else normalized
        for item in re.split(r"[,;|•]+", value):
            name = item.strip(" -\t")
            if name and name not in names:
                names.append(name)
            if len(names) == 6:
                break
        if len(names) >= 3:
            break
    if len(names) < 3:
        return _fallback_parts(location, view)
    return [{"name": name[:80], "description": "", "image": ""} for name in names[:6]]


def _parse_scene_parts_response(raw):
    text = str(raw or "").lstrip("\ufeff").strip()
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I):
        candidate = match.group(1).strip()
        try:
            data = json.loads(candidate)
            break
        except Exception:
            continue
    else:
        try:
            data = json.loads(text)
        except Exception:
            start = text.find("{")
            if start < 0:
                raise RuntimeError("GPT ไม่ได้คืน JSON รายการชิ้นส่วน")
            decoder = json.JSONDecoder()
            try:
                data, _ = decoder.raw_decode(text[start:])
            except Exception as exc:
                raise RuntimeError("GPT คืนรายการชิ้นส่วนที่อ่านไม่ได้") from exc
    parts = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(parts, list):
        raise RuntimeError("GPT ไม่ได้คืน parts เป็นรายการ")
    out = []
    seen = set()
    for item in parts:
        if isinstance(item, str):
            item = {"name": item, "description": ""}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = re.sub(r"\s+", "", name).casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name[:80],
            "description": str(item.get("description") or "").strip()[:500],
            "image": str(item.get("image") or "").strip(),
        })
        if len(out) == 6:
            break
    if len(out) < 3:
        raise RuntimeError("GPT แยกชิ้นส่วนน้อยเกินไป")
    return out


def _fallback_parts(location, view):
    names = []
    for value in _safe_list((location or {}).get("visible_elements")):
        if value not in names:
            names.append(value)
    text = " ".join(str((location or {}).get(key) or "") for key in ("name", "type", "visual_description"))
    is_building = any(word in text.casefold() for word in ("บ้าน", "เรือน", "อาคาร", "วัง", "พระราชวัง", "building", "house", "palace"))
    defaults = (
        ["ตัวอาคารหลัก", "บันได", "ประตู", "หน้าต่าง", "เสาและระเบียง", "รั้วหรือองค์ประกอบหลัก"]
        if view == "exterior" and is_building else
        ["ประตู", "หน้าต่าง", "เฟอร์นิเจอร์หลัก", "โต๊ะ", "ตู้หรือชั้นเก็บของ", "ฉากกั้นหรือองค์ประกอบโครงสร้าง"]
        if view == "interior" else
        ["องค์ประกอบหลักของพื้นที่", "โครงสร้างหลัก", "วัตถุเด่น 1", "วัตถุเด่น 2", "วัตถุเด่น 3", "วัตถุเด่น 4"]
    )
    for value in defaults:
        if value not in names:
            names.append(value)
        if len(names) >= 6:
            break
    return [{"name": name, "description": "", "image": ""} for name in names[:6]]


def install(g: dict, root: tk.Misc, parent: tk.Misc) -> dict:
    """Build the Scene subpage and return refresh/runtime callbacks."""
    base = Path(g.get("BASE") or Path.cwd() / "snapgen_data")
    export_story = Path(g.get("EXPORT_STORY_FACE") or base / "story_face")
    export_dir = export_story / "scene"
    master_dir = export_dir / "master"
    parts_dir = export_dir / "parts"
    master_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)
    state_path = base / "story_scene_state.json"

    scene_box = tk.LabelFrame(parent, text="🎬 ฉาก — ออกแบบข้อความ → ภาพต้นแบบ → แยกชิ้นทำ 3D", bg="#FAFAF7", fg="#1A1A1A", padx=10, pady=8)
    scene_box.pack(fill="both", expand=True, padx=10, pady=10)

    context_state = {"data": {}, "hash": "", "locations": [], "path": None}
    ui_busy = {"master": False, "split": False, "part": set(), "loading": False}
    preview_refs = {"master": None, "parts": [None] * 6}
    state = {"version": 1, "stories": {}}
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        if isinstance(loaded, dict):
            state.update(loaded)
            if not isinstance(state.get("stories"), dict):
                state["stories"] = {}
    except Exception:
        pass

    title_var = tk.StringVar(value="ยังไม่มี Context")
    location_var = tk.StringVar(value="")
    view_var = tk.StringVar(value="exterior")
    status_var = tk.StringVar(value="เลือกสถานที่ แล้วตรวจแบบข้อความก่อนสร้างภาพ")
    master_path_var = tk.StringVar(value="")

    header = tk.Frame(scene_box, bg="#FAFAF7")
    header.pack(fill="x", pady=(0, 8))
    tk.Label(header, text="เรื่อง:", bg="#FAFAF7", fg="#374151", font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left")
    tk.Label(header, textvariable=title_var, bg="#FFFFFF", fg="#111827", relief="solid", bd=1, padx=8, pady=5, width=28, anchor="w").pack(side="left", padx=(5, 10))
    tk.Label(header, text="สถานที่:", bg="#FAFAF7", fg="#374151", font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left")
    location_combo = ttk.Combobox(header, textvariable=location_var, state="readonly", width=34)
    location_combo.pack(side="left", padx=(5, 10))
    tk.Button(header, text="↻ Context", command=lambda: refresh(force=True), bg="#64748B", fg="white", activebackground="#475569", relief="flat", bd=0, padx=10, pady=5, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left")

    view_row = tk.Frame(scene_box, bg="#FAFAF7")
    view_row.pack(fill="x", pady=(0, 8))
    tk.Label(view_row, text="ประเภท:", bg="#FAFAF7", fg="#374151", font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left")
    view_buttons = {}

    def _set_view(value):
        if value not in _current_capabilities():
            return
        view_var.set(value)
        _paint_view_buttons()
        _load_project_into_ui()

    for key, text in (("exterior", "ภายนอก"), ("interior", "ภายใน")):
        button = tk.Button(view_row, text=text, command=lambda value=key: _set_view(value), relief="flat", bd=0, padx=18, pady=6, width=10, font=(SNAPGEN_UI_FONT, 9, "bold"), cursor="hand2")
        button.pack(side="left", padx=(6, 0))
        view_buttons[key] = button
    tk.Label(view_row, textvariable=status_var, bg="#FAFAF7", fg="#6B7280", anchor="w").pack(side="left", padx=14, fill="x", expand=True)

    master_box = tk.LabelFrame(scene_box, text="1. แบบฉาก + ภาพต้นแบบพื้นหลังขาว — มีเพียง 1 Slot", bg="#FFFFFF", fg="#111827", padx=8, pady=8)
    master_box.pack(fill="x", pady=(0, 8))
    master_content = tk.Frame(master_box, bg="#FFFFFF")
    master_content.pack(fill="x")
    master_preview = tk.Label(master_content, text="ยังไม่มีภาพต้นแบบฉาก", bg="#F3F4F6", fg="#6B7280", width=42, height=12, relief="solid", bd=1)
    master_preview.pack(side="left", padx=(0, 10))
    master_info = tk.Frame(master_content, bg="#FFFFFF")
    master_info.pack(side="left", fill="both", expand=True)
    tk.Label(master_info, text="แบบข้อความ (แก้ไขได้ก่อนสร้างรูป):", bg="#FFFFFF", fg="#374151", anchor="w", font=(SNAPGEN_UI_FONT, 9, "bold")).pack(fill="x", pady=(0, 3))
    scene_info = tk.Text(master_info, height=8, wrap="word", bg="#FFFDF7", fg="#111827", relief="solid", bd=1, padx=8, pady=7, font=(SNAPGEN_UI_FONT, 9), undo=True)
    scene_info.pack(fill="x", pady=(0, 7))
    master_actions = tk.Frame(master_info, bg="#FFFFFF")
    master_actions.pack(fill="x")

    parts_box = tk.LabelFrame(scene_box, text="2. ชิ้นสำหรับทำ 3D — สร้างรายการจากแบบข้อความ", bg="#FAFAF7", fg="#111827", padx=8, pady=8)
    parts_box.pack(fill="both", expand=True)
    parts_header = tk.Frame(parts_box, bg="#FAFAF7")
    parts_header.pack(fill="x", pady=(0, 6))
    parts_status_var = tk.StringVar(value="ตรวจแบบข้อความและสร้างภาพต้นแบบก่อน แล้วกด สร้างรายการ 5–6 ชิ้น")
    tk.Label(parts_header, textvariable=parts_status_var, bg="#FAFAF7", fg="#6B7280", anchor="w").pack(side="left", fill="x", expand=True)

    cards = []
    grid = tk.Frame(parts_box, bg="#FAFAF7")
    grid.pack(fill="both", expand=True)
    for column in range(3):
        grid.grid_columnconfigure(column, weight=1, uniform="scene_parts")
    for row in range(2):
        grid.grid_rowconfigure(row, weight=1, uniform="scene_parts_rows")

    def _log(message):
        writer = g.get("_new_log")
        text = "[ฉาก] " + str(message)
        if callable(writer):
            try:
                writer(text)
                return
            except Exception:
                pass
        print(text, flush=True)

    def _load_context():
        for name in ("prompt_ref_context.json", "context_master.json"):
            path = base / name
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
                data = json.loads(raw.decode("utf-8-sig"))
                locations = data.get("locations") if isinstance(data, dict) else None
                if not isinstance(locations, list) or not locations:
                    continue
                normalized = []
                for item in locations:
                    if isinstance(item, str):
                        item = {"name": item}
                    if isinstance(item, dict) and str(item.get("name") or "").strip():
                        normalized.append(dict(item))
                if normalized:
                    return data, normalized, hashlib.sha256(raw).hexdigest(), path
            except Exception:
                continue
        return {}, [], "", None

    def _story_bucket(create=True):
        key = context_state.get("hash") or "no-context"
        stories = state.setdefault("stories", {})
        if create:
            bucket = stories.setdefault(key, {"selected_location": "", "selected_view": "exterior", "projects": {}})
            if not isinstance(bucket.get("projects"), dict):
                bucket["projects"] = {}
            return bucket
        return stories.get(key) or {}

    def _save_state():
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            _log(f"บันทึกสถานะฉากไม่สำเร็จ: {exc}")

    def _locations_by_name():
        return {str(item.get("name") or "").strip(): item for item in context_state.get("locations") or []}

    def _selected_location():
        return _locations_by_name().get(location_var.get().strip()) or {}

    def _current_capabilities():
        return _location_view_capabilities(_selected_location())

    def _project_key():
        return location_var.get().strip() + "::" + view_var.get().strip()

    def _current_project(create=True):
        bucket = _story_bucket(create=create)
        projects = bucket.get("projects") or {}
        key = _project_key()
        if create:
            project = projects.setdefault(key, {"design_text": "", "master_image": "", "parts": []})
            bucket["projects"] = projects
            return project
        return projects.get(key) or {}

    def _paint_view_buttons():
        capabilities = _current_capabilities()
        if view_var.get() not in capabilities and capabilities:
            view_var.set(capabilities[0])
        for key, button in view_buttons.items():
            supported = key in capabilities
            active = key == view_var.get() and supported
            button.config(
                state=tk.NORMAL if supported else tk.DISABLED,
                bg="#6B7280" if active else "#FFFFFF",
                fg="#FFFFFF" if active else ("#1F2937" if supported else "#9CA3AF"),
                activebackground="#4B5563" if active else "#F3F4F6",
                activeforeground="#FFFFFF" if active else "#111827",
            )

    def _set_text(widget, text, readonly=False):
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.edit_modified(False)
        if readonly:
            widget.configure(state="disabled")

    def _save_design_text():
        if ui_busy["loading"] or not _selected_location():
            return
        project = _current_project()
        project["design_text"] = scene_info.get("1.0", tk.END).strip()
        _save_state()

    def _on_design_modified(_event=None):
        if not scene_info.edit_modified():
            return
        scene_info.edit_modified(False)
        _save_design_text()

    scene_info.bind("<<Modified>>", _on_design_modified)

    def _preview(label, path, ref_key, size=(430, 245)):
        p = Path(str(path or ""))
        if not p.is_file() or p.suffix.lower() not in _IMAGE_SUFFIXES:
            label.config(image="", text="ยังไม่มีรูป", width=42, height=12)
            if ref_key == "master":
                preview_refs["master"] = None
            else:
                preview_refs["parts"][int(ref_key)] = None
            return
        try:
            from PIL import Image, ImageTk
            image = Image.open(p).convert("RGB")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            label.config(image=photo, text="", width=image.width, height=image.height)
            if ref_key == "master":
                preview_refs["master"] = photo
            else:
                preview_refs["parts"][int(ref_key)] = photo
        except Exception:
            label.config(image="", text=p.name[:42], width=42, height=12)

    def _open_path(path):
        p = Path(str(path or ""))
        if p.is_file():
            try:
                import os
                os.startfile(str(p))
            except Exception as exc:
                _log(f"เปิดรูปไม่สำเร็จ: {exc}")

    def _sync_bucket_selection():
        bucket = _story_bucket()
        bucket["selected_location"] = location_var.get().strip()
        bucket["selected_view"] = view_var.get().strip()
        _save_state()

    def _render_part_card(index, part=None):
        card = cards[index]
        data = part if isinstance(part, dict) else {}
        card["name"].set(str(data.get("name") or ""))
        card["description"] = str(data.get("description") or "")
        card["image"] = str(data.get("image") or "")
        card["status"].set("พร้อมสร้างรูป" if card["name"].get().strip() else "รอแยกชิ้น")
        _preview(card["preview"], card["image"], index, size=(250, 155))
        ready = bool(card["image"] and Path(card["image"]).is_file())
        card["open_btn"].config(state=tk.NORMAL if ready else tk.DISABLED)
        card["send_btn"].config(state=tk.NORMAL if ready else tk.DISABLED)

    def _sync_parts_to_project():
        project = _current_project()
        project["parts"] = [
            {
                "name": card["name"].get().strip(),
                "description": str(card.get("description") or ""),
                "image": str(card.get("image") or ""),
            }
            for card in cards if card["name"].get().strip()
        ]
        _sync_bucket_selection()

    def _load_project_into_ui():
        _paint_view_buttons()
        location = _selected_location()
        if not location:
            master_path_var.set("")
            master_preview.config(image="", text="ยังไม่มีสถานที่", width=42, height=12)
            ui_busy["loading"] = True
            try:
                _set_text(scene_info, "ยังไม่มีข้อมูลสถานที่จาก Context")
                scene_info.configure(state="disabled")
            finally:
                ui_busy["loading"] = False
            for index in range(6):
                _render_part_card(index)
            return
        project = _current_project()
        design_text = str(project.get("design_text") or "").strip()
        if not design_text:
            design_text, design_parts = _build_scene_design_fallback(location, view_var.get())
            project["design_text"] = design_text
            project["design_parts"] = design_parts
            _save_state()
        ui_busy["loading"] = True
        try:
            scene_info.configure(state="normal")
            _set_text(scene_info, design_text)
        finally:
            ui_busy["loading"] = False
        master = str(project.get("master_image") or "")
        master_path_var.set(master)
        _preview(master_preview, master, "master")
        parts = project.get("parts") if isinstance(project.get("parts"), list) else []
        ui_busy["loading"] = True
        try:
            for index in range(6):
                _render_part_card(index, parts[index] if index < len(parts) else None)
        finally:
            ui_busy["loading"] = False
        master_ready = bool(master and Path(master).is_file())
        split_btn.config(state=tk.NORMAL if master_ready and not ui_busy["split"] else tk.DISABLED)
        open_master_btn.config(state=tk.NORMAL if master_ready else tk.DISABLED)
        parts_status_var.set(
            f"มี {len(parts)} ชิ้น — สร้างรูปแต่ละชิ้นก่อนส่ง 3D" if parts else
            "ภาพต้นแบบพร้อมแล้ว — กด สร้างรายการ 5–6 ชิ้น" if master_ready else
            "ตรวจแบบข้อความ แล้วสร้างภาพต้นแบบพื้นหลังขาวก่อน"
        )
        _sync_bucket_selection()

    def _on_location_changed(_event=None):
        capabilities = _current_capabilities()
        if view_var.get() not in capabilities and capabilities:
            view_var.set(capabilities[0])
        _load_project_into_ui()

    location_combo.bind("<<ComboboxSelected>>", _on_location_changed)

    def refresh(force=False):
        data, locations, context_hash, path = _load_context()
        if not force and context_hash and context_hash == context_state.get("hash"):
            _paint_view_buttons()
            return
        context_state.update(data=data, hash=context_hash, locations=locations, path=path)
        story = data.get("story") if isinstance(data, dict) else {}
        title = str((story or {}).get("title") or (story or {}).get("summary") or "เรื่องปัจจุบัน").strip()
        title_var.set(title[:52] if locations else "ยังไม่มี Context")
        names = [str(item.get("name") or "").strip() for item in locations]
        location_combo["values"] = names
        bucket = _story_bucket()
        selected = str(bucket.get("selected_location") or "")
        if selected not in names:
            selected = names[0] if names else ""
        location_var.set(selected)
        saved_view = str(bucket.get("selected_view") or "exterior")
        view_var.set(saved_view)
        capabilities = _current_capabilities()
        if capabilities and view_var.get() not in capabilities:
            view_var.set(capabilities[0])
        status_var.set(
            f"โหลด {len(names)} สถานที่จาก {path.name}" if path else
            "ยังไม่มี Prompt-Ref Context ที่มีสถานที่"
        )
        _load_project_into_ui()

    def _generate_master():
        if ui_busy["master"]:
            return
        location = _selected_location()
        if not location:
            status_var.set("ยังไม่มีสถานที่ให้สร้าง")
            return
        do_request = g.get("_do_image_request")
        if not callable(do_request):
            status_var.set("ระบบสร้างรูปยังไม่พร้อม")
            return
        design_text = scene_info.get("1.0", tk.END).strip()
        if not design_text:
            status_var.set("แบบข้อความยังว่าง — กด ออกแบบข้อความ ก่อน")
            return
        project = _current_project()
        project["design_text"] = design_text
        _save_state()
        prompt = _build_master_scene_prompt(location, view_var.get(), design_text)
        ui_busy["master"] = True
        master_btn.config(state=tk.DISABLED, text="กำลังสร้าง...")
        status_var.set("กำลังสร้างภาพต้นแบบพื้นหลังขาว...")
        name = f"scene-reference-{location_var.get()}-{view_var.get()}"

        def worker():
            try:
                payload = {"model": "auto", "prompt": prompt, "n": 1, "aspect_ratio": "16:9", "history_and_training_disabled": False, "_use_story_face_history": True}
                out = do_request(payload, is_edit=False, prompt=prompt, name_hint=name, raw_prompt=prompt, output_dir=str(master_dir))
                error = None
            except Exception as exc:
                out, error = "", exc

            def done():
                ui_busy["master"] = False
                master_btn.config(state=tk.NORMAL, text="🎨 สร้างภาพต้นแบบ")
                if error:
                    status_var.set(f"สร้างไม่สำเร็จ: {error}")
                    _log(f"ภาพต้นแบบฉาก ERROR: {error}")
                    return
                project = _current_project()
                project["master_image"] = str(out)
                project["parts"] = []
                _save_state()
                status_var.set("ภาพต้นแบบพร้อม — ถ้าดีไซน์โอเค กด สร้างรายการ 5–6 ชิ้น ได้เลย")
                _log(f"ภาพต้นแบบฉาก: {out}")
                _load_project_into_ui()

            root.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _choose_master():
        path = filedialog.askopenfilename(
            parent=root,
            title="เลือกภาพต้นแบบฉากที่มีอยู่แล้ว",
            filetypes=[("Image", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        project = _current_project()
        project["master_image"] = str(Path(path))
        project["parts"] = []
        _save_state()
        status_var.set("ใช้ภาพต้นแบบที่เลือกแล้ว — กด สร้างรายการ 5–6 ชิ้น ได้เลย")
        _load_project_into_ui()

    def _design_scene_text():
        location = _selected_location()
        if not location:
            status_var.set("ยังไม่มีสถานที่ให้ออกแบบ")
            return
        design_text, design_parts = _build_scene_design_fallback(location, view_var.get())
        project = _current_project()
        project["design_text"] = design_text
        project["design_parts"] = design_parts
        project["parts"] = []
        _save_state()
        ui_busy["loading"] = True
        try:
            scene_info.configure(state="normal")
            _set_text(scene_info, design_text)
        finally:
            ui_busy["loading"] = False
        parts_status_var.set("แบบข้อความพร้อม — แก้ได้ตามต้องการ แล้วสร้างภาพต้นแบบ")
        status_var.set("ออกแบบข้อความแล้ว — ตรวจหรือแก้ข้อความก่อนสร้างรูป")

    def _vision_parts(image_path, location, location_name, view):
        from PIL import Image
        with Image.open(image_path) as image:
            image = image.convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
        prompt = (
            "วิเคราะห์ภาพ MASTER SCENE นี้เพื่อแยกวัตถุไปสร้างภาพเดี่ยวสำหรับ AI-to-3D. "
            "ตอบ JSON object เท่านั้น รูปแบบ {\"parts\":[{\"name\":\"\",\"description\":\"\"}]}. "
            "เลือก 5 หรือ 6 ชิ้นที่เป็นองค์ประกอบ 3D หลักและมองเห็นรูปทรงพอชัดในภาพจริง เช่น ตัวอาคาร บันได ประตู "
            "หน้าต่าง เสา เฟอร์นิเจอร์หลัก หรือพร็อพขนาดใหญ่. ห้ามเลือกแสง เงา ท้องฟ้า พื้นดิน สีบรรยากาศ คน ตัวละคร "
            "หรือวัตถุเล็กจุกจิก. ไม่ซ้ำกัน. description ต้องบอกลักษณะ รูปทรง วัสดุ สี และตำแหน่งที่เห็นในภาพสั้นๆ "
            f"สถานที่: {location_name}; ประเภท: {'ภายใน' if view == 'interior' else 'ภายนอก'}; "
            f"Location Bible: {json.dumps(location, ensure_ascii=False)[:5000]}"
        )
        payload = {
            "model": "auto",
            "mode": "custom",
            "prompt": prompt,
            "input_images": [{
                "name": "story_master_scene.jpg",
                "data_url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            }],
            "temporary_chat": True,
        }
        request = urllib.request.Request(
            "http://127.0.0.1:8000/v1/chatgpt/vision",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json"},
            method="POST",
        )
        lock = g.get("_bridge_queue_lock")
        try:
            if lock:
                with lock:
                    with urllib.request.urlopen(request, timeout=600) as response:
                        result = json.loads(response.read().decode("utf-8", errors="replace"))
            else:
                with urllib.request.urlopen(request, timeout=600) as response:
                    result = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"GPT Vision HTTP {exc.code}: {body}") from exc
        if isinstance(result.get("error"), dict):
            raise RuntimeError(str(result["error"].get("message") or result["error"]))
        raw = str(result.get("text") or ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return _parse_scene_parts_response(raw)

    def _split_master():
        if ui_busy["split"]:
            return
        master = master_path_var.get().strip()
        if not master or not Path(master).is_file():
            parts_status_var.set("ยังไม่มีภาพต้นแบบฉาก")
            return
        location = dict(_selected_location())
        view = view_var.get().strip()
        design_text = scene_info.get("1.0", tk.END).strip()
        parts = _parts_from_design_text(design_text, location, view)
        project = _current_project()
        project["design_text"] = design_text
        project["parts"] = parts
        _save_state()
        _load_project_into_ui()
        parts_status_var.set(f"ได้ {len(parts)} ชิ้นจากแบบข้อความ — สร้างรูปเดี่ยวแต่ละชิ้นก่อนทำ 3D")
        _log(f"สร้างรายการ 3D จากแบบข้อความได้ {len(parts)} ชิ้น")

    def _generate_part(index):
        if index in ui_busy["part"]:
            return
        card = cards[index]
        name = card["name"].get().strip()
        master = master_path_var.get().strip()
        if not name:
            card["status"].set("ใส่ชื่อชิ้นก่อน")
            return
        if not master or not Path(master).is_file():
            card["status"].set("ภาพต้นแบบฉากหาย")
            return
        do_request = g.get("_do_image_request")
        if not callable(do_request):
            card["status"].set("ระบบสร้างรูปยังไม่พร้อม")
            return
        ui_busy["part"].add(index)
        card["create_btn"].config(state=tk.DISABLED, text="กำลังสร้าง...")
        card["status"].set("กำลังแยกเป็นรูปเดี่ยว...")
        part = {"name": name, "description": card.get("description") or ""}
        prompt = _build_part_prompt(part, location_var.get().strip())

        def worker():
            try:
                payload = {"model": "auto", "prompt": prompt, "n": 1, "aspect_ratio": "1:1", "images": [master], "history_and_training_disabled": False, "_use_story_face_history": True}
                out = do_request(payload, is_edit=True, prompt=prompt, name_hint=f"scene-part-{name}", raw_prompt=prompt, output_dir=str(parts_dir))
                error = None
            except Exception as exc:
                out, error = "", exc

            def done():
                ui_busy["part"].discard(index)
                card["create_btn"].config(state=tk.NORMAL, text="🎨 สร้างรูป")
                if error:
                    card["status"].set(f"ผิดพลาด: {error}")
                    _log(f"สร้างชิ้น {name} ไม่สำเร็จ: {error}")
                    return
                card["image"] = str(out)
                card["status"].set("พร้อมส่ง 3D")
                _sync_parts_to_project()
                _preview(card["preview"], out, index, size=(250, 155))
                card["open_btn"].config(state=tk.NORMAL)
                card["send_btn"].config(state=tk.NORMAL)
                _log(f"ชิ้น {name}: {out}")
            root.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _send_part_to_3d(index):
        card = cards[index]
        path = str(card.get("image") or "")
        if not path or not Path(path).is_file():
            card["status"].set("ยังไม่มีรูปเดี่ยว")
            return
        receive = g.get("receive_story_prop")
        if not callable(receive):
            card["status"].set("หน้า Prop/3D ยังไม่พร้อม")
            return
        if receive(path, card["name"].get().strip(), ""):
            card["status"].set("ส่งไป Prop/3D แล้ว")

    design_btn = tk.Button(master_actions, text="✨ ออกแบบข้อความ", command=_design_scene_text, bg="#7C3AED", fg="white", activebackground="#6D28D9", relief="flat", bd=0, padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"))
    design_btn.pack(side="left", padx=(0, 5))
    master_btn = tk.Button(master_actions, text="🎨 สร้างภาพต้นแบบ", command=_generate_master, bg="#059669", fg="white", activebackground="#10B981", relief="flat", bd=0, padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"))
    master_btn.pack(side="left", padx=(0, 5))
    choose_master_btn = tk.Button(master_actions, text="📎 ใช้รูปที่มี", command=_choose_master, bg="#0EA5E9", fg="white", activebackground="#0284C7", relief="flat", bd=0, padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"))
    choose_master_btn.pack(side="left", padx=5)
    open_master_btn = tk.Button(master_actions, text="📂 เปิดรูป", command=lambda: _open_path(master_path_var.get()), state=tk.DISABLED, bg="#2563EB", fg="white", activebackground="#1D4ED8", relief="flat", bd=0, padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"))
    open_master_btn.pack(side="left", padx=5)
    split_btn = tk.Button(master_actions, text="🧩 สร้างรายการ 5–6 ชิ้น", command=_split_master, state=tk.DISABLED, bg="#F97316", fg="white", activebackground="#EA580C", relief="flat", bd=0, padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"))
    split_btn.pack(side="left", padx=5)

    for index in range(6):
        card_frame = tk.Frame(grid, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D1D5DB", padx=6, pady=6)
        card_frame.grid(row=index // 3, column=index % 3, sticky="nsew", padx=4, pady=4)
        name_var = tk.StringVar(value="")
        tk.Label(card_frame, text=f"ชิ้น {index + 1}", bg="#FFFFFF", fg="#6B7280", font=(SNAPGEN_UI_FONT, 8, "bold")).pack(anchor="w")
        entry = tk.Entry(card_frame, textvariable=name_var, bg="#FFFFFF", fg="#111827", relief="solid", bd=1, font=(SNAPGEN_UI_FONT, 9))
        entry.pack(fill="x", pady=(2, 5))
        preview = tk.Label(card_frame, text="รอแยกชิ้น", bg="#F3F4F6", fg="#6B7280", width=26, height=8, relief="solid", bd=1)
        preview.pack(fill="both", expand=True)
        status = tk.StringVar(value="รอแยกชิ้น")
        tk.Label(card_frame, textvariable=status, bg="#FFFFFF", fg="#6B7280", anchor="w", wraplength=240, font=(SNAPGEN_UI_FONT, 8)).pack(fill="x", pady=(4, 2))
        actions = tk.Frame(card_frame, bg="#FFFFFF")
        actions.pack(fill="x")
        create_btn = tk.Button(actions, text="🎨 สร้างรูป", command=lambda i=index: _generate_part(i), bg="#059669", fg="white", activebackground="#10B981", relief="flat", bd=0, padx=8, pady=5, font=(SNAPGEN_UI_FONT, 8, "bold"))
        create_btn.pack(side="left", padx=(0, 3))
        open_btn = tk.Button(actions, text="เปิด", command=lambda i=index: _open_path(cards[i].get("image")), state=tk.DISABLED, bg="#64748B", fg="white", activebackground="#475569", relief="flat", bd=0, padx=8, pady=5, font=(SNAPGEN_UI_FONT, 8, "bold"))
        open_btn.pack(side="left", padx=3)
        send_btn = tk.Button(actions, text="ส่ง 3D", command=lambda i=index: _send_part_to_3d(i), state=tk.DISABLED, bg="#2563EB", fg="white", activebackground="#1D4ED8", relief="flat", bd=0, padx=8, pady=5, font=(SNAPGEN_UI_FONT, 8, "bold"))
        send_btn.pack(side="left", padx=3)
        card = {
            "frame": card_frame,
            "name": name_var,
            "description": "",
            "image": "",
            "preview": preview,
            "status": status,
            "create_btn": create_btn,
            "open_btn": open_btn,
            "send_btn": send_btn,
        }
        cards.append(card)
        name_var.trace_add("write", lambda *_args: None if ui_busy["loading"] else _sync_parts_to_project())

    g["story_scene_refresh"] = refresh
    g["story_scene_generate_master"] = _generate_master
    g["story_scene_split_master"] = _split_master
    refresh(force=True)
    return {"refresh": refresh, "generate_master": _generate_master, "split_master": _split_master}
