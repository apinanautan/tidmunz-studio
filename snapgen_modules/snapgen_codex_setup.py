# -*- coding: utf-8 -*-
"""Install the official Codex CLI for this workstation and sign it in to ChatGPT.

The Bridge sends ChatGPT work through Codex (snapgen_codex_engine.py). Opening
Tidmunz installs Codex by itself when the PC has none, and asks once to sign
in; signing in opens ChatGPT's own login page in the browser (no captures).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

CODEX_VERSION = "0.162.1"
PACKAGE_URL = ("https://github.com/openai/codex/releases/download/rust-v0.162.1/"
               "codex-package-x86_64-pc-windows-msvc.tar.gz")
PACKAGE_SHA256 = "3e993a82ff393f4530558dcaadb36bb4f0fd1ea7243e621348e40b469f900f6a"


def install_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Tidmunz Studio" / "snapgen_data" / "tools" / "codex"


def find_codex() -> str | None:
    own = install_dir() / "bin" / "codex.exe"
    if own.is_file():
        return str(own)
    return shutil.which("codex")


def ensure_codex(log=print) -> str:
    """Path of a working codex; downloads the pinned official build when the PC has none."""
    found = find_codex()
    if found:
        return found
    target = install_dir()
    log(f"[Codex] กำลังติดตั้ง Codex {CODEX_VERSION} (ครั้งเดียว ~160 MB) ...")
    with tempfile.TemporaryDirectory(prefix="tidmunz_codex_dl_") as tmp:
        package = Path(tmp) / "codex.tar.gz"
        digest = hashlib.sha256()
        with urllib.request.urlopen(PACKAGE_URL, timeout=120) as response, open(package, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done, last = 0, -10
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                percent = int(done * 100 / total) if total else 0
                if percent >= last + 10:
                    last = percent
                    log(f"[Codex] ดาวน์โหลด {percent}%")
        if digest.hexdigest() != PACKAGE_SHA256:
            raise RuntimeError("ไฟล์ Codex ที่ดาวน์โหลดไม่ตรงกับของ OpenAI — ไม่ติดตั้ง")
        staging = Path(tmp) / "x"
        with tarfile.open(package, "r:gz") as archive:
            members = [m for m in archive.getmembers() if not m.name.startswith("codex-resources/voice")]
            archive.extractall(staging, members=members, filter="data")
        if not (staging / "bin" / "codex.exe").is_file():
            raise RuntimeError("แพ็กเกจ Codex ไม่มี codex.exe")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(target))
    log("[Codex] ติดตั้งเสร็จ ✓")
    return str(target / "bin" / "codex.exe")


def accounts_dir(bridge_dir) -> Path:
    return Path(bridge_dir) / "secrets" / "accounts"


def active_account(bridge_dir) -> str:
    """The account chosen with Use (Bridge .env), else the first account folder, else account-1."""
    try:
        for line in (Path(bridge_dir) / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("CHATGPT_ACCOUNT="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    except OSError:
        pass
    folder = accounts_dir(bridge_dir)
    names = sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    return names[0] if names else "account-1"


def account_home(bridge_dir, account: str) -> Path:
    return accounts_dir(bridge_dir) / account / "codex"


def logged_in(bridge_dir, account: str | None = None) -> bool:
    """Codex is signed in for this account, another account, or this Windows user."""
    if account and (account_home(bridge_dir, account) / "auth.json").is_file():
        return True
    folder = accounts_dir(bridge_dir)
    if folder.is_dir() and any((p / "codex" / "auth.json").is_file() for p in folder.iterdir() if p.is_dir()):
        return True
    default = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return (default / "auth.json").is_file()


def start_login(bridge_dir, account: str, log=print, on_done=None) -> None:
    """Open ChatGPT's login in the browser for this account (a small window shows the steps)."""
    codex = ensure_codex(log)
    home = account_home(bridge_dir, account)
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home)
    env.pop("OPENAI_API_KEY", None)
    auth = home / "auth.json"
    before = auth.stat().st_mtime if auth.is_file() else 0
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    proc = subprocess.Popen([codex, "login"], env=env, cwd=str(home), creationflags=flags)
    log(f"[Codex] เปิดหน้าล็อกอิน ChatGPT ในเบราว์เซอร์แล้ว ({account}) — ล็อกอินให้เสร็จแล้วกลับมาที่โปรแกรม")

    def wait():
        deadline = time.time() + 900
        while time.time() < deadline:
            if auth.is_file() and auth.stat().st_mtime > before:
                log(f"[Codex] ล็อกอิน ChatGPT สำเร็จ ✓ ({account})")
                if callable(on_done):
                    on_done(True)
                return
            if proc.poll() is not None and not (auth.is_file() and auth.stat().st_mtime > before):
                time.sleep(2)
                if not (auth.is_file() and auth.stat().st_mtime > before):
                    break
            time.sleep(2)
        log(f"[Codex] ยังไม่ได้ล็อกอิน ({account}) — กดปุ่มล็อกอิน ChatGPT อีกครั้งได้")
        if callable(on_done):
            on_done(False)

    import threading
    threading.Thread(target=wait, daemon=True).start()
