"""Seedance Lower contract used by SnapGen's Bytedance video page.

Verified against https://snapgen.ai/app/video-gen/bytedance (2026-10-19).
The web client submits these model IDs to /video-gen/seedance and uses
ref_images for reference uploads, rather than Grok's files field.
"""
from pathlib import Path
import json

MODELS = {
    "sd-2-lower": "Seedance 2.0 - Lower",
    "sd-2-5-lower": "Seedance 2.5 - Lower",
}
API_URL = "https://api.snapgen.ai/uapi/v1/video-gen/seedance"
DURATIONS = {"sd-2-lower": ("5", "10", "15"), "sd-2-5-lower": ("5", "10", "30")}
ASPECTS = ("16:9", "9:16", "1:1", "3:4", "4:3", "21:9")
ROLES = ("ตัวละคร", "สถานที่", "สิ่งของ")


def auto_clip_settings(model, scene):
    slow = bool(scene.get("clip_slow")) and not bool(scene.get("dialogue"))
    needed = float(scene.get("shot_seconds") or scene.get("clip_seconds") or 5) / (2 if slow else 1)
    duration = next((value for value in DURATIONS[model] if int(value) >= needed), None)
    if duration is None:
        raise ValueError(f"ช็อตยาวเกินที่ {MODELS[model]} รองรับ กรุณาแบ่งช็อตก่อนสร้าง")
    return model, int(duration), slow


def scene_references(scene, candidates):
    """Only refs explicitly named as this shot's characters/location are eligible."""
    roles = {str(name).strip().casefold(): "ตัวละคร" for name in scene.get("characters") or []}
    location = str(scene.get("location") or "").strip()
    if location:
        roles[location.casefold()] = "สถานที่"
    refs = []
    for name, path in candidates:
        role = roles.get(str(name).strip().casefold())
        if role:
            refs.append({"name": name, "path": str(path), "role": role})
    found = {str(ref["name"]).strip().casefold() for ref in refs}
    missing = [name for name, role in roles.items() if role == "ตัวละคร" and name not in found]
    if missing:
        raise ValueError("ไม่พบ Ref ตัวละครของช็อตนี้: " + ", ".join(missing)
                         + " — ใส่รูปชื่อเดียวกับตัวละครในโฟลเดอร์ไฟล์แนบ Image AI")
    # Check the same capacity/order rules before a paid job is submitted.
    reference_prompt("", scene["image"], refs)
    return refs


def normalize_settings(resolution, duration, aspect, model="sd-2-lower"):
    aspect = {"landscape": "16:9", "horizontal": "16:9", "portrait": "9:16",
              "vertical": "9:16", "square": "1:1"}.get(str(aspect), str(aspect))
    durations = DURATIONS[model]
    return ("720p", str(duration) if str(duration) in durations else durations[0],
            aspect if aspect in ASPECTS else ASPECTS[0])


def reference_prompt(prompt, image, references=()):
    """Upload order and @image roles are built together; never silently drop refs."""
    entries = [(Path(str(image)), "รูปฉากหลัก: องค์ประกอบ ตำแหน่ง และการจัดฉาก")]
    seen = {str(entries[0][0].resolve()).casefold()}
    for ref in references:
        role = ref.get("role")
        if role not in ROLES:
            raise ValueError("กรุณาระบุประเภท Ref: ตัวละคร สถานที่ หรือสิ่งของ")
        path = Path(str(ref.get("path") or ""))
        key = str(path.resolve()).casefold()
        if key not in seen:
            entries.append((path, f"Ref {role}: {ref.get('name') or path.stem}"))
            seen.add(key)
    if len(entries) > 4:
        raise ValueError("Seedance Lower แนบได้รวม 4 รูป: รูปฉาก 1 + Ref อีกไม่เกิน 3 รูป กรุณาเลือกเฉพาะที่อยู่ในช็อต")
    for path, _ in entries:
        if not path.is_file():
            raise RuntimeError("ไม่พบรูปอ้างอิง Seedance: " + path.name)
    labels = [f"@image{i}: {label}" for i, (_, label) in enumerate(entries, 1)]
    instructions = ("Reference roles (follow these exact image numbers):\n" + "\n".join(labels)
                    + "\nUse character refs only for the named character's face, hair and outfit. "
                    "Use location refs only for the environment; do not copy people from them. "
                    "Preserve the main scene composition. Do not render the reference sheets, labels or a collage.\n\n")
    return instructions + str(prompt or "").strip(), [path for path, _ in entries]


def build_command(api_key, model, prompt, image, resolution, duration, aspect, references=()):
    if model not in MODELS:
        raise ValueError("Unknown Seedance Lower model")
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError("กรุณาใส่ SnapGen API key ในหน้าตั้งค่าก่อน")
    source = Path(str(image or "").strip())
    if not source.is_file():
        raise RuntimeError("ไม่พบรูปเริ่มต้นสำหรับ Seedance Lower")
    resolution, duration, aspect = normalize_settings(resolution, duration, aspect, model)
    prompt, paths = reference_prompt(prompt, image, references)
    command = [
        "curl", "-sS", "--max-time", "60", "-X", "POST", API_URL,
        "-H", "x-api-key: " + key,
        "--form-string", "prompt=" + str(prompt or "").strip(),
        "--form-string", "model=" + model,
        "--form-string", "resolution=" + resolution,
        "--form-string", "duration=" + duration,
        "--form-string", "aspect_ratio=" + aspect,
        "--form-string", "mode=lower",
        "--form-string", "mode_image=ingredient",
    ]
    for path in paths:
        command.extend(["--form", "ref_images=@" + str(path)])
    return command


class ReferenceStore:
    """Refs belong to a specific scene image, not whichever job last used a Slot."""
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    @staticmethod
    def key(image):
        return str(Path(str(image)).resolve()).casefold()

    def get(self, image):
        return [dict(ref) for ref in self.data.get(self.key(image), [])]

    def save(self, image, refs):
        reference_prompt("", image, refs)
        self.data[self.key(image)] = [dict(ref) for ref in refs]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def edit_references(root, store, image):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    if not image or not Path(image).is_file():
        messagebox.showerror("Seedance Ref", "เลือกรูปฉากใน Slot ก่อน", parent=root)
        return
    dialog = tk.Toplevel(root)
    dialog.title("Seedance — รูปฉากและ Ref")
    tk.Label(dialog, text="@image1 = รูปฉากใน Slot: " + Path(image).name).pack(anchor="w", padx=12, pady=8)
    tk.Label(dialog, text="เพิ่ม Ref ได้อีก 3 รูป เลือกประเภทให้ตรงกับรูป (รวมทั้งหมดไม่เกิน 4 รูป)").pack(padx=12)
    rows = tk.Frame(dialog)
    rows.pack(fill="x", padx=12, pady=8)
    refs = store.get(image)
    role_vars = []

    def redraw():
        for ref, var in zip(refs, role_vars):
            ref["role"] = var.get()
        for child in rows.winfo_children():
            child.destroy()
        role_vars.clear()
        for index, ref in enumerate(refs):
            row = tk.Frame(rows)
            row.pack(fill="x", pady=4)
            var = tk.StringVar(value=ref["role"])
            role_vars.append(var)
            ttk.Combobox(row, textvariable=var, values=ROLES, state="readonly", width=10).pack(side="left")
            tk.Label(row, text=f"@image{index + 2}: {ref.get('name') or Path(ref['path']).stem}").pack(side="left", padx=8)
            def remove(i=index):
                for item, variable in zip(refs, role_vars):
                    item["role"] = variable.get()
                refs.pop(i)
                role_vars.clear()
                redraw()
            tk.Button(row, text="ลบ", command=remove).pack(side="right")

    def add():
        paths = filedialog.askopenfilenames(parent=dialog, title="เลือก Ref ตัวละคร / สถานที่ / สิ่งของ",
                                           filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
        if len(refs) + len(paths) > 3:
            messagebox.showerror("Seedance Ref", "เพิ่ม Ref ได้อีกไม่เกิน 3 รูป", parent=dialog)
            return
        for ref, var in zip(refs, role_vars):
            ref["role"] = var.get()
        role_vars.clear()
        refs.extend({"path": path, "role": "ตัวละคร", "name": Path(path).stem} for path in paths)
        redraw()

    def save():
        for ref, var in zip(refs, role_vars):
            ref["role"] = var.get()
        try:
            store.save(image, refs)
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror("Seedance Ref", str(exc), parent=dialog)
            return
        dialog.destroy()
    redraw()
    tk.Button(dialog, text="เพิ่ม Ref", command=add).pack(side="left", padx=12, pady=12)
    tk.Button(dialog, text="บันทึก", command=save).pack(side="right", padx=12, pady=12)
