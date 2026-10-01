"""Install and locate SnapGen's private Maestro runtime on Windows."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import time
import sys
import threading
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = PROJECT_ROOT / "snapgen_data" / "tools" / "maestro"
BOOTSTRAP_ROOT = RUNTIME_ROOT.parent / ".maestro-bootstrap"
LOG_FILE = PROJECT_ROOT / "snapgen_data" / "logs" / "maestro_runtime.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
PRIVATE_APP = RUNTIME_ROOT / "app"
PRIVATE_ENV = PRIVATE_APP / "env-snapgen"
PRIVATE_REPAIR_ENV = PRIVATE_APP / "env-snapgen-repair"
PRIVATE_REPAIR_ENV_2 = PRIVATE_APP / "env-snapgen-repair2"
LEGACY_APP = Path(r"D:\Maestro\app")
REPOSITORY = "https://github.com/Blizaine/Maestro.git"
MAESTRO_REVISION = "811f0f3b26abe615ea2df9ac34833bb820cc2a86"  # Release v1.9.0
UV_VERSION = "0.8.15"
UV_URL = (
    f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
    "uv-x86_64-pc-windows-msvc.zip"
)
MEDIA_TOOLS_ROOT = PROJECT_ROOT / "snapgen_data" / "tools" / "ffmpeg"
FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)

_install_lock = threading.Lock()
_media_tools_lock = threading.Lock()


def _media_tools_ready(path: Path) -> bool:
    return (path / "ffmpeg.exe").is_file() and (path / "ffprobe.exe").is_file()


def resolve_media_tools() -> Path | None:
    """Return a directory containing both ffmpeg and ffprobe."""
    candidates = (
        PROJECT_ROOT / "tools" / "ffmpeg-8.0-full_build" / "bin",
        MEDIA_TOOLS_ROOT,
        PROJECT_ROOT / "tools" / "ffmpeg",
        Path(r"C:\Program Files\Topaz Labs LLC\Topaz AI"),
        Path(r"C:\Program Files\Topaz Labs LLC\Topaz Video AI"),
    )
    for candidate in candidates:
        if _media_tools_ready(candidate):
            return candidate
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe and Path(ffmpeg).parent == Path(ffprobe).parent:
        return Path(ffmpeg).parent
    return None


def ensure_media_tools(log=None) -> Path:
    """Guarantee that Maestro receives a complete portable media-tool pair."""
    existing = resolve_media_tools()
    if existing is not None:
        _say(log, f"FFmpeg + ffprobe พร้อมใช้งาน: {existing}")
        return existing
    if os.name != "nt":
        raise RuntimeError("ไม่พบ ffmpeg และ ffprobe ที่ต้องใช้รวมไฟล์วิดีโอ")
    with _media_tools_lock:
        existing = resolve_media_tools()
        if existing is not None:
            return existing
        _say(log, "กำลังติดตั้ง FFmpeg + ffprobe สำหรับรวมไฟล์วิดีโอ...")
        archive = MEDIA_TOOLS_ROOT.parent / ".ffmpeg-download.zip"
        MEDIA_TOOLS_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(FFMPEG_URL, archive)
            with zipfile.ZipFile(archive) as package:
                members = {
                    Path(name).name.lower(): name
                    for name in package.namelist()
                    if Path(name).name.lower() in {"ffmpeg.exe", "ffprobe.exe"}
                }
                if set(members) != {"ffmpeg.exe", "ffprobe.exe"}:
                    raise RuntimeError("ชุด FFmpeg ที่ดาวน์โหลดมาไม่มี ffmpeg.exe/ffprobe.exe ครบ")
                for filename, member in members.items():
                    with package.open(member) as source, (MEDIA_TOOLS_ROOT / filename).open("wb") as target:
                        shutil.copyfileobj(source, target)
        finally:
            archive.unlink(missing_ok=True)
        if not _media_tools_ready(MEDIA_TOOLS_ROOT):
            raise RuntimeError("ติดตั้ง ffmpeg และ ffprobe ไม่ครบ")
        _say(log, f"ติดตั้ง FFmpeg + ffprobe เสร็จแล้ว: {MEDIA_TOOLS_ROOT}")
        return MEDIA_TOOLS_ROOT


def log_event(message: str) -> None:
    """Keep one bounded diagnostic log without credentials or duplicate archives."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.is_file() and LOG_FILE.stat().st_size >= MAX_LOG_BYTES:
            tail = LOG_FILE.read_bytes()[-(MAX_LOG_BYTES // 2):]
            LOG_FILE.write_bytes(b"[older Maestro log truncated]\n" + tail)
        clean = str(message).replace("\r", " ").replace("\n", " ").strip()
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().isoformat(timespec='seconds')} {clean}\n")
    except OSError:
        pass


def _python_path(app: Path) -> Path | None:
    candidates = (
        app / "env-snapgen-repair2" / "Scripts" / "python.exe",
        app / "env-snapgen-repair" / "Scripts" / "python.exe",
        app / "env-snapgen" / "Scripts" / "python.exe",
        app / "env-sol" / "Scripts" / "python.exe",
        app / "env-rtx50" / "Scripts" / "python.exe",
        app / "env" / "Scripts" / "python.exe",
        app / ".venv" / "Scripts" / "python.exe",
        app / "venv" / "Scripts" / "python.exe",
    )
    return next((path for path in candidates if path.is_file()), None)


def runtime_ready(app: Path) -> bool:
    return (app / "launch.py").is_file() and _python_path(app) is not None


def resolve_app() -> Path:
    """Prefer the portable runtime; retain an existing legacy install."""
    if runtime_ready(PRIVATE_APP):
        return PRIVATE_APP
    if runtime_ready(LEGACY_APP):
        return LEGACY_APP
    return PRIVATE_APP


def runtime_python(app: Path | None = None) -> Path | None:
    return _python_path(app or resolve_app())


def _say(log, message: str) -> None:
    log_event(message)
    if callable(log):
        log(message)


def _run(command: list[str], *, cwd: Path | None = None, log=None) -> None:
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-12:]
        if any(word in line.lower() for word in ("download", "install", "resolved", "prepared")):
            _say(log, f"Maestro: {line[:300]}")
    code = process.wait()
    if code:
        detail = "ติดตั้ง Maestro ไม่สำเร็จ: " + " | ".join(tail[-4:])
        log_event(f"FAILED exit={code}: {detail}")
        raise RuntimeError(detail)


def _windows_gpu_names() -> list[str]:
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _gpu_profile() -> tuple[str, list[str], dict[str, str]]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first_line = (result.stdout or "").splitlines()[0].strip()
        gpu_name = (
            first_line
            if result.returncode == 0
            and re.search(r"\b(?:RTX|GTX|Quadro)\b", first_line, re.IGNORECASE)
            else ""
        )
    except Exception:
        gpu_name = ""
    if not gpu_name:
        names = _windows_gpu_names()
        gpu_name = next(
            (name for name in names if re.search(r"\bRX\s*\d{4}", name, re.IGNORECASE)),
            next((name for name in names if "radeon" in name.lower()), ""),
        )
    if not gpu_name:
        log_event("GPU detection failed: no supported NVIDIA or AMD Radeon adapter")
        raise RuntimeError("LTX-2.5 Local ไม่พบ NVIDIA GPU หรือ AMD Radeon ที่รองรับ")

    log_event(f"Detected GPU: {gpu_name}")

    if "radeon" in gpu_name.lower() or "amd" in gpu_name.lower():
        if re.search(r"RX\s*(?:9060|9070)", gpu_name, re.IGNORECASE):
            index = "https://rocm.nightlies.amd.com/v2/gfx120X-all/"
            override = "12.0.1"
        elif re.search(r"RX\s*(?:7600|7700|7800|7900)", gpu_name, re.IGNORECASE):
            index = "https://rocm.nightlies.amd.com/v2/gfx110X-all/"
            override = "11.0.0"
        else:
            log_event(f"Unsupported AMD GPU: {gpu_name}")
            raise RuntimeError(f"AMD GPU รุ่นนี้ยังไม่มีชุด Maestro ที่ยืนยันแล้ว: {gpu_name}")
        log_event(f"Selected GPU runtime: Python 3.11 / ROCm / {override}")
        return "3.11", [
            "--pre",
            "torch",
            "torchaudio",
            "torchvision",
            "rocm[devel]",
            "--index-url",
            index,
        ], {
            "HSA_OVERRIDE_GFX_VERSION": override,
            "FLASH_ATTENTION_TRITON_AMD_ENABLE": "TRUE",
            "TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL": "1",
            "MIOPEN_FIND_MODE": "FAST",
        }

    if re.search(r"RTX\s*50\d{2}", gpu_name, re.IGNORECASE):
        log_event("Selected GPU runtime: Python 3.11 / CUDA 13.0")
        return "3.11", [
            "torch==2.10.0",
            "torchvision==0.25.0",
            "torchaudio==2.10.0",
            "xformers==0.0.35",
            "--index-url",
            "https://download.pytorch.org/whl/cu130",
        ], {}
    if re.search(r"RTX\s*4\d{3}", gpu_name, re.IGNORECASE):
        log_event("Selected GPU runtime: Python 3.10 / CUDA 12.8")
        return "3.10", [
            "torch==2.7.1",
            "torchvision==0.22.1",
            "torchaudio==2.7.1",
            "xformers==0.0.31",
            "--index-url",
            "https://download.pytorch.org/whl/cu128",
        ], {}
    log_event("Selected GPU runtime: Python 3.10 / CUDA 12.6")
    return "3.10", [
        "torch==2.6.0+cu126",
        "torchvision==0.21.0+cu126",
        "torchaudio==2.6.0+cu126",
        "--index-url",
        "https://download.pytorch.org/whl/cu126",
    ], {}


def runtime_environment() -> dict[str, str]:
    """Environment needed by the selected GPU runtime at launch time."""
    return _gpu_profile()[2]


def _ensure_uv(log=None) -> Path:
    current = shutil.which("uv")
    if current:
        return Path(current)
    uv_exe = BOOTSTRAP_ROOT / "uv.exe"
    if uv_exe.is_file():
        return uv_exe
    _say(log, "กำลังเตรียมตัวติดตั้ง Python สำหรับ Maestro...")
    archive = BOOTSTRAP_ROOT / "uv.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(UV_URL, archive)
    with zipfile.ZipFile(archive) as package:
        member = next((name for name in package.namelist() if name.endswith("/uv.exe") or name == "uv.exe"), None)
        if member is None:
            raise RuntimeError("แพ็กเกจตัวติดตั้ง Maestro ไม่มี uv.exe")
        with package.open(member) as source, uv_exe.open("wb") as destination:
            shutil.copyfileobj(source, destination)
    archive.unlink(missing_ok=True)
    return uv_exe


def _gpu_runtime_status(app: Path, python_override: Path | None = None) -> tuple[bool, str]:
    python = python_override or runtime_python(app)
    if python is None:
        return False, "ไม่พบ Python ใน Maestro runtime"
    env = os.environ.copy()
    env.update(runtime_environment())
    command = [
        str(python),
        "-c",
        (
            "import torch; "
            "from torch.distributed.fsdp import FullyShardedDataParallel; "
            "ok=torch.cuda.is_available(); "
            "name=torch.cuda.get_device_name(0) if ok else 'GPU unavailable'; "
            "print(f'torch={torch.__version__}; gpu={ok}; device={name}')"
        ),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, f"ตรวจ PyTorch ไม่สำเร็จ: {exc}"
    detail = (result.stdout or result.stderr or "PyTorch ไม่คืนสถานะ GPU").strip()
    return result.returncode == 0 and "; gpu=True;" in f"; {detail};", detail[-1000:]


def _build_clean_amd_runtime(
    app: Path,
    current_python: Path,
    python_version: str,
    torch_packages: list[str],
    log=None,
) -> None:
    """Build and validate AMD in an unused environment; never repair in place."""
    uv = _ensure_uv(log)
    rebuild_env = (
        PRIVATE_REPAIR_ENV_2
        if PRIVATE_REPAIR_ENV in current_python.parents
        else PRIVATE_REPAIR_ENV
    )
    shutil.rmtree(rebuild_env, ignore_errors=True)
    _say(log, f"กำลังสร้าง ROCm environment สะอาด: {rebuild_env.name}...")
    _run(
        [str(uv), "venv", "--clear", str(rebuild_env), "--python", python_version],
        cwd=RUNTIME_ROOT,
        log=log,
    )
    repair_python = rebuild_env / "Scripts" / "python.exe"
    _run(
        [
            str(uv), "pip", "install", "--python", str(repair_python),
            "-r", str(app / "requirements.txt"),
            "--index-strategy", "unsafe-best-match",
        ],
        cwd=app,
        log=log,
    )
    _run(
        [str(uv), "pip", "install", "--python", str(repair_python), "hf-xet"],
        cwd=app,
        log=log,
    )
    _run(
        [
            str(uv), "pip", "install", "--python", str(repair_python),
            "--reinstall", *torch_packages,
        ],
        cwd=app,
        log=log,
    )
    ready, detail = _gpu_runtime_status(app, repair_python)
    if not ready or "+rocm" not in detail.lower():
        raise RuntimeError(
            "สร้าง ROCm environment สะอาดแล้วแต่ตรวจ PyTorch ไม่ผ่าน\n"
            f"ผลตรวจ: {detail}\n"
            "กรุณาอัปเดต AMD Adrenalin Driver แล้วลองอีกครั้ง"
        )
    for stale_env in (PRIVATE_ENV, PRIVATE_REPAIR_ENV, PRIVATE_REPAIR_ENV_2):
        if stale_env != rebuild_env:
            shutil.rmtree(stale_env, ignore_errors=True)
    _say(log, f"ROCm environment สะอาดพร้อมใช้งาน: {detail}")


def _ensure_gpu_runtime(app: Path, log=None) -> None:
    ready, detail = _gpu_runtime_status(app)
    if ready:
        log_event(f"GPU runtime ready: {detail}")
        return
    gpu_names = _windows_gpu_names()
    is_amd = any("radeon" in name.lower() or "amd" in name.lower() for name in gpu_names)
    if not is_amd:
        raise RuntimeError(f"Maestro มองไม่เห็นการ์ดจอผ่าน PyTorch: {detail}")

    python = runtime_python(app)
    if python is None:
        raise RuntimeError("ไม่พบ Python สำหรับสร้าง Maestro ROCm")
    python_version, torch_packages, _runtime_env = _gpu_profile()
    _build_clean_amd_runtime(
        app, python, python_version, torch_packages, log
    )
    return

    _say(log, "PyTorch ROCm ยังมองไม่เห็น AMD GPU — กำลังซ่อมชุด gfx120X...")
    uv = _ensure_uv(log)
    python = runtime_python(app)
    if python is None:
        raise RuntimeError("ไม่พบ Python สำหรับซ่อม Maestro ROCm")
    _python_version, torch_packages, _runtime_env = _gpu_profile()
    repair_command = [
        str(uv), "pip", "install", "--python", str(python),
        "--reinstall", *torch_packages,
    ]
    last_error = None
    for attempt in range(1, 4):
        try:
            _run(repair_command, cwd=app, log=log)
            last_error = None
            break
        except RuntimeError as exc:
            last_error = exc
            if "access is denied" not in str(exc).lower():
                raise
            if attempt == 3:
                break
            # Interrupted installers/Defender scans can leave package metadata
            # read-only briefly on Windows. Only touch PyTorch/ROCm package
            # metadata inside this private Maestro environment, then retry.
            site_packages = python.parent / "lib" / "site-packages"
            for pattern in ("torch*", "rocm*", "amd*", "pytorch*"):
                for target in site_packages.glob(pattern):
                    try:
                        os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
                    except OSError:
                        pass
                    if target.is_dir():
                        for child in target.rglob("*"):
                            try:
                                os.chmod(child, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
                            except OSError:
                                pass
            _say(log, f"Windows ล็อกไฟล์ PyTorch — ปลดล็อกและลองซ่อมใหม่ ({attempt}/3)...")
            time.sleep(3)
    if last_error is not None:
        _say(log, "Environment PyTorch เดิมเสียหาย — กำลังสร้าง ROCm environment ใหม่...")
        rebuild_env = (
            PRIVATE_REPAIR_ENV_2
            if PRIVATE_REPAIR_ENV in python.parents
            else PRIVATE_REPAIR_ENV
        )
        shutil.rmtree(rebuild_env, ignore_errors=True)
        _run(
            [str(uv), "venv", str(rebuild_env), "--python", _python_version],
            cwd=RUNTIME_ROOT,
            log=log,
        )
        repair_python = rebuild_env / "Scripts" / "python.exe"
        # Dependencies may resolve a generic torch build. Install the selected
        # GPU build last so ROCm remains authoritative.
        _run(
            [
                str(uv), "pip", "install", "--python", str(repair_python),
                "-r", str(app / "requirements.txt"),
                "--index-strategy", "unsafe-best-match",
            ],
            cwd=app,
            log=log,
        )
        _run(
            [str(uv), "pip", "install", "--python", str(repair_python), "hf-xet"],
            cwd=app,
            log=log,
        )
        _run(
            [str(uv), "pip", "install", "--python", str(repair_python), *torch_packages],
            cwd=app,
            log=log,
        )
        ready, detail = _gpu_runtime_status(app, repair_python)
        if not ready:
            raise RuntimeError(
                "สร้าง ROCm environment ใหม่แล้วแต่ยังมองไม่เห็น AMD GPU\n"
                f"ผลตรวจ: {detail}\n"
                "กรุณาอัปเดต AMD Adrenalin Driver แล้วลองอีกครั้ง"
            )
        for stale_env in (PRIVATE_ENV, PRIVATE_REPAIR_ENV, PRIVATE_REPAIR_ENV_2):
            if stale_env != rebuild_env:
                shutil.rmtree(stale_env, ignore_errors=True)
        _say(log, f"สร้าง ROCm environment ใหม่สำเร็จ: {detail}")
        return
    ready, detail = _gpu_runtime_status(app)
    if not ready:
        log_event(f"FAILED AMD ROCm validation after repair: {detail}")
        raise RuntimeError(
            "ซ่อม PyTorch ROCm แล้วแต่ยังมองไม่เห็น AMD GPU\n"
            f"ผลตรวจ: {detail}\n"
            "กรุณาอัปเดต AMD Adrenalin Driver แล้วลองอีกครั้ง"
        )
    _say(log, f"PyTorch ROCm พร้อมใช้งาน: {detail}")


def ensure_runtime(log=None) -> Path:
    """Return a usable Maestro app, installing SnapGen's private copy if absent."""
    existing = resolve_app()
    if runtime_ready(existing):
        if existing == PRIVATE_APP:
            _ensure_gpu_runtime(existing, log)
        _say(log, f"Maestro runtime พร้อมใช้งาน: {existing}")
        return existing

    with _install_lock:
        if runtime_ready(PRIVATE_APP):
            return PRIVATE_APP
        if os.name != "nt":
            raise RuntimeError("ตัวติดตั้ง Maestro อัตโนมัติของ SnapGen รองรับ Windows เท่านั้น")

        _say(log, "เริ่มตรวจและติดตั้ง Maestro runtime")
        python_version, torch_packages, _runtime_env = _gpu_profile()
        git = shutil.which("git")
        if not git:
            log_event("FAILED: Git executable not found")
            raise RuntimeError("ไม่พบ Git กรุณารัน setup_and_run.bat อีกครั้ง")
        uv = _ensure_uv(log)
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)

        if not (RUNTIME_ROOT / ".git").is_dir():
            if any(RUNTIME_ROOT.iterdir()):
                for child in list(RUNTIME_ROOT.iterdir()):
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            _say(log, "กำลังดาวน์โหลด Maestro runtime สำหรับ SnapGen...")
            _run([git, "init", str(RUNTIME_ROOT)], log=log)
            _run([git, "-C", str(RUNTIME_ROOT), "remote", "add", "origin", REPOSITORY], log=log)
            _run(
                [git, "-C", str(RUNTIME_ROOT), "fetch", "--depth", "1", "origin", MAESTRO_REVISION],
                log=log,
            )
            _run([git, "-C", str(RUNTIME_ROOT), "checkout", "--detach", "FETCH_HEAD"], log=log)

        _say(log, f"กำลังสร้าง Maestro Python {python_version}...")
        _run([str(uv), "venv", str(PRIVATE_ENV), "--python", python_version], cwd=RUNTIME_ROOT, log=log)
        private_python = PRIVATE_ENV / "Scripts" / "python.exe"
        _say(log, "กำลังติดตั้งส่วนประมวลผลของ Maestro...")
        _run(
            [str(uv), "pip", "install", "--python", str(private_python), "-r", str(PRIVATE_APP / "requirements.txt"), "--index-strategy", "unsafe-best-match"],
            cwd=PRIVATE_APP,
            log=log,
        )
        _run(
            [str(uv), "pip", "install", "--python", str(private_python), "hf-xet"],
            cwd=PRIVATE_APP,
            log=log,
        )
        _say(log, "กำลังติดตั้ง PyTorch สำหรับการ์ดจอเครื่องนี้...")
        _run(
            [str(uv), "pip", "install", "--python", str(private_python), *torch_packages],
            cwd=PRIVATE_APP,
            log=log,
        )
        if not runtime_ready(PRIVATE_APP):
            raise RuntimeError("ติดตั้ง Maestro runtime ไม่ครบ")
        _ensure_gpu_runtime(PRIVATE_APP, log)
        shutil.rmtree(BOOTSTRAP_ROOT, ignore_errors=True)
        _say(log, "ติดตั้ง Maestro runtime สำหรับ SnapGen เสร็จแล้ว")
        return PRIVATE_APP


__all__ = [
    "ensure_media_tools",
    "ensure_runtime",
    "log_event",
    "resolve_app",
    "resolve_media_tools",
    "runtime_environment",
    "runtime_python",
    "runtime_ready",
]
