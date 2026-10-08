# -*- coding: utf-8 -*-
"""Speech-to-text on AMD / Intel GPUs through whisper.cpp + Vulkan (Windows).

faster-whisper (CTranslate2) only runs on NVIDIA CUDA, so a Radeon or Intel
Arc machine would fall back to the slow CPU.  This module downloads a
whisper.cpp build made by this repository's "Whisper Vulkan" workflow, the
same large-v3 model in GGML format and the Silero VAD model, then runs
whisper-cli and returns faster-whisper-shaped segments.

Everything lives in snapgen_data/tools/whisper_vulkan.  Downloads go to a
.part file first and only complete files are kept.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

WHISPER_TAG = "whisper-vulkan-v1.9.5"
BUILD_URL = (
    "https://github.com/apinanautan/tidmunz-studio/releases/download/"
    f"{WHISPER_TAG}/whisper-vulkan-win-x64.zip"
)
# Largest Whisper model only (same as faster-whisper large-v3 on NVIDIA).
MODEL_NAME = "ggml-large-v3.bin"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"
MODEL_MIN_BYTES = 3_000_000_000
VAD_NAME = "ggml-silero-v5.1.2.bin"
VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = PROJECT_ROOT / "snapgen_data" / "tools" / "whisper_vulkan"


def has_nvidia_gpu() -> bool:
    """NVIDIA driver present on this machine (checked without faster-whisper,
    which may not be installed yet on a fresh teammate machine)."""
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    if shutil.which("nvidia-smi") or (system32 / "nvidia-smi.exe").is_file():
        return True
    try:
        import snapgen_voice_input as V
        return bool(V._nvidia_gpu_count())
    except Exception:
        return False


def wanted() -> bool:
    """Choose per machine: NVIDIA keeps faster-whisper on CUDA; Windows
    machines with AMD / Intel (or no) GPU use whisper.cpp + Vulkan."""
    if os.name != "nt" or os.environ.get("SNAPGEN_NO_VULKAN_WHISPER"):
        return False
    return not has_nvidia_gpu()


def _download(url, target: Path, log, label, min_bytes=1):
    if target.is_file() and target.stat().st_size >= min_bytes:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Tidmunz-Studio"})
    with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        next_report = 0
        while True:
            chunk = response.read(4 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total and done >= next_report:
                log(f"กำลังดาวน์โหลด {label} {done * 100 // total}% ({done // 1048576}/{total // 1048576} MB)")
                next_report = done + total // 10
    size = part.stat().st_size
    if (total and size != total) or size < min_bytes:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"ดาวน์โหลด {label} ไม่ครบ ({size} bytes)")
    part.replace(target)
    return target


def ensure(log=print):
    """Return (whisper-cli.exe, model, vad model), downloading what is missing."""
    exe = TOOL_DIR / "bin" / "whisper-cli.exe"
    if not exe.is_file():
        log("ดาวน์โหลดตัวถอดเสียงสำหรับ GPU AMD/Intel (Vulkan) ครั้งแรกครั้งเดียว ...")
        archive = _download(BUILD_URL, TOOL_DIR / "whisper-vulkan-win-x64.zip", log, "ตัวถอดเสียง Vulkan")
        staging = TOOL_DIR / "bin.new"
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(staging)
        if not (staging / "whisper-cli.exe").is_file():
            raise RuntimeError("ไฟล์ตัวถอดเสียง Vulkan ไม่มี whisper-cli.exe")
        shutil.rmtree(TOOL_DIR / "bin", ignore_errors=True)
        staging.replace(TOOL_DIR / "bin")
        archive.unlink(missing_ok=True)
    model = TOOL_DIR / MODEL_NAME
    if not (model.is_file() and model.stat().st_size >= MODEL_MIN_BYTES):
        log("ดาวน์โหลดโมเดลเสียงตัวใหญ่สุด large-v3 (ประมาณ 3 GB ครั้งแรกครั้งเดียว) ...")
        _download(MODEL_URL, model, log, "โมเดล large-v3", MODEL_MIN_BYTES)
    vad = _download(VAD_URL, TOOL_DIR / VAD_NAME, log, "โมเดลตัดช่วงเงียบ", 100_000)
    return exe, model, vad


def _ffmpeg():
    try:
        from ai_slow2x import _ffmpeg_bin, ensure_ffmpeg_tool
        found = Path(str(_ffmpeg_bin()))
        if found.is_file():
            return str(found)
        installed = ensure_ffmpeg_tool(lambda _m: None)
        if installed and Path(str(installed)).is_file():
            return str(installed)
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def _words(segment):
    """[start, end, text] per token; whisper.cpp already joins split UTF-8 pieces."""
    words = []
    for token in segment.get("tokens") or []:
        text = str(token.get("text") or "")
        offsets = token.get("offsets") or {}
        if not text.strip() or text.startswith("[_") or "�" in text:
            continue
        if "from" not in offsets or "to" not in offsets:
            continue
        words.append([round(offsets["from"] / 1000, 2), round(offsets["to"] / 1000, 2), text])
    return words


def transcribe(audio, initial_prompt="", words=False, log=print):
    """Return faster-whisper-shaped segments [{start, end, text, words}]."""
    exe, model, vad = ensure(log)
    work = Path(tempfile.mkdtemp(prefix="snapgen_whisper_"))
    try:
        wav = work / "audio.wav"
        convert = subprocess.run(
            [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, creationflags=NO_WINDOW,
        )
        if convert.returncode != 0 or not wav.is_file():
            raise RuntimeError("แปลงเสียงเป็น WAV ไม่สำเร็จ")
        threads = max(1, min(8, (os.cpu_count() or 4) - 1))
        command = [str(exe), "-m", str(model), "-f", str(wav), "-l", "th", "-t", str(threads),
                   "-ojf", "-of", str(work / "result"), "-np",
                   "--vad", "-vm", str(vad)]
        if initial_prompt:
            command += ["--prompt", initial_prompt]
        log("กำลังถอดเสียงด้วย GPU (Vulkan) — whisper large-v3 ...")
        result = subprocess.run(command, capture_output=True, creationflags=NO_WINDOW, cwd=str(exe.parent))
        stderr = result.stderr.decode("utf-8", errors="replace")
        device = next((line.strip() for line in stderr.splitlines() if "ggml_vulkan: 0 =" in line), "")
        if device:
            log("ใช้การ์ดจอ: " + device.split("=", 1)[1].split("|")[0].strip())
        output = work / "result.json"
        if result.returncode != 0 or not output.is_file():
            tail = [line for line in stderr.strip().splitlines() if line.strip()][-1:] or [f"exit {result.returncode}"]
            raise RuntimeError("whisper.cpp ทำงานไม่สำเร็จ: " + tail[0])
        data = json.loads(output.read_bytes().decode("utf-8", errors="replace"), strict=False)
        segments = []
        for segment in data.get("transcription") or []:
            offsets = segment.get("offsets") or {}
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            segments.append({
                "start": offsets.get("from", 0) / 1000,
                "end": offsets.get("to", 0) / 1000,
                "text": text,
                "words": _words(segment) if words else [],
            })
        return segments
    finally:
        shutil.rmtree(work, ignore_errors=True)
