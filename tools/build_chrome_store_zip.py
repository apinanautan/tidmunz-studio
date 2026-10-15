"""Zip the "Tidmunz ChatGPT" extension for the Chrome Web Store (no config.js: it pairs with Tidmunz itself)."""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "snapgen_modules" / "chrome_ext"
FILES = ["manifest.json", "background.js", "icon16.png", "icon48.png", "icon128.png"]

version = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))["version"]
output = ROOT / "tools" / "release" / f"tidmunz-chatgpt-extension-{version}.zip"
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in FILES:
        archive.write(SOURCE / name, name)
print(output)
