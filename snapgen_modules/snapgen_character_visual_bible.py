# -*- coding: utf-8 -*-
"""Canonical Character Visual Bible schema and structural-only migration.

AI owns semantic character decisions. Python only canonicalizes legacy keys,
validates structure, preserves non-empty data, serializes, and transports it.
"""
from __future__ import annotations

from copy import deepcopy

APPEARANCE_FIELDS = ("age", "gender", "body", "height", "skin", "hair", "face", "eyes")
WARDROBE_FIELDS = (
    "top", "bottom", "footwear", "outerwear", "accessories",
    "colors", "materials", "condition", "overall_style", "source", "reason",
)
CHARACTER_CATEGORIES = ("main_characters", "supporting_characters", "animals", "supernatural_entities")
LEGACY_APPEARANCE = {
    "อายุ": "age", "เพศ": "gender", "รูปร่าง": "body", "ส่วนสูง": "height",
    "สีผิว": "skin", "ทรงผม": "hair", "ใบหน้า": "face", "ดวงตา": "eyes", "ดวงต": "eyes",
}
LEGACY_WARDROBE_ALIASES = ("เสื้อผ้า", "เสื้อผ")
DROP_LEGACY_KEYS = set(LEGACY_APPEARANCE) | set(LEGACY_WARDROBE_ALIASES) | {
    "อาชีพ", "ฐานะ", "บทบาท", "wardrobe_source", "wardrobe_reason", "default_outfit", "outfit_variants"
}


def has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _text(value):
    return " ".join(str(value or "").split()).strip()


def _empty_appearance():
    return {key: "" for key in APPEARANCE_FIELDS}


def _empty_wardrobe():
    return {key: "" for key in WARDROBE_FIELDS}


def _legacy_wardrobe(character):
    """Structurally migrate legacy wardrobe without inventing garment meaning."""
    wardrobe = character.get("wardrobe") if isinstance(character.get("wardrobe"), dict) else None
    if wardrobe:
        if isinstance(wardrobe.get("default_outfit"), dict):
            old = wardrobe.get("default_outfit") or {}
            out = _empty_wardrobe()
            for key in ("top", "bottom", "footwear", "outerwear", "accessories", "colors", "materials", "condition", "overall_style"):
                if has_value(old.get(key)):
                    out[key] = deepcopy(old.get(key))
            out["source"] = _text(wardrobe.get("source") or wardrobe.get("wardrobe_source") or character.get("wardrobe_source"))
            out["reason"] = _text(wardrobe.get("reason") or wardrobe.get("wardrobe_reason") or character.get("wardrobe_reason"))
            return out
        out = _empty_wardrobe()
        copied = False
        for key in WARDROBE_FIELDS:
            if has_value(wardrobe.get(key)):
                out[key] = deepcopy(wardrobe.get(key))
                copied = True
        if copied:
            return out
    legacy_clothes = ""
    for key in LEGACY_WARDROBE_ALIASES:
        if has_value(character.get(key)):
            legacy_clothes = deepcopy(character.get(key))
            break
    if legacy_clothes:
        out = _empty_wardrobe()
        # We do not parse/reinterpret a legacy sentence into garments. Preserve it verbatim.
        out["overall_style"] = legacy_clothes
        source = _text(character.get("wardrobe_source"))
        out["source"] = source if source in ("explicit", "inferred") else ""
        out["reason"] = _text(character.get("wardrobe_reason"))
        return out
    return None


def canonicalize_character(character, default_entity_type="human"):
    if not isinstance(character, dict):
        return None
    raw = deepcopy(character)
    name = _text(raw.get("name") or raw.get("ชื่อ"))
    if not name:
        return None
    entity_type = _text(raw.get("entity_type") or raw.get("type") or default_entity_type) or default_entity_type
    character_id = _text(raw.get("character_id") or raw.get("id") or name)

    appearance = _empty_appearance()
    if isinstance(raw.get("appearance"), dict):
        for key in APPEARANCE_FIELDS:
            if has_value(raw["appearance"].get(key)):
                appearance[key] = deepcopy(raw["appearance"].get(key))
    for legacy, canonical in LEGACY_APPEARANCE.items():
        if not has_value(appearance.get(canonical)) and has_value(raw.get(legacy)):
            appearance[canonical] = deepcopy(raw.get(legacy))

    wardrobe = _legacy_wardrobe(raw)
    # Non-human entities do not receive an empty human wardrobe structure by default.
    if entity_type.casefold() in {"animal", "animal_group", "supernatural_unknown", "supernatural", "supernatural_entity", "ghost", "spirit"}:
        if wardrobe and not any(has_value(wardrobe.get(k)) for k in WARDROBE_FIELDS):
            wardrobe = None
        out_costume_identity = None
    else:
        out_costume_identity = deepcopy(raw.get("costume_identity")) if isinstance(raw.get("costume_identity"), dict) else None

    out = {}
    for key, value in raw.items():
        if key in DROP_LEGACY_KEYS or key in {"appearance", "wardrobe", "id", "ชื่อ", "type"}:
            continue
        out[key] = deepcopy(value)
    out["character_id"] = character_id
    out["name"] = name
    out["entity_type"] = entity_type
    out["needs_ref"] = bool(raw.get("needs_ref", False))
    out["appearance"] = appearance
    out["wardrobe"] = wardrobe
    out["occupation"] = deepcopy(raw.get("occupation") if has_value(raw.get("occupation")) else raw.get("อาชีพ") or "")
    out["social_status"] = deepcopy(raw.get("social_status") if has_value(raw.get("social_status")) else raw.get("ฐานะ") or "")
    out["costume_identity"] = out_costume_identity
    out["visual_identity"] = deepcopy(raw.get("visual_identity") or "")
    out["evidence"] = deepcopy(raw.get("evidence") if isinstance(raw.get("evidence"), list) else [])
    out["assumptions"] = deepcopy(raw.get("assumptions") if isinstance(raw.get("assumptions"), list) else [])
    return out


def canonicalize_context(data):
    if not isinstance(data, dict):
        raise RuntimeError("Character Visual Bible ต้องเป็น JSON object")
    out = deepcopy(data)
    flat = []
    seen = set()
    defaults = {
        "main_characters": "human", "supporting_characters": "human",
        "animals": "animal", "supernatural_entities": "supernatural_unknown",
    }
    category_present = any(isinstance(data.get(category), list) and bool(data.get(category)) for category in CHARACTER_CATEGORIES)
    for category in CHARACTER_CATEGORIES:
        rows = data.get(category)
        if isinstance(rows, list) and category_present:
            cleaned = []
            for row in rows:
                char = canonicalize_character(row, defaults[category])
                if not char:
                    continue
                cleaned.append(char)
                ident = (char["entity_type"].casefold(), char["character_id"].casefold())
                if ident not in seen:
                    seen.add(ident); flat.append(deepcopy(char))
            out[category] = cleaned
        elif category_present:
            out[category] = []
    if not category_present:
        cleaned = []
        for row in data.get("characters") or []:
            char = canonicalize_character(row, "human")
            if char:
                cleaned.append(char)
        flat = cleaned
    out["characters"] = flat
    for category in CHARACTER_CATEGORIES:
        out.setdefault(category, [])
    for key in ("locations", "props", "scene_map"):
        if not isinstance(out.get(key), list):
            out[key] = []
    out["version"] = 5
    out["character_visual_bible_schema"] = "canonical_v1"
    return out


def find_character(data, character_id_or_name):
    context = canonicalize_context(data)
    wanted = _text(character_id_or_name).casefold()
    by_name = None
    for char in context.get("characters") or []:
        if _text(char.get("character_id")).casefold() == wanted:
            return deepcopy(char)
        if _text(char.get("name")).casefold() == wanted:
            by_name = char
    return deepcopy(by_name) if by_name else None


def missing_character_fields(character):
    if not isinstance(character, dict) or not character.get("needs_ref"):
        return []
    missing = []
    entity_type = _text(character.get("entity_type")).casefold()
    if not has_value(character.get("visual_identity")):
        missing.append("visual_identity")
    if entity_type == "human":
        appearance = character.get("appearance") if isinstance(character.get("appearance"), dict) else {}
        for key in APPEARANCE_FIELDS:
            if not has_value(appearance.get(key)):
                missing.append("appearance." + key)
        wardrobe = character.get("wardrobe") if isinstance(character.get("wardrobe"), dict) else {}
        required_wardrobe = ("top", "bottom", "footwear", "colors", "materials", "condition", "overall_style", "source", "reason")
        for key in required_wardrobe:
            if not has_value(wardrobe.get(key)):
                missing.append("wardrobe." + key)
        if has_value(wardrobe.get("source")) and str(wardrobe.get("source")).strip() not in ("explicit", "inferred"):
            missing.append("wardrobe.source")
    return missing


def context_missing_fields(data):
    context = canonicalize_context(data)
    result = {}
    for char in context.get("characters") or []:
        missing = missing_character_fields(char)
        if missing:
            result[char["character_id"]] = missing
    return result


def merge_preserve_existing(existing, repair):
    """Existing non-empty values always win; repair may only fill true gaps."""
    if has_value(existing) and not isinstance(existing, (dict, list)):
        return deepcopy(existing)
    if isinstance(existing, dict) and isinstance(repair, dict):
        result = deepcopy(existing)
        for key, value in repair.items():
            if key in result:
                result[key] = merge_preserve_existing(result[key], value)
            elif has_value(value):
                result[key] = deepcopy(value)
        return result
    if isinstance(existing, list):
        return deepcopy(existing) if existing else deepcopy(repair if isinstance(repair, list) else existing)
    return deepcopy(repair) if has_value(repair) else deepcopy(existing)


def apply_character_repairs(context, repairs):
    canonical = canonicalize_context(context)
    repair_rows = repairs.get("repairs") if isinstance(repairs, dict) else None
    if not isinstance(repair_rows, list):
        return canonical
    by_id = {_text(c.get("character_id")).casefold(): c for c in canonical.get("characters") or []}
    by_name = {_text(c.get("name")).casefold(): c for c in canonical.get("characters") or []}
    for patch in repair_rows:
        if not isinstance(patch, dict):
            continue
        target = by_id.get(_text(patch.get("character_id")).casefold()) or by_name.get(_text(patch.get("name")).casefold())
        if not target:
            continue
        merged = merge_preserve_existing(target, patch)
        target.clear(); target.update(canonicalize_character(merged, target.get("entity_type") or "human") or merged)
    # Rebind categorized copies by character_id/name, never by array index.
    flat_lookup = {_text(c.get("character_id")).casefold(): c for c in canonical.get("characters") or []}
    for category in CHARACTER_CATEGORIES:
        replaced = []
        for row in canonical.get(category) or []:
            key = _text(row.get("character_id")).casefold()
            replaced.append(deepcopy(flat_lookup.get(key, row)))
        canonical[category] = replaced
    return canonicalize_context(canonical)


def canonical_schema_text():
    return (
        '{"version":5,"story":{},"main_characters":[CHARACTER],"supporting_characters":[CHARACTER],'
        '"animals":[CHARACTER],"supernatural_entities":[CHARACTER],"locations":[],"props":[],"scene_map":[]}; '
        'CHARACTER={"character_id":"stable id/name","name":"","entity_type":"human|animal|supernatural_unknown",'
        '"needs_ref":true,"appearance":{"age":"","gender":"","body":"","height":"","skin":"","hair":"","face":"","eyes":""},'
        '"occupation":"","social_status":"","wardrobe":{"top":"","bottom":"","footwear":"","outerwear":"","accessories":"",'
        '"colors":"","materials":"","condition":"","overall_style":"","source":"explicit|inferred","reason":""},'
        '"costume_identity":{"signature_palette":["",""],"signature_silhouette":"","signature_material_or_texture":"","recognition_cue":"","avoid_overlap_with":["character_id"]},'
        '"visual_identity":"","evidence":[],"assumptions":[]}. '
        'animal/supernatural ที่ไม่มีเสื้อผ้าจริงให้ wardrobe=null และ costume_identity=null; ห้ามสร้าง alias ภาษาไทยซ้ำกับ canonical fields.'
    )
