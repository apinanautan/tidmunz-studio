# -*- coding: utf-8 -*-
"""Image AI page source UI. Mirrors original pyc layout from screenshot."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import json
import os
import re
from pathlib import Path
from typing import Any, Dict
from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()
from snapgen_page_builder import (
    make_log_box, append_log, set_selection_lock, remove_selection_lock,
)
from snapgen_button_styles import DEFAULT_PADX, DEFAULT_PADY, DEFAULT_WIDTH, STYLE

BG = "#F5F5F2"
PANEL = "#FFFFFF"
BORDER = "#D9D9D9"
BLUE = "#2563EB"
PURPLE = "#7C3AED"
ORANGE = "#F59E0B"
RED = "#DC2626"
PINK = "#DB2777"
CYAN = "#0891B2"
GRAY = "#6B7280"

def _load_prompt_ref_story_title() -> str:
    """Read title from the exact source story being uploaded, not stale Context."""
    data_dir = Path(__file__).resolve().parent.parent / "snapgen_data"
    try:
        for line in (data_dir / "prompt_ref_source.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                return line.strip()[:60]
    except OSError:
        pass
    try:
        context = json.loads((data_dir / "prompt_ref_context.json").read_text(encoding="utf-8"))
        story = context.get("story") if isinstance(context, dict) else None
        candidates = []
        if isinstance(story, dict):
            candidates.extend(story.get(key) for key in ("title", "name", "ชื่อเรื่อง"))
        if isinstance(context, dict):
            candidates.extend(context.get(key) for key in ("title", "name", "ชื่อเรื่อง"))
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()[:60]
    except (OSError, ValueError, TypeError):
        pass
    return "เรื่องจาก Prompt-Ref"


def _drop_image_paths(data, splitlist) -> list[str]:
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    try:
        values = splitlist(data)
    except Exception:
        values = [data]
    return [
        str(Path(value))
        for value in values
        if Path(str(value)).is_file() and Path(str(value)).suffix.lower() in allowed
    ][:10]


def _btn(parent, text, color, command=None, *, padx=12, pady=6, fg="white"):
    b = tk.Button(parent, text=text, command=command, bg=color, fg=fg,
                  activebackground=color, activeforeground=fg,
                  relief="flat", bd=0, cursor="hand2",
                  font=(SNAPGEN_UI_FONT, 9, "bold"), padx=padx, pady=pady)
    return b


def install(g: dict, root: tk.Misc) -> Dict[str, Any]:
    # Lock state belongs to this page only; never share it through global `g`.
    lock_g = {"_selection_locks": {}, "_selection_lock_vars": []}
    export_image_dir = g.get("EXPORT_IMAGE")
    data_dir = Path(__file__).resolve().parent.parent / "snapgen_data"
    page = tk.Frame(root, bg=BG)
    page.columnconfigure(0, weight=1)
    page.rowconfigure(5, weight=1, minsize=220)

    # Prompt panel
    prompt_frame = tk.LabelFrame(page, text="🎨 สร้างรูป AI", bg=BG, fg="#111",
                                 padx=6, pady=5, bd=1, relief="solid")
    prompt_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))
    prompt_frame.columnconfigure(0, weight=1)
    prompt_frame.columnconfigure(1, weight=0)

    # This page displays the one Prompt-Ref story. Only Prompt-Ref owns the
    # New Story action and the conversation cursor.
    story_header = tk.Frame(prompt_frame, bg=BG)
    story_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3))
    story_header.columnconfigure(2, weight=1)
    has_main_story = g.get("has_main_story_history")
    story_title_var = tk.StringVar(
        value=(_load_prompt_ref_story_title() if callable(has_main_story) and has_main_story() else "")
    )
    tk.Label(story_header, text="เรื่อง:", bg=BG, fg="#111", font=(SNAPGEN_UI_FONT, 9, "bold")).grid(row=0, column=0, sticky="w")
    tk.Label(
        story_header, textvariable=story_title_var, width=32, anchor="w",
        bg=PANEL, fg="#111", relief="solid", bd=1, padx=7, pady=3,
    ).grid(row=0, column=1, sticky="w", padx=(6, 8))
    # Keep all top actions in one clean, right-aligned group.  The prompt
    # frame already supplies the outer padding, so padx=(8, 0) leaves the
    # right edge visually aligned with the box while separating it from title.
    top_actions = tk.Frame(story_header, bg=BG)
    top_actions.grid(row=0, column=2, sticky="e", padx=(8, 0))
    for column in range(1):
        top_actions.columnconfigure(column, weight=1, uniform="image_top_action")

    clear_gallery_btn = _btn(
        top_actions, "🧹 ล้างรูป", RED,
        lambda: (g.get("clear_gallery") or (lambda: None))(),
        padx=DEFAULT_PADX, pady=DEFAULT_PADY,
    )
    for column, button in enumerate((clear_gallery_btn,)):
        button.configure(width=DEFAULT_WIDTH)
        button.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=(0 if column == 0 else 5, 0),
        )

    # The old visible "ต่อจากฉากก่อน" button was removed. Keep the variable
    # for the optional internal context helper so no late callback can crash.
    attach_btn = None

    # Keep Prompt compact so Log and Gallery remain visible on common screens.
    prompt_text = tk.Text(prompt_frame, height=4, wrap="word", bg=PANEL, fg="#111",
                          bd=1, relief="solid", font=(SNAPGEN_UI_FONT, 11), padx=6, pady=4)
    prompt_text.grid(row=1, column=0, sticky="nsew", padx=(0, 5))

    # Keep both controls in one compact cluster so neither grid column can
    # stretch independently. Equal uniform columns guarantee matching widths.
    side_controls = tk.Frame(prompt_frame, bg=BG, bd=0)
    side_controls.grid(row=1, column=1, sticky="nse")
    side_controls.rowconfigure(0, weight=1)
    side_controls.columnconfigure(0, weight=1, minsize=150, uniform="image_side_control")
    side_controls.columnconfigure(1, weight=1, minsize=150, uniform="image_side_control")

    # This picker is the same source of truth as reference matching: real files.
    character_frame = tk.LabelFrame(
        side_controls,
        text="📎 เลือกไฟล์แนบ",
        bg=BG,
        fg="#111",
        padx=4,
        pady=3,
        bd=1,
        relief="solid",
        width=150,
    )
    character_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    character_var = tk.StringVar(value="เลือกไฟล์แนบ")
    character_picker = ttk.Combobox(
        character_frame,
        state="readonly",
        width=13,
        textvariable=character_var,
        values=["เลือกไฟล์แนบ"],
        font=(SNAPGEN_UI_FONT, 9),
    )
    character_picker.pack(fill="x", pady=(1, 5))

    # Remember the last editable field and caret position on the Image AI page.
    # Clicking the character picker or the Paste button moves keyboard focus, so
    # root.focus_get() alone cannot identify where the user wanted the name.
    _character_insert_target = {"widget": prompt_text, "index": "1.0"}

    def _remember_character_insert_target(event=None, widget=None):
        target = widget or getattr(event, "widget", None)
        if target is None or not isinstance(target, (tk.Text, tk.Entry, ttk.Entry)):
            return
        try:
            if not target.winfo_exists():
                return
            cursor = target.index(tk.INSERT)
        except Exception:
            return
        _character_insert_target["widget"] = target
        _character_insert_target["index"] = cursor

    def _bind_character_insert_target(widget):
        for sequence in ("<FocusIn>", "<ButtonRelease-1>", "<KeyRelease>"):
            try:
                widget.bind(sequence, _remember_character_insert_target, add="+")
            except Exception:
                pass
        _remember_character_insert_target(widget=widget)

    _bind_character_insert_target(prompt_text)

    def _capture_character_target_before_picker(_event=None):
        # Capture the current field before the readonly combobox takes focus.
        try:
            focused = root.focus_get()
        except Exception:
            focused = None
        _remember_character_insert_target(widget=focused)
        return _refresh_image_character_options(_event)

    def _refresh_image_character_options(_event=None):
        try:
            names = [name for name, _path in _list_ref_files()]
        except NameError:
            names = []
        values = ["เลือกไฟล์แนบ", *names]
        character_picker.configure(values=values)
        if character_var.get() not in values:
            character_var.set("เลือกไฟล์แนบ")
        return names

    def _paste_image_character():
        selected = character_var.get().strip()
        if not selected or selected == "เลือกไฟล์แนบ":
            _log("เลือกชื่อไฟล์แนบก่อนกดวาง")
            character_picker.focus_set()
            return

        target = _character_insert_target.get("widget") or prompt_text
        saved_index = _character_insert_target.get("index")
        try:
            if not target.winfo_exists():
                target = prompt_text
                saved_index = target.index(tk.INSERT)

            if isinstance(target, tk.Text):
                ranges = target.tag_ranges(tk.SEL)
                if len(ranges) >= 2:
                    insert_at = target.index(ranges[0])
                    target.delete(ranges[0], ranges[1])
                else:
                    try:
                        insert_at = target.index(saved_index)
                    except Exception:
                        insert_at = target.index(tk.INSERT)
                before = target.get("1.0", insert_at)
                after = target.get(insert_at, "end-1c")
            else:
                value = target.get()
                try:
                    if target.selection_present():
                        insert_at = int(target.index(tk.SEL_FIRST))
                        selection_end = int(target.index(tk.SEL_LAST))
                        target.delete(insert_at, selection_end)
                        value = target.get()
                    else:
                        insert_at = int(saved_index)
                except Exception:
                    try:
                        insert_at = int(target.index(tk.INSERT))
                    except Exception:
                        insert_at = len(value)
                insert_at = max(0, min(insert_at, len(value)))
                before = value[:insert_at]
                after = value[insert_at:]

            prefix = ""
            suffix = ""
            if before and not before[-1].isspace() and before[-1] not in "([{/:,;—–-":
                prefix = " "
            if after and not after[0].isspace() and after[0] not in ".,;:!?)]}/—–-":
                suffix = " "
            inserted = prefix + selected + suffix

            target.insert(insert_at, inserted)
            if isinstance(target, tk.Text):
                new_index = target.index(f"{insert_at}+{len(inserted)}c")
                target.mark_set(tk.INSERT, new_index)
                target.see(tk.INSERT)
            else:
                new_index = int(insert_at) + len(inserted)
                target.icursor(new_index)
                try:
                    target.xview_moveto(1.0)
                except Exception:
                    pass
            target.focus_set()
            _character_insert_target["widget"] = target
            _character_insert_target["index"] = new_index
            destination = "Prompt หลัก" if target is prompt_text else "ช่องแก้รูป"
            _log(f"วางชื่อไฟล์แนบลง{destination}: {selected}")
        except Exception as exc:
            _log(f"วางชื่อไฟล์แนบไม่สำเร็จ: {exc}")

    paste_character_btn = _btn(
        character_frame,
        "วาง",
        "#E5E7EB",
        _paste_image_character,
        padx=8,
        pady=3,
        fg="#111",
    )
    paste_character_btn.configure(
        activebackground="#D1D5DB",
        activeforeground="#111",
    )
    paste_character_btn.pack(fill="x")
    character_picker.bind("<Button-1>", _capture_character_target_before_picker, add="+")
    character_picker.bind("<FocusIn>", _refresh_image_character_options, add="+")
    root.after(0, _refresh_image_character_options)

    camera_frame = tk.LabelFrame(
        side_controls,
        text="🎥 ควบคุมกล้อง",
        bg=BG,
        fg="#111",
        padx=4,
        pady=3,
        bd=1,
        relief="solid",
        width=150,
    )
    camera_frame.grid(row=0, column=1, sticky="nsew")
    camera_mode = {"key": "auto"}
    camera_labels = {
        "auto": "ตาม Prompt",
        "face": "เน้นหน้า",
        "character": "เน้นตัวละคร",
        "object": "มาโคร / วัตถุ",
    }
    camera_instructions = {
        "face": (
            "FINAL CAMERA OVERRIDE: close-up, eye-level, 85mm portrait lens. Frame the main "
            "character's eyes, face, and readable emotion. This replaces every earlier shot size, "
            "framing, lens, camera-angle, focus, and depth-of-field instruction. Keep identity, "
            "wardrobe, action, location, lighting, and story event unchanged."
        ),
        "character": (
            "FINAL CAMERA OVERRIDE: medium shot, eye-level, 50mm lens. Frame the main character's "
            "upper body, gesture, and body language while retaining enough location context. This "
            "replaces every earlier shot size, framing, lens, camera-angle, focus, and depth-of-field "
            "instruction. Keep identity, wardrobe, action, location, lighting, and story event unchanged."
        ),
        "object": (
            "FINAL CAMERA OVERRIDE: identify the most visually meaningful non-character object or "
            "small environmental detail already named or clearly implied by the prompt. Frame it as "
            "a tight insert close-up, using true macro treatment only when the subject is genuinely "
            "small or detail-rich (for example a leaf, grass blade, droplet, insect, texture, or tiny prop); "
            "otherwise use a natural close object shot. Choose the suitable close-focus lens and depth "
            "of field for that subject. Do not invent a new object, and do not turn this into a face-focused "
            "portrait or a broad establishing view. This replaces every earlier shot size, framing, lens, "
            "camera-angle, focus, and depth-of-field instruction. Keep the existing story event, location, "
            "lighting, continuity, and relevant characters unchanged."
        ),
    }
    camera_buttons = {}

    def _set_camera_mode(key, *, write_log=True):
        camera_mode["key"] = key if key in camera_labels else "auto"
        for mode_key, button in camera_buttons.items():
            active = mode_key == camera_mode["key"]
            button.config(
                bg=BLUE if active else "#E5E7EB",
                fg="white" if active else "#111",
                activebackground=BLUE if active else "#D1D5DB",
                activeforeground="white" if active else "#111",
            )
        if write_log:
            _log(f"กล้อง: {camera_labels[camera_mode['key']]}")
            queue_save = g.get("_queue_image_work_state_save")
            if callable(queue_save):
                queue_save()

    for camera_key in ("auto", "face", "character", "object"):
        camera_button = _btn(
            camera_frame,
            camera_labels[camera_key],
            "#E5E7EB",
            lambda key=camera_key: _set_camera_mode(key),
            padx=8,
            pady=2,
            fg="#111",
        )
        camera_button.pack(fill="x", pady=1)
        camera_buttons[camera_key] = camera_button
    _set_camera_mode("auto", write_log=False)

    def _apply_camera_override(prompt, camera_key):
        """Replace Prompt-Ref camera settings; never stack conflicting camera commands."""
        text = str(prompt or "").strip()
        guidance = camera_instructions.get(camera_key, "")
        if not guidance:
            return text
        # Remove old SnapGen camera blocks and compact shot/lens/angle phrases.
        text = re.sub(r"(?is)\s*(?:CAMERA CONTROL|FINAL CAMERA OVERRIDE)\s*:.*$", "", text).strip()
        text = re.sub(
            r"(?i)\b(?:extreme\s+close[- ]?up|medium\s+close[- ]?up|medium\s+wide\s+shot|"
            r"medium\s+shot|close[- ]?up|wide\s+shot|long\s+shot|full\s+shot)\b",
            "",
            text,
        )
        text = re.sub(r"(?i)\b(?:lens\s*)?\d{2,3}\s*mm\b", "", text)
        text = re.sub(r"กล้องระดับ(?:สายตา|สูง|ต่ำ)(?:เล็กน้อย)?", "", text)
        text = re.sub(r"มุมกล้อง(?:ระดับสายตา|สูง|ต่ำ|ด้านข้าง|ตรง|เฉียง)(?:เล็กน้อย)?", "", text)
        text = re.sub(r"เลนส์\s*\d{2,3}\s*mm", "", text, flags=re.I)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.rstrip(" ,.;\n") + "\n\n" + guidance

    # Action bar like original screenshot
    bar = tk.Frame(page, bg=BG)
    bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 2))

    img_aspect_var = g.get("img_aspect_var") or tk.StringVar(value="16:9")
    img_lighting_var = g.get("img_lighting_var") or tk.StringVar(value="☀ กลางวัน")
    g["img_aspect_var"] = img_aspect_var
    g["img_lighting_var"] = img_lighting_var

    gen_btn = _btn(
        bar, "↻ สร้างรูป", "#059669", lambda: g["generate_image_standalone"](False),
        padx=DEFAULT_PADX, pady=DEFAULT_PADY,
    )
    gen_btn.configure(width=DEFAULT_WIDTH)
    gen_btn.pack(side="left", padx=(0, 6))
    tk.Label(bar, text="ขนาด:", bg=BG).pack(side="left")
    tk.OptionMenu(bar, img_aspect_var, *(g.get("IMG_ASPECT_RATIOS") or ["16:9", "9:16", "1:1", "4:3", "3:4"])).pack(side="left", padx=(2, 8))
    tk.Label(bar, text="แสง:", bg=BG).pack(side="left")
    lighting_keys = list((g.get("LIGHTING_PRESETS") or {"☀ กลางวัน":"", "🌙 กลางคืน":""}).keys())
    lighting_auto_state = {
        "programmatic": False,
        "manual_for_prompt": False,
        "last_prompt": None,
        "after_id": None,
    }

    def _find_lighting_key(kind):
        marker = "กลางคืน" if kind == "night" else "กลางวัน"
        return next((key for key in lighting_keys if marker in str(key)), None)

    def _infer_lighting_from_prompt(value):
        """Return day/night from the latest explicit time cue in the prompt."""
        content = str(value or "").lower()
        cues = {
            "night": (
                "กลางคืน", "ยามค่ำคืน", "ยามค่ำ", "ค่ำมืด", "ตอนค่ำ", "คืนดึก",
                "ดึกสงัด", "ราตรี", "แสงจันทร์", "ใต้แสงจันทร์", "พระจันทร์",
                "ท้องฟ้ายามคืน", "หลังพระอาทิตย์ตก", "หลังตะวันตกดิน",
                "nighttime", "at night", "moonlight", "midnight", "after sunset",
            ),
            "day": (
                "กลางวัน", "ยามเช้า", "ตอนเช้า", "รุ่งเช้า", "เช้าตรู่", "อรุณรุ่ง",
                "ยามสาย", "ตอนสาย", "เที่ยงวัน", "แสงแดด", "แดดจ้า", "ฟ้าสว่าง",
                "พระอาทิตย์ขึ้น", "ก่อนพระอาทิตย์ตก", "daytime", "morning",
                "sunlight", "daylight", "sunrise", "noon",
            ),
        }
        latest = (-1, None)
        for kind, words in cues.items():
            for word in words:
                index = content.rfind(word)
                if index > latest[0]:
                    latest = (index, kind)
        return latest[1]

    def _set_lighting_from_prompt(prompt_value):
        inferred = _infer_lighting_from_prompt(prompt_value)
        if not inferred:
            return False
        target = _find_lighting_key(inferred)
        if not target or img_lighting_var.get() == target:
            return False
        lighting_auto_state["programmatic"] = True
        try:
            img_lighting_var.set(target)
        finally:
            lighting_auto_state["programmatic"] = False
        _log(f"แสงอัตโนมัติจาก Prompt: {target}")
        return True

    def _lighting_selected_by_user(value):
        if lighting_auto_state["programmatic"]:
            return
        lighting_auto_state["manual_for_prompt"] = True
        _log(f"แสงเลือกเอง: {value}")

    lighting_menu = tk.OptionMenu(
        bar,
        img_lighting_var,
        *lighting_keys,
        command=_lighting_selected_by_user,
    )
    lighting_menu.pack(side="left", padx=(2, 8))

    image_work_save_after = [None]

    def _save_image_work_state():
        """Persist the one Image page state shared by desktop and mobile."""
        try:
            cfg = g.get("load_config", lambda: {})() or {}
            cfg["image_work_state"] = {
                "prompt": prompt_text.get("1.0", tk.END).rstrip("\n"),
                "aspect": str(img_aspect_var.get() or ""),
                "lighting": str(img_lighting_var.get() or ""),
                "camera": str(camera_mode.get("key") or "auto"),
            }
            g.get("save_config", lambda _cfg: None)(cfg)
        except Exception as exc:
            _log(f"[image] บันทึกงานล่าสุดไม่ได้: {exc}")

    def _queue_image_work_state_save(*_args):
        try:
            if image_work_save_after[0]:
                root.after_cancel(image_work_save_after[0])
        except Exception:
            pass
        image_work_save_after[0] = root.after(350, _save_image_work_state)

    def _load_image_work_state():
        try:
            cfg = g.get("load_config", lambda: {})() or {}
            saved = cfg.get("image_work_state")
            if not isinstance(saved, dict):
                return
            prompt = str(saved.get("prompt") or "")
            prompt_text.delete("1.0", tk.END)
            prompt_text.insert("1.0", prompt)
            aspect = str(saved.get("aspect") or "")
            lighting = str(saved.get("lighting") or "")
            if aspect in (g.get("IMG_ASPECT_RATIOS") or []):
                img_aspect_var.set(aspect)
            if lighting in lighting_keys:
                img_lighting_var.set(lighting)
            _set_camera_mode(str(saved.get("camera") or "auto"), write_log=False)
        except Exception as exc:
            _log(f"[image] โหลดงานล่าสุดไม่ได้: {exc}")

    g["_save_image_work_state"] = _save_image_work_state
    g["_queue_image_work_state_save"] = _queue_image_work_state_save
    _load_image_work_state()
    img_aspect_var.trace_add("write", _queue_image_work_state_save)
    img_lighting_var.trace_add("write", _queue_image_work_state_save)

    def _image_prompt_modified(_event=None):
        try:
            if prompt_text.edit_modified():
                prompt_text.edit_modified(False)
                _queue_image_work_state_save()
        except Exception:
            pass

    prompt_text.edit_modified(False)
    prompt_text.bind("<<Modified>>", _image_prompt_modified, add="+")

    def _check_prompt_lighting():
        lighting_auto_state["after_id"] = None
        current = prompt_text.get("1.0", tk.END).strip()
        if current == lighting_auto_state["last_prompt"]:
            return
        # A changed prompt starts a new lighting decision. Automatic detection
        # runs once for that content; a later manual menu choice wins until the
        # prompt changes again.
        lighting_auto_state["last_prompt"] = current
        lighting_auto_state["manual_for_prompt"] = False
        _set_lighting_from_prompt(current)

    def _schedule_prompt_lighting(_event=None):
        try:
            pending = lighting_auto_state.get("after_id")
            if pending:
                root.after_cancel(pending)
        except Exception:
            pass
        lighting_auto_state["after_id"] = root.after(350, _check_prompt_lighting)

    prompt_text.bind("<<Modified>>", _schedule_prompt_lighting, add="+")
    root.after(450, _check_prompt_lighting)
    prompt_btn = _btn(
        bar, "Prompt", PURPLE,
        lambda: (g.get("pick_prompt_for_image") or g.get("preview_and_insert_refs") or (lambda: None))(),
        padx=DEFAULT_PADX, pady=DEFAULT_PADY,
    )
    prompt_btn.configure(width=DEFAULT_WIDTH)
    prompt_btn.pack(side="left", padx=(8, 3))
    clear_btn = _btn(
        bar, "Clear", RED,
        lambda: (prompt_text.delete("1.0", tk.END), _schedule_ref_highlight(),
                 (g.get("_img_log") or (lambda _m: None))("ล้าง prompt แล้ว")),
        padx=DEFAULT_PADX, pady=DEFAULT_PADY,
    )
    clear_btn.configure(width=DEFAULT_WIDTH)
    clear_btn.pack(side="left", padx=3)

    # Reference-folder controls share the main generation row and stay pinned
    # to its right edge. Do not create a separate row below this bar.
    ref_row = tk.Frame(bar, bg=BG)
    ref_row.pack(side="right")
    ref_folder = g.get("img_ref_folder") or [None]
    ref_names_var = g.get("img_ref_names_var") or tk.StringVar(value="")
    ref_match_var = tk.StringVar(value="โฟลเดอร์: ไม่มี")
    choose_btn = _btn(ref_row, "📂 เลือกโฟลเดอร์อ้างอิง", ORANGE, g.get("browse_ref_folder"),
                      padx=DEFAULT_PADX, pady=DEFAULT_PADY)
    choose_btn.configure(width=DEFAULT_WIDTH)
    # Resolve callbacks when the user clicks. Their implementations are
    # defined later in install(), after all page state has been created.
    # SCENE CONTINUITY CONTRACT:
    # This button selects an image from the previous event/shot. It is not a
    # shortcut for "latest file" and it is not a character-reference picker.
    # The selected image is uploaded once as a separate Vision turn in the
    # continuing Image AI conversation. It must not be attached again to each
    # generation where it could conflict with character/location references.
    ref_match_label = tk.Label(ref_row, textvariable=ref_match_var, fg=PURPLE, bg=BG, anchor="e",
                               font=(SNAPGEN_UI_FONT, 9, "bold"))
    # Compatibility name for code that expects img_ref_label. There is only
    # one visible status label; never add folder-name and match-count labels again.
    ref_label = ref_match_label
    ref_clear_btn = _btn(ref_row, "X", RED, g.get("clear_ref_folder"), padx=8)
    # Pack from right to left: clear, chooser, one compact status.
    ref_clear_btn.pack(side="right")
    choose_btn.pack(side="right", padx=(3, 3))
    ref_match_label.pack(side="right", padx=(8, 6))

    # Log panel
    log_frame = tk.Frame(page, bg=BG)
    log_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 2))
    log_frame.columnconfigure(0, weight=1)
    log_box = make_log_box(log_frame, bg=PANEL)
    log_box.grid(row=0, column=0, sticky="ew")

    # Gallery panel
    gallery_frame = tk.LabelFrame(page, text="แกลเลอรี", bg=BG, padx=8, pady=8, bd=1, relief="solid")
    gallery_frame.grid(row=5, column=0, sticky="nsew", padx=8, pady=(4, 8))
    gallery_frame.columnconfigure(0, weight=1)
    gallery_frame.rowconfigure(0, weight=1)
    gallery = tk.Canvas(gallery_frame, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
    scroll = tk.Scrollbar(gallery_frame, orient="vertical", command=gallery.yview)
    inner = tk.Frame(gallery, bg=PANEL)
    gallery_window = gallery.create_window((0, 0), window=inner, anchor="nw")
    gallery.configure(yscrollcommand=scroll.set)
    gallery.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")

    gallery_cards = []
    gallery_columns = [0]
    gallery_layout_dirty = [True]
    gallery_sync_after = [None]

    def _relayout_gallery():
        try:
            width = max(gallery.winfo_width(), gallery_frame.winfo_width())
            columns = 4 if width >= 1320 else (3 if width >= 980 else (2 if width >= 640 else 1))
            columns_changed = columns != gallery_columns[0]
            if not columns_changed and not gallery_layout_dirty[0]:
                return
            if columns_changed:
                for column in range(4):
                    inner.grid_columnconfigure(column, weight=0)
                for column in range(columns):
                    inner.grid_columnconfigure(column, weight=1, uniform="image_gallery_cards")
                gallery_columns[0] = columns
            for index, card in enumerate(gallery_cards):
                card.grid(
                    row=index // columns, column=index % columns,
                    sticky="nsew", padx=4, pady=4,
                )
            gallery_layout_dirty[0] = False
        except Exception:
            pass

    def _apply_gallery_layout():
        gallery_sync_after[0] = None
        try:
            gallery.itemconfigure(gallery_window, width=max(gallery.winfo_width() - 4, 1))
        except Exception:
            pass
        _relayout_gallery()
        try:
            gallery.configure(scrollregion=gallery.bbox("all") or (0, 0, 0, 0))
        except Exception:
            pass

    def _sync_gallery_scrollregion(_event=None):
        # Configure events arrive in bursts while cards are created/resized.
        # Coalesce them so Tk performs one layout pass instead of one full pass
        # for every child widget event.
        try:
            pending = gallery_sync_after[0]
            if pending:
                root.after_cancel(pending)
        except Exception:
            pass
        gallery_sync_after[0] = root.after(80, _apply_gallery_layout)

    def _gallery_on_mousewheel(event):
        # Scroll when the pointer is anywhere over the gallery area,
        # not only on the thin scrollbar.
        try:
            if not gallery.winfo_exists():
                return
            first, last = gallery.yview()
            if float(last) - float(first) >= 0.999:
                return
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return
            gallery.yview_scroll(int(-1 * (delta / 120)), "units")
            return "break"
        except Exception:
            return

    def _bind_gallery_wheel(widget):
        try:
            widget.bind("<Enter>", lambda _e: gallery.bind_all("<MouseWheel>", _gallery_on_mousewheel), add="+")
            widget.bind("<Leave>", lambda _e: gallery.unbind_all("<MouseWheel>"), add="+")
            widget.bind("<MouseWheel>", _gallery_on_mousewheel, add="+")
        except Exception:
            pass

    inner.bind("<Configure>", _sync_gallery_scrollregion)
    gallery.bind("<Configure>", _sync_gallery_scrollregion)
    for _w in (gallery_frame, gallery, inner, scroll):
        _bind_gallery_wheel(_w)
    g["_bind_gallery_wheel"] = _bind_gallery_wheel
    g["_sync_gallery_scrollregion"] = _sync_gallery_scrollregion

    thumbs = []
    history = []
    gallery_state_path = Path(__file__).resolve().parent.parent / "snapgen_data" / "meta" / "image_gallery.json"
    gallery_paths = []
    mobile_editors = {}
    gallery_pending_paths = []
    gallery_rendered_paths = set()
    GALLERY_INITIAL_RENDER = 18
    GALLERY_LOAD_BATCH = 18
    busy = [False]
    first_row = [None]
    auto_gen_state = {"running": False, "cancel": False}

    def _notify_done():
        notify = g.get("_snapgen_notify_done")
        if callable(notify):
            try:
                notify()
            except Exception:
                pass

    manual_refs = []
    prompt_drop_refs = []
    folder_ref_names = []
    has_saved_story = g.get("has_main_story_history")
    story_context_state = {
        # A complete persisted cursor means GPT already received the context
        # before SnapGen was closed. Do not force the user to upload it again.
        "ready": bool(has_saved_story()) if callable(has_saved_story) else False,
        "sending": False,
    }

    def _attach_refs():
        from tkinter import filedialog
        files = filedialog.askopenfilenames(title="แนบรูป", filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
        if not files:
            return
        for old_ref in manual_refs:
            remove_selection_lock(lock_g, "reference", os.path.splitext(os.path.basename(old_ref))[0])
        manual_refs[:] = list(files)[:10]
        for path in manual_refs:
            set_selection_lock(lock_g, "reference", os.path.splitext(os.path.basename(path))[0], append=True)
        _save_ref_state()
        _log(f"✅ แนบ {len(manual_refs)} รูป")
        _update_ref_highlight(log=True)

    def _clear_refs():
        for old_ref in manual_refs:
            remove_selection_lock(lock_g, "reference", os.path.splitext(os.path.basename(old_ref))[0])
        manual_refs.clear(); _save_ref_state(); _log("ล้างรูปแนบแล้ว"); _update_ref_highlight()

    def _log(msg):
        append_log(log_box, msg)

    def _resolve_portable_ref_folder(saved):
        """Resolve a saved reference folder after drive letters change."""
        if not saved:
            return ""
        original = Path(str(saved))
        if original.is_dir():
            return str(original)
        candidates = []
        try:
            # Google Drive commonly keeps the same path but receives another
            # drive letter on a different PC (G: -> H:, for example).
            parts = original.parts
            tail = Path(*parts[1:]) if original.drive and len(parts) > 1 else original
            if os.name == "nt":
                for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                    candidates.append(Path(f"{letter}:/") / tail)
            project_root = Path(__file__).resolve().parent.parent
            candidates.extend([
                project_root / "export" / "ref" / original.name,
                project_root / "export" / "ref",
            ])
        except Exception:
            candidates = []
        for candidate in candidates:
            try:
                if candidate.is_dir():
                    return str(candidate)
            except Exception:
                pass
        return ""

    def _load_saved_ref_state():
        try:
            cfg = g.get("load_config", lambda: {})() or {}
            last_dirs = cfg.get("last_dirs") if isinstance(cfg.get("last_dirs"), dict) else {}
            saved_folder = last_dirs.get("image_ref") or cfg.get("ref_folder")
            folder = _resolve_portable_ref_folder(saved_folder)
            if folder:
                ref_folder[0] = folder
            saved_files = cfg.get("image_manual_refs") or []
            if isinstance(saved_files, list):
                restored = []
                for path in saved_files:
                    if os.path.isfile(str(path)):
                        restored.append(str(path))
                    elif folder:
                        moved = os.path.join(folder, os.path.basename(str(path)))
                        if os.path.isfile(moved):
                            restored.append(moved)
                manual_refs[:] = restored[:10]
            prompt_drop_refs[:] = [
                str(path) for path in (cfg.get("image_prompt_drop_refs") or [])
                if os.path.isfile(str(path))
            ][:10]
            # Persist the repaired local path, or clear only this PC's stale
            # reference values so Repair does not warn forever.
            changed = bool(saved_folder and folder != str(saved_folder))
            if folder:
                last_dirs["image_ref"] = folder
                cfg["ref_folder"] = folder
            elif saved_folder:
                last_dirs.pop("image_ref", None)
                cfg.pop("ref_folder", None)
                changed = True
            cfg["last_dirs"] = last_dirs
            cfg["image_manual_refs"] = list(manual_refs)
            cfg["image_prompt_drop_refs"] = list(prompt_drop_refs)
            if changed:
                g.get("save_config", lambda _cfg: None)(cfg)
        except Exception as exc:
            _log(f"[ref] โหลดสถานะรูปอ้างอิงไม่ได้: {exc}")

    def _save_ref_state():
        try:
            cfg = g.get("load_config", lambda: {})() or {}
            last_dirs = cfg.get("last_dirs") if isinstance(cfg.get("last_dirs"), dict) else {}
            folder = ref_folder[0] if ref_folder and ref_folder[0] and os.path.isdir(ref_folder[0]) else ""
            if folder:
                last_dirs["image_ref"] = folder
                cfg["ref_folder"] = folder
            else:
                last_dirs.pop("image_ref", None)
                cfg.pop("ref_folder", None)
            cfg["last_dirs"] = last_dirs
            cfg["image_manual_refs"] = [str(path) for path in manual_refs if os.path.isfile(str(path))][:10]
            cfg["image_prompt_drop_refs"] = [
                str(path) for path in prompt_drop_refs if os.path.isfile(str(path))
            ][:10]
            g.get("save_config", lambda _cfg: None)(cfg)
        except Exception as exc:
            _log(f"[ref] บันทึกสถานะรูปอ้างอิงไม่ได้: {exc}")

    _load_saved_ref_state()
    if ref_folder[0] and os.path.isdir(ref_folder[0]):
        try:
            _restored_images = [
                name for name in os.listdir(ref_folder[0])
                if os.path.splitext(name)[1].lower() in (".png", ".jpg", ".jpeg", ".webp")
            ]
            ref_match_var.set("ใช้ไฟล์แนบ 0 รูป")
            ref_names_var.set(", ".join(os.path.splitext(name)[0] for name in _restored_images))
            folder_ref_names[:] = [os.path.splitext(name)[0] for name in _restored_images]
            for name in folder_ref_names:
                set_selection_lock(lock_g, "reference", name, append=True)
        except Exception as exc:
            _log(f"[ref] แสดงโฟลเดอร์อ้างอิงเดิมไม่ได้: {exc}")
    for _restored_ref in manual_refs:
        set_selection_lock(
            lock_g, "reference", os.path.splitext(os.path.basename(_restored_ref))[0], append=True,
        )
    for _restored_ref in prompt_drop_refs:
        set_selection_lock(
            lock_g, "reference", os.path.splitext(os.path.basename(_restored_ref))[0], append=True,
        )

    _REF_HL_COLORS = ("#7C3AED", "#2563EB", "#DB2777", "#0891B2", "#F59E0B")
    _ref_preview_after = [None]

    def _list_ref_files():
        out = []
        seen = set()
        folder = ref_folder[0] if ref_folder else None
        if folder and os.path.isdir(folder):
            try:
                for fn in sorted(os.listdir(folder)):
                    if os.path.splitext(fn)[1].lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        path = os.path.join(folder, fn)
                        stem = os.path.splitext(fn)[0]
                        if path not in seen:
                            seen.add(path)
                            out.append((stem, path))
            except Exception:
                pass
        for path in manual_refs:
            if path and path not in seen and os.path.exists(path):
                seen.add(path)
                out.append((os.path.splitext(os.path.basename(path))[0], path))
        for path in prompt_drop_refs:
            if path and path not in seen and os.path.exists(path):
                seen.add(path)
                out.append((os.path.splitext(os.path.basename(path))[0], path))
        return out

    def _drop_prompt_images(event=None, paths=None):
        paths = paths or _drop_image_paths(getattr(event, "data", ""), root.tk.splitlist)
        if not paths:
            _log("ลากได้เฉพาะไฟล์ PNG, JPG, JPEG หรือ WEBP")
            return "break"
        for path in paths:
            if path not in prompt_drop_refs:
                prompt_drop_refs.append(path)
            name = os.path.splitext(os.path.basename(path))[0]
            set_selection_lock(lock_g, "reference", name, append=True)
            current = prompt_text.get("1.0", tk.END).strip()
            if name.casefold() not in current.casefold():
                prompt_text.insert(tk.END, (" " if current else "") + name)
        del prompt_drop_refs[10:]
        _save_ref_state()
        _refresh_image_character_options()
        _update_ref_highlight(log=True)
        _log(f"✅ ลากแนบ {len(paths)} รูปแล้ว — ใส่ชื่อไฟล์ใน Prompt อัตโนมัติ")
        return "break"

    try:
        from tkinterdnd2 import DND_FILES
        prompt_text.drop_target_register(DND_FILES)
        prompt_text.dnd_bind("<<Drop>>", _drop_prompt_images)
    except Exception as exc:
        _log(f"[ลากรูป] เปิดใช้ไม่ได้: {exc}")

    def _matching_ref_files_for_text(text):
        """Match reference names by span and suppress ownership-only character mentions."""
        prompt = str(text or "")
        lowered = prompt.lower()

        # Character names are read from the current story context. A character
        # name used only as an owner/location qualifier (บ้านของแบงค์,
        # ครอบครัวแบงค์) must not attach that character's portrait.
        character_names = set()
        try:
            context_path = Path(BASE_ROOT) / "snapgen_data" / "prompt_ref_context.json"
            payload = json.loads(context_path.read_text(encoding="utf-8"))
            for item in payload.get("characters", []) if isinstance(payload, dict) else []:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip().casefold()
                    if name:
                        character_names.add(name)
        except Exception:
            pass

        ownership_prefixes = (
            "ของ", "ครอบครัว", "บ้านของ", "ไร่ของ", "สวนของ", "ร้านของ",
            "รถของ", "ห้องของ", "ที่ดินของ", "บริษัทของ", "โรงงานของ",
            "พ่อของ", "แม่ของ", "ลูกของ", "ญาติของ", "เพื่อนของ",
        )

        def ownership_only(start, key):
            if key.casefold() not in character_names:
                return False
            before = lowered[max(0, start - 28):start].rstrip(" _-–—,:;()[]{}\n\t")
            return any(before.endswith(prefix.casefold()) for prefix in ownership_prefixes)

        candidates = []
        for order, (stem, path) in enumerate(_list_ref_files()):
            key = str(stem).strip()
            if len(key) < 2:
                continue
            needle = key.lower()
            search_from = 0
            while True:
                index = lowered.find(needle, search_from)
                if index < 0:
                    break
                end = index + len(needle)
                if not ownership_only(index, key):
                    candidates.append((-(len(needle)), index, end, order, key, path))
                search_from = index + 1

        # Longest name wins only where spans overlap. The same short character
        # name may still match at a separate position later in the Prompt.
        candidates.sort(key=lambda item: (item[0], item[1], item[3]))
        occupied = []
        selected = []
        selected_paths = set()
        for _neg_len, start, end, _order, key, path in candidates:
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            try:
                path_key = os.path.normcase(os.path.abspath(str(path)))
            except Exception:
                path_key = str(path).casefold()
            if path_key in selected_paths:
                continue
            occupied.append((start, end))
            selected_paths.add(path_key)
            selected.append((start, key, path))

        selected.sort(key=lambda item: item[0])
        return [(key, path) for _start, key, path in selected]

    def _matching_ref_files():
        return _matching_ref_files_for_text(prompt_text.get("1.0", tk.END))

    def _update_ref_highlight(log=False):
        try:
            for tag in prompt_text.tag_names():
                if str(tag).startswith("ref_word_hl_"):
                    prompt_text.tag_delete(tag)
        except Exception:
            pass

        matched = _matching_ref_files()
        total_hits = 0
        for i, (name, _path) in enumerate(sorted(matched, key=lambda x: len(x[0]), reverse=True)):
            color = _REF_HL_COLORS[i % len(_REF_HL_COLORS)]
            tag = "ref_word_hl_" + re.sub(r"\W+", "_", name, flags=re.UNICODE)
            try:
                prompt_text.tag_config(tag, foreground=color, background="#F3E8FF")
                start = "1.0"
                while True:
                    pos = prompt_text.search(name, start, stopindex=tk.END, nocase=True)
                    if not pos:
                        break
                    end = pos + f"+{len(name)}c"
                    prompt_text.tag_add(tag, pos, end)
                    total_hits += 1
                    start = end
            except Exception:
                pass

        count = len(matched)
        if ref_folder[0] and os.path.isdir(str(ref_folder[0])):
            folder_name = os.path.basename(os.path.normpath(str(ref_folder[0]))) or str(ref_folder[0])
            ref_match_var.set(f"{folder_name} · ใช้ {count} รูป")
        else:
            ref_match_var.set("โฟลเดอร์: ไม่มี")
        if count:
            names = ", ".join(name for name, _ in matched[:8])
            if log:
                _log(f"[ref] ใช้ไฟล์แนบ {count} รูป: {names}")
        else:
            if log:
                _log("[ref] ยังไม่เจอชื่อไฟล์แนบ")
        return count, total_hits

    def _schedule_ref_highlight(_event=None):
        try:
            old = _ref_preview_after[0]
            if old:
                root.after_cancel(old)
        except Exception:
            pass
        _ref_preview_after[0] = root.after(180, _update_ref_highlight)

    g["highlight_matched_words"] = lambda: _update_ref_highlight(log=True)
    g["auto_update_ref_preview"] = _schedule_ref_highlight
    root.after(0, _update_ref_highlight)
    try:
        prompt_text.bind("<KeyRelease>", _schedule_ref_highlight, add="+")
        prompt_text.bind("<<Modified>>", lambda _e: (prompt_text.edit_modified(False), _schedule_ref_highlight()), add="+")
    except Exception:
        pass

    def _save_gallery_state():
        gallery_state_path.parent.mkdir(parents=True, exist_ok=True)
        gallery_state_path.write_text(
            json.dumps({"paths": gallery_paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _gallery_add(path, persist=True, prepend=True):
        import os
        import subprocess
        path = str(Path(path))
        if path in gallery_rendered_paths:
            return
        gallery_rendered_paths.add(path)
        if persist and path not in gallery_paths:
            gallery_paths.insert(0, path)
            _save_gallery_state()
        row = tk.Frame(inner, bg=PANEL, bd=1, relief="groove", padx=4, pady=4)
        if prepend:
            gallery_cards.insert(0, row)
            first_row[0] = row
        else:
            gallery_cards.append(row)
            if first_row[0] is None:
                first_row[0] = row
        gallery_layout_dirty[0] = True

        photo = None
        try:
            from PIL import Image, ImageTk
            pil = Image.open(path)
            pil.thumbnail((160, 100))
            photo = ImageTk.PhotoImage(pil)
            thumbs.append(photo)
        except Exception:
            pass

        # Keep cards short enough that filename and every action button remain
        # visible inside the gallery at common 768/900 px window heights.
        thumb = tk.Frame(row, bg=PANEL, height=108)
        thumb.pack(fill="x", padx=4, pady=(3, 1))
        thumb.pack_propagate(False)
        if photo:
            tk.Label(thumb, image=photo, bg=PANEL).pack(expand=True)
        else:
            tk.Label(thumb, text="ไม่มี preview", bg=PANEL, fg="#9CA3AF").pack(expand=True)

        tk.Label(row, text=os.path.basename(str(path)), bg=PANEL, fg="#111",
                 anchor="w", justify="left", wraplength=280, height=1).pack(fill="x", padx=6, pady=(1, 2))

        # Per-image edit row, matching the useful Gallery workflow on Ref/Prop.
        # The original image is sent as the edit reference and the result is
        # inserted back at the top of this same Image AI Gallery.
        edit_row = tk.Frame(row, bg=PANEL)
        edit_row.pack(fill="x", padx=4, pady=(1, 4))
        tk.Label(
            edit_row, text="แก้รูป:", bg=PANEL, fg="#6B7280",
            font=(SNAPGEN_UI_FONT, 8, "bold"),
        ).pack(side="left", padx=(2, 4))
        edit_var = tk.StringVar()
        edit_entry = tk.Entry(
            edit_row,
            textvariable=edit_var,
            relief="solid",
            bd=1,
            bg="#FFFFFF",
            fg="#111827",
            font=(SNAPGEN_UI_FONT, 9),
        )
        edit_entry.pack(side="left", fill="x", expand=True)
        _bind_character_insert_target(edit_entry)

        def _edit_gallery_image(p=path, instruction_override=None):
            if instruction_override is not None:
                edit_var.set(instruction_override)
            instruction = edit_var.get().strip()
            if not instruction:
                _log("[แก้รูป] พิมพ์สิ่งที่ต้องการแก้ก่อน")
                edit_entry.focus_set()
                return
            if not Path(p).is_file():
                _log(f"[แก้รูป] ไม่พบรูป: {p}")
                return
            if story_context_state["sending"]:
                _log("[แก้รูป] รอให้ระบบส่งบริบทเสร็จก่อน")
                return
            if busy[0]:
                _log("[แก้รูป] กำลังสร้างหรือแก้รูปอยู่ — รอให้งานเดิมเสร็จก่อน")
                return

            encoder = g.get("_encode_image_b64")
            do_req = g.get("_do_image_request")
            if not callable(encoder) or not callable(do_req):
                _log("[แก้รูป] ไม่พบระบบส่งรูปเข้า Bridge")
                return

            # Preserve the original Image Prompt number, so the edited result
            # still maps to the correct Video Slot when sent later.
            source_prompt_index = None
            matcher = g.get("video_prompt_for_image_path")
            if callable(matcher):
                try:
                    linked_number, _video_prompt, _reason = matcher(p, fallback_slot=None)
                    if linked_number is not None:
                        source_prompt_index = int(linked_number)
                except Exception:
                    source_prompt_index = None

            # The first attachment is always the generated image being edited.
            # Any reference filename mentioned in the edit instruction is appended
            # after it, so names such as แบงค์ / ลุงกร / ไร่กระเทียม resolve to
            # their saved reference images without replacing the edit target.
            matched_edit_refs = []
            seen_edit_paths = {os.path.normcase(os.path.abspath(str(p)))}
            for ref_name, ref_path in _matching_ref_files_for_text(instruction):
                try:
                    ref_key = os.path.normcase(os.path.abspath(str(ref_path)))
                except Exception:
                    ref_key = str(ref_path).casefold()
                if ref_key in seen_edit_paths or not Path(ref_path).is_file():
                    continue
                seen_edit_paths.add(ref_key)
                matched_edit_refs.append((ref_name, str(ref_path)))
                if len(matched_edit_refs) >= 9:  # source image + up to 9 named refs
                    break

            busy[0] = True
            gen_btn.config(state="disabled")
            edit_entry.config(state="disabled")
            edit_btn.config(state="disabled", text="กำลังแก้...")
            _log(f"[แก้รูป] กำลังแก้ {Path(p).name}: {instruction}")
            if matched_edit_refs:
                _log("[แก้รูป] แนบเรฟตามชื่อ: " + ", ".join(name for name, _path in matched_edit_refs))

            def worker():
                try:
                    reference_rule = ""
                    if matched_edit_refs:
                        reference_rule = (
                            " รูปแนบลำดับแรกคือภาพหลักที่ต้องแก้ไข ห้ามแทนที่ด้วยรูปอื่น "
                            "รูปแนบลำดับถัดไปเป็นภาพอ้างอิงตามชื่อ ใช้เฉพาะตัวตน ใบหน้า เสื้อผ้า "
                            "สถานที่ หรือวัตถุที่ตรงกับชื่อในคำสั่ง และห้ามเปลี่ยนองค์ประกอบอื่นโดยไม่จำเป็น"
                        )
                    edit_prompt = (
                        "แก้ไขภาพที่แนบมาโดยคงตัวละครเดิม ใบหน้าเดิม เสื้อผ้าเดิม "
                        "สไตล์ภาพเดิม สถานที่เดิม องค์ประกอบเดิม มุมกล้องเดิม และแสงเดิมไว้ "
                        "เว้นแต่คำสั่งต่อไปนี้ระบุให้เปลี่ยนโดยตรง ห้ามสร้างฉากหรือคนใหม่ที่ไม่เกี่ยวข้อง "
                        + reference_rule + " เปลี่ยนเฉพาะสิ่งนี้: " + instruction
                    )
                    edit_images = [encoder(p)]
                    edit_images.extend(encoder(ref_path) for _name, ref_path in matched_edit_refs)
                    payload = {
                        "model": "auto",
                        "prompt": edit_prompt,
                        "aspect_ratio": img_aspect_var.get(),
                        "images": edit_images,
                        # Continue the exact story chat established by
                        # Prompt-Ref's "เริ่มเรื่องใหม่"; never create a separate edit chat.
                        "_use_story_history": True,
                    }
                    out = do_req(
                        payload,
                        is_edit=True,
                        prompt=edit_prompt,
                        name_hint=f"{Path(p).stem}_แก้ไข",
                        raw_prompt=instruction,
                        prompt_index=source_prompt_index,
                        output_dir=str(export_image_dir) if export_image_dir else None,
                    )

                    def complete(result=out):
                        _gallery_add(result)
                        edit_var.set("")
                        _log(f"[แก้รูป] ✓ {Path(str(result)).name}")
                        _notify_done()

                    root.after(0, complete)
                except Exception as exc:
                    root.after(0, lambda message=str(exc): _log("[แก้รูป] ❌ " + message))
                finally:
                    def release():
                        busy[0] = False
                        gen_btn.config(state="normal")
                        edit_entry.config(state="normal")
                        edit_btn.config(state="normal", text="แก้ไข")
                    root.after(0, release)

            import threading
            threading.Thread(target=worker, daemon=True).start()

        edit_btn = tk.Button(
            edit_row,
            text="แก้ไข",
            command=_edit_gallery_image,
            bg="#059669",
            fg="white",
            activebackground="#10B981",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            font=(SNAPGEN_UI_FONT, 8, "bold"),
            cursor="hand2",
        )
        edit_btn.pack(side="left", padx=(4, 0))
        mobile_editors[str(Path(path).resolve())] = _edit_gallery_image
        edit_entry.bind("<Return>", lambda _event: _edit_gallery_image(), add="+")

        btns = tk.Frame(row, bg=PANEL)
        btns.pack(fill="x", padx=4, pady=(0, 3))
        def open_image(p=path):
            target = os.path.normpath(str(p))
            try:
                os.startfile(target)  # open image with default viewer
            except Exception as exc:
                _log(f"เปิดรูปไม่สำเร็จ: {exc}")

        tk.Button(btns, text="📂 เปิด", command=open_image).pack(side="left", fill="x", expand=True)
        slotrow = tk.Frame(btns, bg=PANEL)
        slotrow.pack(side="right", padx=(4, 0))

        def send(slot, p=path):
            fn = g.get("load_slot_image")
            if not callable(fn):
                _log("ไม่พบปุ่มส่งเข้า Slot จากหน้า Video")
                return
            try:
                fn(slot, p, skip_sidecar=True)
            except TypeError:
                fn(slot, p)

            # Fill the matching Video Prompt here, in the actual button
            # callback.  Do not depend on load_slot_image being wrapped: the
            # recovered Video page and adapters can replace that callable at
            # different points during startup.
            matched_prompt = False
            matcher = g.get("video_prompt_for_image_path")
            if callable(matcher):
                try:
                    prompt_no, video_prompt, reason = matcher(p, fallback_slot=None)
                    boxes = g.get("slot_prompts") or []
                    if video_prompt and 0 <= int(slot) < len(boxes):
                        box = boxes[int(slot)]
                        box.delete("1.0", tk.END)
                        box.insert("1.0", video_prompt)
                        matched_prompt = True
                        _log(f"[slot] รูปนี้ตรงกับ Prompt {prompt_no} — ใส่ Video Prompt {prompt_no} แล้ว ({reason})")
                except Exception as exc:
                    _log(f"[slot] จับคู่ Prompt ไม่สำเร็จ: {exc}")
            if not matched_prompt:
                _log(f"[slot] ส่งรูปไป Slot {slot + 1} แล้ว แต่ไม่พบ Prompt ต้นทางของรูป")
            switch = g.get("switch_mode")
            if callable(switch):
                try:
                    root.after(80, lambda: switch("video"))
                except Exception:
                    try:
                        switch("video")
                    except Exception:
                        pass

        for i in range(2):
            tk.Button(slotrow, text=f"Slot {i + 1}", width=6, bg="#C8E6C9",
                      command=lambda i=i: send(i)).pack(side="left", padx=1)
        # Keep mouse-wheel scrolling active over every gallery row widget.
        bind_wheel = g.get("_bind_gallery_wheel")
        if callable(bind_wheel):
            for widget in (row, thumb, edit_row, edit_entry, edit_btn, btns, slotrow):
                bind_wheel(widget)
            for child in (
                list(row.winfo_children())
                + list(edit_row.winfo_children())
                + list(btns.winfo_children())
                + list(slotrow.winfo_children())
            ):
                bind_wheel(child)
        _sync_gallery_scrollregion()
        if prepend:
            history.insert(0, path)
        else:
            history.append(path)
        _schedule_ref_highlight()

    def _prompt_entries():
        loader = g.get("load_prompt_bank_entries_by_mode")
        if callable(loader):
            return [(k, p) for k, p in loader("image") if str(p).strip()]
        return []

    def _is_storyboard_prompt(key, prompt):
        text = f"{key or ''}\n{prompt or ''}"
        return bool(re.search(r"(?i)storyboard|รวม\s*ซีน|ภาพรวม|single\s+image\s+storyboard|panel|grid", text))

    def _scene_prompts_only(entries):
        scenes = []
        for key, prompt in entries:
            prompt = str(prompt).strip()
            if prompt and not _is_storyboard_prompt(key, prompt):
                scenes.append(prompt)
        return scenes

    def _storyboard_prompt_from_entries(entries):
        for key, prompt in reversed(entries):
            prompt = str(prompt).strip()
            if prompt and _is_storyboard_prompt(key, prompt):
                return prompt
        prompts = [str(p).strip() for _k, p in entries if str(p).strip()]
        return prompts[-1] if prompts else ""

    def _pick_prompt():
        entries = _prompt_entries()
        win = tk.Toplevel(root)
        win.title("เลือก Prompt - สร้างรูป")
        win.geometry("820x620")
        win.minsize(760, 500)
        win.configure(bg="#FFFFFF")
        accent = PURPLE
        accent_soft = "#F3E8FF"
        neutral = "#E5E7EB"
        neutral_text = "#111827"
        try:
            win.transient(root)
        except Exception:
            pass

        wrap = tk.Frame(win, bg="#FFFFFF")
        wrap.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(wrap, text=f"พบ {len(entries)} prompts — เลื่อนดูลงมาได้ทีละกล่อง",
                 bg="#FFFFFF", fg="#555", font=(SNAPGEN_UI_FONT, 10)).pack(anchor="w", pady=(0, 6))

        canvas = tk.Canvas(wrap, bg="#FFFFFF", highlightthickness=0)
        scroll = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#FFFFFF")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        selected = {"idx": 0}
        selected_label = tk.StringVar(value=(entries[0][0] if entries else "ไม่มี prompt"))
        cards = []

        def choose(idx):
            if not entries:
                return
            selected["idx"] = idx
            selected_label.set(entries[idx][0])
            for j, card in enumerate(cards):
                active = j == idx
                bg = accent_soft if active else "#FFFFFF"
                card.config(bg=bg, relief=("ridge" if active else "groove"))
                for child in card.winfo_children():
                    try:
                        child.config(bg=bg)
                    except Exception:
                        pass

        for i, (key, prompt_body) in enumerate(entries):
            card = tk.Frame(inner, bd=1, relief="groove", bg="#FFFFFF", padx=8, pady=6)
            card.pack(fill="x", padx=2, pady=4)
            cards.append(card)
            tk.Label(card, text=f"#{i + 1}  {key}", anchor="w", bg="#FFFFFF",
                     fg="#111", font=(SNAPGEN_UI_FONT, 10, "bold")).pack(fill="x")
            msg = tk.Message(card, text=prompt_body, width=720, bg="#FFFFFF",
                             fg="#111", font=(SNAPGEN_UI_FONT, 10))
            msg.pack(fill="x", pady=(3, 0))
            for widget in (card, msg):
                widget.bind("<Button-1>", lambda _e, idx=i: choose(idx))
                widget.bind("<Double-Button-1>", lambda _e, idx=i: (choose(idx), use()))

        if not entries:
            tk.Label(inner, text="ไม่มี prompt ใน prompt_bank.txt", bg="#FFFFFF",
                     fg="#666", font=(SNAPGEN_UI_FONT, 11)).pack(anchor="w", padx=8, pady=16)

        def on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def use():
            if not entries:
                (g.get("show_error") or (lambda _t, _m: _log(_m)))("Prompt", "ไม่มี prompt ใน prompt_bank.txt")
                return
            _key, chosen_prompt = entries[selected["idx"]]
            prompt_text.delete("1.0", tk.END)
            prompt_text.insert("1.0", chosen_prompt)
            _log(set_selection_lock(lock_g, "prompt", _key))
            fn = g.get("auto_update_ref_preview")
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
            close()

        def close():
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            win.destroy()

        canvas.bind_all("<MouseWheel>", on_wheel)

        bottom = tk.Frame(win, bg="#FFFFFF")
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(bottom, textvariable=selected_label, anchor="w", bg="#FFFFFF",
                 fg="#111", font=(SNAPGEN_UI_FONT, 10)).pack(side="left", fill="x", expand=True)
        tk.Button(bottom, text="Use", command=use, bg=accent, fg="white",
                  activebackground=accent, activeforeground="white",
                  relief="flat", bd=0, padx=14, pady=6,
                  font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="right", padx=(6, 0))
        tk.Button(bottom, text="Close", command=close, bg=neutral, fg=neutral_text,
                  activebackground=neutral, activeforeground=neutral_text,
                  relief="flat", bd=0, padx=14, pady=6,
                  font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", close)
        if entries:
            choose(0)
        try:
            win.update_idletasks()
            x = root.winfo_rootx() + max(0, (root.winfo_width() - win.winfo_width()) // 2)
            y = root.winfo_rooty() + max(0, (root.winfo_height() - win.winfo_height()) // 2)
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _generate(is_edit=False, prompt_override=None, name_hint=None, prompt_index=None):
        prompt = (prompt_override or prompt_text.get("1.0", tk.END)).strip()
        if not prompt:
            (g.get("show_error") or (lambda _t, _m: _log(_m)))("สร้างรูป", "ใส่ prompt ก่อน")
            return
        if story_context_state["sending"]:
            _log("กำลังส่งบริบทเหตุการณ์เข้า GPT — รอให้เสร็จก่อนสร้างรูป")
            return
        if manual_refs and not story_context_state["ready"]:
            _log("❌ รูปเหตุการณ์ก่อนหน้ายังไม่ได้ส่งเข้า GPT — กดปุ่มต่อจากฉากก่อนแล้วรอข้อความว่ารับรู้แล้ว")
            return
        # The Prompt picker previously copied only the text into the editor.
        # A normal click on "สร้างรูป" therefore lost its Image Slot number,
        # so Video could not know which matching prompt to load.  Resolve the
        # exact source here for every generation path, including pasted text
        # that exactly matches an entry in the image prompt bank.
        if prompt_index is None:
            normalized = re.sub(r"\s+", " ", prompt).strip()
            for entry_pos, (entry_key, entry_prompt) in enumerate(_prompt_entries(), 1):
                if re.sub(r"\s+", " ", str(entry_prompt)).strip() != normalized:
                    continue
                match = re.search(r"(\d{1,3})", str(entry_key or ""))
                prompt_index = int(match.group(1)) if match else entry_pos
                break
        if busy[0]:
            _log("กำลังสร้างรูปอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        matched_refs = [
            item for item in _matching_ref_files()
            if (g.get("_story_ref_age_allowed", lambda _path, _prompt: True)(item[1], prompt))
        ]
        run = {}
        try:
            run_getter = g.get("_story_run_for_prompt")
            run = run_getter(prompt_index) if callable(run_getter) else {}
        except Exception:
            run = {}
        storyboard_derived = bool(run and prompt_index not in (None, 11) and
                                  (g.get("_story_is_storyboard_derived") or
                                   (lambda p, i=None: bool(re.search(r"storyboard\\s*(?:ช่อง|shot|panel)|อ้างอิงองค์ประกอบ", p, re.I))))(prompt, prompt_index))
        panel_path = None
        if storyboard_derived:
            try:
                panel_path = g.get("_storyboard_panel_crop")(run["storyboard_path"], data_dir, run["run_id"], int(prompt_index), run.get("panel_count"))
            except Exception as exc:
                _log("❌ Storyboard ช่องที่ " + str(prompt_index) + " ใช้เป็นเรฟไม่ได้: " + str(exc))
                return
        prompt_lock_name = str(name_hint or (f"Prompt {prompt_index}" if prompt_index else prompt[:45])).strip()
        set_selection_lock(lock_g, "prompt", prompt_lock_name)
        for ref_name, _ref_path in matched_refs:
            set_selection_lock(lock_g, "reference", ref_name, append=True)
        busy[0] = True
        gen_btn.config(state="disabled")
        _log(f"กำลังสร้างรูป... ใช้ไฟล์แนบ {len(matched_refs)} รูป" if matched_refs else "กำลังสร้างรูป... ไม่เจอชื่อไฟล์แนบใน prompt")
        def worker():
            try:
                do_req = g.get("_do_image_request")
                if not do_req: raise RuntimeError("_do_image_request missing")
                ref_items = []
                seen_refs = set()
                if panel_path:
                    _append_ref(
                        ref_items, seen_refs, panel_path,
                        "the exact cropped frame from this Storyboard shot; use it for composition, pose, placement, and camera only",
                        f"storyboard panel {prompt_index:02d}",
                    )
                # Previous-event/storyboard images were already uploaded as a
                # separate Vision turn in this same conversation.  Do not send
                # them again here where they could conflict with character refs.
                for ref_name, matched_path in matched_refs:
                    _append_ref(
                        ref_items, seen_refs, matched_path,
                        "matched character/place reference from the library. Use it only for identity or named place/object details that appear in the prompt; do not copy unrelated old background, pose, lighting, or clothes.",
                        ref_name,
                    )
                # Camera buttons apply only to normal manual image generation.
                # Storyboard and Auto-Gen retain the camera language written in
                # each scene prompt so a whole movie is not forced to one shot.
                camera_key = camera_mode["key"] if prompt_override is None else "auto"
                request_prompt = _apply_camera_override(prompt, camera_key)
                presets = g.get("LIGHTING_PRESETS") or {}
                selected_lighting = str(img_lighting_var.get() or "").strip()
                # Explicit time cues in the current scene always win. This prevents
                # แสงกลางคืน from being paired with the daytime preset / "not night".
                inferred_kind = _infer_lighting_from_prompt(prompt)
                inferred_key = _find_lighting_key(inferred_kind) if inferred_kind else None
                if inferred_key:
                    selected_lighting = inferred_key
                    try:
                        lighting_auto_state["programmatic"] = True
                        img_lighting_var.set(inferred_key)
                    finally:
                        lighting_auto_state["programmatic"] = False
                lighting_instruction = str(presets.get(selected_lighting, "") or "").strip()
                if lighting_instruction:
                    request_prompt = request_prompt.rstrip() + (
                        "\n\nLIGHTING: "
                        + lighting_instruction
                    )
                if storyboard_derived:
                    request_prompt += g.get("_story_authority_block", lambda *_args: "")(
                        g.get("_story_current_scene_text", lambda _run_id=None: "")(run.get("run_id")),
                        int(prompt_index),
                        prompt,
                    )
                request_prompt += _reference_guidance(ref_items)
                payload = {
                    "prompt": request_prompt,
                    "aspect_ratio": img_aspect_var.get(),
                    # Every Image AI action continues this page's story chat.
                    # Only Prompt-Ref's "เริ่มเรื่องใหม่" button may clear that chat.
                    "_use_story_history": True,
                    "_story_run_id": run.get("run_id"),
                    "_storyboard_path": run.get("storyboard_path"),
                    "_reference_labels": [item.get("label") for item in ref_items],
                }
                if ref_items:
                    enc = g.get("_encode_image_b64")
                    if enc: payload["images"] = [enc(item["path"]) for item in ref_items[:10]]
                out = do_req(payload, is_edit=bool(payload.get("images")), prompt=request_prompt, name_hint=name_hint or prompt, raw_prompt=prompt, prompt_index=prompt_index, output_dir=str(export_image_dir) if export_image_dir else None)
                root.after(0, lambda p=out: (_gallery_add(p), _log("สร้างรูปเสร็จ"), _notify_done()))
            except Exception as e:
                root.after(0, lambda m=str(e): _log("❌ " + m))
            finally:
                def release():
                    busy[0] = False
                    gen_btn.config(state="normal")
                root.after(0, release)
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _storyboard():
        entries = _prompt_entries(); prompts = [p for _k, p in entries]
        sb = next((p for p in prompts if "Storyboard" in p or "รวมซีน" in p), prompts[-1] if prompts else prompt_text.get("1.0", tk.END).strip())
        prompt_text.delete("1.0", tk.END); prompt_text.insert("1.0", sb)
        _generate(False, sb, "storyboard", 11)

    def _matched_refs_for_prompt(prompt):
        return _matching_ref_files_for_text(prompt)

    def _reference_label(path):
        try:
            return os.path.splitext(os.path.basename(str(path)))[0]
        except Exception:
            return "reference"

    def _append_ref(ref_items, seen, path, role, label=None):
        if not path:
            return
        try:
            norm = os.path.normcase(os.path.abspath(str(path)))
        except Exception:
            norm = str(path)
        if norm in seen:
            return
        if not os.path.exists(str(path)):
            return
        seen.add(norm)
        ref_items.append({
            "path": str(path),
            "role": role,
            "label": label or _reference_label(path),
        })

    def _previous_event_context_role():
        return (
            "USER-PROVIDED PREVIOUS-EVENT CONTEXT. This may be either one image of the "
            "immediately previous scene or a rough multi-panel storyboard covering the "
            "preceding part of this event. First infer the narrative sequence: who is present, "
            "where they are, what happened, actions, relationships, mood, wardrobe, props, "
            "lighting, positions, and the latest unresolved story state. If it is a storyboard, "
            "read its panels in natural story order and treat the final relevant panel as the "
            "latest state. Use it as background knowledge for the next moment only. Do not "
            "recreate the storyboard grid, do not combine all panels into one image, and do not "
            "copy an old composition when the current written prompt asks for the next action."
        )

    def _reference_guidance(ref_items):
        if not ref_items:
            return ""
        lines = [
            "",
            "",
            "ATTACHED REFERENCES:",
            "Use each file only for the named identity or place; follow the current scene prompt for action, lighting, and composition.",
        ]
        for i, item in enumerate(ref_items[:10], 1):
            label = item.get("label") or f"reference {i}"
            role = item.get("role") or "visual reference"
            lines.append(f"Image {i}: {role} — file/name hint: {label}")
        return "\n".join(lines)

    def _build_image_payload(base_prompt, ref_paths=None, scene_index=None, scene_total=None):
        aspect = img_aspect_var.get()
        lighting = ""
        presets = g.get("LIGHTING_PRESETS") or {}
        try:
            lighting = presets.get(img_lighting_var.get(), "")
        except Exception:
            lighting = ""
        lock = ""
        quality = "Photorealistic, ultra detailed, sharp focus, high resolution, crisp edges, professional photography quality."
        prefix = ""
        if scene_index is not None and scene_total is not None:
            prefix = (
                f"นี่คือเหตุการณ์ที่ {scene_index} จากทั้งหมด {scene_total}. "
                if scene_index <= 1 else
                f"นี่คือเหตุการณ์ที่ {scene_index} จากทั้งหมด {scene_total} ต่อจากเหตุการณ์ {scene_index - 1}. "
            )
        ref_items = []
        if ref_paths:
            for item in ref_paths:
                if isinstance(item, dict):
                    ref_items.append(item)
                else:
                    ref_items.append({
                        "path": str(item),
                        "role": "supporting reference image",
                        "label": _reference_label(item),
                    })
        full = (
            prefix
            + base_prompt.rstrip(".")
            + f". Use {aspect} aspect ratio composition. {lock} {quality} {lighting}."
            + _reference_guidance(ref_items)
        )
        payload = {
            "prompt": full,
            "aspect_ratio": aspect,
            "history_and_training_disabled": False,
            # Storyboard and Auto-Gen belong to the same Image AI history too.
            "_use_story_history": True,
        }
        if ref_items:
            enc = g.get("_encode_image_b64")
            if enc:
                payload["images"] = [enc(item["path"]) for item in ref_items[:10]]
        return payload, full

    def _auto_gen(mobile_range=None):
        if auto_gen_state["running"]:
            auto_gen_state["cancel"] = True
            _log("[auto] สั่งหยุดคิวแล้ว — รอรูปปัจจุบันเสร็จก่อน")
            return
        if story_context_state["sending"]:
            _log("[auto] กำลังส่งบริบทเหตุการณ์เข้า GPT — รอให้เสร็จก่อน")
            return
        if manual_refs and not story_context_state["ready"]:
            _log("[auto] ❌ รูปเหตุการณ์ก่อนหน้ายังไม่ได้ส่งเข้า GPT")
            return
        entries = _prompt_entries()
        if not entries:
            (g.get("show_error") or (lambda _t, _m: _log(_m)))("Auto-Gen", "ไม่มี prompt ใน prompt_bank.txt")
            return
        prompts = _scene_prompts_only(entries)
        if not prompts:
            _log("ไม่มี prompt ซีนใน prompt_bank.txt")
            return
        total = len(prompts)

        sel_win = tk.Toplevel(root)
        sel_win.title("เลือกช่วง Auto-Gen")
        sel_win.geometry("340x220")
        sel_win.configure(bg="#FFFFFF")
        try:
            sel_win.transient(root)
        except Exception:
            pass
        tk.Label(sel_win, text=f"มี {total} ซีน — ไม่รวม Storyboard", bg="#FFFFFF", fg="#333",
                 font=(SNAPGEN_UI_FONT, 10, "bold")).pack(pady=10)
        rng_frame = tk.Frame(sel_win, bg="#FFFFFF")
        rng_frame.pack(pady=4)
        from_val = tk.IntVar(value=1)
        to_val = tk.IntVar(value=total)
        if mobile_range is not None:
            start, end = map(int, mobile_range)
            if not 1 <= start <= end <= total:
                sel_win.destroy()
                raise ValueError("ช่วง Auto-Gen ไม่ถูกต้อง")
            sel_win.withdraw()
            from_val.set(start)
            to_val.set(end)
        tk.Label(rng_frame, text="จาก", bg="#FFFFFF").pack(side="left", padx=4)
        tk.OptionMenu(rng_frame, from_val, *range(1, total + 1)).pack(side="left")
        tk.Label(rng_frame, text="ถึง", bg="#FFFFFF").pack(side="left", padx=4)
        tk.OptionMenu(rng_frame, to_val, *range(1, total + 1)).pack(side="left")

        def start_queue():
            start_n = from_val.get()
            end_n = to_val.get()
            if start_n > end_n:
                start_n, end_n = end_n, start_n
            sel_win.destroy()
            queue_nums = list(range(start_n, end_n + 1))
            auto_gen_state["running"] = True
            auto_gen_state["cancel"] = False
            gen_btn.config(state="disabled")
            _log(f"[auto] เริ่ม — สร้าง Storyboard ก่อน แล้วซีน {start_n}-{end_n} จาก {total} ซีน (ไม่รวม Storyboard)")

            def finish(done, total_count):
                auto_gen_state["running"] = False
                auto_gen_state["cancel"] = False
                gen_btn.config(state="normal")
                _log(f"[auto] เสร็จทั้งหมด — {done}/{total_count} ซีน")
                _notify_done()

            def worker():
                do_req = g.get("_do_image_request")
                if not callable(do_req):
                    root.after(0, lambda: (_log("❌ _do_image_request missing"), finish(0, len(queue_nums))))
                    return

                storyboard_path = None
                try:
                    sb_prompt = _storyboard_prompt_from_entries(entries)
                    if sb_prompt:
                        root.after(0, lambda: _log("[auto] Storyboard reference กำลังสร้าง..."))
                        sb_refs = []
                        sb_seen = set()
                        for ref_name, path in _matched_refs_for_prompt(sb_prompt):
                            _append_ref(
                                sb_refs, sb_seen, path,
                                "matched character/place reference for building the storyboard overview. Use for identity and named world details only.",
                                ref_name,
                            )
                        # User context was ingested as an earlier Vision turn
                        # in this conversation; never attach it again here.
                        payload, full = _build_image_payload(sb_prompt, sb_refs)
                        lock = g.get("_bridge_queue_lock")
                        if lock:
                            with lock:
                                wait = g.get("_wait_bridge_free")
                                if callable(wait):
                                    wait(log_fn=_log)
                                storyboard_path = do_req(payload, is_edit=bool(sb_refs), prompt=full, name_hint=sb_prompt, raw_prompt=sb_prompt, prompt_index=11, output_dir=str(export_image_dir) if export_image_dir else None)
                        else:
                            storyboard_path = do_req(payload, is_edit=bool(sb_refs), prompt=full, name_hint=sb_prompt, raw_prompt=sb_prompt, prompt_index=11, output_dir=str(export_image_dir) if export_image_dir else None)
                        root.after(0, lambda p=storyboard_path: (_gallery_add(p), _log(f"[auto] Storyboard เสร็จ: {os.path.basename(str(p))}")))
                except Exception as e:
                    root.after(0, lambda m=str(e): _log(f"[auto] Storyboard error: {m} — ดำเนินต่อ"))
                    storyboard_path = None

                done = 0
                prev_path = None
                for n in queue_nums:
                    if auto_gen_state["cancel"]:
                        root.after(0, lambda done=done: _log(f"[auto] หยุดแล้ว — เสร็จ {done}/{len(queue_nums)} ซีน"))
                        break
                    p = prompts[n - 1]
                    root.after(0, lambda n=n, p=p: (
                        prompt_text.delete("1.0", tk.END),
                        prompt_text.insert("1.0", p),
                        _update_ref_highlight(),
                        _log(f"[auto] ซีน {n}/{queue_nums[-1]} — กำลังสร้าง...")
                    ))
                    try:
                        ref_items = []
                        seen_refs = set()
                        # Continuity comes from the current scene text and the
                        # shared story history. Do not attach every old image;
                        # it can introduce an unrelated person or prop.
                        # User context is already in conversation history.
                        for ref_name, path in _matched_refs_for_prompt(p):
                            if not g.get("_story_ref_age_allowed", lambda _path, _prompt: True)(path, p):
                                continue
                            _append_ref(
                                ref_items, seen_refs, path,
                                "matched character/place reference from the library. Use it only for identity or named place/object details that appear in the prompt; do not copy unrelated old background, pose, lighting, or clothes.",
                                ref_name,
                            )
                        run_getter = g.get("_story_run_for_prompt")
                        run = run_getter(n) if callable(run_getter) else {}
                        panel_path = None
                        if run and storyboard_path and n != 11:
                            panel_path = g.get("_storyboard_panel_crop")(
                                storyboard_path, data_dir, run["run_id"], n, run.get("panel_count")
                            )
                        _append_ref(
                            ref_items, seen_refs, panel_path or storyboard_path,
                            "exact cropped frame from this Storyboard shot; use for composition and placement only" if panel_path else
                            "storyboard overview for the whole sequence. Use only as broad plan and scene order; do not override the previous-scene image or current prompt.",
                            f"storyboard panel {n:02d}" if panel_path else "storyboard overview",
                        )
                        payload, full = _build_image_payload(
                            p, ref_items[:10], scene_index=n, scene_total=total
                        )
                        if run:
                            full += g.get("_story_authority_block", lambda *_args: "")(
                                g.get("_story_current_scene_text", lambda _run_id=None: "")(run.get("run_id")), n, p
                            )
                            payload["_story_run_id"] = run.get("run_id")
                            payload["_storyboard_path"] = run.get("storyboard_path")
                            payload["_reference_labels"] = [item.get("label") for item in ref_items]
                            payload["prompt"] = full
                        lock = g.get("_bridge_queue_lock")
                        if lock:
                            with lock:
                                wait = g.get("_wait_bridge_free")
                                if callable(wait):
                                    wait(log_fn=_log)
                                out = do_req(payload, is_edit=bool(ref_items), prompt=full, name_hint=p, raw_prompt=p, prompt_index=n, output_dir=str(export_image_dir) if export_image_dir else None)
                        else:
                            out = do_req(payload, is_edit=bool(ref_items), prompt=full, name_hint=p, raw_prompt=p, prompt_index=n, output_dir=str(export_image_dir) if export_image_dir else None)
                        prev_path = out
                        done += 1
                        root.after(0, lambda out=out, n=n: (_gallery_add(out), _log(f"[auto] ซีน {n} เสร็จ: {os.path.basename(str(out))}")))
                    except Exception as e:
                        done += 1
                        root.after(0, lambda m=str(e), n=n: _log(f"[auto] ❌ ซีน {n} error: {m}"))
                root.after(0, lambda done=done: finish(done, len(queue_nums)))

            import threading
            threading.Thread(target=worker, daemon=True).start()

        if mobile_range is not None:
            start_queue()
            return
        tk.Button(sel_win, text="เริ่ม Auto-Gen", command=start_queue, bg=PINK, fg="white",
                  relief="flat", padx=18, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(pady=8)
        tk.Button(sel_win, text="ยกเลิก", command=sel_win.destroy, bg="#E5E7EB", fg="#111",
                  relief="flat", padx=18, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(pady=4)

    def _attach_refs():
        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title="เลือกภาพเหตุการณ์ก่อนหน้า หรือ Storyboard บริบท",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")],
        )
        if not files:
            return
        for old in manual_refs:
            remove_selection_lock(lock_g, "reference", os.path.splitext(os.path.basename(old))[0])
        manual_refs[:] = list(files)[:10]
        for path in manual_refs:
            set_selection_lock(lock_g, "reference", os.path.splitext(os.path.basename(path))[0], append=True)
        _save_ref_state()
        story_context_state["ready"] = False
        story_context_state["sending"] = True
        if attach_btn is not None:
            attach_btn.config(state="disabled", text="กำลังส่งบริบท...")
        _log(f"[บริบท] เลือก {len(manual_refs)} รูป — กำลังส่งขึ้น GPT ในประวัติเรื่องเดิม")
        _update_ref_highlight(log=True)
        context_paths = list(manual_refs[:10])

        def worker():
            error = None
            try:
                encode = g.get("_encode_image_b64")
                ingest = g.get("ingest_image_story_context")
                if not callable(encode):
                    raise RuntimeError("_encode_image_b64 missing")
                if not callable(ingest):
                    raise RuntimeError("Bridge ยังไม่มีระบบรับรู้บริบท กรุณาปิดเปิดโปรแกรมและ Bridge ใหม่")
                encoded = [encode(path) for path in context_paths]
                ingest(encoded, log_fn=lambda message: root.after(0, lambda m=message: _log(m)))
            except Exception as exc:
                error = str(exc)

            def finish():
                story_context_state["sending"] = False
                story_context_state["ready"] = error is None
                if attach_btn is not None:
                    attach_btn.config(state="normal", text="📎 ต่อจากฉากก่อน")
                if error:
                    _log("❌ [บริบท] " + error)
                else:
                    _log("[บริบท] พร้อมแล้ว — คำขอสร้างรูปถัดไปจะต่อในประวัติเดียวกัน")

            root.after(0, finish)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _clear_refs():
        for old in manual_refs:
            remove_selection_lock(lock_g, "reference", os.path.splitext(os.path.basename(old))[0])
        manual_refs.clear()
        story_context_state["ready"] = False
        story_context_state["sending"] = False
        _save_ref_state(); _log("ล้างรูปบริบทแล้ว — ประวัติ GPT เดิมยังอยู่จนกด เริ่มเรื่องใหม่ ใน Prompt-Ref"); _update_ref_highlight()

    def _clear_gallery():
        """Clear only the on-screen gallery. Real files stay in export/image."""
        for w in list(inner.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        thumbs.clear()
        history.clear()
        gallery_paths.clear()
        mobile_editors.clear()
        gallery_pending_paths.clear()
        gallery_rendered_paths.clear()
        _save_gallery_state()
        gallery_cards.clear()
        gallery_layout_dirty[0] = True
        first_row[0] = None
        try:
            gallery_more_btn.grid_remove()
        except Exception:
            pass
        try:
            gallery.yview_moveto(0)
            sync = g.get("_sync_gallery_scrollregion")
            if callable(sync):
                sync()
            else:
                gallery.configure(scrollregion=gallery.bbox("all") or (0, 0, 0, 0))
        except Exception:
            pass
        _log("ล้าง gallery แล้ว — ไฟล์จริงยังอยู่ใน export/image")

    def _set_ref_folder(d):
        import os
        d = os.path.abspath(str(d or ""))
        if not os.path.isdir(d):
            raise ValueError("ไม่พบโฟลเดอร์อ้างอิงนี้")
        ref_folder[0] = d
        imgs = [f for f in os.listdir(d) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
        ref_match_var.set(f"{os.path.basename(os.path.normpath(d)) or d} · ใช้ 0 รูป")
        ref_names_var.set(", ".join(os.path.splitext(x)[0] for x in imgs))
        for old in folder_ref_names:
            remove_selection_lock(lock_g, "reference", old)
        folder_ref_names[:] = [os.path.splitext(x)[0] for x in imgs]
        for name in folder_ref_names:
            set_selection_lock(lock_g, "reference", name, append=True)
        _save_ref_state()
        _refresh_image_character_options()
        _log(f"[ref] ล็อกโฟลเดอร์ {os.path.basename(d)} — {len(imgs)} รูป")
        _update_ref_highlight(log=True)
        return len(imgs)

    def _browse_ref_folder():
        from tkinter import filedialog
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์อ้างอิง")
        if not d: return
        _set_ref_folder(d)

    def _clear_ref_folder():
        for old in folder_ref_names:
            remove_selection_lock(lock_g, "reference", old)
        folder_ref_names.clear()
        ref_folder[0] = None; _save_ref_state(); _refresh_image_character_options(); ref_match_var.set("โฟลเดอร์: ไม่มี"); ref_names_var.set(""); _log("ล้างโฟลเดอร์อ้างอิงแล้ว"); _update_ref_highlight()

    gen_btn.config(command=lambda: _generate(False))
    prompt_btn.config(command=_pick_prompt)
    clear_gallery_btn.config(command=_clear_gallery)
    choose_btn.config(command=_browse_ref_folder)
    ref_clear_btn.config(command=_clear_ref_folder)
    g["clear_gallery"] = _clear_gallery
    g["set_image_ref_folder"] = _set_ref_folder
    g["get_image_ref_folder"] = lambda: str(ref_folder[0] or "")

    def _update_gallery_more_button():
        remaining = len(gallery_pending_paths)
        try:
            if remaining:
                gallery_more_btn.config(text=f"โหลดรูปเก่าเพิ่ม ({remaining})")
                gallery_more_btn.grid()
            else:
                gallery_more_btn.grid_remove()
        except Exception:
            pass

    def _load_more_gallery():
        batch = gallery_pending_paths[:GALLERY_LOAD_BATCH]
        del gallery_pending_paths[:len(batch)]
        for path in batch:
            _gallery_add(path, persist=False, prepend=False)
        _update_gallery_more_button()
        if batch:
            _log(f"[gallery] โหลดรูปเก่าเพิ่ม {len(batch)} รูป")

    gallery_more_btn = tk.Button(
        gallery_frame, text="โหลดรูปเก่าเพิ่ม", command=_load_more_gallery,
        bg="#F3F4F6", fg="#374151", activebackground="#E5E7EB",
        relief="flat", bd=0, padx=12, pady=5, font=(SNAPGEN_UI_FONT, 9, "bold"),
    )
    gallery_more_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    gallery_more_btn.grid_remove()

    def _restore_gallery_state():
        try:
            payload = json.loads(gallery_state_path.read_text(encoding="utf-8"))
            saved = payload.get("paths") if isinstance(payload, dict) else []
        except FileNotFoundError:
            # One-time migration for installations that predate Gallery state.
            saved = [
                str(path) for path in sorted(
                    Path(export_image_dir).glob("*") if export_image_dir else [],
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
        except (json.JSONDecodeError, OSError):
            saved = []
        gallery_paths[:] = [str(Path(path)) for path in saved if Path(path).is_file()]
        gallery_rendered_paths.clear()
        gallery_pending_paths[:] = gallery_paths[GALLERY_INITIAL_RENDER:]
        _save_gallery_state()
        for path in gallery_paths[:GALLERY_INITIAL_RENDER]:
            _gallery_add(path, persist=False, prepend=False)
        _update_gallery_more_button()
        if gallery_paths:
            _log(f"[gallery] แสดงล่าสุด {min(len(gallery_paths), GALLERY_INITIAL_RENDER)}/{len(gallery_paths)} รูป")

    # Restore only explicitly saved Gallery entries. Never scan export/image:
    # retained files must not return after the user intentionally clears them.
    root.after(50, _restore_gallery_state)

    # Export same global names overlay expects.
    def _mobile_edit(path, instruction):
        key = str(Path(path).resolve())
        if key not in mobile_editors:
            _gallery_add(path)
        mobile_editors[key](instruction_override=instruction)

    # Mobile uses these exact page callbacks, including their validation and history.
    g["mobile_image_actions"] = {
        "generate": _generate, "storyboard": _storyboard, "auto": _auto_gen,
        "clear_gallery": _clear_gallery,
        "camera": _set_camera_mode, "attach": lambda paths: _drop_prompt_images(paths=paths),
        "edit": _mobile_edit, "refs": _list_ref_files, "clear_ref_folder": _clear_ref_folder,
        "gallery": lambda: list(gallery_paths), "auto_state": auto_gen_state,
        "context_state": story_context_state,
    }
    g.update({
        "img_page": page,
        "img_prompt_frame": prompt_frame,
        "img_prompt_text": prompt_text,
        "img_btn_row": bar,
        "img_gen_btn": gen_btn,
        "img_edit_btn": None,
        "img_preview_refs_btn": None,
        "img_status_var": tk.StringVar(value="พร้อมสร้างรูป"),
        "img_ref_row": ref_row,
        "img_ref_label": ref_label,
        "img_ref_folder": ref_folder,
        # Same attachment matching the Image page uses (Slot 2 ออโต้ reuses it).
        "img_match_refs_for_text": _matching_ref_files_for_text,
        "img_list_ref_files": _list_ref_files,
        "img_ref_names_var": ref_names_var,
        "img_ref_match_var": ref_match_var,
        "img_gallery_frame": gallery_frame,
        "img_gallery": gallery,
        "img_gallery_inner": inner,
        "img_gallery_add": _gallery_add,
        "img_gallery_thumbs": thumbs,
        "img_history": history,
        "img_busy": busy,
        "img_gallery_first_row": first_row,
        "img_story_title_var": story_title_var,
        "image_action_buttons": [gen_btn, prompt_btn, clear_gallery_btn],
        "img_side_controls": side_controls,
        "img_character_frame": character_frame,
        "img_character_var": character_var,
        "img_character_picker": character_picker,
        "img_paste_character": _paste_image_character,
        "img_refresh_characters": _refresh_image_character_options,
        "img_camera_frame": camera_frame,
        "img_camera_mode": camera_mode,
        # Voice input and other late-installed controls must write to the Log
        # owned by this source page, not an obsolete pyc log widget.
        "_img_log": _log,
        "img_log_box": log_box,
    })
    return {"page": page, "prompt_text": prompt_text, "gallery_inner": inner, "log_box": log_box}
