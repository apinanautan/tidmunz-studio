"""SnapGen Grok Lower video API adapter."""

import json
import subprocess
from pathlib import Path


MODEL = "grok-lower"
LABEL = "Grok Lower — เสียเครดิต 50%"
API_URL = "https://api.snapgen.ai/uapi/v1/video-gen/grok-lower"
SUPPORTED_DURATIONS = ("6", "10", "15")


def build_command(api_key, prompt, image, resolution, duration, aspect, mode):
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError("กรุณาใส่ SnapGen API key ในหน้าตั้งค่าก่อน")
    source = Path(str(image or "").strip())
    if not source.is_file():
        raise RuntimeError("ไม่พบรูปเริ่มต้นสำหรับ Grok Lower")
    return [
        "curl", "-sS", "-N", "--max-time", "45", "-X", "POST", API_URL,
        "-H", "x-api-key: " + key,
        "--form", "prompt=" + str(prompt or "").strip(),
        "--form", "model=" + MODEL,
        "--form", "resolution=" + str(resolution or "480p").strip(),
        "--form", "duration=" + str(duration or "6").strip(),
        "--form", "aspect_ratio=" + str(aspect or "landscape").strip(),
        "--form", "mode=" + str(mode or "custom").strip(),
        "--form", "skip_audio=false",
        "--form", "files=@" + str(source),
    ]


def _json_lines(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw or "").strip()
    if not text:
        return []
    candidates = [text]
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line and line != "[DONE]":
            candidates.append(line)
    payloads = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            payloads.append(value)
    return payloads


def request(api_key, prompt, image, resolution, duration, aspect, mode):
    command = build_command(api_key, prompt, image, resolution, duration, aspect, mode)
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        output = (result.stdout or result.stderr or "").strip()
        return _response_from_output(output, result.returncode)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or exc.stderr or ""
        return _response_from_output(output, 28)


def _response_from_output(output, returncode):
    payloads = _json_lines(output)
    if payloads:
        for payload in payloads:
            if any(payload.get(key) for key in ("uuid", "history_uuid", "conversion_uuid", "id")):
                return payload
        return payloads[0]
    if returncode:
        raise RuntimeError("Grok Lower API ตอบกลับไม่สำเร็จ: " + str(output)[:1000])
    raise RuntimeError("Grok Lower API ตอบกลับไม่ใช่ JSON: " + str(output)[:1000])
