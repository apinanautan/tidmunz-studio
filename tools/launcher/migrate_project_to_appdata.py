# -*- coding: utf-8 -*-
"""One-time move of an existing project-folder install to the launcher layout.

Usage (SnapGen must be closed):
    python tools/launcher/migrate_project_to_appdata.py "<project folder>"

- snapgen_data (settings, accounts, models) is moved to
  %LOCALAPPDATA%\\Tidmunz Studio and a junction is left at the old location,
  so absolute paths saved in old JSON files keep working.
- export (videos/images) is NOT moved; the saved export_root keeps using it.
- .venv312 and the FFmpeg suite are copied so nothing needs re-downloading.
- Program files come from the latest GitHub Release (same as a new machine).
- ``Tidmunz Studio.exe`` is copied to the Desktop.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tidmunz_launcher as launcher  # noqa: E402

PRIVATE_ROOT_FILES = (
    "ltx_cloud.json",
    "minimax_cloud.json",
    "snapgen_vast_ed25519",
    "snapgen_vast_ed25519.pub",
)


def _snapgen_running() -> bool:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'snapgen_gui_v2.py' } | % ProcessId"],
        capture_output=True, text=True,
    ).stdout
    return bool(out.strip())


def _is_junction(path: Path) -> bool:
    try:
        return path.is_junction()  # Python 3.12+
    except AttributeError:
        return False


def migrate(project: Path, launcher_exe: Path | None) -> None:
    home = launcher.install_home()
    project = project.resolve()
    if not (project / "snapgen_gui_v2.py").is_file():
        raise SystemExit(f"ไม่ใช่โฟลเดอร์โปรเจกต์: {project}")
    if _snapgen_running():
        raise SystemExit("ปิด SnapGen ก่อน แล้วรันใหม่")
    home.mkdir(parents=True, exist_ok=True)

    old_data = project / "snapgen_data"
    new_data = home / "snapgen_data"
    if _is_junction(old_data):
        print("snapgen_data ย้ายไปแล้ว")
    else:
        if new_data.exists():
            raise SystemExit(f"มี {new_data} อยู่แล้ว — ไม่ย้ายทับ")
        print(f"ย้าย snapgen_data -> {new_data}")
        os.rename(old_data, new_data)  # same drive: instant, nothing copied
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(old_data), str(new_data)],
                       check=True, capture_output=True)

    for name in (".venv312", os.path.join("tools", "ffmpeg-8.0-full_build")):
        src, dst = project / name, home / name
        if src.is_dir() and not dst.exists():
            print(f"คัดลอก {name}")
            shutil.copytree(src, dst, symlinks=True)
    for name in PRIVATE_ROOT_FILES:
        src, dst = project / name, home / name
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)

    print("ติดตั้งไฟล์โปรแกรมจาก GitHub Release ...")
    version = launcher.install_program(home, progress=print)

    if launcher_exe and launcher_exe.is_file():
        desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
        shutil.copy2(launcher_exe, desktop / launcher_exe.name)
        print(f"วาง {launcher_exe.name} บน Desktop แล้ว")
    print(f"เสร็จ: v{version} ที่ {home}")


if __name__ == "__main__":
    exe = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    migrate(Path(sys.argv[1]), exe)
