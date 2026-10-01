"""Build v5.0.22 from verified v5.0.21 with complete cursor helper patch."""
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
module_name = "snapgen_modules/snapgen_bridge_cursor_patch.py"
text = files[module_name].decode("utf-8")

text = text.replace("CAPABILITY_VERSION = 3", "CAPABILITY_VERSION = 4", 1)
text = text.replace(
    '        "robust_file_upload": True,\n',
    '        "robust_file_upload": True,\n        "cursor_message_id_helper": True,\n',
    1,
)

old_probe = '''        "from chatgpt_api.api.openai_compat import _openai_content_to_provider_parts as c; "
        f"raw=base64.b64decode('{sample}'); "
        f"p=c([{{'type':'input_file','file_data':'{sample}',"
'''
new_probe = '''        "from chatgpt_api.api.openai_compat import _openai_content_to_provider_parts as c, _latest_message_id_from_value as mid; "
        f"raw=base64.b64decode('{sample}'); "
        f"p=c([{{'type':'input_file','file_data':'{sample}',"
'''
if text.count(old_probe) != 1:
    raise RuntimeError("runtime probe import marker mismatch")
text = text.replace(old_probe, new_probe, 1)
old_assert = '''        "assert len(p)==1 and p[0].kind=='file_bytes' and p[0].data==raw and p[0].name=='snapgen_probe.docx'; "
        "print('DOCX_BRIDGE_OK')"
'''
new_assert = '''        "assert len(p)==1 and p[0].kind=='file_bytes' and p[0].data==raw and p[0].name=='snapgen_probe.docx'; "
        "assert mid([{'message':{'id':'cursor-probe'}}])=='cursor-probe'; "
        "print('DOCX_BRIDGE_OK CURSOR_HELPER_OK')"
'''
if text.count(old_assert) != 1:
    raise RuntimeError("runtime probe assertion marker mismatch")
text = text.replace(old_assert, new_assert, 1)
text = text.replace(
    'return result.returncode == 0 and "DOCX_BRIDGE_OK" in output, output.strip()[-1200:]',
    'return result.returncode == 0 and "DOCX_BRIDGE_OK" in output and "CURSOR_HELPER_OK" in output, output.strip()[-1200:]',
    1,
)

cursor_helper_installer = r'''

def _install_cursor_message_id_helper(bridge_dir, log) -> bool:
    """Install helper in openai_compat.py, where cursor callbacks call it."""
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
    if not path.is_file():
        raise RuntimeError(f"ไม่พบ Bridge source: {path}")
    source = path.read_text(encoding="utf-8")
    if "def _latest_message_id_from_value(" in source:
        return False
    anchor = "\n\nasync def _collect_text(provider: ChatGPTProvider, request: ChatRequest, operation_id: str | None = None) -> str:\n"
    helper = (
        "\n\ndef _latest_message_id_from_value(value: Any) -> str | None:\n"
        "    found: list[str] = []\n\n"
        "    def walk(item: Any) -> None:\n"
        "        if isinstance(item, dict):\n"
        "            message = item.get(\"message\")\n"
        "            if isinstance(message, dict) and isinstance(message.get(\"id\"), str):\n"
        "                found.append(message[\"id\"])\n"
        "            direct_id = item.get(\"message_id\")\n"
        "            if isinstance(direct_id, str):\n"
        "                found.append(direct_id)\n"
        "            for nested in item.values():\n"
        "                walk(nested)\n"
        "        elif isinstance(item, list):\n"
        "            for nested in item:\n"
        "                walk(nested)\n\n"
        "    walk(value)\n"
        "    return found[-1] if found else None\n"
    )
    if anchor not in source:
        raise RuntimeError("ไม่พบตำแหน่งเพิ่ม cursor helper ใน Bridge")
    source = source.replace(anchor, helper + anchor, 1)
    temp = path.with_suffix(path.suffix + ".cursor-helper.tmp")
    try:
        temp.write_text(source, encoding="utf-8")
        py_compile.compile(str(temp), doraise=True)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    log("✓ Bridge รองรับ parent message cursor ครบแล้ว")
    return True
'''
anchor = "\ndef cursor_supported(bridge_dir) -> bool:\n"
if text.count(anchor) != 1:
    raise RuntimeError("cursor_supported anchor mismatch")
text = text.replace(anchor, cursor_helper_installer + anchor, 1)

old_install = '''    file_changed = _install_file_upload(bridge_dir, log)
    robust_changed = _install_robust_upload(bridge_dir, log)
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
'''
new_install = '''    file_changed = _install_file_upload(bridge_dir, log)
    robust_changed = _install_robust_upload(bridge_dir, log)
    helper_changed = _install_cursor_message_id_helper(bridge_dir, log)
    path = Path(bridge_dir) / "chatgpt_api" / "api" / "openai_compat.py"
'''
if text.count(old_install) != 1:
    raise RuntimeError("install marker mismatch")
text = text.replace(old_install, new_install, 1)
text = text.replace(
    "return file_changed or robust_changed",
    "return file_changed or robust_changed or helper_changed",
    1,
)
files[module_name] = text.encode("utf-8")

version_data = {
    "version": version,
    "repository": "tidmunzsocial-lab/tidmunz-studio",
    "changelog": [
        "แก้ NameError _latest_message_id_from_value ใน Prompt-Ref Context",
        "ติดตั้ง cursor helper ใน openai_compat.py ก่อนตรวจว่า Bridge patch พร้อม",
        "runtime probe ทดสอบทั้ง DOCX และ parent message cursor จริง",
    ],
}
version_bytes = (json.dumps(version_data, ensure_ascii=False, indent=2) + "\n").encode()
for n in ("snapgen_data/meta/snapgen_version.json", "snapgen_version.json"):
    files[n] = version_bytes

manifest = {
    "version": version,
    "repository": "tidmunzsocial-lab/tidmunz-studio",
    "recovery_base": "5.0.21",
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
