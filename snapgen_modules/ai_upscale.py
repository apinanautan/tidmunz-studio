# -*- coding: utf-8 -*-
"""AI video upscale using FFmpeg scale + unsharp (no AI). Replaces waifu2x/Real-ESRGAN."""
import os, re, shutil, subprocess, sys, tempfile, threading, time, zipfile
from pathlib import Path
_tool_install_lock = threading.Lock()

def _video_ok(path):
    try: return Path(path).is_file() and Path(path).stat().st_size > 0
    except Exception: return False

def _ffmpeg_bin():
    root = Path(__file__).resolve().parent.parent
    for c in [root/"snapgen_data"/"tools"/"ffmpeg"/"ffmpeg.exe", root/"tools"/"ffmpeg"/"ffmpeg.exe", root/"ffmpeg.exe"]:
        try:
            if c.is_file(): return str(c)
        except: pass
    f = shutil.which("ffmpeg")
    return f if f else "ffmpeg"

def _waifu2x_install_complete(folder): return True
def ensure_waifu2x_tool(log=None): return "ffmpeg"

def _probe_video(ffmpeg, path):
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    vt = (r.stderr or "") + (r.stdout or "")
    vl = next((l for l in vt.splitlines() if "Video:" in l), "")
    m = re.search(r"[^\d](\d{2,5})x(\d{2,5})[^\d]", vl)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    m2 = re.search(r"[,\s](\d+(?:\.\d+)?)\s+fps[,\s]", vl)
    fps = float(m2.group(1)) if m2 else 24.0
    if fps <= 0 or fps > 240: fps = 24.0
    return w, h, fps, any("Audio:" in l for l in vt.splitlines())

def _detect_encoder(ffmpeg):
    """Always use CPU libx264 for upscale — reliable on every machine."""
    return "libx264", "CPU"

def upscale_video_ai(input_video, output_video=None, target_height=720, log=None, **_kwargs):
    """Resize+sharpen using FFmpeg lanczos + unsharp 25%. No AI, fast, no GPU needed."""
    def say(m):
        if callable(log):
            try: log(m)
            except: pass
    inp = Path(input_video)
    if not _video_ok(inp):
        say(f"[upscale] Video not found: {input_video}")
        return str(input_video)
    if output_video is None:
        output_video = str(inp.with_name(inp.stem + f"_sh{target_height}p" + inp.suffix))
    out = Path(output_video)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg = _ffmpeg_bin()
        w, h, fps, has_audio = _probe_video(ffmpeg, inp)
        if w == 0 or h == 0: raise RuntimeError("bad resolution")
        _enc, _enc_label = _detect_encoder(ffmpeg)
        say(f"[upscale] FFmpeg ({ffmpeg}) | {w}x{h} -> {target_height}p + unsharp 25% [{_enc_label}]")
        vf = f"scale=-2:{target_height}:flags=lanczos,unsharp=lx=5:ly=5:la=0.25:cx=5:cy=5:ca=0.0"
        say("[upscale] Running FFmpeg scale + unsharp...")
        cmd = [ffmpeg, "-y", "-i", str(inp), "-vf", vf]
        if has_audio: cmd += ["-c:a", "aac", "-b:a", "192k"]
        else: cmd += ["-an"]
        if _enc == "libx264":
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        else:
            cmd += ["-c:v", _enc, "-b:v", "8M"]
        cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace")
        if r.returncode: raise RuntimeError(r.stderr[-500:] if r.stderr else "ffmpeg error")
        if _video_ok(out):
            say(f"[upscale] Resize+sharpen done: {out.name}")
            return str(out)
        raise RuntimeError("output missing")
    except Exception as e:
        say(f"[upscale] FFmpeg failed - using original: {e}")
        return str(inp)