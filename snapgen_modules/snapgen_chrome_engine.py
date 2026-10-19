# -*- coding: utf-8 -*-
"""ChatGPT through the user's own Chrome tab (copied into the Bridge as snapgen_chrome.py).

ChatGPT's website stopped accepting the Bridge's replayed requests, but it still
works in a real, logged-in Chrome. Tidmunz runs a small job queue on 127.0.0.1
(snapgen_chrome_relay.py) and the "Tidmunz ChatGPT" extension runs each job in a
ChatGPT tab. This transport is used whenever that extension is connected; story
histories stay real chatgpt.com conversations, so old stories keep working.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator

from chatgpt_api.core.types import ChatDelta, ChatRequest, ImageAsset, ImageRequest, ImageResponse

try:
    from chatgpt_api.core.errors import ProviderError
except Exception:  # older Bridge layout
    from chatgpt_api.providers.chatgpt.transport import ProviderError  # type: ignore

JOB_WAIT = 16 * 60


def _relay_info() -> dict:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    try:
        return json.loads((Path(local) / "Tidmunz Studio" / "snapgen_data" / "chrome_relay.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def chrome_connected() -> bool:
    """Tidmunz's job queue is running (Tidmunz is open). It opens the program's Chrome itself when a
    job arrives and the extension is not connected yet, so a closed Chrome no longer falls back to the
    website replay that ChatGPT refuses."""
    info = _relay_info()
    if not info.get("port"):
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/status", timeout=2) as response:
            json.loads(response.read().decode("utf-8"))
        return True
    except Exception:
        return False


def _data_url(data: bytes, mime: str | None) -> str:
    return f"data:{mime or 'image/png'};base64," + base64.b64encode(data).decode("ascii")


class ChromeTransport:
    """Same surface as ChatGPTWebTransport for what the Bridge calls."""

    def conversation_init(self, *_args, **_kwargs) -> dict[str, Any]:
        return {}

    def stop_conversation(self, *_args, **_kwargs) -> dict[str, Any]:
        return {"stopped": False}

    def stop_deep_research(self, *_args, **_kwargs) -> dict[str, Any]:
        return {"stopped": False}

    async def deep_research(self, request):
        raise ProviderError("Deep Research is not available through the Chrome tab")

    def _job(self, job: dict) -> dict:
        info = _relay_info()
        if not info.get("port"):
            raise ProviderError("Tidmunz Chrome relay is not running")
        request = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}/job?token={info.get('token', '')}",
            data=json.dumps(job).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=JOB_WAIT) as response:
            result = json.loads(response.read().decode("utf-8"))
        error = str(result.get("error") or "")
        if error:
            if result.get("not_found") or "conversation failed: 404" in error:
                raise ProviderError("ChatGPT conversation failed: 404 — " + error)
            raise ProviderError("ChatGPT (Chrome): " + error)
        return result

    # ── chat ──
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        result = await asyncio.to_thread(self._chat_sync, request)
        conversation_id = result.get("conversation_id")
        message = {"message": {"id": result.get("message_id")}} if result.get("message_id") else None
        yield ChatDelta(text=str(result.get("text") or ""), conversation_id=conversation_id, raw=message)
        yield ChatDelta(conversation_id=conversation_id, raw=message, done=True)

    def _chat_sync(self, request: ChatRequest) -> dict:
        messages = list(request.messages or [])
        images: list[str] = []
        if request.conversation_id:
            prompt = _message_text(_latest_user(messages), images)
        elif len(messages) == 1:
            prompt = _message_text(messages[0], images)
        else:
            prompt = "\n\n".join(f"[{str(getattr(m, 'role', 'user')).upper()}]\n{_message_text(m, images)}" for m in messages)
        return self._job({"type": "chat", "prompt": prompt, "images": images,
                          "conversation_id": request.conversation_id or None})

    # ── pictures ──
    async def generate_image(self, request: ImageRequest) -> ImageResponse:
        return await asyncio.to_thread(self._image_sync, request)

    def _image_sync(self, request: ImageRequest) -> ImageResponse:
        metadata = request.metadata or {}
        images: list[str] = []
        if request.image is not None:
            images.append(_data_url(request.image, request.image_mime_type))
        for item in request.input_images or []:
            images.append(_data_url(item.data, item.mime_type))
        conversation_id = metadata.get("conversation_id") if isinstance(metadata.get("conversation_id"), str) else None
        result = self._job({"type": "image", "prompt": request.prompt, "images": images,
                            "conversation_id": conversation_id})
        callback = metadata.get("on_conversation_id")
        if result.get("conversation_id") and callable(callback):
            callback(result["conversation_id"])
        pictures = []
        for url in result.get("images") or []:
            head, _, payload = str(url).partition(",")
            pictures.append(ImageAsset(data=base64.b64decode(payload), mime_type=(head[5:].split(";")[0] or "image/png")))
        if not pictures:
            text = str(result.get("text") or "").strip()
            low = text.lower()
            if any(w in low for w in ("limit", "ลิมิต", "try again", "ลองใหม่อีกครั้ง", "ขีดจำกัด")):
                raise ProviderError("ChatGPT image rate limit: " + text[:300])
            raise ProviderError("ChatGPT did not draw the image. GPT said: " + (text or "no picture"))
        return ImageResponse(images=pictures, prompt=request.prompt,
                             raw={"conversation_id": result.get("conversation_id"),
                                  "parent_message_id": result.get("message_id"), "engine": "chrome"})


def _latest_user(messages):
    for message in reversed(messages):
        if getattr(message, "role", "") == "user":
            return message
    return messages[-1] if messages else None


def _message_text(message, images: list[str]) -> str:
    if message is None:
        return ""
    parts = []
    for part in getattr(message, "content", None) or []:
        kind = getattr(part, "kind", "")
        if kind == "text":
            parts.append(str(getattr(part, "text", "") or ""))
        elif kind in ("image_bytes", "image_url"):
            data = getattr(part, "data", None)
            url = str(getattr(part, "url", "") or "")
            if data is not None:
                images.append(_data_url(data, getattr(part, "mime_type", None)))
            elif url.startswith("data:"):
                images.append(url)
            elif url.startswith("http"):
                with urllib.request.urlopen(url, timeout=60) as response:
                    images.append(_data_url(response.read(), response.headers.get_content_type()))
        elif kind == "file_bytes" and getattr(part, "data", None):
            # The ChatGPT tab only takes pictures: a document goes in as its text (a .docx story file).
            name = str(getattr(part, "name", "") or "file")
            parts.append(f"\n===== ไฟล์แนบ: {name} =====\n{_document_text(part.data, name)}\n===== จบไฟล์ {name} =====")
    return "\n".join(p for p in parts if p)


def _document_text(data: bytes, name: str) -> str:
    """Plain text of an attached document: .docx paragraphs, anything else decoded as text."""
    if name.lower().endswith(".docx") or data[:2] == b"PK":
        import io
        import re
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
            lines = ["".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)) for p in paragraphs]
            text = "\n".join(lines)
            for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
                text = text.replace(a, b)
            return text.strip()
        except Exception as exc:
            return f"(อ่านไฟล์ {name} ไม่ได้: {exc})"
    return data.decode("utf-8", errors="replace")


def chrome_provider_for_account(config, account: str | None):
    """A ChatGPTProvider backed by the Chrome tab when the extension is connected, else None."""
    # SNAPGEN_ENGINE=web only means "not Codex" (set when Codex became opt-in), so it keeps Chrome;
    # "replay" forces the old website replay, "codex" forces Codex.
    if os.environ.get("SNAPGEN_ENGINE", "").strip().lower() in ("replay", "codex") or not chrome_connected():
        return None
    from chatgpt_api.providers.chatgpt.provider import ChatGPTProvider
    provider = ChatGPTProvider(ChromeTransport())  # type: ignore[arg-type]
    setattr(provider, "_chatgpt_api_account", account)
    return provider
