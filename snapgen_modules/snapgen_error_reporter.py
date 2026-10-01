# -*- coding: utf-8 -*-
"""Privacy-safe automatic error reports shared by every SnapGen workstation.

Reports use GitHub Issues, not commits.  Each PC keeps a local queue until the
GitHub CLI is signed in, so application work never waits for the network.
"""
from __future__ import annotations

import hashlib
import atexit
import faulthandler
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid

REPOSITORY = "tidmunzsocial-lab/tidmunz-studio"
ERROR_WORDS = re.compile(
    r"(?:\bERROR\b|\bFAILED?\b|\bEXCEPTION\b|\bWARNING\b|\bWARN\b|\bFATAL\b|\bCRITICAL\b|TRACEBACK|ล้มเหลว|ไม่สำเร็จ|ผิดพลาด)",
    re.IGNORECASE,
)
_root: Path | None = None
_queue_file: Path | None = None
_machine_file: Path | None = None
_session_file: Path | None = None
_fatal_file: Path | None = None
_fatal_stream = None
_worker_lock = threading.Lock()
_queue_io_lock = threading.Lock()
_worker_running = False
_periodic_started = False
_login_started = False
_login_last_attempt = 0.0
_recent: dict[str, float] = {}
_specs_cache: dict | None = None
_old_sys_hook = sys.excepthook
_old_thread_hook = getattr(threading, "excepthook", None)
_streams_installed = False
_current_area = "startup"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def configure(project_root) -> None:
    """Point reporter at this portable copy and flush pending reports."""
    global _root, _queue_file, _machine_file, _session_file, _fatal_file
    global _fatal_stream, _specs_cache, _periodic_started
    if _fatal_stream is not None:
        try:
            _fatal_stream.flush()
            _fatal_stream.close()
        except Exception:
            pass
        _fatal_stream = None
    _root = Path(project_root).resolve()
    data = _root / "snapgen_data" / "error_reports"
    data.mkdir(parents=True, exist_ok=True)
    _queue_file = data / "pending.jsonl"
    _machine_file = data / "machine_id.txt"
    _session_file = data / "active_session.json"
    _fatal_file = data / "fatal_python.log"
    _specs_cache = None
    stale = data / "sending.jsonl"
    if stale.is_file():
        try:
            with _queue_io_lock:
                with _queue_file.open("a", encoding="utf-8") as out:
                    out.write(stale.read_text(encoding="utf-8"))
                stale.unlink(missing_ok=True)
        except Exception:
            pass
    threading.Thread(target=_machine_specs, name="SnapGenSpecs", daemon=True).start()
    if not _periodic_started:
        _periodic_started = True
        threading.Thread(target=_periodic_loop, name="SnapGenErrorRetry", daemon=True).start()
    _recover_previous_session()
    bootstrap = data / "bootstrap_error.log"
    try:
        if bootstrap.is_file() and bootstrap.stat().st_size:
            detail = bootstrap.read_text(encoding="utf-8", errors="replace")[-12000:]
            _enqueue("early startup", "SnapGen failed before the main UI loaded", detail)
            bootstrap.write_text("", encoding="utf-8")
    except Exception:
        pass
    try:
        _fatal_stream = _fatal_file.open("a", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_fatal_stream, all_threads=True)
    except Exception:
        _fatal_stream = None
    _write_active_session()
    atexit.register(mark_clean_shutdown)
    _start_worker()


def _recover_previous_session() -> None:
    if _session_file is None or not _session_file.is_file():
        return
    try:
        previous = json.loads(_session_file.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
    pid = int(previous.get("pid") or 0)
    if previous.get("clean") or _pid_alive(pid):
        return
    fatal_tail = ""
    try:
        if _fatal_file and _fatal_file.is_file():
            fatal_tail = _fatal_file.read_text(encoding="utf-8", errors="replace")[-8000:]
    except Exception:
        pass
    detail = "\n".join([
        "Previous SnapGen session ended without a clean shutdown.",
        f"Previous session: {previous.get('session_id', 'unknown')}",
        f"Started: {previous.get('started_at', 'unknown')}",
        f"Last heartbeat: {previous.get('heartbeat_at', 'unknown')}",
        f"Last area: {previous.get('area', 'unknown')}",
        ("Fatal Python trace:\n" + fatal_tail) if fatal_tail else "No Python fatal trace was produced; process/OS/power termination is possible.",
    ])
    _enqueue("previous session crash", "SnapGen closed unexpectedly", detail)
    try:
        if _fatal_file:
            _fatal_file.write_text("", encoding="utf-8")
    except Exception:
        pass


def _write_active_session(area=None) -> None:
    global _current_area
    if _session_file is None:
        return
    if area:
        _current_area = str(area)[:80]
    now = time.strftime("%Y-%m-%d %H:%M:%S %z")
    current = {}
    try:
        if _session_file.is_file():
            current = json.loads(_session_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    if int(current.get("pid") or 0) != os.getpid() or current.get("clean"):
        current = {
            "session_id": uuid.uuid4().hex[:12], "pid": os.getpid(),
            "started_at": now, "clean": False,
        }
    current.update({"heartbeat_at": now, "area": _current_area, "clean": False})
    temp = _session_file.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, _session_file)
    except Exception:
        pass


def mark_clean_shutdown() -> None:
    if _session_file is None:
        return
    try:
        current = json.loads(_session_file.read_text(encoding="utf-8")) if _session_file.is_file() else {}
        if int(current.get("pid") or 0) == os.getpid():
            current["clean"] = True
            current["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
            _session_file.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def shutdown() -> None:
    """Close diagnostic handles cleanly; normal app exit also calls this."""
    mark_clean_shutdown()
    try:
        if _fatal_stream:
            _fatal_stream.flush()
            _fatal_stream.close()
    except Exception:
        pass


def _machine_id() -> str:
    if _machine_file is None:
        return "PC-UNKNOWN"
    try:
        value = _machine_file.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"PC-[A-F0-9]{8}", value):
            return value
    except Exception:
        pass
    value = "PC-" + uuid.uuid4().hex[:8].upper()
    try:
        _machine_file.write_text(value, encoding="utf-8")
    except Exception:
        pass
    return value

def _machine_name() -> str:
    """Human-readable Windows PC name; machine_id remains permanent identity."""
    value = str(platform.node() or os.environ.get("COMPUTERNAME") or "ไม่ทราบชื่อเครื่อง").strip()
    return re.sub(r"[^0-9A-Za-zก-๙._ -]+", "-", value)[:80] or "ไม่ทราบชื่อเครื่อง"


def _run_text(command, timeout=12) -> str:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=flags,
        )
        return (result.stdout or "").strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _machine_specs() -> dict:
    global _specs_cache
    if _specs_cache is not None:
        return dict(_specs_cache)
    specs = {
        "machine_id": _machine_id(),
        "machine_name": _machine_name(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "python": platform.python_version(),
        "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "ram_gb": "unknown",
        "gpu": "unknown",
        "app_version": "unknown",
    }
    ps = shutil.which("powershell.exe") or shutil.which("powershell")
    if ps:
        cpu = _run_text([ps, "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)"])
        ram = _run_text([ps, "-NoProfile", "-Command", "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)"])
        gpu = _run_text([ps, "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ' | '"])
        if cpu:
            specs["cpu"] = cpu
        if ram:
            specs["ram_gb"] = ram
        if gpu:
            specs["gpu"] = gpu
    if _root:
        for candidate in (
            _root / "snapgen_version.json",
            _root / "snapgen_data" / "meta" / "snapgen_version.json",
        ):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                specs["app_version"] = str(data.get("version") or "unknown")
                break
            except Exception:
                pass
    _specs_cache = dict(specs)
    return specs


def _report_specs() -> dict:
    """Never make a failing UI/worker wait for slow WMI hardware queries."""
    if _specs_cache is not None:
        return dict(_specs_cache)
    return {
        "machine_id": _machine_id(),
        "machine_name": _machine_name(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "python": platform.python_version(),
        "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "collecting"),
        "ram_gb": "collecting",
        "gpu": "collecting",
        "app_version": "collecting",
    }


def _sanitize(value) -> str:
    """Remove credentials, personal paths and common user content fields."""
    text = str(value or "")
    home = str(Path.home())
    if home:
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.IGNORECASE)
    if _root:
        text = re.sub(re.escape(str(_root)), "%SNAPGEN%", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,'\"]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)\b(api[_-]?key|token|cookie|secret|password)\b\s*[:=]\s*[^\s,;}]+", r"\1=<redacted>", text)
    text = re.sub(r"(?i)([?&](?:token|key|secret|signature)=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>", text)
    text = re.sub(r"(?i)(\"?(?:prompt|input|text|content|cookie)\"?\s*:\s*)\".*?\"", r"\1\"<redacted>\"", text)
    text = re.sub(r"(?i)\b(prompt|input|content)\b\s*[:=]\s*[^\r\n]+", r"\1=<redacted>", text)
    text = re.sub(r"(?i)C:\\Users\\[^\\\s]+", r"%USERPROFILE%", text)
    text = re.sub(r"(?i)%USERPROFILE%(?:\\[^\s\"']+)+", "%USERPROFILE%\\<local-path>", text)
    text = re.sub(r"(?i)\b[A-Z]:\\[^\r\n\"']+", "<local-path>", text)
    return text[:12000]


def _signature(kind: str, detail: str) -> str:
    stable = _sanitize(detail)
    stable = re.sub(r"line \d+", "line #", stable, flags=re.IGNORECASE)
    stable = re.sub(r"0x[0-9a-f]+", "0x#", stable, flags=re.IGNORECASE)
    stable = re.sub(r"\b\d{4,}\b", "#", stable)
    return hashlib.sha256((kind + "\n" + stable).encode("utf-8", "replace")).hexdigest()[:12]


def _enqueue(kind: str, summary: str, detail: str) -> None:
    if _queue_file is None:
        return
    clean_summary = " ".join(_sanitize(summary).split())[:180] or "Unknown error"
    clean_detail = _sanitize(detail)
    fingerprint = _signature(kind, clean_detail or clean_summary)
    now = time.time()
    if now - _recent.get(fingerprint, 0) < 300:
        return
    _recent[fingerprint] = now
    item = {
        "fingerprint": fingerprint,
        "kind": _sanitize(kind)[:80],
        "summary": clean_summary,
        "detail": clean_detail,
        "occurred_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "specs": _report_specs(),
    }
    try:
        with _queue_io_lock:
            with _queue_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        return
    _start_worker()


def report_exception(kind, exc_type, exc_value, exc_traceback) -> None:
    rendered = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    _enqueue(str(kind), f"{getattr(exc_type, '__name__', 'Exception')}: {exc_value}", rendered)


def report_log(message, source="application log") -> None:
    global _current_area
    text = " ".join(str(message or "").split()).strip()
    if text:
        _current_area = str(source or "application log")[:80]
    if text and ERROR_WORDS.search(text):
        _enqueue(source, text[:180], text)


class _ReportingStream:
    """Pass console through unchanged and inspect complete error lines."""
    def __init__(self, original, source):
        self.original = original
        self.source = source
        self.buffer = ""

    def write(self, value):
        text = str(value or "")
        result = self.original.write(text)
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            report_log(line, self.source)
        return result

    def flush(self):
        return self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


def install_stream_capture() -> None:
    global _streams_installed
    if _streams_installed:
        return
    _streams_installed = True
    if sys.stdout is not None and not isinstance(sys.stdout, _ReportingStream):
        sys.stdout = _ReportingStream(sys.stdout, "console stdout")
    if sys.stderr is not None and not isinstance(sys.stderr, _ReportingStream):
        sys.stderr = _ReportingStream(sys.stderr, "console stderr")


def install_tk_watchdog(root, *, threshold_seconds=45) -> None:
    """Report a frozen Tk main thread once per freeze, then recover silently."""
    state = {"last": time.monotonic(), "reported": False, "alive": True}

    def pulse():
        state["last"] = time.monotonic()
        state["reported"] = False
        _write_active_session("tk-mainloop")
        try:
            if root.winfo_exists():
                root.after(2000, pulse)
            else:
                state["alive"] = False
        except Exception:
            state["alive"] = False

    def monitor():
        poll_seconds = max(0.05, min(5.0, float(threshold_seconds) / 3.0))
        while state["alive"]:
            time.sleep(poll_seconds)
            stalled = time.monotonic() - state["last"]
            if stalled < threshold_seconds or state["reported"]:
                continue
            state["reported"] = True
            stacks = []
            try:
                frames = sys._current_frames()
                for thread in threading.enumerate():
                    frame = frames.get(thread.ident)
                    if frame is not None:
                        stacks.append(f"\n--- {thread.name} ---\n" + "".join(traceback.format_stack(frame)[-18:]))
            except Exception as exc:
                stacks.append(f"Could not collect stacks: {exc}")
            _enqueue(
                "UI watchdog", f"SnapGen UI stopped responding for {int(stalled)} seconds",
                f"UI heartbeat was delayed {stalled:.1f} seconds. Thread stacks:\n{''.join(stacks)}",
            )

    root.after(0, pulse)
    threading.Thread(target=monitor, name="SnapGenUIWatchdog", daemon=True).start()


def install_bridge_watchdog(
    url="http://127.0.0.1:8000/health", api_key="local-dev-key",
    *, interval_seconds=60, failure_limit=3,
) -> None:
    """Report a Bridge that dies after it was healthy; ignore normal startup."""
    state = {"was_healthy": False, "failures": 0, "reported": False}

    def monitor():
        while True:
            error = "health response was not ready"
            try:
                request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
                with urllib.request.urlopen(request, timeout=4) as response:
                    payload = response.read(2048).decode("utf-8", "replace")
                healthy = response.status == 200 and '"ok"' in payload and "true" in payload.lower()
            except Exception as exc:
                healthy = False
                error = str(exc)
            if healthy:
                state.update(was_healthy=True, failures=0, reported=False)
            elif state["was_healthy"]:
                state["failures"] += 1
                if state["failures"] >= int(failure_limit) and not state["reported"]:
                    state["reported"] = True
                    _enqueue(
                        "Bridge watchdog", "Local ChatGPT Bridge stopped responding",
                        f"Bridge was healthy, then failed {state['failures']} consecutive checks. Last error: {error}",
                    )
            time.sleep(max(0.05, float(interval_seconds)))

    threading.Thread(target=monitor, name="SnapGenBridgeWatchdog", daemon=True).start()


def install_exception_hooks() -> None:
    def sys_hook(exc_type, exc_value, exc_traceback):
        report_exception("unhandled main thread", exc_type, exc_value, exc_traceback)
        if callable(_old_sys_hook) and getattr(_old_sys_hook, "__name__", "") != "_bootstrap_exception_hook":
            _old_sys_hook(exc_type, exc_value, exc_traceback)
        else:
            traceback.print_exception(exc_type, exc_value, exc_traceback)
    sys.excepthook = sys_hook
    if hasattr(threading, "excepthook"):
        def thread_hook(args):
            report_exception(
                f"background task: {getattr(args.thread, 'name', 'unknown')}",
                args.exc_type, args.exc_value, args.exc_traceback,
            )
            if callable(_old_thread_hook):
                _old_thread_hook(args)
        threading.excepthook = thread_hook


def _issue_body(item: dict) -> str:
    specs = item.get("specs") or {}
    return "\n".join([
        "Automatic privacy-filtered SnapGen error report.", "",
        f"- Machine name: `{specs.get('machine_name', 'unknown')}`",
        f"- Machine ID: `{specs.get('machine_id', 'unknown')}`",
        f"- App: `{specs.get('app_version', 'unknown')}`",
        f"- OS: `{specs.get('os', 'unknown')}`",
        f"- Python: `{specs.get('python', 'unknown')}`",
        f"- CPU: `{specs.get('cpu', 'unknown')}`",
        f"- RAM: `{specs.get('ram_gb', 'unknown')} GB`",
        f"- GPU: `{specs.get('gpu', 'unknown')}`",
        f"- Time: `{item.get('occurred_at', '')}`",
        f"- Area: `{item.get('kind', '')}`",
        f"- Fingerprint: `{item.get('fingerprint', '')}`", "",
        "```text", str(item.get("detail") or item.get("summary") or "")[:12000], "```",
        "", "No prompt, media, account cookie, API key, or raw user path is intentionally included.",
    ])


def _gh_ready() -> bool:
    gh = shutil.which("gh")
    if not gh:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            [gh, "auth", "status", "--hostname", "github.com"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=12, creationflags=flags,
        ).returncode == 0
    except Exception:
        return False


def _upload(item: dict) -> bool:
    gh = shutil.which("gh")
    if not gh:
        return False
    # Hardware collection runs away from the failing UI. Enrich queued reports
    # immediately before upload so GitHub still receives complete PC specs.
    item = dict(item)
    item["specs"] = _machine_specs()
    fp = str(item.get("fingerprint") or "")
    machine_id = str(item["specs"].get("machine_id") or "PC-UNKNOWN")
    machine_name = str(item["specs"].get("machine_name") or "unknown")
    app_version = str(item["specs"].get("app_version") or "unknown").strip().lstrip("v") or "unknown"
    title = f"[SnapGen Error][v{app_version}][{machine_name}][{machine_id}] {item.get('summary', 'Unknown error')[:58]} [{fp}]"
    body = _issue_body(item)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    common = dict(capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, creationflags=flags)
    try:
        found = subprocess.run(
            [gh, "issue", "list", "--repo", REPOSITORY, "--state", "open",
             "--search", f"{machine_id} {fp} in:title", "--limit", "1", "--json", "number", "--jq", ".[0].number"],
            **common,
        )
        number = (found.stdout or "").strip() if found.returncode == 0 else ""
        if number.isdigit():
            result = subprocess.run([gh, "issue", "comment", number, "--repo", REPOSITORY, "--body", body], **common)
        else:
            result = subprocess.run([gh, "issue", "create", "--repo", REPOSITORY, "--title", title, "--body", body], **common)
        return result.returncode == 0
    except Exception:
        return False


def _flush() -> None:
    global _worker_running
    try:
        if _queue_file is None or not _queue_file.is_file():
            return
        if not _gh_ready():
            _launch_login_once()
            return
        batch = _queue_file.with_name("sending.jsonl")
        with _queue_io_lock:
            try:
                if batch.is_file():
                    with _queue_file.open("a", encoding="utf-8") as out:
                        out.write(batch.read_text(encoding="utf-8"))
                    batch.unlink(missing_ok=True)
                os.replace(_queue_file, batch)
            except Exception:
                return
        lines = batch.read_text(encoding="utf-8").splitlines()
        pending = []
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not _upload(item):
                pending.append(item)
        if pending:
            with _queue_io_lock:
                with _queue_file.open("a", encoding="utf-8") as out:
                    out.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in pending))
        batch.unlink(missing_ok=True)
    finally:
        with _worker_lock:
            _worker_running = False


def _start_worker() -> None:
    global _worker_running
    with _worker_lock:
        if _worker_running:
            return
        _worker_running = True
    threading.Thread(target=_flush, name="SnapGenErrorReporter", daemon=True).start()


def _periodic_loop() -> None:
    # Error lists change rarely. A 15-minute retry avoids constant GitHub/API
    # traffic while still sending queued reports after login/network returns.
    while True:
        time.sleep(900)
        _start_worker()


def _launch_login_once() -> None:
    """Open one browser authorization flow; users never handle logs or Git."""
    global _login_started, _login_last_attempt
    if _login_started or time.time() - _login_last_attempt < 900:
        return
    gh = shutil.which("gh")
    if not gh:
        return
    _login_started = True
    _login_last_attempt = time.time()
    try:
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        process = subprocess.Popen(
            [gh, "auth", "login", "--hostname", "github.com",
             "--git-protocol", "https", "--web", "--clipboard"],
            creationflags=flags,
        )
    except Exception:
        _login_started = False
        return

    def wait_for_login():
        global _login_started
        try:
            process.wait(timeout=600)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
        _login_started = False
        if _gh_ready():
            _start_worker()

    threading.Thread(target=wait_for_login, name="SnapGenGitHubLogin", daemon=True).start()


def pending_count() -> int:
    try:
        return sum(1 for line in _queue_file.read_text(encoding="utf-8").splitlines() if line.strip()) if _queue_file else 0
    except Exception:
        return 0
