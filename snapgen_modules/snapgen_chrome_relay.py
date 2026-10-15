# -*- coding: utf-8 -*-
"""Job queue between the Bridge and the "Tidmunz ChatGPT" Chrome extension.

Runs inside Tidmunz Studio on 127.0.0.1 only. The Bridge posts a job to /job and
waits; the extension long-polls /next, runs the job in the user's own ChatGPT tab
and posts /result. Every call except /status and /pair needs the token. A folder
install reads it from config.js; the Web Store build gets it from /pair, which
answers Chrome extensions only. The Bridge reads it from chrome_relay.json.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORTS = (8766, 8767, 8768)
JOB_TIMEOUT = 15 * 60
CONNECTED_WITHIN = 45  # seconds since the extension last asked for work


def data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Tidmunz Studio" / "snapgen_data"


def program_chrome_profile() -> Path:
    """Tidmunz's own Chrome profile (also used for account capture): the extension and the ChatGPT
    login live here, apart from the user's everyday Chrome."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "TidMunStudio" / "SnapGenChromeProfile"


def extension_in_program_chrome() -> bool:
    """The program's Chrome has loaded our extension folder (Chrome records its path in the profile)."""
    marker = str(extension_dir()).replace("\\", "\\\\").lower()
    for name in ("Secure Preferences", "Preferences"):
        try:
            text = (program_chrome_profile() / "Default" / name).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "tidmunz chatgpt" in text or marker in text or "chrome_extension" in text and "tidmunz" in text:
            return True
    return False


def chrome_exe() -> str | None:
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            path = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if path.is_file():
                return str(path)
    return shutil.which("chrome")


def open_program_chrome(url: str = "https://chatgpt.com/") -> bool:
    """Open (or bring forward) Tidmunz's own Chrome window."""
    chrome = chrome_exe()
    if not chrome:
        return False
    profile = program_chrome_profile()
    profile.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.Popen([chrome, f"--user-data-dir={profile}", "--profile-directory=Default", "--new-window", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return True


def extension_dir() -> Path:
    return data_dir() / "chrome_extension"


class _Relay:
    def __init__(self) -> None:
        self.lock = threading.Condition()
        self.pending: list[dict] = []
        self.waiting: dict[str, dict] = {}  # job id -> {"event", "result"}
        self.last_seen = 0.0
        self.version = ""
        self.email = ""  # the ChatGPT account the Chrome profile is logged into
        self.images = ""  # pictures left today, as chatgpt.com reports it
        self.reset = ""
        self.token = ""
        self.port = 0

    # extension side
    def next_job(self, wait: float):
        self.last_seen = time.time()
        deadline = time.time() + wait
        with self.lock:
            while not self.pending:
                left = deadline - time.time()
                if left <= 0:
                    return None
                self.lock.wait(min(left, 5))
                self.last_seen = time.time()
            return self.pending.pop(0)

    def finish(self, result: dict) -> None:
        self.last_seen = time.time()
        slot = self.waiting.get(str(result.get("id") or ""))
        if slot:
            slot["result"] = result
            slot["event"].set()

    # Bridge side
    def run(self, job: dict) -> dict:
        job = dict(job)
        job["id"] = uuid.uuid4().hex
        slot = {"event": threading.Event(), "result": None}
        self.waiting[job["id"]] = slot
        with self.lock:
            self.pending.append(job)
            self.lock.notify_all()
        try:
            if not slot["event"].wait(JOB_TIMEOUT):
                with self.lock:
                    if job in self.pending:
                        self.pending.remove(job)
                return {"error": "Chrome did not finish the ChatGPT job in time (is Chrome open?)"}
            return slot["result"] or {"error": "empty result"}
        finally:
            self.waiting.pop(job["id"], None)

    def status(self) -> dict:
        return {"connected": time.time() - self.last_seen < CONNECTED_WITHIN, "last_seen": self.last_seen,
                "queue": len(self.pending), "busy": len(self.waiting), "extension_version": self.version,
                "email": self.email, "images_left": self.images, "images_reset": self.reset}


RELAY = _Relay()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, code: int, payload=None) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _query(self):
        url = urlparse(self.path)
        return url.path, {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}

    def _allowed(self, query) -> bool:
        return secrets.compare_digest(str(query.get("token") or ""), RELAY.token)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}

    def do_GET(self):
        path, query = self._query()
        if path == "/status":
            return self._send(200, RELAY.status())
        if path == "/pair":
            # The Web Store build of the extension has no config.js: hand it the token, but only to a
            # Chrome extension (web pages send their own https:// Origin and cannot fake this one).
            origin = str(self.headers.get("Origin") or "")
            allowed = _allowed_extension_ids()
            extension_id = origin[len("chrome-extension://"):] if origin.startswith("chrome-extension://") else ""
            if not extension_id or (allowed and extension_id not in allowed):
                return self._send(403, {"error": "extensions only"})
            return self._send(200, {"token": RELAY.token})
        if not self._allowed(query):
            return self._send(403, {"error": "bad token"})
        if path == "/next":
            RELAY.version = str(query.get("v") or RELAY.version)
            if "email" in query:
                RELAY.email = str(query.get("email") or "")
            if "images" in query:
                RELAY.images, RELAY.reset = str(query.get("images") or ""), str(query.get("reset") or "")
            job = RELAY.next_job(min(float(query.get("wait") or 25), 30))
            return self._send(200, job) if job else self._send(204)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path, query = self._query()
        if not self._allowed(query):
            return self._send(403, {"error": "bad token"})
        try:
            body = self._body()
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if path == "/result":
            RELAY.finish(body)
            return self._send(200, {"ok": True})
        if path == "/job":
            return self._send(200, RELAY.run(body))
        self._send(404, {"error": "not found"})


# Chrome Web Store id of "Tidmunz ChatGPT" once published; an empty set accepts any extension id.
STORE_EXTENSION_IDS: set[str] = set()


def _allowed_extension_ids() -> set[str]:
    ids = set(STORE_EXTENSION_IDS)
    try:
        ids.update(json.loads((data_dir() / "chrome_extension_ids.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        pass
    return ids


def _write_extension(source: Path, token: str, port: int) -> None:
    """Copy the extension next to Tidmunz's data and give it this PC's port and token."""
    target = extension_dir()
    target.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.*"):
        destination = target / item.name
        if not destination.is_file() or destination.read_bytes() != item.read_bytes():
            shutil.copy2(item, destination)
    config = f"self.TZ_CONFIG = {json.dumps({'port': port, 'token': token})};\n"
    config_path = target / "config.js"
    if not config_path.is_file() or config_path.read_text(encoding="utf-8") != config:
        config_path.write_text(config, encoding="utf-8")


def start(log=print) -> dict:
    """Start the relay once per program run; returns {port, token, extension_dir}."""
    if RELAY.port:
        return {"port": RELAY.port, "token": RELAY.token, "extension_dir": str(extension_dir())}
    info_path = data_dir() / "chrome_relay.json"
    try:
        saved = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    # Keep the same token and port across runs: the extension reads them from config.js once.
    token = str(saved.get("token") or secrets.token_urlsafe(24))
    ports = [int(saved["port"])] + [p for p in PORTS if p != int(saved["port"])] if saved.get("port") else list(PORTS)
    server = None
    for port in ports:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("no free port for the Chrome relay")
    server.daemon_threads = True
    RELAY.token, RELAY.port = token, server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text(json.dumps({"port": RELAY.port, "token": token}), encoding="utf-8")
    _write_extension(Path(__file__).with_name("chrome_ext"), token, RELAY.port)
    log(f"[Chrome] พร้อมรับส่วนขยาย Tidmunz ChatGPT ที่ 127.0.0.1:{RELAY.port}")
    return {"port": RELAY.port, "token": token, "extension_dir": str(extension_dir())}

