# -*- coding: utf-8 -*-
"""SnapGen karaoke page.

This module owns the widgets, state, and callbacks for this page only.
"""
from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

_KARAOKE_ROMANIZATION_PATH = (
    Path(__file__).resolve().parent.parent / "snapgen_data" / "karaoke_romanizations.json"
)


def _load_karaoke_romanizations(path=_KARAOKE_ROMANIZATION_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return {
            str(name).strip(): str(value).strip()
            for name, value in data.items()
            if str(name).strip() and str(value).strip()
        }
    except (OSError, ValueError, AttributeError):
        return {}


def _save_karaoke_romanizations(values, path=_KARAOKE_ROMANIZATION_PATH):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def _karaoke_story_rows(characters):
    """Normalize Story Face rows, keeping only the first occurrence of each name."""
    rows = []
    seen_names = set()
    for character in characters or []:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name") or "").strip()
        if not name:
            continue
        name_key = name.casefold()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        variant = str(character.get("variant") or "").strip()
        rows.append({
            "order": len(rows) + 1,
            "name": name,
            "variant": variant,
            "label": str(character.get("label") or name + (f" — {variant}" if variant else "")),
            "roman": str(character.get("roman") or "").strip(),
        })
    return rows


def _parse_karaoke_batch_response(text, expected_count):
    """Parse one GPT batch response into romanized names in row order."""
    raw = str(text or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        raise ValueError("GPT ไม่ได้คืนรายการ JSON")
    items = json.loads(raw[start:end + 1])
    if not isinstance(items, list) or len(items) != expected_count:
        raise ValueError(f"GPT คืนชื่อไม่ครบ {expected_count} รายการ")
    by_id = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("รูปแบบชื่อจาก GPT ไม่ถูกต้อง")
        item_id = int(item.get("id") or 0)
        if item_id < 1 or item_id > expected_count or item_id in by_id:
            raise ValueError("เลขรายการจาก GPT ไม่ตรงกับหน้านิทาน")
        roman = str(item.get("roman") or "").strip()
        if not roman:
            raise ValueError(f"ชื่อรายการที่ {item_id} ไม่มีคำอ่าน")
        by_id[item_id] = roman
    if set(by_id) != set(range(1, expected_count + 1)):
        raise ValueError("GPT คืนเลขรายการไม่ครบ")
    return [by_id[index] for index in range(1, expected_count + 1)]


def _karaoke_copy_value(row):
    """Return the translated reading for a row, never the Thai source name."""
    return str(row.get("roman") or "").strip()


def _karaoke_pending_rows(rows):
    """Return only names that do not already have a saved reading."""
    return [row for row in rows if not _karaoke_copy_value(row)]


def install(g: dict, root: tk.Misc) -> tk.Misc:
    """Build this page and return its root frame."""
    globals().update(g)
    # ── Karaoke Page (built via snapgen_page_builder) ─────────────────────
    from snapgen_page_builder import (
        build_page, make_action_row, make_styled_button,
        make_label, make_entry, make_status_label,
        make_log_box, append_log,
    )
    
    karaoke_page, karaoke_box = build_page(root, "🔤 คาราโอเกะ — แปลงชื่อไทยเป็นคำอ่าน")
    
    # Form rows — keep labels, entries, and buttons on the same grid so the
    # two lines line up cleanly instead of drifting by each row's packed width.
    karaoke_form = make_action_row(karaoke_box)
    karaoke_form.pack(fill="x", pady=(0, 12))
    karaoke_form.grid_columnconfigure(0, minsize=92)
    karaoke_form.grid_columnconfigure(1, minsize=360)
    karaoke_form.grid_columnconfigure(2, minsize=170)
    karaoke_form.grid_columnconfigure(3, minsize=170)

    make_label(karaoke_form, "ชื่อภาษาไทย:", sub=True).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 10))
    karaoke_name_var = tk.StringVar()
    karaoke_input = make_entry(karaoke_form, karaoke_name_var, width=36)
    karaoke_input.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(0, 10), ipady=3)

    # One paste only. Windows/Tk can fire Control-v then <<Paste>> for the same keypress.
    _karaoke_paste_guard = {"ts": 0.0}

    def _paste_karaoke_text(replace_all=False):
        """Paste clipboard into the Thai name field. Returns True on success."""
        paste_fn = g.get("_paste_into_widget")
        try:
            txt = str(root.clipboard_get())
        except Exception:
            return False
        txt = str(txt or "").strip()
        if not txt:
            return False
        if replace_all:
            karaoke_name_var.set(txt)
            try:
                karaoke_input.icursor(tk.END)
                karaoke_input.focus_set()
            except Exception:
                pass
            return True
        if callable(paste_fn):
            ok = paste_fn(karaoke_input, txt)
            return bool(ok)
        try:
            if karaoke_input.selection_present():
                karaoke_input.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except Exception:
            pass
        try:
            karaoke_input.insert(tk.INSERT, txt)
        except Exception:
            karaoke_name_var.set(txt)
        return True

    def _paste_karaoke_once(_event=None):
        import time as _time
        now = _time.time()
        if now - float(_karaoke_paste_guard.get("ts") or 0.0) < 0.12:
            return "break"
        _karaoke_paste_guard["ts"] = now
        _paste_karaoke_text(replace_all=False)
        return "break"

    for _seq in ("<Control-v>", "<Control-V>"):
        karaoke_input.bind(_seq, _paste_karaoke_once)
    
    make_label(karaoke_form, "คำอ่าน:", sub=True).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
    karaoke_result_var = tk.StringVar()
    karaoke_result_entry = make_entry(karaoke_form, karaoke_result_var, width=36, readonly=True)
    karaoke_result_entry.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 8), ipady=3)
    
    # Status
    karaoke_status_var = tk.StringVar()
    make_status_label(karaoke_box, karaoke_status_var).pack(anchor="w")
    karaoke_log_box = make_log_box(karaoke_box)
    karaoke_log_box.pack(fill="x", pady=(8, 0))

    story_names_box = tk.LabelFrame(
        karaoke_box, text="รายชื่อจากหน้านิทาน — เรียงตามข้อมูลชุด",
        bg="#FAFAF7", fg="#1A1A1A", padx=8, pady=8,
    )
    story_names_box.pack(fill="both", expand=True, pady=(10, 0))
    story_names_toolbar = tk.Frame(story_names_box, bg="#FAFAF7")
    story_names_toolbar.pack(fill="x", pady=(0, 7))
    story_names_summary = tk.StringVar(value="กำลังอ่านรายชื่อจากหน้านิทาน...")
    tk.Label(
        story_names_toolbar, textvariable=story_names_summary,
        bg="#FAFAF7", fg="#64748B", anchor="w",
    ).pack(side="left", fill="x", expand=True)
    story_names_refresh_btn = make_styled_button(
        story_names_toolbar, "SECONDARY", "↻ ดึงรายชื่อใหม่"
    )
    story_names_refresh_btn.pack(side="right", padx=(4, 0))
    story_names_convert_btn = make_styled_button(
        story_names_toolbar, "PRIMARY", "🔤 แปลงทั้งหมด"
    )
    story_names_convert_btn.pack(side="right", padx=(4, 0))
    story_names_copy_btn = make_styled_button(
        story_names_toolbar, "SECONDARY", "📋 Copy ทั้งหมด"
    )
    story_names_copy_btn.pack(side="right", padx=(4, 0))
    story_names_copy_selected_btn = make_styled_button(
        story_names_toolbar, "SECONDARY", "📋 Copy ชื่อที่เลือก"
    )
    story_names_copy_selected_btn.pack(side="right", padx=(4, 0))

    story_names_tree = ttk.Treeview(
        story_names_box,
        columns=("order", "thai", "variant", "roman"),
        show="headings", height=13, selectmode="browse",
    )
    story_names_tree.heading("order", text="#")
    story_names_tree.heading("thai", text="ชื่อจากนิทาน")
    story_names_tree.heading("variant", text="ช่วงวัย / แบบ")
    story_names_tree.heading("roman", text="คำอ่าน")
    story_names_tree.column("order", width=44, minwidth=44, anchor="center", stretch=False)
    story_names_tree.column("thai", width=190, minwidth=120, anchor="w")
    story_names_tree.column("variant", width=270, minwidth=150, anchor="w")
    story_names_tree.column("roman", width=210, minwidth=130, anchor="w")
    story_names_scroll = ttk.Scrollbar(
        story_names_box, orient="vertical", command=story_names_tree.yview
    )
    story_names_tree.configure(yscrollcommand=story_names_scroll.set)
    story_names_tree.pack(side="left", fill="both", expand=True)
    story_names_scroll.pack(side="right", fill="y")
    story_name_rows = []
    story_names_running = [False]
    saved_romanizations = _load_karaoke_romanizations()
    
    def _karaoke_status(msg):
        karaoke_status_var.set(msg)
        append_log(karaoke_log_box, msg)

    def _notify_done():
        notify = g.get("_snapgen_notify_done")
        if callable(notify):
            try:
                notify()
            except Exception:
                pass
    
    # Buttons (consistent style via page builder)
    # Row 0: [Paste] [Convert]
    # Paste fills the Thai name and auto-converts immediately.
    # Convert can still be pressed again later for the current text.
    karaoke_paste_btn = make_styled_button(karaoke_form, "SECONDARY", "📋 วาง")
    karaoke_paste_btn.grid(row=0, column=2, sticky="ew", padx=(0, 4), pady=(0, 10))
    karaoke_convert_btn = make_styled_button(karaoke_form, "PRIMARY", "🔤 แปลง")
    karaoke_convert_btn.grid(row=0, column=3, sticky="ew", padx=(0, 0), pady=(0, 10))
    karaoke_input.bind("<Return>", lambda e: convert_karaoke())
    
    karaoke_copy_btn = make_styled_button(karaoke_form, "SECONDARY", "📋 Copy",
        command=lambda: (root.clipboard_clear(), root.clipboard_append(karaoke_result_var.get()), _karaoke_status("คัดลอกแล้ว ✓")))
    karaoke_copy_btn.grid(row=1, column=2, sticky="ew", padx=(0, 4), pady=(0, 8))
    
    karaoke_clear_btn = make_styled_button(karaoke_form, "DANGER", "🧹 Clear",
        command=lambda: (karaoke_name_var.set(""), karaoke_result_var.set(""), _karaoke_status("")))
    karaoke_clear_btn.grid(row=1, column=3, sticky="ew", padx=(0, 0), pady=(0, 8))
    
    def _thai_to_roman(thai_name):
        import urllib.request
        try:
            body = {
                "model": "auto",
                "messages": [
                    {"role": "system", "content": "คุณคือผู้ช่วยถอดเสียงภาษาไทยเป็นอักษรโรมันแบบอ่านง่ายสำหรับฝรั่ง ตอบเฉพาะคำอ่านเท่านั้น ห้ามอธิบาย\n\nกฎ:\n- ใช้ตัวสะกดแบบภาษาอังกฤษธรรมชาติ อ่านแล้วออกเสียงใกล้เคียงที่สุด\n- สระสั้น-ยาว ไม่ต้องแยกชัด เช่น ก้าน = Kan (ไม่ใช่ Kaan), จันทร์ = Chan (ไม่ใช่ Chanthr)\n- พยัญชนะต้นใช้ตัวอักษรที่ฝรั่งอ่านออก เช่น จ = Ch/J, ท = T, พ = P, ภ = P, ก = K\n- ตัวสะกดใช้ตัวที่ฝรั่งคุ้น เช่น -น = n, -ด = t/d, -บ = p/b, -ก = k\n- ไม่ต้องใส่วรรณยุกต์\n- ทำให้สั้น กระชับ อ่านง่ายที่สุด"},
                    {"role": "user", "content": f"แปลงชื่อนี้เป็นคำอ่านภาษาอังกฤษ: {thai_name}"},
                ],
                "temperature": 0.1,
            }
            req = urllib.request.Request(
                "http://127.0.0.1:8000/v1/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"ERROR: {e}"

    def _thai_to_roman_batch(rows):
        """Romanize the complete ordered list with one GPT request."""
        import urllib.request
        items = [
            {"id": index, "thai": row["name"], "variant": row.get("variant", "")}
            for index, row in enumerate(rows, 1)
        ]
        try:
            body = {
                "model": "auto",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "ถอดเสียงชื่อภาษาไทยทุกชื่อเป็นอักษรโรมันแบบสั้น อ่านง่ายสำหรับฝรั่ง "
                            "คงจำนวนและลำดับเดิม ตอบเป็น JSON array เท่านั้น รูปแบบ "
                            '[{"id":1,"roman":"..."}] ห้ามใส่ markdown หรือคำอธิบาย'
                        ),
                    },
                    {
                        "role": "user",
                        "content": "แปลงรายชื่อชุดนี้ทั้งหมดในครั้งเดียว:\n" + json.dumps(items, ensure_ascii=False),
                    },
                ],
                "temperature": 0.1,
            }
            req = urllib.request.Request(
                "http://127.0.0.1:8000/v1/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = data["choices"][0]["message"]["content"]
            return _parse_karaoke_batch_response(content, len(items))
        except Exception as error:
            raise RuntimeError(str(error)) from error

    def _render_story_names():
        selected = story_names_tree.selection()
        selected_index = int(selected[0]) if selected and selected[0].isdigit() else None
        story_names_tree.delete(*story_names_tree.get_children())
        for index, row in enumerate(story_name_rows):
            story_names_tree.insert(
                "", "end", iid=str(index),
                values=(row["order"], row["name"], row["variant"], row["roman"]),
            )
        if selected_index is not None and selected_index < len(story_name_rows):
            story_names_tree.selection_set(str(selected_index))
        story_names_summary.set(
            f"{len(story_name_rows)} รายการ · ลำดับเดียวกับหน้านิทาน"
            if story_name_rows else
            "ยังไม่มีรายชื่อ — ไปหน้าหน้านิทานแล้วบันทึกข้อมูลชุดก่อน"
        )

    def refresh_story_names():
        provider = g.get("story_face_ordered_characters")
        previous = {
            (row["name"], row["variant"]): row.get("roman", "")
            for row in story_name_rows
        }
        try:
            rows = _karaoke_story_rows(provider() if callable(provider) else [])
        except Exception as error:
            rows = []
            _karaoke_status(f"ดึงรายชื่อจากหน้านิทานไม่สำเร็จ: {error}")
        for row in rows:
            row["roman"] = previous.get(
                (row["name"], row["variant"]),
                saved_romanizations.get(row["name"], ""),
            )
        story_name_rows[:] = rows
        _render_story_names()
        return len(rows)

    def _select_story_name(_event=None):
        selected = story_names_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if not 0 <= index < len(story_name_rows):
            return
        row = story_name_rows[index]
        karaoke_name_var.set(row["name"])
        karaoke_result_var.set(row["roman"])

    def convert_all_story_names():
        if story_names_running[0]:
            return
        if not story_name_rows and not refresh_story_names():
            _karaoke_status("ยังไม่มีรายชื่อในหน้านิทาน")
            return
        pending_rows = _karaoke_pending_rows(story_name_rows)
        if not pending_rows:
            _karaoke_status("คำอ่านถูกบันทึกไว้ครบแล้ว ไม่ต้องแปลซ้ำ ✓")
            return
        story_names_running[0] = True
        story_names_convert_btn.config(state="disabled", text="⏳ กำลังแปลง...")
        _karaoke_status(f"กำลังแปลงชื่อใหม่ {len(pending_rows)} รายการ...")

        def worker():
            try:
                converted = _thai_to_roman_batch(list(pending_rows))
                error = None
            except Exception as exc:
                converted = []
                error = str(exc)

            def done():
                if not error:
                    for row, roman in zip(pending_rows, converted):
                        row["roman"] = roman
                    saved_romanizations.update(
                        {row["name"]: row["roman"] for row in story_name_rows if row.get("roman")}
                    )
                    try:
                        _save_karaoke_romanizations(saved_romanizations)
                    except OSError as save_error:
                        _karaoke_status(f"แปลงเสร็จ แต่บันทึกคำอ่านไม่ได้: {save_error}")
                story_names_running[0] = False
                story_names_convert_btn.config(state="normal", text="🔤 แปลงทั้งหมด")
                _render_story_names()
                _karaoke_status(
                    f"แปลงชื่อใหม่เสร็จ {len(pending_rows)} รายการ ✓"
                    if not error else
                    f"แปลงรายชื่อชุดไม่สำเร็จ: {error}"
                )
                if not error:
                    _notify_done()
            root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def copy_all_story_names():
        rows = [
            f'{row["order"]}. {row["label"]} = {row["roman"]}'
            for row in story_name_rows if row.get("roman")
        ]
        if not rows:
            _karaoke_status("กด แปลงทั้งหมด ก่อน Copy")
            return
        root.clipboard_clear()
        root.clipboard_append("\n".join(rows))
        _karaoke_status(f"คัดลอกรายชื่อ {len(rows)} รายการแล้ว ✓")

    def copy_selected_story_name(_event=None):
        if _event is not None:
            item = story_names_tree.identify_row(_event.y)
            if item:
                story_names_tree.selection_set(item)
        selected = story_names_tree.selection()
        if not selected:
            _karaoke_status("เลือกชื่อที่ต้องการคัดลอกก่อน")
            return "break" if _event is not None else None
        index = int(selected[0])
        if not 0 <= index < len(story_name_rows):
            return "break" if _event is not None else None
        row = story_name_rows[index]
        name = row["name"]
        value = _karaoke_copy_value(row)
        if not value:
            _karaoke_status(f"ชื่อ “{name}” ยังไม่มีคำอ่าน — กด แปลงทั้งหมด ก่อน")
            return "break" if _event is not None else None
        root.clipboard_clear()
        root.clipboard_append(value)
        _karaoke_status(f"คัดลอกคำอ่าน “{value}” ของ {name} แล้ว ✓")
        return "break" if _event is not None else None
    
    def convert_karaoke():
        name = karaoke_name_var.get().strip()
        if not name:
            _karaoke_status("ใส่ชื่อภาษาไทยก่อน")
            return
        karaoke_convert_btn.config(state="disabled", text="⏳ กำลังแปลง...")
        _karaoke_status("กำลังแปลง...")
        def worker():
            result = _thai_to_roman(name)
            def done():
                karaoke_result_var.set(result)
                if not result.startswith("ERROR"):
                    saved_romanizations[name] = result
                    try:
                        _save_karaoke_romanizations(saved_romanizations)
                    except OSError:
                        pass
                karaoke_convert_btn.config(state="normal", text="🔤 แปลง")
                _karaoke_status("แปลงเสร็จ ✓" if not result.startswith("ERROR") else result)
                if not result.startswith("ERROR"):
                    _notify_done()
            root.after(0, done)
        threading.Thread(target=worker, daemon=True).start()
    
    def paste_and_convert():
        """Paste clipboard into the name box, then convert immediately."""
        if not _paste_karaoke_text(replace_all=True):
            _karaoke_status("คลิปบอร์ดว่าง หรือวางไม่ได้")
            return
        _karaoke_status("วางแล้ว — กำลังแปลง...")
        convert_karaoke()

    karaoke_paste_btn.config(command=paste_and_convert)
    karaoke_convert_btn.config(command=convert_karaoke)
    story_names_refresh_btn.config(command=refresh_story_names)
    story_names_convert_btn.config(command=convert_all_story_names)
    story_names_copy_btn.config(command=copy_all_story_names)
    story_names_copy_selected_btn.config(command=copy_selected_story_name)
    story_names_tree.bind("<<TreeviewSelect>>", _select_story_name)
    story_names_tree.bind("<Double-1>", copy_selected_story_name)
    refresh_story_names()
    g["karaoke_page"] = karaoke_page
    g["_karaoke_status"] = _karaoke_status
    g["karaoke_log_box"] = karaoke_log_box
    g["refresh_karaoke_story_names"] = refresh_story_names
    # Karaoke has no selectable entity, so it intentionally has no lock bar.
    g["karaoke_lock_bar"] = None
    
    old_switch = g.get("switch_mode")
    g["karaoke_page"] = karaoke_page
    return karaoke_page
