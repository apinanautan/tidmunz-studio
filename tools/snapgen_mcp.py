"""Small MCP bridge for inspecting Snapgen without driving the GUI."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP



ROOT = Path(__file__).resolve().parents[1]
mcp = FastMCP("Snapgen")


@mcp.tool()
def project_overview() -> str:
    """Return safe, high-level information about the Snapgen project."""
    files = sorted(
        p.name
        for p in ROOT.iterdir()
        if p.is_file() and p.suffix in {".py", ".md", ".json"}
    )
    return json.dumps(
        {
            "name": "Snapgen",
            "type": "Python Tkinter desktop app",
            "main_file": "snapgen_gui_v2.py",
            "files": files,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def read_project_state() -> str:
    """Read the project's short, user-maintained state summary."""
    state = ROOT / "STATE.md"
    if not state.exists():
        return "ยังไม่มี STATE.md ในโปรเจกต์"
    return state.read_text(encoding="utf-8")


@mcp.tool()
def list_outputs(limit: int = 20) -> str:
    """List recent generated files without opening or exposing their contents."""
    output_dir = ROOT / "test_outputs"
    if not output_dir.exists():
        return "ยังไม่มีโฟลเดอร์ test_outputs"
    items = sorted(
        (
            {"name": p.name, "size": p.stat().st_size}
            for p in output_dir.iterdir()
            if p.is_file()
        ),
        key=lambda item: item["name"],
        reverse=True,
    )[: max(1, min(limit, 100))]
    return json.dumps(items, ensure_ascii=False, indent=2)


@mcp.tool()
def create_image(prompt: str, aspect_ratio: str = "1:1", name_hint: str = "voice_image") -> str:
    """Queue an image in the running Snapgen GUI, using its real create flow."""
    queue = ROOT / "snapgen_data" / "mcp_image_queue.json"
    queue.write_text(json.dumps({"prompt": prompt, "aspect_ratio": aspect_ratio,
                                 "name_hint": name_hint}, ensure_ascii=False), encoding="utf-8")
    return "ส่งคำสั่งเข้า Snapgen แล้ว รอให้หน้าต่างประมวลผลคิวสร้างภาพ"


@mcp.tool()
def create_story_face(name: str, variant: str = "") -> str:
    """Use Story Face's existing Select + Create Face flow for one character."""
    queue = ROOT / "snapgen_data" / "mcp_image_queue.json"
    queue.write_text(json.dumps({"kind": "story_face", "name": name, "variant": variant}, ensure_ascii=False), encoding="utf-8")
    return "ส่งคำสั่งเลือกตัวละครและสร้าง Face เข้า Snapgen แล้ว"


@mcp.tool()
def create_selected_story_face() -> str:
    """Press Story Face's existing Create Face action for the current selection."""
    queue = ROOT / "snapgen_data" / "mcp_image_queue.json"
    queue.write_text(json.dumps({"kind": "story_face_current"}, ensure_ascii=False), encoding="utf-8")
    return "ส่งคำสั่งสร้าง Face ของตัวละครที่เลือกอยู่เข้า Snapgen แล้ว"


if __name__ == "__main__":
    mcp.run()
