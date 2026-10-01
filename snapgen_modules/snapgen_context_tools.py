# -*- coding: utf-8 -*-
"""Prompt Context tools for SnapGen.
Standalone on purpose: keep snapgen_gui_v2.py launcher thin.
"""
import json
import time
from pathlib import Path

UNKNOWN_VALUES = {"", "ไม่ระบุ", "unknown", "null", "None", None}
CHAR_FIELDS = ["อายุ", "เพศ", "สีผิว", "ทรงผม", "ใบหน้า", "เสื้อผ้า", "ลักษณะเด่น"]

def is_unknown(value):
    """Accept structured GPT fields without hashing lists or dictionaries."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in UNKNOWN_VALUES
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False

def location_name(value):
    """Return one stable text name from string/list/object location fields."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("place") or value.get("location") or ""
    if isinstance(value, (list, tuple, set)):
        value = " / ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def paths(base):
    base = Path(base)
    return {
        "master": base / "context_master.json",
        "snapshot": base / "context_master.last.json",
        "ref_json": base / "prompt_ref_context.json",
        "ref_txt": base / "prompt_ref_context.txt",
    }


def load_context_any(base):
    ps = paths(base)
    for p in [ps["master"], ps["ref_json"]]:
        try:
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    try:
        txt = ps["ref_txt"].read_text(encoding="utf-8")
    except Exception:
        txt = ""
    return {"version": 1, "story": {"raw": txt}, "characters": [], "locations": [], "props": [], "scene_map": []}


def invent_missing_char_detail(ch, field):
    name = ch.get("name", "ตัวละคร")
    defaults = {
        "อายุ": "วัยผู้ใหญ่ตอนต้น (สมมุติเพื่อภาพ)",
        "เพศ": "ไม่ระบุเพศชัดเจน (สมมุติเพื่อภาพ)",
        "สีผิว": "ผิวสองสีธรรมชาติแบบไทย (สมมุติเพื่อภาพ)",
        "ทรงผม": "ผมสีดำทรงเรียบร้อย ไม่ปิดหน้า (สมมุติเพื่อภาพ)",
        "ใบหน้า": "ใบหน้าคนไทยสมจริง แสงสม่ำเสมอ มองเห็นชัด (สมมุติเพื่อภาพ)",
        "เสื้อผ้า": "เสื้อผ้าร่วมสมัยเรียบง่าย สีไม่ฉูดฉาด (สมมุติเพื่อภาพ)",
        "ลักษณะเด่น": f"บุคลิกจำง่ายของ{name} แต่ยังสมจริง (สมมุติเพื่อภาพ)",
    }
    return defaults.get(field, "รายละเอียดสมจริง (สมมุติเพื่อภาพ)")


def normalize_context_master(base, data=None, invent=False):
    data = data if isinstance(data, dict) else load_context_any(base)
    master = {
        "version": 3,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "story": data.get("story") if isinstance(data.get("story"), dict) else {"raw": str(data.get("story", ""))},
        "characters": data.get("characters") if isinstance(data.get("characters"), list) else [],
        "locations": data.get("locations") if isinstance(data.get("locations"), list) else [],
        "props": data.get("props") if isinstance(data.get("props"), list) else [],
        "scene_map": data.get("scene_map") if isinstance(data.get("scene_map"), list) else [],
        "visual_rules": data.get("visual_rules") if isinstance(data.get("visual_rules"), dict) else {},
        "forbidden": data.get("forbidden") if isinstance(data.get("forbidden"), list) else [],
        "locks": data.get("locks") if isinstance(data.get("locks"), dict) else {},
    }

    loc_names = {
        location_name(loc.get("name"))
        for loc in master["locations"] if isinstance(loc, dict) and location_name(loc.get("name"))
    }
    story = master.get("story", {})
    for raw_name in story.get("key_places", []) if isinstance(story, dict) else []:
        name = location_name(raw_name)
        if name and name not in loc_names:
            master["locations"].append({"name": name, "type": "location"})
            loc_names.add(name)
    for sc in master["scene_map"]:
        if isinstance(sc, dict):
            name = location_name(sc.get("place") or sc.get("location"))
            if name and name not in loc_names:
                master["locations"].append({"name": name, "type": "location", "note": sc.get("note", "")})
                loc_names.add(name)

    for ch in master["characters"]:
        if not isinstance(ch, dict):
            continue
        ch.setdefault("locks", {"face": True, "clothes": True, "allow_outfit_change_by_scene": False})
        for f in CHAR_FIELDS:
            if is_unknown(ch.get(f)):
                ch[f] = invent_missing_char_detail(ch, f) if invent else "ไม่ระบุ"
    return master


def context_health(base, data=None):
    m = normalize_context_master(base, data, invent=False)
    issues = []
    total = 0
    ok = 0
    for ch in m.get("characters", []):
        if not isinstance(ch, dict):
            continue
        name = ch.get("name", "(ไม่มีชื่อ)")
        for f in CHAR_FIELDS:
            total += 1
            if not is_unknown(ch.get(f)):
                ok += 1
            else:
                issues.append(f"{name} ไม่มี {f}")
    for key, msg in [("locations", "ไม่มีสถานที่"), ("props", "ไม่มี props"), ("scene_map", "ไม่มี scene map")]:
        total += 1
        if m.get(key):
            ok += 1
        else:
            issues.append(msg)
    score = int((ok / total) * 100) if total else 0
    return score, issues, m


def write_context_master(base, data=None, invent=False, sync_ref=True):
    """Normalize and persist one canonical Context for every SnapGen page."""
    ps = paths(base)
    old = ps["master"].read_text(encoding="utf-8") if ps["master"].exists() else ""
    if old:
        ps["snapshot"].write_text(old, encoding="utf-8")
    master = normalize_context_master(base, data=data, invent=invent)
    encoded = json.dumps(master, ensure_ascii=False, indent=2) + "\n"
    ps["master"].write_text(encoded, encoding="utf-8")
    if sync_ref:
        ps["ref_json"].write_text(encoded, encoding="utf-8")
    return master


def context_diff_text(base):
    ps = paths(base)
    if not ps["snapshot"].exists() or not ps["master"].exists():
        return "ยังไม่มี snapshot สำหรับเทียบ — กด Normalize ก่อนอย่างน้อย 2 ครั้ง"
    try:
        old = json.loads(ps["snapshot"].read_text(encoding="utf-8"))
        new = json.loads(ps["master"].read_text(encoding="utf-8"))
    except Exception as e:
        return f"อ่าน diff ไม่ได้: {e}"
    lines = []

    def names(x, key):
        return {i.get("name") for i in x.get(key, []) if isinstance(i, dict) and i.get("name")}

    for key, label in [("characters", "ตัวละคร"), ("locations", "สถานที่"), ("props", "Prop")]:
        add = sorted(names(new, key) - names(old, key))
        rem = sorted(names(old, key) - names(new, key))
        if add:
            lines.append(f"เพิ่ม{label}: " + ", ".join(add))
        if rem:
            lines.append(f"ลบ{label}: " + ", ".join(rem))
    return "\n".join(lines) if lines else "Context ไม่เปลี่ยนในระดับชื่อรายการ"


def context_preview_text(base):
    score, issues, m = context_health(base, load_context_any(base))
    return "\n".join([
        f"Context Score: {score}%",
        f"Characters: {len(m.get('characters', []))} | Locations: {len(m.get('locations', []))} | Props: {len(m.get('props', []))} | Scenes: {len(m.get('scene_map', []))}",
        "",
        "ขาด / ควรเติม:",
        *(issues[:30] or ["พร้อมใช้"]),
    ])


def prompt_preview_text(base):
    m = normalize_context_master(base, load_context_any(base), invent=False)
    lines = ["Prompt Preview Source", "", "Characters:"]
    for ch in m.get("characters", [])[:5]:
        lines.append(f"- {ch.get('name')}: {ch.get('อายุ')} | {ch.get('ใบหน้า')} | {ch.get('เสื้อผ้า')} | lock={ch.get('locks')}")
    lines += ["", "Scenes:"]
    for sc in m.get("scene_map", [])[:5]:
        if isinstance(sc, dict):
            lines.append(f"- {sc.get('place') or sc.get('location')}: {sc.get('note') or sc.get('summary') or ''}")
    return "\n".join(lines)


def install(globals_dict):
    """Expose old function names used by snapgen_gui_v2.py patch code."""
    base = Path(globals_dict.get("BASE", Path.cwd()))
    globals_dict.update({
        "_load_context_any": lambda: load_context_any(base),
        "_normalize_context_master": lambda data=None, invent=False: normalize_context_master(base, data, invent),
        "_context_health": lambda data=None: context_health(base, data),
        "_write_context_master": lambda data=None, invent=False: write_context_master(base, data, invent),
        "_context_diff_text": lambda: context_diff_text(base),
        "_context_preview_text": lambda: context_preview_text(base),
        "_prompt_preview_text": lambda: prompt_preview_text(base),
    })
