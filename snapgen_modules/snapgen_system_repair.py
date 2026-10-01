# -*- coding: utf-8 -*-
"""Portable system check/repair used by Settings on every Windows machine.

Never assume a developer username, drive letter, Git, uv, winget, or a
pre-installed FFmpeg.  Everything is discovered from the running copy of the
application and the current Windows user profile.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse
import venv
import zipfile


BRIDGE_RELEASE_VERSION = "1.0.1"
BRIDGE_ZIP_URL = "https://github.com/tidmunzsocial-lab/snapgen-chatgpt-bridge/releases/download/v1.0.1/snapgen-bridge-v1.0.1.zip"
BRIDGE_ZIP_SHA256 = "ad0d1df5295a32e312a6949299549e1121d891d83e9c3821b37f0f202bba5f01"
BRIDGE_RELEASE_FILE = ".snapgen_bridge_release.json"
MAIN_BRIDGE_HOST = "127.0.0.1"
REQUIRED_APP_PACKAGES = (
    ("PIL", "Pillow"),
    ("qcloud_cos", "cos-python-sdk-v5"),
    ("tkinterdnd2", "tkinterdnd2"),
)


def _run(cmd, *, cwd=None, timeout=900):
    return subprocess.run(
        [str(x) for x in cmd], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def _pip_install(python_exe: Path, packages, log):
    packages = list(packages)
    if not packages:
        return
    log("กำลังติดตั้ง: " + ", ".join(packages))
    result = _run([python_exe, "-m", "pip", "install", "--disable-pip-version-check", *packages])
    if result.returncode:
        detail = (result.stderr or result.stdout or "pip install failed")[-1800:]
        raise RuntimeError(detail)


def _download(url: str, destination: Path, log):
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "SnapGen-System-Repair/1.0"})
    log("กำลังดาวน์โหลดจากอินเทอร์เน็ต...")
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)


def _bridge_source_ok(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "chatgpt_api").is_dir()

def _bridge_release_ok(path: Path) -> bool:
    try:
        data = json.loads((path / BRIDGE_RELEASE_FILE).read_text(encoding="utf-8"))
        return _bridge_source_ok(path) and str(data.get("version")) == BRIDGE_RELEASE_VERSION
    except (OSError, ValueError, TypeError):
        return False


def _install_bridge_source(bridge_dir: Path, log, *, refresh=False):
    """Install pinned company Bridge without touching accounts or outputs."""
    if _bridge_release_ok(bridge_dir) and not refresh:
        log(f"✓ Bridge รุ่น {BRIDGE_RELEASE_VERSION} พร้อม")
        return
    bridge_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snapgen-bridge-") as td:
        temp = Path(td)
        archive = temp / "bridge.zip"
        _download(BRIDGE_ZIP_URL, archive, log)
        actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual_hash != BRIDGE_ZIP_SHA256:
            raise RuntimeError(f"ไฟล์ Bridge เสียหรือไม่ตรงรุ่น: SHA256={actual_hash}")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(temp / "extract")
        roots = [p for p in (temp / "extract").iterdir() if p.is_dir()]
        if not roots or not _bridge_release_ok(roots[0]):
            raise RuntimeError("ไฟล์ Bridge ที่ดาวน์โหลดมาไม่สมบูรณ์")
        # Overlay source only; existing secrets/accounts and outputs survive.
        # refresh=True repairs old Bridge code on ordinary user machines
        # without deleting account captures or forcing a second login.
        shutil.copytree(roots[0], bridge_dir, dirs_exist_ok=True)
    if not _bridge_release_ok(bridge_dir):
        raise RuntimeError("ติดตั้ง Bridge แล้วแต่รุ่นไม่ตรง")
    log(f"✓ ติดตั้ง Bridge รุ่น {BRIDGE_RELEASE_VERSION} แล้ว")

def refresh_bridge_source(bridge_dir, log=print):
    """Refresh executable Bridge code while preserving user accounts/data."""
    bridge = Path(bridge_dir).expanduser().resolve()
    _install_bridge_source(bridge, log, refresh=True)
    return _ensure_bridge_venv(bridge, log)

def bridge_release_ready(bridge_dir) -> bool:
    """Return True only for exact Bridge source shipped to every workstation."""
    return _bridge_release_ok(Path(bridge_dir).expanduser().resolve())


def rebuild_bridge_runtime(bridge_dir, log=print):
    """Rebuild executable Bridge code/venv while preserving accounts and data."""
    bridge = Path(bridge_dir).expanduser().resolve()
    # Account captures live under secrets/. Never remove that folder.
    for relative in ("chatgpt_api", "chatgpt_api.egg-info", ".venv"):
        target = (bridge / relative).resolve()
        if target.parent != bridge:
            raise RuntimeError(f"พาธซ่อม Bridge ไม่ปลอดภัย: {target}")
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=False)
        elif target.exists():
            target.unlink()
    for cache in bridge.rglob("__pycache__") if bridge.is_dir() else ():
        try:
            shutil.rmtree(cache, ignore_errors=True)
        except OSError:
            pass
    log("ล้างเฉพาะ Bridge runtime แล้ว — เก็บ Account เดิมไว้")
    _install_bridge_source(bridge, log, refresh=True)
    return _ensure_bridge_venv(bridge, log)


def _ensure_bridge_venv(bridge_dir: Path, log) -> Path:
    py = bridge_dir / ".venv" / "Scripts" / "python.exe"
    if py.is_file():
        test = _run([py, "-c", "import sys; print(sys.version)"], timeout=30)
        if test.returncode == 0:
            log("✓ Python ของ Bridge ใช้งานได้")
        else:
            shutil.rmtree(bridge_dir / ".venv", ignore_errors=True)
    if not py.is_file():
        log("กำลังสร้าง Python environment สำหรับ Bridge...")
        venv.EnvBuilder(with_pip=True, clear=False).create(bridge_dir / ".venv")
    if not py.is_file():
        raise RuntimeError("สร้าง Python environment ของ Bridge ไม่สำเร็จ")
    result = _run([py, "-m", "pip", "install", "--disable-pip-version-check", "-e", "."], cwd=bridge_dir)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "ติดตั้ง Bridge dependencies ไม่สำเร็จ")[-2000:])
    check = _run([py, "-c", "import chatgpt_api; print('OK')"], cwd=bridge_dir, timeout=60)
    if check.returncode:
        raise RuntimeError((check.stderr or check.stdout or "import chatgpt_api ไม่สำเร็จ")[-1500:])
    log("✓ Bridge dependencies พร้อม")
    return py


def _find_ffmpeg(project_root: Path):
    candidates = [
        project_root / "snapgen_data" / "tools" / "ffmpeg" / "ffmpeg.exe",
        project_root / "tools" / "ffmpeg" / "ffmpeg.exe",
    ]
    system = shutil.which("ffmpeg")
    if system:
        candidates.append(Path(system))
    for item in candidates:
        if item.is_file():
            return item
    return None


def _find_curl(project_root: Path):
    bundled = project_root / "snapgen_data" / "tools" / "curl" / "curl.exe"
    if bundled.is_file():
        return bundled
    system = shutil.which("curl.exe") or shutil.which("curl")
    return Path(system) if system else None

def _find_gh(project_root: Path):
    bundled = project_root / "snapgen_data" / "tools" / "gh" / "bin" / "gh.exe"
    if bundled.is_file():
        return bundled
    system = shutil.which("gh.exe") or shutil.which("gh")
    return Path(system) if system else None

def _ensure_gh(project_root: Path, log) -> Path:
    existing = _find_gh(project_root)
    if existing:
        return existing
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64").upper()
    wanted = "windows_arm64.zip" if "ARM64" in arch else "windows_amd64.zip"
    request = urllib.request.Request(
        "https://api.github.com/repos/cli/cli/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "SnapGen-System-Repair/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.loads(response.read().decode("utf-8", "replace"))
    asset = next(
        (item for item in release.get("assets", []) if str(item.get("name", "")).lower().endswith(wanted)),
        None,
    )
    if not asset or not asset.get("browser_download_url"):
        raise RuntimeError("หา GitHub CLI สำหรับ Windows รุ่นล่าสุดไม่พบ")
    target = project_root / "snapgen_data" / "tools" / "gh"
    with tempfile.TemporaryDirectory(prefix="snapgen-gh-") as td:
        temp = Path(td)
        archive = temp / "gh.zip"
        _download(asset["browser_download_url"], archive, log)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(temp / "extract")
        matches = list((temp / "extract").rglob("gh.exe"))
        if not matches:
            raise RuntimeError("ดาวน์โหลด GitHub CLI แล้วแต่ไม่พบ gh.exe")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(matches[0].parent, target / "bin", dirs_exist_ok=True)
    gh = target / "bin" / "gh.exe"
    if not gh.is_file():
        raise RuntimeError("ติดตั้ง GitHub CLI แบบพกพาไม่สำเร็จ")
    os.environ["PATH"] = str(gh.parent) + os.pathsep + os.environ.get("PATH", "")
    log(f"✓ ติดตั้ง GitHub CLI แบบพกพาแล้ว: {gh}")
    return gh


def _ensure_curl(project_root: Path, log) -> Path:
    existing = _find_curl(project_root)
    if existing:
        log(f"✓ curl พร้อม: {existing}")
        return existing
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64").upper()
    package = "win64a-mingw.zip" if "ARM64" in arch else "win64-mingw.zip"
    url = f"https://curl.se/windows/latest.cgi?p={package}"
    target_dir = project_root / "snapgen_data" / "tools" / "curl"
    with tempfile.TemporaryDirectory(prefix="snapgen-curl-") as td:
        temp = Path(td)
        archive = temp / "curl.zip"
        _download(url, archive, log)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(temp / "extract")
        matches = list((temp / "extract").rglob("curl.exe"))
        if not matches:
            raise RuntimeError("ดาวน์โหลด curl แล้วแต่ไม่พบ curl.exe")
        source_dir = matches[0].parent
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    curl_exe = target_dir / "curl.exe"
    if not curl_exe.is_file():
        raise RuntimeError("ติดตั้ง curl แบบพกพาไม่สำเร็จ")
    os.environ["PATH"] = str(target_dir) + os.pathsep + os.environ.get("PATH", "")
    log(f"✓ ติดตั้ง curl แบบพกพาแล้ว: {curl_exe}")
    return curl_exe


def _ensure_ffmpeg(project_root: Path, python_exe: Path, log) -> Path:
    existing = _find_ffmpeg(project_root)
    if existing:
        log(f"✓ FFmpeg พร้อม: {existing}")
        return existing
    # imageio-ffmpeg supplies a portable Windows binary and needs no winget.
    _pip_install(python_exe, ["imageio-ffmpeg"], log)
    result = _run([python_exe, "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"], timeout=60)
    source = Path((result.stdout or "").strip())
    if result.returncode or not source.is_file():
        raise RuntimeError((result.stderr or result.stdout or "หา FFmpeg ที่ดาวน์โหลดมาไม่พบ")[-1200:])
    target = project_root / "snapgen_data" / "tools" / "ffmpeg" / "ffmpeg.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    log(f"✓ ติดตั้ง FFmpeg แบบพกพาแล้ว: {target}")
    return target


def _ensure_project_dirs(project_root: Path, log):
    names = ("image", "video", "ref", "prop", "story_face", "karaoke")
    for name in names:
        (project_root / "export" / name).mkdir(parents=True, exist_ok=True)
    (project_root / "snapgen_data" / "logs").mkdir(parents=True, exist_ok=True)
    log("✓ โฟลเดอร์งานและ log พร้อม")


def _check_machine_specific_config(project_root: Path, log):
    """Repair reference paths copied from a PC with another drive letter."""
    config_path = project_root / "snapgen_data" / "snapgen_config.json"
    if not config_path.is_file():
        return False
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    stale = False
    changed = False

    def resolve_moved_folder(value):
        original = Path(str(value))
        if original.is_dir():
            return str(original)
        try:
            parts = original.parts
            tail = Path(*parts[1:]) if original.drive and len(parts) > 1 else original
            if os.name == "nt":
                for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                    candidate = Path(f"{letter}:/") / tail
                    if candidate.is_dir():
                        return str(candidate)
            local_ref = project_root / "export" / "ref" / original.name
            if local_ref.is_dir():
                return str(local_ref)
        except Exception:
            pass
        return ""

    ref_folder = str(data.get("ref_folder") or "").strip()
    if ref_folder and not Path(ref_folder).exists():
        repaired = resolve_moved_folder(ref_folder)
        if repaired:
            data["ref_folder"] = repaired
            log(f"✓ ย้าย ref_folder ให้ตรงกับเครื่องนี้: {repaired}")
        else:
            data.pop("ref_folder", None)
            stale = True
            log("✓ ล้าง ref_folder จากเครื่องเดิมแล้ว — เลือกโฟลเดอร์อ้างอิงของเครื่องนี้ใหม่ได้")
        changed = True
    last_dirs = data.get("last_dirs")
    if isinstance(last_dirs, dict):
        for key, value in list(last_dirs.items()):
            value = str(value or "").strip()
            if value and not Path(value).exists():
                if key == "image_ref":
                    repaired = resolve_moved_folder(value)
                    if repaired:
                        last_dirs[key] = repaired
                        data["ref_folder"] = repaired
                        log(f"✓ ย้าย last_dirs.image_ref ให้ตรงกับเครื่องนี้: {repaired}")
                    else:
                        last_dirs.pop(key, None)
                        stale = True
                        log("✓ ล้าง last_dirs.image_ref จากเครื่องเดิมแล้ว")
                    changed = True
                else:
                    stale = True
                    log(f"⚠ last_dirs.{key} หาไม่พบในเครื่องนี้")
    saved_refs = data.get("image_manual_refs")
    if isinstance(saved_refs, list):
        valid_refs = [str(path) for path in saved_refs if Path(str(path)).is_file()]
        folder = str(data.get("ref_folder") or "")
        if folder:
            for path in saved_refs:
                moved = Path(folder) / Path(str(path)).name
                if moved.is_file() and str(moved) not in valid_refs:
                    valid_refs.append(str(moved))
        if valid_refs != saved_refs:
            data["image_manual_refs"] = valid_refs[:10]
            changed = True
            log(f"✓ ปรับไฟล์แนบอ้างอิงให้ตรงกับเครื่องนี้: {len(valid_refs[:10])} รูป")
    if changed:
        temp = config_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(temp), str(config_path))
    return stale


def _find_tailscale():
    candidates = [
        shutil.which("tailscale.exe") or shutil.which("tailscale"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale-ipn.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _ensure_tailscale(log):
    existing = _find_tailscale()
    if existing:
        log(f"✓ Tailscale พร้อม: {existing}")
        return existing
    log("ไม่พบ Tailscale — กำลังดาวน์โหลดตัวติดตั้งทางการ...")
    page_url = "https://pkgs.tailscale.com/stable/"
    request = urllib.request.Request(page_url, headers={"User-Agent": "SnapGen-System-Repair/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        page = response.read().decode("utf-8", "replace")
    names = re.findall(r'href=["\']([^"\']*tailscale-setup-full-[^"\']+\.exe)["\']', page, re.I)
    if not names:
        raise RuntimeError("หาไฟล์ติดตั้ง Tailscale รุ่นล่าสุดไม่พบ")
    installer_url = urllib.parse.urljoin(page_url, names[-1])
    with tempfile.TemporaryDirectory(prefix="snapgen-tailscale-") as td:
        installer = Path(td) / "tailscale-setup.exe"
        _download(installer_url, installer, log)
        result = _run([installer, "/quiet"], timeout=600)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or f"installer exit {result.returncode}")[-1200:])
    found = _find_tailscale()
    if not found:
        raise RuntimeError("ติดตั้ง Tailscale แล้วแต่ยังหาโปรแกรมไม่พบ; อาจต้องอนุญาต UAC")
    log(f"✓ ติดตั้ง Tailscale แล้ว: {found}")
    return found


def _tailscale_state(executable: Path):
    cli = executable
    if executable.name.lower() == "tailscale-ipn.exe":
        sibling = executable.with_name("tailscale.exe")
        if sibling.is_file():
            cli = sibling
    result = _run([cli, "status", "--json"], timeout=15)
    if result.returncode:
        return {"ok": False, "ips": [], "email": "", "error": (result.stderr or result.stdout).strip()}
    try:
        data = json.loads(result.stdout or "{}")
    except Exception as exc:
        return {"ok": False, "ips": [], "email": "", "error": str(exc)}
    self_node = data.get("Self") or {}
    user_id = str(self_node.get("UserID") or "")
    user = (data.get("User") or {}).get(user_id) or {}
    email = user.get("LoginName") or self_node.get("HostName") or ""
    ips = [str(x) for x in self_node.get("TailscaleIPs") or []]
    backend = str(data.get("BackendState") or "")
    return {"ok": backend == "Running" and bool(ips), "ips": ips, "email": str(email), "backend": backend}


def _bridge_health(host=MAIN_BRIDGE_HOST, port=8000):
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/health",
            headers={"Authorization": "Bearer local-dev-key"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        return bool(data.get("ok")), data
    except Exception as exc:
        return False, {"error": str(exc)}


def _start_bridge(bridge_dir: Path, bridge_python: Path, log, host=MAIN_BRIDGE_HOST):
    ok, data = _bridge_health(host)
    if ok:
        log(f"✓ Bridge ทำงานอยู่แล้ว (account={data.get('account') or '-'})")
        return True
    accounts_dir = bridge_dir / "secrets" / "accounts"
    accounts = sorted(p.name for p in accounts_dir.iterdir() if p.is_dir()) if accounts_dir.is_dir() else []
    env = os.environ.copy()
    env["CHATGPT_API_KEY"] = "local-dev-key"
    env["CHATGPT_ACCOUNTS_DIR"] = "./secrets/accounts"
    env["CHATGPT_ACCOUNT_STRATEGY"] = "auto"
    command = [
        bridge_python, "-m", "chatgpt_api", "serve", "--host", "0.0.0.0",
        "--port", "8000", "--api-key", "local-dev-key",
        "--account-strategy", "auto", "--web-timeout", "600", "--normal-chat",
    ]
    if accounts:
        env["CHATGPT_ACCOUNT"] = accounts[0]
        env["CHATGPT_ACCOUNTS"] = ",".join(accounts)
        command += ["--account", accounts[0], "--accounts", ",".join(accounts)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, cwd=str(bridge_dir), env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
    for _ in range(20):
        time.sleep(1)
        ok, data = _bridge_health(host)
        if ok:
            log(f"✓ Bridge เริ่มทำงานแล้ว (account={data.get('account') or '-'})")
            return True
    if not accounts:
        log("⚠ Bridge ติดตั้งแล้ว แต่ยังไม่มี Account — เปิด Bridge แล้วเพิ่ม Account")
    else:
        log("⚠ Bridge ยังไม่ตอบสนอง — เปิด Bridge Manager เพื่อตรวจ account")
    return False


def repair_all(project_root, bridge_dir=None, log=print, patch_bridge=None, bridge_host=MAIN_BRIDGE_HOST):
    """Check and repair this machine. Returns a structured summary."""
    root = Path(project_root).resolve()
    bridge = Path(bridge_dir or (Path.home() / "chatgpt-api")).expanduser().resolve()
    python_exe = Path(sys.executable).resolve()
    failures = []
    repaired = []

    log("=== ตรวจและแก้บัคอัตโนมัติสำหรับเครื่องนี้ ===")
    log(f"โปรแกรม: {root}")
    log(f"ผู้ใช้ Windows: {Path.home()}")
    log(f"Python: {python_exe}")
    log("หมายเหตุ: ระบบตรวจจากเครื่องปัจจุบัน ไม่ใช้พาธของเครื่องผู้พัฒนา")

    try:
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError(f"โปรแกรมต้องใช้ Python 3.12 แต่กำลังรัน {sys.version_info.major}.{sys.version_info.minor}")
        log("✓ Python 3.12 ถูกต้อง")
    except Exception as exc:
        failures.append(f"Python: {exc}")

    try:
        missing = [package for module, package in REQUIRED_APP_PACKAGES if importlib.util.find_spec(module) is None]
        if missing:
            _pip_install(python_exe, missing, log)
            repaired.extend(missing)
        log("✓ Python packages หลักพร้อม")
    except Exception as exc:
        failures.append(f"Python packages: {exc}")
        log(f"✗ Python packages: {exc}")

    try:
        _ensure_project_dirs(root, log)
        _check_machine_specific_config(root, log)
    except Exception as exc:
        failures.append(f"Folders: {exc}")

    try:
        if not _find_curl(root):
            repaired.append("curl")
        _ensure_curl(root, log)
    except Exception as exc:
        failures.append(f"curl: {exc}")
        log(f"✗ curl: {exc}")

    try:
        gh = _ensure_gh(root, log)
        auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=20)
        if auth.returncode:
            log("⚠ GitHub ยังไม่ได้ Login — Error จะเก็บคิวในเครื่องและส่งหลัง Login")
        else:
            log("✓ ระบบรายงาน Error กลางพร้อมส่ง GitHub Issues")
    except Exception as exc:
        log(f"⚠ ตรวจระบบรายงาน Error ไม่สำเร็จ: {exc}")

    try:
        if not _find_ffmpeg(root):
            repaired.append("FFmpeg")
        _ensure_ffmpeg(root, python_exe, log)
    except Exception as exc:
        failures.append(f"FFmpeg: {exc}")
        log(f"✗ FFmpeg: {exc}")

    try:
        # Slow motion is intentionally RIFE-only.  Install the complete
        # executable/DLL/model package on this workstation during Repair so
        # the first real video job does not discover missing tools late.
        import ai_slow2x
        rife_dir = root / "snapgen_data" / "tools" / "rife-ncnn-vulkan"
        if not ai_slow2x._rife_install_complete(rife_dir):
            repaired.append("RIFE AI Slow 2x")
        rife = ai_slow2x.ensure_rife_tool(log=log)
        log(f"✓ RIFE AI Slow 2x พร้อม: {rife}")
    except Exception as exc:
        failures.append(f"RIFE AI Slow 2x: {exc}")
        log(f"✗ RIFE AI Slow 2x: {exc}")

    try:
        import ai_upscale
        w2x = ai_upscale.ensure_waifu2x_tool(log=log)
        log("✓ Resize+sharpen ready")
    except Exception as exc:
        failures.append(f"Resize+sharpen: {exc}")
        log(f"✗ Resize+sharpen: {exc}")

    bridge_python = None
    try:
        tailscale_exe = _ensure_tailscale(log)
        ts = _tailscale_state(tailscale_exe)
        if not ts.get("ok"):
            raise RuntimeError("Tailscale ยังไม่ได้ล็อกอิน/เชื่อมต่อ — เปิด Tailscale แล้ว Sign in หนึ่งครั้ง")
        log(f"✓ Tailscale เชื่อมต่อแล้ว: {ts.get('email') or '-'} | IP={', '.join(ts.get('ips') or [])}")
        log("✓ เครื่องนี้ใช้ Bridge ส่วนตัวที่ 127.0.0.1:8000")
        source_was_missing = not _bridge_source_ok(bridge)
        _install_bridge_source(bridge, log)
        if source_was_missing:
            repaired.append("Bridge source")
        bridge_python = _ensure_bridge_venv(bridge, log)
        if callable(patch_bridge):
            patch_bridge(bridge, lambda message: log(message))
        _start_bridge(bridge, bridge_python, log, "127.0.0.1")
    except Exception as exc:
        failures.append(f"Bridge: {exc}")
        log(f"✗ Bridge: {exc}")

    # Check only critical files that make the app unable to start.
    # Optional modules (ai_upscale, .ico, etc.) are checked later individually.
    _critical = (
        root / "snapgen_gui_v2.py",
        root / "__pycache__" / "snapgen_core.cpython-312.pyc",
        root / "snapgen_modules" / "snapgen_page_video.py",
        root / "snapgen_modules" / "snapgen_page_image.py",
    )
    _missing_critical = [str(p) for p in _critical if not p.is_file()]
    if _missing_critical:
        failures.append("ไฟล์หลักขาด: " + ", ".join(_missing_critical))
        log("✗ ไฟล์หลักไม่ครบ — ต้องคัดลอกทั้งโฟลเดอร์ Project snapgen.ai")
    else:
        log("✓ ไฟล์หลักครบ")

    # ── Pipeline test: ใช้ไฟล์วิดีโอจริงใน export/video ─────────────────
    try:
        log("=== ทดสอบ Pipeline: FFmpeg → AI Upscale → AI Slow 2x ===")
        _test_480p = None
        ffmpeg = _find_ffmpeg(root) or "ffmpeg"
        _videos = sorted([p for p in (root / "export" / "video").glob("*.mp4") if "_slow2x" not in p.stem.lower() and "_pipe_" not in p.stem], key=lambda p: p.stat().st_mtime, reverse=True)
        _test_src = _videos[0] if _videos else None
        if _test_src is None or not _test_src.is_file():
            log("⚠ ไม่มีวิดีโอใน export/video — ข้ามทดสอบ Pipeline")
        else:
            log(f"✓ ใช้วิดีโอ: {_test_src.name} ({_test_src.stat().st_size} bytes)")
            # ย่อเป็น 480p สำหรับทดสอบ (ถ้าต้นฉบับสูงกว่า)
            _test_480p = _test_src.with_name("_pipe_480p.mp4")
            _r480 = _run([ffmpeg, "-y", "-i", str(_test_src),
                          "-vf", "scale=-2:480:flags=lanczos",
                          "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                          str(_test_480p)], timeout=60)
            if _r480.returncode == 0 and _test_480p.is_file():
                log(f"✓ ย่อเป็น 480p สำหรับทดสอบ")
                _test_src = _test_480p
            else:
                log("⚠ ย่อ 480p ไม่สำเร็จ — ใช้ต้นฉบับ")
            # ทดสอบ FFmpeg Lanczos resize 480/720 → 1080
            _r1080 = _test_src.with_name("_pipe_1080p.mp4")
            _t0 = time.time()
            _r = _run([ffmpeg, "-y", "-i", str(_test_src),
                       "-vf", "scale=-2:1080:flags=lanczos",
                       "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                       str(_r1080)], timeout=60)
            _t = time.time() - _t0
            if _r.returncode == 0 and _r1080.is_file():
                log(f"✓ FFmpeg resize →1080p: {_t:.0f}วินาที")
            else:
                log(f"✗ FFmpeg resize ล้มเหลว: {(_r.stderr or '')[-200:]}")
            _r1080.unlink(missing_ok=True)
            # ทดสอบ waifu2x AI Upscale
            _w2x_out = _test_src.with_name("_pipe_w2x.mp4")
            try:
                import ai_upscale as _aup
                _t0 = time.time()
                _aup.upscale_video_ai(str(_test_src), output_video=str(_w2x_out),
                                      target_height=720, log=log)
                _t = time.time() - _t0
                if _w2x_out.is_file():
                    log(f"✓ waifu2x 2x upscale: {_t:.0f}วินาที")
                else:
                    log("✗ waifu2x ล้มเหลว (ไม่มี output)")
            except Exception as _e:
                log(f"✗ waifu2x ล้มเหลว: {_e}")
            _w2x_out.unlink(missing_ok=True)
            # ทดสอบ RIFE Slow 2x
            _slow_out = _test_src.with_name("_pipe_slow2x.mp4")
            try:
                import ai_slow2x as _as2x
                _t0 = time.time()
                _as2x.make_ai_slow2x(str(_test_src), output_video=str(_slow_out),
                                     factor=2, log=log)
                _t = time.time() - _t0
                if _slow_out.is_file():
                    log(f"✓ RIFE Slow 2x: {_t:.0f}วินาที")
                else:
                    log("✗ RIFE Slow 2x ล้มเหลว")
            except Exception as _e:
                log(f"✗ RIFE Slow 2x ล้มเหลว: {_e}")
            _slow_out.unlink(missing_ok=True)
            # ── ทดสอบ Full Pipeline: 480p→AI720→FF1080→Slow2x ──
            log("--- Full Pipeline: 480p→waifu2x 720p→FF 1080p→Slow2x ---")
            _full_ai = _test_src.with_name("_pipe_full_ai720.mp4")
            _full_1080 = _test_src.with_name("_pipe_full_1080.mp4")
            _full_slow = _test_src.with_name("_pipe_full_slow.mp4")
            _full_ok = True
            # Step 1: AI 720p
            try:
                import ai_upscale as _aup
                _t0 = time.time()
                _aup.upscale_video_ai(str(_test_src), output_video=str(_full_ai), target_height=720, log=log)
                _t = time.time() - _t0
                if _full_ai.is_file():
                    log(f"  ✓ waifu2x→720p: {_t:.0f}วินาที")
                else:
                    log("  ✗ waifu2x→720p ล้มเหลว"); _full_ok = False
            except Exception as _e:
                log(f"  ✗ waifu2x→720p: {_e}"); _full_ok = False
            # Step 2: FFmpeg 1080p (ใช้ผลจาก AI ถ้ามี, ถ้าไม่ใช้ต้นฉบับ)
            _full_src = _full_ai if _full_ai.is_file() else _test_src
            _t0 = time.time()
            _r = _run([ffmpeg, "-y", "-i", str(_full_src),
                       "-vf", "scale=-2:1080:flags=lanczos",
                       "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                       str(_full_1080)], timeout=120)
            _t = time.time() - _t0
            if _r.returncode == 0 and _full_1080.is_file():
                log(f"  ✓ FF→1080p: {_t:.0f}วินาที")
            else:
                log(f"  ✗ FF→1080p ล้มเหลว: {(_r.stderr or "")[-300:]}"); _full_ok = False
            # Step 3: Slow 2x (ใช้ 1080p ถ้ามี, ถ้าไม่ใช้ต้นฉบับ)
            _full_src2 = _full_1080 if _full_1080.is_file() else _test_src
            try:
                import ai_slow2x as _as2x
                _t0 = time.time()
                _as2x.make_ai_slow2x(str(_full_src2), output_video=str(_full_slow), factor=2, log=log)
                _t = time.time() - _t0
                if _full_slow.is_file():
                    log(f"  ✓ Slow2x: {_t:.0f}วินาที")
                else:
                    log("  ✗ Slow2x ล้มเหลว"); _full_ok = False
            except Exception as _e:
                log(f"  ✗ Slow2x: {_e}"); _full_ok = False
            if _full_ok:
                log("✓ Full Pipeline สำเร็จ")
            else:
                log("✗ Full Pipeline มีปัญหา")
            for _f in (_full_ai, _full_1080, _full_slow):
                _f.unlink(missing_ok=True)
        log("=== จบทดสอบ Pipeline ===")
        # cleanup 480p temp
        for _f in (_test_480p,):
            try: _f.unlink(missing_ok=True)
            except: pass
    except Exception as _pe:
        log(f"⚠ ทดสอบ Pipeline มีปัญหา: {_pe}")
    # Warn about optional files but don't fail
    _optional = (
        root / "snapgen_modules" / "snapgen_page_prop.py",
        root / "snapgen_modules" / "snapgen_page_ref.py",
        root / "snapgen_modules" / "snapgen_page_story_face.py",
        root / "snapgen_modules" / "snapgen_page_karaoke.py",
        root / "snapgen_modules" / "ai_slow2x.py",
        root / "snapgen_modules" / "ai_upscale.py",
        root / "snapgen_modules" / "snapgen_updater.py",
        root / "assets" / "tidmun_studio_icon_final.ico",
    )
    _missing_optional = [str(p) for p in _optional if not p.is_file()]
    if _missing_optional:
        log("⚠ ไฟล์เพิ่มเติมขาด (อาจต้องอัปเดตก่อน): " + ", ".join(_missing_optional))

    summary = {"ok": not failures, "repaired": repaired, "failures": failures,
               "project_root": str(root), "bridge_dir": str(bridge)}
    report = root / "snapgen_data" / "logs" / "system_repair.json"
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        log(f"=== เสร็จ แต่ยังเหลือ {len(failures)} ปัญหา — ดู {report} ===")
    else:
        log("=== ตรวจครบแล้ว ระบบพร้อมใช้งาน ===")
    return summary
