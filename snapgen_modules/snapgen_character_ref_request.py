# -*- coding: utf-8 -*-
"""Character Ref serialization from canonical Visual Bible only."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from snapgen_character_visual_bible import canonicalize_context, find_character, has_value


def _flatten(value):
    return " ".join(str(value or "").split()).strip()


def wardrobe_text(character):
    wardrobe = character.get("wardrobe") if isinstance(character, dict) and isinstance(character.get("wardrobe"), dict) else None
    if not wardrobe:
        return "none; do not invent human clothing"
    labels = (
        ("top", "top"), ("bottom", "bottom"), ("footwear", "footwear"), ("outerwear", "outerwear"),
        ("accessories", "accessories"), ("colors", "colors"), ("materials", "materials"),
        ("condition", "condition"), ("overall_style", "overall_style"), ("source", "source"), ("reason", "reason"),
    )
    return "; ".join(f"{label}={_flatten(wardrobe.get(key))}" for key, label in labels if has_value(wardrobe.get(key))) or "none"


def character_prompt_text(character):
    if not isinstance(character, dict):
        raise RuntimeError("ไม่พบ canonical character สำหรับสร้าง Ref")
    appearance = character.get("appearance") if isinstance(character.get("appearance"), dict) else {}
    identity_parts = [
        f"character_id={_flatten(character.get('character_id'))}", f"name={_flatten(character.get('name'))}",
        f"entity_type={_flatten(character.get('entity_type'))}", f"visual_identity={_flatten(character.get('visual_identity'))}",
        f"age={_flatten(appearance.get('age'))}", f"gender={_flatten(appearance.get('gender'))}",
        f"body={_flatten(appearance.get('body'))}", f"height={_flatten(appearance.get('height'))}",
        f"skin={_flatten(appearance.get('skin'))}", f"hair={_flatten(appearance.get('hair'))}",
        f"face={_flatten(appearance.get('face'))}", f"eyes={_flatten(appearance.get('eyes'))}",
        f"occupation={_flatten(character.get('occupation'))}", f"social_status={_flatten(character.get('social_status'))}",
    ]
    identity = "; ".join(x for x in identity_parts if not x.endswith("="))
    wardrobe = wardrobe_text(character)
    return (
        f"สร้าง CHARACTER REFERENCE IMAGE แนวนอน 16:9 ของ '{_flatten(character.get('name'))}' จาก CANONICAL CHARACTER VISUAL BIBLE นี้เท่านั้น. "
        "ห้ามตีความ identity หรือ wardrobe ใหม่ และห้ามใช้ข้อมูลของตัวละครอื่น. "
        f"CANONICAL CHARACTER: {identity}. WARDROBE: {wardrobe}. "
        "MANDATORY COMPOSITION: EXACTLY 3 visible depictions เรียงซ้ายไปขวา: "
        "(1) FRONT FACE head-and-shoulders หน้าตรงมองกล้อง, "
        "(2) THREE-QUARTER VIEW head-and-shoulders หันประมาณ 45 องศา, "
        "(3) FULL-BODY FRONT VIEW ยืนตรงเห็นศีรษะถึงเท้าครบ. "
        "ทั้ง 3 ต้องเป็น object/คนเดียวกัน identity เดียวกัน และใช้ wardrobe object เดียวกัน 100%; "
        "ห้ามเปลี่ยน top, bottom, footwear, outerwear, accessories, colors, materials หรือ condition ระหว่างมุม. "
        "ถ้า wardrobe=null/none ห้ามเพิ่ม human clothing เอง. Full-body ต้องแสดง garment/accessory ทุกชิ้นที่ canonical wardrobe ระบุครบ. "
        "พื้นหลังขาวหรือเทาอ่อนเรียบ แสง ID-photo สม่ำเสมอ photorealistic, sharp, no text, no watermark."
    )


def load_character_from_context_text(context_text, character_id_or_name):
    try:
        data = json.loads(str(context_text or ""))
    except Exception as exc:
        raise RuntimeError(f"อ่าน Prompt-Ref Context ไม่ได้: {exc}") from exc
    canonical = canonicalize_context(data)
    character = find_character(canonical, character_id_or_name)
    return canonical, character


def save_debug_snapshot(base, character, prompt):
    base = Path(base)
    debug_dir = base / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / "ref_last_request.json"
    payload = {
        "character_id": _flatten(character.get("character_id")),
        "character_name": _flatten(character.get("name")),
        "canonical_character": deepcopy(character),
        "canonical_wardrobe": deepcopy(character.get("wardrobe")),
        "costume_identity": deepcopy(character.get("costume_identity")),
        "final_ref_prompt": str(prompt or ""),
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
