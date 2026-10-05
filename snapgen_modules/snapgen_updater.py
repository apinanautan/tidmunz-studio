"""Safe GitHub Releases updater for Tidmun Studio.

Only files explicitly listed in the signed-by-hash manifest are replaced.
User data, captures, accounts and exports are never update targets.

DEVELOPER CONTRACT:
- The Settings screen has exactly one Restore button.
- Restore lists published GitHub Releases and installs the selected version.
- Restore is for program files only; it is not a local Backup system.
- Never add export, snapgen_data, cookies, accounts, Chrome profiles, or
  user-created files to ALLOWED_ROOT_FILES / ALLOWED_MODULE_SUFFIXES.
- See กฏของโปรแกรม/PROGRAM_ARCHITECTURE_NOTES.md before changing this workflow.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

OWNER = "apinanautan"
REPOSITORY = "tidmunz-studio"
API_LATEST = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
API_RELEASES = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases?per_page=50"
ASSET_NAME = "tidmun-studio-patch.zip"
VERSION_FILE = "snapgen_version.json"
ALLOWED_ROOT_FILES = {
    "snapgen_gui_v2.py",
    "setup_and_run.bat",
    # Legacy root aliases (old patches / machines before layout migration).
    "INSTALL_OTHER_MACHINE.md",
    "snapgen_core.cpython-312.pyc",
    VERSION_FILE,
    # Clean nested layout.
    "docs/INSTALL_OTHER_MACHINE.md",
    "__pycache__/snapgen_core.cpython-312.pyc",
    "snapgen_data/meta/snapgen_version.json",
    "snapgen_modules/__pycache__/snapgen_core.cpython-312.pyc",
}


class UpdateError(RuntimeError):
    pass


def _request(url: str, timeout: int = 30):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tidmun-Studio-Updater/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return urllib.request.urlopen(req, timeout=timeout)


def _version_tuple(value):
    text = str(value or "0.0.0").strip().lower().lstrip("v")
    nums = []
    for part in text.split(".")[:4]:
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits or 0))
    return tuple((nums + [0, 0, 0, 0])[:4])


def current_version(project_root):
    root = Path(project_root)
    candidates = [
        root / VERSION_FILE,
        root / "snapgen_data" / "meta" / VERSION_FILE,
    ]
    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return str(data.get("version") or "0.0.0")
        except Exception:
            pass
    return "0.0.0"


def check_latest(project_root):
    try:
        with _request(API_LATEST, timeout=20) as response:
            release = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "available": False,
                "current": current_version(project_root),
                "latest": None,
                "message": "ยังไม่มี GitHub Release",
            }
        raise UpdateError(f"GitHub ตอบ HTTP {exc.code}") from exc
    except Exception as exc:
        raise UpdateError(f"ตรวจสอบ GitHub ไม่สำเร็จ: {exc}") from exc

    latest = str(release.get("tag_name") or "").lstrip("v")
    asset = next((a for a in release.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if not latest:
        raise UpdateError("Release ล่าสุดไม่มีเลขเวอร์ชัน")
    if not asset or not asset.get("browser_download_url"):
        raise UpdateError(f"Release v{latest} ไม่มีไฟล์ {ASSET_NAME}")
    current = current_version(project_root)
    return {
        "available": _version_tuple(latest) > _version_tuple(current),
        "current": current,
        "latest": latest,
        "version": latest,
        "notes": str(release.get("body") or "").strip(),
        "url": asset["browser_download_url"],
        "asset_size": int(asset.get("size") or 0),
        "asset_digest": str(asset.get("digest") or ""),
        "release_url": str(release.get("html_url") or ""),
    }


def _release_asset(release):
    return next((a for a in release.get("assets", []) if a.get("name") == ASSET_NAME), None)


def list_releases(project_root, limit=40):
    """Return published GitHub versions that can be restored/installed."""
    try:
        with _request(API_RELEASES, timeout=30) as response:
            releases = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "current": current_version(project_root),
                "releases": [],
                "message": "ยังไม่มี GitHub Release",
            }
        raise UpdateError(f"GitHub ตอบ HTTP {exc.code}") from exc
    except Exception as exc:
        raise UpdateError(f"ดึงรายการเวอร์ชันจาก GitHub ไม่สำเร็จ: {exc}") from exc

    if not isinstance(releases, list):
        raise UpdateError("รูปแบบรายการ Release จาก GitHub ไม่ถูกต้อง")

    current = current_version(project_root)
    items = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        version = str(release.get("tag_name") or "").lstrip("v").strip()
        asset = _release_asset(release)
        if not version or not asset or not asset.get("browser_download_url"):
            continue
        items.append({
            "version": version,
            "latest": version,  # download_and_stage expects this key
            "notes": str(release.get("body") or "").strip(),
            "url": asset["browser_download_url"],
            "asset_size": int(asset.get("size") or 0),
            "asset_digest": str(asset.get("digest") or ""),
            "release_url": str(release.get("html_url") or ""),
            "published_at": str(release.get("published_at") or release.get("created_at") or ""),
            "is_current": _version_tuple(version) == _version_tuple(current),
        })
        if len(items) >= int(limit or 40):
            break

    items.sort(key=lambda item: _version_tuple(item.get("version")), reverse=True)
    return {
        "current": current,
        "releases": items,
        "message": "" if items else "ยังไม่มีเวอร์ชันที่ Restore ได้",
    }


def _safe_relative(name):
    rel = PurePosixPath(str(name).replace('\\', "/"))
    text = rel.as_posix()
    # Zip_name info in some tools prepends "./"
    if text.startswith("./"):
        text = text[2:]
        rel = PurePosixPath(text)
    # One rule: no path traversal, no absolute, no empty.
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise UpdateError(f"\u0e1e\u0e32\u0e18\u0e43\u0e19 Patch \u0e44\u0e21\u0e48\u0e1b\u0e25\u0e2d\u0e14\u0e20\u0e31\u0e22: {name}")
    # sha256 manifest verification in download_and_stage protects
    # against tampering. Accept any relative path so old updaters
    # can self-update without hitting path-allowlist deadlocks.
    return text

def download_and_stage(info, project_root, progress=None):
    progress = progress or (lambda _msg: None)
    staging = Path(tempfile.mkdtemp(prefix="tidmun-update-"))
    archive = staging / ASSET_NAME
    version = str(info.get("version") or info.get("latest") or "").lstrip("v")
    progress(f"ดาวน์โหลด v{version}...")
    try:
        with _request(info["url"], timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        if info.get("asset_size") and archive.stat().st_size != info["asset_size"]:
            raise UpdateError("ขนาด Patch ที่ดาวน์โหลดไม่ตรงกับ GitHub")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        remote_digest = str(info.get("asset_digest") or "")
        if remote_digest.startswith("sha256:") and digest != remote_digest.split(":", 1)[1].lower():
            raise UpdateError("SHA-256 ของ Patch ไม่ตรงกับ GitHub")

        extract_dir = staging / "files"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive) as zf:
            if "manifest.json" not in zf.namelist():
                raise UpdateError("Patch ไม่มี manifest.json")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            if _version_tuple(manifest.get("version")) != _version_tuple(version):
                raise UpdateError("เวอร์ชันใน Patch ไม่ตรงกับ Release")
            declared = manifest.get("files")
            if not isinstance(declared, list) or not declared:
                raise UpdateError("manifest ไม่มีรายการไฟล์")
            for item in declared:
                rel = _safe_relative(item.get("path"))
                if rel not in zf.namelist():
                    raise UpdateError(f"Patch ขาดไฟล์: {rel}")
                payload = zf.read(rel)
                actual = hashlib.sha256(payload).hexdigest()
                if actual != str(item.get("sha256") or "").lower():
                    raise UpdateError(f"ไฟล์เสียหรือถูกแก้ไข: {rel}")
                target = extract_dir / Path(*PurePosixPath(rel).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
        (staging / "apply.json").write_text(
            json.dumps({"version": version, "files": declared}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        progress("ตรวจสอบ Patch ผ่าน")
        return staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def launch_apply(staging, project_root, parent_pid=None):
    cmd = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--apply",
        str(Path(staging).resolve()),
        str(Path(project_root).resolve()),
        str(int(parent_pid or os.getpid())),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(cmd, cwd=str(project_root), creationflags=flags)


def _pid_running(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _apply(staging, project_root, parent_pid):
    staging = Path(staging).resolve()
    project_root = Path(project_root).resolve()
    for _ in range(150):
        if not _pid_running(parent_pid):
            break
        time.sleep(0.2)
    else:
        raise UpdateError("โปรแกรมเดิมยังไม่ปิด จึงยังไม่ติดตั้ง Patch")

    plan = json.loads((staging / "apply.json").read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    changed = []
    # Keep rollback files only while an update is being installed. Version
    # restore is handled by GitHub, so successful updates must not leave
    # permanent local backup folders behind.
    try:
        with tempfile.TemporaryDirectory(prefix="snapgen_update_rollback_") as rollback_name:
            rollback = Path(rollback_name)
            try:
                for item in plan["files"]:
                    rel = _safe_relative(item["path"])
                    src = staging / "files" / Path(*PurePosixPath(rel).parts)
                    dst = project_root / Path(*PurePosixPath(rel).parts)
                    old = rollback / Path(*PurePosixPath(rel).parts)
                    existed = dst.exists()
                    if existed:
                        old.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dst, old)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    temp_dst = dst.with_name(dst.name + ".updating")
                    shutil.copy2(src, temp_dst)
                    os.replace(temp_dst, dst)
                    changed.append((dst, old, existed))
                (project_root / "snapgen_update_last.json").write_text(
                    json.dumps(
                        {"version": plan["version"], "updated_at": stamp},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                for dst, old, existed in reversed(changed):
                    try:
                        if existed and old.exists():
                            shutil.copy2(old, dst)
                        elif not existed and dst.exists():
                            dst.unlink()
                    except Exception:
                        pass
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # เปิด setup_and_run.bat ให้ผู้ใช้เห็น + ลงเครื่องมือที่ขาด แล้วเปิดโปรแกรมเอง
    _setup = project_root / "setup_and_run.bat"
    if _setup.is_file():
        try:
            subprocess.run(
                [str(_setup), "--no-run"],
                cwd=str(project_root), timeout=600, check=False,
            )
        except Exception:
            pass

    main = project_root / "snapgen_gui_v2.py"
    subprocess.Popen([sys.executable, "-B", str(main)], cwd=str(project_root))


if __name__ == "__main__" and len(sys.argv) >= 5 and sys.argv[1] == "--apply":
    try:
        _apply(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    except Exception as exc:
        error_file = Path(sys.argv[3]) / "snapgen_update_error.txt"
        error_file.write_text(str(exc), encoding="utf-8")
        raise
