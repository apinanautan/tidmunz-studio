# -*- coding: utf-8 -*-
"""Smooth slow motion using bundled RIFE NCNN Vulkan with FFmpeg fallback."""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time, zipfile
from pathlib import Path

RIFE_RELEASE_URL = "https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-windows.zip"
_tool_install_lock = threading.Lock()

def _video_ok(path):
    """Check if video file exists and is non-empty."""
    try:
        return Path(path).is_file() and Path(path).stat().st_size > 0
    except Exception:
        return False

def _latest_video_near(path):
    """Find the newest generated video when the recovered app passes a folder."""
    candidates = []
    try:
        p = Path(path)
        search_dirs = []
        if p.is_dir():
            search_dirs.append(p)
        elif p.parent.exists():
            search_dirs.append(p.parent)

        project_root = Path(__file__).resolve().parent.parent
        search_dirs.extend([
            project_root / "export" / "video",
            project_root / "snapgen_data",
            Path.home() / "Downloads" / "SnapGen",
        ])

        seen = set()
        for folder in search_dirs:
            try:
                folder = Path(folder)
                key = str(folder.resolve())
                if key in seen or not folder.exists():
                    continue
                seen.add(key)
                for ext in ("*.mp4", "*.webm", "*.mov", "*.mkv"):
                    candidates.extend(folder.glob(ext))
            except Exception:
                pass
        candidates = [c for c in candidates if _video_ok(c)]
        if candidates:
            return max(candidates, key=lambda c: c.stat().st_mtime)
    except Exception:
        pass
    return None

def _ffmpeg_bin():
    """Return a usable ffmpeg executable path."""
    project_root = Path(__file__).resolve().parent.parent
    candidates = [
        project_root / "snapgen_data" / "tools" / "ffmpeg" / "ffmpeg.exe",
        project_root / "tools" / "ffmpeg" / "ffmpeg.exe",
        project_root / "ffmpeg.exe",
    ]
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))
    for c in candidates:
        try:
            if Path(c).is_file():
                return str(c)
        except Exception:
            pass
    return "ffmpeg"

def ensure_ffmpeg_tool(log=None):
    """Install a private FFmpeg copy when another PC has none."""
    current = _ffmpeg_bin()
    if current != "ffmpeg" or shutil.which(current):
        return current
    say = log if callable(log) else (lambda _msg: None)
    with _tool_install_lock:
        current = _ffmpeg_bin()
        if current != "ffmpeg" or shutil.which(current):
            return current
        say("[slow2x] กำลังติดตั้ง FFmpeg สำหรับถอด/ประกอบวิดีโอ...")
        try:
            import imageio_ffmpeg
        except Exception:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "imageio-ffmpeg"],
                capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace",
            )
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout or "ติดตั้ง imageio-ffmpeg ไม่สำเร็จ")[-1000:])
            import imageio_ffmpeg
        source = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if not source.is_file():
            raise RuntimeError("ติดตั้ง FFmpeg แล้วแต่หาไฟล์โปรแกรมไม่พบ")
        project_root = Path(__file__).resolve().parent.parent
        target = project_root / "snapgen_data" / "tools" / "ffmpeg" / "ffmpeg.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        say("[slow2x] ติดตั้ง FFmpeg แล้ว")
        return str(target)


def _rife_install_complete(folder):
    folder = Path(folder)
    required = (
        folder / "rife-ncnn-vulkan.exe",
        folder / "vcomp140.dll",
        folder / "rife-v2.3" / "contextnet.bin",
        folder / "rife-v2.3" / "contextnet.param",
        folder / "rife-v2.3" / "flownet.bin",
        folder / "rife-v2.3" / "flownet.param",
        folder / "rife-v2.3" / "fusionnet.bin",
        folder / "rife-v2.3" / "fusionnet.param",
    )
    return all(path.is_file() and path.stat().st_size > 0 for path in required)


def ensure_rife_tool(log=None):
    """Download and atomically install the complete RIFE package."""
    say = log if callable(log) else (lambda _msg: None)
    project_root = Path(__file__).resolve().parent.parent
    # Runtime tools have one canonical home. Keeping a fallback copy beside
    # this module previously duplicated the complete ~470 MB RIFE package.
    candidate_dirs = [
        project_root / "snapgen_data" / "tools" / "rife-ncnn-vulkan",
    ]
    for folder in candidate_dirs:
        if _rife_install_complete(folder):
            return str(folder / "rife-ncnn-vulkan.exe")

    with _tool_install_lock:
        for folder in candidate_dirs:
            if _rife_install_complete(folder):
                return str(folder / "rife-ncnn-vulkan.exe")
        if os.name != "nt":
            raise RuntimeError("RIFE ชุดนี้รองรับ Windows เท่านั้น")
        if "ARM" in os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64").upper():
            raise RuntimeError("RIFE รุ่น Windows นี้ไม่รองรับเครื่อง ARM64")
        import urllib.request
        rife_dir = candidate_dirs[0]
        tools_dir = rife_dir.parent
        tools_dir.mkdir(parents=True, exist_ok=True)
        say("[slow2x] เครื่องนี้ยังไม่มี RIFE — กำลังดาวน์โหลดชุด AI ครั้งแรก...")
        with tempfile.TemporaryDirectory(prefix="snapgen-rife-install-") as td:
            temp = Path(td)
            archive = temp / "rife.zip"
            request = urllib.request.Request(RIFE_RELEASE_URL, headers={"User-Agent": "Tidmun-Studio/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as out:
                shutil.copyfileobj(response, out)
            if not zipfile.is_zipfile(archive):
                raise RuntimeError("ไฟล์ RIFE ที่ดาวน์โหลดมาไม่ใช่ ZIP ที่ถูกต้อง")
            extracted = temp / "extracted"
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extracted)
            exe = next(extracted.rglob("rife-ncnn-vulkan.exe"), None)
            if exe is None:
                raise RuntimeError("ดาวน์โหลด RIFE แล้วแต่ไม่พบ rife-ncnn-vulkan.exe")
            package_root = exe.parent
            if not _rife_install_complete(package_root):
                raise RuntimeError("แพ็กเกจ RIFE ดาวน์โหลดไม่ครบ (.exe/DLL/model ขาด)")
            incoming = tools_dir / "rife-ncnn-vulkan.installing"
            shutil.rmtree(incoming, ignore_errors=True)
            shutil.copytree(package_root, incoming)
            shutil.rmtree(rife_dir, ignore_errors=True)
            os.replace(str(incoming), str(rife_dir))
        if not _rife_install_complete(rife_dir):
            raise RuntimeError("ติดตั้ง RIFE ไม่สมบูรณ์")
        say("[slow2x] ติดตั้ง RIFE ครบแล้ว")
        return str(rife_dir / "rife-ncnn-vulkan.exe")


def _probe_video(ffmpeg, path):
    """Read source FPS and audio presence from FFmpeg without ffprobe."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    text = (r.stderr or "") + "\n" + (r.stdout or "")
    video_line = next((line for line in text.splitlines() if "Video:" in line), "")
    match = re.search(r"(?:,|\s)(\d+(?:\.\d+)?)\s+fps(?:,|\s)", video_line)
    fps = float(match.group(1)) if match else 24.0
    if fps <= 0 or fps > 240:
        fps = 24.0
    has_audio = any("Audio:" in line for line in text.splitlines())
    return fps, has_audio

def _load_rife_choice(rife) -> dict:
    """This PC's RIFE set-up that gives clean frames ({gpu, model}), or {broken} — kept next to RIFE."""
    try:
        data = json.loads((Path(rife).parent / "snapgen_rife_choice.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    if data.get("broken") and time.time() - float(data.get("checked") or 0) > 3 * 86400:
        return {}  # a driver may have been updated since: look again
    return data if isinstance(data, dict) else {}


def _save_rife_choice(rife, data: dict) -> None:
    try:
        (Path(rife).parent / "snapgen_rife_choice.json").write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def _rife_gpus(rife) -> dict:
    """{vulkan index: name} of the cards RIFE can see, from a 2-frame run (its start-up lines)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="snapgen_rife_probe_"))
    try:
        from PIL import Image
        (tmp_dir / "in").mkdir()
        (tmp_dir / "out").mkdir()
        for i in (1, 2):
            Image.new("RGB", (64, 64), (i * 60, 0, 0)).save(tmp_dir / "in" / f"{i:08d}.png")
        text = subprocess.run([rife, "-i", str(tmp_dir / "in"), "-o", str(tmp_dir / "out")],
                              cwd=str(Path(rife).parent), capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stderr or ""
        import re
        return {int(i): name.strip() for i, name in re.findall(r"^\[(\d+) ([^\]]+)\]", text, re.M)}
    except Exception:
        return {}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _is_built_in_gpu(name: str) -> bool:
    """Graphics inside the CPU (laptops): slow for RIFE and the usual source of streak frames."""
    low = name.lower()
    return ("intel" in low and "arc" not in low) or "radeon(tm) graphics" in low or low.endswith("radeon graphics") \
        or "vega" in low or "llvmpipe" in low or "microsoft basic" in low


LOW_VRAM_MB = 3000  # below this RIFE (720p, v2.3) may return streak frames instead of failing


def _free_vram_mb():
    """Free memory of the NVIDIA card (MB), or None when nvidia-smi is not there."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        values = [int(v) for v in out.split() if v.strip().isdigit()]
        return max(values) if values else None
    except Exception:
        return None


def _stripe_ratio(png) -> float:
    """Vertical / horizontal detail of a frame: broken RIFE frames are horizontal streaks (ratio > 3)."""
    try:
        from PIL import Image, ImageChops
        with Image.open(png) as image:
            gray = image.convert("L").resize((192, 108))
        down = ImageChops.difference(gray.crop((0, 1, 192, 108)), gray.crop((0, 0, 192, 107)))
        across = ImageChops.difference(gray.crop((1, 0, 192, 108)), gray.crop((0, 0, 191, 108)))
        mean = lambda img: sum(i * n for i, n in enumerate(img.histogram())) / max(1, img.width * img.height)
        return mean(down) / (mean(across) + 0.01)
    except Exception:
        return 0.0


def _rife_frames_broken(in_dir, out_dir, samples=12) -> bool:
    """True when RIFE's output has streak frames that the source frames do not have."""
    outs = sorted(Path(out_dir).glob("*.png"))
    ins = sorted(Path(in_dir).glob("*.png"))
    if len(outs) < 4 or not ins:
        return False
    step = max(1, len(outs) // samples)
    striped = sum(1 for p in outs[::step][:samples] if _stripe_ratio(p) > 3)
    source = max(_stripe_ratio(p) for p in ins[:: max(1, len(ins) // 4)][:4])
    return striped >= 3 and source < 2


def video_has_streaks(path, source=None, samples=24) -> bool:
    """True when a finished slow clip has RIFE streak frames (compared with its normal-speed source)."""
    ffmpeg = ensure_ffmpeg_tool()
    tmp_dir = Path(tempfile.mkdtemp(prefix="snapgen_streak_"))
    try:
        dirs = {}
        for name, video in (("out", path), ("in", source)):
            if not video or not _video_ok(video):
                continue
            folder = tmp_dir / name
            folder.mkdir()
            # Every frame of the first seconds: in-between frames alternate with real ones.
            subprocess.run([ffmpeg, "-y", "-i", str(video), "-t", "4", "-vf", "scale=384:-2",
                            str(folder / "%05d.png")], capture_output=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            dirs[name] = folder
        outs = sorted(dirs["out"].glob("*.png")) if "out" in dirs else []
        if len(outs) < 4:
            return False
        step = max(1, len(outs) // samples) | 1  # odd: an even step would land only on real frames
        striped = sum(1 for p in outs[::step][:samples] if _stripe_ratio(p) > 3)
        ins = sorted(dirs["in"].glob("*.png")) if "in" in dirs else []
        source_ratio = max((_stripe_ratio(p) for p in ins[:: max(1, len(ins) // 4)][:4]), default=0.0)
        return striped >= 3 and source_ratio < 2
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def make_ai_slow2x(input_video, output_video=None, factor=2, log=None, **_kwargs):
    """Create a slowed video.

    Guard: if input already has _Slow2x in its name, return immediately.
    The pyc imports this function directly (bypassing g["make_ai_slow2x"])
    and would otherwise double-slow a video the download flow already slowed.

    Accepts `log=` because the recovered video flow passes it.  Prefer the
    bundled RIFE tool when available; otherwise use an ffmpeg fallback so video
    generation does not fail after download.
    """
    # Guard: already slowed? Skip — prevents 6s → 12s → 24s double-slow.
    # pyc imports this function directly, bypassing g["make_ai_slow2x"].
    try:
        if "_slow2x" in Path(input_video).stem.lower():
            if callable(log):
                log("[slow2x] ข้าม — ทำ Slow 2x ไปแล้ว")
            return str(input_video)
    except Exception:
        pass
    def say(msg):
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    mute_audio = bool(_kwargs.get("mute", False))

    inp = Path(input_video)
    if not _video_ok(inp):
        fallback = _latest_video_near(inp)
        if fallback:
            say(f"[slow2x] input path ไม่ใช่ไฟล์ — ใช้วิดีโอล่าสุดแทน: {fallback.name}")
            inp = fallback
        else:
            say(f"[slow2x] หาไฟล์วิดีโอไม่เจอ — ข้าม Slow 2x: {input_video}")
            return str(input_video)
    if output_video is None:
        output_video = str(inp.with_name(inp.stem + f"_Slow{factor}x" + inp.suffix))
    out = Path(output_video)
    out.parent.mkdir(parents=True, exist_ok=True)

    # RIFE generates the missing in-between frames. Encoding those frames at
    # the original FPS doubles duration without cutting temporal resolution.
    try:
        rife = ensure_rife_tool(log=say)
        ffmpeg = ensure_ffmpeg_tool(log=say)
        source_fps, has_audio = _probe_video(ffmpeg, inp)
        if mute_audio:
            has_audio = False
        say(f"[slow2x] ใช้ RIFE AI | ต้นฉบับ {source_fps:g} FPS | Slow {factor}x")
        _started = time.time()
        tmp_dir = Path(tempfile.mkdtemp(prefix="snapgen_rife_"))
        try:
            in_dir = tmp_dir / "in"
            out_dir = tmp_dir / "out"
            in_dir.mkdir()
            out_dir.mkdir()
            extracted = subprocess.run([
                ffmpeg, "-y", "-i", str(inp), "-vsync", "0", "-compression_level", "1", str(in_dir / "%08d.png")
            ], capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
            if extracted.returncode:
                raise RuntimeError((extracted.stderr or extracted.stdout or "extract frames failed")[-1200:])
            input_count = len(list(in_dir.glob("*.png")))
            if input_count < 2:
                raise RuntimeError(f"พบเฟรมต้นฉบับเพียง {input_count} เฟรม")
            if int(factor) != 2:
                raise RuntimeError("RIFE v2.3 ที่ติดตั้งรองรับ Slow 2x เท่านั้น")
            # This 20221029 build uses the bundled v2.3 model. Directory mode
            # already outputs N*2 frames; custom -n belongs to the v4 model.
            target_count = input_count * 2
            say(f"[slow2x] RIFE สร้างเฟรมกลาง {input_count} → {target_count} เฟรม")
            _last_pct = -1
            def _poll_progress():
                nonlocal _last_pct
                try:
                    _done = len(list(out_dir.glob("*.png")))
                    _pct = int(_done * 100 / target_count)
                    if _pct > _last_pct:
                        _last_pct = _pct
                        say(f"[slow2x] RIFE: {_done}/{target_count} ({_pct}%)")
                except Exception:
                    pass
            def _ffmpeg_slow():
                # No GPU set-up gives clean RIFE frames here (RIFE on the CPU takes ~70x longer).
                _slow_cmd = [
                    ffmpeg, "-y", "-i", str(inp),
                    "-vf", f"minterpolate=fps={source_fps*factor:.0f}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir,setpts={factor}.0*PTS",
                ]
                if not mute_audio and has_audio:
                    _slow_cmd += ["-c:a", "aac", "-b:a", "192k"]
                else:
                    _slow_cmd += ["-an"]
                _slow_cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                              "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
                say("[slow2x] FFmpeg minterpolate running...")
                _r = subprocess.run(_slow_cmd, capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace")
                if _r.returncode:
                    err = (_r.stderr or "")[-400:].strip()
                    raise RuntimeError("FFmpeg minterpolate failed: " + (err or "unknown"))
                if _video_ok(out):
                    say(f"[slow2x] FFmpeg minterpolate done: {out.name}")
                    return str(out)
                raise RuntimeError("FFmpeg minterpolate no output")

            choice = _load_rife_choice(rife)
            if choice.get("broken") and _kwargs.get("recheck"):
                choice = {}  # the user freed the card and asked again: try RIFE once more
            if choice.get("broken"):
                say("[slow2x] เครื่องนี้การ์ดจอทำ RIFE ไม่ได้ (ตรวจไว้แล้ว) — ใช้ FFmpeg สโลว์แทน")
                return _ffmpeg_slow()
            # First clip on a PC: find a GPU + model whose in-between frames are clean, then remember it.
            # Broken frames come from RIFE picking the CPU's built-in graphics, an old driver, or a model
            # the card computes wrongly — each is tried in turn, never RIFE on the CPU.
            remembered = [(choice.get("gpu"), choice.get("model"))] if choice.get("model") else []
            candidates = list(dict.fromkeys(remembered + [
                (None, "rife-v2.3"), (0, "rife-v2.3"), (1, "rife-v2.3"), (2, "rife-v2.3"),
                (None, "rife-v4.6"), (0, "rife-v4.6"), (1, "rife-v4.6")]))
            gpus = _rife_gpus(rife)
            real = [i for i, name in gpus.items() if not _is_built_in_gpu(name)]
            if real and len(gpus) > 1:
                # Laptops with two cards: "auto" may land on the CPU's graphics (slow, streaky) — name the real card.
                built_in = set(gpus) - set(real)
                candidates = list(dict.fromkeys(
                    [(real[0], "rife-v2.3"), (real[0], "rife-v4.6")]
                    + [(g, m) for g, m in candidates if g is not None and g not in built_in]))
            if gpus:
                say("[slow2x] การ์ดจอที่ RIFE เห็น: " + ", ".join(f"{i}={n}" for i, n in gpus.items())
                    + (f" → ใช้ {gpus[candidates[0][0]]}" if candidates[0][0] in gpus else ""))
            # More work in flight at once: ~35% faster RIFE, but it needs memory the card may not have free.
            free_now = _free_vram_mb()
            threads = "2:4:4" if free_now is not None and free_now >= LOW_VRAM_MB else ""
            working = None
            low_vram = []
            for gpu, model in candidates:
                if not (Path(rife).parent / model).is_dir():
                    continue
                shutil.rmtree(out_dir, ignore_errors=True)
                out_dir.mkdir()
                _last_pct = -1
                rife_command = [rife, "-i", str(in_dir), "-o", str(out_dir), "-f", "%08d.png", "-m", model]
                if gpu is not None:
                    rife_command += ["-g", str(gpu)]
                if threads:
                    rife_command += ["-j", threads]
                _proc = subprocess.Popen(rife_command, cwd=str(Path(rife).parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while _proc.poll() is None:
                    _poll_progress()
                    time.sleep(1)
                _poll_progress()
                done = len(list(out_dir.glob("*.png")))
                label = f"GPU {'อัตโนมัติ' if gpu is None else gpu} / {model}"
                if _proc.returncode or done < target_count - 2:
                    say(f"[slow2x] RIFE {label} ใช้ไม่ได้ — ลองชุดถัดไป")
                    continue
                if _rife_frames_broken(in_dir, out_dir):
                    free = _free_vram_mb()
                    if free is not None and free < LOW_VRAM_MB:
                        # Seen on a 16 GB card with CC5/iClone open: RIFE gets too little memory and
                        # returns streaks instead of an error. Not the card's fault — do not mark it broken.
                        low_vram.append(free)
                        say(f"[slow2x] ⚠ หน่วยความจำการ์ดจอเหลือ {free} MB — RIFE ได้เฟรมเสีย "
                            "ปิดโปรแกรมที่ใช้การ์ดจอ (เช่น Character Creator, iClone, เกม) แล้วลองใหม่")
                    say(f"[slow2x] RIFE {label} ให้เฟรมกลางเสีย (ลายเส้น/ภาพกระพริบ) — ลองชุดถัดไป")
                    continue
                working = (gpu, model)
                break
            if working is None and low_vram:
                say("[slow2x] ⚠ คลิปนี้ใช้ FFmpeg สโลว์แทน เพราะหน่วยความจำการ์ดจอไม่พอ "
                    "(ไม่จำว่าการ์ดจอเสีย — ปิดโปรแกรมที่ใช้การ์ดจอแล้วคลิปต่อไปจะใช้ RIFE ตามปกติ)")
                return _ffmpeg_slow()
            if working is None:
                _save_rife_choice(rife, {"broken": True, "checked": time.time()})
                say("[slow2x] ⚠ การ์ดจอเครื่องนี้ทำ RIFE ไม่ได้ทุกชุด — ใช้ FFmpeg สโลว์แทน "
                    "(อัปเดตไดรเวอร์การ์ดจอแล้วเปิดโปรแกรมใหม่ จะตรวจใหม่ใน 3 วัน)")
                return _ffmpeg_slow()
            if (working[0], working[1]) != (choice.get("gpu"), choice.get("model")):
                _save_rife_choice(rife, {"gpu": working[0], "model": working[1], "checked": time.time()})
                if working != (None, "rife-v2.3"):
                    say(f"[slow2x] ใช้ RIFE GPU {'อัตโนมัติ' if working[0] is None else working[0]} / {working[1]} "
                        "(ชุดนี้ภาพปกติ — จำไว้ใช้ครั้งต่อไป)")
            output_count = len(list(out_dir.glob("*.png")))
            if output_count < target_count - 2:
                raise RuntimeError(f"RIFE returned too few frames {output_count}/{target_count}")
            fps_text = f"{source_fps:.6f}".rstrip("0").rstrip(".")
            encode = [
                ffmpeg, "-y", "-framerate", fps_text, "-i", str(out_dir / "%08d.png"),
            ]
            if has_audio:
                encode += [
                    "-i", str(inp), "-filter_complex", f"[1:a]atempo={1 / float(factor):.6f}[a]",
                    "-map", "0:v:0", "-map", "[a]", "-c:a", "aac", "-b:a", "192k",
                ]
            else:
                encode += ["-map", "0:v:0", "-an"]
            encode += [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
            ]
            r = subprocess.run(encode, capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace")
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout or "ffmpeg encode failed")[-1200:])
            if _video_ok(out):
                say(f"[slow2x] RIFE ✓ {out.name} ({time.time() - _started:.0f} วินาที)")
                return str(out)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as e:
        say(f"[slow2x] RIFE ทำงานไม่สำเร็จ — ไม่ใช้ตัวแปลงอื่น: {e}")
        return str(inp)
