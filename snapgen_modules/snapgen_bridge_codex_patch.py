# -*- coding: utf-8 -*-
"""Let the Bridge send chats and pictures through Chrome or the official Codex CLI.

Copies snapgen_chrome_engine.py and snapgen_codex_engine.py into the Bridge and
makes _provider_for_account pick, in order: the user's own ChatGPT tab (when the
"Tidmunz ChatGPT" Chrome extension is connected), Codex (opt-in,
SNAPGEN_ENGINE=codex), then the original website replay. Every page keeps
calling the Bridge the same way.
"""
from __future__ import annotations

from pathlib import Path
import py_compile

MARKER = "# SnapGen Codex engine"
ENGINES = {"snapgen_codex.py": "snapgen_codex_engine.py", "snapgen_chrome.py": "snapgen_chrome_engine.py"}

PROVIDER_OLD = '''    resolved_account = account or config.account
    capture_path = resolve_account_capture_path(resolved_account, config.accounts_dir)
    if not capture_path.exists():'''
# The first version (Codex only), replaced in place on Bridges that already have it.
PROVIDER_V1 = '''    resolved_account = account or config.account
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
PROVIDER_NEW = '''    resolved_account = account or config.account
    # SnapGen Codex engine v2: the user's Chrome tab first, then Codex (opt-in), then the website replay.
    _snapgen_provider = None
    try:
        from chatgpt_api.providers.chatgpt.snapgen_chrome import chrome_provider_for_account as _snapgen_chrome
        _snapgen_provider = _snapgen_chrome(config, resolved_account)
    except Exception:
        _snapgen_provider = None
    if _snapgen_provider is None:
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
    if not compat.is_file():
        raise RuntimeError(f"Bridge source not found: {compat}")
    changed = False
    for name, source in ENGINES.items():
        engine = Path(__file__).with_name(source).read_text(encoding="utf-8")
        target = root / "providers" / "chatgpt" / name
        if not target.is_file() or target.read_text(encoding="utf-8") != engine:
            _write_checked(target, engine)
            changed = True
    text = compat.read_text(encoding="utf-8")
    if PROVIDER_NEW not in text:
        if PROVIDER_V1 in text:
            text = text.replace(PROVIDER_V1, PROVIDER_NEW, 1)
        elif PROVIDER_OLD in text:
            text = text.replace(PROVIDER_OLD, PROVIDER_NEW, 1)
        else:
            raise RuntimeError("Bridge structure changed at _provider_for_account")
        _write_checked(compat, text)
        changed = True
    if accounts.is_file():
        text = accounts.read_text(encoding="utf-8")
        if MARKER not in text and ACCOUNT_OLD in text:
            _write_checked(accounts, text.replace(ACCOUNT_OLD, ACCOUNT_NEW, 1))
            changed = True
    if changed:
        log("✓ Bridge ส่งงาน ChatGPT ผ่าน Chrome (ส่วนขยาย Tidmunz ChatGPT) / Codex ได้แล้ว")
    return changed
