# -*- coding: utf-8 -*-
"""Show the program's own Chrome (ChatGPT) inside a Tidmunz window.

The Chrome window that runs the "Tidmunz ChatGPT" extension is laid over a page
of the main window (the 🌐 ChatGPT button), so there is no separate browser
window to minimize by accident. When Tidmunz closes, the Chrome window goes back
to the desktop unchanged; Chrome itself keeps running.
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
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
SW_RESTORE = 9
SW_SHOW = 5
SW_SHOWNOACTIVATE = 4
GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
WS_EX_APPWINDOW = 0x00040000
GA_ROOT = 2
GW_OWNER = 4
PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
SWP_FRAMECHANGED = 0x0020
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
FRAME_STYLES = WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
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
    """Chrome as a frameless window owned by Tidmunz, laid exactly over the page.

    It stays its own top-level window (not a child), because Tidmunz is not DPI-aware: a child
    would be stretched by Windows with the rest of the program and look blurred. Owned by the
    main window it keeps above it, hides when it is minimized and has no taskbar button.
    """

    def __init__(self, host: tk.Widget) -> None:
        self.host = host
        self.hwnd = None
        self.visible = False
        self.old_style = 0
        self.old_exstyle = 0

    def owner(self) -> int:
        return user32.GetAncestor(self.host.winfo_id(), GA_ROOT)

    def attach(self, hwnd: int) -> None:
        user32.ShowWindow(hwnd, SW_RESTORE)  # a maximized window would not follow the page's size
        self.old_style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        self.old_exstyle = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, (self.old_style & ~FRAME_STYLES) | WS_POPUP)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, self.old_exstyle & ~WS_EX_APPWINDOW)
        user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, self.owner())
        self.hwnd = hwnd
        self.fit(frame_changed=True)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

    def attached(self) -> bool:
        hwnd = self.hwnd
        return bool(hwnd and user32.IsWindow(hwnd) and user32.GetWindow(hwnd, GW_OWNER) == self.owner())

    def fit(self, _event=None, frame_changed: bool = False) -> None:
        """Lay Chrome over the page in real screen pixels, or out of sight when the page is hidden."""
        hwnd = self.hwnd
        if not hwnd or not user32.IsWindow(hwnd):
            return
        if user32.IsIconic(hwnd):  # minimized from its own tab strip: it must keep working
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        flags = SWP_NOZORDER | SWP_NOACTIVATE | (SWP_FRAMECHANGED if frame_changed else 0)
        previous = user32.SetThreadDpiAwarenessContext(PER_MONITOR_AWARE_V2)
        try:
            rect = wintypes.RECT()
            user32.GetWindowRect(self.host.winfo_id(), ctypes.byref(rect))
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if self.visible and width > 50 and height > 50:
                user32.SetWindowPos(hwnd, None, rect.left, rect.top, width, height, flags)
            else:
                user32.SetWindowPos(hwnd, None, -20000, -20000, max(width, 1200), max(height, 800), flags)
        finally:
            if previous:
                user32.SetThreadDpiAwarenessContext(previous)

    def release(self) -> None:
        """Give the window back to the desktop as a normal Chrome window."""
        hwnd, self.hwnd = self.hwnd, None
        if not hwnd or not user32.IsWindow(hwnd):
            return
        user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, 0)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, self.old_style or (FRAME_STYLES & ~WS_POPUP))
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, self.old_exstyle)
        user32.SetWindowPos(hwnd, None, 80, 60, 1200, 820, SWP_NOZORDER | SWP_FRAMECHANGED)
        user32.ShowWindow(hwnd, SW_SHOW)


class ChatGPTPage:
    """A page of the main window (below the top bar) that holds the program's Chrome.

    Hidden means moved out of sight, not unmapped: Chrome stays a visible window, so ChatGPT keeps
    working in the background exactly as when the page is shown.
    """

    def __init__(self, root: tk.Misc, top_bar: tk.Widget | None, log=print) -> None:
        self.root = root
        self.top_bar = top_bar
        self.log = log
        self.shown = False
        self.page = tk.Frame(root, bg="#111827")
        self.note = tk.Label(self.page, text="กำลังหา Chrome ของโปรแกรม…", bg="#111827", fg="#E5E7EB",
                             font=("Segoe UI", 11))
        self.host = tk.Frame(self.page, bg="#111827")
        self.host.pack(fill="both", expand=True)
        self.embed = EmbeddedChrome(self.host)
        self.host.bind("<Configure>", self.embed.fit)
        # Tk tells the frame before the program's window goes away: Chrome goes back to the desktop.
        self.host.bind("<Destroy>", lambda e: self.embed.release())
        root.bind("<Configure>", lambda e: self.layout() if e.widget is root else None, add="+")
        self.pids: set[int] = set()
        self.pids_at = 0.0
        self.searching = False
        self.opened = False
        self.layout()
        relay.INSIDE_PROGRAM = True
        root.after(1000, self.watch)

    def top(self) -> int:
        try:
            bar = self.top_bar
            return max(0, bar.winfo_rooty() + bar.winfo_height() - self.root.winfo_rooty()) if bar else 0
        except tk.TclError:
            return 0

    def layout(self) -> None:
        y = self.top()
        x = 0 if self.shown else -(self.root.winfo_width() + 200)
        self.page.place(in_=self.root, x=x, y=y, relwidth=1, relheight=1, height=-y)
        if self.shown:
            self.page.lift()
        self.embed.visible = self.shown
        self.root.after_idle(self.embed.fit)  # also when the main window only moved

    def show(self) -> None:
        self.shown = True
        self.layout()
        if not self.attached():
            self.note.pack(side="top", fill="x", before=self.host)
            self.opened = True  # the user asked for it: open the program's Chrome when it is closed
            self.search()

    def hide(self, _event=None) -> None:
        if self.shown:
            self.shown = False
            self.layout()

    def toggle(self) -> None:
        self.hide() if self.shown else self.show()

    def attached(self) -> bool:
        return self.embed.attached()

    def watch(self) -> None:
        """Keep the program's Chrome inside this page, also when it was opened (again) by a job."""
        try:
            if not self.attached():
                self.embed.hwnd = None
                self.search()
            else:
                self.embed.fit()
        finally:
            self.root.after(2000, self.watch)

    def search(self) -> None:
        if self.searching:
            return
        self.searching = True
        want_open, self.opened = self.opened, False

        def work():
            hwnd = None
            try:
                if time.time() - self.pids_at > (20 if self.pids else 8):
                    self.pids, self.pids_at = program_chrome_pids(), time.time()
                windows = chrome_windows(self.pids) if self.pids else []
                if windows:
                    hwnd = windows[0]
                elif want_open:
                    hwnd = find_or_open()
                    self.pids_at = 0.0
            finally:
                self.searching = False
            try:
                self.root.after(0, lambda: self.attach(hwnd, want_open))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def attach(self, hwnd, asked: bool) -> None:
        if not hwnd:
            if asked:
                self.note.config(text="หา Chrome ของโปรแกรมไม่พบ — กด ⚙ › 🧩 ส่วนขยาย ChatGPT เพื่อตรวจการติดตั้ง",
                                 fg="#FCA5A5")
            return
        if self.attached():
            return
        try:
            self.root.update_idletasks()
            self.embed.attach(hwnd)
            self.note.pack_forget()
        except Exception as error:
            self.note.config(text=f"ฝัง Chrome ไม่สำเร็จ: {error}", fg="#FCA5A5")
            self.log(f"[ChatGPT page] {error}")


def install(root: tk.Misc, top_bar: tk.Widget | None, log=print) -> ChatGPTPage:
    return ChatGPTPage(root, top_bar, log)
