# -*- coding: utf-8 -*-
"""Install conversation cursor support for Bridge's /v1/chatgpt/vision route."""
from __future__ import annotations

from pathlib import Path
import py_compile

MARKER = 'vision metadata requires both conversation_id and parent_message_id'


def supported(bridge_dir) -> bool:
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        MARKER in text
        and 'conversation_state=conversation_state' in text
        and 'response[key] = value' in text
    )


def install(bridge_dir, log=print) -> bool:
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
    if not path.is_file():
        raise RuntimeError(f"ไม่พบ Bridge source: {path}")
    if supported(bridge_dir):
        return False
    text = path.read_text(encoding="utf-8")
    old = '''    temporary_chat = _resolve_temporary_chat_mode(config, body)
    router = _router_for_request(config, router, body)
    parts = [
        *(ContentPart.image_bytes(image.data, image.mime_type, image.name) for image in input_images),
        ContentPart.text_part(prompt),
    ]
    account, _provider, text = await _collect_messages_text_with_accounts(
        config,
        router,
        [{"role": "user", "content": parts}],
        requested_model,
        model_slug,
        thinking_effort,
        temporary_chat,
    )
    response = _completion_response(requested_model, text, [], account=account)
    response.update(
        {
            "object": "chatgpt.vision",
            "text": text,
            "mode": mode,
            "input_image_count": len(input_images),
            "limits_note": (
                "A single request can attach up to 10 images. The bridge preflights reported "
                "file_upload and image quotas when ChatGPT exposes them, but hidden burst limits may still apply."
            ),
        }
    )
    return response
'''
    new = '''    request_metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    conversation_id = _str_or_none(request_metadata.get("conversation_id"))
    parent_message_id = _str_or_none(request_metadata.get("parent_message_id"))
    if bool(conversation_id) != bool(parent_message_id):
        raise ValueError("vision metadata requires both conversation_id and parent_message_id")
    conversation_state: dict[str, str | None] = {
        "conversation_id": conversation_id,
        "parent_message_id": parent_message_id,
    }
    temporary_chat = False if conversation_id else _resolve_temporary_chat_mode(config, body)
    router = _router_for_request(config, router, body)
    parts = [
        *(ContentPart.image_bytes(image.data, image.mime_type, image.name) for image in input_images),
        ContentPart.text_part(prompt),
    ]
    account, _provider, text = await _collect_messages_text_with_accounts(
        config,
        router,
        [{"role": "user", "content": parts}],
        requested_model,
        model_slug,
        thinking_effort,
        temporary_chat,
        conversation_id=conversation_id,
        parent_message_id=parent_message_id,
        conversation_state=conversation_state,
    )
    response = _completion_response(requested_model, text, [], account=account)
    response.update(
        {
            "object": "chatgpt.vision",
            "text": text,
            "mode": mode,
            "input_image_count": len(input_images),
            "limits_note": (
                "A single request can attach up to 10 images. The bridge preflights reported "
                "file_upload and image quotas when ChatGPT exposes them, but hidden burst limits may still apply."
            ),
        }
    )
    for key in ("conversation_id", "parent_message_id"):
        value = conversation_state.get(key)
        if isinstance(value, str) and value:
            response[key] = value
    return response
'''
    if old not in text:
        raise RuntimeError("โครงสร้าง Vision endpoint ของ Bridge ไม่ตรงกับ patch")
    text = text.replace(old, new, 1)
    temp = path.with_suffix(path.suffix + ".vision-cursor.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    if not supported(bridge_dir):
        raise RuntimeError("ติดตั้ง Vision conversation cursor ไม่ครบ")
    log("✓ Bridge Vision ใช้ประวัติ Image AI เดิมแล้ว")
    return True
