# -*- coding: utf-8 -*-
"""Let the Bridge send chats and pictures through the official Codex CLI.

Copies snapgen_codex_engine.py into the Bridge and makes _provider_for_account
return a Codex-backed provider whenever Codex is installed and signed in for
that account (or for this Windows user). Every page keeps calling the Bridge
the same way; set SNAPGEN_ENGINE=web in the Bridge .env to use the website route.
"""
from __future__ import annotations

from pathlib import Path
import py_compile

MARKER = "# SnapGen Codex engine"
ENGINE_NAME = "snapgen_codex.py"

PROVIDER_OLD = '''    resolved_account = account or config.account
    capture_path = resolve_account_capture_path(resolved_account, config.accounts_dir)
    if not capture_path.exists():'''
PROVIDER_NEW = '''    resolved_account = account or config.account
    # SnapGen Codex engine: ChatGPT through the official Codex CLI when it is signed in.
    try:
        from chatgpt_api.providers.chatgpt.snapgen_codex import codex_provider_for_account as _snapgen_codex
        _snapgen_provider = _snapgen_codex(config, resolved_account)
    except Exception:
        _snapgen_provider = None
    if _snapgen_provider is not None:
        return _snapgen_provider
    capture_path = resolve_account_capture_path(resolved_account, config.accounts_dir)
    if not capture_path.exists():'''

ACCOUNT_OLD = '''    @property
    def exists(self) -> bool:
        return self.capture_path.exists()'''
ACCOUNT_NEW = '''    @property
    def exists(self) -> bool:
        # SnapGen Codex engine: an account signed in with Codex needs no browser capture.
        return self.capture_path.exists() or (self.capture_path.parent / "codex" / "auth.json").is_file()'''


def _write_checked(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".codex.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def install(bridge_dir, log=print) -> bool:
    """Return True when Bridge files changed (the running Bridge must restart)."""
    root = Path(bridge_dir) / "chatgpt_api"
    compat = root / "api" / "openai_compat.py"
    accounts = root / "providers" / "chatgpt" / "accounts.py"
    target = root / "providers" / "chatgpt" / ENGINE_NAME
    if not compat.is_file():
        raise RuntimeError(f"Bridge source not found: {compat}")
    changed = False
    engine = (Path(__file__).with_name("snapgen_codex_engine.py")).read_text(encoding="utf-8")
    if not target.is_file() or target.read_text(encoding="utf-8") != engine:
        _write_checked(target, engine)
        changed = True
    text = compat.read_text(encoding="utf-8")
    if MARKER not in text:
        if PROVIDER_OLD not in text:
            raise RuntimeError("Bridge structure changed at _provider_for_account")
        _write_checked(compat, text.replace(PROVIDER_OLD, PROVIDER_NEW, 1))
        changed = True
    if accounts.is_file():
        text = accounts.read_text(encoding="utf-8")
        if MARKER not in text and ACCOUNT_OLD in text:
            _write_checked(accounts, text.replace(ACCOUNT_OLD, ACCOUNT_NEW, 1))
            changed = True
    if changed:
        log("✓ Bridge ส่งงาน ChatGPT ผ่าน Codex (ทางการของ OpenAI) แล้ว")
    return changed
