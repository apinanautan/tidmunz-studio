# -*- coding: utf-8 -*-
"""Desktop launcher for Tidmunz Studio (built as a single small .exe).

The program itself lives in %LOCALAPPDATA%\\Tidmunz Studio.  This launcher:
- opens the installed program, or
- installs it from the latest GitHub Release on first run, then runs
  setup_and_run.bat to prepare Python 3.12 and the packages.

Program updates are done inside the app (Settings -> update button), so this
launcher rarely needs to change.  Standard library only.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

OWNER = "tidmunzsocial-lab"
REPOSITORY = "tidmunz-studio"
API_LATEST = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
APP_NAME = "Tidmunz Studio"
MAIN_SCRIPT = "snapgen_gui_v2.py"


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
            "User-Agent": "Tidmunz-Studio-Launcher/1.0",
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


def install_program(home: Path, progress=lambda _m: None) -> str:
    """Download program files of the latest Release into ``home``.

    Only program files from the repository are written; existing user data
    (snapgen_data, export, .venv312) is never deleted or overwritten.
    """
    info = latest_release()
    progress(f"กำลังดาวน์โหลด {APP_NAME} v{info['version']} ...")
    url = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/zipball/{info['tag']}"
    with _request(url, timeout=300) as response:
        payload = response.read()
    progress("กำลังแตกไฟล์โปรแกรม ...")
    home.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            parts = Path(member.filename).parts[1:]  # strip "owner-repo-sha/"
            if not parts or member.is_dir():
                continue
            if any(p in ("..", "") for p in parts) or parts[0] in ("snapgen_data", "export"):
                continue
            target = home.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    meta = home / "snapgen_data" / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "snapgen_version.json").write_text(
        json.dumps({"version": info["version"]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return info["version"]


def _venv_python(home: Path) -> Path | None:
    for name in ("pythonw.exe", "python.exe"):
        candidate = home / ".venv312" / "Scripts" / name
        if candidate.is_file():
            return candidate
    return None


def run_setup(home: Path) -> None:
    """Run setup_and_run.bat in its own console; it installs then starts the app."""
    script = home / "setup_and_run.bat"
    if not script.is_file():
        raise RuntimeError(f"ไม่พบ {script}")
    subprocess.Popen(
        ["cmd.exe", "/c", str(script)],
        cwd=str(home),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


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


def _install_with_window(home: Path) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("460x120")
    root.resizable(False, False)
    status = tk.StringVar(value="กำลังเตรียมติดตั้งครั้งแรก ...")
    tk.Label(root, textvariable=status, wraplength=420, justify="left", padx=16, pady=24).pack(fill="both")
    result: dict = {}

    def worker():
        try:
            install_program(home, progress=lambda m: root.after(0, status.set, m))
        except Exception as exc:  # pragma: no cover - network dependent
            result["error"] = exc
        root.after(0, root.destroy)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    if "error" in result:
        raise RuntimeError(f"ติดตั้งไม่สำเร็จ: {result['error']}")


def main() -> int:
    home = install_home()
    try:
        if not (home / MAIN_SCRIPT).is_file():
            _install_with_window(home)
            run_setup(home)
            return 0
        if not start_app(home):
            run_setup(home)
        return 0
    except Exception as exc:
        _show_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
