# -*- coding: utf-8 -*-
"""snapgen_image_gen.py — standalone image generation via chatgpt-api bridge.

All pages (Image AI, Ref, Prop, Story Face) call generate_image() here.
Single source of truth — fix once, all pages benefit.
No dependency on pyc internals. Uses urllib.request directly (no curl subprocess).
"""

import os, json, time, threading, urllib.request, urllib.error, base64, shutil, hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from snapgen_story_types import normalize_story_type, story_type_profile

# ── Config (override via set_config) ──────────────────────────────
BRIDGE_URL = "http://127.0.0.1:8000"
BRIDGE_KEY = "local-dev-key"
# Account-aware: the Bridge resolves the best supported model for the active
# Go/Plus/Pro capture instead of pinning one model ID that may return 404.
MODEL = "auto"
DEFAULT_OUTPUT_DIR = None  # set at runtime
TIMEOUT = 300  # seconds per request
RETRY_COUNT = 0
RETRY_DELAY = 5  # seconds between retries


# Set once ChatGPT refuses to read back a temporary chat in this session.
_temporary_chat_unsupported = [False]


def _is_conversation_lost(text):
    """True when ChatGPT cannot open the chat from the active account."""
    lowered = str(text or "").casefold()
    return any(marker in lowered for marker in (
        "conversation_not_found", "conversation not found", "conversation was not found",
        "conversation_inaccessible",
        "บัญชีที่ถูกต้อง", "correct account",
    ))

# ── State ─────────────────────────────────────────────────────────
_queue_lock = threading.Lock()
_active_count = 0
_active_lock = threading.Lock()
_log_fn = None  # callable(msg: str)
_story_state_lock = threading.Lock()
_STORY_STATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "snapgen_data"
    / "meta"
    / "image_story_conversation.json"
)
_story_conversation = {
    "conversation_id": None, "parent_message_id": None,
    "account_alias": "",
    "story_title": "", "story_hash": "",
    "storyboard_hash": "", "storyboard_path": "",
    "storyboard_conversation_id": "",
}
_REF_STORY_STATE_PATH = _STORY_STATE_PATH.with_name("ref_story_conversation.json")
_ref_story_conversation = {
    "conversation_id": None, "parent_message_id": None,
    "account_alias": "", "story_type_lock": "",
    "story_title": "", "story_hash": "",
}
_STORY_FACE_STATE_PATH = _STORY_STATE_PATH.with_name("story_face_conversation.json")
_story_face_conversation = {
    "conversation_id": None, "parent_message_id": None,
    "story_context_parent_message_id": "",
    "account_alias": "",
    "story_title": "", "story_hash": "",
}
_PROP_STATE_PATH = _STORY_STATE_PATH.with_name("prop_conversation.json")
_prop_conversation = {"conversation_id": None, "parent_message_id": None, "account_alias": ""}

STORY_FACE_DIVERSITY_LOCK = (
    "STORY FACE DIVERSITY LOCK — สำหรับเรื่องนี้ ห้ามพยายามทำให้ใบหน้าของตัวละครต่างคนคล้ายกัน "
    "หรือใช้ใบหน้าต้นแบบเดียวกัน. ออกแบบแต่ละ identity ให้เป็นคนละบุคคลอย่างชัดเจนตั้งแต่รูปหน้า "
    "สัดส่วนหน้าผาก โหนกแก้ม กราม คาง รูปคิ้ว รูปตา ระยะห่างตา สันและปลายจมูก รูปปาก สีผิว อายุ "
    "และตำหนิเฉพาะตัว. ห้ามปรับทุกคนให้เป็นใบหน้ามาตรฐานหล่อหรือสวยแบบเดียวกัน. "
    "ใช้ภาพอ้างอิงซ้ำได้เฉพาะคนเดียวกันต่างวัยหรือต่างสภาพ; คนละ identity ห้ามใช้โครงหน้าเดียวกัน. "
    "รักษาความแตกต่างนี้ไว้ใน Character Bible และทุกภาพต่อจากนี้. "
    "ตอบ FACE_DIVERSITY_LOCKED พร้อมยืนยันว่าเข้าใจกฎนี้ และยังไม่ต้องสร้างภาพ."
)


def _with_story_face_diversity_lock(instruction):
    """Add the per-story face separation rule without duplicating it on retries."""
    text = str(instruction or "").strip()
    if "STORY FACE DIVERSITY LOCK" in text:
        return text
    return f"{text}\n\n{STORY_FACE_DIVERSITY_LOCK}" if text else STORY_FACE_DIVERSITY_LOCK

def _story_hash(content):
    normalized = bytes(content or b"").decode("utf-8", errors="replace")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def _title_from_story_content(content, fallback="เรื่องจาก Prompt-Ref"):
    """Use the exact uploaded story bytes as title source; UI/Context may be stale."""
    text = bytes(content or b"").decode("utf-8", errors="replace")
    for line in text.splitlines():
        value = line.strip().lstrip("#").strip()
        if value:
            return value[:60]
    return str(fallback or "เรื่องจาก Prompt-Ref").strip()[:60]

def _current_source_hash():
    try:
        return _story_hash(_STORY_STATE_PATH.parent.parent.joinpath("prompt_ref_source.txt").read_bytes())
    except OSError:
        return ""

def _load_conversation(path, target):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("conversation_id") and raw.get("parent_message_id"):
            for key in target:
                target[key] = str(raw.get(key) or "")
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, AttributeError):
        pass

def _save_conversation(path, state):
    with _story_state_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)

def _valid_story_title(state):
    current_hash = _current_source_hash()
    if not (state.get("conversation_id") and state.get("parent_message_id")):
        return ""
    saved_hash = state.get("story_hash")
    if saved_hash != current_hash:
        # Migrate state written before hashes normalized trailing whitespace.
        try:
            source_bytes = _STORY_STATE_PATH.parent.parent.joinpath("prompt_ref_source.txt").read_bytes()
            legacy_hash = hashlib.sha256(source_bytes).hexdigest()
        except OSError:
            legacy_hash = ""
        if saved_hash and saved_hash == legacy_hash and current_hash:
            state["story_hash"] = current_hash
            if state is _story_conversation:
                _save_story_conversation()
            elif state is _ref_story_conversation:
                _save_ref_story_conversation()
        else:
            return ""
    if not current_hash:
        return ""
    return str(state.get("story_title") or "").strip()


def _load_story_conversation():
    """Load only Image AI's conversation ids; never prompt/image content."""
    _load_conversation(_STORY_STATE_PATH, _story_conversation)


def _save_story_conversation():
    """Persist Image AI's ids so closing SnapGen does not start a new history."""
    _save_conversation(_STORY_STATE_PATH, _story_conversation)


def reset_story_conversation():
    """Start a new Image AI history. This is the only normal history reset."""
    _story_conversation.update(
        conversation_id=None,
        parent_message_id=None,
        account_alias="",
        story_title="",
        story_hash="",
        storyboard_hash="",
        storyboard_path="",
        storyboard_conversation_id="",
    )
    _save_story_conversation()


def has_story_conversation():
    """Return whether Image AI has a complete persisted conversation cursor."""
    return bool(_valid_story_title(_story_conversation))


def get_image_story_cursor():
    """Return the saved chat cursor even when the source file was edited later."""
    if not (_story_conversation.get("conversation_id") and _story_conversation.get("parent_message_id")):
        return None
    return {
        "conversation_id": str(_story_conversation["conversation_id"]),
        "parent_message_id": str(_story_conversation["parent_message_id"]),
        "account_alias": str(_story_conversation.get("account_alias") or ""),
        "story_hash": str(_story_conversation.get("story_hash") or ""),
    }

def get_story_title():
    return _valid_story_title(_story_conversation)

def _load_ref_story_conversation():
    _load_conversation(_REF_STORY_STATE_PATH, _ref_story_conversation)

def _save_ref_story_conversation():
    _save_conversation(_REF_STORY_STATE_PATH, _ref_story_conversation)

def reset_ref_story_conversation():
    _ref_story_conversation.update(
        conversation_id=None, parent_message_id=None, account_alias="",
        story_type_lock="", story_title="", story_hash="",
    )
    _save_ref_story_conversation()

def has_ref_story_conversation():
    return bool(_valid_story_title(_ref_story_conversation))

def get_ref_story_title():
    return _valid_story_title(_ref_story_conversation)

def get_ref_story_request_context():
    """Return the current Ref cursor for text or image Bridge requests."""
    conversation_id = str(_ref_story_conversation.get("conversation_id") or "").strip()
    parent_message_id = str(_ref_story_conversation.get("parent_message_id") or "").strip()
    if not conversation_id or not parent_message_id:
        return {}
    context = {
        "metadata": {
            "conversation_id": conversation_id,
            "parent_message_id": parent_message_id,
        }
    }
    account_alias = str(_ref_story_conversation.get("account_alias") or "").strip()
    if account_alias:
        context["chatgpt_account"] = account_alias
    return context

def update_ref_story_conversation(result):
    """Advance Ref's persisted cursor from any successful Bridge response."""
    if not isinstance(result, dict):
        return
    if result.get("conversation_id"):
        _ref_story_conversation["conversation_id"] = str(result["conversation_id"])
    if result.get("parent_message_id"):
        _ref_story_conversation["parent_message_id"] = str(result["parent_message_id"])
    if result.get("chatgpt_account"):
        _ref_story_conversation["account_alias"] = str(result["chatgpt_account"])
    if (
        _ref_story_conversation.get("conversation_id")
        and _ref_story_conversation.get("parent_message_id")
    ):
        _save_ref_story_conversation()


def send_ref_story_type_lock(
    story_type, *, log_fn=None, conversation_state=None, conversation_save_fn=None,
):
    """Send story-type rules once into the caller-owned story conversation."""
    state = conversation_state if isinstance(conversation_state, dict) else _ref_story_conversation
    if not (state.get("conversation_id") and state.get("parent_message_id")):
        raise RuntimeError("ยังไม่มีประวัติเรื่องหลักสำหรับส่งประเภทเรื่อง")
    story_type_id = normalize_story_type(story_type)
    if state.get("story_type_lock") == story_type_id:
        return False
    _, profile = story_type_profile(story_type_id)
    label = str(profile.get("label") or "อัตโนมัติตามบท")
    rules = " ".join(
        str(profile.get(key) or "").strip()
        for key in ("world", "characters", "period", "costume", "avoid")
        if str(profile.get(key) or "").strip()
    )
    if not rules:
        rules = (
            "ให้วิเคราะห์ประเภทจากบทจริงก่อนเลือกโลกเรื่อง ยุค ตัวละคร เสื้อผ้า วัสดุ สี แสง "
            "และองค์ประกอบภาพ; ห้ามยัดกฎของเรื่องผีหรือวรรณคดีถ้าบทไม่ได้ระบุ"
        )
    instruction = (
        "PROMPT-REF STORY TYPE LOCK — ใช้เป็นกฎกลางของการสร้าง Ref ต่อจากนี้ในประวัติเดิมนี้.\n"
        f"story_type: {story_type_id}\n"
        f"ประเภทเรื่อง: {label}\n"
        f"DESIGN RULES: {rules}\n"
        "รักษาข้อเท็จจริงจากบทเป็นหลัก กฎประเภทนี้ใช้กำกับโลกเรื่อง ยุค รูปลักษณ์ ตัวละคร "
        "เสื้อผ้า วัสดุ สี แสง และสถาปัตยกรรมให้ไปในทิศทางเดียวกัน. "
        "ห้ามเปลี่ยนเรื่องให้เป็นผี วรรณคดี หรือแฟนตาซีเพียงเพราะชื่อประเภท; "
        "ถ้าข้อมูลในบทขัดกับกฎประเภท ให้ยึดบทและแจ้งความขัดแย้งสั้นๆ. "
        "ตอบ TYPE_LOCKED พร้อมยืนยัน story_type เดียวเท่านั้น; ยังไม่ต้องสร้างภาพ."
    )
    log = log_fn or _log
    payload = {
        "model": str(MODEL or "auto"),
        "chatgpt_image_intercept": False,
        "messages": [{"role": "user", "content": instruction}],
        "history_and_training_disabled": False,
    }
    payload["metadata"] = {
        "conversation_id": state["conversation_id"],
        "parent_message_id": state["parent_message_id"],
    }
    if state.get("account_alias"):
        payload["chatgpt_account"] = state["account_alias"]
    try:
        log(f"[ประเภทเรื่อง] ส่งกฎ {label} เข้า GPT ในประวัติเรื่องหลักเดิม...")
        with _queue_lock:
            request = urllib.request.Request(
                f"{BRIDGE_URL}/v1/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {BRIDGE_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"ส่งกฎประเภทเรื่องไม่สำเร็จ HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"ส่งกฎประเภทเรื่องไม่สำเร็จ: {exc}") from exc
    if isinstance(result.get("error"), dict):
        raise RuntimeError(str(result["error"].get("message") or result["error"]))
    previous_conversation_id = str(state.get("conversation_id") or "").strip()
    returned_conversation_id = str(result.get("conversation_id") or "").strip()
    returned_parent_message_id = str(result.get("parent_message_id") or "").strip()
    if not returned_conversation_id or not returned_parent_message_id:
        raise RuntimeError("Bridge รับกฎประเภทเรื่องแล้วแต่ไม่ส่ง cursor กลับมา")
    if previous_conversation_id and returned_conversation_id != previous_conversation_id:
        raise RuntimeError("Bridge เปิดประวัติใหม่แทนประวัติเรื่องหลักตอนส่งกฎประเภทเรื่อง")
    previous_account = str(state.get("account_alias") or "").strip()
    returned_account = str(result.get("chatgpt_account") or "").strip()
    if previous_account and returned_account and returned_account != previous_account:
        raise RuntimeError("Bridge ใช้บัญชีไม่ตรงกับประวัติเรื่องหลักตอนส่งกฎประเภทเรื่อง")
    state["conversation_id"] = returned_conversation_id
    state["parent_message_id"] = returned_parent_message_id
    state["account_alias"] = str(result.get("chatgpt_account") or state.get("account_alias") or "")
    state["story_type_lock"] = story_type_id
    if callable(conversation_save_fn):
        conversation_save_fn()
    elif state is _ref_story_conversation:
        _save_ref_story_conversation()
    log(f"[ประเภทเรื่อง] GPT รับกฎ {label} แล้ว — พร้อมสร้าง Ref")
    return True

def _load_story_face_conversation():
    _load_conversation(_STORY_FACE_STATE_PATH, _story_face_conversation)

def _save_story_face_conversation():
    _save_conversation(_STORY_FACE_STATE_PATH, _story_face_conversation)

def reset_story_face_conversation():
    """Reset only Story 3D history; Image AI and Ref histories stay untouched."""
    _story_face_conversation.update(
        conversation_id=None,
        parent_message_id=None,
        story_context_parent_message_id="",
        account_alias="",
        story_title="",
        story_hash="",
    )
    _save_story_face_conversation()

def has_story_face_conversation():
    return bool(
        _story_face_conversation.get("conversation_id")
        and _story_face_conversation.get("parent_message_id")
        and _story_face_conversation.get("story_title")
    )

def get_story_face_title():
    return str(_story_face_conversation.get("story_title") or "").strip() if has_story_face_conversation() else ""

def get_story_face_hash():
    return str(_story_face_conversation.get("story_hash") or "").strip() if has_story_face_conversation() else ""

def _load_prop_conversation():
    _load_conversation(_PROP_STATE_PATH, _prop_conversation)

def _save_prop_conversation():
    _save_conversation(_PROP_STATE_PATH, _prop_conversation)

def reset_prop_conversation():
    """Reset only Prop page history; other page histories stay untouched."""
    _prop_conversation.update(conversation_id=None, parent_message_id=None, account_alias="")
    _save_prop_conversation()


def invalidate_histories_for_account(active_account):
    """Drop cursors bound to another account after Bridge Use switches capture."""
    active = str(active_account or "").strip().casefold()
    if not active:
        return []
    histories = (
        ("Image", _story_conversation, reset_story_conversation),
        ("Ref", _ref_story_conversation, reset_ref_story_conversation),
        ("Story Face", _story_face_conversation, reset_story_face_conversation),
        ("Prop", _prop_conversation, reset_prop_conversation),
    )
    invalidated = []
    for label, state, reset in histories:
        bound = str(state.get("account_alias") or "").strip().casefold()
        if bound and bound != active:
            reset()
            invalidated.append(label)
    return invalidated


_load_story_conversation()
_load_ref_story_conversation()
_load_story_face_conversation()
_load_prop_conversation()


def ingest_story_context(
    ref_images, *, log_fn=None, storyboard_panel_mode=False,
    conversation_state=None, conversation_save_fn=None,
):
    """Upload prior-event images into the current story chat without generating.

    The returned conversation/message ids become the parent of the next image
    request, so storyboard context is learned before character refs are sent.
    """
    images = list(ref_images or [])[:10]
    if not images:
        raise RuntimeError("ไม่มีรูปบริบทเหตุการณ์ให้ส่ง")
    log = log_fn or _log
    state = conversation_state if isinstance(conversation_state, dict) else _story_conversation
    requested_conversation_id = str(state.get("conversation_id") or "").strip()
    requested_parent_message_id = str(state.get("parent_message_id") or "").strip()
    if not requested_conversation_id or not requested_parent_message_id:
        raise RuntimeError("ยังไม่ได้เริ่มเรื่องจาก Prompt-Ref")
    if storyboard_panel_mode:
        prompt = (
            "These attached images are STORYBOARD REFERENCES. For the next image-generation "
            "request, use only the storyboard panel/slot number specified in that request. "
            "Generate the image to match that exact panel as closely as possible, including "
            "composition, camera angle, framing, character placement, action, location, props, "
            "wardrobe, and lighting. Do not summarize, reinterpret, or use the storyboard as "
            "overall narrative context. Do not generate an image now. Reply only CONTEXT_READY."
        )
    else:
        prompt = (
            "These attached images are previous-event context for the same story. Study them "
            "before the next image request and preserve the latest relevant continuity. Do not "
            "generate an image now. Reply only CONTEXT_READY."
        )
    payload = {
        "model": MODEL,
        "mode": "custom",
        "prompt": prompt,
        "images": images,
    }
    if requested_conversation_id:
        payload["metadata"] = {
            "conversation_id": requested_conversation_id,
            "parent_message_id": requested_parent_message_id,
        }
        if state.get("account_alias"):
            payload["chatgpt_account"] = state["account_alias"]
    req = urllib.request.Request(
        f"{BRIDGE_URL}/v1/chatgpt/vision",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {BRIDGE_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    log("[บริบท] กำลังส่งเหตุการณ์ก่อนหน้าเข้า GPT...")
    try:
        with _queue_lock:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ส่งบริบทไม่สำเร็จ HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"ส่งบริบทไม่สำเร็จ: {exc}") from exc
    if isinstance(result.get("error"), dict):
        raise RuntimeError(str(result["error"].get("message") or result["error"]))
    conversation_id = result.get("conversation_id")
    parent_message_id = result.get("parent_message_id")
    if not conversation_id or not parent_message_id:
        raise RuntimeError("Bridge รับรูปแล้วแต่ไม่ส่งรหัสประวัติกลับมา กรุณารีสตาร์ต Bridge")
    if str(conversation_id) != requested_conversation_id:
        raise RuntimeError("Bridge เปิดแชตใหม่แทนประวัติเรื่องเดิม — ยกเลิกผลลัพธ์")
    state["parent_message_id"] = str(parent_message_id)
    if result.get("chatgpt_account"):
        state["account_alias"] = str(result["chatgpt_account"])
    if callable(conversation_save_fn):
        conversation_save_fn()
    elif state is _story_conversation:
        _save_story_conversation()
    summary = str(result.get("text") or "").strip()
    log("[บริบท] GPT รับรู้เหตุการณ์ก่อนหน้าแล้ว")
    if summary:
        log("[บริบท] " + summary[:240])
    return {
        "conversation_id": conversation_id,
        "parent_message_id": parent_message_id,
        "summary": summary,
    }




def ensure_latest_storyboard_context(*, log_fn=None):
    """Register the latest Prompt-Ref storyboard once in Image AI history.

    A storyboard is sent only when its file hash changes or Image AI switches to
    another conversation. The acknowledged turn becomes the parent for the next
    image-generation request.
    """
    meta_path = _STORY_STATE_PATH.parent.parent / "prompt_ref_storyboard_image.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {"sent": False, "reason": "no_storyboard_meta"}

    source = Path(str(meta.get("image_path") or "")).expanduser()
    try:
        source = source.resolve()
    except OSError:
        pass
    if not source.is_file() or source.stat().st_size <= 0:
        return {"sent": False, "reason": "no_storyboard_image"}

    log = log_fn or _log
    storyboard_story_hash = str(meta.get("context_hash") or meta.get("story_hash") or "").strip()
    # A source edit does not end the user's story. Never reset or re-upload the
    # script here; only the explicit New Story action may start another history.
    if not get_image_story_cursor():
        return {"sent": False, "reason": "no_story_history"}

    current_story_hash = str(_story_conversation.get("story_hash") or "").strip()
    if storyboard_story_hash and current_story_hash and storyboard_story_hash != current_story_hash:
        return {"sent": False, "reason": "storyboard_from_other_story"}

    # Include the instruction version so a changed storyboard-use rule is sent
    # once again even when the image file itself has not changed.
    digest = hashlib.sha256(source.read_bytes() + b"|storyboard-panel-direct-v2").hexdigest()
    conversation_id = str(_story_conversation.get("conversation_id") or "").strip()
    already_registered = (
        str(_story_conversation.get("storyboard_hash") or "") == digest
        and str(_story_conversation.get("storyboard_conversation_id") or "") == conversation_id
    )
    if already_registered:
        return {
            "sent": False,
            "reason": "same_storyboard",
            "storyboard_hash": digest,
            "storyboard_path": str(source),
        }

    try:
        from PIL import Image
        import io
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=84, optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:
        raise RuntimeError(f"เตรียม Storyboard สำหรับ Image AI ไม่สำเร็จ: {exc}") from exc

    log("[Storyboard Context] พบ Storyboard ใหม่ — ส่งให้ Image AI รับรู้ก่อนสร้างรูป...")
    result = ingest_story_context([encoded], log_fn=log, storyboard_panel_mode=True)
    acknowledgement = str(result.get("summary") or "").strip()
    if not acknowledgement:
        raise RuntimeError("Image AI รับ Storyboard แล้วแต่ไม่ตอบยืนยัน")

    _story_conversation["storyboard_hash"] = digest
    _story_conversation["storyboard_path"] = str(source)
    _story_conversation["storyboard_conversation_id"] = str(
        _story_conversation.get("conversation_id") or ""
    )
    _save_story_conversation()
    log("[Storyboard Context] ✓ Image AI รับรู้ Storyboard ใหม่แล้ว")
    return {
        "sent": True,
        "reason": "new_storyboard",
        "storyboard_hash": digest,
        "storyboard_path": str(source),
        "acknowledgement": acknowledgement,
    }


def mark_storyboard_context_registered(source_path):
    """Mark a Storyboard image already generated in Image AI history.

    Prompt-Ref's direct Storyboard flow now generates through the Image AI
    history itself. Recording the file here prevents the next image request
    from uploading the same Storyboard a second time as a separate vision turn.
    """
    source = Path(str(source_path or "")).expanduser()
    try:
        source = source.resolve()
    except OSError:
        pass
    if not source.is_file() or source.stat().st_size <= 0:
        return False
    if not has_story_conversation():
        return False
    digest = hashlib.sha256(source.read_bytes() + b"|storyboard-panel-direct-v2").hexdigest()
    _story_conversation["storyboard_hash"] = digest
    _story_conversation["storyboard_path"] = str(source)
    _story_conversation["storyboard_conversation_id"] = str(
        _story_conversation.get("conversation_id") or ""
    )
    _save_story_conversation()
    return True


def _video_prompt_instruction(current_prompt="", prevent_turn_back=False):
    draft = str(current_prompt or "").strip()
    instruction = (
        "Look carefully at the attached image and the draft video prompt below. "
        "Write one concise, model-neutral video prompt in English, using 1 to 3 sentences "
        "and no more than 80 words. State what the image shows, the main visible action "
        "or natural motion and the visual continuity to preserve. "
        "Do not choose, describe, or mention camera movement in the output. Leave camera movement "
        "unspecified because SnapGen's Camera dropdown controls it. Ignore any camera instruction "
        "in the draft, including zoom, push-in, pull-out, pan, tilt, dolly, tracking, handheld, "
        "or static-camera wording. Never add a camera movement by default. "
        "Use the image as the visual truth and keep the draft's original intent. "
        "Do not invent people, events, props, text, or details that are not supported by the image or draft. "
        "Do not mention model names, settings, aspect ratios, analysis, or these instructions. "
        "Output only the final video prompt."
    )
    if draft:
        instruction += "\n\nDRAFT VIDEO PROMPT:\n" + draft
    else:
        instruction += "\n\nThere is no draft prompt. Describe the image and propose one simple, natural video action."
    if prevent_turn_back:
        instruction += (
            "\n\nPreserve the starting back-facing view throughout the clip; "
            "the subject must not turn the head or face toward the camera."
        )
    return instruction


def _compact_video_prompt(answer, max_words=80):
    words = str(answer or "").strip().split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(" ,;:") + "."


def generate_video_prompt_from_story_image(
    image_path,
    *,
    current_prompt="",
    slot_number=None,
    prevent_turn_back=False,
    model_name="",
    log_fn=None,
    history_cursor=None,
    conversation_save_fn=None,
):
    """Write a Video Prompt from the Slot image, continuing a story cursor when available."""
    source = Path(str(image_path or "")).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"ไม่พบรูปใน Slot: {source}")
    cursor = history_cursor or get_image_story_cursor()
    if cursor and not (cursor.get("conversation_id") and cursor.get("parent_message_id")):
        cursor = None

    log = log_fn or _log
    slot_text = f"Slot {int(slot_number)}" if slot_number is not None else "Video Slot"
    try:
        import io
        from PIL import Image
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
            image_bytes = buffer.getvalue()
    except Exception as exc:
        raise RuntimeError(f"เตรียมรูปสำหรับ GPT ไม่สำเร็จ: {exc}") from exc

    prompt = _video_prompt_instruction(
        current_prompt=current_prompt,
        prevent_turn_back=prevent_turn_back,
    )

    requested_conversation_id = str(cursor.get("conversation_id") or "").strip() if cursor else ""
    payload = {
        "model": "chatgpt-web/auto",
        "thinking_effort": "standard",
        "mode": "custom",
        "prompt": prompt,
        "input_images": [{
            "name": "slot_video_prompt.jpg",
            "data_url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii"),
        }],
    }
    if cursor:
        payload["metadata"] = {
            "conversation_id": requested_conversation_id,
            "parent_message_id": cursor["parent_message_id"],
        }
    else:
        payload["temporary_chat"] = True
    if cursor and cursor.get("account_alias"):
        payload["chatgpt_account"] = cursor["account_alias"]

    log(f"[GPT Video Prompt] กำลังดูรูปของ {slot_text}...")
    try:
        with _queue_lock:
            # Use the newest cursor when this request reaches the queue.
            if cursor:
                latest = get_image_story_cursor() if history_cursor is None else cursor
                if not latest or str(latest.get("conversation_id") or "") != requested_conversation_id:
                    raise RuntimeError("ประวัติเรื่องเปลี่ยนระหว่างรอคิว กรุณากดส่งใหม่")
                payload["metadata"] = {
                    "conversation_id": latest["conversation_id"],
                    "parent_message_id": latest["parent_message_id"],
                }
            request = urllib.request.Request(
                f"{BRIDGE_URL}/v1/chatgpt/vision",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {BRIDGE_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:900]
        raise RuntimeError(f"สร้าง Video Prompt ไม่สำเร็จ HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"สร้าง Video Prompt ไม่สำเร็จ: {exc}") from exc

    if isinstance(result.get("error"), dict):
        raise RuntimeError(str(result["error"].get("message") or result["error"]))
    conversation_id = str(result.get("conversation_id") or "").strip()
    parent_message_id = str(result.get("parent_message_id") or "").strip()
    if cursor and (not conversation_id or not parent_message_id):
        raise RuntimeError("Bridge ตอบกลับแต่ไม่คืนรหัสประวัติ")
    if cursor and conversation_id != requested_conversation_id:
        raise RuntimeError("Bridge เปิดแชตใหม่แทนประวัติเรื่องหลัก — ยกเลิกผลลัพธ์")

    answer = str(
        result.get("text")
        or ((result.get("choices") or [{}])[0].get("message") or {}).get("content")
        or ""
    ).strip()
    answer = answer.replace("```text", "").replace("```markdown", "").replace("```", "").strip()
    answer = answer.strip('"“”` ')
    answer = re.sub(
        r"(?is)^\s*(?:video\s*prompt|prompt\s*วิดีโอ|วิดีโอ\s*prompt)\s*[:：\-–—]*\s*",
        "",
        answer,
    ).strip()
    if len(answer) < 20:
        raise RuntimeError("GPT คืน Video Prompt สั้นหรือว่างเกินไป")
    answer = _compact_video_prompt(answer)
    if prevent_turn_back and "must not turn the head" not in answer.lower():
        answer = answer.rstrip(" .") + (
            " The subject must remain facing away throughout the clip and must not turn "
            "the head or face toward the camera."
        )

    if cursor and history_cursor is None:
        _story_conversation["conversation_id"] = conversation_id
        _story_conversation["parent_message_id"] = parent_message_id
        _story_conversation["account_alias"] = str(result.get("chatgpt_account") or cursor.get("account_alias") or "")
        _save_story_conversation()
    elif cursor:
        history_cursor["parent_message_id"] = parent_message_id
        history_cursor["account_alias"] = str(result.get("chatgpt_account") or cursor.get("account_alias") or "")
        if callable(conversation_save_fn):
            conversation_save_fn()
    log(f"[GPT Video Prompt] พร้อมแล้วสำหรับ {slot_text}")
    return answer

def generate_prompt_ref_storyboard_json_from_image(
    image_path,
    *,
    prompt,
    conversation_id,
    parent_message_id,
    account_alias="",
    log_fn=None,
):
    """Use the exact working GPT Video Prompt vision route for Prompt-Ref JSON."""
    source = Path(str(image_path or "")).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"ไม่พบรูป Storyboard: {source}")
    conversation_id = str(conversation_id or "").strip()
    parent_message_id = str(parent_message_id or "").strip()
    if not conversation_id or not parent_message_id:
        raise RuntimeError("Prompt-Ref ไม่มี conversation cursor สำหรับส่งรูป")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise RuntimeError("คำสั่งแตก Prompt ว่าง")

    log = log_fn or _log
    try:
        import io
        from PIL import Image
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
            image_bytes = buffer.getvalue()
    except Exception as exc:
        raise RuntimeError(f"เตรียมรูป Storyboard สำหรับ GPT ไม่สำเร็จ: {exc}") from exc

    payload = {
        "model": "auto",
        "mode": "custom",
        "prompt": prompt,
        "input_images": [{
            "name": "prompt_ref_storyboard.jpg",
            "data_url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii"),
        }],
        "metadata": {
            "conversation_id": conversation_id,
            "parent_message_id": parent_message_id,
        },
    }
    account_alias = str(account_alias or "").strip()
    if account_alias:
        payload["chatgpt_account"] = account_alias

    log("[Prompt-Ref Storyboard] ส่งรูปด้วย vision route เดียวกับปุ่ม GPT Video Prompt...")
    try:
        with _queue_lock:
            request = urllib.request.Request(
                f"{BRIDGE_URL}/v1/chatgpt/vision",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {BRIDGE_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1800]
        raise RuntimeError(f"แตก Prompt จาก Storyboard ไม่สำเร็จ HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"แตก Prompt จาก Storyboard ไม่สำเร็จ: {exc}") from exc

    if isinstance(result.get("error"), dict):
        raise RuntimeError(str(result["error"].get("message") or result["error"]))
    returned_conversation_id = str(result.get("conversation_id") or "").strip()
    returned_parent_message_id = str(result.get("parent_message_id") or "").strip()
    if not returned_conversation_id or not returned_parent_message_id:
        raise RuntimeError("Bridge ตอบกลับแต่ไม่คืนรหัสประวัติ Prompt-Ref")
    if returned_conversation_id != conversation_id:
        raise RuntimeError("Bridge เปิดแชตใหม่แทน Prompt-Ref เดิม — ยกเลิกผลลัพธ์")
    returned_account = str(result.get("chatgpt_account") or "").strip()
    if account_alias and returned_account and returned_account != account_alias:
        raise RuntimeError("Bridge ใช้บัญชีไม่ตรงกับ Prompt-Ref เดิม")

    answer = str(
        result.get("text")
        or ((result.get("choices") or [{}])[0].get("message") or {}).get("content")
        or ""
    ).strip()
    if not answer:
        raise RuntimeError("GPT คืนคำตอบว่างจากรูป Storyboard")
    log("[Prompt-Ref Storyboard] GPT ส่งคำตอบกลับแล้ว")
    return {
        "text": answer,
        "conversation_id": returned_conversation_id,
        "parent_message_id": returned_parent_message_id,
        "chatgpt_account": returned_account,
    }

def _ingest_story_file_into(
    state, save_fn, story_title, filename, file_bytes, *, purpose,
    log_fn=None, attach_file=True, instruction_override=None,
    story_face_lock=False, continue_history=False, update_story_identity=True,
):
    name = str(filename or "story.txt").strip() or "story.txt"
    content = bytes(file_bytes or b"")
    if not content:
        raise RuntimeError("ไฟล์บทว่าง")
    title = _title_from_story_content(content, story_title)
    log = log_fn or _log
    instruction = str(instruction_override or "").strip() or (
        f"นี่คือบททั้งเรื่องชื่อ '{title}' สำหรับ{purpose}ของ SnapGen "
        "อ่านให้ครบและจำตัวละคร บทบาท ความสัมพันธ์ ยุค ฐานะ รูปลักษณ์ เสื้อผ้าที่เหมาะกับเหตุการณ์ "
        "สถานที่ และข้อเท็จจริงของเรื่องไว้ในประวัตินี้ ยังไม่ต้องสร้างภาพ ตอบ STORY_READY พร้อมสรุปหนึ่งประโยค"
    )
    if story_face_lock:
        instruction = _with_story_face_diversity_lock(instruction)
    story_text = content.decode("utf-8", errors="replace")
    if attach_file:
        message_content = [
            {"type": "input_file", "filename": name, "mime_type": "text/plain; charset=utf-8",
             "file_data": base64.b64encode(content).decode("ascii")},
            {"type": "input_text", "text": instruction},
        ]
    else:
        message_content = instruction + "\n\n--- บทเต็ม ---\n" + story_text

    def post_story(content_value, model_value):
        payload = {
            # The Bridge's account-aware alias is `auto`; keep story ingestion
            # on the same route as the rest of Image AI instead of forcing the
            # ChatGPT Web model namespace.
            "model": str(model_value or "auto"),
            "messages": [{"role": "user", "content": content_value}],
            "history_and_training_disabled": False,
            "metadata": {"chatgpt_image_intercept": False},
        }
        if (
            continue_history
            and state.get("conversation_id")
            and state.get("parent_message_id")
        ):
            payload["metadata"].update({
                "conversation_id": state["conversation_id"],
                "parent_message_id": state["parent_message_id"],
            })
            if state.get("account_alias"):
                payload["chatgpt_account"] = state["account_alias"]
        req = urllib.request.Request(
            f"{BRIDGE_URL}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {BRIDGE_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    log(f"[บทเรื่อง] กำลังส่งบท {name} เข้า GPT...")
    try:
        with _queue_lock:
            result = post_story(message_content, MODEL)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:900]
        pointer_error = "midi_asset_pointer" in body.casefold() or "provider_status=422" in body.casefold()
        if not pointer_error:
            raise RuntimeError(f"ส่งบทไม่สำเร็จ HTTP {exc.code}: {body}") from exc
        # Some ChatGPT Web captures reject any file/pointer-shaped content in
        # conversation requests. Retry as one plain text message so story
        # ingestion remains usable on those captures, including Ref uploads.
        log("[บทเรื่อง] Bridge ไม่รับ pointer — ลองส่งบทเป็นข้อความล้วนแทน...")
        try:
            with _queue_lock:
                result = post_story(
                    instruction + "\n\n--- บทเต็ม ---\n" + story_text,
                    "auto",
                )
        except urllib.error.HTTPError as retry_exc:
            retry_body = retry_exc.read().decode("utf-8", errors="replace")[:900]
            raise RuntimeError(f"ส่งบทไม่สำเร็จ HTTP {retry_exc.code}: {retry_body}") from retry_exc
        except Exception as retry_exc:
            raise RuntimeError(f"ส่งบทไม่สำเร็จ: {retry_exc}") from retry_exc
    except Exception as exc:
        raise RuntimeError(f"ส่งบทไม่สำเร็จ: {exc}") from exc
    if isinstance(result.get("error"), dict):
        raise RuntimeError(str(result["error"].get("message") or result["error"]))
    conversation_id = result.get("conversation_id")
    parent_message_id = result.get("parent_message_id")
    if not conversation_id or not parent_message_id:
        raise RuntimeError("Bridge รับบทแล้วแต่ไม่ส่งรหัสประวัติกลับมา")
    previous_conversation_id = str(state.get("conversation_id") or "").strip()
    if continue_history and previous_conversation_id and str(conversation_id) != previous_conversation_id:
        raise RuntimeError("Bridge เปิดประวัติใหม่แทนประวัติเรื่องหลักเดิม — ยกเลิกเพื่อไม่ให้เรื่องแยก")
    previous_account = str(state.get("account_alias") or "").strip()
    returned_account = str(result.get("chatgpt_account") or "").strip()
    if continue_history and previous_account and returned_account and returned_account != previous_account:
        raise RuntimeError("Bridge ใช้บัญชีไม่ตรงกับประวัติเรื่องหลักเดิม — ยกเลิกเพื่อไม่ให้เรื่องแยก")
    state["conversation_id"] = str(conversation_id)
    state["parent_message_id"] = str(parent_message_id)
    if state is _story_face_conversation:
        # Keep image requests in this story's conversation, but branch every
        # image from the latest text/Character-Bible turn instead of chaining
        # one generated face into the next character's visual context.
        state["story_context_parent_message_id"] = str(parent_message_id)
    state["account_alias"] = str(result.get("chatgpt_account") or state.get("account_alias") or "")
    if update_story_identity:
        state["story_title"] = title
        state["story_hash"] = _story_hash(content)
    save_fn()
    message = ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or result.get("text") or ""
    summary = str(message).strip()
    log(f"[บทเรื่อง] GPT พร้อมสำหรับเรื่อง: {title}")
    if summary:
        log("[บทเรื่อง] " + summary[:240])
    return {
        "conversation_id": conversation_id,
        "parent_message_id": parent_message_id,
        "summary": summary,
        "story_title": title,
        "story_hash": str(state.get("story_hash") or ""),
    }

def ingest_story_file(story_title, filename, file_bytes, *, log_fn=None):
    """Upload a complete story file before Image AI starts generating scenes."""
    return _ingest_story_file_into(
        _story_conversation, _save_story_conversation,
        story_title, filename, file_bytes,
        purpose="งานสร้างภาพและวิดีโอต่อเนื่อง", log_fn=log_fn, attach_file=False,
    )

def ingest_ref_story_file(story_title, filename, file_bytes, *, log_fn=None):
    """Upload current Prompt-Ref story into Ref's independent GPT history."""
    reset_ref_story_conversation()
    return _ingest_story_file_into(
        _ref_story_conversation, _save_ref_story_conversation,
        story_title, filename, file_bytes,
        purpose="งานออกแบบ Ref ตัวละครและสถานที่", log_fn=log_fn,
    )

def ingest_story_face_file(story_title, filename, file_bytes, *, log_fn=None):
    """Start Story 3D's independent history from its own dataset."""
    reset_story_face_conversation()
    return _ingest_story_file_into(
        _story_face_conversation, _save_story_face_conversation,
        story_title, filename, file_bytes,
        purpose="งานนิทาน 3D ใบหน้าตัวละครและเสื้อผ้า", log_fn=log_fn,
        story_face_lock=True,
    )


def analyze_story_face_dataset(
    story_title, text, instruction, *, log_fn=None,
    conversation_state=None, conversation_save_fn=None,
):
    """Analyze Story Face rows inside the caller-owned main story history."""
    source = str(text or "").strip()
    if not source:
        raise RuntimeError("ข้อมูลชุดนิทานว่าง")
    source_hash = _story_hash(source.encode("utf-8"))
    external_state = isinstance(conversation_state, dict)
    state = conversation_state if external_state else _story_face_conversation
    save_fn = conversation_save_fn if external_state else _save_story_face_conversation
    existing_hash = "" if external_state else get_story_face_hash()
    continue_history = bool(
        state.get("conversation_id") and state.get("parent_message_id")
    )
    if not continue_history:
        raise RuntimeError("ยังไม่มีประวัติเรื่องหลักจาก Prompt-Ref")
    if not external_state and existing_hash and existing_hash != source_hash:
        raise RuntimeError("ข้อมูลชุดนิทานไม่ตรงกับประวัติ Story Face เดิม — กด เปลี่ยนเรื่อง ก่อนวิเคราะห์ใหม่")
    return _ingest_story_file_into(
        state,
        save_fn,
        story_title,
        "story-face-dataset.txt",
        source.encode("utf-8"),
        purpose="วิเคราะห์ Character Bible และสร้างใบหน้าตัวละคร",
        log_fn=log_fn,
        attach_file=False,
        instruction_override=instruction,
        story_face_lock=True,
        continue_history=continue_history,
        update_story_identity=not external_state,
    )

def set_config(*, bridge_url=None, bridge_key=None, model=None,
               output_dir=None, timeout=None, retry_count=None,
               retry_delay=None, log_fn=None):
    """Override defaults. Call once at startup."""
    global BRIDGE_URL, BRIDGE_KEY, MODEL, DEFAULT_OUTPUT_DIR
    global TIMEOUT, RETRY_COUNT, RETRY_DELAY, _log_fn
    if bridge_url is not None:
        BRIDGE_URL = bridge_url.rstrip("/")
    if bridge_key is not None:
        BRIDGE_KEY = bridge_key
    if model is not None:
        MODEL = model
    if output_dir is not None:
        DEFAULT_OUTPUT_DIR = output_dir
    if timeout is not None:
        TIMEOUT = timeout
    if retry_count is not None:
        RETRY_COUNT = retry_count
    if retry_delay is not None:
        RETRY_DELAY = retry_delay
    if log_fn is not None:
        _log_fn = log_fn


def _log(msg):
    if _log_fn:
        try:
            _log_fn(msg)
        except Exception:
            pass


def _slug(text, max_len=40):
    """Safe filename slug from prompt text."""
    import re
    s = text.strip().lower()
    s = re.sub(r'[^a-z0-9\u0E00-\u0E7F\s_-]', '', s)
    s = re.sub(r'\s+', '-', s)
    return s[:max_len].strip("-") or "image"


def _scene_slug(text, max_len=64):
    """Readable scene filename while preserving the source Prompt number."""
    import re
    raw = str(text or "").strip()
    # Keep slot/order prefix if caller sends it, but use the scene sentence for the rest.
    prefix = ""
    m = re.match(r"^\s*(\d{1,2})[_\-\s]+(.+)$", raw, flags=re.S)
    if m:
        prefix = f"{int(m.group(1)):02d}_"
        raw = m.group(2).strip()

    # Prompt scaffolding is intentionally repeated for continuity, but it is
    # not a useful filename.  Keep the Prompt number above for old-file
    # compatibility, then name the image from its actual visible subject.
    raw = re.sub(
        r"^\s*(?:สร้างรูปภาพ(?:จริงหนึ่งรูป)?|สร้างภาพ|วาดภาพ|create(?:\s+an?)?\s+image|generate(?:\s+an?)?\s+image)\s*[:：-]?\s*",
        "",
        raw,
        flags=re.I,
    )
    raw = re.sub(
        r"^\s*(?:(?:keyframe|เฟรมเริ่มต้น|ภาพเริ่มต้น|starting\s+frame|start\s+frame)\s*)+[:：-]?\s*",
        "",
        raw,
        flags=re.I,
    )
    raw = re.sub(
        r"^\s*(?:(?:extreme\s+)?(?:close[- ]?up|medium(?:\s+wide|\s+close[- ]?up|\s+long)?|wide|long|full)(?:\s+shot)?\s*)?"
        r"(?:(?:lens|เลนส์)\s*\d+\s*mm\s*)?"
        r"(?:(?:camera\s+angle|กล้อง|มุมกล้อง|มุม)(?:ระดับสายตา|สูง(?:เล็กน้อย)?|ต่ำ(?:เล็กน้อย)?|ด้านข้าง|ตรง|เฉียง|eye[- ]?level)?\s*)?",
        "",
        raw,
        flags=re.I,
    )
    cut_markers = (
        "ภาพนิ่ง", "cinematic", "wide shot", "medium shot", "close-up",
        "กล้อง", "มุมกล้อง", "เลนส์", "lens", "foreground", "midground", "background",
        "shot", "perspective", "camera", "lighting", "โทนภาพ",
    )
    lowered = raw.lower()
    cut_at = min((lowered.find(x) for x in cut_markers if lowered.find(x) > 0), default=-1)
    if cut_at > 0:
        raw = raw[:cut_at]
    raw = re.split(r"[.!?\n\r]", raw, 1)[0]
    raw = re.sub(r"[^a-zA-Z0-9\u0E00-\u0E7F\s_-]+", " ", raw)
    words = re.findall(r"[^\s_-]+", raw)
    if words:
        raw = "_".join(words[:5])
    else:
        raw = re.sub(r"\s+", "_", raw).strip("_")
    return (prefix + raw[:max_len].strip("_")) or "image"


def _unique_output_path(out_dir, stem, suffix):
    """Avoid overwriting without putting a date/time in the visible filename."""
    dest = Path(out_dir) / f"{stem}{suffix}"
    if not dest.exists():
        return dest
    number = 2
    while True:
        candidate = Path(out_dir) / f"{stem}_{number}{suffix}"
        if not candidate.exists():
            return candidate
        number += 1


def _download(url, dest, timeout=120):
    """Download file from URL to local path."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
    return dest


def _artifact_snapshot():
    """Return current Bridge artifact IDs; failure is harmless."""
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/v1/chatgpt/admin/artifacts?limit=10",
            headers={"Authorization": f"Bearer {BRIDGE_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        rows = data.get("artifacts") if isinstance(data, dict) else []
        return {str(x.get("file_id") or "") for x in rows or [] if isinstance(x, dict)}
    except Exception:
        return set()


def _recover_new_artifact(before_ids, started_at, out_dir, name_hint, prompt, log):
    """Recover an image saved by Bridge when the HTTP response was lost.

    The Bridge has a global one-image queue, and we also exclude every artifact
    that existed before this request, so this cannot pick an older user's file.
    """
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/v1/chatgpt/admin/artifacts?limit=10",
            headers={"Authorization": f"Bearer {BRIDGE_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        rows = data.get("artifacts") if isinstance(data, dict) else []
        candidates = []
        for item in rows or []:
            if not isinstance(item, dict) or item.get("kind") != "image":
                continue
            file_id = str(item.get("file_id") or "")
            if not file_id or file_id in before_ids:
                continue
            created = str(item.get("created_at") or "").replace("Z", "+00:00")
            try:
                created_ts = datetime.fromisoformat(created).astimezone(timezone.utc).timestamp()
            except Exception:
                created_ts = 0
            if created_ts + 10 < started_at:
                continue
            candidates.append((created_ts, item))
        if not candidates:
            return None
        item = max(candidates, key=lambda row: row[0])[1]
        slug = _scene_slug(str(name_hint or prompt), max_len=64)
        dest = _unique_output_path(Path(out_dir), slug, ".png")
        src_path = Path(str(item.get("path") or ""))
        if src_path.is_file():
            shutil.copyfile(str(src_path), str(dest))
        else:
            url = str(item.get("download_url") or "")
            if not url:
                return None
            if url.startswith("/"):
                url = f"{BRIDGE_URL}{url}"
            download_req = urllib.request.Request(url, headers={"Authorization": f"Bearer {BRIDGE_KEY}"})
            with urllib.request.urlopen(download_req, timeout=60) as response, open(dest, "wb") as output:
                shutil.copyfileobj(response, output)
        if dest.is_file() and dest.stat().st_size > 0:
            log(f"[image-gen] ✓ กู้รูปที่ Bridge สร้างเสร็จแล้วกลับมาอัตโนมัติ: {dest}")
            return str(dest)
    except Exception:
        pass
    return None


def generate_image(prompt, *, output_dir=None, name_hint=None,
                   is_edit=False, ref_images=None, aspect_ratio="1:1",
                   save_sidecar=True, log_fn=None, use_story_history=False,
                   use_ref_story_history=False, use_story_face_history=False,
                   use_prop_history=False, conversation_state=None,
                   conversation_save_fn=None, temporary_chat=False):
    """Generate one image via chatgpt-api bridge.

    Returns local path to downloaded image, or raises RuntimeError.

    Args:
        prompt: image generation prompt
        output_dir: where to save (default: DEFAULT_OUTPUT_DIR or cwd/ai_images)
        name_hint: filename prefix (default: slug of prompt)
        is_edit: use /images/edits endpoint (requires ref_images)
        ref_images: list of base64-encoded reference images for edit mode
        aspect_ratio: "1:1", "16:9", "9:16", etc.
        save_sidecar: save .txt sidecar with prompt
        log_fn: per-call log function (falls back to global _log_fn)
        use_story_history: continue and update Image AI's persistent story chat.
            Keep False for Ref, Prop, Story Face, video slots, and other pages.
    """
    log = log_fn or _log

    # Register a changed storyboard only in the existing Image AI history.
    if use_story_history:
        ensure_latest_storyboard_context(log_fn=log)

    # ── Build payload ──────────────────────────────────────────
    # A detailed cinematic prompt by itself can make ChatGPT answer with text
    # instead of reliably invoking its image tool.  The browser succeeds when
    # the user explicitly asks it to create an image, so enforce that same
    # intent centrally for every SnapGen page without changing visual details.
    submitted_prompt = str(prompt or "").strip()
    aspect_ratio = str(aspect_ratio or "1:1").strip()
    if aspect_ratio != "1:1":
        orientation = "portrait vertical" if aspect_ratio in {"9:16", "3:4", "2:3"} else "landscape horizontal"
        submitted_prompt = (
            f"OUTPUT CANVAS LOCK: {aspect_ratio} {orientation}. "
            f"The final image itself must use exactly this aspect ratio; do not place a {aspect_ratio} image inside another canvas.\n\n"
            + submitted_prompt
        )

    if not re.match(r"^(?:สร้าง|วาด|generate|create|make)\s*(?:รูป|ภาพ|image|an?\s+image)", submitted_prompt, re.I):
        action = "แก้ไขและสร้างรูปภาพใหม่จริงหนึ่งรูป" if is_edit else "สร้างรูปภาพจริงหนึ่งรูป"
        submitted_prompt = (
            f"{action}ตามคำอธิบายต่อไปนี้ ใช้เครื่องมือสร้างภาพทันที "
            "ห้ามตอบเป็นข้อความ ห้ามอธิบาย และต้องส่งผลลัพธ์เป็นรูปภาพ:\n\n"
            + submitted_prompt
        )
    endpoint = "/v1/images/edits" if is_edit else "/v1/images/generations"
    payload = {
        "model": MODEL,
        "prompt": submitted_prompt,
        "n": 1,
        "size": {
            "9:16": "1024x1792",
            "16:9": "1792x1024",
            "3:4": "1024x1792",
            "2:3": "1024x1792",
            "4:3": "1792x1024",
            "3:2": "1792x1024",
        }.get(aspect_ratio, "1024x1024"),
        "response_format": "b64_json",
    }
    if temporary_chat and not _temporary_chat_unsupported[0]:
        payload["metadata"] = {"history_and_training_disabled": True}
    if is_edit and ref_images:
        payload["images"] = ref_images
    if aspect_ratio and aspect_ratio != "1:1":
        payload["aspect_ratio"] = aspect_ratio
    if use_story_history and _story_conversation["conversation_id"] and _story_conversation.get("account_alias"):
        payload["metadata"] = {
            "conversation_id": _story_conversation["conversation_id"],
            "parent_message_id": _story_conversation["parent_message_id"],
        }
        payload["chatgpt_account"] = _story_conversation["account_alias"]
    elif use_ref_story_history:
        payload.update(get_ref_story_request_context())
    elif use_story_face_history and _story_face_conversation["conversation_id"] and _story_face_conversation.get("account_alias"):
        payload["metadata"] = {
            "conversation_id": _story_face_conversation["conversation_id"],
            "parent_message_id": (
                _story_face_conversation.get("story_context_parent_message_id")
                or _story_face_conversation["parent_message_id"]
            ),
        }
        payload["chatgpt_account"] = _story_face_conversation["account_alias"]
    elif use_prop_history and _prop_conversation["conversation_id"] and _prop_conversation.get("account_alias"):
        payload["metadata"] = {
            "conversation_id": _prop_conversation["conversation_id"],
            "parent_message_id": _prop_conversation["parent_message_id"],
        }
        payload["chatgpt_account"] = _prop_conversation["account_alias"]
    elif isinstance(conversation_state, dict) and conversation_state.get("conversation_id") and conversation_state.get("account_alias"):
        payload["metadata"] = {
            "conversation_id": conversation_state["conversation_id"],
            "parent_message_id": (
                conversation_state.get("story_context_parent_message_id")
                or conversation_state["parent_message_id"]
            ),
        }
        payload["chatgpt_account"] = conversation_state["account_alias"]

    # ── Output dir ─────────────────────────────────────────────
    out_dir = Path(output_dir or DEFAULT_OUTPUT_DIR or os.path.join(os.getcwd(), "ai_images"))
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        project_root = Path(__file__).resolve().parent.parent
        export_root = (project_root / "export").resolve()
        out_resolved = out_dir.resolve()
        if out_resolved == export_root or export_root in out_resolved.parents:
            save_sidecar = False
    except Exception:
        pass

    # ── Retry loop ─────────────────────────────────────────────
    last_error = None
    request_started = time.time()
    artifact_ids_before = set()
    lost_history_reset = False
    retry_fresh_chat = False

    def reset_lost_history(text):
        """Forget a page-owned chat that the active account cannot open."""
        nonlocal lost_history_reset, retry_fresh_chat
        temporary = (payload.get("metadata") or {}).get("history_and_training_disabled")
        if temporary and not lost_history_reset and _is_conversation_lost(text):
            # Some accounts cannot read back a temporary chat; a new normal
            # chat is still separate from every story history.
            lost_history_reset = retry_fresh_chat = True
            payload.pop("metadata", None)
            _temporary_chat_unsupported[0] = True
            log("[image-gen] บัญชีนี้ใช้แชตชั่วคราวไม่ได้ — สร้างในแชตใหม่แยกแทน")
            return True
        if (
            lost_history_reset
            or not isinstance(conversation_state, dict)
            or current_history is not conversation_state
            or not expected_conversation_id
            or not _is_conversation_lost(text)
        ):
            return False
        # The chat lives in another ChatGPT account (account switched), so
        # this story continues in a new chat on the active account.
        lost_history_reset = retry_fresh_chat = True
        for key in ("conversation_id", "parent_message_id", "account_alias", "story_context_parent_message_id"):
            conversation_state.pop(key, None)
        if callable(conversation_save_fn):
            conversation_save_fn()
        log("[image-gen] แชตเดิมอยู่ในบัญชี ChatGPT อื่น — เริ่มแชตใหม่ของเรื่องนี้ในบัญชีปัจจุบัน")
        return True

    current_history = None
    expected_conversation_id = ""
    attempt = -1
    while True:
        attempt += 1
        if retry_fresh_chat:
            retry_fresh_chat = False
        elif attempt > RETRY_COUNT + (1 if lost_history_reset else 0):
            break
        elif attempt > 0:
            log(f"[image-gen] retry {attempt}/{RETRY_COUNT} in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

        try:
            # Hold the lock for the complete HTTP operation.  Previously it
            # protected only Request construction, so another page could send
            # an image job while this request was still running.
            with _queue_lock:
                # Read the newest cursor only when this request reaches the
                # queue. Jobs started close together must continue from the
                # immediately previous result, not branch from a stale parent.
                current_history = None
                if use_story_history:
                    current_history = _story_conversation
                elif use_ref_story_history:
                    current_history = _ref_story_conversation
                elif use_story_face_history:
                    current_history = _story_face_conversation
                elif use_prop_history:
                    current_history = _prop_conversation
                elif isinstance(conversation_state, dict):
                    current_history = conversation_state
                expected_conversation_id = ""
                expected_account_alias = ""
                if current_history and current_history.get("conversation_id") and current_history.get("parent_message_id"):
                    expected_conversation_id = str(current_history["conversation_id"])
                    expected_account_alias = str(current_history.get("account_alias") or "")
                    payload["metadata"] = {
                        "conversation_id": current_history["conversation_id"],
                        "parent_message_id": (
                            current_history.get("story_context_parent_message_id")
                            or current_history["parent_message_id"]
                            if current_history.get("story_context_parent_message_id")
                            else current_history["parent_message_id"]
                        ),
                    }
                    if current_history.get("account_alias"):
                        payload["chatgpt_account"] = current_history["account_alias"]
                    else:
                        payload.pop("chatgpt_account", None)
                elif current_history is not None:
                    payload.pop("metadata", None)
                    payload.pop("chatgpt_account", None)
                request_started = time.time()
                artifact_ids_before = _artifact_snapshot()
                data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    f"{BRIDGE_URL}{endpoint}",
                    data=data_bytes,
                    headers={
                        "Authorization": f"Bearer {BRIDGE_KEY}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    result = json.loads(resp.read().decode("utf-8", errors="replace"))

            # ── Parse response ─────────────────────────────────
            if "error" in result:
                err = result["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                if reset_lost_history(f"{msg} {err}"):
                    continue
                raise RuntimeError(f"Bridge error: {msg}")

            if expected_conversation_id:
                returned_conversation_id = str(result.get("conversation_id") or "").strip()
                returned_parent_message_id = str(result.get("parent_message_id") or "").strip()
                if not returned_conversation_id or not returned_parent_message_id:
                    raise RuntimeError("Bridge สร้างรูปแล้วแต่ไม่คืน cursor ของประวัติเรื่องเดิม")
                if returned_conversation_id != expected_conversation_id:
                    raise RuntimeError(
                        "Bridge เปิดแชตใหม่แทนประวัติเรื่องเดิม — ยกเลิกการเปลี่ยน cursor"
                    )
                returned_account_alias = str(result.get("chatgpt_account") or "").strip()
                if expected_account_alias and returned_account_alias and returned_account_alias != expected_account_alias:
                    raise RuntimeError("Bridge ใช้บัญชีไม่ตรงกับประวัติเรื่องเดิม")

            data_list = result.get("data", [])
            if not data_list:
                raise RuntimeError("Bridge returned no data (empty response)")
            if use_story_history:
                if result.get("conversation_id"):
                    _story_conversation["conversation_id"] = result["conversation_id"]
                if result.get("parent_message_id"):
                    _story_conversation["parent_message_id"] = result["parent_message_id"]
                if result.get("chatgpt_account"):
                    _story_conversation["account_alias"] = result["chatgpt_account"]
                if (
                    _story_conversation["conversation_id"]
                    and _story_conversation["parent_message_id"]
                ):
                    _save_story_conversation()
            elif use_ref_story_history:
                if result.get("conversation_id"):
                    _ref_story_conversation["conversation_id"] = result["conversation_id"]
                if result.get("parent_message_id"):
                    _ref_story_conversation["parent_message_id"] = result["parent_message_id"]
                if result.get("chatgpt_account"):
                    _ref_story_conversation["account_alias"] = result["chatgpt_account"]
                if _ref_story_conversation["conversation_id"] and _ref_story_conversation["parent_message_id"]:
                    _save_ref_story_conversation()
            elif use_story_face_history:
                if result.get("conversation_id"):
                    _story_face_conversation["conversation_id"] = result["conversation_id"]
                if result.get("parent_message_id"):
                    _story_face_conversation["parent_message_id"] = result["parent_message_id"]
                if result.get("chatgpt_account"):
                    _story_face_conversation["account_alias"] = result["chatgpt_account"]
                if _story_face_conversation["conversation_id"] and _story_face_conversation["parent_message_id"]:
                    _save_story_face_conversation()
            elif use_prop_history:
                if result.get("conversation_id"):
                    _prop_conversation["conversation_id"] = result["conversation_id"]
                if result.get("parent_message_id"):
                    _prop_conversation["parent_message_id"] = result["parent_message_id"]
                if result.get("chatgpt_account"):
                    _prop_conversation["account_alias"] = result["chatgpt_account"]
                if _prop_conversation["conversation_id"] and _prop_conversation["parent_message_id"]:
                    _save_prop_conversation()
            elif isinstance(conversation_state, dict):
                if result.get("conversation_id"):
                    conversation_state["conversation_id"] = str(result["conversation_id"])
                if result.get("parent_message_id"):
                    conversation_state["parent_message_id"] = str(result["parent_message_id"])
                if result.get("chatgpt_account"):
                    conversation_state["account_alias"] = str(result["chatgpt_account"])
                if callable(conversation_save_fn):
                    conversation_save_fn()

            slug = _scene_slug(str(name_hint or prompt), max_len=64)
            ext = ".png"
            dest = _unique_output_path(out_dir, slug, ext)

            item = data_list[0]
            b64 = item.get("b64_json")
            src_path = item.get("path")
            if b64:
                dest.write_bytes(base64.b64decode(b64))
            elif src_path and Path(str(src_path)).exists():
                shutil.copyfile(str(src_path), str(dest))
            else:
                url = item.get("url") or item.get("download_url")
                if not url:
                    raise RuntimeError(f"Bridge returned no image bytes/path/url: {json.dumps(item, ensure_ascii=False)[:200]}")
                if url.startswith("/"):
                    url = f"{BRIDGE_URL}{url}"
                _download(url, str(dest), timeout=120)

            # ── Save sidecar ───────────────────────────────────
            if save_sidecar:
                sidecar = dest.with_suffix(".txt")
                sidecar.write_text(prompt, encoding="utf-8")

            log(f"[image-gen] ✓ {dest}")
            return str(dest)

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            if reset_lost_history(body):
                continue
            last_error = RuntimeError(f"HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            last_error = RuntimeError(f"Connection error: {e.reason}")
        except RuntimeError as e:
            last_error = e
        except Exception as e:
            last_error = RuntimeError(f"Unexpected: {e}")

        recovered = _recover_new_artifact(
            artifact_ids_before, request_started, out_dir, name_hint, prompt, log,
        )
        if recovered:
            return recovered

    raise last_error or RuntimeError("Image generation failed after all retries")


def generate_images_batch(prompts, *, output_dir=None, name_hints=None,
                          parallel=False, log_fn=None, **kwargs):
    """Generate multiple images (sequential or parallel).

    Args:
        prompts: list of prompt strings
        name_hints: optional list of filename hints
        parallel: if True, run in threads (max 2 concurrent for bridge)
        **kwargs: passed to generate_image()

    Returns list of (prompt, path_or_error) tuples.
    """
    results = []

    if parallel:
        sem = threading.Semaphore(2)  # bridge concurrency limit
        threads = []

        def _worker(idx, p):
            sem.acquire()
            try:
                hint = name_hints[idx] if name_hints and idx < len(name_hints) else None
                path = generate_image(p, output_dir=output_dir, name_hint=hint,
                                      log_fn=log_fn, **kwargs)
                results.append((p, path))
            except Exception as e:
                results.append((p, e))
            finally:
                sem.release()

        for i, p in enumerate(prompts):
            t = threading.Thread(target=_worker, args=(i, p), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()
    else:
        for i, p in enumerate(prompts):
            try:
                hint = name_hints[i] if name_hints and i < len(name_hints) else None
                path = generate_image(p, output_dir=output_dir, name_hint=hint,
                                      log_fn=log_fn, **kwargs)
                results.append((p, path))
            except Exception as e:
                results.append((p, e))

    return results


# ── Convenience: encode image to base64 for edit mode ────────────
def encode_image_b64(path):
    """Read image file and return base64 string."""
    import base64
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")
