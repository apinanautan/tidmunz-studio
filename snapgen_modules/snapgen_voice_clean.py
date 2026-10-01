# -*- coding: utf-8 -*-
"""Lightweight local cleanup for dialogue extracted from generated video."""

from __future__ import annotations

import subprocess
from pathlib import Path


SUPPORTED_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


def clean_output_path(source: Path) -> Path:
    """Return a non-destructive output path beside *source*."""
    source = Path(source)
    stem = source.stem[:-6] if source.stem.lower().endswith("_voice") else source.stem
    candidate = source.with_name(f"{stem}_clean_voice.mp3")
    counter = 2
    while candidate.exists():
        candidate = source.with_name(f"{stem}_clean_voice_{counter}.mp3")
        counter += 1
    return candidate


def latest_audio(audio_dir: Path) -> Path | None:
    """Find the newest usable audio export without selecting our own output."""
    audio_dir = Path(audio_dir)
    if not audio_dir.is_dir():
        return None
    choices = [
        path
        for path in audio_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES
        and "_clean_voice" not in path.stem.lower()
    ]
    return max(choices, key=lambda path: path.stat().st_mtime, default=None)


def clean_voice(source: Path, ffmpeg: str | Path, output: Path | None = None) -> Path:
    """Reduce mixed noise and polish dialogue with built-in FFmpeg filters.

    This intentionally avoids heavyweight source-separation runtimes. It is a
    speech-focused cleanup pass, not a promise of perfect stem separation.
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"ไม่พบไฟล์เสียง: {source}")

    output = Path(output) if output else clean_output_path(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = (
        "highpass=f=75,"
        "lowpass=f=14000,"
        "afftdn=nf=-24:tn=1,"
        "acompressor=threshold=-20dB:ratio=3:attack=10:release=120:makeup=3,"
        "loudnorm=I=-16:TP=-1.5:LRA=7"
    )
    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-af",
        filters,
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "FFmpeg ไม่คืนไฟล์เสียง")[-1200:]
        raise RuntimeError(f"ล้างเสียงไม่สำเร็จ: {detail}")
    return output
