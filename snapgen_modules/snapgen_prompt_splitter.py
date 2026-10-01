# -*- coding: utf-8 -*-
"""One-click Prompt Splitter for SnapGen.

Standalone module: reads current scene + context_master, asks bridge GPT to
produce exactly 10 scene prompts + prompt 11 storyboard grid summary.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BRIDGE_URL = "http://127.0.0.1:8000/v1/chat/completions"
BRIDGE_HEALTH_URL = "http://127.0.0.1:8000/health"
BRIDGE_KEY = "local-dev-key"
MODEL = "auto"
BRIDGE_TIMEOUT_SECONDS = 620

PROMPT_DIRECTOR_SYSTEM = """คุณคือ Prompt Director สำหรับสร้างภาพ/วิดีโอจากฉากเดียว

INPUT:
1. CURRENT SCENE = เหตุการณ์ที่ต้องแตก prompt
2. CONTEXT MASTER = ข้อมูลตัวละคร สถานที่ props continuity

RULES:
- ใช้เหตุการณ์จาก CURRENT SCENE เท่านั้น
- ห้ามเพิ่มเหตุการณ์ใหม่จาก full story
- ใช้ CONTEXT MASTER เพื่อคงหน้าตา เสื้อผ้า สถานที่ props
- อ่าน CURRENT SCENE ก่อนภาพทุกครั้ง: บทเป็นผู้กำหนดว่าใครทำอะไรกับใครและความสัมพันธ์ของตัวละคร ส่วนภาพใช้ยืนยันตำแหน่ง/องค์ประกอบเท่านั้น ห้ามสลับผู้กระทำกับผู้ถูกกระทำ เช่น แม่อุ้มลูกต้องเป็นแม่ที่อุ้มลูก
- ถ้าข้อมูลตัวละครขาด ให้เติมเองและใส่ "(สมมุติเพื่อภาพ)"
- prompt ต้องเรียงตาม beat ของฉาก
- video prompt ห้ามกำหนดหรือกล่าวถึงการเคลื่อนกล้อง การซูม push-in pull-out pan tilt dolly tracking handheld หรือ static camera; เว้นส่วนกล้องไว้ให้เมนู Camera ของโปรแกรมเป็นผู้กำหนด และห้ามเติมการเคลื่อนกล้องเป็นค่าเริ่มต้น
- สร้าง 10 prompts + prompt 11 เป็น storyboard grid summary

OUTPUT FORMAT:
ตอบเป็น JSON เท่านั้น ห้าม markdown ห้ามคำอธิบายเพิ่ม
{
  "beats": ["..."],
  "prompts": [
    {"number": 1, "prompt": "..."},
    ...,
    {"number": 11, "prompt": "storyboard grid summary ..."}
  ],
  "validation": {
    "prompt_count": 11,
    "used_current_scene_only": true,
    "notes": []
  }
}
"""

UNKNOWN = {"", "ไม่ระบุ", "unknown", "null", "None", None}


def load_context(base: str | Path) -> dict[str, Any]:
    base = Path(base)
    for name in ["context_master.json", "prompt_ref_context.json"]:
        p = base / name
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {"characters": [], "locations": [], "props": [], "scene_map": []}


def _scene_terms(scene: str) -> set[str]:
    # Thai-safe-ish token extraction: keep meaningful chunks, skip tiny chars.
    return {t.strip(" ,.:;!?()[]{}\"'“”‘’") for t in re.split(r"\s+", scene) if len(t.strip()) >= 2}


def compact_context_for_scene(context: dict[str, Any], current_scene: str) -> dict[str, Any]:
    """Use only context likely relevant to current scene. Keeps payload small."""
    terms = _scene_terms(current_scene)

    def hit_item(item: dict[str, Any]) -> bool:
        blob = json.dumps(item, ensure_ascii=False)
        name = str(item.get("name") or item.get("place") or item.get("location") or "")
        return bool(name and name in current_scene) or any(t and t in blob and t in current_scene for t in terms)

    chars = [c for c in context.get("characters", []) if isinstance(c, dict) and hit_item(c)]
    locs = [l for l in context.get("locations", []) if isinstance(l, dict) and hit_item(l)]
    props = [p for p in context.get("props", []) if isinstance(p, dict) and hit_item(p)]
    scenes = [s for s in context.get("scene_map", []) if isinstance(s, dict) and hit_item(s)]

    # If scene text names nobody, keep small fallback so GPT still has continuity.
    if not chars:
        chars = [c for c in context.get("characters", []) if isinstance(c, dict)][:5]
    if not locs:
        locs = [l for l in context.get("locations", []) if isinstance(l, dict)][:5]
    if not props:
        props = [p for p in context.get("props", []) if isinstance(p, dict)][:8]

    return {
        "characters": chars[:8],
        "locations": locs[:6],
        "props": props[:10],
        "scene_map_matches": scenes[:6],
    }


def build_user_prompt(current_scene: str, context_master: dict[str, Any]) -> str:
    compact = compact_context_for_scene(context_master, current_scene)
    return "\n".join([
        "CURRENT SCENE:",
        current_scene.strip(),
        "",
        "CONTEXT MASTER (relevant subset):",
        json.dumps(compact, ensure_ascii=False, indent=2),
        "",
        "TASK:",
        "แตก CURRENT SCENE เป็น 10 prompts เรียงตาม beat ของฉาก และ prompt 11 เป็น storyboard grid summary.",
        "ห้ามใช้เหตุการณ์นอก CURRENT SCENE.",
    ])


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("GPT output is not JSON object")
    return data


def check_bridge(timeout: int = 3) -> None:
    try:
        req = urllib.request.Request(
            BRIDGE_HEALTH_URL,
            headers={"Authorization": f"Bearer {BRIDGE_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) >= 400:
                raise ConnectionError("GPT Bridge ไม่พร้อมใช้งาน")
    except Exception as exc:
        raise ConnectionError("GPT Bridge ไม่พร้อม กรุณารอให้สถานะ GPT บนโปรแกรมเป็นพร้อม แล้วลองใหม่") from exc


def call_bridge_chat(system_prompt: str, user_prompt: str, timeout: int = BRIDGE_TIMEOUT_SECONDS) -> str:
    body = {
        "model": MODEL,
        "chatgpt_image_intercept": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        BRIDGE_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {BRIDGE_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GPT Bridge ตอบกลับผิดพลาด HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("GPT ใช้เวลานานเกิน 10 นาที กรุณากดลองใหม่") from exc
        raise ConnectionError("เชื่อมต่อ GPT Bridge ไม่ได้ กรุณาตรวจสถานะ GPT บนโปรแกรม") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise TimeoutError("GPT ใช้เวลานานเกิน 10 นาที กรุณากดลองใหม่") from exc
    data = json.loads(raw)
    return data["choices"][0]["message"]["content"]


def validate_split(result: dict[str, Any], current_scene: str) -> tuple[bool, list[str]]:
    issues: list[str] = []
    prompts = result.get("prompts")
    if not isinstance(prompts, list):
        return False, ["ไม่มี prompts array"]
    if len(prompts) != 11:
        issues.append(f"จำนวน prompt ต้องเป็น 11 แต่ได้ {len(prompts)}")
    nums = [p.get("number") for p in prompts if isinstance(p, dict)]
    if nums != list(range(1, 12)):
        issues.append(f"เลข prompt ไม่เรียง 1-11: {nums}")
    for p in prompts[:10]:
        txt = str(p.get("prompt", "")) if isinstance(p, dict) else ""
        if len(txt) < 40:
            issues.append(f"prompt {p.get('number') if isinstance(p, dict) else '?'} สั้นเกินไป")
    if prompts and isinstance(prompts[-1], dict):
        last = prompts[-1].get("prompt", "")
        if "storyboard" not in str(last).lower() and "grid" not in str(last).lower() and "ตาราง" not in str(last):
            issues.append("prompt 11 ต้องเป็น storyboard grid summary")
    return not issues, issues


def split_scene_prompts(base: str | Path, current_scene: str, retry_once: bool = True) -> dict[str, Any]:
    check_bridge()
    context = load_context(base)
    user_prompt = build_user_prompt(current_scene, context)
    raw = call_bridge_chat(PROMPT_DIRECTOR_SYSTEM, user_prompt)
    result = _extract_json(raw)
    ok, issues = validate_split(result, current_scene)
    if ok or not retry_once:
        result.setdefault("validation", {})
        result["validation"].update({"local_ok": ok, "local_issues": issues})
        return result

    fix_prompt = user_prompt + "\n\nVALIDATION FAILED:\n" + "\n".join(f"- {x}" for x in issues) + "\nแก้ใหม่ให้ผ่านทุกข้อ ตอบ JSON เท่านั้น"
    raw2 = call_bridge_chat(PROMPT_DIRECTOR_SYSTEM, fix_prompt)
    result2 = _extract_json(raw2)
    ok2, issues2 = validate_split(result2, current_scene)
    result2.setdefault("validation", {})
    result2["validation"].update({"local_ok": ok2, "local_issues": issues2, "retried": True})
    return result2


def render_prompts_text(result: dict[str, Any]) -> str:
    prompts = result.get("prompts", [])
    lines = []
    for p in prompts:
        if isinstance(p, dict):
            lines.append(f"{p.get('number')}. {p.get('prompt', '').strip()}")
    return "\n\n".join(lines).strip()


def split_scene_to_text(base: str | Path, current_scene: str) -> str:
    return render_prompts_text(split_scene_prompts(base, current_scene))
