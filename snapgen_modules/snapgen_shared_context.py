# -*- coding: utf-8 -*-
"""Team-shared Prompt-Ref Context stored next to the story file.

When Context is built for ``บท - เรื่อง.docx`` it is also written to
``บท - เรื่อง.tidmunz-context.json`` in the same folder.  On a shared drive
(Google Drive etc.) every teammate who selects that story gets the same
Context automatically, so GPT does not analyse the story again and character
names stay identical across machines.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

SUFFIX = ".tidmunz-context.json"
# นิทาน builds its own character-batch Context from the same story file. It
# must never overwrite (or be loaded as) the main Prompt-Ref Context.
STORY_FACE_SUFFIX = ".tidmunz-story-face-context.json"
FORMAT = "tidmunz-shared-context"


def sidecar_path(story_path, suffix=SUFFIX) -> Path | None:
    if not story_path:
        return None
    path = Path(str(story_path))
    if not path.name:
        return None
    return path.with_name(path.stem + suffix)


def story_hash(text) -> str:
    normalized = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_usable_context(context) -> bool:
    return (
        isinstance(context, dict)
        and isinstance(context.get("characters"), list)
        and isinstance(context.get("locations"), list)
        and bool(context.get("characters") or context.get("locations"))
    )


def save(story_path, context, story_text="", suffix=SUFFIX) -> str:
    """Write the sidecar atomically.  Returns "" on success or an error text."""
    target = sidecar_path(story_path, suffix)
    if target is None or not target.parent.is_dir():
        return "ไม่พบโฟลเดอร์ของไฟล์บท"
    if not is_usable_context(context):
        return "Context ว่าง"
    payload = {
        "format": FORMAT,
        "version": 1,
        "source_name": Path(str(story_path)).name,
        "story_sha256": story_hash(story_text) if story_text else "",
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "context": context,
    }
    temp = target.with_name(target.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, target)
        return ""
    except OSError as exc:  # offline / read-only shared drive
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return str(exc)


def load(story_path, suffix=SUFFIX) -> dict | None:
    """Return the sidecar payload (with a usable ``context``) or None."""
    target = sidecar_path(story_path, suffix)
    if target is None or not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        return None
    if not is_usable_context(payload.get("context")):
        return None
    payload["path"] = str(target)
    return payload


def matches_story(payload, story_text) -> bool:
    """False only when both hashes are known and differ (story was edited)."""
    expected = str((payload or {}).get("story_sha256") or "")
    if not expected or not story_text:
        return True
    return expected == story_hash(story_text)


def same_context(a, b) -> bool:
    def canon(value):
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "updated_at"}
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return canon(a) == canon(b)
