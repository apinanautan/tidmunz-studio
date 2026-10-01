# -*- coding: utf-8 -*-
"""Hunyuan 3D API client — standalone implementation.
Tencent Hunyuan 3D — text→image and image→3D mesh generation.

Endpoints (verified 2026-07-19):
  - text2Image : POST /api/3d/worldPlay/text2Image    (scene prompts only)
  - generations: POST /api/3d/creations/generations    (signed query, image2ModelV3.1)
  - detail     : GET  /api/3d/creations/detail          (signed query, status + result URLs)

Usage:
  from hunyuan3d import Hunyuan3DClient

  client = Hunyuan3DClient(cookie_file=str(Path(__file__).resolve().parent.parent / "snapgen_data" / "hunyuan_cookies.txt"))
  # 1. text → image (scene description required, NOT single object)
  img_url = client.text2image("a wooden chair in a sunlit room with plants")
  # 2. image → 3D model (returns creationsId)
  task_id = client.image_to_3d(img_url)
  # 3. poll until done, return result dict
  result = client.wait_for_task(task_id, timeout=300)
  # 4. download model files
  client.download_result(result, out_dir="output/")
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import string
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

SIGN_SECRET = "Hf6d6KFB3D"
API_BASE = "https://3d.hunyuan.tencent.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
DEFAULT_USER_ID = "dc3af0e7f3814613b5a3f7068b3b91f7"


class Hunyuan3DError(Exception):
    """Hunyuan 3D API error."""
    pass


def _gen_timestamp() -> str:
    return str(int(time.time()))


def _gen_nonce(length: int = 16) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def sign_request(params: dict[str, str] | None = None) -> str:
    """HMAC-SHA256 sign sorted query params. Returns query string with &sign=..."""
    p = dict(params or {})
    p["timestamp"] = _gen_timestamp()
    p["nonce"] = _gen_nonce()
    sorted_keys = sorted(p.keys())
    raw = "&".join(f"{k}={p[k]}" for k in sorted_keys)
    sig = hmac.new(
        SIGN_SECRET.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{raw}&sign={sig}"


def _parse_netscape_cookies(cookie_file: str) -> dict[str, str]:
    """Parse Netscape cookie file into {name: value} dict."""
    cookies: dict[str, str] = {}
    with open(cookie_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                name, value = parts[5], parts[6]
                cookies[name] = value
    return cookies


def _cookie_header(cookie_file: str) -> str:
    """Build Cookie header string from Netscape cookie file."""
    cookies = _parse_netscape_cookies(cookie_file)
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


class Hunyuan3DClient:
    """Hunyuan 3D API client."""

    def __init__(self, cookie_file: str, user_id: str | None = None):
        self.cookie_file = cookie_file
        self.user_id = user_id or DEFAULT_USER_ID
        self._ua = UA

    def _signed_headers(self) -> list[str]:
        """Build curl headers for signed API requests."""
        cookie_str = _cookie_header(self.cookie_file)
        return [
            "-H", "accept: application/json, text/plain, */*",
            "-H", "Content-Type: application/json",
            "-H", f"Referer: {API_BASE}/",
            "-H", f"Origin: {API_BASE}",
            "-H", "x-product: hunyuan3d",
            "-H", "x-source: web",
            "-H", f"x_hunyuan_inner_user_id: {self.user_id}",
            "-H", f"Cookie: {cookie_str}",
        ]

    def _unsigned_headers(self) -> list[str]:
        """Build curl headers for unsigned API requests (text2image)."""
        cookie_str = _cookie_header(self.cookie_file)
        return [
            "-H", f"User-Agent: {self._ua}",
            "-H", "Content-Type: application/json",
            "-H", f"Referer: {API_BASE}/",
            "-H", f"Origin: {API_BASE}",
            "-H", f"Cookie: {cookie_str}",
        ]

    def _curl(self, method: str, url: str, headers: list[str], data: str | None = None, timeout: int = 120) -> dict[str, Any]:
        """Execute curl and return parsed JSON."""
        cmd = ["curl", "-s", "-X", method, url, *headers, "--max-time", str(timeout)]
        if data:
            cmd += ["--data-raw", data]

        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 10)
        out = (r.stdout or r.stderr or "").strip()
        if r.returncode != 0:
            raise Hunyuan3DError(out[:500] or f"curl exit {r.returncode}")
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            raise Hunyuan3DError(f"Invalid JSON: {out[:500]}")

    def daily_quota(self) -> dict[str, Any]:
        """Return today's free Hunyuan 3D quota without creating a task."""
        cookies = _parse_netscape_cookies(self.cookie_file)
        user_id = cookies.get("hy_user") or cookies.get("hunyuan_user") or self.user_id
        old_user_id = self.user_id
        self.user_id = user_id
        try:
            return self._curl(
                "POST",
                f"{API_BASE}/api/3d/quotainfo",
                [*self._signed_headers(), "-H", f"User-Agent: {self._ua}", "-H", f"Trace-Id: {uuid.uuid4().hex}"],
                json.dumps({"sceneType": "3dCreations"}, separators=(",", ":")),
                timeout=20,
            )
        finally:
            self.user_id = old_user_id

    # ── text2image ──────────────────────────────────────────────

    def text2image(self, prompt: str) -> str:
        """Generate a scene image from text prompt. Returns image URL.

        IMPORTANT: prompt must describe a SCENE (e.g. "a chair in a room"),
        NOT a single object. Single-object prompts return 400 error.
        """
        result = self._curl(
            "POST",
            f"{API_BASE}/api/3d/worldPlay/text2Image",
            self._unsigned_headers(),
            json.dumps({"prompt": prompt}, ensure_ascii=False),
        )
        url = result.get("imageUrl")
        if not url:
            raise Hunyuan3DError(f"No imageUrl in response: {json.dumps(result, ensure_ascii=False)[:300]}")
        return url

    # ── upload local image ─────────────────────────────────────

    def upload_image(self, image_path: str) -> str:
        """Upload a local image to Hunyuan COS and return its Hunyuan resourceUrl."""
        p = Path(image_path)
        if not p.is_file():
            raise Hunyuan3DError(f"Image file not found: {image_path}")
        safe_name = f"snapgen_prop{p.suffix.lower() if p.suffix else '.png'}"
        info = self._curl(
            "POST",
            f"{API_BASE}/api/3d/resource/genUploadInfo",
            self._signed_headers(),
            json.dumps({"fileName": safe_name}, ensure_ascii=False),
        )
        err = info.get("error")
        if isinstance(err, dict) and str(err.get("code", "0")) not in ("", "0"):
            raise Hunyuan3DError(f"Upload info failed: {json.dumps(info, ensure_ascii=False)[:500]}")
        resource_url = info.get("resourceUrl")
        if not resource_url:
            raise Hunyuan3DError(f"No resourceUrl in upload info: {json.dumps(info, ensure_ascii=False)[:500]}")
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except Exception as e:
            raise Hunyuan3DError(f"qcloud_cos missing: {e}") from e
        config = CosConfig(
            Region=info["region"],
            SecretId=info["encryptTmpSecretId"],
            SecretKey=info["encryptTmpSecretKey"],
            Token=info["encryptToken"],
            Scheme="https",
        )
        cos = CosS3Client(config)
        content_type = mimetypes.guess_type(str(p))[0] or "image/png"
        # A prop image is small enough for one direct request.  The SDK's
        # upload_file helper starts its multipart worker machinery and can
        # terminate silently when called from SnapGen's background thread.
        # put_object follows the same upload flow used by the web page without
        # spawning workers.
        with p.open("rb") as image_file:
            cos.put_object(
                Bucket=info["bucketName"],
                Body=image_file,
                Key=info["location"],
                EnableMD5=False,
                ContentType=content_type,
            )
        return resource_url

    # ── image_to_3d ─────────────────────────────────────────────

    def image_to_3d(
        self,
        image_url: str,
        model_type: str = "image2ModelV3.1",
        enable_pbr: bool = True,
        face_count: int = 50000,
    ) -> str:
        """Convert a local image or image URL to a 3D model.

        Local files are uploaded through Hunyuan's resource API first.  Keeping
        this conversion here gives every caller (GUI and CLI) the same flow.

        Args:
            image_url: Local image path or an HTTP(S) image URL.
            model_type: "image2ModelV3.1" (default) or "image2ModelV2.0".
            enable_pbr: Generate PBR textures.
            face_count: Target face count (50000 default).
        """
        image_source = str(image_url or "").strip()
        if not image_source:
            raise Hunyuan3DError("Image path/URL is empty")
        if not image_source.lower().startswith(("http://", "https://")):
            image_source = self.upload_image(image_source)

        query = sign_request()
        body = json.dumps({
            "sceneType": "playGround3D-2.0",
            "count": 1,
            "modelType": model_type,
            "title": "",
            "style": "",
            "imageList": [image_source],
            "enable_pbr": enable_pbr,
            "enableLowPoly": False,
            "faceCount": face_count,
        }, ensure_ascii=False)

        result = self._curl(
            "POST",
            f"{API_BASE}/api/3d/creations/generations?{query}",
            self._signed_headers(),
            body,
        )
        task_id = result.get("creationsId")
        if not task_id:
            raise Hunyuan3DError(f"No creationsId: {json.dumps(result, ensure_ascii=False)[:300]}")
        return task_id

    # ── text_to_3d ──────────────────────────────────────────────

    def text_to_3d(
        self,
        prompt: str,
        model_type: str = "text2ModelV3.1",
        enable_pbr: bool = True,
        face_count: int = 50000,
    ) -> str:
        """Convert text to 3D model. Returns creationsId.

        NOTE: text2Model may not be available on all accounts.
        Use text2image() + image_to_3d() as fallback.
        """
        query = sign_request()
        body = json.dumps({
            "sceneType": "playGround3D-2.0",
            "count": 1,
            "modelType": model_type,
            "title": "",
            "style": "",
            "prompt": prompt,
            "enable_pbr": enable_pbr,
            "enableLowPoly": False,
            "faceCount": face_count,
        }, ensure_ascii=False)

        result = self._curl(
            "POST",
            f"{API_BASE}/api/3d/creations/generations?{query}",
            self._signed_headers(),
            body,
        )
        task_id = result.get("creationsId")
        if not task_id:
            raise Hunyuan3DError(f"No creationsId: {json.dumps(result, ensure_ascii=False)[:300]}")
        return task_id

    # ── status / polling ────────────────────────────────────────

    def convert_format(self, source_url: str, source_format: str = "zip", target_format: str = "fbx") -> str:
        """Convert a generated model to another format via resourceConvert API.
        
        Returns download URL of the converted file.
        """
        body = json.dumps({
            "sourceResource": [{"format": source_format, "url": source_url}],
            "targetFormatList": [target_format],
        }, ensure_ascii=False)
        # resourceConvert does NOT use signed query — uses cookie auth like browser
        cookie_str = _cookie_header(self.cookie_file)
        headers = [
            "-H", "accept: application/json, text/plain, */*",
            "-H", "Content-Type: application/json",
            "-H", f"Referer: {API_BASE}/assets",
            "-H", f"Origin: {API_BASE}",
            "-H", f"Cookie: {cookie_str}",
            "-H", "sec-ch-ua-platform: Windows",
            "-H", "sec-ch-ua: \"Not;A=Brand\";v=\"8\", \"Chromium\";v=\"150\", \"Google Chrome\";v=\"150\"",
            "-H", "sec-ch-ua-mobile: ?0",
            "-H", "x_hunyuan_inner_user_id: dc3af0e7f3814613b5a3f7068b3b91f7",
            "-H", "x-product: hunyuan3d",
            "-H", "x-source: web",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        ]
        result = self._curl(
            "POST",
            f"{API_BASE}/api/3d/creations/resourceConvert",
            headers,
            data=body,
            timeout=120,
        )
        # Try direct URL from response (synchronous conversion)
        # Response shape: {"convertResult": [{"format": "fbx", "url": "..."}]}
        direct_url = ""
        cr = result.get("convertResult") or []
        if cr and isinstance(cr, list):
            for item in cr:
                if item.get("format") == target_format:
                    direct_url = item.get("url") or ""
                    if direct_url:
                        break
        if not direct_url:
            direct_url = (result.get("url") or 
                          result.get("data", {}).get("url") or
                          result.get("result", [{}])[0].get("url") or
                          result.get("result", [{}])[0].get("urlResult", {}).get(target_format) or
                          "")
        if direct_url:
            return direct_url
        # Try creationsId (async task) — poll via detail endpoint
        cid = (result.get("creationsId") or 
               result.get("data", {}).get("creationsId") or 
               result.get("result", [{}])[0].get("creationsId") or 
               "")
        if cid:
            status = self.wait_for_task(cid, timeout=300)
            fbx_url = self._find_fbx_url(status)
            if fbx_url:
                return fbx_url
        raise Hunyuan3DError(f"convert_format: no result in response: {json.dumps(result, ensure_ascii=False)[:500]}")

    def get_task_status(self, creations_id: str) -> dict[str, Any]:
        """Get task status by creationsId."""
        query = sign_request()
        result = self._curl(
            "GET",
            f"{API_BASE}/api/3d/creations/detail?creationsId={creations_id}&{query}",
            self._signed_headers(),
        )
        return result

    def wait_for_task(self, creations_id: str, timeout: int = 600) -> dict[str, Any]:
        """Poll until task completes. Returns full status dict with download URLs."""
        start = time.time()
        while True:
            status = self.get_task_status(creations_id)
            state = status.get("status", "")
            if state == "success":
                return status
            if state == "failed":
                raise Hunyuan3DError(f"Task failed: {json.dumps(status, ensure_ascii=False)[:500]}")
            if time.time() - start > timeout:
                raise Hunyuan3DError(f"Task timed out after {timeout}s: {state}")
            time.sleep(5)

    # ── download ────────────────────────────────────────────────

    def download_result(
        self,
        status: dict[str, Any],
        out_dir: str,
        formats: list[str] | None = None,
    ) -> dict[str, Any]:
        """Download model files from task result. Returns {format: local_path} dict.

        Downloads OBJ (with MTL + textures) and GLB if available.
        Auto-extracts .zip archives.
        If formats includes "fbx", auto-converts via resourceConvert API.
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        downloaded: dict[str, Any] = {}

        results = status.get("result", [])
        for res in results:
            url_result = res.get("urlResult", {})

            # OBJ (zip archive)
            obj_url = url_result.get("obj")
            if obj_url:
                obj_zip = out / "model_obj.zip"
                self._download_file(obj_url, obj_zip)
                # Extract
                extract_dir = out / "obj"
                extract_dir.mkdir(exist_ok=True)
                with zipfile.ZipFile(obj_zip, "r") as zf:
                    zf.extractall(extract_dir)
                obj_zip.unlink()  # clean up zip
                downloaded["obj"] = str(extract_dir)

            # GLB
            glb_url = url_result.get("glb")
            if glb_url:
                glb_path = out / "model.glb"
                self._download_file(glb_url, glb_path)
                downloaded["glb"] = str(glb_path)

            # FBX — API may return it directly
            fbx_url = url_result.get("fbx")
            if fbx_url:
                fbx_path = out / "model.fbx"
                self._download_file(fbx_url, fbx_path)
                downloaded["fbx"] = str(fbx_path)
            # If not in result, try resourceConvert API
            elif formats and "fbx" in formats:
                fbx_src = obj_url or glb_url
                fbx_fmt = "zip" if obj_url else "glb"
                if fbx_src:
                    try:
                        fbx_url = self.convert_format(fbx_src, source_format=fbx_fmt, target_format="fbx")
                        fbx_path = out / "model.fbx"
                        self._download_file(fbx_url, fbx_path)
                        downloaded["fbx"] = str(fbx_path)
                    except Exception as e:
                        print(f"[Hunyuan3D] FBX fallback conversion failed: {e}")

            # Preview image
            img_url = url_result.get("image") or url_result.get("png")
            if img_url:
                img_path = out / "model.png"
                self._download_file(img_url, img_path)
                downloaded["image"] = str(img_path)

        if not downloaded:
            raise Hunyuan3DError(f"No download URLs in result: {json.dumps(status, ensure_ascii=False)[:500]}")

        return downloaded

    def _find_fbx_url(self, status: dict[str, Any]) -> str | None:
        """Extract FBX download URL from conversion task result."""
        for res in status.get("result", []):
            url_result = res.get("urlResult", {})
            fbx = url_result.get("fbx")
            if fbx:
                return fbx
            # Sometimes nested under other keys
            for v in url_result.values():
                if isinstance(v, str) and v.endswith(".fbx"):
                    return v
        return None

    def _download_file(self, url: str, dest: Path):
        """Download a file via curl."""
        cmd = ["curl", "-s", "-L", "-o", str(dest), url, "--max-time", "300"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=310)
        if r.returncode != 0 or not dest.exists():
            raise Hunyuan3DError(f"Download failed: {url[:100]}")


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Hunyuan 3D CLI")
    ap.add_argument("--cookie", default=str(Path(__file__).resolve().parent.parent / "snapgen_data" / "hunyuan_cookies.txt"), help="Netscape cookie file")
    ap.add_argument("--out", "-o", default="hunyuan_output", help="Output directory")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("text2image")
    p.add_argument("prompt")

    p = sub.add_parser("image2model")
    p.add_argument("image", help="Image URL or local path")

    p = sub.add_parser("text2model")
    p.add_argument("prompt")

    p = sub.add_parser("status")
    p.add_argument("creations_id")

    args = ap.parse_args()
    client = Hunyuan3DClient(args.cookie)
    out_dir = args.out

    if args.cmd == "text2image":
        url = client.text2image(args.prompt)
        print(f"Image URL: {url}")
        out = Path(out_dir)
        out.mkdir(exist_ok=True)
        r = subprocess.run(["curl", "-s", "-L", "-o", str(out / "scene.png"), url, "--max-time", "120"], timeout=130)
        print(f"Saved to {out / 'scene.png'}")

    elif args.cmd == "image2model":
        image_source = args.image
        if not image_source.lower().startswith(("http://", "https://")):
            image_source = client.upload_image(image_source)
        task_id = client.image_to_3d(image_source)
        print(f"Task ID: {task_id}")
        status = client.wait_for_task(task_id, timeout=600)
        files = client.download_result(status, out_dir)
        print(f"Done: {json.dumps({k: str(v) for k, v in files.items()}, ensure_ascii=False)}")

    elif args.cmd == "text2model":
        task_id = client.text_to_3d(args.prompt)
        print(f"Task ID: {task_id}")
        status = client.wait_for_task(task_id, timeout=600)
        files = client.download_result(status, out_dir)
        print(f"Done: {json.dumps({k: str(v) for k, v in files.items()}, ensure_ascii=False)}")

    elif args.cmd == "status":
        s = client.get_task_status(args.creations_id)
        print(json.dumps(s, ensure_ascii=False, indent=2))
