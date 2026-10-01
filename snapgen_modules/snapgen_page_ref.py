# -*- coding: utf-8 -*-
"""SnapGen ref page.

This module owns the widgets, state, and callbacks for this page only.
"""
from __future__ import annotations

import os
import json
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk as _ttk
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
from snapgen_story_types import STORY_TYPE_OPTIONS, normalize_story_type, story_type_profile


def is_explicit_ghost_character(name, entity):
    """Detect explicit supernatural identity, ignoring negative constraints."""
    values = [str(name or "")]
    if isinstance(entity, dict):
        for key in (
            "บทบาท", "visual_identity", "ลักษณะเด่น", "สีผิว", "ทรงผม",
            "ใบหน้า", "ดวงตา", "เสื้อผ้า", "must_include", "assumptions",
        ):
            value = entity.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            elif value is not None:
                values.append(str(value))
    text = " ".join(values).strip().casefold()
    return any(word in text for word in (
        "ผี", "วิญญาณ", "ภูต", "ปีศาจ", "อมนุษย์", "เหนือธรรมชาติ",
        "ghost", "spirit", "specter", "spectre", "supernatural entity",
    ))


GHOST_CINEMATIC_HORROR_EFFECTS = (
    "ทำให้เห็นทันทีว่าเป็นผีไทยเหนือธรรมชาติ ไม่ใช่เพียงคนแก่ คนป่วย หรือคนสกปรก: "
    "คงใบหน้าและตัวตนเดิม แต่เพิ่มผิวตายซีดเทาหม่นไม่สม่ำเสมอ เบ้าตาลึกคล้ำ "
    "ดวงตาเย็นชาผิดธรรมชาติ เส้นเลือดหรือรอยยุบใต้ผิว และท่าทางนิ่งไร้ชีวิตอย่างชัดเจน. "
    "เครื่องหมายเฉพาะตามคติของผีชนิดนั้นต้องเด่นและเป็นส่วนหนึ่งของร่างกายจริง "
    "ไม่ใช่หลอดไฟ LED สติ๊กเกอร์ เครื่องประดับ หรือแสงที่แปะบนผิว. "
    "ให้สมจริงแบบ practical supernatural horror makeup รายละเอียดผิวชัด แต่ไม่ใช้ gore. "
    "REFERENCE LIGHTING LOCK: พื้นหลังขาวล้วน high-key studio แสงขาวนุ่มสม่ำเสมอจากด้านหน้าและรอบทิศ "
    "เห็นรายละเอียดผม ใบหน้า เสื้อผ้า และรูปร่างชัด ไม่มีฉากมืด ไม่มีเงาดำ ไม่มีหมอก ไม่มีกลางคืน"
)


def build_ref_edit_prompt(comment, ghost=True):
    prompt = (
        "แก้ไข CHARACTER REFERENCE SHEET ที่แนบมาในประวัติ Ref เดิม. "
        "รักษาตัวละครเดิม ใบหน้าเดิม อายุเดิม ชนิดผีเดิม เครื่องหมายจำเพาะเดิม "
        "ทรงผม เสื้อผ้า รูปร่าง การแบ่งช่อง และมุมกล้องเดิมทั้งหมด. "
        "ห้ามเปลี่ยนเป็นตัวละครใหม่และห้ามเปลี่ยนชนิดผี. "
        "เปลี่ยนพื้นหลังทุกช่องเป็นขาวล้วน ใช้ high-key studio lighting แสงขาวนุ่มสม่ำเสมอ ให้เห็นรายละเอียดผม ใบหน้า เสื้อผ้า และรูปร่างชัด. "
        "ลบหัวเรื่อง NOTE ป้ายชื่อ คำอธิบาย และตัวอักษรทั้งหมดออกจากภาพ เพราะโปรแกรมจะเติมชื่อภายหลัง. "
        "ห้ามพื้นดำ ฉากกลางคืน หมอก ควัน วิวสถานที่ หรือแสงมืด. "
        "เพิ่มความน่ากลัวด้วยสีหน้า ดวงตา ผิว ท่าทาง และลักษณะเหนือธรรมชาติแบบสมจริงได้. "
    )
    if ghost:
        prompt += GHOST_CINEMATIC_HORROR_EFFECTS + ". "
    return prompt + "เปลี่ยนเฉพาะตามคำสั่งผู้ใช้ต่อไปนี้: " + str(comment or "").strip()


def install(g: dict, root: tk.Misc) -> tk.Misc:
    """Build this page and return its root frame."""
    globals().update(g)

    def _bind_ref_story_payload(payload):
        """Keep every Ref-side GPT request on the one main story history."""
        context_fn = g.get("get_main_story_request_context")
        if callable(context_fn):
            payload.update(context_fn())
        return payload

    def _advance_ref_story_history(result):
        update_fn = g.get("advance_main_story_history")
        if callable(update_fn):
            update_fn(result)

    lock_g = {"_selection_locks": {}, "_selection_lock_vars": []}
    export_ref_dir = g.get("EXPORT_REF", BASE / "ref")
    ref_page = tk.Frame(root, bg="#FAFAF7")
    g["ref_page"] = ref_page
    ref_name_var = tk.StringVar(value="")
    ref_outfit_var = tk.StringVar(value="อัตโนมัติตามเนื้อเรื่อง")
    ref_custom_outfit_var = tk.StringVar(value="")
    ref_settings_path = Path(BASE) / "ref_page_settings.json"
    try:
        _saved_ref_settings = json.loads(ref_settings_path.read_text(encoding="utf-8"))
    except Exception:
        _saved_ref_settings = {}
    ref_use_context_var = tk.BooleanVar(value=bool(_saved_ref_settings.get("use_context", False)))
    saved_story_type = normalize_story_type(_saved_ref_settings.get("story_type") or "auto")
    story_type_labels = [label for _story_type, label in STORY_TYPE_OPTIONS]
    story_type_by_label = {label: story_type for story_type, label in STORY_TYPE_OPTIONS}
    saved_story_type_label = next(
        (label for story_type, label in STORY_TYPE_OPTIONS if story_type == saved_story_type),
        story_type_labels[0],
    )
    ref_story_type_var = tk.StringVar(value=saved_story_type_label)
    g["_ref_selected_context"] = ""
    g["_ref_selected_name"] = ""
    g["_ref_selected_kind"] = ""
    g["_ref_manual_entry"] = True
    
    g["ref_name_var"] = ref_name_var
    g["ref_outfit_var"] = ref_outfit_var
    g["ref_custom_outfit_var"] = ref_custom_outfit_var
    g["ref_use_context_var"] = ref_use_context_var
    g["ref_story_type_var"] = ref_story_type_var
    
    box = tk.LabelFrame(ref_page, text="🎭 Ref", bg="#FAFAF7", fg="#1A1A1A", padx=10, pady=8)
    box.pack(fill="x", padx=10, pady=10)
    # Fixed pixel geometry keeps every form row on one exact vertical axis.
    # Character-based Label/Entry widths vary with bold text and Windows DPI.
    FORM_LABEL_WIDTH = 46
    FORM_FIELD_WIDTH = 300
    FORM_FIELD_HEIGHT = 34

    def _form_label(parent, text, *, bold=False):
        host = tk.Frame(parent, width=FORM_LABEL_WIDTH, height=FORM_FIELD_HEIGHT, bg="#FAFAF7")
        host.pack(side="left")
        host.pack_propagate(False)
        tk.Label(
            host, text=text, anchor="e", bg="#FAFAF7", fg="#111" if bold else "#333",
            font=(SNAPGEN_UI_FONT, 9, "bold" if bold else "normal"),
        ).pack(fill="both", expand=True)
        return host

    story_row = tk.Frame(box, bg="#FAFAF7")
    story_row.pack(fill="x", pady=(0, 6))
    get_ref_story_title = g.get("get_ref_story_title")
    ref_story_title_var = tk.StringVar(
        value=(get_ref_story_title() if callable(get_ref_story_title) else "")
    )
    ref_story_status_var = tk.StringVar(value="")
    _form_label(story_row, "เรื่อง:", bold=True)
    story_title_box = tk.Frame(
        story_row, width=FORM_FIELD_WIDTH, height=FORM_FIELD_HEIGHT,
        bg="#FFFFFF", highlightthickness=1, highlightbackground="#111111",
    )
    story_title_box.pack(side="left", padx=(6, 8))
    story_title_box.pack_propagate(False)
    tk.Label(story_title_box, textvariable=ref_story_title_var, anchor="w",
             bg="#FFFFFF", fg="#111", padx=7).pack(fill="both", expand=True)
    tk.Label(story_row, textvariable=ref_story_status_var, bg="#FAFAF7",
             fg="#6B7280").pack(side="left", padx=(0, 8))

    type_row = tk.Frame(box, bg="#FAFAF7")
    type_row.pack(fill="x", pady=(0, 6))
    _form_label(type_row, "ประเภทเรื่อง:", bold=True)
    ref_story_type_combo = _ttk.Combobox(
        type_row, textvariable=ref_story_type_var, values=story_type_labels,
        state="readonly", width=38,
    )
    ref_story_type_combo.pack(side="left", padx=(6, 8), ipady=4)
    tk.Label(
        type_row,
        text="อัตโนมัติ = อ่านจากบท | เปลี่ยนได้ก่อนสร้างตัวละคร",
        bg="#FAFAF7", fg="#6B7280",
    ).pack(side="left")

    def _prompt_ref_story_title():
        try:
            for line in (Path(BASE) / "prompt_ref_source.txt").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    return line.strip()[:60]
        except OSError:
            pass
        context_path = Path(BASE) / "prompt_ref_context.json"
        try:
            data = json.loads(context_path.read_text(encoding="utf-8"))
            story = data.get("story") if isinstance(data, dict) else {}
            for value in (story.get("title"), story.get("name"), data.get("title")):
                if isinstance(value, str) and value.strip():
                    return value.strip()[:60]
        except Exception:
            pass
        return "เรื่องจาก Prompt-Ref"

    ref_story_status_var.set("ใช้ประวัติหลักจาก Prompt-Ref")
    g["ref_story_title_var"] = ref_story_title_var

    row = tk.Frame(box, bg="#FAFAF7")
    row.pack(fill="x", pady=(0, 6))
    _form_label(row, "ชื่อ:")
    entry_wrap = tk.Frame(row, width=FORM_FIELD_WIDTH, height=FORM_FIELD_HEIGHT, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D1D5DB")
    entry_wrap.pack(side="left", padx=(6, 8))
    entry_wrap.pack_propagate(False)
    entry = tk.Entry(entry_wrap, textvariable=ref_name_var, relief="flat", bg="#FFFFFF", fg="#111")
    entry.pack(fill="x", padx=8, pady=6)
    placeholder = tk.Label(entry_wrap, text="ใส่ชื่อ", bg="#FFFFFF", fg="#B0B0B0", font=(SNAPGEN_UI_FONT, 9))
    placeholder.place(x=10, y=6)
    def _sync_placeholder(*_):
        try:
            if ref_name_var.get().strip(): placeholder.place_forget()
            else: placeholder.place(x=10, y=6)
        except Exception:
            pass
    ref_name_var.trace_add("write", _sync_placeholder)
    entry.bind("<FocusIn>", lambda _e: _sync_placeholder(), add="+")
    def _clear_ref_selected_context(_event=None):
        # Typing means manual Ref; context is used only after Select.
        g["_ref_selected_context"] = ""
        g["_ref_selected_name"] = ""
        g["_ref_selected_kind"] = ""
        g["_ref_manual_entry"] = True
        try:
            ref_outfit_combo.configure(state="readonly")
        except Exception:
            pass
    entry.bind("<KeyRelease>", _clear_ref_selected_context, add="+")

    def _save_ref_page_settings(*_args):
        try:
            ref_settings_path.write_text(
                json.dumps({
                    "use_context": bool(ref_use_context_var.get()),
                    "story_type": story_type_by_label.get(ref_story_type_var.get(), "auto"),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _current_ref_story_type_id():
        selected = normalize_story_type(story_type_by_label.get(ref_story_type_var.get(), "auto"))
        if selected != "auto":
            return selected
        try:
            context = json.loads((Path(BASE) / "prompt_ref_context.json").read_text(encoding="utf-8"))
            story = context.get("story") if isinstance(context, dict) else {}
            if isinstance(story, dict):
                return normalize_story_type(
                    story.get("story_type") or story.get("genre") or story.get("story_type_label")
                )
        except Exception:
            pass
        return "auto"

    ref_use_context_var.trace_add("write", _save_ref_page_settings)
    ref_story_type_var.trace_add("write", _save_ref_page_settings)
    tk.Checkbutton(
        row, text="ใช้ Context", variable=ref_use_context_var,
        bg="#FAFAF7", activebackground="#FAFAF7", fg="#374151",
        selectcolor="#FFFFFF", bd=0, highlightthickness=0,
        font=(SNAPGEN_UI_FONT, 9),
    ).pack(side="left", padx=(8, 4))
    
    # Select button — opens character/location list from prompt_ref_context.json.
    # Keep extraction local to this page so Ref does not break when Story Face or
    # the main GUI changes its helper functions.
    def _extract_ref_characters(context_text):
        characters = []

        def add_character(item):
            if not isinstance(item, dict):
                return
            name = str(item.get("name") or item.get("ชื่อ") or "").strip()
            if not name:
                return
            if any(str(x.get("name") or "").strip() == name for x in characters):
                return
            normalized = {"name": name}
            for key in (
                "อายุ", "เพศ", "บทบาท", "รูปร่าง", "ส่วนสูง", "สีผิว",
                "ทรงผม", "ใบหน้า", "ดวงตา", "ดวงต", "เสื้อผ้า",
                "visual_identity", "ลักษณะเด่น", "must_include",
                "must_not_include", "assumptions",
            ):
                value = item.get(key)
                if value is not None and str(value).strip() and str(value).strip() != "ไม่ระบุ":
                    # Normalize the older typo key so prompt building can use it.
                    normalized["ดวงตา" if key == "ดวงต" else key] = value
            characters.append(normalized)

        try:
            import json as _json
            data = _json.loads(context_text)
            if isinstance(data, dict):
                for item in data.get("characters", []) or data.get("ตัวละคร", []) or []:
                    add_character(item)
                if characters:
                    return characters
        except Exception:
            pass

        current = {}
        section = ""
        for raw in str(context_text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("##"):
                section = "character" if any(word in line for word in ("ตัวละคร", "Character", "character")) else ""
                continue
            if section != "character":
                continue
            if line.startswith("-"):
                if current:
                    add_character(current)
                item = line.lstrip("- ").strip()
                if ":" in item:
                    name, rest = item.split(":", 1)
                elif "—" in item:
                    name, rest = item.split("—", 1)
                else:
                    name, rest = item, ""
                current = {"name": name.strip()}
                if rest.strip():
                    current["visual_identity"] = rest.strip()
                continue
            if current:
                for field in ("อายุ", "เพศ", "บทบาท", "สีผิว", "ทรงผม", "ใบหน้า", "เสื้อผ้า", "ลักษณะเด่น"):
                    if line.startswith(field + ":"):
                        current[field] = line.split(":", 1)[1].strip()
                        break
        if current:
            add_character(current)
        return characters

    def _apply_ref_character(character, selector=None):
        name = str(character.get("name", "")).strip()
        ref_name_var.set(name)
        # Build prompt from context details (not just name)
        labels = (
            ("อายุ", "age"), ("บทบาท", "role/background"), ("สีผิว", "skin"),
            ("ทรงผม", "hair"), ("ใบหน้า", "face"), ("เสื้อผ้า", "clothes"),
            ("ลักษณะเด่น", "distinctive traits"), ("อารมณ์", "personality/mood"),
        )
        details = [f"{english}: {character[field]}" for field, english in labels if character.get(field) and str(character[field]).strip() and str(character[field]).strip() != "ไม่ระบุ"]
        context = "; ".join(details) if details else "Thai character, no specific details in context"
        # Store context for generate_ref to use
        g["_ref_selected_context"] = context
        g["_ref_selected_name"] = name
        g["_ref_selected_kind"] = "character"
        g["_ref_manual_entry"] = False
        ref_use_context_var.set(True)
        try:
            ref_outfit_combo.configure(state="readonly")
        except Exception:
            pass
        lock_text = _builder_set_selection_lock(lock_g, "character", name)
        summary = " · ".join(character.get(key, "") for key in ("อายุ", "สีผิว", "ทรงผม") if character.get(key) and character.get(key) != "ไม่ระบุ")
        _ref_log(lock_text + (f" — {summary}" if summary else ""))
        if selector is not None:
            selector.destroy()

    def _extract_ref_locations(context_text):
        items = []

        def add_place(name, detail=""):
            name = str(name or "").strip()
            detail = str(detail or "").strip()
            if not name or name == "ไม่ระบุ":
                return
            if not any(x.get("name") == name for x in items):
                items.append({"name": name, "detail": detail})

        try:
            import json as _json
            data = _json.loads(context_text)
            if isinstance(data, dict):
                for key in ("locations", "places", "key_places", "สถานที่"):
                    for place in data.get(key, []) or []:
                        if isinstance(place, dict):
                            name = place.get("name") or place.get("place") or place.get("location") or place.get("สถานที่")
                            detail = place.get("detail") or place.get("description") or place.get("note") or place.get("รายละเอียด") or place.get("บรรยากาศ")
                            add_place(name, detail)
                        else:
                            add_place(place)
                story = data.get("story") if isinstance(data.get("story"), dict) else {}
                for place in story.get("key_places", []) or []:
                    add_place(place)
                for place in data.get("scene_map", []) or []:
                    if isinstance(place, dict):
                        add_place(
                            place.get("name") or place.get("place") or place.get("location"),
                            place.get("detail") or place.get("description") or place.get("note"),
                        )
                add_place(
                    data.get("main_location") or data.get("location") or data.get("สถานที่หลัก")
                    or story.get("main_location")
                )
                # A newly summarized Context already contains a complete
                # Location Bible.  Prefer it over an older external cache.
                if any(
                    isinstance(place, dict) and (place.get("visual_description") or place.get("views"))
                    for place in data.get("locations", []) or []
                ):
                    return items
        except Exception:
            pass

        section = ""
        for raw in str(context_text or "").splitlines():
            line = raw.strip()
            if line.startswith("##"):
                section = "location" if any(word in line for word in ("สถานที่", "ฉาก", "Location", "location")) else ""
                continue
            if section != "location" or not line.startswith("-"):
                continue
            item = line.lstrip("- ").strip()
            if not item:
                continue
            if ":" in item:
                name, detail = item.split(":", 1)
            elif "—" in item:
                name, detail = item.split("—", 1)
            else:
                name, detail = item, ""
            add_place(name, detail)
        try:
            locked_items = []
            for target in (_load_location_bible().get("targets") or []):
                if isinstance(target, dict):
                    parent = str(target.get("parent_location") or "").strip()
                    fact = str(target.get("story_fact") or "").strip()
                    detail = " · ".join(x for x in (f"อยู่ใน {parent}" if parent else "", fact) if x)
                    name = str(target.get("name") or "").strip()
                    if name and not any(x.get("name") == name for x in locked_items):
                        locked_items.append({"name": name, "detail": detail})
            if locked_items:
                return locked_items
        except Exception:
            pass
        return items

    def _apply_ref_location(location, selector=None):
        name = str(location.get("name", "")).strip()
        ref_name_var.set(name)
        detail = str(location.get("detail", "")).strip()
        g["_ref_selected_context"] = detail
        g["_ref_selected_name"] = name
        g["_ref_selected_kind"] = "location"
        g["_ref_manual_entry"] = False
        ref_use_context_var.set(True)
        try:
            ref_outfit_combo.configure(state="disabled")
        except Exception:
            pass
        lock_text = _builder_set_selection_lock(lock_g, "location", name)
        _ref_log(lock_text + (f" — {detail[:80]}" if detail else ""))
        if selector is not None:
            selector.destroy()
    
    def _open_ref_selector():
        context = _load_ref_context()
        characters = _extract_ref_characters(context)
        locations = _extract_ref_locations(context)
        # A failed/empty Context update must not make the character picker
        # disappear completely. Recover only names from the uploaded story;
        # full Context is still used exclusively after the user clicks Select.
        if not characters:
            try:
                source_path = BASE / "prompt_ref_source.txt"
                source = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
                found_names = []
                patterns = (
                    r"ผมชื่อ\s*([ก-๙A-Za-z][ก-๙A-Za-z0-9_-]{1,24})",
                    r"(?:เธอ|เขา|ผู้หญิง|ผู้ชาย)?\s*ชื่อ\s+([ก-๙A-Za-z][ก-๙A-Za-z0-9_-]{1,24})",
                    r"(?m)^\s*([ก-๙A-Za-z][ก-๙A-Za-z0-9 _-]{0,24})\s*[:：]",
                )
                blocked = {"เรื่อง", "เสียง", "เวลา", "ห้อง", "สถานที่", "จังหวัด", "ผม", "เธอ", "เขา"}
                for pattern in patterns:
                    for match in re.finditer(pattern, source):
                        name = " ".join(match.group(1).split()).strip(" -_")
                        if name and name not in blocked and name not in found_names:
                            found_names.append(name)
                characters = [{"name": name, "บทบาท": "ตัวละครที่พบในบทหลัก"} for name in found_names[:20]]
                if characters:
                    _ref_log(f"[Select] Context ยังไม่มีตัวละคร — กู้รายชื่อจากบทหลัก {len(characters)} คน")
            except Exception:
                pass
        if not characters and not locations:
            _ref_log("[Select] ไม่พบตัวละคร/สถานที่ใน prompt_ref_context (json/txt)")
            return
        selector = tk.Toplevel(root)
        selector.title("เลือกตัวละคร/สถานที่ — สำหรับ Ref")
        selector.configure(bg="#FAFAF7")
        selector.resizable(False, False)
        selector.transient(root)
        tk.Label(selector, text="เลือกตัวละคร / สถานที่", bg="#FAFAF7", fg="#111", font=(SNAPGEN_UI_FONT, 11, "bold")).pack(fill="x", padx=14, pady=(12, 6))
        if characters:
            tk.Label(selector, text="ตัวละคร", bg="#FAFAF7", fg="#6B7280", font=(SNAPGEN_UI_FONT, 9, "bold")).pack(fill="x", padx=14, pady=(2, 2))
        for character in characters:
            name = character["name"]
            summary = " · ".join(character.get(key, "") for key in ("อายุ", "สีผิว", "ทรงผม") if character.get(key))
            label = name + (f"  —  {summary}" if summary else "")
            tk.Button(selector, text=label, anchor="w", command=lambda c=character: _apply_ref_character(c, selector), bg="#FFFFFF", fg="#111", activebackground="#E0E7FF", activeforeground="#111", relief="flat", bd=0, padx=12, pady=8).pack(fill="x", padx=12, pady=3)
        if locations:
            tk.Label(selector, text="สถานที่", bg="#FAFAF7", fg="#6B7280", font=(SNAPGEN_UI_FONT, 9, "bold")).pack(fill="x", padx=14, pady=(8 if characters else 2, 2))
        for location in locations:
            name = location["name"]
            detail = str(location.get("detail", "")).strip()
            label = "📍 " + name + (f"  —  {detail[:70]}" if detail else "")
            tk.Button(selector, text=label, anchor="w", command=lambda loc=location: _apply_ref_location(loc, selector), bg="#FFFFFF", fg="#111", activebackground="#DCFCE7", activeforeground="#111", relief="flat", bd=0, padx=12, pady=8).pack(fill="x", padx=12, pady=3)
        discover_btn = tk.Button(
            selector, text="🔎 ค้นหาสถานที่ที่มีฉากจริงในบท", bg="#059669", fg="white",
            activebackground="#047857", activeforeground="white", relief="flat", bd=0, padx=12, pady=8,
        )
        def discover_more_locations():
            discover_btn.config(state="disabled", text="กำลังวิเคราะห์บท...")
            _ref_log("[location] กำลังค้นหาเฉพาะสถานที่ที่มีเหตุการณ์เกิดขึ้นจริงในบท")
            def worker():
                try:
                    lock = globals().get("_bridge_queue_lock")
                    if lock:
                        with lock:
                            targets, _reused = _discover_location_targets_via_context()
                    else:
                        targets, _reused = _discover_location_targets_via_context()
                    def done():
                        selector.destroy()
                        _ref_log(f"[location] พบ {len(targets)} สถานที่ — เปิดรายการใหม่แล้ว")
                        _open_ref_selector()
                    root.after(0, done)
                except Exception as e:
                    root.after(0, lambda e=e: (discover_btn.config(state="normal", text="🔎 ลองค้นหาอีกครั้ง"), _ref_log(f"[location] ERROR: {e}")))
            threading.Thread(target=worker, daemon=True).start()
        discover_btn.config(command=discover_more_locations)
        discover_btn.pack(fill="x", padx=12, pady=(10, 12))
        selector.grab_set()
    

    # --- Character outfit selector. It changes clothes only; identity stays locked. ---
    outfit_options = (
        "อัตโนมัติตามเนื้อเรื่อง",
        "ชุดอยู่บ้าน",
        "ชุดทำงาน",
        "ชุดลำลองออกนอกบ้าน",
        "ชุดนอน",
        "ชุดสุภาพ/ทางการ",
        "กำหนดชุดเอง",
    )
    outfit_frame = tk.Frame(box, bg="#FAFAF7")
    outfit_frame.pack(fill="x", pady=(0, 6))
    _form_label(outfit_frame, "ชุด:")
    outfit_combo_box = tk.Frame(
        outfit_frame, width=FORM_FIELD_WIDTH, height=FORM_FIELD_HEIGHT, bg="#FAFAF7"
    )
    outfit_combo_box.pack(side="left", padx=(6, 8))
    outfit_combo_box.pack_propagate(False)
    ref_outfit_combo = _ttk.Combobox(
        outfit_combo_box,
        textvariable=ref_outfit_var,
        values=outfit_options,
        state="readonly",
        font=(SNAPGEN_UI_FONT, 9),
    )
    ref_outfit_combo.pack(fill="both", expand=True)
    custom_outfit_entry = tk.Entry(
        outfit_frame,
        textvariable=ref_custom_outfit_var,
        bg="#FFFFFF",
        fg="#111111",
        relief="solid",
        bd=1,
        font=(SNAPGEN_UI_FONT, 9),
    )

    def _sync_outfit_selector(_event=None):
        mode = ref_outfit_var.get().strip() or outfit_options[0]
        if mode == "กำหนดชุดเอง":
            if not custom_outfit_entry.winfo_manager():
                custom_outfit_entry.pack(side="left", fill="x", expand=True, padx=(2, 6), ipady=5)
            custom_outfit_entry.focus_set()
        else:
            custom_outfit_entry.pack_forget()
        try:
            _builder_set_selection_lock(lock_g, "outfit", mode)
        except Exception:
            pass

    ref_outfit_combo.bind("<<ComboboxSelected>>", _sync_outfit_selector, add="+")
    ref_custom_outfit_var.trace_add(
        "write",
        lambda *_: _builder_set_selection_lock(
            lock_g,
            "outfit",
            ref_custom_outfit_var.get().strip() or "กำหนดชุดเอง",
        ) if ref_outfit_var.get() == "กำหนดชุดเอง" else None,
    )
    g["ref_outfit_combo"] = ref_outfit_combo

    # --- Ref image attachment: paste/select with thumbnail, same as Prop ---
    ref_attach_path = [None]
    ref_attach_preview_photo = [None]
    ref_attach_paste_busy = [False]
    ref_attach_last_paste = {"sig": None, "time": 0.0}
    g["ref_attach_path"] = ref_attach_path
    ref_attach_frame = tk.Frame(box, bg="#FAFAF7")
    ref_attach_frame.pack(fill="x", padx=(FORM_LABEL_WIDTH + 6, 0), pady=(0, 6))
    ref_attach_thumb = tk.Label(
        ref_attach_frame, text="วางรูป / เลือกไฟล์", bg="#FFFFFF", fg="#6B7280",
        width=18, height=6, relief="solid", bd=1, anchor="center",
    )
    ref_attach_thumb.pack(side="left", padx=(0, 8), pady=2)
    ref_attach_controls = tk.Frame(ref_attach_frame, bg="#FAFAF7")
    ref_attach_controls.pack(side="left", fill="x", expand=True)
    ref_attach_info = tk.StringVar(value="ยังไม่มีรูปแนบ")
    tk.Label(ref_attach_controls, textvariable=ref_attach_info, bg="#FAFAF7", fg="#374151", anchor="w").pack(fill="x", pady=(0, 5))

    def _set_ref_attach(path):
        p = Path(str(path or "")).expanduser()
        if not p.exists() or not p.is_file():
            _ref_log(f"[ref-attach] ไม่พบไฟล์: {p}")
            return
        old_name = Path(ref_attach_path[0]).name if ref_attach_path[0] else ""
        if old_name:
            _builder_remove_selection_lock(lock_g, "reference", old_name)
        ref_attach_path[0] = str(p)
        ref_attach_info.set(p.name)
        try:
            from PIL import Image, ImageTk
            image = Image.open(p)
            image.thumbnail((150, 110), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            ref_attach_preview_photo[0] = photo
            ref_attach_thumb.config(image=photo, text="", width=150, height=110)
        except Exception:
            ref_attach_preview_photo[0] = None
            ref_attach_thumb.config(image="", text=p.name[:28], width=18, height=6)
        _ref_log(_builder_set_selection_lock(lock_g, "reference", p.name, append=True))

    def _paste_ref_attach():
        if ref_attach_paste_busy[0]:
            return
        ref_attach_paste_busy[0] = True
        try:
            from PIL import Image, ImageGrab
            clip = ImageGrab.grabclipboard()
            if clip is None:
                _ref_log("[ref-attach] Clipboard ไม่มีรูป")
                return
            attach_dir = Path(export_ref_dir) / "_attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            now = time.time()
            if isinstance(clip, Image.Image):
                sample = clip.convert("RGB").resize((16, 16))
                sig = ("image", clip.size, clip.mode, sample.tobytes())
                if ref_attach_last_paste["sig"] == sig and now - ref_attach_last_paste["time"] < 1.0:
                    return
                ref_attach_last_paste.update({"sig": sig, "time": now})
                out = attach_dir / f"ref_clip_{time.strftime('%Y%m%d-%H%M%S')}.png"
                clip.convert("RGBA").save(out)
                _set_ref_attach(out)
                return
            if isinstance(clip, (list, tuple)):
                for item in clip:
                    src = Path(str(item))
                    if src.exists() and src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                        sig = ("file", str(src.resolve()), src.stat().st_mtime_ns, src.stat().st_size)
                        if ref_attach_last_paste["sig"] == sig and now - ref_attach_last_paste["time"] < 1.0:
                            return
                        ref_attach_last_paste.update({"sig": sig, "time": now})
                        out = attach_dir / src.name
                        if src.resolve() != out.resolve():
                            shutil.copy2(src, out)
                        _set_ref_attach(out)
                        return
            _ref_log("[ref-attach] Clipboard ไม่ใช่รูปที่ใช้ได้")
        except Exception as exc:
            _ref_log(f"[ref-attach] วางรูปไม่สำเร็จ: {exc}")
        finally:
            ref_attach_paste_busy[0] = False

    def _choose_ref_attach():
        from tkinter import filedialog
        start = ref_attach_path[0] if ref_attach_path[0] and os.path.isdir(os.path.dirname(ref_attach_path[0])) else str(export_ref_dir)
        p = filedialog.askopenfilename(title="เลือกรูปอ้างอิง", initialdir=start, filetypes=[("รูป", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if p:
            _set_ref_attach(p)

    def _clear_ref_attach():
        old_name = os.path.basename(ref_attach_path[0]) if ref_attach_path[0] else ""
        ref_attach_path[0] = None
        ref_attach_preview_photo[0] = None
        ref_attach_info.set("ยังไม่มีรูปแนบ")
        ref_attach_thumb.config(image="", text="วางรูป / เลือกไฟล์", width=18, height=6)
        _builder_remove_selection_lock(lock_g, "reference", old_name)
        _ref_log("[ref-attach] ล้างรูปแนบ")

    tk.Button(ref_attach_controls, text="📋 วางรูป", command=_paste_ref_attach, bg="#475569", fg="white", relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=(0, 4))
    tk.Button(ref_attach_controls, text="📎 เลือกไฟล์", command=_choose_ref_attach, bg="#475569", fg="white", relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)
    tk.Button(ref_attach_controls, text="ล้าง", command=_clear_ref_attach, bg="#DC2626", fg="white", relief="flat", padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)

    def _install_ref_image_drop():
        try:
            project_root = Path(__file__).resolve().parent.parent
            vendor = project_root / "vendor"
            if str(vendor) not in sys.path:
                sys.path.insert(0, str(vendor))
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD.require(root)
            allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

            def dropped(event):
                files = root.tk.splitlist(event.data)
                source = next((Path(item) for item in files if Path(item).suffix.lower() in allowed), None)
                ref_attach_thumb.configure(highlightthickness=0)
                if source is None or not source.is_file():
                    _ref_log("[ลากรูป] รับเฉพาะไฟล์รูป PNG/JPG/WEBP/BMP")
                else:
                    _set_ref_attach(source)
                    _ref_log(f"[ลากรูป] ใส่สล็อต Ref: {source.name}")
                return event.action

            ref_attach_thumb.drop_target_register(DND_FILES)
            ref_attach_thumb.dnd_bind(
                "<<DropEnter>>",
                lambda event: (ref_attach_thumb.configure(highlightthickness=2, highlightbackground="#2563EB"), event.action)[1],
            )
            ref_attach_thumb.dnd_bind(
                "<<DropLeave>>",
                lambda event: (ref_attach_thumb.configure(highlightthickness=0), event.action)[1],
            )
            ref_attach_thumb.dnd_bind("<<Drop>>", dropped)
        except Exception as exc:
            _ref_log(f"[ลากรูป] เปิดใช้งานไม่ได้: {exc}")

    _install_ref_image_drop()
    g["ref_attach_preview_photo"] = ref_attach_preview_photo
    # ---
    ref_select_btn = tk.Button(row, text="Select", command=_open_ref_selector, bg="#2563EB", fg="white", activebackground="#1D4ED8", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    g["ref_select_btn"] = ref_select_btn
    g["_open_ref_selector"] = _open_ref_selector
    g["_apply_ref_character"] = _apply_ref_character
    g["_apply_ref_location"] = _apply_ref_location

    log = _builder_make_log_box(box)
    log.pack(fill="x", pady=(8, 0))
    g["ref_log_box"] = log
    def _ref_log(msg):
        _builder_append_log(log, msg)
    g["_ref_log"] = _ref_log

    def _ref_bridge_ready():
        try:
            import json as _json
            import urllib.request as _urlreq
            base_fn = globals().get("_chatgpt_api_base")
            if callable(base_fn):
                base_url = str(base_fn()).rstrip("/").replace("/v1", "")
            else:
                host = globals().get("BRIDGE_HOST", "127.0.0.1")
                port = globals().get("BRIDGE_PORT", 8000)
                base_url = f"http://{host}:{port}"
            req = _urlreq.Request(base_url + "/health", headers={"Authorization": "Bearer local-dev-key"})
            with _urlreq.urlopen(req, timeout=3) as resp:
                data = _json.loads(resp.read().decode("utf-8", errors="replace"))
            if not data.get("ok"):
                return False, "Bridge ยังไม่พร้อม"
            if not data.get("account"):
                return False, "Bridge ยังไม่มี Account — ไป Settings > Bridge แล้วกดเปิดและจับ Account อัตโนมัติ"
            return True, f"Bridge พร้อม: {data.get('account_email') or data.get('account')}"
        except Exception as exc:
            return False, "Bridge ติดต่อไม่ได้ — ไป Settings > Bridge แล้วกดเริ่ม/ตรวจสอบ: " + str(exc)
    g["_ref_bridge_ready"] = _ref_bridge_ready
    
    gallery = tk.LabelFrame(ref_page, text="แกลเลอรี", bg="#FAFAF7", fg="#1A1A1A", padx=8, pady=6)
    gallery.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    gallery_canvas = tk.Canvas(gallery, bg="#FAFAF7", highlightthickness=0)
    gallery_scroll = tk.Scrollbar(gallery, orient="vertical", command=gallery_canvas.yview)
    gallery_canvas.configure(yscrollcommand=gallery_scroll.set)
    gallery_inner = tk.Frame(gallery_canvas, bg="#FAFAF7")
    _rg_window = gallery_canvas.create_window((0, 0), window=gallery_inner, anchor="nw")
    gallery_canvas.pack(side="left", fill="both", expand=True)
    gallery_scroll.pack(side="right", fill="y")
    g["ref_gallery_inner"] = gallery_inner
    ref_gallery_images = []  # keep Tk image refs alive
    g["ref_gallery_images"] = ref_gallery_images
    ref_gallery_state_path = Path(BASE) / "meta" / "ref_gallery.json"
    ref_gallery_paths = []
    ref_gallery_cards = []

    def _save_ref_gallery():
        ref_gallery_state_path.parent.mkdir(parents=True, exist_ok=True)
        ref_gallery_state_path.write_text(
            json.dumps({"paths": ref_gallery_paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _relayout_ref_gallery():
        for item in ref_gallery_cards:
            try:
                item.pack_forget()
                item.pack(fill="x", pady=2)
            except Exception:
                pass
        try:
            gallery_canvas.yview_moveto(0)
            root.after_idle(_rg_sync)
        except Exception:
            pass

    def _rg_sync(_e=None):
        gallery_canvas.configure(scrollregion=gallery_canvas.bbox("all") or (0, 0, 0, 0))
        try:
            gallery_canvas.itemconfigure(_rg_window, width=max(gallery_canvas.winfo_width() - 4, 1))
        except Exception:
            pass

    def _rg_on_mousewheel(event):
        try:
            if not gallery_canvas.winfo_exists():
                return
            first, last = gallery_canvas.yview()
            if float(last) - float(first) >= 0.999:
                return
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return
            gallery_canvas.yview_scroll(int(-1 * (delta / 120)), "units")
            return "break"
        except Exception:
            return

    def _rg_bind_wheel(widget):
        try:
            widget.bind("<Enter>", lambda _e: gallery_canvas.bind_all("<MouseWheel>", _rg_on_mousewheel), add="+")
            widget.bind("<Leave>", lambda _e: gallery_canvas.unbind_all("<MouseWheel>"), add="+")
            widget.bind("<MouseWheel>", _rg_on_mousewheel, add="+")
        except Exception:
            pass

    gallery_inner.bind("<Configure>", _rg_sync)
    gallery_canvas.bind("<Configure>", _rg_sync)
    for _w in (gallery, gallery_canvas, gallery_inner, gallery_scroll):
        _rg_bind_wheel(_w)

    def _normalize_ref_output(path, name):
        """Enforce Ref sheet layout after image generation.

        Preserve generated five-panel layout and add one deterministic name
        strip. Never crop/rearrange panels here; layout belongs to the prompt.
        """
        source = Path(str(path or ""))
        if not source.is_file():
            return str(path)
        try:
            from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

            with Image.open(source) as opened:
                if opened.info.get("snapgen_ref_layout") == "v2":
                    return str(source)
                image = opened.convert("RGB")
            width, height = image.size
            if width < 400 or height < 400:
                return str(source)

            strip_h = max(54, round(height * 0.06))
            content_h = height - strip_h
            canvas = Image.new("RGB", (width, height), "#F3F4F6")
            canvas.paste(image.resize((width, content_h), Image.Resampling.LANCZOS), (0, 0))

            draw = ImageDraw.Draw(canvas)
            font = None
            for font_path in (
                Path(r"C:\Windows\Fonts\LeelawUI.ttf"),
                Path(r"C:\Windows\Fonts\tahoma.ttf"),
                Path(r"C:\Windows\Fonts\segoeui.ttf"),
            ):
                if font_path.is_file():
                    try:
                        font = ImageFont.truetype(str(font_path), max(18, round(height * 0.028)))
                        break
                    except Exception:
                        pass
            if font is None:
                font = ImageFont.load_default()
            label = " ".join(str(name or "").split()).strip() or source.stem
            label = f"ชื่อ: {label}"
            bbox = draw.textbbox((0, 0), label, font=font)
            x = max(12, (width - (bbox[2] - bbox[0])) // 2)
            y = content_h + max(4, (strip_h - (bbox[3] - bbox[1])) // 2)
            draw.text((x, y), label, fill="#374151", font=font)

            temp = source.with_suffix(source.suffix + ".refnorm.tmp")
            if source.suffix.lower() == ".png":
                metadata = PngImagePlugin.PngInfo()
                metadata.add_text("snapgen_ref_layout", "v2")
                canvas.save(temp, format="PNG", pnginfo=metadata)
            else:
                canvas.save(temp, format="PNG")
            os.replace(str(temp), str(source))
            return str(source)
        except Exception as exc:
            _ref_log(f"[Ref layout] จัดสัดส่วน/ป้ายชื่อไม่สำเร็จ ใช้รูปเดิม: {exc}")
            return str(path)

    def _ref_gallery_add(path, prepend=True, is_ghost=False, persist=True):
        path = str(Path(path))
        if persist and path not in ref_gallery_paths:
            ref_gallery_paths.insert(0 if prepend else len(ref_gallery_paths), path)
            _save_ref_gallery()
        card = tk.Frame(gallery_inner, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E5E7EB")
        thumb_box = tk.Frame(card, bg="#FFFFFF", width=96, height=96)
        thumb_box.pack(side="left", padx=6, pady=6)
        thumb_box.pack_propagate(False)
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((96, 96), Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
            ref_gallery_images.append(photo)
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

        def _edit_ref_from_comment(p=path):
            comment = comment_var.get().strip()
            if not comment:
                _ref_log("[แก้ Ref] พิมพ์สิ่งที่ต้องการแก้ก่อน")
                return
            if not Path(p).is_file():
                _ref_log(f"[แก้ Ref] ไม่พบรูป: {p}")
                return
            comment_entry.config(state=tk.DISABLED)
            edit_btn.config(state=tk.DISABLED, text="กำลังแก้...")

            def worker():
                try:
                    encoder = g.get("_encode_image_b64") or globals().get("_encode_image_b64")
                    if not callable(encoder):
                        raise RuntimeError("ไม่พบตัวเข้ารหัสรูป")
                    prompt = build_ref_edit_prompt(comment, ghost=is_ghost)
                    payload = {
                        "model": "auto", "prompt": prompt, "n": 1,
                        "aspect_ratio": "1:1", "history_and_training_disabled": False,
                        "images": [encoder(p)], "_use_ref_story_history": True,
                    }
                    out = g["_do_image_request"](
                        payload, is_edit=True, prompt=prompt,
                        name_hint=f"{Path(p).stem}_แก้ไข", raw_prompt=comment,
                        output_dir=str(export_ref_dir),
                    )
                    out = _normalize_ref_output(out, str(g.get("_ref_selected_name") or Path(p).stem))
                    root.after(0, lambda out=out: (
                        _ref_gallery_add(str(out), True, is_ghost=is_ghost),
                        _ref_log(f"[แก้ Ref] ✓ {out}"), _notify_done(),
                    ))
                except Exception as exc:
                    root.after(0, lambda exc=exc: _ref_log(f"[แก้ Ref] ERROR: {exc}"))
                finally:
                    root.after(0, lambda: (
                        comment_entry.config(state=tk.NORMAL),
                        edit_btn.config(state=tk.NORMAL, text="ส่งแก้ไข"),
                    ))

            threading.Thread(target=worker, daemon=True).start()

        edit_btn = tk.Button(
            comment_row, text="ส่งแก้ไข", command=_edit_ref_from_comment,
            bg="#059669", activebackground="#10B981", fg="white",
            relief="flat", font=(SNAPGEN_UI_FONT, 8, "bold"),
        )
        edit_btn.pack(side="left", padx=(4, 0))
        comment_entry.bind("<Return>", lambda _event: _edit_ref_from_comment(), add="+")
        tk.Button(card, text="📂 เปิด", command=lambda p=path: subprocess.Popen(["explorer", "/select,", p])).pack(side="right", padx=4, pady=4)
        ref_gallery_cards.insert(0 if prepend else len(ref_gallery_cards), card)
        _relayout_ref_gallery()
    g["_ref_gallery_add"] = _ref_gallery_add
    
    def _clean_character_ref_context(line):
        import re
        text = str(line or "").strip()
        text = re.sub(r"^.*?:", "", text, count=1).strip()
        m = re.search(r"ลักษณะภาพ\s*:\s*(.*?)(?:\s*อารมณ์\s*:|\s*@ref\s*:|$)", line)
        if m:
            text = m.group(1).strip()
        banned = ("ห้อง", "ผนัง", "ประตู", "ขอบประตู", "สถานที่", "ฉาก", "บริบท", "โต๊ะ", "เตียง", "รถ", "หลังห้อง", "หน้าห้อง", "เลขยันต์ป้องกัน")
        parts = re.split(r"[,;。.!]|\s+และ\s+", text)
        kept = []
        for part in parts:
            p = part.strip(" .;,")
            if not p:
                continue
            if any(b in p for b in banned):
                continue
            kept.append(p)
        return ", ".join(kept)[:140]
    
    def _extract_character_appearance(name):
        """อ่าน block ตัวละครจาก prompt_ref_context (JSON หรือ markdown txt) ดึงทุก field ใบหน้า/รูปกาย"""
        import re, json
        # Try JSON first (newer format)
        json_p = BASE / "prompt_ref_context.json"
        if json_p.is_file():
            try:
                data = json.loads(json_p.read_text(encoding="utf-8"))
                for ch in data.get("characters", []):
                    if str(ch.get("name", "")).strip() == name:
                        fields = {}
                        for field in ["อายุ", "เสื้อผ้า", "สีผิว", "ทรงผม", "ใบหน้า", "ลักษณะเด่น"]:
                            val = str(ch.get(field, "")).strip()
                            if val and val != "ไม่ระบุ":
                                fields[field] = val
                        parts = []
                        if "อายุ" in fields: parts.append(f"age: {fields['อายุ']}")
                        if "สีผิว" in fields: parts.append(f"skin: {fields['สีผิว']}")
                        if "ทรงผม" in fields: parts.append(f"hair: {fields['ทรงผม']}")
                        if "ใบหน้า" in fields: parts.append(f"face: {fields['ใบหน้า']}")
                        if "เสื้อผ้า" in fields: parts.append(f"clothes: {fields['เสื้อผ้า']}")
                        if "ลักษณะเด่น" in fields: parts.append(f"distinctive: {fields['ลักษณะเด่น']}")
                        return ". ".join(parts)
            except Exception:
                pass
        # Fallback to markdown txt
        try:
            lines = _load_ref_context().splitlines()
        except Exception:
            return ""
        in_block = False
        block_lines = []
        for raw in lines:
            line = raw.strip()
            if line.startswith("- **") and name in line:
                in_block = True
                block_lines.append(line)
                continue
            if in_block:
                if line.startswith("- **") or line.startswith("##") or line.startswith("###"):
                    break
                if line:
                    block_lines.append(line)
        if not block_lines:
            return ""
        fields = {}
        for bl in block_lines:
            bl = re.sub(r"[\ue200-\ue2ff]\S*?[\ue201]", "", bl).strip()
            bl = re.sub(r"filecite\S*", "", bl).strip()
            for field in ["อายุ", "เสื้อผ้า", "สีผิว", "ทรงผม", "ใบหน้า", "ลักษณะเด่น"]:
                m = re.search(rf"{field}\s*:\s*(.+?)(?:\s*\(สมมุติเพื่อภาพ\))?(?:\s*$)", bl)
                if m:
                    val = m.group(1).strip()
                    if val and val != "ไม่ระบุ":
                        fields[field] = val
        parts = []
        if "อายุ" in fields: parts.append(f"age: {fields['อายุ']}")
        if "สีผิว" in fields: parts.append(f"skin: {fields['สีผิว']}")
        if "ทรงผม" in fields: parts.append(f"hair: {fields['ทรงผม']}")
        if "ใบหน้า" in fields: parts.append(f"face: {fields['ใบหน้า']}")
        if "เสื้อผ้า" in fields: parts.append(f"clothes: {fields['เสื้อผ้า']}")
        if "ลักษณะเด่น" in fields: parts.append(f"distinctive: {fields['ลักษณะเด่น']}")
        return ". ".join(parts)
    
    def _context_entity(name, requested_kind=""):
        """Return the matching entity plus film-level context from Prompt Context."""
        import json as _json
        raw = _load_ref_context()
        try:
            data = _json.loads(raw) if raw.strip() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            return requested_kind, {}, {}
        wanted = str(name or "").strip().casefold()
        kind = str(requested_kind or "").strip().lower()
        found = {}
        if kind != "location":
            for item in data.get("characters", []) or []:
                if isinstance(item, dict) and str(item.get("name", "")).strip().casefold() == wanted:
                    found = item
                    kind = "character"
                    break
        if not found and kind != "character":
            pools = []
            pools.extend(data.get("locations", []) or [])
            pools.extend(data.get("scene_map", []) or [])
            story = data.get("story") if isinstance(data.get("story"), dict) else {}
            pools.extend(story.get("key_places", []) or [])
            for item in pools:
                item_name = (item.get("name") or item.get("place") or item.get("location")) if isinstance(item, dict) else item
                if str(item_name or "").strip().casefold() == wanted:
                    found = item if isinstance(item, dict) else {"name": item_name}
                    kind = "location"
                    break
        story = data.get("story") if isinstance(data.get("story"), dict) else {}
        film = {
            "summary": str(story.get("summary") or "").strip(),
            "era": str(story.get("era") or "").strip(),
            "main_location": str(story.get("main_location") or "").strip(),
            "story_type": str(story.get("story_type") or story.get("genre") or story.get("story_type_label") or data.get("story_type") or data.get("story_type_label") or "").strip(),
            "story_type_label": str(story.get("story_type_label") or data.get("story_type_label") or "").strip(),
            "story_type_evidence": str(story.get("story_type_evidence") or data.get("story_type_evidence") or "").strip(),
        }
        return kind, found, film

    def _useful_context_fields(entity, fields):
        rows = []
        for key, label in fields:
            value = str(entity.get(key) or "").strip() if isinstance(entity, dict) else ""
            if value and not value.startswith("ไม่ระบุ") and value not in ("null", "None"):
                rows.append(f"{label}: {value}")
        return "; ".join(rows)

    def _clip_ref_prompt(text, limit=800):
        text = " ".join(str(text or "").split()).strip()
        if len(text) <= limit:
            return text
        clipped = text[:limit]
        return clipped.rsplit(" ", 1)[0].rstrip(" ,;")

    def _character_outfit_instruction(entity, film):
        """Return clothing-only direction without changing identity traits."""
        mode = str(ref_outfit_var.get() or "อัตโนมัติตามเนื้อเรื่อง").strip()
        presets = {
            "ชุดอยู่บ้าน": (
                "ชุดอยู่บ้านธรรมดาที่เหมาะกับวัย ฐานะ ยุค และอากาศของเรื่อง "
                "ห้ามใส่ยูนิฟอร์มหรือชุดทำงานเพียงเพราะตัวละครมีอาชีพ"
            ),
            "ชุดทำงาน": "ชุดทำงานที่ตรงกับอาชีพ สถานที่ทำงาน ยุค และฐานะของตัวละคร",
            "ชุดลำลองออกนอกบ้าน": "ชุดลำลองออกนอกบ้านที่เหมาะกับวัย ยุค สถานที่ และสภาพอากาศ",
            "ชุดนอน": "ชุดนอนหรือชุดพักผ่อนในบ้านที่เรียบง่าย เหมาะกับวัย ยุค และฐานะ",
            "ชุดสุภาพ/ทางการ": "ชุดสุภาพหรือชุดทางการที่เหมาะกับวัย ยุค ฐานะ และวัฒนธรรมของเรื่อง",
        }
        if mode == "กำหนดชุดเอง":
            custom = " ".join(ref_custom_outfit_var.get().split()).strip()
            return f"ใส่ชุดตามนี้เท่านั้น: {custom}" if custom else "ชุดธรรมดาเรียบง่าย ไม่ใช่ยูนิฟอร์ม"
        if mode in presets:
            return presets[mode]

        context_clothes = str(entity.get("เสื้อผ้า") or entity.get("clothes") or "").strip() if isinstance(entity, dict) else ""
        story_hint = str(film.get("summary") or "").strip() if isinstance(film, dict) else ""
        if story_hint:
            story_hint = _clip_ref_prompt(story_hint, 120)
            clothes_hint = ""
            if context_clothes and context_clothes not in ("ไม่ระบุ", "null", "None"):
                clothes_hint = f"; ข้อมูลชุดเดิมคือ {context_clothes} ใช้ได้เฉพาะเมื่อเข้ากับสถานการณ์หลัก"
            return (
                f"เลือกชุดที่ตัวละครใช้ในสถานการณ์หลักของเรื่องนี้: {story_hint}; "
                "อาชีพเป็นเพียงข้อมูลพื้นหลัง ห้ามใช้ชุดทำงานถ้าเหตุการณ์หลักไม่ได้อยู่ที่ทำงาน"
                f"{clothes_hint}"
            )
        if context_clothes and context_clothes not in ("ไม่ระบุ", "null", "None"):
            return f"เลือกชุดหลักจาก Context: {context_clothes}"
        return "ชุดธรรมดาที่เหมาะกับสถานการณ์หลักของตัวละคร ไม่อนุมานยูนิฟอร์มจากอาชีพ"

    def _outfit_name_hint(name, kind):
        if str(kind or "").lower() == "location":
            return name
        mode = str(ref_outfit_var.get() or "").strip()
        if mode == "อัตโนมัติตามเนื้อเรื่อง" or not mode:
            return name
        if mode == "กำหนดชุดเอง":
            custom = " ".join(ref_custom_outfit_var.get().split()).strip()
            suffix = custom[:36] if custom else "กำหนดชุดเอง"
        else:
            suffix = mode
        return f"{name}__{suffix}"

    ghost_bible_path = BASE / "ghost_visual_bible.json"
    GHOST_DESIGN_VERSION = 3

    def _is_ghost_character(name, entity):
        """Only explicit supernatural characters enter ghost research."""
        return is_explicit_ghost_character(name, entity)

    def _load_ghost_bible():
        try:
            data = json.loads(ghost_bible_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("ghosts"), dict):
                return data
        except Exception:
            pass
        return {"version": 1, "ghosts": {}}

    def _save_ghost_bible(data):
        temp = ghost_bible_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(temp), str(ghost_bible_path))

    def _analyze_ghost_character(name, entity, film):
        """Research one named ghost before image generation; cache by context."""
        import hashlib
        context = {
            "name": str(name or "").strip(),
            "character": entity if isinstance(entity, dict) else {},
            "story": film if isinstance(film, dict) else {},
        }
        context_hash = hashlib.sha256(
            json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        bible = _load_ghost_bible()
        cache_key = str(name or "").strip().casefold()
        cached = bible.get("ghosts", {}).get(cache_key)
        if (isinstance(cached, dict)
                and cached.get("context_hash") == context_hash
                and cached.get("design_version") == GHOST_DESIGN_VERSION):
            return cached, True

        system_prompt = (
            "คุณเป็นนักค้นคว้าคติผีไทยและ character concept artist. "
            "วิเคราะห์ชนิดผีจากชื่อและข้อมูลเรื่องก่อนออกแบบภาพ ไม่สร้างรูป และตอบ JSON object เท่านั้น. "
            "แยกข้อเท็จจริงในคติที่รู้จักทั่วไปออกจากรายละเอียดที่เรื่องแต่งเพิ่ม. "
            "หากชื่อเป็นชนิดเฉพาะ เช่น ผีโพง กระสือ กระหัง ปอบ เปรต นางตานี หรือแม่นาก "
            "ต้องอธิบายเครื่องหมายจำเพาะของชนิดนั้น ห้ามเปลี่ยนเป็นผีผู้หญิงชุดขาวทั่วไป. "
            "ถ้าคติแตกต่างตามท้องถิ่น ให้เลือกภาพจำที่พบกว้างและระบุ uncertainty สั้นๆ. "
            "ออกแบบ HORROR SELLING POINTS ที่เห็นแล้วจำได้ทันที 2-3 ข้อ และต้องมองเห็นได้จริงใน reference sheet พื้นขาวทุกมุม. "
            "จุดขายต้องมาจากกายภาพของผี เช่น โครงหน้า ดวงตา ปาก ผิว เส้นเลือด สัดส่วน ท่าทาง หรือเครื่องหมายตามคติ ไม่ใช่พึ่งฉากมืด หมอก เลือด หรือไฟเรืองอย่างเดียว. "
            "ต้องมี silhouette และ facial read แบบตัวร้ายหนังผีไทยระดับภาพยนตร์ เห็นภาพแล้วรู้ว่าเป็นสิ่งเหนือธรรมชาติ ไม่ใช่นักแสดงแต่งหน้าแก่หรือเปื้อนดิน. "
            "ออกแบบผิวแบบศพซอมบี้สมจริงโดยไม่เปลี่ยนชนิดผี: ผิวซีดเทาอมเขียวแบบไร้เลือด ด่างเป็นหย่อม "
            "แห้งกร้านคล้ายหนัง เส้นเลือดคล้ำเห็นใต้ผิว รอยแตกลายละเอียด เนื้อรอบตาและแก้มยุบ และสีผิวตายไม่สม่ำเสมอ. "
            "ห้ามทำเป็นเพียงริ้วรอยคนแก่ และห้ามแผลเปิด เนื้อฉีก หรือ gore. "
            "ออกแบบความน่ากลัวแบบ non-graphic supernatural horror: ใช้รูปร่าง ผิว ดวงตา ท่าทาง "
            "เสื้อผ้า แสง และสิ่งเรือง/ควันตามคติ; ห้ามเลือดสาด แผลเปิด อวัยวะขาด เนื้อฉีก gore "
            "หรือความรุนแรงต่อเด็ก. ต้องรักษาอายุ เพศ ใบหน้า ยุค และข้อเท็จจริงจากเรื่อง. "
            "อนุญาตการแต่งหน้าเอฟเฟกต์ภาพยนตร์ เช่น ผิวแตกร้าวเหนือธรรมชาติ ผิวชำรุด รอยช้ำคล้ำ "
            "คราบสีแดงเข้มแห้ง เงาดำใต้ผิว และท่าทางผิดธรรมชาติ เมื่อเข้ากับชนิดผี. "
            "schema: {\"ghost_type\":\"\",\"folklore_identity\":\"\",\"horror_selling_points\":[\"\"],"
            "\"signature_traits\":[\"\"],\"face_and_skin\":\"\",\"eyes\":\"\","
            "\"hair\":\"\",\"body_and_pose\":\"\",\"wardrobe\":\"\","
            "\"supernatural_effects\":[\"\"],\"must_include\":[\"\"],"
            "\"must_not_include\":[\"\"],\"uncertainty\":\"\"}"
        )
        user_prompt = (
            "วิเคราะห์ตัวละครผีนี้ให้เป็นแบบภาพอ้างอิงที่จำเพาะและสมจริง:\n" +
            json.dumps(context, ensure_ascii=False, indent=2)[:12000]
        )
        payload = {
            "model": "auto",
            "chatgpt_image_intercept": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        _bind_ref_story_payload(payload)
        payload_path = Path(BASE) / "snapgen_data" / "temp" / "ghost_ref_analysis.json"
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            data = _run_json([
                "curl", "--max-time", "240", "-s", _chatgpt_api_base() + "/chat/completions",
                "-H", "Authorization: Bearer local-dev-key",
                "-H", "Content-Type: application/json",
                "--data-binary", "@" + str(payload_path),
            ], timeout=250)
            if data.get("error"):
                raise RuntimeError(json.dumps(data["error"], ensure_ascii=False))
            _advance_ref_story_history(data)
            output = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            output = output.replace("```json", "").replace("```", "").strip()
            start, end = output.find("{"), output.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("GPT ไม่ได้ส่งผลวิเคราะห์ผีเป็น JSON")
            design = json.loads(output[start:end + 1])
            if not isinstance(design, dict) or not design.get("ghost_type"):
                raise RuntimeError("GPT ยังไม่ได้ระบุชนิดผี")
            design["context_hash"] = context_hash
            design["design_version"] = GHOST_DESIGN_VERSION
            design["name"] = str(name or "").strip()
            bible.setdefault("ghosts", {})[cache_key] = design
            _save_ghost_bible(bible)
            return design, False
        finally:
            try:
                payload_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _ghost_design_prompt_text(design):
        if not isinstance(design, dict):
            return ""
        rows = []
        for key, label in (
            ("ghost_type", "ชนิดผี"), ("folklore_identity", "คติและภาพจำ"),
            ("horror_selling_points", "จุดขายหนังผี"),
            ("signature_traits", "เครื่องหมายจำเพาะ"), ("face_and_skin", "ใบหน้าและผิว"),
            ("eyes", "ดวงตา"), ("hair", "ผม"), ("body_and_pose", "รูปร่างและท่า"),
            ("wardrobe", "เสื้อผ้า"), ("supernatural_effects", "ลักษณะเหนือธรรมชาติ"),
            ("must_include", "ต้องมี"), ("must_not_include", "ห้ามมี"),
        ):
            value = design.get(key)
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value if str(item).strip())
            if str(value or "").strip():
                rows.append(f"{label}: {value}")
        return "; ".join(rows)

    location_bible_path = BASE / "location_visual_bible.json"

    def _load_location_bible():
        import json as _json
        try:
            data = _json.loads(location_bible_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("locations"), dict):
                return data
        except Exception:
            pass
        return {"version": 1, "locations": {}}

    def _save_location_bible(data):
        import json as _json
        tmp = location_bible_path.with_suffix(".tmp")
        tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(location_bible_path))

    def _location_context_hash(context_text):
        import hashlib
        return hashlib.sha256(str(context_text or "").encode("utf-8", "replace")).hexdigest()[:16]

    def _canonical_location_design(name, payload, context_hash):
        if not isinstance(payload, dict):
            payload = {}
        design = payload.get("location_design") if isinstance(payload.get("location_design"), dict) else payload
        def text_value(key, fallback=""):
            return " ".join(str(design.get(key) or fallback).split()).strip()
        def list_value(key):
            value = design.get(key)
            if isinstance(value, str):
                value = [x.strip() for x in value.split(",") if x.strip()]
            if not isinstance(value, list):
                return []
            return [" ".join(str(x).split()).strip() for x in value if str(x).strip()][:12]
        result = {
            "name": str(name).strip(),
            "design_version": 5,
            "context_hash": context_hash,
            "parent_location": text_value("parent_location"),
            "space_identity": text_value("space_identity", f"สถานที่ชื่อ {name}"),
            "visual_description": text_value("visual_description"),
            "atmosphere": text_value("atmosphere"),
            "materials": text_value("materials"),
            "visible_elements": list_value("visible_elements"),
            "views": list_value("views")[:4],
            "must_include": list_value("must_include"),
            "must_not_include": list_value("must_not_include"),
            "assumptions": list_value("assumptions"),
        }
        if not result["visual_description"] and not result["visible_elements"]:
            raise RuntimeError("GPT ยังไม่ได้สรุปภาพที่กล้องต้องเห็นในสถานที่")
        if len(result["views"]) < 4:
            result["views"] = [
                "ภาพรวม establishing เห็นสถานที่ทั้งหมด",
                "มุมหลักที่ใช้เล่าเหตุการณ์",
                "มุมอีกด้านที่เห็นองค์ประกอบสำคัญ",
                "มุมย้อนกลับเห็นด้านที่ยังไม่เห็น",
            ]
        return result

    def _design_location_via_context(name):
        """Create and persist one physical location design from the story context."""
        import json as _json, tempfile as _tempfile
        context_text = _load_ref_context()
        if not context_text.strip():
            raise RuntimeError("ไม่มี Prompt Context สำหรับออกแบบสถานที่")
        context_hash = _location_context_hash(context_text)
        try:
            context_for_design = _json.loads(context_text)
            if isinstance(context_for_design, dict):
                context_for_design.pop("location_storyboard_reference", None)
            context_for_design = _json.dumps(context_for_design, ensure_ascii=False, indent=2)
        except Exception:
            context_for_design = context_text
        bible = _load_location_bible()
        cached = bible.get("locations", {}).get(name)
        # Version 5 adds GPT-selected storyboard views.  Older one-angle
        # designs must be regenerated instead of silently reusing the cache.
        if isinstance(cached, dict) and cached.get("context_hash") == context_hash and cached.get("design_version") == 5:
            return cached, True
        # New Prompt Context stores the complete visual design while GPT still
        # has the full source story.  Use that design directly; do not ask GPT
        # to reinterpret the same location every time Ref is generated.
        _kind, context_location, _film = _context_entity(name, "location")
        if isinstance(context_location, dict) and (
            context_location.get("visual_description") or context_location.get("views")
        ):
            design = _canonical_location_design(name, context_location, context_hash)
            bible.setdefault("locations", {})[name] = design
            bible["context_hash"] = context_hash
            _save_location_bible(bible)
            return design, False
        target_info = {}
        for row in bible.get("targets", []) or []:
            if isinstance(row, dict) and str(row.get("name") or "").strip().casefold() == str(name).strip().casefold():
                target_info = row
                break

        system_prompt = (
            "คุณคือผู้ออกแบบสถานที่และ storyboard artist สำหรับหนังสั้น. รับชื่อ TARGET LOCATION กับเรื่องย่อ แล้วคิดว่าสถานที่นี้ควรมีหน้าตาอย่างไรให้เข้ากับหนัง พร้อมกำหนดภาพอ้างอิง 4 มุม. "
            "นี่เป็นงานออกแบบข้อความเท่านั้น ห้ามสร้างรูป ห้าม markdown และตอบ JSON object เท่านั้น.\n"
            "กฎ:\n"
            "1) สร้างเฉพาะ TARGET LOCATION ที่ระบุ เช่น TARGET เป็นห้องนอนก็อธิบายห้องนอนธรรมดา ไม่ต้องแสดงทั้งห้องเช่า ห้องน้ำ ครัว หรืออาคารพร้อมกัน.\n"
            "2) parent_location ใช้เพียงช่วยเข้าใจว่าเป็นห้องของใครและอยู่ในโลกเรื่องแบบใด ห้ามเอาสถานที่แม่ทั้งหมดมายัดในภาพ.\n"
            "3) บอกลักษณะภาพ วัสดุ และของหลักที่ควรมองเห็น แล้วกำหนด views จำนวน 4 มุมที่ช่วยให้รู้จักสถานที่ครบ: ต้องมีภาพรวม establishing 1 มุม และอีก 3 มุมเลือกตามสถานที่ เช่น หน้ารวม ด้านหลัง ด้านข้าง ภายใน หรือมุมย้อนกลับ. ห้ามสร้างเหตุการณ์ต่อเนื่อง 4 ฉาก; ทั้งหมดคือสถานที่เดียวกัน.\n"
            "ห้ามเขียนผังพื้น ขนาดเป็นเมตร ภาพ 3D ความสัมพันธ์ของทุกห้อง ทางเข้าออกที่บทไม่ได้ใช้ หรือรายละเอียดสถาปัตยกรรมเกินจำเป็น.\n"
            "4) รักษาข้อเท็จจริงที่เกี่ยวกับรูปลักษณ์ของสถานที่นี้จากเรื่อง ส่วนที่ไม่ระบุเติมให้น้อยที่สุดและสมเหตุสมผลกับยุค ฐานะ และพื้นที่.\n"
            "5) ห้ามนำตัวละคร การกระทำ วิญญาณ เลือด อารมณ์หนัง แสงกลางคืน หรือสภาพอากาศมาเป็นส่วนของแบบสถานที่. ใช้ภาพกลางสว่างเพื่อมองรายละเอียดชัด.\n"
            "schema: {\"location_design\":{\"parent_location\":\"บริบทสั้นๆ\",\"space_identity\":\"สถานที่นี้คืออะไร\",\"visual_description\":\"รูปลักษณ์ที่เข้ากับเรื่องย่อ\",\"materials\":\"วัสดุหลัก\",\"visible_elements\":[\"ของหลัก\"],\"views\":[\"มุม 1 ภาพรวม...\",\"มุม 2...\",\"มุม 3...\",\"มุม 4...\"],\"must_include\":[\"...\"],\"must_not_include\":[\"...\"],\"assumptions\":[\"...\"]}}"
        )
        user_prompt = (
            f"TARGET LOCATION:\n{name}\n\n"
            "TARGET RELATION FROM LOCATION BREAKDOWN:\n"
            f"parent_location: {target_info.get('parent_location') or '(ให้หาในบท)'}\n"
            f"type: {target_info.get('type') or '(ให้จำแนกจากบท)'}\n"
            f"story_fact: {target_info.get('story_fact') or '(ให้หาในบท)'}\n\n"
            "STORY SUMMARY / CONTEXT — ใช้ตัดสินว่าสถานที่ควรมีหน้าตาแบบใดและมุมใดมีประโยชน์:\n"
            f"{context_for_design[:12000]}\n\n"
            "ออกแบบ TARGET LOCATION แห่งเดียวและวาง Location Storyboard 4 มุมสำหรับสร้างรูปอ้างอิง."
        )
        payload_file = os.path.join(_tempfile.gettempdir(), "snapgen_location_design.json")
        try:
            with open(payload_file, "w", encoding="utf-8") as f:
                _json.dump(_bind_ref_story_payload({
                    "model": "auto",
                    "chatgpt_image_intercept": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.15,
                }), f, ensure_ascii=False)
            data = _run_json([
                "curl", "--max-time", "240", "-s", _chatgpt_api_base() + "/chat/completions",
                "-H", "Authorization: Bearer local-dev-key",
                "-H", "Content-Type: application/json",
                "--data-binary", "@" + payload_file,
            ], timeout=250)
            if data.get("error"):
                raise RuntimeError(_json.dumps(data["error"], ensure_ascii=False))
            _advance_ref_story_history(data)
            out = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            out = out.replace("```json", "").replace("```", "").strip()
            start, end = out.find("{"), out.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError("GPT ไม่ได้ส่งแบบสถานที่เป็น JSON")
            design = _canonical_location_design(name, _json.loads(out[start:end + 1]), context_hash)
            bible.setdefault("locations", {})[name] = design
            bible["context_hash"] = context_hash
            _save_location_bible(bible)
            return design, False
        finally:
            try:
                os.remove(payload_file)
            except Exception:
                pass

    def _location_design_prompt_text(design):
        if not isinstance(design, dict):
            return ""
        rows = []
        # Put visible facts first because the final image prompt has an
        # 800-character ceiling.  Parent/background context is lowest priority.
        for key, label in (
            ("views", "มุม 4 ช่อง"), ("visual_description", "ภาพที่ควรเห็น"),
            ("atmosphere", "ฟีลสถานที่"), ("visible_elements", "ของหลักที่เห็น"),
            ("materials", "วัสดุหลัก"), ("space_identity", "สถานที่เป้าหมาย"),
            ("parent_location", "บริบทสถานที่"),
        ):
            if design.get(key):
                value = design[key]
                if isinstance(value, list):
                    value = ", ".join(value)
                rows.append(f"{label}: {value}")
        for key, label in (("must_include", "ต้องมี"), ("must_not_include", "ห้ามมี")):
            if design.get(key):
                rows.append(f"{label}: {', '.join(design[key])}")
        return "; ".join(rows)

    def _discover_location_targets_via_context():
        """Find reusable locations and nested spaces for Auto Location."""
        import json as _json, tempfile as _tempfile
        context_text = _load_ref_context()
        source_path = BASE / "prompt_ref_source.txt"
        try:
            source_text = source_path.read_text(encoding="utf-8", errors="replace").strip() if source_path.exists() else ""
        except Exception:
            source_text = ""
        if not context_text.strip() and not source_text:
            raise RuntimeError("ไม่มี Prompt Context หรือบทต้นฉบับสำหรับค้นหาสถานที่")
        discovery_hash = _location_context_hash(context_text + "\n" + source_text)
        bible = _load_location_bible()
        cached = bible.get("targets")
        if bible.get("targets_hash") == discovery_hash and bible.get("targets_version") == 3 and isinstance(cached, list) and cached:
            return cached, True

        system_prompt = (
            "คุณคือ location breakdown artist สำหรับหนังสั้น อ่าน STORY CONTEXT และ SOURCE STORY แล้วเลือกเฉพาะสถานที่ที่เหตุการณ์เกิดขึ้นให้กล้องเห็นจริง. "
            "ตอบ JSON เท่านั้น ห้ามสร้างรูป ห้าม markdown.\n"
            "หลักตัดสินคือการกระทำหลักของฉาก ไม่ใช่รายการโครงสร้างอาคาร: ถ้าตัวละครนอนหรือเหตุเกิดบนเตียง และบทระบุว่ามีห้องนอนแยก ให้เลือก 'ห้องนอนของตัวละคร' ไม่ใช่ห้องทั้งยูนิต; ถ้าตัวละครเข้าไปหรือเหตุการณ์เกิดในห้องน้ำจึงเลือกห้องน้ำ. "
            "ข้อความว่า 'มีห้องน้ำอยู่ด้านหลัง' หรือ 'อาคารมีสิบห้อง' อย่างเดียวไม่ใช่เหตุผลให้แตกทุกห้องเป็น Ref. ห้ามสร้างรายการพื้นที่เพียงเพราะบทบอกว่ามีอยู่. "
            "ไม่ต้องสร้างลำดับสถานที่แม่ > ยูนิต > ทุกห้องให้ครบ และไม่ต้องวางแผนผัง 3D. สถานที่แม่ใส่เฉพาะเมื่อมันเป็นฉากที่กล้องเห็นจริง. "
            "ชื่อพื้นที่ย่อยต้องบอกเจ้าของหรือบริบทเท่าที่จำเป็น เช่น 'ห้องนอนของชด' เพื่อไม่ให้สับสน แต่ไม่ต้องบรรยายโครงสร้างทั้งหมด. "
            "โต๊ะ เตียง ประตู เครื่องราง และวัตถุเป็น props/fixed elements ไม่ใช่ location แยก. พื้นที่เดียวกันต่างเวลา/ก่อน-หลังเหตุการณ์ต้องเป็น Ref เดียว ห้ามแยกซ้ำ. "
            "story_fact ต้องระบุการกระทำหรือเหตุการณ์ที่เกิดในสถานที่นั้นอย่างชัดเจน ห้ามใช้เพียงคำว่า 'บทกล่าวถึง' หรือ 'เป็นส่วนหนึ่งของ'. จำกัดไม่เกิน 10 สถานที่. "
            "schema: {\"locations\":[{\"name\":\"...\",\"parent_location\":\"บริบทสั้นๆ หรือว่าง\",\"type\":\"exterior|building|unit|room|workplace|landscape|other\",\"story_fact\":\"การกระทำที่เกิดให้กล้องเห็นในสถานที่นี้\"}]}"
        )
        user_prompt = (
            "STORY CONTEXT:\n" + context_text[:9000] + "\n\n"
            "SOURCE STORY:\n" + (source_text[:14000] if source_text else "(ไม่มี)") + "\n\n"
            "สร้างรายการเฉพาะฉากสถานที่ที่ต้องเห็นจริงในหนังเรื่องนี้ อย่าแตกห้องที่ไม่มีเหตุการณ์เกิดขึ้น."
        )
        payload_file = os.path.join(_tempfile.gettempdir(), "snapgen_location_targets.json")
        try:
            with open(payload_file, "w", encoding="utf-8") as f:
                _json.dump(_bind_ref_story_payload({
                    "model": "auto", "chatgpt_image_intercept": False,
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    "temperature": 0.1,
                }), f, ensure_ascii=False)
            data = _run_json([
                "curl", "--max-time", "240", "-s", _chatgpt_api_base() + "/chat/completions",
                "-H", "Authorization: Bearer local-dev-key", "-H", "Content-Type: application/json",
                "--data-binary", "@" + payload_file,
            ], timeout=250)
            if data.get("error"):
                raise RuntimeError(_json.dumps(data["error"], ensure_ascii=False))
            _advance_ref_story_history(data)
            out = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            out = out.replace("```json", "").replace("```", "").strip()
            start, end = out.find("{"), out.rfind("}")
            parsed = _json.loads(out[start:end + 1]) if start >= 0 and end > start else {}
            rows = parsed.get("locations") if isinstance(parsed, dict) else []
            targets, seen = [], set()
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                name = " ".join(str(row.get("name") or "").split()).strip()
                location_type = " ".join(str(row.get("type") or "other").split()).strip()
                story_fact = " ".join(str(row.get("story_fact") or "").split()).strip()
                prop_words = ("โต๊ะ", "เตียง", "ประตู", "หน้าต่าง", "เก้าอี้", "ตู้", "กุญแจ", "เครื่องราง", "กระจก", "รถ")
                # A model sometimes relabels a prop as exterior/room to bypass
                # the prompt rule.  Names centred on an object are never a
                # separate location, regardless of the returned type.
                if any(word in name for word in prop_words):
                    continue
                # Mentions, guesses and background descriptions are not an
                # on-screen scene.  They must not create Location Ref targets.
                non_scene_phrases = ("สันนิษฐาน", "คาดว่า", "อาจมี", "อาจเป็น", "กล่าวว่าอาจ", "เพียงกล่าวถึง", "เป็นส่วนหนึ่งของ")
                if not story_fact or any(phrase in story_fact for phrase in non_scene_phrases):
                    continue
                folded = name.casefold()
                if not name or folded in seen:
                    continue
                seen.add(folded)
                targets.append({
                    "name": name,
                    "parent_location": " ".join(str(row.get("parent_location") or "").split()).strip(),
                    "type": location_type,
                    "story_fact": story_fact,
                })
            if not targets:
                raise RuntimeError("GPT ไม่พบรายการสถานที่จาก Context")
            bible["targets"] = targets[:10]
            bible["targets_hash"] = discovery_hash
            bible["targets_version"] = 3
            _save_location_bible(bible)
            return bible["targets"], False
        finally:
            try:
                os.remove(payload_file)
            except Exception:
                pass

    def _build_ref_prompt(name, entity_kind="", location_design=None, use_context_override=None, ghost_design=None):
        selected_ctx = str(g.get("_ref_selected_context", "") or "").strip()
        selected_name = str(g.get("_ref_selected_name", "") or "").strip()
        selected_kind = str(g.get("_ref_selected_kind", "") or "").strip().lower()
        selected_match = bool(selected_name == name and selected_kind in ("character", "location"))
        context_allowed = True if use_context_override is None else bool(use_context_override)
        requested_kind = (entity_kind or (selected_kind if selected_match else "")) if context_allowed else ""
        use_context = bool(requested_kind)
        if use_context:
            kind, entity, film = _context_entity(name, requested_kind)
            kind = kind or requested_kind
        else:
            kind, entity, film = "manual", {}, {}
        selected_story_type = story_type_by_label.get(ref_story_type_var.get(), "auto")
        story_type_id, story_profile = story_type_profile(
            selected_story_type if selected_story_type != "auto" else film.get("story_type")
        )
        story_type_label = story_profile.get("label") or "ยังไม่ระบุประเภท"
        story_type_rules = _clip_ref_prompt(" ".join(
            str(story_profile.get(key) or "").strip()
            for key in ("world", "characters", "period", "costume", "avoid")
            if str(story_profile.get(key) or "").strip()
        ), 300)

        if isinstance(ghost_design, dict):
            # Keep room for the non-negotiable reference-sheet layout and
            # lighting rules below. Long folklore analysis used to push the
            # white-background lock past the 1,200-character clip boundary.
            ghost_source = _clip_ref_prompt(_ghost_design_prompt_text(ghost_design), 260)
            identity = _clip_ref_prompt(_useful_context_fields(entity, (
                ("visual_identity", "ตัวตนเดิม"), ("อายุ", "วัย"), ("เพศ", "เพศ"),
                ("รูปร่าง", "รูปร่าง"), ("ทรงผม", "ผม"), ("ใบหน้า", "ใบหน้า"),
                ("ลักษณะเด่น", "จุดจำ"),
            )), 150) or "ยึดชื่อและชนิดผีที่ผู้ใช้ระบุ"
            return _clip_ref_prompt(
                f"ประเภทเรื่อง: {story_type_label} ({story_type_id}). {story_type_rules} "
                f"สร้าง THAI GHOST CHARACTER REFERENCE SHEET ของ '{name}' ผีชนิดเฉพาะเพียงตัวเดียว. "
                "REFERENCE SHEET LOCK: พื้นหลังขาวล้วนทุกช่อง ใช้ bright high-key studio แสงขาวนุ่มสม่ำเสมอ เห็นผม ใบหน้า เสื้อผ้า และรูปร่างครบชัดเจน. ห้ามพื้นดำ โทนมืด ฉากกลางคืน หมอก ควัน วิวสถานที่ และ dramatic low-key lighting. "
                "HORROR CREATURE LOCK: ทำเป็นตัวร้ายหนังผีไทยที่มีจุดขายทางกายภาพ 2-3 จุดเด่น เห็นชัดและจำได้ทันทีทุกมุม แม้อยู่บนพื้นขาว. จุดขายต้องอยู่ที่ใบหน้า ดวงตา ปาก ผิว สัดส่วน ท่าทาง หรือเครื่องหมายเฉพาะชนิดผี ไม่ใช่แค่คนแก่สกปรกและไม่ใช่แค่แสงเรืองที่จมูก. "
                "ZOMBIE-SKIN LOCK: ใช้เฉพาะลักษณะผิวศพซอมบี้ที่สมจริงกับผีตัวเดิม—ผิวซีดเทาอมเขียวไร้เลือด ด่างเป็นหย่อม แห้งกร้านคล้ายหนัง เส้นเลือดคล้ำใต้ผิว รอยแตกลายละเอียด เบ้าตาและแก้มยุบ สีผิวตายไม่สม่ำเสมอ. ต้องเห็นลักษณะนี้ชัดทั้งใบหน้า คอ แขน และขา. ห้ามให้เป็นเพียงริ้วรอยคนแก่ ห้ามแผลเปิด เนื้อฉีก และ gore. "
                "จัดเป็นแผ่นอ้างอิง 5 ช่อง. แถวบนมี 4 ช่องเล็กเท่ากันเรียงซ้ายไปขวา: ใบหน้า close-up ด้านซ้าย, ใบหน้า close-up ด้านขวา, เต็มตัวด้านหน้า, เต็มตัวด้านหลัง. "
                "แถวล่างมี 1 ช่องใหญ่เป็นใบหน้าตรง close-up ระยะหัวถึงไหล่ ให้ใบหน้าตรงเป็นภาพหลักและใหญ่ที่สุด เห็นตา จมูก ปาก ผม และเครื่องหมายจำเพาะชัด. ภาพเต็มตัวต้องเห็นศีรษะถึงเท้าครบแต่มีขนาดเล็กกว่าหน้าตรง. "
                f"ล็อกตัวตนจากเรื่อง: {identity}. งานวิเคราะห์คติและรูปลักษณ์ก่อนสร้าง: {ghost_source}. "
                "ห้ามทำเป็นผีชุดขาวทั่วไป ห้ามเปลี่ยนชนิดผี และต้องเห็นเครื่องหมายจำเพาะชัดทุกมุม. ผลลัพธ์ต้องดูเป็นผีอย่างชัดเจนแม้อยู่บนพื้นขาว ห้ามออกมาเป็นเพียงมนุษย์แก่หรือมนุษย์สกปรก. "
                "ห้ามสร้างหัวเรื่อง ป้ายชื่อ NOTE คำอธิบาย หรือตัวอักษรใดๆ ภายในภาพ เพราะโปรแกรมจะเติมป้ายชื่อภายหลัง. "
                "ทุกช่องเป็นคนเดียวกัน ใบหน้า อายุ เสื้อผ้า รูปร่าง และลักษณะเหนือธรรมชาติตรงกัน. "
                f"{GHOST_CINEMATIC_HORROR_EFFECTS}. "
                "photorealistic Thai supernatural character reference, pure white background, no text, no watermark.",
                1600,
            )

        if kind == "manual":
            outfit_mode = str(ref_outfit_var.get() or "อัตโนมัติตามเนื้อเรื่อง").strip()
            outfit_text = ""
            if outfit_mode != "อัตโนมัติตามเนื้อเรื่อง":
                outfit_text = f" เปลี่ยนเฉพาะเสื้อผ้าตามคำสั่งนี้: {_character_outfit_instruction({}, {})}."
            return _clip_ref_prompt(
                f"ประเภทเรื่อง: {story_type_label} ({story_type_id}). {story_type_rules} "
                f"สร้าง SUBJECT REFERENCE SHEET ของ '{name}' จากชื่อที่ผู้ใช้พิมพ์เท่านั้น ห้ามใช้ข้อมูลจาก Prompt Context หรือเนื้อเรื่อง. "
                "LIGHTING LOCK: flat frontal ID-photo lighting, large camera-front softbox plus equal 1:1 fill from every direction. Face brightness must be perfectly even. ZERO facial shadows: none under brows, eyes, nose, cheeks, lips, or chin. No side/top/rim/Rembrandt/chiaroscuro/dramatic lighting. "
                "ต้องเป็นสิ่งเดียวกันทุกช่องและรักษาชนิด รูปร่าง สี ลวดลาย และจุดจำให้ตรงกัน. "
                "จัดเป็นแผ่นอ้างอิง 5 ช่อง. แถวบนมี 4 ช่องเล็กเท่ากันเรียงซ้ายไปขวา: ใบหน้า close-up ด้านซ้าย, ใบหน้า close-up ด้านขวา, เต็มตัวด้านหน้า, เต็มตัวด้านหลัง. "
                "แถวล่างมี 1 ช่องใหญ่เป็นใบหน้าตรง close-up ระยะหัวถึงไหล่ ให้หน้าตรงเป็นภาพหลักและใหญ่ที่สุด. ภาพเต็มตัวต้องเห็นศีรษะถึงเท้าครบแต่เล็กกว่าหน้าตรง. ห้ามสร้างตัวอักษร หัวเรื่อง NOTE หรือป้ายชื่อภายในภาพ เพราะโปรแกรมจะเติมชื่อภายหลัง. ห้ามฉากหรือภาพรวมอื่น. "
                "ถ้าเป็นสัตว์หรือสิ่งที่ปกติไม่สวมเสื้อผ้า ห้ามเพิ่มเสื้อผ้าเอง. "
                f"แถบล่างใส่ชื่อไทย '{name}' ครั้งเดียว.{outfit_text} "
                "พื้นขาว 5600K คงสีผิวและรายละเอียดผิวจริง ไม่ทำหน้าขาวลอยหรือผิวพลาสติก. รายละเอียดคมชัด ห้ามฉาก คนอื่น ข้อความอื่น และ watermark. photorealistic."
            )

        if kind == "location":
            source = _location_design_prompt_text(location_design)
            if not source:
                details = _useful_context_fields(entity, (
                    ("detail", "รายละเอียด"), ("description", "ลักษณะสถานที่"),
                    ("layout", "ผัง"), ("architecture", "สถาปัตยกรรม"),
                    ("materials", "วัสดุ"),
                ))
                world = "; ".join(
                    x for x in (
                        f"ยุค: {film['era']}" if film.get("era") else "",
                        f"สถานที่หลัก: {film['main_location']}" if film.get("main_location") else "",
                    ) if x
                )
                source = "; ".join(x for x in (details, world) if x) or "Context ระบุชื่อสถานที่นี้แต่ไม่มีรายละเอียดกายภาพ"
            # Leave enough room for the fixed four-view sheet instructions;
            # otherwise the final view/name rules are truncated at 800 chars.
            source = _clip_ref_prompt(source, 185)
            return _clip_ref_prompt(
                f"ประเภทเรื่อง: {story_type_label} ({story_type_id}). {story_type_rules} "
                f"สร้าง LOCATION REFERENCE SHEET ของ '{name}' สถานที่เดียว แบ่ง 4 ช่องเท่ากัน 2x2. "
                "ทั้ง 4 ช่องต้องเป็นสถานที่และดีไซน์เดียวกัน ล็อกวัสดุ สี จำนวนห้อง ประตู หน้าต่าง และองค์ประกอบให้ตรงกัน. "
                "มุมที่ 1 ด้านหน้า, มุมที่ 2 เฉียงซ้าย, มุมที่ 3 เฉียงขวา, มุมที่ 4 ย้อนกลับจากอีกด้าน. "
                f"แถบล่างใส่ชื่อภาษาไทยว่า '{name}' เพียงครั้งเดียว อ่านชัด; ห้ามข้อความอื่น. "
                f"แบบสถานที่: {source}. "
                "แสงกลางวันกลาง 5600K สว่างชัด สีวัสดุตรงจริง. "
                "ห้ามคน เหตุการณ์ วิญญาณ โทนมืด กลางคืน หมอก ฝน floor plan ภาพ 3D cutaway สถานที่อื่น และ watermark. photorealistic, sharp."
            )

        # Character Ref is an attachment-ready identity sheet.  Context only
        # locks visible identity traits; story events and locations stay out.
        details = _useful_context_fields(entity, (
            ("visual_identity", "ภาพจำ"), ("อายุ", "วัย"), ("เพศ", "เพศ"),
            ("รูปร่าง", "รูปร่าง"), ("ส่วนสูง", "ส่วนสูง"), ("สีผิว", "สีผิว"),
            ("ทรงผม", "ผม"), ("ใบหน้า", "ใบหน้า"), ("ดวงตา", "ดวงตา"),
            ("ลักษณะเด่น", "จุดจำ"),
        ))
        if not details and selected_match and selected_ctx:
            details = selected_ctx
        source = _clip_ref_prompt(
            details or "คนไทยสมจริงหนึ่งแบบตามชื่อและบทบาทใน Context",
            190,
        )
        return _clip_ref_prompt(
            f"ประเภทเรื่อง: {story_type_label} ({story_type_id}). {story_type_rules} "
            f"สร้าง CHARACTER REFERENCE SHEET ของ '{name}' คนเดียว. "
            "LIGHTING LOCK: flat frontal ID-photo lighting, large camera-front softbox plus equal 1:1 fill from every direction. Face brightness must be perfectly even. ZERO facial shadows: none under brows, eyes, nose, cheeks, lips, or chin. No side/top/rim/Rembrandt/chiaroscuro/dramatic lighting. "
            "จัดเป็นแผ่นอ้างอิง 5 ช่อง. แถวบนมี 4 ช่องเล็กเท่ากันเรียงซ้ายไปขวา: ใบหน้า close-up ด้านซ้าย, ใบหน้า close-up ด้านขวา, เต็มตัวด้านหน้า, เต็มตัวด้านหลัง. "
            "แถวล่างมี 1 ช่องใหญ่เป็นใบหน้าตรง close-up ระยะหัวถึงไหล่ ให้หน้าตรงเป็นภาพหลักและใหญ่ที่สุด. ภาพเต็มตัวต้องเห็นศีรษะถึงเท้าครบแต่เล็กกว่าหน้าตรง. ห้ามสร้างตัวอักษร หัวเรื่อง NOTE หรือป้ายชื่อภายในภาพ เพราะโปรแกรมจะเติมชื่อภายหลัง. ทุกช่องต้องเป็นคนเดียวกัน สีหน้าเป็นกลาง และรักษาใบหน้าเดิม. "
            f"ล็อกรูปลักษณ์จาก Character Bible: {source}. "
            f"เปลี่ยนเฉพาะเสื้อผ้า: {_character_outfit_instruction(entity, film)}. "
            "พื้นขาว 5600K คงสีผิวและรายละเอียดผิวจริง ไม่ทำหน้าขาวลอยหรือผิวพลาสติก. "
            "ห้ามฉาก พร็อพ คนอื่น อารมณ์เหตุการณ์ บาดแผล โทนมืด กลางคืน และ watermark. photorealistic, sharp."
        )

    g["_clean_character_ref_context"] = _clean_character_ref_context
    g["_extract_character_appearance"] = _extract_character_appearance
    g["_context_entity_for_ref"] = _context_entity
    g["_design_location_via_context"] = _design_location_via_context
    g["_discover_location_targets_via_context"] = _discover_location_targets_via_context
    g["_load_location_bible"] = _load_location_bible
    g["_build_ref_prompt"] = _build_ref_prompt
    
    def _load_ref_context():
        # Auto Ref source: compact Prompt-Ref context.
        # Prefer JSON (structured) over markdown .txt.
        json_p = BASE / "prompt_ref_context.json"
        if json_p.is_file():
            try:
                return json_p.read_text(encoding="utf-8")
            except Exception:
                pass
        p = BASE / "prompt_ref_context.txt"
        if not p.is_file():
            return ""
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""
    g["_load_ref_context"] = _load_ref_context
    
    def _extract_auto_ref_names(text):
        # Extract story characters from JSON Prompt Context first.
        # Ref creates character reference sheets, so do not mix locations/props here.
        try:
            import json as _json
            ctx = _json.loads(text)
            chars = ctx.get("characters", []) if isinstance(ctx, dict) else []
            names = []
            for ch in chars:
                if not isinstance(ch, dict):
                    continue
                name = str(ch.get("name", "")).strip()
                if name and name not in names:
                    names.append(name)
            if names:
                return names
        except Exception:
            pass

        # Fallback for older markdown context.
        names = []
        section = ""
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("##"):
                if "ตัวละคร" in line:
                    section = "character"
                else:
                    section = ""
                continue
            if section != "character" or not line.startswith("-"):
                continue
            item = line.lstrip("- ").strip()
            if not item:
                continue
            name = item.split(":", 1)[0].strip()
            if name and name not in names:
                names.append(name)
        return names
    g["_extract_auto_ref_names"] = _extract_auto_ref_names
    
    auto_ref_stop = [False]
    auto_ref_running = [False]
    ref_job_running = [False]
    auto_ref_btn = [None]
    ref_make_btn = [None]
    g["auto_ref_stop"] = auto_ref_stop
    g["auto_ref_running"] = auto_ref_running
    g["ref_job_running"] = ref_job_running

    def _notify_done():
        notify = g.get("_snapgen_notify_done")
        if callable(notify):
            try:
                notify()
            except Exception:
                pass
    
    def _set_ref_action_buttons_running(running, auto=False):
        ref_job_running[0] = bool(running)
        try:
            if ref_make_btn[0]:
                ref_make_btn[0].config(state=(tk.DISABLED if running else tk.NORMAL))
            if auto_ref_btn[0] and not auto:
                auto_ref_btn[0].config(state=(tk.DISABLED if running else tk.NORMAL))
        except Exception:
            pass
    g["_set_ref_action_buttons_running"] = _set_ref_action_buttons_running
    
    def _set_auto_ref_button_running(running):
        btn = auto_ref_btn[0]
        if not btn:
            return
        try:
            _set_ref_action_buttons_running(running, auto=True)
            if running:
                btn.config(text="⏹ หยุด", command=stop_auto_ref, state=tk.NORMAL, bg="#DC2626", activebackground="#B91C1C")
            else:
                btn.config(text="⚡ Auto ตัวละคร", command=generate_auto_ref, state=tk.NORMAL, bg="#0EA5E9", activebackground="#0284C7")
        except Exception:
            pass
    g["_set_auto_ref_button_running"] = _set_auto_ref_button_running
    
    def stop_auto_ref():
        auto_ref_stop[0] = True
        _ref_log("[auto-ref] ขอหยุด — จะหยุดหลังรูปที่กำลังสร้างเสร็จ")
    g["stop_auto_ref"] = stop_auto_ref
    
    def _run_ref_jobs(names, auto=False, ref_image=None, entity_kind=""):
        names = [n.strip() for n in names if n and n.strip()]
        if not names:
            _ref_log("ไม่มีชื่อสำหรับสร้าง Ref")
            return
        has_ref_story = g.get("has_ref_story_history")
        current_ref_title = g.get("get_ref_story_title")
        valid_ref_story = bool(has_ref_story()) if callable(has_ref_story) else False
        title = current_ref_title() if callable(current_ref_title) else ""
        if not valid_ref_story or not title:
            ref_story_title_var.set("")
            _ref_log("[บทเรื่อง] ยังไม่มีประวัติเรื่องหลัก — เริ่มเรื่องจาก Prompt-Ref ก่อน")
            return
        ref_story_title_var.set(title)
        ok_bridge, bridge_msg = _ref_bridge_ready()
        _ref_log(bridge_msg)
        if not ok_bridge:
            return
        if ref_job_running[0]:
            _ref_log("กำลังสร้างอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        selected_story_type = _current_ref_story_type_id()
        # Auto buttons are Context-driven by definition.  A single manual Ref
        # follows the new checkbox and never reads Tk variables in the worker.
        context_enabled = bool(auto or ref_use_context_var.get())
        if auto:
            auto_ref_stop[0] = False
            auto_ref_running[0] = True
            root.after(0, lambda: _set_auto_ref_button_running(True))
        else:
            root.after(0, lambda: _set_ref_action_buttons_running(True, auto=False))
        def worker():
            try:
                lock = globals().get("_bridge_queue_lock")
                def run_one(idx, name):
                    selected_match = bool(
                        g.get("_ref_selected_name") == name
                        and str(g.get("_ref_selected_kind") or "") in ("character", "location")
                    )
                    requested_kind = (
                        entity_kind or (str(g.get("_ref_selected_kind") or "") if selected_match else "")
                    ) if context_enabled else ""
                    if requested_kind:
                        context_kind, context_entity, _film = _context_entity(name, requested_kind)
                        context_kind = context_kind or requested_kind
                    else:
                        context_kind, context_entity, _film = "manual", {}, {}
                    location_design = None
                    ghost_design = None
                    if context_kind == "location" or entity_kind == "location":
                        _ref_log(f"[location-lock] กำลังอ่าน Context และออกแบบ: {name}")
                        location_design, reused = _design_location_via_context(name)
                        _ref_log(
                            f"[location-lock] {'ใช้แบบที่ล็อกไว้' if reused else 'สร้างและบันทึกแบบใหม่'}: {name}"
                        )
                    elif context_kind in ("character", "manual") and _is_ghost_character(name, context_entity):
                        _ref_log(f"[ghost-research] กำลังวิเคราะห์ชนิดผีและคติที่เกี่ยวข้อง: {name}")
                        ghost_design, reused = _analyze_ghost_character(name, context_entity, _film)
                        ghost_type = str(ghost_design.get("ghost_type") or "ผี").strip()
                        _ref_log(
                            f"[ghost-research] {'ใช้ Ghost Bible เดิม' if reused else 'วิเคราะห์และบันทึก Ghost Bible แล้ว'}: "
                            f"{name} — {ghost_type}"
                        )
                    prompt = _build_ref_prompt(
                        name,
                        entity_kind=entity_kind or context_kind,
                        location_design=location_design,
                        use_context_override=context_enabled,
                        ghost_design=ghost_design,
                    )
                    if context_kind == "manual":
                        _ref_log(f"[manual] ใช้เฉพาะชื่อที่พิมพ์: {name} — ไม่ดึง Prompt Context")
                    elif context_entity:
                        _ref_log(f"[context] ใช้ข้อมูล {context_kind or 'ref'} ของ {name} จาก Prompt Context")
                    else:
                        _ref_log(f"[context] พบเพียงชื่อ {name} — เติมเฉพาะรายละเอียดที่ Context ไม่ระบุ")
                    refine = g.get("_refine_prompt_via_ai") or globals().get("_refine_prompt_via_ai")
                    if callable(refine) and ghost_design is None:
                        _ref_log(f"[refine] ส่ง GPT แปลง prompt Ref: {name}")
                        # Select details are already embedded in `prompt`.
                        # Never append the full story context here.
                        refined_prompt = refine(prompt, kind="ref", use_context=False)
                        if refined_prompt and refined_prompt != prompt:
                            _ref_log(f"[refine] ได้ prompt ใหม่ ({len(refined_prompt)} chars)")
                            prompt = refined_prompt
                    else:
                        _ref_log(
                            "[refine] ใช้ Ghost Bible โดยตรงเพื่อไม่ให้ชนิดผีถูกตีความใหม่"
                            if ghost_design is not None else
                            "[refine] ไม่เจอตัวแปลง prompt — ใช้ prompt เดิม"
                        )
                    if auto and auto_ref_stop[0]:
                        _ref_log(f"[auto-ref] หยุดแล้ว — ข้าม {name}")
                        return
                    _ref_log(f"[auto-ref] รูปที่ {idx}/{len(names)} — เริ่มสร้าง: {name}")
                    payload = {
                        "model":"auto", "prompt":prompt, "n":1,
                        "aspect_ratio":"1:1", "history_and_training_disabled":False,
                        "_use_ref_story_history": True,
                    }
                    if ref_image and os.path.exists(ref_image):
                        with open(ref_image, "rb") as _f:
                            import base64 as _b64
                            payload["images"] = [_b64.b64encode(_f.read()).decode("utf-8")]
                        _ref_log(f"[ref] แนบรูปอ้างอิง: {os.path.basename(ref_image)}")
                    file_hint = _outfit_name_hint(name, entity_kind or context_kind)
                    out = g["_do_image_request"](payload, is_edit=bool(ref_image and os.path.exists(ref_image)), prompt=prompt, name_hint=file_hint, raw_prompt=prompt, output_dir=str(export_ref_dir))
                    out = _normalize_ref_output(out, name)
                    root.after(0, lambda out=out, ghost=bool(ghost_design): (
                        _ref_log(f"✓ {out}"), _ref_gallery_add(out, True, is_ghost=ghost), _notify_done()
                    ))
                def run_all():
                    for idx, name in enumerate(names, 1):
                        if auto and auto_ref_stop[0]:
                            _ref_log("[auto-ref] หยุดตามคำสั่ง")
                            break
                        run_one(idx, name)
                if lock:
                    with lock:
                        send_type = g.get("send_ref_story_type_lock")
                        if callable(send_type):
                            send_type(selected_story_type, log_fn=_ref_log)
                        run_all()
                else:
                    send_type = g.get("send_ref_story_type_lock")
                    if callable(send_type):
                        send_type(selected_story_type, log_fn=_ref_log)
                    run_all()
            except Exception as e:
                root.after(0, lambda e=e: _ref_log(f"ERROR: {e}"))
            finally:
                if auto:
                    auto_ref_running[0] = False
                    root.after(0, lambda: _set_auto_ref_button_running(False))
                else:
                    root.after(0, lambda: _set_ref_action_buttons_running(False, auto=False))
        threading.Thread(target=worker, daemon=True).start()
    g["_run_ref_jobs"] = _run_ref_jobs
    
    def generate_ref():
        name = ref_name_var.get().strip()
        if not name:
            _ref_log("ใส่ชื่อก่อน")
            return
        ref_img = ref_attach_path[0] if ref_attach_path and ref_attach_path[0] else None
        selected_kind = str(g.get("_ref_selected_kind", "") or "") if g.get("_ref_selected_name") == name else ""
        if ref_use_context_var.get():
            if not _load_ref_context().strip():
                _ref_log("[context] เปิดใช้ Context แต่ยังไม่มี Prompt Context — อัปเดต Context ก่อน")
                return
            detected_kind, detected_entity, _film = _context_entity(name, selected_kind)
            if not detected_entity:
                _ref_log(f"[context] ไม่พบ '{name}' ในตัวละครหรือสถานที่ — เลือกจาก Select หรือปิด ใช้ Context")
                return
            selected_kind = detected_kind
            _ref_log(f"[context] พบ {selected_kind}: {name} — โหลดข้อมูลจากเนื้อเรื่องก่อนสร้าง Ref")
        else:
            selected_kind = ""
            _ref_log(f"[manual] ปิดใช้ Context — สร้างจากชื่อ '{name}' เท่านั้น")
        _ref_log(_builder_set_selection_lock(lock_g, selected_kind if selected_kind in ("character", "location") else "ref", name))
        _run_ref_jobs([name], ref_image=ref_img, entity_kind=selected_kind)
    g["generate_ref"] = generate_ref
    
    def generate_auto_ref():
        ctx = _load_ref_context()
        if not ctx.strip():
            _ref_log("ไม่เจอ prompt_ref_context.txt")
            return
        names = _extract_auto_ref_names(ctx)
        if not names:
            _ref_log("ไม่เจอชื่อตัวละคร/สถานที่ใน ref context")
            return
        _ref_log(_builder_set_selection_locks(lock_g, "ref", names))
        _ref_log(f"[auto-ref] จะสร้างทั้งหมด {len(names)} รูป: " + ", ".join(names))
        _run_ref_jobs(names, auto=True, entity_kind="character")
    g["generate_auto_ref"] = generate_auto_ref
    
    def clear_ref_gallery():
        for child in gallery_inner.winfo_children():
            child.destroy()
        ref_gallery_images.clear()
        ref_gallery_cards.clear()
        ref_gallery_paths.clear()
        _save_ref_gallery()
        _ref_log("ล้างรูป Ref gallery แล้ว")
    g["clear_ref_gallery"] = clear_ref_gallery

    def _restore_ref_gallery():
        try:
            payload = json.loads(ref_gallery_state_path.read_text(encoding="utf-8"))
            saved = payload.get("paths") if isinstance(payload, dict) else []
        except FileNotFoundError:
            saved = [
                str(path) for path in sorted(
                    Path(export_ref_dir).glob("*"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
        except (json.JSONDecodeError, OSError):
            saved = []
        ref_gallery_paths[:] = [str(Path(path)) for path in saved if Path(path).is_file()]
        _save_ref_gallery()
        for path in reversed(ref_gallery_paths):
            _ref_gallery_add(path, prepend=True, persist=False)

    root.after(50, _restore_ref_gallery)
    
    make_ref_btn = tk.Button(row, text="🎭 สร้าง Ref", command=generate_ref, bg="#059669", fg="white", activebackground="#10B981", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    make_ref_btn.pack(side="left", padx=4)
    ref_make_btn[0] = make_ref_btn
    g["ref_make_btn"] = make_ref_btn
    
    # Pack Select AFTER สร้าง Ref (so สร้าง Ref appears first on the left)
    ref_select_btn.pack(side="left", padx=(0, 4))
    auto_btn = tk.Button(row, text="⚡ Auto ตัวละคร", command=generate_auto_ref, bg="#0EA5E9", fg="white", activebackground="#0284C7", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    auto_btn.pack(side="left", padx=4)
    auto_ref_btn[0] = auto_btn
    g["auto_ref_btn"] = auto_btn
    # --- Auto Ref Location button ---
    def _extract_auto_ref_location_names(text):
        """Extract location names from JSON Prompt Context."""
        try:
            import json as _json
            ctx = _json.loads(text) if isinstance(text, str) else text
            if not isinstance(ctx, dict):
                return []
            locs = []
            locs.extend(ctx.get("locations") or [])
            locs.extend(ctx.get("key_places") or [])
            locs.extend(ctx.get("scene_map") or [])
            story = ctx.get("story") if isinstance(ctx.get("story"), dict) else {}
            locs.extend(story.get("key_places") or [])
            if story.get("main_location"):
                locs.append(story.get("main_location"))
            seen = set()
            names = []
            for item in locs:
                if isinstance(item, dict):
                    n = str(item.get("name") or item.get("place") or item.get("location") or "").strip()
                elif isinstance(item, str):
                    n = item.strip()
                else:
                    continue
                if n and n not in seen:
                    seen.add(n)
                    names.append(n)
            return names
        except Exception:
            return []
    def generate_auto_ref_location():
        ctx_raw = _load_ref_context()
        if not ctx_raw.strip():
            _ref_log("ไม่เจอ prompt_ref_context.txt")
            return
        if ref_job_running[0]:
            _ref_log("กำลังสร้างอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        ref_job_running[0] = True
        _ref_log("[auto-ref location] กำลังวิเคราะห์เฉพาะสถานที่ที่มีเหตุการณ์เกิดขึ้นจริงในบท...")
        def discover_worker():
            try:
                lock = globals().get("_bridge_queue_lock")
                if lock:
                    with lock:
                        targets, reused = _discover_location_targets_via_context()
                else:
                    targets, reused = _discover_location_targets_via_context()
                names = [str(x.get("name") or "").strip() for x in targets if isinstance(x, dict) and str(x.get("name") or "").strip()]
                if not names:
                    raise RuntimeError("ไม่พบสถานที่สำหรับสร้าง Ref")
                def start_jobs():
                    ref_job_running[0] = False
                    _ref_log(
                        f"[auto-ref location] {'ใช้รายการที่ล็อกไว้' if reused else 'บันทึกรายการใหม่'} "
                        f"{len(names)} แห่ง: " + ", ".join(names)
                    )
                    ref_img = ref_attach_path[0] if ref_attach_path and ref_attach_path[0] else None
                    _run_ref_jobs(names, auto=True, ref_image=ref_img, entity_kind="location")
                root.after(0, start_jobs)
            except Exception as e:
                def fail(e=e):
                    ref_job_running[0] = False
                    _ref_log(f"[auto-ref location] ERROR: {e}")
                root.after(0, fail)
        threading.Thread(target=discover_worker, daemon=True).start()
    g["generate_auto_ref_location"] = generate_auto_ref_location
    auto_loc_btn = tk.Button(row, text="🏠 Auto สถานที่", command=generate_auto_ref_location, bg="#0288D1", fg="white", activebackground="#0277BD", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    auto_loc_btn.pack(side="left", padx=4)
    tk.Button(row, text="Clear", command=lambda: (ref_name_var.set(""), log.delete("1.0", tk.END)), bg="#DC2626", fg="white", activebackground="#B91C1C", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)
    tk.Button(row, text="🧹 ล้างรูป", command=clear_ref_gallery, bg="#DC2626", fg="white", activebackground="#B91C1C", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)
    g["ref_page"] = ref_page
    return ref_page
