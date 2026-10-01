"""Build the small desktop launcher ``Tidmunz Studio.exe`` (one file)."""
from __future__ import annotations

import shutil
from pathlib import Path

import PyInstaller.__main__ as pyinstaller

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "tools" / "release"
WORK = RELEASE / "launcher_build"
OUTPUT = RELEASE / "Tidmunz Studio.exe"


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    pyinstaller.run([
        str(ROOT / "tools" / "launcher" / "tidmunz_launcher.py"),
        "--name", "Tidmunz Studio",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", str(ROOT / "assets" / "tidmun_studio_icon_final.ico"),
        "--distpath", str(WORK / "dist"),
        "--workpath", str(WORK / "work"),
        "--specpath", str(WORK),
    ])
    RELEASE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WORK / "dist" / "Tidmunz Studio.exe", OUTPUT)
    shutil.rmtree(WORK, ignore_errors=True)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
