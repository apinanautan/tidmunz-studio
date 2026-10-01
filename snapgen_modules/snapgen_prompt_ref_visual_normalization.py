# -*- coding: utf-8 -*-
"""Install the final Prompt-Ref Character Visual Bible architecture.

AI owns semantics. Python canonicalizes, validates, requests only missing fields,
and merges repairs without overwriting existing non-empty values.
"""
from __future__ import annotations

import json

from snapgen_character_visual_bible import (
    apply_character_repairs,
    canonical_schema_text,
    canonicalize_context,
    context_missing_fields,
)


def _quality_rules():
    return (
        "CHARACTER VISUAL BIBLE RULES: AI เป็น semantic source of truth เพียงจุดเดียว. "
        "อ่านบทแล้วตัดสิน identity, appearance, occupation, social_status และ wardrobe ของตัวละครแต่ละตัวเอง. "
        "ถ้าบทไม่ระบุ ให้ infer อย่างสมเหตุผลจากยุค ประเทศ/วัฒนธรรม สถานที่ อายุ อาชีพ ฐานะ บทบาท และเหตุการณ์ แล้วใส่เหตุผลใน assumptions/wardrobe.reason. "
        "wardrobe.source ต้องเป็น explicit เมื่อบทระบุจริง หรือ inferred เมื่อสมมุติเพื่อภาพ. "
        "ห้ามใช้ alias ภาษาไทยซ้ำกับ canonical fields; ใช้ appearance.age/gender/body/height/skin/hair/face/eyes และ wardrobe.top/bottom/footwear/outerwear/accessories/colors/materials/condition/overall_style/source/reason เท่านั้น. "
        "animal หรือ supernatural ที่ไม่ได้ใส่เสื้อผ้าจริงให้ wardrobe=null และ costume_identity=null. "
        "character_id ต้องคงที่และไม่ซ้ำ; ใช้ชื่อเดิมจากบทได้เมื่อไม่มี id อื่น. "
        "evidence เก็บเฉพาะข้อเท็จจริงจากบท; assumptions เก็บสิ่งที่ infer. "
        "COSTUME DESIGNER RULE: wardrobe แต่ละตัวละครต้องสะท้อน identity เฉพาะของตัวเอง — "
        "ระบุสีจริง (เช่น กรมท่าซีด ชมพูอ่อน ขาวนวล) วัสดุจริง (เช่น ผ้าฝ้ายบาง เดนิม โทเร) ทรงจริง — "
        "ห้ามตอบ 'สีหม่น' 'วัสดุที่หาได้ในยุค' 'เสื้อผ้าที่เหมาะกับตัวละคร' หรือ template กลางๆ. "
        "CROSS-CHARACTER: ก่อน finalize เปรียบเทียบ human characters ทั้งหมด — "
        "ห้ามให้ 2 ตัวละครหลักมี signature_palette/top/silhouette เดียวกัน; ทุกคนต้องจำแนกกันได้จาก full-body shot. "
        "COSTUME_IDENTITY REQUIRED สำหรับ human ที่ needs_ref=true: "
        "costume_identity.signature_palette=[\"สีหลัก\",\"สีรอง\"], signature_silhouette=\"ทรงเสื้อ+กางเกง/กระโปรง\", "
        "signature_material_or_texture=\"วัสดุ\", recognition_cue=\"visual marker ที่จำได้ทันที\", avoid_overlap_with=[\"character_id อื่น\"]. "
    )


def install_prompt_ref_visual_normalization(g):
    if not isinstance(g, dict):
        return False
    if g.get("_prompt_ref_canonical_visual_bible_installed"):
        return False
    parse_json = g.get("_parse_bridge_context_json")
    prompt_chat = g.get("_prompt_ref_chat")
    persist = g.get("_persist_prompt_ref_breakdown")
    if not all(callable(fn) for fn in (parse_json, prompt_chat, persist)):
        return False

    def normalize(parsed):
        return canonicalize_context(parsed)

    def incomplete(parsed):
        return bool(context_missing_fields(parsed))

    def audit(reply, story_text=""):
        # No second semantic audit/regeneration pass. Validate the AI result as-is.
        parsed = normalize(parse_json(reply))
        missing = context_missing_fields(parsed)
        if missing:
            targets = []
            for char in parsed.get("characters") or []:
                cid = str(char.get("character_id") or "").strip()
                if cid in missing:
                    targets.append({
                        "character_id": cid,
                        "name": char.get("name"),
                        "entity_type": char.get("entity_type"),
                        "missing_fields": missing[cid],
                    })
            repair_prompt = (
                "CHARACTER VISUAL BIBLE FIELD REPAIR. เติมเฉพาะ field ที่ระบุว่า missing เท่านั้นจาก FULL STORY ในประวัติเดียวกัน. "
                "ห้าม regenerate character object, ห้ามแก้ field ที่มีข้อมูลอยู่แล้ว, ห้ามเปลี่ยน character_id/name/entity_type/needs_ref. "
                "ตอบ JSON object รูปแบบ {\"repairs\":[{\"character_id\":\"...\", ...เฉพาะ field ที่ขาด...}]} เท่านั้น. "
                "ถ้าต้อง infer ให้ใส่ค่า inferred และเหตุผลใน assumptions หรือ wardrobe.reason โดยไม่แตะ evidence เดิม. "
                "WARDROBE SPECIFICITY: ระบุสีจริง วัสดุจริง ทรงจริง — ห้ามใช้ 'สีหม่น' 'วัสดุที่หาได้ในยุค' 'เหมาะกับตัวละคร'. "
                "COSTUME_IDENTITY: ถ้า entity_type=human และ needs_ref=true ให้เพิ่ม costume_identity "
                "{signature_palette, signature_silhouette, signature_material_or_texture, recognition_cue, avoid_overlap_with} "
                "ที่ต่างจากตัวละครอื่นที่มีอยู่แล้วในเรื่อง. "
                "canonical schema reference: " + canonical_schema_text()
                + "\nMISSING_TARGETS:\n" + json.dumps(targets, ensure_ascii=False)
            )
            repaired = prompt_chat([{"role": "user", "content": repair_prompt}], require_history=True)
            if repaired:
                repair_obj = parse_json(repaired)
                parsed = apply_character_repairs(parsed, repair_obj)
            remaining = context_missing_fields(parsed)
            if remaining:
                raise RuntimeError(
                    "Character Visual Bible ยังขาด field หลัง repair: "
                    + json.dumps(remaining, ensure_ascii=False)
                )
        persist(parsed)
        return parsed

    g["_prompt_ref_context_quality_rules"] = _quality_rules
    g["_prompt_ref_context_schema_text"] = canonical_schema_text
    g["_normalize_prompt_ref_breakdown"] = normalize
    g["_prompt_ref_visual_bible_incomplete"] = incomplete
    g["_audit_prompt_ref_context"] = audit
    g["_prompt_ref_canonical_visual_bible_installed"] = True
    g["_prompt_ref_visual_alias_patch_installed"] = True  # legacy marker compatibility
    return True
