"""Preserve streamed provider errors instead of misreporting a missing story."""
from pathlib import Path
import py_compile


PREVIEW_OLD = '''    try:
        text = response.text or ""
    except Exception:
        return ""
'''
PREVIEW_NEW = '''    try:
        text = response.text or ""
        # SnapGen: error bodies are not buffered when stream=True.
        if not text and getattr(response, "status_code", 0) >= 400:
            data = bytearray()
            try:
                for chunk in response.iter_content():
                    data.extend(chunk[:4096 - len(data)])
                    if len(data) >= 4096:
                        break
                text = bytes(data).decode("utf-8", errors="replace")
            finally:
                response.close()
    except Exception:
        return ""
'''


def patch_transport(source):
    if PREVIEW_NEW in source:
        return source
    if PREVIEW_OLD not in source:
        raise RuntimeError("Bridge error preview structure changed")
    return source.replace(PREVIEW_OLD, PREVIEW_NEW, 1)


def patch_classifier(source):
    replacements = (
        ('if provider_status == 404 and "conversation failed" in normalized:',
         'if provider_status == 404 and "conversation failed" in normalized and "model not found" not in normalized:'),
        ('("not valid", "invalid model", "unsupported model")',
         '("not valid", "invalid model", "unsupported model", "model not found")'),
    )
    for old, new in replacements:
        if new in source:
            continue
        if old not in source:
            raise RuntimeError("Bridge provider error classifier structure changed")
        source = source.replace(old, new, 1)
    return source


def install(bridge_dir, log=print):
    root = Path(bridge_dir) / "chatgpt_api"
    changed = False
    for path, patch in (
        (root / "providers" / "chatgpt" / "transport.py", patch_transport),
        (root / "api" / "openai_compat.py", patch_classifier),
    ):
        source = path.read_text(encoding="utf-8")
        updated = patch(source)
        if updated == source:
            continue
        temp = path.with_suffix(path.suffix + ".web-error.tmp")
        try:
            temp.write_text(updated, encoding="utf-8")
            py_compile.compile(str(temp), doraise=True)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        changed = True
    if changed:
        log("✓ Bridge อ่านรายละเอียด model error และไม่ตีความเป็นประวัติหายแล้ว")
    return changed
