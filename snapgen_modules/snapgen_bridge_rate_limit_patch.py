# -*- coding: utf-8 -*-
"""Report ChatGPT's own tool rate-limit notice instead of "no image asset".

When the image tool is rate limited, ChatGPT records a system_error message
(ChatGPTAgentToolRateLimitException) whose text contains the exact sentence
shown to web users, e.g. "...ถึงลิมิตของแพ็กเกจ Go แล้ว ... อีก 17 ชั่วโมง".
The upstream Bridge only saw "no image asset", waited the full poll timeout
and suggested refreshing the account capture. This patch stops polling as
soon as the notice appears and raises "ChatGPT image rate limit: <notice>".
"""
from __future__ import annotations

from pathlib import Path
import py_compile

MARKER = "# SnapGen image rate limit notice"

HELPER = '''

# SnapGen image rate limit notice
def _snapgen_rate_limit_notice(value):
    """Return ChatGPT's user-facing rate-limit sentence found in events/JSON."""
    import re as _re
    found = []

    def walk(item, depth=0):
        if depth > 40:
            return
        if isinstance(item, dict):
            content = item.get("content")
            if (isinstance(content, dict) and content.get("content_type") == "system_error"
                    and "ratelimit" in str(content.get("name") or "").replace("_", "").lower()):
                found.append(str(content.get("text") or ""))
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)

    walk(value)
    for text in found:
        quoted = _re.search(r'begin your response with "([^"]+)"', text)
        if quoted:
            return quoted.group(1).strip()
    if found:
        return "image generation rate limit reached"
    return None
'''


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if new in source:
        return source
    if old not in source:
        raise RuntimeError(f"Bridge structure changed at {label}")
    return source.replace(old, new, 1)


def rate_limit_supported(bridge_dir) -> bool:
    path = Path(bridge_dir) / "chatgpt_api" / "providers" / "chatgpt" / "transport.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return MARKER in text and text.count('"ChatGPT image rate limit: "') >= 2


def _patch_transport(source: str) -> str:
    poll_old = '''            terminal_error = _conversation_image_terminal_error(data)
            if terminal_error:
                raise ProviderError(terminal_error)
'''
    poll_new = '''            terminal_error = _conversation_image_terminal_error(data)
            if terminal_error:
                raise ProviderError(terminal_error)
            limit_notice = _snapgen_rate_limit_notice(data)
            if limit_notice:
                raise ProviderError("ChatGPT image rate limit: " + limit_notice)
'''
    source = _replace_once(source, poll_old, poll_new, "image poll rate limit")

    final_old = '''        if not assets:
            raise ProviderError("ChatGPT image generation returned no image asset")
'''
    final_new = '''        if not assets:
            limit_notice = _snapgen_rate_limit_notice(events)
            if not limit_notice and conversation_id:
                try:
                    from curl_cffi import requests as _snapgen_requests
                    _snapgen_response = _snapgen_requests.get(
                        f"{self.endpoints.base_url}/backend-api/conversation/{conversation_id}",
                        headers=_json_headers_for_token_refresh(headers),
                        impersonate=self.impersonate,
                        timeout=min(self.timeout, 30.0),
                    )
                    if _snapgen_response.status_code < 400:
                        limit_notice = _snapgen_rate_limit_notice(_json_response(_snapgen_response))
                except Exception:
                    limit_notice = None
            if limit_notice:
                raise ProviderError("ChatGPT image rate limit: " + limit_notice)
            raise ProviderError("ChatGPT image generation returned no image asset")
'''
    source = _replace_once(source, final_old, final_new, "image no-asset rate limit")
    if MARKER not in source:
        source = source.rstrip("\n") + "\n" + HELPER
    return source


def install(bridge_dir, log=print) -> bool:
    path = Path(bridge_dir) / "chatgpt_api" / "providers" / "chatgpt" / "transport.py"
    if not path.is_file():
        raise RuntimeError(f"Bridge source not found: {path}")
    if rate_limit_supported(bridge_dir):
        return False
    updated = _patch_transport(path.read_text(encoding="utf-8"))
    temp = path.with_suffix(path.suffix + ".rate-limit.tmp")
    try:
        temp.write_text(updated, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    if not rate_limit_supported(bridge_dir):
        raise RuntimeError("Bridge rate-limit patch did not install completely")
    log("✓ Bridge แจ้งลิมิตสร้างรูปของ ChatGPT ตรงๆ แล้ว")
    return True
