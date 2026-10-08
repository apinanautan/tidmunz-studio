# -*- coding: utf-8 -*-
"""Voice input module for SnapGen"""
import threading
import time
import gc
import os
import shutil
from pathlib import Path
try:
    import speech_recognition as sr
    _HAS_SR = True
except Exception:
    _HAS_SR = False
try:
    import pyaudio
    _HAS_PA = True
except Exception:
    _HAS_PA = False
_AVAILABLE = _HAS_SR and _HAS_PA
_MIC_INDEX = None

def is_available():
    return _AVAILABLE

def list_microphones():
    if not _AVAILABLE:
        return []
    try:
        names = sr.Microphone.list_microphone_names()
        return [(i, name) for i, name in enumerate(names)]
    except Exception:
        return []

def set_mic_index(index):
    global _MIC_INDEX
    _MIC_INDEX = int(index) if index is not None else None

_WHISPER_MODEL = None
_WHISPER_BACKEND = None
_WHISPER_MODEL_NAME = "large-v3"
_SUPPORTED_WHISPER_MODELS = ("large-v3",)


def supported_whisper_models():
    return _SUPPORTED_WHISPER_MODELS


def set_whisper_model(model_name):
    """Use SnapGen's single speech model; it downloads on first use."""
    global _WHISPER_MODEL, _WHISPER_BACKEND, _WHISPER_MODEL_NAME
    name = "large-v3"
    if name != _WHISPER_MODEL_NAME:
        _WHISPER_MODEL = None
        _WHISPER_BACKEND = None
    _WHISPER_MODEL_NAME = name


def get_whisper_model_name():
    return _WHISPER_MODEL_NAME


def _whisper_cache_root():
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _whisper_cache_path(model_name):
    return _whisper_cache_root() / f"models--Systran--faster-whisper-{model_name}"


def cached_whisper_models():
    """Return downloaded model names and their on-disk sizes."""
    result = []
    for name in _SUPPORTED_WHISPER_MODELS:
        path = _whisper_cache_path(name)
        if not path.is_dir():
            continue
        size = 0
        seen_files = set()
        try:
            for item in path.rglob("*"):
                if not item.is_file():
                    continue
                resolved = str(item.resolve())
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                size += item.stat().st_size
        except Exception:
            pass
        result.append({"name": name, "bytes": size, "path": str(path)})
    return result


def download_whisper_model(model_name):
    """Download one model without changing the selected model."""
    name = str(model_name or "").strip().lower()
    if name not in _SUPPORTED_WHISPER_MODELS:
        raise ValueError(f"unsupported Whisper model: {name}")
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=f"Systran/faster-whisper-{name}")
    return _whisper_cache_path(name)


def delete_whisper_model(model_name):
    """Delete one downloaded model selected explicitly by the user."""
    global _WHISPER_MODEL, _WHISPER_BACKEND
    name = str(model_name or "").strip().lower()
    if name not in _SUPPORTED_WHISPER_MODELS:
        raise ValueError(f"unsupported Whisper model: {name}")
    if name == _WHISPER_MODEL_NAME and _WHISPER_MODEL is not None:
        _WHISPER_MODEL = None
        _WHISPER_BACKEND = None
        gc.collect()
    path = _whisper_cache_path(name)
    if path.is_dir():
        shutil.rmtree(path)
        return True
    return False


def _ensure_faster_whisper(log_fn=None):
    """Install faster-whisper into this Python on first use.

    It is not part of setup_and_run.bat, so a teammate's machine would only
    show "No module named faster_whisper" instead of transcribing.
    """
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    import importlib
    import subprocess
    import sys
    python = Path(sys.executable)
    console = python.with_name("python.exe")
    if console.is_file():
        python = console  # pythonw cannot run pip with a usable console
    if callable(log_fn):
        log_fn("ยังไม่มีตัวถอดเสียง faster-whisper — กำลังติดตั้ง (ครั้งแรกครั้งเดียว) ...")
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "faster-whisper"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError("ติดตั้ง faster-whisper ไม่สำเร็จ: " + (detail[-1] if detail else f"exit {result.returncode}"))
    importlib.invalidate_caches()
    import faster_whisper  # noqa: F401
    if callable(log_fn):
        log_fn("ติดตั้ง faster-whisper เสร็จ")


def _ffmpeg_path():
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


def load_audio(path):
    """16 kHz mono float32 samples decoded with FFmpeg.

    faster-whisper decodes files with PyAV, and PyAV 15+ removed the
    ``metadata_errors`` argument it passes ("open() got an unexpected keyword
    argument 'metadata_errors'").  Handing it samples skips PyAV entirely.
    """
    import subprocess
    import numpy as np
    result = subprocess.run(
        [_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError("อ่านไฟล์เสียงไม่ได้: " + (detail[-1] if detail else f"exit {result.returncode}"))
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def pcm16_to_audio(raw_bytes):
    """Raw 16-bit mono PCM at 16 kHz -> float32 samples for faster-whisper."""
    import numpy as np
    return np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0


def _nvidia_gpu_count():
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


_CUDA_PACKAGES = (("nvidia.cublas", "nvidia-cublas-cu12"), ("nvidia.cudnn", "nvidia-cudnn-cu12"))


def _prepare_cuda_libraries(log_fn=None):
    """Make cuBLAS/cuDNN loadable on Windows NVIDIA machines.

    Without these DLLs the model opens on the GPU but the first transcription
    fails. They come from pip, so no CUDA Toolkit install is needed.
    """
    import importlib
    import importlib.util
    import subprocess
    import sys
    if os.name != "nt":
        return
    missing = [package for module, package in _CUDA_PACKAGES if importlib.util.find_spec(module) is None]
    if missing:
        python = Path(sys.executable)
        if python.with_name("python.exe").is_file():
            python = python.with_name("python.exe")
        if callable(log_fn):
            log_fn("กำลังติดตั้งไลบรารี CUDA สำหรับการ์ดจอ NVIDIA (ครั้งแรกครั้งเดียว) ...")
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", *missing],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 and callable(log_fn):
            log_fn("ติดตั้งไลบรารี CUDA ไม่สำเร็จ — อาจต้องใช้ CPU")
        importlib.invalidate_caches()
    for module, _package in _CUDA_PACKAGES:
        try:
            spec = importlib.util.find_spec(module)
        except Exception:
            spec = None
        for location in (getattr(spec, "submodule_search_locations", None) or []):
            bin_dir = Path(location) / "bin"
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except (OSError, AttributeError):
                    pass
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _nvidia_vram_mb():
    """Total memory of the first NVIDIA GPU in MB (0 when unknown)."""
    import subprocess
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int(str(result.stdout).strip().splitlines()[0].strip())
    except Exception:
        return 0


def _gpu_compute_types():
    """large-v3 in float16 needs ~5 GB of VRAM: 4-6 GB laptop cards such as
    the RTX 3050 Laptop start with int8 (~3 GB) and still run on the GPU."""
    vram = _nvidia_vram_mb()
    if vram and vram < 8000:
        return ("int8_float16", "int8", "float16", "float32")
    return ("float16", "int8_float16", "int8", "float32")


def _get_whisper_model(log_fn=None, force_cpu=False, compute_types=None):
    """Load the best local speech model this computer can actually run.

    Never hard-code one GPU model here: SnapGen is shared across different
    computers.  CUDA is tried first for speed; any unavailable/incompatible
    GPU transparently falls back to CPU rather than breaking voice input.
    """
    global _WHISPER_MODEL, _WHISPER_BACKEND
    if compute_types is not None and _WHISPER_BACKEND not in (None, "CPU"):
        _WHISPER_MODEL = None  # retry the GPU with lighter types
        _WHISPER_BACKEND = None
    if force_cpu and _WHISPER_BACKEND not in (None, "CPU"):
        _WHISPER_MODEL = None
        _WHISPER_BACKEND = None
    if _WHISPER_MODEL is not None:
        if callable(log_fn):
            log_fn(f"ใช้ Whisper {_WHISPER_MODEL_NAME} • {_WHISPER_BACKEND}")
        return _WHISPER_MODEL, _WHISPER_BACKEND

    _ensure_faster_whisper(log_fn)
    from faster_whisper import WhisperModel
    if callable(log_fn):
        cached = _whisper_cache_path(_WHISPER_MODEL_NAME).is_dir()
        action = "กำลังเปิด" if cached else "กำลังดาวน์โหลด (ประมาณ 3 GB ครั้งแรกครั้งเดียว) และเปิด"
        log_fn(f"{action} Whisper {_WHISPER_MODEL_NAME}...")
    gpu_error = None
    if force_cpu:
        gpu_error = "ใช้ CPU ตามที่สั่ง"
    elif not _nvidia_gpu_count():
        # faster-whisper (CTranslate2) runs on NVIDIA CUDA only; AMD/Intel GPUs
        # and machines without a GPU use the CPU with the same large model.
        gpu_error = "ไม่พบการ์ดจอ NVIDIA (AMD/Intel ใช้ CPU แทน)"
    else:
        _prepare_cuda_libraries(log_fn)
        # float16 needs a recent card; older NVIDIA cards fall through to the
        # lighter types instead of dropping straight to the slow CPU.
        for compute_type in (compute_types or _gpu_compute_types()):
            try:
                _WHISPER_MODEL = WhisperModel(
                    _WHISPER_MODEL_NAME, device="cuda", compute_type=compute_type, num_workers=1,
                )
                _WHISPER_BACKEND = f"GPU {compute_type}"
                gpu_error = None
                break
            except Exception as exc:
                gpu_error = exc
    if gpu_error is not None:
        if callable(log_fn):
            log_fn(f"GPU ใช้ไม่ได้ ({gpu_error}) — ใช้ CPU")
        _WHISPER_MODEL = WhisperModel(
            _WHISPER_MODEL_NAME, device="cpu", compute_type="int8",
            cpu_threads=max(1, min(8, (os.cpu_count() or 4) - 1)), num_workers=1,
        )
        _WHISPER_BACKEND = "CPU"
    if callable(log_fn):
        log_fn(f"Whisper {_WHISPER_MODEL_NAME} พร้อม • {_WHISPER_BACKEND}")
    return _WHISPER_MODEL, _WHISPER_BACKEND

def listen_once(on_text=None, on_error=None, on_status=None, on_log=None, lang="th-TH"):
    if not _AVAILABLE:
        if callable(on_error):
            on_error("no speech_recognition or pyaudio")
        return lambda: None
    cancel_event = threading.Event()
    active_source = [None]

    def cancel():
        """Request the current recording to finish and be transcribed.

        Do not stop/close PortAudio from the Tk callback.  The old behavior
        closed the stream while ``Recognizer.listen`` was still reading it,
        which could terminate the whole Tk process on some devices.
        """
        cancel_event.set()
    def _worker():
        r = sr.Recognizer()
        try:
            if cancel_event.is_set():
                return
            if callable(on_status):
                on_status("listening")
            r.energy_threshold = 300
            r.dynamic_energy_threshold = False
            # Capture raw PCM ourselves so the Stop button can finish the
            # current audio immediately.  Recognizer.listen() cannot be
            # interrupted safely and used to leave no audio to transcribe.
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                input_device_index=_MIC_INDEX,
                frames_per_buffer=1024,
            )
            active_source[0] = stream
            frames = []
            started = time.monotonic()
            try:
                while not cancel_event.is_set() and time.monotonic() - started < 30:
                    frames.append(stream.read(1024, exception_on_overflow=False))
            finally:
                active_source[0] = None
                try:
                    stream.stop_stream()
                finally:
                    stream.close()
                    pa.terminate()
            if not frames:
                raise RuntimeError("ไม่มีเสียงที่บันทึกได้")
            audio = sr.AudioData(b"".join(frames), 16000, 2)
            if callable(on_status):
                on_status("processing")
            if callable(on_log):
                on_log("กำลังถอดเสียงเป็นข้อความ...")
            text = None
            try:
                model, _backend = _get_whisper_model(log_fn=on_log)
                samples = pcm16_to_audio(audio.get_raw_data(convert_rate=16000, convert_width=2))
                segments, _ = model.transcribe(samples, language="th", beam_size=3, vad_filter=True)
                text = " ".join(s.text for s in segments).strip()
            except Exception as local_error:
                if callable(on_log):
                    on_log(f"Whisper ถอดไม่สำเร็จ ({local_error}) — ลอง Google Speech")
            if not text:
                try:
                    text = r.recognize_google(audio, language=lang)
                except Exception:
                    pass
            if not text:
                try:
                    text = r.recognize_google(audio, language="en-US")
                except Exception:
                    pass
            if callable(on_status):
                on_status("done")
            if text:
                if callable(on_text):
                    on_text(text)
            else:
                if callable(on_error):
                    on_error("could not hear anything")
        except Exception as e:
            active_source[0] = None
            if cancel_event.is_set():
                if callable(on_status):
                    on_status("done")
                return
            if callable(on_error):
                on_error(str(e))
            if callable(on_status):
                on_status("done")
    threading.Thread(target=_worker, daemon=True).start()
    return cancel

def set_bridge(base, key):
    pass

def create_global_mic_button(parent, root, target_getter, size=34, log_fn=None, target_log_getter=None):
    """One microphone for every editable Text/Entry in SnapGen."""
    import tkinter as tk

    button = tk.Button(
        parent, text="🎙️", bg="#EFF6FF", fg="#315A75",
        activebackground="#DBEAFE", activeforeground="#315A75",
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground="#BFDBFE", highlightcolor="#93C5FD",
        width=3, height=1, padx=0, pady=4,
        font=("Segoe UI Emoji", 20), cursor="hand2",
    )
    idle = ("🎙️", "#EFF6FF")
    listening = ("🎤", "#FEE2E2")
    processing = ("🔎", "#FEF3C7")
    session = [0]

    def is_text_target(widget):
        try:
            return str(widget.winfo_class()) in ("Text", "Entry", "TEntry")
        except Exception:
            return False

    def ui(callback):
        try:
            root.after(0, callback)
        except Exception:
            pass

    def log(message):
        writer = getattr(button, "_voice_log_fn", None)
        if not callable(writer):
            writer = log_fn
        if callable(writer):
            ui(lambda writer=writer, message=message: writer(message))

    def set_state(text, color):
        button.configure(text=text, bg=color, activebackground=color)

    def insert_text(widget, text):
        try:
            if not widget or not widget.winfo_exists():
                raise RuntimeError("ช่องข้อความที่เลือกถูกปิดแล้ว")
            state = str(widget.cget("state") or "normal")
            if state in ("disabled", "readonly"):
                raise RuntimeError("ช่องนี้แก้ไขไม่ได้")
            if str(widget.winfo_class()) == "Text":
                try:
                    ranges = widget.tag_ranges(tk.SEL)
                    if len(ranges) >= 2:
                        widget.delete(ranges[0], ranges[1])
                except Exception:
                    pass
                widget.insert(tk.INSERT, text + " ")
            elif str(widget.winfo_class()) in ("Entry", "TEntry"):
                try:
                    if widget.selection_present():
                        widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                except Exception:
                    pass
                widget.insert(tk.INSERT, text + " ")
            else:
                raise RuntimeError("คลิกช่องข้อความก่อนใช้ไมค์")
            widget.focus_set()
            log("✅ " + text[:80])
        except Exception as exc:
            log("❌ " + str(exc))

    def on_click(_event=None):
        if getattr(button, "_listening", False):
            cancel = getattr(button, "_cancel_listen", None)
            if callable(cancel):
                cancel()
            set_state(*processing)
            log("⏹ หยุดบันทึกเสียง — กำลังวิเคราะห์")
            return "break"
        target = target_getter() if callable(target_getter) else None
        button._voice_log_fn = None
        if not is_text_target(target):
            log("คลิกช่องข้อความที่ต้องการก่อน แล้วกดไมค์")
            try:
                root.bell()
            except Exception:
                pass
            try:
                from tkinter import messagebox
                messagebox.showinfo("ไมโครโฟน", "คลิกช่องข้อความที่ต้องการก่อน แล้วกดไมค์")
            except Exception:
                pass
            return "break"
        session[0] += 1
        current_session = session[0]
        # Lock this recording to the field selected when recording starts.
        button._voice_target = target
        button._voice_log_fn = target_log_getter() if callable(target_log_getter) else None
        button._listening = True
        set_state(*listening)
        log("🎤 เริ่มบันทึกเสียง")

        def on_text(text):
            ui(lambda: insert_text(button._voice_target, text) if current_session == session[0] else None)

        def on_error(error):
            log("❌ ถอดเสียงไม่สำเร็จ: " + str(error))

        def on_status(status):
            def update():
                if current_session != session[0]:
                    return
                if status == "listening":
                    set_state(*listening)
                elif status == "processing":
                    set_state(*processing)
                elif status == "done":
                    button._listening = False
                    set_state(*idle)
            ui(update)

        button._cancel_listen = listen_once(
            on_text=on_text, on_error=on_error, on_status=on_status, on_log=log,
        )
        return "break"

    button.configure(command=on_click)
    return button

def create_mic_icon_button(parent, text_widget, root, size=28, log_fn=None, bottom_inset=3):
    import tkinter as tk
    # Own the mic by the Text widget itself. A sibling action row can cover a
    # mic placed on an outer frame even when its coordinates look correct.
    frame = tk.Frame(text_widget, width=size, height=size, bg="#1E293B", highlightthickness=0, bd=0)
    frame.pack_propagate(False)
    label = tk.Label(frame, text="\U0001F399\U0000FE0F", bg="#1E293B", fg="#FFFFFF", font=("Segoe UI Emoji", 16), bd=0, padx=0, pady=0)
    label.pack(fill="both", expand=True)
    def _do_place():
        try:
            frame.place_forget()
            # Child coordinates are relative to Text, so the whole button stays
            # exactly inside its bottom-left corner on every page.
            # `Text` uses internal padx. Child coordinates start after that
            # padding, so subtract it or the mic appears to float ~10 px from
            # the visible left border. Bottom alignment is already correct.
            try:
                text_padx = int(float(text_widget.cget("padx") or 0))
            except Exception:
                text_padx = 0
            x = -text_padx
            y = max(text_widget.winfo_height() - size - int(bottom_inset), 0)
            frame.place(x=x, y=y, width=size, height=size)
            frame.lift()
        except Exception:
            pass
    _do_place()
    root.after_idle(_do_place)
    text_widget.bind("<Configure>", lambda e: _do_place(), add="+")
    _idle_bg = "#1E293B"
    _listening_bg = "#DC2626"
    _processing_bg = "#F59E0B"
    _session = [0]
    def _ui(fn):
        try:
            root.after(0, fn)
        except Exception:
            pass
    def _log(msg):
        if callable(log_fn):
            def _write_log():
                try:
                    log_fn(msg)
                except Exception:
                    pass
            _ui(_write_log)
    def on_click(e=None):
        if getattr(frame, "_listening", False):
            cancel = getattr(frame, "_cancel_listen", None)
            if callable(cancel):
                cancel()
            label.config(text="\U0001F50D")
            frame.config(bg=_processing_bg)
            label.config(bg=_processing_bg)
            _log("⏹ หยุดบันทึกเสียง — กำลังวิเคราะห์")
            return "break"
        _session[0] += 1
        session = _session[0]
        frame._listening = True
        label.config(text="\U0001F3A4")
        frame.config(bg=_listening_bg)
        label.config(bg=_listening_bg)
        _log("🎤 เริ่มบันทึกเสียง")
        def _on_text(text):
            # "done" can reach Tk's event queue before this callback.  The
            # old frame._listening check then discarded valid recognized text.
            _ui(lambda: _insert(text) if session == _session[0] else None)
            _log("\u2705 " + text[:80])
        def _insert(text):
            try:
                text_widget.insert("insert", text + " ")
                text_widget.focus_set()
            except Exception:
                pass
        def _on_error(err):
            _log("❌ ถอดเสียงไม่สำเร็จ: " + str(err))
            _ui(lambda: _error_reset() if session == _session[0] else None)
        def _error_reset():
            frame._listening = False
            label.config(text="\U0001F399\U0000FE0F")
            frame.config(bg=_idle_bg)
            label.config(bg=_idle_bg)
        def _on_status(status):
            def _update():
                if session != _session[0]:
                    return
                if status == "listening":
                    label.config(text="\U0001F3A4")
                    frame.config(bg=_listening_bg)
                    label.config(bg=_listening_bg)
                elif status == "processing":
                    label.config(text="\U0001F50D")
                    frame.config(bg=_processing_bg)
                    label.config(bg=_processing_bg)
                elif status == "done":
                    frame._listening = False
                    label.config(text="\U0001F399\U0000FE0F")
                    frame.config(bg=_idle_bg)
                    label.config(bg=_idle_bg)
            _ui(_update)
        frame._cancel_listen = listen_once(
            on_text=_on_text,
            on_error=_on_error,
            on_status=_on_status,
            on_log=_log,
        )
        return "break"
    label.bind("<Button-1>", on_click)
    frame.bind("<Button-1>", on_click)
    return frame
