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
from tkinter import filedialog, messagebox, ttk

MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right")
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
FPS = 25
CROSSFADE = 0.5
PLAN_WINDOW = 180.0  # seconds of narration planned per GPT request
IMAGE_COUNTS = ("35", "50", "80")  # ChatGPT allows ~120 images a day
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Scenes are planned before character images: the plan decides which
# characters actually appear, so only those get a reference image.
STAGES = (
    ("context", "วิเคราะห์บท", 5),
    ("transcribe", "ฟังเสียง", 10),
    ("plan", "วางแผนฉาก", 10),
    ("characters", "รูปตัวละคร", 10),
    ("images", "สร้างรูปฉาก", 50),
    ("video", "ตัดต่อ", 15),
)
DEFAULT_STYLE = "ภาพสมจริงแบบภาพยนตร์ แสงธรรมชาติ รายละเอียดสูง ไม่มีตัวหนังสือหรือคำบรรยายในภาพ"


class Stopped(Exception):
    pass


class HistoryLost(RuntimeError):
    """The story's GPT conversation no longer exists; never replaced silently."""


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
        shortest = min(range(1, len(scenes)), key=lambda i: durs[i])
        del scenes[shortest]
    return scenes


def segment_durations(starts: list, duration: float) -> list:
    ends = list(starts[1:]) + [duration]
    return [max(0.5, round(e - s, 3)) for s, e in zip(starts, ends)]


def zoompan_filter(motion: str, frames: int, width: int, height: int) -> str:
    frames = max(1, int(frames))
    progress = f"on/{frames}"
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_out":
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


def xfade_graph(lengths: list, fade: float = CROSSFADE) -> tuple[str, float]:
    """filter_complex chaining inputs 0..n-1 with crossfades; returns (graph, output length)."""
    if len(lengths) == 1:
        return "[0:v]null[vout]", lengths[0]
    parts, label, elapsed = [], "[0:v]", lengths[0]
    for i in range(1, len(lengths)):
        out = "[vout]" if i == len(lengths) - 1 else f"[x{i}]"
        offset = max(0.0, elapsed - fade)
        parts.append(f"{label}[{i}:v]xfade=transition=fade:duration={fade}:offset={offset:.3f}{out}")
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
    parts = [str(character.get(k) or "").strip() for k in keys]
    return " ".join(p for p in parts if p and p not in ("ไม่ระบุ", "-"))


def plan_request(window: list, count: int, names: list, previous: str, era: str) -> str:
    lines = "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in window)
    return (
        f"วางแผนภาพประกอบเสียงบรรยายช่วง {fmt_time(window[0]['start'])}–{fmt_time(window[-1]['end'])} "
        f"ประมาณ {count} ภาพ จากประโยคที่ถอดจากเสียงพร้อมเวลาเริ่ม (วินาที) ด้านล่าง. "
        "เลือกจุดเปลี่ยนภาพที่เหตุการณ์ สถานที่ หรือผู้พูดเปลี่ยน ภาพติดกันห้ามซ้ำมุมกล้องเดิม. "
        f"ยุค/บรรยากาศ: {era}. ตัวละครที่ใช้ได้ (ใช้ชื่อตรงตัวเท่านั้น): {', '.join(names) or '-'}. "
        + (f"ภาพก่อนหน้าคือ: {previous}. " if previous else "")
        + "ตอบ JSON เท่านั้น: {\"scenes\":[{\"start\":0.0,\"characters\":[],\"location\":\"\",\"prompt\":\"\",\"motion\":\"\"}]} "
        "start = เวลาเริ่มของประโยคที่ภาพนี้เริ่ม (ต้องเป็นตัวเลขในวงเล็บด้านล่าง). "
        "characters = ชื่อตัวละครที่ปรากฏในภาพนี้ (ว่างได้ถ้าเป็นภาพสถานที่). "
        "prompt = คำบรรยายภาพนิ่งภาษาไทย: ใครทำอะไร ที่ไหน เวลา แสง มุมกล้อง อารมณ์ ไม่มีตัวหนังสือในภาพ. "
        f"motion = หนึ่งใน {', '.join(MOTIONS)}.\n\n" + lines
    )


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
    from snapgen_page_builder import append_log, build_page, make_log_box, make_styled_button

    runtime = g.get("_runtime_g") or g
    page, box = build_page(root, "🎞️ เล่าภาพ — ใส่บท + เสียงบรรยาย แล้วกดเริ่ม ได้วิดีโอภาพประกอบทั้งเรื่อง")
    bg = box.cget("bg")

    state = {"project": None, "folder": None, "busy": False, "stop": False, "lock": threading.RLock()}
    script_var = tk.StringVar(value="ลากไฟล์บทมาวาง หรือกดเลือก (.docx / .txt)")
    audio_var = tk.StringVar(value="ลากไฟล์เสียงมาวาง หรือกดเลือก (.wav / .mp3 / .m4a)")
    aspect_var = tk.StringVar(value="16:9")
    count_var = tk.StringVar(value="50")
    subtitle_var = tk.BooleanVar(value=False)
    review_var = tk.BooleanVar(value=False)
    stage_var = tk.StringVar(value="พร้อม")
    detail_var = tk.StringVar(value="")

    # ── input row ──
    inputs = tk.Frame(box, bg=bg)
    inputs.pack(fill="x", padx=8, pady=(6, 2))

    def file_card(parent, title, var, command):
        card = tk.Frame(parent, bg="#FFFFFF", highlightthickness=1, highlightbackground="#CBD5E1")
        card.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Label(card, text=title, bg="#FFFFFF", fg="#0F172A", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        tk.Label(card, textvariable=var, bg="#FFFFFF", fg="#475569", anchor="w", wraplength=420, justify="left").pack(fill="x", padx=10)
        make_styled_button(card, "SECONDARY", "เลือกไฟล์", command=command).pack(anchor="w", padx=10, pady=(4, 8))
        return card

    options = tk.Frame(box, bg=bg)
    options.pack(fill="x", padx=8, pady=2)
    tk.Label(options, text="ภาพ", bg=bg).pack(side="left")
    ttk.Combobox(options, textvariable=aspect_var, values=list(SIZES), width=6, state="readonly").pack(side="left", padx=(4, 14))
    tk.Label(options, text="จำนวนรูปทั้งเรื่อง", bg=bg).pack(side="left")
    ttk.Combobox(options, textvariable=count_var, values=IMAGE_COUNTS, width=5, state="readonly").pack(side="left", padx=4)
    tk.Label(options, text="รูปฉาก (+ รูปตัวละครตามเรื่อง)", bg=bg).pack(side="left", padx=(0, 14))
    tk.Checkbutton(options, text="ใส่ซับไตเติล", variable=subtitle_var, bg=bg).pack(side="left", padx=(0, 10))
    tk.Checkbutton(options, text="หยุดให้ตรวจแผนก่อนสร้างรูป", variable=review_var, bg=bg).pack(side="left")

    # ── run row ──
    run_row = tk.Frame(box, bg=bg)
    run_row.pack(fill="x", padx=8, pady=(6, 2))
    start_btn = make_styled_button(run_row, "PRIMARY", "▶ เริ่มทำทั้งเรื่อง", command=lambda: start_pipeline())
    start_btn.pack(side="left")
    make_styled_button(run_row, "DANGER", "⏸ หยุด", command=lambda: request_stop()).pack(side="left", padx=6)
    make_styled_button(run_row, "SECONDARY", "เปิดโฟลเดอร์", command=lambda: open_path(state["folder"])).pack(side="left", padx=6)
    make_styled_button(run_row, "SUCCESS", "▶ เปิดวิดีโอ", command=lambda: open_path((state["project"] or {}).get("last_video"))).pack(side="left")

    progress_row = tk.Frame(box, bg=bg)
    progress_row.pack(fill="x", padx=8, pady=(4, 0))
    bar = ttk.Progressbar(progress_row, maximum=100)
    bar.pack(fill="x")
    tk.Label(progress_row, textvariable=stage_var, bg=bg, fg="#0F172A", font=("TkDefaultFont", 10, "bold"), anchor="w").pack(fill="x", pady=(4, 0))
    tk.Label(progress_row, textvariable=detail_var, bg=bg, fg="#475569", anchor="w").pack(fill="x")
    stage_row = tk.Frame(box, bg=bg)
    stage_row.pack(fill="x", padx=8, pady=(2, 4))
    stage_labels = {}
    for key, title, _weight in STAGES:
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
    make_styled_button(table_tools, "DANGER", "เริ่มประวัติ GPT ใหม่", command=lambda: reset_history()).pack(side="right")
    table_frame = tk.Frame(box, bg=bg)
    table_frame.pack(fill="both", expand=True, padx=8, pady=4)
    columns = ("no", "time", "chars", "prompt", "status")
    table = ttk.Treeview(table_frame, columns=columns, show="headings", height=10, selectmode="extended")
    for key, title, width in (("no", "#", 44), ("time", "เวลา", 64), ("chars", "ตัวละคร", 170),
                              ("prompt", "ภาพ", 560), ("status", "รูป", 80)):
        table.heading(key, text=title)
        table.column(key, width=width, stretch=key == "prompt")
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    table.pack(side="left", fill="both", expand=True)
    scroll.pack(side="left", fill="y")
    preview = tk.Label(table_frame, bg="#F1F5F9", width=40, text="เลือกฉากเพื่อดูรูป")
    preview.pack(side="left", fill="y", padx=(8, 0))

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
        folder = project_folder_for(export_root() / "เล่าภาพ", script, text)
        folder.mkdir(parents=True, exist_ok=True)
        state["folder"] = str(folder)
        try:
            project = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            project = {"version": 2, "conversation": {}, "done": {}, "scenes": []}
            log("เรื่องใหม่ — จะเปิดประวัติ GPT ใหม่ของเรื่องนี้เมื่อกดเริ่ม")
        else:
            log("เปิดงานเดิมของเรื่องนี้ — ทำต่อในประวัติ GPT เดิม")
        project["script"] = script
        project["script_hash"] = script_hash(text)
        state["project"] = project
        script_var.set(Path(script).name)
        if project.get("audio"):
            audio_var.set(f"{Path(project['audio']).name} · {fmt_time(project.get('duration'))}")
        aspect_var.set(project.get("aspect", aspect_var.get()))
        count_var.set(str(project.get("image_count", count_var.get())))
        subtitle_var.set(bool(project.get("subtitles", subtitle_var.get())))
        save_project()
        refresh_all()

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
        log(f"เสียงยาว {fmt_time(duration)} → {count_var.get()} รูป เปลี่ยนภาพเฉลี่ยทุก {duration / int(count_var.get()):.0f} วินาที")

    file_card(inputs, "📄 ไฟล์บท", script_var, choose_script)
    file_card(inputs, "🎙 ไฟล์เสียงบรรยาย", audio_var, choose_audio)

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
            status = "✓" if image and os.path.isfile(image) else ("ผิดพลาด" if scene.get("error") else "—")
            table.insert("", "end", iid=str(i), values=(
                i + 1, fmt_time(scene.get("start")), ", ".join(scene.get("characters") or []),
                scene.get("prompt", "").replace("\n", " "), status))

    def refresh_stages(active=None):
        done = (state["project"] or {}).get("done", {})
        for key, title, _w in STAGES:
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
        total = sum(w for _k, _t, w in STAGES)
        percent = int(sum(w for k, _t, w in STAGES if done.get(k)) * 100 / total)
        bar.configure(value=percent)
        if percent == 100:
            stage_var.set("เสร็จแล้ว 100% — กด ▶ เปิดวิดีโอ")
        elif percent:
            stage_var.set(f"ทำไปแล้ว {percent}% — กดเริ่มเพื่อทำต่อ")
            start_btn.config(text="▶ ทำต่อ")

    def set_progress(stage_key, fraction, detail=""):
        done_weight = 0
        total = sum(w for _k, _t, w in STAGES)
        for key, title, weight in STAGES:
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

    def make_image(prompt, refs, out_dir, name, aspect):
        imgmod = runtime.get("_imgmod") or g.get("_imgmod")
        if imgmod is None:
            raise RuntimeError("ระบบสร้างรูปยังไม่พร้อม")
        project = state["project"]
        encoded = [base64.b64encode(Path(p).read_bytes()).decode("ascii") for p in refs]
        if refs:
            prompt += "\n\nATTACHED REFERENCES (keep these exact faces, bodies and outfits):\n" + "\n".join(
                f"Image {i}: {Path(p).stem}" for i, p in enumerate(refs, 1))
        out = imgmod.generate_image(
            prompt, output_dir=str(out_dir), name_hint=name, is_edit=bool(encoded),
            ref_images=encoded or None, aspect_ratio=aspect, save_sidecar=False,
            conversation_state=project.setdefault("conversation", {}), conversation_save_fn=save_project,
        )
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
                log(f"❌ {label} ครั้งที่ {attempt}: {str(exc)[:200]}")
                if "conversation_not_found" in text or ("conversation" in text and ("not found" in text or "404" in text)):
                    # One story = one GPT history. Never open a replacement
                    # chat silently; the user decides with the explicit button.
                    raise HistoryLost(
                        "ประวัติ GPT ของเรื่องนี้หายไป (ถูกลบหรือเปลี่ยนบัญชี) — โปรแกรมไม่เปิดประวัติใหม่ให้เอง "
                        "ถ้าต้องการทำต่อในประวัติใหม่ กดปุ่ม 'เริ่มประวัติ GPT ใหม่'") from exc
                time.sleep(3 * attempt)
        raise RuntimeError(f"{label} ไม่สำเร็จ: {last}")

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

    def stage_transcribe():
        project, folder = state["project"], Path(state["folder"])
        import snapgen_voice_input
        set_progress("transcribe", 0.02, "กำลังเปิด Whisper ...")
        model, backend = snapgen_voice_input._get_whisper_model(log_fn=log)
        names = [c.get("name") for c in (project.get("context") or {}).get("characters", []) if c.get("name")]
        segments_iter, _info = model.transcribe(
            project["audio"], language="th", vad_filter=True, beam_size=1,
            initial_prompt=("ชื่อในเรื่อง: " + ", ".join(names)) if names else None,
        )
        segments, duration = [], float(project.get("duration") or 1)
        for seg in segments_iter:
            check_stop()
            text = seg.text.strip().replace("ํา", "ำ")  # ํา → ำ
            if text:
                segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
            set_progress("transcribe", seg.end / duration, f"ฟังเสียง {fmt_time(seg.end)} / {fmt_time(duration)} ({backend})")
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
        return f"สไตล์: {style}. ยุค/บรรยากาศ: {era}." if era else f"สไตล์: {style}."

    def stage_characters():
        project = state["project"]
        ensure_story_in_history()
        refs_dir = Path(state["folder"]) / "refs"
        needed = characters_needing_refs(project.get("context") or {}, project.get("scenes") or [])
        characters = [c for c, _count in needed]
        existing = character_refs()
        todo = [c for c in characters if c["name"] not in existing]
        for n, character in enumerate(todo, 1):
            check_stop()
            name = character["name"]
            set_progress("characters", (n - 1) / max(1, len(todo)), f"ทำรูปตัวละคร {n}/{len(todo)}: {name}")
            prompt = (
                f"ภาพอ้างอิงตัวละคร '{name}': {character_description(character)}. "
                "ภาพเต็มตัวยืนตรง หันหน้าเข้ากล้อง เห็นหน้าชัด พื้นหลังสีเทาเรียบ แสงสม่ำเสมอ ไม่มีวัตถุอื่น "
                + style_text()
            )
            with_retries(f"รูปตัวละคร {name}",
                         lambda p=prompt, nm=safe_name(name): make_image(p, [], refs_dir, nm, "1:1"))
            log(f"✓ รูปตัวละคร {name}")
        log(f"✓ รูปตัวละครครบ {len(characters)} ตัว ตามที่ปรากฏในฉาก (เปลี่ยนรูปได้ที่ {refs_dir})")

    def stage_plan():
        project, folder = state["project"], Path(state["folder"])
        ensure_story_in_history()
        segments = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        context = project.get("context") or {}
        names = [c.get("name") for c in context.get("characters", []) if c.get("name")]
        era = (context.get("story") or {}).get("era") or ""
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
                chat(plan_request(window, count, names, previous, era))))
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
                    "motion": item.get("motion") if item.get("motion") in MOTIONS else MOTIONS[len(scenes) % 4],
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
            save_project()
        log(f"✓ วางแผน {len(scenes)} ฉาก (ไม่เกิน {target} รูป)")

    def scene_prompt(scene):
        context = state["project"].get("context") or {}
        details = {c.get("name"): character_description(c) for c in context.get("characters", [])}
        who = "; ".join(f"{n}: {details.get(n, '')}" for n in scene.get("characters") or [])
        location = f" สถานที่: {scene['location']}." if scene.get("location") else ""
        return f"{scene['prompt']}{location}" + (f"\nตัวละครในภาพ — {who}" if who else "") + f"\n{style_text()}"

    def stage_images(indices=None):
        project = state["project"]
        ensure_story_in_history()
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
            ref_paths = [refs[c] for c in scene.get("characters") or [] if c in refs][:4]
            try:
                scene["image"] = with_retries(f"ฉาก {i + 1}", lambda: make_image(
                    scene_prompt(scene), ref_paths, images_dir, f"scene_{i + 1:03d}", project["aspect"]))
                scene.pop("error", None)
                failures_in_row = 0
            except (Stopped, HistoryLost):
                raise
            except Exception as exc:
                scene["error"] = str(exc)[:300]
                failures_in_row += 1
                if failures_in_row >= 3:
                    save_project()
                    raise RuntimeError("สร้างรูปล้มเหลว 3 ฉากติดกัน (เครดิตหมดหรือ Bridge มีปัญหา) — แก้แล้วกดเริ่มเพื่อทำต่อ")
            save_project()
            ui(refresh_table)
        missing = [i + 1 for i, s in enumerate(scenes) if not (s.get("image") and os.path.isfile(s["image"]))]
        if missing:
            raise RuntimeError(f"ยังขาดรูปฉาก {', '.join(map(str, missing[:15]))} — กดเริ่มอีกครั้งเพื่อลองใหม่")
        log(f"✓ รูปครบ {len(scenes)} ฉาก")

    def stage_video():
        project, folder = state["project"], Path(state["folder"])
        ffmpeg = ffmpeg_path()
        width, height = SIZES.get(project["aspect"], SIZES["16:9"])
        scenes = project["scenes"]
        work = folder / "_render"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        duration = float(project["duration"])
        durations = segment_durations([s["start"] for s in scenes], duration)
        lengths = [d + (CROSSFADE if i < len(durations) - 1 else 0) for i, d in enumerate(durations)]
        encode = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS)]

        def run(args):
            proc = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  creationflags=NO_WINDOW)
            if proc.returncode != 0:
                raise RuntimeError("FFmpeg: " + (proc.stderr or "").strip()[-500:])

        clips = []
        for i, (scene, length) in enumerate(zip(scenes, lengths)):
            check_stop()
            set_progress("video", 0.75 * i / len(scenes), f"ทำภาพเคลื่อนไหว {i + 1}/{len(scenes)}")
            frames = max(1, round(length * FPS))
            clip = work / f"clip_{i:04d}.mp4"
            run(["-i", scene["image"], "-vf", zoompan_filter(scene.get("motion", "zoom_in"), frames, width, height),
                 "-frames:v", str(frames), *encode, str(clip)])
            clips.append((clip, length))

        # Crossfade in groups of 20, then crossfade the groups together.
        def crossfade(items, out):
            graph, total = xfade_graph([length for _c, length in items])
            args = []
            for clip, _length in items:
                args += ["-i", str(clip)]
            run([*args, "-filter_complex", graph, "-map", "[vout]", *encode, str(out)])
            return out, total

        set_progress("video", 0.8, "ต่อภาพแบบจางทับกัน ...")
        groups = [crossfade(clips[k:k + 20], work / f"group_{k:04d}.mp4") for k in range(0, len(clips), 20)]
        check_stop()
        picture, _total = crossfade(groups, work / "picture.mp4") if len(groups) > 1 else groups[0]

        set_progress("video", 0.92, "ใส่เสียงบรรยาย" + (" และซับไตเติล" if project.get("subtitles") else "") + " ...")
        final = folder / f"{safe_name(Path(project['script']).stem)}_เล่าภาพ_{time.strftime('%Y%m%d-%H%M')}.mp4"
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
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(picture), *audio_args[:6],
                 "-vf", "ass=subs.ass:fontsdir=fonts",
                 *encode, *audio_args[6:], str(final)],
                cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=NO_WINDOW)
            if proc.returncode != 0:
                raise RuntimeError("FFmpeg ซับไตเติล: " + (proc.stderr or "").strip()[-500:])
        else:
            run(["-i", str(picture), *audio_args[:6], "-c:v", "copy", *audio_args[6:], str(final)])
        shutil.rmtree(work, ignore_errors=True)
        project["last_video"] = str(final)
        log(f"✓ วิดีโอเสร็จ: {final}")

    STAGE_FUNCS = {"context": stage_context, "transcribe": stage_transcribe, "characters": stage_characters,
                   "plan": stage_plan, "images": stage_images, "video": stage_video}

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
        new_refs = [(c, n) for c, n in characters_needing_refs(project.get("context") or {}, project.get("scenes") or [])
                    if c["name"] not in existing]
        missing = sum(1 for s in project.get("scenes") or [] if not (s.get("image") and os.path.isfile(s["image"])))
        total = len(new_refs) + missing
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
            + f"\nรูปฉาก {missing} รูป (เปลี่ยนภาพเฉลี่ยทุก {project['duration'] / max(1, len(project.get('scenes') or [1])):.0f} วินาที)\n"
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
        if project.get("scenes") and project.get("aspect") and project["aspect"] != aspect_var.get():
            if not messagebox.askyesno("เล่าภาพ", "เปลี่ยนสัดส่วนภาพ ต้องสร้างรูปฉากใหม่ทั้งหมด ต่อไหม?", parent=page):
                return
            for scene in project["scenes"]:
                scene.pop("image", None)
            project["done"].pop("images", None)
        wanted = int(count_var.get())
        if project.get("done", {}).get("plan") and int(project.get("image_count", wanted)) != wanted and only is None:
            if not messagebox.askyesno(
                    "เล่าภาพ", f"เปลี่ยนจำนวนรูปเป็น {wanted} ต้องวางแผนฉากใหม่ (รูปฉากเดิมจะไม่ถูกใช้) ต่อไหม?", parent=page):
                return
            project["scenes"] = []
            for key in ("plan", "images", "video"):
                project["done"].pop(key, None)
        project["image_count"] = wanted
        project.update({"aspect": aspect_var.get(),
                        "subtitles": bool(subtitle_var.get())})
        project["done"].pop("video", None)
        save_project()
        state["busy"], state["stop"] = True, False
        start_btn.config(state="disabled")

        def worker():
            current = None
            try:
                keys = [only] if only else [k for k, _t, _w in STAGES]
                confirmed = False
                for key in keys:
                    current = key
                    if project["done"].get(key) and key != "video":
                        continue
                    if key in ("characters", "images") and not confirmed:
                        if not confirm_credits():
                            log("⏸ ยังไม่สร้างรูป — แผนฉากยังอยู่ แก้แล้วกดเริ่มเพื่อทำต่อได้")
                            ui(stage_var.set, "รอยืนยันก่อนสร้างรูป")
                            return
                        confirmed = True
                    ui(refresh_stages, key)
                    log(f"▶ {dict((k, t) for k, t, _w in STAGES)[key]}")
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

    def regenerate_selected():
        project = state["project"]
        selected = sorted(int(i) for i in table.selection())
        if state["busy"] or not project or not selected:
            return
        if not messagebox.askyesno("เล่าภาพ", f"สร้างรูปใหม่ {len(selected)} ฉาก (ใช้เครดิต {len(selected)} รูป)?", parent=page):
            return
        state["busy"], state["stop"] = True, False

        def worker():
            try:
                stage_images(selected)
            except Exception as exc:
                log(f"❌ {exc}")
            finally:
                project["done"].pop("video", None)
                save_project()
                state["busy"] = False
                ui(refresh_all)
                log("สร้างรูปใหม่เสร็จ — กด 'ต่อวิดีโอใหม่' เพื่อทำวิดีโออีกรอบ")
        threading.Thread(target=worker, daemon=True).start()

    def edit_prompt(_event=None):
        selection = table.selection()
        if not selection or state["busy"]:
            return
        scene = state["project"]["scenes"][int(selection[0])]
        win = tk.Toplevel(page)
        win.title(f"แก้ฉาก {int(selection[0]) + 1}")
        win.geometry("760x360")
        text = tk.Text(win, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", scene.get("prompt", ""))

        def save():
            scene["prompt"] = text.get("1.0", tk.END).strip()
            save_project()
            refresh_table()
            win.destroy()
        make_styled_button(win, "PRIMARY", "บันทึก", command=save).pack(anchor="e", padx=8, pady=(0, 8))

    def show_preview(_event=None):
        selection = table.selection()
        scenes = (state["project"] or {}).get("scenes") or []
        if not selection or int(selection[0]) >= len(scenes):
            return
        image = scenes[int(selection[0])].get("image")
        if not image or not os.path.isfile(image):
            preview.config(image="", text=scenes[int(selection[0])].get("error") or "ยังไม่มีรูป", wraplength=280)
            return
        try:
            from PIL import Image, ImageTk
            pic = Image.open(image)
            pic.thumbnail((300, 300))
            photo = ImageTk.PhotoImage(pic)
            preview.config(image=photo, text="")
            preview.image = photo
        except Exception as exc:
            preview.config(image="", text=f"เปิดรูปไม่ได้: {exc}")

    table.bind("<Double-1>", edit_prompt)
    table.bind("<<TreeviewSelect>>", show_preview)
    g["narrate_open_project"] = open_project
    return page
