# -*- coding: utf-8 -*-
"""Download RMBG 2.0 from the owner's supplied Drive mirror, then verify it."""
from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

FILE_ID = "1TSWzo_G5QsNlij4Bg2CNPwUhi-AYjffy"
DOWNLOAD_URL = f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download&confirm=t"
DOWNLOAD_URLS = (
    DOWNLOAD_URL,
    f"https://drive.google.com/uc?export=download&id={FILE_ID}&confirm=t",
)
EXPECTED_SIZE = 884_878_856
EXPECTED_SHA256 = "566ed80c3d95f87ada6864d4cbe2290a1c5eb1c7bb0b123e984f60f76b02c3a7"
PUBLIC_CONFIG_BASE = "https://raw.githubusercontent.com/Bria-AI/RMBG-2.0/dev"
PUBLIC_CONFIG_FILES = (
    "config.json",
    "BiRefNet_config.py",
    "birefnet.py",
    "preprocessor_config.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid(path: Path) -> bool:
    return path.is_file() and path.stat().st_size == EXPECTED_SIZE and _sha256(path) == EXPECTED_SHA256


def _ensure_public_configs(directory: Path) -> None:
    for name in PUBLIC_CONFIG_FILES:
        path = directory / name
        if path.is_file() and path.stat().st_size > 0:
            continue
        temp = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(f"{PUBLIC_CONFIG_BASE}/{name}", temp)
        if not temp.is_file() or temp.stat().st_size == 0:
            raise RuntimeError(f"ดาวน์โหลด {name} ไม่สำเร็จ")
        os.replace(temp, path)


def _download_model(partial: Path, progress) -> None:
    """Download with Python standard library for every workstation."""
    current = partial.stat().st_size if partial.is_file() else 0
    if current == EXPECTED_SIZE:
        return
    if current > EXPECTED_SIZE:
        partial.unlink()
        current = 0
    last_error = None
    for attempt in range(1, 7):
        try:
            headers = {"User-Agent": "SnapGen/1.0"}
            if current:
                headers["Range"] = f"bytes={current}-"
            request = urllib.request.Request(DOWNLOAD_URLS[(attempt - 1) % len(DOWNLOAD_URLS)], headers=headers)
            with urllib.request.urlopen(request, timeout=600) as response:
                # Google may ignore Range. Overwrite instead of appending a
                # complete response to a partial file.
                append = current > 0 and response.status == 206
                completed = current if append else 0
                mode = "ab" if append else "wb"
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/html" in content_type:
                    raise RuntimeError("Google Drive ส่งหน้าเว็บแทนไฟล์โมเดล")
                with partial.open(mode) as stream:
                    while True:
                        chunk = response.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        completed += len(chunk)
                        progress(completed, EXPECTED_SIZE)
            return
        except Exception as exc:
            last_error = exc
            if attempt == 6:
                break
            print(f"DOWNLOAD|{int(current * 100 / EXPECTED_SIZE)}|ดาวน์โหลด RMBG 2.0 สะดุด — ลองต่อ {attempt}/5: {exc}", flush=True)
            time.sleep(min(attempt * 3, 15))
            current = partial.stat().st_size if partial.is_file() else 0
    raise RuntimeError(f"ดาวน์โหลดจาก Google Drive ไม่สำเร็จหลังลองต่อ 6 ครั้ง: {last_error}")


def main() -> int:
    if len(sys.argv) != 2:
        print("ERROR|ต้องระบุตำแหน่ง model.safetensors", flush=True)
        return 2
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    if _valid(target):
        _ensure_public_configs(target.parent)
        print("DOWNLOAD|100|RMBG 2.0 พร้อมใช้งาน", flush=True)
        return 0

    print("DOWNLOAD|0|เริ่มดาวน์โหลด RMBG 2.0", flush=True)
    last_percent = -1

    def progress(current: int, total: int | None):
        nonlocal last_percent
        percent = int(current * 100 / total) if total else 0
        if percent != last_percent:
            last_percent = percent
            print(f"DOWNLOAD|{percent}|ดาวน์โหลด RMBG 2.0", flush=True)

    try:
        _download_model(partial, progress)
        if not partial.is_file():
            raise RuntimeError("Google Drive ไม่ส่งไฟล์กลับมา")
        print("DOWNLOAD|99|กำลังตรวจ SHA-256", flush=True)
        if partial.stat().st_size != EXPECTED_SIZE:
            raise RuntimeError(
                f"ขนาดไฟล์ผิด: {partial.stat().st_size:,} ต้องเป็น {EXPECTED_SIZE:,} bytes"
            )
        actual_hash = _sha256(partial)
        if actual_hash != EXPECTED_SHA256:
            raise RuntimeError(f"SHA-256 ไม่ตรง: {actual_hash}")
        os.replace(partial, target)
        _ensure_public_configs(target.parent)
        print("DOWNLOAD|100|ติดตั้ง RMBG 2.0 เสร็จ", flush=True)
        return 0
    except Exception as exc:
        print(f"ERROR|ดาวน์โหลด RMBG 2.0 ไม่สำเร็จ: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
