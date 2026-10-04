# -*- coding: utf-8 -*-
"""Smooth slow motion using bundled RIFE NCNN Vulkan with FFmpeg fallback."""
import os, re, shutil, subprocess, sys, tempfile, threading, time, zipfile
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
        tmp_dir = Path(tempfile.mkdtemp(prefix="snapgen_rife_"))
        try:
            in_dir = tmp_dir / "in"
            out_dir = tmp_dir / "out"
            in_dir.mkdir()
            out_dir.mkdir()
            extracted = subprocess.run([
                ffmpeg, "-y", "-i", str(inp), "-vsync", "0", str(in_dir / "%08d.png")
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
            rife_command = [
                rife, "-i", str(in_dir), "-o", str(out_dir), "-f", "%08d.png"
            ]
            _proc = subprocess.Popen(rife_command, cwd=str(Path(rife).parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while _proc.poll() is None:
                _poll_progress()
                time.sleep(3)
            _poll_progress()
            if _proc.returncode:
                # Keep RIFE as the interpolator.  On PCs with unsupported or
                # missing Vulkan GPU, retry its documented CPU mode instead
                # of switching to ffmpeg minterpolate (which visibly judders).
                say("[slow2x] RIFE GPU ใช้ไม่ได้ — ลอง RIFE CPU (ช้ากว่าแต่ยังลื่น)")
                shutil.rmtree(out_dir, ignore_errors=True)
                out_dir.mkdir()
                _last_pct = -1
                _proc2 = subprocess.Popen(
                    [*rife_command, "-g", "-1"],
                    cwd=str(Path(rife).parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                while _proc2.poll() is None:
                    _poll_progress()
                    time.sleep(3)
                _poll_progress()
                if _proc2.returncode:
                    say("[slow2x] RIFE CPU too slow - using FFmpeg minterpolate instead")
                    shutil.rmtree(out_dir, ignore_errors=True)
                    out_dir.mkdir()
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
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
            ]
            r = subprocess.run(encode, capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace")
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout or "ffmpeg encode failed")[-1200:])
            if _video_ok(out):
                say(f"[slow2x] RIFE {chr(2360)} {out.name}")
                return str(out)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as e:
        say(f"[slow2x] RIFE ทำงานไม่สำเร็จ — ไม่ใช้ตัวแปลงอื่น: {e}")
        return str(inp)
