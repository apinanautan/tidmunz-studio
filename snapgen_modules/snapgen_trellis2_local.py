"""Run local TRELLIS.2-stableprojectorz and return GLB + FBX to SnapGen."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


API = "http://127.0.0.1:7960"


def progress(value: int, text: str) -> None:
    print(f"PROGRESS|{value}|{text}", flush=True)


def api_json(path: str, *, timeout: int = 5) -> dict:
    with urllib.request.urlopen(API + path, timeout=timeout) as response:
        return json.load(response)


def _server_log_tail(process: subprocess.Popen | None) -> str:
    path = getattr(process, "_snapgen_log_path", None) if process else None
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-6000:] if path else ""
    except Exception:
        return ""

def wait_ready(process: subprocess.Popen | None, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if api_json("/ping").get("status") == "running":
                return
        except Exception:
            pass
        if process and process.poll() is not None:
            raise RuntimeError(
                f"TRELLIS.2 ปิดระหว่างโหลดโมเดล (code {process.returncode})\n{_server_log_tail(process)}"
            )
        time.sleep(2)
    raise RuntimeError("TRELLIS.2 โหลดโมเดลเกิน 5 นาที")


def start_api(tool_dir: Path) -> tuple[subprocess.Popen | None, bool]:
    try:
        if api_json("/ping", timeout=2).get("status") == "running":
            return None, False
    except Exception:
        pass
    code_dir = tool_dir / "code"
    python = code_dir / "venv" / "Scripts" / "python.exe"
    server = code_dir / "api_spz" / "main_api.py"
    if not python.is_file() or not server.is_file():
        raise RuntimeError(f"TRELLIS.2 ยังติดตั้งไม่ครบ: {tool_dir}")
    env = os.environ.copy()
    env["HF_HOME"] = str(code_dir / "models")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_path = tool_dir / "server_runtime.log"
    log_stream = log_path.open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        [str(python), "-B", str(server), "--host", "127.0.0.1", "--port", "7960"],
        cwd=str(code_dir), env=env, stdin=subprocess.DEVNULL,
        stdout=log_stream, stderr=subprocess.STDOUT, creationflags=flags,
    )
    log_stream.close()
    process._snapgen_log_path = str(log_path)
    return process, True


def multipart(image: Path) -> tuple[bytes, str]:
    boundary = "----SnapGen" + uuid.uuid4().hex
    mime = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{image.name}"\r\n'.encode("utf-8")
    )
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
    body.extend(image.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), boundary


def generate(image: Path, resolution: int = 1024) -> None:
    body, boundary = multipart(image)
    query = (
        # Match the upstream web app's quality pipeline instead of the old
        # low-memory 512 path. Keep 300k faces, but export a 4K texture.
        "/generate_no_preview?seed=1234&guidance_scale=7.5&num_inference_steps=12"
        f"&resolution={resolution}&mesh_simplify=300&apply_texture=true&texture_size=4096"
        "&tex_rescale_t=3.0&tex_guidance_strength=1.0&output_format=glb"
    )
    request = urllib.request.Request(
        API + query, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    outcome: dict = {}

    def submit() -> None:
        try:
            with urllib.request.urlopen(request, timeout=3600) as response:
                outcome["result"] = json.load(response)
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=submit, daemon=True)
    worker.start()
    last = None
    while worker.is_alive():
        try:
            status = api_json("/status")
            value = max(20, int(status.get("progress", 20)))
            text = status.get("message") or "กำลังสร้าง 3D"
            if (value, text) != last:
                progress(value, text)
                last = (value, text)
        except Exception:
            pass
        worker.join(2)
    if "error" in outcome:
        exc = outcome["error"]
        if isinstance(exc, urllib.error.HTTPError):
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"TRELLIS.2 ตอบกลับ {exc.code}: {detail[:500]}") from exc
        raise RuntimeError(f"เชื่อมต่อ TRELLIS.2 ไม่สำเร็จ: {exc}") from exc
    result = outcome.get("result", {})
    if result.get("status") != "COMPLETE":
        raise RuntimeError(result.get("message") or "TRELLIS.2 สร้างงานไม่สำเร็จ")


def find_blender() -> Path | None:
    found = shutil.which("blender")
    if found:
        return Path(found)
    candidate = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe")
    return candidate if candidate.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tool-dir", required=True, type=Path)
    parser.add_argument("--asset-name", default="")
    parser.add_argument("--resolution", type=int, choices=(512, 768, 1024), default=1024)
    args = parser.parse_args()
    image = args.input.resolve()
    output = args.output.resolve()
    asset_name = args.asset_name.strip() or "prop"
    if not image.is_file():
        raise RuntimeError(f"ไม่พบรูป: {image}")
    output.mkdir(parents=True, exist_ok=True)
    process = None
    owned = False
    try:
        progress(5, "กำลังเปิด TRELLIS.2")
        process, owned = start_api(args.tool_dir.resolve())
        progress(10, "กำลังโหลดโมเดล TRELLIS.2")
        wait_ready(process)
        progress(20, "กำลังสร้าง Mesh และ Texture")
        generate(image, args.resolution)
        progress(88, "กำลังดาวน์โหลด GLB")
        with urllib.request.urlopen(API + "/download/model", timeout=300) as response:
            (output / f"{asset_name}.glb").write_bytes(response.read())
        if not (output / f"{asset_name}.glb").is_file():
            raise RuntimeError("ดาวน์โหลด GLB ไม่สำเร็จ")
        blender = find_blender()
        converter = Path(__file__).resolve().parent / "snapgen_blender_glb_to_fbx.py"
        if blender and converter.is_file():
            progress(94, "กำลังแปลง FBX สำหรับ iClone")
            result = subprocess.run(
                [str(blender), "--background", "--python", str(converter), "--",
                 str(output / f"{asset_name}.glb"), str(output / f"{asset_name}.fbx"), asset_name],
                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                print("WARNING: แปลง FBX ไม่สำเร็จ — ยังเก็บ GLB ไว้", flush=True)
        progress(100, "เสร็จแล้ว")
        print(f"RESULT|{output / f'{asset_name}.glb'}", flush=True)
        if (output / f"{asset_name}.fbx").is_file():
            print(f"RESULT|{output / f'{asset_name}.fbx'}", flush=True)
        return 0
    finally:
        if owned and process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(1)
