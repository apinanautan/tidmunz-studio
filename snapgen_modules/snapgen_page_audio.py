# -*- coding: utf-8 -*-
"""Local audio cutter/converter. Audio never leaves this computer."""
from __future__ import annotations

import subprocess
import threading
import sys
import tempfile
import winsound
import re
import time
import ctypes
import json
import urllib.request
from array import array
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()


def install(g: dict, root: tk.Misc) -> tk.Frame:
    from snapgen_page_builder import build_page

    page, box = build_page(root, "✂️ ตัดเสียง / แปลงเสียง")
    source_var = tk.StringVar()
    start_var = tk.StringVar(value="00:00:00")
    end_var = tk.StringVar(value="")
    format_var = tk.StringVar(value="MP3")
    status_var = tk.StringVar(value="พร้อม")
    story_title_var = tk.StringVar(value="—")
    custom_name_enabled = tk.BooleanVar(value=False)
    custom_name_var = tk.StringVar(value="")
    busy = [False]
    quick_buttons = {}
    history_buttons = {}
    waveform = {
        "samples": [], "duration": 0.0, "start": 0.0, "end": 0.0,
        "drag": None, "items": {}, "playing": False, "play_token": 0,
        "preview": None, "playhead": 0.0, "play_started_at": 0.0,
        "play_started_from": 0.0, "play_duration": 0.0,
        "wave_photo": None,
        "seek_after": None, "resume_after_seek": False,
        "player_alias": "snapgen_audio_preview",
        "preview_source_start": 0.0,
    }

    def save_last_audio(path=""):
        try:
            config = g.get("load_config", lambda: {})() or {}
            config["audio_editor_last_file"] = str(path or "")
            g.get("save_config", lambda _config: None)(config)
        except Exception:
            pass

    def save_story_title(path: Path, title: str):
        try:
            config = g.get("load_config", lambda: {})() or {}
            cache = config.get("audio_editor_story_titles")
            if not isinstance(cache, dict):
                cache = {}
            cache[str(path.resolve())] = title
            config["audio_editor_story_titles"] = cache
            g.get("save_config", lambda _config: None)(config)
        except Exception:
            pass

    def cached_story_title(path: Path) -> str:
        try:
            config = g.get("load_config", lambda: {})() or {}
            cache = config.get("audio_editor_story_titles") or {}
            return str(cache.get(str(path.resolve())) or "").strip()
        except Exception:
            return ""

    def seconds(value: str) -> float:
        parts = value.strip().split(":")
        if not 1 <= len(parts) <= 3:
            raise ValueError("เวลาใช้รูปแบบ ชั่วโมง:นาที:วินาที")
        numbers = [float(part) for part in parts]
        if any(number < 0 for number in numbers):
            raise ValueError("เวลาต้องไม่ติดลบ")
        if len(numbers) == 3:
            return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        if len(numbers) == 2:
            return numbers[0] * 60 + numbers[1]
        return numbers[0]

    def clock(value: float) -> str:
        value = max(0.0, float(value))
        hours, rest = divmod(value, 3600)
        minutes, secs = divmod(rest, 60)
        if hours >= 1:
            return f"{int(hours):02d}:{int(minutes):02d}:{secs:04.1f}"
        return f"{int(minutes):02d}:{secs:04.1f}"

    def smart_audio_name(path: Path) -> str:
        """Keep useful source title; remove common download/edit noise."""
        name = path.stem.strip()
        name = re.sub(r"^เสียง[\s_-]*", "", name, flags=re.IGNORECASE)
        name = re.split(
            r"\s*[-_–—|]\s*(?:remix|รีมิกซ์|มิกซ์|mix|edited|edit|ตัด|cut|ใหม่)\b",
            name, maxsplit=1, flags=re.IGNORECASE,
        )[0]
        name = re.sub(
            r"[\s_-]*(?:remix|รีมิกซ์|มิกซ์|mix|edited|edit|ตัด|cut)(?:[\s_-]*\d+)?\s*$",
            "", name, flags=re.IGNORECASE,
        )
        name = re.sub(r"\s*\((?:remix|รีมิกซ์|edit|edited|cut)[^)]*\)\s*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"[_]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" ._-")
        return name or "เสียง"

    def analyze_story_title(source: Path):
        cached = cached_story_title(source)
        fallback = smart_audio_name(source)
        if cached:
            story_title_var.set(cached)
            custom_name_var.set(cached)
            return
        story_title_var.set("กำลังวิเคราะห์...")

        def worker():
            title = fallback
            try:
                body = {
                    "model": "auto",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "คุณตั้งชื่อเรื่องภาษาไทยจากชื่อไฟล์เสียง ตอบเฉพาะชื่อเรื่องสั้นๆ เท่านั้น "
                                "ห้ามมีคำว่า เรื่อง, เสียง, remix, รีมิกซ์, online-audio-converter, นามสกุลไฟล์, "
                                "เลขลำดับดาวน์โหลด หรือวงเล็บเกินจำเป็น ห้ามอธิบาย"
                            ),
                        },
                        {"role": "user", "content": f"ชื่อไฟล์: {source.name}"},
                    ],
                    "temperature": 0.1,
                }
                request = urllib.request.Request(
                    str(g.get("CHATGPT_API_BASE", "http://127.0.0.1:8000/v1")).rstrip("/") + "/chat/completions",
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {g.get('CHATGPT_API_KEY', 'local-dev-key')}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    data = json.loads(response.read().decode("utf-8", errors="replace"))
                answer = str(data["choices"][0]["message"]["content"] or "").strip()
                answer = answer.strip('"“”').removeprefix("เรื่อง").lstrip(" :：-").strip()
                if answer and len(answer) <= 100:
                    title = answer
            except Exception:
                title = fallback
            save_story_title(source, title)

            def finish():
                if Path(source_var.get().strip()) == source:
                    story_title_var.set(title)
                    custom_name_var.set(title)
            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def exported_slots(source: Path) -> set[int]:
        output_dir = source.parent / "audio_output"
        done = set()
        if not output_dir.is_dir():
            return done
        for output in output_dir.iterdir():
            if not output.is_file() or output.suffix.lower() not in {".mp3", ".wav", ".m4a"}:
                continue
            match = re.search(r"(?:^|\s)(\d{1,2})(?:_\d+)?$", output.stem)
            if match:
                number = int(match.group(1))
                if 1 <= number <= 20:
                    done.add(number)
        return done

    def range_index_path(source: Path) -> Path:
        return source.parent / "audio_output" / "snapgen_audio_ranges.json"

    def load_saved_ranges(source: Path) -> dict:
        if not source.is_file():
            return {}
        try:
            data = json.loads(range_index_path(source).read_text(encoding="utf-8"))
            ranges = data.get(str(source.resolve()), {}) if isinstance(data, dict) else {}
            return ranges if isinstance(ranges, dict) else {}
        except Exception:
            return {}

    def save_slot_range(source: Path, slot_number: int, start: float, end: float, output: Path):
        path = range_index_path(source)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        source_ranges = data.setdefault(str(source.resolve()), {})
        source_ranges[str(slot_number)] = {
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "file": str(output),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def refresh_quick_buttons():
        source = Path(source_var.get().strip())
        done = exported_slots(source) if source.is_file() else set()
        saved_ranges = load_saved_ranges(source)
        for number, button in quick_buttons.items():
            finished = number in done
            button._audio_quick_finished = finished
            button.config(
                bg="#DCEEFF" if finished else "#F1F5F9",
                activebackground="#C7E3FA" if finished else "#E2E8F0",
                fg="#1E5F8A" if finished else "#334155",
                activeforeground="#1E5F8A" if finished else "#334155",
            )
        for number, button in history_buttons.items():
            available = str(number) in saved_ranges
            button._audio_quick_finished = available
            button.config(
                state="normal" if available else "disabled",
                bg="#DCEEFF" if available else "#F1F5F9",
                activebackground="#C7E3FA" if available else "#F1F5F9",
                fg="#1E5F8A" if available else "#94A3B8",
                disabledforeground="#94A3B8",
            )

    def restore_saved_range(number: int):
        source = Path(source_var.get().strip())
        row = load_saved_ranges(source).get(str(number))
        if not isinstance(row, dict):
            return
        try:
            start = max(0.0, float(row["start"]))
            end = min(waveform["duration"], float(row["end"]))
            if end <= start:
                return
            stop_preview()
            waveform.update(start=start, end=end, playhead=start, drag=None)
            start_var.set(clock(start))
            end_var.set(clock(end))
            update_waveform_selection()
            status_var.set(f"ช่วง {number}: {clock(start)} – {clock(end)}")
        except Exception:
            return

    form = tk.Frame(box, bg="#FFFFFF")
    # Layout contract: controls stay pinned to bottom; waveform owns every
    # remaining pixel and grows with the window instead of leaving dead space.
    box.grid_columnconfigure(0, weight=1)
    box.grid_rowconfigure(0, weight=1)
    form.grid(row=0, column=0, sticky="nsew")
    form.grid_columnconfigure(1, weight=1)
    form.grid_rowconfigure(1, weight=1, minsize=180)

    # Keep story title inside the page heading. Never spend a separate content
    # row on it; that row compresses waveform and quick-slot controls.
    title_header = tk.Frame(box, bg="#FFFFFF")
    tk.Label(
        title_header, text="✂️ ตัดเสียง / แปลงเสียง", bg="#FFFFFF", fg="#1A1A1A",
        font=(SNAPGEN_UI_FONT, 11, "bold"),
    ).pack(side="left")
    tk.Label(
        title_header, text="   เรื่อง:", bg="#FFFFFF", fg="#64748B",
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left")
    tk.Label(
        title_header, textvariable=story_title_var, bg="#FFFFFF", fg="#334155",
        font=(SNAPGEN_UI_FONT, 9, "bold"), anchor="w",
    ).pack(side="left", padx=(5, 0))
    box.configure(labelwidget=title_header)

    def label(text, row):
        tk.Label(form, text=text, bg="#FFFFFF", fg="#374151", anchor="w",
                 font=(SNAPGEN_UI_FONT, 10)).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)

    label("ไฟล์เสียง", 0)
    tk.Entry(form, textvariable=source_var, relief="solid", bd=1,
             font=(SNAPGEN_UI_FONT, 10)).grid(row=0, column=1, sticky="ew", pady=5)

    def set_source(path):
        source = Path(str(path or "")).expanduser()
        if source.is_file() and source.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}:
            source_var.set(str(source))
            custom_name_var.set(smart_audio_name(source))
            status_var.set(source.name)
            save_last_audio(source)
            load_waveform(source)
            analyze_story_title(source)
            refresh_quick_buttons()
            return True
        status_var.set("ไฟล์นี้ไม่ใช่ไฟล์เสียงที่รองรับ")
        return False

    def choose_source():
        path = filedialog.askopenfilename(
            title="เลือกไฟล์เสียง",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma"), ("All files", "*.*")],
        )
        if path:
            set_source(path)

    tk.Button(form, text="เลือกไฟล์", command=choose_source, bg="#2563EB", fg="white",
              relief="flat", padx=16, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold")).grid(
                  row=0, column=2, padx=(8, 0), pady=5)

    waveform_canvas = tk.Canvas(
        form, height=180, bg="#FFFFFF", highlightthickness=0,
        cursor="sb_h_double_arrow",
    )
    waveform_canvas.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(10, 6))

    label("เวลาเริ่ม", 2)
    time_row = tk.Frame(form, bg="#FFFFFF")
    time_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
    tk.Entry(time_row, textvariable=start_var, width=14, relief="solid", bd=1,
             font=(SNAPGEN_UI_FONT, 10)).pack(side="left")
    tk.Label(time_row, text="เวลาจบ", bg="#FFFFFF", fg="#374151",
             font=(SNAPGEN_UI_FONT, 10)).pack(side="left", padx=(18, 8))
    tk.Entry(time_row, textvariable=end_var, width=14, relief="solid", bd=1,
             font=(SNAPGEN_UI_FONT, 10)).pack(side="left")
    tk.Label(time_row, text="เว้นว่าง = จนจบไฟล์", bg="#FFFFFF", fg="#9CA3AF",
             font=(SNAPGEN_UI_FONT, 9)).pack(side="left", padx=8)

    label("รูปแบบ", 3)
    format_row = tk.Frame(form, bg="#FFFFFF")
    format_row.grid(row=3, column=1, columnspan=2, sticky="w", pady=5)
    menu = tk.OptionMenu(format_row, format_var, "MP3", "WAV", "M4A")
    menu.config(bg="#F3F4F6", relief="flat", width=8, font=(SNAPGEN_UI_FONT, 9))
    menu.pack(side="left")
    play_btn = tk.Button(
        format_row, text="เล่น", bg="#059669", activebackground="#047857",
        fg="white", activeforeground="white", relief="flat", padx=18, pady=6,
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    )
    play_btn.pack(side="left", padx=(8, 0))

    def move_playhead_to(side):
        stop_preview()
        waveform["playhead"] = waveform["start"] if side == "left" else waveform["end"]
        update_waveform_selection()

    def move_edge_to_playhead(side):
        # While preview is running, capture exact current position before moving
        # either trim edge. Playback continues; only selected export range changes.
        was_playing = bool(waveform["playing"])
        if was_playing and waveform["play_started_at"]:
            elapsed = max(0.0, time.perf_counter() - waveform["play_started_at"])
            waveform["playhead"] = min(
                waveform["end"], waveform["play_started_from"] + elapsed
            )
        minimum_gap = min(0.1, waveform["duration"])
        if side == "left":
            waveform["start"] = min(waveform["playhead"], waveform["end"] - minimum_gap)
            start_var.set(clock(waveform["start"]))
        else:
            waveform["end"] = max(waveform["playhead"], waveform["start"] + minimum_gap)
            end_var.set(clock(waveform["end"]))
        update_waveform_selection()
        if side == "right" and was_playing:
            stop_preview()

    def select_next_segment():
        """Continue cutting: old end becomes new start; new end is file end."""
        stop_preview()
        duration = max(0.0, float(waveform["duration"] or 0.0))
        current_end = max(0.0, min(duration, float(waveform["end"] or 0.0)))
        if duration <= 0:
            status_var.set("เลือกไฟล์เสียงก่อน")
            return
        if duration - current_end < 0.1:
            status_var.set("อยู่ท่อนสุดท้ายแล้ว")
            return
        waveform.update(
            start=current_end, end=duration, playhead=current_end, drag=None,
        )
        start_var.set(clock(current_end))
        end_var.set(clock(duration))
        update_waveform_selection()
        status_var.set(f"ท่อนถัดไป: {clock(current_end)} – {clock(duration)}")

    tk.Button(
        format_row, text="◀", command=lambda: move_playhead_to("left"),
        bg="#E0F2FE", activebackground="#BAE6FD", fg="#0369A1",
        relief="flat", width=3, height=1, padx=0, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(4, 0))
    tk.Button(
        format_row, text="▶", command=lambda: move_playhead_to("right"),
        bg="#E0F2FE", activebackground="#BAE6FD", fg="#0369A1",
        relief="flat", width=3, height=1, padx=0, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(4, 0))
    tk.Button(
        format_row, text="|◀", command=lambda: move_edge_to_playhead("left"),
        bg="#E0F2FE", activebackground="#BAE6FD", fg="#0369A1",
        relief="flat", width=3, height=1, padx=0, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(10, 0))
    tk.Button(
        format_row, text="▶|", command=lambda: move_edge_to_playhead("right"),
        bg="#E0F2FE", activebackground="#BAE6FD", fg="#0369A1",
        relief="flat", width=3, height=1, padx=0, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(4, 0))
    tk.Button(
        format_row, text="⏭ ท่อนถัดไป", command=select_next_segment,
        bg="#EFF6FF", activebackground="#DBEAFE",
        fg="#315A75", activeforeground="#315A75",
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground="#BFDBFE", highlightcolor="#93C5FD",
        padx=12, pady=6, font=(SNAPGEN_UI_FONT, 9, "bold"),
    ).pack(side="left", padx=(10, 0))

    tk.Label(box, textvariable=status_var, bg="#FFFFFF", fg="#475569", anchor="w",
             font=(SNAPGEN_UI_FONT, 9)).grid(row=1, column=0, sticky="ew", pady=(7, 0))

    def write(message):
        try:
            import snapgen_error_reporter
            snapgen_error_reporter.report_log(message, "audio page")
        except Exception:
            pass
        root.after(0, lambda: status_var.set(str(message)))
    g["_audio_log"] = write

    def ffmpeg_path():
        from ai_slow2x import _ffmpeg_bin, ensure_ffmpeg_tool
        found = Path(str(_ffmpeg_bin()))
        if found.is_file():
            return str(found)
        installed = ensure_ffmpeg_tool(write)
        candidate = Path(str(installed or _ffmpeg_bin()))
        if not candidate.is_file():
            raise RuntimeError("ติดตั้ง FFmpeg ไม่สำเร็จ")
        return str(candidate)

    def draw_waveform(_event=None):
        canvas = waveform_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 110)
        margin = 18
        usable = max(width - margin * 2, 1)
        middle = height / 2
        samples = waveform["samples"]
        duration = waveform["duration"]
        canvas.create_line(margin, middle, width - margin, middle, fill="#E0F2FE")
        if not samples or duration <= 0:
            canvas.create_text(width / 2, middle, text="ลากไฟล์เสียงมาวาง หรือกดเลือกไฟล์",
                               fill="#8CAAC4", font=(SNAPGEN_UI_FONT, 10))
            return
        # Render the real waveform into one cached bitmap. Windows Tk Canvas is
        # software-rendered; retaining an 800-point polygon makes every handle
        # move repaint that geometry. A PhotoImage turns it into one cheap blit.
        step = usable / max(len(samples) - 1, 1)
        amplitude = height * 0.36
        upper = [
            (margin + index * step, middle - max(1.0, peak * amplitude))
            for index, peak in enumerate(samples)
        ]
        lower = [
            (margin + index * step, middle + max(1.0, peak * amplitude))
            for index, peak in reversed(list(enumerate(samples)))
        ]
        try:
            from PIL import Image, ImageDraw, ImageTk
            scale = 2
            image = Image.new("RGB", (width * scale, height * scale), "#FFFFFF")
            painter = ImageDraw.Draw(image)
            painter.line(
                [(margin * scale, middle * scale), ((width - margin) * scale, middle * scale)],
                fill="#E0F2FE", width=2,
            )
            polygon = [
                (int(x * scale), int(y * scale)) for x, y in upper + lower
            ]
            painter.polygon(polygon, fill="#7DD3FC")
            image = image.resize((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            waveform["wave_photo"] = photo
            canvas.create_image(0, 0, image=photo, anchor="nw")
        except Exception:
            points = [coordinate for point in upper + lower for coordinate in point]
            canvas.create_polygon(points, fill="#7DD3FC", outline="")
        waveform["items"] = {
            "start": canvas.create_rectangle(0, 0, 0, height, fill="#38BDF8", outline=""),
            "end": canvas.create_rectangle(0, 0, 0, height, fill="#38BDF8", outline=""),
            "start_grip": canvas.create_rectangle(0, 0, 0, 0, fill="#0EA5E9", outline=""),
            "end_grip": canvas.create_rectangle(0, 0, 0, 0, fill="#0EA5E9", outline=""),
            "start_text": canvas.create_text(0, height - 8, fill="#0284C7", anchor="s", font=(SNAPGEN_UI_FONT, 8, "bold")),
            "end_text": canvas.create_text(0, height - 8, fill="#0284C7", anchor="s", font=(SNAPGEN_UI_FONT, 8, "bold")),
            "range_duration": canvas.create_text(
                0, 0, fill="#94A3B8", anchor="center",
                font=(SNAPGEN_UI_FONT, 10, "bold"),
            ),
            "playhead": canvas.create_line(0, 0, 0, height, fill="#2563EB", width=2, dash=(4, 4)),
            "playhead_text": canvas.create_text(0, 10, fill="#2563EB", anchor="n", font=(SNAPGEN_UI_FONT, 8, "bold")),
            "playhead_box": canvas.create_rectangle(0, 5, 0, 29, fill="#FFFFFF", outline=""),
        }
        canvas.tag_raise(waveform["items"]["playhead_text"])
        update_waveform_selection()

    def update_waveform_selection():
        if not waveform["items"] or waveform["duration"] <= 0:
            return
        canvas = waveform_canvas
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 110)
        margin = 18
        usable = max(width - margin * 2, 1)
        start_x = margin + usable * waveform["start"] / waveform["duration"]
        end_x = margin + usable * waveform["end"] / waveform["duration"]
        playhead_x = margin + usable * waveform["playhead"] / waveform["duration"]
        items = waveform["items"]
        canvas.coords(items["start"], start_x - 2, 0, start_x + 2, height - 28)
        canvas.coords(items["end"], end_x - 2, 0, end_x + 2, height - 28)
        canvas.coords(items["start_grip"], start_x - 12, height - 27, start_x + 12, height)
        canvas.coords(items["end_grip"], end_x - 12, height - 27, end_x + 12, height)
        canvas.coords(items["start_text"], start_x, height - 7)
        canvas.coords(items["end_text"], end_x, height - 7)
        canvas.itemconfigure(items["start_text"], text=clock(waveform["start"]))
        canvas.itemconfigure(items["end_text"], text=clock(waveform["end"]))
        canvas.coords(items["range_duration"], (start_x + end_x) / 2, height * 0.72)
        canvas.itemconfigure(
            items["range_duration"],
            text=clock(max(0.0, waveform["end"] - waveform["start"])),
        )
        canvas.coords(items["playhead"], playhead_x, 0, playhead_x, height)
        playhead_text = clock(waveform["playhead"])
        text_width = max(52, len(playhead_text) * 8)
        canvas.coords(items["playhead_box"], playhead_x - text_width / 2, 5, playhead_x + text_width / 2, 29)
        canvas.coords(items["playhead_text"], playhead_x, 8)
        canvas.itemconfigure(items["playhead_text"], text=playhead_text)

    def load_waveform(path):
        status_var.set("กำลังอ่านคลื่นเสียง...")

        def worker():
            try:
                rate = 8000
                command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", str(path),
                           "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
                result = subprocess.run(command, capture_output=True,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode != 0 or not result.stdout:
                    raise RuntimeError((result.stderr.decode("utf-8", "replace") or "อ่านเสียงไม่ได้")[-500:])
                pcm = array("h")
                pcm.frombytes(result.stdout)
                if sys.byteorder != "little":
                    pcm.byteswap()
                duration = len(pcm) / rate
                # 800 bars stay detailed at desktop width but redraw smoothly while dragging.
                bucket_count = 800
                bucket_size = max(1, len(pcm) // bucket_count)
                peaks = []
                for offset in range(0, len(pcm), bucket_size):
                    block = pcm[offset:offset + bucket_size]
                    peaks.append(max((abs(value) for value in block), default=0) / 32768.0)

                def finish():
                    waveform.update(samples=peaks, duration=duration, start=0.0, end=duration,
                                    playhead=0.0, drag=None)
                    start_var.set(clock(0))
                    end_var.set(clock(duration))
                    status_var.set(f"{Path(path).name} — {clock(duration)}")
                    draw_waveform()
                root.after(0, finish)
            except Exception as exc:
                write(f"อ่านคลื่นเสียงไม่ได้: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def waveform_x_to_seconds(x):
        width = max(waveform_canvas.winfo_width(), 400)
        margin = 18
        ratio = min(1.0, max(0.0, (x - margin) / max(width - margin * 2, 1)))
        return ratio * waveform["duration"]

    def waveform_press(event):
        if waveform["duration"] <= 0:
            return
        value = waveform_x_to_seconds(event.x)
        width = max(waveform_canvas.winfo_width(), 400)
        height = max(waveform_canvas.winfo_height(), 110)
        seconds_per_pixel = waveform["duration"] / max(width - 36, 1)
        grip_range = seconds_per_pixel * 16
        if event.y >= height - 34 and min(
            abs(value - waveform["start"]), abs(value - waveform["end"])
        ) <= grip_range:
            waveform["drag"] = (
                "start" if abs(value - waveform["start"]) <= abs(value - waveform["end"])
                else "end"
            )
        else:
            waveform["drag"] = "playhead"
        waveform_drag(event)

    def apply_waveform_drag(x):
        value = waveform_x_to_seconds(x)
        if not waveform["drag"] or waveform["duration"] <= 0:
            return
        minimum_gap = min(0.1, waveform["duration"])
        if waveform["drag"] == "start":
            waveform["start"] = min(value, waveform["end"] - minimum_gap)
            if waveform["playing"]:
                waveform["playhead"] = waveform["start"]
                schedule_live_seek()
            else:
                waveform["playhead"] = max(waveform["playhead"], waveform["start"])
            start_var.set(clock(waveform["start"]))
        elif waveform["drag"] == "end":
            waveform["end"] = max(value, waveform["start"] + minimum_gap)
            if waveform["playing"]:
                waveform["playhead"] = waveform["end"]
                schedule_live_seek()
            else:
                waveform["playhead"] = min(waveform["playhead"], waveform["end"])
            end_var.set(clock(waveform["end"]))
        else:
            waveform["playhead"] = min(waveform["end"], max(waveform["start"], value))
            schedule_live_seek()
        update_waveform_selection()

    def waveform_drag(event):
        if not waveform["drag"] or waveform["duration"] <= 0:
            return
        # Waveform is now one cached bitmap, so moving controls is cheap. Update
        # the visible playhead directly in this mouse event; audio seek remains
        # throttled separately and can never delay the line under the cursor.
        apply_waveform_drag(event.x)

    def waveform_release(_event):
        if waveform["drag"] in ("playhead", "start", "end") and waveform["playing"]:
            if waveform.get("seek_after") is not None:
                try:
                    root.after_cancel(waveform["seek_after"])
                except Exception:
                    pass
                waveform["seek_after"] = None
            seek_preview_live()
        waveform["drag"] = None

    waveform_canvas.bind("<Configure>", draw_waveform)
    waveform_canvas.bind("<Button-1>", waveform_press)
    waveform_canvas.bind("<B1-Motion>", waveform_drag)
    waveform_canvas.bind("<ButtonRelease-1>", waveform_release)

    def install_audio_drop():
        try:
            project_root = Path(__file__).resolve().parent.parent
            vendor = project_root / "vendor"
            if str(vendor) not in sys.path:
                sys.path.insert(0, str(vendor))
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD.require(root)

            def dropped(event):
                files = root.tk.splitlist(event.data)
                if files:
                    set_source(files[0])
                return event.action

            waveform_canvas.drop_target_register(DND_FILES)
            waveform_canvas.dnd_bind("<<Drop>>", dropped)
            return True
        except Exception:
            return False

    install_audio_drop()

    def mci(command):
        error = ctypes.windll.winmm.mciSendStringW(command, None, 0, None)
        if error:
            raise RuntimeError(f"Windows audio error {error}")

    def close_mci():
        try:
            mci(f'close {waveform["player_alias"]}')
        except Exception:
            pass

    def seek_preview_live():
        waveform["seek_after"] = None
        if not waveform["playing"] or not waveform.get("preview"):
            return
        try:
            alias = waveform["player_alias"]
            milliseconds = max(0, int(
                (waveform["playhead"] - waveform["preview_source_start"]) * 1000
            ))
            mci(f'seek {alias} to {milliseconds}')
            mci(f'play {alias}')
            waveform["play_started_at"] = time.perf_counter()
            waveform["play_started_from"] = waveform["playhead"]
        except Exception:
            pass

    def schedule_live_seek():
        if waveform["playing"] and waveform["seek_after"] is None:
            waveform["seek_after"] = root.after(40, seek_preview_live)

    def update_playhead_during_preview(token):
        if not waveform["playing"] or token != waveform["play_token"]:
            return
        if waveform.get("drag") in ("playhead", "start", "end"):
            # User owns the playhead while scrubbing any marker. Do not let the
            # playback timer pull it back to the old audio position.
            root.after(16, lambda: update_playhead_during_preview(token))
            return
        elapsed = max(0.0, time.perf_counter() - waveform["play_started_at"])
        waveform["playhead"] = min(
            waveform["end"], waveform["play_started_from"] + elapsed
        )
        update_waveform_selection()
        if waveform["playhead"] < waveform["end"]:
            root.after(16, lambda: update_playhead_during_preview(token))
        elif token == waveform["play_token"]:
            stop_preview(preserve_playhead=True)

    def stop_preview(preserve_playhead=False):
        held_playhead = waveform["playhead"]
        if not preserve_playhead and waveform["playing"] and waveform["play_started_at"]:
            elapsed = max(0.0, time.perf_counter() - waveform["play_started_at"])
            waveform["playhead"] = min(
                waveform["end"], waveform["play_started_from"] + elapsed
            )
            update_waveform_selection()
        waveform["play_token"] += 1
        waveform["playing"] = False
        waveform["play_started_at"] = 0.0
        if waveform.get("seek_after") is not None:
            try:
                root.after_cancel(waveform["seek_after"])
            except Exception:
                pass
            waveform["seek_after"] = None
        close_mci()
        winsound.PlaySound(None, winsound.SND_PURGE)
        preview = waveform.get("preview")
        if preview:
            try:
                Path(preview).unlink(missing_ok=True)
            except OSError:
                pass
            waveform["preview"] = None
        try:
            play_btn.config(text="เล่น")
        except Exception:
            pass
        if preserve_playhead:
            waveform["playhead"] = held_playhead
            update_waveform_selection()

    def play_selection():
        if waveform["playing"]:
            stop_preview()
            return
        source = Path(source_var.get().strip())
        if not source.is_file():
            messagebox.showwarning("ยังไม่มีไฟล์", "เลือกไฟล์เสียงก่อน")
            return
        start = waveform["playhead"]
        preview_start = waveform["start"]
        duration = waveform["end"] - preview_start
        if duration <= 0:
            return
        waveform["playing"] = True
        waveform["play_token"] += 1
        token = waveform["play_token"]
        play_btn.config(text="หยุด")

        def worker():
            try:
                preview = Path(tempfile.gettempdir()) / "snapgen_audio_preview.wav"
                command = [ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
                           "-ss", str(preview_start), "-i", str(source), "-t", str(duration),
                           "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(preview)]
                result = subprocess.run(command, capture_output=True,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode != 0 or not preview.is_file():
                    raise RuntimeError("สร้างเสียงตัวอย่างไม่สำเร็จ")
                if token != waveform["play_token"]:
                    return
                def begin_playback():
                    if token != waveform["play_token"]:
                        return
                    waveform["preview"] = str(preview)
                    waveform["preview_source_start"] = preview_start
                    waveform["play_started_from"] = start
                    waveform["play_duration"] = duration
                    close_mci()
                    mci(f'open "{preview}" alias {waveform["player_alias"]}')
                    mci(f'seek {waveform["player_alias"]} to {max(0, int((start - preview_start) * 1000))}')
                    mci(f'play {waveform["player_alias"]}')
                    waveform["play_started_at"] = time.perf_counter()
                    update_playhead_during_preview(token)
                root.after(0, begin_playback)
            except Exception as exc:
                root.after(0, lambda exc=exc: (stop_preview(), write(f"เล่นเสียงไม่ได้: {exc}")))

        threading.Thread(target=worker, daemon=True).start()

    def restart_preview_from_playhead():
        """Seek during playback: keep chosen position, rebuild preview once."""
        target = waveform["playhead"]
        stop_preview(preserve_playhead=True)
        waveform["playhead"] = target
        update_waveform_selection()
        play_selection()

    def convert(slot_number=None):
        if busy[0]:
            return
        source = Path(source_var.get().strip())
        if not source.is_file():
            messagebox.showwarning("ยังไม่มีไฟล์", "เลือกไฟล์เสียงก่อน")
            return
        start = start_var.get().strip() or "00:00:00"
        end = end_var.get().strip()
        try:
            start_seconds = seconds(start)
            duration = None if not end else seconds(end) - start_seconds
            if duration is not None and duration <= 0:
                raise ValueError("เวลาจบต้องมากกว่าเวลาเริ่ม")
        except ValueError as exc:
            messagebox.showwarning("เวลาไม่ถูกต้อง", str(exc))
            return
        extension = {"MP3": ".mp3", "WAV": ".wav", "M4A": ".m4a"}[format_var.get()]
        output_dir = source.parent / "audio_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        if slot_number is None:
            output_stem = f"{source.stem}_cut"
        elif custom_name_enabled.get():
            automatic_name = smart_audio_name(source)
            safe_name = re.sub(r'[<>:"/\\|?*]+', "_", automatic_name).strip(" .")
            output_stem = f"{safe_name or 'เสียง'} {slot_number}"
        else:
            output_stem = str(slot_number)
        output = output_dir / f"{output_stem}{extension}"
        index = 2
        while output.exists():
            output = output_dir / f"{output_stem}_{index}{extension}"
            index += 1
        busy[0] = True
        convert_btn.config(state="disabled", text="กำลังแปลง...")

        def worker():
            try:
                command = [ffmpeg_path(), "-y", "-hide_banner", "-ss", str(start_seconds), "-i", str(source)]
                if duration is not None:
                    command += ["-t", str(duration)]
                if extension == ".mp3":
                    command += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
                elif extension == ".m4a":
                    command += ["-vn", "-c:a", "aac", "-b:a", "256k"]
                else:
                    command += ["-vn", "-c:a", "pcm_s16le"]
                command.append(str(output))
                result = subprocess.run(command, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode != 0 or not output.is_file():
                    raise RuntimeError((result.stderr or "FFmpeg ทำงานไม่สำเร็จ")[-500:])
                if slot_number is not None:
                    range_end = start_seconds + duration if duration is not None else waveform["duration"]
                    save_slot_range(source, int(slot_number), start_seconds, range_end, output)
                write(f"เสร็จ: {output}")
                root.after(0, refresh_quick_buttons)
                root.after(0, lambda: messagebox.showinfo("เสร็จแล้ว", f"บันทึกที่\n{output}"))
            except Exception as exc:
                write(f"ERROR: {exc}")
            finally:
                busy[0] = False
                root.after(0, lambda: convert_btn.config(state="normal", text="ตัด / แปลง"))

        threading.Thread(target=worker, daemon=True).start()

    def clear_audio():
        stop_preview()
        save_last_audio("")
        source_var.set("")
        custom_name_var.set("")
        story_title_var.set("—")
        start_var.set("00:00:00")
        end_var.set("")
        status_var.set("พร้อม")
        waveform.update(samples=[], duration=0.0, start=0.0, end=0.0,
                        playhead=0.0, drag=None, items={})
        draw_waveform()
        refresh_quick_buttons()

    actions = tk.Frame(box, bg="#FFFFFF")
    actions.grid(row=2, column=0, sticky="sew", pady=(8, 0))
    play_btn.config(command=play_selection)
    primary_actions = tk.Frame(actions, bg="#FFFFFF")
    primary_actions.pack(side="left")
    primary_actions.grid_columnconfigure(0, weight=1, uniform="audio_primary_actions", minsize=168)
    primary_actions.grid_columnconfigure(1, weight=1, uniform="audio_primary_actions", minsize=168)
    convert_btn = tk.Button(primary_actions, text="ตัด / แปลง", command=convert, bg="#64748B", fg="white",
                            activebackground="#475569", activeforeground="white",
                            relief="flat", width=14, height=1, padx=14, pady=7,
                            font=(SNAPGEN_UI_FONT, 9, "bold"))
    convert_btn.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    clear_btn = tk.Button(primary_actions, text="ล้าง", command=clear_audio,
                          bg="#DC2626", fg="white", relief="flat", width=14, height=1,
                          padx=14, pady=7, font=(SNAPGEN_UI_FONT, 9, "bold"))
    clear_btn.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
    # Expose only for deterministic geometry QA; no UI or behavior dependency.
    page._audio_primary_action_buttons = (convert_btn, clear_btn)
    tk.Checkbutton(
        actions, text="ใส่ชื่อ", variable=custom_name_enabled,
        bg="#FFFFFF", activebackground="#FFFFFF", font=(SNAPGEN_UI_FONT, 9),
    ).pack(side="left", padx=(12, 4))
    tk.Label(actions, textvariable=custom_name_var, bg="#FFFFFF", fg="#9CA3AF",
             font=(SNAPGEN_UI_FONT, 8)).pack(side="left", padx=(0, 8))

    quick_panel = tk.LabelFrame(
        actions, bg="#FFFFFF", fg="#475569", bd=1, relief="solid",
        padx=5, pady=5, font=(SNAPGEN_UI_FONT, 9, "bold"),
    )
    quick_panel.pack(side="right")
    quick_tabs = tk.Frame(quick_panel, bg="#FFFFFF")
    quick_tabs.pack(fill="x", pady=(0, 4))
    quick_content = tk.Frame(quick_panel, bg="#FFFFFF")
    quick_content.pack(fill="both", expand=True)
    quick_box = tk.Frame(quick_content, bg="#FFFFFF")
    history_box = tk.Frame(quick_content, bg="#FFFFFF")
    quick_box.grid(row=0, column=0, sticky="nsew")
    history_box.grid(row=0, column=0, sticky="nsew")
    quick_content.grid_rowconfigure(0, weight=1)
    quick_content.grid_columnconfigure(0, weight=1)

    def show_quick_page(page_name):
        if page_name == "save":
            quick_box.tkraise()
            save_tab.config(bg="#DCEEFF", fg="#1E5F8A")
            history_tab.config(bg="#F1F5F9", fg="#64748B")
        else:
            history_box.tkraise()
            history_tab.config(bg="#DCEEFF", fg="#1E5F8A")
            save_tab.config(bg="#F1F5F9", fg="#64748B")
        refresh_quick_buttons()

    save_tab = tk.Button(
        quick_tabs, text="บันทึก", command=lambda: show_quick_page("save"),
        bg="#DCEEFF", fg="#1E5F8A", relief="flat", padx=14, pady=4,
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    )
    save_tab._audio_quick_tab = True
    save_tab.pack(side="left")
    history_tab = tk.Button(
        quick_tabs, text="ย้อนฟังช่วง", command=lambda: show_quick_page("history"),
        bg="#F1F5F9", fg="#64748B", relief="flat", padx=14, pady=4,
        font=(SNAPGEN_UI_FONT, 9, "bold"),
    )
    history_tab._audio_quick_tab = True
    history_tab.pack(side="left", padx=(4, 0))
    for column in range(10):
        quick_box.grid_columnconfigure(column, minsize=52, uniform="audio_quick")
        history_box.grid_columnconfigure(column, minsize=52, uniform="audio_history")
    for number in range(1, 21):
        quick_button = tk.Button(
            quick_box, text=str(number), command=lambda value=number: convert(value),
            bg="#F1F5F9", activebackground="#E2E8F0", fg="#334155", relief="flat",
            width=4, height=1, padx=0, pady=2, font=(SNAPGEN_UI_FONT, 11, "bold"),
        )
        quick_button._audio_quick_finished = False
        quick_button.grid(row=(number - 1) // 10, column=(number - 1) % 10,
                          padx=2, pady=2, ipadx=6, ipady=6)
        quick_buttons[number] = quick_button
        history_button = tk.Button(
            history_box, text=str(number), command=lambda value=number: restore_saved_range(value),
            bg="#F1F5F9", activebackground="#E2E8F0", fg="#94A3B8",
            disabledforeground="#94A3B8", relief="flat", width=4, height=1,
            padx=0, pady=2, font=(SNAPGEN_UI_FONT, 11, "bold"), state="disabled",
        )
        history_button._audio_quick_finished = False
        history_button.grid(row=(number - 1) // 10, column=(number - 1) % 10,
                            padx=2, pady=2, ipadx=6, ipady=6)
        history_buttons[number] = history_button
    show_quick_page("save")
    refresh_quick_buttons()
    # Read-only handles for deterministic UI verification; runtime logic does
    # not depend on these attributes.
    page._audio_quick_buttons = quick_buttons
    page._audio_history_buttons = history_buttons
    page._audio_refresh_quick_buttons = refresh_quick_buttons
    page._audio_restore_saved_range = restore_saved_range
    page._audio_waveform_state = waveform

    def space_preview(_event=None):
        current = g.get("current_mode")
        focused = root.focus_get()
        if current is not None and current.get() == "audio" and not isinstance(focused, (tk.Entry, tk.Text)):
            play_selection()
            return "break"
        return None

    root.bind("<space>", space_preview, add="+")

    def restore_last_audio():
        try:
            config = g.get("load_config", lambda: {})() or {}
            saved = str(config.get("audio_editor_last_file") or "").strip()
            if not saved:
                return
            if not set_source(saved):
                save_last_audio("")
        except Exception:
            pass

    root.after(250, restore_last_audio)

    # Read-only handles for deterministic UI verification.
    page._audio_story_title_var = story_title_var
    page._audio_analyze_story_title = analyze_story_title

    return page
