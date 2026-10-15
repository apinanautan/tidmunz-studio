# -*- coding: utf-8 -*-
"""ChatGPT through the official Codex CLI (copied into the Bridge as snapgen_codex.py).

ChatGPT's website started refusing the Bridge's replayed browser requests
("Model not found" behind a Turnstile check). The official Codex CLI signs in
with the same ChatGPT plan and can chat and draw pictures, so the Bridge hands
its work to `codex exec` instead. Pages keep calling the Bridge exactly as
before: a story's chat history becomes one Codex thread (resumed by id) and
pictures come from Codex's image tool. Runs are never ephemeral, because the
Bridge marks chats temporary by default yet pages continue them.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from chatgpt_api.core.types import ChatDelta, ChatRequest, ImageAsset, ImageRequest, ImageResponse

try:
    from chatgpt_api.core.errors import ProviderError
except Exception:  # older Bridge layout
    from chatgpt_api.providers.chatgpt.transport import ProviderError  # type: ignore

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TEXT_TIMEOUT = 600
IMAGE_TIMEOUT = 900
_THREAD_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def codex_binary() -> str | None:
    """Tidmunz's own copy first (installed on every workstation), then one on PATH."""
    candidates = [os.environ.get("SNAPGEN_CODEX_BIN", "")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "Tidmunz Studio" / "snapgen_data" / "tools" / "codex" / "bin" / "codex.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which("codex")


def account_codex_home(accounts_dir: Path, account: str | None) -> Path | None:
    """The Codex sign-in of a Bridge account, else this Windows user's own Codex sign-in."""
    root = Path(accounts_dir).expanduser()
    if account:
        own = root / account / "codex"
        if (own / "auth.json").is_file():
            return own
    # The Bridge's default account name may have no sign-in of its own: any signed-in account works.
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if (folder / "codex" / "auth.json").is_file():
                return folder / "codex"
    default = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if (default / "auth.json").is_file():
        return default
    return None


def engine_wanted() -> bool:
    # Keep existing web stories on their original provider. Codex is opt-in.
    return os.environ.get("SNAPGEN_ENGINE", "web").strip().lower() == "codex"


class CodexTransport:
    """Same surface as ChatGPTWebTransport for what the Bridge calls."""

    def __init__(self, codex: str, home: Path, account: str | None = None) -> None:
        self.codex = codex
        self.home = Path(home)
        self.account = account
        self.workdir = Path(tempfile.gettempdir()) / "tidmunz_codex"
        self.workdir.mkdir(parents=True, exist_ok=True)

    # ── what the Bridge calls besides chat / image ──
    def conversation_init(self, *_args, **_kwargs) -> dict[str, Any]:
        return {}  # no web quota numbers: Codex reports its own limits as errors

    def stop_conversation(self, *_args, **_kwargs) -> dict[str, Any]:
        return {"stopped": False}

    def stop_deep_research(self, *_args, **_kwargs) -> dict[str, Any]:
        return {"stopped": False}

    async def deep_research(self, request):
        raise ProviderError("Deep Research is not available through Codex")

    # ── running codex ──
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self.home)
        env.pop("OPENAI_API_KEY", None)  # always the ChatGPT plan, never a paid API key
        return env

    def _run(self, prompt: str, *, thread_id: str | None, images: list[Path], ephemeral: bool,
             timeout: int) -> tuple[str, str]:
        """Returns (thread_id, final reply). Raises ProviderError with Codex's own message."""
        cmd = [self.codex, "exec"]
        if thread_id:
            cmd += ["resume", thread_id]
        cmd += ["--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"]
        if not thread_id:
            cmd += ["-s", "read-only"]
        if ephemeral:
            cmd += ["--ephemeral"]
        for image in images:
            cmd += ["-i", str(image)]
        cmd += ["-"]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, cwd=str(self.workdir), env=self._env(),
                                  creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("Codex did not answer in time") from exc
        found_thread, replies, errors = thread_id or "", [], []
        for line in (proc.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "thread.started":
                found_thread = event.get("thread_id") or found_thread
            elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                replies.append(str(event["item"].get("text") or ""))
            elif kind in ("error", "turn.failed"):
                errors.append(str(event.get("message") or (event.get("error") or {}).get("message") or event))
        if thread_id and not found_thread_seen(proc.stdout) and "no rollout found" in (proc.stderr or ""):
            raise _history_moved()
        if errors or (proc.returncode and not replies) or (not replies and "Error:" in (proc.stderr or "")):
            raise _codex_error(" ".join(errors) or (proc.stderr or "")[-600:] or f"codex exit {proc.returncode}")
        return found_thread, (replies[-1] if replies else "")

    # ── chat ──
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        thread_id, reply = await asyncio.to_thread(self._chat_sync, request)
        yield ChatDelta(text=reply, conversation_id=thread_id or None,
                        raw={"message": {"id": str(uuid.uuid4())}})
        yield ChatDelta(conversation_id=thread_id or None, raw={"message": {"id": str(uuid.uuid4())}}, done=True)

    def _chat_sync(self, request: ChatRequest) -> tuple[str, str]:
        thread_id = self._resume_id(request.conversation_id)
        with tempfile.TemporaryDirectory(prefix="tidmunz_codex_in_") as tmp:
            images: list[Path] = []
            if thread_id:
                prompt = _message_text(_latest_user(request.messages), images, Path(tmp))
            else:
                prompt = _transcript(request.messages, images, Path(tmp))
            prompt = ("Answer as a plain chat assistant: reply only with the answer text. "
                      "Do not run commands, open files or use tools unless the message asks for a picture.\n\n" + prompt)
            return self._run(prompt, thread_id=thread_id, images=images, ephemeral=False,
                             timeout=TEXT_TIMEOUT)

    def _resume_id(self, conversation_id: str | None) -> str | None:
        if not conversation_id:
            return None
        if _THREAD_RE.match(conversation_id):
            return conversation_id  # Codex says "no rollout found" when it is not one of its threads
        raise _history_moved()

    # ── pictures ──
    async def generate_image(self, request: ImageRequest) -> ImageResponse:
        return await asyncio.to_thread(self._image_sync, request)

    def _image_sync(self, request: ImageRequest) -> ImageResponse:
        metadata = request.metadata or {}
        thread_id = self._resume_id(metadata.get("conversation_id") if isinstance(metadata.get("conversation_id"), str) else None)
        with tempfile.TemporaryDirectory(prefix="tidmunz_codex_img_") as tmp:
            images: list[Path] = []
            inputs = list(request.input_images or [])
            if request.image is not None:
                images.append(_write_image(Path(tmp), request.image, request.image_mime_type, 0))
            for n, item in enumerate(inputs, 1):
                images.append(_write_image(Path(tmp), item.data, item.mime_type, n))
            shape = _aspect_words(metadata.get("size"))
            prompt = ("Use your built-in image_gen tool exactly once to make the picture described below"
                      + (" (edit/follow the attached reference images)" if images else "")
                      + f".{shape} Do not copy or save files yourself: the picture is collected automatically. "
                      "If you cannot draw it, reply with the reason in one sentence.\n\nPICTURE:\n" + request.prompt)
            started = time.time() - 2
            found_thread, reply = self._run(prompt, thread_id=thread_id, images=images, ephemeral=False,
                                            timeout=IMAGE_TIMEOUT)
        made = self._new_images(found_thread, started)
        if not made:
            if _limit_words(reply):
                raise ProviderError("ChatGPT image rate limit: " + reply.strip()[:300])
            raise ProviderError("ChatGPT did not draw the image. GPT said: " + (reply.strip() or "no picture"))
        newest = max(made, key=lambda p: p.stat().st_mtime)
        data = newest.read_bytes()
        mime = "image/png" if newest.suffix.lower() == ".png" else ("image/webp" if newest.suffix.lower() == ".webp" else "image/jpeg")
        parent = str(uuid.uuid4())
        callback = metadata.get("on_conversation_id")
        if found_thread and callable(callback):
            callback(found_thread)
        return ImageResponse(
            images=[ImageAsset(data=data, mime_type=mime, raw={"path": str(newest)})],
            prompt=request.prompt,
            raw={"conversation_id": found_thread or None, "parent_message_id": parent, "engine": "codex"},
        )

    def _new_images(self, thread_id: str, since: float) -> list[Path]:
        root = self.home / "generated_images"
        folders = [root / thread_id] if thread_id and (root / thread_id).is_dir() else ([root] if root.is_dir() else [])
        found = []
        for folder in folders:
            for path in folder.rglob("*"):
                if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and path.stat().st_mtime >= since:
                    found.append(path)
        return found


# ── helpers ──
def found_thread_seen(stdout: str) -> bool:
    return '"thread.started"' in (stdout or "")


def _history_moved() -> ProviderError:
    # A story history made on the ChatGPT website (or deleted): the app decides what to do.
    return ProviderError("ChatGPT conversation failed: 404 — this story's history was on the ChatGPT "
                         "website; Codex keeps a new history (codex history moved)")

def _limit_words(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in ("usage limit", "rate limit", "limit reached", "try again in", "ลิมิต"))


def _codex_error(message: str) -> ProviderError:
    low = message.lower()
    if any(w in low for w in ("not logged in", "login", "unauthorized", "401", "refresh token", "sign in")):
        return ProviderError("ChatGPT Provider status 401: Codex needs to sign in to ChatGPT again — " + message[:300])
    if _limit_words(message):
        return ProviderError("ChatGPT image rate limit: " + message[:300])
    return ProviderError("Codex: " + message[:500])


def _latest_user(messages):
    for message in reversed(list(messages or [])):
        if getattr(message, "role", "") == "user":
            return message
    return (list(messages or []) or [None])[-1]


def _message_text(message, images: list[Path], folder: Path) -> str:
    if message is None:
        return ""
    parts = []
    for part in getattr(message, "content", None) or []:
        kind = getattr(part, "kind", "")
        if kind == "text":
            parts.append(str(getattr(part, "text", "") or ""))
        elif kind in ("image_bytes", "image_url", "file_bytes"):
            data = getattr(part, "data", None)
            url = str(getattr(part, "url", "") or "")
            mime = str(getattr(part, "mime_type", "") or "image/png")
            if data is None and url.startswith("data:"):
                header, _, payload = url.partition(",")
                data = base64.b64decode(payload)
                mime = header[5:].split(";")[0] or mime
            elif data is None and url.startswith("http"):
                with urllib.request.urlopen(url, timeout=60) as response:
                    data = response.read()
            if data is not None and mime.startswith("image/"):
                images.append(_write_image(folder, data, mime, len(images)))
    return "\n".join(p for p in parts if p)


def _transcript(messages, images: list[Path], folder: Path) -> str:
    messages = list(messages or [])
    if len(messages) == 1:
        return _message_text(messages[0], images, folder)
    lines = []
    for message in messages:
        role = str(getattr(message, "role", "user")).upper()
        lines.append(f"[{role}]\n{_message_text(message, images, folder)}")
    return "\n\n".join(lines)


def _write_image(folder: Path, data: bytes, mime: str, index: int) -> Path:
    ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}.get(str(mime).lower(), ".png")
    path = folder / f"ref_{index:02d}{ext}"
    path.write_bytes(data)
    return path


def _aspect_words(size: Any) -> str:
    match = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", str(size or ""))
    if not match:
        return ""
    w, h = int(match.group(1)), int(match.group(2))
    if w > h * 1.2:
        return " Make it a wide landscape picture (about 16:9)."
    if h > w * 1.2:
        return " Make it a tall portrait picture (about 9:16)."
    return " Make it a square picture."


def codex_provider_for_account(config, account: str | None):
    """A ChatGPTProvider backed by Codex, or None to keep the website route."""
    if not engine_wanted():
        return None
    codex = codex_binary()
    if not codex:
        return None
    accounts_dir = Path(getattr(config, "accounts_dir", None) or Path.cwd() / "secrets" / "accounts")
    home = account_codex_home(accounts_dir, account)
    if home is None:
        return None
    from chatgpt_api.providers.chatgpt.provider import ChatGPTProvider
    provider = ChatGPTProvider(CodexTransport(codex, home, account))  # type: ignore[arg-type]
    setattr(provider, "_chatgpt_api_account", account)
    return provider
