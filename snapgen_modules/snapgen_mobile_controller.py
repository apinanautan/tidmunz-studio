"""Tk-owned state and commands for the mobile view. No HTTP thread touches Tk."""
from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import re
import stat
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from contextlib import nullcontext
from pathlib import Path

try:
    from .snapgen_grok_lower import SUPPORTED_DURATIONS as GROK_LOWER_DURATIONS
except ImportError:
    from snapgen_grok_lower import SUPPORTED_DURATIONS as GROK_LOWER_DURATIONS


CONFIG_KEYS = ("model", "resolution", "duration", "aspect", "mode", "camera_movement", "dialogue")
VIDEO_AUTO_ASPECT = "อัตโนมัติ"
CAMERAS = ("อัตโนมัติ", "กล้องนิ่ง ห้ามขยับ", "เลื่อนเข้า", "เลื่อนออก", "เลื่อนซ้าย", "เลื่อนขวา", "เงยขึ้น", "ก้มลง", "ติดตามตัวละคร", "กล้องมือถือ", "กล้องสั่นแรง")
FLAG_LABELS = ("ลบลายน้ำ Veo", "เสียงแจ้งเตือน", "AI Slow 2x", "ปิดเสียงวิดีโอ", "Upscale 1080p")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def value(var, default=""):
    try:
        return var.get() if hasattr(var, "get") else default
    except Exception:
        return default


def text_value(widget):
    try:
        return widget.get("1.0", "end-1c")
    except Exception:
        return ""


def replace_text(widget, text):
    widget.delete("1.0", "end")
    widget.insert("1.0", str(text))


def safe_log(text):
    text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[redacted]", str(text))
    text = re.sub(r"(?i)(api[_-]?key|token|cookie|authorization|secret|password)([\s\"':=]+)[^\s,;}]+", r"\1\2[redacted]", text)
    return re.sub(r"[A-Za-z]:[\\/][^\r\n]*", "[ไฟล์บนคอม]", text)[-3500:]


class MobileController:
    def __init__(self, g, root, base_root, app_globals):
        self.g, self.root, self.base = g, root, Path(base_root)
        self.app = app_globals
        self.commands = queue.Queue(maxsize=32)
        self.lock = threading.RLock()
        self.requests = OrderedDict()
        self.media = {}
        self.snapshot = {"ready": False, "slots": [], "image": {}, "flags": []}
        self.last_tick = 0.0
        self.flags = {}
        self.last_media_scan = 0.0
        self.videos = []
        self.story_document_cache = {"stamp": None, "document": None}
        self.mobile_story_override = None
        self.pending_story_sync = None
        self.prompt_split = {"busy": False, "scene": "", "prompts": [], "video_prompts": [],
                             "image_prompts": [], "storyboard": None,
                             "status": "พร้อมสร้าง Storyboard + Prompt", "error": ""}
        self.closed = False
        self._find_flags(root)

    def _find_flags(self, widget):
        try:
            if widget.winfo_class() == "Checkbutton":
                label = str(widget.cget("text")).strip()
                if label in FLAG_LABELS:
                    self.flags[label] = widget
            for child in widget.winfo_children():
                self._find_flags(child)
        except Exception:
            pass

    def register_media(self, path):
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".mkv", ".webm"}:
            return None
        key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
        with self.lock:
            self.media[key] = path
        return {"id": key, "name": path.name, "url": "/media/" + key,
                "kind": "video" if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} else "image"}

    def resolve_media(self, key):
        with self.lock:
            path = self.media.get(str(key))
        if not path or not path.is_file():
            raise ValueError("ไม่พบรูปหรือวิดีโอนี้ กรุณาเลือกใหม่")
        return path

    def _computer_roots(self):
        drives = []
        try:
            drives = [Path(path).resolve() for path in os.listdrives() if Path(path).is_dir()]
        except (AttributeError, OSError):
            anchor = Path(self.base.anchor or self.base).resolve()
            drives = [anchor]
        shortcuts = [
            ("Desktop", Path.home() / "Desktop"),
            ("Downloads", Path.home() / "Downloads"),
            ("Pictures", Path.home() / "Pictures"),
            ("Documents", Path.home() / "Documents"),
            ("โปรเจกต์ SnapGen", self.base),
        ]
        rows, seen = [], set()
        for label, path in shortcuts + [(str(path), path) for path in drives]:
            try:
                resolved = Path(path).resolve()
            except OSError:
                continue
            key = os.path.normcase(str(resolved))
            if resolved.is_dir() and key not in seen:
                seen.add(key)
                rows.append({"name": label, "path": str(resolved)})
        return rows, drives

    def resolve_computer_folder(self, path):
        raw = str(path or "").strip()
        if not raw or len(raw) > 1000:
            raise ValueError("เลือกโฟลเดอร์ในคอมก่อน")
        candidate = Path(raw).resolve()
        _rows, drives = self._computer_roots()
        allowed = any(candidate == root or root in candidate.parents for root in drives)
        if not allowed or not candidate.is_dir():
            raise ValueError("ไม่พบโฟลเดอร์นี้ในคอม")
        return candidate

    def browse_computer_folders(self, path=""):
        roots, _drives = self._computer_roots()
        if not str(path or "").strip():
            return {"current": "", "parent": None, "folders": roots, "image_count": 0,
                    "selected": str(self.g.get("get_image_ref_folder", lambda: "")() or "")}
        current = self.resolve_computer_folder(path)
        folders = []
        try:
            def created_time(entry):
                info = entry.stat()
                return getattr(info, "st_birthtime", info.st_ctime)
            children = sorted(
                (entry for entry in current.iterdir() if entry.is_dir()),
                key=created_time,
                reverse=True,
            )
        except OSError as exc:
            raise ValueError("เปิดโฟลเดอร์นี้ไม่ได้") from exc
        for entry in children[:300]:
            if entry.name.startswith((".", "$", "__")):
                continue
            try:
                attributes = entry.stat().st_file_attributes
                if attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM):
                    continue
            except (AttributeError, OSError):
                pass
            folders.append({"name": entry.name, "path": str(entry.resolve())})
        try:
            image_count = sum(1 for entry in current.iterdir() if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS)
        except OSError:
            image_count = 0
        parent = current.parent
        try:
            parent_path = str(self.resolve_computer_folder(parent)) if parent != current else None
        except ValueError:
            parent_path = None
        return {"current": str(current), "parent": parent_path, "folders": folders,
                "image_count": image_count,
                "selected": str(self.g.get("get_image_ref_folder", lambda: "")() or "")}

    def status(self):
        with self.lock:
            result = dict(self.snapshot)
        result["ready"] = not self.closed and time.monotonic() - self.last_tick < 10
        return result

    def submit(self, request):
        key = str(request.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", key):
            raise ValueError("คำสั่งต้องมีหมายเลขอ้างอิง")
        with self.lock:
            if key in self.requests:
                previous, event, result = self.requests[key]
                if previous != request:
                    raise ValueError("หมายเลขคำสั่งซ้ำแต่ข้อมูลไม่ตรงกัน")
                return event, result
            if not self.status()["ready"]:
                raise RuntimeError("SnapGen ยังไม่พร้อม กรุณารอโปรแกรมบนคอมเปิดเสร็จ")
            event, result = threading.Event(), {}
            try:
                self.commands.put_nowait((request, event, result))
            except queue.Full:
                raise RuntimeError("คิวคำสั่งเต็ม กรุณารอสักครู่")
            self.requests[key] = (dict(request), event, result)
            while len(self.requests) > 128:
                oldest, (_, old_event, _) = next(iter(self.requests.items()))
                if not old_event.is_set():
                    break
                self.requests.pop(oldest)
            return event, result

    def receipt(self, key):
        with self.lock:
            entry = self.requests.get(key)
            if entry is None:
                return {"ok": False, "error": "ไม่มีผลตอบรับคำสั่งนี้ อาจมีการเปิดโปรแกรมใหม่ ตรวจ Log ก่อนสร้างซ้ำ"}
            return dict(entry[2]) if entry[1].is_set() else {"pending": True}

    def _models(self):
        labels = {
            "vela-ai-video": "Vela AI Video",
            "grok-lower": "Grok Lower — เสียเครดิต 50%",
        }
        models = [{"value": x, "label": labels.get(x, x)}
                  for x in ("vela-ai-video", "grok-lower", "veo-2", "veo-3.1-lite", "grok-3")]
        for prefix in ("LTX23_LOCAL", "LTX25_MAESTRO"):
            key = self.app.get(prefix + "_MODEL")
            if key:
                models.append({"value": key, "label": self.app.get(prefix + "_LABEL", key)})
        return models

    def options(self, model):
        vela = model == "vela-ai-video"
        durations = (
            ["5"] if vela
            else list(GROK_LOWER_DURATIONS) if model == "grok-lower"
            else self.g["duration_values_for_model"](model)
        )
        grok = model in ("grok-3", "grok-lower")
        return {"duration": [str(x) for x in durations],
                "resolution": ["480p", "720p"] if grok else ["720p", "1080p"],
                "aspect": ([VIDEO_AUTO_ASPECT, "16:9", "9:16", "1:1"] if vela else
                           [VIDEO_AUTO_ASPECT, "landscape", "portrait", "square", "vertical", "horizontal"] if grok else
                           [VIDEO_AUTO_ASPECT, "16:9", "9:16"]),
                "mode": ["custom", "normal", "extremely-crazy", "extremely-spicy-or-crazy"] if grok else ["custom"],
                "camera_movement": list(CAMERAS), "dialogue": ["มีบทพูด", "ไม่มีบทพูด"]}

    @staticmethod
    def _plain_story(name, text, source):
        paragraphs = [{"runs": [{"text": line or " ", "color": None}]} for line in text[:200000].splitlines()]
        return {"name": name, "source": source, "paragraphs": paragraphs,
                "characters": sum(len(run["text"]) for p in paragraphs for run in p["runs"]),
                "version": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]}

    @staticmethod
    def _docx_story(name, content, source):
        """Read DOCX text runs with their Word font colors intact."""
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError("บท DOCX ใหญ่เกินไป")
            root = ET.fromstring(archive.read(info))
            style_colors = {}
            try:
                styles = ET.fromstring(archive.read("word/styles.xml"))
                for style in styles.iter(ns + "style"):
                    style_id = style.get(ns + "styleId")
                    color = style.find("./" + ns + "rPr/" + ns + "color")
                    if style_id and color is not None:
                        style_colors[style_id] = color.get(ns + "val")
            except (KeyError, ET.ParseError):
                pass

        def clean_color(value):
            value = str(value or "").lstrip("#")
            return "#" + value.upper() if re.fullmatch(r"[0-9A-Fa-f]{6}", value) else None

        paragraphs, total = [], 0
        for para in root.iter(ns + "p"):
            para_style = para.find("./" + ns + "pPr/" + ns + "pStyle")
            para_color = style_colors.get(para_style.get(ns + "val")) if para_style is not None else None
            runs = []
            for run in para.iter(ns + "r"):
                pieces = []
                for node in run.iter():
                    if node.tag == ns + "t":
                        pieces.append(node.text or "")
                    elif node.tag == ns + "tab":
                        pieces.append("\t")
                    elif node.tag in {ns + "br", ns + "cr"}:
                        pieces.append("\n")
                text = "".join(pieces)
                if not text:
                    continue
                direct = run.find("./" + ns + "rPr/" + ns + "color")
                run_style = run.find("./" + ns + "rPr/" + ns + "rStyle")
                value = direct.get(ns + "val") if direct is not None else None
                if not value and run_style is not None:
                    value = style_colors.get(run_style.get(ns + "val"))
                color = clean_color(value or para_color)
                runs.append({"text": text, "color": color})
                total += len(text)
                if total > 200000:
                    raise ValueError("บทยาวเกิน 200,000 ตัวอักษร")
            if runs:
                paragraphs.append({"runs": runs})
        return {"name": name, "source": source, "paragraphs": paragraphs, "characters": total,
                "version": hashlib.sha256(content).hexdigest()[:16]}

    @classmethod
    def _story_from_bytes(cls, name, content, source):
        suffix = Path(name).suffix.lower()
        if suffix == ".docx":
            return cls._docx_story(name, content, source)
        if suffix == ".txt":
            return cls._plain_story(name, content.decode("utf-8-sig", errors="replace"), source)
        raise ValueError("รองรับไฟล์บท .docx และ .txt เท่านั้น")

    def load_mobile_story(self, name, content):
        if not content or len(content) > 12 * 1024 * 1024:
            raise ValueError("ไฟล์บทต้องมีขนาดไม่เกิน 12 MB")
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(str(name or "บทมือถือ.docx")).name).strip(" .")[:120]
        if Path(safe_name).suffix.lower() not in {".docx", ".txt"}:
            raise ValueError("รองรับไฟล์บท .docx และ .txt เท่านั้น")
        document = self._story_from_bytes(safe_name, content, "mobile")
        if not document["paragraphs"]:
            raise ValueError("ไม่พบข้อความในไฟล์บท")
        data_dir = self.base / "snapgen_data"
        upload_dir = data_dir / "mobile_story_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved_path = upload_dir / safe_name

        def atomic_write(path, payload):
            temp = path.with_name(path.name + f".{time.time_ns()}.tmp")
            temp.write_bytes(payload)
            temp.replace(path)

        atomic_write(saved_path, content)
        saver = self.app.get("_save_prompt_ref_source_file")
        if callable(saver):
            cached_path = Path(saver(saved_path)).resolve()
        else:
            cached_path = (data_dir / ("prompt_ref_source" + saved_path.suffix.lower())).resolve()
            atomic_write(cached_path, content)
            meta = data_dir / "meta" / "prompt_ref_source_file.json"
            meta.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "original_path": str(saved_path.resolve()), "cached_path": str(cached_path),
                "filename": safe_name, "last_dir": str(upload_dir.resolve()),
            }, ensure_ascii=False, indent=2).encode("utf-8")
            atomic_write(meta, payload)
        story_text = "\n".join(
            "".join(str(run.get("text") or "") for run in paragraph.get("runs", []))
            for paragraph in document["paragraphs"]
        ).rstrip() + "\n"
        atomic_write(data_dir / "prompt_ref_source.txt", story_text.encode("utf-8"))
        with self.lock:
            self.mobile_story_override = document
            self.story_document_cache = {"stamp": None, "document": None}
            self.pending_story_sync = {"name": Path(safe_name).stem}
        return document

    def use_desktop_story(self):
        with self.lock:
            self.mobile_story_override = None
        return self._story_document()

    def _story_document(self):
        with self.lock:
            if self.mobile_story_override is not None:
                return self.mobile_story_override
        data_dir = self.base / "snapgen_data"
        docx_path = data_dir / "prompt_ref_source.docx"
        text_path = data_dir / "prompt_ref_source.txt"
        meta = data_dir / "meta" / "prompt_ref_source_file.json"
        selected_path = None
        try:
            selected = json.loads(meta.read_text(encoding="utf-8-sig"))
            for key in ("cached_path", "original_path"):
                candidate = Path(str(selected.get(key) or ""))
                if candidate.is_file() and candidate.suffix.lower() in {".docx", ".txt"}:
                    selected_path = candidate
                    break
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        path = selected_path or (docx_path if docx_path.is_file() else text_path)
        try:
            stat = path.stat()
            stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp != self.story_document_cache["stamp"]:
            document = None
            if stamp is not None:
                try:
                    display_name = path.name
                    if meta.is_file():
                        display_name = Path(str(json.loads(meta.read_text(encoding="utf-8-sig")).get("filename") or path.name)).name
                    document = self._story_from_bytes(display_name, path.read_bytes(), "desktop")
                except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError):
                    pass
            self.story_document_cache = {"stamp": stamp, "document": document}
        return self.story_document_cache["document"] or {"name": "", "source": "desktop", "paragraphs": [], "characters": 0}

    def _busy_image(self):
        actions = self.g["mobile_image_actions"]
        return bool(self.g.get("img_busy", [False])[0] or actions["auto_state"]["running"] or
                    self.g.get("img_story_file_state", {}).get("sending") or actions["context_state"]["sending"])

    def _start_prompt_split(self, scene):
        scene = str(scene or "").strip()
        if not scene:
            raise ValueError("ใส่ท่อนบทที่ต้องการแตก Prompt ก่อน")
        if len(scene) > 30000:
            raise ValueError("ท่อนบทยาวเกิน 30,000 ตัวอักษร กรุณาแบ่งเป็นฉาก")
        with self.lock:
            if self.prompt_split["busy"]:
                raise ValueError("กำลังแตก Prompt อยู่ กรุณารอให้เสร็จก่อน")
            self.prompt_split = {"busy": True, "scene": scene, "prompts": [], "video_prompts": [],
                                 "image_prompts": [], "storyboard": None,
                                 "status": "กำลังสร้าง Storyboard จากฉาก · อาจใช้เวลาหลายนาที…", "error": ""}

        def worker():
            try:
                shared_runner = self.app.get("_run_prompt_ref_storyboard_workflow")
                make_storyboard = self.g.get("_generate_prompt_ref_storyboard_image_from_scene")
                read_storyboard = self.g.get("_generate_prompts_from_storyboard_image")
                split_modes = self.g.get("_split_prompt_ref_output_modes")
                format_bank = self.g.get("_format_prompt_bank")
                if callable(shared_runner):
                    def progress(message):
                        with self.lock:
                            self.prompt_split["status"] = str(message)
                    result = shared_runner(scene, progress=progress)
                    video_entries = list(result["video_entries"])
                    image_entries = list(result["image_entries"])
                    storyboard_path = result["storyboard_path"]
                    storyboard = self.register_media(storyboard_path)
                    video_prompts = [{"number": i, "prompt": text[:10000]} for i, text in enumerate(video_entries, 1)]
                    image_prompts = [{"number": i, "prompt": text[:10000]} for i, text in enumerate(image_entries, 1)]
                    prompts = video_prompts
                    status = result["status"]
                elif all(callable(fn) for fn in (make_storyboard, read_storyboard, split_modes, format_bank)):
                    ready = self.g.get("_prompt_ref_context_history_ready")
                    if callable(ready) and not ready():
                        raise RuntimeError("ยังไม่ได้เริ่มเรื่องใน Prompt-Ref · เปิดหน้าบนคอมแล้วกด เริ่มเรื่องจากบทนี้ ก่อน")
                    queue_lock = self.g.get("_bridge_queue_lock")
                    with queue_lock if queue_lock is not None else nullcontext():
                        with self.lock:
                            self.prompt_split["status"] = "ขั้นที่ 1/3 · กำลังสร้างภาพ Storyboard…"
                        storyboard_path = make_storyboard(scene)
                        with self.lock:
                            self.prompt_split["status"] = "ขั้นที่ 2/3 · กำลังอ่านแต่ละช่องจากภาพ Storyboard…"
                        raw = read_storyboard(storyboard_path, scene)
                        video_entries, image_entries = split_modes(raw)
                    video_entries = [str(text).strip() for text in video_entries if str(text).strip()]
                    image_entries = [str(text).strip() for text in image_entries if str(text).strip()]
                    if not video_entries or not image_entries:
                        raise RuntimeError("อ่าน Storyboard แล้วแต่ไม่ได้ Prompt รูปและวิดีโอ")
                    video_path = self.g.get("PROMPT_BANK_VIDEO") or self.base / "snapgen_data" / "prompt_bank_video.txt"
                    image_path = self.g.get("PROMPT_BANK_IMAGE") or self.base / "snapgen_data" / "prompt_bank_image.txt"
                    legacy_path = self.g.get("PROMPT_BANK_LEGACY") or self.base / "snapgen_data" / "prompt_bank.txt"
                    Path(video_path).write_text(format_bank(video_entries, "Video Slot"), encoding="utf-8")
                    Path(image_path).write_text(format_bank(image_entries, "Image Slot"), encoding="utf-8")
                    Path(legacy_path).write_text(format_bank(video_entries, "Video Slot"), encoding="utf-8")
                    storyboard = self.register_media(storyboard_path)
                    video_prompts = [{"number": i, "prompt": text[:10000]} for i, text in enumerate(video_entries, 1)]
                    image_prompts = [{"number": i, "prompt": text[:10000]} for i, text in enumerate(image_entries, 1)]
                    prompts = video_prompts
                    status = f"พร้อม: Storyboard 1 ภาพ · วิดีโอ {len(video_prompts)} Slot · รูป {len(image_prompts)} Slot"
                else:
                    # Isolated tests and older shells keep the lightweight fallback.
                    from snapgen_prompt_splitter import split_scene_prompts
                    result = split_scene_prompts(self.base / "snapgen_data", scene)
                    prompts = []
                    for index, item in enumerate(result.get("prompts") or [], 1):
                        if not isinstance(item, dict):
                            continue
                        text = str(item.get("prompt") or "").strip()
                        if text:
                            prompts.append({"number": int(item.get("number") or index), "prompt": text[:10000]})
                    if not prompts:
                        raise RuntimeError("GPT ไม่ได้ส่ง Prompt กลับมา")
                    video_prompts = prompts[:]
                    image_prompts = prompts[:]
                    storyboard = None
                    status = f"แตก Prompt เสร็จแล้ว {len(prompts)} รายการ"
                with self.lock:
                    self.prompt_split = {"busy": False, "scene": scene, "prompts": prompts,
                                         "video_prompts": video_prompts, "image_prompts": image_prompts,
                                         "storyboard": storyboard, "status": status, "error": ""}
            except Exception as exc:
                with self.lock:
                    self.prompt_split = {"busy": False, "scene": scene, "prompts": [],
                                         "video_prompts": [], "image_prompts": [], "storyboard": None,
                                         "status": "แตก Prompt ไม่สำเร็จ", "error": safe_log(str(exc))}

        threading.Thread(target=worker, daemon=True).start()

    def update_prompt_ref_remote(self, *, busy=None, status=None, error=None, scene=None):
        """Receive progress when the same shared workflow starts on desktop."""
        with self.lock:
            if busy is not None:
                self.prompt_split["busy"] = bool(busy)
            if status is not None:
                self.prompt_split["status"] = str(status)
            if error is not None:
                self.prompt_split["error"] = safe_log(error)
            if scene is not None:
                self.prompt_split["scene"] = str(scene)

    def refresh(self):
        g = self.g
        if len(self.flags) < len(FLAG_LABELS):
            self._find_flags(self.root)
        actions = g["mobile_image_actions"]
        slots = []
        # These are the two real visible slots, not invented slots 3..10.
        for i in range(min(2, len(g.get("slot_cfg_vars", [])))):
            cfg = {key: str(value(g["slot_cfg_vars"][i].get(key))) for key in CONFIG_KEYS}
            path = value(g["slot_images"][i])
            buttons = g.get("video_prompt_ai_buttons", [])
            ai_busy = i < len(buttons) and buttons[i] is not None and "กำลัง" in str(buttons[i].cget("text"))
            slots.append({"index": i, "config": cfg, "image": self.register_media(path) if path else None,
                          "prompt": text_value(g["slot_prompts"][i]), "busy": bool(g["slot_busy"][i] or ai_busy),
                          "status": str(value(g.get("slot_statuses", [])[i])) if i < len(g.get("slot_statuses", [])) else "Idle",
                          "log": safe_log(text_value(g["slot_logs"][i])),
                          "expression": value(g.get("video_expression_vars", [])[i]) if i < len(g.get("video_expression_vars", [])) else "ตาม Prompt",
                          "no_turn_back": bool(value(g.get("video_no_turn_back_vars", [])[i], False)) if i < len(g.get("video_no_turn_back_vars", [])) else False})
        models = self._models()
        for slot in slots:
            model = slot["config"]["model"]
            if model and model not in [m["value"] for m in models]:
                models.append({"value": model, "label": model})
        refs = []
        for name, path in actions["refs"]():
            item = self.register_media(path)
            if item:
                refs.append(dict(item, label=name))
        gallery = [item for path in actions["gallery"]() if (item := self.register_media(path))]
        if time.monotonic() - self.last_media_scan > 5:
            folder = Path(g.get("EXPORT_VIDEO") or self.base / "export" / "video")
            files = sorted((p for p in folder.glob("*") if p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}), key=lambda p: p.stat().st_mtime, reverse=True)
            self.videos = [item for p in files[:30] if (item := self.register_media(p))]
            self.last_media_scan = time.monotonic()
        loader = g.get("load_prompt_bank_entries_by_mode")
        prompts = {mode: [{"name": str(k), "prompt": str(p)} for k, p in (loader(mode) if loader else [])] for mode in ("image", "video")}
        flags = [{"label": label, "value": bool(int(w.getvar(str(w.cget("variable")))))} for label, w in self.flags.items()]
        ref_folder = str(g.get("get_image_ref_folder", lambda: "")() or "")
        image = {"prompt": text_value(g.get("img_prompt_text")), "aspect": value(g.get("img_aspect_var")),
                 "lighting": value(g.get("img_lighting_var")), "camera": g.get("img_camera_mode", {}).get("key", "auto"),
                 "story": value(g.get("img_story_title_var")), "story_status": value(g.get("img_story_file_var")),
                 "busy": self._busy_image(), "auto": dict(actions["auto_state"]), "refs": refs,
                 "ref_folder": ref_folder, "ref_folder_name": Path(ref_folder).name if ref_folder else "",
                 "log": safe_log(text_value(g.get("img_log_box"))), "gallery": gallery}
        story_face = {"title": "", "status": "", "name": "", "selected_key": "", "age": "อัตโนมัติ",
                      "ages": ["อัตโนมัติ", "เด็ก", "วัยรุ่น", "ผู้ใหญ่", "ผู้สูงอายุ"],
                      "characters": [], "running": False, "log": "", "gallery": []}
        face_actions = g.get("mobile_story_face_actions") or {}
        snapshot = face_actions.get("snapshot")
        if callable(snapshot):
            try:
                raw_face = snapshot() or {}
                story_face.update({key: raw_face[key] for key in story_face if key in raw_face})
                story_face["log"] = safe_log(story_face.get("log", ""))
                story_face["gallery"] = [
                    item for path in raw_face.get("gallery", [])
                    if (item := self.register_media(path))
                ]
            except Exception as exc:
                story_face["status"] = "อ่านหน้านิทานไม่สำเร็จ"
                story_face["log"] = safe_log(str(exc))
        credit = str(value(g.get("credit_status_var"), "") or "").strip()
        if not credit:
            try:
                credit = str(g.get("credit_button").cget("text") or "").strip()
            except Exception:
                credit = ""
        credit = credit if re.fullmatch(r"\d[\d,.]*", credit) else ""
        split_snapshot = dict(self.prompt_split)
        if not split_snapshot.get("busy"):
            def bank_rows(mode):
                rows = []
                for index, item in enumerate(prompts.get(mode) or [], 1):
                    name, text = str(item.get("name") or ""), str(item.get("prompt") or "").strip()
                    if not text:
                        continue
                    match = re.search(r"(\d{1,3})", name)
                    rows.append({"number": int(match.group(1)) if match else index, "prompt": text[:10000]})
                return rows
            split_snapshot["video_prompts"] = bank_rows("video")
            split_snapshot["image_prompts"] = bank_rows("image")
            if not split_snapshot.get("storyboard"):
                meta_path = self.g.get("PROMPT_REF_STORYBOARD_IMAGE_META") or self.base / "snapgen_data" / "prompt_ref_storyboard_image.json"
                try:
                    storyboard_path = Path(json.loads(Path(meta_path).read_text(encoding="utf-8")).get("image_path") or "")
                    split_snapshot["storyboard"] = self.register_media(storyboard_path) if storyboard_path.is_file() else None
                except Exception:
                    split_snapshot["storyboard"] = None
            if not split_snapshot.get("error") and (split_snapshot["video_prompts"] or split_snapshot["image_prompts"]):
                split_snapshot["status"] = (
                    f"พร้อม: วิดีโอ {len(split_snapshot['video_prompts'])} Slot · "
                    f"รูป {len(split_snapshot['image_prompts'])} Slot"
                )
        result = {"slots": slots, "image": image, "models": models, "model_options": {m["value"]: self.options(m["value"]) for m in models},
                  "aspects": list(g.get("IMG_ASPECT_RATIOS") or ["16:9", "9:16", "1:1", "4:3", "3:4"]),
                  "lighting": list(g.get("LIGHTING_PRESETS") or self.app.get("LIGHTING_PRESETS", {})),
                  "expressions": list(self.app.get("VIDEO_EXPRESSION_PRESETS", {"ตาม Prompt": ""})),
                  "characters": self.app.get("_video_context_character_names", lambda: [])(),
                  "flags": flags, "prompts": prompts, "videos": self.videos,
                  "story": self._story_document(), "prompt_split": split_snapshot,
                  "story_face": story_face,
                  "credit": credit, "ready": True}
        with self.lock:
            self.snapshot = result
            self.last_tick = time.monotonic()

    def _slot(self, request):
        i = request.get("slot")
        if type(i) is not int or not 0 <= i < min(2, len(self.g["slot_cfg_vars"])):
            raise ValueError("เลือก Slot 1 หรือ Slot 2")
        if self.g["slot_busy"][i] or self.snapshot["slots"][i]["busy"]:
            raise ValueError(f"Slot {i + 1} กำลังทำงาน รอให้เสร็จก่อน")
        return i

    def _video_fields(self, i, data):
        cfg = self.g["slot_cfg_vars"][i]
        supplied = data.get("config", {})
        if not isinstance(supplied, dict) or any(k not in CONFIG_KEYS for k in supplied):
            raise ValueError("การตั้งค่า Slot ไม่ถูกต้อง")
        model = str(supplied.get("model", value(cfg["model"])))
        if model not in {m["value"] for m in self.snapshot["models"]}:
            raise ValueError("ไม่มีโมเดลนี้ในโปรแกรม")
        options = self.options(model)
        updates = {"model": model}
        for key in CONFIG_KEYS[1:]:
            val = str(supplied.get(key, value(cfg.get(key))))
            if val not in options[key]:
                if key in supplied:
                    raise ValueError(f"{key}: ค่านี้ใช้กับโมเดล {model} ไม่ได้")
                val = options[key][0]
            updates[key] = val
        expression = data.get("expression")
        if expression is not None and expression not in self.snapshot["expressions"]:
            raise ValueError("อารมณ์ไม่ถูกต้อง")
        if "no_turn_back" in data and type(data["no_turn_back"]) is not bool:
            raise ValueError("ค่าห้ามหันหน้าไม่ถูกต้อง")
        # Validate every field before changing anything. Model watchers may set defaults.
        for key, val in updates.items():
            if key in cfg and value(cfg[key]) != val:
                cfg[key].set(val)
        if "prompt" in data:
            replace_text(self.g["slot_prompts"][i], data["prompt"])
        if expression is not None:
            self.g["set_video_expression"](i, expression)
        if "no_turn_back" in data:
            self.g["video_no_turn_back_vars"][i].set(data["no_turn_back"])
        self.g["save_slot_configs"]()
        self.g.get("refresh_slot_duration_menu", lambda _: None)(i)
        self.g.get("refresh_slot_cfg_label", lambda _: None)(i)
        self.g.get("_save_video_work_state", lambda: None)()

    def execute(self, request):
        action = request.get("action")
        data = request.get("data", {})
        if not isinstance(data, dict):
            raise ValueError("ข้อมูลคำสั่งไม่ถูกต้อง")
        g, image = self.g, self.g["mobile_image_actions"]
        if str(action).startswith("story_face_"):
            face = g.get("mobile_story_face_actions") or {}
            if not face:
                raise ValueError("หน้านิทานยังไม่พร้อม กรุณาปิดเปิดโปรแกรมใหม่")
            character_key = str(data.get("character") or "")
            age = str(data.get("age") or "อัตโนมัติ")
            if action == "story_face_select":
                label = face["select"](character_key, age)
                return f"เลือก {label} ในหน้านิทานแล้ว"
            if action == "story_face_generate":
                label = face["generate"](character_key, age)
                return f"เริ่มสร้างหน้าตรง มุมข้าง และ Body ของ {label} แล้ว"
            raise ValueError("ไม่พบคำสั่งหน้านิทาน")
        if action == "prompt_split_start":
            self._start_prompt_split(data.get("scene"))
            return "เริ่มสร้าง Storyboard + Prompt แล้ว รอดูผลในหน้านี้"
        if action == "prompt_split_save":
            mode = str(data.get("mode") or "")
            entries = data.get("prompts")
            if mode not in {"video", "image"} or not isinstance(entries, list) or not 1 <= len(entries) <= 16:
                raise ValueError("ข้อมูล Slot ที่จะบันทึกไม่ถูกต้อง")
            cleaned = [str(text).strip() for text in entries]
            if any(not text or len(text) > 10000 for text in cleaned):
                raise ValueError("Prompt ใน Slot ว่างหรือยาวเกินไป")
            formatter = g.get("_format_prompt_bank")
            if not callable(formatter):
                raise ValueError("โปรแกรมยังไม่พร้อมบันทึก Prompt-Ref")
            target = g.get("PROMPT_BANK_IMAGE" if mode == "image" else "PROMPT_BANK_VIDEO")
            if not target:
                raise ValueError("ไม่พบคลัง Prompt ของโปรแกรม")
            Path(target).write_text(formatter(cleaned, "Image Slot" if mode == "image" else "Video Slot"), encoding="utf-8")
            if mode == "video" and g.get("PROMPT_BANK_LEGACY"):
                Path(g["PROMPT_BANK_LEGACY"]).write_text(formatter(cleaned, "Video Slot"), encoding="utf-8")
            return f"บันทึก Prompt {('วิดีโอ' if mode == 'video' else 'รูป')} {len(cleaned)} Slot แล้ว"
        if action == "flag":
            label = data.get("label")
            if label not in self.flags or type(data.get("value")) is not bool:
                raise ValueError("ตัวเลือกไม่ถูกต้อง")
            if any(g["slot_busy"]) or self._busy_image():
                raise ValueError("รอให้งานปัจจุบันเสร็จก่อนเปลี่ยนตัวเลือกประมวลผล")
            widget = self.flags[label]
            if bool(int(widget.getvar(str(widget.cget("variable"))))) != data["value"]:
                widget.invoke()
            return "บันทึกตัวเลือกแล้ว"
        if str(action).startswith("video_"):
            i = self._slot(request)
            if action == "video_clear":
                g["clear_slot"](i)
                return f"ล้าง Slot {i + 1} แล้ว"
            if action == "video_load":
                path = self.resolve_media(data.get("media"))
                if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                    raise ValueError("เลือกไฟล์รูปสำหรับวิดีโอ")
                g["load_slot_image"](i, str(path))
                g.get("_save_video_work_state", lambda: None)()
                return f"ส่งรูปเข้า Slot {i + 1} แล้ว พร้อมแก้ Prompt และสร้างวิดีโอ"
            if action not in {"video_save", "video_generate", "video_gpt"}:
                raise ValueError("ไม่พบคำสั่งวิดีโอ")
            if action in {"video_generate", "video_gpt"}:
                path = str(value(g["slot_images"][i]))
                if not path or not Path(path).is_file():
                    raise ValueError("เลือกรูปเข้า Slot ก่อน")
                if action == "video_generate" and not str(data.get("prompt", text_value(g["slot_prompts"][i]))).strip():
                    raise ValueError("ใส่ Prompt วิดีโอก่อนกด Generate this")
                if action == "video_gpt" and not g.get("has_main_story_history", lambda: False)():
                    raise ValueError("ยังไม่มีประวัติเรื่องหลัก ให้เริ่มเรื่องจาก Prompt-Ref ก่อนใช้ GPT")
            self._video_fields(i, data)
            if action == "video_generate":
                # This page is video creation, not the desktop's separate voice-only mode.
                g.get("_veo3_voice_state", {}).pop(i, None)
                g["on_generate_slot"](i)
                if not g["slot_busy"][i]:
                    raise ValueError("ยังไม่ได้เริ่มงาน ตรวจ Log ของ Slot")
                return f"Slot {i + 1} เริ่มสร้างวิดีโอแล้ว"
            if action == "video_gpt":
                g["video_prompt_ai_buttons"][i].invoke()
                return "GPT กำลังเขียน Prompt ดูผลในช่อง Prompt และ Log"
            return f"บันทึก Slot {i + 1} แล้ว"
        if str(action).startswith("image_"):
            if action == "image_stop_auto":
                image["auto_state"]["cancel"] = True
                return "หยุดคิวหลังรูปปัจจุบันเสร็จ"
            if self._busy_image():
                raise ValueError("หน้า Image AI กำลังทำงาน รอให้งานเดิมเสร็จก่อน")
            if action == "image_ref_folder":
                folder = self.resolve_computer_folder(data.get("path"))
                setter = g.get("set_image_ref_folder")
                if not callable(setter):
                    raise ValueError("หน้า Image AI ยังไม่พร้อมเลือกโฟลเดอร์อ้างอิง")
                count = int(setter(str(folder)) or 0)
                return f"ใช้โฟลเดอร์ {folder.name or folder} แล้ว · พบรูป {count} รูป"
            if action == "image_attach":
                keys = data.get("media", [])
                if not isinstance(keys, list) or not 1 <= len(keys) <= 10:
                    raise ValueError("แนบรูปได้ครั้งละ 1–10 รูป")
                paths = [self.resolve_media(k) for k in keys]
                if any(p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} for p in paths):
                    raise ValueError("แนบได้เฉพาะรูป")
                image["attach"]([str(p) for p in paths])
                return "แนบรูปและใส่ชื่อใน Prompt แล้ว"
            if action == "image_clear_gallery":
                image["clear_gallery"]()
                return "ล้างแกลเลอรีแล้ว ไฟล์ผลงานยังอยู่บนคอม"
            if action == "image_edit":
                path = self.resolve_media(data.get("media"))
                instruction = str(data.get("instruction", "")).strip()
                if not instruction:
                    raise ValueError("พิมพ์สิ่งที่ต้องการแก้รูปก่อน")
                image["edit"](str(path), instruction)
                return "เริ่มแก้รูปแล้ว ดูผลในแกลเลอรี"
            if action not in {"image_save", "image_generate", "image_storyboard", "image_auto"}:
                raise ValueError("ไม่พบคำสั่งรูป")
            if action == "image_generate" and not str(data.get("prompt", text_value(g["img_prompt_text"]))).strip():
                raise ValueError("ใส่ Prompt รูปก่อน")
            for key, choices in (("aspect", self.snapshot["aspects"]), ("lighting", self.snapshot["lighting"]), ("camera", ["auto", "face", "character", "object"])):
                if key in data and data[key] not in choices:
                    raise ValueError(f"{key}: ตัวเลือกไม่ถูกต้อง")
            if action in {"image_auto", "image_storyboard"} and not self.snapshot["prompts"]["image"]:
                raise ValueError("ยังไม่มี Prompt ในคลังของโปรแกรม")
            if "prompt" in data:
                replace_text(g["img_prompt_text"], data["prompt"])
            for key in ("aspect", "lighting"):
                if key in data:
                    g["img_" + ("aspect" if key == "aspect" else "lighting") + "_var"].set(data[key])
            if "camera" in data:
                image["camera"](data["camera"])
            g.get("_save_image_work_state", lambda: None)()
            if action == "image_generate":
                image["generate"]()
                if not g["img_busy"][0]:
                    raise ValueError("ยังไม่ได้เริ่มสร้างรูป ตรวจ Log ด้านล่าง")
                return "เริ่มสร้างรูปแล้ว ดูผลในแกลเลอรี"
            if action == "image_storyboard":
                image["storyboard"]()
                return "เริ่มสร้าง Storyboard แล้ว"
            if action == "image_auto":
                image["auto"](mobile_range=(data.get("from", 1), data.get("to", 1)))
                return "เริ่ม Auto-Gen แล้ว ดูความคืบหน้าใน Log"
            return "บันทึกหน้า Image AI แล้ว"
        raise ValueError("ไม่พบคำสั่งนี้")

    def tick(self):
        if self.closed:
            return
        with self.lock:
            story_sync, self.pending_story_sync = self.pending_story_sync, None
        if story_sync:
            invalidate = self.g.get("invalidate_downstream_story_histories")
            if callable(invalidate):
                invalidate()
            title_var = self.g.get("img_story_title_var")
            if hasattr(title_var, "set"):
                title_var.set(story_sync["name"])
            status_var = self.g.get("img_story_file_var")
            if hasattr(status_var, "set"):
                status_var.set("ใช้บทที่อัปโหลดจากมือถือแล้ว")
        for _ in range(4):
            try:
                request, event, result = self.commands.get_nowait()
            except queue.Empty:
                break
            try:
                result.update(ok=True, message=self.execute(request))
            except Exception as exc:
                result.update(ok=False, error=safe_log(str(exc)))
            finally:
                try:
                    self.refresh()
                except Exception as exc:
                    result.update(ok=False, error=safe_log(str(exc)))
                event.set()
        try:
            self.refresh()
        except Exception as exc:
            self.snapshot = dict(self.snapshot, error=safe_log(str(exc)))
        self.root.after(400, self.tick)
