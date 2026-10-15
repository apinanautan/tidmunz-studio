# -*- coding: utf-8 -*-
"""Show the program's own Chrome (ChatGPT) inside a Tidmunz window.

The Chrome window that runs the "Tidmunz ChatGPT" extension is re-parented into a
Tk frame, so the user does not need a separate browser window and cannot minimize
it by accident. Closing the Tidmunz window hands the Chrome window back to the
desktop unchanged; Chrome itself keeps running.
"""
from __future__ import annotations

import ctypes
import subprocess
import threading
import time
import tkinter as tk
from ctypes import wintypes

import snapgen_chrome_relay as relay

user32 = ctypes.WinDLL("user32", use_last_error=True)
GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_CLIPCHILDREN = 0x02000000
SW_RESTORE = 9
SW_SHOW = 5
SWP_FRAMECHANGED = 0x0020
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
FRAME_STYLES = WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def program_chrome_pids() -> set[int]:
    """Browser processes started with Tidmunz's Chrome profile (not its renderers)."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*SnapGenChromeProfile*' -and $_.CommandLine -notlike '*--type=*' } | "
              "ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return set()
    return {int(x) for x in out.split() if x.strip().isdigit()}


def _title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def chrome_windows(pids: set[int]) -> list[int]:
    """Visible top-level browser windows of those processes, ChatGPT windows first."""
    found = []

    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and _class(hwnd) == "Chrome_WidgetWin_1" and _title(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(visit), 0)
    found.sort(key=lambda h: "chatgpt" not in _title(h).lower())
    return found


def find_or_open(wait: float = 30.0) -> int | None:
    """The program Chrome's window; opens the program Chrome when none is running."""
    pids = program_chrome_pids()
    windows = chrome_windows(pids) if pids else []
    if windows:
        return windows[0]
    if not relay.open_program_chrome():
        return None
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(1.5)
        pids = program_chrome_pids() or pids
        windows = chrome_windows(pids) if pids else []
        if windows:
            return windows[0]
    return None


class EmbeddedChrome:
    def __init__(self, host: tk.Widget) -> None:
        self.host = host
        self.hwnd = None
        self.old_style = 0
        self.old_parent = None

    def attach(self, hwnd: int) -> None:
        host_id = self.host.winfo_id()
        style = user32.GetWindowLongPtrW(host_id, GWL_STYLE)
        user32.SetWindowLongPtrW(host_id, GWL_STYLE, style | WS_CLIPCHILDREN)
        user32.ShowWindow(hwnd, SW_RESTORE)  # a maximized window would not follow the frame's size
        self.old_style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        self.old_parent = user32.GetParent(hwnd)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, (self.old_style & ~FRAME_STYLES) | WS_CHILD)
        user32.SetParent(hwnd, host_id)
        self.hwnd = hwnd
        self.fit()
        user32.ShowWindow(hwnd, SW_SHOW)

    def fit(self, _event=None) -> None:
        if not self.hwnd:
            return
        rect = wintypes.RECT()
        user32.GetClientRect(self.host.winfo_id(), ctypes.byref(rect))
        user32.SetWindowPos(self.hwnd, None, 0, 0, rect.right, rect.bottom,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def release(self) -> None:
        """Give the window back to the desktop as a normal Chrome window."""
        hwnd, self.hwnd = self.hwnd, None
        if not hwnd or not user32.IsWindow(hwnd):
            return
        user32.SetParent(hwnd, self.old_parent or None)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, self.old_style or (FRAME_STYLES & ~WS_POPUP))
        user32.SetWindowPos(hwnd, None, 80, 60, 1200, 820, SWP_NOZORDER | SWP_FRAMECHANGED)
        user32.ShowWindow(hwnd, SW_SHOW)


_open_window = [None]


def open_window(root: tk.Misc, log=print) -> None:
    """Open (or bring forward) the Tidmunz window that holds the program's Chrome."""
    win = _open_window[0]
    if win is not None and win.winfo_exists():
        win.deiconify()
        win.lift()
        return
    win = tk.Toplevel(root)
    _open_window[0] = win
    win.title("ChatGPT — Chrome ของโปรแกรม")
    win.geometry("1280x860")
    win.configure(bg="#111827")
    note = tk.Label(win, text="กำลังเปิด Chrome ของโปรแกรม…", bg="#111827", fg="#E5E7EB", font=("Segoe UI", 11))
    note.pack(side="top", fill="x", pady=2)
    host = tk.Frame(win, bg="#111827")
    host.pack(fill="both", expand=True)
    embed = EmbeddedChrome(host)
    host.bind("<Configure>", embed.fit)

    def closing():
        embed.release()
        _open_window[0] = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", closing)
    # Tk tells the frame before its own window goes away: Chrome must leave first or it is destroyed too.
    host.bind("<Destroy>", lambda e: embed.release())

    def attach_when_found(hwnd):
        if not win.winfo_exists():
            return
        if not hwnd:
            note.config(text="หา Chrome ของโปรแกรมไม่พบ — กด 🧩 ส่วนขยาย ChatGPT ใน ⚙ เพื่อตรวจการติดตั้ง", fg="#FCA5A5")
            return
        try:
            win.update_idletasks()
            embed.attach(hwnd)
            note.config(text="Chrome ของโปรแกรม (ส่วนขยาย Tidmunz ChatGPT ทำงานในนี้) — ปิดหน้าต่างนี้ Chrome จะแยกออกไปเป็นหน้าต่างปกติ",
                        fg="#9CA3AF", font=("Segoe UI", 9))
        except Exception as error:
            note.config(text=f"ฝัง Chrome ไม่สำเร็จ: {error}", fg="#FCA5A5")
            log(f"[ChatGPT embed] {error}")

    def search():
        hwnd = find_or_open()
        try:
            root.after(0, lambda: attach_when_found(hwnd))
        except Exception:
            pass

    threading.Thread(target=search, daemon=True).start()
