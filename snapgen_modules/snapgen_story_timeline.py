# -*- coding: utf-8 -*-
"""The story's timeline (time of day of each part), kept in the team's shared Context JSON.

Several people make different parts of one film. Each GPT used to pick day or
night from its own part only, so the parts did not match. The timeline is
written once from the whole story, stored as ``context["timeline"]`` in the
Context sidecar every PC reads, and each part's shots take their time from it.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

TIMES = ("เช้ามืด", "เช้า", "กลางวัน", "บ่าย", "เย็น", "พลบค่ำ", "กลางคืน", "ดึก")
_LETTERS = re.compile(r"[\s“”\"'‘’.,!?…:：()\-]")


def request(story: str) -> str:
    return (
        "อ่านเรื่องทั้งเรื่องด้านล่าง แล้วแบ่งเป็นช่วงตามเวลาในเรื่อง (ทุกครั้งที่ช่วงเวลาหรือวันเปลี่ยน ให้เริ่มช่วงใหม่) "
        "เพื่อให้ทีมหลายคนที่ทำคนละท่อนของหนังเรื่องนี้ใช้เวลากลางวัน/กลางคืนตรงกัน.\n"
        f"time ต้องเป็นหนึ่งใน: {', '.join(TIMES)}. "
        "ถ้าเรื่องไม่บอกตรงๆ ให้เลือกเวลาที่สมเหตุสมผลที่สุดจากเหตุการณ์ และให้ช่วงที่ต่อเนื่องกันในวันเดียวกันไล่เวลาไปข้างหน้า. "
        "starts_with = ข้อความ 20-40 ตัวอักษรแรกของช่วงนั้น คัดลอกตรงตัวจากเรื่อง (ใช้หาตำแหน่ง). "
        "past = true ถ้าเป็นเหตุการณ์ในอดีต (ย้อนอดีต) ไม่ใช่ปัจจุบันของผู้เล่า. "
        "light = แสงของช่วงนั้นสั้นๆ (เช่น แดดบ่ายอ่อน, ฟ้าส้มหลังพระอาทิตย์ตก, แสงจันทร์และตะเกียง). "
        "ช่วงแรกต้องเริ่มที่ต้นเรื่อง เรียงตามลำดับในเรื่อง ไม่ข้ามส่วนไหน.\n"
        'ตอบ JSON เท่านั้น: {"timeline":[{"starts_with":"","part":"","time":"","past":false,"light":""}]}\n\n'
        "เรื่องทั้งเรื่อง:\n" + story)


def parse(reply: dict) -> list:
    rows = []
    for item in (reply or {}).get("timeline") or []:
        if not isinstance(item, dict):
            continue
        time = str(item.get("time") or "").strip()
        start = str(item.get("starts_with") or "").strip()
        if time in TIMES and start:
            rows.append({"starts_with": start, "part": str(item.get("part") or "").strip(), "time": time,
                         "past": str(item.get("past")).lower() == "true", "light": str(item.get("light") or "").strip()})
    return rows


def _position(story: str, quote: str, after: int = 0) -> int:
    """Index of ``quote`` in the letters-only story at/after ``after`` (fuzzy), or -1."""
    want = _LETTERS.sub("", quote)[:40]
    if not want:
        return -1
    found = story.find(want, after)
    if found >= 0:
        return found
    best, best_at = 0.0, -1
    step = max(1, len(want) // 2)
    for at in range(after, max(after, len(story) - len(want)) + 1, step):
        ratio = SequenceMatcher(None, story[at:at + len(want)], want, autojunk=False).ratio()
        if ratio > best:
            best, best_at = ratio, at
    return best_at if best >= 0.6 else -1


def sections_for(timeline: list, story: str, part: str) -> list:
    """The timeline rows the given part of the story falls in (in order)."""
    full = _LETTERS.sub("", story or "")
    piece = _LETTERS.sub("", part or "")
    if not timeline or not full or not piece:
        return []
    starts, cursor = [], 0
    for row in timeline:
        at = _position(full, row["starts_with"], cursor)
        if at < 0:
            at = cursor
        starts.append(at)
        cursor = at
    begin = _position(full, piece[:60])
    if begin < 0:
        return []
    end = begin + len(piece)
    chosen = []
    for k, row in enumerate(timeline):
        lo = starts[k]
        hi = starts[k + 1] if k + 1 < len(starts) else len(full) + 1
        if lo < end and hi > begin:
            chosen.append(row)
    return chosen


def note(rows: list) -> str:
    """The line every shot of the part is planned with."""
    if not rows:
        return ""
    if len(rows) == 1:
        r = rows[0]
        return (f". เวลาในเรื่องของท่อนนี้ (ล็อกตาม Context ของทีม ทุกเครื่องใช้ตรงกัน): {r['time']}"
                + (f" — {r['light']}" if r["light"] else "") + (" (ย้อนอดีต)" if r["past"] else "")
                + " — ทุกช็อตต้องเป็นเวลานี้ ห้ามเปลี่ยนเป็นกลางวัน/กลางคืนเอง")
    lines = "; ".join(f"ตั้งแต่ “{r['starts_with'][:30]}” = {r['time']}" + (f" ({r['light']})" if r["light"] else "")
                      + (" ย้อนอดีต" if r["past"] else "") for r in rows)
    return (". เวลาในเรื่อง (ล็อกตาม Context ของทีม ทุกเครื่องใช้ตรงกัน): " + lines
            + " — แต่ละช็อตใช้เวลาของช่วงที่คำบรรยายของช็อตนั้นอยู่ ห้ามเปลี่ยนเวลาเอง")


def add_to_sidecar(path: Path, timeline: list) -> bool:
    """Write the timeline into the shared Context file, only if it still has none (another PC may have
    added one meanwhile: theirs stays). Nothing else in the file changes."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    context = data.get("context") if isinstance(data.get("context"), dict) else data
    if context.get("timeline"):
        return False
    context["timeline"] = timeline
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
