# -*- coding: utf-8 -*-
"""Desktop launcher for Tidmunz Studio (built as a single small .exe).

The program itself lives in %LOCALAPPDATA%\\Tidmunz Studio.  This launcher:
- opens the installed program directly, or
- on first run shows a progress window while it downloads the latest GitHub
  Release and runs setup_and_run.bat (Python 3.12 + packages), then opens it.

Program updates are done inside the app (Settings -> update button), so this
launcher rarely needs to change.  Standard library only.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

OWNER = "apinanautan"
REPOSITORY = "tidmunz-studio"
API_LATEST = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
APP_NAME = "Tidmunz Studio"
MAIN_SCRIPT = "snapgen_gui_v2.py"
SETUP_STEPS = 7  # "[n/7]" markers printed by setup_and_run.bat
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def install_home() -> Path:
    override = os.environ.get("TIDMUNZ_HOME", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def _request(url: str, timeout: int = 60):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tidmunz-Studio-Launcher/1.1",
        },
    )
    return urllib.request.urlopen(req, timeout=timeout)


def latest_release() -> dict:
    with _request(API_LATEST, timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))
    tag = str(release.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("GitHub Release ล่าสุดไม่มีเลขเวอร์ชัน")
    return {"tag": tag, "version": tag.lstrip("v")}


def install_program(home: Path, progress=lambda _text, _fraction=None: None) -> str:
    """Download program files of the latest Release into ``home``.

    ``progress(text, fraction)`` receives 0..1 for the download.  Only program
    files are written; snapgen_data, export and .venv312 are never touched.
    """
    progress("กำลังตรวจเวอร์ชันล่าสุด ...", 0.0)
    info = latest_release()
    url = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/zipball/{info['tag']}"
    buffer = io.BytesIO()
    with _request(url, timeout=300) as response:
        total = int(response.headers.get("Content-Length") or 0)
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            buffer.write(chunk)
            done = buffer.tell()
            mb = done / 1048576
            if total:
                progress(f"ดาวน์โหลดโปรแกรม v{info['version']} ... {mb:.1f} MB", done / total)
            else:  # GitHub zipballs are streamed without a size
                progress(f"ดาวน์โหลดโปรแกรม v{info['version']} ... {mb:.1f} MB", min(0.95, mb / 4.0))
    progress("กำลังแตกไฟล์โปรแกรม ...", 1.0)
    home.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(buffer) as archive:
        for member in archive.infolist():
            parts = Path(member.filename).parts[1:]  # strip "owner-repo-sha/"
            if not parts or member.is_dir():
                continue
            if any(p in ("..", "") for p in parts) or parts[0] in ("snapgen_data", "export"):
                continue
            data = archive.read(member)
            if parts[-1].lower().endswith((".bat", ".cmd")):
                # cmd.exe misparses LF-only batch files; force CRLF.
                data = re.sub(rb"\r?\n", b"\r\n", data)
            target = home.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()  # setup_and_run.bat hides some files; Windows refuses to truncate hidden files
            target.write_bytes(data)
    meta = home / "snapgen_data" / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "snapgen_version.json").write_text(
        json.dumps({"version": info["version"]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return info["version"]


def _venv_python(home: Path, windowed: bool = True) -> Path | None:
    names = ("pythonw.exe", "python.exe") if windowed else ("python.exe",)
    for name in names:
        candidate = home / ".venv312" / "Scripts" / name
        if candidate.is_file():
            return candidate
    return None


def run_setup(home: Path, progress=lambda _text, _fraction=None: None) -> None:
    """Run setup_and_run.bat hidden and report its "[n/7]" steps."""
    script = home / "setup_and_run.bat"
    if not script.is_file():
        raise RuntimeError(f"ไม่พบ {script}")
    proc = subprocess.Popen(
        ["cmd.exe", "/d", "/c", "call", str(script), "--no-run"],
        cwd=str(home),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )
    tail: list[str] = []
    step = 0
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode("oem", errors="replace").strip()
        if not line or set(line) <= {"="}:
            continue
        tail = (tail + [line])[-15:]
        match = re.match(r"\[(\d+)/(\d+)\]", line)
        if match:
            step = int(match.group(1))
        progress(line, min(step, SETUP_STEPS) / SETUP_STEPS)
    code = proc.wait()
    if code != 0 or _venv_python(home) is None:
        raise RuntimeError("ติดตั้ง Python/แพ็กเกจไม่สำเร็จ\n\n" + "\n".join(tail))


def start_app(home: Path) -> bool:
    python = _venv_python(home)
    if python is None:
        return False
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [str(python), "-B", str(home / MAIN_SCRIPT)],
        cwd=str(home),
        creationflags=flags,
        close_fds=True,
    )
    return True


def _show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def _setup_with_window(home: Path) -> None:
    """Progress window: download (0-30%) then Python/package setup (30-100%)."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} - ติดตั้งครั้งแรก")
    root.geometry("560x200")
    root.resizable(False, False)
    title = tk.StringVar(value="กำลังเตรียมติดตั้ง ... 0%")
    detail = tk.StringVar(value="")
    tk.Label(root, textvariable=title, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x", padx=20, pady=(22, 8))
    bar = ttk.Progressbar(root, maximum=100, length=520)
    bar.pack(padx=20)
    tk.Label(root, textvariable=detail, anchor="w", justify="left", wraplength=520, fg="#555").pack(fill="x", padx=20, pady=10)
    hint = tk.Label(root, text="ครั้งแรกอาจใช้เวลาหลายนาที ห้ามปิดหน้าต่างนี้", fg="#888")
    hint.pack(fill="x", padx=20)
    result: dict = {}

    def show(stage: str, low: float, high: float, text: str, fraction) -> None:
        if fraction is None:
            fraction = 0.0
        percent = int(low + (high - low) * max(0.0, min(1.0, fraction)))
        if percent < int(bar["value"]):
            percent = int(bar["value"])  # never move backwards
        bar["value"] = percent
        title.set(f"{stage} ... {percent}%")
        detail.set(text)

    def report(stage, low, high):
        return lambda text, fraction=None: root.after(0, show, stage, low, high, text, fraction)

    def worker():
        try:
            if not (home / MAIN_SCRIPT).is_file():
                install_program(home, report("ดาวน์โหลดโปรแกรม", 0, 30))
            run_setup(home, report("ติดตั้ง Python และแพ็กเกจ", 30, 100))
        except Exception as exc:  # pragma: no cover - network/machine dependent
            result["error"] = exc
        root.after(0, root.destroy)

    root.protocol("WM_DELETE_WINDOW", lambda: None)  # keep window until done
    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    if "error" in result:
        raise RuntimeError(str(result["error"]))


def main() -> int:
    home = install_home()
    try:
        if (home / MAIN_SCRIPT).is_file() and start_app(home):
            return 0
        _setup_with_window(home)
        if not start_app(home):
            raise RuntimeError("ติดตั้งเสร็จแต่ไม่พบ Python ของโปรแกรม")
        return 0
    except Exception as exc:
        _show_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
