# -*- coding: utf-8 -*-
"""Stylist pass: every character's face, hair and outfit designed to fit the story and be clearly different.

After a Context is built, GPT reviews all characters together and rewrites only the look fields of
the ones that could be drawn alike (same sex / similar age), unless the script says they look alike
(twins, parent and child). Names, roles and anything the script states are never changed; the
answer is merged back by name, so a bad reply cannot damage the rest of the Context.
"""
from __future__ import annotations

import json

LOOK_FIELDS = ("ใบหน้า", "ดวงตา", "ทรงผม", "เสื้อผ้า", "ลักษณะเด่น", "visual_identity")


def request(context: dict, picture_style: str = "") -> str:
    people = [
        {"name": c.get("name"), **{k: c.get(k) for k in ("อายุ", "เพศ", "บทบาท", "รูปร่าง", "สีผิว", *LOOK_FIELDS)}}
        for c in (context.get("characters") or []) if isinstance(c, dict) and c.get("name")
    ]
    story = context.get("story") if isinstance(context.get("story"), dict) else {}
    setting = ", ".join(str(story.get(k)) for k in ("era", "main_location", "story_type_label") if story.get(k))
    return (
        "คุณคือสไตลิสต์และผู้ออกแบบตัวละครของภาพยนตร์เรื่องนี้ "
        + (f"(ยุค/สถานที่/แนวเรื่อง: {setting}) " if setting else "")
        + (f"แนวภาพของเรื่อง: {picture_style}. " if picture_style else "")
        + "ออกแบบหน้าตาตัวละครทั้งหมดพร้อมกัน: รูปที่สร้างมักออกมาหน้าคล้ายกันไปหมด. "
        "หาคู่ที่เสี่ยงหน้าคล้ายกัน (เพศเดียวกัน วัยใกล้กัน หรือคำบรรยายหน้าตาคลุมเครือ) แล้วเขียนหน้าตาใหม่ให้แต่ละคนต่างกันชัดเจนที่สุด: "
        "รูปหน้า (กลม/เหลี่ยม/ยาว/รูปไข่ คางแหลม/คางกว้าง), โหนกแก้ม, คิ้ว, ตา (ชั้นเดียว/สองชั้น ตาโต/ตาเรียว), จมูก, ปาก, "
        "ทรงผม (ความยาว สี ทรง การแสกหรือมัด) และจุดสังเกต 1 อย่างที่เห็นได้จากระยะไกล (เช่น ไฝ แผลเป็น ผมขาวแซม แว่น). "
        "ออกแบบชุดให้เข้ากับยุค ท้องถิ่น ฐานะ อาชีพ และนิสัยของแต่ละคน (เนื้อผ้า สี ความเก่าใหม่ เครื่องประดับ) "
        "ให้ทั้งเรื่องเป็นโทนเดียวกับแนวภาพ แต่แต่ละคนจำได้ทันทีจากสีและทรงของชุด. "
        "ห้ามขัดกับสิ่งที่บทบอกไว้ (อายุ เพศ บทบาท หรือหน้าตา/ชุดที่บทระบุ) ห้ามเปลี่ยนชื่อ; "
        "ถ้าบทบอกว่าหน้าเหมือนกัน (ฝาแฝด พ่อลูก) ให้คงความเหมือนแต่ยังต่างกันด้วยทรงผมหรือจุดสังเกต. "
        "ตอบ JSON เท่านั้น: {\"characters\":[{\"name\":\"ชื่อเดิมตรงตัว\",\"ใบหน้า\":\"\",\"ดวงตา\":\"\",\"ทรงผม\":\"\",\"เสื้อผ้า\":\"\","
        "\"ลักษณะเด่น\":\"\",\"visual_identity\":\"ประโยคสั้นที่บอกว่าคนนี้ต่างจากคนอื่นอย่างไร\"}]} "
        "ใส่ทุกตัวละคร (ตัวที่ไม่ต้องเปลี่ยนให้คงค่าเดิม).\n\n"
        + json.dumps(people, ensure_ascii=False)
    )


def merge(context: dict, reply: dict) -> int:
    """Copy the new look fields into the Context by exact name. Returns how many characters changed."""
    if not isinstance(reply, dict):
        return 0
    by_name = {str(c.get("name")): c for c in (context.get("characters") or []) if isinstance(c, dict)}
    changed = 0
    for item in reply.get("characters") or []:
        target = by_name.get(str((item or {}).get("name") if isinstance(item, dict) else ""))
        if target is None:
            continue
        before = {k: target.get(k) for k in LOOK_FIELDS}
        for key in LOOK_FIELDS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                target[key] = value.strip()
        if before != {k: target.get(k) for k in LOOK_FIELDS}:
            changed += 1
    return changed
