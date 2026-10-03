# -*- coding: utf-8 -*-
"""เล่าภาพ — turn a narrated story into a picture video.

Flow (each step is a button, the user decides when credits are spent):
1. เลือกไฟล์เสียงบรรยาย → read its duration.
2. วางแผนฉาก → GPT (inside the existing Prompt-Ref story history, so names
   match the Context) picks key scenes in order and writes one image prompt
   per scene.  Timing comes from where each scene's quote sits in the script.
3. สร้างรูป → one image per scene through the shared image adapter, with
   reference images from the Image page's reference folder whose file names
   appear in the prompt.
4. ต่อวิดีโอ → FFmpeg gives each image a slow zoom/pan for its time slot and
   lays the narration audio underneath.

Everything for one story lives in EXPORT_ROOT/เล่าภาพ/<story>/ (plan.json,
scene images, final MP4) so work resumes after a restart.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right")
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
FPS = 25
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ── pure helpers (unit-tested) ────────────────────────────────────────────

def safe_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", str(text or "")).strip()
    return re.sub(r"\s+", " ", cleaned)[:80] or "เรื่อง"


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def scene_count(duration: float, seconds_per_shot: float) -> int:
    if duration <= 0:
        return 0
    return max(1, min(120, round(duration / max(3.0, seconds_per_shot))))


def assign_times(story: str, quotes: list, duration: float) -> list:
    """Start time (seconds) for each scene from its quote's position in the story.

    Quotes that cannot be found are spread evenly between known neighbours;
    the first scene always starts at 0 and starts never go backwards.
    """
    n = len(quotes)
    if n == 0:
        return []
    flat = _squash(story)
    total = max(1, len(flat))
    fractions = []
    cursor = 0
    for quote in quotes:
        needle = _squash(quote)
        pos = -1
        for size in (len(needle), 12, 8):
            probe = needle[:size]
            if len(probe) >= 4:
                pos = flat.find(probe, cursor)
                if pos >= 0:
                    break
        if pos >= 0:
            fractions.append(pos / total)
            cursor = pos + 1
        else:
            fractions.append(None)
    fractions[0] = 0.0
    # Fill unknown fractions by linear interpolation.
    known = [i for i, f in enumerate(fractions) if f is not None]
    for i, f in enumerate(fractions):
        if f is not None:
            continue
        left = max(k for k in known if k < i)
        right_candidates = [k for k in known if k > i]
        if right_candidates:
            right = right_candidates[0]
            lf, rf = fractions[left], fractions[right]
        else:
            right, lf, rf = n, fractions[left], 1.0
        fractions[i] = lf + (rf - lf) * (i - left) / (right - left)
    starts = []
    minimum_gap = min(2.0, duration / (n * 2))
    for f in fractions:
        start = round(f * duration, 2)
        if starts and start < starts[-1] + minimum_gap:
            start = round(starts[-1] + minimum_gap, 2)
        starts.append(min(start, max(0.0, duration - minimum_gap)))
    return starts


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


def match_reference_files(prompt: str, folder) -> list:
    """Reference images whose file name (without extension) appears in the prompt."""
    if not folder or not os.path.isdir(str(folder)):
        return []
    text = str(prompt or "").casefold()
    found = []
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in (".png", ".jpg", ".jpeg", ".webp") or len(stem.strip()) < 2:
            continue
        if stem.strip().casefold() in text:
            found.append((stem.strip(), os.path.join(folder, name)))
    found.sort(key=lambda item: -len(item[0]))  # most specific names first
    return found[:6]


def media_duration(ffmpeg: str, path: str) -> float:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
    if not match:
        raise RuntimeError("อ่านความยาวไฟล์เสียงไม่ได้")
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def plan_request_text(count: int, duration: float) -> str:
    minutes = f"{int(duration // 60)}:{int(duration % 60):02d}"
    return (
        f"งานเล่าภาพ: ทำภาพประกอบเสียงบรรยายของบททั้งเรื่องในประวัตินี้ ความยาวเสียง {minutes} นาที. "
        f"เลือกฉากสำคัญ {count} ฉาก เรียงตามลำดับเหตุการณ์ตั้งแต่ต้นจนจบ กระจายให้ทั่วทั้งเรื่อง "
        "ให้ช่วงพีคและการเปลี่ยนสถานที่ได้ภาพของตัวเอง ภาพต่อเนื่องกันต้องไม่ซ้ำมุมกล้องเดิม. "
        "ใช้ชื่อตัวละคร สถานที่ และหน้าตาตาม Context ของเรื่องนี้ตรงตัวทุกครั้ง. "
        "ตอบ JSON เท่านั้น ห้าม markdown: "
        '{"scenes":[{"quote":"","prompt":"","motion":""}]} '
        "quote = ข้อความ 10-25 ตัวอักษรคัดลอกตรงตัวจากบท ณ จุดที่ภาพนี้เริ่มถูกบรรยาย. "
        "prompt = พรอมต์ภาษาไทยสำหรับสร้างภาพนิ่งฉากนั้น ระบุชื่อตัวละครที่อยู่ในภาพ การกระทำ สถานที่ เวลา แสง และมุมกล้อง "
        "สไตล์ภาพยนตร์สมจริง ไม่มีตัวหนังสือในภาพ. "
        f"motion = เลือกหนึ่งค่าจาก {', '.join(MOTIONS)} ให้เข้ากับอารมณ์ภาพ."
    )


# ── page ──────────────────────────────────────────────────────────────────

def install(g: dict, root: tk.Misc) -> tk.Frame:
    from snapgen_page_builder import (
        append_log, build_page, make_action_row, make_log_box, make_styled_button,
    )

    runtime = g.get("_runtime_g") or g  # live dict: Image page installs later
    page, box = build_page(root, "🎞️ เล่าภาพ — บท + เสียงบรรยาย → วิดีโอภาพประกอบ")

    state = {"plan": None, "folder": None, "busy": False, "stop": False}
    audio_var = tk.StringVar(value="ยังไม่ได้เลือกไฟล์เสียง")
    story_var = tk.StringVar(value="")
    aspect_var = tk.StringVar(value="16:9")
    seconds_var = tk.IntVar(value=7)

    # Row: story + audio + options
    top = tk.Frame(box, bg=box.cget("bg"))
    top.pack(fill="x", padx=8, pady=(6, 2))
    tk.Label(top, text="บท:", bg=top.cget("bg")).pack(side="left")
    tk.Label(top, textvariable=story_var, bg=top.cget("bg"), fg="#1D4ED8").pack(side="left", padx=(4, 16))
    tk.Label(top, text="ภาพ:", bg=top.cget("bg")).pack(side="left")
    ttk.Combobox(top, textvariable=aspect_var, values=list(SIZES), width=6, state="readonly").pack(side="left", padx=(4, 16))
    tk.Label(top, text="ภาพละประมาณ (วินาที):", bg=top.cget("bg")).pack(side="left")
    tk.Spinbox(top, from_=4, to=15, textvariable=seconds_var, width=4).pack(side="left", padx=4)

    audio_row = tk.Frame(box, bg=box.cget("bg"))
    audio_row.pack(fill="x", padx=8, pady=2)

    actions = make_action_row(box)
    actions.pack(fill="x", padx=8, pady=(4, 2))
    log_box = make_log_box(box)
    log_box.pack(side="bottom", fill="x", padx=8, pady=(2, 6))
    table_frame = tk.Frame(box, bg=box.cget("bg"))
    table_frame.pack(fill="both", expand=True, padx=8, pady=4)
    columns = ("no", "time", "quote", "prompt", "status")
    table = ttk.Treeview(table_frame, columns=columns, show="headings", height=12, selectmode="extended")
    for key, title, width in (
        ("no", "#", 40), ("time", "เวลา", 70), ("quote", "ช่วงในบท", 220),
        ("prompt", "พรอมต์ (ดับเบิลคลิกเพื่อแก้)", 520), ("status", "รูป", 90),
    ):
        table.heading(key, text=title)
        table.column(key, width=width, stretch=key in ("quote", "prompt"))
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    table.pack(side="left", fill="both", expand=True)
    scroll.pack(side="left", fill="y")
    preview = tk.Label(table_frame, bg="#F1F5F9", width=40, text="เลือกช็อตเพื่อดูรูป")
    preview.pack(side="left", fill="y", padx=(8, 0))

    def log(message):
        root.after(0, lambda m=str(message): append_log(log_box, m))

    # ── paths / persistence ──
    def export_root() -> Path:
        value = runtime.get("EXPORT_ROOT") or g.get("EXPORT_ROOT")
        return Path(str(value)) if value else Path.cwd() / "export"

    def story_title() -> str:
        try:
            meta = g["_load_prompt_ref_source_file_meta"]()
            name = meta.get("filename") or Path(str(meta.get("original_path") or "")).name
            return Path(name).stem if name else ""
        except Exception:
            return ""

    def work_folder() -> Path:
        folder = export_root() / "เล่าภาพ" / safe_name(story_title())
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_plan():
        if state["plan"] is not None:
            path = work_folder() / "plan.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state["plan"], ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)

    def load_plan():
        title = story_title()
        story_var.set(title or "— ยังไม่ได้เลือกบทใน Prompt-Ref —")
        state["plan"] = None
        if title:
            path = work_folder() / "plan.json"
            try:
                state["plan"] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state["plan"] = None
        plan = state["plan"] or {}
        if plan.get("audio"):
            audio_var.set(f"{Path(plan['audio']).name} · {fmt_time(plan.get('duration', 0))}")
            aspect_var.set(plan.get("aspect") or "16:9")
        refresh_table()

    def fmt_time(seconds) -> str:
        seconds = float(seconds or 0)
        return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

    def refresh_table():
        table.delete(*table.get_children())
        for i, scene in enumerate((state["plan"] or {}).get("scenes", [])):
            image = scene.get("image")
            status = "✓ มีรูป" if image and os.path.isfile(image) else scene.get("error", "—")[:12] or "—"
            table.insert("", "end", iid=str(i), values=(
                i + 1, fmt_time(scene.get("start", 0)), scene.get("quote", ""),
                scene.get("prompt", "").replace("\n", " "), status,
            ))

    def ffmpeg_path() -> str:
        from ai_slow2x import _ffmpeg_bin, ensure_ffmpeg_tool
        found = Path(str(_ffmpeg_bin()))
        if found.is_file():
            return str(found)
        installed = Path(str(ensure_ffmpeg_tool(log) or _ffmpeg_bin()))
        if not installed.is_file():
            raise RuntimeError("ติดตั้ง FFmpeg ไม่สำเร็จ")
        return str(installed)

    def run_in_background(label, job):
        if state["busy"]:
            messagebox.showinfo("เล่าภาพ", "กำลังทำงานอยู่ รอให้เสร็จก่อน", parent=page)
            return
        state["busy"], state["stop"] = True, False
        log(f"▶ {label}")

        def worker():
            try:
                job()
            except Exception as exc:
                log(f"❌ {label}: {exc}")
            finally:
                state["busy"] = False
                root.after(0, refresh_table)
        threading.Thread(target=worker, daemon=True).start()

    # ── step 1: audio ──
    def choose_audio():
        path = filedialog.askopenfilename(
            parent=page, title="เลือกไฟล์เสียงบรรยาย",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("All files", "*.*")],
        )
        if not path:
            return

        def job():
            duration = media_duration(ffmpeg_path(), path)
            plan = state["plan"] or {"scenes": []}
            plan.update({"audio": path, "duration": round(duration, 2), "story": story_title()})
            state["plan"] = plan
            save_plan()
            root.after(0, lambda: audio_var.set(f"{Path(path).name} · {fmt_time(duration)}"))
            log(f"เสียงยาว {fmt_time(duration)} → แนะนำ {scene_count(duration, seconds_var.get())} ภาพ")
        run_in_background("อ่านไฟล์เสียง", job)

    make_styled_button(audio_row, "SECONDARY", "🎙 เลือกไฟล์เสียงบรรยาย", command=choose_audio).pack(side="left")
    tk.Label(audio_row, textvariable=audio_var, bg=audio_row.cget("bg"), fg="#334155").pack(side="left", padx=8)

    # ── step 2: plan ──
    def plan_scenes():
        plan = state["plan"] or {}
        if not plan.get("duration"):
            messagebox.showwarning("เล่าภาพ", "เลือกไฟล์เสียงบรรยายก่อน", parent=page)
            return
        if plan.get("scenes") and not messagebox.askyesno(
            "เล่าภาพ", "มีแผนฉากอยู่แล้ว วางแผนใหม่จะแทนที่รายการเดิม (รูปเดิมยังอยู่ในโฟลเดอร์) ต่อไหม?", parent=page,
        ):
            return
        try:
            ready = g["_prompt_ref_cursor_ready"]()
        except Exception:
            ready = False
        if not ready:
            messagebox.showwarning("เล่าภาพ", "ยังไม่มีประวัติเรื่องใน Prompt-Ref — ส่งบทใน Prompt-Ref ก่อน", parent=page)
            return
        count = scene_count(plan["duration"], seconds_var.get())

        def job():
            story = g["_ensure_prompt_ref_story_text"]()
            if not story:
                raise RuntimeError("อ่านบทหลักไม่ได้")
            log(f"ให้ GPT เลือก {count} ฉากจากบทในประวัติเดิม (ไม่ส่งบทซ้ำ) ...")
            with g["_bridge_queue_lock"]:
                g["_wait_bridge_free"](log_fn=log)
                raw = g["_prompt_ref_chat"](
                    [{"role": "user", "content": plan_request_text(count, plan["duration"])}],
                    require_history=True,
                )
            parsed = g["_parse_bridge_context_json"](raw)
            scenes = [s for s in (parsed.get("scenes") or []) if isinstance(s, dict) and str(s.get("prompt") or "").strip()]
            if not scenes:
                raise RuntimeError("GPT ไม่ได้ส่งรายการฉากกลับมา")
            starts = assign_times(story, [str(s.get("quote") or "") for s in scenes], plan["duration"])
            plan["scenes"] = [{
                "quote": str(s.get("quote") or "").strip(),
                "prompt": str(s.get("prompt") or "").strip(),
                "motion": s.get("motion") if s.get("motion") in MOTIONS else MOTIONS[i % len(MOTIONS)],
                "start": start,
            } for i, (s, start) in enumerate(zip(scenes, starts))]
            plan["aspect"] = aspect_var.get()
            state["plan"] = plan
            save_plan()
            log(f"✓ วางแผนแล้ว {len(scenes)} ฉาก — ตรวจ/แก้พรอมต์ได้ แล้วกดสร้างรูป")
        run_in_background("วางแผนฉาก", job)

    # ── step 3: images ──
    def generate(indices):
        plan = state["plan"] or {}
        scenes = plan.get("scenes") or []
        if not scenes:
            messagebox.showwarning("เล่าภาพ", "กดวางแผนฉากก่อน", parent=page)
            return
        if not indices:
            messagebox.showinfo("เล่าภาพ", "ทุกฉากมีรูปแล้ว", parent=page)
            return
        plan["aspect"] = aspect_var.get()
        folder = work_folder()

        def job():
            ref_folder = (runtime.get("img_ref_folder") or [None])[0]
            encode = runtime.get("_encode_image_b64") or g.get("_encode_image_b64")
            do_request = runtime.get("_do_image_request") or g.get("_do_image_request")
            for count, i in enumerate(indices, 1):
                if state["stop"]:
                    log("⏹ หยุดแล้ว")
                    break
                scene = scenes[i]
                refs = match_reference_files(scene["prompt"], ref_folder)
                prompt = scene["prompt"]
                payload = {"prompt": prompt, "aspect_ratio": plan["aspect"], "_use_story_history": True}
                if refs and encode:
                    payload["images"] = [encode(path) for _name, path in refs]
                    prompt += "\n\nATTACHED REFERENCES: use each file only for the named identity or place; follow this scene for action, lighting and composition.\n" + "\n".join(
                        f"Image {n}: {name}" for n, (name, _path) in enumerate(refs, 1)
                    )
                    payload["prompt"] = prompt
                log(f"[{count}/{len(indices)}] ฉาก {i + 1}" + (f" · แนบ {', '.join(n for n, _ in refs)}" if refs else ""))
                try:
                    out = do_request(payload, is_edit=bool(payload.get("images")), prompt=prompt,
                                     name_hint=f"narrate_{i + 1:02d}", output_dir=str(folder))
                    target = folder / f"scene_{i + 1:03d}{Path(out).suffix or '.png'}"
                    if Path(out).resolve() != target.resolve():
                        shutil.move(str(out), str(target))
                    scene["image"] = str(target)
                    scene.pop("error", None)
                except Exception as exc:
                    scene["error"] = "ผิดพลาด"
                    log(f"❌ ฉาก {i + 1}: {exc}")
                save_plan()
                root.after(0, refresh_table)
            log("✓ สร้างรูปรอบนี้เสร็จ")
            notify = g.get("_snapgen_notify_done")
            if callable(notify):
                root.after(0, notify)
        run_in_background("สร้างรูป", job)

    def generate_missing():
        scenes = (state["plan"] or {}).get("scenes") or []
        generate([i for i, s in enumerate(scenes) if not (s.get("image") and os.path.isfile(s["image"]))])

    def generate_selected():
        generate(sorted(int(iid) for iid in table.selection()))

    def stop():
        state["stop"] = True
        log("จะหยุดหลังฉากที่กำลังทำเสร็จ")

    # ── step 4: video ──
    def build_video():
        plan = state["plan"] or {}
        scenes = plan.get("scenes") or []
        missing = [i + 1 for i, s in enumerate(scenes) if not (s.get("image") and os.path.isfile(s["image"]))]
        if not scenes or not plan.get("audio"):
            messagebox.showwarning("เล่าภาพ", "ต้องมีไฟล์เสียงและแผนฉากก่อน", parent=page)
            return
        if missing:
            messagebox.showwarning("เล่าภาพ", f"ยังไม่มีรูปฉาก: {', '.join(map(str, missing[:20]))}", parent=page)
            return
        width, height = SIZES.get(aspect_var.get(), SIZES["16:9"])
        folder = work_folder()

        def job():
            ffmpeg = ffmpeg_path()
            seg_dir = folder / "segments"
            shutil.rmtree(seg_dir, ignore_errors=True)
            seg_dir.mkdir()
            starts = [float(s.get("start", 0)) for s in scenes]
            durations = segment_durations(starts, float(plan["duration"]))
            listing = []
            for i, (scene, seconds) in enumerate(zip(scenes, durations)):
                if state["stop"]:
                    log("⏹ หยุดแล้ว")
                    return
                frames = max(1, round(seconds * FPS))
                out = seg_dir / f"seg_{i + 1:03d}.mp4"
                log(f"ต่อภาพ {i + 1}/{len(scenes)} ({seconds:.1f} วิ, {scene.get('motion')})")
                subprocess.run([
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", scene["image"],
                    "-vf", zoompan_filter(scene.get("motion", "zoom_in"), frames, width, height),
                    "-frames:v", str(frames), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "20", "-pix_fmt", "yuv420p", str(out),
                ], check=True, creationflags=NO_WINDOW)
                listing.append(f"file '{out.as_posix()}'")
            concat = seg_dir / "list.txt"
            concat.write_text("\n".join(listing) + "\n", encoding="utf-8")
            final = folder / f"{safe_name(story_title())}_เล่าภาพ_{time.strftime('%Y%m%d-%H%M')}.mp4"
            log("รวมภาพกับเสียงบรรยาย ...")
            subprocess.run([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat), "-i", plan["audio"],
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart", str(final),
            ], check=True, creationflags=NO_WINDOW)
            shutil.rmtree(seg_dir, ignore_errors=True)
            plan["last_video"] = str(final)
            save_plan()
            log(f"✓ วิดีโอเสร็จ: {final}")
            notify = g.get("_snapgen_notify_done")
            if callable(notify):
                root.after(0, notify)
        run_in_background("ต่อวิดีโอ", job)

    def open_folder():
        try:
            os.startfile(str(work_folder()))  # type: ignore[attr-defined]
        except Exception as exc:
            log(f"เปิดโฟลเดอร์ไม่ได้: {exc}")

    # ── editing / preview ──
    def edit_prompt(_event=None):
        selection = table.selection()
        if not selection:
            return
        i = int(selection[0])
        scene = state["plan"]["scenes"][i]
        win = tk.Toplevel(page)
        win.title(f"แก้พรอมต์ฉาก {i + 1}")
        win.geometry("720x320")
        text = tk.Text(win, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", scene.get("prompt", ""))

        def save():
            scene["prompt"] = text.get("1.0", tk.END).strip()
            save_plan()
            refresh_table()
            win.destroy()
        make_styled_button(win, "PRIMARY", "บันทึก", command=save).pack(anchor="e", padx=8, pady=(0, 8))

    def show_preview(_event=None):
        selection = table.selection()
        scenes = (state["plan"] or {}).get("scenes") or []
        if not selection or int(selection[0]) >= len(scenes):
            return
        image = scenes[int(selection[0])].get("image")
        if not image or not os.path.isfile(image):
            preview.config(image="", text="ยังไม่มีรูป")
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

    make_styled_button(actions, "PRIMARY", "1 วางแผนฉาก", command=plan_scenes).pack(side="left", padx=4)
    make_styled_button(actions, "PRIMARY", "2 สร้างรูปที่ยังไม่มี", command=generate_missing).pack(side="left", padx=4)
    make_styled_button(actions, "SECONDARY", "สร้างใหม่ช็อตที่เลือก", command=generate_selected).pack(side="left", padx=4)
    make_styled_button(actions, "DANGER", "หยุด", command=stop).pack(side="left", padx=4)
    make_styled_button(actions, "PRIMARY", "3 ต่อวิดีโอ", command=build_video).pack(side="left", padx=4)
    make_styled_button(actions, "SECONDARY", "เปิดโฟลเดอร์", command=open_folder).pack(side="left", padx=4)

    page.bind("<Map>", lambda _e: load_plan() if not state["busy"] else None, add="+")
    g["narrate_reload"] = load_plan
    return page
