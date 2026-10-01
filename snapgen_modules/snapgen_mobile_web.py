"""Tailscale mobile view of the running SnapGen Image/Video pages."""
from __future__ import annotations
import base64
import io
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import urllib.parse
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
MAX_BODY = 18 * 1024 * 1024
ASSET_DIR = Path(__file__).resolve().parent / "mobile"
ACCESS_QUERY = "access"
ACCESS_COOKIE = "snapgen_mobile"


def build_access_url(base_url, access_token):
    """Attach this installation's access token to a machine-specific URL."""
    return base_url.rstrip("/") + "/?" + urllib.parse.urlencode({ACCESS_QUERY: access_token})


def _tailscale_exe():
    found = shutil.which("tailscale.exe") or shutil.which("tailscale")
    if found:
        return found
    return str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe")


def _tailscale_ipv4():
    exe = shutil.which("tailscale.exe") or str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe")
    try:
        result = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in result.stdout.splitlines():
            address = ipaddress.ip_address(line.strip())
            if address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10"):
                return str(address)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return ""


def _mobile_access_token(base_root):
    path = Path(base_root) / "snapgen_data" / "mobile_web_token.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def _run_tailscale(args, timeout=10):
    try:
        return subprocess.run(
            [_tailscale_exe(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _tailscale_dns_name():
    result = _run_tailscale(["status", "--json"], timeout=6)
    if not result or result.returncode != 0:
        return ""
    try:
        name = str((json.loads(result.stdout).get("Self") or {}).get("DNSName") or "").strip().rstrip(".")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    return name if name.endswith(".ts.net") else ""


def _enable_funnel(port):
    result = _run_tailscale(["funnel", "--bg", "--yes", str(int(port))], timeout=15)
    if not result or result.returncode != 0:
        lines = [] if not result else (result.stderr or result.stdout or "").strip().splitlines()
        return "", (lines[-1][:240] if lines else "Tailscale Funnel ยังไม่พร้อม")
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    match = re.search(r"https://[A-Za-z0-9.-]+\.ts\.net(?::(?:443|8443|10000))?/?", text)
    if match:
        return match.group(0).rstrip("/") + "/", ""
    dns_name = _tailscale_dns_name()
    if dns_name:
        return f"https://{dns_name}/", ""
    return "", "เปิด Funnel แล้ว แต่ยังอ่าน Public URL ไม่ได้"


def make_server(host, port, controller, access_token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SnapGenMobile/2"

        def log_message(self, *_args):
            pass

        def reply(self, status, raw, mime="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            if getattr(self, "set_auth_cookie", False):
                cookie = f"{ACCESS_COOKIE}={access_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
                forwarded_https = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                public_host = self.headers.get("Host", "").split(":", 1)[0].endswith(".ts.net")
                if forwarded_https or public_host:
                    cookie += "; Secure"
                self.send_header("Set-Cookie", cookie)
                self.set_auth_cookie = False
            self.end_headers()
            self.wfile.write(raw)

        def json_reply(self, status, data):
            self.reply(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def authorized(self, url):
            query = urllib.parse.parse_qs(url.query)
            supplied = (query.get(ACCESS_QUERY) or [""])[0]
            if supplied and secrets.compare_digest(supplied, access_token):
                self.set_auth_cookie = True
                return True
            # Safari can reject the cookie after opening a shared link. Keep the
            # access token in the top-page URL and authorize its same-origin
            # assets/API requests from that exact referring page as a fallback.
            referer = urllib.parse.urlsplit(self.headers.get("Referer", ""))
            referer_token = (urllib.parse.parse_qs(referer.query).get(ACCESS_QUERY) or [""])[0]
            if (referer.netloc == self.headers.get("Host", "") and referer_token
                    and secrets.compare_digest(referer_token, access_token)):
                return True
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except Exception:
                pass
            current = cookies.get(ACCESS_COOKIE)
            if current and secrets.compare_digest(current.value, access_token):
                return True
            self.reply(
                401,
                "ลิงก์นี้ไม่มีสิทธิ์เข้า SnapGen กรุณาใช้ลิงก์มือถือที่โปรแกรมสร้างให้".encode("utf-8"),
                "text/plain; charset=utf-8",
            )
            return False

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            try:
                if not self.authorized(url):
                    return
                assets = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
                if url.path in assets:
                    filename = assets[url.path]
                    mime = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[filename]
                    self.reply(200, (ASSET_DIR / filename).read_bytes(), mime + "; charset=utf-8")
                elif url.path == "/api/status":
                    self.json_reply(200, controller.status())
                elif url.path.startswith("/api/receipt/"):
                    self.json_reply(200, controller.receipt(url.path.rsplit("/", 1)[-1]))
                elif url.path.startswith("/media/"):
                    path = controller.resolve_media(url.path.rsplit("/", 1)[-1])
                    if "thumb" in urllib.parse.parse_qs(url.query) and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                        from PIL import Image, ImageOps
                        with Image.open(path) as source:
                            preview = ImageOps.exif_transpose(source).convert("RGB")
                            preview.thumbnail((480, 360))
                            out = io.BytesIO()
                            preview.save(out, "JPEG", quality=80)
                        self.reply(200, out.getvalue(), "image/jpeg")
                    else:
                        self.send_media(path, "download" in urllib.parse.parse_qs(url.query))
                else:
                    self.json_reply(404, {"error": "ไม่พบหน้านี้"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (OSError, ValueError):
                self.json_reply(404, {"error": "ไม่พบไฟล์นี้"})

        def send_media(self, path, download=False):
            size = path.stat().st_size
            start, end = 0, size - 1
            partial = self.headers.get("Range")
            if partial:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", partial)
                if not match or not any(match.groups()):
                    self.reply(416, b"")
                    return
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(end, int(right)) if right else end
                else:
                    start = max(0, size - int(right))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if download:
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(path.name))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = source.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_POST(self):
            try:
                url = urllib.parse.urlsplit(self.path)
                if not self.authorized(url):
                    return
                origin = self.headers.get("Origin")
                fetch_site = self.headers.get("Sec-Fetch-Site", "")
                if origin and fetch_site and fetch_site not in {"same-origin", "same-site"}:
                    self.json_reply(403, {"error": "ใช้ปุ่มจากหน้า SnapGen เท่านั้น"})
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self.json_reply(415, {"error": "รองรับ JSON เท่านั้น"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("ไฟล์ใหญ่เกินกำหนด (สูงสุด 12 MB ต่อรูป)")
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("ข้อมูลคำขอไม่ถูกต้อง")
                if url.path == "/api/computer-folders":
                    self.json_reply(200, {"ok": True, **controller.browse_computer_folders(data.get("path", ""))})
                elif url.path == "/api/upload":
                    raw = base64.b64decode(data.get("content", ""), validate=True)
                    if not raw or len(raw) > 12 * 1024 * 1024:
                        raise ValueError("รูปต้องมีขนาดไม่เกิน 12 MB")
                    from PIL import Image, ImageOps
                    with Image.open(io.BytesIO(raw)) as source:
                        if source.width * source.height > 40_000_000:
                            raise ValueError("ภาพใหญ่เกิน 40 ล้านพิกเซล")
                        source.load()
                        converted = ImageOps.exif_transpose(source).convert("RGB")
                    folder = controller.base / "snapgen_data" / "mobile_uploads"
                    folder.mkdir(parents=True, exist_ok=True)
                    name = re.sub(r"[^\wก-๙ .-]", "_", Path(str(data.get("name", "รูปมือถือ"))).stem)[:60] or "รูปมือถือ"
                    path = folder / (name + "_" + uuid.uuid4().hex[:8] + ".jpg")
                    converted.save(path, "JPEG", quality=95)
                    self.json_reply(200, {"media": controller.register_media(path)})
                elif url.path == "/api/story-upload":
                    raw = base64.b64decode(data.get("content", ""), validate=True)
                    story = controller.load_mobile_story(data.get("name", "บทมือถือ.docx"), raw)
                    self.json_reply(200, {"ok": True, "story": story})
                elif url.path == "/api/story-desktop":
                    self.json_reply(200, {"ok": True, "story": controller.use_desktop_story()})
                elif url.path == "/api/command":
                    event, result = controller.submit(data)
                    if event.wait(8):
                        self.json_reply(200 if result.get("ok") else 409, result)
                    else:
                        self.json_reply(202, {"pending": True, "id": data["id"], "message": "รอโปรแกรมตอบรับคำสั่ง ยังไม่ต้องกดซ้ำ"})
                else:
                    self.json_reply(404, {"error": "ไม่พบคำสั่ง"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.json_reply(400, {"error": str(exc)[:300]})
            except RuntimeError as exc:
                self.json_reply(503, {"error": str(exc)[:300]})
            except Exception:
                self.json_reply(500, {"error": "คำสั่งไม่สำเร็จ กรุณาลองใหม่"})

    server = ThreadingHTTPServer((host, int(port)), Handler)
    server.daemon_threads = True
    return server


def install(g, root, base_root, app_globals, log_fn=print):
    from snapgen_mobile_controller import MobileController
    controller = MobileController(g, root, base_root, app_globals)
    g["mobile_controller"] = controller
    controller.tick()
    stopped = threading.Event()
    servers = []
    access_token = _mobile_access_token(base_root)
    url_path = Path(base_root) / "snapgen_data" / "mobile_web_url.txt"
    state_lock = threading.Lock()
    link_state = {
        "url": "",
        "public": False,
        "status": "กำลังสร้างลิงก์ Tailscale ของเครื่องนี้...",
        "error": "",
    }

    def get_mobile_web_link():
        with state_lock:
            return dict(link_state)

    def set_link_state(**values):
        with state_lock:
            link_state.update(values)

    g["get_mobile_web_link"] = get_mobile_web_link

    def write_access_url(base_url, public):
        url = build_access_url(base_url, access_token)
        url_path.write_text(url, encoding="utf-8")
        set_link_state(
            url=url,
            public=bool(public),
            status=(
                "พร้อมใช้งานผ่านอินเทอร์เน็ต (Tailscale Funnel)"
                if public else
                "ใช้ได้เฉพาะอุปกรณ์ที่เชื่อม Tailscale เดียวกัน"
            ),
            error="",
        )

    def listen():
        try:
            server = make_server("0.0.0.0", PORT, controller, access_token)
            servers.append(server)
            log_fn(f"[SnapGen] Mobile Web รอ Public HTTPS ที่ port {PORT}")
            server.serve_forever()
        except OSError as exc:
            set_link_state(status="เปิดเว็บมือถือไม่ได้", error=str(exc)[:240])
            log_fn(f"[SnapGen] Mobile Web: {exc}")

    def publish():
        last_private = ""
        last_error = ""
        while not stopped.is_set():
            public_url, error = _enable_funnel(PORT)
            if public_url:
                write_access_url(public_url, True)
                log_fn(
                    "[SnapGen] Mobile Public HTTPS พร้อม: "
                    + public_url
                    + " (ลิงก์เข้าใช้งานอยู่ใน snapgen_data/mobile_web_url.txt)"
                )
                return
            ip = _tailscale_ipv4()
            if ip:
                private_url = f"http://{ip}:{PORT}/"
                write_access_url(private_url, False)
                if private_url != last_private:
                    log_fn("[SnapGen] Mobile ใช้ลิงก์ Tailnet ชั่วคราว; กำลังรอ Tailscale Funnel")
                    last_private = private_url
            if error and error != last_error:
                log_fn("[SnapGen] Tailscale Funnel: " + error)
                last_error = error
            if not ip:
                set_link_state(
                    url="",
                    public=False,
                    status="ยังเชื่อมต่อ Tailscale ไม่ได้",
                    error=str(error or "กรุณาเปิด Tailscale แล้วลองใหม่")[:240],
                )
            stopped.wait(15)

    def close(event):
        if event.widget is root:
            controller.closed = True
            stopped.set()
            for server in servers:
                threading.Thread(target=server.shutdown, daemon=True).start()

    root.bind("<Destroy>", close, add="+")
    threading.Thread(target=listen, name="SnapGenMobileWeb", daemon=True).start()
    threading.Thread(target=publish, name="SnapGenMobileFunnel", daemon=True).start()
    return link_state
