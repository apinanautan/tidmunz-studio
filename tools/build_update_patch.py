"""Build the public GitHub Release asset without user data or credentials."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_DATA = json.loads((ROOT / "snapgen_data" / "meta" / "snapgen_version.json").read_text(encoding="utf-8"))
VERSION = str(VERSION_DATA["version"])
OUTPUT = ROOT / "tools" / "release" / "tidmun-studio-patch.zip"

files = [
    ROOT / "snapgen_gui_v2.py",
    ROOT / "setup_and_run.bat",
    # Nested layout targets for clean installs / migrated machines.
    ROOT / "__pycache__" / "snapgen_core.cpython-312.pyc",
    ROOT / "docs" / "INSTALL_OTHER_MACHINE.md",
    ROOT / "กฏของโปรแกรม" / "BRIDGE_TEAM_ARCHITECTURE.md",
    ROOT / "กฏของโปรแกรม" / "PROGRAM_ARCHITECTURE_NOTES.md",
    ROOT / "snapgen_data" / "meta" / "snapgen_version.json",
    ROOT / "assets" / "video_forbidden_words.json",
    *sorted((ROOT / "snapgen_modules").glob("*.py")),
    *sorted((ROOT / "snapgen_modules" / "mobile").glob("*.*")),
]
files = [p for p in files if p.is_file() and not p.name.startswith("test_")]

# Keep nested paths in the patch so other machines install into the clean layout.
# Also include legacy root aliases for one release so old updaters still work.
ZIP_ALIASES = {
    "__pycache__/snapgen_core.cpython-312.pyc": [
        "__pycache__/snapgen_core.cpython-312.pyc",
        "snapgen_core.cpython-312.pyc",
    ],
    "docs/INSTALL_OTHER_MACHINE.md": [
        "docs/INSTALL_OTHER_MACHINE.md",
        "INSTALL_OTHER_MACHINE.md",
    ],
    "snapgen_data/meta/snapgen_version.json": [
        "snapgen_data/meta/snapgen_version.json",
        "snapgen_version.json",
    ],
}

manifest_files = []
entries = []
for path in files:
    rel = path.relative_to(ROOT).as_posix()
    names = ZIP_ALIASES.get(rel, [rel])
    data = path.read_bytes()
    for zip_name in names:
        entries.append((zip_name, data))
        manifest_files.append({
            "path": zip_name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        })

manifest = {
    "version": VERSION,
    "repository": "tidmunzsocial-lab/tidmunz-studio",
    "files": manifest_files,
}
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    for zip_name, data in entries:
        zf.writestr(zip_name, data)

with zipfile.ZipFile(OUTPUT) as zf:
    assert zf.testzip() is None
print(OUTPUT)
print(f"version={VERSION} files={len(entries)} sha256={hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}")
