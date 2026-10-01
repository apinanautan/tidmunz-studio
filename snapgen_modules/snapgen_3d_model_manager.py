# -*- coding: utf-8 -*-
"""Install, inspect, and remove per-machine local 3D model weights."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "snapgen_data" / "tools"
Progress = Callable[[str], None]
INSTALL_LOG = PROJECT_ROOT / "snapgen_data" / "logs" / "3d_install.log"

COMFY_MODELS = Path(r"D:\ComfyUI-Easy-Install\ComfyUI-Easy-Install\ComfyUI\models")
TRIPOSPLAT_REPO = "https://github.com/VAST-AI-Research/TripoSplat.git"
COMFYUI_REPO = "https://github.com/comfyanonymous/ComfyUI.git"
TRELLIS2_REPO = "https://github.com/IgorAherne/TRELLIS.2-stableprojectorz.git"
TRIPOSPLAT_ZIP = "https://github.com/VAST-AI-Research/TripoSplat/archive/refs/heads/main.zip"
COMFYUI_ZIP = "https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
TRELLIS2_ZIP = "https://github.com/IgorAherne/TRELLIS.2-stableprojectorz/archive/refs/heads/main.zip"
PYTHON311_ZIP = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

_TRIPOSPLAT_REQUIRED = (
    "ckpts/diffusion_models/triposplat_fp16.safetensors",
    "ckpts/vae/triposplat_vae_decoder_fp16.safetensors",
    "ckpts/clip_vision/dino_v3_vit_h.safetensors",
    "ckpts/vae/flux2-vae.safetensors",
    "ckpts/background_removal/birefnet.safetensors",
    "bg_models/RMBG-2.0/model.safetensors",
)
_TRELLIS_REQUIRED = (
    "code/MODELS/dinov3/model.safetensors",
    "code/MODELS/RMBG-2.0/model.safetensors",
)
_TRELLIS_HF_FILES = (
    "pipeline.json",
    "texturing_pipeline.json",
    "ckpts/shape_dec_next_dc_f16c32_fp16.json",
    "ckpts/ss_flow_img_dit_1_3B_64_bf16.safetensors",
    "ckpts/ss_flow_img_dit_1_3B_64_bf16.json",
    "ckpts/shape_dec_next_dc_f16c32_fp16.safetensors",
    "ckpts/shape_enc_next_dc_f16c32_fp16.json",
    "ckpts/shape_enc_next_dc_f16c32_fp16.safetensors",
    "ckpts/slat_flow_img2shape_dit_1_3B_1024_bf16.json",
    "ckpts/slat_flow_img2shape_dit_1_3B_1024_bf16.safetensors",
    "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16.json",
    "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16.safetensors",
    "ckpts/slat_flow_imgshape2tex_dit_1_3B_1024_bf16.json",
    "ckpts/slat_flow_imgshape2tex_dit_1_3B_1024_bf16.safetensors",
    "ckpts/slat_flow_imgshape2tex_dit_1_3B_512_bf16.json",
    "ckpts/slat_flow_imgshape2tex_dit_1_3B_512_bf16.safetensors",
    "ckpts/tex_dec_next_dc_f16c32_fp16.json",
    "ckpts/tex_dec_next_dc_f16c32_fp16.safetensors",
    "ckpts/tex_enc_next_dc_f16c32_fp16.json",
    "ckpts/tex_enc_next_dc_f16c32_fp16.safetensors",
)

_TRIPOSPLAT_HF_FILES = _TRIPOSPLAT_REQUIRED[:-1]


def _tool(name: str) -> Path:
    key = str(name).strip().lower()
    if key == "triposplat":
        return TOOLS_ROOT / "triposplat"
    if key in {"trellis.2", "trellis2"}:
        return TOOLS_ROOT / "trellis2"
    raise ValueError(f"ไม่รู้จักโมเดล 3D: {name}")


def _required(name: str) -> tuple[str, ...]:
    return _TRIPOSPLAT_REQUIRED if str(name).strip().lower() == "triposplat" else _TRELLIS_REQUIRED


def _bytes(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        if path.is_file():
            total += path.stat().st_size
        elif path.is_dir():
            for root, _dirs, files in os.walk(path):
                for filename in files:
                    try:
                        total += (Path(root) / filename).stat().st_size
                    except OSError:
                        pass
    return total


def model_info(name: str) -> dict:
    tool = _tool(name)
    required = [tool / relative for relative in _required(name)]
    key = str(name).strip().lower()
    if key == "triposplat":
        required.extend((
            tool / ".venv" / "Scripts" / "python.exe",
            tool / ".venv" / "Lib" / "site-packages" / "torch" / "__init__.py",
            tool / ".venv" / "Lib" / "site-packages" / "transformers" / "__init__.py",
            tool / "source" / "triposplat.py",
            tool / "source" / "model.py",
            tool / "source" / "snapgen_splat_to_glb.py",
            tool / "comfy_source" / "comfy_extras" / "nodes_gaussian_splat.py",
        ))
    else:
        trellis_python = _trellis_python(tool)
        trellis_site = (
            trellis_python.parent.parent / "Lib" / "site-packages"
            if trellis_python.parent.name.lower() == "scripts"
            else trellis_python.parent / "Lib" / "site-packages"
        )
        required.extend((
            trellis_python,
            trellis_site / "torch" / "__init__.py",
            trellis_site / "o_voxel" / "__init__.py",
            tool / "code" / "install.py",
            tool / "code" / "trellis2" / "__init__.py",
        ))
        hub = tool / "code" / "models" / "hub" / "models--microsoft--TRELLIS.2-4B"
        refs = hub / "refs" / "main"
        try:
            revision = refs.read_text(encoding="utf-8").strip()
        except OSError:
            revision = ""
        snapshot = hub / "snapshots" / revision
        if not snapshot.is_dir():
            snapshots = hub / "snapshots"
            candidates = sorted(
                (path for path in snapshots.glob("*") if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            ) if snapshots.is_dir() else []
            snapshot = candidates[0] if candidates else snapshot
        required.extend(snapshot / relative for relative in _TRELLIS_HF_FILES)
    return {
        "name": "TripoSplat" if key == "triposplat" else "TRELLIS.2",
        "installed": all(path.is_file() and path.stat().st_size > 0 for path in required),
        "bytes": _bytes(required),
        "path": str(tool),
    }


def list_models() -> list[dict]:
    return [model_info("TripoSplat"), model_info("TRELLIS.2")]


def _run(command: list[str], cwd: Path, progress: Progress) -> None:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_DISABLE_XET"] = "1"
    # Multi-GB weights often have long quiet periods on ordinary team PCs.
    # Keep the connection alive; a failed process is retried and Hugging Face
    # resumes its existing .incomplete file instead of starting over.
    env["HF_HUB_DOWNLOAD_TIMEOUT"] = "600"
    env["HF_HUB_ETAG_TIMEOUT"] = "60"
    process = subprocess.Popen(
        command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    tail: list[str] = []
    INSTALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with INSTALL_LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n[{started}] cwd={cwd}\nCOMMAND: {subprocess.list2cmdline(command)}\n")
            for line in process.stdout:
                text = line.strip()
                if text:
                    tail.append(text)
                    del tail[:-12]
                    log.write(text + "\n")
                    log.flush()
                    progress(text)
    except OSError:
        for line in process.stdout:
            text = line.strip()
            if text:
                tail.append(text)
                del tail[:-12]
                progress(text)
    code = process.wait()
    if code:
        detail = "\n".join(tail)
        command_name = Path(str(command[0])).name if command else "คำสั่งไม่ทราบชื่อ"
        message = f"{command_name} หยุดทำงาน (code {code})"
        if detail:
            message += f"\nรายละเอียดล่าสุด:\n{detail}"
        try:
            with INSTALL_LOG.open("a", encoding="utf-8") as log:
                log.write(f"EXIT: {code}\nDETAIL: {detail}\n")
        except OSError:
            pass
        raise RuntimeError(message)

def _safe_tool_path(path: Path) -> Path:
    """Reject source extraction targets outside this app's tool directory."""
    root = TOOLS_ROOT.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError(f"ปฏิเสธตำแหน่งติดตั้งที่ไม่ปลอดภัย: {resolved}")
    return resolved


def _zip_member_path(staging: Path, member_name: str) -> Path:
    """Return safe extraction path; reject traversal and absolute ZIP entries."""
    normalized = member_name.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or ".." in relative.parts or (relative.parts and ":" in relative.parts[0]):
        raise RuntimeError(f"ไฟล์ ZIP มีตำแหน่งไม่ปลอดภัย: {member_name}")
    destination = (staging / Path(*relative.parts)).resolve()
    staging_root = staging.resolve()
    if destination != staging_root and staging_root not in destination.parents:
        raise RuntimeError(f"ไฟล์ ZIP หลุดนอกโฟลเดอร์ชั่วคราว: {member_name}")
    return destination


def _extract_zip_safely(archive: Path, staging: Path, progress: Progress) -> None:
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            if not member.filename:
                continue
            destination = _zip_member_path(staging, member.filename)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def _repo_payload_root(staging: Path, subdir: str | None) -> Path:
    """Find GitHub's single archive root, then optional repository subdirectory."""
    if subdir:
        direct = staging / Path(*PurePosixPath(subdir).parts)
        if direct.is_dir():
            return direct
    top_dirs = [item for item in staging.iterdir() if item.is_dir() and item.name != "__MACOSX"]
    if len(top_dirs) == 1:
        root = top_dirs[0]
        if subdir:
            nested = root / Path(*PurePosixPath(subdir).parts)
            if nested.is_dir():
                return nested
        return root
    if subdir:
        raise RuntimeError(f"ไฟล์ ZIP ไม่มีโฟลเดอร์ที่ต้องใช้: {subdir}")
    return staging


def _download_repo_zip(
    url: str,
    target: Path,
    progress: Progress,
    *,
    subdir: str | None = None,
) -> None:
    """Download and merge repository source without requiring Git on Windows."""
    target = _safe_tool_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent.parent / "downloads" / f"{target.name}-source.zip"
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-zip-", dir=str(target.parent)))
    staging = _safe_tool_path(staging)
    try:
        progress(f"กำลังดาวน์โหลดตัวโปรแกรม {target.name} จาก GitHub (ไม่ต้องใช้ Git)")
        _download_file(url, archive, progress)
        progress(f"กำลังแตกไฟล์ตัวโปรแกรม {target.name}")
        _extract_zip_safely(archive, staging, progress)
        payload = _repo_payload_root(staging, subdir)
        if not payload.is_dir():
            raise RuntimeError(f"ไฟล์ ZIP ของ {target.name} ไม่ครบ")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(payload, target, dirs_exist_ok=True)
    finally:
        # Staging is a generated, tightly scoped directory under this model's
        # tool folder. Never remove target or any project-level directory here.
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)

def _download_file(url: str, target: Path, progress: Progress) -> Path:
    """Resume a portable runtime download after interrupted connections."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    existing = partial.stat().st_size if partial.is_file() else 0
    request = urllib.request.Request(url)
    if existing:
        request.add_header("Range", f"bytes={existing}-")
    progress(f"กำลังดาวน์โหลด {target.name}" + (" ต่อจากไฟล์เดิม" if existing else ""))
    with urllib.request.urlopen(request, timeout=600) as response:
        append = existing > 0 and getattr(response, "status", 200) == 206
        mode = "ab" if append else "wb"
        downloaded = existing if append else 0
        total = int(response.headers.get("Content-Length") or 0) + downloaded
        with partial.open(mode) as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if total:
                    progress(f"ดาวน์โหลด {target.name} {int(downloaded * 100 / total)}%")
    partial.replace(target)
    return target

def _pip(python: Path, args: list[str], cwd: Path, progress: Progress) -> None:
    _run(
        [
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            "--retries", "5", "--timeout", "120", "--no-input", *args,
        ],
        cwd,
        progress,
    )


def _ensure_pip(python: Path, tool: Path, progress: Progress) -> None:
    """Make isolated Python usable on fresh PCs without asking for pip setup."""
    try:
        subprocess.run(
            [str(python), "-m", "pip", "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    except Exception:
        pass

    progress("Python แยกยังไม่มี pip — กำลังกู้คืนอัตโนมัติ")
    try:
        _run([str(python), "-m", "ensurepip", "--upgrade"], tool, progress)
    except Exception as ensure_error:
        progress(f"ensurepip ใช้ไม่ได้: {ensure_error}")
        get_pip = _download_file(GET_PIP_URL, tool / "downloads" / "get-pip.py", progress)
        _run([str(python), str(get_pip), "--disable-pip-version-check"], tool, progress)

    try:
        subprocess.run(
            [str(python), "-m", "pip", "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        raise RuntimeError(f"กู้คืน pip ไม่สำเร็จ: {exc}") from exc


def _verify_triposplat_runtime(tool: Path, python: Path, progress: Progress) -> None:
    """Fail with import details before model download starts."""
    source = tool / "source"
    script = (
        "import importlib,sys;"
        f"sys.path.insert(0,{str(source)!r});"
        "[importlib.import_module(name) for name in "
        "('torch','torchvision','numpy','safetensors','PIL','tqdm','huggingface_hub','trimesh','pygltflib')];"
        "importlib.import_module('triposplat');"
        "print('TripoSplat runtime import OK')"
    )
    _run([str(python), "-c", script], tool, progress)

def _bootstrap_triposplat(progress: Progress) -> None:
    tool = TOOLS_ROOT / "triposplat"
    source = tool / "source"
    comfy = tool / "comfy_source"
    python = tool / ".venv" / "Scripts" / "python.exe"
    if not (source / "triposplat.py").is_file() or not (source / "model.py").is_file():
        _download_repo_zip(TRIPOSPLAT_ZIP, source, progress)
    if not (comfy / "comfy_extras" / "nodes_gaussian_splat.py").is_file():
        _download_repo_zip(COMFYUI_ZIP, comfy, progress)
    converter = PROJECT_ROOT / "snapgen_modules" / "snapgen_splat_to_glb.py"
    if not converter.is_file():
        raise RuntimeError("โปรแกรมขาดตัวแปลง TripoSplat เป็น GLB — อัปเดต SnapGen แล้วลองใหม่")
    shutil.copy2(converter, source / converter.name)
    if not python.is_file():
        progress("กำลังสร้าง Python แยกสำหรับ TripoSplat")
        _run([sys.executable, "-m", "venv", str(tool / ".venv")], PROJECT_ROOT, progress)
    marker = tool / ".snapgen_runtime_ready"
    # Marker can be missing after an interrupted install while packages are
    # already usable. Verify first; this avoids needless pip/network work.
    try:
        _verify_triposplat_runtime(tool, python, progress)
        if not marker.is_file():
            marker.write_text("ready\n", encoding="utf-8")
        return
    except Exception as runtime_error:
        marker.unlink(missing_ok=True)
        progress(f"Runtime TripoSplat ยังไม่ครบ: {runtime_error}")

    _ensure_pip(python, tool, progress)
    if not marker.is_file():
        progress("กำลังติดตั้งไลบรารี TripoSplat ครั้งแรก")
        _pip(python, ["torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu128"], tool, progress)
        requirements = comfy / "requirements.txt"
        if requirements.is_file():
            _pip(python, ["-r", str(requirements)], tool, progress)
        _pip(
            python,
            ["numpy", "safetensors", "pillow", "tqdm", "huggingface_hub", "transformers==4.47.1", "timm", "scipy", "trimesh", "pygltflib"],
            tool,
            progress,
        )
        _verify_triposplat_runtime(tool, python, progress)
        marker.write_text("ready\n", encoding="utf-8")

def _portable_python311(tool: Path, progress: Progress) -> Path:
    python_dir = tool / "python"
    python = python_dir / "python.exe"
    if not python.is_file():
        archive = _download_file(PYTHON311_ZIP, tool / "downloads" / "python311.zip", progress)
        python_dir.mkdir(parents=True, exist_ok=True)
        progress("กำลังแตก Python 3.11 สำหรับ TRELLIS.2")
        with zipfile.ZipFile(archive) as package:
            package.extractall(python_dir)
    pth = python_dir / "python311._pth"
    if pth.is_file():
        text = pth.read_text(encoding="utf-8")
        if "#import site" in text:
            pth.write_text(text.replace("#import site", "import site"), encoding="utf-8")
    try:
        subprocess.run([str(python), "-m", "pip", "--version"], check=True, capture_output=True)
    except Exception:
        get_pip = _download_file(GET_PIP_URL, tool / "downloads" / "get-pip.py", progress)
        _run([str(python), str(get_pip)], tool, progress)
    return python

def _trellis_python(tool: Path) -> Path:
    candidates = (
        tool / "code" / "venv" / "Scripts" / "python.exe",
        tool / "python" / "python.exe",
    )
    return next((path for path in candidates if path.is_file()), candidates[-1])

def _bootstrap_trellis2(progress: Progress) -> None:
    tool = TOOLS_ROOT / "trellis2"
    code = tool / "code"
    if not (code / "install.py").is_file() or not (code / "trellis2" / "__init__.py").is_file():
        # The pinned Windows archive includes o-voxel and Eigen content. A
        # normal GitHub source ZIP does not need Git or submodule commands.
        _download_repo_zip(TRELLIS2_ZIP, code, progress, subdir="code")
    python = _portable_python311(tool, progress)
    marker = tool / ".snapgen_runtime_ready"
    if not marker.is_file():
        installer = code / "install.py"
        if not installer.is_file():
            raise RuntimeError("ดาวน์โหลดตัวติดตั้ง TRELLIS.2 ไม่ครบ")
        progress("กำลังติดตั้งไลบรารี Windows ของ TRELLIS.2 ครั้งแรก")
        _run([str(python), "-B", str(installer)], code, progress)
        marker.write_text("ready\n", encoding="utf-8")


def _require_huggingface() -> None:
    try:
        socket.getaddrinfo("huggingface.co", 443)
    except OSError as exc:
        raise RuntimeError("เชื่อม Hugging Face ไม่ได้ — ตรวจอินเทอร์เน็ตหรือ DNS แล้วกดติดตั้งใหม่") from exc

def _download_hf_file(
    python: Path,
    repo_id: str,
    filename: str,
    cwd: Path,
    progress: Progress,
    *,
    local_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> None:
    """Download one file per process; Hugging Face resumes its .incomplete file."""
    destination = local_dir or cache_dir
    assert destination is not None
    destination.mkdir(parents=True, exist_ok=True)
    kwargs = (
        f"local_dir={str(local_dir)!r}"
        if local_dir is not None
        else f"cache_dir={str(cache_dir)!r}"
    )
    script = (
        "from huggingface_hub import hf_hub_download;"
        f"print(hf_hub_download(repo_id={repo_id!r},filename={filename!r},{kwargs}))"
    )
    last_error = None
    for attempt in range(1, 11):
        progress(f"ดาวน์โหลด {filename} — ครั้งที่ {attempt}/10 (ไฟล์เดิมจะโหลดต่อ)")
        try:
            _run([str(python), "-c", script], cwd, progress)
            return
        except Exception as exc:
            last_error = exc
            if attempt < 10:
                time.sleep(min(attempt * 2, 15))
    raise RuntimeError(f"ดาวน์โหลด {filename} ไม่สำเร็จหลังลองต่อไฟล์ 10 ครั้ง: {last_error}")


def _copy_if_available(source: Path, target: Path, progress: Progress) -> bool:
    if target.is_file():
        return True
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    progress(f"พบสำเนาในเครื่อง — กำลังคัดลอก {target.name}")
    shutil.copy2(source, target)
    return True


def _restore_triposplat_from_local(progress: Progress) -> None:
    tool = TOOLS_ROOT / "triposplat"
    pairs = (
        (COMFY_MODELS / "diffusion_models" / "triposplat_fp16.safetensors", tool / "ckpts" / "diffusion_models" / "triposplat_fp16.safetensors"),
        (COMFY_MODELS / "vae" / "triposplat_vae_decoder_fp16.safetensors", tool / "ckpts" / "vae" / "triposplat_vae_decoder_fp16.safetensors"),
        (COMFY_MODELS / "clip_vision" / "dino_v3_vit_h.safetensors", tool / "ckpts" / "clip_vision" / "dino_v3_vit_h.safetensors"),
        (COMFY_MODELS / "vae" / "flux2-vae.safetensors", tool / "ckpts" / "vae" / "flux2-vae.safetensors"),
        (COMFY_MODELS / "background_removal" / "birefnet.safetensors", tool / "ckpts" / "background_removal" / "birefnet.safetensors"),
    )
    for source, target in pairs:
        _copy_if_available(source, target, progress)
    rmbg_target = tool / "bg_models" / "RMBG-2.0" / "model.safetensors"
    for source in (
        Path.home() / "Downloads" / "model.safetensors",
        COMFY_MODELS / "RMBG" / "RMBG-2.0" / "model.safetensors",
    ):
        if _copy_if_available(source, rmbg_target, progress):
            break


def install_model(name: str, progress: Progress | None = None) -> None:
    report = progress or (lambda _text: None)
    existing = model_info(name)
    if existing["installed"]:
        report(f"{existing['name']} ติดตั้งครบแล้ว พร้อมใช้งาน")
        return
    tool = _tool(name)
    key = str(name).strip().lower()
    if key == "triposplat":
        _bootstrap_triposplat(report)
        python = tool / ".venv" / "Scripts" / "python.exe"
        _restore_triposplat_from_local(report)
        missing = [relative for relative in _TRIPOSPLAT_HF_FILES if not (tool / relative).is_file()]
        if missing:
            _require_huggingface()
            report(f"TripoSplat ขาด {len(missing)} ไฟล์ — ดาวน์โหลดทีละไฟล์แบบต่อได้")
            for relative in missing:
                _download_hf_file(
                    python, "VAST-AI/TripoSplat", relative.removeprefix("ckpts/"),
                    tool, report, local_dir=tool / "ckpts",
                )
        downloader = PROJECT_ROOT / "snapgen_modules" / "snapgen_download_rmbg2.py"
        target = tool / "bg_models" / "RMBG-2.0" / "model.safetensors"
        # Always run verifier. It exits immediately for a valid model and
        # resumes/replaces missing, partial, or corrupt files automatically.
        _run([sys.executable, "-B", str(downloader), str(target)], PROJECT_ROOT, report)
    else:
        _bootstrap_trellis2(report)
        python = _trellis_python(tool)
        installer = tool / "code" / "install.py"
        _require_huggingface()
        report("กำลังตรวจโมเดลช่วยของ TRELLIS.2")
        _run([str(python), "-c", "import install;install.download_models()"], installer.parent, report)
        hub = tool / "code" / "models" / "hub"
        info = model_info(name)
        if not info["installed"]:
            report("TRELLIS.2 ดาวน์โหลดทีละไฟล์แบบต่อได้ — ไฟล์ที่ครบแล้วจะไม่โหลดใหม่")
            for filename in _TRELLIS_HF_FILES:
                _download_hf_file(
                    python, "microsoft/TRELLIS.2-4B", filename,
                    installer.parent, report, cache_dir=hub,
                )
    if not model_info(name)["installed"]:
        raise RuntimeError("ดาวน์โหลดเสร็จแต่ไฟล์โมเดลยังไม่ครบ")
    report(f"{model_info(name)['name']} พร้อมใช้งาน")


def delete_model(name: str) -> bool:
    tool = _tool(name)
    # A model install includes its own source, Python runtime, and CUDA packages.
    # Removing only checkpoint folders leaves tens of GB behind while the UI says
    # the model is not installed, so remove the complete per-model tool directory.
    targets = [tool]
    removed = False
    for target in targets:
        resolved = target.resolve()
        if TOOLS_ROOT.resolve() not in resolved.parents:
            raise RuntimeError(f"ปฏิเสธตำแหน่งลบที่ไม่ปลอดภัย: {resolved}")
        if target.is_dir():
            shutil.rmtree(target)
            removed = True
        elif target.is_file():
            target.unlink()
            removed = True
    return removed


if __name__ == "__main__":
    names = [item["name"] for item in list_models()]
    assert names == ["TripoSplat", "TRELLIS.2"]
    print(list_models())
