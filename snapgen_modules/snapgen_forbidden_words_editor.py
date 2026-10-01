# -*- coding: utf-8 -*-
"""Forbidden-words editor for Video prompts.

Adds a small settings button near the Video page that opens a popup where
users can edit, enable/disable, and push the forbidden-word list to GitHub.
"""
from __future__ import annotations

import base64, json, os, subprocess, threading, time, urllib.request
from pathlib import Path
from tkinter import Toplevel, Frame, Label, Entry, Button, Listbox, Scrollbar, Checkbutton, BooleanVar, StringVar, messagebox, END
from snapgen_fonts import font_family as _snapgen_font_family

SNAPGEN_UI_FONT = _snapgen_font_family()

REPO = "tidmunzsocial-lab/tidmunz-studio"
BRANCH = "main"
REMOTE_PATH = "assets/video_forbidden_words.json"
SYNC_TTL_SECONDS = 600


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"enabled": True, "match_case": False, "words": []}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))



def _normalise(data):
    if not isinstance(data, dict) or not isinstance(data.get("words"), list):
        raise ValueError("รูปแบบข้อมูลคำต้องห้ามไม่ถูกต้อง")
    words = []
    seen = set()
    for value in data["words"]:
        word = str(value).strip()
        key = word.casefold()
        if word and key not in seen:
            seen.add(key)
            words.append(word)
    return {
        "enabled": bool(data.get("enabled", True)),
        "match_case": bool(data.get("match_case", False)),
        "words": words,
    }

def _fetch_remote_data():
    """Read shared data without requiring GitHub login on client machines."""
    url = (
        f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{REMOTE_PATH}"
        f"?t={int(time.time())}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "SnapGen-forbidden-words"})
    with urllib.request.urlopen(request, timeout=10) as resp:
        return _normalise(json.loads(resp.read().decode("utf-8-sig")))

def _fetch_from_github(json_path: Path) -> bool:
    """Download latest shared words without updating program files."""
    try:
        data = _fetch_remote_data()
        if data != _load_json(json_path):
            _save_json(json_path, data)
        return True
    except Exception as e:
        if os.environ.get("SNAPGEN_VERBOSE_STARTUP") == "1":
            print(f"[SnapGen] forbidden words sync skipped: {e}")
    return False

def _publish_to_github(local_data, baseline, log_fn):
    """Update only the shared JSON through GitHub API; never commit program files."""
    try:
        remote = _fetch_remote_data()
        baseline_words = {word.casefold(): word for word in baseline.get("words", [])}
        local_words = {word.casefold(): word for word in local_data.get("words", [])}
        remote_words = {word.casefold(): word for word in remote.get("words", [])}
        additions = set(local_words) - set(baseline_words)
        deletions = set(baseline_words) - set(local_words)
        for key in additions:
            remote_words[key] = local_words[key]
        for key in deletions:
            remote_words.pop(key, None)
        merged = _normalise({
            "enabled": local_data["enabled"],
            "match_case": local_data["match_case"],
            "words": list(remote_words.values()),
        })

        info = subprocess.run(
            ["gh", "api", "--method", "GET", f"repos/{REPO}/contents/{REMOTE_PATH}", "-f", f"ref={BRANCH}"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
        if info.returncode != 0:
            raise RuntimeError("เครื่องนี้ยังไม่ได้ Login GitHub หรือไม่มีสิทธิ์แก้รายการ")
        sha = json.loads(info.stdout)["sha"]
        payload = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
        result = subprocess.run(
            [
                "gh", "api", "--method", "PUT", f"repos/{REPO}/contents/{REMOTE_PATH}",
                "-f", "message=Update shared video forbidden words",
                "-f", f"content={base64.b64encode(payload).decode('ascii')}",
                "-f", f"sha={sha}", "-f", f"branch={BRANCH}",
            ],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "GitHub ไม่รับข้อมูล")[-300:])
        log_fn("✅ ข้อมูลกลางออนไลน์แล้ว")
        return merged
    except Exception as e:
        log_fn(f"⚠️  อัปเดตข้อมูลกลางไม่ได้: {e}")
        return None


def open_editor(root, json_path, reload_fn=None):
    json_path = Path(json_path)
    data = _load_json(json_path)
    baseline = dict(data)
    baseline["words"] = list(data.get("words", []))

    win = Toplevel(root)
    win.title("จัดการคำต้องห้ามในช่องวิดีโอ")
    win.geometry("500x520")
    win.transient(root)
    win.grab_set()

    Label(win, text="คำต้องห้าม ( forbidden words )", font=(SNAPGEN_UI_FONT, 12, "bold")).pack(pady=10)

    enabled_var = BooleanVar(value=data.get("enabled", True))
    case_var = BooleanVar(value=data.get("match_case", False))

    Checkbutton(win, text="เปิดใช้งานไฮไลท์", variable=enabled_var).pack(anchor="w", padx=20)
    Checkbutton(win, text="ตรงตัวพิมพ์เล็ก/ใหญ่", variable=case_var).pack(anchor="w", padx=20)

    frame = Frame(win)
    frame.pack(fill="both", expand=True, padx=20, pady=10)

    scrollbar = Scrollbar(frame)
    scrollbar.pack(side="right", fill="y")

    lb = Listbox(frame, yscrollcommand=scrollbar.set, font=(SNAPGEN_UI_FONT, 11))
    lb.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=lb.yview)

    for w in data.get("words", []):
        lb.insert(END, w)

    entry_var = StringVar()
    entry = Entry(win, textvariable=entry_var, font=(SNAPGEN_UI_FONT, 11))
    entry.pack(fill="x", padx=20, pady=5)
    entry.bind("<Return>", lambda _e: add_word())

    status_var = StringVar(value="ออนไลน์ — ดึงข้อมูลเมื่อเปิดหน้านี้")
    status = Label(win, textvariable=status_var, fg="gray", font=(SNAPGEN_UI_FONT, 9))
    status.pack(pady=2)

    def set_status(msg, color="gray"):
        status_var.set(msg)
        status.config(fg=color)

    def add_word():
        word = entry_var.get().strip()
        if not word:
            return
        items = [lb.get(i) for i in range(lb.size())]
        if word.lower() in [w.lower() for w in items]:
            set_status(f"'{word}' มีอยู่แล้ว", "orange")
            return
        lb.insert(END, word)
        entry_var.set("")
        set_status(f"เพิ่ม '{word}' แล้ว", "green")

    def remove_word():
        sel = lb.curselection()
        if not sel:
            set_status("เลือกคำที่จะลบก่อน", "orange")
            return
        word = lb.get(sel[0])
        lb.delete(sel[0])
        set_status(f"ลบ '{word}' แล้ว", "green")

    def save_and_push():
        words = [lb.get(i).strip() for i in range(lb.size()) if lb.get(i).strip()]
        new_data = {
            "enabled": enabled_var.get(),
            "match_case": case_var.get(),
            "words": words,
        }
        _save_json(json_path, new_data)
        set_status("กำลังส่งข้อมูลกลาง...", "blue")
        save_btn.config(state="disabled")

        def worker():
            messages = []
            merged = _publish_to_github(new_data, baseline, messages.append)
            def done():
                save_btn.config(state="normal")
                if merged:
                    _save_json(json_path, merged)
                    baseline.clear()
                    baseline.update(merged)
                    if callable(reload_fn):
                        try:
                            reload_fn()
                        except Exception:
                            pass
                    set_status("ออนไลน์แล้ว — เครื่องอื่นจะเห็นเมื่อเปิดส่วนนี้", "green")
                else:
                    set_status(messages[-1] if messages else "อัปเดตข้อมูลกลางไม่ได้", "red")
            try:
                root.after(0, done)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    btn_frame = Frame(win)
    btn_frame.pack(fill="x", padx=20, pady=5)

    Button(btn_frame, text="➕ เพิ่ม", command=add_word, bg="#16A34A", fg="white").pack(side="left", padx=3)
    Button(btn_frame, text="🗑 ลบ", command=remove_word, bg="#DC2626", fg="white").pack(side="left", padx=3)

    save_btn = Button(
        win, text="💾 บันทึกข้อมูลกลาง", command=save_and_push,
        bg="#2563EB", fg="white", font=(SNAPGEN_UI_FONT, 10, "bold"), padx=20, pady=8,
    )
    save_btn.pack(pady=15)


def install_button(root, g, json_path):
    """Add a small 'forbidden words' button that only shows on Video page."""
    try:
        import tkinter as tk
        json_path = Path(json_path)
        # Shared-data sync is independent from program updates. Every running
        # machine reads the same GitHub JSON; only an explicit Save writes it.
        sync_busy = [False]
        last_sync = [0.0]

        def sync_shared_data(force=False, after=None):
            if not force and time.time() - last_sync[0] < SYNC_TTL_SECONDS:
                if callable(after):
                    after()
                return
            if sync_busy[0]:
                if callable(after):
                    root.after(250, lambda: sync_shared_data(force=True, after=after))
                return
            sync_busy[0] = True

            def worker():
                before = _load_json(json_path)
                ok = _fetch_from_github(json_path)
                changed = ok and _load_json(json_path) != before
                def done():
                    sync_busy[0] = False
                    if ok:
                        last_sync[0] = time.time()
                    if changed:
                        reload = g.get("reload_video_forbidden_words")
                        if callable(reload):
                            try:
                                reload()
                            except Exception:
                                pass
                    if callable(after):
                        after()
                try:
                    root.after(0, done)
                except Exception:
                    pass
            threading.Thread(target=worker, daemon=True).start()

        sync_shared_data(force=True)

        def on_click():
            reload = g.get("reload_video_forbidden_words")
            sync_shared_data(
                force=True,
                after=lambda: open_editor(root, json_path, reload_fn=reload),
            )

        parent = g.get("slots") or root
        btn = tk.Button(
            parent,
            text="⚠️",
            command=on_click,
            bg="#DC2626",
            fg="white",
            font=(SNAPGEN_UI_FONT, 9, "bold"),
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=10,
            pady=4,
        )

        def _is_video_mode():
            try:
                cur = g.get("current_mode")
                if cur is not None and hasattr(cur, "get"):
                    return str(cur.get()).lower() == "video"
            except Exception:
                pass
            return True

        def reposition(*_):
            try:
                w = parent.winfo_width()
                if _is_video_mode():
                    btn.place(relx=1.0, x=-10, y=4, anchor='ne')
                else:
                    btn.place_forget()
            except Exception:
                pass

        # Start visible only if in video mode
        if _is_video_mode():
            btn.place(relx=1.0, x=-10, y=4, anchor='ne')
        else:
            btn.place_forget()

        parent.bind("<Configure>", lambda _e: reposition(), add="+")
        reposition()

        # Hook into switch_mode so button hides on non-video pages
        _old_switch = g.get("switch_mode")
        def _patched_switch(mode, *a, **kw):
            try:
                result = _old_switch(mode, *a, **kw) if _old_switch else None
            except Exception:
                result = None
            try:
                if str(mode).lower() == "video":
                    sync_shared_data()
                    w = parent.winfo_width()
                    btn.place(relx=1.0, x=-10, y=4, anchor='ne')
                else:
                    btn.place_forget()
            except Exception:
                pass
            return result
        g["switch_mode"] = _patched_switch

        g["_forbidden_words_btn"] = btn
        return btn
    except Exception as e:
        print(f"[SnapGen] forbidden words button install failed: {e}")
        return None
