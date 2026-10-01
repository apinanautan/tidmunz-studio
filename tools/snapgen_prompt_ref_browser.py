from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def _chrome_exe() -> Path:
    for value in (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Google/Chrome/Application/chrome.exe",
    ):
        if value.is_file():
            return value
    raise RuntimeError("ไม่พบ Google Chrome")


def _tabs(port: int):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
        value = json.loads(response.read().decode("utf-8", errors="replace"))
    return value if isinstance(value, list) else []


def _conversation_id(url: str) -> str:
    value = urlparse(str(url or "")).path.rstrip("/").rsplit("/", 1)[-1]
    if not value:
        raise RuntimeError("Prompt-Ref ไม่มี conversation URL")
    return value


def _ensure_tab(url: str, profile: Path):
    cid = _conversation_id(url)
    for port in range(9223, 9244):
        try:
            for tab in _tabs(port):
                if tab.get("type") == "page" and cid in str(tab.get("url") or ""):
                    return port, tab
        except Exception:
            pass
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    profile = profile.parent if profile.name.lower() == "default" else profile
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        str(_chrome_exe()),
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            for tab in _tabs(port):
                if tab.get("type") == "page" and cid in str(tab.get("url") or ""):
                    return port, tab
        except Exception:
            pass
        time.sleep(.5)
    raise RuntimeError("เปิด Prompt-Ref ใน SnapGen Chrome ไม่สำเร็จ")


class _CDP:
    def __init__(self, url: str, port: int):
        import websocket
        self.websocket = websocket
        self.ws = websocket.create_connection(url, timeout=10, origin=f"http://127.0.0.1:{port}")
        self.ws.settimeout(1)
        self.seq = 0

    def close(self):
        try: self.ws.close()
        except Exception: pass

    def call(self, method: str, params=None, timeout=30):
        self.seq += 1
        request_id = self.seq
        self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                value = json.loads(self.ws.recv())
            except self.websocket.WebSocketTimeoutException:
                continue
            if value.get("id") == request_id:
                if value.get("error"):
                    raise RuntimeError(str(value["error"]))
                return value.get("result") or {}
        raise RuntimeError(f"Chrome timeout: {method}")

    def eval(self, expression: str, await_promise=False, timeout=30):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": bool(await_promise),
            "returnByValue": True,
        }, timeout)
        return ((result.get("result") or {}).get("value"))


def _wait(cdp: _CDP, expression: str, timeout: float, name: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = cdp.eval(expression)
            if value:
                return value
        except Exception:
            pass
        time.sleep(.5)
    raise RuntimeError(f"หมดเวลารอ{name}")


def ask_json_in_prompt_ref(*, conversation_url, image_path, prompt, profile_path, timeout=600):
    image = Path(str(image_path)).resolve()
    if not image.is_file():
        raise RuntimeError("ไม่พบรูป Storyboard")
    cid = _conversation_id(conversation_url)
    profile = Path(str(profile_path or Path(os.environ.get("LOCALAPPDATA", "")) / "TidMunStudio/SnapGenChromeProfile/Default"))
    port, tab = _ensure_tab(conversation_url, profile)
    cdp = _CDP(str(tab.get("webSocketDebuggerUrl") or ""), port)
    try:
        cdp.call("Runtime.enable")
        cdp.call("DOM.enable")
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": conversation_url})
        _wait(cdp, "document.readyState==='complete' && !!document.querySelector('#prompt-textarea') && !!document.querySelector('input#upload-photos')", 60, "หน้า Prompt-Ref")
        before = cdp.eval(f"""(async()=>{{const r=await fetch('/backend-api/conversation/{cid}',{{credentials:'include'}});const j=await r.json();return j.current_node||'';}})()""", True) or ""
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        root_id = ((doc.get("root") or {}).get("nodeId"))
        node_id = (cdp.call("DOM.querySelector", {"nodeId": root_id, "selector": "input#upload-photos"}).get("nodeId"))
        if not node_id:
            raise RuntimeError("หน้า Prompt-Ref ไม่มีช่องแนบรูป")
        cdp.call("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(image)]})
        _wait(cdp, "!!document.querySelector('#prompt-textarea')", 30, "ช่องพิมพ์")
        cdp.eval("document.querySelector('#prompt-textarea').focus(); document.execCommand('selectAll'); document.execCommand('delete'); true")
        cdp.call("Input.insertText", {"text": str(prompt)})
        _wait(cdp, "(() => {const b=document.querySelector('button[data-testid=\"send-button\"]'); return !!b && !b.disabled;})()", 90, "ปุ่มส่ง")
        if not cdp.eval("(() => {const b=document.querySelector('button[data-testid=\"send-button\"]'); if(!b||b.disabled)return false; b.click(); return true;})()"):
            raise RuntimeError("กดส่ง Prompt-Ref ไม่สำเร็จ")
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = cdp.eval(f"""(async()=>{{
                const r=await fetch('/backend-api/conversation/{cid}',{{credentials:'include'}});
                const j=await r.json(); const id=j.current_node||''; const n=(j.mapping||{{}})[id]||{{}};
                const m=n.message||{{}}; const c=m.content||{{}}; const p=Array.isArray(c.parts)?c.parts:[];
                return {{id,role:(m.author||{{}}).role||'',text:p.filter(x=>typeof x==='string').join('').trim()}};
            }})()""", True, 30) or {}
            text = str(result.get("text") or "").strip()
            if result.get("id") and result.get("id") != before and result.get("role") == "assistant" and "{" in text and "}" in text:
                return {"text": text, "conversation_id": cid, "parent_message_id": str(result.get("id"))}
            if "Something went wrong" in text or "เกิดข้อผิดพลาด" in text or "มีบางอย่างผิดพลาด" in text:
                raise RuntimeError("ChatGPT Web ตอบ error แทน JSON")
            time.sleep(1)
        raise RuntimeError("GPT ไม่ตอบ JSON ภายในเวลาที่กำหนด")
    finally:
        cdp.close()
