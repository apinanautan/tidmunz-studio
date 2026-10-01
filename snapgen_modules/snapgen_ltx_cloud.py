# -*- coding: utf-8 -*-
"""LTX-2.5 ComfyUI backend for local proof and a user-owned Vast.ai instance.

The module is intentionally isolated from Storyboard and the recovered core.
It owns only LTX Cloud configuration, Vast lifecycle, provisioning, ComfyUI API
transport, and the small Settings dialog used to control those pieces.
"""
from __future__ import annotations

import json
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path


MODEL_ID = "ltx-2.5-cloud"
MODEL_LABEL = "LTX-2.5 Fast HQ — เจนจริง ไม่เช่า Vast"
CONFIG_NAME = "ltx_cloud.json"
VAST_API = "https://console.vast.ai"
RENTAL_ENABLED = False
_LOCAL_MODEL_DOWNLOAD_LOCK = threading.Lock()
COMFY_ROOT = "/workspace/ComfyUI"
COMFY_REVISION = "86aedfd943d36d485e5ed3cb9d962f21f73d1741"
QUALITY_UPSCALER = "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"

HF_BASE = "https://huggingface.co/Lightricks/LTX-2.5/resolve/main"
MODEL_FILES = (
    ("diffusion_models", "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", f"{HF_BASE}/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", 21_504_034_224),
    ("text_encoders", "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", f"{HF_BASE}/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", 15_372_971_786),
    ("vae", "ltx-2.5-audio-vae-bf16.safetensors", f"{HF_BASE}/vae/ltx-2.5-audio-vae-bf16.safetensors", 364_866_540),
    ("vae", "ltx-2.5-video-vae-conv-bf16.safetensors", f"{HF_BASE}/vae/ltx-2.5-video-vae-conv-bf16.safetensors", 1_452_269_922),
)

CUSTOM_NODES = (
    ("ComfyUI-LTXVideo", "https://github.com/Lightricks/ComfyUI-LTXVideo.git", "ac4d99839020b983e956a8ab67ec38aec1b6e65a"),
    ("ComfyUI-GGUF", "https://github.com/city96/ComfyUI-GGUF.git", "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"),
    ("ComfyUI-KJNodes", "https://github.com/kijai/ComfyUI-KJNodes.git", "6ab7e8130e449ed2c0037589bcf84146ceb7fc9c"),
    ("ComfyUI-VideoHelperSuite", "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git", "4ee72c065db22c9d96c2427954dc69e7b908444b"),
)

REQUIRED_CLASSES = (
    "UNETLoader", "CLIPLoader", "CLIPTextEncode", "VAELoader",
    "LTXVConditioning", "LTXVPreprocess",
    "EmptyLTXVLatentVideo", "LTXVImgToVideoInplace", "ResizeImagesByLongerEdge",
    "LTXVEmptyLatentAudio", "LTXVConcatAVLatent", "LTXVSeparateAVLatent",
    "LatentUpscaleModelLoader", "LTXVLatentUpsampler",
    "ManualSigmas", "CFGGuider", "SamplerCustomAdvanced", "VAEDecodeTiled",
    "LTXVAudioVAEDecode", "CreateVideo", "SaveVideo",
)


def _config_path(base: Path) -> Path:
    return Path(base) / CONFIG_NAME


def defaults() -> dict:
    return {
        "vast_api_key": "",
        "runtime_mode": "local",
        "local_comfy_url": "http://127.0.0.1:8188",
        "instance_id": "",
        "ssh_host": "",
        "ssh_port": 0,
        "ssh_user": "root",
        "ssh_key": str(Path.home() / ".ssh" / "id_ed25519"),
        "gpu_name": "RTX 3090",
        "max_dph": 0.25,
        "min_reliability": 0.97,
        "disk": 80,
        "image": "vastai/comfy:v0.28.0-cuda-13.2-py312",
        "instance_label": "SnapGen LTX25 Cloud",
        "auto_stop_after_job": True,
        "setup_complete": False,
    }


def load_config(base: Path) -> dict:
    cfg = defaults()
    path = _config_path(base)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if isinstance(raw, dict):
            cfg.update(raw)
        # SnapGen already had one user-owned Vast account before this isolated
        # LTX settings file was added. Import that credential once so the user
        # is not forced to paste the same API key into every model backend.
        if not str(cfg.get("vast_api_key") or "").strip() and not cfg.get("shared_vast_key_migrated"):
            legacy_path = Path(base) / "minimax_cloud.json"
            legacy = json.loads(legacy_path.read_text(encoding="utf-8")) if legacy_path.is_file() else {}
            legacy_key = str(legacy.get("vast_api_key") or "").strip() if isinstance(legacy, dict) else ""
            if legacy_key:
                cfg["vast_api_key"] = legacy_key
                cfg["shared_vast_key_migrated"] = True
                save_config(base, cfg)
    except Exception as exc:
        raise RuntimeError(f"อ่านตั้งค่า LTX Cloud ไม่สำเร็จ: {exc}") from exc
    return cfg


def save_config(base: Path, cfg: dict) -> None:
    path = _config_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _huggingface_python() -> Path:
    candidates = (
        Path(r"C:\ComfyUI\.venv\Scripts\python.exe"),
        Path(sys.executable),
    )
    for candidate in candidates:
        if candidate.is_file():
            probe = subprocess.run(
                [str(candidate), "-c", "import huggingface_hub"],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if probe.returncode == 0:
                return candidate
    raise RuntimeError("ไม่พบ Python ที่ติดตั้ง huggingface_hub")


def huggingface_whoami() -> str:
    python = _huggingface_python()
    code = (
        "from huggingface_hub import whoami; "
        "data=whoami(); print(data.get('name') or data.get('fullname') or 'logged-in')"
    )
    proc = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode:
        raise RuntimeError("ยังไม่ได้เข้าสู่ระบบ Hugging Face")
    return (proc.stdout or "").strip().splitlines()[-1]


def huggingface_browser_has_ltx25_access() -> bool:
    """Read the visible gate state from SnapGen Browser without exporting cookies."""
    import urllib.request
    try:
        import websocket
    except Exception:
        return False
    for port in range(9223, 9244):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=0.3) as response:
                tabs = json.loads(response.read().decode("utf-8", "replace"))
            tab = next(
                item for item in tabs
                if item.get("type") == "page" and "huggingface.co/Lightricks/LTX-2.5" in str(item.get("url") or "")
            )
            ws = websocket.create_connection(
                str(tab.get("webSocketDebuggerUrl") or ""), timeout=5,
                origin=f"http://127.0.0.1:{port}",
            )
            try:
                ws.send(json.dumps({
                    "id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": "document.body ? document.body.innerText : ''", "returnByValue": True},
                }))
                while True:
                    message = json.loads(ws.recv())
                    if message.get("id") == 1:
                        text = str((((message.get("result") or {}).get("result") or {}).get("value") or ""))
                        return "you have been granted access to this model" in text.lower()
            finally:
                ws.close()
        except Exception:
            continue
    return False


def _snapgen_huggingface_cookies() -> list[dict]:
    """Read HF cookies from the SnapGen profile; no HF tab must stay open."""
    import urllib.request
    try:
        import websocket
    except Exception as exc:
        raise RuntimeError(f"SnapGen ไม่มี websocket-client: {exc}") from exc

    def read_once() -> list[dict]:
        for port in range(9223, 9244):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=0.3) as response:
                    tabs = json.loads(response.read().decode("utf-8", "replace"))
                pages = [item for item in tabs if item.get("type") == "page" and item.get("webSocketDebuggerUrl")]
                # Prefer an HF tab, but Network.getAllCookies returns cookies
                # for the entire browser profile even when called from any tab.
                pages.sort(key=lambda item: "huggingface.co" not in str(item.get("url") or ""))
                for tab in pages:
                    ws = websocket.create_connection(
                        str(tab.get("webSocketDebuggerUrl") or ""), timeout=5,
                        origin=f"http://127.0.0.1:{port}",
                    )
                    try:
                        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
                        while True:
                            message = json.loads(ws.recv())
                            if message.get("id") == 1:
                                cookies = ((message.get("result") or {}).get("cookies") or [])
                                result = [
                                    item for item in cookies
                                    if str(item.get("domain") or "").lstrip(".").endswith("huggingface.co")
                                ]
                                if result:
                                    return result
                                break
                    finally:
                        ws.close()
            except Exception:
                continue
        return []

    cookies = read_once()
    if cookies:
        return cookies
    _open_url_in_snapgen_chrome("https://huggingface.co/Lightricks/LTX-2.5")
    for _attempt in range(20):
        time.sleep(0.5)
        cookies = read_once()
        if cookies:
            return cookies
    raise RuntimeError(
        "SnapGen Browser ยังไม่ได้เข้าสู่ระบบ Hugging Face — "
        "โปรแกรมเปิดหน้า LTX-2.5 ให้แล้ว กรุณาล็อกอินหนึ่งครั้งแล้วกด Generate ใหม่"
    )


def _authorized_huggingface_download_url(url: str) -> str:
    """Resolve a short-lived gated LFS URL without storing browser credentials."""
    from urllib.parse import urljoin

    req = _requests()
    session = req.Session()
    for cookie in _snapgen_huggingface_cookies():
        session.cookies.set(
            str(cookie.get("name") or ""), str(cookie.get("value") or ""),
            domain=str(cookie.get("domain") or ""), path=str(cookie.get("path") or "/"),
        )
    response = session.get(str(url), allow_redirects=False, stream=True, timeout=60)
    try:
        if response.status_code not in (301, 302, 303, 307, 308):
            raise RuntimeError(f"Hugging Face ไม่คืนลิงก์ดาวน์โหลด: HTTP {response.status_code}")
        location = str(response.headers.get("Location") or "").strip()
        if not location:
            raise RuntimeError("Hugging Face ไม่คืน Location สำหรับดาวน์โหลด")
        resolved = urljoin(str(url), location)
    finally:
        response.close()
    probe = req.get(resolved, headers={"Range": "bytes=0-0"}, stream=True, timeout=60)
    try:
        if probe.status_code not in (200, 206):
            raise RuntimeError(f"ลิงก์ดาวน์โหลดชั่วคราวใช้ไม่ได้: HTTP {probe.status_code}")
    finally:
        probe.close()
    return resolved


def _open_url_in_snapgen_chrome(url: str) -> None:
    """Open a tab in SnapGen Browser, reusing its inspectable Chrome session."""
    import urllib.parse
    import urllib.request
    import webbrowser

    # SnapGen reserves this range for the dedicated, remote-debuggable browser.
    # Reusing that browser is important: merely launching the same profile does
    # not give the running SnapGen process a DevTools endpoint it can inspect.
    for port in range(9223, 9244):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=0.15,
            ) as response:
                if response.status != 200:
                    continue
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?"
                + urllib.parse.quote(str(url), safe=""),
                method="PUT",
            )
            with urllib.request.urlopen(request, timeout=3):
                return
        except Exception:
            continue

    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
    )
    chrome = next((path for path in candidates if path.is_file()), None)
    if chrome is not None:
        profile = Path.home() / "AppData" / "Local" / "TidMunStudio" / "SnapGenChromeProfile"
        profile.mkdir(parents=True, exist_ok=True)
        port = None
        for candidate_port in range(9223, 9244):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(("127.0.0.1", candidate_port))
                port = candidate_port
                break
            except OSError:
                continue
        if port is None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = int(sock.getsockname()[1])
        subprocess.Popen([
            str(chrome),
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            str(url),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    webbrowser.open(str(url))


def start_huggingface_login(*, root, parent, status_var) -> None:
    """Run HF's browser OAuth without exposing the password/token to SnapGen."""
    from tkinter import messagebox

    if huggingface_browser_has_ltx25_access():
        status_var.set("Hugging Face: ล็อกอินและได้รับสิทธิ์ LTX-2.5 แล้ว")
        _open_url_in_snapgen_chrome("https://huggingface.co/Lightricks/LTX-2.5")
        messagebox.showinfo("Hugging Face Login", "บัญชีใน SnapGen Browser ได้รับสิทธิ์ LTX-2.5 แล้ว ไม่ต้องล็อกอินซ้ำ", parent=parent)
        return

    def worker():
        process = None
        try:
            python = _huggingface_python()
            code = "from huggingface_hub import login; login(skip_if_logged_in=False)"
            process = subprocess.Popen(
                [str(python), "-u", "-c", code],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            verification_url = ""
            user_code = ""
            lines = []
            for raw_line in process.stdout or ():
                line = raw_line.strip()
                if line:
                    lines.append(line)
                match = re.search(r"https://(?:huggingface\.co|hf\.co)/\S+", line)
                if match and not verification_url:
                    verification_url = match.group(0)
                    _open_url_in_snapgen_chrome(verification_url)
                code_match = re.search(r"enter the code:\s*([A-Z0-9-]+)", line, re.I)
                if code_match and not user_code:
                    user_code = code_match.group(1)
                    root.after(0, lambda value=user_code: messagebox.showinfo(
                        "Hugging Face Login",
                        "เข้าสู่ระบบในหน้าที่เปิด แล้วอนุมัติรหัสนี้:\n\n" + value,
                        parent=parent,
                    ))
            return_code = process.wait(timeout=30)
            if return_code:
                raise RuntimeError(lines[-1] if lines else "Hugging Face login ไม่สำเร็จ")
            username = huggingface_whoami()
            _open_url_in_snapgen_chrome("https://huggingface.co/Lightricks/LTX-2.5")
            root.after(0, lambda: status_var.set(f"Hugging Face: เข้าสู่ระบบแล้ว ({username})"))
            root.after(0, lambda: messagebox.showinfo(
                "Hugging Face Login",
                "เข้าสู่ระบบสำเร็จแล้ว\nขั้นต่อไปให้เปิดหน้า LTX-2.5 และกด Agree and Access หนึ่งครั้ง",
                parent=parent,
            ))
        except Exception as exc:
            if process is not None and process.poll() is None:
                process.terminate()
            root.after(0, lambda detail=str(exc): status_var.set("Hugging Face: " + detail))
            root.after(0, lambda detail=str(exc): messagebox.showerror("Hugging Face Login", detail, parent=parent))

    status_var.set("Hugging Face: รออนุมัติในเบราว์เซอร์...")
    threading.Thread(target=worker, daemon=True).start()


def confirm_generate_rental(base: Path, parent) -> bool:
    """Compatibility hook: Generate is dry-run only while rental is disabled."""
    return True


def _requests():
    try:
        import requests
        return requests
    except Exception as exc:
        raise RuntimeError(f"LTX Cloud ต้องใช้ requests: {exc}") from exc


def _vast(cfg: dict, method: str, path: str, *, body=None, params=None, timeout=45):
    key = str(cfg.get("vast_api_key") or "").strip()
    if not key:
        raise RuntimeError("ยังไม่ได้กรอก Vast API Key ใน Settings > LTX Cloud / Vast")
    req = _requests()
    response = req.request(
        str(method).upper(), VAST_API + path, params=dict(params or {}),
        json=body, timeout=max(10, int(timeout)),
        headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
    )
    if not response.ok:
        raise RuntimeError(f"Vast API {response.status_code}: {response.text[:600]}")
    return response.json() if response.text.strip() else {}


def _instances(cfg: dict) -> list:
    data = _vast(cfg, "GET", "/api/v0/instances/", timeout=30)
    rows = data.get("instances") or [] if isinstance(data, dict) else []
    return list(rows.values()) if isinstance(rows, dict) else list(rows)


def _instance(cfg: dict, instance_id=None):
    wanted = str(instance_id or cfg.get("instance_id") or "").strip()
    if not wanted:
        return None
    try:
        data = _vast(cfg, "GET", f"/api/v0/instances/{wanted}/", timeout=30)
    except RuntimeError as exc:
        if "Vast API 404:" in str(exc):
            return None
        raise
    row = data.get("instances") if isinstance(data, dict) else None
    if isinstance(row, dict) and str(row.get("id") or wanted) == wanted:
        return row
    return None


def _ssh_endpoint(row: dict) -> tuple[str, int]:
    ports = row.get("ports") or {}
    p22 = (ports.get("22/tcp") or [{}])[0]
    host = str(row.get("ssh_host") or row.get("public_ipaddr") or "").strip()
    try:
        port = int(row.get("ssh_port") or p22.get("HostPort") or 0)
    except Exception:
        port = 0
    return host, port


def _public_key(cfg: dict) -> str:
    private = Path(str(cfg.get("ssh_key") or "").strip())
    public = Path(str(private) + ".pub")
    if not private.is_file():
        raise RuntimeError(f"ไม่พบ SSH private key: {private}")
    if not public.is_file():
        proc = subprocess.run(
            [r"C:\Windows\System32\OpenSSH\ssh-keygen.exe", "-y", "-f", str(private)],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode or not proc.stdout.strip():
            raise RuntimeError("สร้าง SSH public key จาก private key ไม่สำเร็จ")
        return proc.stdout.strip()
    return public.read_text(encoding="utf-8").strip()


def _ensure_account_ssh_key(cfg: dict, log_fn=None) -> None:
    key = str(cfg.get("vast_api_key") or "").strip()
    pub = _public_key(cfg)
    req = _requests()
    response = req.get(
        VAST_API + "/api/v0/ssh/",
        headers={"Authorization": f"Bearer {key}"}, timeout=15,
    )
    rows = response.json() if response.ok else []
    marker = pub.split()[-1] if len(pub.split()) >= 3 else pub[:48]
    if any(marker in str(row.get("key") or row.get("public_key") or row.get("ssh_key") or "") for row in rows if isinstance(row, dict)):
        return
    response = req.post(
        VAST_API + "/api/v0/ssh/",
        headers={"Authorization": f"Bearer {key}"}, json={"ssh_key": pub}, timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"เพิ่ม SSH key ใน Vast ไม่สำเร็จ: {response.status_code} {response.text[:300]}")
    if callable(log_fn):
        log_fn("LTX Cloud: ลงทะเบียน SSH key กับ Vast แล้ว")


def find_offer(cfg: dict) -> dict:
    gpu_name = str(cfg.get("gpu_name") or "RTX 3090").replace("_", " ").strip()
    max_dph = float(cfg.get("max_dph") or 0.25)
    disk = max(70, int(cfg.get("disk") or 80))
    query = {
        "gpu_name": {"in": [gpu_name]}, "num_gpus": {"gte": 1},
        "gpu_ram": {"gte": 24000}, "verified": {"eq": True},
        "rentable": {"eq": True}, "rented": {"eq": False},
        "direct_port_count": {"gte": 1},
        "reliability": {"gte": float(cfg.get("min_reliability") or 0.97)},
        "type": "on-demand", "limit": 100,
    }
    try:
        data = _vast(cfg, "POST", "/api/v0/bundles/", body=query, timeout=45)
    except Exception:
        data = _vast(cfg, "GET", "/api/v0/bundles/", params={"q": json.dumps(query)}, timeout=45)
    offers = data.get("offers") or [] if isinstance(data, dict) else []
    eligible = []
    for offer in offers:
        try:
            dph = float(offer.get("dph_total") or 999)
            reliability = float(offer.get("reliability2") or 0)
            space = float(offer.get("disk_space") or 0)
        except Exception:
            continue
        if dph <= max_dph and reliability >= float(cfg.get("min_reliability") or 0) and space >= disk:
            eligible.append(offer)
    if not eligible:
        raise RuntimeError(f"ไม่พบ {gpu_name} ราคาไม่เกิน ${max_dph:.3f}/ชม. ที่มีดิสก์อย่างน้อย {disk}GB")
    eligible.sort(key=lambda x: (float(x.get("dph_total") or 999), -float(x.get("reliability2") or 0)))
    return eligible[0]


def preflight(base: Path, *, check_offer=True) -> dict:
    cfg = load_config(base)
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    add("api_key", bool(str(cfg.get("vast_api_key") or "").strip()), "ตั้งค่าแล้ว" if cfg.get("vast_api_key") else "ยังไม่ได้ตั้ง")
    key_path = Path(str(cfg.get("ssh_key") or ""))
    add("ssh_key", key_path.is_file(), str(key_path))
    add("disk", int(cfg.get("disk") or 0) >= 70, f"{int(cfg.get('disk') or 0)} GB")
    for folder, name, url, minimum in MODEL_FILES:
        try:
            _authorized_huggingface_download_url(url)
            add("model:" + name, True, f"พร้อมให้ Vast ดาวน์โหลดตรง → {folder}")
        except Exception as exc:
            add("model:" + name, False, exc)
    if check_offer and checks[0]["ok"]:
        try:
            user = _vast(cfg, "GET", "/api/v0/users/current/", timeout=30)
            balance = user.get("credit") if isinstance(user, dict) else None
            add("vast_account", True, f"credit={balance}")
            offer = find_offer(cfg)
            add("vast_offer", True, f"{offer.get('gpu_name')} ${float(offer.get('dph_total') or 0):.3f}/ชม. (ยังไม่เช่า)")
        except Exception as exc:
            add("vast_offer", False, exc)
    return {"ok": all(row["ok"] for row in checks), "checks": checks}


def _save_endpoint(base: Path, cfg: dict, row: dict) -> dict:
    host, port = _ssh_endpoint(row)
    updated = dict(cfg)
    updated.update({
        "instance_id": str(row.get("id") or cfg.get("instance_id") or ""),
        "ssh_host": host, "ssh_port": port, "ssh_user": "root",
    })
    save_config(base, updated)
    return updated


def _wait_instance(base: Path, cfg: dict, instance_id: str, log_fn=None, timeout=420) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = _instance(cfg, instance_id)
        if row is None:
            raise RuntimeError(f"Vast ไม่พบ instance {instance_id}")
        status = str(row.get("actual_status") or row.get("status_msg") or "กำลังเตรียม")
        host, port = _ssh_endpoint(row)
        if status.lower() == "running" and host and port:
            return _save_endpoint(base, cfg, row)
        if callable(log_fn):
            log_fn(f"LTX Cloud: รอ instance {instance_id} | {status}")
        time.sleep(5)
    try:
        _vast(cfg, "PUT", f"/api/v0/instances/{instance_id}/", body={"state": "stopped"}, timeout=30)
    except Exception:
        pass
    raise RuntimeError("Vast instance ไม่พร้อมภายใน 7 นาที จึงสั่ง Stop เพื่อหยุดค่า GPU")


def _prepare_instance(base: Path, cfg: dict, log_fn=None, *, rent_authorized=False) -> dict:
    raise RuntimeError("ระบบเช่าและ Start Vast ถูกถอดออก: Generate ใช้ GPU เครื่องนี้เท่านั้น")


def _ssh_base(cfg: dict) -> list[str]:
    key = Path(str(cfg.get("ssh_key") or ""))
    host = str(cfg.get("ssh_host") or "").strip()
    port = int(cfg.get("ssh_port") or 0)
    if not key.is_file() or not host or not port:
        raise RuntimeError("ข้อมูล SSH ของ Vast ยังไม่พร้อม")
    return [
        r"C:\Windows\System32\OpenSSH\ssh.exe", "-i", str(key), "-p", str(port),
        "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
        f"{str(cfg.get('ssh_user') or 'root')}@{host}",
    ]


def _ssh(cfg: dict, command: str, *, timeout=3600, check=True):
    proc = subprocess.run(
        _ssh_base(cfg) + [command], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if check and proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "SSH command failed")[-1200:])
    return proc


def _wait_ssh(cfg: dict, log_fn=None, timeout=600) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            proc = _ssh(cfg, "echo SNAPGEN_SSH_OK", timeout=25, check=False)
            if proc.returncode == 0 and "SNAPGEN_SSH_OK" in proc.stdout:
                return
            last = (proc.stderr or proc.stdout or "").strip()
        except Exception as exc:
            last = str(exc)
        if callable(log_fn):
            log_fn("LTX Cloud: รอ SSH พร้อม...")
        time.sleep(5)
    raise RuntimeError("SSH ไม่พร้อมภายใน 10 นาที: " + last[-500:])


def _setup_cloud(base: Path, cfg: dict, log_fn=None) -> dict:
    _wait_ssh(cfg, log_fn=log_fn)
    if callable(log_fn):
        log_fn("LTX Cloud: ตรวจ custom nodes และโมเดล...")
    _ssh(
        cfg,
        f"set -e; git -C '{COMFY_ROOT}' fetch --depth 1 origin '{COMFY_REVISION}'; "
        f"git -C '{COMFY_ROOT}' checkout --detach '{COMFY_REVISION}'; "
        f"/venv/main/bin/pip install -q -r '{COMFY_ROOT}/requirements.txt'",
        timeout=1800,
    )
    for folder, repo, revision in CUSTOM_NODES:
        dest = f"{COMFY_ROOT}/custom_nodes/{folder}"
        cmd = (
            f"set -e; if [ ! -d '{dest}/.git' ]; then git clone --depth 1 '{repo}' '{dest}'; "
            f"fi; git -C '{dest}' fetch --depth 1 origin '{revision}'; "
            f"git -C '{dest}' checkout --detach '{revision}'; "
            f"if [ -f '{dest}/requirements.txt' ]; then /venv/main/bin/pip install -q -r '{dest}/requirements.txt'; fi"
        )
        if callable(log_fn):
            log_fn(f"LTX Cloud: เตรียม node {folder}")
        _ssh(cfg, cmd, timeout=1800)

    model_download_started = time.perf_counter()
    downloaded_models = 0
    for folder, filename, url, minimum in MODEL_FILES:
        dest = f"{COMFY_ROOT}/models/{folder}/{filename}"
        check = _ssh(cfg, f"test -s '{dest}' && stat -c %s '{dest}' || echo 0", timeout=30, check=False)
        try:
            current = int((check.stdout or "0").strip().splitlines()[-1])
        except Exception:
            current = 0
        if current >= int(minimum * 0.95):
            if callable(log_fn):
                log_fn(f"LTX Cloud: มี {filename} แล้ว")
            continue
        if callable(log_fn):
            log_fn(f"LTX Cloud: Vast เริ่มดาวน์โหลด {filename} ({minimum / 1024**3:.1f} GB)")
        file_download_started = time.perf_counter()
        part = dest + ".part"
        download_url = shlex.quote(_authorized_huggingface_download_url(url))
        cmd = (
            f"set -e; mkdir -p \"$(dirname '{dest}')\"; "
            f"if command -v aria2c >/dev/null 2>&1; then aria2c -c -x8 -s8 --summary-interval=10 -o \"$(basename '{part}')\" -d \"$(dirname '{part}')\" {download_url}; "
            f"else curl -L --fail --retry 5 -C - -o '{part}' {download_url}; fi; "
            f"test $(stat -c %s '{part}') -ge {int(minimum * 0.95)}; mv -f '{part}' '{dest}'"
        )
        _ssh(cfg, cmd, timeout=14400)
        downloaded_models += 1
        if callable(log_fn):
            elapsed = time.perf_counter() - file_download_started
            log_fn(f"LTX Cloud: Vast ดาวน์โหลด {filename} เสร็จใน {elapsed:.1f} วินาที")

    if callable(log_fn):
        if downloaded_models:
            elapsed = time.perf_counter() - model_download_started
            log_fn(f"LTX Cloud: เวลาโหลดโมเดลครั้งแรกบน Vast รวม {elapsed:.1f} วินาที")
        else:
            log_fn("LTX Cloud: โมเดลบน Vast มีครบแล้ว ไม่มีการดาวน์โหลดใหม่")

    # Start our own predictable localhost-only ComfyUI process. SnapGen reaches
    # it through SSH forwarding, so no Caddy token or public ComfyUI port is used.
    start = (
        "pkill -f 'python.*main.py' 2>/dev/null || true; sleep 3; "
        f"cd {COMFY_ROOT}; nohup /venv/main/bin/python main.py --listen 127.0.0.1 --port 8188 --lowvram "
        "> /tmp/snapgen_ltx_comfy.log 2>&1 < /dev/null &"
    )
    _ssh(cfg, start, timeout=40, check=False)
    cfg["setup_complete"] = True
    save_config(base, cfg)
    if callable(log_fn):
        log_fn("LTX Cloud: ติดตั้งและเริ่ม ComfyUI แล้ว")
    return cfg


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _open_tunnel(cfg: dict):
    local_port = _free_local_port()
    base = _ssh_base(cfg)
    target = base.pop()
    cmd = base + ["-N", "-L", f"{local_port}:127.0.0.1:8188", target]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc, f"http://127.0.0.1:{local_port}"


def _wait_comfy(url: str, tunnel, log_fn=None, timeout=360):
    req = _requests()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if tunnel.poll() is not None:
            detail = ""
            try:
                detail = tunnel.stderr.read().decode("utf-8", "replace")[-600:]
            except Exception:
                pass
            raise RuntimeError("SSH tunnel ปิดก่อน ComfyUI พร้อม: " + detail)
        try:
            response = req.get(url + "/system_stats", timeout=5)
            if response.ok:
                return
        except Exception:
            pass
        if callable(log_fn):
            log_fn("LTX Cloud: รอ ComfyUI API...")
        time.sleep(5)
    raise RuntimeError("ComfyUI ไม่ตอบภายใน 6 นาที")


def _ensure_local_comfy(url: str, log_fn=None, timeout=180) -> None:
    req = _requests()
    try:
        response = req.get(url.rstrip("/") + "/system_stats", timeout=3)
        if response.ok:
            return
    except Exception:
        pass
    root = Path(r"C:\ComfyUI")
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file() or not (root / "main.py").is_file():
        raise RuntimeError("ไม่พบ ComfyUI ที่ C:\\ComfyUI")
    if callable(log_fn):
        log_fn("LTX-2.5 Local: กำลังเปิด ComfyUI...")
    subprocess.Popen(
        [str(python), "main.py", "--listen", "127.0.0.1", "--port", "8188", "--lowvram"],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = req.get(url.rstrip("/") + "/system_stats", timeout=3)
            if response.ok:
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("เปิด ComfyUI Local ไม่สำเร็จภายใน 3 นาที")


def _verify_local_model_files() -> int:
    root = Path(r"C:\ComfyUI\models")
    missing = []
    total = 0
    for folder, filename, _url, expected in MODEL_FILES:
        path = root / folder / filename
        actual = path.stat().st_size if path.is_file() else 0
        if actual != expected:
            missing.append(f"{filename} ({actual}/{expected} bytes)")
        else:
            total += actual
    if missing:
        raise RuntimeError("โมเดล LTX-2.5 ในเครื่องไม่ครบ: " + ", ".join(missing))
    return total


def _local_model_inventory(root=Path(r"C:\ComfyUI\models")) -> dict:
    """Return exact-size cache state without contacting Hugging Face."""
    rows = []
    complete_bytes = 0
    resumable_bytes = 0
    for folder, filename, _url, expected in MODEL_FILES:
        final = Path(root) / folder / filename
        part = final.with_suffix(final.suffix + ".part")
        final_bytes = final.stat().st_size if final.is_file() else 0
        part_bytes = part.stat().st_size if part.is_file() else 0
        complete = final_bytes == expected
        resume = min(part_bytes, expected) if not complete else 0
        if complete:
            complete_bytes += expected
        else:
            resumable_bytes += resume
        rows.append({
            "folder": folder, "filename": filename, "expected": expected,
            "final": final, "part": part, "final_bytes": final_bytes,
            "part_bytes": part_bytes, "complete": complete, "resume_bytes": resume,
        })
    expected_total = sum(row["expected"] for row in rows)
    return {
        "rows": rows,
        "expected_total": expected_total,
        "complete_bytes": complete_bytes,
        "resumable_bytes": resumable_bytes,
        "available_bytes": complete_bytes + resumable_bytes,
        "complete": complete_bytes == expected_total,
    }


@contextmanager
def _local_model_download_guard(root: Path, log_fn=None):
    """Serialize model writes across Slots and separate SnapGen processes."""
    import msvcrt

    log = log_fn if callable(log_fn) else (lambda _message: None)
    lock_path = Path(root) / ".snapgen_ltx25_download.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    announced = False
    try:
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not announced:
                    log("LTX-2.5: มีอีก Slot/หน้าต่างกำลังดาวน์โหลด — รอใช้ไฟล์เดียวกัน ไม่โหลดซ้ำ")
                    announced = True
                time.sleep(2)
        yield
    finally:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def _ensure_local_model_files_unlocked(log_fn=None, progress_fn=None) -> int:
    """Download the four gated LTX-2.5 files to local ComfyUI with byte progress."""
    log = log_fn if callable(log_fn) else (lambda _message: None)
    progress = progress_fn if callable(progress_fn) else (lambda _percent: None)
    root = Path(r"C:\ComfyUI\models")
    inventory = _local_model_inventory(root)
    expected_total = inventory["expected_total"]
    progress(int(inventory["available_bytes"] * 100 / expected_total))
    if inventory["complete"]:
        log(f"LTX-2.5: ตรวจโมเดลครบ 4 ไฟล์ ({expected_total / 1024**3:.1f} GiB) — ไม่ดาวน์โหลดซ้ำ")
        progress(100)
        return expected_total
    missing_bytes = expected_total - inventory["available_bytes"]
    free = shutil.disk_usage(root).free
    if free < missing_bytes + 2 * 1024**3:
        raise RuntimeError(
            f"พื้นที่ C: ไม่พอสำหรับโมเดล LTX-2.5: ต้องเพิ่ม {missing_bytes / 1024**3:.1f} GiB "
            f"แต่เหลือ {free / 1024**3:.1f} GiB"
        )

    def downloaded_bytes() -> int:
        total = 0
        for model_folder, model_filename, _model_url, model_expected in MODEL_FILES:
            model_final = root / model_folder / model_filename
            model_part = model_final.with_suffix(model_final.suffix + ".part")
            if model_final.is_file() and model_final.stat().st_size == model_expected:
                total += model_expected
            elif model_part.is_file():
                total += min(model_expected, model_part.stat().st_size)
        return total

    overall_started = time.perf_counter()
    if inventory["resumable_bytes"]:
        log(f"LTX-2.5: พบไฟล์ดาวน์โหลดค้าง {inventory['resumable_bytes'] / 1024**3:.1f} GiB — โหลดต่อ ไม่เริ่มใหม่")
    req = _requests()
    for folder, filename, url, expected in MODEL_FILES:
        final = root / folder / filename
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.is_file() and final.stat().st_size == expected:
            log(f"LTX-2.5: มี {filename} ครบแล้ว")
            continue
        part = final.with_suffix(final.suffix + ".part")
        if part.is_file() and part.stat().st_size > expected:
            part.unlink()
        file_started = time.perf_counter()
        attempts = 0
        while (part.stat().st_size if part.is_file() else 0) < expected:
            attempts += 1
            if attempts > 8:
                raise RuntimeError(f"ดาวน์โหลด {filename} ไม่สำเร็จหลังลองต่อไฟล์ 8 ครั้ง")
            offset = part.stat().st_size if part.is_file() else 0
            signed_url = _authorized_huggingface_download_url(url)
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                response = req.get(signed_url, headers=headers, stream=True, timeout=(60, 300))
                if response.status_code not in ((206,) if offset else (200, 206)):
                    raise RuntimeError(f"HTTP {response.status_code}")
                append = offset > 0 and response.status_code == 206
                if offset and not append:
                    offset = 0
                mode = "ab" if append else "wb"
                last_report = 0.0
                with open(part, mode) as handle:
                    for chunk in response.iter_content(4 * 1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        current = handle.tell()
                        now = time.monotonic()
                        percent = int(downloaded_bytes() * 100 / expected_total)
                        progress(max(0, min(99, percent)))
                        if now - last_report >= 10:
                            log(f"LTX-2.5: ดาวน์โหลด {filename} {current * 100 / expected:.1f}%")
                            last_report = now
                response.close()
            except Exception as exc:
                log(f"LTX-2.5: การเชื่อมต่อสะดุด กำลังต่อ {filename} ({attempts}/8): {exc}")
                time.sleep(min(10, attempts * 2))
                continue
            if part.stat().st_size == expected:
                break
        actual = part.stat().st_size if part.is_file() else 0
        if actual != expected:
            raise RuntimeError(f"ขนาด {filename} ผิด: {actual}/{expected} bytes")
        part.replace(final)
        progress(min(100, int(downloaded_bytes() * 100 / expected_total)))
        log(f"LTX-2.5: ดาวน์โหลด {filename} เสร็จใน {time.perf_counter() - file_started:.1f} วินาที")
    progress(100)
    log(f"LTX-2.5: โหลดโมเดลจริงครบใน {time.perf_counter() - overall_started:.1f} วินาที")
    return _verify_local_model_files()


def _ensure_local_model_files(log_fn=None, progress_fn=None) -> int:
    root = Path(r"C:\ComfyUI\models")
    with _LOCAL_MODEL_DOWNLOAD_LOCK:
        with _local_model_download_guard(root, log_fn=log_fn):
            # Re-scan only after acquiring both locks: another Slot/process may
            # have completed the files while this caller was waiting.
            return _ensure_local_model_files_unlocked(log_fn=log_fn, progress_fn=progress_fn)


def _frames(duration) -> int:
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", str(duration or "4"))
    seconds = float(match.group(0)) if match else 4.0
    seconds = max(1.0, min(15.0, seconds))
    target = int(round(seconds * 25))
    return max(33, 1 + 8 * max(4, int(round((target - 1) / 8))))


def _generation_frames(duration) -> int:
    """Generate half the frames; RIFE restores the requested duration later."""
    full = _frames(duration)
    return max(33, 1 + 8 * max(4, int(round((full / 2 - 1) / 8))))


def _slot_workflow_dimensions(cfg_vars: dict, *, fallback=(640, 384)) -> tuple[int, int, str, str]:
    """Translate the Slot resolution/aspect preset into LTX's 32px latent grid."""
    def selected(key, default):
        value = cfg_vars.get(key) if isinstance(cfg_vars, dict) else None
        return str(value.get() if hasattr(value, "get") else default).strip()

    resolution = selected("resolution", "720p") or "720p"
    aspect = selected("aspect", "16:9") or "16:9"
    explicit = re.search(r"(\d+)\s*[x×]\s*(\d+)", resolution, re.I)
    if explicit:
        ideal_width, ideal_height = int(explicit.group(1)), int(explicit.group(2))
    else:
        match = re.search(r"\d+", resolution)
        short_edge = int(match.group(0)) if match else min(fallback)
        ratio_match = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", aspect)
        if ratio_match:
            ratio = float(ratio_match.group(1)) / max(0.001, float(ratio_match.group(2)))
            if ratio >= 1.0:
                ideal_height = short_edge
                ideal_width = int(round(short_edge * ratio))
            else:
                ideal_width = short_edge
                ideal_height = int(round(short_edge / ratio))
        else:
            ideal_width, ideal_height = fallback

    # EmptyLTXVLatentVideo uses spatial_downscale=32. Round down so a named
    # preset never silently exceeds the requested resolution/VRAM envelope.
    align = lambda value: max(64, 32 * max(2, int(value) // 32))
    return align(ideal_width), align(ideal_height), resolution, aspect


def build_workflow(image_name: str, prompt: str, duration, *, seed=None, width=384, height=224) -> dict:
    length = _generation_frames(duration)
    seed = int(seed if seed is not None else random.randint(1, 2**63 - 1))
    refine_seed = 1 if seed >= 2**63 - 1 else seed + 1
    width = max(64, 32 * int(round(int(width) / 32)))
    height = max(64, 32 * int(round(int(height) / 32)))
    stage_width = max(64, width // 2)
    stage_height = max(64, height // 2)
    positive = str(prompt or "").strip()
    if "[VISUAL]" not in positive:
        positive = "[VISUAL] " + positive + "\n[SOUNDS] Natural synchronized ambience matching the visible scene."
    w = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "type": "ltxv", "device": "cpu"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": positive}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "blurry, low quality, distorted face, flicker, jitter, artifacts, subtitles, watermark"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx-2.5-video-vae-conv-bf16.safetensors"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx-2.5-audio-vae-bf16.safetensors"}},
        "7": {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": 25.0}},
        "8": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "9": {"class_type": "ResizeImagesByLongerEdge", "inputs": {"images": ["8", 0], "longer_edge": max(width, height)}},
        "10": {"class_type": "LTXVPreprocess", "inputs": {"image": ["9", 0], "img_compression": 18}},
        "11": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": stage_width, "height": stage_height, "length": length, "batch_size": 1}},
        "12": {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["5", 0], "image": ["10", 0], "latent": ["11", 0], "strength": 0.7, "bypass": False}},
        "13": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"audio_vae": ["6", 0], "frames_number": length, "frame_rate": 25.0, "batch_size": 1}},
        "14": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["12", 0], "audio_latent": ["13", 0]}},
        "15": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["7", 0], "negative": ["7", 1], "cfg": 1.0}},
        "16": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "17": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
        "18": {"class_type": "ManualSigmas", "inputs": {"sigmas": "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}},
        "19": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["16", 0], "guider": ["15", 0], "sampler": ["17", 0], "sigmas": ["18", 0], "latent_image": ["14", 0]}},
        "20": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["19", 0]}},
        "25": {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": QUALITY_UPSCALER}},
        "26": {"class_type": "LTXVLatentUpsampler", "inputs": {"samples": ["20", 0], "upscale_model": ["25", 0], "vae": ["5", 0]}},
        "27": {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["5", 0], "image": ["9", 0], "latent": ["26", 0], "strength": 1.0, "bypass": False}},
        "28": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["27", 0], "audio_latent": ["20", 1]}},
        "29": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["7", 0], "negative": ["7", 1], "cfg": 1.0}},
        "30": {"class_type": "RandomNoise", "inputs": {"noise_seed": refine_seed}},
        "31": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
        "32": {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.85, 0.7250, 0.4219, 0.0"}},
        "33": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["30", 0], "guider": ["29", 0], "sampler": ["31", 0], "sigmas": ["32", 0], "latent_image": ["28", 0]}},
        "34": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["33", 0]}},
        "21": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["34", 0], "vae": ["5", 0], "tile_size": 512, "overlap": 64, "temporal_size": 128, "temporal_overlap": 32}},
        "22": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["34", 1], "audio_vae": ["6", 0]}},
        "23": {"class_type": "CreateVideo", "inputs": {"images": ["21", 0], "audio": ["22", 0], "fps": 25.0, "bit_depth": 8}},
        "24": {"class_type": "SaveVideo", "inputs": {"video": ["23", 0], "filename_prefix": "snapgen/ltx25_hq", "format": "mp4", "codec": "h264", "codec.encoding": "re-encode", "codec.encoding.crf": 16.0}},
    }
    return w


def _validate_workflow_definition(workflow: dict) -> None:
    if not isinstance(workflow, dict) or not workflow:
        raise RuntimeError("workflow ว่างหรือรูปแบบไม่ถูกต้อง")
    node_ids = set(workflow)
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or not str(node.get("class_type") or "").strip():
            raise RuntimeError(f"workflow node {node_id} ไม่มี class_type")
        for value in (node.get("inputs") or {}).values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                if value[0] not in node_ids:
                    raise RuntimeError(f"workflow node {node_id} อ้างถึง node ที่ไม่มี: {value[0]}")
    if not any(node.get("class_type") == "SaveVideo" for node in workflow.values()):
        raise RuntimeError("workflow ไม่มี SaveVideo output")


def dry_run(base: Path, img: str, prompt: str, cfg_vars: dict, log_fn=None, progress_fn=None) -> dict:
    """Validate the complete pre-rental plan without creating or using a Vast instance."""
    log = log_fn if callable(log_fn) else (lambda _message: None)
    progress = progress_fn if callable(progress_fn) else (lambda _percent: None)
    progress(0)
    cfg = load_config(base)
    log("LTX-2.5 Vast Dry Run: ระบบเช่าปิดอยู่ — ไม่มีการสร้าง instance และไม่มีค่าใช้จ่าย")
    image_path = Path(str(img or ""))
    if not image_path.is_file():
        raise RuntimeError("Dry Run ไม่พบรูปต้นฉบับ")
    if not str(prompt or "").strip():
        raise RuntimeError("Dry Run ไม่พบ prompt")
    if not str(cfg.get("vast_api_key") or "").strip():
        raise RuntimeError("Dry Run ไม่พบ Vast API Key")
    key_path = Path(str(cfg.get("ssh_key") or ""))
    if not key_path.is_file():
        raise RuntimeError(f"Dry Run ไม่พบ SSH private key: {key_path}")
    progress(12)

    account = _vast(cfg, "GET", "/api/v0/users/current/", timeout=30)
    if not isinstance(account, dict):
        raise RuntimeError("Dry Run ตรวจบัญชี Vast ไม่สำเร็จ")
    log("Dry Run: Vast API และ SSH key พร้อม (ตรวจแบบอ่านอย่างเดียว)")
    progress(25)
    offer = find_offer(cfg)
    log(f"Dry Run: พบ {offer.get('gpu_name')} ราคา ${float(offer.get('dph_total') or 0):.3f}/ชม. — ไม่ได้เช่า")
    progress(37)

    model_progress = (50, 62, 75, 87)
    for index, (folder, filename, url, _minimum) in enumerate(MODEL_FILES):
        _authorized_huggingface_download_url(url)
        log(f"Dry Run: Hugging Face อนุญาตไฟล์ {filename} → {folder}")
        progress(model_progress[index])

    duration_var = cfg_vars.get("duration") if isinstance(cfg_vars, dict) else None
    duration = duration_var.get() if hasattr(duration_var, "get") else "4"
    width, height, resolution, aspect = _slot_workflow_dimensions(cfg_vars)
    log(f"Dry Run: ใช้ค่าจาก Slot {resolution} | {aspect} → LTX workflow {width}x{height}")
    workflow = build_workflow(image_path.name, prompt, duration, width=width, height=height)
    _validate_workflow_definition(workflow)
    progress(100)
    result = {
        "ok": True,
        "nodes": len(workflow),
        "frames": _generation_frames(duration) * 2,
        "generated_frames": _generation_frames(duration),
        "width": width,
        "height": height,
        "resolution": resolution,
        "aspect": aspect,
        "models": len(MODEL_FILES),
        "custom_nodes": len(CUSTOM_NODES),
        "rental_created": False,
    }
    log(
        f"Dry Run ผ่าน: workflow {result['nodes']} nodes | {result['generated_frames']} → "
        f"RIFE {result['frames']} frames | "
        f"{result['width']}x{result['height']} | โมเดล {result['models']} ไฟล์ | ไม่ได้เช่า Vast"
    )
    return result


def _validate_remote(url: str) -> None:
    req = _requests()
    missing = []
    for class_name in REQUIRED_CLASSES:
        try:
            response = req.get(url + "/object_info/" + class_name, timeout=15)
            if not response.ok or class_name not in response.json():
                missing.append(class_name)
        except Exception:
            missing.append(class_name)
    if missing:
        raise RuntimeError("ComfyUI ขาด nodes: " + ", ".join(missing))


def _history_output(history: dict, prompt_id: str):
    item = history.get(prompt_id) or {}
    status = item.get("status") or {}
    messages = status.get("messages") or []
    for message in messages:
        if isinstance(message, list) and message and message[0] == "execution_error":
            detail = message[1] if len(message) > 1 and isinstance(message[1], dict) else {}
            raise RuntimeError("ComfyUI error: " + str(detail.get("exception_message") or detail)[:600])
    for output in (item.get("outputs") or {}).values():
        for key in ("gifs", "videos", "images"):
            for entry in output.get(key) or []:
                if str(entry.get("filename") or "").lower().endswith(".mp4"):
                    return entry
    return None


def _comfy_step_percent(message: dict, prompt_id: str) -> int | None:
    """Map both HQ sampler stages into a monotonic 68-95% inference band."""
    if not isinstance(message, dict) or message.get("type") != "progress":
        return None
    data = message.get("data") or {}
    if str(data.get("prompt_id") or "") != str(prompt_id or ""):
        return None
    try:
        value = max(0.0, float(data.get("value") or 0))
        maximum = float(data.get("max") or 0)
    except Exception:
        return None
    if maximum <= 0:
        return None
    ratio = min(1.0, value / maximum)
    node_id = str(data.get("node") or "")
    if node_id == "19":
        return 68 + int(round(16 * ratio))
    if node_id == "33":
        return 84 + int(round(11 * ratio))
    return max(68, min(95, 68 + int(round(27 * ratio))))


def generate(base: Path, base_root: Path, g: dict, i: int, img: str, prompt: str, cfg_vars: dict, *, rent_authorized=False, progress_fn=None) -> str:
    log_fn = lambda message: g["append_log"](i, message)
    progress = progress_fn if callable(progress_fn) else (lambda _percent: None)
    cloud = load_config(base)
    local_mode = str(cloud.get("runtime_mode") or "local").strip().lower() == "local"
    tunnel = None
    instance_ready = False
    overall_started = time.perf_counter()
    try:
        if local_mode:
            url = str(cloud.get("local_comfy_url") or "http://127.0.0.1:8188").rstrip("/")
            log_fn("LTX-2.5 Local Test: ใช้ GPU เครื่องนี้ ไม่ได้เช่า Vast")
            model_bytes = _ensure_local_model_files(
                log_fn=log_fn,
                progress_fn=lambda value: progress(int(max(0, min(100, value)) * 0.60)),
            )
            log_fn(f"LTX-2.5: ตรวจพบโมเดลครบ 4 ไฟล์ในเครื่อง ({model_bytes / 1024**3:.1f} GiB)")
            _ensure_local_comfy(url, log_fn=log_fn)
            progress(62)
        else:
            phase_started = time.perf_counter()
            cloud = _prepare_instance(base, cloud, log_fn=log_fn, rent_authorized=rent_authorized)
            instance_ready = True
            log_fn(f"LTX Cloud เวลาเตรียม/เช่า instance: {time.perf_counter() - phase_started:.1f} วินาที")
            phase_started = time.perf_counter()
            cloud = _setup_cloud(base, cloud, log_fn=log_fn)
            log_fn(f"LTX Cloud เวลาดาวน์โหลด/ตรวจโมเดลและ nodes: {time.perf_counter() - phase_started:.1f} วินาที")
            tunnel, url = _open_tunnel(cloud)
            _wait_comfy(url, tunnel, log_fn=log_fn)
        _validate_remote(url)
        progress(64)
        req = _requests()
        with open(img, "rb") as handle:
            response = req.post(url + "/upload/image", files={"image": (Path(img).name, handle)}, data={"overwrite": "true"}, timeout=120)
        if not response.ok:
            raise RuntimeError(f"อัปโหลดรูปเข้า ComfyUI ไม่สำเร็จ: {response.status_code} {response.text[:500]}")
        image_name = str(response.json().get("name") or Path(img).name)
        progress(66)
        duration_var = cfg_vars.get("duration") if isinstance(cfg_vars, dict) else None
        duration = duration_var.get() if hasattr(duration_var, "get") else "4"
        width, height = _slot_workflow_dimensions(cfg_vars)[:2]
        workflow = build_workflow(image_name, prompt, duration, width=width, height=height)
        generated_frames = _generation_frames(duration)
        log_fn(
            f"LTX-2.5 Fast HQ: ส่ง Two-Stage workflow | {generated_frames} frames → "
            f"RIFE {generated_frames * 2} frames | {width}x{height} | 8+3 steps"
        )
        inference_started = time.perf_counter()
        client_id = f"snapgen-{time.time_ns()}"
        progress_socket = None
        try:
            import websocket
            ws_url = re.sub(r"^http", "ws", url, count=1) + f"/ws?clientId={client_id}"
            progress_socket = websocket.create_connection(ws_url, timeout=5)
        except Exception as exc:
            log_fn(f"LTX-2.5: อ่าน step progress ไม่ได้ จะติดตามผลจาก history แทน: {exc}")
        response = req.post(url + "/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=120)
        if not response.ok:
            raise RuntimeError(f"ComfyUI ปฏิเสธ workflow: {response.status_code} {response.text[:1000]}")
        payload = response.json()
        if payload.get("node_errors"):
            raise RuntimeError("ComfyUI node errors: " + json.dumps(payload["node_errors"], ensure_ascii=False)[:1200])
        prompt_id = str(payload.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("ComfyUI ไม่คืน prompt_id")
        progress(68)
        started = time.time()
        output = None
        last_logged = -1
        try:
            while time.time() - started < 7200:
                if progress_socket is not None:
                    try:
                        message = json.loads(progress_socket.recv())
                        step_percent = _comfy_step_percent(message, prompt_id)
                        if step_percent is not None:
                            progress(step_percent)
                    except Exception:
                        pass
                else:
                    time.sleep(5)
                response = req.get(url + f"/history/{prompt_id}", timeout=30)
                if response.ok:
                    output = _history_output(response.json(), prompt_id)
                    if output:
                        break
                elapsed = int(time.time() - started)
                bucket = elapsed // 30
                if bucket > last_logged:
                    last_logged = bucket
                    log_fn(f"LTX-2.5: กำลังโหลดเข้า RAM/VRAM, encode หรือสร้างวิดีโอ {elapsed}s")
        finally:
            if progress_socket is not None:
                try:
                    progress_socket.close()
                except Exception:
                    pass
        if not output:
            raise RuntimeError("LTX Cloud ไม่เสร็จภายใน 2 ชั่วโมง")
        progress(96)
        log_fn(f"LTX-2.5 เวลาโหลดโมเดลเข้า VRAM + เจนวิดีโอ: {time.perf_counter() - inference_started:.1f} วินาที")
        download_started = time.perf_counter()
        params = {"filename": output["filename"], "subfolder": output.get("subfolder") or "", "type": output.get("type") or "output"}
        response = req.get(url + "/view", params=params, timeout=600, stream=True)
        response.raise_for_status()
        export_dir = Path(g.get("EXPORT_VIDEO") or (Path(base) / "exports" / "video"))
        export_dir.mkdir(parents=True, exist_ok=True)
        dst = export_dir / f"ltx25_{'local' if local_mode else 'cloud'}_{int(time.time())}.mp4"
        with open(dst, "wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    handle.write(chunk)
        if dst.stat().st_size < 1024:
            raise RuntimeError("ไฟล์ MP4 ที่ดาวน์โหลดกลับมามีขนาดผิดปกติ")
        log_fn(f"LTX-2.5 เวลาดาวน์โหลด MP4 กลับเครื่อง: {time.perf_counter() - download_started:.1f} วินาที")
        interpolate = g.get("make_ai_slow2x")
        if not callable(interpolate):
            raise RuntimeError("ไม่พบ RIFE สำหรับประกอบ LTX-2.5 Fast HQ ให้ครบเวลา")
        progress(97)
        rife_started = time.perf_counter()
        fast_output = dst.with_name(dst.stem + "_FastHQ" + dst.suffix)
        interpolated = Path(interpolate(
            str(dst), output_video=str(fast_output), factor=2,
            log=log_fn, mute=False,
        ))
        if interpolated.resolve() == dst.resolve() or not interpolated.is_file() or interpolated.stat().st_size < 1024:
            raise RuntimeError("RIFE เติมเฟรม LTX-2.5 Fast HQ ไม่สำเร็จ")
        try:
            dst.unlink()
        except OSError:
            pass
        progress(100)
        log_fn(f"LTX-2.5 Fast HQ เวลาเติมเฟรม RIFE: {time.perf_counter() - rife_started:.1f} วินาที")
        log_fn(f"LTX-2.5 เวลารวมทั้งงาน: {time.perf_counter() - overall_started:.1f} วินาที")
        log_fn(f"LTX-2.5 Fast HQ: บันทึกแล้ว {interpolated}")
        return str(interpolated)
    finally:
        if tunnel is not None:
            try:
                tunnel.terminate()
            except Exception:
                pass
        if (not local_mode) and instance_ready and bool(cloud.get("auto_stop_after_job", True)):
            try:
                _vast(cloud, "PUT", f"/api/v0/instances/{cloud['instance_id']}/", body={"state": "stopped"}, timeout=30)
                log_fn("LTX Cloud: Stop Vast instance แล้วเพื่อหยุดค่า GPU")
            except Exception as exc:
                log_fn("LTX Cloud: WARNING หยุด instance ไม่สำเร็จ กรุณาเข้า Vast ตรวจทันที: " + str(exc))


def instance_action(base: Path, action: str) -> str:
    cfg = load_config(base)
    instance_id = str(cfg.get("instance_id") or "").strip()
    if not instance_id:
        raise RuntimeError("ยังไม่มี instance_id")
    if action == "delete":
        _vast(cfg, "DELETE", f"/api/v0/instances/{instance_id}/", timeout=30)
        cfg.update({"instance_id": "", "ssh_host": "", "ssh_port": 0, "setup_complete": False})
        save_config(base, cfg)
        return "ลบ Vast instance แล้ว"
    state = {"stop": "stopped", "start": "running", "reboot": "restarting"}.get(action)
    if not state:
        raise RuntimeError("action ไม่ถูกต้อง")
    _vast(cfg, "PUT", f"/api/v0/instances/{instance_id}/", body={"state": state}, timeout=30)
    return f"ส่งคำสั่ง {action} แล้ว"


def inject_settings(settings_win, *, root, base: Path, ui_font="Tahoma") -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    try:
        if getattr(settings_win, "_snapgen_ltx_cloud_button", False):
            return
        settings_win._snapgen_ltx_cloud_button = True
    except Exception:
        return

    def open_dialog():
        cfg = load_config(base)
        win = tk.Toplevel(root)
        win.title("LTX-2.5 — Hugging Face / Vast.ai")
        win.geometry("780x600")
        win.transient(settings_win)
        vars_ = {
            "vast_api_key": tk.StringVar(value=str(cfg.get("vast_api_key") or "")),
            "ssh_key": tk.StringVar(value=str(cfg.get("ssh_key") or "")),
            "max_dph": tk.StringVar(value=str(cfg.get("max_dph") or 0.25)),
            "disk": tk.StringVar(value=str(cfg.get("disk") or 80)),
            "auto_stop_after_job": tk.BooleanVar(value=bool(cfg.get("auto_stop_after_job", True))),
            "use_local_gpu": tk.BooleanVar(value=True),
        }
        status = tk.StringVar(value="โหมดจริง: ดาวน์โหลดโมเดลและเจนด้วย GPU เครื่องนี้ (ไม่เช่า Vast)")
        if huggingface_browser_has_ltx25_access():
            hf_initial = "Hugging Face: ล็อกอินและได้รับสิทธิ์ LTX-2.5 แล้ว"
        else:
            try:
                hf_initial = "Hugging Face CLI: เข้าสู่ระบบแล้ว (" + huggingface_whoami() + ")"
            except Exception:
                hf_initial = "Hugging Face: เปิด SnapGen Browser เพื่อตรวจสถานะ"
        hf_status = tk.StringVar(value=hf_initial)
        box = tk.LabelFrame(win, text="LTX-2.5 Authentication / Vast.ai", padx=12, pady=10)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        rows = (("Vast API Key", "vast_api_key"), ("SSH Private Key", "ssh_key"), ("ราคาสูงสุด $/ชม.", "max_dph"), ("Disk GB", "disk"))
        for row, (label, key) in enumerate(rows):
            tk.Label(box, text=label, width=18, anchor="w", font=(ui_font, 9)).grid(row=row, column=0, sticky="w", pady=5)
            entry = tk.Entry(box, textvariable=vars_[key], show="*" if key == "vast_api_key" else "", font=(ui_font, 9))
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            if key == "ssh_key":
                tk.Button(box, text="เลือก", command=lambda: vars_["ssh_key"].set(filedialog.askopenfilename(parent=win) or vars_["ssh_key"].get())).grid(row=row, column=2, padx=(6, 0))
        box.grid_columnconfigure(1, weight=1)
        tk.Checkbutton(box, text="ใช้ GPU เครื่องนี้ — ดาวน์โหลดและเจนจริง (ไม่เช่า Vast)", variable=vars_["use_local_gpu"], state="disabled").grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 4))
        tk.Checkbutton(box, text="Stop Vast instance อัตโนมัติหลังงานเสร็จหรือเกิดข้อผิดพลาด", variable=vars_["auto_stop_after_job"]).grid(row=5, column=0, columnspan=3, sticky="w")

        def collect_save():
            current = load_config(base)
            current["vast_api_key"] = vars_["vast_api_key"].get().strip()
            current["ssh_key"] = vars_["ssh_key"].get().strip()
            try:
                current["max_dph"] = float(vars_["max_dph"].get())
                current["disk"] = max(70, int(float(vars_["disk"].get())))
            except Exception:
                raise RuntimeError("ราคา/Disk ต้องเป็นตัวเลข")
            current["auto_stop_after_job"] = bool(vars_["auto_stop_after_job"].get())
            current["runtime_mode"] = "local"
            save_config(base, current)
            return current

        def save_clicked():
            try:
                collect_save()
                status.set("บันทึกแล้ว")
            except Exception as exc:
                messagebox.showerror("LTX Cloud", str(exc), parent=win)

        def preflight_clicked():
            try:
                collect_save()
            except Exception as exc:
                messagebox.showerror("LTX Cloud", str(exc), parent=win)
                return
            status.set("กำลังตรวจ ไม่เช่าเครื่อง...")
            def worker():
                current = load_config(base)
                if str(current.get("runtime_mode") or "local") == "local":
                    checks = []
                    try:
                        _ensure_local_comfy(str(current.get("local_comfy_url") or "http://127.0.0.1:8188"))
                        checks.append({"name": "ComfyUI Local", "ok": True, "detail": "พร้อม"})
                    except Exception as exc:
                        checks.append({"name": "ComfyUI Local", "ok": False, "detail": str(exc)})
                    for folder, filename, _url, minimum in MODEL_FILES:
                        path = Path(r"C:\ComfyUI\models") / folder / filename
                        checks.append({"name": filename, "ok": path.is_file() and path.stat().st_size == minimum, "detail": str(path)})
                    report = {"checks": checks, "ok": all(row["ok"] for row in checks)}
                else:
                    report = preflight(base, check_offer=True)
                lines = [("✅ " if row["ok"] else "❌ ") + row["name"] + ": " + row["detail"] for row in report["checks"]]
                root.after(0, lambda: status.set("ผ่านทั้งหมด" if report["ok"] else "พบจุดที่ไม่ผ่าน"))
                root.after(0, lambda: messagebox.showinfo("LTX Cloud Preflight", "\n".join(lines), parent=win))
            threading.Thread(target=worker, daemon=True).start()

        def action_clicked(action):
            if action == "delete" and not messagebox.askokcancel("LTX Cloud", "ลบ Vast instance นี้หรือไม่?", parent=win):
                return
            def worker():
                try:
                    text = instance_action(base, action)
                except Exception as exc:
                    text = "ผิดพลาด: " + str(exc)
                root.after(0, lambda: status.set(text))
            threading.Thread(target=worker, daemon=True).start()

        buttons = tk.Frame(box)
        buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(16, 8))
        tk.Button(
            buttons, text="เข้าสู่ระบบ Hugging Face (SnapGen Chrome)",
            command=lambda: start_huggingface_login(root=root, parent=win, status_var=hf_status),
            bg="#F59E0B", fg="white",
        ).pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="บันทึก", command=save_clicked, bg="#2563EB", fg="white").pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="ตรวจพร้อม (ไม่เช่า)", command=preflight_clicked, bg="#16A34A", fg="white").pack(side="left", padx=6)
        if RENTAL_ENABLED:
            tk.Button(buttons, text="Start", command=lambda: action_clicked("start")).pack(side="left", padx=6)
            tk.Button(buttons, text="Stop", command=lambda: action_clicked("stop")).pack(side="left", padx=6)
            tk.Button(buttons, text="Delete", command=lambda: action_clicked("delete"), fg="#B91C1C").pack(side="right")
        tk.Label(box, textvariable=hf_status, anchor="w", justify="left", wraplength=710, fg="#B45309").grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        tk.Label(box, textvariable=status, anchor="w", justify="left", wraplength=710).grid(row=8, column=0, columnspan=3, sticky="ew", pady=(4, 0))

    button = tk.Button(
        settings_win, text="LTX-2.5 / Hugging Face / Vast", command=open_dialog,
        bg="#7C3AED", fg="white", relief="flat", padx=12, pady=6,
        font=(ui_font, 9, "bold"),
    )
    # Recovered Settings builds vary: some manage their direct children with
    # pack, others with grid.  Tk forbids mixing both in one parent.
    managers = {str(child.winfo_manager()) for child in settings_win.winfo_children() if child is not button}
    if "grid" in managers and "pack" not in managers:
        occupied = [int(child.grid_info().get("row", 0)) for child in settings_win.winfo_children() if child is not button and child.winfo_manager() == "grid"]
        button.grid(row=(max(occupied) + 1 if occupied else 0), column=0, sticky="w", padx=12, pady=(4, 8))
    else:
        button.pack(side="bottom", anchor="w", padx=12, pady=(4, 8))
