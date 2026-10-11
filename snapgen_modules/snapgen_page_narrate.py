# -*- coding: utf-8 -*-
"""เล่าภาพ — script + narration audio → finished picture video, one button.

Drop a story file (.docx/.txt) and its narration audio, press เริ่ม.  The
pipeline runs in order and every stage is saved, so stopping, closing the
program or a failure can always continue with the same button:

1. วิเคราะห์บท    GPT builds a Context (characters, places, era, style) in this
                  story's own GPT history.  A team Context next to the script
                  (<script>.tidmunz-context.json) is reused instead.
2. ฟังเสียง       local Whisper turns the narration into timestamped sentences.
3. รูปตัวละคร    one reference image per character (refs/<name>.png).  Drop
                  your own file with the same name to replace it.
4. วางแผนฉาก     GPT picks scenes ~3 minutes at a time from the timestamped
                  sentences, naming the characters in every scene.
5. สร้างรูปฉาก    one image per scene with the references of its characters.
6. ตัดต่อ        FFmpeg: slow zoom/pan per image, crossfades, narration audio,
                  optional subtitles → MP4.

Everything lives in EXPORT_ROOT/เล่าภาพ/<script name>/.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right")
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
FPS = 25
CROSSFADE = 0.5
PLAN_WINDOW = 180.0  # seconds of narration planned per GPT request
IMAGE_COUNTS = ("36", "49", "64", "81")  # full storyboard grids (6x6 ... 9x9); ChatGPT allows ~120 images a day
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Heavy work (FFmpeg, Whisper) runs below normal priority so the PC stays usable.
LOW_PRIORITY = NO_WINDOW | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_ENCODER_CACHE: dict = {}

# GPU encoders first (NVIDIA, AMD, Intel); CPU x264 limited to 2 threads last.
_ENCODERS = (
    ("NVIDIA GPU", ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]),
    ("AMD GPU", ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20"]),
    ("Intel GPU", ["-c:v", "h264_qsv", "-global_quality", "20"]),
)
_CPU_ENCODER = ("CPU (2 threads)", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2"])


def video_encoder(ffmpeg: str) -> tuple:
    """(label, args) for the fastest encoder that really works on this PC."""
    if ffmpeg in _ENCODER_CACHE:
        return _ENCODER_CACHE[ffmpeg]
    chosen = _CPU_ENCODER
    for label, args in _ENCODERS:
        try:
            probe = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.3",
                 *args, "-pix_fmt", "yuv420p", "-f", "null", "-"],
                capture_output=True, timeout=30, creationflags=NO_WINDOW)
            if probe.returncode == 0:
                chosen = (label, args)
                break
        except (OSError, subprocess.SubprocessError):
            continue
    _ENCODER_CACHE[ffmpeg] = chosen
    return chosen


_TRANSCRIBE_CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
def emit(item):
    sys.stdout.write(json.dumps(item, ensure_ascii=False) + "\n"); sys.stdout.flush()
import snapgen_voice_input as V
sent = [0]
samples = []
log = lambda m: emit({"log": str(m)})
try:
    import snapgen_whisper_vulkan as WV
except Exception:
    WV = None
vulkan_tried = [False]
def try_vulkan():
    # whisper.cpp + Vulkan: any GPU (AMD, Intel, NVIDIA without CUDA libs).
    if WV is None or not WV.usable() or vulkan_tried[0]:
        return False
    vulkan_tried[0] = True
    try:
        found = WV.transcribe(sys.argv[2], sys.argv[3], sys.argv[4] == "1", log=log)
    except Exception as exc:
        log(f"ถอดเสียงด้วยการ์ดจอ (Vulkan) ไม่ได้ ({exc})")
        return False
    emit({"backend": "GPU Vulkan"})
    for item in found:
        emit(item)
    return True
def run(force_cpu, compute_types=None, gpu_only=False):
    model, backend = V._get_whisper_model(log_fn=log, force_cpu=force_cpu,
                                          compute_types=compute_types, gpu_only=gpu_only)
    emit({"backend": backend})
    if not samples:
        samples.append(V.load_audio(sys.argv[2]))  # FFmpeg, not PyAV
    segments, _info = model.transcribe(samples[0], language="th", vad_filter=True, beam_size=1, temperature=0.0,
                                       initial_prompt=sys.argv[3] or None, word_timestamps=sys.argv[4] == "1")
    for s in segments:
        words = [[round(w.start, 2), round(w.end, 2), w.word] for w in (s.words or [])]
        emit({"start": s.start, "end": s.end, "text": s.text, "words": words})
        sent[0] += 1
# Order on every machine: NVIDIA CUDA -> (int8 on CUDA) -> Vulkan GPU -> CPU.
vram = V._nvidia_vram_mb()
if 0 < vram < 6000:
    # large-v3 does not fit a 4 GB laptop card: Windows spills it into shared
    # system memory and the GPU becomes many times slower than the CPU.
    log(f"การ์ดจอมีหน่วยความจำ {vram} MB ไม่พอสำหรับโมเดลตัวใหญ่ — ใช้ CPU ซึ่งเร็วกว่า")
    run(True)
    sys.exit(0)
if WV is not None and WV.wanted() and try_vulkan():
    sys.exit(0)  # AMD / Intel: Vulkan first
done = False
try:
    run(False, gpu_only=True)
    done = True
except Exception as exc:
    if sent[0]:
        raise
    if str(V._WHISPER_BACKEND or "").startswith("GPU") and V._WHISPER_BACKEND != "GPU int8":
        # Opened on CUDA but failed while transcribing (missing cuDNN, out of memory).
        log(f"GPU ถอดเสียงไม่ได้ ({exc}) — ลองใหม่บน GPU แบบประหยัดหน่วยความจำ (int8)")
        try:
            run(False, ("int8",), gpu_only=True)
            done = True
        except Exception as again:
            if sent[0]:
                raise
            exc = again
    if not done:
        log(f"CUDA ใช้ไม่ได้ ({exc}) — ลองถอดเสียงด้วยการ์ดจอแบบ Vulkan")
if not done and not try_vulkan():
    log("ใช้การ์ดจอไม่ได้ — ถอดเสียงด้วย CPU")
    run(True)
"""


def transcribe_in_background(audio: str, initial_prompt: str = "", should_stop=lambda: False, words: bool = False):
    """Yield Whisper results from a separate below-normal-priority process.

    ``words`` adds Whisper word times (Slot 2 ออโต้ cuts dialogue with them); เล่าภาพ does not need them.
    """
    import sys
    python = sys.executable
    console_python = Path(python).with_name("python.exe")
    if console_python.is_file():
        python = str(console_python)  # pythonw has no usable stdout pipe on some PCs
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [python, "-B", "-c", _TRANSCRIBE_CHILD, str(Path(__file__).resolve().parent), str(audio), initial_prompt, "1" if words else "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=LOW_PRIORITY)
    try:
        for raw in proc.stdout:
            if should_stop():
                proc.kill()
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("{"):
                yield json.loads(line)
        code = proc.wait()
        if code != 0:
            error = proc.stderr.read().decode("utf-8", errors="replace").strip().splitlines()
            raise RuntimeError("Whisper ทำงานไม่สำเร็จ: " + (error[-1] if error else f"exit {code}"))
    finally:
        if proc.poll() is None:
            proc.kill()
# Scenes are planned before character images: the plan decides which
# characters actually appear, so only those get a reference image.
STAGES = (
    ("context", "วิเคราะห์บท", 5),
    ("transcribe", "ฟังเสียง", 10),
    ("plan", "วางแผนฉาก", 10),
    ("characters", "รูปตัวละคร", 10),
    ("storyboard", "สตอรี่ชีต", 5),
    ("images", "สร้างรูปฉาก", 45),
    ("video", "ตัดต่อ", 15),
)
# Slot 2 auto mode: the same pipeline, plus one AI video clip per scene.
VIDEO_STAGES = (
    ("context", "วิเคราะห์บท", 4),
    ("transcribe", "ฟังเสียง", 6),
    ("plan", "วางแผนฉาก", 5),
    ("storyboard", "สตอรี่ชีต", 5),
    ("images", "สร้างรูปฉาก", 20),
    ("motion", "พรอมต์วิดีโอ", 5),
    ("clips", "สร้างคลิปวิดีโอ", 45),
    ("video", "ตัดต่อ", 10),
)
# Every plan, storyboard and fix: the picture shows only what the script says at that moment.
SCOPE_RULE = ("ขอบเขต (ห้ามออกนอกบท): ภาพต้องแสดงเฉพาะสิ่งที่คำบรรยาย/บทช่วงนั้นพูดถึง — ตัวละคร สถานที่ เวลา "
              "เหตุการณ์ และสิ่งของตามบทเท่านั้น ห้ามแต่งเหตุการณ์ คน สัตว์ สิ่งของ หรือสถานที่ที่บทไม่ได้กล่าวถึง "
              "ห้ามข้ามไปเล่าเหตุการณ์ของช่วงอื่นของเรื่อง ถ้าบทไม่ได้บอกรายละเอียด ให้เลือกแบบเรียบง่ายที่สอดคล้องกับบท. ")
# ออโต้ writes each clip's video prompt from this many finished pictures per GPT look.
MOTION_BATCH = 4
# Slot 2 ออโต้ makes a film: pictures must look like live-action footage, never drawn.
REALISM_NOTE = ("ภาพนิ่งจากภาพยนตร์ไลฟ์แอ็กชันสมจริง (photorealistic live-action film still) ถ่ายด้วยกล้องภาพยนตร์ "
                "นักแสดงจริง ผิว ผ้า น้ำ หิน และพื้นผิวสมจริง แสงและเงาแบบหนังจริง ระยะชัดตื้นแบบเลนส์ภาพยนตร์; "
                "สัตว์หรือสิ่งมีชีวิตในตำนาน (เช่น พญานาค) ทำเป็น CGI สมจริงระดับหนังฟอร์มยักษ์ มีเกล็ด น้ำหนัก และแสงสะท้อนจริง; "
                "ห้ามเป็นการ์ตูน อนิเมะ ภาพวาด ภาพประกอบ หรือ 3D เรนเดอร์แบบเกม")
DEFAULT_STYLE = "ภาพสมจริงแบบภาพยนตร์ แสงธรรมชาติ รายละเอียดสูง ไม่มีตัวหนังสือหรือคำบรรยายในภาพ"
# Picture styles the user can pick per story. "still" = no zoom/pan in the video.
STYLES = {
    "ปกติ": {"prompt": "", "still": False},
    "เรื่องผี": {
        "prompt": ("ภาพถ่ายสมจริงแบบภาพยนตร์สยองขวัญไทย โทนสีมืด หม่น อึมครึม สีซีดอมเขียวเทา แสงน้อย เงาเข้ม "
                   "บรรยากาศน่ากลัววังเวง มีหมอกหรือความมืดรอบภาพ ไม่มีตัวหนังสือหรือคำบรรยายในภาพ"),
        "still": True,
    },
}

# Thai ghosts GPT often does not know. Looks follow common Thai folklore and
# are phrased for film (no gore words) so image models do not refuse.
THAI_GHOSTS = (
    (("กระสือ", "krasue"), "ผีกระสือ: ศีรษะหญิงสูงวัยหน้าซีดลอยอยู่กลางอากาศตอนกลางคืน ไม่มีร่างกาย ผมยาวยุ่งสยาย ตาเรืองแสง "
     "ใต้คอมีสายยาวโปร่งแสงสีแดงอมเขียวเรืองแสงห้อยระย้าลงมาแบบพร็อพเอฟเฟกต์ภาพยนตร์สยองขวัญ มีแสงสีเขียวเรืองรอบศีรษะ"),
    (("กระหัง",), "ผีกระหัง: ชายร่างผอมลอยในความมืด ใช้กระด้งสองใบเป็นปีกที่แขน สากตำข้าวสอดไว้เป็นหาง ตาวาว"),
    (("ปอบ",), "ผีปอบ: คนที่ถูกผีปอบสิง หน้าซีดเผือด ตาแดงก่ำ ผมยุ่งเหยิง ท่าทางหิวโหยแบบน่ากลัว ในหมู่บ้านอีสานยามค่ำ"),
    (("เปรต",), "เปรต: ร่างสูงมากผอมเหลือแต่กระดูก ผิวคล้ำแห้ง ปากเล็กเท่ารูเข็ม คอยาว ยืนในวัดยามค่ำคืน"),
    (("แม่นาก", "นางนาก", "ผีตายท้องกลม", "ตายทั้งกลม"), "ผีหญิงตายทั้งกลม: หญิงสาวชุดไทยโบราณ ผมยาวดำปิดหน้า ผิวซีดขาว "
     "สายตาเศร้าและน่ากลัว แขนยาวผิดปกติได้"),
    (("ตานี", "นางตานี"), "นางตานี: หญิงสาวสวยชุดไทยโบราณสีเขียวอ่อน ผิวซีด ปรากฏใต้ต้นกล้วยตานียามค่ำ มีแสงจันทร์"),
    (("ตะเคียน", "นางตะเคียน"), "นางตะเคียน: หญิงสาวชุดไทยโบราณ ผมยาว สิงอยู่ที่ต้นตะเคียนใหญ่ที่ผูกผ้าสีหลายสี"),
    (("ผีพราย", "พราย"), "ผีพราย: หญิงสาวผีน้ำ ผมยาวเปียกน้ำ ผิวซีดอมฟ้า โผล่จากน้ำหรือริมน้ำยามค่ำ"),
    (("ผีโพง", "โพง"), "ผีโพง: ชายผอมแห้ง มีแสงเรืองสีเขียวออกจากจมูก เดินตามทุ่งนายามค่ำ"),
    (("กองกอย",), "ผีกองกอย: ผีป่าตัวเล็กผอม เดินกระโดดขาเดียว ผมยุ่ง อยู่ในป่าลึก"),
    (("ผีหัวขาด", "หัวขาด"), "ผีหัวขาด: ร่างชายในชุดทหารโบราณ ถือศีรษะของตัวเองไว้ในมือ ไม่มีศีรษะบนบ่า (เอฟเฟกต์ภาพยนตร์ ไม่เห็นบาดแผล)"),
    (("ผีปู่โสม", "ปู่โสม"), "ผีปู่โสม: ชายชราผอม เคราขาวยาว เฝ้าไหสมบัติในถ้ำหรือใต้ดิน"),
    (("ผีเสื้อสมุทร",), "ผีเสื้อสมุทร: ยักษินีร่างใหญ่ในทะเล ผิวเขียวคล้ำ เขี้ยวยาว ผมยาวรุงรัง"),
)


def find_ghosts(text: str, extra=()) -> list:
    """[(name, look)] for every ghost named in text; extra = GPT research entries."""
    text = str(text or "")
    found, seen = [], set()
    for aliases, look in THAI_GHOSTS:
        if any(a in text for a in aliases) and aliases[0] not in seen:
            seen.add(aliases[0])
            found.append((aliases[0], look))
    for entry in extra or ():
        names = [entry.get("name", "")] + list(entry.get("aliases") or [])
        if entry.get("look") and any(n and n in text for n in names) and names[0] not in seen:
            seen.add(names[0])
            found.append((names[0], entry["look"]))
    return found


# Mythical bodies video models get wrong (a naga grows hands, crouches on legs).
# (aliases, body facts, what must never appear). Skipped when the shot says the
# being is in human form.
CREATURE_BODIES = (
    (("พญานาค", "นาคราช", "นาคี", "นาคิน", "นาคา", "naga"),
     "พญานาคเป็นงูยักษ์ ลำตัวยาวมีเกล็ด มีหงอนบนหัว ไม่มีแขน ไม่มีขา ไม่มีมือ ไม่มีเท้า; เคลื่อนที่ด้วยการเลื้อยและขดตัว "
     "สู้ด้วยการฉกด้วยเขี้ยว รัดด้วยลำตัว ฟาดหาง หรือพ่นพลัง; ท่าหมอบ/ยอมแพ้ = ขดตัวลดหัวลงแนบพื้น",
     "มือหรือแขนบนตัวพญานาค, ขาหรือเท้าบนตัวพญานาค, พญานาคถืออาวุธ"),
    (("มังกร", "dragon"),
     "มังกรมีลำตัวยาวมีเกล็ด มีขา 4 ขาพร้อมกรงเล็บ (ต่างจากพญานาคที่ไม่มีขา)",
     ""),
)
HUMAN_FORM = re.compile(r"ร่างมนุษย์|ร่างคน|แปลงร่างเป็น(?:คน|มนุษย์|หญิง|ชาย)|ในร่างของ(?:คน|มนุษย์|หญิง|ชาย)")


def creature_bodies(text: str) -> tuple[str, str]:
    """(body facts, forbidden parts) for the mythical beings named in one shot's text."""
    text = str(text or "")
    if HUMAN_FORM.search(text):
        return "", ""
    lower = text.lower()
    found = [(body, forbid) for aliases, body, forbid in CREATURE_BODIES if any(a in lower for a in aliases)]
    return "; ".join(b for b, _f in found), ", ".join(f for _b, f in found if f)


def merge_forbid(*lists) -> str:
    """Comma lists joined without repeats, in order."""
    words = [w.strip() for text in lists for w in str(text or "").split(",")]
    return ", ".join(dict.fromkeys(w for w in words if w))


GHOST_HINT = re.compile(r"ผี|วิญญาณ|ปีศาจ|อมนุษย์|กระสือ|กระหัง|ปอบ|เปรต|พราย|โพง|ตานี|ตะเคียน|ซอมบี้|ยักษ์")


def ghost_research_request(script: str) -> str:
    return (
        "ในบทนี้มีผี วิญญาณ อมนุษย์ หรือสิ่งเหนือธรรมชาติตามความเชื่อไทยตัวไหนบ้าง (เฉพาะที่มีอยู่ในบทจริง)?\n"
        "สำคัญ: ก่อนตอบ ให้ค้นข้อมูลจากอินเทอร์เน็ตเกี่ยวกับคติชน/ตำนานผีไทยของแต่ละตัว อย่าเดา "
        "แล้วบรรยายรูปลักษณ์ภายนอกตามความเชื่อไทยให้ละเอียดพอสำหรับวาดภาพ (รูปร่าง หน้า ผม เสื้อผ้า แสง สถานที่ที่มักปรากฏ) "
        "ผีไทยหลายตัวในตำนานมีเลือด ไส้ หรือบาดแผล — ให้คงความน่ากลัวไว้แต่แปลงเป็นคำเอฟเฟกต์/พร็อพภาพยนตร์ที่สร้างภาพได้ "
        "เช่น ไส้ → สายโปร่งแสงสีแดงอมเขียวเรืองแสง, เลือด → คราบสีแดงเข้มแบบเมคอัพภาพยนตร์ ห้ามใช้คำว่า เลือด ไส้ อวัยวะ บาดแผล.\n"
        "ตอบ JSON เท่านั้น: {\"ghosts\":[{\"name\":\"ชื่อที่ใช้ในบท\",\"aliases\":[\"ชื่ออื่น\"],\"look\":\"ลักษณะสำหรับวาด\",\"source\":\"แหล่งที่ค้น\"}]}"
        " ถ้าไม่มีให้ตอบ {\"ghosts\":[]}\n\nบท:\n" + script[:12000]
    )


def looks_like_reference_sheet(path) -> bool:
    """True when a scene image came back as a plain studio reference (flat gray border all round)."""
    try:
        from PIL import Image, ImageStat
        image = Image.open(path).convert("RGB").resize((96, 96))
    except Exception:
        return False
    w, h = image.size
    # Top edge and the upper two thirds of both sides: a reference's body touches the bottom.
    border = [image.getpixel((x, y)) for x in range(w) for y in (0, 1)] + \
             [image.getpixel((x, y)) for y in range(h * 2 // 3) for x in (0, 1, w - 2, w - 1)]
    lum = [sum(p) / 3 for p in border]
    mean = sum(lum) / len(lum)
    spread = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
    sat = sum(max(p) - min(p) for p in border) / len(border)
    # Measured: references spread <= 8.5, saturation <= 16.5; real scenes spread >= 20.
    return spread < 11 and 60 <= mean <= 235 and sat < 22


def local_image_problems(scenes, aspect) -> dict:
    """Scene pictures that are surely broken: wrong shape (a leftover collage) or a copy of another scene."""
    import hashlib
    try:
        from PIL import Image
    except Exception:
        return {}
    w, h = SIZES.get(aspect, SIZES["16:9"])
    want = w / h
    problems, seen = {}, {}
    for i, scene in enumerate(scenes):
        path = scene.get("image")
        if not (path and os.path.isfile(path)):
            continue
        try:
            with Image.open(path) as image:
                ratio = image.width / image.height
        except Exception:
            problems[i] = "เปิดรูปไม่ได้"
            continue
        digest = hashlib.md5(Path(path).read_bytes()).hexdigest()
        if abs(ratio - want) / want > 0.12:
            problems[i] = "สัดส่วนรูปผิด (น่าจะเป็นภาพหลายช่อง/รูปค้างจากฉากอื่น)"
        elif digest in seen:
            problems[i] = f"รูปซ้ำกับฉาก {seen[digest] + 1}"
        seen.setdefault(digest, i)
    return problems


def error_reason(error: str) -> str:
    """Short Thai label + GPT's own words for why a scene picture was not made."""
    text = str(error or "")
    for marker in ("GPT said:", "blocked by safety policy:"):
        if marker in text:
            return "ผิดกฎ/GPT ไม่ยอมวาด — " + text.split(marker, 1)[1].strip()[:200]
    if "rate limit" in text.lower() or "ถึงลิมิต" in text:
        return "โควตารูปหมด"
    if "รูปเก่าซ้ำ" in text:
        return "GPT ไม่วาดรูปใหม่ (ส่งรูปเก่ากลับมา) — ลองกด GPT ช่วยแก้ prompt"
    return text[:160]


def save_storyboard(scenes, out_path, tile_width=480) -> str:
    """All finished scene pictures in order on one image (square-ish grid: 35 scenes -> 6 x 6)."""
    import math
    from PIL import Image, ImageDraw
    paths = [(i, s.get("image")) for i, s in enumerate(scenes) if s.get("image") and os.path.isfile(s["image"])]
    if not paths:
        return ""
    with Image.open(paths[0][1]) as first:
        tile_height = round(tile_width * first.height / first.width)
    cols = math.ceil(math.sqrt(len(paths)))
    rows = math.ceil(len(paths) / cols)
    sheet = Image.new("RGB", (cols * tile_width, rows * tile_height), "black")
    draw = ImageDraw.Draw(sheet)
    for n, (i, path) in enumerate(paths):
        x, y = (n % cols) * tile_width, (n // cols) * tile_height
        with Image.open(path) as image:
            sheet.paste(image.convert("RGB").resize((tile_width, tile_height)), (x, y))
        draw.rectangle((x, y, x + 34, y + 18), fill="black")
        draw.text((x + 4, y + 3), f"{i + 1:02d}", fill="white")
    sheet.save(out_path, "JPEG", quality=90)
    return str(out_path)


def contact_sheet_data_url(scenes) -> str:
    """All scene pictures on one numbered sheet, as a JPEG data URL for one GPT check."""
    import io
    from PIL import Image, ImageDraw
    paths = [(i, s.get("image")) for i, s in enumerate(scenes) if s.get("image") and os.path.isfile(s["image"])]
    cols, tw, th = 7, 300, 169
    rows = max(1, (len(paths) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 22)), "white")
    draw = ImageDraw.Draw(sheet)
    for n, (i, path) in enumerate(paths):
        x, y = (n % cols) * tw, (n // cols) * (th + 22)
        with Image.open(path) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((tw, th))
        sheet.paste(thumb, (x, y + 22))
        draw.text((x + 4, y + 4), f"SCENE {i + 1}", fill="red")
    buffer = io.BytesIO()
    sheet.save(buffer, "JPEG", quality=75)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


IMAGE_CHECK_PROMPT = (
    "ภาพนี้รวมรูปฉากทั้งหมด {total} ฉากของวิดีโอเรื่องเล่าไทยไว้ในรูปเดียว แต่ละช่องมีเลขฉาก (SCENE n) ตรวจครบทุกช่องแล้วบอกฉากที่ใช้ไม่ได้: "
    "1) เป็นภาพหลายช่อง/คอลลาจ/ตารางในรูปเดียว 2) ฉาก บ้านเรือน หรือเครื่องแต่งกายไม่ใช่แบบไทย (เช่น จีน ญี่ปุ่น ตะวันตก){abroad} "
    "3) มีตัวหนังสือ/ลายน้ำ 4) ภาพเสียหรือว่างเปล่า "
    'ตอบ JSON เท่านั้น {{"bad":[{{"scene":n,"reason":"สั้นๆ"}}]}} ถ้าดีหมดตอบ {{"bad":[]}}')


class Stopped(Exception):
    pass


class HistoryLost(RuntimeError):
    """The story's GPT conversation no longer exists; never replaced silently."""


class RateLimited(RuntimeError):
    """ChatGPT's image quota is used up; stop instead of retrying."""


# ── pure helpers (unit-tested) ────────────────────────────────────────────

def safe_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", str(text or "")).strip()
    return re.sub(r"\s+", " ", cleaned)[:80] or "เรื่อง"


def fmt_time(seconds) -> str:
    seconds = max(0.0, float(seconds or 0))
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def read_script(path) -> str:
    path = Path(str(path))
    if path.suffix.lower() == ".docx":
        import xml.etree.ElementTree as ET
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
        paragraphs = ("".join(t.text or "" for t in p.iter(ns + "t")).strip() for p in root.iter(ns + "p"))
        return "\n".join(p for p in paragraphs if p)
    return path.read_text(encoding="utf-8-sig", errors="replace").strip()


def script_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(_squash(text).encode("utf-8")).hexdigest()


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def project_folder_for(base: Path, script: str, text: str) -> Path:
    """One folder (and one GPT history) per story.

    The same file, or the same script content moved elsewhere, reopens its
    project.  A different story whose file happens to have the same name
    gets "<name> (2)", "<name> (3)" ... instead of sharing a history.
    """
    stem = safe_name(Path(script).stem)
    digest = script_hash(text)
    for n in range(1, 100):
        folder = Path(base) / (stem if n == 1 else f"{stem} ({n})")
        try:
            existing = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return folder
        same_file = os.path.normcase(os.path.abspath(str(existing.get("script") or ""))) == \
            os.path.normcase(os.path.abspath(str(script)))
        if same_file or existing.get("script_hash") == digest:
            return folder
    raise RuntimeError("มีโปรเจกต์ชื่อซ้ำกันมากเกินไป")


def match_reference_files(text: str, folder) -> list:
    """Fallback matcher: images in ``folder`` whose file name appears in ``text``."""
    if not folder or not os.path.isdir(str(folder)):
        return []
    lowered = str(text or "").casefold()
    found = []
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if ext.lower() in (".png", ".jpg", ".jpeg", ".webp") and len(stem.strip()) >= 2 and stem.strip().casefold() in lowered:
            found.append((stem.strip(), os.path.join(folder, name)))
    found.sort(key=lambda item: -len(item[0]))
    return found


def plan_windows(segments: list, window: float = PLAN_WINDOW) -> list:
    """Group transcript segments into consecutive windows of about ``window`` s."""
    groups, current, start = [], [], None
    for seg in segments:
        if start is None:
            start = seg["start"]
        current.append(seg)
        if seg["end"] - start >= window:
            groups.append(current)
            current, start = [], None
    if current:
        groups.append(current)
    return groups


def snap_starts(raw_starts: list, segment_starts: list, window_start: float) -> list:
    """Snap GPT start times to real sentence starts, sorted and de-duplicated.

    The first scene of a window always begins at the window start so no part
    of the narration is left without a picture.
    """
    if not segment_starts:
        return [window_start]
    snapped = []
    for value in raw_starts:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        snapped.append(min(segment_starts, key=lambda s: abs(s - value)))
    snapped = sorted(set(snapped) | {window_start})
    return snapped


def script_audio_match(segments: list, script: str) -> float:
    """Share of the heard text's 3-letter pieces found in the script (same story ≈ 1.0, another story ≈ 0.3)."""
    def pieces(text):
        text = re.sub(r"[\s“”\"'.,!?…]+", "", text)
        return {text[i:i + 3] for i in range(len(text) - 2)}
    heard = pieces(" ".join(str(s.get("text") or "") for s in segments))
    return len(heard & pieces(script)) / len(heard) if heard else 1.0


def correct_with_script(segments: list, script: str) -> list:
    """Replace each Whisper sentence with the matching words from the script.

    Whisper mishears names and rare words; the script is what was actually
    read.  Each sentence is matched inside a window just after the previous
    match.  Weak matches keep Whisper's text, so narration that departs from
    the script is never replaced with the wrong passage.
    """
    from difflib import SequenceMatcher
    text = re.sub(r"\s+", " ", script)
    cursor, fixed = 0, []
    for seg in segments:
        heard = seg["text"].strip()
        size = len(heard)
        window = text[cursor:cursor + size * 3 + 120]
        blocks = [b for b in SequenceMatcher(None, heard, window, autojunk=False).get_matching_blocks() if b.size >= 2]
        matched = sum(b.size for b in blocks)
        if blocks and size and matched / size >= 0.6:
            start, end = blocks[0].b, blocks[-1].b + blocks[-1].size
            # Extend to the edge of the word on both sides (Thai text has no
            # spaces inside a phrase, so stop at the nearest space or bound).
            while start > 0 and window[start - 1] != " " and blocks[0].a > 0:
                start -= 1
                if blocks[0].b - start >= blocks[0].a:
                    break
            tail = size - (blocks[-1].a + blocks[-1].size)
            if tail > 0:
                # Unmatched heard text at the end: take script words only up to
                # the last space it covers, never half a word.
                space = window.rfind(" ", end, end + tail + 1)
                end = space if space > 0 else end
            # Finish the current word if the match stopped inside it.
            nxt = window.find(" ", end)
            if 0 <= nxt - end <= 6:
                end = nxt
            elif nxt < 0 and len(window) - end <= 6:
                end = len(window)
            replacement = window[start:end].strip()
            if replacement:
                fixed.append(dict(seg, text=replacement))
                cursor += end
                continue
        fixed.append(dict(seg))
    return fixed


def limit_scenes(scenes: list, target: int, duration: float) -> list:
    """Merge the shortest scenes into the previous one until ``target`` remain."""
    scenes = list(scenes)
    while len(scenes) > max(1, target):
        durs = segment_durations([s["start"] for s in scenes], duration)
        # Short punchy shots GPT marked as highlights stay; merge ordinary ones first.
        plain = [i for i in range(1, len(scenes)) if not scenes[i].get("highlight")] or list(range(1, len(scenes)))
        shortest = min(plain, key=lambda i: durs[i])
        del scenes[shortest]
    return scenes


VIDEO_AUTO_MODEL = "grok-lower"
VIDEO_CHEAP_MODEL = "vela-ai-video"
GROK_CLIP_SECONDS = (6, 10, 15)  # lengths grok-lower accepts; AI Slow 2x doubles a clip
VELA_CLIP_SECONDS = 5  # vela-ai-video only makes 5 s
VIDEO_AUTO_AVG_SECONDS = 8  # average shot length used to size the plan
SHOT_LIMIT = 12.0  # longer shots are split into continuing shots
CLIP_STRETCH = 1.15  # a clip may be slowed this much unnoticed to fill its shot
# Economy (Slow 2x on every shot): fewer, longer shots — one clip (grok 10 s + Slow 2x) covers up to 20 s.
ECONOMY_TARGET = 14.0
ECONOMY_LIMIT = 20.0
ECONOMY_NOTE = (". โหมดประหยัด: ทุกช็อตเล่นสโลว์ 2 เท่าและยาวประมาณ 12–20 วินาที — เขียนแต่ละช็อตเป็นภาพเดียวต่อเนื่องที่เล่าได้ทั้งช่วง "
                "(เช่น บรรยากาศ ตัวละครทำสิ่งหนึ่งอย่างช้าๆ กล้องเคลื่อนช้า) ภาพไม่ต้องตรงทุกประโยค ขอให้สอดคล้องกับเรื่องช่วงนั้น; "
                "ช็อตติดกันที่ยังอยู่ในเหตุการณ์/สถานที่เดิม ให้อยู่ฉากเดิมกับคนเดิมต่อเนื่องกัน ห้ามสลับฉากไปมา "
                "เปลี่ยนฉากเฉพาะเมื่อเรื่องย้ายสถานที่หรือเวลาจริง")


def pick_clip(length: float, slow_ok: bool, cheap_ok: bool = False, stretch: float = CLIP_STRETCH) -> tuple:
    """Cheapest (model, seconds, slow) whose finished clip covers ``length`` s of narration.

    vela (cheap, 5 s) first when the shot allows it, then grok-lower by
    requested seconds, plain before Slow 2x.
    """
    slows = (False, True) if slow_ok else (False,)
    options = [(VIDEO_CHEAP_MODEL, VELA_CLIP_SECONDS, slow) for slow in slows] if cheap_ok else []
    options += [(VIDEO_AUTO_MODEL, s, slow) for s in GROK_CLIP_SECONDS for slow in slows]
    for model, seconds, slow in options:
        if seconds * (2 if slow else 1) * stretch >= length - CROSSFADE:
            return model, seconds, slow
    return options[-1]


def clip_limit(scene: dict) -> float:
    """Longest shot one clip covers: 12 s, or 20 s (grok 10 s + Slow 2x) for slow shots."""
    return 20.0 if scene.get("slow") else SHOT_LIMIT


def assign_clips(scenes: list, duration: float, economy: bool = False) -> None:
    """Store each shot's model, clip length and Slow 2x choice from its narration length.

    ``economy``: every shot without a spoken line buys a short clip and plays it Slow 2x
    (about half the video credit), not only the shots GPT marked slow.
    """
    for scene, length in zip(scenes, segment_durations([s["start"] for s in scenes], duration)):
        if scene.get("clip_fallback"):  # vela already failed here: stay on grok-lower
            scene["cheap"] = False
        scene["shot_seconds"] = length
        spoken = bool(scene.get("dialogue"))  # dialogue: real speed, clip only trimmed, never slowed
        scene["clip_model"], scene["clip_seconds"], scene["clip_slow"] = pick_clip(
            length, (economy or bool(scene.get("slow"))) and not spoken, bool(scene.get("cheap")) and not spoken,
            1.0 if spoken else CLIP_STRETCH)


def clip_label(scene: dict) -> str:
    model = "vela" if scene.get("clip_model") == VIDEO_CHEAP_MODEL else "grok"
    return f"{model} {scene.get('clip_seconds', '?')}วิ" + (" สโลว์×2" if scene.get("clip_slow") else "")


def clip_summary(scenes: list) -> str:
    """'grok 10วิ = 4 คลิป, vela 5วิ สโลว์×2 = 3 คลิป' count of planned clips."""
    counts = {}
    for scene in scenes:
        counts[clip_label(scene)] = counts.get(clip_label(scene), 0) + 1
    return ", ".join(f"{label} = {n} คลิป" for label, n in sorted(counts.items(), key=lambda kv: kv[0]))


def split_long_scenes(scenes: list, duration: float, clip_seconds: float, max_factor: float = 1.6,
                      limit_for=None) -> list:
    """Video mode: a shot longer than ~1.6 clips becomes continuing sub-shots.

    A narration sentence can run 20 s while a clip lasts 6 s; one clip would
    otherwise be slowed and then frozen for most of that time. ``limit_for``
    gives a per-shot maximum instead (grok-lower: 15 s, 30 s with Slow 2x).
    """
    out = []
    durs = segment_durations([s["start"] for s in scenes], duration)
    for scene, length in zip(scenes, durs):
        parts = 1
        if limit_for is not None:
            limit = limit_for(scene)
            if length > limit:
                parts = int(-(-length // limit))
        elif clip_seconds > 0 and length > clip_seconds * max_factor:
            parts = int(-(-length // clip_seconds))
        for j in range(parts):
            piece = dict(scene, start=round(scene["start"] + j * length / parts, 2))
            if j:
                note = f" (ช็อตต่อเนื่อง {j + 1}/{parts} ของฉากเดียวกัน มุมกล้องต่างจากช็อตก่อน)"
                piece["prompt"] = scene["prompt"] + note
                piece["transition"] = "dissolve"
                piece["video_prompt"] = (scene.get("video_prompt") or "") + note
                piece["motion"] = MOTIONS[(MOTIONS.index(scene.get("motion", MOTIONS[0])) + j) % len(MOTIONS)] \
                    if scene.get("motion") in MOTIONS else MOTIONS[j % len(MOTIONS)]
            out.append(piece)
    return out


def segment_durations(starts: list, duration: float) -> list:
    ends = list(starts[1:]) + [duration]
    return [max(0.5, round(e - s, 3)) for s, e in zip(starts, ends)]


def clip_fit_filter(clip_seconds: float, target: float, width: int, height: int) -> str:
    """Fit one AI clip to its narration slot: trim if long, slow (≤1.6x) then hold if short."""
    factor = 1.0
    if clip_seconds > 0 and clip_seconds < target:
        factor = min(1.6, target / clip_seconds)
    hold = max(0.0, target - clip_seconds * factor)
    vf = (f"setpts={factor:.4f}*PTS,fps={FPS},"
          f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}")
    if hold > 0.01:
        vf += f",tpad=stop_mode=clone:stop_duration={hold + 0.2:.3f}"
    return vf + f",trim=duration={target:.3f},setpts=PTS-STARTPTS,format=yuv420p"


def zoompan_filter(motion: str, frames: int, width: int, height: int) -> str:
    frames = max(1, int(frames))
    progress = f"on/{frames}"
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "still":
        z, x, y = "1", centre_x, centre_y
    elif motion == "zoom_out":
        z, x, y = f"1.15-0.15*{progress}", centre_x, centre_y
    elif motion == "pan_left":
        z, x, y = "1.12", f"(iw-iw/zoom)*(1-{progress})", centre_y
    elif motion == "pan_right":
        z, x, y = "1.12", f"(iw-iw/zoom)*{progress}", centre_y
    else:
        z, x, y = f"1+0.15*{progress}", centre_x, centre_y
    big_w, big_h = width * 2, height * 2
    return (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={FPS},"
        "format=yuv420p"
    )


# How one shot gives way to the next: (FFmpeg xfade effect, seconds).
TRANSITIONS = {
    "dissolve": ("fade", 0.8),        # soft overlap: the same scene continues
    "fadeblack": ("fadeblack", 1.2),  # dip to black: new place, time of day, time jump
    "cut": ("fade", 0.12),            # near-cut: hits and fast action
}


def transition_into(previous: dict | None, scene: dict) -> str:
    """The transition into ``scene``: GPT's choice, else dip to black when the place changes."""
    chosen = str(scene.get("transition") or "").strip().lower()
    if chosen in TRANSITIONS:
        return chosen
    before, now = str((previous or {}).get("location") or "").strip(), str(scene.get("location") or "").strip()
    return "fadeblack" if before and now and before != now else "dissolve"


def xfade_graph(lengths: list, fade: float = CROSSFADE, kinds: list | None = None) -> tuple[str, float]:
    """filter_complex chaining inputs 0..n-1 with transitions; returns (graph, output length).

    ``kinds[i]`` is the transition into input i (``kinds[0]`` unused); each
    input's length must already include the overlap with the next input.
    """
    if len(lengths) == 1:
        return "[0:v]null[vout]", lengths[0]
    parts, label, elapsed = [], "[0:v]", lengths[0]
    for i in range(1, len(lengths)):
        out = "[vout]" if i == len(lengths) - 1 else f"[x{i}]"
        effect, seconds = TRANSITIONS.get((kinds or [])[i] if kinds and i < len(kinds) else "", ("fade", fade))
        offset = max(0.0, elapsed - seconds)
        parts.append(f"{label}[{i}:v]xfade=transition={effect}:duration={seconds}:offset={offset:.3f}{out}")
        label = out
        elapsed = offset + lengths[i]
    return ";".join(parts), elapsed


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(segments: list, width: int, height: int, font: str = "Noto Sans Thai") -> str:
    size = round(height * (0.048 if width > height else 0.035))
    margin = round(height * 0.06)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},&H00FFFFFF,&H00000000,&H64000000,0,0,1,{max(2, size // 14)},1,2,60,60,{margin},222",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for seg in segments:
        text = re.sub(r"\s+", " ", str(seg.get("text") or "")).strip().replace("{", "(").replace("}", ")")
        if text:
            lines.append(f"Dialogue: 0,{ass_time(seg['start'])},{ass_time(seg['end'])},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def analysis_request(script: str) -> str:
    """Step 1 of the Prompt-Ref Context method: facts with evidence from the script."""
    return (
        "อ่าน FULL STORY ด้านล่างทั้งเรื่องแล้วสรุปข้อเท็จจริงก่อน โดยยังไม่ต้องสร้าง SnapGen Context. ตอบ JSON เท่านั้น: "
        '{"summary":"","protagonist":{"name":"","evidence":[]},"characters":'
        '[{"name":"","role":"","importance":"main|supporting|minor","is_group":false,"evidence":[]}],'
        '"locations":[{"name":"","evidence":[]}],"props":[{"name":"","evidence":[]}]} '
        "ระบุตัวเอกและตัวละครจากสิ่งที่พูดหรือกระทำในเนื้อเรื่องจริง พร้อมข้อความหลักฐานสั้นๆ จากบท. "
        "ถ้าผู้เล่าบอกชื่อตัวเอง เช่น 'ผมชื่อ ...' ให้ใช้ชื่อนั้น. "
        "คำในวงเล็บท้ายหัวเรื่องเป็น metadata เว้นแต่ปรากฏเป็นบุคคลในเนื้อเรื่องด้วย. "
        "importance: main = ตัวที่เรื่องเดินตาม, supporting = มีบทบาทและปรากฏหลายช่วง, minor = ผ่านมาสั้นๆ. "
        "is_group = true เมื่อเป็นกลุ่มคนไม่มีตัวตนเฉพาะ เช่น ชาวบ้าน ทหาร ฝูงชน. "
        "ถ้าตัวละครมีหลายช่วงวัยที่หน้าตาต่างกันมาก ให้แยกเป็นคนละรายการ เช่น 'นายจำนง (วัยหนุ่ม)' กับ 'นายจำนง (วัย 50)'. "
        "เก็บตัวละคร สถานที่ และพร็อพที่มีผลต่อเหตุการณ์ให้ครบ.\n\nFULL STORY:\n" + script
    )


def context_from_analysis_request(analysis: dict) -> str:
    """Step 2: turn the facts into the program's SnapGen Context (same schema as Prompt-Ref)."""
    try:
        from snapgen_story_types import story_type_prompt_rules
        type_rules = story_type_prompt_rules()
    except Exception:
        type_rules = ""
    return (
        "แปลง STORY_ANALYSIS_JSON ที่บันทึกจากขั้นวิเคราะห์เป็น SnapGen Context เท่านั้น:\n"
        + json.dumps(analysis, ensure_ascii=False) + "\n\n"
        "ห้ามตีความตัวละครหรือเปลี่ยนชื่อใหม่ ให้รักษาชื่อ บทบาท importance is_group สถานที่ และพร็อพตามเดิม. "
        "ตอบ JSON object เท่านั้น ห้าม markdown. ใช้ข้อมูลจากบทจริง; รายละเอียดภาพที่บทไม่ระบุแต่จำเป็นให้สมมุติอย่างสมเหตุผล "
        "คงที่ทั้งเรื่อง และลงท้าย '(สมมุติเพื่อภาพ)'. ระบุยุคสมัยตามบท ห้ามเดาเป็นยุคโบราณถ้าบทเป็นยุคปัจจุบัน. "
        "ห้ามใส่คำบอกวัยหรือประเภทบุคคลต่อท้าย name เว้นแต่แยกช่วงวัยไว้แล้ว. "
        + type_rules + " schema: "
        "{\"version\":3,\"story\":{\"title\":\"\",\"summary\":\"\",\"era\":\"\",\"main_location\":\"\",\"story_type\":\"\","
        "\"story_type_label\":\"\",\"key_places\":[]},"
        "\"characters\":[{\"name\":\"\",\"importance\":\"\",\"is_group\":false,\"อายุ\":\"\",\"เพศ\":\"\",\"บทบาท\":\"\","
        "\"รูปร่าง\":\"\",\"ส่วนสูง\":\"\",\"สีผิว\":\"\",\"ทรงผม\":\"\",\"ใบหน้า\":\"\",\"ดวงตา\":\"\",\"เสื้อผ้า\":\"\","
        "\"visual_identity\":\"\",\"ลักษณะเด่น\":\"\",\"must_include\":[],\"must_not_include\":[],\"assumptions\":[],\"@ref\":null}],"
        "\"locations\":[{\"name\":\"\",\"type\":\"\",\"story_fact\":\"\",\"visual_description\":\"\",\"atmosphere\":\"\"}],"
        "\"props\":[],\"scene_map\":[],\"visual_rules\":{\"tone\":\"\",\"lighting\":{},\"palette\":\"\",\"camera\":{},\"style\":\"\"},"
        "\"forbidden\":[],\"locks\":{}}. เก็บตัวละครทุกคน สถานที่ที่มีเหตุการณ์เกิดจริง และ props สำคัญให้ครบ"
    )


GROUP_WORDS = ("ชาวบ้าน", "ทหาร", "ฝูง", "กลุ่ม", "ผู้คน", "ฝูงชน", "พวก", "หลายคน")


def is_group_character(character: dict) -> bool:
    if character.get("is_group") is True:
        return True
    name = str(character.get("name") or "")
    return any(word in name for word in GROUP_WORDS)


def characters_needing_refs(context: dict, scenes: list) -> list:
    """Characters that appear in the planned scenes, most frequent first.

    The story decides the count: every named individual who is actually
    shown gets one reference image; crowds and groups do not.
    """
    counts = {}
    for scene in scenes:
        for name in scene.get("characters") or []:
            counts[name] = counts.get(name, 0) + 1
    chosen = [
        c for c in context.get("characters", [])
        if c.get("name") in counts and not is_group_character(c)
    ]
    chosen.sort(key=lambda c: -counts[c["name"]])
    return [(c, counts[c["name"]]) for c in chosen]


def character_description(character: dict) -> str:
    keys = ("อายุ", "เพศ", "รูปร่าง", "สีผิว", "ทรงผม", "ใบหน้า", "เสื้อผ้า", "ลักษณะเด่น", "visual_identity")
    # Context marks guessed details "(สมมุติเพื่อภาพ)"; the note itself is noise in a picture prompt.
    parts = [str(character.get(k) or "").replace("(สมมุติเพื่อภาพ)", "").strip() for k in keys]
    return " ".join(p for p in parts if p and p not in ("ไม่ระบุ", "-"))


PACING_NOTE = (
    "จังหวะภาพต้องไม่เท่ากัน: ช่วงเล่าเรื่องเรียบๆ ให้ภาพเดียวแช่ยาวได้ 8–15 วินาที "
    "ส่วนจุดเด่น (เหตุการณ์สำคัญ จุดหักมุม สิ่งที่โผล่ขึ้นมา หรือรายละเอียดที่ควรเห็นชัด) ให้ตัดเป็นภาพสั้น 2–5 วินาทีถี่ๆ "
    "ใส่ภาพแทรกเด่นๆ ที่ช่วยเล่าเรื่องได้ เช่น โคลสอัพสิ่งของ สีหน้า หรือสิ่งที่เคลื่อนออกมา. "
    "ทำเครื่องหมาย highlight=true ให้ภาพที่เป็นจุดเด่นของเรื่อง. "
    "ห้ามวาดคนเล่าเรื่อง/ผู้บรรยาย/เจ้าของช่อง ไมโครโฟน หรือห้องอัดเสียง — ช่วงเปิดเรื่อง ทักทาย หรือปิดท้าย "
    "ให้ใช้ภาพจากในเรื่องแทน: เปิดเรื่องเป็นภาพเหตุการณ์/สถานที่ที่ชวนสงสัยของเรื่อง ปิดท้ายเป็นภาพฉากสำคัญหรือภาพสรุปของเรื่อง. ")
HORROR_NOTE = (
    "เรื่องนี้เป็นเรื่องผี ต้องขายความสยอง: ใส่ภาพแทรกสร้างความหลอน เช่น เงาที่มุมห้อง ดวงตาในความมืด ประตูแง้มเอง "
    "และโคลสอัพสีหน้าตกใจ ในจุดที่บทกำลังเข้มข้น — ภาพแทรกต้องมาจากผี สิ่งของ และสถานที่ที่มีในบทเท่านั้น "
    "ห้ามเพิ่มสัตว์ ผีตัวอื่น หรือสิ่งที่บทไม่ได้พูดถึง (เช่น งู ถ้าบทไม่มีงู). ")


def plan_request(window: list, count: int, names: list, previous: str, era: str, clip_seconds: float = 0,
                 horror: bool = False) -> str:
    lines = "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in window)
    return (
        f"วางแผนภาพประกอบเสียงบรรยายช่วง {fmt_time(window[0]['start'])}–{fmt_time(window[-1]['end'])} "
        f"ประมาณ {count} ภาพ จากประโยคที่ถอดจากเสียงพร้อมเวลาเริ่ม (วินาที) ด้านล่าง. "
        "เลือกจุดเปลี่ยนภาพที่เหตุการณ์ สถานที่ หรือผู้พูดเปลี่ยน ภาพติดกันห้ามซ้ำมุมกล้องเดิม. "
        + PACING_NOTE + (HORROR_NOTE if horror else "") + SCOPE_RULE +
        f"ยุค/บรรยากาศ: {era}. ตัวละครที่ใช้ได้ (ใช้ชื่อตรงตัวเท่านั้น): {', '.join(names) or '-'}. "
        + (f"ภาพก่อนหน้าคือ: {previous}. " if previous else "")
        + "ตอบ JSON เท่านั้น: {\"scenes\":[{\"start\":0.0,\"characters\":[],\"location\":\"\",\"prompt\":\"\",\"motion\":\"\",\"highlight\":false}]} "
        "start = เวลาเริ่มของประโยคที่ภาพนี้เริ่ม (ต้องเป็นตัวเลขในวงเล็บด้านล่าง). "
        "characters = ชื่อตัวละครที่ปรากฏในภาพนี้ (ว่างได้ถ้าเป็นภาพสถานที่). "
        "prompt = คำบรรยายภาพนิ่งภาษาไทย: ใครทำอะไร ที่ไหน เวลา แสง มุมกล้อง อารมณ์ ไม่มีตัวหนังสือในภาพ. "
        f"motion = หนึ่งใน {', '.join(MOTIONS)}. "
        + "\n\n" + lines
    )


DIALOGUE_RE = re.compile(r"(?m)^[ \t]*([^\n:：“”\"]{1,40}?)[ \t]*[:：][ \t]*[“\"](.+?)[”\"]", re.S)
_NOT_MATCHED = re.compile(r"[\s“”\"'‘’.,!?…]")


def script_dialogues(script: str) -> list:
    """[(speaker, line)] for every `ชื่อ : “ … ”` line of the script, in order."""
    return [(m.group(1).strip(), re.sub(r"\s+", " ", m.group(2)).strip()) for m in DIALOGUE_RE.finditer(script)]


def _char_time(seg: dict, k: int, n: int) -> float:
    """Time of character k of n in a sentence: from Whisper word times when kept, else even spread."""
    start, end = float(seg["start"]), float(seg["end"])
    words = [w for w in seg.get("words") or [] if len(w) >= 3 and _NOT_MATCHED.sub("", str(w[2]))]
    if words:
        sizes = [len(_NOT_MATCHED.sub("", str(w[2]))) for w in words]
        target = k / max(1, n) * sum(sizes)
        for (w_start, w_end, _w), size in zip(words, sizes):
            if target < size:  # a letter right after a word belongs to the next word
                return float(w_start) + (float(w_end) - float(w_start)) * target / size
            target -= size
        return float(words[-1][1])
    return start + (end - start) * k / max(1, n)


def _locate(full: str, want: str, cursor: int):
    """(start, end) of ``want`` in ``full`` at/after cursor — exact, else a ≥60 % fuzzy match."""
    if not want:
        return None
    found = full.find(want, cursor)
    if found >= 0:
        return found, found + len(want)
    from difflib import SequenceMatcher
    window = full[cursor:cursor + len(want) * 3 + 200]
    blocks = [b for b in SequenceMatcher(None, window, want, autojunk=False).get_matching_blocks() if b.size >= 2]
    matched = sum(b.size for b in blocks)
    # Narrators sometimes read only the start of a line: accept a clearly heard beginning too.
    if blocks and (matched / len(want) >= 0.6 or (blocks[0].b <= 3 and matched >= 12 and matched / len(want) >= 0.4)):
        return cursor + blocks[0].a, cursor + blocks[-1].a + blocks[-1].size
    return None


def split_dialogue(segments: list, script: str) -> list:
    """Make every spoken line of the script its own sentence with its own start/end time.

    Whisper sentences mix dialogue with the narration around it ("…ปล่อยข้าไปเถิด ” แต่คำอ้อนวอน…").
    Lines are found in the transcript text in script order; their times come
    from Whisper's word times (or an even spread when a transcript has none).
    Dialogue pieces get ``dialogue`` (speaker) and ``line``.
    """
    lines = script_dialogues(script)
    if not lines or not segments:
        return [dict(s) for s in segments]
    chars, times, owner = [], [], []
    for i, seg in enumerate(segments):
        text = str(seg["text"]) + " "
        letters = len(_NOT_MATCHED.sub("", text))
        k = 0  # letters before this character (spaces and quotes take no time)
        for ch in text:
            chars.append(ch)
            times.append(_char_time(seg, k, letters))
            owner.append(i)
            k += 0 if _NOT_MATCHED.match(ch) else 1
    keep = [p for p, ch in enumerate(chars) if not _NOT_MATCHED.match(ch)]
    full = "".join(chars[p] for p in keep)
    label = [None] * len(chars)
    cursor = 0
    for number, (speaker, line) in enumerate(lines):
        span = _locate(full, _NOT_MATCHED.sub("", line), cursor)
        if not span:
            continue
        cursor = span[1]
        for p in range(keep[span[0]], keep[span[1] - 1] + 1):
            label[p] = number
    out = []
    for p, ch in enumerate(chars):
        key = ("d", label[p]) if label[p] is not None else ("s", owner[p])
        if not out or out[-1]["_key"] != key:
            if out and _NOT_MATCHED.match(ch):
                out[-1]["text"] += ch  # spaces/quotes stay with the piece before; the next starts at a letter
                continue
            piece = {"_key": key, "start": round(times[p], 2), "text": ""}
            if label[p] is not None:
                piece["dialogue"], piece["line"] = lines[label[p]]
            out.append(piece)
        out[-1]["text"] += ch
    for a, b in zip(out, out[1:] + [None]):
        a["end"] = b["start"] if b else float(segments[-1]["end"])
        a["text"] = re.sub(r"\s+", " ", a.pop("_key") and a["text"]).strip(" “”\"")
    return [p for p in out if p["text"].strip() and p["end"] > p["start"]]


def video_shots(segments: list, target: float = 8.0, limit: float = SHOT_LIMIT) -> list:
    """Video mode: cut the narration into shots of whole sentences, about 6–11 s each.

    Sentences are joined until the shot reaches ``target`` s, never past
    ``limit`` s; a single sentence longer than ``limit`` stays one shot (it is
    split into continuing shots later).  Shot boundaries are always sentence
    starts, so every clip begins where the narration starts something new.
    """
    shots = []
    for seg in segments:
        if shots:
            shot = shots[-1]
            joined = seg["end"] - shot["start"]
            # A spoken line is always a shot of its own, cut exactly at its words.
            alone = shot.get("dialogue") or seg.get("dialogue")
            if not alone and shot["end"] - shot["start"] < target and joined <= limit:
                shot["end"] = seg["end"]
                shot["text"] += " " + seg["text"]
                continue
        shot = {"start": 0.0 if not shots else seg["start"], "end": seg["end"], "text": seg["text"]}
        if seg.get("dialogue"):
            shot["dialogue"], shot["line"] = seg["dialogue"], seg.get("line") or seg["text"]
        shots.append(shot)
    # A short last shot joins the one before it when that still fits.
    if (len(shots) > 1 and not shots[-1].get("dialogue") and not shots[-2].get("dialogue")
            and shots[-1]["end"] - shots[-1]["start"] < 4 and shots[-1]["end"] - shots[-2]["start"] <= limit):
        last = shots.pop()
        shots[-1]["end"], shots[-1]["text"] = last["end"], shots[-1]["text"] + " " + last["text"]
    return shots


def plan_video(segments: list, duration: float, names: list, era: str, ask, horror: bool = False,
               progress=None, out: list | None = None, batch: int = 20, script: str = "",
               direction: dict | None = None, refs: list | None = None, aspect: str = "16:9",
               economy: bool = False) -> list:
    """Video mode plan: program-cut shots, GPT writes each one, every shot gets a clip choice.

    ``ask(prompt) -> dict`` talks to GPT.  Shots GPT skipped are asked again
    once; any still missing reuse the narration text so nothing is left blank.
    Spoken lines of ``script`` become their own shots (no slow motion).
    ``economy``: longer shots (about 14 s, up to 20 s = grok 10 s Slow 2x), fewer cuts, all slowed.
    """
    lines = split_dialogue(segments, script) if script else segments
    shots = video_shots(lines, ECONOMY_TARGET, ECONOMY_LIMIT) if economy else video_shots(lines)
    if economy:
        era = era + ECONOMY_NOTE
    scenes = out if out is not None else []
    # Director pass first: the whole story as film sequences, so shots are directed, not illustrated.
    # ``direction`` is filled in place (the page saves it); a filled one is reused.
    direction = direction if direction is not None else {}
    if not direction.get("sequences"):
        try:
            direction.update(ask(director_request(shots, names, era + aspect_note(aspect), horror, refs)) or {})
        except Exception:
            pass  # still plannable shot by shot
    for first in range(0, len(shots), batch):
        group = shots[first:first + batch]
        previous = (f"{scenes[-1]['prompt'][:200]} / จบคลิปด้วย: {scenes[-1].get('video_prompt', '')[-160:]}"
                    if scenes else "")
        numbers = list(range(first + 1, first + len(group) + 1))
        items = {}
        for _attempt in range(2):
            wanted = [n for n in numbers if n not in items]
            if not wanted:
                break
            reply = ask(video_plan_request([(n, shots[n - 1]) for n in wanted], names, previous, era + aspect_note(aspect), horror,
                                           direction_for(direction, wanted), refs))
            for item in reply.get("scenes") or []:
                if not isinstance(item, dict) or not str(item.get("prompt") or "").strip():
                    continue
                try:
                    number = int(item.get("shot"))
                except (TypeError, ValueError):
                    continue
                if number in wanted:
                    items.setdefault(number, item)
        for n, shot in zip(numbers, group):
            item = items.get(n) or {"prompt": shot["text"], "video_prompt": shot["text"]}
            scene = {
                "start": shot["start"], "text": shot["text"],
                "characters": [c for c in (item.get("characters") or []) if c in names or c in (refs or [])],
                "location": str(item.get("location") or ""),
                "prompt": str(item.get("prompt") or "").strip(),
                "video_prompt": str(item.get("video_prompt") or "").strip(),
                "motion": MOTIONS[len(scenes) % len(MOTIONS)],
                "slow": str(item.get("slow")).lower() == "true",
                "cheap": str(item.get("cheap")).lower() == "true",
                "shot_no": n,  # number in the director plan (its sequences use these)
            }
            if str(item.get("transition") or "").strip().lower() in TRANSITIONS:
                scene["transition"] = str(item["transition"]).strip().lower()
            if shot.get("dialogue"):  # must stay in sync with the voice: real speed, full quality
                scene.update(dialogue=shot["dialogue"], line=shot["line"], slow=False, cheap=False)
            scenes.append(scene)
        if progress:
            progress(min(len(shots), first + batch), len(shots), group[0]["start"])
    apply_continuity(scenes, direction.get("continuity"))  # one scene per shot here: numbers match
    if economy:
        for scene in scenes:
            scene["slow"] = not scene.get("dialogue")
    split = split_long_scenes(scenes, duration, 0, limit_for=clip_limit)
    scenes[:] = split
    assign_clips(scenes, duration, economy)
    return scenes


def _shot_lines(numbered: list) -> str:
    return "\n".join(f"[{n}] {s['start']:.1f}–{s['end']:.1f} ({s['end'] - s['start']:.0f} วิ) "
                     + (f"บทพูดของ {s['dialogue']}: “{s['line']}”" if s.get("dialogue") else s["text"])
                     for n, s in numbered)


def aspect_note(aspect: str) -> str:
    """Picture shape for GPT's framing (appended to the era line)."""
    if aspect == "9:16":
        return (". สัดส่วนภาพ 9:16 แนวตั้ง (มือถือ): จัดองค์ประกอบแนวตั้ง ตัวละครหลักอยู่กลางเฟรม "
                "ใช้ช็อตกลาง/ใกล้มากขึ้น ช็อตกว้างให้ใช้ความสูง (ท้องฟ้า ความลึก มุมเงย) แทนความกว้าง "
                "สองตัวละครเผชิญหน้ากันให้ใช้ข้ามไหล่หรือหน้า-หลังแทนการยืนซ้าย-ขวา")
    return ". สัดส่วนภาพ 16:9 แนวนอนแบบภาพยนตร์"


def refs_note(refs) -> str:
    """Attachment names GPT must write exactly, so each picture gets its reference files."""
    if not refs:
        return ""
    return ("ไฟล์แนบรูปอ้างอิง (ตัวละคร/สถานที่/สิ่งของ) ที่มี: " + ", ".join(refs)
            + ". เมื่อสิ่งนั้นอยู่ในภาพ ให้เขียนชื่อตรงตามชื่อไฟล์นี้ทุกตัวอักษรใน characters/location/prompt "
            "ห้ามเปลี่ยนคำ ห้ามย่อ (โปรแกรมแนบรูปตามชื่อนี้). ")


CONTINUITY_RULES = (
    "continuity = บันทึกความต่อเนื่องของหนัง (เหมือนฝ่ายคุมความต่อเนื่องในกองถ่าย): ไล่ทั้งเรื่องแล้วบันทึกทุกสภาพที่เปลี่ยนไป "
    "ของตัวละครแต่ละตัว ที่ต้องเห็นต่อเนื่องในช็อตถัดๆ ไป เช่น บาดแผล (ตำแหน่งบนร่างกาย ขนาด) เลือด เกล็ด/เสื้อผ้าขาด "
    "ความเปียก ฝุ่นโคลน ความอ่อนแรง อวัยวะที่ขาดหรือพิการจากเหตุการณ์ในเรื่อง ร่างที่แปลงไป ของที่ถืออยู่. from_shot = ช็อตที่สภาพนั้นเริ่มเกิด, "
    "to_shot = ช็อตสุดท้ายที่ยังต้องเห็น (ถึงตอนจบเรื่องถ้าไม่หาย); ถ้าสภาพเปลี่ยนอีก (เช่น แผลหนักขึ้น แปลงร่าง) ให้เริ่มรายการใหม่. "
    "ช็อตที่เล่าย้อนเหตุการณ์ก่อนสภาพนั้นจะเกิด (ภาพย้อนอดีต คนอื่นเล่าเหตุการณ์ก่อนหน้า) ต้องไม่อยู่ในช่วง from–to "
    "ให้แยกเป็นหลายรายการเว้นช็อตนั้นไว้ เช่น คนที่ตายแล้วแต่ช็อตนี้เล่าตอนเขายังมีชีวิต ต้องเป็นร่างคนปกติ. "
    "character = ชื่อตัวละครตรงตามรายชื่อ. state = คำบรรยายภาพที่ต้องเห็นจริง สั้นและชัด (เช่น 'แผลฉีกยาวจากดาบที่ลำตัวด้านซ้าย "
    "เกล็ดสีนิลแตก มีเลือดซึม เคลื่อนไหวอ่อนแรง'). "
    "state ต้องเป็นเฉพาะรูปลักษณ์ที่มองเห็นบนตัวในช็อตนั้น (แผล คราบ เสื้อผ้า ความเปียก ร่างกาย) เท่านั้น "
    "ห้ามใส่ประวัติ ความสัมพันธ์ อาชีพ นิสัย อารมณ์ หรือการกระทำ/เหตุการณ์ (เช่น ห้าม 'อาศัยอยู่กับลูก' 'เดินสำรวจบ้านร้าง') "
    "ถ้าตัวละครไม่มีสภาพที่เปลี่ยนจากปกติ ไม่ต้องใส่รายการ. ")


def continuity_request(numbered: list, names: list) -> str:
    """For a plan made before continuity was recorded: the continuity record only (text, one GPT request)."""
    lines = "\n".join(f"[{n}] ตัวละคร: {', '.join(s.get('characters') or []) or '-'} | เสียง: "
                      f"{s.get('line') or s.get('text') or ''} | ภาพ: {str(s.get('prompt') or '')[:160]}"
                      for n, s in numbered)
    return (
        "ช็อตทั้งหมดของหนังเรื่องนี้อยู่ด้านล่าง (เลขช็อต ตัวละคร คำบรรยายเสียง และภาพที่วางไว้). "
        f"ตัวละคร: {', '.join(names) or '-'}. "
        "ตอบ JSON เท่านั้น: {\"continuity\":[{\"character\":\"\",\"from_shot\":1,\"to_shot\":1,\"state\":\"\"}]} "
        + CONTINUITY_RULES + "\n\n" + lines
    )


def apply_continuity(scenes: list, entries, numbers: list | None = None) -> None:
    """Write each scene's continuity state (who looks how in this shot) from the continuity record.

    ``numbers[i]`` is scene i's shot number in the record (default i + 1). A
    character's state applies only to scenes that show that character.
    """
    for i, scene in enumerate(scenes):
        n = numbers[i] if numbers else i + 1
        notes = []
        for entry in entries or []:
            if not isinstance(entry, dict) or not str(entry.get("state") or "").strip():
                continue
            try:
                a, b = int(entry.get("from_shot")), int(entry.get("to_shot") or entry.get("from_shot"))
            except (TypeError, ValueError):
                continue
            who = str(entry.get("character") or "").strip()
            shown = scene.get("characters") or []
            if a <= n <= b and (who in shown if who else True):
                notes.append(f"{who}: {str(entry['state']).strip()}" if who else str(entry["state"]).strip())
        if notes:
            scene["continuity"] = "; ".join(dict.fromkeys(notes))
        else:
            scene.pop("continuity", None)


BOARD_CELLS = 9  # one storyboard sheet = 3 x 3 panels, each the video's own shape


def board_groups(scenes: list, direction: dict | None) -> list:
    """Scene indices per storyboard sheet: one director sequence per sheet (split above 9 panels).

    Shots without a director sequence (older plans) go in runs of up to 9.
    """
    sequence_of = {}
    for k, seq in enumerate((direction or {}).get("sequences") or []):
        try:
            a, b = (int(x) for x in (seq.get("shots") or [])[:2])
        except (TypeError, ValueError):
            continue
        for n in range(a, b + 1):
            sequence_of.setdefault(n, k)
    groups, last = [], object()
    for i, scene in enumerate(scenes):
        key = sequence_of.get(scene.get("shot_no"), ("run", i // BOARD_CELLS))
        if groups and key == last and len(groups[-1]) < BOARD_CELLS:
            groups[-1].append(i)
        else:
            groups.append([i])
        last = key
    return groups


def crop_board(sheet_path, count: int, out_paths: list, inset: float = 0.03) -> None:
    """Cut panels 1..count (left to right, top to bottom) out of a 3 x 3 storyboard sheet."""
    from PIL import Image
    with Image.open(sheet_path) as sheet:
        sheet = sheet.convert("RGB")
        w, h = sheet.size
        cw, ch = w / 3, h / 3
        for k in range(count):
            row, col = divmod(k, 3)
            box = (int(col * cw + cw * inset), int(row * ch + ch * inset),
                   int((col + 1) * cw - cw * inset), int((row + 1) * ch - ch * inset))
            sheet.crop(box).save(out_paths[k])


def board_request(numbered: list, aspect: str, live_action: bool = True) -> str:
    """One storyboard sheet: a 3 x 3 grid, panel k = the k-th listed shot, in story order.

    ``live_action`` False (เล่าภาพ): the sheet follows the story's chosen style instead of film realism.
    """
    shape = {"9:16": "แนวตั้ง 9:16", "1:1": "สี่เหลี่ยมจัตุรัส 1:1"}.get(aspect, "แนวนอน 16:9")
    lines = "\n".join(
        f"ช่อง {k}: ช็อต {n} — {str(s.get('prompt') or '')[:260]}"
        + (f" | สภาพต่อเนื่อง: {s['continuity']}" if s.get("continuity") else "")
        for k, (n, s) in enumerate(numbered, 1))
    empty = BOARD_CELLS - len(numbered)
    return (
        "วาดสตอรี่บอร์ดภาพยนตร์ 1 รูป เป็นตาราง 3 แถว x 3 คอลัมน์ ช่องเท่ากันทุกช่อง "
        f"แต่ละช่องเป็นภาพ{shape} คั่นด้วยเส้นขาวบางๆ เรียงช่องจากซ้ายไปขวา บนลงล่าง ตามลำดับช็อตด้านล่าง "
        "ทุกช่องเป็นช็อตต่อเนื่องของหนังเรื่องเดียวกัน: ตัวละครหน้าตาเหมือนกันทุกช่อง สภาพตัวละคร (บาดแผล เลือด ความเปียก ร่างที่เปลี่ยน) "
        "ต่อเนื่องจากช่องก่อน แสงและโทนสีต่อเนื่องกัน ทิศทางจอสอดคล้องกัน. "
        + ("ภาพสมจริงแบบภาพนิ่งจากภาพยนตร์ไลฟ์แอ็กชัน ไม่ใช่การ์ตูน ไม่มีตัวหนังสือ ไม่มีตัวเลขในภาพ. " if live_action
           else "ทุกช่องใช้สไตล์ภาพเดียวกันตามที่ระบุท้ายคำสั่ง ไม่มีตัวหนังสือ ไม่มีตัวเลขในภาพ. ")
        + SCOPE_RULE
        + (f"ช่องท้ายสุด {empty} ช่องที่ไม่มีช็อต ให้เป็นสีดำล้วน. " if empty > 0 else "")
        + "รูปที่แนบมาใช้เป็นหน้าตา/รูปร่างของตัวละครและสถานที่เท่านั้น.\n\n" + lines
    )


def director_request(shots: list, names: list, era: str, horror: bool = False, refs: list | None = None) -> str:
    """Director pass: read the whole narration as a film and break it into sequences before any shot is written."""
    return (
        "คุณคือผู้กำกับภาพยนตร์ ต้องทำเรื่องเล่าด้านล่างให้เป็นหนังสั้นที่ดูเป็นภาพยนตร์จริง ไม่ใช่ภาพประกอบคำบรรยาย. "
        "ภาพทั้งเรื่อง: " + REALISM_NOTE + ". "
        "เสียงบรรยายถูกแบ่งเป็นช็อตแล้ว (เลขช็อต เวลา ความยาว คำบรรยาย) — อ่านทั้งเรื่องก่อน แล้วแตกเป็นซีเควนซ์ "
        "(ช่วงที่เหตุการณ์/สถานที่/อารมณ์ต่อเนื่องกัน) ทุกช็อตต้องอยู่ในซีเควนซ์ใดซีเควนซ์หนึ่ง เรียงต่อกันไม่ข้าม. "
        "คิดแบบผู้กำกับ: แต่ละซีเควนซ์ต้องการบอกอะไร อารมณ์ไต่ระดับอย่างไร ใครอยู่ตรงไหนในฉาก ฝั่งไหนของจอ "
        "จะเปิดด้วยภาพอะไร ไปจบที่ภาพอะไร. ประโยคนามธรรม (ความคิด อดีต คำทำนาย ความรู้สึก ข้อมูลเบื้องหลัง) "
        "ต้องคิดภาพรูปธรรมที่เล่าแทนได้ เช่น ภาพย้อนอดีตโทนสีต่าง โคลสอัพสีหน้า สิ่งของสัญลักษณ์ ปฏิกิริยาของตัวละคร. "
        + (HORROR_NOTE if horror else "")
        + f"ยุค/บรรยากาศ: {era}. ตัวละคร: {', '.join(names) or '-'}. " + refs_note(refs)
        + "ตอบ JSON เท่านั้น: {\"look\":{\"genre_tone\":\"\",\"palette\":\"\",\"lighting\":\"\",\"camera_style\":\"\"},"
        "\"sequences\":[{\"shots\":[1,5],\"name\":\"\",\"location\":\"\",\"time_of_day\":\"\",\"purpose\":\"\","
        "\"emotion\":\"\",\"blocking\":\"\",\"visual_plan\":\"\",\"abstract_lines\":\"\"}],"
        "\"continuity\":[{\"character\":\"\",\"from_shot\":1,\"to_shot\":1,\"state\":\"\"}]} "
        + CONTINUITY_RULES +
        "look = โทนหนังทั้งเรื่อง (แนว โทนสี แสง สไตล์กล้อง) ใช้คงที่ทุกช็อต. "
        "shots = ช็อตแรกและช็อตสุดท้ายของซีเควนซ์. blocking = ตำแหน่งและทิศทางของตัวละครในฉาก (ใครอยู่ซ้าย/ขวาจอ หันไปทางไหน). "
        "visual_plan = ลำดับภาพของซีเควนซ์แบบผู้กำกับ: เปิดด้วยช็อตกว้างสร้างสถานที่ → ขยับเข้ามาระดับกลาง → โคลสอัพอารมณ์ "
        "→ ภาพแทรกรายละเอียด/ปฏิกิริยา และบอกว่าช็อตไหนควรเป็นภาพแบบไหน. "
        "abstract_lines = วิธีเล่าประโยคนามธรรมในซีเควนซ์นี้ด้วยภาพ (ว่างได้)."
        "\n\n" + _shot_lines(list(enumerate(shots, 1)))
    )


def direction_for(direction: dict, numbers: list) -> str:
    """The film look plus only the sequences that cover these shot numbers, for the shot request."""
    if not direction:
        return ""
    picked = []
    for seq in direction.get("sequences") or []:
        try:
            a, b = (int(x) for x in (seq.get("shots") or [])[:2])
        except (TypeError, ValueError):
            continue
        if any(a <= n <= b for n in numbers):
            picked.append(seq)
    return json.dumps({"look": direction.get("look") or {}, "sequences": picked}, ensure_ascii=False)


def video_plan_request(numbered: list, names: list, previous: str, era: str, horror: bool = False,
                       direction: str = "", refs: list | None = None) -> str:
    """Ask GPT to direct one AI video clip per given (number, shot) — the cuts are already fixed."""
    lines = _shot_lines(numbered)
    return (
        f"กำกับคลิปวิดีโอ AI {len(numbered)} ช็อตของหนังเรื่องนี้ ตามแผนผู้กำกับ (ถ้ามี) "
        "(เสียงบรรยายแบ่งช็อตไว้แล้วด้านล่าง: เลขช็อต เวลา ความยาว และคำบรรยายของช็อตนั้น). "
        "เขียนให้ครบทุกช็อต ช็อตละ 1 รายการ ห้ามรวม ห้ามข้าม. "
        "หลักการทำให้เป็นหนัง ไม่ใช่ภาพประกอบคำ: "
        "1) แต่ละช็อตเล่าเหตุการณ์/อารมณ์ของคำบรรยายช็อตนั้นด้วยการกระทำที่เห็นได้ ประโยคนามธรรมให้ใช้ภาพรูปธรรมตามแผนผู้กำกับ. "
        "2) ภาษากล้องชัด: ทุก prompt ระบุขนาดภาพ (ช็อตกว้างมาก/กว้าง/กลาง/ใกล้/โคลสอัพ/ภาพแทรก) มุมกล้อง (ระดับสายตา/มุมต่ำ/มุมสูง/ข้ามไหล่) และเลนส์. "
        "3) ตัดต่อแบบหนัง: ช็อตติดกันต้องเปลี่ยนขนาดภาพหรือมุมกล้อง เปิดซีเควนซ์ใหม่ด้วยช็อตสร้างสถานที่ "
        "ใช้ภาพปฏิกิริยาและภาพแทรกรายละเอียดสลับ รักษาทิศทางจอ (ใครอยู่ซ้าย/ขวา ทิศการเคลื่อนที่) และเส้นสายตาให้ต่อเนื่อง. "
        "4) ช็อตต่อเนื่อง: ช็อตถัดไปต่อจากจุดที่คลิปก่อนจบ (การเคลื่อนไหว ตำแหน่ง แสง สภาพตัวละคร เช่น บาดแผล ความเปียก). "
        "5) คลิป AI ทำได้ดีเมื่อมีการกระทำหลักเดียวที่ชัด: ช็อตละ 1 การกระทำ ตัวละครหลักในเฟรมไม่เกิน 2 ตน "
        "ตัวละครที่ไม่ใช่คน (เช่น พญานาค สัตว์ ผี) ต้องบอกรูปร่างให้ชัดทุก prompt และ video_prompt "
        "(เช่น 'พญานาคเป็นงูยักษ์ ลำตัวยาวมีเกล็ด ไม่มีแขน ไม่มีขา ไม่มีมือ') และห้ามให้ทำท่าที่ต้องใช้มือ "
        "(ถือดาบ ชี้นิ้ว กำหมัด) — ถ้าบทบอกว่าใช้อาวุธ ให้เล่าด้วยหาง ลำตัว เขี้ยว หรือพลังแทน เว้นแต่ Context ระบุว่ามีมือ. "
        "ฉากต่อสู้ให้แตกเป็นจังหวะเดียวต่อช็อต (ฟาด / หลบ / ปะทะ / ปฏิกิริยา) แทนการต่อสู้ยาวในช็อตเดียว. "
        "6) โทนสี แสง และสไตล์กล้องตาม look เดียวกันทุกช็อต. "
        "7) " + REALISM_NOTE + ". "
        "ห้ามวาดคนเล่าเรื่อง ผู้บรรยาย ไมโครโฟน หรือห้องอัดเสียง. ไม่มีตัวหนังสือในภาพ. "
        "ช็อต 'บทพูดของ X' = ภาพใกล้ระดับอก/ใบหน้าของ X กำลังพูดประโยคนั้น เห็นปากชัด สีหน้าและท่าทางตรงกับคำพูด "
        "(video_prompt ให้ X ขยับปากพูดตลอดคลิปด้วยความเร็วปกติ) ช็อตนี้ slow=false cheap=false เสมอ. "
        + (HORROR_NOTE if horror else "")
        + f"ยุค/บรรยากาศ: {era}. ตัวละครที่ใช้ได้ (ใช้ชื่อตรงตัวเท่านั้น): {', '.join(names) or '-'}. "
        + refs_note(refs)
        + (f"แผนผู้กำกับของช่วงนี้: {direction}. " if direction else "")
        + (f"ช็อตก่อนหน้าคือ: {previous}. " if previous else "")
        + "ตอบ JSON เท่านั้น: {\"scenes\":[{\"shot\":1,\"characters\":[],\"location\":\"\",\"prompt\":\"\","
        "\"video_prompt\":\"\",\"slow\":false,\"cheap\":false,\"transition\":\"dissolve\"}]} "
        "transition = การเปลี่ยนภาพเข้าสู่ช็อตนี้ให้ต่อกันเนียนแบบหนัง: dissolve = ภาพจางซ้อนนุ่มๆ (ฉากเดิมต่อเนื่อง); "
        "fadeblack = มืดลงแล้วค่อยสว่างเข้าช็อตนี้ (เปลี่ยนสถานที่ เช่น ใต้น้ำ→ป่า, เปลี่ยนเวลา เช่น กลางวัน→กลางคืน, ข้ามเวลา, ย้อนอดีต, เปิดซีเควนซ์ใหม่); "
        "cut = ตัดเร็ว (จังหวะกระแทกในฉากต่อสู้หรือเหตุการณ์ฉับพลันเท่านั้น). "
        "shot = เลขช็อตในวงเล็บ. characters = ชื่อตัวละครที่ปรากฏในภาพ (ว่างได้ถ้าเป็นภาพสถานที่). "
        "prompt = ภาพแรกของคลิป ภาษาไทย: ขนาดภาพ มุมกล้อง เลนส์ ใครอยู่ตรงไหนของจอทำอะไร ที่ไหน เวลา แสง อารมณ์. "
        "video_prompt = สิ่งที่เกิดตลอดคลิปที่เริ่มจากภาพนั้น ภาษาไทย: การกระทำหลัก 1 อย่าง สีหน้า สิ่งรอบตัว "
        "และกล้องเคลื่อนอย่างไร (ดอลลี่เข้า/ถอย แทร็กตาม เครน แพน ถือกล้องสั่นเล็กน้อยในฉากต่อสู้) ให้เต็มความยาวช็อต ไม่มีบทพูด ไม่มีตัวหนังสือ. "
        "slow = true เมื่อเหมาะกับภาพสโลว์โมชัน 2 เท่า (บรรยากาศ วิว ฉากเงียบ เศร้า ลึกลับ การเคลื่อนไหวช้าๆ); "
        "false เมื่อมีแอ็กชันเร็ว ต่อสู้ วิ่ง หรือท่าทางที่ต้องดูเป็นธรรมชาติ. "
        "cheap = true เฉพาะช็อตง่ายที่ไม่สำคัญ (วิว ทะเล ท้องฟ้า สถานที่ สิ่งของ ขยับน้อย ไม่เห็นหน้าตัวละครชัด) "
        "ใช้โมเดลราคาถูกได้; false เมื่อเห็นตัวละครชัด มีการกระทำหรืออารมณ์สำคัญ."
        "\n\n" + lines
    )


def motion_request(items: list, era: str, horror: bool = False) -> str:
    """Write each clip's video prompt from its finished first frame (attached in order) and the script.

    items: dicts with shot, seconds, line, characters, before, after, planned,
    continuity, dialogue, bodies, notes (problems already seen in this shot).
    """
    blocks = []
    for k, it in enumerate(items, 1):
        rows = [f"รูปที่ {k} = ช็อต {it['shot']} (คลิปยาว {it.get('seconds') or '?'} วินาที)",
                f"คำบรรยายของช็อตนี้: {it.get('line') or '-'}",
                f"ตัวละครในช็อต: {it.get('characters') or '-'}"]
        if it.get("before"):
            rows.append(f"ช็อตก่อนหน้า: {it['before']}")
        if it.get("after"):
            rows.append(f"ช็อตถัดไป: {it['after']}")
        if it.get("planned"):
            rows.append(f"แผนการเคลื่อนไหวเดิม (ร่างก่อนมีรูป): {it['planned']}")
        if it.get("continuity"):
            rows.append(f"สภาพต่อเนื่องที่ต้องคงไว้: {it['continuity']}")
        if it.get("dialogue"):
            rows.append(f"บทพูด: {it['dialogue']}")
        if it.get("bodies"):
            rows.append(f"ร่างกาย: {it['bodies']}")
        if it.get("notes"):
            rows.append(f"ปัญหาที่เคยเจอในช็อตนี้ (ห้ามเกิดอีก): {it['notes']}")
        blocks.append("\n".join(rows))
    return (
        f"รูปที่แนบมา {len(items)} รูปคือภาพแรกจริงของคลิปวิดีโอ AI แต่ละช็อตของเรื่องนี้ (เรียงตามลำดับด้านล่าง; "
        "ภาพแรกวาดตามสตอรี่บอร์ดแล้ว). เขียนพรอมต์วิดีโอใหม่ของแต่ละช็อตให้ครบทุกด้านและชัดพอที่โมเดลวิดีโอ AI เข้าใจได้ทันที. "
        "video_prompt เขียนเป็นหัวข้อตามลำดับนี้ทุกช็อต: "
        "[เปิดภาพ] ตรงกับที่เห็นจริงในรูป: ใครอยู่ตรงไหนของจอ หันทางไหน ท่าทางตอนเริ่ม ฉากหลัง แสง — คลิปเริ่มจากภาพนี้พอดี "
        "ห้ามเปลี่ยนองค์ประกอบ มุมกล้อง หรือตำแหน่งตัวละครไปจากภาพนี้; "
        "[จังหวะ] แบ่งตามเวลาให้เต็มความยาวคลิป เช่น '0–2 วิ: … / 2–5 วิ: … / 5–8 วิ: …' "
        "การกระทำต้องเล่าเหตุการณ์ของคำบรรยายช็อตนี้ ต่อจากช็อตก่อน และพาไปสู่ช็อตถัดไป มีการกระทำหลัก 1 อย่างที่เห็นชัด "
        "บอกว่าใครทำอะไร ด้วยส่วนไหนของร่างกาย ไปทางไหน (ซ้าย/ขวา/เข้าหากล้อง) และผลที่เกิด; "
        "ฉากต่อสู้/แอ็กชัน: ช็อตละ 1 จังหวะ (โจมตี / หลบ / ปะทะ / ล้ม) บอกผู้โจมตี อาวุธหรือส่วนของร่างกาย จุดที่โดน และปฏิกิริยา "
        "ห้ามเขียนแค่ 'ต่อสู้กัน'; "
        "[ร่างกาย] ทุกตัวในเฟรม (คน สัตว์ สิ่งมีชีวิตในตำนาน ผี หุ่น) ขยับได้เฉพาะแบบที่ร่างกายของตัวนั้นทำได้จริงตาม 'ร่างกาย' "
        "ที่ให้ไว้ — ตัวที่ไม่มีมือ/ขาห้ามทำท่าที่ต้องใช้มือ/ขา, สัตว์สี่ขาเดินสี่ขา, คนมี 2 แขน 2 ขา มือละ 5 นิ้ว; "
        "[กล้อง] ขนาดภาพ มุม และการเคลื่อนกล้อง (นิ่ง/ดอลลี่/แพน/แทร็ก/ถือกล้อง); "
        "[บรรยากาศ] สิ่งรอบตัวที่ขยับ (น้ำ ลม ฝุ่น ไฟ ผม ผ้า) แสงและอารมณ์. "
        "anatomy = เฉพาะเรื่องร่างกายที่ต้องระวังในเฟรมนี้ (ท่าตอนเริ่ม ส่วนที่เห็น/ถูกบัง ส่วนที่ต้องขยับ) "
        "ไม่ต้องทวน 'ร่างกาย' ที่ให้ไว้. "
        "negative = negative prompt ของช็อตนี้: สิ่งที่ห้ามปรากฏตลอดคลิป คั่นด้วยจุลภาค ระบุเจ้าของเสมอ "
        "(เช่น มือบนตัวพญานาค, ขาที่ห้าบนตัวม้า, นิ้วเกินบนมือคน, ตัวละครใหม่, เปลี่ยนสถานที่, ตัวหนังสือ — "
        "ห้ามเขียนแค่ 'มือ' เฉยๆ เพราะคนในเฟรมยังต้องมีมือ) และสิ่งที่ผิดบทหรือผิดยุคของช็อตนี้. "
        + REALISM_NOTE + ". ไม่มีตัวหนังสือ ไม่มีคนเล่าเรื่อง. "
        + (HORROR_NOTE if horror else "")
        + f"ยุค/บรรยากาศ: {era}. "
        "ตอบ JSON เท่านั้น: {\"shots\":[{\"shot\":1,\"seen\":\"\",\"video_prompt\":\"\",\"anatomy\":\"\",\"negative\":\"\"}]} "
        "seen = สิ่งที่เห็นในรูปสั้นๆ.\n\n"
        + "\n\n".join(blocks)
    )


def base_looks_request(described: list) -> str:
    """Once per story: every character's look at their first appearance, without changes that come later."""
    lines = "\n".join(f"- {name}: {look}" for name, look in described)
    return (
        "คำบรรยายตัวละครด้านล่างอาจปนสภาพที่เกิดทีหลังในเรื่อง (เช่น ขาขาดตอนท้าย แผลจากเหตุการณ์ ร่างเละหลังตาย "
        "ผมหงอกตอนแก่) ทำให้รูปช่วงต้นเรื่องผิด. ตามบทในประวัตินี้ เขียนรูปลักษณ์ของแต่ละตัว 'ตอนปรากฏตัวครั้งแรกในเรื่อง' ใหม่: "
        "ตัดทุกอย่างที่เกิดขึ้นทีหลังออก (สิ่งนั้นจะใส่เองเฉพาะช่วงที่เกิดแล้ว) แต่คงสิ่งที่เป็นมาตั้งแต่ต้นเรื่องไว้ "
        "(เช่น พิการแต่กำเนิด แผลเป็นเก่า หรือเป็นผีตั้งแต่แรก). เขียนสั้นและชัดสำหรับวาดภาพ ชื่อตรงตามรายชื่อ. "
        "ตอบ JSON เท่านั้น: {\"looks\":[{\"name\":\"\",\"look\":\"\",\"later\":\"\"}]} "
        "later = สิ่งที่ตัดออกเพราะเกิดทีหลัง (ว่างได้).\n\n" + lines
    )


def same_person_request(names: list) -> str:
    """Once per story: characters that are another form of another character (same face)."""
    return (
        "จากบทในประวัตินี้ ตัวละครในรายชื่อด้านล่าง ตัวไหนเป็น 'คนเดียวกัน' กับอีกตัวแต่อยู่ในอีกร่าง "
        "เช่น ผีหรือวิญญาณของคนที่ตายไปแล้ว ร่างที่ถูกสิง หรือร่างที่แปลงไป (ไม่นับคนละคนที่แค่เกี่ยวข้องกัน). "
        "form = ชื่อร่างอื่น, person = ชื่อตัวละครร่างปกติของคนนั้น (ต้องเป็นชื่อในรายชื่อตรงตัว), "
        "how = ร่างนี้ต่างจากร่างปกติอย่างไรสั้นๆ (เช่น วิญญาณหลังตายจากรถชน ร่างซีดโปร่ง). "
        "ถ้าบทไม่บอกชัดว่าเป็นของใคร ให้เลือกตัวที่บทบอกว่าตายหรือกลายร่างตรงกับร่างนี้ที่สุด. "
        f"รายชื่อ: {', '.join(names)}. "
        "ตอบ JSON เท่านั้น: {\"forms\":[{\"form\":\"\",\"person\":\"\",\"how\":\"\"}]} ถ้าไม่มีให้ตอบ {\"forms\":[]}"
    )


def bodies_request(names: list, era: str) -> str:
    """Once per story: how every character's body is built, moves, and what must never appear on it."""
    return (
        "ทำ 'ใบร่างกาย' ของทุกตัวละครในเรื่องนี้ (ตามบทและ Context ในประวัตินี้) ไว้ใช้คุมรูปและวิดีโอ AI ไม่ให้ร่างกายผิด. "
        "ทุกตัว ทั้งคน สัตว์ สิ่งมีชีวิตในตำนาน ผี ยักษ์ เทวดา หุ่น หรือสิ่งของที่ขยับได้: "
        "kind = ประเภท (คน/สัตว์/สิ่งมีชีวิตในตำนาน/ผี/สิ่งของ ...); "
        "body = ร่างกายจริงสั้นๆ 1 ประโยค: มีอะไร ไม่มีอะไร จำนวนแขน ขา ปีก หาง หัว ผิว/เกล็ด/ขน ขนาด "
        "(ตามความเชื่อไทยหรือตามบท เช่น พญานาค = งูยักษ์มีหงอน ไม่มีแขนขา); "
        "moves = เคลื่อนที่ แสดงอารมณ์ และต่อสู้ด้วยอะไร และท่าที่มักพลาด เช่น 'หมอบ' ของตัวนี้คืออะไร; "
        "negative = สิ่งที่ห้ามมีบนตัวนี้ คั่นด้วยจุลภาค ระบุเจ้าของ (เช่น มือบนตัวพญานาค, นิ้วเกินบนมือนางแก้ว, ปีกบนตัวม้า). "
        "ถ้าบทบอกว่าตัวละครแปลงร่าง ให้ใส่ forms = ร่างแต่ละร่างและช่วงที่ใช้. "
        f"ยุค/บรรยากาศ: {era or '-'}. รายชื่อ: {', '.join(names) or '-'}. "
        "ตอบ JSON เท่านั้น: {\"bodies\":[{\"name\":\"\",\"kind\":\"\",\"body\":\"\",\"moves\":\"\",\"negative\":\"\",\"forms\":\"\"}]}"
    )


# Every clip: things AI video often breaks, whatever the story.
VIDEO_NEGATIVE = ("ตัวหนังสือ, ลายน้ำ, ตัวละครใหม่ที่ไม่มีในภาพเริ่มต้น, หน้าตาหรือร่างกายเปลี่ยนไปเองโดยบทไม่ได้สั่ง, "
                  "แขนขาหรือนิ้วเกินหรือหาย, ร่างกายบิดเบี้ยว, ภาพการ์ตูนหรืออนิเมะ, ฉากเปลี่ยนกะทันหัน, "
                  "ตัดฉาก, ตัดไปช็อตอื่น, เปลี่ยนสถานที่, กิจกรรมอื่นที่ไม่ได้สั่ง")
# First line of every clip request: AI video likes to cut to new scenes inside one clip.
ONE_SHOT_RULE = ("ช็อตเดียวต่อเนื่องตลอดคลิป (one continuous shot): กล้องตัวเดียว สถานที่เดียว เวลาเดียว "
                 "เริ่มจากภาพเริ่มต้นและอยู่ในฉากนั้นจนจบคลิป ห้ามตัดฉาก ห้ามตัดไปช็อตอื่น ห้ามเปลี่ยนสถานที่ "
                 "ห้ามเพิ่มคนหรือกิจกรรมที่ไม่ได้สั่ง ทำเฉพาะการกระทำที่เขียนไว้ด้านล่าง.")
CONTINUITY_VERSION = 2  # rules changed: records made before this are built again (text only)
# A jump to another scene inside a clip: mean pixel change over half a second (64x36 frames).
# Measured on real grok-lower clips: single continuous shots stay under ~25, scene jumps reach 45-90.
CUT_THRESHOLD = 30
# Checker 1: a separate GPT (temporary chat) reads the full request before any video credit is spent.
PROMPT_CHECK = (
    "คุณคือผู้ตรวจคำสั่งก่อนส่งให้ AI สร้างวิดีโอ {seconds} วินาที จากภาพเริ่มต้นที่แนบ (ภาพแรกของคลิป ตามสตอรี่บอร์ด). "
    "AI วิดีโอทำทุกอย่างที่อ่านเจอ: ถ้าคำสั่งเอ่ยถึงกิจกรรม สถานที่ หรือเหตุการณ์อื่น มันจะตัดฉากไปทำสิ่งนั้นเองกลางคลิป. "
    "ตรวจคำสั่งทั้งหมดด้านล่าง (ทุกบรรทัด รวมบรรทัดร่างกาย ความต่อเนื่อง และ Negative) ตามเช็กลิสต์: "
    "1) เป็นช็อตเดียวต่อเนื่อง สถานที่เดียว เวลาเดียว ตรงกับภาพเริ่มต้น; "
    "2) ทุกการกระทำมาจากคำบรรยายของช็อตนี้ ไม่มีกิจกรรม เหตุการณ์ หรือคนจากช่วงอื่นของเรื่อง (เช่น ทำไร่ อุ้มลูก ฟันไม้ ถ้าช็อตนี้ไม่ได้เล่า); "
    "3) ตัวละครและตำแหน่งตรงกับภาพเริ่มต้น; "
    "4) บรรทัดร่างกายและความต่อเนื่องมีแต่รูปลักษณ์ที่มองเห็นบนตัว ไม่มีประวัติ ความสัมพันธ์ นิสัย หรือการกระทำ; "
    "5) ทำได้จริงในเวลาที่มี ไม่ยัดหลายเหตุการณ์. "
    "ถ้าผ่านทุกข้อ ตอบ pass=true. ถ้าไม่ผ่าน ใส่ problems และเขียนใหม่: video_prompt = ส่วนหลักของคำสั่ง "
    "(รูปแบบเดิม [เปิดภาพ] [จังหวะ] [ร่างกาย] [กล้อง] [บรรยากาศ] เฉพาะช็อตนี้), anatomy = ร่างกายของคนในเฟรมนี้สั้นๆ (รูปลักษณ์เท่านั้น), "
    "continuity = สภาพที่มองเห็นบนตัวที่ต้องคงไว้ (ว่างได้). "
    "ตอบ JSON เท่านั้น: {{\"pass\":true,\"problems\":[],\"video_prompt\":\"\",\"anatomy\":\"\",\"continuity\":\"\"}}\n\n"
    "ช็อตนี้ในเรื่อง:\n{anchor}\n\nคำสั่งที่จะส่งให้ AI วิดีโอ:\n{request}")
# Checker 2: after the clip, a GPT look at its frames decides whether it can be used.
CLIP_CHECK = (
    "รูปแรกที่แนบคือเฟรมจากคลิปวิดีโอ AI ยาว {length} วินาที เรียงซ้ายไปขวา บนลงล่าง ทุก {step} วินาที "
    "(เฟรมที่ k เริ่มนับ 0 = วินาทีที่ k×{step}); รูปที่สองคือภาพเริ่มต้นที่ตั้งใจไว้. "
    "โปรแกรมตรวจพบภาพกระโดดที่วินาที: {cuts}. "
    "ตัดสินคลิปนี้: pass = เป็นช็อตเดียวต่อเนื่อง (หรือตัดมุมกล้องในฉากเดิมกับคนเดิมแบบหนังทั่วไป) ตรงกับคำบรรยาย ตัวละครหน้าเดิม ร่างกายถูก; "
    "trim = ช่วงแรกใช้ได้ แต่หลังจากนั้นตัดไปฉาก/สถานที่/เหตุการณ์อื่น หรือร่างกายพัง — ใส่ use_until = วินาทีสุดท้ายที่ยังใช้ได้; "
    "redo = ใช้ไม่ได้ตั้งแต่ต้น หรือส่วนที่ใช้ได้สั้นเกิน 2 วินาที. reason = เหตุผลสั้นๆ ภาษาไทย. "
    "ตอบ JSON เท่านั้น: {{\"verdict\":\"pass\",\"use_until\":0,\"reason\":\"\"}}\n\n"
    "ช็อตนี้ในเรื่อง:\n{anchor}\n\nคำสั่งที่ใช้สร้าง:\n{request}")


def image_data_url(path, max_side: int = 768) -> str:
    """A small JPEG data URL of one picture for a GPT look."""
    import io
    from PIL import Image
    with Image.open(path) as image:
        pic = image.convert("RGB")
    pic.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    pic.save(buffer, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def file_digest(path) -> str:
    import hashlib
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def parse_json_reply(text: str) -> dict:
    text = str(text or "").strip()
    for candidate in [text] + re.findall(r"```(?:json)?\s*([\s\S]*?)```", text):
        start = candidate.find("{")
        if start < 0:
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate[start:])
            if isinstance(value, dict):
                return value
        except ValueError:
            continue
    raise RuntimeError("GPT ไม่ได้ตอบเป็น JSON")


# ── page ──────────────────────────────────────────────────────────────────

def install(g: dict, root: tk.Misc) -> tk.Frame:
    from snapgen_page_builder import build_page
    page, box = build_page(root, "🎞️ เล่าภาพ — ใส่บท + เสียงบรรยาย แล้วกดเริ่ม ได้วิดีโอภาพประกอบทั้งเรื่อง")
    _build(g, root, page, box, mode="image")
    return page


def _ancestors(widget) -> list:
    out = []
    while widget is not None:
        out.append(widget)
        widget = widget.master
    return out


def install_video_auto(g: dict, root: tk.Misc, slot_index: int = 1):
    """Slot 2 'ออโต้': swap the Slot for the automatic panel and back."""
    runtime = g.get("_runtime_g") or g
    prompts = runtime.get("slot_prompts") or []
    if len(prompts) <= slot_index:
        return None
    slot_frame = prompts[slot_index].master.master.master  # Text -> content -> body -> Slot frame
    # The panel covers the area holding every Slot (their closest common parent): a full page.
    shared = set.intersection(*({id(w) for w in _ancestors(p.master.master.master.master)} for p in prompts))
    container = next(w for w in _ancestors(slot_frame.master) if id(w) in shared)
    panel = tk.Frame(container, bg="#F8FAFC", highlightthickness=2, highlightbackground="#60A5FA")
    header = tk.Frame(panel, bg="#DBEAFE")
    header.pack(fill="x")
    tk.Label(header, text=f"🤖 Slot {slot_index + 1} ออโต้ — บท + เสียง → วิดีโอทั้งเรื่อง (GPT เลือกโมเดล/ความยาวต่อช็อต ใช้สัดส่วนภาพจาก ⚙ ของ Slot {slot_index + 1})",
             bg="#DBEAFE", fg="#1E3A8A", font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=10, pady=6)
    body = tk.Frame(panel, bg="#F8FAFC")
    body.pack(fill="both", expand=True)
    controls = _build(g, root, panel, body, mode="video", slot_index=slot_index)

    def show_auto():
        # Full page: the panel covers every Slot so the plan table, progress and log all fit.
        panel.place(x=0, y=0, relwidth=1, relheight=1)
        panel.lift()
        controls["refresh_clip_info"]()

    def show_slot():
        if controls["busy"]():
            messagebox.showinfo("ออโต้", "กำลังทำงานอยู่ — กดหยุดก่อนกลับเป็น Slot ปกติ", parent=panel)
            return
        panel.place_forget()

    tk.Button(header, text=f"↩ กลับเป็น Slot {slot_index + 1} ปกติ", command=show_slot, relief="flat",
              bg="#FFFFFF", fg="#1E3A8A", cursor="hand2").pack(side="right", padx=8, pady=4)
    toggle_style = dict(text="🤖 ออโต้", command=show_auto, relief="flat", bg="#2563EB", fg="#FFFFFF",
                        activebackground="#1D4ED8", activeforeground="#FFFFFF", cursor="hand2",
                        font=("TkDefaultFont", 9, "bold"), padx=10)
    placed = False
    try:
        # Sit next to the Slot's own Generate button so nothing is covered.
        generate_btn = (runtime.get("slot_buttons") or [])[slot_index]
        row_frame = generate_btn.master
        if generate_btn.winfo_manager() == "pack":
            side = generate_btn.pack_info().get("side", "left")
            tk.Button(row_frame, **toggle_style).pack(side=side, padx=4, after=generate_btn)
            placed = True
        elif generate_btn.winfo_manager() == "grid":
            info = generate_btn.grid_info()
            columns = [int(w.grid_info().get("column", 0)) for w in row_frame.grid_slaves(row=int(info["row"]))]
            tk.Button(row_frame, **toggle_style).grid(row=int(info["row"]), column=max(columns) + 1, padx=4,
                                                      sticky=info.get("sticky", ""))
            placed = True
    except Exception:
        placed = False
    if not placed:
        tk.Button(slot_frame, **toggle_style).place(relx=1.0, x=-6, y=2, anchor="ne")
    runtime["video_auto_show"] = show_auto
    runtime["video_auto_hide"] = show_slot
    return panel


def _build(g: dict, root: tk.Misc, page: tk.Misc, box: tk.Misc, mode: str = "image", slot_index: int = 1) -> dict:
    from snapgen_page_builder import append_log, make_log_box, make_styled_button

    runtime = g.get("_runtime_g") or g
    video_mode = mode == "video"
    stages = VIDEO_STAGES if video_mode else STAGES
    work_dir_name = "วิดีโอออโต้" if video_mode else "เล่าภาพ"
    bg = box.cget("bg")

    state = {"project": None, "folder": None, "busy": False, "stop": False, "lock": threading.RLock()}
    script_var = tk.StringVar(value="ลากไฟล์มาวาง หรือกดเลือก (.docx / .txt)")
    audio_var = tk.StringVar(value="ลากไฟล์มาวาง หรือกดเลือก (.wav / .mp3 / .m4a)")
    aspect_var = tk.StringVar(value="16:9")
    count_var = tk.StringVar(value="36")
    subtitle_var = tk.BooleanVar(value=False)
    style_var = tk.StringVar(value="ปกติ")
    review_var = tk.BooleanVar(value=False)
    economy_var = tk.BooleanVar(value=True)  # ออโต้: Slow 2x on every shot, longer shots, about half the credit
    stage_var = tk.StringVar(value="พร้อม")

    def economy_on() -> bool:
        return bool(video_mode and (state.get("project") or {}).get("economy", economy_var.get()))
    detail_var = tk.StringVar(value="")

    def slot_settings():
        """(model, clip seconds, aspect) from the Slot the auto mode drives."""
        try:
            cfg = runtime["slot_cfg_vars"][slot_index]
            model = str(cfg["model"].get() or "").strip()
            seconds = float(str(cfg["duration"].get() or "8").strip().rstrip("s") or 8)
            aspect = str(cfg["aspect"].get() or "").strip()
        except Exception:
            model, seconds, aspect = "", 8.0, "16:9"
        return model, max(2.0, seconds), aspect if aspect in SIZES else "16:9"

    def attachment_folder():
        folder = (runtime.get("img_ref_folder") or [None])[0]
        return folder if folder and os.path.isdir(str(folder)) else None

    def attachment_names() -> list:
        """File names (without extension) of the Image page's attachment folder."""
        folder = attachment_folder()
        if not folder:
            return []
        return sorted(os.path.splitext(n)[0].strip() for n in os.listdir(folder)
                      if os.path.splitext(n)[1].lower() in (".png", ".jpg", ".jpeg", ".webp"))

    def attachments_for(scene):
        """Video flow: the Image page's attachments, matched by name like Image AI does."""
        text = " ".join([scene_prompt(scene), " ".join(scene.get("characters") or []), str(scene.get("location") or ""),
                         str(scene.get("text") or "")])
        matcher = runtime.get("img_match_refs_for_text")
        if callable(matcher):
            found = matcher(text)
        else:
            found = match_reference_files(text, attachment_folder())
        return [str(path) for _name, path in found][:6]

    def desired_count() -> int:
        if not video_mode:
            return int(count_var.get())
        duration = float((state["project"] or {}).get("duration") or 0)
        return max(1, int(-(-duration // VIDEO_AUTO_AVG_SECONDS))) if duration else 0

    def desired_aspect() -> str:
        return aspect_var.get() if aspect_var.get() in SIZES else "16:9"

    def refresh_clip_info():
        if not video_mode:
            return
        _model, _seconds, aspect = slot_settings()
        count = desired_count()
        folder = attachment_folder()
        clip_info_var.set(f"ไฟล์แนบ: {Path(folder).name if folder else 'ยังไม่ได้เลือก (เลือกที่หน้ารูป AI)'} · "
                          "GPT เลือกต่อช็อต: grok-lower 6/10 วิ หรือ vela 5 วิ (+สโลว์ 2x)"
                          + (f" → ประมาณ {count} คลิป" if count else ""))

    # ── input row ──
    inputs = tk.Frame(box, bg=bg)
    inputs.pack(fill="x", padx=8, pady=(4, 0))

    def file_card(parent, title, var, command):
        # One slim line (title · file · button): the plan table needs the room more.
        card = tk.Frame(parent, bg="#FFFFFF", highlightthickness=1, highlightbackground="#CBD5E1")
        card.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Label(card, text=title, bg="#FFFFFF", fg="#0F172A", font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=(8, 4), pady=3)
        tk.Button(card, text="เลือก…", command=command, relief="flat", bg="#E2E8F0", fg="#0F172A",
                  cursor="hand2", padx=8).pack(side="right", padx=4, pady=3)
        tk.Label(card, textvariable=var, bg="#FFFFFF", fg="#475569", anchor="w").pack(side="left", fill="x", expand=True)
        return card

    options = tk.Frame(box, bg=bg)
    options.pack(fill="x", padx=8, pady=2)
    clip_info_var = tk.StringVar(value="")
    if video_mode:
        aspect_var.set(slot_settings()[2])
        tk.Label(options, text="ภาพ", bg=bg).pack(side="left")
        ttk.Combobox(options, textvariable=aspect_var, values=list(SIZES), width=6, state="readonly").pack(side="left", padx=(4, 10))
        tk.Label(options, textvariable=clip_info_var, bg=bg, fg="#1E3A8A").pack(side="left", padx=(0, 14))

        def economy_changed():
            if state.get("project") is not None:
                state["project"]["economy"] = bool(economy_var.get())
                save_project()
        tk.Checkbutton(options, text="สโลว์ประหยัด (ทุกช็อตสโลว์ 2x ช็อตยาว ตัดน้อย)", variable=economy_var,
                       command=economy_changed, bg=bg).pack(side="left", padx=(0, 10))
    else:
        tk.Label(options, text="ภาพ", bg=bg).pack(side="left")
        ttk.Combobox(options, textvariable=aspect_var, values=list(SIZES), width=6, state="readonly").pack(side="left", padx=(4, 14))
        tk.Label(options, text="จำนวนรูปทั้งเรื่อง", bg=bg).pack(side="left")
        ttk.Combobox(options, textvariable=count_var, values=IMAGE_COUNTS, width=5, state="readonly").pack(side="left", padx=4)
        tk.Label(options, text="รูปฉาก (+ รูปตัวละครตามเรื่อง)", bg=bg).pack(side="left", padx=(0, 14))
    tk.Label(options, text="สไตล์", bg=bg).pack(side="left")
    ttk.Combobox(options, textvariable=style_var, values=list(STYLES), width=9, state="readonly").pack(side="left", padx=(4, 14))
    tk.Checkbutton(options, text="ใส่ซับไตเติล", variable=subtitle_var, bg=bg).pack(side="left", padx=(0, 10))
    tk.Checkbutton(options, text="หยุดให้ตรวจแผนก่อนสร้างรูป", variable=review_var, bg=bg).pack(side="left")

    # ── run row ──
    run_row = tk.Frame(box, bg=bg)
    run_row.pack(fill="x", padx=8, pady=(6, 2))
    start_btn = make_styled_button(run_row, "PRIMARY", "▶ เริ่มทำทั้งเรื่อง", command=lambda: start_pipeline())
    start_btn.pack(side="left")
    make_styled_button(run_row, "DANGER", "⏸ หยุด", command=lambda: request_stop()).pack(side="left", padx=6)
    make_styled_button(run_row, "SECONDARY", "เปิดโปรเจกต์", command=lambda: choose_saved_project()).pack(side="left", padx=6)
    make_styled_button(run_row, "SUCCESS", "▶ เปิดวิดีโอ", command=lambda: open_path((state["project"] or {}).get("last_video"))).pack(side="left")
    redo_var = tk.StringVar(value=stages[2][1])
    make_styled_button(run_row, "SECONDARY", "↺ ทำใหม่ตั้งแต่ขั้น", command=lambda: redo_from(redo_var.get())).pack(side="right")
    ttk.Combobox(run_row, textvariable=redo_var, values=[t for _k, t, _w in stages], width=14,
                 state="readonly").pack(side="right", padx=6)

    progress_row = tk.Frame(box, bg=bg)
    progress_row.pack(fill="x", padx=8, pady=(4, 0))
    bar = ttk.Progressbar(progress_row, maximum=100)
    bar.pack(fill="x")
    tk.Label(progress_row, textvariable=stage_var, bg=bg, fg="#0F172A", font=("TkDefaultFont", 10, "bold"), anchor="w").pack(fill="x", pady=(4, 0))
    tk.Label(progress_row, textvariable=detail_var, bg=bg, fg="#475569", anchor="w").pack(fill="x")
    stage_row = tk.Frame(box, bg=bg)
    stage_row.pack(fill="x", padx=8, pady=(2, 4))
    stage_labels = {}
    for key, title, _weight in stages:
        label = tk.Label(stage_row, text=f"○ {title}", bg=bg, fg="#94A3B8")
        label.pack(side="left", padx=(0, 14))
        stage_labels[key] = label

    log_box = make_log_box(box)
    log_box.pack(side="bottom", fill="x", padx=8, pady=(2, 6))

    # ── scenes table ──
    table_tools = tk.Frame(box, bg=bg)
    table_tools.pack(fill="x", padx=8)
    tk.Label(table_tools, text="ฉาก (ดับเบิลคลิกเพื่อแก้พรอมต์)", bg=bg, fg="#334155").pack(side="left")
    make_styled_button(table_tools, "SECONDARY", "ต่อวิดีโอใหม่", command=lambda: start_pipeline(only="video")).pack(side="right")
    make_styled_button(table_tools, "SECONDARY", "สร้างรูปใหม่ช็อตที่เลือก", command=lambda: regenerate_selected()).pack(side="right", padx=6)
    make_styled_button(table_tools, "DANGER", "🛠 แก้ช็อตที่มีปัญหา" if video_mode else "🛠 แก้รูปที่มีปัญหา",
                       command=lambda: fix_problem_selected()).pack(side="right", padx=6)
    if video_mode:
        make_styled_button(table_tools, "SUCCESS", "🎬 เจนวิดีโอใหม่ช็อตที่เลือก",
                           command=lambda: remake_clips_selected()).pack(side="right", padx=6)
        make_styled_button(table_tools, "SECONDARY", "🔍 ตรวจคลิป",
                           command=lambda: review_clips_selected()).pack(side="right", padx=6)
    make_styled_button(table_tools, "PRIMARY", "GPT ช่วยแก้ prompt", command=lambda: refine_selected()).pack(side="right", padx=6)
    make_styled_button(table_tools, "DANGER", "เริ่มประวัติ GPT ใหม่", command=lambda: reset_history()).pack(side="right")
    table_frame = tk.Frame(box, bg=bg)
    table_frame.pack(fill="both", expand=True, padx=8, pady=4)
    # The picture preview takes its own fixed column first, so a long table never pushes it off screen.
    preview_box = tk.Frame(table_frame, bg="#F1F5F9", width=320)
    preview_box.pack(side="right", fill="y", padx=(8, 0))
    preview_box.pack_propagate(False)
    preview = tk.Label(preview_box, bg="#F1F5F9", text="เลือกฉากเพื่อดูรูป")
    preview.pack(fill="both", expand=True)
    columns = ("no", "time", "chars", "prompt", "status")
    table = ttk.Treeview(table_frame, columns=columns, show="headings", height=10, selectmode="extended")
    table.tag_configure("bad", foreground="#DC2626")
    for key, title, width in (("no", "#", 44), ("time", "เวลา", 64),
                              ("chars", "ไฟล์แนบที่ใช้ (ตัวละคร/สถานที่)" if video_mode else "ตัวละคร", 220 if video_mode else 170),
                              ("prompt", "ภาพ", 560), ("status", "รูป", 170 if video_mode else 80)):
        table.heading(key, text=title)
        table.column(key, width=width, stretch=key == "prompt")
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    table.pack(side="left", fill="both", expand=True)
    scroll.pack(side="left", fill="y")

    # ── small utilities ──
    def log(message):
        try:
            import snapgen_error_reporter
            if str(message).startswith("❌"):
                snapgen_error_reporter.report_log(str(message), "เล่าภาพ")
        except Exception:
            pass
        root.after(0, lambda m=str(message): append_log(log_box, m))

    def ui(fn, *args):
        root.after(0, lambda: fn(*args))

    def export_root() -> Path:
        value = runtime.get("EXPORT_ROOT") or g.get("EXPORT_ROOT")
        return Path(str(value)) if value else Path.cwd() / "export"

    def open_path(path):
        if path and os.path.exists(str(path)):
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("เล่าภาพ", "ยังไม่มีไฟล์", parent=page)

    def ffmpeg_path() -> str:
        from ai_slow2x import _ffmpeg_bin, ensure_ffmpeg_tool
        found = Path(str(_ffmpeg_bin()))
        if found.is_file():
            return str(found)
        installed = Path(str(ensure_ffmpeg_tool(log) or _ffmpeg_bin()))
        if not installed.is_file():
            raise RuntimeError("ติดตั้ง FFmpeg ไม่สำเร็จ")
        return str(installed)

    def media_duration(path) -> float:
        proc = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
        if not match:
            raise RuntimeError("อ่านความยาวไฟล์เสียงไม่ได้")
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)

    # ── project persistence ──
    def project_path() -> Path:
        return Path(state["folder"]) / "project.json"

    def save_project():
        with state["lock"]:
            project = state["project"]
            if project is None or not state["folder"]:
                return
            path = project_path()
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)

    def open_project(script: str):
        try:
            text = read_script(script)
        except Exception as exc:
            messagebox.showerror("เล่าภาพ", f"อ่านไฟล์บทไม่ได้: {exc}", parent=page)
            return
        folder = project_folder_for(export_root() / work_dir_name, script, text)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            project = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            project = {"version": 2, "conversation": {}, "done": {}, "scenes": []}
            log("เรื่องใหม่ — จะเปิดประวัติ GPT ใหม่ของเรื่องนี้เมื่อกดเริ่ม")
        else:
            log("เปิดงานเดิมของเรื่องนี้ — ทำต่อในประวัติ GPT เดิม")
        project["script"] = script
        project["script_hash"] = script_hash(text)
        show_project(folder, project)

    def last_project_file() -> Path:
        return export_root() / work_dir_name / "_last_project.txt"

    def show_project(folder, project):
        state["folder"] = str(folder)
        state["project"] = project
        try:
            last_project_file().write_text(str(folder), encoding="utf-8")
        except OSError:
            pass
        script_var.set(Path(project.get("script") or Path(folder).name).name)
        if project.get("audio"):
            audio_var.set(f"{Path(project['audio']).name} · {fmt_time(project.get('duration'))}")
        aspect_var.set(project.get("aspect", aspect_var.get()))
        count_var.set(str(project.get("image_count", count_var.get())))
        subtitle_var.set(bool(project.get("subtitles", subtitle_var.get())))
        if video_mode:
            economy_var.set(bool(project.get("economy", True)))
        style_var.set(project.get("style_mode") if project.get("style_mode") in STYLES else "ปกติ")
        save_project()
        refresh_all()
        refresh_clip_info()

    def load_saved_project(folder, quiet=False) -> bool:
        """Reopen a story's work folder (project.json) as it was, without needing the script file."""
        folder = Path(folder)
        try:
            project = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if not quiet:
                messagebox.showerror("เล่าภาพ", f"เปิดโปรเจกต์ไม่ได้ (ไม่มี project.json): {exc}", parent=page)
            return False
        show_project(folder, project)
        log(f"เปิดโปรเจกต์เดิม: {folder.name} — ทำต่อ/แก้ฉากได้เลย")
        return True

    def choose_saved_project():
        if state["busy"]:
            return
        base = export_root() / work_dir_name
        base.mkdir(parents=True, exist_ok=True)
        folder = filedialog.askdirectory(parent=page, title="เลือกโฟลเดอร์โปรเจกต์ที่ทำไว้", initialdir=str(base))
        if folder:
            load_saved_project(folder)

    def choose_script(path=None):
        if state["busy"]:
            return
        path = path or filedialog.askopenfilename(parent=page, title="เลือกไฟล์บท",
                                                  filetypes=[("บท", "*.docx *.txt *.md"), ("All files", "*.*")])
        if path:
            open_project(path)
            log(f"บท: {Path(path).name} → โฟลเดอร์งาน {state['folder']}")

    def choose_audio(path=None):
        if state["busy"]:
            return
        if not state["project"]:
            messagebox.showinfo("เล่าภาพ", "เลือกไฟล์บทก่อน", parent=page)
            return
        path = path or filedialog.askopenfilename(parent=page, title="เลือกไฟล์เสียงบรรยาย",
                                                  filetypes=[("เสียง", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg"), ("All files", "*.*")])
        if not path:
            return
        project = state["project"]
        if project.get("audio") and os.path.normcase(project["audio"]) != os.path.normcase(path):
            # A different narration invalidates timing-dependent stages.
            for key in ("transcribe", "plan", "video"):
                project["done"].pop(key, None)
            project["scenes"] = []
        try:
            duration = media_duration(path)
        except Exception as exc:
            messagebox.showerror("เล่าภาพ", str(exc), parent=page)
            return
        project.update({"audio": path, "duration": round(duration, 2)})
        save_project()
        audio_var.set(f"{Path(path).name} · {fmt_time(duration)}")
        if video_mode:
            refresh_clip_info()
            log(f"เสียงยาว {fmt_time(duration)} → {desired_count()} คลิป (คลิปละ {slot_settings()[1]:g} วินาที)")
        else:
            log(f"เสียงยาว {fmt_time(duration)} → {count_var.get()} รูป เปลี่ยนภาพเฉลี่ยทุก {duration / int(count_var.get()):.0f} วินาที")

    file_card(inputs, "📄 บท", script_var, choose_script)
    file_card(inputs, "🎙 เสียง", audio_var, choose_audio)

    def enable_drop(widget, handler):
        try:
            from tkinterdnd2 import DND_FILES
        except Exception:
            return

        def on_drop(event):
            paths = widget.tk.splitlist(event.data)
            if paths:
                handler(paths[0])
            return "break"
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", on_drop)
            for child in widget.winfo_children():
                child.drop_target_register(DND_FILES)
                child.dnd_bind("<<Drop>>", on_drop)
        except Exception:
            pass
    cards = inputs.winfo_children()
    enable_drop(cards[0], choose_script)
    enable_drop(cards[1], choose_audio)

    # ── display ──
    def refresh_table():
        table.delete(*table.get_children())
        for i, scene in enumerate((state["project"] or {}).get("scenes", [])):
            image = scene.get("image")
            status = "✓" if image and os.path.isfile(image) else "—"
            if scene.get("error"):
                status = "เจนไม่ได้: " + error_reason(scene["error"])
            if scene.get("bad"):
                status = "ต้องเจนใหม่: " + scene["bad"]
            if video_mode:
                clip = scene.get("clip")
                verdict = (scene.get("clip_review") or {}).get("verdict") if clip and os.path.isfile(clip) else None
                status = (f"รูป{status} คลิป{clip_label(scene)}"
                          + ("✓" if clip and os.path.isfile(clip) else ("✗" if scene.get("clip_error") else "—"))
                          + {"pass": " ตรวจผ่าน", "trim": f" ✂ใช้ถึง {scene.get('clip_use_until', '-')}วิ",
                             "redo": " ⚠ควรเจนใหม่"}.get(verdict, ""))
                if verdict == "redo":
                    status += ": " + str((scene.get("clip_review") or {}).get("reason") or "")[:60]
            who = ", ".join(scene.get("characters") or [])
            if video_mode:
                # What the picture of this shot is drawn with: the Image page attachments matched by name.
                try:
                    who = ", ".join(Path(p).stem for p in attachments_for(scene)) or "— ไม่มีไฟล์แนบ"
                except Exception:
                    pass
            redo = video_mode and (scene.get("clip_review") or {}).get("verdict") == "redo"
            table.insert("", "end", iid=str(i), tags=("bad",) if scene.get("bad") or scene.get("error") or redo else (), values=(
                i + 1, fmt_time(scene.get("start")), who,
                scene.get("prompt", "").replace("\n", " "), status))

    def refresh_stages(active=None):
        done = (state["project"] or {}).get("done", {})
        for key, title, _w in stages:
            if done.get(key):
                stage_labels[key].config(text=f"✓ {title}", fg="#16A34A")
            elif key == active:
                stage_labels[key].config(text=f"● {title}", fg="#2563EB")
            else:
                stage_labels[key].config(text=f"○ {title}", fg="#94A3B8")

    def refresh_all():
        refresh_table()
        refresh_stages()
        if state["busy"]:
            return
        done = (state["project"] or {}).get("done", {})
        total = sum(w for _k, _t, w in stages)
        percent = int(sum(w for k, _t, w in stages if done.get(k)) * 100 / total)
        bar.configure(value=percent)
        if percent == 100:
            stage_var.set("เสร็จแล้ว 100% — กด ▶ เปิดวิดีโอ")
        elif percent:
            stage_var.set(f"ทำไปแล้ว {percent}% — กดเริ่มเพื่อทำต่อ")
            start_btn.config(text="▶ ทำต่อ")

    def set_progress(stage_key, fraction, detail=""):
        done_weight = 0
        total = sum(w for _k, _t, w in stages)
        for key, title, weight in stages:
            if key == stage_key:
                percent = int((done_weight + weight * max(0.0, min(1.0, fraction))) * 100 / total)
                ui(lambda p=percent, t=title: (bar.configure(value=p), stage_var.set(f"{t} ... {p}%")))
                break
            done_weight += weight
        ui(detail_var.set, detail)

    def check_stop():
        if state["stop"]:
            raise Stopped()

    # ── Bridge calls (this story's own GPT history) ──
    def chat(content: str) -> str:
        project = state["project"]
        conversation = project.setdefault("conversation", {})
        body = {"model": "auto", "chatgpt_image_intercept": False, "temperature": 0.2,
                "messages": [{"role": "user", "content": content}]}
        if conversation.get("conversation_id") and conversation.get("parent_message_id"):
            body["metadata"] = {"conversation_id": conversation["conversation_id"],
                                "parent_message_id": conversation["parent_message_id"]}
            if conversation.get("account_alias"):
                body["chatgpt_account"] = conversation["account_alias"]
        request = urllib.request.Request(
            g["_chatgpt_api_base"]() + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json; charset=utf-8"},
            method="POST")
        lock = g.get("_bridge_queue_lock") or threading.Lock()
        with lock:
            wait_free = g.get("_wait_bridge_free")
            if callable(wait_free):
                wait_free(log_fn=log)
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    data = json.loads(response.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
                raise RuntimeError(f"Bridge HTTP {exc.code}: {detail}") from exc
        if data.get("error"):
            raise RuntimeError(json.dumps(data["error"], ensure_ascii=False)[:600])
        conversation_id, parent_id = g["_extract_bridge_cursor"](data)
        if conversation_id and parent_id:
            conversation.update({"conversation_id": str(conversation_id), "parent_message_id": str(parent_id)})
            if data.get("chatgpt_account"):
                conversation["account_alias"] = str(data["chatgpt_account"])
            save_project()
        message = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if isinstance(message, list):
            message = "".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in message)
        return str(message)

    def make_image(prompt, refs, out_dir, name, aspect, board=None, identity=False):
        imgmod = runtime.get("_imgmod") or g.get("_imgmod")
        if imgmod is None:
            raise RuntimeError("ระบบสร้างรูปยังไม่พร้อม")
        project = state["project"]
        if board:
            # Image 1 = this shot's storyboard panel (layout to follow), then identity references.
            refs = [board, *refs][:6]
        encoded = [base64.b64encode(Path(p).read_bytes()).decode("ascii") for p in refs]
        if board:
            prompt += (
                "\n\nImage 1 is the STORYBOARD PANEL of this shot: follow its composition, camera angle, framing, "
                "character positions, action and each character's condition (wounds, blood, wet, transformed) exactly, "
                + ("but redraw it as a full-resolution photorealistic live-action film frame with real detail. "
                   if video_mode else "but redraw it as a full-resolution finished picture in the style stated above. ")
                +
                "No panel borders, no shot numbers, no text."
                + ("\nOther attached images are ONLY each character's identity (face, body, outfit):\n"
                   + "\n".join(f"Image {i}: {Path(p).stem}" for i, p in enumerate(refs[1:], 2)) if len(refs) > 1 else ""))
        elif refs and identity:
            # A character's other form (e.g. their ghost): a reference picture of the same person.
            prompt += ("\n\nThe attached image is the SAME PERSON in normal form: keep the exact face, facial "
                       "structure, body, height and hair; change only what the prompt says this form changes.")
        elif refs:
            prompt += (
                "\n\nATTACHED REFERENCES are ONLY for each character's identity (face, body, outfit):\n"
                + "\n".join(f"Image {i}: {Path(p).stem}" for i, p in enumerate(refs, 1))
                + "\nDraw a completely NEW scene picture as described above, with its own setting, background, "
                "lighting, camera angle and action. Do NOT copy the references' standing pose, plain gray background, "
                "framing or layout, and never output a character reference sheet or a person on a plain backdrop.")
        import hashlib
        old = {hashlib.md5(Path(s["image"]).read_bytes()).hexdigest()
               for s in project.get("scenes") or [] if s.get("image") and os.path.isfile(s["image"])}
        old.update(project.get("rejected_hashes") or [])
        out = imgmod.generate_image(
            prompt, output_dir=str(out_dir), name_hint=name, is_edit=bool(encoded),
            ref_images=encoded or None, aspect_ratio=aspect, save_sidecar=False,
            conversation_state=project.setdefault("conversation", {}), conversation_save_fn=save_project,
        )
        if hashlib.md5(Path(out).read_bytes()).hexdigest() in old:
            # The story chat handed back an old picture (GPT drew nothing new): ask once more in a fresh
            # temporary chat so the scene really gets a new image.
            log(f"{name}: ได้รูปเก่าซ้ำกลับมา — สร้างใหม่ในแชตชั่วคราว")
            Path(out).unlink(missing_ok=True)
            out = imgmod.generate_image(
                prompt, output_dir=str(out_dir), name_hint=name, is_edit=bool(encoded),
                ref_images=encoded or None, aspect_ratio=aspect, save_sidecar=False, temporary_chat=True)
            if hashlib.md5(Path(out).read_bytes()).hexdigest() in old:
                Path(out).unlink(missing_ok=True)
                raise RuntimeError("GPT ส่งรูปเก่าซ้ำกลับมา ไม่ได้สร้างรูปใหม่")
        target = Path(out_dir) / f"{name}{Path(out).suffix or '.png'}"
        for old in Path(out_dir).glob(f"{name}.*"):
            if old.resolve() != Path(out).resolve():
                old.unlink(missing_ok=True)
        if Path(out).resolve() != target.resolve():
            shutil.move(str(out), str(target))
        return str(target)

    def with_retries(label, action, attempts=3):
        last = None
        for attempt in range(1, attempts + 1):
            check_stop()
            try:
                return action()
            except Stopped:
                raise
            except Exception as exc:
                last = exc
                text = str(exc).lower()
                if "image rate limit" in text or "ratelimitexception" in text or "ถึงลิมิต" in str(exc):
                    # ChatGPT quota is used up: retrying only wastes time.
                    friendly = runtime.get("_snapgen_friendly_bridge_error") or g.get("_snapgen_friendly_bridge_error")
                    message = friendly(str(exc)) if callable(friendly) else str(exc)
                    raise RateLimited(message) from exc
                if "401" in str(exc) and ("ChatGPT" in str(exc) or "Provider status" in str(exc)):
                    # The account this story's history lives on has to log in again; retrying cannot help.
                    alias = ((state["project"] or {}).get("conversation") or {}).get("account_alias") or "บัญชีที่ใช้อยู่"
                    raise RuntimeError(
                        f"บัญชี ChatGPT ({alias}) ใช้ไม่ได้ (401 ต้องล็อกอินใหม่) — "
                        "กด Use บัญชีที่ใช้ได้ใน Bridge แล้วกด ▶ ทำต่อ เรื่องจะย้ายไปบัญชีนั้นเอง "
                        "(หรือล็อกอินบัญชีนี้ใหม่ถ้าอยากใช้ประวัติเดิม)") from exc
                log(f"❌ {label} ครั้งที่ {attempt}: {str(exc)[:200]}")
                if "GPT said:" in str(exc) or "safety policy" in text:
                    # GPT refused this prompt: asking the same thing again only burns time.
                    raise
                if "conversation_not_found" in text or ("conversation" in text and ("not found" in text or "404" in text)):
                    # One story = one GPT history. Never open a replacement
                    # chat silently; the user decides with the explicit button.
                    raise HistoryLost(
                        "ประวัติ GPT ของเรื่องนี้หายไป (ถูกลบหรือเปลี่ยนบัญชี) — โปรแกรมไม่เปิดประวัติใหม่ให้เอง "
                        "ถ้าต้องการทำต่อในประวัติใหม่ กดปุ่ม 'เริ่มประวัติ GPT ใหม่'") from exc
                time.sleep(3 * attempt)
        raise RuntimeError(f"{label} ไม่สำเร็จ: {last}")

    def active_bridge_account() -> str:
        """The account chosen with Bridge Manager 'Use' (CHATGPT_ACCOUNT in the Bridge .env; name only)."""
        bridge_dir = Path(str(runtime.get("BRIDGE_DIR") or g.get("BRIDGE_DIR") or Path.home() / "chatgpt-api"))
        try:
            for line in (bridge_dir / ".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("CHATGPT_ACCOUNT="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return ""

    def follow_active_account():
        """Pressing Use on another account moves this story there: a ChatGPT chat lives in one account only,
        so the story starts a new history in the chosen account (script and Context are sent first;
        plan, pictures and clips are kept)."""
        project = state["project"]
        conversation = project.get("conversation") or {}
        bound = str(conversation.get("account_alias") or "").strip()
        active = active_bridge_account()
        if bound and active and bound.casefold() != active.casefold():
            project["conversation"] = {}
            project["history_seeded"] = False
            log(f"ใช้ {active} ตามที่กด Use (เดิมเรื่องนี้อยู่ใน {bound}) — เริ่มประวัติ GPT ของเรื่องใหม่ในบัญชีนี้ "
                "ส่งบทและ Context ให้ก่อน แผน รูป และคลิปที่ทำแล้วยังอยู่ครบ")

    def ensure_story_in_history():
        """Make sure this story's GPT history has the script and Context.

        A Context reused from the team file was built elsewhere, so the first
        request of this history would otherwise know nothing about the story.
        """
        project = state["project"]
        if project.get("history_seeded") or (project.get("conversation") or {}).get("conversation_id"):
            project["history_seeded"] = True
            return
        set_progress("plan", 0.0, "ส่งบทและ Context เข้าประวัติ GPT ของเรื่องนี้ (ครั้งเดียว) ...")
        content = (
            "บทและ SnapGen Context ของเรื่องนี้อยู่ด้านล่าง เก็บไว้ในประวัตินี้เพื่อใช้วางแผนฉากและสร้างรูปทุกครั้งต่อจากนี้ "
            "ใช้ชื่อและหน้าตาตัวละครตาม Context ตรงตัว ตอบสั้นๆ ว่า OK เท่านั้น\n\nCONTEXT:\n"
            + json.dumps(project.get("context") or {}, ensure_ascii=False)
            + "\n\nFULL STORY:\n" + read_script(project["script"])
        )
        with_retries("ส่งบทเข้าประวัติ", lambda: chat(content))
        project["history_seeded"] = True
        save_project()

    # ── stages ──
    def stage_context():
        project, folder = state["project"], Path(state["folder"])
        shared = None
        try:
            import snapgen_shared_context
            if not project.pop("force_new_context", False):  # "ทำใหม่ตั้งแต่วิเคราะห์บท" asks GPT again
                shared = snapgen_shared_context.load(project["script"])
        except Exception:
            pass
        if shared:
            context = shared["context"]
            log("ใช้ Context ที่ทีมแตกไว้แล้ว (หน้า Prompt-Ref หรือเล่าภาพ) ไม่ต้องให้ GPT วิเคราะห์ใหม่")
        else:
            # Same two-step method as Prompt-Ref: facts with evidence first,
            # then the full SnapGen Context built only from those facts.
            script = read_script(project["script"])
            set_progress("context", 0.2, "GPT กำลังอ่านบทและหาหลักฐานตัวละคร ...")
            analysis = with_retries("วิเคราะห์บท", lambda: parse_json_reply(chat(analysis_request(script))))
            project["history_seeded"] = True  # the full script is now in this story's history
            if not isinstance(analysis.get("characters"), list) or not analysis["characters"]:
                raise RuntimeError("GPT วิเคราะห์บทไม่ครบ: ไม่มีรายชื่อตัวละคร")
            (folder / "story_analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
            set_progress("context", 0.6, "GPT กำลังสร้าง Context ตัวละครและสถานที่ ...")
            context = with_retries("สร้าง Context", lambda: parse_json_reply(chat(context_from_analysis_request(analysis))))
            if not isinstance(context.get("characters"), list) or not context["characters"]:
                raise RuntimeError("GPT คืน Context ไม่ครบ")
            try:
                from snapgen_context_tools import normalize_context_master
                extras = {c.get("name"): c for c in context["characters"] if isinstance(c, dict)}
                context = normalize_context_master(folder, context)
                for character in context.get("characters", []):  # keep page-only flags
                    source = extras.get(character.get("name")) or {}
                    for key in ("importance", "is_group"):
                        if key in source:
                            character[key] = source[key]
            except Exception as exc:
                log(f"จัดรูปแบบ Context ไม่ได้ ใช้ตามที่ GPT ตอบ: {exc}")
            try:
                import snapgen_shared_context
                snapgen_shared_context.save(project["script"], context, script)
                log("บันทึก Context ไว้ข้างไฟล์บทแล้ว — หน้า Prompt-Ref และคนในทีมใช้ต่อได้")
            except Exception:
                pass
        (folder / "context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        project["context"] = context
        labels = []
        for c in context.get("characters", []):
            if c.get("name"):
                tag = "กลุ่ม" if is_group_character(c) else {"main": "หลัก", "supporting": "รอง", "minor": "ประกอบ"}.get(c.get("importance"), "")
                labels.append(f"{c['name']}" + (f" ({tag})" if tag else ""))
        log(f"✓ ตัวละคร {len(labels)}: {', '.join(labels)}")
        research_ghosts()

    def research_ghosts():
        """Ghost stories: learn how each Thai ghost looks before any picture is drawn."""
        project = state["project"]
        if "ghosts" in project:
            return
        script = read_script(project["script"])
        if not (GHOST_HINT.search(script) or project.get("style_mode") == "เรื่องผี"):
            project["ghosts"] = []
            return
        set_progress("context", 0.9, "GPT กำลังค้นข้อมูลผีในเรื่องก่อนสร้างรูป ...")
        try:
            reply = with_retries("ค้นข้อมูลผี", lambda: parse_json_reply(chat(ghost_research_request(script))))
            ghosts = [g_ for g_ in reply.get("ghosts") or [] if isinstance(g_, dict) and g_.get("name") and g_.get("look")]
        except (Stopped, HistoryLost, RateLimited):
            raise
        except Exception as exc:
            log(f"ค้นข้อมูลผีไม่สำเร็จ ใช้ข้อมูลผีไทยในโปรแกรมแทน: {exc}")
            ghosts = []
        try:  # GPT may still write gore words; the image model refuses those
            from snapgen_page_story_face import soften_injury_text
            for g_ in ghosts:
                g_["look"] = soften_injury_text(g_["look"]).replace("ไส้", "สายโปร่งแสงเรืองแสง").replace("อวัยวะ", "สายโปร่งแสง")
        except Exception:
            pass
        project["ghosts"] = ghosts
        known = [name for name, _look in find_ghosts(script)]
        names = sorted({g_["name"] for g_ in ghosts} | set(known))
        log("✓ ผีในเรื่อง: " + (", ".join(names) if names else "ไม่พบ"))
        save_project()

    def stage_transcribe():
        project, folder = state["project"], Path(state["folder"])
        # The same narration may already be transcribed by the other page
        # (เล่าภาพ / Slot 2 ออโต้): reuse it instead of running Whisper again.
        same_audio = os.path.normcase(os.path.abspath(project["audio"]))
        for other in (export_root() / "เล่าภาพ", export_root() / "วิดีโอออโต้"):
            for candidate in other.glob("*/project.json") if other.is_dir() else []:
                if candidate.parent == folder:
                    continue
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                transcript = candidate.parent / "transcript.json"
                if video_mode and transcript.is_file() and '"words"' not in transcript.read_text(encoding="utf-8")[:20000]:
                    continue  # older transcript without word times: dialogue cuts need them
                if (data.get("done", {}).get("transcribe") and transcript.is_file()
                        and os.path.normcase(os.path.abspath(str(data.get("audio") or ""))) == same_audio):
                    shutil.copy2(transcript, folder / "transcript.json")
                    log(f"ใช้ข้อความถอดเสียงที่ทำไว้แล้วจาก {candidate.parent.parent.name}/{candidate.parent.name}")
                    return
        set_progress("transcribe", 0.02, "กำลังเปิด Whisper ...")
        names = [c.get("name") for c in (project.get("context") or {}).get("characters", []) if c.get("name")]
        segments, duration, backend = [], float(project.get("duration") or 1), ""
        # Whisper runs in its own low-priority process: the GPU does the model,
        # but audio decoding/features use the CPU and must not freeze the PC.
        for item in transcribe_in_background(project["audio"], ("ชื่อในเรื่อง: " + ", ".join(names)) if names else "",
                                             should_stop=lambda: state["stop"], words=video_mode):
            if "log" in item:
                log(item["log"])
                continue
            if "backend" in item:
                backend = item["backend"]
                continue
            text = item["text"].strip().replace("ํา", "ำ")  # ํา → ำ
            if text:
                segments.append({"start": round(item["start"], 2), "end": round(item["end"], 2), "text": text})
                if item.get("words"):  # Slot 2 ออโต้ only: word times place dialogue cuts
                    segments[-1]["words"] = item["words"]
            set_progress("transcribe", item["end"] / duration, f"ฟังเสียง {fmt_time(item['end'])} / {fmt_time(duration)} ({backend})")
        check_stop()
        if not segments:
            raise RuntimeError("Whisper ไม่ได้ยินเสียงพูดในไฟล์")
        try:
            corrected = correct_with_script(segments, read_script(project["script"]))
            changed = sum(1 for a, b in zip(segments, corrected) if a["text"] != b["text"])
            log(f"แก้คำที่ฟังผิดตามบท {changed} ประโยค")
            segments = corrected
        except Exception as exc:
            log(f"แก้คำตามบทไม่ได้ ใช้ข้อความจากเสียงแทน: {exc}")
        (folder / "transcript.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"✓ ถอดเสียงได้ {len(segments)} ประโยค")

    def character_refs():
        refs_dir = Path(state["folder"]) / "refs"
        refs_dir.mkdir(exist_ok=True)
        found = {}
        for character in (state["project"].get("context") or {}).get("characters", []):
            name = str(character.get("name") or "").strip()
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                path = refs_dir / f"{safe_name(name)}{ext}"
                if name and path.is_file():
                    found[name] = str(path)
                    break
        return found

    def style_text():
        context = state["project"].get("context") or {}
        rules = context.get("visual_rules") or {}
        era = (context.get("story") or {}).get("era") or ""
        style = str(rules.get("style") or "").strip() or DEFAULT_STYLE
        mood = STYLES.get(state["project"].get("style_mode") or "ปกติ", STYLES["ปกติ"])["prompt"]
        if mood:
            # The chosen story style overrides the Context's general look.
            style = mood
        thai = (" ฉาก บ้านเรือน วัด ร้านค้า เครื่องแต่งกาย และผู้คนเป็นแบบไทยของประเทศไทย "
                "ห้ามออกเป็นแบบจีน ญี่ปุ่น เกาหลี หรือตะวันตก เว้นแต่บทระบุว่าอยู่ต่างประเทศ.")
        if video_mode:
            style = REALISM_NOTE + ". " + style
        return (f"สไตล์: {style}. ยุค/บรรยากาศ: {era}." if era else f"สไตล์: {style}.") + thai

    def ensure_base_looks():
        """Once per story: each character's look at first appearance (later injuries come from continuity)."""
        project = state["project"]
        if project.get("base_looks") is not None:
            return
        described = [(c["name"], character_description(c)) for c in (project.get("context") or {}).get("characters", [])
                     if c.get("name") and character_description(c)]
        looks = {}
        if described:
            ensure_story_in_history()
            try:
                reply = with_retries("รูปลักษณ์ตอนต้นเรื่อง", lambda: parse_json_reply(chat(base_looks_request(described))),
                                     attempts=2)
                names = {n for n, _l in described}
                for item in reply.get("looks") or []:
                    name, look = str(item.get("name") or "").strip(), str(item.get("look") or "").strip()
                    if name in names and look:
                        looks[name] = look
                        if str(item.get("later") or "").strip():
                            log(f"{name}: ไม่ใส่ในรูปตั้งแต่ต้นเรื่อง (เกิดทีหลัง) — {str(item['later']).strip()[:120]}")
                            # The later change now lives only in the continuity record: build it again (text only).
                            project["continuity_done"] = False
            except (Stopped, HistoryLost, RateLimited):
                raise
            except Exception as exc:
                log(f"ทำรูปลักษณ์ตอนต้นเรื่องไม่สำเร็จ ({str(exc)[:120]}) — ใช้คำบรรยายตัวละครเดิม")
        project["base_looks"] = looks
        save_project()

    def look_of(character) -> str:
        """How a character looks before anything in the story changes them."""
        looks = state["project"].get("base_looks") or {}
        return looks.get(character.get("name")) or character_description(character)

    def ensure_same_person():
        """Once per story: which characters are another form (ghost, spirit, transformed) of another one."""
        project = state["project"]
        if project.get("same_person") is not None:
            return project["same_person"]
        names = [c.get("name") for c in (project.get("context") or {}).get("characters", [])
                 if c.get("name") and not is_group_character(c)]
        found = {}
        if len(names) > 1:
            ensure_story_in_history()
            try:
                reply = with_retries("หาร่างอื่นของตัวละคร", lambda: parse_json_reply(chat(same_person_request(names))),
                                     attempts=2)
                for item in reply.get("forms") or []:
                    form, person = str(item.get("form") or "").strip(), str(item.get("person") or "").strip()
                    if form in names and person in names and form != person:
                        found[form] = {"person": person, "how": str(item.get("how") or "").strip()}
            except (Stopped, HistoryLost, RateLimited):
                raise
            except Exception as exc:
                log(f"หาร่างอื่นของตัวละครไม่สำเร็จ ({str(exc)[:120]}) — ทำรูปตัวละครแยกกันตามเดิม")
        project["same_person"] = found
        save_project()
        if found:
            log("คนเดียวกันคนละร่าง: " + ", ".join(f"{f} = {v['person']}" for f, v in found.items()))
        return found

    def forms_to_remake(existing) -> list:
        """Form pictures (e.g. a ghost) drawn before they were tied to their person: draw them again from the person."""
        project = state["project"]
        same = project.get("same_person") or {}
        tied = set(project.get("form_refs") or [])
        return [f for f, v in same.items() if f in existing and f not in tied and v["person"] in existing]

    def stage_characters():
        project = state["project"]
        ensure_story_in_history()
        ensure_base_looks()
        same = ensure_same_person()
        refs_dir = Path(state["folder"]) / "refs"
        needed = characters_needing_refs(project.get("context") or {}, project.get("scenes") or [])
        characters = [c for c, _count in needed]
        existing = character_refs()
        for form in forms_to_remake(existing):
            # Keep the old picture as a backup; the new one is drawn from the person's own picture.
            old = Path(existing.pop(form))
            old.replace(old.with_name(old.stem + "_สำรอง" + old.suffix))
            log(f"ทำรูป {form} ใหม่จากหน้าของ {same[form]['person']} (รูปเดิมเก็บเป็น _สำรอง)")
        todo = [c for c in characters if c["name"] not in existing]
        todo.sort(key=lambda c: c["name"] in same)  # each person before their other forms
        for n, character in enumerate(todo, 1):
            check_stop()
            name = character["name"]
            set_progress("characters", (n - 1) / max(1, len(todo)), f"ทำรูปตัวละคร {n}/{len(todo)}: {name}")
            base = same.get(name)
            base_ref = character_refs().get(base["person"]) if base else None
            prompt = (
                f"ภาพอ้างอิงตัวละคร '{name}' ตอนปรากฏตัวครั้งแรกในเรื่อง: {look_of(character)}. "
                + (f"'{name}' คือ '{base['person']}' คนเดียวกันในอีกร่าง ({base['how'] or 'ร่างผี/วิญญาณ'}): "
                   f"ใช้หน้าตา โครงหน้า รูปร่าง ส่วนสูง และทรงผมของ '{base['person']}' จากรูปที่แนบให้เหมือนเดิมทุกจุด "
                   "เปลี่ยนเฉพาะสิ่งที่ร่างนี้ต่างไป. " if base_ref else "")
                + "".join(f"ลักษณะผีตามความเชื่อไทย (ต้องวาดตามนี้): {look}. "
                          for _n, look in find_ghosts(name, project.get("ghosts") or []))
                +
                "ภาพเต็มตัวยืนตรง หันหน้าเข้ากล้อง เห็นหน้าชัด พื้นหลังสีเทาเรียบ แสงสม่ำเสมอ ไม่มีวัตถุอื่น "
                + style_text()
            )
            with_retries(f"รูปตัวละคร {name}",
                         lambda p=prompt, nm=safe_name(name), r=base_ref: make_image(
                             p, [r] if r else [], refs_dir, nm, "1:1", identity=bool(r)))
            if base_ref:
                project.setdefault("form_refs", []).append(name)
                save_project()
            log(f"✓ รูปตัวละคร {name}" + (f" (หน้าเดียวกับ {base['person']})" if base_ref else ""))
        log(f"✓ รูปตัวละครครบ {len(characters)} ตัว ตามที่ปรากฏในฉาก (เปลี่ยนรูปได้ที่ {refs_dir})")

    def check_script_matches_audio():
        """Stop before GPT or picture credits when the script and the narration are different stories."""
        project = state["project"]
        transcript = Path(state["folder"]) / "transcript.json"
        if not transcript.is_file():
            return
        score = script_audio_match(json.loads(transcript.read_text(encoding="utf-8")), read_script(project["script"]))
        if score < 0.6:
            raise RuntimeError(
                f"ไฟล์บทกับไฟล์เสียงไม่ใช่เรื่องเดียวกัน (ตรงกันแค่ {score:.0%}) — "
                f"บท: {Path(project['script']).name} / เสียง: {Path(project['audio']).name}. "
                "เลือกไฟล์บทของเสียงนี้ใหม่ (จะได้โปรเจกต์ใหม่ของเรื่องนั้น) หรือเลือกเสียงของบทนี้")

    def stage_plan():
        project, folder = state["project"], Path(state["folder"])
        check_script_matches_audio()
        ensure_story_in_history()
        segments = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        context = project.get("context") or {}
        names = [c.get("name") for c in context.get("characters", []) if c.get("name")]
        era = (context.get("story") or {}).get("era") or ""
        if video_mode:
            # Shots are cut at sentence starts by the program; GPT writes each one.
            def ask(prompt):
                check_stop()
                return with_retries("วางแผนคลิป", lambda: parse_json_reply(chat(prompt)))

            def progress(done, total, start):
                set_progress("plan", done / total, f"วางแผนคลิป {done}/{total} ({fmt_time(start)})")
                project["scenes"] = scenes_so_far
                save_project()
                ui(refresh_table)
            if script_dialogues(read_script(project["script"])) and not any(s.get("words") for s in segments):
                log("⚠ ข้อความถอดเสียงนี้ไม่มีเวลาของแต่ละคำ จุดตัดบทพูดอาจคลาดได้ราว 1 วินาที")
            scenes_so_far = []
            plan_video(segments, float(project["duration"]), names, era, ask,
                       horror=project.get("style_mode") == "เรื่องผี", progress=progress, out=scenes_so_far,
                       script=read_script(project["script"]), direction=project.setdefault("direction", {}),
                       refs=attachment_names(), aspect=desired_aspect(), economy=economy_on())
            if project["direction"]:
                (folder / "director_plan.json").write_text(
                    json.dumps(project["direction"], ensure_ascii=False, indent=2), encoding="utf-8")
            # The director pass already recorded continuity; otherwise it is asked for before pictures.
            project["continuity_done"] = bool(project["direction"].get("continuity"))
            project["continuity_v"] = CONTINUITY_VERSION
            project["scenes"] = scenes_so_far
            save_project()
            log(f"✓ วางแผน {len(scenes_so_far)} คลิป ({clip_summary(scenes_so_far)})")
            return
        windows = plan_windows(segments)
        planned = project.setdefault("planned_windows", 0)
        scenes = project["scenes"] if planned else []
        for w in range(planned, len(windows)):
            check_stop()
            window = windows[w]
            span = window[-1]["end"] - window[0]["start"]
            target = int(project.get("image_count", 50))
            count = max(1, round(target * span / float(project["duration"])))
            set_progress("plan", w / len(windows), f"วางแผนช่วง {w + 1}/{len(windows)} ({fmt_time(window[0]['start'])})")
            previous = scenes[-1]["prompt"][:200] if scenes else ""
            reply = with_retries("วางแผนฉาก", lambda: parse_json_reply(
                chat(plan_request(window, count, names, previous, era,
                                  float(project.get("clip_seconds") or 0) if video_mode else 0,
                                  horror=project.get("style_mode") == "เรื่องผี"))))
            items = [s for s in reply.get("scenes") or [] if isinstance(s, dict) and str(s.get("prompt") or "").strip()]
            window_start = 0.0 if w == 0 else window[0]["start"]
            seg_starts = [s["start"] for s in window]
            if w == 0:
                seg_starts = [0.0] + seg_starts
            by_start = {}
            for item in items:
                snapped = snap_starts([item.get("start")], seg_starts, window_start)
                # snap_starts always adds window_start; the item's own time is the other entry.
                own = [s for s in snapped if s != window_start] or [window_start]
                by_start.setdefault(own[0], item)
            for start in snap_starts(list(by_start), seg_starts, window_start):
                item = by_start.get(start) or (items[0] if items else {"prompt": window[0]["text"]})
                chars = [c for c in (item.get("characters") or []) if c in names]
                scenes.append({
                    "start": start, "characters": chars, "location": str(item.get("location") or ""),
                    "prompt": str(item.get("prompt") or "").strip(),
                    "video_prompt": str(item.get("video_prompt") or "").strip(),
                    "motion": item.get("motion") if item.get("motion") in MOTIONS else MOTIONS[len(scenes) % 4],
                    "highlight": bool(item.get("highlight")),
                })
            project["scenes"] = scenes
            project["planned_windows"] = w + 1
            save_project()
            ui(refresh_table)
        project.pop("planned_windows", None)
        target = int(project.get("image_count", 50))
        if len(scenes) > target:
            scenes = limit_scenes(scenes, target, float(project["duration"]))
            project["scenes"] = scenes
        project["continuity_done"] = False  # a new plan needs its own continuity record
        save_project()
        log(f"✓ วางแผน {len(scenes)} ฉาก (ไม่เกิน {target} รูป)")

    def scene_prompt(scene):
        context = state["project"].get("context") or {}
        details = {c.get("name"): look_of(c) for c in context.get("characters", [])}
        who = "; ".join(f"{n}: {details.get(n, '')}" for n in scene.get("characters") or [])
        location = f" สถานที่: {scene['location']}." if scene.get("location") else ""
        text = " ".join([scene.get("prompt", ""), " ".join(scene.get("characters") or []), scene.get("location", ""),
                         str(scene.get("text") or "")])
        ghosts = find_ghosts(text, state["project"].get("ghosts") or [])
        ghost_note = ("\nลักษณะผีตามความเชื่อไทย (ต้องวาดตามนี้ ห้ามเดาเอง): " + "; ".join(look for _n, look in ghosts)) if ghosts else ""
        state_note = (f"\nความต่อเนื่องจากช็อตก่อน (ต้องเห็นในภาพนี้ชัดเจน แม้รูปอ้างอิงจะไม่มี): {scene['continuity']}"
                      if scene.get("continuity") else "")
        bodies, forbid = shot_bodies(scene)
        body_note = (f"\nร่างกายที่ถูกต้อง (ห้ามผิด): {bodies}" if bodies else "") + \
                    (f"\nสิ่งที่ห้ามมีในภาพ: {forbid}" if forbid else "")
        same = state["project"].get("same_person") or {}
        forms = [f"{c} คือ {same[c]['person']} คนเดียวกัน (หน้าตาเดิม) ในร่าง {same[c]['how'] or 'ผี/วิญญาณ'}"
                 for c in scene.get("characters") or [] if c in same]
        if forms:
            location += "\nคนเดียวกันคนละร่าง: " + "; ".join(forms) + "."
        narration = (f"\nภาพนี้ประกอบคำบรรยายช่วงนี้เท่านั้น (ห้ามเพิ่มสิ่งที่บทไม่ได้พูดถึง): {scene['text']}"
                     if not video_mode and scene.get("text") else "")
        return (f"{scene['prompt']}{location}" + narration + (f"\nตัวละครในภาพ — {who}" if who else "") + state_note + ghost_note
                + body_note + f"\n{style_text()}")

    def shot_bodies(scene):
        """(body facts, negative) of everyone in one shot, from the story's body sheet.

        Characters missing from the sheet fall back to the built-in mythical bodies (e.g. พญานาค).
        """
        sheet = (state["project"] or {}).get("bodies") or {}
        if not sheet:  # no body sheet yet (or GPT failed): built-in bodies found anywhere in the shot
            return creature_bodies(" ".join([scene.get("prompt", ""), " ".join(scene.get("characters") or []),
                                             str(scene.get("text") or ""), str(scene.get("line") or "")]))
        facts, never, missing = [], [], []
        for name in scene.get("characters") or []:
            entry = sheet.get(name)
            if not entry:
                missing.append(name)
                continue
            fact = f"{name}" + (f" ({entry['kind']})" if entry.get("kind") else "") + f": {entry.get('body', '')}"
            # "moves" (e.g. "farms, carries her child, chops wood") is not added either: AI video
            # acted out every listed activity as extra scenes cut into the clip.
            # "forms" (e.g. a leg lost near the end) is not added here: every shot would show it from
            # the start. The continuity record puts a change only on the shots after it happens.
            facts.append(fact)
            never.append(entry.get("negative"))
        if missing:
            local, local_never = creature_bodies(" ".join(missing))
            facts.append(local)
            never.append(local_never)
        return "; ".join(f for f in facts if f), merge_forbid(*never)

    def ensure_bodies():
        """Once per story: GPT writes every character's body sheet (kind, body, moves, negative)."""
        project = state["project"]
        ensure_base_looks()
        if project.get("bodies") is not None or state.get("bodies_failed"):
            return
        names = [c.get("name") for c in (project.get("context") or {}).get("characters", []) if c.get("name")]
        if video_mode:  # เล่าภาพ never uses the Image AI attachment folder (it may hold another story)
            names += [n for n in attachment_names() if n not in names]
        if not names:
            return
        ensure_story_in_history()
        log("GPT กำลังทำใบร่างกายของทุกตัวละคร (มีอะไร ไม่มีอะไร ขยับยังไง สิ่งที่ห้ามมี) ...")
        era = ((project.get("context") or {}).get("story") or {}).get("era") or ""
        try:
            reply = with_retries("ใบร่างกายตัวละคร", lambda: parse_json_reply(chat(bodies_request(names, era))), attempts=2)
        except (Stopped, HistoryLost, RateLimited):
            raise
        except Exception as exc:
            state["bodies_failed"] = True  # not again this run; pictures still get the built-in bodies
            log(f"ทำใบร่างกายไม่สำเร็จ ({str(exc)[:120]}) — ใช้ข้อมูลร่างกายในตัวโปรแกรมแทน")
            return
        sheet = {}
        for item in reply.get("bodies") or []:
            if isinstance(item, dict) and str(item.get("name") or "").strip() and str(item.get("body") or "").strip():
                sheet[str(item["name"]).strip()] = {k: str(item.get(k) or "").strip()
                                                   for k in ("kind", "body", "moves", "negative", "forms")}
        project["bodies"] = sheet
        save_project()
        try:
            (Path(state["folder"]) / "character_bodies.json").write_text(
                json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        log(f"✓ ใบร่างกาย {len(sheet)} ตัว: " + ", ".join(f"{n} ({e.get('kind') or '-'})" for n, e in sheet.items()))

    def who_in(scene) -> str:
        context = state["project"].get("context") or {}
        details = {c.get("name"): look_of(c) for c in context.get("characters", [])}
        return "; ".join(f"{n}: {details.get(n, '')}".rstrip(": ") for n in scene.get("characters") or [])

    def ensure_scene_text():
        """เล่าภาพ plans keep only start times: give each picture the narration it covers (once)."""
        scenes = state["project"].get("scenes") or []
        transcript = Path(state["folder"]) / "transcript.json"
        if not scenes or any(sc.get("text") for sc in scenes) or not transcript.is_file():
            return
        segments = json.loads(transcript.read_text(encoding="utf-8"))
        ends = [sc["start"] for sc in scenes[1:]] + [float("inf")]
        for sc, end in zip(scenes, ends):
            sc["text"] = " ".join(s["text"] for s in segments if sc["start"] <= s["start"] < end)[:400]
        save_project()

    def story_anchor(index) -> str:
        """What this shot is in the story: narration, people, continuity, neighbours — keeps redraws on the script."""
        ensure_scene_text()
        scenes = state["project"]["scenes"]
        scene = scenes[index]
        rows = [f"ช็อต {index + 1} ของเรื่อง — คำบรรยาย: {scene.get('text') or '-'}"]
        if scene.get("dialogue"):
            rows.append(f"บทพูดของ {scene['dialogue']}: “{scene.get('line', '')}”")
        if scene.get("characters"):
            rows.append(f"ตัวละครในภาพ: {who_in(scene)}")
        if scene.get("location"):
            rows.append(f"สถานที่: {scene['location']}")
        if scene.get("continuity"):
            rows.append(f"สภาพต่อเนื่องที่ต้องคงไว้: {scene['continuity']}")
        bodies, forbid = shot_bodies(scene)
        if bodies:
            rows.append(f"ร่างกายที่ถูกต้อง: {bodies}")
        if forbid:
            rows.append(f"สิ่งที่ห้ามมี: {forbid}")
        if index > 0:
            rows.append(f"ช็อตก่อนหน้า: {str(scenes[index - 1].get('prompt') or '')[:200]}")
        if index + 1 < len(scenes):
            rows.append(f"ช็อตถัดไป: {str(scenes[index + 1].get('prompt') or '')[:200]}")
        return "\n".join(rows)

    def ensure_continuity():
        """Plans made before the continuity record: ask GPT for it once (text only, story history)."""
        project = state["project"]
        scenes = project.get("scenes") or []
        if scenes:
            ensure_base_looks()  # may hand later changes (e.g. a lost leg) over to the continuity record
        if not scenes or (project.get("continuity_done") and project.get("continuity_v") == CONTINUITY_VERSION):
            return
        set_progress("plan", 1.0, "GPT กำลังทำบันทึกความต่อเนื่อง (บาดแผล เลือด ร่างที่เปลี่ยน) ของทั้งเรื่อง ...")
        ensure_story_in_history()
        ensure_scene_text()
        names = [c.get("name") for c in (project.get("context") or {}).get("characters", []) if c.get("name")]
        if video_mode:  # เล่าภาพ never uses the Image AI attachment folder (it may hold another story)
            names += [n for n in attachment_names() if n not in names]
        reply = with_retries("บันทึกความต่อเนื่อง", lambda: parse_json_reply(
            chat(continuity_request(list(enumerate(scenes, 1)), names))))
        apply_continuity(scenes, reply.get("continuity"))
        project["continuity_done"] = True
        project["continuity_v"] = CONTINUITY_VERSION
        save_project()
        ui(refresh_table)
        count = sum(1 for sc in scenes if sc.get("continuity"))
        log(f"✓ บันทึกความต่อเนื่อง: {count} ช็อตมีสภาพที่ต้องต่อเนื่อง (เช่น บาดแผล) — ใส่ในคำสั่งรูปและคลิปให้อัตโนมัติ")

    def stage_storyboard():
        """Draw the shots as storyboard sheets (one per sequence, 3 x 3), then cut each sheet into panels.

        Each shot's picture is later drawn from its own panel, so the sheet's
        continuity (same faces, wounds, light, screen direction) carries over.
        """
        project = state["project"]
        check_script_matches_audio()
        ensure_story_in_history()
        ensure_continuity()
        ensure_bodies()
        scenes = project["scenes"]
        board_dir = Path(state["folder"]) / "storyboard"
        board_dir.mkdir(exist_ok=True)
        people = {} if video_mode else character_refs()  # เล่าภาพ: the story's own character pictures
        groups = [grp for grp in board_groups(scenes, project.get("direction"))
                  if any(not (scenes[i].get("image") and os.path.isfile(scenes[i]["image"])) for i in grp)]
        groups = [grp for grp in groups if not all(scenes[i].get("board") and os.path.isfile(scenes[i]["board"]) for i in grp)
                  and not any(scenes[i].get("board_skipped") for i in grp)]
        if not groups:
            log("สตอรี่ชีต: ไม่มีช็อตที่ต้องวาดรูปใหม่ — ข้าม")
            return
        for k, grp in enumerate(groups, 1):
            check_stop()
            first, last = grp[0] + 1, grp[-1] + 1
            set_progress("storyboard", (k - 1) / len(groups), f"วาดสตอรี่ชีต {k}/{len(groups)} (ช็อต {first}–{last})")
            refs = []
            for i in grp:
                found = (attachments_for(scenes[i]) if video_mode else
                         [people[c] for c in scenes[i].get("characters") or [] if c in people])
                refs += [p for p in found if p not in refs]
            name = f"sheet_{first:03d}-{last:03d}"

            def draw(grp=grp, refs=refs, name=name):
                return with_retries(f"สตอรี่ชีต ช็อต {first}–{last}", lambda: make_image(
                    board_request([(i + 1, scenes[i]) for i in grp], project["aspect"], live_action=video_mode)
                    + "\n" + style_text(),
                    refs[:6], board_dir, name, project["aspect"]))
            try:
                sheet = draw()
            except (Stopped, HistoryLost, RateLimited):
                raise
            except Exception as exc:
                # Usually ChatGPT refused the sheet (a violent beat): GPT rewrites these shots' prompts
                # once (same rules as a failed scene picture), then the sheet is drawn again right away.
                log(f"สตอรี่ชีต ช็อต {first}–{last} สร้างไม่ได้ ({str(exc)[:160]}) — "
                    "ให้ GPT แก้ prompt ของช็อตในชีตนี้แล้ววาดใหม่ทันที")
                for i in grp:
                    if not scenes[i].get("prompt_before_refine"):
                        set_progress("storyboard", (k - 1) / len(groups), f"GPT แก้ prompt ช็อต {i + 1} (สตอรี่ชีตถูกปฏิเสธ)")
                        try:
                            log(f"ช็อต {i + 1} prompt ใหม่: {refine_scene_prompt(i)[:160]}")
                        except Stopped:
                            raise
                        except Exception as fix_exc:
                            log(f"GPT แก้ prompt ช็อต {i + 1} ไม่สำเร็จ ({str(fix_exc)[:120]}) — ใช้ prompt เดิม")
                try:
                    sheet = draw()
                except (Stopped, HistoryLost, RateLimited):
                    raise
                except Exception as again:
                    # A sheet is only a layout guide: never stop the story for it.
                    for i in grp:
                        scenes[i]["board_skipped"] = True
                    save_project()
                    log(f"⚠ สตอรี่ชีต ช็อต {first}–{last} ยังสร้างไม่ได้ ({str(again)[:120]}) — "
                        "ข้ามชีตนี้ รูปฉากของช่วงนี้จะวาดโดยไม่มีสตอรี่ชีต")
                    continue
            panels = [board_dir / f"panel_{i + 1:03d}.png" for i in grp]
            crop_board(sheet, len(grp), panels)
            for i, panel in zip(grp, panels):
                scenes[i]["board"] = str(panel)
            save_project()
            log(f"✓ สตอรี่ชีต ช็อต {first}–{last}: {Path(sheet).name}")
        log(f"✓ สตอรี่ชีตครบ {len(groups)} ชีต (โฟลเดอร์ storyboard) — รูปฉากแต่ละช็อตจะวาดตามช่องของตัวเอง")

    def stage_images(indices=None):
        project = state["project"]
        check_script_matches_audio()
        project["images_verified"] = False
        ensure_story_in_history()
        ensure_continuity()
        ensure_bodies()
        research_ghosts()  # stories analysed before this feature
        images_dir = Path(state["folder"]) / "images"
        images_dir.mkdir(exist_ok=True)
        refs = character_refs()
        scenes = project["scenes"]
        todo = indices if indices is not None else [
            i for i, s in enumerate(scenes) if not (s.get("image") and os.path.isfile(s["image"]))]
        started, failures_in_row = time.time(), 0
        for n, i in enumerate(todo, 1):
            check_stop()
            scene = scenes[i]
            eta = ""
            if n > 1:
                remaining = (time.time() - started) / (n - 1) * (len(todo) - n + 1)
                eta = f" · เหลือประมาณ {int(remaining // 60)} นาที"
            set_progress("images", (n - 1) / max(1, len(todo)), f"สร้างรูปฉาก {n}/{len(todo)} (ฉากที่ {i + 1}){eta}")
            if video_mode:
                ref_paths = attachments_for(scene)
            else:
                same = project.get("same_person") or {}
                shown = list(scene.get("characters") or [])
                # A ghost form also gets its person's picture, so the face stays the same person.
                shown += [same[c]["person"] for c in shown if c in same and same[c]["person"] not in shown]
                ref_paths = [refs[c] for c in shown if c in refs][:4]
            try:
                board = scene.get("board") if scene.get("board") and os.path.isfile(scene["board"]) else None
                scene["image"] = with_retries(f"ฉาก {i + 1}", lambda: make_image(
                    scene_prompt(scene), ref_paths, images_dir, f"scene_{i + 1:03d}", project["aspect"], board=board))
                if ref_paths and looks_like_reference_sheet(scene["image"]):
                    # The picture came back as a copy of a reference (plain backdrop): draw the scene again
                    # without attachments so every shot is a real scene picture.
                    log(f"ฉาก {i + 1} ออกมาเหมือนรูปอ้างอิง (พื้นหลังเรียบ) — สร้างฉากใหม่")
                    scene["image"] = with_retries(f"ฉาก {i + 1}", lambda: make_image(
                        scene_prompt(scene) + "\nภาพฉากจริงที่มีฉากหลังและบรรยากาศตามเรื่อง ห้ามพื้นหลังสีเรียบ",
                        [], images_dir, f"scene_{i + 1:03d}", project["aspect"]))
                scene.pop("error", None)
                scene.pop("bad", None)
                failures_in_row = 0
            except (Stopped, HistoryLost, RateLimited):
                raise
            except Exception as exc:
                if scene.get("refine_count", 0) < 2:
                    # Usually a refused prompt (e.g. violence, a child in danger): GPT softens it (up to
                    # twice per scene) and the picture is drawn again right away.
                    try:
                        log(f"ฉาก {i + 1} เจนไม่ได้ ({str(exc)[:120]}) — ให้ GPT แก้ prompt ให้เจนได้แล้วลองใหม่")
                        refine_scene_prompt(i)
                        if scene.get("refine_count", 0) >= 2:
                            board = None  # the panel itself may carry what was refused
                        scene["image"] = make_image(scene_prompt(scene), ref_paths, images_dir, f"scene_{i + 1:03d}",
                                                    project["aspect"], board=board)
                        scene.pop("error", None)
                        scene.pop("bad", None)
                        failures_in_row = 0
                        save_project()
                        ui(refresh_table)
                        continue
                    except (Stopped, HistoryLost, RateLimited):
                        raise
                    except Exception as retry_exc:
                        exc = retry_exc
                scene["error"] = str(exc)[:300]
                failures_in_row += 1
                if failures_in_row >= 3:
                    save_project()
                    raise RuntimeError("สร้างรูปล้มเหลว 3 ฉากติดกัน (เครดิตหมดหรือ Bridge มีปัญหา) — แก้แล้วกดเริ่มเพื่อทำต่อ")
            save_project()
            ui(refresh_table)
        missing = [i + 1 for i, s in enumerate(scenes) if not (s.get("image") and os.path.isfile(s["image"]))]
        if missing and indices is None:
            # One more pass by itself over the scenes that failed (their prompts were already softened).
            log(f"ลองสร้างฉากที่ยังขาดอีกรอบอัตโนมัติ: {', '.join(map(str, missing[:15]))}")
            stage_images([n - 1 for n in missing])
            missing = [i + 1 for i, s in enumerate(scenes) if not (s.get("image") and os.path.isfile(s["image"]))]
        if missing:
            raise RuntimeError(f"ยังขาดรูปฉาก {', '.join(map(str, missing[:15]))} — กดเริ่มอีกครั้งเพื่อลองใหม่")
        log(f"✓ รูปครบ {len(scenes)} ฉาก")
        if indices is None:
            verify_images()

    def gpt_text(prompt: str) -> str:
        """One question to GPT in a temporary chat (keeps the story history clean)."""
        body = {"model": "auto", "temporary_chat": True, "chatgpt_image_intercept": False,
                "messages": [{"role": "user", "content": prompt}]}
        request = urllib.request.Request(
            g["_chatgpt_api_base"]() + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json; charset=utf-8"},
            method="POST")
        with g.get("_bridge_queue_lock") or threading.Lock():
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        message = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return str(message)

    def refine_scene_prompt(index) -> str:
        """Ask GPT to rewrite one scene prompt so the image generator accepts it, keeping the story beat."""
        project = state["project"]
        scene = project["scenes"][index]
        ensure_bodies()
        reply = gpt_text(
            "prompt ภาพนี้ใช้สร้างรูปประกอบเรื่องเล่าไทยไม่ผ่าน (ตัวสร้างรูปปฏิเสธ หรือไม่ยอมวาด). "
            "เขียน prompt ใหม่ภาษาไทยให้สร้างรูปได้ โดยยังเป็นช็อตเดิมของเรื่องเดิม: ตัวละครเดิม (หน้าตา รูปร่าง ชุด) "
            "สถานที่เดิม เหตุการณ์เดิมตามคำบรรยาย และต่อเนื่องกับช็อตก่อน/หลัง — ห้ามเปลี่ยนเป็นเรื่องอื่น ห้ามแปลงร่างตัวละคร "
            "ห้ามเพิ่มตัวละครใหม่ คงองค์ประกอบ มุมกล้อง และตำแหน่งตัวละครเดิม (ตามสตอรี่บอร์ด) "
            "แก้เฉพาะวิธีเล่าภาพที่ทำให้ถูกปฏิเสธ. " + SCOPE_RULE + "หลักการ: "
            "ถ้ามีเด็ก ห้ามให้เด็กดูตกอยู่ในอันตรายหรือถูกคุกคาม — ให้สิ่งน่ากลัวอยู่ห่าง เห็นแค่บางส่วน ถ่ายเด็กจากด้านหลังหรือไกลๆ "
            "อารมณ์เด็กเป็นสงสัย/ชะงักแทนหวาดกลัว หรือทำเป็นภาพแทรกที่ไม่มีเด็กในเฟรม; "
            "ความรุนแรง เลือด บาดแผล ให้เปลี่ยนเป็นนัยหรือเอฟเฟกต์ภาพยนตร์; ฉากและชุดเป็นแบบไทย; ภาพเดียวเต็มเฟรม ไม่มีตัวหนังสือ; "
            "ถ้า prompt เดิมเป็นภาพคนเล่าเรื่อง/เจ้าของช่อง/ไมโครโฟน/ห้องอัด ให้เปลี่ยนเป็นภาพเหตุการณ์หรือสถานที่สำคัญจากในเรื่องแทน. "
            "ตอบ JSON เท่านั้น {\"prompt\":\"...\"}\n\n"
            f"{story_anchor(index)}\nprompt เดิม: {scene.get('prompt', '')}")
        new = str((parse_json_reply(reply) or {}).get("prompt") or "").strip()
        if not new:
            raise RuntimeError("GPT ไม่ได้ส่ง prompt ใหม่กลับมา")
        scene.setdefault("prompt_before_refine", scene.get("prompt", ""))
        scene["prompt"] = new
        scene["refine_count"] = scene.get("refine_count", 0) + 1
        save_project()
        return new

    def refine_selected():
        project = state["project"]
        selected = sorted(int(i) for i in table.selection())
        if not project or not selected:
            messagebox.showinfo("เล่าภาพ", "เลือกฉากในตารางก่อน (คลิกแถว กด Ctrl เพื่อเลือกหลายฉาก)", parent=page)
            return
        if state["busy"]:
            messagebox.showinfo("เล่าภาพ", "กำลังทำงานอยู่ — รอให้เสร็จ หรือกดหยุดก่อน", parent=page)
            return
        state["busy"], state["stop"] = True, False

        def worker():
            done = []
            try:
                for i in selected:
                    set_progress("images", 0, f"GPT กำลังแก้ prompt ฉาก {i + 1}")
                    log(f"ฉาก {i + 1} prompt ใหม่: {refine_scene_prompt(i)}")
                    done.append(i)
            except Exception as exc:
                log(f"❌ GPT แก้ prompt ไม่สำเร็จ: {exc}")
            finally:
                state["busy"] = False
                ui(refresh_all)
            if done:
                def ask():
                    if messagebox.askyesno("เล่าภาพ", f"แก้ prompt แล้ว {len(done)} ฉาก (ดูได้ในตาราง/ดับเบิลคลิก) — เจนรูปใหม่เลยไหม?", parent=page):
                        regenerate_selected(done, ask=False)
                ui(ask)
        threading.Thread(target=worker, daemon=True).start()

    def gpt_image_problems(scenes):
        """One look by GPT at a numbered sheet of every scene (temporary chat, not the story history)."""
        story = (state["project"].get("context") or {}).get("story") or {}
        place = story.get("main_location") or ""
        prompt = IMAGE_CHECK_PROMPT.format(total=sum(1 for s in scenes if s.get("image") and os.path.isfile(s["image"])), abroad=f" — สถานที่หลักของเรื่อง: {place}" if place else "")
        body = {"model": "auto", "temporary_chat": True, "chatgpt_image_intercept": False, "messages": [{
            "role": "user", "content": [{"type": "text", "text": prompt},
                                        {"type": "image_url", "image_url": {"url": contact_sheet_data_url(scenes)}}]}]}
        request = urllib.request.Request(
            g["_chatgpt_api_base"]() + "/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json"}, method="POST")
        with g.get("_bridge_queue_lock") or threading.Lock():
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        found = {}
        for item in (parse_json_reply(str(reply)) or {}).get("bad") or []:
            try:
                n = int(item.get("scene")) - 1
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= n < len(scenes):
                found[n] = str(item.get("reason") or "GPT ตรวจว่าใช้ไม่ได้")[:80]
        return found

    def verify_images(rounds=3):
        """Before the video: find broken scene pictures, mark them red and draw them again until they pass."""
        project = state["project"]
        scenes = project["scenes"]
        for round_no in range(1, rounds + 2):
            check_stop()
            set_progress("images", 1.0, f"ตรวจรูปทุกฉากก่อนตัดต่อ (รอบ {round_no})")
            problems = local_image_problems(scenes, project["aspect"])
            try:
                for i, reason in gpt_image_problems(scenes).items():
                    problems.setdefault(i, reason)
            except Exception as exc:
                log(f"GPT ตรวจรูปไม่สำเร็จ ({str(exc)[:120]}) — ใช้ผลตรวจในเครื่องอย่างเดียว")
            for i, scene in enumerate(scenes):
                scene.pop("bad", None)
                if i in problems:
                    scene["bad"] = problems[i]
            save_project()
            ui(refresh_table)
            if not problems:
                project["images_verified"] = True
                save_project()
                log("✓ ตรวจรูปแล้ว ใช้ได้ทุกฉาก")
                try:
                    board = save_storyboard(scenes, Path(state["folder"]) / "storyboard_ทุกฉาก.jpg")
                    if board:
                        log(f"บันทึกรูปรวมทุกฉาก: {Path(board).name}")
                except Exception as exc:
                    log(f"ทำรูปรวมทุกฉากไม่สำเร็จ: {str(exc)[:120]}")
                return
            if round_no > rounds:
                break
            log("รูปที่ต้องเจนใหม่: " + ", ".join(f"ฉาก {i + 1} ({r})" for i, r in sorted(problems.items())))
            for i in problems:
                image = scenes[i].get("image")
                if image and os.path.isfile(image):
                    import hashlib
                    rejected = project.setdefault("rejected_hashes", [])
                    digest = hashlib.md5(Path(image).read_bytes()).hexdigest()
                    if digest not in rejected:
                        rejected.append(digest)
                    os.remove(image)
                scenes[i]["image"] = None
                scenes[i]["prompt"] = scenes[i].get("prompt", "") if "ภาพเดียวเต็มเฟรม" in scenes[i].get("prompt", "") else \
                    scenes[i].get("prompt", "") + " (ภาพเดียวเต็มเฟรม ไม่แบ่งช่อง ฉากแบบไทย)"
            stage_images(sorted(problems))
        left = [i + 1 for i, s in enumerate(scenes) if s.get("bad")]
        raise RuntimeError(f"รูปฉาก {', '.join(map(str, left))} ยังใช้ไม่ได้หลังเจนใหม่ {rounds} รอบ — "
                           "เลือกฉากแล้วกดสร้างรูปใหม่ หรือกดเริ่มอีกครั้ง")

    def stage_video():
        if not state["project"].get("images_verified"):
            verify_images()
        project, folder = state["project"], Path(state["folder"])
        ffmpeg = ffmpeg_path()
        width, height = SIZES.get(project["aspect"], SIZES["16:9"])
        scenes = project["scenes"]
        work = folder / "_render"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        duration = float(project["duration"])
        durations = segment_durations([s["start"] for s in scenes], duration)
        # Each shot runs on under the next one for as long as that transition lasts.
        kinds = [transition_into(scenes[i - 1] if i else None, s) for i, s in enumerate(scenes)]
        overlaps = [TRANSITIONS[kinds[i + 1]][1] for i in range(len(scenes) - 1)] + [0.0]
        lengths = [d + overlaps[i] for i, d in enumerate(durations)]
        encoder_label, encoder_args = video_encoder(ffmpeg)
        log(f"เข้ารหัสวิดีโอด้วย {encoder_label}")
        encode = [*encoder_args, "-pix_fmt", "yuv420p", "-r", str(FPS)]
        # Filters (zoom/pan, crossfade) run on the CPU: use at most half the
        # cores so the rest of the PC stays responsive.
        threads = str(max(2, (os.cpu_count() or 4) // 2))
        limit = ["-filter_threads", threads, "-filter_complex_threads", threads]

        def run(args):
            proc = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *limit, *args],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  creationflags=LOW_PRIORITY)
            if proc.returncode != 0:
                raise RuntimeError("FFmpeg: " + (proc.stderr or "").strip()[-500:])

        def render_one(i, scene, length):
            check_stop()
            frames = max(1, round(length * FPS))
            clip = work / f"clip_{i:04d}.mp4"
            if video_mode:
                source, fit = fitted_clip(scene, length, width, height)
                run(["-i", source, "-an", "-vf", fit, "-frames:v", str(frames), *encode, str(clip)])
            else:
                still = STYLES.get(project.get("style_mode") or "ปกติ", STYLES["ปกติ"])["still"]
                motion = "still" if still else scene.get("motion", "zoom_in")
                run(["-i", scene["image"], "-vf", zoompan_filter(motion, frames, width, height),
                     "-frames:v", str(frames), *encode, str(clip)])
            return clip

        # The zoom/pan filter uses one core per scene: render a few scenes at
        # once (about a third of the cores, low priority) instead of one.
        from concurrent.futures import ThreadPoolExecutor
        workers = max(1, (os.cpu_count() or 4) // 3)
        done_count = [0]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(render_one, i, scene, length) for i, (scene, length) in enumerate(zip(scenes, lengths))]
            for future in futures:
                future.result()
                done_count[0] += 1
                set_progress("video", 0.75 * done_count[0] / len(scenes), f"ทำภาพเคลื่อนไหว {done_count[0]}/{len(scenes)}")
        clips = [(work / f"clip_{i:04d}.mp4", length) for i, length in enumerate(lengths)]

        # Crossfade in groups of 20, then crossfade the groups together.
        def crossfade(items, out, item_kinds):
            graph, total = xfade_graph([length for _c, length in items], kinds=item_kinds)
            args = []
            for clip, _length in items:
                args += ["-i", str(clip)]
            run([*args, "-filter_complex", graph, "-map", "[vout]", *encode, str(out)])
            return out, total

        set_progress("video", 0.8, "ต่อภาพแบบจางซ้อน / มืดลงเปลี่ยนฉาก ...")
        starts = list(range(0, len(clips), 20))
        groups = [crossfade(clips[k:k + 20], work / f"group_{k:04d}.mp4", kinds[k:k + 20]) for k in starts]
        check_stop()
        picture, _total = (crossfade(groups, work / "picture.mp4", [kinds[k] for k in starts])
                           if len(groups) > 1 else groups[0])

        set_progress("video", 0.92, "ใส่เสียงบรรยาย" + (" และซับไตเติล" if project.get("subtitles") else "") + " ...")
        final = folder / f"{safe_name(Path(project['script']).stem)}_{work_dir_name}_{time.strftime('%Y%m%d-%H%M')}.mp4"
        audio_args = ["-i", project["audio"], "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest",
                      "-movflags", "+faststart"]
        if project.get("subtitles"):
            segments = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
            (work / "subs.ass").write_text(build_ass(segments, width, height), encoding="utf-8")
            fonts = Path(__file__).resolve().parent.parent / "assets" / "fonts"
            if fonts.is_dir():
                shutil.copytree(fonts, work / "fonts", dirs_exist_ok=True)
            # Run from the render folder with relative paths: FFmpeg filter
            # syntax cannot take a Windows drive path ("C:") without escaping.
            proc = subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *limit, "-i", str(picture), *audio_args[:6],
                 "-vf", "ass=subs.ass:fontsdir=fonts",
                 *encode, *audio_args[6:], str(final)],
                cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=LOW_PRIORITY)
            if proc.returncode != 0:
                raise RuntimeError("FFmpeg ซับไตเติล: " + (proc.stderr or "").strip()[-500:])
        else:
            run(["-i", str(picture), *audio_args[:6], "-c:v", "copy", *audio_args[6:], str(final)])
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(folder / "_preview", ignore_errors=True)
        project["last_video"] = str(final)
        log(f"✓ วิดีโอเสร็จ: {final}")

    def update_preview():
        """Video mode, after each clip: clips made so far from shot 1 + their narration → a watchable MP4.

        Each fitted clip is rendered once and kept; joining them is a stream
        copy, so this takes seconds. Plain cuts here; the final video crossfades.
        """
        project, folder = state["project"], Path(state["folder"])
        scenes = project["scenes"]
        ready = 0
        while ready < len(scenes) and scenes[ready].get("clip") and os.path.isfile(scenes[ready]["clip"]):
            ready += 1
        if not ready:
            return
        ffmpeg = ffmpeg_path()
        width, height = SIZES.get(project.get("aspect"), SIZES["16:9"])
        lengths = segment_durations([s["start"] for s in scenes], float(project["duration"]))
        encode = [*video_encoder(ffmpeg)[1], "-pix_fmt", "yuv420p", "-r", str(FPS)]
        work = folder / "_preview"
        work.mkdir(exist_ok=True)

        def run(args):
            proc = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args], cwd=str(work),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  creationflags=LOW_PRIORITY)
            if proc.returncode != 0:
                raise RuntimeError("FFmpeg: " + (proc.stderr or "").strip()[-300:])

        names = []
        for i in range(ready):
            source = scenes[i]["clip"]
            part, stamp = work / f"part_{i:04d}.mp4", work / f"part_{i:04d}.key"
            key = (f"{source}|{os.path.getmtime(source)}|{lengths[i]}|{width}x{height}"
                   f"|{scenes[i].get('clip_use_until')}")
            if not (part.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == key):
                run(["-i", source, "-an", "-vf", fitted_clip(scenes[i], lengths[i], width, height)[1],
                     "-frames:v", str(max(1, round(lengths[i] * FPS))), *encode, part.name])
                stamp.write_text(key, encoding="utf-8")
            names.append(part.name)
        (work / "list.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
        out = folder / f"ตัวอย่าง_ถึงช็อต_{ready:03d}.mp4"
        run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-i", project["audio"], "-t", f"{sum(lengths[:ready]):.3f}",
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             str(out)])
        for old in folder.glob("ตัวอย่าง_ถึงช็อต_*.mp4"):
            if old != out:
                try:
                    old.unlink()
                except OSError:
                    pass  # open in a player: leave it
        project["last_video"] = str(out)
        log(f"▶ ตัวอย่างถึงช็อต {ready}/{len(scenes)} ({fmt_time(sum(lengths[:ready]))}) — กด 'เปิดวิดีโอ' ดูได้")

    def run_on_ui(fn):
        """Run fn on the Tk thread and return its result to this worker."""
        result, event = {}, threading.Event()

        def call():
            try:
                result["value"] = fn()
            except Exception as exc:
                result["error"] = exc
            finally:
                event.set()
        root.after(0, call)
        event.wait()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def video_output_dir() -> Path:
        value = runtime.get("EXPORT_VIDEO") or g.get("EXPORT_VIDEO")
        return Path(str(value)) if value else export_root() / "video"

    def set_slot_config(cfg, model, duration, aspect):
        """Set the Slot's model, length and picture shape (Tk thread).

        Changing the model makes the Slot queue an idle reset of its shape to
        'อัตโนมัติ'; run that first so it cannot overwrite the shape set here
        (grok-lower rejects 'อัตโนมัติ' as an aspect ratio).
        """
        if str(cfg["model"].get() or "").strip() != model:
            cfg["model"].set(model)
            page.update_idletasks()
        cfg["duration"].set(duration)
        cfg["aspect"].set(aspect)

    def motion_items(indices):
        scenes = state["project"]["scenes"]
        items = []
        for i in indices:
            scene = scenes[i]
            bodies, _forbid = shot_bodies(scene)
            items.append({
                "shot": i + 1, "seconds": scene.get("clip_seconds") or scene.get("shot_seconds"),
                "line": scene.get("text"), "characters": who_in(scene),
                "before": str(scenes[i - 1].get("video_prompt") or scenes[i - 1].get("prompt") or "")[:220] if i > 0 else "",
                "after": str(scenes[i + 1].get("prompt") or "")[:220] if i + 1 < len(scenes) else "",
                "planned": scene.get("video_prompt_planned") or scene.get("video_prompt") or "",
                "continuity": scene.get("continuity"),
                "dialogue": f"{scene['dialogue']} พูดว่า “{scene.get('line', '')}”" if scene.get("dialogue") else "",
                "bodies": bodies, "notes": "; ".join(scene.get("fix_notes") or [])})
        return items

    def stage_motion(indices=None):
        """After the pictures: GPT looks at each finished first frame with the script and writes its video prompt.

        A shot is written again only when its picture changed since (or never had one).
        The planning-time prompt is kept as video_prompt_planned.
        """
        project = state["project"]
        scenes = project["scenes"]
        pool = indices if indices is not None else [
            i for i, sc in enumerate(scenes) if not (sc.get("clip") and os.path.isfile(sc["clip"]))]
        todo = [i for i in pool if scenes[i].get("image") and os.path.isfile(scenes[i]["image"])
                and scenes[i].get("motion_image") != file_digest(scenes[i]["image"])]
        if not todo:
            return
        ensure_story_in_history()
        ensure_continuity()
        ensure_bodies()
        assign_clips(scenes, float(project["duration"]), economy_on())
        era = ((project.get("context") or {}).get("story") or {}).get("era") or "-"
        horror = project.get("style_mode") == "เรื่องผี"
        batches = [todo[k:k + MOTION_BATCH] for k in range(0, len(todo), MOTION_BATCH)]
        for b, batch in enumerate(batches, 1):
            check_stop()
            shots = ", ".join(str(i + 1) for i in batch)
            set_progress("motion", (b - 1) / len(batches),
                         f"GPT ดูรูปและบท เขียนพรอมต์วิดีโอ {b}/{len(batches)} (ช็อต {shots})")
            request = motion_request(motion_items(batch), era, horror)
            reply = None
            if not state.get("motion_text_only"):
                content = [{"type": "text", "text": request}]
                content += [{"type": "image_url", "image_url": {"url": image_data_url(scenes[i]["image"])}} for i in batch]
                try:
                    reply = parse_json_reply(chat(content))
                except (Stopped, HistoryLost, RateLimited):
                    raise
                except Exception as exc:
                    # Pictures could not be sent: for the rest of this run write from the script and image prompt.
                    state["motion_text_only"] = True
                    log(f"ส่งรูปให้ GPT ดูไม่ได้ ({str(exc)[:120]}) — เขียนพรอมต์วิดีโอจากบทและคำสั่งรูปแทน")
            if reply is None:
                text = request + "\n\n(ไม่มีรูปแนบ: ภาพแรกของแต่ละช็อตคือ) " + " | ".join(
                    f"ช็อต {i + 1}: {str(scenes[i].get('prompt') or '')[:300]}" for i in batch)
                reply = with_retries(f"พรอมต์วิดีโอ ช็อต {shots}", lambda text=text: parse_json_reply(chat(text)))
            for item in reply.get("shots") or []:
                try:
                    i = int(item.get("shot")) - 1
                except (TypeError, ValueError, AttributeError):
                    continue
                prompt = str(item.get("video_prompt") or "").strip()
                if i not in batch or not prompt:
                    continue
                scene = scenes[i]
                scene.setdefault("video_prompt_planned", scene.get("video_prompt") or "")
                scene["video_prompt"] = prompt
                scene["anatomy"] = str(item.get("anatomy") or "").strip()
                scene["forbid"] = merge_forbid(scene.get("forbid"), item.get("negative"), item.get("forbid"))
                scene["motion_image"] = file_digest(scene["image"])
            missed = [i + 1 for i in batch if scenes[i].get("motion_image") != file_digest(scenes[i]["image"])]
            if missed:
                log(f"พรอมต์วิดีโอ: GPT ไม่ได้ส่งช็อต {', '.join(map(str, missed))} — ใช้พรอมต์จากแผนเดิม")
            save_project()
            ui(refresh_table)
        log(f"✓ พรอมต์วิดีโอจากรูปจริง + บท: {len(todo)} ช็อต")

    def clip_prompt(scene) -> str:
        """Everything the AI video model receives for one shot, in the order it reads it."""
        prompt = (scene.get("video_prompt") or scene["prompt"]).strip()
        bodies, body_forbid = shot_bodies(scene)
        # The body sheet always (GPT's per-shot summary may leave someone out), then what GPT saw in this frame.
        note = str(scene.get("clip_anatomy") if scene.get("clip_anatomy") is not None else scene.get("anatomy") or "")
        anatomy = "; ".join(x for x in (bodies, f"ในช็อตนี้: {note}" if note and note[:40] not in bodies else "") if x)
        if anatomy:  # stated early: video models weigh the opening words most
            prompt = f"ร่างกายที่ต้องคงไว้ตลอดคลิป: {anatomy}\n{prompt}"
        prompt = ONE_SHOT_RULE + "\n" + prompt
        if scene.get("dialogue"):
            prompt += (f"\n{scene['dialogue']} พูดว่า “{scene.get('line', '')}” ขยับปากพูดด้วยความเร็วปกติตลอดคลิป "
                       "ไม่มีตัวหนังสือในภาพ ตัวละครหน้าตาเหมือนในภาพเริ่มต้นตลอดคลิป")
        else:
            prompt += "\nไม่มีบทพูด ไม่มีตัวหนังสือในภาพ ตัวละครหน้าตาเหมือนในภาพเริ่มต้นตลอดคลิป"
        prompt += "\nภาพสมจริงแบบภาพยนตร์ไลฟ์แอ็กชัน ไม่ใช่การ์ตูนหรืออนิเมะ"
        continuity = scene.get("clip_continuity") if scene.get("clip_continuity") is not None else scene.get("continuity")
        if continuity:  # wounds, blood, wet, transformed ... carried from earlier shots
            prompt += f"\nความต่อเนื่อง (สภาพที่เห็นบนตัว คงไว้ตลอดคลิป): {continuity}"
        # Negative prompt: this shot's (GPT + 🛠 problems seen) + each body's + what AI video always breaks.
        prompt += f"\nNegative — ห้ามปรากฏเด็ดขาดตลอดทั้งคลิป: {merge_forbid(scene.get('forbid'), body_forbid, VIDEO_NEGATIVE)}"
        return prompt

    def gpt_look(text: str, images=()) -> dict:
        """One question with pictures to GPT in a temporary chat (keeps the story history clean); JSON reply."""
        content = [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": url}} for url in images if url]
        body = {"model": "auto", "temporary_chat": True, "chatgpt_image_intercept": False,
                "messages": [{"role": "user", "content": content}]}
        request = urllib.request.Request(
            g["_chatgpt_api_base"]() + "/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json"}, method="POST")
        with g.get("_bridge_queue_lock") or threading.Lock():
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        return parse_json_reply(str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")) or {}

    def review_clip_prompt(scene, index):
        """Checker 1, before credits: a separate GPT reads the whole request + the start frame and passes or fixes it."""
        import hashlib
        key = hashlib.md5((clip_prompt(scene) + str(scene.get("image"))).encode("utf-8")).hexdigest()
        if (scene.get("prompt_review") or {}).get("key") == key:
            return
        for attempt in (1, 2):
            check_stop()
            set_progress("clips", state.get("clip_fraction", 0), f"ผู้ตรวจ GPT ตรวจคำสั่งช็อต {index + 1} (รอบ {attempt})")
            try:
                reply = gpt_look(
                    PROMPT_CHECK.format(seconds=scene.get("clip_seconds") or "", anchor=story_anchor(index),
                                        request=clip_prompt(scene)),
                    [image_data_url(scene["image"])] if scene.get("image") and os.path.isfile(scene["image"]) else [])
            except (Stopped, HistoryLost, RateLimited):
                raise
            except Exception as exc:
                log(f"ผู้ตรวจคำสั่งช็อต {index + 1} ใช้ไม่ได้ ({str(exc)[:120]}) — ส่งคำสั่งเดิม")
                return
            problems = [str(p) for p in reply.get("problems") or [] if str(p).strip()]
            passed = reply.get("pass") is True or str(reply.get("pass")).lower() == "true"
            if passed or not str(reply.get("video_prompt") or "").strip():
                break
            log(f"🔎 ผู้ตรวจ: ช็อต {index + 1} ไม่ผ่าน — {'; '.join(problems)[:300]} → แก้คำสั่งแล้ว")
            scene.setdefault("video_prompt_before_review", scene.get("video_prompt", ""))
            scene["video_prompt"] = str(reply["video_prompt"]).strip()
            scene["clip_anatomy"] = str(reply.get("anatomy") or "").strip()
            scene["clip_continuity"] = str(reply.get("continuity") or "").strip()
            save_project()
        else:
            passed = False
        scene["prompt_review"] = {"key": hashlib.md5((clip_prompt(scene) + str(scene.get("image"))).encode("utf-8")).hexdigest(),
                                  "pass": passed, "problems": problems}
        save_project()
        if passed:
            log(f"🔎 ผู้ตรวจ: คำสั่งช็อต {index + 1} ผ่าน")

    def detect_clip_cuts(path) -> list:
        """Seconds (in the file) where the picture jumps to another scene: large change within half a second."""
        import numpy as np
        raw = subprocess.run([ffmpeg_path(), "-loglevel", "error", "-i", str(path), "-vf",
                              "fps=8,scale=64:36,format=rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, creationflags=NO_WINDOW).stdout
        frames = np.frombuffer(raw, np.uint8)
        if frames.size < 64 * 36 * 3 * 6:
            return []
        frames = frames[: frames.size // (64 * 36 * 3) * (64 * 36 * 3)].reshape(-1, 36, 64, 3).astype(np.float32)
        diff = np.abs(frames[4:] - frames[:-4]).mean(axis=(1, 2, 3))
        cuts = []
        for k, value in enumerate(diff):
            if value > CUT_THRESHOLD and value == diff[max(0, k - 8):k + 9].max():
                at = round((k + 2) / 8, 2)
                if 0.4 < at < len(frames) / 8 - 0.4:
                    cuts.append(at)
        return cuts

    def frames_sheet_url(path, seconds: float) -> tuple:
        """(data URL of a 1-row-per-4 sheet of frames, seconds between frames)."""
        step = 1.0 if seconds <= 13 else 2.0
        sheet = Path(state["folder"]) / "_review_sheet.jpg"
        subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(path), "-vf",
                        f"fps=1/{step},scale=320:-2,tile=5x4", "-frames:v", "1", str(sheet)],
                       capture_output=True, creationflags=NO_WINDOW)
        try:
            return image_data_url(sheet, max_side=1600), step
        finally:
            try:
                sheet.unlink()
            except OSError:
                pass

    def review_clip(scene, index):
        """Checker 2, after the clip: local cut detection + a GPT look at the frames → pass / trim / redo."""
        clip = scene.get("clip")
        if not (clip and os.path.isfile(clip)):
            return
        length = media_duration(clip)
        cuts = detect_clip_cuts(clip)
        scene["clip_cuts"] = cuts
        verdict, reason, until = ("trim", f"ตัดฉากเองที่วินาที {cuts[0]:g}", cuts[0] - 0.15) if cuts else ("pass", "", None)
        try:
            url, step = frames_sheet_url(clip, length)
            reply = gpt_look(CLIP_CHECK.format(step=step, length=round(length, 1), cuts=", ".join(f"{c:g}" for c in cuts) or "ไม่พบ",
                                               anchor=story_anchor(index), request=clip_prompt(scene)),
                             [url] + ([image_data_url(scene["image"])] if scene.get("image") and os.path.isfile(scene["image"]) else []))
            answer = str(reply.get("verdict") or "").strip().lower()
            if answer in ("pass", "trim", "redo"):
                verdict, reason = answer, str(reply.get("reason") or reason).strip()
                if answer == "trim":
                    try:
                        until = float(reply.get("use_until") or until or 0) or until
                    except (TypeError, ValueError):
                        pass
                elif answer == "pass" and cuts:
                    until = None  # a camera cut inside the same scene: GPT says keep it all
        except (Stopped, HistoryLost, RateLimited):
            raise
        except Exception as exc:
            log(f"ผู้ตรวจคลิปช็อต {index + 1} ใช้ไม่ได้ ({str(exc)[:120]}) — ใช้ผลตรวจในเครื่อง")
        if verdict == "redo" and cuts:
            until = cuts[0] - 0.15  # until it is made again, use only the part before the jump
        scene["clip_review"] = {"verdict": verdict, "reason": reason[:200]}
        if until and 0.5 < until < length:
            scene["clip_use_until"] = round(until, 2)
        else:
            scene.pop("clip_use_until", None)
        save_project()
        mark = {"pass": "✓ ผ่าน", "trim": f"✂ ใช้ถึงวินาที {scene.get('clip_use_until', '-')}", "redo": "⚠ ควรเจนใหม่"}[verdict]
        log(f"🔎 ผู้ตรวจคลิปช็อต {index + 1}: {mark}" + (f" — {reason}" if reason else ""))

    def fitted_clip(scene, length, width, height) -> tuple:
        """(source, filter) of a shot's clip fitted to its slot, using only the part the checker kept."""
        source = scene["clip"]
        usable = media_duration(source)
        until = float(scene.get("clip_use_until") or 0)
        head = ""
        if 0.5 < until < usable:
            head, usable = f"trim=duration={until:.3f},setpts=PTS-STARTPTS,", until
        return source, head + clip_fit_filter(usable, length, width, height)

    def review_clips_selected():
        """Button: run checker 2 on the selected shots (all shots when none selected)."""
        project = state["project"]
        if not project or state["busy"]:
            return
        selected = sorted(int(i) for i in table.selection()) or list(range(len(project.get("scenes") or [])))
        state["busy"], state["stop"] = True, False

        def worker():
            try:
                for n, i in enumerate(selected, 1):
                    check_stop()
                    set_progress("clips", (n - 1) / max(1, len(selected)), f"ผู้ตรวจคลิป {n}/{len(selected)} (ช็อต {i + 1})")
                    review_clip(project["scenes"][i], i)
                    ui(refresh_table)
                redo = [i + 1 for i in selected if (project["scenes"][i].get("clip_review") or {}).get("verdict") == "redo"]
                log("✓ ตรวจคลิปเสร็จ" + (f" — ควรเจนใหม่: ช็อต {', '.join(map(str, redo))} "
                                          "(เลือกแล้วกด 🎬 เจนวิดีโอใหม่ช็อตที่เลือก)" if redo else " — ผ่านทุกช็อต")
                    + " · กด 'ต่อวิดีโอใหม่' เพื่อตัดต่อตามผลตรวจ")
            except Stopped:
                log("⏸ หยุดตรวจคลิป")
            except Exception as exc:
                log(f"❌ ตรวจคลิปไม่สำเร็จ: {exc}")
            finally:
                project["done"].pop("video", None)
                save_project()
                state["busy"] = False
                ui(refresh_all)
        threading.Thread(target=worker, daemon=True).start()

    def make_clip(scene, index):
        """Generate one clip by driving the Slot exactly like pressing its Generate button."""
        busy = runtime["slot_busy"]
        waited = 0
        while busy[slot_index]:
            check_stop()
            time.sleep(2)
            waited += 2
            if waited > 1800:
                raise RuntimeError(f"Slot {slot_index + 1} ไม่ว่างนานเกินไป")
        out_dir = video_output_dir()
        before = {str(f): f.stat().st_mtime for f in out_dir.glob("*.mp4")} if out_dir.is_dir() else {}
        started = time.time()
        errors = []
        review_clip_prompt(scene, index)  # checker 1: a second GPT passes or rewrites the request first
        prompt = clip_prompt(scene)

        def submit():
            state["show_error_backup"] = runtime.get("show_error")
            runtime["show_error"] = lambda title, msg="", *a, **k: errors.append(f"{title}: {msg}")
            # This shot's own model / length / Slow 2x (chosen at planning time).
            cfg = runtime["slot_cfg_vars"][slot_index]
            set_slot_config(cfg, scene.get("clip_model") or VIDEO_AUTO_MODEL,
                            str(scene.get("clip_seconds") or GROK_CLIP_SECONDS[0]),
                            state["project"].get("aspect") or "16:9")
            runtime["_ai_slow2x_override"] = bool(scene.get("clip_slow"))
            runtime["slot_images"][slot_index].set(scene["image"])
            box = runtime["slot_prompts"][slot_index]
            box.delete("1.0", tk.END)
            box.insert("1.0", prompt)
            runtime["on_generate_slot"](slot_index)
            return bool(runtime["slot_busy"][slot_index])

        def restore():
            runtime["_ai_slow2x_override"] = None  # back to the Slow 2x checkbox
            if "show_error_backup" in state:
                runtime["show_error"] = state.pop("show_error_backup")
        try:
            if not run_on_ui(submit):
                raise RuntimeError("Slot ไม่รับงาน: " + ("; ".join(errors) or "ตรวจรูป/พรอมต์/โมเดลใน Slot"))
            waited = 0
            while busy[slot_index]:
                time.sleep(3)
                waited += 3
                if waited % 30 == 0:
                    set_progress("clips", state.get("clip_fraction", 0), f"{state.get('clip_label', '')} · รอ {waited // 60}:{waited % 60:02d}")
                if waited > 3600:
                    raise RuntimeError("รอคลิปเกิน 60 นาที")
        finally:
            run_on_ui(restore)
        if errors:
            raise RuntimeError("; ".join(errors)[:400])
        new_files = [f for f in out_dir.glob("*.mp4")
                     if f.stat().st_mtime >= started - 1 and before.get(str(f)) != f.stat().st_mtime]
        if not new_files:
            raise RuntimeError("Slot ทำงานเสร็จแต่ไม่พบไฟล์วิดีโอใหม่")
        newest = max(new_files, key=lambda f: f.stat().st_mtime)  # post-processed version is written last
        clips_dir = Path(state["folder"]) / "clips"
        clips_dir.mkdir(exist_ok=True)
        # A shot made again never overwrites its earlier clips: they move to clips/_สำรอง with the time.
        old = sorted(clips_dir.glob(f"clip_{index + 1:03d}.mp4")) + sorted(clips_dir.glob(f"clip_{index + 1:03d}_*.mp4"))
        if old:
            backup_dir = clips_dir / "_สำรอง"
            backup_dir.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for path in old:
                shutil.move(str(path), str(backup_dir / f"{path.stem}_เก่า_{stamp}{path.suffix}"))
            log(f"เก็บคลิปเดิมของช็อต {index + 1} ไว้ที่ clips/_สำรอง ({len(old)} ไฟล์)")
        if not scene.get("clip_slow"):
            target = clips_dir / f"clip_{index + 1:03d}.mp4"
            shutil.copy2(newest, target)
            return str(target)
        # Slow 2x shot: keep both speeds for hand editing; the slow one goes in the video.
        target = clips_dir / f"clip_{index + 1:03d}_สโลว์.mp4"
        shutil.copy2(newest, target)
        oldest = min(new_files, key=lambda f: f.stat().st_mtime)  # the download, before Slow 2x
        if oldest != newest:
            normal = clips_dir / f"clip_{index + 1:03d}_ปกติ.mp4"
            shutil.copy2(oldest, normal)
            scene["clip_normal"] = str(normal)
        return str(target)

    def stage_clips(indices=None):
        project = state["project"]
        ensure_continuity()
        scenes = project["scenes"]
        todo = indices if indices is not None else [
            i for i, sc in enumerate(scenes) if not (sc.get("clip") and os.path.isfile(sc["clip"]))]
        stage_motion(todo)  # pictures changed since (redrawn / edited) get a fresh video prompt first
        assign_clips(scenes, float(project["duration"]), economy_on())
        cfg = runtime["slot_cfg_vars"][slot_index]
        saved_slot = run_on_ui(lambda: (cfg["model"].get(), cfg["duration"].get(), cfg["aspect"].get()))
        try:
            run_clips(project, scenes, todo)
        finally:
            def put_back():
                set_slot_config(cfg, *saved_slot)
                save_slots = runtime.get("save_slot_configs")
                if callable(save_slots):
                    save_slots()
            run_on_ui(put_back)
        missing = [i + 1 for i, sc in enumerate(scenes) if not (sc.get("clip") and os.path.isfile(sc["clip"]))]
        if missing:
            raise RuntimeError(f"ยังขาดคลิปฉาก {', '.join(map(str, missing[:15]))} — กดเริ่มอีกครั้งเพื่อลองใหม่")
        log(f"✓ คลิปครบ {len(scenes)} ฉาก")

    def make_scene_clip(scene, i):
        """vela first when the plan says cheap; if vela can't make it, the same shot goes to grok-lower."""
        try:
            return with_retries(f"คลิปฉาก {i + 1}", lambda: make_clip(scene, i),
                                attempts=1 if scene.get("clip_model") == VIDEO_CHEAP_MODEL else 2)
        except (Stopped, HistoryLost, RateLimited):
            raise
        except Exception as exc:
            if scene.get("clip_model") != VIDEO_CHEAP_MODEL:
                raise
            scene["clip_fallback"] = True
            scene["clip_model"], scene["clip_seconds"], scene["clip_slow"] = pick_clip(
                float(scene.get("shot_seconds") or VIDEO_AUTO_AVG_SECONDS), bool(scene.get("slow")))
            log(f"ฉาก {i + 1}: vela เจนไม่ได้ ({error_reason(str(exc))}) → ใช้ {clip_label(scene)}")
            return with_retries(f"คลิปฉาก {i + 1}", lambda: make_clip(scene, i), attempts=2)

    def run_clips(project, scenes, todo):
        started, failures_in_row = time.time(), 0
        for n, i in enumerate(todo, 1):
            check_stop()
            scene = scenes[i]
            if not (scene.get("image") and os.path.isfile(scene["image"])):
                raise RuntimeError(f"ฉาก {i + 1} ยังไม่มีรูป")
            eta = ""
            if n > 1:
                remaining = (time.time() - started) / (n - 1) * (len(todo) - n + 1)
                eta = f" · เหลือประมาณ {int(remaining // 60)} นาที"
            state["clip_fraction"] = (n - 1) / max(1, len(todo))
            state["clip_label"] = f"สร้างคลิป {n}/{len(todo)} (ฉากที่ {i + 1}) ด้วย {clip_label(scene)}{eta}"
            set_progress("clips", state["clip_fraction"], state["clip_label"])
            try:
                scene["clip"] = make_scene_clip(scene, i)
                scene.pop("clip_error", None)
                failures_in_row = 0
                try:
                    review_clip(scene, i)  # checker 2: pass / use only the first part / should be made again
                except (Stopped, HistoryLost, RateLimited):
                    raise
                except Exception as exc:  # a check must never stop the clips
                    log(f"ตรวจคลิปช็อต {i + 1} ไม่ได้: {str(exc)[:200]}")
                ui(refresh_table)
                try:
                    update_preview()
                except Exception as exc:  # a preview must never stop the clips
                    log(f"ทำตัวอย่างไม่ได้: {str(exc)[:200]}")
            except (Stopped, HistoryLost, RateLimited):
                raise
            except Exception as exc:
                scene["clip_error"] = str(exc)[:300]
                failures_in_row += 1
                if failures_in_row >= 3:
                    save_project()
                    raise RuntimeError("สร้างคลิปล้มเหลว 3 ฉากติดกัน (เครดิตวิดีโอหมดหรือโมเดลมีปัญหา) — แก้แล้วกดเริ่มเพื่อทำต่อ")
            save_project()
            ui(refresh_table)

    STAGE_FUNCS = {"context": stage_context, "transcribe": stage_transcribe, "characters": stage_characters,
                   "plan": stage_plan, "storyboard": stage_storyboard, "images": stage_images,
                   "motion": stage_motion, "clips": stage_clips, "video": stage_video}

    # ── control ──
    def ask_on_ui(title, message) -> bool:
        """Ask a yes/no question from the worker thread and wait for the answer."""
        answer, event = {}, threading.Event()

        def ask():
            answer["yes"] = messagebox.askyesno(title, message, parent=page)
            event.set()
        root.after(0, ask)
        event.wait()
        return bool(answer.get("yes"))

    def confirm_credits() -> bool:
        """Shown after planning (text only, no image credits), with exact numbers."""
        project = state["project"]
        existing = character_refs()
        if video_mode:
            scenes = project.get("scenes") or []
            matched = sorted({Path(p).stem for sc in scenes for p in attachments_for(sc)})
            missing = sum(1 for sc in scenes if not (sc.get("image") and os.path.isfile(sc["image"])))
            todo = [sc for sc in scenes if not (sc.get("clip") and os.path.isfile(sc["clip"]))]
            if missing + len(todo) == 0:
                return True
            assign_clips(scenes, float(project["duration"]), economy_on())
            sheets = sum(1 for grp in board_groups(scenes, project.get("direction"))
                         if any(not (scenes[i].get("image") and os.path.isfile(scenes[i]["image"])) for i in grp)
                         and not all(scenes[i].get("board") and os.path.isfile(scenes[i]["board"]) for i in grp))
            return ask_on_ui(
                "ออโต้ — ยืนยันใช้เครดิต",
                f"วางแผนเสร็จแล้ว\n\nไฟล์แนบที่จะใช้ ({len(matched)}): {', '.join(matched[:15]) or '-'}\n"
                + (f"สตอรี่ชีต {sheets} รูป (ตามซีเควนซ์ ชีตละไม่เกิน 9 ช็อต) + " if sheets else "")
                + f"รูปฉาก {missing} รูป + คลิปวิดีโอ {len(todo)} คลิป (vela = ตัวถูก ถ้าเจนไม่ได้จะเปลี่ยนเป็น grok-lower เอง)\n"
                f"   {clip_summary(todo)}\n   (สโลว์×2 = ทำ AI Slow 2x หลังได้คลิป)\n\nเริ่มเลยไหม?")
        ensure_same_person()  # text only: a ghost of a known person is drawn from that person's face
        remake = set(forms_to_remake(existing))
        new_refs = [(c, n) for c, n in characters_needing_refs(project.get("context") or {}, project.get("scenes") or [])
                    if c["name"] not in existing or c["name"] in remake]
        scenes = project.get("scenes") or []
        missing = sum(1 for s in scenes if not (s.get("image") and os.path.isfile(s["image"])))
        sheets = sum(1 for grp in board_groups(scenes, project.get("direction"))
                     if any(not (scenes[i].get("image") and os.path.isfile(scenes[i]["image"])) for i in grp)
                     and not all(scenes[i].get("board") and os.path.isfile(scenes[i]["board"]) for i in grp)
                     and not any(scenes[i].get("board_skipped") for i in grp))
        total = len(new_refs) + sheets + missing
        if total == 0:
            return True
        people = "\n".join(f"   • {c['name']} — อยู่ใน {n} ฉาก" for c, n in new_refs[:20]) or "   (มีครบแล้ว)"
        if len(new_refs) > 20:
            people += f"\n   … และอีก {len(new_refs) - 20} ตัว"
        groups = [c["name"] for c in (project.get("context") or {}).get("characters", []) if is_group_character(c)]
        quota = ("\n⚠ เกิน 120 รูป จะใช้มากกว่า 1 วัน (หยุดรอเมื่อโควตาหมด แล้วกดทำต่อได้)" if total > 120
                 else "\nChatGPT สร้างรูปได้ประมาณ 120 รูปต่อวัน ถ้าโควตาหมดระหว่างทาง โปรแกรมจะหยุดรอ แล้วกดทำต่อได้")
        return ask_on_ui(
            "เล่าภาพ — ยืนยันใช้เครดิต",
            f"วางแผนเสร็จแล้ว จะสร้างรูปทั้งหมด {total} รูป\n\n"
            f"รูปตัวละคร {len(new_refs)} รูป (เฉพาะตัวละครที่ปรากฏในฉาก):\n{people}\n"
            + (f"   ไม่ทำรูปให้กลุ่มคน: {', '.join(groups)}\n" if groups else "")
            + (f"\nสตอรี่ชีต {sheets} รูป (ชีตละ 9 ฉาก ให้ภาพติดกันต่อเนื่อง)" if sheets else "")
            + f"\nรูปฉาก {missing} รูป (เปลี่ยนภาพเฉลี่ยทุก {project['duration'] / max(1, len(project.get('scenes') or [1])):.0f} วินาที)\n"
            + (f"คลิปวิดีโอ {sum(1 for x in project.get('scenes') or [] if not (x.get('clip') and os.path.isfile(x['clip'])))} คลิป "
               f"ด้วย {project.get('video_model')} คลิปละ {project.get('clip_seconds'):g} วินาที (ใช้เครดิตวิดีโอตามโมเดล)\n"
               if video_mode else "")
            + quota + "\n\nเริ่มสร้างรูปเลยไหม?")

    def start_pipeline(only=None):
        project = state["project"]
        if state["busy"]:
            return
        if not project or not project.get("script"):
            messagebox.showinfo("เล่าภาพ", "เลือกไฟล์บทก่อน", parent=page)
            return
        if not project.get("audio"):
            messagebox.showinfo("เล่าภาพ", "เลือกไฟล์เสียงบรรยายก่อน", parent=page)
            return
        aspect = desired_aspect()
        if project.get("scenes") and project.get("aspect") and project["aspect"] != aspect:
            if video_mode:
                # Shots are framed for the picture shape: plan again (text only), then new pictures and clips.
                if not messagebox.askyesno(
                        "ออโต้", f"เปลี่ยนสัดส่วนเป็น {aspect} — จะวางแผนช็อตใหม่ให้เหมาะกับภาพ{'แนวตั้ง' if aspect == '9:16' else 'แนวนอน'} "
                        "แล้วสร้างรูปและคลิปใหม่ทั้งหมด (บทกับเสียงที่ถอดไว้ใช้ต่อได้) ต่อไหม?", parent=page):
                    return
                project["scenes"] = []
                project.pop("direction", None)
                for key in ("plan", "images", "motion", "clips", "video"):
                    project["done"].pop(key, None)
            else:
                if not messagebox.askyesno("เล่าภาพ", "เปลี่ยนสัดส่วนภาพ ต้องสร้างรูปฉากใหม่ทั้งหมด ต่อไหม?", parent=page):
                    return
                for scene in project["scenes"]:
                    scene.pop("image", None)
                    scene.pop("clip", None)
                for key in ("images", "motion", "clips"):
                    project["done"].pop(key, None)
        new_style = style_var.get() if style_var.get() in STYLES else "ปกติ"
        if (project.get("scenes") and any(s.get("image") for s in project["scenes"])
                and (project.get("style_mode") or "ปกติ") != new_style and only is None):
            # Pictures carry the look, so a new style needs new pictures (the plan stays).
            if not messagebox.askyesno("เล่าภาพ", f"เปลี่ยนสไตล์เป็น \"{new_style}\" ต้องสร้างรูปฉากใหม่ทั้งหมด ต่อไหม?", parent=page):
                return
            for scene in project["scenes"]:
                scene.pop("image", None)
                scene.pop("clip", None)
            for key in ("images", "motion", "clips"):
                project["done"].pop(key, None)
        if video_mode:
            if not attachment_folder():
                messagebox.showinfo("ออโต้", "เลือกโฟลเดอร์ไฟล์แนบ (รูปตัวละคร/สถานที่) ที่หน้ารูป AI ก่อน — "
                                    "ออโต้ใช้ไฟล์แนบชุดเดียวกับ flow สร้างวิดีโอ", parent=page)
                return
            # Plans made for one fixed clip length (before grok-lower 6/10/15 + Slow 2x) need re-planning.
            changed_clip = project.get("done", {}).get("plan") and project.get("video_model") != VIDEO_AUTO_MODEL
            project.update({"clip_seconds": VIDEO_AUTO_AVG_SECONDS, "video_model": VIDEO_AUTO_MODEL})
        else:
            changed_clip = False
        wanted = desired_count()
        if project.get("done", {}).get("plan") and only is None and (
                changed_clip or int(project.get("image_count", wanted)) != wanted):
            what = "ความยาวคลิป" if changed_clip else "จำนวนรูป"
            if not messagebox.askyesno(
                    "เล่าภาพ", f"เปลี่ยน{what}แล้ว ต้องวางแผนฉากใหม่ (รูปและคลิปเดิมจะไม่ถูกใช้) ต่อไหม?", parent=page):
                return
            project["scenes"] = []
            project.pop("direction", None)
            for key in ("plan", "images", "motion", "clips", "video"):
                project["done"].pop(key, None)
        if video_mode and not project.get("done", {}).get("plan") and project.get("done", {}).get("transcribe"):
            # Dialogue shots are cut at word times; older transcripts have none: listen again (local, free).
            transcript = Path(state["folder"]) / "transcript.json"
            try:
                has_words = any(s.get("words") for s in json.loads(transcript.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                has_words = False
            if not has_words and script_dialogues(read_script(project["script"])):
                project["done"].pop("transcribe", None)
        project["image_count"] = wanted
        project.update({"aspect": aspect,
                        "subtitles": bool(subtitle_var.get()),
                        "style_mode": style_var.get() if style_var.get() in STYLES else "ปกติ"})
        project["done"].pop("video", None)
        follow_active_account()
        save_project()
        state["busy"], state["stop"] = True, False
        start_btn.config(state="disabled")

        def worker():
            current = None
            try:
                keys = [only] if only else [k for k, _t, _w in stages]
                confirmed = False
                for key in keys:
                    current = key
                    if project["done"].get(key) and key != "video":
                        continue
                    if key in ("characters", "storyboard", "images") and not confirmed:
                        if not confirm_credits():
                            log("⏸ ยังไม่สร้างรูป — แผนฉากยังอยู่ แก้แล้วกดเริ่มเพื่อทำต่อได้")
                            ui(stage_var.set, "รอยืนยันก่อนสร้างรูป")
                            return
                        confirmed = True
                    ui(refresh_stages, key)
                    log(f"▶ {dict((k, t) for k, t, _w in stages)[key]}")
                    STAGE_FUNCS[key]()
                    project["done"][key] = True
                    save_project()
                    ui(refresh_stages)
                    if key == "plan" and review_var.get() and not only:
                        set_progress("plan", 1.0, "หยุดให้ตรวจแผน — แก้พรอมต์ได้ แล้วกดเริ่มเพื่อทำต่อ")
                        log("⏸ แผนพร้อมแล้ว ตรวจ/แก้ แล้วกด ▶ เริ่ม เพื่อสร้างรูปต่อ")
                        return
                ui(lambda: (bar.configure(value=100), stage_var.set("เสร็จแล้ว 100%")))
                ui(detail_var.set, f"วิดีโอ: {project.get('last_video', '')}")
                notify = g.get("_snapgen_notify_done")
                if callable(notify):
                    ui(notify)
            except Stopped:
                log("⏸ หยุดแล้ว — กดเริ่มเพื่อทำต่อจากจุดเดิม")
                ui(stage_var.set, "หยุดแล้ว (ทำต่อได้)")
            except RateLimited as exc:
                log("⛔ " + str(exc).splitlines()[0])
                ui(stage_var.set, "ติดลิมิตสร้างรูป — กด ▶ ทำต่อ หลังเวลารีเซ็ต (งานที่ทำแล้วเก็บไว้ครบ)")
                ui(detail_var.set, " ".join(str(exc).splitlines()[1:3]))
            except Exception as exc:
                log(f"❌ {current}: {exc}")
                ui(stage_var.set, "ติดปัญหา — แก้แล้วกดเริ่มเพื่อทำต่อ")
                ui(detail_var.set, str(exc)[:200])
            finally:
                save_project()
                state["busy"] = False
                ui(lambda: start_btn.config(state="normal", text="▶ ทำต่อ"))
                ui(refresh_all)
        threading.Thread(target=worker, daemon=True).start()

    def redo_from(title):
        """Do one stage again and every stage after it; earlier stages are kept."""
        project = state["project"]
        if state["busy"] or not project:
            return
        keys = [k for k, _t, _w in stages]
        titles = {t: k for k, t, _w in stages}
        if title not in titles:
            return
        redo = keys[keys.index(titles[title]):]
        names = ", ".join(t for k, t, _w in stages if k in redo)
        if not messagebox.askyesno(
                "ทำใหม่ตั้งแต่ขั้น", f"จะทำใหม่: {names}\nขั้นก่อนหน้านั้นเก็บไว้ใช้ต่อ\n\n"
                + ("รูป/คลิปเดิมของช็อตจะไม่ถูกใช้ (ไฟล์ยังอยู่ในโฟลเดอร์)\n\n" if {"plan", "images", "clips"} & set(redo) else "")
                + "เริ่มเลยไหม?", parent=page):
            return
        for key in redo:
            project["done"].pop(key, None)
        if "context" in redo:
            project.pop("context", None)
            project.pop("ghosts", None)
            project["force_new_context"] = True
        if "transcribe" in redo:
            try:
                (Path(state["folder"]) / "transcript.json").unlink()
            except OSError:
                pass
        if "plan" in redo:
            project["scenes"] = []
            project.pop("direction", None)
            project.pop("planned_windows", None)
        for scene in project.get("scenes") or []:
            if "storyboard" in redo:
                scene.pop("board", None)
                scene.pop("board_skipped", None)
            if "images" in redo:
                for key in ("image", "bad", "error"):
                    scene.pop(key, None)
            if "motion" in redo:
                scene.pop("motion_image", None)
            if "clips" in redo or "images" in redo:
                for key in ("clip", "clip_normal", "clip_error", "clip_fallback"):
                    scene.pop(key, None)
        if "images" in redo:
            project["images_verified"] = False
        save_project()
        refresh_all()
        log(f"↺ ทำใหม่ตั้งแต่ {title}")
        start_pipeline()

    def reset_history():
        """The only way this story gets a second GPT history: the user asks for it."""
        project = state["project"]
        if state["busy"] or not project:
            return
        if not messagebox.askyesno(
                "เล่าภาพ — เริ่มประวัติ GPT ใหม่",
                "ทุกขั้นของเรื่องนี้ใช้ประวัติ GPT เดียวกัน\n"
                "เริ่มใหม่เฉพาะเมื่อประวัติเดิมหายหรือใช้งานไม่ได้เท่านั้น\n\n"
                "แผนฉาก รูปตัวละคร และรูปที่ทำแล้วยังอยู่ครบ ประวัติใหม่จะได้รับบทและ Context ก่อนทำงานต่อ\n\nเริ่มประวัติใหม่ไหม?",
                parent=page):
            return
        project["conversation"] = {}
        project["history_seeded"] = False
        save_project()
        log("เริ่มประวัติ GPT ใหม่แล้ว — กดเริ่มเพื่อทำต่อ (จะส่งบทและ Context เข้าประวัติใหม่ก่อน)")

    def request_stop():
        if state["busy"]:
            state["stop"] = True
            log("กำลังหยุดหลังขั้นตอนย่อยที่ทำอยู่ ...")

    def regenerate_selected(only=None, ask=True):
        project = state["project"]
        selected = sorted(only if only is not None else (int(i) for i in table.selection()))
        if not project or not selected:
            messagebox.showinfo("เล่าภาพ", "เลือกฉากในตารางก่อน (คลิกแถว กด Ctrl เพื่อเลือกหลายฉาก)", parent=page)
            return
        if state["busy"]:
            state.setdefault("redraw_queue", []).extend(selected)
            log(f"รอคิว: ฉาก {', '.join(str(i + 1) for i in selected)} จะเจนรูปใหม่ทันทีที่งานปัจจุบันเสร็จ")
            return
        if ask and not messagebox.askyesno("เล่าภาพ", f"สร้างรูปใหม่ {len(selected)} ฉาก (ใช้เครดิต {len(selected)} รูป)?", parent=page):
            return
        follow_active_account()
        state["busy"], state["stop"] = True, False

        def worker():
            try:
                stage_images(selected)
                if video_mode:
                    for i in selected:
                        project["scenes"][i].pop("clip", None)
                    project["done"].pop("clips", None)
            except Exception as exc:
                log(f"❌ {exc}")
            finally:
                project["done"].pop("video", None)
                save_project()
                state["busy"] = False
                ui(refresh_all)
                log("สร้างรูปใหม่เสร็จ — กด 'ต่อวิดีโอใหม่' เพื่อทำวิดีโออีกรอบ")
        threading.Thread(target=worker, daemon=True).start()

    def fix_request(index: int, problem: str) -> str:
        """Ask GPT (in this story's history) to rewrite one shot so a seen problem does not happen again."""
        project = state["project"]
        scene = project["scenes"][index]
        notes = "; ".join(scene.get("fix_notes") or [])
        what = "วิดีโอ" if video_mode else "รูป"
        return (
            f"ช็อตที่ {index + 1} เจน{what}ออกมามีปัญหา: {problem}. "
            + (f"ปัญหาที่เคยเจอในช็อตนี้แล้ว (ห้ามกลับมาอีก): {notes}. " if notes else "")
            + f"เขียนคำสั่ง{what}ช็อตนี้ใหม่แบบเข้มงวด ให้ปัญหานี้ไม่เกิดอีกแน่นอน แต่ยังเป็นช็อตเดิมของเรื่องเดิม: "
            "คงเหตุการณ์ตามคำบรรยาย ตัวละครเดิม สถานที่เดิม อารมณ์ และความต่อเนื่องกับช็อตก่อน/หลัง "
            + ("และคงองค์ประกอบ ขนาดภาพ มุมกล้อง ตำแหน่งตัวละครตามสตอรี่บอร์ด/ภาพแรกเดิมของช็อตนี้ แก้เฉพาะสิ่งที่เป็นปัญหา. "
               if video_mode else "และคงขนาดภาพ มุมกล้องเดิม แก้เฉพาะสิ่งที่เป็นปัญหา. ")
            + "หลักการ: "
            "1) บอกร่างกายที่ถูกต้องของทุกตัวในเฟรมชัดเจนในทางบวกตั้งแต่ประโยคแรก และย้ำอีกครั้งตอนกลาง "
            "(ตาม 'ร่างกายที่ถูกต้อง' ด้านล่าง เช่น 'พญานาคเป็นงูยักษ์ ไม่มีแขนขา', 'ม้ามีสี่ขา', 'คนมือละ 5 นิ้ว'); "
            "2) ตัดการกระทำที่ทำให้เกิดปัญหาออกทั้งหมด แทนด้วยการกระทำที่ร่างกายนั้นทำได้จริงและไม่มีทางทำให้เกิดปัญหานั้น "
            "(เช่น พญานาคถือดาบ → ฟาดหาง ฉกด้วยเขี้ยว); ใช้การเคลื่อนไหวน้อยลงและชัดขึ้น 1 อย่าง; "
            "3) forbid = รายการสิ่งที่ห้ามปรากฏเด็ดขาดตลอดคลิป สั้นๆ คั่นด้วยจุลภาค ระบุเจ้าของเสมอ เพราะคนในเฟรมยังต้องมีมือ "
            "(เช่น มือบนตัวพญานาค, ขาบนตัวพญานาค, พญานาคถืออาวุธ — ห้ามเขียนแค่ 'มือ' เฉยๆ); "
            "4) " + SCOPE_RULE + "แก้เฉพาะปัญหาที่แจ้ง ห้ามเปลี่ยนเรื่องหรือเพิ่มสิ่งใหม่ที่บทไม่มี; "
            + ("ภาพสมจริงแบบภาพยนตร์ ไม่มีตัวหนังสือ. " if video_mode else "คงสไตล์ภาพเดิมของเรื่อง ไม่มีตัวหนังสือ. ")
            + "ตอบ JSON เท่านั้น {\"prompt\":\"\",\"video_prompt\":\"\",\"forbid\":\"\",\"change\":\"\"} "
            "prompt = ภาพแรกของช็อต (ใช้เมื่อวาดภาพใหม่), video_prompt = การเคลื่อนไหวตลอดคลิป, change = สรุปสั้นๆ ว่าแก้อะไร.\n\n"
            f"{story_anchor(index)}\n"
            f"prompt เดิม: {scene.get('prompt', '')}\n"
            + (f"video_prompt เดิม: {scene.get('video_prompt', '')}" if video_mode else "")
        )

    def fix_problem_selected():
        """Tell GPT what went wrong in the selected shots; it rewrites them and they are made again."""
        project = state["project"]
        selected = sorted(int(i) for i in table.selection())
        if not project or not selected:
            messagebox.showinfo("แก้ช็อต", "เลือกช็อตที่มีปัญหาในตารางก่อน (Ctrl+คลิก เลือกได้หลายช็อต)", parent=page)
            return
        if state["busy"]:
            messagebox.showinfo("แก้ช็อต", "กำลังทำงานอยู่ — กดหยุด หรือรอให้เสร็จก่อน", parent=page)
            return
        problem = simpledialog.askstring(
            "แก้ช็อตที่มีปัญหา",
            f"ช็อต {', '.join(str(i + 1) for i in selected)} มีปัญหาอะไร?\n"
            "(เช่น พญานาคมีมือโผล่ขึ้นมาตอนต่อสู้ / หน้าตัวละครเปลี่ยน / ภาพเป็นการ์ตูน)", parent=page)
        if not problem or not problem.strip():
            return
        problem = problem.strip()
        follow_active_account()
        state["busy"], state["stop"] = True, False

        def worker():
            try:
                ensure_story_in_history()
                ensure_continuity()
                ensure_bodies()
                redraw = []
                for i in selected:
                    scene = project["scenes"][i]
                    set_progress("clips" if video_mode else "images", 0,
                                 f"GPT กำลังเขียนคำสั่งช็อต {i + 1} ใหม่เพื่อแก้: {problem[:40]}")
                    reply = with_retries(f"แก้ช็อต {i + 1}", lambda i=i: parse_json_reply(chat(fix_request(i, problem))))
                    if not str(reply.get("prompt") or "").strip():
                        raise RuntimeError(f"GPT ไม่ได้ส่ง prompt ใหม่ของช็อต {i + 1}")
                    scene.setdefault("prompt_before_fix", scene.get("prompt", ""))
                    scene["prompt"] = str(reply["prompt"]).strip()
                    if video_mode and str(reply.get("video_prompt") or "").strip():
                        scene["video_prompt"] = str(reply["video_prompt"]).strip()
                    scene.setdefault("fix_notes", []).append(problem)
                    forbid = merge_forbid(reply.get("negative"), reply.get("forbid"))
                    if forbid:  # repeated at the end of every clip request of this shot
                        scene["forbid"] = ", ".join(dict.fromkeys(
                            [w.strip() for w in (scene.get("forbid", "") + "," + forbid).split(",") if w.strip()]))
                    log(f"🛠 ช็อต {i + 1}: {reply.get('change') or 'เขียน prompt ใหม่แล้ว'}"
                        + (f" · ห้ามเด็ดขาด: {scene['forbid']}" if scene.get("forbid") else ""))
                    if not video_mode:
                        redraw.append(i)  # เล่าภาพ: the picture is the result
                    save_project()
                    ui(refresh_table)
                if redraw:
                    log(f"วาดภาพเริ่มต้นใหม่: ช็อต {', '.join(str(i + 1) for i in redraw)}")
                    stage_images(redraw)
                if video_mode:
                    # ออโต้: keep the first picture; making the video again uses credits, so ask first.
                    shots = ", ".join(str(i + 1) for i in selected)
                    set_progress("clips", 0, f"แก้คำสั่งช็อต {shots} แล้ว — รอยืนยันเจนวิดีโอ")
                    if not ask_on_ui("เจนวิดีโอใหม่?",
                                     f"แก้คำสั่งวิดีโอช็อต {shots} แล้ว (ดูได้ใน Log/ดับเบิลคลิกในตาราง)\n\n"
                                     f"เจนวิดีโอใหม่ {len(selected)} คลิปเลยไหม? (ใช้เครดิตวิดีโอ)\n"
                                     "ถ้าภาพแรกก็มีปัญหาเดียวกัน ให้ตอบ 'ไม่' แล้วกด 'สร้างรูปใหม่ช็อตที่เลือก' ก่อน"):
                        log(f"⏸ ยังไม่เจนวิดีโอ — คำสั่งใหม่บันทึกแล้ว กด '🎬 เจนวิดีโอใหม่ช็อตที่เลือก' เมื่อพร้อม")
                        ui(stage_var.set, "แก้คำสั่งแล้ว ยังไม่เจนวิดีโอ")
                        return
                    remake_clips(selected)
                log("✓ แก้ช็อตเสร็จ — กด 'ต่อวิดีโอใหม่' เพื่อรวมวิดีโออีกรอบ")
            except Exception as exc:
                log(f"❌ แก้ช็อตไม่สำเร็จ: {exc}")
            finally:
                project["done"].pop("video", None)
                save_project()
                state["busy"] = False
                ui(refresh_all)
        threading.Thread(target=worker, daemon=True).start()

    def remake_clips(selected):
        """Worker thread: make the clips of these shots again with their current prompts (and show it)."""
        project = state["project"]
        shots = ", ".join(str(i + 1) for i in selected)
        log(f"🎬 กำลังเจนวิดีโอใหม่: ช็อต {shots}")
        ui(stage_var.set, f"กำลังเจนวิดีโอใหม่ ช็อต {shots}")
        for i in selected:
            for key in ("clip", "clip_normal", "clip_error", "clip_review", "clip_use_until", "clip_cuts"):
                project["scenes"][i].pop(key, None)
        project["done"].pop("clips", None)
        stage_clips(selected)
        log(f"✓ เจนวิดีโอใหม่เสร็จ: ช็อต {shots}")

    def remake_clips_selected():
        """Button: make the video of the selected shots again (custom redo, nothing else changes)."""
        project = state["project"]
        selected = sorted(int(i) for i in table.selection())
        if not project or not selected:
            messagebox.showinfo("เจนวิดีโอใหม่", "เลือกช็อตในตารางก่อน (Ctrl+คลิก เลือกได้หลายช็อต)", parent=page)
            return
        if state["busy"]:
            messagebox.showinfo("เจนวิดีโอใหม่", "กำลังทำงานอยู่ — กดหยุด หรือรอให้เสร็จก่อน", parent=page)
            return
        missing = [i + 1 for i in selected if not (project["scenes"][i].get("image") and os.path.isfile(project["scenes"][i]["image"]))]
        if missing:
            messagebox.showinfo("เจนวิดีโอใหม่", f"ช็อต {', '.join(map(str, missing))} ยังไม่มีรูป — สร้างรูปก่อน", parent=page)
            return
        if not messagebox.askyesno("เจนวิดีโอใหม่", f"เจนวิดีโอใหม่ช็อต {', '.join(str(i + 1) for i in selected)} "
                                   f"({len(selected)} คลิป ใช้เครดิตวิดีโอ)?", parent=page):
            return
        follow_active_account()
        state["busy"], state["stop"] = True, False

        def worker():
            try:
                remake_clips(selected)
                log("กด 'ต่อวิดีโอใหม่' เพื่อรวมวิดีโออีกรอบ")
            except Exception as exc:
                log(f"❌ เจนวิดีโอใหม่ไม่สำเร็จ: {exc}")
                ui(stage_var.set, "เจนวิดีโอใหม่ไม่สำเร็จ")
            finally:
                project["done"].pop("video", None)
                save_project()
                state["busy"] = False
                ui(refresh_all)
        threading.Thread(target=worker, daemon=True).start()

    def edit_scene_image(index, wish):
        """Small change on the existing picture: send it back to GPT with the wish, keep everything else."""
        project = state["project"]
        scene = project["scenes"][index]
        state["busy"], state["stop"] = True, False

        def worker():
            try:
                imgmod = runtime.get("_imgmod") or g.get("_imgmod")
                current = scene["image"]
                ensure_bodies()
                # Image 1 = the picture to change; image 2 = its storyboard panel (layout); then identities.
                board = scene.get("board") if scene.get("board") and os.path.isfile(scene["board"]) else None
                refs = [p for p in (attachments_for(scene) if video_mode else
                                    [character_refs().get(c) for c in scene.get("characters") or []]) if p][:3]
                prompt = (f"แก้ไขรูปที่ 1 (รูปเดิมของช็อตนี้) เฉพาะจุดนี้: {wish}\n"
                          "คงองค์ประกอบ ตัวละคร มุมกล้อง แสง และสไตล์เดิมทั้งหมด เปลี่ยนเฉพาะสิ่งที่สั่ง ภาพเดียวเต็มเฟรม ไม่มีตัวหนังสือ.\n"
                          "รูปนี้ต้องยังเป็นช็อตเดิมของเรื่องเดิม: ตัวละครเป็นคน/สิ่งมีชีวิตเดิม หน้าตาและรูปร่างเดิม "
                          "ห้ามแปลงร่างเป็นสิ่งอื่น ห้ามเพิ่มตัวละครใหม่ เหตุการณ์ยังตรงกับคำบรรยาย.\n"
                          f"{story_anchor(index)}\nภาพนี้คือ: {scene.get('prompt', '')}"
                          + ("\nรูปที่ 2: สตอรี่บอร์ดของช็อตนี้ — คงองค์ประกอบ ขนาดภาพ มุมกล้อง และตำแหน่งตัวละครตามนี้"
                             if board else "")
                          + ("\n" + "\n".join(f"รูปที่ {k}: หน้าตา/รูปร่าง/ชุดของ {Path(p).stem} เท่านั้น"
                                               for k, p in enumerate(refs, 3 if board else 2)) if refs else ""))
                out = with_retries(f"แก้ฉาก {index + 1}", lambda: imgmod.generate_image(
                    prompt, output_dir=str(Path(current).parent), name_hint=f"scene_{index + 1:03d}_edit", is_edit=True,
                    ref_images=[base64.b64encode(Path(p).read_bytes()).decode("ascii")
                                for p in [current, *([board] if board else []), *refs]],
                    aspect_ratio=project["aspect"], save_sidecar=False,
                    conversation_state=project.setdefault("conversation", {}), conversation_save_fn=save_project))
                target = Path(current).with_suffix(Path(out).suffix or ".png")
                Path(current).unlink(missing_ok=True)
                shutil.move(str(out), str(target))
                scene["image"] = str(target)
                scene.pop("bad", None)
                scene["prompt"] = scene.get("prompt", "") + f" (แก้: {wish})"
                project["images_verified"] = False
                log(f"✓ แก้ฉาก {index + 1}: {wish}")
            except Exception as exc:
                log(f"❌ แก้ฉาก {index + 1} ไม่สำเร็จ: {exc}")
            finally:
                project["done"].pop("video", None)
                save_project()
                state["busy"] = False
                ui(refresh_all)
        threading.Thread(target=worker, daemon=True).start()

    def edit_prompt(_event=None):
        selection = table.selection()
        if not selection:
            return
        index = int(selection[0])
        scene = state["project"]["scenes"][index]
        win = tk.Toplevel(page)
        win.title(f"แก้ฉาก {int(selection[0]) + 1}")
        win.geometry("980x380")
        buttons = tk.Frame(win)
        buttons.pack(side="bottom", anchor="e", padx=8, pady=(0, 8))
        text = tk.Text(win, wrap="word", height=12)
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", scene.get("prompt", ""))

        def save():
            scene["prompt"] = text.get("1.0", tk.END).strip()
            save_project()
            refresh_table()
            win.destroy()

        tk.Label(buttons, text="แก้นิดเดียวจากรูปเดิม (เช่น เปลี่ยนศาลเป็นศาลเจ้าจีน):").pack(side="left", padx=(0, 4))
        fix_entry = tk.Entry(buttons, width=34)
        fix_entry.pack(side="left", padx=4)

        def fix_from_current():
            wish = fix_entry.get().strip()
            if not wish:
                messagebox.showinfo("เล่าภาพ", "พิมพ์สิ่งที่อยากแก้ในช่องก่อน", parent=win)
                return
            if not (scene.get("image") and os.path.isfile(scene["image"])):
                messagebox.showinfo("เล่าภาพ", "ฉากนี้ยังไม่มีรูปเดิม — ใช้ 'บันทึกแล้วเจนรูปใหม่'", parent=win)
                return
            save()
            if state["busy"]:
                messagebox.showinfo("เล่าภาพ", "บันทึก prompt แล้ว แต่ตอนนี้กำลังทำงานอยู่ — กดแก้จากรูปเดิมอีกครั้งเมื่องานเสร็จ", parent=win)
                return
            edit_scene_image(index, wish)
        make_styled_button(buttons, "SECONDARY", "แก้จากรูปเดิม", command=fix_from_current).pack(side="left", padx=4)

        def save_and_redraw():
            save()
            if state["busy"]:
                # Keep the user's edit and draw it as soon as the current job ends.
                state.setdefault("redraw_queue", []).append(index)
                log(f"บันทึก prompt ฉาก {index + 1} แล้ว — รอคิว จะเจนรูปใหม่ทันทีที่งานปัจจุบันเสร็จ")
                return
            regenerate_selected([index], ask=False)
        make_styled_button(buttons, "SECONDARY", "บันทึก", command=save).pack(side="left", padx=4)
        make_styled_button(buttons, "PRIMARY", "บันทึกแล้วเจนรูปใหม่", command=save_and_redraw).pack(side="left", padx=4)

    def show_preview(_event=None):
        selection = table.selection()
        scenes = (state["project"] or {}).get("scenes") or []
        if not selection or int(selection[0]) >= len(scenes):
            return
        image = scenes[int(selection[0])].get("image")
        if scenes[int(selection[0])].get("error"):
            preview.config(image="", text="เจนไม่ได้:\n" + error_reason(scenes[int(selection[0])]["error"]), wraplength=280)
            return
        if not image or not os.path.isfile(image):
            preview.config(image="", text=scenes[int(selection[0])].get("error") or "ยังไม่มีรูป", wraplength=280)
            return
        try:
            from PIL import Image, ImageTk
            pic = Image.open(image)
            # Fit the preview column (portrait pictures use its height).
            pic.thumbnail((max(120, preview_box.winfo_width() - 8), max(120, preview_box.winfo_height() - 8)))
            photo = ImageTk.PhotoImage(pic)
            preview.config(image=photo, text="")
            preview.image = photo
        except Exception as exc:
            preview.config(image="", text=f"เปิดรูปไม่ได้: {exc}")

    def run_redraw_queue():
        queue = state.get("redraw_queue") or []
        if queue and not state["busy"] and state["project"]:
            state["redraw_queue"] = []
            regenerate_selected(sorted(set(queue)), ask=False)
        page.after(2000, run_redraw_queue)
    page.after(2000, run_redraw_queue)

    table.bind("<Double-1>", edit_prompt)
    table.bind("<<TreeviewSelect>>", show_preview)
    g["video_auto_open_project" if video_mode else "narrate_open_project"] = open_project
    try:
        # Come back to the story that was open last time (after an update/restart nothing is lost).
        last = last_project_file().read_text(encoding="utf-8").strip()
        if last and os.path.isfile(os.path.join(last, "project.json")):
            page.after(300, lambda: load_saved_project(last, quiet=True))
    except OSError:
        pass
    return {"open_project": open_project, "refresh_clip_info": refresh_clip_info, "busy": lambda: state["busy"]}
