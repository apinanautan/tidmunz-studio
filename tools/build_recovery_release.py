"""Build one recovery release from a verified extracted release, with tiny overlays."""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path


base = Path(sys.argv[1]).resolve()
version = sys.argv[2]
bridge_patch = Path(sys.argv[3]).read_bytes()
output = Path(sys.argv[4]).resolve()

files = {
    path.relative_to(base).as_posix(): path.read_bytes()
    for path in base.rglob("*")
    if path.is_file() and path.name != "manifest.json"
}

gui_name = "snapgen_gui_v2.py"
gui = files[gui_name].decode("utf-8")
replacements = (
    ('for model in ("gpt-5-5", "gpt-4o-mini"):',
     'for model in ("auto", "auto"):'),
    ('def _prompt_ref_chat(messages, *, require_history=False, _repair_retry=True, model="gpt-5-5"):',
     'def _prompt_ref_chat(messages, *, require_history=False, _repair_retry=True, model="auto"):'),
    ('"model": str(model or "gpt-5-5"),',
     '"model": str(model or "auto"),'),
)
for old, new in replacements:
    if old in gui:
        gui = gui.replace(old, new, 1)
files[gui_name] = gui.encode("utf-8")
files["snapgen_modules/snapgen_bridge_cursor_patch.py"] = bridge_patch

version_data = {
    "version": version,
    "repository": "tidmunzsocial-lab/tidmunz-studio",
    "changelog": [
        "กู้คืนไฟล์โปรแกรมทั้งหมดจาก v5.0.18 หลัง v5.0.19 เผลอใช้ source เก่า",
        "Prompt-Ref Context ใช้ DOCX จริงและ model auto ตามบัญชีแต่ละเครื่อง",
        "Bridge อัปโหลดไฟล์แบบ retry และคืน Error รายขั้น",
    ],
}
version_bytes = (json.dumps(version_data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
for name in ("snapgen_data/meta/snapgen_version.json", "snapgen_version.json"):
    files[name] = version_bytes

manifest = {
    "version": version,
    "repository": "tidmunzsocial-lab/tidmunz-studio",
    "recovery_base": "5.0.18",
    "files": [
        {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for name, data in sorted(files.items())
    ],
}
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    for name, data in sorted(files.items()):
        archive.writestr(name, data)
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
print(output)
print("sha256=" + hashlib.sha256(output.read_bytes()).hexdigest())
