# -*- coding: utf-8 -*-
"""Keep SnapGen Image AI generations inside one persistent story conversation."""
from __future__ import annotations

from pathlib import Path
import py_compile

MARKER = "# SnapGen image history cursor"
STRICT_MARKER = "# SnapGen image history cursor v2: preserve exactly one conversation"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if new in source:
        return source
    if old not in source:
        raise RuntimeError(f"Bridge structure changed at {label}")
    return source.replace(old, new, 1)


def image_cursor_supported(bridge_dir) -> bool:
    root = Path(bridge_dir) / "chatgpt_api"
    try:
        compat = (root / "api" / "openai_compat.py").read_text(encoding="utf-8")
        transport = (root / "providers" / "chatgpt" / "transport.py").read_text(encoding="utf-8")
    except OSError:
        return False
    return all((
        MARKER in compat,
        MARKER in transport,
        STRICT_MARKER in transport,
        'metadata = dict(request_metadata)' in compat,
        'result["conversation_id"] = conversation_id' in compat,
        'result["parent_message_id"] = parent_message_id' in compat,
        'conversation_id = request.metadata.get("conversation_id")' in transport,
        'payload["conversation_id"] = conversation_id' in transport,
        '"parent_message_id": parent_message_id' in transport,
        'self._repair_invalid_conversation_parent(payload, headers)' in transport,
        'ChatGPT image response switched conversation' in transport,
        '# SnapGen temporary image chat' in transport,
    ))


def _patch_compat(source: str) -> str:
    generation_old = '''    metadata = {
        "size": body.get("size"),
        "quality": body.get("quality"),
        "style": body.get("style"),
        "response_format": body.get("response_format"),
    }
'''
    generation_new = '''    # SnapGen image history cursor: preserve the Image AI story cursor.
    metadata = dict(request_metadata)
    metadata.update({
        "size": body.get("size"),
        "quality": body.get("quality"),
        "style": body.get("style"),
        "response_format": body.get("response_format"),
    })
'''
    source = _replace_once(source, generation_old, generation_new, "image generation metadata")

    edit_old = '''    metadata = {
        "source": "images_edits",
        "response_format": body.get("response_format"),
        "aspect_ratio": aspect_ratio,
        "input_image_count": len(input_images),
        "aspect_ratio_warning": IMAGE_EDIT_ASPECT_RATIO_WARNING,
    }
'''
    edit_new = '''    # SnapGen image history cursor: image edits continue the same story cursor.
    metadata = dict(request_metadata)
    metadata.update({
        "source": "images_edits",
        "response_format": body.get("response_format"),
        "aspect_ratio": aspect_ratio,
        "input_image_count": len(input_images),
        "aspect_ratio_warning": IMAGE_EDIT_ASPECT_RATIO_WARNING,
    })
'''
    source = _replace_once(source, edit_old, edit_new, "image edit metadata")

    response_old = '''    if account:
        result["chatgpt_account"] = account
    return result
'''
    response_new = '''    # SnapGen image history cursor: return the newest cursor to Image AI.
    raw = response.raw if isinstance(response.raw, dict) else {}
    conversation_id = raw.get("conversation_id")
    parent_message_id = raw.get("parent_message_id")
    if isinstance(conversation_id, str) and conversation_id:
        result["conversation_id"] = conversation_id
    if isinstance(parent_message_id, str) and parent_message_id:
        result["parent_message_id"] = parent_message_id
    if account:
        result["chatgpt_account"] = account
    return result
'''
    source = _replace_once(source, response_old, response_new, "image response cursor")
    return source


def _patch_transport(source: str) -> str:
    # Older SnapGen releases may already have the strict cursor patch. Add the
    # temporary-image flag without replaying every historical replacement.
    if STRICT_MARKER in source and "# SnapGen temporary image chat" not in source:
        function_start = source.index("    def _build_image_chat_payload(")
        function_end = source.find("\n    def ", function_start + 20)
        if function_end < 0:
            raise RuntimeError("Bridge image payload function boundary not found")
        block = source[function_start:function_end]
        old = '''        if isinstance(conversation_id, str) and conversation_id:
            payload["conversation_id"] = conversation_id
        return payload
'''
        new = '''        if isinstance(conversation_id, str) and conversation_id:
            payload["conversation_id"] = conversation_id
        # SnapGen temporary image chat: keep utility images out of story history.
        history_disabled = request.metadata.get("history_and_training_disabled")
        if isinstance(history_disabled, bool):
            payload["history_and_training_disabled"] = history_disabled
        return payload
'''
        block = _replace_once(block, old, new, "temporary image chat")
        return source[:function_start] + block + source[function_end:]

    event_old = '''        conversation_id = _conversation_id_from_events(events)
        on_conversation_id = request.metadata.get("on_conversation_id")
'''
    event_new = '''        conversation_id = _conversation_id_from_events(events)
        if not conversation_id:
            saved_conversation_id = request.metadata.get("conversation_id")
            conversation_id = saved_conversation_id if isinstance(saved_conversation_id, str) else None
        parent_message_id = _latest_message_id_from_value(events)
        if not parent_message_id:
            saved_parent_message_id = request.metadata.get("parent_message_id")
            parent_message_id = saved_parent_message_id if isinstance(saved_parent_message_id, str) else None
        on_conversation_id = request.metadata.get("on_conversation_id")
'''
    source = _replace_once(source, event_old, event_new, "image event cursor")

    repair_old = '''        payload = self._build_image_chat_payload(request, uploaded_files)
        if self.refresh_web_tokens:
'''
    repair_new = '''        payload = self._build_image_chat_payload(request, uploaded_files)
        # SnapGen image history cursor v2: preserve exactly one conversation.
        # Text turns already repair a stale parent from the durable mapping;
        # image turns must do the same before sending to ChatGPT.
        # SnapGen guarded parent repair: older Bridge releases lack this helper.
        if hasattr(self, "_repair_invalid_conversation_parent"):
            self._repair_invalid_conversation_parent(payload, headers)
        if self.refresh_web_tokens:
'''
    source = _replace_once(source, repair_old, repair_new, "image parent repair")

    strict_old = '''        conversation_id = _conversation_id_from_events(events)
        if not conversation_id:
            saved_conversation_id = request.metadata.get("conversation_id")
            conversation_id = saved_conversation_id if isinstance(saved_conversation_id, str) else None
        parent_message_id = _latest_message_id_from_value(events)
'''
    strict_new = '''        conversation_id = _conversation_id_from_events(events)
        saved_conversation_id = request.metadata.get("conversation_id")
        if not conversation_id:
            conversation_id = saved_conversation_id if isinstance(saved_conversation_id, str) else None
        if (
            isinstance(saved_conversation_id, str)
            and saved_conversation_id
            and conversation_id != saved_conversation_id
        ):
            raise ProviderError(
                "ChatGPT image response switched conversation "
                f"from {saved_conversation_id} to {conversation_id}"
            )
        parent_message_id = _latest_message_id_from_value(events)
'''
    source = _replace_once(source, strict_old, strict_new, "strict image conversation")

    return_old = '''        return ImageResponse(images=images, prompt=request.prompt, raw={"events": events, "assets": assets})
'''
    return_new = '''        return ImageResponse(
            images=images,
            prompt=request.prompt,
            raw={
                "events": events,
                "assets": assets,
                "conversation_id": conversation_id,
                "parent_message_id": parent_message_id,
            },
        )
'''
    source = _replace_once(source, return_old, return_new, "image raw response cursor")

    function_start = source.index("    def _build_image_chat_payload(")
    function_end = source.find("\n    def ", function_start + 20)
    if function_end < 0:
        raise RuntimeError("Bridge image payload function boundary not found")
    block = source[function_start:function_end]

    timezone_old = '''        timezone_payload = local_timezone_payload()
        system_hints = list(template.get("system_hints") or [])
'''
    timezone_new = '''        timezone_payload = local_timezone_payload()
        # SnapGen image history cursor: continue from the story-ingestion turn.
        conversation_id = request.metadata.get("conversation_id")
        parent_message_id = request.metadata.get("parent_message_id")
        system_hints = list(template.get("system_hints") or [])
'''
    block = _replace_once(block, timezone_old, timezone_new, "image payload cursor values")

    parent_old = '            "parent_message_id": "client-created-root",\n'
    parent_new = '''            "parent_message_id": (
                parent_message_id
                if isinstance(parent_message_id, str) and parent_message_id
                else "client-created-root"
            ),
'''
    block = _replace_once(block, parent_old, parent_new, "image parent message")

    payload_return_old = '''        }
        return payload
'''
    payload_return_new = '''        }
        if isinstance(conversation_id, str) and conversation_id:
            payload["conversation_id"] = conversation_id
        return payload
'''
    block = _replace_once(block, payload_return_old, payload_return_new, "image conversation id")

    temporary_old = payload_return_new
    temporary_new = '''        }
        if isinstance(conversation_id, str) and conversation_id:
            payload["conversation_id"] = conversation_id
        # SnapGen temporary image chat: keep utility images out of story history.
        history_disabled = request.metadata.get("history_and_training_disabled")
        if isinstance(history_disabled, bool):
            payload["history_and_training_disabled"] = history_disabled
        return payload
'''
    block = _replace_once(block, temporary_old, temporary_new, "temporary image chat")
    return source[:function_start] + block + source[function_end:]


GUARD_MARKER = "# SnapGen guarded parent repair"
_UNGUARDED_CALL = "        self._repair_invalid_conversation_parent(payload, headers)\n"
_GUARDED_CALL = (
    "        " + GUARD_MARKER + ": older Bridge releases lack this helper.\n"
    '        if hasattr(self, "_repair_invalid_conversation_parent"):\n'
    "            self._repair_invalid_conversation_parent(payload, headers)\n"
)


def _guard_parent_repair(transport_path: Path) -> bool:
    """Bridges patched by earlier SnapGen releases call a helper that only
    newer Bridge code defines, failing every image request with
    AttributeError. Make that call conditional in place."""
    source = transport_path.read_text(encoding="utf-8")
    if GUARD_MARKER in source or _UNGUARDED_CALL not in source:
        return False
    updated = source.replace(_UNGUARDED_CALL, _GUARDED_CALL, 1)
    temp = transport_path.with_suffix(transport_path.suffix + ".image-cursor.tmp")
    try:
        temp.write_text(updated, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.replace(transport_path)
    finally:
        temp.unlink(missing_ok=True)
    return True


def install(bridge_dir, log=print) -> bool:
    root = Path(bridge_dir) / "chatgpt_api"
    paths = {
        "compat": root / "api" / "openai_compat.py",
        "transport": root / "providers" / "chatgpt" / "transport.py",
    }
    for path in paths.values():
        if not path.is_file():
            raise RuntimeError(f"Bridge source not found: {path}")
    if image_cursor_supported(bridge_dir):
        if _guard_parent_repair(paths["transport"]):
            log("✓ แก้ Bridge: ข้ามการซ่อม parent ที่ Bridge รุ่นนี้ไม่มี")
            return True
        return False

    original = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    updated = {
        "compat": _patch_compat(original["compat"]),
        "transport": _patch_transport(original["transport"]),
    }

    pending = []
    try:
        for name, path in paths.items():
            temp = path.with_suffix(path.suffix + ".image-cursor.tmp")
            temp.write_text(updated[name], encoding="utf-8")
            py_compile.compile(str(temp), doraise=True)
            pending.append((temp, path))
        for temp, path in pending:
            temp.replace(path)
    finally:
        for temp, _path in pending:
            temp.unlink(missing_ok=True)

    if not image_cursor_supported(bridge_dir):
        raise RuntimeError("Bridge image story cursor patch did not install completely")
    log("✓ Bridge สร้างรูปต่อในประวัติเรื่องเดิมแล้ว")
    return True
