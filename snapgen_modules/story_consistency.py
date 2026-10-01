"""Small, shared helpers for keeping Storyboard image jobs traceable.

This module deliberately contains no UI or provider code.  It owns only the
portable run metadata and the deterministic Storyboard panel crop used by the
desktop and mobile image paths.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any


def atomic_json_write(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]


def canonical_identity_key(value: str) -> str:
    """Stable person identity key for GPT honorific differences."""
    value = re.sub(r"\.(png|jpg|jpeg|webp)$", "", str(value or ""), flags=re.I)
    value = re.sub(r"[\s_\-./\\]+", "", value.casefold())
    return re.sub(r"^(?:พระนาง|พระราชา|พระ|นางสาว|นาง|นาย|ท้าว|แม่ทัพ|คุณ)", "", value)


def run_path(data_dir: str | Path, run_id: str) -> Path:
    return Path(data_dir) / "meta" / "story_runs" / f"{run_id}.json"


def save_run(data_dir: str | Path, run: dict[str, Any]) -> Path:
    path = run_path(data_dir, str(run.get("run_id") or "unknown"))
    atomic_json_write(path, run)
    return path


def load_run(data_dir: str | Path, run_id: str) -> dict[str, Any]:
    try:
        value = json.loads(run_path(data_dir, run_id).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def current_scene_text(data_dir: str | Path, run_id: str | None = None) -> str:
    if run_id:
        run = load_run(data_dir, run_id)
        if run.get("scene_text"):
            return str(run["scene_text"]).strip()
    data_dir = Path(data_dir)
    for path in (data_dir / "prompt_ref_storyboard_direct.json", data_dir / "prompt_ref_storyboard_image.json", data_dir / "prompt_ref_scene_draft.txt"):
        try:
            if path.suffix == ".json":
                value = json.loads(path.read_text(encoding="utf-8"))
                text = value.get("scene_text") if isinstance(value, dict) else ""
            else:
                text = path.read_text(encoding="utf-8", errors="replace")
            if str(text or "").strip():
                return str(text).strip()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return ""


def is_storyboard_derived(prompt: str, prompt_index: int | None = None) -> bool:
    if prompt_index is not None and int(prompt_index) == 11:
        return False
    return bool(re.search(r"storyboard\s*(?:ช่อง|shot|panel)|อ้างอิงองค์ประกอบ|อ้างอิงจากสตอรี่บอร์ด", str(prompt or ""), re.I))


def storyboard_panel_crop(
    storyboard_path: str | Path,
    data_dir: str | Path,
    run_id: str,
    slot: int,
    panel_count: int | None = None,
) -> Path:
    """Crop one panel and remove its caption strip before it reaches image AI.

    SnapGen's storyboard contract is a 2-column sheet.  Refusing unknown
    layouts is safer than silently attaching the wrong panel to a character.
    """
    storyboard = Path(storyboard_path)
    if not storyboard.is_file():
        raise ValueError("ไม่พบไฟล์ Storyboard สำหรับ Slot นี้")
    try:
        slot = int(slot)
    except (TypeError, ValueError) as exc:
        raise ValueError("เลขช่อง Storyboard ไม่ถูกต้อง") from exc
    if panel_count is None:
        panel_count = 0
        for candidate in (Path(data_dir) / "prompt_ref_storyboard_direct.json", Path(data_dir) / "prompt_ref_storyboard_image.json"):
            try:
                obj = json.loads(candidate.read_text(encoding="utf-8"))
                panel_count = int(obj.get("panel_count") or obj.get("count") or 0) if isinstance(obj, dict) else 0
                if panel_count:
                    break
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    if not 2 <= int(panel_count or 0) <= 12 or not 1 <= slot <= int(panel_count):
        raise ValueError(f"ไม่สามารถระบุขอบเขต Storyboard ช่องที่ {slot}")

    try:
        from PIL import Image
        with Image.open(storyboard) as source:
            source = source.convert("RGB")
            width, height = source.size
            columns = 2
            rows = math.ceil(int(panel_count) / columns)
            col = (slot - 1) % columns
            row = (slot - 1) // columns
            cell_w = width / columns
            cell_h = height / rows
            # Keep the visual frame and remove the bottom caption band.  The
            # margins also remove the white grid gutters without rescaling the
            # panel into a different aspect ratio.
            left = int(round(col * cell_w + cell_w * 0.018))
            right = int(round((col + 1) * cell_w - cell_w * 0.018))
            top = int(round(row * cell_h + cell_h * 0.015))
            bottom = int(round((row + 1) * cell_h - cell_h * 0.19))
            if right - left < 80 or bottom - top < 50:
                raise ValueError(f"ไม่สามารถระบุขอบเขต Storyboard ช่องที่ {slot}")
            out_dir = Path(data_dir) / "generated" / "storyboard_panels" / str(run_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            output = out_dir / f"panel_{slot:02d}.png"
            if not output.is_file():
                source.crop((left, top, right, bottom)).save(output, format="PNG")
            return output.resolve()
    except ImportError as exc:
        raise RuntimeError("ระบบอ่านภาพ Storyboard ต้องใช้ Pillow") from exc


def authority_block(scene_text: str, slot: int, prompt: str) -> str:
    scene = re.sub(r"\s+", " ", str(scene_text or "")).strip()
    current = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not scene:
        return ""
    return (
        "\n\nSTORY AUTHORITY (current run): "
        + scene[:12000]
        + f"\nSTORYBOARD SHOT {int(slot):02d} AUTHORITY: use this shot's visible composition, "
        "but preserve the story's named person, age, actor, recipient, action, location, and prop. "
        "Preserve the attached panel's shot size, lens impression, subject scale, framing boundaries, and foreground/background placement. "
        "Do not zoom in, crop, reframe, or replace a wide/medium shot with a close-up. "
        "Do not reverse who holds whom, do not turn a newborn into an adult, and do not invent a new action. "
        "The current shot prompt is: " + current[:6000]
    )
