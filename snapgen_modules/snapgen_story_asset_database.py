# -*- coding: utf-8 -*-
"""Browse the shared Character Creator asset catalog inside นิทาน."""
from __future__ import annotations

import random
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk


_CATEGORIES = ("ทั้งหมด", "ทรงผม", "เสื้อ/ชุด", "กางเกง/ท่อนล่าง", "รองเท้า", "เครื่องประดับ", "อื่น ๆ")
_GENDERS = ("ทั้งหมด", "ชาย", "หญิง")
_AGES = ("ทุกวัย", "เด็ก", "หนุ่มสาว", "วัยกลางคน", "ผู้สูงอายุ")


def _category(item):
    part = str(item.get("part") or "").casefold()
    kind = str(item.get("kind") or "").casefold()
    if part == "hair" or kind == "hair":
        return "ทรงผม"
    if part in {"upper", "full", "outer"}:
        return "เสื้อ/ชุด"
    if part == "lower":
        return "กางเกง/ท่อนล่าง"
    if part == "shoes" or kind == "shoes":
        return "รองเท้า"
    if part in {"jewelry", "hat", "beard", "brows", "bag"} or kind in {"accessory", "gloves"}:
        return "เครื่องประดับ"
    return "อื่น ๆ"


def _matches_gender(value, wanted):
    text = str(value or "").casefold()
    if wanted == "ชาย":
        return any(token in text for token in ("ชาย", "male", "ทั้งคู่", "ได้ทั้ง"))
    if wanted == "หญิง":
        return any(token in text for token in ("หญิง", "female", "ทั้งคู่", "ได้ทั้ง"))
    return True


def _matches_age(value, wanted):
    if wanted == "ทุกวัย":
        return True
    text = str(value or "").casefold()
    tokens = {
        "เด็ก": ("เด็ก", "child", "ทุกวัย"),
        "หนุ่มสาว": ("หนุ่ม", "สาว", "adult", "young", "ทุกวัย"),
        "วัยกลางคน": ("กลางคน", "middle", "adult", "ทุกวัย"),
        "ผู้สูงอายุ": ("สูงอายุ", "แก่", "old", "ทุกวัย"),
    }.get(wanted, ())
    return any(token in text for token in tokens)


def _is_classified(item):
    if item.get("kind") == "hair":
        return item.get("review_status") == "curated" and bool(item.get("groups"))
    return bool(
        item.get("review_status") == "curated"
        and item.get("name_th") and item.get("part")
        and item.get("gender") and item.get("age")
    )


def _review_label(item):
    return {
        "curated": "ผ่านคัด",
        "rejected": "คัดออก",
        "needs_classification": "รอตรวจ",
    }.get(item.get("review_status"), "รอตรวจ")


def install(g: dict, parent: tk.Misc):
    """Create the catalog page and return a refresh callback."""
    base = Path(g.get("BASE", Path.cwd() / "snapgen_data"))
    base_root = Path(g.get("BASE_ROOT") or base.parent)
    catalog_path = base_root / "cc5_catalog" / "catalog.json"
    legacy_hair_pool = base / "cc5_hair_pool.json"

    page = parent
    toolbar = tk.Frame(page, bg="#FAFAF7")
    toolbar.pack(fill="x", padx=12, pady=(12, 6))

    category_var = tk.StringVar(value="ทั้งหมด")
    gender_var = tk.StringVar(value="ทั้งหมด")
    age_var = tk.StringVar(value="ทุกวัย")
    search_var = tk.StringVar(value="")
    status_var = tk.StringVar(value="")

    tk.Label(toolbar, text="ประเภท", bg="#FAFAF7").pack(side="left")
    category_box = ttk.Combobox(toolbar, textvariable=category_var, values=_CATEGORIES, state="readonly", width=16)
    category_box.pack(side="left", padx=(4, 10))
    tk.Label(toolbar, text="เพศ", bg="#FAFAF7").pack(side="left")
    gender_box = ttk.Combobox(toolbar, textvariable=gender_var, values=_GENDERS, state="readonly", width=9)
    gender_box.pack(side="left", padx=(4, 10))
    tk.Label(toolbar, text="วัย", bg="#FAFAF7").pack(side="left")
    age_box = ttk.Combobox(toolbar, textvariable=age_var, values=_AGES, state="readonly", width=13)
    age_box.pack(side="left", padx=(4, 10))
    tk.Label(toolbar, text="ค้นหา", bg="#FAFAF7").pack(side="left")
    search_entry = ttk.Entry(toolbar, textvariable=search_var, width=24)
    search_entry.pack(side="left", padx=4)

    body = tk.Frame(page, bg="#FAFAF7")
    body.pack(fill="both", expand=True, padx=12, pady=4)
    columns = ("category", "gender", "age", "fit", "review", "file")
    table = ttk.Treeview(body, columns=columns, show="tree headings", selectmode="browse")
    table.heading("#0", text="รายการ")
    table.column("#0", width=230, stretch=True)
    for column, label, width in (
        ("category", "ประเภท", 115), ("gender", "เพศ", 75),
        ("age", "วัย", 115), ("fit", "ความเหมาะสม", 90),
        ("review", "สถานะข้อมูล", 100), ("file", "ไฟล์", 260),
    ):
        table.heading(column, text=label)
        table.column(column, width=width, stretch=(column == "file"))
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)
    table.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="left", fill="y")

    detail = tk.Frame(body, bg="#FFFFFF", width=290, padx=10, pady=8, relief="solid", bd=1)
    detail.pack(side="right", fill="y", padx=(10, 0))
    detail.pack_propagate(False)
    preview_label = tk.Label(detail, text="เลือกรายการเพื่อดูข้อมูล", bg="#FFFFFF", fg="#6B7280", wraplength=260)
    preview_label.pack(fill="x", pady=(0, 8))
    image_label = tk.Label(detail, bg="#FFFFFF")
    image_label.pack(fill="x")
    info_label = tk.Label(detail, text="", bg="#FFFFFF", fg="#222222", justify="left", anchor="nw", wraplength=260)
    info_label.pack(fill="both", expand=True, pady=(8, 0))
    edit_button = tk.Button(detail, text="✎ จัดหมวด/บันทึกข้อมูล", bg="#2563EB", fg="white", relief="flat", padx=8, pady=5)
    edit_button.pack(fill="x", pady=(8, 0))
    detail._photo = None

    footer = tk.Frame(page, bg="#FAFAF7")
    footer.pack(fill="x", padx=12, pady=(4, 12))
    tk.Label(footer, textvariable=status_var, bg="#FAFAF7", fg="#555555", anchor="w").pack(side="left", fill="x", expand=True)
    random_button = tk.Button(footer, text="🎲 สุ่มรายการ", bg="#7C3AED", fg="white", relief="flat", padx=12, pady=6)
    random_button.pack(side="right", padx=(6, 0))
    refresh_button = tk.Button(footer, text="↻ โหลดฐานข้อมูล", bg="#374151", fg="white", relief="flat", padx=12, pady=6)
    refresh_button.pack(side="right")

    records = {}
    filtered_ids = []
    pending_refresh = [None]

    def show_record(item_id):
        item = records.get(item_id)
        if not item:
            return
        preview_label.config(text=str(item.get("name_th") or item.get("file") or "รายการทรัพย์สิน"))
        tags = [
            f"ประเภท: {_category(item)}",
            f"เพศ: {item.get('gender') or 'ไม่ระบุ'}",
            f"วัย: {item.get('age') or 'ไม่ระบุ'}",
            f"เหมาะกับนิทาน: {item.get('folk_fit') if item.get('folk_fit') is not None else 'ยังไม่จัดระดับ'}",
            f"ไฟล์: {item.get('file') or Path(item.get('path') or '').name}",
            f"ตำแหน่ง: {item.get('path') or ''}",
            str(item.get("note") or "").strip(),
        ]
        info_label.config(text="\n".join(line for line in tags if line))
        detail._photo = None
        image_label.config(image="", text="")
        thumb = Path(str(item.get("thumb") or ""))
        try:
            image = None
            if thumb.is_file():
                from PIL import Image
                image = Image.open(thumb).convert("RGB")
            elif item.get("kind") == "hair" and item.get("path"):
                from snapgen_cc5_catalog import extract_thumbnail
                image = extract_thumbnail(item["path"])
            if image is not None:
                from PIL import ImageTk
                image.thumbnail((260, 210))
                detail._photo = ImageTk.PhotoImage(image)
                image_label.config(image=detail._photo)
            else:
                image_label.config(text="ไม่มีภาพตัวอย่างในฐานข้อมูล", fg="#6B7280")
        except Exception:
            image_label.config(text="เปิดภาพตัวอย่างไม่ได้", fg="#6B7280")

    def edit_record():
        selected = table.selection()
        if not selected or selected[0] not in records:
            status_var.set("เลือกรายการก่อนจัดหมวด")
            return
        item_id = selected[0]
        item = records[item_id]
        win = tk.Toplevel(page)
        win.title("จัดหมวดทรัพย์สิน CC5")
        win.configure(bg="#FAFAF7")
        win.transient(page.winfo_toplevel())
        win.grab_set()
        form = tk.Frame(win, bg="#FAFAF7", padx=14, pady=12)
        form.pack(fill="both", expand=True)
        name_var = tk.StringVar(value=str(item.get("name_th") or Path(item.get("file") or "").stem))
        gender_value = str(item.get("gender") or "ไม่ระบุ")
        if gender_value not in {"ชาย", "หญิง", "ได้ทั้งคู่", "ไม่ระบุ"}:
            gender_value = "ไม่ระบุ"
        gender_edit = tk.StringVar(value=gender_value)
        age_value = str(item.get("age") or "ไม่ระบุ")
        age_edit = tk.StringVar(value=age_value if age_value in (*_AGES, "ไม่ระบุ") else "หลายช่วงวัย (คงกลุ่มเดิม)" if item.get("groups") else "ไม่ระบุ")
        fit_value = str(item.get("folk_fit") if item.get("folk_fit") is not None else "2")
        fit_edit = tk.StringVar(value=fit_value if fit_value in {"0", "1", "2", "3"} else "2")

        tk.Label(form, text="ชื่อ/ลักษณะ", bg="#FAFAF7").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=name_var, width=38).grid(row=0, column=1, sticky="ew", pady=4)
        tk.Label(form, text="เพศ", bg="#FAFAF7").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(form, textvariable=gender_edit, values=("ชาย", "หญิง", "ได้ทั้งคู่", "ไม่ระบุ"), state="readonly", width=16).grid(row=1, column=1, sticky="w", pady=4)
        tk.Label(form, text="วัย", bg="#FAFAF7").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(form, textvariable=age_edit, values=(*_AGES, "ไม่ระบุ", "หลายช่วงวัย (คงกลุ่มเดิม)"), state="readonly", width=26).grid(row=2, column=1, sticky="w", pady=4)
        tk.Label(form, text="เหมาะกับนิทาน", bg="#FAFAF7").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(form, textvariable=fit_edit, values=("0", "1", "2", "3"), state="readonly", width=16).grid(row=3, column=1, sticky="w", pady=4)
        form.columnconfigure(1, weight=1)

        def save_metadata():
            name = name_var.get().strip()
            gender = gender_edit.get()
            age = age_edit.get()
            if not name:
                status_var.set("ใส่ชื่อรายการก่อนบันทึก")
                return
            try:
                from snapgen_cc5_catalog import load_catalog
                data = load_catalog(catalog_path, legacy_hair_pool)
                target = data.get("items", {}).get(item_id)
                if not isinstance(target, dict):
                    raise ValueError("ไม่พบรายการนี้ในฐานข้อมูล")
                target.update({"name_th": name, "gender": gender, "age": age, "folk_fit": int(fit_edit.get())})
                target["review_source"] = "manual"
                if target.get("kind") == "hair":
                    genders = {"ชาย": ("male",), "หญิง": ("female",), "ได้ทั้งคู่": ("male", "female")}.get(gender, ())
                    age_groups = {
                        "เด็ก": ("child",), "หนุ่มสาว": ("adult",),
                        "วัยกลางคน": ("adult",), "ผู้สูงอายุ": ("old",),
                        "ทุกวัย": ("child", "adult", "old"),
                    }.get(age, tuple(dict.fromkeys(str(group).partition("_")[2] for group in target.get("groups", []))) if age == "หลายช่วงวัย (คงกลุ่มเดิม)" else ())
                    groups = []
                    for gender_key in genders:
                        for age_key in age_groups:
                            if age == "วัยกลางคน" and gender_key == "male":
                                age_key = "old"
                            group = f"{gender_key}_{age_key}"
                            if group not in groups:
                                groups.append(group)
                    target["groups"] = groups
                    target["review_status"] = "curated" if groups else "needs_classification"
                    target["part"] = "hair"
                    if age != "หลายช่วงวัย (คงกลุ่มเดิม)":
                        target["age"] = age
                else:
                    target["review_status"] = "curated" if gender != "ไม่ระบุ" and age != "ไม่ระบุ" else "needs_classification"
                temp = catalog_path.with_suffix(catalog_path.suffix + ".tmp")
                temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(temp, catalog_path)
                win.destroy()
                refresh()
                if item_id in filtered_ids:
                    table.selection_set(item_id)
                    table.focus(item_id)
                    show_record(item_id)
                status_var.set("บันทึกข้อมูลในฐานข้อมูล CC5 แล้ว")
            except Exception as exc:
                status_var.set(f"บันทึกไม่สำเร็จ: {exc}")

        actions = tk.Frame(form, bg="#FAFAF7")
        actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        tk.Button(actions, text="ยกเลิก", command=win.destroy, relief="flat", padx=12, pady=5).pack(side="right", padx=(6, 0))
        tk.Button(actions, text="บันทึก", command=save_metadata, bg="#059669", fg="white", relief="flat", padx=14, pady=5).pack(side="right")

    def apply_filters(*_):
        nonlocal filtered_ids
        query = search_var.get().strip().casefold()
        category = category_var.get()
        gender = gender_var.get()
        age = age_var.get()
        filtered_ids = []
        table.delete(*table.get_children())
        for item_id, item in records.items():
            if category != "ทั้งหมด" and _category(item) != category:
                continue
            if not _matches_gender(item.get("gender"), gender):
                continue
            if not _matches_age(item.get("age"), age):
                continue
            searchable = " ".join(str(item.get(key) or "") for key in ("name_th", "file", "folder", "style", "note", "path")).casefold()
            if query and query not in searchable:
                continue
            label = str(item.get("name_th") or item.get("file") or item_id)
            table.insert("", "end", iid=item_id, text=label, values=(
                _category(item), item.get("gender") or "—", item.get("age") or "—",
                item.get("folk_fit") if item.get("folk_fit") is not None else "—",
                _review_label(item),
                item.get("file") or Path(item.get("path") or "").name,
            ))
            filtered_ids.append(item_id)
        status_var.set(f"แสดง {len(filtered_ids):,} / {len(records):,} รายการ จากฐานข้อมูลกลาง")

    def refresh(force_scan=False):
        try:
            from snapgen_cc5_catalog import load_catalog
            data = load_catalog(catalog_path, legacy_hair_pool, scan_hair_inventory=force_scan)
            records.clear()
            for item_id, item in data.get("items", {}).items():
                if isinstance(item, dict):
                    records[str(item_id)] = item
            apply_filters()
        except Exception as exc:
            status_var.set(f"โหลดฐานข้อมูลไม่สำเร็จ: {exc}")

    def schedule_refresh(_event=None):
        if pending_refresh[0] is not None:
            page.after_cancel(pending_refresh[0])
        def run_refresh():
            pending_refresh[0] = None
            apply_filters()
        pending_refresh[0] = page.after(160, run_refresh)

    def select_record(_event=None):
        selected = table.selection()
        if selected:
            show_record(selected[0])

    def pick_random():
        candidates = [
            item_id for item_id in filtered_ids
            if records[item_id].get("review_status") == "curated"
        ]
        if not candidates:
            status_var.set("ยังไม่มีรายการที่ผ่านการคัดในผลที่กรอง")
            return
        item_id = random.choice(candidates)
        table.selection_set(item_id)
        table.focus(item_id)
        table.see(item_id)
        show_record(item_id)
        status_var.set("สุ่มจากรายการที่ผ่านการคัดแล้ว")

    for widget in (category_box, gender_box, age_box):
        widget.bind("<<ComboboxSelected>>", schedule_refresh)
    search_entry.bind("<KeyRelease>", schedule_refresh)
    table.bind("<<TreeviewSelect>>", select_record)
    random_button.config(command=pick_random)
    refresh_button.config(command=lambda: refresh(True))
    edit_button.config(command=edit_record)
    return {"page": page, "refresh": refresh}
