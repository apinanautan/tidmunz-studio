"""Build v5.0.21 from verified v5.0.20 and preserve every unrelated byte."""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path


base = Path(sys.argv[1]).resolve()
version = sys.argv[2]
output = Path(sys.argv[3]).resolve()
files = {
    p.relative_to(base).as_posix(): p.read_bytes()
    for p in base.rglob("*") if p.is_file() and p.name != "manifest.json"
}
name = "snapgen_gui_v2.py"
text = files[name].decode("utf-8")
old = '''        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            # Image generation uses another Bridge path and may still work.
            # Normal chat may fail while image generation still works, commonly
            # from an old/crashed team-PC Bridge. Repair once and retry safely.
            # Prompt-Ref context does not spend image quota, so one automatic
            # repair + retry is safe. Never copy this retry into image jobs.
            transport_error = str(exc)
            recoverable = not isinstance(exc, urllib.error.HTTPError) or exc.code >= 500
            if not (_repair_retry and recoverable and _repair_prompt_ref_bridge_once()):
                raise RuntimeError(f"Prompt-Ref Bridge ติดต่อไม่ได้: {transport_error}") from exc
            return _prompt_ref_chat(messages, require_history=require_history, _repair_retry=False, model=model)
'''
new = '''        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            # HTTPError carries the real Bridge/GPT failure in its response
            # body. Reading it here prevents every distinct failure from being
            # flattened to only "502 Bad Gateway" in team-PC logs.
            transport_error = str(exc)
            bridge_error_body = ""
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    bridge_error_body = exc.read().decode("utf-8", errors="replace").strip()
                except Exception:
                    bridge_error_body = ""
                if bridge_error_body:
                    try:
                        error_payload = json.loads(bridge_error_body)
                        error_info = error_payload.get("error") if isinstance(error_payload, dict) else None
                        if isinstance(error_info, dict):
                            fields = [str(error_info.get("message") or "").strip()]
                            for key in ("code", "type", "provider_status"):
                                value = error_info.get(key)
                                if value not in (None, ""):
                                    fields.append(f"{key}={value}")
                            bridge_error_body = " | ".join(value for value in fields if value)
                    except Exception:
                        pass
                    transport_error += " | Bridge detail: " + bridge_error_body[:4000]
                # Bridge returned structured JSON, so its process is alive.
                # Rebuilding cannot fix account/model/storage provider errors.
                if bridge_error_body:
                    raise RuntimeError(f"Prompt-Ref Bridge/GPT ทำงานไม่สำเร็จ: {transport_error}") from exc
            recoverable = not isinstance(exc, urllib.error.HTTPError) or exc.code >= 500
            if not (_repair_retry and recoverable and _repair_prompt_ref_bridge_once()):
                raise RuntimeError(f"Prompt-Ref Bridge ติดต่อไม่ได้: {transport_error}") from exc
            return _prompt_ref_chat(messages, require_history=require_history, _repair_retry=False, model=model)
'''
if text.count(old) != 1:
    raise RuntimeError("v5.0.20 HTTPError marker mismatch")
files[name] = text.replace(old, new, 1).encode("utf-8")

version_data = {
    "version": version,
    "repository": "tidmunzsocial-lab/tidmunz-studio",
    "changelog": [
        "แสดงสาเหตุจริงใน response body ของ Prompt-Ref HTTP 502",
        "Log ระบุ message, code, type และ provider status แทนคำว่า Bad Gateway อย่างเดียว",
        "ไม่ rebuild Bridge ซ้ำเมื่อ Bridge ตอบ Error JSON ได้ตามปกติ",
    ],
}
version_bytes = (json.dumps(version_data, ensure_ascii=False, indent=2) + "\n").encode()
for version_name in ("snapgen_data/meta/snapgen_version.json", "snapgen_version.json"):
    files[version_name] = version_bytes

manifest = {
    "version": version,
    "repository": "tidmunzsocial-lab/tidmunz-studio",
    "recovery_base": "5.0.20",
    "files": [
        {"path": n, "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)}
        for n, d in sorted(files.items())
    ],
}
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    for n, d in sorted(files.items()):
        z.writestr(n, d)
with zipfile.ZipFile(output) as z:
    assert z.testzip() is None
print(output)
print("sha256=" + hashlib.sha256(output.read_bytes()).hexdigest())
