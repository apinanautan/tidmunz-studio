# -*- coding: utf-8 -*-
"""Small lifecycle helpers for SnapGen-owned local generation backends."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request


def _read_json(url: str):
    with urllib.request.urlopen(url, timeout=1.5) as response:
        return json.load(response)


def jobs_active() -> bool:
    """Return True when either local backend reports queued/running work."""
    try:
        jobs = _read_json("http://127.0.0.1:7861/api/v1/jobs").get("jobs") or []
        if any(str(job.get("status") or "").lower() in {"queued", "running"} for job in jobs):
            return True
    except Exception:
        pass
    try:
        queue = _read_json("http://127.0.0.1:8188/queue")
        if queue.get("queue_running") or queue.get("queue_pending"):
            return True
    except Exception:
        pass
    return False


def stop_all() -> int:
    """Stop Maestro and ComfyUI process pairs so Windows returns all memory."""
    if os.name != "nt":
        return 0
    script = r"""
$targets = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'python.exe' -and (
        $_.CommandLine -like '*\maestro\app\launch.py*' -or
        $_.CommandLine -like '*main.py --listen 127.0.0.1 --port 8188*'
    )
})
$count = $targets.Count
$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Output $count
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20,
        encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "หยุด Local backend ไม่สำเร็จ")[-1000:])
    try:
        return int((result.stdout or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0
