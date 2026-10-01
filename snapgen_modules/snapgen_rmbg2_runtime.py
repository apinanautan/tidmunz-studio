# -*- coding: utf-8 -*-
"""Portable RMBG-2.0 runtime shared by Hunyuan on every workstation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import venv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = PROJECT_ROOT / "snapgen_data" / "tools" / "rmbg2"
PYTHON = TOOL_DIR / ".venv" / "Scripts" / "python.exe"
MODEL_DIR = TOOL_DIR / "RMBG-2.0"
READY = TOOL_DIR / ".ready"
READY_VERSION = "rmbg2-cpu-v2"

def _runtime_works(python: Path, packages: str = "torch,torchvision,transformers,timm,kornia,PIL") -> bool:
    if not python.is_file():
        return False
    check = subprocess.run(
        [str(python), "-c", f"import {packages}"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return check.returncode == 0

def _run(command, progress):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [str(x) for x in command], cwd=str(TOOL_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    tail = []
    for line in process.stdout:
        text = line.strip()
        if text:
            tail.append(text)
            tail = tail[-20:]
            progress(text)
    code = process.wait()
    if code:
        raise RuntimeError("\n".join(tail[-8:]) or f"ตัวติดตั้งหยุดทำงาน (code {code})")

def ensure_runtime(progress=print):
    """Create CPU-compatible runtime and verified model; return paths."""
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    # Reuse complete TripoSplat runtime when present; avoid duplicate PyTorch
    # and RMBG weights on developer/workstations that already installed it.
    shared_tool = PROJECT_ROOT / "snapgen_data" / "tools" / "triposplat"
    shared_python = shared_tool / ".venv" / "Scripts" / "python.exe"
    shared_model_dir = shared_tool / "bg_models" / "RMBG-2.0"
    if _runtime_works(shared_python, "torch,torchvision,transformers,timm,PIL"):
        if not _runtime_works(shared_python):
            progress("[RMBG] กำลังติดตั้ง kornia ในระบบ 3D ที่มีอยู่")
            _run([shared_python, "-m", "pip", "install", "--disable-pip-version-check", "kornia"], progress)
        if _runtime_works(shared_python):
            downloader = PROJECT_ROOT / "snapgen_modules" / "snapgen_download_rmbg2.py"
            _run([shared_python, "-B", downloader, shared_model_dir / "model.safetensors"], progress)
            return shared_python, shared_model_dir

    if not PYTHON.is_file():
        progress("[RMBG] กำลังสร้างระบบแยกครั้งแรก")
        venv.EnvBuilder(with_pip=True, clear=False).create(TOOL_DIR / ".venv")
    if not PYTHON.is_file():
        raise RuntimeError("สร้าง Python สำหรับ RMBG 2.0 ไม่สำเร็จ")

    try:
        ready = READY.read_text(encoding="utf-8").strip() == READY_VERSION
    except OSError:
        ready = False
    if ready:
        ready = _runtime_works(PYTHON)
    if not ready:
        progress("[RMBG] กำลังติดตั้งระบบลบพื้นหลังครั้งแรก")
        _run([PYTHON, "-m", "pip", "install", "--disable-pip-version-check",
              "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"], progress)
        _run([PYTHON, "-m", "pip", "install", "--disable-pip-version-check",
              "transformers==4.44.2", "timm", "kornia", "safetensors", "Pillow"], progress)
        READY.write_text(READY_VERSION + "\n", encoding="utf-8")

    downloader = PROJECT_ROOT / "snapgen_modules" / "snapgen_download_rmbg2.py"
    model = MODEL_DIR / "model.safetensors"
    _run([PYTHON, "-B", downloader, model], progress)
    if not model.is_file():
        raise RuntimeError("ดาวน์โหลด RMBG 2.0 เสร็จแต่ยังไม่พบโมเดล")
    return PYTHON, MODEL_DIR
