# -*- coding: utf-8 -*-
"""SnapGen story face page.

This module owns the widgets, state, and callbacks for this page only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import uuid
import tkinter as tk
from tkinter import ttk
from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()
from pathlib import Path
from snapgen_page_builder import (
    make_log_box as _builder_make_log_box,
    append_log as _builder_append_log,
    set_selection_lock as _builder_set_selection_lock,
)


def _apply_story_face_view(prompt, view):
    if view != "side":
        return str(prompt or "").rstrip()
    return str(prompt or "").rstrip() + (
        "\n\nFINAL CAMERA VIEW OVERRIDE — MANDATORY: create one exact 90-degree side-profile "
        "identity portrait of the same person, facing left. Show one eye, one eyebrow, the complete nose and "
        "lip profile, chin, jaw contour, and one ear. Keep the head level and shoulders natural. No front view, "
        "no three-quarter view, no second angle, no duplicate face, no collage, and no text. Preserve the exact "
        "identity from the attached identity portrait. This camera-view instruction overrides every earlier "
        "front-facing, looking-at-camera, both-eyes-visible, and both-ears-visible instruction."
    )


def _story_body_gender(character):
    """Resolve gender from Thai/English fields and legacy free-text records."""
    appearance = character.get("appearance")
    nested = appearance if isinstance(appearance, dict) else {}

    def classify(value):
        text = str(value or "").strip().casefold()
        female = bool(re.search(r"(?:ผู้หญิง|เด็กหญิง|หญิง|นางสาว|นาง|แม่|ภรรยา)|\b(?:female|woman|girl|f)\b", text))
        male = bool(re.search(r"(?:ผู้ชาย|เด็กชาย|ชาย|นาย|พ่อ|สามี)|\b(?:male|man|boy|m)\b", text))
        if female and not male:
            return "female"
        if male and not female:
            return "male"
        return ""

    explicit_values = (
        character.get("เพศ"), character.get("gender"), character.get("sex"),
        nested.get("gender"), nested.get("sex"),
    )
    for value in explicit_values:
        gender = classify(value)
        if gender:
            return gender
    for value in (appearance if not nested else "", character.get("name"), character.get("role"), character.get("source")):
        gender = classify(value)
        if gender:
            return gender
    return ""


def _story_body_facts(character):
    """Keep only facts that affect body reconstruction."""
    appearance = character.get("appearance")
    nested = appearance if isinstance(appearance, dict) else {}

    def first(*keys):
        for key in keys:
            value = character.get(key)
            if value in (None, ""):
                value = nested.get(key)
            if str(value or "").strip():
                return str(value).strip()
        return ""

    values = (
        ("age", first("อายุ", "age")),
        ("life stage", first("ช่วงชีวิต", "life_stage", "age_group")),
        ("body build", first("รูปร่าง", "body_build", "body_type", "build", "physique", "body")),
        ("height", first("ส่วนสูง", "height")),
        ("weight", first("น้ำหนัก", "weight")),
        ("visible health", first("สุขภาพ", "health", "health_status")),
        ("physical condition", first("สภาพร่างกาย", "physical_condition", "life_condition")),
    )
    facts = [f"{label}: {value}" for label, value in values if value]
    gender = _story_body_gender(character)
    if gender == "male":
        facts.insert(0, "sex/gender: MALE; body-sex lock: unmistakably male chest, torso, shoulders, pelvis, limbs, fat distribution, and overall silhouette; never a female body")
    elif gender == "female":
        facts.insert(0, "sex/gender: FEMALE; body-sex lock: unmistakably female chest, torso, shoulders, pelvis, limbs, fat distribution, and overall silhouette; never a male body")
    return "; ".join(facts)


def _apply_story_body_reference(_face_prompt, body_facts=""):
    """Build a clean full-body photo for Headshot 3 body reconstruction."""
    facts = str(body_facts or "").strip()
    return (
        "Create exactly one photorealistic full-body front-view studio photo intended only for 3D body-shape "
        "reconstruction. "
        + (f"BODY FACTS TO PRESERVE: {facts}. " if facts else "Use natural average human body proportions. ")
        + "The output must contain one single standing person and one view only. The person's head may appear only "
        "at its natural small scale as part of the complete body. Do not add a separate face portrait, enlarged head, "
        "inset, panel, split layout, contact sheet, character sheet, second view, duplicate person, collage, or text. "
        "Use ONLY the body-related facts listed above. IGNORE facial identity, name, hairstyle, facial features, "
        "story costume, uniform, accessories, "
        "jewelry, props, and all identity-reference instructions. The model "
        "may be an anonymous generic person; matching the face is unnecessary. Dress the model only in plain, "
        "matte, close-fitting neutral-gray body-analysis clothing: a fitted short-sleeve top and fitted above-knee "
        "shorts, with no folds that hide the silhouette, no logos, no pattern, no belt, and no accessories. "
        "Show the complete body from top of head to soles of both feet with generous margin. Stand straight in a "
        "neutral anatomical pose, facing the camera exactly front-on, head level, spine straight, arms relaxed "
        "10-15 degrees away from the torso so the waist and arm contours are visible, hands relaxed, legs straight, "
        "feet parallel and about hip-width apart. Do not cross limbs and do not crop fingers, elbows, knees, ankles, "
        "feet, or head. Use realistic human anatomy and preserve natural asymmetry; no stylization, exaggerated "
        "muscles, slimming, beautification, fashion posing, or heroic proportions. Camera centered at mid-torso "
        "height, long-normal lens with minimal perspective distortion, camera level, no wide-angle distortion. "
        "Even shadow-minimized white studio lighting, neutral light-gray background, sharp focus over the entire "
        "body, true-to-life proportions and texture. The body silhouette and proportions are the only priority."
    )


# Story injuries are shown as non-graphic film/stage makeup. Image generation
# refuses literal blood and wounds, but the story still needs the character
# to look hurt: the look survives, the graphic wording does not.
_INJURY_SOFTEN = (
    (r"เลือด(?:ไหล|ออก|สด|ท่วม|โชก|อาบ|กบปาก)?", "คราบสีแดงเข้มแบบแต่งหน้าเอฟเฟกต์ภาพยนตร์ (คล้ายซอสมะเขือเทศที่แห้งแล้ว)"),
    # not แผลง (แผลงฤทธิ์) and not ผ้าพันแผล (a clean bandage is fine as written)
    (r"(?<!พัน)(?:บาด)?แผล(?!ง)(?:เปิด|ฉกรรจ์|ลึก|สด|แตก|ถลอก)?", "รอยแต่งหน้าเอฟเฟกต์ภาพยนตร์โทนแดงจางๆ"),
    (r"(?:รอย)?ฟกช้ำ(?:ดำเขียว)?|รอยช้ำ|ช้ำเลือด|หน้าช้ำ|ตาช้ำ", "รอยแต่งหน้าโทนม่วงอมน้ำเงินจางๆ แบบแต่งหน้าละคร"),
    (r"\b(?:bloody|bloodied|blood-soaked|blood)\b", "dark red film-makeup smudge (dried ketchup-like)"),
    (r"\b(?:open wounds?|wounds?|gashes|gash|lacerations?|cuts)\b", "light reddish stage-makeup marks"),
    (r"\b(?:bruised|bruises|bruise)\b", "soft purple-blue theatrical makeup tint"),
    (r"\b(?:gore|gory)\b", "non-graphic film makeup"),
)


def soften_injury_text(value: str) -> str:
    """Replace graphic injury words with non-graphic film-makeup wording."""
    result = str(value or "")
    for pattern, replacement in _INJURY_SOFTEN:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def install(g: dict, root: tk.Misc) -> tk.Misc:
    """Build this page and return its root frame."""
    globals().update(g)
    lock_g = {"_selection_locks": {}, "_selection_lock_vars": []}
    export_story_face_dir = g.get("EXPORT_STORY_FACE", BASE / "story_face")

    # ── นิทาน owns its story, Context and GPT history ─────────────────────
    # This page never uses the Prompt-Ref / Video / Image AI history or
    # Context. One story (script file, else ข้อมูลชุด) = one GPT history.
    face_store_path = BASE / "story_face_histories.json"
    face_context_path = BASE / "story_face_context.json"
    try:
        face_store = json.loads(face_store_path.read_text(encoding="utf-8"))
        if not isinstance(face_store, dict):
            face_store = {}
    except Exception:
        face_store = {}
    face_store.setdefault("histories", {})
    face_store.setdefault("story", {})

    def _save_face_store():
        try:
            temp = face_store_path.with_suffix(".tmp")
            temp.write_text(json.dumps(face_store, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, face_store_path)
        except Exception as exc:
            print(f"[นิทาน] บันทึกประวัติไม่สำเร็จ: {exc}")

    def _face_story_key():
        story = face_store.get("story") or {}
        if story.get("hash"):
            return "story:" + story["hash"]
        text = re.sub(r"\s+", "", str((globals().get("_face_batch_state") or {}).get("text") or ""))
        if text:
            return "data:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        return "general"

    def _face_conversation():
        """The GPT history of the current นิทาน story (created on first request)."""
        return face_store["histories"].setdefault(_face_story_key(), {})

    def _face_payload_history(payload, fresh=False):
        """Attach นิทาน's story history, or a fresh temporary chat.

        A new identity (no reference face) is drawn in a fresh chat: inside the
        story history the model sees earlier faces and drifts toward them, so
        different people end up looking alike. Variants keep the history and
        attach their master face instead.
        """
        if fresh:
            payload["_temporary_chat"] = True
            payload.pop("_conversation_state", None)
            payload.pop("_conversation_save", None)
            return payload
        payload["_conversation_state"] = _face_conversation()
        payload["_conversation_save"] = _save_face_store
        return payload

    def _load_face_context_text():
        try:
            return face_context_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # Every former Prompt-Ref Context read in this page now reads นิทาน's own Context.
    globals()["_load_ref_context"] = _load_face_context_text
    g["story_face_conversation"] = _face_conversation
    g["story_face_save_history"] = _save_face_store
    g["story_face_context_path"] = face_context_path
    new_page = tk.Frame(root, bg="#FAFAF7")
    g["new_page"] = new_page

    # Story owns its own two subpages. Keep them inside the Story container so
    # the application's top-level navigation remains unchanged.
    story_subnav = tk.Frame(new_page, bg="#FAFAF7")
    story_subnav.pack(fill="x", padx=10, pady=(10, 0))
    story_content = tk.Frame(new_page, bg="#FAFAF7")
    story_content.pack(fill="both", expand=True)
    story_character_page = tk.Frame(story_content, bg="#FAFAF7")
    story_scene_page = tk.Frame(story_content, bg="#FAFAF7")

    story_subpage_buttons = {}
    story_scene_refresh = [lambda: None]

    def _show_story_subpage(name="character"):
        target = "scene" if name == "scene" else "character"
        story_character_page.pack_forget()
        story_scene_page.pack_forget()
        page = story_scene_page if target == "scene" else story_character_page
        page.pack(fill="both", expand=True)
        if target == "scene":
            try:
                story_scene_refresh[0]()
            except Exception:
                pass
        for key, button in story_subpage_buttons.items():
            active = key == target
            button.config(
                bg="#6B7280" if active else "#FAFAF7",
                fg="#FFFFFF" if active else "#1A1A1A",
                activebackground="#4B5563" if active else "#F3F4F6",
                activeforeground="#FFFFFF" if active else "#1A1A1A",
            )

    for key, label in (("character", "👤 ตัวละคร"), ("scene", "🎬 ฉาก")):
        button = tk.Button(
            story_subnav,
            text=label,
            command=lambda page_key=key: _show_story_subpage(page_key),
            bg="#FAFAF7",
            fg="#1A1A1A",
            activebackground="#F3F4F6",
            activeforeground="#1A1A1A",
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            width=12,
            font=(SNAPGEN_UI_FONT, 10, "bold"),
            cursor="hand2",
            highlightthickness=0,
        )
        button.pack(side="left", padx=(0, 4))
        story_subpage_buttons[key] = button

    from snapgen_story_scene import install as _install_story_scene_page
    scene_runtime = _install_story_scene_page(g, root, story_scene_page)
    if callable(scene_runtime.get("refresh")):
        story_scene_refresh[0] = scene_runtime["refresh"]

    g["story_character_page"] = story_character_page
    g["story_scene_page"] = story_scene_page
    g["show_story_subpage"] = _show_story_subpage
    _show_story_subpage("character")

    new_name_var = tk.StringVar(value="")
    g["new_name_var"] = new_name_var
    story_face_running = [False]
    auto_face_running = [False]

    def _notify_done():
        notify = g.get("_snapgen_notify_done")
        if callable(notify):
            try:
                notify()
            except Exception:
                pass
    
    new_box = tk.LabelFrame(story_character_page, text="👤 นิทาน — ใบหน้าตัวละคร", bg="#FAFAF7", fg="#1A1A1A", padx=10, pady=8)
    new_box.pack(fill="x", padx=10, pady=10)
    story_history_row = tk.Frame(new_box, bg="#FAFAF7")
    story_history_row.pack(fill="x", pady=(0, 5))
    story_face_title_var = tk.StringVar(
        value=str((face_store.get("story") or {}).get("title") or "")
    )
    g["story_face_title_var"] = story_face_title_var
    story_face_status_var = tk.StringVar(value="")
    tk.Label(story_history_row, text="เรื่อง:", bg="#FAFAF7", fg="#333", font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left")
    tk.Label(
        story_history_row, textvariable=story_face_title_var, width=34, anchor="w",
        bg="#FFFFFF", fg="#111", relief="solid", bd=1, padx=7, pady=3,
    ).pack(side="left", padx=(6, 8))
    tk.Label(story_history_row, textvariable=story_face_status_var, bg="#FAFAF7", fg="#6B7280").pack(side="left")

    def _choose_face_story():
        import tkinter.filedialog as _fd
        path = _fd.askopenfilename(title="เลือกไฟล์บทของหน้านิทาน",
                                   filetypes=[("บท", "*.docx *.txt *.md"), ("All files", "*.*")])
        if not path:
            return
        import snapgen_page_narrate as _narrate
        try:
            script = _narrate.read_script(path)
        except Exception as exc:
            _new_log(f"[บท] อ่านไฟล์ไม่ได้: {exc}")
            return
        face_store["story"] = {"path": path, "title": Path(path).stem, "hash": _narrate.script_hash(script)}
        _save_face_store()
        story_face_title_var.set(Path(path).stem)
        state_text = "ต่อประวัตินิทานของเรื่องนี้" if _face_conversation().get("conversation_id") else "เรื่องใหม่ — จะเปิดประวัติใหม่"
        story_face_status_var.set(state_text + " (แยกจากหน้าอื่น)")
        _new_log(f"[บท] {Path(path).name} — กำลังเตรียม Context ของหน้านิทาน ...")

        def worker():
            try:
                import snapgen_shared_context as _shared
                shared = _shared.load(path)
                if shared:
                    context = shared["context"]
                    root.after(0, lambda: _new_log("[บท] ใช้ Context ที่ทีมแตกไว้แล้ว ไม่ต้องให้ GPT วิเคราะห์ใหม่"))
                else:
                    context = _face_story_context_via_gpt(script)
                    _shared.save(path, context, script)
                face_context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
                names = [c.get("name") for c in context.get("characters", []) if isinstance(c, dict) and c.get("name")]
                places = [l.get("name") for l in context.get("locations", []) if isinstance(l, dict) and l.get("name")]
                root.after(0, lambda: _new_log(f"[บท] ✓ ตัวละคร {len(names)}: {', '.join(names)} | สถานที่ {len(places)}"))
                root.after(0, story_scene_refresh[0])
            except Exception as exc:
                root.after(0, lambda m=str(exc): _new_log(f"[บท] ❌ {m}"))
        threading.Thread(target=worker, daemon=True).start()

    def _face_story_context_via_gpt(script):
        """Same two-step method as Prompt-Ref, inside นิทาน's own history."""
        import snapgen_page_narrate as _narrate

        def chat(content):
            own = _face_conversation()
            body = {"model": "auto", "chatgpt_image_intercept": False, "temperature": 0.2,
                    "messages": [{"role": "user", "content": content}]}
            if own.get("conversation_id") and own.get("parent_message_id"):
                body["metadata"] = {"conversation_id": own["conversation_id"], "parent_message_id": own["parent_message_id"]}
                if own.get("account_alias"):
                    body["chatgpt_account"] = own["account_alias"]
            base_fn = globals().get("_chatgpt_api_base")
            base = base_fn() if callable(base_fn) else "http://127.0.0.1:8000/v1"
            request = urllib.request.Request(
                base.rstrip("/") + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json; charset=utf-8"},
                method="POST")
            lock = globals().get("_bridge_queue_lock") or threading.Lock()
            with lock:
                with urllib.request.urlopen(request, timeout=600) as response:
                    result = json.loads(response.read().decode("utf-8", errors="replace"))
            if result.get("error"):
                raise RuntimeError(json.dumps(result["error"], ensure_ascii=False)[:400])
            extract = globals().get("_extract_bridge_cursor")
            cid, pid = extract(result) if callable(extract) else (result.get("conversation_id"), result.get("parent_message_id"))
            if cid and pid:
                own.update({"conversation_id": str(cid), "parent_message_id": str(pid)})
                if result.get("chatgpt_account"):
                    own["account_alias"] = str(result["chatgpt_account"])
                _save_face_store()
            message = ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if isinstance(message, list):
                message = "".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in message)
            return str(message)

        root.after(0, lambda: _new_log("[บท] GPT กำลังอ่านบทและหาหลักฐานตัวละคร ..."))
        analysis = _narrate.parse_json_reply(chat(_narrate.analysis_request(script)))
        root.after(0, lambda: _new_log("[บท] GPT กำลังสร้าง Context ตัวละครและสถานที่ ..."))
        context = _narrate.parse_json_reply(chat(_narrate.context_from_analysis_request(analysis)))
        if not isinstance(context.get("characters"), list) or not context["characters"]:
            raise RuntimeError("GPT คืน Context ไม่ครบ")
        try:
            from snapgen_context_tools import normalize_context_master
            context = normalize_context_master(BASE, context)
        except Exception:
            pass
        return context

    tk.Button(story_history_row, text="📄 เลือกบท", command=_choose_face_story, bg="#2563EB", fg="white",
              activebackground="#1D4ED8", activeforeground="white", relief="flat", bd=0, padx=10, pady=3,
              font=(SNAPGEN_UI_FONT, 9, "bold"), cursor="hand2").pack(side="right")
    new_row = tk.Frame(new_box, bg="#FAFAF7")
    new_row.pack(fill="x")
    tk.Label(new_row, text="ชื่อ:", bg="#FAFAF7", fg="#333").pack(side="left")
    new_entry_wrap = tk.Frame(new_row, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D1D5DB")
    new_entry_wrap.pack(side="left", fill="x", expand=True, padx=6)
    new_entry = tk.Entry(new_entry_wrap, textvariable=new_name_var, relief="flat", bg="#FFFFFF", fg="#111")
    new_entry.pack(fill="x", padx=8, pady=6)
    new_placeholder = tk.Label(new_entry_wrap, text="ใส่ชื่อ", bg="#FFFFFF", fg="#B0B0B0", font=(SNAPGEN_UI_FONT, 9))
    new_placeholder.place(x=10, y=6)
    
    # Age dropdown (วัย)
    new_age_var = tk.StringVar(value="อัตโนมัติ")
    g["new_age_var"] = new_age_var
    _FACE_AGES = ("อัตโนมัติ", "เด็ก", "วัยรุ่น", "ผู้ใหญ่", "ผู้สูงอายุ")
    _FACE_AGE_MAP = {"อัตโนมัติ": "auto", "เด็ก": "8", "วัยรุ่น": "17", "ผู้ใหญ่": "35", "ผู้สูงอายุ": "70"}
    g["_FACE_AGE_MAP"] = _FACE_AGE_MAP
    tk.Label(new_row, text="วัย:", bg="#FAFAF7", fg="#333").pack(side="left", padx=(8, 0))
    new_age_menu = tk.OptionMenu(new_row, new_age_var, *_FACE_AGES)
    new_age_menu.config(relief="flat", bg="#FFFFFF", fg="#111", font=(SNAPGEN_UI_FONT, 9), highlightthickness=1, highlightbackground="#D1D5DB")
    new_age_menu.pack(side="left", padx=4)
    g["new_age_menu"] = new_age_menu

    # Identity reference stays automatic and hidden. Select resolves the prior
    # face; users should not have to manage a technical attachment control.
    identity_ref_path = [None]
    identity_ref_paths = {}

    def _refresh_identity_refs():
        identity_ref_paths.clear()
        try:
            files = sorted(
                (
                    path for path in Path(export_story_face_dir).iterdir()
                    if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            files = []
        labels = []
        used = set()
        for path in files:
            label = path.name
            if label in used:
                continue
            used.add(label)
            labels.append(label)
            identity_ref_paths[label] = path
    # Do not scan files while constructing the page. Refresh only after the
    # user selects a character, outside the page-open path.
    
    new_select_btn = tk.Button(new_row, text="Select", command=lambda: None, bg="#2563EB", fg="white", activebackground="#1D4ED8", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    g["story_face_select_btn"] = new_select_btn
    
    def _sync_new_placeholder(*_):
        try:
            if new_name_var.get().strip():
                new_placeholder.place_forget()
            else:
                new_placeholder.place(x=10, y=6)
        except Exception:
            pass
    
    new_name_var.trace_add("write", _sync_new_placeholder)
    new_entry.bind("<FocusIn>", lambda _e: _sync_new_placeholder(), add="+")
    
    story_face_prompt = (
        "Close-up face portrait of a Thai character named {name}, {age} years old. "
        "Distinct non-generic identity with age-accurate face shape, eyes, eyebrows, nose, lips, jaw and cheekbones. "
        "Head-and-shoulders, full front-facing, centered, looking straight at camera. Expression and visible life "
        "condition must follow the current character prompt; use neutral expression only when no condition is given. "
        "Mouth fully closed with relaxed closed lips; absolutely no visible teeth, no open mouth, no smile. "
        "Hair fully pulled and secured behind the head; absolutely no bangs or loose strands covering the forehead, "
        "temples, eyebrows, cheeks, jawline or ears. Full hairline and both ears visible. 85mm portrait lens. "
        "Age-accurate unretouched skin microdetail: visible pores, fine lines, wrinkles, crow's-feet, nasolabial folds, "
        "spots, freckles, moles, scars and uneven texture where appropriate. Older faces show pronounced authentic age "
        "lines. No beauty filter, no airbrushing, no waxy, porcelain, plastic or excessively smooth skin. "
        "Shadowless color-calibrated white studio lighting at neutral D55 white balance: two identical extra-large "
        "softboxes symmetrically left and right with equal height, angle, distance and power, plus centered on-axis fill. "
        "Both sides of the face have identical brightness and color, less than 5 percent luminance difference. Uniform "
        "exposure forehead to neck, flat neutral albedo appearance. Pure neutral light-gray background. No yellow, orange "
        "or warm cast, no sepia, green or blue cast, no directional key, side, rim, back or dramatic light, no dark half "
        "of face, no highlight gradient, no cinematic color grading. Tack-sharp 85mm micro-focus with high local contrast, crisp pores and skin texture, no soft blur, no diffusion filter, no plastic smoothing. Photorealistic, color-accurate skin, 3:4 portrait."
    )
    g["story_face_prompt_template"] = story_face_prompt
    # The full face prompt is intentionally hidden from the page. Keep the
    # current generated prompt in state so Select/Generate can still share it
    # without calling a widget that no longer exists.
    face_prompt_state = [story_face_prompt]

    def _set_face_prompt(value):
        face_prompt_state[0] = str(value or "")

    def _get_face_prompt():
        return str(face_prompt_state[0] or "").strip()

    # Keep the face prompt internal. It is sent automatically; showing the full
    # template wastes vertical space needed by the gallery.

    # Batch input lives in a separate dialog opened from one compact button.
    # Keep only its data/state here so the main Story Face page stays simple.
    batch_save_path = BASE / "story_face_batch_latest.json"
    try:
        saved_batch_payload = json.loads(batch_save_path.read_text(encoding="utf-8"))
        if not isinstance(saved_batch_payload, dict):
            saved_batch_payload = {}
    except Exception:
        saved_batch_payload = {}
    batch_source_state = {"text": str(saved_batch_payload.get("text") or "")}
    globals()["_face_batch_state"] = batch_source_state
    batch_status_var = tk.StringVar(
        value="โหลดข้อมูลชุดล่าสุดแล้ว" if batch_source_state["text"] else "ยังไม่มีข้อมูลชุด"
    )
    saved_characters = saved_batch_payload.get("characters")
    if not isinstance(saved_characters, list):
        saved_characters = []
    saved_source = str(saved_batch_payload.get("source") or "")
    if saved_source != batch_source_state["text"]:
        saved_source = ""
        saved_characters = []
    batch_cache = {
        "source": saved_source,
        "characters": saved_characters,
        "design_page": str(saved_batch_payload.get("design_page") or ""),
    }
    g["story_face_batch_source"] = batch_source_state
    g["story_face_batch_status_var"] = batch_status_var

    def _story_dataset_title(text):
        for line in str(text or "").splitlines():
            title = line.strip().strip('"').strip()
            if title:
                return title[:60]
        return "นิทาน 3D"

    def _ensure_story_face_history(force=False):
        """นิทาน uses its own GPT history; the first request simply creates it."""
        story = face_store.get("story") or {}
        title = str(story.get("title") or "") or (
            _story_dataset_title(batch_source_state.get("text")) if batch_source_state.get("text") else "ทั่วไป")
        state_text = "ต่อประวัตินิทานของเรื่องนี้" if _face_conversation().get("conversation_id") else "จะเปิดประวัตินิทานใหม่ของเรื่องนี้"
        root.after(0, lambda value=title: (
            story_face_title_var.set(value),
            story_face_status_var.set(state_text + " (แยกจากหน้าอื่น)"),
        ))
        return title

    new_log = _builder_make_log_box(new_box)
    new_log.pack(fill="x", pady=(8, 0))
    g["new_log_box"] = new_log
    
    def _new_log(msg):
        _builder_append_log(new_log, msg)

    def _story_error(error):
        friendly = g.get("_snapgen_friendly_bridge_error")
        return friendly(error) if callable(friendly) else str(error)

    def _is_bridge_failure(error):
        text = str(error).casefold()
        return any(marker in text for marker in (
            "http 401", "http 429", "http 500", "http 502", "http 503", "http 504",
            "provider_error", "connection error", "timed out", "timeout",
        ))

    # UV repair is a separate image-edit job. It never reuses the Face prompt
    # or changes the source file; GPT writes one repaired sibling into export.
    uv_source_path = [None]
    uv_source_var = tk.StringVar(value="ยังไม่ได้เลือกไฟล์ UV")
    uv_ref_path = [None]
    uv_ref_var = tk.StringVar(value="ยังไม่ได้เลือก Ref ใบหน้า")
    uv_repair_running = [False]
    uv_box = tk.LabelFrame(
        new_box, text="🧩 ซ่อม UV Texture", bg="#FAFAF7", fg="#1A1A1A",
        padx=8, pady=6,
    )
    uv_box.pack(fill="x", pady=(8, 0))
    tk.Label(
        uv_box, text="ไฟล์ UV:", bg="#FAFAF7", fg="#333",
    ).pack(side="left")
    tk.Label(
        uv_box, textvariable=uv_source_var, bg="#FFFFFF", fg="#374151",
        anchor="w", relief="solid", bd=1, padx=8, pady=6,
    ).pack(side="left", fill="x", expand=True, padx=6)
    tk.Label(
        uv_box, text="Ref หน้า:", bg="#FAFAF7", fg="#333",
    ).pack(side="left", padx=(4, 0))
    tk.Label(
        uv_box, textvariable=uv_ref_var, bg="#FFFFFF", fg="#374151",
        anchor="w", relief="solid", bd=1, padx=8, pady=6,
    ).pack(side="left", fill="x", expand=True, padx=6)

    uv_repair_prompt = (
        "Repair only this existing 3D character UV albedo texture. Preserve the exact UV atlas, canvas size, "
        "all island positions, boundaries, packing, shirt islands and background. Do not create a portrait or a new UV map. "
        "Restore missing short dark hair with subtle gray strands across the large left and right side-head islands, "
        "continuing naturally from the central hairline with scalp-following strand direction. "
        "Add realistic high-frequency older Thai male skin detail only where it is missing: pores, fine wrinkles, "
        "subtle sun spots, uneven tone, crow's-feet and nasolabial texture across face, scalp, ears and neck. "
        "Keep the current face identity, eyes, nose, mouth, ears and correct texture unchanged. "
        "Never move, resize, rotate, crop, merge or delete UV islands. No blank patches, plastic skin, smears, "
        "duplicate facial features, extra eyes, ears or mouths, text, watermark, lighting, background or clothing changes."
    )
    g["story_face_uv_repair_prompt"] = uv_repair_prompt

    def _lock_uv_layout(source_path, repaired_path, preserve_detail=True):
        """Keep repaired UV detail while restoring the source canvas dimensions."""
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        import numpy as np

        if preserve_detail:
            with Image.open(source_path) as source_image, Image.open(repaired_path) as repaired_image:
                original = source_image.convert("RGBA")
                repaired = repaired_image.convert("RGBA")
                source_alpha = original.getchannel("A").resize(repaired.size, Image.Resampling.LANCZOS)
                repaired.putalpha(source_alpha)
                repaired.save(repaired_path, format="PNG")
                return repaired.size

        with Image.open(source_path) as source_image:
            original = source_image.convert("RGBA")
        with Image.open(repaired_path) as repaired_image:
            repaired = repaired_image.convert("RGBA").resize(original.size, Image.Resampling.LANCZOS)
        width, height = original.size

        original_rgb = original.convert("RGB")
        repaired_rgb = repaired.convert("RGB")

        # Accept the whole generated side-hair cap, not only dark pixels. The
        # old dark-pixel filter restored source skin into bright gaps and made
        # holes throughout the hair mass.
        hair_band = Image.new("L", original.size, 0)
        hair_draw = ImageDraw.Draw(hair_band)
        hair_draw.ellipse(
            (int(width * 0.055), int(height * 0.018), int(width * 0.495), int(height * 0.405)),
            fill=255,
        )
        hair_draw.ellipse(
            (int(width * 0.505), int(height * 0.018), int(width * 0.945), int(height * 0.405)),
            fill=255,
        )

        # Fill bright skin-colored holes inside the hair cap from nearby valid
        # dark hair. Work at 1/4 scale for speed, then preserve GPT detail where
        # it is already valid.
        work_size = (max(256, width // 4), max(256, height // 4))
        small = repaired_rgb.resize(work_size, Image.Resampling.LANCZOS)
        small_band = hair_band.resize(work_size, Image.Resampling.LANCZOS)
        small_array = np.asarray(small, dtype=np.float32)
        luminance = (
            small_array[..., 0] * 0.299
            + small_array[..., 1] * 0.587
            + small_array[..., 2] * 0.114
        )
        band_array = np.asarray(small_band, dtype=np.float32) / 255.0
        valid_array = ((luminance < 165) & (band_array > 0.1)).astype(np.float32)
        valid_mask = Image.fromarray((valid_array * 255).astype("uint8"), "L")
        # Build one continuous hair mass from generated dark hair, closing
        # scalp-colored pinholes without turning the whole ellipse into hair.
        mass_mask = valid_mask.filter(ImageFilter.MaxFilter(61))
        mass_mask = mass_mask.filter(ImageFilter.MinFilter(31))
        mass_mask = ImageChops.multiply(mass_mask, small_band)
        blur_radius = max(12, work_size[0] // 35)
        denominator = np.asarray(valid_mask.filter(ImageFilter.GaussianBlur(blur_radius)), dtype=np.float32) / 255.0
        inferred_channels = []
        for channel in range(3):
            weighted = Image.fromarray(
                (small_array[..., channel] * valid_array).clip(0, 255).astype("uint8"),
                "L",
            ).filter(ImageFilter.GaussianBlur(blur_radius))
            numerator = np.asarray(weighted, dtype=np.float32)
            inferred_channels.append(numerator / np.maximum(denominator, 0.025))
        inferred_array = np.stack(inferred_channels, axis=-1).clip(0, 255)
        mass_array = np.asarray(mass_mask, dtype=np.float32) / 255.0
        holes = (mass_array > 0.1) & (luminance >= 165) & (denominator > 0.025)
        filled_array = small_array.copy()
        filled_array[holes] = inferred_array[holes]
        filled_hair = Image.fromarray(filled_array.astype("uint8"), "RGB").resize(
            original.size, Image.Resampling.LANCZOS,
        )
        # Preserve detailed generated strands on valid dark hair; use inferred
        # texture only to close skin-colored holes.
        repaired_luma = repaired_rgb.convert("L")
        bright_holes = repaired_luma.point(lambda value: 255 if value >= 165 else 0)
        hair_mass = mass_mask.resize(original.size, Image.Resampling.LANCZOS)
        hole_mask = ImageChops.multiply(bright_holes, hair_mass)
        hole_mask = hole_mask.filter(ImageFilter.GaussianBlur(max(3, int(width * 0.0015))))
        solid_hair = Image.composite(filled_hair, repaired_rgb, hole_mask)
        hair_mask = hair_mass.filter(ImageFilter.GaussianBlur(max(5, int(width * 0.0025))))
        output = Image.composite(solid_hair, original_rgb, hair_mask)

        # Skin repair uses the original map only. A weak low-frequency blend
        # evens blotchy color without importing GPT's invented pore pattern.
        smooth_skin = original_rgb.filter(ImageFilter.GaussianBlur(max(12, int(width * 0.006))))
        skin_mask = Image.new("L", original.size, 0)
        draw = ImageDraw.Draw(skin_mask)
        draw.ellipse(
            (int(width * 0.015), int(height * 0.12), int(width * 0.40), int(height * 0.76)),
            fill=42,
        )
        draw.ellipse(
            (int(width * 0.60), int(height * 0.12), int(width * 0.985), int(height * 0.76)),
            fill=42,
        )
        draw.ellipse(
            (int(width * 0.18), int(height * 0.56), int(width * 0.82), int(height * 0.84)),
            fill=30,
        )
        # Never smooth central face, ears, hair, background, or bottom islands.
        draw.ellipse(
            (int(width * 0.20), int(height * 0.12), int(width * 0.80), int(height * 0.70)),
            fill=0,
        )
        skin_mask = ImageChops.subtract(skin_mask, hair_mask)
        skin_mask = skin_mask.filter(ImageFilter.GaussianBlur(max(10, int(width * 0.004))))
        output = Image.composite(smooth_skin, output, skin_mask)

        # Restore exact flat background from source after every blend so GPT
        # cannot grow hair or skin outside the original UV islands.
        corner = original_rgb.getpixel((0, 0))
        flat_background = Image.new("RGB", original.size, corner)
        difference = ImageChops.difference(original_rgb, flat_background)
        red, green, blue = difference.split()
        distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        background_mask = distance.point(lambda value: 255 if value <= 6 else 0)
        background_mask = background_mask.filter(ImageFilter.MaxFilter(5))
        output = Image.composite(original_rgb, output, background_mask)

        final = output.convert("RGBA")
        final.putalpha(original.getchannel("A"))
        final.save(repaired_path, format="PNG")
        return original.size

    def _upscale_uv_to_4096(repaired_path):
        """Use the bundled compact Real-ESRGAN photo model before completing UV repair."""
        from PIL import Image

        repaired_path = Path(repaired_path)
        with Image.open(repaired_path) as image:
            width, height = image.size
        if (width, height) == (4096, 4096):
            return repaired_path

        runtime = BASE / "snapgen_data" / "tools" / "realesrgan-ncnn-vulkan"
        executable = runtime / "realesrgan-ncnn-vulkan.exe"
        model = runtime / "models" / "realesrgan-x4plus.bin"
        if not executable.is_file() or not model.is_file():
            raise RuntimeError("ไม่พบ Real-ESRGAN x4plus สำหรับขยาย UV เป็น 4096")

        temp_output = repaired_path.with_name(repaired_path.stem + "_upscale_tmp.png")
        scale = 4 if max(width, height) <= 1500 else 2
        _new_log(f"[UV] Real-ESRGAN x4plus: {width}x{height} → 4096x4096...")
        process = subprocess.run(
            [str(executable), "-i", str(repaired_path), "-o", str(temp_output),
             "-s", str(scale), "-t", "256", "-m", str(runtime / "models"),
             "-n", "realesrgan-x4plus", "-f", "png"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.returncode or not temp_output.is_file():
            temp_output.unlink(missing_ok=True)
            raise RuntimeError("Real-ESRGAN ขยาย UV ไม่สำเร็จ: " + (process.stderr or process.stdout)[-500:])
        try:
            with Image.open(temp_output) as image:
                image.convert("RGBA").resize((4096, 4096), Image.Resampling.LANCZOS).save(repaired_path, format="PNG")
        finally:
            temp_output.unlink(missing_ok=True)
        return repaired_path

    def _choose_uv_file():
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=root,
            title="เลือกไฟล์ UV Texture",
            filetypes=[
                ("รูปภาพ", "*.png *.jpg *.jpeg *.webp"),
                ("ทุกไฟล์", "*.*"),
            ],
        )
        if not path:
            return
        source = Path(path)
        uv_source_path[0] = source
        uv_source_var.set(source.name)
        _new_log(f"[UV] เลือกไฟล์: {source}")

    def _choose_uv_ref_file():
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=root,
            title="เลือก Ref ใบหน้าตัวอย่าง",
            filetypes=[
                ("รูปภาพ", "*.png *.jpg *.jpeg *.webp"),
                ("ทุกไฟล์", "*.*"),
            ],
        )
        if not path:
            return
        source = Path(path)
        uv_ref_path[0] = source
        uv_ref_var.set(source.name)
        _new_log(f"[UV] เลือก Ref ใบหน้า: {source}")

    def _repair_uv_texture():
        source = uv_source_path[0]
        if not source or not Path(source).is_file():
            _new_log("[UV] เลือกไฟล์ UV ก่อน")
            return
        if uv_repair_running[0]:
            _new_log("[UV] กำลังซ่อมอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        source = Path(source)
        uv_repair_running[0] = True
        uv_repair_btn.config(state=tk.DISABLED, text="กำลังซ่อม...")
        _new_log(f"[UV] ส่ง GPT ซ่อม texture โดยล็อก layout: {source.name}")

        def worker():
            try:
                _ensure_story_face_history()
                encoded = base64.b64encode(source.read_bytes()).decode("ascii")
                repair_prompt = uv_repair_prompt
                repair_images = [encoded]
                ref_source = uv_ref_path[0]
                if ref_source and Path(ref_source).is_file():
                    repair_images.append(
                        base64.b64encode(Path(ref_source).read_bytes()).decode("ascii")
                    )
                    repair_prompt += (
                        "\n\nREFERENCE FACE: image 2 is the desired face identity and neutral appearance. "
                        "Use it only as the facial appearance/color reference for the matching UV face islands. "
                        "Image 1 remains the authoritative UV map: never copy the portrait layout, background, "
                        "pose, or rectangular face image into the UV texture. Preserve every UV island position."
                    )
                output_dir = Path(export_story_face_dir) / "uv_repair"
                output_dir.mkdir(parents=True, exist_ok=True)
                payload = _build_story_face_payload(repair_prompt)
                _face_payload_history(payload)
                payload["aspect_ratio"] = "1:1"
                payload["images"] = repair_images
                lock = globals().get("_bridge_queue_lock")

                def request_image():
                    if "_wait_bridge_free" in globals():
                        globals()["_wait_bridge_free"](log_fn=_new_log)
                    return g["_do_image_request"](
                        payload,
                        is_edit=True,
                        prompt=repair_prompt,
                        name_hint=f"{source.stem}_uv_fixed",
                        raw_prompt=repair_prompt,
                        output_dir=str(output_dir),
                        save_sidecar=False,
                    )

                if lock:
                    with lock:
                        result = Path(request_image())
                else:
                    result = Path(request_image())

                # GPT commonly returns 1024 square. Run the compact photo upscaler
                # before final UV normalization so the delivered map is always 4K.
                result = _upscale_uv_to_4096(result)
                _lock_uv_layout(source, result)
                from PIL import Image
                with Image.open(result) as verified:
                    if verified.size != (4096, 4096) or verified.mode != "RGBA":
                        raise RuntimeError("ไฟล์ที่ซ่อมไม่ผ่านการตรวจขนาด/โหมดสี UV")

                def done():
                    uv_repair_running[0] = False
                    uv_repair_btn.config(state=tk.NORMAL, text="🧩 ซ่อม UV")
                    _new_log(f"✓ ซ่อม UV สำเร็จ: {result}")
                    _notify_done()
                root.after(0, done)
            except Exception as exc:
                def failed(error=str(exc)):
                    uv_repair_running[0] = False
                    uv_repair_btn.config(state=tk.NORMAL, text="🧩 ซ่อม UV")
                    _new_log(f"[UV] ERROR: {error}")
                root.after(0, failed)

        threading.Thread(target=worker, daemon=True).start()

    tk.Button(
        uv_box, text="📂 เลือกไฟล์", command=_choose_uv_file,
        bg="#2563EB", fg="white", relief="flat", width=14,
        padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(0, 6))
    tk.Button(
        uv_box, text="📷 เลือก Ref", command=_choose_uv_ref_file,
        bg="#7C3AED", fg="white", relief="flat", width=12,
        padx=12, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(0, 6))
    uv_repair_btn = tk.Button(
        uv_box, text="🧩 ซ่อม UV", command=_repair_uv_texture,
        bg="#059669", activebackground="#10B981", fg="white",
        relief="flat", width=14, padx=14, pady=7,
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    )
    uv_repair_btn.pack(side="left")
    g["story_face_choose_uv_file"] = _choose_uv_file
    g["story_face_choose_uv_ref_file"] = _choose_uv_ref_file
    g["story_face_repair_uv_texture"] = _repair_uv_texture

    # Story 3D outfit design belongs here. Each coordinated outfit becomes two
    # separate images; users may send either image to Prop only when needed.
    outfit_box = tk.LabelFrame(
        new_box, text="👕 ชุดตัวละคร 3D — แยกเสื้อ / ท่อนล่าง",
        bg="#FAFAF7", fg="#1A1A1A", padx=8, pady=6,
    )
    outfit_box.pack(fill="x", pady=(8, 0))
    outfit_character_var = tk.StringVar(value="เลือกตัวละคร")
    outfit_characters = {}
    outfit_running = [False]
    tk.Label(outfit_box, text="ตัวละคร:", bg="#FAFAF7", fg="#333").pack(side="left")
    outfit_picker = ttk.Combobox(
        outfit_box, state="readonly", width=28,
        textvariable=outfit_character_var, values=(),
    )
    outfit_picker.pack(side="left", padx=6)

    def _load_outfit_characters():
        # Story 3D owns its cast. Never read Prompt-Ref Context here because
        # that file belongs to the separate Film/AI workflow.
        characters = _current_dataset_characters()
        outfit_characters.clear()
        labels = []
        for character in characters or []:
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            if not name:
                continue
            age = str(character.get("อายุ") or character.get("age") or "").strip()
            variant = str(character.get("variant") or "").strip()
            detail = " / ".join(value for value in (variant, age) if value)
            label = name + (f" — {detail}" if detail else "")
            base_label = label
            number = 2
            while label in outfit_characters:
                label = f"{base_label} ({number})"
                number += 1
            labels.append(label)
            outfit_characters[label] = character
        outfit_picker.config(values=labels)
        if labels and outfit_character_var.get() not in outfit_characters:
            outfit_character_var.set(labels[0])
        elif not labels:
            outfit_character_var.set("กด ข้อมูลชุด → วิเคราะห์ ก่อน")
        return labels

    def _analyze_outfit(character):
        story_source = str(batch_source_state.get("text") or "").strip()
        character_bible = str(batch_cache.get("design_page") or "").strip()
        system = (
            "คุณเป็น costume designer สำหรับนิทาน 3D ไทย. ตอบ JSON object เท่านั้น มี outfit_id, concept, upper, lower. "
            "upper และ lower ต้องมี item_name, item_type, colors, fabric, construction, wear_condition, details. "
            "ออกแบบชุดหนึ่งชุดเป็น 2 ชิ้นแยกกัน เสื้อและท่อนล่างต้องตรงกันด้านยุค อาชีพ ฐานะ สี ผ้า ความเก่าและเหตุการณ์. "
            "ท่อนล่างเลือกกางเกง กระโปรง ผ้าถุง โจงกระเบน หรือชนิดอื่นตามเรื่อง. ห้ามคน หุ่น รองเท้า เครื่องประดับและฉาก."
        )
        payload_data = {
            "model": "auto", "chatgpt_image_intercept": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": (
                    "STORY 3D SOURCE — จากข้อมูลชุดนิทานเท่านั้น:\n" + story_source[:12000]
                    + "\n\nSTORY CHARACTER BIBLE:\n" + character_bible[:4000]
                    + "\n\nTARGET STORY CHARACTER:\n" + json.dumps(character, ensure_ascii=False)
                )},
            ],
            "temperature": 0.2,
        }
        own = _face_conversation()
        if own.get("conversation_id") and own.get("parent_message_id"):
            payload_data["metadata"] = {"conversation_id": own["conversation_id"],
                                        "parent_message_id": own["parent_message_id"]}
            if own.get("account_alias"):
                payload_data["chatgpt_account"] = own["account_alias"]
        payload = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
        base_fn = globals().get("_chatgpt_api_base")
        base = base_fn() if callable(base_fn) else "http://127.0.0.1:8000/v1"
        request = urllib.request.Request(
            base.rstrip("/") + "/chat/completions", data=payload,
            headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json", "User-Agent": "Tidmun-Studio/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("conversation_id") and result.get("parent_message_id"):
            own.update({"conversation_id": str(result["conversation_id"]),
                        "parent_message_id": str(result["parent_message_id"])})
            if result.get("chatgpt_account"):
                own["account_alias"] = str(result["chatgpt_account"])
            _save_face_store()
        content = str((((result.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").replace("```json", "").replace("```", "").strip()
        start, end = content.find("{"), content.rfind("}")
        design = json.loads(content[start:end + 1] if start >= 0 and end > start else content)
        if not isinstance(design.get("upper"), dict) or not isinstance(design.get("lower"), dict):
            raise RuntimeError("GPT ไม่คืน upper/lower")
        return design

    def _fallback_outfit(character):
        name = str(character.get("name") or "ตัวละคร")
        clothes = str(character.get("เสื้อผ้า") or character.get("clothes") or "ชุดตามบริบทเรื่อง")
        common = {
            "colors": "วิเคราะห์สีจากบุคลิก ฐานะ ยุค สถานที่ และเหตุการณ์ในเรื่อง",
            "fabric": "วิเคราะห์วัสดุจากวัฒนธรรม ยุค อาชีพ ฐานะ และภูมิอากาศในเรื่อง",
            "wear_condition": "วิเคราะห์สภาพใหม่ เก่า สะอาด โทรม หรือใช้งานหนักจากชีวิตตัวละคร",
            "details": clothes,
        }
        return {
            "outfit_id": f"{name}_ชุดหลัก", "concept": clothes,
            "upper": {**common, "item_name": f"เสื้อของ{name}", "item_type": "เลือกชนิดเสื้อที่เหมาะที่สุดจากข้อมูลเรื่อง", "construction": "วิเคราะห์รูปทรงและการตัดเย็บจากเรื่อง"},
            "lower": {**common, "item_name": f"ท่อนล่างของ{name}", "item_type": "เลือกกางเกง กระโปรง ผ้าถุง โจงกระเบน หรือชนิดที่เหมาะที่สุดจากเรื่อง", "construction": "วิเคราะห์รูปทรงและการตัดเย็บจากเรื่อง"},
        }

    def _garment_3d_type(slot, garment):
        text = " ".join(str(garment.get(key) or "") for key in (
            "item_name", "item_type", "details", "construction",
        )).lower()
        if any(word in text for word in ("dress", "เดรส", "ชุดกระโปรง", "ชุดชิ้นเดียว")):
            return "Dress"
        if slot == "upper":
            return "Shirt"
        if any(word in text for word in ("skirt", "sarong", "กระโปรง", "ผ้าถุง")):
            return "Skirt"
        return "Pants"

    def _outfit_image_prompt(name, outfit_id, concept, garment, slot, character):
        story_source = str(batch_source_state.get("text") or "").strip()
        piece = (
            "UPPER GARMENT ONLY — MANDATORY VOLUMETRIC GHOST-MANNEQUIN T-POSE: the garment must hold the full three-dimensional "
            "shape of an invisible human torso in T-pose while absolutely no mannequin, body or skin is visible. Torso has realistic chest, back, "
            "shoulder slope, side depth and waist circumference. Left and right sleeves extend straight horizontally at shoulder height as hollow "
            "cylindrical fabric volumes, forming a capital T silhouette. Show real depth inside collar opening, sleeve openings and bottom hem, plus "
            "underarm construction, side seams, fabric thickness and natural gravity folds around a volumetric form. Use a slight three-quarter "
            "front camera angle (about 15 degrees) so front and side thickness are readable while both sleeves remain horizontal and equally visible. "
            "This is NOT flat-lay clothing, NOT a paper-thin cutout, NOT a flattened shirt icon, NOT folded, and NOT hanging from a hanger. "
            "Sleeves must not hang down, bend, point diagonally or use an A-pose. Preserve the designed sleeve length and garment type. "
            if slot == "upper" else
            "LOWER GARMENT ONLY — VOLUMETRIC GHOST-MANNEQUIN SHAPE: trousers, skirt, sarong, chong kraben or the designed lower piece must hold "
            "realistic invisible waist, hip, seat and leg volume with no mannequin, body or skin visible. Show waistband depth, side thickness, hollow "
            "openings, fabric thickness and natural gravity folds from a slight three-quarter front angle. NOT flat-lay and NOT a paper-thin cutout. "
        )
        return (
            f"Create one photorealistic 3D clothing Prop source image. OUTFIT ID: {outfit_id}. Character context: {name}. "
            f"STORY SOURCE: {story_source[:2500]}. "
            f"STORY CHARACTER DATA: {json.dumps(character, ensure_ascii=False)[:3000]}. "
            f"Coordinated concept: {concept}. {piece}. GARMENT DESIGN: {json.dumps(garment, ensure_ascii=False)}. "
            "STORY-DRIVEN COSTUME ANALYSIS — HIGHEST PRIORITY: before rendering, infer the story's country/culture, historical period, "
            "region or setting, genre, climate, character age, gender presentation, occupation, social class, wealth, personality, current life condition "
            "and scene needs solely from STORY SOURCE and STORY CHARACTER DATA. Choose the garment type, silhouette, cut, fabric, colors, patterns, "
            "construction, condition and level of decoration that are most plausible for those facts. Do not force modern, historical, rural, urban, "
            "luxury, fantasy or any other style unless the story supports it. If the source is ambiguous, choose the least speculative ordinary option "
            "consistent with the known facts. Upper and lower pieces must clearly belong to one coordinated outfit for the same character and moment. "
            "Exactly one complete empty garment supported by an invisible ghost form, full piece visible with strong readable 3D volume. "
            + ("Slight three-quarter front view, symmetric horizontal T-pose silhouette. " if slot == "upper" else "Slight three-quarter front product view. ") +
            "realistic fabric thickness, seams, stitching, closures, folds and wear. Pure white seamless background, neutral studio light, sharp detail. "
            "No visible person, model, mannequin, skin, head, hands, legs, hanger, rack, shoes, accessories, other garment, duplicate, collage, text, scene or watermark."
        )

    def _create_story_outfit():
        character = outfit_characters.get(outfit_character_var.get())
        if not character:
            if not _load_outfit_characters():
                _new_log("[ชุด 3D] ยังไม่มีตัวละครนิทาน — กด ข้อมูลชุด แล้วกด วิเคราะห์ ก่อน")
                return
            character = outfit_characters.get(outfit_character_var.get())
        if outfit_running[0]:
            _new_log("[ชุด 3D] กำลังสร้างอยู่")
            return
        outfit_running[0] = True
        outfit_create_btn.config(state=tk.DISABLED, text="กำลังออกแบบ...")
        name = str(character.get("name") or "ตัวละคร")

        def worker():
            try:
                lock = globals().get("_bridge_queue_lock")
                def run():
                    # Character data already has story, role and clothes. Send it
                    # straight to image generation; do not add a fragile chat/JSON call.
                    design = _fallback_outfit(character)
                    outfit_id = str(design.get("outfit_id") or f"{name}_ชุดหลัก")
                    concept = str(design.get("concept") or "ชุดตามเรื่อง")
                    output_dir = Path(export_story_face_dir) / "outfits"
                    output_dir.mkdir(parents=True, exist_ok=True)
                    outputs = []
                    for index, (slot, thai) in enumerate((("upper", "เสื้อ"), ("lower", "ท่อนล่าง")), 1):
                        prompt = _outfit_image_prompt(name, outfit_id, concept, design[slot], slot, character)
                        if "_wait_bridge_free" in globals():
                            globals()["_wait_bridge_free"](log_fn=_new_log)
                        _new_log(f"[ชุด 3D] {index}/2 — สร้าง {thai}")
                        payload = {"model": "auto", "prompt": prompt, "n": 1, "aspect_ratio": "1:1", "history_and_training_disabled": False}
                        output = g["_do_image_request"](
                            payload, is_edit=False, prompt=prompt,
                            name_hint=f"{name}_{thai}_{outfit_id}", raw_prompt=prompt,
                            output_dir=str(output_dir), save_sidecar=False,
                        )
                        garment_type = _garment_3d_type(slot, design[slot])
                        Path(str(output) + ".garment.json").write_text(
                            json.dumps({"garment_type": garment_type}, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        outputs.append((thai, output, garment_type))
                    return outfit_id, outputs
                if lock:
                    with lock:
                        outfit_id, outputs = run()
                else:
                    outfit_id, outputs = run()
                def done():
                    for thai, output, garment_type in outputs:
                        _new_gallery_add(
                            output, True, send_to_prop=True,
                            prop_name=f"{name}_{thai}_{outfit_id}",
                            prop_type=garment_type,
                        )
                        _new_log(f"✓ {thai}: {output}")
                    _new_log(f"[ชุด 3D] สร้างครบ: {outfit_id}")
                    _notify_done()
                root.after(0, done)
            except Exception as exc:
                root.after(0, lambda error=exc: _new_log(f"[ชุด 3D] ERROR: {_story_error(error)}"))
            finally:
                root.after(0, lambda: (outfit_running.__setitem__(0, False), outfit_create_btn.config(state=tk.NORMAL, text="👕 สร้างชุด")))
        threading.Thread(target=worker, daemon=True).start()

    outfit_picker.bind("<Button-1>", lambda _event: _load_outfit_characters(), add="+")
    outfit_create_btn = tk.Button(
        outfit_box, text="👕 สร้างชุด", command=_create_story_outfit,
        bg="#059669", activebackground="#10B981", fg="white", relief="flat",
        width=14, padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"),
    )
    outfit_create_btn.pack(side="left", padx=(0, 6))

    g["create_story_outfit"] = _create_story_outfit

    def _apply_final_face_lighting_lock(prompt):
        """Make shadowless identity lighting the final instruction after AI refine."""
        return str(prompt or "").rstrip() + (
            "\n\nFINAL LIGHTING OVERRIDE — MANDATORY: use centered frontal ring-light plus a large "
            "camera-axis softbox and equal-power fill from left, right, above, and below. The left and "
            "right halves of the face must have identical exposure and white balance, with under 1% "
            "luminance difference. ZERO left-to-right brightness gradient. ZERO facial shading or cast "
            "shadow: none under eyebrows, around eyes, beside or beneath the nose, across cheeks, lips, "
            "jaw, or chin. No directional key light, side light, top light, rim light, Rembrandt pattern, "
            "chiaroscuro, cinematic contrast, dark face side, or vignette. Preserve true skin color, pores, "
            "wrinkles, and texture without whitening, blur, beauty filter, or plastic skin. Even lighting must reveal, "
            "not erase, every requested sign of age, stress, illness, exhaustion, weight change, poor sleep, or hardship. Neutral light-gray "
            "background must also be evenly lit. This lighting override replaces every earlier lighting instruction."
        )

    def _force_neutral_face_prompt(prompt):
        return str(prompt or "").rstrip() + (
            "\n\nFINAL SUBJECT AND EXPRESSION OVERRIDE — MANDATORY: exactly ONE person and ONE "
            "face only; no spouse, companion, second portrait, split image, or collage. Use an ordinary "
            "calm neutral ID-photo expression regardless of story emotion: eyebrows resting naturally, "
            "eyes calm, forehead relaxed, jaw relaxed, mouth fully closed with relaxed lips. No anger, "
            "scowl, frown, sadness, fear, smile, teeth, or dramatic emotion. This final instruction "
            "overrides every earlier emotion or expression description."
        )

    def _apply_clean_face_lock(prompt):
        """Keep face assets clean while preserving real skin and identity marks."""
        return str(prompt or "").rstrip() + (
            "\n\nFINAL CLEAN FACE AND HAIR OVERRIDE — MANDATORY: keep the entire face portrait clean and "
            "unobstructed. No jewelry or decorative accessory anywhere on the head, forehead, temples, eyebrows, "
            "ears, nose, cheeks, lips, jaw, or neck: no headband, tiara, crown, forehead chain, bindi, jewel, "
            "hair ornament, earrings, ear cuffs, nose ring, facial piercing, necklace, choker, collar ornament, "
            "face paint, glitter, or decorative makeup. Use plain unobstructed ears and a plain unobstructed neck. "
            "Hair must stay fully behind the head and ears with the complete hairline, forehead, temples, eyebrows, "
            "cheeks, jawline, and ears visible; no bangs, fringe, wisps, curls, side locks, or stray strands crossing "
            "the face. Preserve only natural skin features and identity marks explicitly required by the target, such "
            "as pores, fine lines, wrinkles, freckles, moles, scars, or texture. Do not replace skin detail with "
            "smooth beauty skin. This clean-face instruction overrides earlier clothing, grooming, accessory, "
            "headwear, jewelry, and hairstyle details."
            + (" EXCEPTION: the STORY MAKEUP EFFECT above is required non-graphic film makeup for the story — "
               "keep it exactly as described; it is not decorative makeup." if "STORY MAKEUP EFFECT" in str(prompt or "") else "")
        )

    def _apply_face_age_override(prompt, age_label):
        age_rules = {
            "เด็ก": "an 8-year-old child with unmistakable child facial proportions",
            "วัยรุ่น": "a 17-year-old teenager with unmistakable adolescent facial proportions",
            "ผู้ใหญ่": "an approximately 35-year-old adult",
            "ผู้สูงอายุ": (
                "an unmistakably elderly person approximately 70-80 years old, with pronounced authentic "
                "forehead lines, crow's-feet, under-eye aging, nasolabial folds, age spots, reduced facial "
                "fullness, and naturally looser mature skin; absolutely not a teenager, young adult, or "
                "middle-aged person"
            ),
        }
        rule = age_rules.get(str(age_label or "").strip())
        if not rule:
            return str(prompt or "").rstrip()
        return str(prompt or "").rstrip() + (
            "\n\nFINAL AGE OVERRIDE — MANDATORY: the target must visibly be "
            + rule
            + ". Preserve identity, but never copy the age or skin condition of a younger reference image. "
              "This final age instruction overrides every earlier age description."
        )

    identity_lock = (
        "\n\nIDENTITY REFERENCE — MANDATORY: the attached image is the same person at another age, "
        "not a second person and not a scene reference. Preserve recognizable identity: facial proportions, "
        "eye shape and spacing, eyebrows, nose structure, lips, jaw, cheekbones, ears, skin tone, and distinctive "
        "marks. The CURRENT TARGET PROMPT has priority for variable condition: requested age, stress, exhaustion, "
        "illness, hardship, weight loss or gain, sun damage, poor sleep, grooming, expression, wrinkles, skin firmness, "
        "facial fat, hair color or density, and other visible life changes must be clearly rendered. Identity similarity "
        "does not mean copying the reference condition. Do not copy the reference age, clothing, "
        "background, lighting, pose, or expression. Output one person only."
    )
    selected_condition = [""]
    selected_body = {"name": "", "facts": ""}
    selected_character_key = [""]

    def _story_face_character_key(character):
        signature = json.dumps(character, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]

    def _condition_text(character):
        values = []
        for key in (
            "ช่วงชีวิต", "สภาพชีวิต", "สุขภาพ", "สภาพร่างกาย", "น้ำหนัก", "ใบหน้า", "ดวงตา",
            "life_condition", "appearance", "skin_detail",
        ):
            value = str(character.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
        return "; ".join(values)

    def _append_condition_override(prompt, condition):
        text = str(prompt or "").rstrip()
        condition = str(condition or "").strip()
        if not condition or "CURRENT CONDITION OVERRIDE — MANDATORY:" in text:
            return text
        return (
            text
            + "\n\nCURRENT CONDITION OVERRIDE — MANDATORY: "
            + condition
            + ". Show this condition visibly and strongly enough to distinguish this version from the normal identity master. "
              "It may change expression, eye tension, under-eye darkness, facial fullness, wrinkles, skin vitality, hair and grooming, "
              "but must remain recognizably the same person."
        )

    def _identity_reference_payload(prompt, reference=None):
        """Attach one prior face and lock identity while allowing age change."""
        reference = Path(reference) if reference else identity_ref_path[0]
        if not reference or not reference.is_file():
            return str(prompt), None
        encoded = base64.b64encode(reference.read_bytes()).decode("ascii")
        text = str(prompt).rstrip()
        if "IDENTITY REFERENCE — MANDATORY:" not in text:
            text += identity_lock
        return text, [encoded]

    def _identity_family_key(character):
        """Group age/emotion variants of one named person without merging other cast."""
        explicit = str(
            character.get("identity_group")
            or character.get("reference_from")
            or ""
        ).strip().casefold()
        if explicit:
            return explicit
        name = str(character.get("name") or "").strip().casefold()
        variant = str(character.get("variant") or "").strip().casefold()
        if variant:
            name = re.sub(rf"(?:\s*[-—:()]?\s*{re.escape(variant)}\s*[)]?)$", "", name).strip()
        name = name.replace("_", " ")
        suffix = re.compile(
            r"\s*(?:ตอน)?(?:วัยเด็ก|เด็ก|วัยรุ่น|หนุ่ม|สาว|วัยหนุ่ม|วัยสาว|วัยทำงาน|วัยกลางคน|ผู้ใหญ่|แก่|วัยชรา|ชรา|ผู้สูงอายุ|ปกติ|เครียด|เศร้า|เสียใจ|โกรธ|ดีใจ|กลัว|ตกใจ|ป่วย|บาดเจ็บ)\s*$"
        )
        previous = None
        while name and name != previous:
            previous = name
            name = suffix.sub("", name).strip(" -—:()")
        return name or str(character.get("name") or "").strip().casefold()

    _CAST_FACE_PROFILES = (
        "long narrow face, high forehead, deep-set close-set almond eyes, straight long nose, thin lips, narrow jaw and pointed chin",
        "round broad face, low forehead, wide-set round eyes, short broad nose, full lips, soft jaw and rounded chin",
        "square face, broad forehead, heavy straight brows, hooded eyes, strong nose bridge, wide jaw and flat chin",
        "heart-shaped face, broad upper cheekbones, tapered jaw, slightly downturned eyes, narrow nose and defined cupid's bow",
        "balanced oval face, medium forehead, medium-spaced almond eyes, gently arched brows, medium nose and soft jaw",
        "rectangular face, low brow line, deep-set eyes, prominent cheekbones, broad nose and firm long jaw",
        "diamond face, narrow forehead and jaw, very high cheekbones, wide-set eyes, narrow nose and small mouth",
        "pear-shaped face, narrow forehead, full lower cheeks, slightly hooded eyes, broad jaw and round chin",
        "asymmetrical oval face, one eyebrow naturally slightly higher, uneven cheek fullness, medium nose and tapered jaw",
        "angular mature face, deep eye sockets, pronounced nasolabial folds, high cheekbones, long nose and strong chin",
        "short wide face, low cheekbones, large close-set eyes, flat nose bridge, wide mouth and compact jaw",
        "tall oval face, sloping forehead, narrow eyes with large spacing, prominent nose tip, thin upper lip and long chin",
    )

    # Independent feature axes. Each cast member steps through every axis with a
    # different stride, so no two people share the same combination and
    # neighbours in the cast list differ on every axis at once.
    _FACE_AXES = (
        ("casting type", (
            "Thai-Chinese heritage bone structure, flat wide cheekbones",
            "Isan heritage bone structure, strong prominent cheekbones",
            "southern Thai heritage bone structure, deep brow ridge",
            "northern hill-people heritage bone structure, flat mid-face",
            "Khmer-Thai heritage bone structure, wide jaw",
            "Mon-Thai heritage bone structure, long mid-face",
            "mixed rural heritage, rough coarse uneven features")),
        ("face shape", (
            "extremely long narrow horse-like face", "very round moon face", "very square blocky face with a huge jaw",
            "sharply pointed heart face with a tiny chin", "very wide short face", "very long face with a sloping forehead",
            "pear face, narrow top and wide bottom", "extremely angular face with sharp corners")),
        ("eyes", (
            "very small deep-set squinting eyes", "very large bulging round eyes", "heavy drooping hooded eyes",
            "sharply upturned slit eyes", "sad strongly downturned eyes", "very close-set narrow eyes",
            "very wide-set eyes far apart")),
        ("nose", (
            "very broad flat nose with wide flaring nostrils", "long thin pointed nose", "tiny button nose",
            "large prominent hooked nose", "short strongly upturned nose", "big bulbous fleshy nose tip",
            "crooked visibly bent nose")),
        ("mouth", (
            "very thin straight lips", "very thick protruding lips", "tiny pursed mouth", "very wide mouth",
            "strongly downturned mouth corners", "heavy hanging lower lip")),
        ("skin tone", (
            "very dark deep-brown skin", "very pale porcelain-white skin", "olive tan skin",
            "dark leathery weathered skin", "sallow yellowish skin", "reddish ruddy skin")),
        ("build", (
            "very obese face with a big double chin", "emaciated thin face with sunken cheeks", "stocky heavy-set face",
            "average face", "plump chubby baby-face", "lean wiry face with visible bones")),
        ("distinct mark", (
            "a large mole on the cheek", "heavy freckles", "deep old acne pits", "a visible scar across the brow",
            "big protruding ears", "a deep cleft chin", "very deep dimples", "dark heavy under-eye bags",
            "a large birthmark near the jaw", "a unibrow", "very bushy eyebrows")),
    )
    _FACE_STRIDES = (1, 3, 5, 3, 5, 5, 5, 7)

    def _unique_face_profile(index):
        parts = []
        for (label, values), stride in zip(_FACE_AXES, _FACE_STRIDES):
            parts.append(f"{label}: {values[(index * stride + stride // 2) % len(values)]}")
        return "; ".join(parts)

    def _assign_cast_face_profiles(characters):
        """Give each identity its own combination on every facial axis so faces cannot converge."""
        mapping = {}
        for character in characters or []:
            key = _identity_family_key(character)
            if key not in mapping:
                mapping[key] = _unique_face_profile(len(mapping))
        return mapping

    def _reference_family_key(path):
        stem = Path(path).stem
        stem = re.sub(r"(?:[-_ ]face)(?:[-_ ]side)?(?:[-_ ]?\d+)?$", "", stem, flags=re.I)
        stem = re.sub(r"[-_ ]\d+$", "", stem)
        return _identity_family_key({"name": stem})

    def _select_identity_reference_for_character(character):
        """Select newest existing face of the same person; user may override it."""
        _refresh_identity_refs()
        family = _identity_family_key(character)
        matches = [
            (label, path) for label, path in identity_ref_paths.items()
            if _reference_family_key(path) == family
        ]
        if not matches:
            identity_ref_path[0] = None
            return None
        label, path = max(matches, key=lambda item: item[1].stat().st_mtime)
        identity_ref_path[0] = path
        return path
    
    g["_new_log"] = _new_log
    
    def _expand_group_characters(characters):
        """Turn 'นายอ่ำ + นางแก้ว' into separate one-person face jobs."""
        expanded = []
        seen = set()
        for character in characters or []:
            if not isinstance(character, dict):
                continue
            original_name = str(character.get("name") or "").strip()
            names = [part.strip() for part in original_name.split("+") if part.strip()]
            for name in names:
                item = dict(character)
                item["name"] = name
                if len(names) > 1:
                    item["identity_group"] = name
                    item["reference_from"] = name
                    item["identity_master"] = True
                    role = str(item.get("role") or "").strip()
                    if role:
                        item["role"] = role.replace(original_name, name)
                key = (name.casefold(), str(item.get("variant") or "").strip().casefold())
                if key not in seen:
                    seen.add(key)
                    expanded.append(item)
        return expanded

    def _apply_story_face_character(character, selector=None, reference_override=None):
        name = str(character.get("name", "")).strip()
        prompt = _build_story_face_prompt_from_character(character)
        visible_condition = _condition_text(character)
        selected_condition[0] = visible_condition
        selected_body.update(name=name.casefold(), facts=_story_body_facts(character))
        selected_character_key[0] = _story_face_character_key(character)
        prompt = _append_condition_override(prompt, visible_condition)
        new_name_var.set(name)
        _set_face_prompt(prompt)
        _new_log(_builder_set_selection_lock(lock_g, "character", name))
        reference = Path(reference_override) if reference_override and Path(reference_override).is_file() else _select_identity_reference_for_character(character)
        if reference:
            identity_ref_path[0] = reference
        if reference:
            _new_log(f"[Select] ใช้หน้าเดิมของคนเดียวกัน: {reference.name}")
        else:
            _new_log("[Select] ยังไม่มีหน้าเดิมของคนนี้ — จะสร้างหน้าแรก")
        if selector is not None:
            selector.destroy()
    
    def _open_story_face_selector():
        import re as _re
        context = _load_ref_context()
        context_chars = _expand_group_characters(_extract_story_face_characters(context))
        # Select must never call GPT or wait for network. Dataset characters
        # come only from the last completed Analyze action.
        batch_chars = _expand_group_characters(_current_dataset_characters())
        raw_lines = []
        try:
            saved = str(batch_source_state.get("text") or "").strip()
            if saved:
                raw_lines = saved.split("\n")
        except Exception:
            pass
        # A single or double asterisk marks every named character on that row as main.
        starred_lines = [_l for _l in raw_lines if "*" in _l]
        for _c in batch_chars:
            _cn = str(_c.get("name","") or _c.get("display_name","")).strip()
            _base_cn = _re.sub(r"\s*คนที่\s*\d+$", "", _cn).strip()
            if _base_cn and any(_base_cn in _l for _l in starred_lines):
                _c["_important"] = True
        # Context: strip ** from names
        for _c in context_chars:
            _n = str(_c.get("name","")).strip()
            if "**" in _n:
                _c["_important"] = True
                _c["name"] = _c["name"].replace("**","").strip()
        if not context_chars and not batch_chars:
            _new_log("[Select] ยังไม่มีรายชื่อ — กด 📄 เลือกบท หรือใส่ ข้อมูลชุด ก่อน")
            return
        win = tk.Toplevel(root)
        win.title("Select character")
        win.configure(bg="#FFFFFF")
        win.geometry("580x520")
        win.transient(root)
        tab_bar = tk.Frame(win, bg="#FFFFFF")
        tab_bar.pack(fill="x", padx=14, pady=(14, 0))
        ctx_tab = tk.Button(tab_bar, text="From Context", bg="#2563EB", fg="white", relief="flat", bd=0, activebackground="#1D4ED8", padx=16, pady=8, font=(SNAPGEN_UI_FONT, 9, "bold"), cursor="hand2", command=lambda: None)
        ctx_tab.pack(side="left", padx=(0, 4))
        ds_tab = tk.Button(tab_bar, text="From Dataset", bg="#E5E7EB", fg="#111827", relief="flat", bd=0, activebackground="#D1D5DB", padx=16, pady=8, font=(SNAPGEN_UI_FONT, 9, "bold"), cursor="hand2", command=lambda: None)
        ds_tab.pack(side="left")
        cf = tk.Frame(win, bg="#FFFFFF")
        cf.pack(fill="both", expand=True, padx=14, pady=(10, 14))
        def _show_tab(tab):
            for w in cf.winfo_children(): w.destroy()
            if tab == "context":
                ctx_tab.config(bg="#2563EB",fg="white")
                ds_tab.config(bg="#E5E7EB",fg="#111827")
                chars = sorted(context_chars, key=lambda x: (0 if x.get("_important") else 1, str(x.get("name",""))))
            else:
                ctx_tab.config(bg="#E5E7EB",fg="#111827")
                ds_tab.config(bg="#2563EB",fg="white")
                chars = batch_chars
            if not chars: tk.Label(cf, text="Empty", bg="#FFFFFF",fg="#9CA3AF").pack(pady=40); return
            wrap = tk.Frame(cf, bg="#FFFFFF")
            wrap.pack(fill="both", expand=True)
            ca = tk.Canvas(wrap, bg="#FFFFFF", highlightthickness=0)
            sc = tk.Scrollbar(wrap, orient="vertical", command=ca.yview)
            inner = tk.Frame(ca, bg="#FFFFFF")
            inner.bind("<Configure>", lambda _e: ca.configure(scrollregion=ca.bbox("all")))
            ca.create_window((0, 0), window=inner, anchor="nw")
            ca.configure(yscrollcommand=sc.set)
            ca.pack(side="left", fill="both", expand=True)
            sc.pack(side="right", fill="y")
            for ch in chars:
                imp = ch.get("_important", False)
                n = str(ch.get("name","") or ch.get("display_name","") or "").strip()
                if tab == "context":
                    summ = " ".join(ch.get(k,"") for k in ("age","skin","hair") if ch.get(k))
                else:
                    variant = str(ch.get("variant", "") or "").strip()
                    age = str(ch.get("age","") or "").strip()
                    role = str(ch.get("role","") or "").strip()
                    summ = " | ".join(x for x in (variant, age, role) if x)
                lbl = n + ("  " + summ if summ else "")
                bg2 = "#FEF2F2" if imp else "#F9FAFB"
                fg2 = "#991B1B" if imp else "#111827"
                pre = "* " if imp else ""
                tk.Button(inner, text=pre+lbl, anchor="w",
                    command=lambda c=ch: (_apply_story_face_character(c, win), win.destroy()),
                    bg=bg2, fg=fg2, activebackground="#E0E7FF",
                    activeforeground=fg2, relief="flat", bd=0,
                    padx=12, pady=9, wraplength=500, justify="left",
                    font=(SNAPGEN_UI_FONT, 9), cursor="hand2").pack(fill="x", padx=4, pady=2)
        ctx_tab.config(command=lambda: _show_tab("context"))
        ds_tab.config(command=lambda: _show_tab("dataset"))
        # Story Dataset is the source for this page. Context remains available
        # as a separate tab, but must not replace this story's cast by default.
        _show_tab("dataset" if batch_chars else "context")
        try: win.grab_set()
        except Exception: pass
    new_select_btn.config(command=_open_story_face_selector)
    g["_open_story_face_selector"] = _open_story_face_selector
    g["_apply_story_face_character"] = _apply_story_face_character
    
    new_gallery = tk.LabelFrame(story_character_page, text="แกลเลอรี", bg="#FAFAF7", fg="#1A1A1A", padx=8, pady=6)
    new_gallery.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    new_gallery_canvas = tk.Canvas(new_gallery, bg="#FAFAF7", highlightthickness=0)
    new_gallery_scroll = tk.Scrollbar(new_gallery, orient="vertical", command=new_gallery_canvas.yview)
    new_gallery_canvas.configure(yscrollcommand=new_gallery_scroll.set)
    new_gallery_inner = tk.Frame(new_gallery_canvas, bg="#FAFAF7")
    _sg_window = new_gallery_canvas.create_window((0, 0), window=new_gallery_inner, anchor="nw")
    new_gallery_canvas.pack(side="left", fill="both", expand=True)
    new_gallery_scroll.pack(side="right", fill="y")
    g["new_gallery_inner"] = new_gallery_inner
    new_gallery_images = []
    g["new_gallery_images"] = new_gallery_images
    gallery_state_path = Path(BASE) / "meta" / "story_face_gallery.json"
    gallery_paths = []
    new_gallery_cards = []
    new_gallery_columns = 5
    for column_index in range(new_gallery_columns):
        new_gallery_inner.grid_columnconfigure(column_index, weight=1, uniform="story_face_gallery")

    def _sg_sync(_e=None):
        new_gallery_canvas.configure(scrollregion=new_gallery_canvas.bbox("all") or (0, 0, 0, 0))
        try:
            new_gallery_canvas.itemconfigure(_sg_window, width=max(new_gallery_canvas.winfo_width() - 4, 1))
        except Exception:
            pass

    def _sg_on_mousewheel(event):
        try:
            if not new_gallery_canvas.winfo_exists():
                return
            first, last = new_gallery_canvas.yview()
            if float(last) - float(first) >= 0.999:
                return
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return
            new_gallery_canvas.yview_scroll(int(-1 * (delta / 120)), "units")
            return "break"
        except Exception:
            return

    def _sg_bind_wheel(widget):
        try:
            widget.bind("<Enter>", lambda _e: new_gallery_canvas.bind_all("<MouseWheel>", _sg_on_mousewheel), add="+")
            widget.bind("<Leave>", lambda _e: new_gallery_canvas.unbind_all("<MouseWheel>"), add="+")
            widget.bind("<MouseWheel>", _sg_on_mousewheel, add="+")
        except Exception:
            pass

    new_gallery_inner.bind("<Configure>", _sg_sync)
    new_gallery_canvas.bind("<Configure>", _sg_sync)
    for _w in (new_gallery, new_gallery_canvas, new_gallery_inner, new_gallery_scroll):
        _sg_bind_wheel(_w)
    
    def _save_story_gallery():
        gallery_state_path.parent.mkdir(parents=True, exist_ok=True)
        gallery_state_path.write_text(
            json.dumps({"paths": gallery_paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _relayout_story_gallery():
        for index, card in enumerate(new_gallery_cards):
            row, column = divmod(index, new_gallery_columns)
            card.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
        try:
            new_gallery_canvas.yview_moveto(0)
            root.after_idle(_sg_sync)
        except Exception:
            pass

    def _new_gallery_add(path, prepend=True, send_to_prop=False, prop_name="", prop_type="", persist=True):
        path = str(Path(path))
        if persist and path not in gallery_paths:
            gallery_paths.insert(0 if prepend else len(gallery_paths), path)
            _save_story_gallery()
        card = tk.Frame(
            new_gallery_inner, bg="#FFFFFF", height=180,
            highlightthickness=1, highlightbackground="#E5E7EB",
        )
        card.pack_propagate(False)
        # Keep the whole card (preview + filename + Open button) inside the
        # gallery height. A tall preview hides the controls behind the footer.
        thumb_box = tk.Frame(card, bg="#FFFFFF", width=140, height=105)
        thumb_box.pack(fill="x", padx=6, pady=(5, 1))
        thumb_box.pack_propagate(False)
        try:
            from PIL import Image, ImageTk
            image = Image.open(path)
            image.thumbnail((130, 98), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            new_gallery_images.append(photo)
            tk.Label(thumb_box, image=photo, bg="#FFFFFF").pack(expand=True)
        except Exception:
            tk.Label(thumb_box, text="ไม่มี preview", bg="#FFFFFF", fg="#9CA3AF", wraplength=150).pack(expand=True)
        display_name = os.path.basename(path)
        if len(display_name) > 30:
            display_name = f"{display_name[:27]}..."
        tk.Label(
            card, text=display_name, bg="#FFFFFF", fg="#111",
            anchor="center", height=1,
        ).pack(fill="x", padx=6, pady=(1, 2))
        def open_image(p=path):
            try:
                os.startfile(os.path.normpath(str(p)))
            except Exception as exc:
                _new_log(f"เปิดรูปไม่สำเร็จ: {exc}")

        button_row = tk.Frame(card, bg="#FFFFFF")
        button_row.pack(pady=(0, 6))
        tk.Button(button_row, text="🖼 เปิดรูป", command=open_image).pack(side="left", padx=2)
        def send_prop(p=path, name=prop_name, garment_type=prop_type):
            receiver = g.get("receive_story_prop")
            if not callable(receiver):
                _new_log("[ส่งไป Prop] ระบบ Prop ยังไม่พร้อม — ปิดแล้วเปิดโปรแกรมใหม่")
                return
            if receiver(p, name or Path(p).stem, garment_type):
                _new_log(f"[ส่งไป Prop] {Path(p).name}")
        tk.Button(
            button_row, text="📦 ส่งไป Prop", command=send_prop,
            bg="#2563EB", fg="white", relief="flat", padx=8, pady=3,
            font=(SNAPGEN_UI_FONT, 8, "bold"),
        ).pack(side="left", padx=2)
        new_gallery_cards.insert(0 if prepend else len(new_gallery_cards), card)
        _relayout_story_gallery()
        # Keep the new output in the tiny identity index without rescanning
        # the whole folder or touching disk metadata for every gallery card.
        try:
            identity_ref_paths[Path(path).name] = Path(path)
        except Exception:
            pass
    g["_new_gallery_add"] = _new_gallery_add

    def _clear_story_gallery():
        for child in new_gallery_inner.winfo_children():
            child.destroy()
        new_gallery_images.clear()
        new_gallery_cards.clear()
        gallery_paths.clear()
        _save_story_gallery()
        _new_log("ล้าง Gallery แล้ว — ไฟล์รูปจริงยังอยู่")

    def _restore_story_gallery():
        try:
            payload = json.loads(gallery_state_path.read_text(encoding="utf-8"))
            saved = payload.get("paths") if isinstance(payload, dict) else []
        except FileNotFoundError:
            # One-time migration for installations that predate Gallery state.
            saved = [
                str(path) for path in sorted(
                    Path(export_story_face_dir).glob("*"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
        except (json.JSONDecodeError, OSError):
            saved = []
        gallery_paths[:] = [str(Path(path)) for path in saved if Path(path).is_file()]
        _save_story_gallery()
        pending = iter(gallery_paths)
        def add_next():
            try:
                path = next(pending)
            except StopIteration:
                return
            _new_gallery_add(path, prepend=False, persist=False)
            root.after(5, add_next)
        add_next()

    root.after(50, _restore_story_gallery)
    
    def _set_story_face_running(running):
        story_face_running[0] = bool(running)
        try:
            new_make_btn.config(
                state=(tk.DISABLED if running else tk.NORMAL),
                text=("กำลังสร้าง Face + Body..." if running else "🎭 สร้าง Face + Body"),
            )
            new_select_btn.config(state=(tk.DISABLED if running else tk.NORMAL))
        except Exception:
            pass
    
    def generate_story_face():
        name = new_name_var.get().strip()
        prompt = _get_face_prompt()
        if not name:
            _new_log("[สร้าง Face] ใส่ชื่อหรือกด Select ก่อน")
            return
        if not prompt:
            _new_log("[สร้าง Face] ไม่มี Prompt ใบหน้า")
            return
        if len([part for part in name.split("+") if part.strip()]) > 1:
            _new_log("[สร้าง Face] ชื่อนี้มีหลายคน กด Select แล้วเลือกสร้างทีละคน")
            return
        if story_face_running[0]:
            _new_log("[สร้าง Face] กำลังสร้างอยู่ — รอให้งานเดิมเสร็จก่อน")
            return
        _new_log(_builder_set_selection_lock(lock_g, "character", name))
        # Inject age from dropdown (วัย). "อัตโนมัติ" keeps story/character age instead of a fixed number.
        age_label = str(new_age_var.get() or "อัตโนมัติ").strip()
        age_val = _FACE_AGE_MAP.get(age_label, "35")
        if age_label == "อัตโนมัติ" or age_val == "auto":
            prompt = (
                prompt
                .replace(", {age} years old", ", age matching the character story identity automatically")
                .replace("{age} years old", "age matching the character story identity automatically")
                .replace("{age}", "auto from character identity")
            )
        else:
            prompt = prompt.replace("{age}", age_val)
        prompt = prompt.replace("{name}", name)
        if not identity_ref_path[0]:
            # A new person: give the same unique geometry the batch would give.
            order = []
            for item in _expand_group_characters(_current_dataset_characters()):
                key = _identity_family_key(item)
                if key not in order:
                    order.append(key)
            key = _identity_family_key({"name": name})
            index = order.index(key) if key in order else int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % 997
            prompt += (
                "\n\nMANDATORY UNIQUE FACE GEOMETRY (every item must be clearly visible; this person must look obviously "
                "different from a generic attractive face and from any other character; cast like a real character actor with ordinary, imperfect, memorable features — NOT a pretty model; exaggerate these traits while staying photorealistic): " + _unique_face_profile(index)
            )
        body_base_prompt = prompt
        prompt, identity_images = _identity_reference_payload(prompt)
        _set_story_face_running(True)
        identity_note = f" • อ้างอิงหน้าเดิม: {Path(identity_ref_path[0]).name}" if identity_images else ""
        _new_log(f"[สร้าง Face] เริ่มงาน Face + Body: {name} (วัย: {age_label}){identity_note}")
    
        def worker():
            try:
                _ensure_story_face_history()
                story_face_dir = export_story_face_dir
                story_face_dir.mkdir(parents=True, exist_ok=True)
                lock = globals().get("_bridge_queue_lock")

                def request_images():
                    if "_wait_bridge_free" in globals():
                        globals()["_wait_bridge_free"](log_fn=_new_log)
                    # Select already embeds only that character's details in
                    # `prompt`; manual input contains only what the user typed.
                    # Do not append the whole story context in either case.
                    # Keep the structured face description intact; the generic
                    # rewriter can remove the exact geometry that distinguishes
                    # this character from earlier faces in the same history.
                    refined_prompt = prompt
                    refined_prompt = _append_condition_override(refined_prompt, selected_condition[0])

                    # Branch Body before face identity is re-applied. Headshot 3
                    # needs the character's body proportions, not face or outfit identity.
                    body_facts = selected_body["facts"] if selected_body["name"] == name.casefold() else ""
                    if age_label != "อัตโนมัติ":
                        body_facts = f"selected age group: {age_label}; {body_facts}".rstrip("; ")
                    body_prompt = _apply_story_body_reference(body_base_prompt, body_facts)

                    final_prompt = refined_prompt
                    if identity_images and "IDENTITY REFERENCE — MANDATORY:" not in final_prompt:
                        final_prompt = str(final_prompt).rstrip() + identity_lock
                    final_prompt = _force_neutral_face_prompt(
                        _apply_final_face_lighting_lock(final_prompt)
                    )
                    final_prompt = _apply_face_age_override(final_prompt, age_label)
                    final_prompt = _apply_clean_face_lock(final_prompt)

                    front_payload = _build_story_face_payload(final_prompt)
                    _face_payload_history(front_payload, fresh=not identity_images)
                    if identity_images:
                        front_payload["images"] = identity_images
                    front = g["_do_image_request"](
                        front_payload, is_edit=bool(identity_images), prompt=final_prompt,
                        name_hint=f"{name}-face", raw_prompt=prompt,
                        output_dir=str(story_face_dir), save_sidecar=False,
                    )
                    root.after(0, lambda out=front: _publish_face_output(out, "หน้าตรง"))

                    side_prompt, side_images = _identity_reference_payload(final_prompt, front)
                    side_prompt = _apply_story_face_view(side_prompt, "side")
                    side_payload = _build_story_face_payload(side_prompt)
                    _face_payload_history(side_payload)
                    side_payload["images"] = side_images
                    side = g["_do_image_request"](
                        side_payload, is_edit=True, prompt=side_prompt,
                        name_hint=f"{name}-face-side", raw_prompt=prompt,
                        output_dir=str(story_face_dir), save_sidecar=False,
                    )

                    body_payload = _build_story_face_payload(body_prompt)
                    _face_payload_history(body_payload, fresh=not identity_images)
                    body = g["_do_image_request"](
                        body_payload, is_edit=False, prompt=body_prompt,
                        name_hint=f"{name}-body", raw_prompt=prompt,
                        output_dir=str(story_face_dir), save_sidecar=False,
                    )
                    return side, body

                def _publish_face_output(out, view_label, finished=False):
                    try:
                        _new_gallery_add(out, True)
                        _new_log(f"✓ สร้าง{view_label}สำเร็จ: {out}")
                        if finished:
                            _notify_done()
                    except Exception as gallery_error:
                        _new_log(f"ERROR gallery: {gallery_error}")
                    if finished:
                        _set_story_face_running(False)

                if lock:
                    with lock:
                        side_out, body_out = request_images()
                else:
                    side_out, body_out = request_images()
                root.after(0, lambda out=side_out: _publish_face_output(out, "ด้านข้าง"))
                root.after(0, lambda out=body_out: _publish_face_output(out, "Body สำหรับ Headshot 3", True))
            except Exception as error:
                def on_error(error=error):
                    _new_log(f"ERROR: {error}")
                    _set_story_face_running(False)
                root.after(0, on_error)
        threading.Thread(target=worker, daemon=True).start()
    g["generate_story_face"] = generate_story_face
    
    def _new_action_waiting(label):
        _new_log(f"[{label}] รอกำหนดการทำงานของปุ่ม")
    
    # ── Auto Face: generate faces for all characters from Prompt Context ──
    auto_face_stop = [False]
    def _persist_batch_state():
        """Keep the latest batch text and analyzed cast across app restarts."""
        payload = {
            "text": str(batch_source_state.get("text") or ""),
            "source": str(batch_cache.get("source") or ""),
            "characters": list(batch_cache.get("characters") or []),
            "design_page": str(batch_cache.get("design_page") or ""),
        }
        batch_save_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = batch_save_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(batch_save_path)

    def _clean_json_response(text):
        text = str(text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = text.replace("```", "").strip()
        try:
            return json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise

    def _fallback_batch_characters(text):
        """Deterministic cast coverage from every numbered source row."""
        source_text = str(text or "")
        matches = list(re.finditer(r"(?m)^\s*\d+(?:\.\d+)*[.)]?\s*", source_text))
        rows = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source_text)
            row = re.sub(r"\s+", " ", source_text[match.end():end]).strip()
            if row:
                rows.append(row)
        out = []
        for row in rows:
            important = "**" in row
            clean = re.sub(r"[\*\"]+", "", row).strip(" :-–—")
            lowered = clean.casefold()
            non_person_markers = (
                "รูปถ่าย", "prop", "พร็อพ", "พ็อป", "สิ่งของ", "สถานที่",
                "อาคาร", "ห้องนอน", "ห้องน้ำ",
                "สัตว์", "สุนัข", "แมว", "เอกสาร",
            )
            non_person_prefixes = (
                "บ้าน", "เรือน", "ห้อง", "รถ", "ถนน", "ป่า", "วัด", "โรงเรียน",
                "โรงพยาบาล", "ตลาด", "ร้าน", "โต๊ะ", "เก้าอี้", "โทรศัพท์", "จดหมาย",
            )
            if (
                not clean
                or any(marker in lowered for marker in non_person_markers)
                or lowered.startswith(non_person_prefixes)
            ):
                continue
            # A row can contain one person plus an object, e.g. widower + car.
            # Drop only the object tail, never the person before it.
            clean = re.split(r"\s*\+\s*(?:รถ|บ้าน|ห้อง|เอกสาร|สัตว์)", clean, maxsplit=1)[0].strip()
            if "ชาวบ้านหลายคน" in clean:
                names = ["ชาวบ้านหญิงขี้นินทา"]
                variants = [""]
            else:
                count_match = re.search(r"(.+?)\s+(\d+)\s*คน", clean)
                if count_match and "ลูกจ้าง" in clean:
                    count = max(1, min(10, int(count_match.group(2))))
                    base_name = count_match.group(1).strip(" :-–—")
                    names = [f"{base_name} คนที่ {number}" for number in range(1, count + 1)]
                    variants = [""]
                else:
                    before_variants, separator, variant_text = clean.partition("//")
                    name = re.split(
                        r"\s*(?:\(|\sวัย|\sตอนวัย|\sมีอายุ|\sที่อยู่|\sลูกบุญธรรม|\sลูกสาว|\sเมียคน|\sภรรยา|\sน้องสาว)",
                        before_variants,
                        maxsplit=1,
                    )[0].strip(" :-–—")
                    if not name:
                        name = before_variants[:50].strip()
                    names = [
                        item.strip(" :-–—")
                        for item in re.split(r"\s*\+\s*", name)
                        if item.strip(" :-–—")
                    ]
                    variants = [
                        item.strip(" :-–—") for item in re.split(r"\s*\+\s*", variant_text)
                        if item.strip(" :-–—")
                    ] if separator else [""]
            for name in names:
                for variant in variants:
                    out.append({
                        "name": name,
                        "variant": variant,
                        "identity_group": name,
                        "identity_master": False,
                        "reference_from": name,
                        "age": "",
                        "role": clean,
                        "life_condition": variant,
                        "expression": "",
                        "appearance": "",
                        "face_design": "",
                        "skin_detail": "",
                        "hair": "",
                        "clothes": clean,
                        "source": clean,
                        "_important": important,
                    })
        seen_groups = set()
        for item in out:
            group = str(item.get("identity_group") or "").casefold()
            is_master = group not in seen_groups
            item["identity_master"] = is_master
            item["reference_from"] = "" if is_master else item["identity_group"]
            seen_groups.add(group)
        return out

    def _current_dataset_characters():
        """Return cached details without allowing old cache to hide source rows."""
        cached = [item for item in (batch_cache.get("characters") or []) if isinstance(item, dict)]
        current_source = str(batch_source_state.get("text") or "").strip()
        if cached and str(batch_cache.get("source") or "").strip() == current_source:
            return cached
        required = _fallback_batch_characters(current_source)
        if not required:
            return cached

        def comparable(value):
            return re.sub(r"[^0-9A-Za-zก-๙]", "", str(value or "")).casefold()

        def variant_key(value):
            return re.sub(r"(?:ตอน|วัย|ช่วง|กำลัง)", "", comparable(value))

        remaining = list(cached)
        complete = []
        for expected in required:
            expected_name = comparable(expected.get("identity_group") or expected.get("name"))
            expected_variant = variant_key(expected.get("variant"))
            match = None
            for candidate in remaining:
                candidate_name = comparable(candidate.get("identity_group") or candidate.get("name"))
                if candidate_name != expected_name:
                    continue
                candidate_variant = variant_key(candidate.get("variant"))
                if not expected_variant or not candidate_variant or expected_variant in candidate_variant or candidate_variant in expected_variant:
                    match = candidate
                    break
            if match is None:
                complete.append(expected)
                continue
            merged = dict(expected)
            merged.update({key: value for key, value in match.items() if value not in (None, "")})
            merged["name"] = expected["name"]
            if expected.get("variant"):
                merged["variant"] = expected["variant"]
            merged["identity_group"] = expected["identity_group"]
            complete.append(merged)
            remaining.remove(match)
        return complete + remaining

    def _parse_batch_characters_via_bridge(text, force=False):
        """Turn free-form numbered data into one face job per identity/age."""
        source = str(text or "").strip()
        if not source:
            return []
        cached = list(batch_cache.get("characters") or [])
        if (not force and
            batch_cache["source"] == source
            and cached
            and all(isinstance(item, dict) and item.get("identity_group") for item in cached)
        ):
            return cached

        system = (
            "คุณเป็นตัวแยกรายการตัวละครสำหรับสร้างภาพใบหน้า ไม่ได้สร้างภาพ "
            "ก่อนแยกรายคน ให้เขียน Character Bible รวมหนึ่งหน้าโดยมองนักแสดงทุกคนพร้อมกัน "
            "และออกแบบให้แต่ละคนแตกต่างกันชัดเจนด้านรูปหน้า สัดส่วนตา คิ้ว จมูก ปาก กราม "
            "โหนกแก้ม สีผิว พื้นผิวผิว รอยตำหนิ และอายุ ห้ามใช้ใบหน้าต้นแบบซ้ำกัน. "
            "ตอบ JSON เท่านั้นในรูป {\"design_page\":\"...\",\"subjects\":[...]}. "
            "design_page ต้องเป็นแผนรวมทั้งเรื่องที่อธิบายความแตกต่างของทุกใบหน้าในหน้าเดียว. "
            "แต่ละ subject ต้องมี name, variant, identity_group, identity_master, reference_from, age, gender, "
            "body_build, height, weight, role, life_condition, expression, appearance, face_design, face_profile, skin_detail, "
            "hair, clothes, injury_look, source. gender ต้องเป็น male หรือ female ตามข้อมูล ห้ามปล่อยว่างเมื่อระบุเพศได้. "
            "injury_look: ถ้าบทระบุว่าตัวละครบาดเจ็บ มีเลือด แผล รอยฟกช้ำ หรือคราบ ให้เก็บไว้เฉพาะช่องนี้ "
            "โดยเขียนเป็นการแต่งหน้าเอฟเฟกต์ภาพยนตร์ที่ไม่รุนแรงแทนของจริง เช่น คราบสีแดงเข้มคล้ายซอสมะเขือเทศที่แห้งแล้วที่มุมปาก "
            "รอยแต่งหน้าโทนม่วงอมน้ำเงินจางๆ ใต้ตา รอยเปื้อนดินมอมแมม ผ้าพันแผลสะอาด; ต้องยังดูออกว่าบาดเจ็บตามบท "
            "แต่ห้ามใช้คำว่า เลือด แผลเปิด บาดแผลลึก เนื้อ กระดูก หรือรายละเอียดน่ากลัว และห้ามใส่เรื่องบาดเจ็บในช่องอื่น "
            "(appearance, skin_detail, life_condition, face_design ต้องเป็นสภาพปกติ). "
            "ถ้าคนเดียวกันมีทั้งสภาพปกติและสภาพบาดเจ็บ ให้สภาพปกติเป็น identity_master และแยกสภาพบาดเจ็บเป็น subject variant "
            "'สภาพบาดเจ็บ' ที่ reference_from ตัวหลัก; ถ้าไม่บาดเจ็บให้ injury_look เป็นค่าว่าง. "
            "identity_group คือรหัสคนจริงคนเดียวกัน ใช้ชื่อหลักสั้นคงที่ เช่น นายพยง; ทุกวัยและทุกอารมณ์ของคนเดียวกัน "
            "ต้องใช้ identity_group เดียวกัน. identity_master เป็น boolean: true ได้เพียงหนึ่ง subject ต่อ identity_group "
            "โดยเลือกวัย/สภาพปกติที่เห็นใบหน้าชัดที่สุดเป็นรูปหลัก และจัด subject ตัวหลักไว้ก่อนตัวแปรอื่น. "
            "reference_from ของตัวหลักเป็นค่าว่าง; subject วัยหรืออารมณ์อื่นต้องใส่ identity_group ของตัวหลักใน reference_from. "
            "life_condition ต้องเก็บเฉพาะสภาพชีวิต/สุขภาพทางกายที่มองเห็น เช่น อดนอน ป่วย ผอมลง หรือผ่านงานหนัก โดยไม่ใส่อารมณ์. "
            "expression ต้องเป็นค่าว่างเสมอ เพราะภาพอ้างอิงใบหน้าต้องใช้สีหน้าธรรมดา สงบ ปากปิด ไม่โกรธ ไม่เศร้า ไม่กลัว และไม่ยิ้ม. "
            "หนึ่ง subject ต้องแทนมนุษย์หนึ่งคนในหนึ่งวัยเท่านั้น ห้ามรวมหลายคนหรือหลายวัยไว้ใน subject เดียว. "
            "ถ้าข้อความกล่าวถึงลูกชายและลูกสาว ต้องแยกเป็นคนละ subject เสมอ แม้อายุหรือบทบาทเหมือนกัน. "
            "ถ้าคนเดียวกันปรากฏหลายวัย ให้สร้างหนึ่ง subject ต่อวัย ใช้ identity_group เดียวกัน และมี identity_master=true เพียงวัยเดียว. "
            "วัยที่เหลือต้องใส่ reference_from เท่ากับ identity_group เพื่อใช้ภาพวัยหลักเป็น Identity Ref. "
            "รักษาลำดับบุคคลตามข้อมูลต้นฉบับ และรอบนี้วิเคราะห์อย่างเดียว ห้ามสร้างภาพ. "
            "รับเฉพาะมนุษย์ที่ควรมีภาพใบหน้าตัวละครหนึ่งคนต่อหนึ่ง subject เท่านั้น. "
            "ตัด Prop, สิ่งของ, ยานพาหนะ, สถานที่, อาคาร, ห้อง, สัตว์, เอกสาร, "
            "รูปถ่ายกลุ่ม และรายการที่ไม่ใช่บุคคลออกทั้งหมด. "
            "แยกคนละ identity เป็นคนละ subject. ตัวละครชื่อเดิมที่ต่างช่วงวัยให้แยกเป็นคนละ subject "
            "และใส่ variant เช่น วัยเด็ก/วัยรุ่น/วัยหนุ่ม/วัยกลางคน. "
            "ถ้ามีเครื่องหมาย + ที่หมายถึงคนละคน เช่น ลูกชาย + ลูกสะใภ้ หรือ วิชัย + ภรรยา "
            "ให้แยกคนละ subject. ถ้า + เป็นเพียงหลายชุดของคนเดิม ไม่ต้องสร้าง identity ใหม่ "
            "แต่รวมชุดไว้ใน clothes. ข้ามรายการที่เป็นรูปถ่ายกลุ่มหรือวัตถุและไม่ได้เพิ่มคนใหม่. "
            "ห้ามแต่งชื่อจริงให้คนที่บทไม่ได้ให้ชื่อ ให้ใช้ชื่อบทบาท เช่น ภรรยาของวิชัย. "
            "ต้องตรวจทุกข้อเลขในต้นฉบับก่อนตอบ ห้ามข้ามข้อที่เป็นมนุษย์. กลุ่มชาวบ้านให้สร้างตัวแทนหนึ่งคน. "
            "ข้อความที่ระบุจำนวน เช่น ลูกจ้าง 2 คน ต้องคืน 2 subjects แยกกัน. ถ้าแถวมีคน + สิ่งของ ให้เก็บคนและตัดเฉพาะสิ่งของ. "
            "รักษาชื่อไทยและข้อมูลยุค/เรื่องจากหัวข้อไว้ใน role/source. ห้ามอธิบายนอก JSON."
        )
        user = (
            "แยกข้อมูลชุดนี้เป็นงานสร้างใบหน้าทั้งหมด:\n\n"
            + source[:12000]
        )
        imgmod = g.get("_imgmod")
        if imgmod is None:
            raise RuntimeError("ระบบประวัติแชทหน้า Face ยังไม่พร้อม")

        def analyze(*args, **kwargs):
            return imgmod.analyze_story_face_dataset(
                *args, conversation_state=_face_conversation(), conversation_save_fn=_save_face_store, **kwargs)
        try:
            result = analyze(
                _story_dataset_title(source),
                source,
                system + "\n\n" + user,
                log_fn=_new_log,
            )
            content = str((result or {}).get("summary") or "")
            parsed = _clean_json_response(content)
            raw_subjects = parsed.get("subjects") if isinstance(parsed, dict) else None
            if not isinstance(raw_subjects, list):
                raise RuntimeError("GPT ไม่คืน subjects")
            subjects = []
            seen = set()
            for item in raw_subjects:
                if not isinstance(item, dict):
                    continue
                normalized = {
                    key: str(item.get(key) or "").strip()
                    for key in (
                        "name", "variant", "identity_group", "reference_from", "age", "gender", "body_build", "height", "weight",
                        "role", "life_condition", "expression", "appearance", "face_design", "face_profile", "skin_detail",
                        "hair", "clothes", "injury_look", "source",
                    )
                }
                normalized["expression"] = ""
                raw_master = item.get("identity_master", False)
                normalized["identity_master"] = (
                    raw_master is True
                    or str(raw_master).strip().casefold() in {"true", "1", "yes", "ใช่", "หลัก", "main", "master"}
                )
                if not normalized["name"]:
                    continue
                if not normalized["identity_group"]:
                    normalized["identity_group"] = _identity_family_key(normalized)
                if normalized["identity_master"]:
                    normalized["reference_from"] = ""
                elif not normalized["reference_from"]:
                    normalized["reference_from"] = normalized["identity_group"]
                key = (
                    normalized["name"].casefold(),
                    normalized["variant"].casefold(),
                    normalized["age"].casefold(),
                )
                if key in seen:
                    continue
                seen.add(key)
                subjects.append(normalized)
            if not subjects:
                raise RuntimeError("ไม่พบรายชื่อตัวละครจากข้อมูลชุด")
            # Ensure exactly one master appears first in every identity group.
            grouped = {}
            group_order = []
            for subject in subjects:
                group = subject["identity_group"].casefold()
                if group not in grouped:
                    grouped[group] = []
                    group_order.append(group)
                grouped[group].append(subject)
            subjects = []
            for group in group_order:
                members = grouped[group]
                masters = [item for item in members if item["identity_master"]]
                master = masters[0] if masters else members[0]
                for item in members:
                    item["identity_master"] = item is master
                    item["reference_from"] = "" if item is master else item["identity_group"]
                subjects.extend([master] + [item for item in members if item is not master])
            design_page = str(parsed.get("design_page") or "").strip()
            if not design_page:
                design_page = "Character Bible รวม: " + "; ".join(
                    f"{item['name']} {item.get('variant', '')}: {item.get('face_design') or item.get('appearance') or item.get('role')}"
                    for item in subjects
                )
        except Exception as error:
            _new_log(f"[ข้อมูลชุด] GPT แยกไม่สำเร็จ ใช้รายการเลขแทน: {error}")
            subjects = _fallback_batch_characters(source)
            design_page = (
                "Character Bible รวมจากรายการต้นฉบับ: ตัวละครแต่ละคนต้องมีโครงหน้า "
                "ตา คิ้ว จมูก ปาก กราม สีผิว พื้นผิว และริ้วรอยแตกต่างกันชัดเจน. "
                + source[:1600]
            )
        batch_cache["source"] = source
        batch_cache["characters"] = list(subjects)
        batch_cache["design_page"] = design_page
        return subjects

    def _three_d_age_reserve(character):
        text = " ".join(str(character.get(key) or "") for key in ("variant", "age"))
        if "วัยกลางคน" in text and "แก่" in text:
            return "3D AGE: visibly 58–65, never under 55; hooded lids, deep folds, jowls, soft jaw, gray hair. "
        return ""

    def _batch_face_prompt(character, overview, face_profile=""):
        name = str(character.get("name") or "").strip()
        variant = str(character.get("variant") or "").strip()
        age = str(character.get("age") or "").strip()
        role = str(character.get("role") or "").strip()
        life_condition = str(character.get("life_condition") or "").strip()
        # Acting emotion belongs in video prompts, not in a reusable face asset.
        expression = ""
        appearance = str(character.get("appearance") or "").strip()
        face_design = str(character.get("face_design") or "").strip()
        face_profile = str(face_profile or character.get("face_profile") or "").strip()
        skin_detail = str(character.get("skin_detail") or "").strip()
        hair = str(character.get("hair") or "").strip()
        clothes = str(character.get("clothes") or "").strip()
        injury_look = soften_injury_text(str(character.get("injury_look") or "").strip())
        identity = name + (f" ({variant})" if variant else "")
        identity_group = str(character.get("identity_group") or _identity_family_key(character)).strip()
        identity_role = "MAIN IDENTITY MASTER" if character.get("identity_master") else f"IDENTITY VARIANT; REFERENCE FROM: {character.get('reference_from') or identity_group}"
        details = "; ".join(
            value for value in (
                f"age: {age}" if age else "",
                f"role: {role}" if role else "",
                f"VISIBLE LIFE CONDITION — REQUIRED: {life_condition}" if life_condition else "",
                f"VISIBLE EXPRESSION — REQUIRED: {expression}" if expression else "",
                f"appearance: {appearance}" if appearance else "",
                f"face design: {face_design}" if face_design else "",
                (f"MANDATORY UNIQUE FACE GEOMETRY (every item must be clearly visible; this person must look "
                 f"obviously different from a generic attractive face and from any other character; cast him/her like a real character actor with ordinary, imperfect, memorable features — NOT a pretty model, NOT an idealized average face, exaggerate these traits while staying photorealistic): {face_profile}"
                 if face_profile else ""),
                f"skin detail: {skin_detail}" if skin_detail else "",
                f"hair identity: {hair}" if hair else "",
                f"clothes: {clothes}" if clothes else "",
                (f"STORY MAKEUP EFFECT (non-graphic film makeup for the story, not a real injury; keep it visible): {injury_look}"
                 if injury_look else ""),
            ) if value
        )
        details = soften_injury_text(details)
        age_for_3d = _three_d_age_reserve(character)
        rules = (
            age_for_3d
            +
            "สร้างเพียงคนเป้าหมายหนึ่งคน. MANDATORY UNIQUE FACE GEOMETRY เป็นข้อกำหนดหลักสุดของใบหน้านี้ "
            "ห้ามเปลี่ยนกลับเป็นหน้าแม่แบบกลางและห้ามใช้โครงหน้าเดียวกับตัวละครอื่น. "
            "กราม โหนกแก้ม สีผิวและตำหนิ ห้ามใช้หน้าแม่แบบซ้ำ. สภาพชีวิต สุขภาพ ความโทรม น้ำหนัก ริ้วรอยและอารมณ์ "
            "ที่ระบุใน TARGET DETAILS ต้องเห็นชัดและมีสิทธิ์เปลี่ยนจากรูปหลัก โดยยังดูเป็นคนเดิม. close-up head-and-shoulders, "
            "full front-facing, centered, looking straight at camera. Use an ordinary calm neutral expression "
            "regardless of story emotion. Mouth fully closed with relaxed closed lips; "
            "absolutely no visible teeth, no open mouth, no smile. Forehead, cheeks, hairline and both ears visible. "
            "Hair fully pulled and secured behind head; no bangs or loose strands over forehead, temples, eyebrows, "
            "cheeks, jawline or ears. Age-accurate unretouched skin microdetail: visible pores, fine lines, wrinkles, "
            "crow's-feet, nasolabial folds, spots, freckles, moles, scars and uneven texture where appropriate. "
            "Older faces show pronounced authentic age lines. No beauty filter, airbrushing, waxy, porcelain, plastic "
            "or excessively smooth skin. Photorealistic Thai identity, color-accurate natural skin, neutral light-gray "
            "background. Shadowless calibrated white studio light at D55: identical large softboxes symmetrically left "
            "and right with equal power plus centered fill; both sides of face equal brightness and color, uniform "
            "exposure forehead to neck. No yellow, orange, warm, sepia, green or blue cast; no side, rim, back, dramatic "
            "or cinematic light, no dark half of face, no collage, grid or extra person. Tack-sharp 85mm micro-focus with high local contrast, crisp pores and skin texture, no soft blur, no diffusion filter, no plastic smoothing, 3:4 portrait."
        )
        head = (
            f"สร้างรูปภาพใบหน้าตัวละครไทยหนึ่งคนเท่านั้น TARGET CHARACTER: {identity}. "
            f"IDENTITY GROUP: {identity_group}. IDENTITY ROLE: {identity_role}. "
            f"TARGET DETAILS: {details}. "
            "The previous generated faces in this same chat are unrelated people. Do not continue, imitate, or average their facial appearance. "
        )
        return head.rstrip(" ,;.") + ". " + rules
    
    def _extract_auto_face_characters(text):
        """Extract full character objects from prompt_ref_context JSON."""
        try:
            import json as _j
            ctx = _j.loads(text)
            chars = ctx.get("characters", []) if isinstance(ctx, dict) else []
            out = []
            seen = set()
            for c in chars:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("name", "")).strip()
                if name and name not in seen:
                    seen.add(name)
                    out.append(c)
            return out
        except Exception:
            return []
    
    def _set_auto_face_button_running(running):
        btn = new_auto_btn
        try:
            if running:
                btn.config(text="⏹ หยุด", command=stop_auto_face, state=tk.NORMAL, bg="#DC2626", activebackground="#B91C1C")
            else:
                btn.config(text="📋 ข้อมูลชุด", command=_open_story_face_batch_dialog, state=tk.NORMAL, bg="#0EA5E9", activebackground="#0284C7")
        except Exception:
            pass
    
    def stop_auto_face():
        auto_face_stop[0] = True
        _new_log("[auto-face] ขอหยุด — จะหยุดหลักรูปที่กำลังสร้างเสร็จ")
    
    def generate_auto_face():
        if auto_face_running[0]:
            _new_log("[auto-face] กำลังทำงานอยู่ — รอเสร็จก่อน")
            return
        batch_source = str(batch_source_state.get("text") or "").strip()
        auto_face_stop[0] = False
        auto_face_running[0] = True
        root.after(0, lambda: _set_auto_face_button_running(True))
        if batch_source:
            batch_status_var.set("กำลังแยกรายชื่อด้วย GPT...")
            _new_log("[ข้อมูลชุด] กำลังแยกชื่อ ช่วงวัย บทบาท และชุด...")
        else:
            _new_log("[auto-face] ไม่พบข้อมูลชุด — ใช้รายชื่อจาก Context ของหน้านิทาน")

        def worker():
            try:
                _ensure_story_face_history()
                lock = globals().get("_bridge_queue_lock")
                story_face_dir = export_story_face_dir
                story_face_dir.mkdir(parents=True, exist_ok=True)
                first_face_by_name = {}
                # Shared by run_all (fills it) and run_one (reads it).
                face_profiles = {}

                def resolve_characters():
                    if batch_source:
                        characters = _expand_group_characters(
                            _parse_batch_characters_via_bridge(batch_source)
                        )
                        overview = str(batch_cache.get("design_page") or "").strip()
                        if not overview:
                            overview = re.sub(
                                r"(?m)^\s*\d+(?:\.\d+)*[.)]?\s*.+$",
                                "",
                                batch_source,
                            ).strip() or batch_source[:1600]
                        return characters, overview, "ข้อมูลชุด"
                    context_text = _load_face_context_text()
                    characters = _extract_auto_face_characters(context_text)
                    if not characters:
                        raise RuntimeError("หน้านิทานยังไม่มีรายชื่อตัวละคร — กด 📄 เลือกบท หรือใส่ ข้อมูลชุด ก่อน")
                    return characters, "", "Context นิทาน"

                def run_one(idx, character, characters, overview, source_kind):
                    name = str(character.get("name", "")).strip()
                    if auto_face_stop[0]:
                        _new_log(f"[auto-face] หยุดแล้ว — ข้าม {name}")
                        return
                    variant = str(character.get("variant", "")).strip()
                    display_name = name + (f" — {variant}" if variant else "")
                    _new_log(f"[auto-face] {idx}/{len(characters)} — เริ่มสร้าง: {display_name}")
                    if source_kind == "ข้อมูลชุด":
                        prompt = _batch_face_prompt(character, overview, face_profiles.get(_identity_family_key(character), ""))
                    else:
                        # Build prompt from full Prompt Context details, same as Select.
                        builder = globals().get("_build_story_face_prompt_from_character")
                        if callable(builder):
                            prompt = builder(character)
                        else:
                            tmpl = g.get("story_face_prompt_template", "")
                            prompt = tmpl.replace("{name}", name)
                        profile = face_profiles.get(_identity_family_key(character), "")
                        if profile:
                            prompt = (
                                "MANDATORY UNIQUE FACE GEOMETRY — preserve exactly: " + profile + ". "
                                "This is a new unrelated identity; do not continue or imitate any earlier face in this chat.\n\n"
                                + prompt
                            )
                    age_label = str(new_age_var.get() or "อัตโนมัติ").strip()
                    age_val = _FACE_AGE_MAP.get(age_label, "35")
                    prompt = prompt.replace("{age}", age_val).replace("{name}", name)
                    identity_key = _identity_family_key(character)
                    identity_source = first_face_by_name.get(identity_key)
                    prompt, identity_images = _identity_reference_payload(prompt, identity_source) if identity_source else (prompt, None)
                    condition = _condition_text(character)
                    prompt = _append_condition_override(prompt, condition)
                    if "_wait_bridge_free" in globals():
                        globals()["_wait_bridge_free"](log_fn=_new_log)
                    # Keep the structured face geometry intact. The generic
                    # rewriter used to compress it and make every cast member
                    # look like the same default portrait.
                    final_prompt = prompt
                    age_reserve = _three_d_age_reserve(character)
                    if age_reserve:
                        final_prompt = str(final_prompt).rstrip() + "\n\nFINAL " + age_reserve
                    if identity_images and "IDENTITY REFERENCE — MANDATORY:" not in final_prompt:
                        final_prompt = str(final_prompt).rstrip() + identity_lock
                    final_prompt = _force_neutral_face_prompt(
                        _apply_final_face_lighting_lock(final_prompt)
                    )
                    final_prompt = _apply_face_age_override(final_prompt, age_label)
                    final_prompt = _apply_clean_face_lock(final_prompt)
                    payload = _build_story_face_payload(final_prompt)
                    _face_payload_history(payload, fresh=not identity_images)
                    if identity_images:
                        payload["images"] = identity_images
                    hint = f"{name}-{variant}-face" if variant else f"{name}-face"
                    final_prompt = soften_injury_text(final_prompt)
                    payload["prompt"] = final_prompt
                    try:
                        out = g["_do_image_request"](
                            payload, is_edit=bool(identity_images), prompt=final_prompt,
                            name_hint=hint, raw_prompt=prompt,
                            output_dir=str(story_face_dir), save_sidecar=False,
                        )
                    except Exception as exc:
                        refused = any(word in str(exc).lower() for word in (
                            "policy", "safety", "moderation", "not allowed", "can't help", "cannot help",
                            "unable to generate", "violat", "ไม่สามารถ"))
                        if not (refused and "STORY MAKEUP EFFECT" in final_prompt):
                            raise RuntimeError(f"ขั้นสร้างรูปผ่าน GPT ล้มเหลว: {exc}") from exc
                        # Refused because of the injury look: make the face without it
                        # so the character still gets a usable identity image.
                        root.after(0, lambda n=display_name: _new_log(
                            f"[auto-face] {n}: GPT ไม่ยอมทำรอยบาดเจ็บ — ทำหน้าปกติแทน (แต่งรอยภายหลังได้)"))
                        final_prompt = re.sub(r"; STORY MAKEUP EFFECT[^;]*", "", final_prompt)
                        final_prompt = final_prompt.replace("STORY MAKEUP EFFECT", "")
                        payload["prompt"] = final_prompt
                        try:
                            out = g["_do_image_request"](
                                payload, is_edit=bool(identity_images), prompt=final_prompt,
                                name_hint=hint, raw_prompt=prompt,
                                output_dir=str(story_face_dir), save_sidecar=False,
                            )
                        except Exception as exc2:
                            raise RuntimeError(f"ขั้นสร้างรูปผ่าน GPT ล้มเหลว: {exc2}") from exc2
                    first_face_by_name.setdefault(identity_key, out)
                    root.after(0, lambda out=out, display_name=display_name: (_new_log(f"✓ {display_name}: {out}"), _new_gallery_add(out, True), _notify_done()))

                def run_all():
                    characters, overview, source_kind = resolve_characters()
                    if not characters:
                        if batch_source:
                            raise RuntimeError("ไม่พบรายชื่อในข้อมูลชุด กรุณาใช้รายการขึ้นต้น 1., 2., 2.1 ...")
                        raise RuntimeError("ไม่พบตัวละครใน Prompt Context — วางข้อมูลชุดหรือสรุปบทหลักก่อน")
                    characters = sorted(
                        characters,
                        key=lambda item: (
                            _identity_family_key(item),
                            0 if item.get("identity_master") else 1,
                        ),
                    )
                    face_profiles.update(_assign_cast_face_profiles(characters) or {})
                    names = [
                        str(c.get("name", "")).strip()
                        + (f" ({str(c.get('variant', '')).strip()})" if str(c.get("variant", "")).strip() else "")
                        for c in characters
                    ]
                    root.after(
                        0,
                        lambda names=names, source_kind=source_kind: (
                            batch_status_var.set(f"พบ {len(names)} งานจาก {source_kind}"),
                            _new_log(f"[auto-face] จะสร้างทีละใบหน้า {len(names)} งาน: {', '.join(names)}"),
                        ),
                    )
                    for idx, character in enumerate(characters, 1):
                        if auto_face_stop[0]:
                            _new_log("[auto-face] หยุดตามคำสั่ง")
                            break
                        run_one(idx, character, characters, overview, source_kind)
                if lock:
                    with lock: run_all()
                else:
                    run_all()
            except Exception as e:
                root.after(0, lambda e=e: _new_log(f"[auto-face] ERROR: {e}"))
            finally:
                auto_face_running[0] = False
                root.after(0, lambda: _set_auto_face_button_running(False))
        threading.Thread(target=worker, daemon=True).start()
    g["generate_auto_face"] = generate_auto_face
    g["stop_auto_face"] = stop_auto_face

    def _open_story_face_batch_dialog():
        if auto_face_running[0]:
            _new_log("[ข้อมูลชุด] กำลังสร้างอยู่ — กดหยุดหรือรอให้เสร็จก่อน")
            return
        win = tk.Toplevel(root)
        win.title("ข้อมูลตัวละครเป็นชุด — นิทาน")
        dialog_width, dialog_height = 780, 620
        try:
            root.update_idletasks()
            x = root.winfo_rootx() + max(0, (root.winfo_width() - dialog_width) // 2)
            y = root.winfo_rooty() + max(0, (root.winfo_height() - dialog_height) // 2)
            win.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
        except Exception:
            win.geometry(f"{dialog_width}x{dialog_height}")
        win.minsize(700, 540)
        win.configure(bg="#FFFFFF")
        win.transient(root)

        header = tk.Frame(win, bg="#FFFFFF")
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(
            header,
            text="ข้อมูลตัวละครเป็นชุด",
            bg="#FFFFFF",
            fg="#111827",
            font=(SNAPGEN_UI_FONT, 14, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            header,
            text="วางข้อมูลแบบรายการ 1., 2., 2.1 ... ระบบจะแยกคน ช่วงวัย บทบาท และชุด ก่อนสร้างใบหน้าทีละรูป",
            bg="#FFFFFF",
            fg="#6B7280",
            font=(SNAPGEN_UI_FONT, 9),
            anchor="w",
        ).pack(fill="x", pady=(3, 0))

        input_frame = tk.LabelFrame(
            win,
            text="1. วางข้อมูล",
            bg="#FFFFFF",
            fg="#111827",
            padx=8,
            pady=6,
        )
        input_frame.pack(fill="x", padx=16, pady=(0, 8))
        editor = tk.Text(
            input_frame,
            height=12,
            wrap="word",
            bg="#FFFFFF",
            fg="#111827",
            insertbackground="#111827",
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            font=(SNAPGEN_UI_FONT, 10),
        )
        editor.pack(fill="x")
        editor.insert("1.0", str(batch_source_state.get("text") or ""))

        preview_frame = tk.LabelFrame(
            win,
            text="2. รายการใบหน้าที่ระบบจะสร้าง",
            bg="#FFFFFF",
            fg="#111827",
            padx=8,
            pady=6,
        )
        preview = tk.Text(
            preview_frame,
            height=9,
            wrap="word",
            bg="#F9FAFB",
            fg="#111827",
            relief="flat",
            padx=8,
            pady=6,
            font=(SNAPGEN_UI_FONT, 9),
            state=tk.DISABLED,
        )
        preview.pack(fill="both", expand=True)

        controls = tk.Frame(win, bg="#FFFFFF")
        controls.pack(side="bottom", fill="x", padx=16, pady=(0, 14))
        dialog_status = tk.StringVar(value=batch_status_var.get())
        tk.Label(
            controls,
            textvariable=dialog_status,
            bg="#FFFFFF",
            fg="#6B7280",
            anchor="w",
            font=(SNAPGEN_UI_FONT, 9),
        ).pack(side="left", fill="x", expand=True)

        def save_text():
            value = editor.get("1.0", tk.END).strip()
            changed = value != str(batch_source_state.get("text") or "")
            batch_source_state["text"] = value
            if changed:
                batch_cache.update(source="", characters=[], design_page="")
            _persist_batch_state()
            message = "บันทึกข้อมูลล่าสุดแล้ว" if value else "บันทึกข้อมูลว่างแล้ว"
            batch_status_var.set(message)
            dialog_status.set(message)
            return value

        def render_preview(subjects):
            preview.config(state=tk.NORMAL)
            preview.delete("1.0", tk.END)
            if not subjects:
                preview.insert(
                    "1.0",
                    "ไม่พบรายการที่เป็นบุคคล จึงยังไม่มีรูปใบหน้าที่จะสร้าง\n"
                    "Prop, สิ่งของ, สถานที่, สัตว์ และรูปถ่ายกลุ่มจะถูกตัดออก",
                )
                create_btn.config(state=tk.DISABLED)
            else:
                design_page = str(batch_cache.get("design_page") or "").strip()
                if design_page:
                    preview.insert(
                        tk.END,
                        "CHARACTER BIBLE — แผนใบหน้ารวมทั้งเรื่อง\n"
                        + design_page
                        + "\n\n"
                        + ("─" * 72)
                        + "\n\n",
                    )
                preview.insert(
                    tk.END,
                    f"จะสร้างทั้งหมด {len(subjects)} รูป — เฉพาะบุคคลด้านล่าง\n\n",
                )
                for index, subject in enumerate(subjects, 1):
                    name = str(subject.get("name") or "").strip()
                    variant = str(subject.get("variant") or "").strip()
                    age = str(subject.get("age") or "").strip()
                    role = str(subject.get("role") or "").strip()
                    life_condition = str(subject.get("life_condition") or "").strip()
                    expression = str(subject.get("expression") or "").strip()
                    face_design = str(subject.get("face_design") or "").strip()
                    skin_detail = str(subject.get("skin_detail") or "").strip()
                    hair = str(subject.get("hair") or "").strip()
                    clothes = str(subject.get("clothes") or "").strip()
                    title = name + (f" — {variant}" if variant else "")
                    identity_group = str(subject.get("identity_group") or "").strip()
                    identity_role = "ตัวหลัก" if subject.get("identity_master") else f"อ้างจาก: {subject.get('reference_from') or identity_group}"
                    detail = " | ".join(
                        value for value in (
                            f"ชุดหน้า: {identity_group}" if identity_group else "",
                            identity_role,
                            f"วัย: {age}" if age else "",
                            f"บทบาท: {role}" if role else "",
                            f"สภาพ: {life_condition}" if life_condition else "",
                            f"อารมณ์: {expression}" if expression else "",
                            f"ใบหน้า: {face_design}" if face_design else "",
                            f"ผิว: {skin_detail}" if skin_detail else "",
                            f"ผม: {hair}" if hair else "",
                            f"ชุด: {clothes}" if clothes else "",
                        ) if value
                    )
                    preview.insert(
                        tk.END,
                        f"{index}. {title}" + (f"\n   {detail}" if detail else "") + "\n",
                    )
                create_btn.config(state=tk.NORMAL)
            preview.config(state=tk.DISABLED)
            preview.see("1.0")

        def save_and_close():
            save_text()
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        def clear_text():
            editor.delete("1.0", tk.END)
            batch_source_state["text"] = ""
            batch_cache.update(source="", characters=[], design_page="")
            batch_status_var.set("ยังไม่มีข้อมูลชุด")
            dialog_status.set("ล้างข้อมูลชุดแล้ว")
            _persist_batch_state()
            render_preview([])

        def analyze():
            value = save_text()
            if not value:
                dialog_status.set("กรุณาวางข้อมูลตัวละครก่อน")
                return
            analyze_btn.config(state=tk.DISABLED, text="กำลังวิเคราะห์...")
            create_btn.config(state=tk.DISABLED)
            dialog_status.set("กำลังวิเคราะห์ว่าอะไรเป็นบุคคลและควรสร้างใบหน้าอะไร...")

            def worker():
                try:
                    # User pressed Analyze: always rebuild from current text.
                    subjects = _parse_batch_characters_via_bridge(value, force=True)
                    error = None
                except Exception as exc:
                    subjects = []
                    error = str(exc)

                def done():
                    try:
                        current = editor.get("1.0", tk.END).strip()
                        analyze_btn.config(state=tk.NORMAL, text="🔎 วิเคราะห์")
                        if current != value:
                            create_btn.config(state=tk.DISABLED)
                            dialog_status.set("ข้อมูลถูกแก้ระหว่างวิเคราะห์ — กดวิเคราะห์ใหม่")
                            return
                        if error:
                            dialog_status.set("วิเคราะห์ไม่สำเร็จ: " + error)
                            render_preview([])
                            return
                        render_preview(subjects)
                        _persist_batch_state()
                        _load_outfit_characters()
                        message = f"วิเคราะห์แล้ว: จะสร้าง {len(subjects)} รูป"
                        batch_status_var.set(message)
                        dialog_status.set(message + " — ตรวจรายการก่อนกดสร้างทั้งหมด")
                    except Exception:
                        pass
                root.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def create_all():
            value = save_text()
            if not value:
                dialog_status.set("กรุณาวางข้อมูลตัวละครก่อน")
                return
            if batch_cache.get("source") != value or not batch_cache.get("characters"):
                create_btn.config(state=tk.DISABLED)
                dialog_status.set("กรุณากดวิเคราะห์และตรวจรายการก่อนสร้าง")
                return
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()
            generate_auto_face()

        def on_editor_modified(_event=None):
            try:
                if not editor.edit_modified():
                    return
                editor.edit_modified(False)
                current = editor.get("1.0", tk.END).strip()
                batch_source_state["text"] = current
                if batch_cache.get("source") != current:
                    batch_cache.update(source="", characters=[], design_page="")
                    create_btn.config(state=tk.DISABLED)
                    dialog_status.set("ข้อมูลเปลี่ยนแล้ว — กดวิเคราะห์เพื่อดูรายการใหม่")
            except Exception:
                pass

        tk.Button(
            controls,
            text="ปิด",
            command=save_and_close,
            bg="#F3F4F6",
            fg="#111827",
            relief="flat",
            padx=14,
            pady=7,
            font=(SNAPGEN_UI_FONT, 9, "bold"),
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            controls,
            text="💾 Save",
            command=save_text,
            bg="#16A34A",
            fg="#FFFFFF",
            relief="flat",
            padx=14,
            pady=7,
            font=(SNAPGEN_UI_FONT, 9, "bold"),
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            controls,
            text="ล้าง",
            command=clear_text,
            bg="#DC2626",
            fg="#FFFFFF",
            relief="flat",
            padx=14,
            pady=7,
            font=(SNAPGEN_UI_FONT, 9, "bold"),
        ).pack(side="right", padx=(6, 0))
        create_btn = tk.Button(
            controls,
            text="⚡ สร้างทั้งหมด",
            command=create_all,
            bg="#059669",
            activebackground="#10B981",
            fg="#FFFFFF",
            relief="flat",
            padx=16,
            pady=7,
            font=(SNAPGEN_UI_FONT, 9, "bold"),
            state=tk.DISABLED,
        )
        create_btn.pack(side="right", padx=(6, 0))
        analyze_btn = tk.Button(
            controls,
            text="🔎 วิเคราะห์",
            command=analyze,
            bg="#7C3AED",
            fg="#FFFFFF",
            relief="flat",
            padx=16,
            pady=7,
            font=(SNAPGEN_UI_FONT, 9, "bold"),
        )
        analyze_btn.pack(side="right", padx=(6, 0))

        # Pack the expandable preview only after the fixed footer.  This keeps
        # every action button visible even on shorter screens.
        preview_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        editor.edit_modified(False)
        editor.bind("<<Modified>>", on_editor_modified, add="+")
        saved_source = str(batch_source_state.get("text") or "").strip()
        if saved_source and batch_cache.get("source") == saved_source and batch_cache.get("characters"):
            render_preview(batch_cache["characters"])
            dialog_status.set(
                f"วิเคราะห์แล้ว: จะสร้าง {len(batch_cache['characters'])} รูป"
            )

        win.protocol("WM_DELETE_WINDOW", save_and_close)
        try:
            win.grab_set()
            editor.focus_set()
        except Exception:
            pass

    g["_open_story_face_batch_dialog"] = _open_story_face_batch_dialog
    
    new_make_btn = tk.Button(new_row, text="🎭 สร้าง Face + Body", command=generate_story_face, bg="#059669", fg="white", activebackground="#10B981", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=16, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    new_make_btn.pack(side="left", padx=4)
    
    # Pack Select AFTER สร้าง Face (so สร้าง Face appears first on the left)
    new_select_btn.pack(side="left", padx=(0, 4))
    new_auto_btn = tk.Button(new_row, text="📋 ข้อมูลชุด", command=_open_story_face_batch_dialog, bg="#0EA5E9", fg="white", activebackground="#0284C7", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold"))
    new_auto_btn.pack(side="left", padx=4)
    tk.Button(new_row, text="Clear", command=lambda: (new_name_var.set(""), new_log.delete("1.0", tk.END)), bg="#DC2626", fg="white", activebackground="#B91C1C", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)
    tk.Button(new_row, text="🧹 ล้างรูป", command=_clear_story_gallery, bg="#DC2626", fg="white", activebackground="#B91C1C", activeforeground="white", relief="flat", bd=0, padx=14, pady=7, width=14, height=1, font=(SNAPGEN_UI_FONT, 9, "bold")).pack(side="left", padx=4)

    def _mobile_story_face_characters():
        """Expose the desktop character list without creating mobile-only state."""
        try:
            context_characters = _extract_story_face_characters(_load_ref_context())
        except Exception:
            context_characters = []
        # Match the desktop selector: the current Story Dataset wins when the
        # same name/variant also exists in the wider Prompt Context.
        characters = _expand_group_characters(
            _current_dataset_characters()
        ) + _expand_group_characters(context_characters)
        rows, lookup, seen = [], {}, set()
        for character in characters:
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            variant = str(character.get("variant") or "").strip()
            if not name:
                continue
            key = _story_face_character_key(character)
            identity = (name.casefold(), variant.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            lookup[key] = character
            rows.append({
                "key": key,
                "name": name,
                "variant": variant,
                "label": name + (f" — {variant}" if variant else ""),
            })
        return rows, lookup

    def _story_face_ordered_characters():
        """Return the current Story Dataset in the same order shown by Story Face."""
        characters = _expand_group_characters(_current_dataset_characters())
        if not characters:
            try:
                characters = _expand_group_characters(
                    _extract_story_face_characters(_load_ref_context())
                )
            except Exception:
                characters = []
        rows = []
        for character in characters:
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            variant = str(character.get("variant") or "").strip()
            if not name:
                continue
            rows.append({
                "key": _story_face_character_key(character),
                "name": name,
                "variant": variant,
                "label": name + (f" — {variant}" if variant else ""),
            })
        return rows

    def _mobile_story_face_select(character_key, age_label=None):
        if story_face_running[0] or auto_face_running[0]:
            raise RuntimeError("หน้านิทานกำลังสร้างอยู่ กรุณารอให้เสร็จก่อน")
        rows, lookup = _mobile_story_face_characters()
        character = lookup.get(str(character_key or ""))
        if character is None:
            raise ValueError("ไม่พบตัวละครนี้ กรุณาเลือกใหม่")
        if age_label is not None:
            age_label = str(age_label).strip()
            if age_label not in _FACE_AGES:
                raise ValueError("วัยที่เลือกไม่ถูกต้อง")
            new_age_var.set(age_label)
        _apply_story_face_character(character)
        return next(row["label"] for row in rows if row["key"] == character_key)

    def _mobile_story_face_generate(character_key, age_label):
        label = _mobile_story_face_select(character_key, age_label)
        generate_story_face()
        if not story_face_running[0]:
            raise RuntimeError("ยังไม่ได้เริ่มสร้างตัวละคร ตรวจ Log หน้านิทาน")
        return label

    def _mobile_story_face_snapshot():
        rows, _lookup = _mobile_story_face_characters()
        selected_name = str(new_name_var.get() or "").strip()
        selected_key = selected_character_key[0]
        if selected_key not in {row["key"] for row in rows}:
            selected_key = next((row["key"] for row in rows if row["name"] == selected_name), "")
        return {
            "title": str(story_face_title_var.get() or "").strip(),
            "status": str(story_face_status_var.get() or "").strip(),
            "name": selected_name,
            "selected_key": selected_key,
            "age": str(new_age_var.get() or "อัตโนมัติ"),
            "ages": list(_FACE_AGES),
            "characters": rows,
            "running": bool(story_face_running[0] or auto_face_running[0]),
            "log": str(new_log.get("1.0", tk.END)).strip(),
            "gallery": list(gallery_paths),
        }

    g["mobile_story_face_actions"] = {
        "snapshot": _mobile_story_face_snapshot,
        "select": _mobile_story_face_select,
        "generate": _mobile_story_face_generate,
    }
    g["story_face_ordered_characters"] = _story_face_ordered_characters
    g["new_make_btn"] = new_make_btn
    g["new_auto_btn"] = new_auto_btn
    g["new_page"] = new_page
    return new_page
