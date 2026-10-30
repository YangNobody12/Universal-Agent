#!/usr/bin/env python3
r"""
Universal System-Wide AI Autocomplete for Windows (Typhoon, Ollama, OpenAI, Claude, Gemini ... via API)
Now with a Cute Transparent-Background Cartoon Mascot ("ตัวการ์ตูนวิ่งดุ๊กดิ๊กเข้ามาเขียนให้เราตอนกด Tab")!

Works in EVERY Windows application:
  - Browsers (Chrome, Edge, Firefox — Gmail, Docs, Line, Discord, Facebook, ChatGPT)
  - Editors & Office (Notepad, Word, Excel, PowerPoint, Notion, Obsidian)
  - IDEs & Terminals (VS Code, Antigravity IDE, Cursor, PyCharm, Windows Terminal, PowerShell)

How it works:
  1. Low-Level Win32 Keyboard Hook (WH_KEYBOARD_LL) decodes Thai & English keystrokes in real time
     using the active window's keyboard layout (ToUnicodeEx).
  2. Animated Non-Activating Floating Ghost Overlay (WS_EX_NOACTIVATE | WS_EX_TOPMOST) slides in smoothly
     toward your cursor and previews the AI suggestion without stealing focus.
  3. When you press [Tab], a cute transparent-background Cartoon Mascot holding a glowing magic pen
     literally RUNS across your screen to the cursor, scribbles with sparkles while typing each character
     live into your active window via Win32 SendInput, and does a victory jump when finished!
  4. Pressing [Alt + \] triggers completion on demand in any program.
"""

import ctypes
from ctypes import wintypes
import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request

# Official provider SDKs (requirements.txt). Each is optional: without it that API is called with urllib.
try:
    import openai as openai_sdk
except ImportError:
    openai_sdk = None
try:
    import anthropic as anthropic_sdk
except ImportError:
    anthropic_sdk = None
try:
    import ollama as ollama_sdk
except ImportError:
    ollama_sdk = None

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 1 ms timer resolution: otherwise Tk after() polls and time.sleep() snap to 15.6 ms ticks
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

# =====================================================================
# Win32 API Constants & Structures
# =====================================================================

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Per-monitor DPI awareness so caret coords (Win32/MSAA) and Tk window coords share the same physical pixels.
# Without this, on 125%/150% scaling the mascot lands offset from the real caret.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
HC_ACTION = 0

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12      # Alt
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_PRIOR = 0x21     # Page Up
VK_NEXT = 0x22      # Page Down
VK_END = 0x23
VK_HOME = 0x24
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
CARET_MOVE_KEYS = (VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_HOME, VK_END, VK_PRIOR, VK_NEXT)

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
VK_OEM_5 = 0xDC     # '\' key

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


OUR_INJECT_TAG = 0x5157454E  # 'QWEN' marker so our hook ignores our own injected keys
TRANSPARENT_KEY = "#010203"  # Chroma-key color for 100% transparent cartoon mascot overlay

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL


user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
WM_APP_REHOOK = 0x8000 + 1  # WM_APP + 1: asks the hook thread to reinstall its hooks


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def tick_newer(a: int, b: int, margin: int = 0) -> bool:
    """GetTickCount values wrap every 49.7 days: True if tick a is more than `margin` ms after tick b."""
    d = (a - b) & 0xFFFFFFFF
    return margin < d < 0x80000000


def last_input_tick() -> int:
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    user32.GetLastInputInfo(ctypes.byref(li))
    return li.dwTime


def is_process_elevated(pid: int) -> bool:
    """True if the process runs as administrator. Windows (UIPI) hides an elevated window's keystrokes
    from a non-elevated low-level hook and blocks SendInput into it."""
    TOKEN_QUERY, TOKEN_ELEVATION = 0x0008, 20
    hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not hproc:
        return False
    try:
        token = wintypes.HANDLE()
        if not ctypes.windll.advapi32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(token)):
            return False
        try:
            elevated = wintypes.DWORD(0)
            size = wintypes.DWORD(0)
            ok = ctypes.windll.advapi32.GetTokenInformation(
                token, TOKEN_ELEVATION, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(size)
            )
            return bool(ok and elevated.value)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(hproc)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def foreground_is_elevated() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return is_process_elevated(pid.value)


_window_info_cache = {"hwnd": None, "name": "", "title": ""}

# Apps where Enter usually submits (search box, chat message): the next text is a fresh context
SUBMIT_ON_ENTER_APPS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "line.exe", "discord.exe", "ms-teams.exe", "telegram.exe", "whatsapp.exe", "slack.exe",
}

# Strong code markers only: "=" and "://" also show up in prose, math and URLs
CODE_MARKERS = ("def ", "class ", "import ", "function ", "=>", "{", "};", "#include", "return ", "const ", "let ")


def normalize_title(title: str) -> str:
    """Drops unsaved-change markers (Notepad '*', editors '●') so they don't count as a context change."""
    return title.lstrip("*●• ").strip()


def get_active_window_info():
    """Returns (hwnd, process_name, window_title, thread_id).
    Process name and title are cached per window: this runs inside the low-level keyboard hook, where
    OpenProcess / GetWindowText (which can block on a busy window) on every keystroke adds typing lag."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "unknown", "", 0
    pid = wintypes.DWORD(0)
    thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    # InternalGetWindowText never sends WM_GETTEXT, so it can't block and is cheap enough to re-read
    # every keystroke (the title is how we notice page navigation inside the same browser window).
    buf = ctypes.create_unicode_buffer(512)
    user32.InternalGetWindowText(hwnd, buf, 512)
    title = buf.value
    if _window_info_cache["hwnd"] == hwnd:
        _window_info_cache["title"] = title
        return hwnd, _window_info_cache["name"], title, thread_id

    proc_name = "app.exe"
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if hproc:
        pbuf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
            proc_name = os.path.basename(pbuf.value)
        kernel32.CloseHandle(hproc)
    _window_info_cache.update(hwnd=hwnd, name=proc_name, title=title)
    return hwnd, proc_name, title, thread_id


def trim_buffer(buf: str, limit: int = 1500, keep: int = 1000) -> str:
    """Trims the context buffer in big steps (not 1 char per keystroke) so its start stays stable
    and the server's incremental prefix cache keeps hitting."""
    return buf[-keep:] if len(buf) > limit else buf


# ---------------------------------------------------------------------
# Exact Caret Locator (Win32 System Caret -> MSAA OBJID_CARET -> Mouse fallback)
# ---------------------------------------------------------------------

ole32 = ctypes.windll.ole32
oleacc = ctypes.windll.oleacc
_IS_64 = ctypes.sizeof(ctypes.c_void_p) == 8
OBJID_CARET = 0xFFFFFFF8
VT_I4 = 3


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class VARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort),
        ("val", ctypes.c_longlong),
    ] + ([("pad", ctypes.c_longlong)] if _IS_64 else [])


IID_IAccessible = GUID(
    0x618736E0, 0x3C3D, 0x11CF, (ctypes.c_ubyte * 8)(0x81, 0x0C, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71)
)
oleacc.AccessibleObjectFromWindow.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)
]
oleacc.AccessibleObjectFromWindow.restype = ctypes.c_long
_ACC_LOCATION = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
    ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
    VARIANT,
)
_COM_RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_com_tls = threading.local()


def _ensure_com():
    if not getattr(_com_tls, "init", False):
        try:
            ole32.CoInitialize(None)
        except Exception:
            pass
        _com_tls.init = True


def _msaa_caret_rect(hwnd):
    """Queries the caret via MSAA (works in Chrome, Edge, VS Code/Electron, Word, many modern apps)."""
    if not hwnd:
        return None
    _ensure_com()
    ptr = ctypes.c_void_p()
    try:
        hr = oleacc.AccessibleObjectFromWindow(hwnd, OBJID_CARET, ctypes.byref(IID_IAccessible), ctypes.byref(ptr))
    except Exception:
        return None
    if hr != 0 or not ptr.value:
        return None
    vtbl = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    try:
        l, t, w, h = ctypes.c_long(), ctypes.c_long(), ctypes.c_long(), ctypes.c_long()
        child = VARIANT()
        child.vt = VT_I4
        child.val = 0  # CHILDID_SELF
        hr = _ACC_LOCATION(vtbl[22])(ptr, ctypes.byref(l), ctypes.byref(t), ctypes.byref(w), ctypes.byref(h), child)
        if hr == 0 and h.value > 0 and (l.value != 0 or t.value != 0):
            return l.value, t.value, t.value + h.value
    except Exception:
        return None
    finally:
        try:
            _COM_RELEASE(vtbl[2])(ptr)
        except Exception:
            pass
    return None


def get_caret_rect():
    """
    Returns (x, top, bottom, source) of the real text caret in SCREEN pixels.
      source = 'win32' | 'msaa' | 'mouse'
    """
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        pid = wintypes.DWORD(0)
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        focus = None
        if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            focus = gti.hwndFocus
            rc = gti.rcCaret
            if gti.hwndCaret and (rc.bottom - rc.top) > 0 and (rc.left or rc.top or rc.right or rc.bottom):
                p1 = POINT(rc.left, rc.top)
                p2 = POINT(rc.left, rc.bottom)
                user32.ClientToScreen(gti.hwndCaret, ctypes.byref(p1))
                user32.ClientToScreen(gti.hwndCaret, ctypes.byref(p2))
                return p1.x, p1.y, p2.y, "win32"

        r = _msaa_caret_rect(focus or hwnd)
        if r is None and focus and focus != hwnd:
            r = _msaa_caret_rect(hwnd)
        if r is not None:
            return r[0], r[1], r[2], "msaa"

    mpt = POINT()
    user32.GetCursorPos(ctypes.byref(mpt))
    if mpt.x == 0 and mpt.y == 0:
        return 500, 380, 400, "mouse"
    return mpt.x, mpt.y - 10, mpt.y + 10, "mouse"


def get_caret_or_window_pos():
    """Returns screen (x, y) just below the text caret (used for the preview popup)."""
    x, top, bottom, _src = get_caret_rect()
    return max(0, int(x)), max(0, int(bottom) + 6)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def get_work_area(x: int, y: int):
    """Returns (left, top, right, bottom) of the work area of the monitor containing (x, y)."""
    try:
        hmon = user32.MonitorFromPoint(POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_caret_anchor():
    """Returns (x, bottom, source) of the caret: the exact point the mascot's pen tip should touch."""
    x, top, bottom, src = get_caret_rect()
    return int(x), int(bottom), src


def inject_unicode_string(text: str):
    """Injects arbitrary Unicode text (Thai, English, code) directly into the focused app via SendInput."""
    if not text:
        return
    inputs = []
    utf16_bytes = text.encode("utf-16-le")
    code_units = [int.from_bytes(utf16_bytes[i:i+2], "little") for i in range(0, len(utf16_bytes), 2)]

    for cu in code_units:
        if cu == 0x000D:
            continue
        if cu == 0x000A:
            inp_down = INPUT(type=INPUT_KEYBOARD)
            inp_down.u.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=0, time=0, dwExtraInfo=OUR_INJECT_TAG)
            inp_up = INPUT(type=INPUT_KEYBOARD)
            inp_up.u.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=OUR_INJECT_TAG)
            inputs.extend([inp_down, inp_up])
        else:
            inp_down = INPUT(type=INPUT_KEYBOARD)
            inp_down.u.ki = KEYBDINPUT(wVk=0, wScan=cu, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=OUR_INJECT_TAG)
            inp_up = INPUT(type=INPUT_KEYBOARD)
            inp_up.u.ki = KEYBDINPUT(
                wVk=0,
                wScan=cu,
                dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                time=0,
                dwExtraInfo=OUR_INJECT_TAG,
            )
            inputs.extend([inp_down, inp_up])

    if inputs:
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(INPUT))


def inject_backspaces(n: int):
    """Presses Backspace n times in the focused app (tagged so our own hook ignores it)."""
    inputs = []
    for _ in range(n):
        for flags in (0, KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki = KEYBDINPUT(wVk=VK_BACK, wScan=0, dwFlags=flags, time=0, dwExtraInfo=OUR_INJECT_TAG)
            inputs.append(inp)
    if inputs:
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(INPUT))


def vk_to_unicode(vk_code: int, scan_code: int, thread_id: int) -> str:
    """Translates a virtual key press into its exact Unicode character (Thai or English layout)."""
    state = (ctypes.c_ubyte * 256)()
    if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
        state[VK_SHIFT] = 0x80
    if user32.GetKeyState(0x14) & 0x0001:  # VK_CAPITAL (Caps Lock)
        state[0x14] = 0x01

    hkl = user32.GetKeyboardLayout(thread_id)
    buf = ctypes.create_unicode_buffer(8)
    res = user32.ToUnicodeEx(vk_code, scan_code, state, buf, 8, 0, hkl)
    if res > 0:
        return buf.value[:res]
    return ""


# =====================================================================
# Cartoon Mascot Renderer (Chibi Qwen Bot with Cape, Running Legs & Magic Pen)
# =====================================================================

# Preview popup frame insets: used to line the ghost text up with the caret's x position.
OVERLAY_PADX = 8
OVERLAY_BORDER = 2
OVERLAY_GAP = 3  # px between caret bottom and popup top

# The mascot is designed on a 340x155 grid, then scaled down by MASCOT_SCALE.
MASCOT_SCALE = 0.5
MASCOT_W = int(340 * MASCOT_SCALE)
MASCOT_H = int(155 * MASCOT_SCALE)
# Pen-nib tip in scaled canvas coords: this exact pixel is placed on the caret.
PEN_TIP_X = int(18 * MASCOT_SCALE)
PEN_TIP_Y = int(128 * MASCOT_SCALE)


def draw_cartoon_mascot(canvas: tk.Canvas, state: str, frame: int, bubble_text: str = "", progress: float = 0.0):
    """
    Draws an animated cute Cartoon Character on a transparent canvas:
      - state == 'run'     : Running left toward the cursor with dust clouds & swinging legs/cape
      - state == 'write'   : Scribbling fast with a glowing magic pen + flying star sparkles
      - state == 'victory' : Jumping in the air cheering with sparkles!
    """
    canvas.delete("all")

    # Base anchor of the character
    cx = 92
    bob = int(math.sin(frame * 0.9) * 4) if state == "run" else (
        int(math.sin(frame * 1.4) * 2) if state == "write" else -12
    )
    cy = 102 + bob

    # 1. Dust puffs when running or sparkle burst when writing
    if state == "run":
        for i in range(3):
            dx = cx + 30 + i * 16 + (frame % 3) * 4
            dy = 136 - i * 4 - (frame % 2) * 3
            r = 8 - i * 2
            canvas.create_oval(dx - r, dy - r, dx + r, dy + r, fill="#cbd5e1", outline="#94a3b8", width=1)
        # Speed lines
        canvas.create_line(cx + 38, cy - 18, cx + 68, cy - 18, fill="#38bdf8", width=2)
        canvas.create_line(cx + 44, cy + 2, cx + 78, cy + 2, fill="#34d399", width=2)

    # 2. Fluttering Hero Scarf / Cape behind the character
    cape_wave = int(math.sin(frame * 1.1) * 7)
    canvas.create_polygon(
        cx + 6, cy - 2,
        cx + 42, cy - 8 + cape_wave,
        cx + 48, cy + 10 + cape_wave,
        cx + 10, cy + 14,
        fill="#f43f5e",
        outline="#be123c",
        width=2,
    )

    # 3. Animated Legs (Running cycle vs Writing stance vs Victory jump)
    if state == "run":
        stride = math.sin(frame * 1.25) * 16
        l_foot_x, l_foot_y = cx - 8 + int(stride), 138 - int(max(0, -stride * 0.4))
        r_foot_x, r_foot_y = cx + 8 - int(stride), 138 - int(max(0, stride * 0.4))
    elif state == "write":
        l_foot_x, l_foot_y = cx - 12, 138
        r_foot_x, r_foot_y = cx + 10, 138
    else:  # victory jump
        l_foot_x, l_foot_y = cx - 16, 126
        r_foot_x, r_foot_y = cx + 16, 126

    # Draw legs & cute boots
    canvas.create_line(cx - 6, cy + 16, l_foot_x, l_foot_y, fill="#1e293b", width=6, capstyle=tk.ROUND)
    canvas.create_line(cx + 6, cy + 16, r_foot_x, r_foot_y, fill="#1e293b", width=6, capstyle=tk.ROUND)
    canvas.create_oval(l_foot_x - 8, l_foot_y - 4, l_foot_x + 6, l_foot_y + 5, fill="#38bdf8", outline="#0284c7", width=2)
    canvas.create_oval(r_foot_x - 8, r_foot_y - 4, r_foot_x + 6, r_foot_y + 5, fill="#38bdf8", outline="#0284c7", width=2)

    # 4. Cute Chibi Body (Torso with glowing Qwen core)
    canvas.create_oval(cx - 18, cy - 8, cx + 18, cy + 22, fill="#f8fafc", outline="#0284c7", width=2)
    canvas.create_oval(cx - 10, cy - 1, cx + 10, cy + 15, fill="#0f172a", outline="#38bdf8", width=1)
    # Glowing core heart/bolt
    core_col = "#34d399" if frame % 2 == 0 else "#38bdf8"
    canvas.create_oval(cx - 4, cy + 4, cx + 4, cy + 11, fill=core_col, outline="")

    # 5. Cute Chibi Head + Cat/Bot Ears + Antenna
    hx, hy = cx - 2, cy - 26
    # Left & Right Cyber Cat Ears
    canvas.create_polygon(hx - 22, hy - 8, hx - 18, hy - 28, hx - 4, hy - 16, fill="#38bdf8", outline="#0284c7", width=2)
    canvas.create_polygon(hx + 22, hy - 8, hx + 18, hy - 28, hx + 4, hy - 16, fill="#38bdf8", outline="#0284c7", width=2)
    canvas.create_polygon(hx - 18, hy - 11, hx - 16, hy - 22, hx - 8, hy - 15, fill="#f472b6", outline="")
    canvas.create_polygon(hx + 18, hy - 11, hx + 16, hy - 22, hx + 8, hy - 15, fill="#f472b6", outline="")

    # Glowing Antenna on top
    ant_bob = int(math.cos(frame * 1.3) * 3)
    canvas.create_line(hx, hy - 18, hx + ant_bob, hy - 32, fill="#38bdf8", width=3)
    canvas.create_oval(
        hx + ant_bob - 5, hy - 37, hx + ant_bob + 5, hy - 27,
        fill="#fbbf24" if frame % 2 == 0 else "#34d399",
        outline="#f59e0b",
        width=2,
    )

    # Head Helmet & Dark Visor Face
    canvas.create_oval(hx - 25, hy - 20, hx + 25, hy + 20, fill="#f8fafc", outline="#0284c7", width=2)
    canvas.create_oval(hx - 20, hy - 12, hx + 18, hy + 14, fill="#0f172a", outline="#38bdf8", width=2)

    # Rosy Blush Cheeks
    canvas.create_oval(hx - 17, hy + 3, hx - 10, hy + 8, fill="#f472b6", outline="")
    canvas.create_oval(hx + 6, hy + 3, hx + 13, hy + 8, fill="#f472b6", outline="")

    # Expressive Eyes & Mouth
    if state == "write":
        # Super focused ">  <" or sparkling eyes while scribbling
        canvas.create_line(hx - 13, hy - 4, hx - 7, hy - 1, fill="#34d399", width=2)
        canvas.create_line(hx - 13, hy + 2, hx - 7, hy - 1, fill="#34d399", width=2)
        canvas.create_line(hx + 9, hy - 4, hx + 3, hy - 1, fill="#34d399", width=2)
        canvas.create_line(hx + 9, hy + 2, hx + 3, hy - 1, fill="#34d399", width=2)
        # Cute open mouth
        canvas.create_oval(hx - 4, hy + 4, hx + 2, hy + 9, fill="#fb7185", outline="")
    elif state == "victory":
        # Happy cheering "^  ^" eyes
        canvas.create_arc(hx - 14, hy - 5, hx - 5, hy + 4, start=0, extent=180, style=tk.ARC, outline="#fbbf24", width=3)
        canvas.create_arc(hx + 1, hy - 5, hx + 10, hy + 4, start=0, extent=180, style=tk.ARC, outline="#fbbf24", width=3)
        canvas.create_arc(hx - 6, hy + 2, hx + 4, hy + 10, start=180, extent=180, style=tk.ARC, outline="#34d399", width=2)
    else:
        # Cute glowing cyan eyes looking toward the cursor
        canvas.create_oval(hx - 14, hy - 5, hx - 6, hy + 3, fill="#38bdf8", outline="")
        canvas.create_oval(hx + 2, hy - 5, hx + 10, hy + 3, fill="#38bdf8", outline="")
        canvas.create_oval(hx - 12, hy - 4, hx - 9, hy - 1, fill="#ffffff", outline="")
        canvas.create_oval(hx + 4, hy - 4, hx + 7, hy - 1, fill="#ffffff", outline="")
        canvas.create_arc(hx - 5, hy + 3, hx + 3, hy + 9, start=180, extent=180, style=tk.ARC, outline="#34d399", width=2)

    # 6. Arms + Giant Glowing Magic Pen!
    if state in ("run", "write"):
        pen_wiggle = int(math.sin(frame * 2.1) * 7) if state == "write" else int(math.sin(frame * 1.1) * 3)
        hand_x, hand_y = cx - 22, cy + 4 + (pen_wiggle // 2)
        canvas.create_line(cx - 12, cy + 2, hand_x, hand_y, fill="#f8fafc", width=6, capstyle=tk.ROUND)

        # Magic Pen from (tx, ty) at the caret tip up to (px2, py2).
        # The nib stays pinned on the caret; only the pen's upper end wiggles.
        tx, ty = 18, 128
        px2, py2 = tx + 44, ty - 34 + pen_wiggle
        # Pen body
        canvas.create_line(tx + 7, ty - 5, px2, py2, fill="#fbbf24", width=8, capstyle=tk.ROUND)
        canvas.create_line(tx + 7, ty - 5, px2, py2, fill="#fef08a", width=3, capstyle=tk.ROUND)
        # Pink Eraser cap
        canvas.create_line(px2, py2, px2 + 7, py2 - 5, fill="#f472b6", width=8, capstyle=tk.ROUND)
        # Glowing Nib at the writing point!
        nib_col = "#34d399" if frame % 2 == 0 else "#38bdf8"
        canvas.create_polygon(tx, ty, tx + 10, ty - 3, tx + 6, ty - 9, fill=nib_col, outline="#ffffff", width=1)

        # Sparkles shooting off the magic pen tip while writing!
        if state == "write":
            spark_colors = ("#fbbf24", "#34d399", "#38bdf8", "#f472b6")
            for s_idx in range(4):
                ang = (frame * 0.9) + s_idx * 1.57
                dist = 8 + ((frame * 3 + s_idx * 5) % 16)
                sx = tx + int(math.cos(ang) * dist)
                sy = ty + int(math.sin(ang) * dist)
                c = spark_colors[(frame + s_idx) % len(spark_colors)]
                canvas.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill=c, outline="")
    else:
        # Victory raised arms!
        canvas.create_line(cx - 14, cy + 2, cx - 28, cy - 16, fill="#f8fafc", width=6, capstyle=tk.ROUND)
        canvas.create_line(cx + 14, cy + 2, cx + 28, cy - 16, fill="#f8fafc", width=6, capstyle=tk.ROUND)
        for s_idx in range(5):
            ang = frame * 0.7 + s_idx * 1.25
            sx = cx + int(math.cos(ang) * 42)
            sy = cy - 10 + int(math.sin(ang) * 28)
            canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill="#fbbf24", outline="")

    # 7. Speech Bubble above the cartoon mascot
    if bubble_text:
        bx1, by1, bx2, by2 = 10, 6, 330, 44
        border_col = "#fbbf24" if state == "write" else ("#34d399" if state == "victory" else "#38bdf8")
        canvas.create_rectangle(bx1, by1, bx2, by2, fill="#0f172a", outline=border_col, width=2)
        # Speech bubble pointer triangle pointing down to head
        canvas.create_polygon(cx - 6, by2, cx + 6, by2, cx - 2, by2 + 9, fill="#0f172a", outline=border_col, width=2)
        canvas.create_text(
            bx1 + 10,
            (by1 + by2) // 2 - 2,
            text=bubble_text[:26],
            anchor="w",
            fill="#f8fafc",
            font=("Segoe UI", 7, "bold"),
        )
        # Mini progress bar inside speech bubble when writing
        if state == "write" and progress > 0:
            bar_w = int((bx2 - bx1 - 16) * min(1.0, progress))
            canvas.create_rectangle(bx1 + 8, by2 - 6, bx1 + 8 + bar_w, by2 - 3, fill="#34d399", outline="")

    # Shrink the whole drawing (coords and stroke widths) to MASCOT_SCALE
    if MASCOT_SCALE != 1.0:
        canvas.scale("all", 0, 0, MASCOT_SCALE, MASCOT_SCALE)
        for item in canvas.find_all():
            if canvas.type(item) == "text":
                continue
            try:
                w = float(canvas.itemcget(item, "width"))
                canvas.itemconfig(item, width=max(1, round(w * MASCOT_SCALE)))
            except Exception:
                pass


# =====================================================================
# Completion Providers (cloud APIs and local runtimes like Ollama / LM Studio, all over HTTP)
# =====================================================================

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ua_config.json")

DEFAULT_CONFIG = {
    "provider": "typhoon",
    "temperature": 0.2,
    "timeout": 10,
    "skip_apps": [],         # process names to leave alone, e.g. ["code.exe"] if a VS Code extension handles it
    "run_as_admin": False,   # also works in programs running as administrator (asks UAC once at start)
    "spellcheck": True,      # suggest fixes for misspelled words in the line being typed (Tab = fix)
}

# api: "openai" = /chat/completions, "ollama" = native /api/chat, "anthropic" = /messages.
# An empty model means "use the first model the server lists" (handy for Ollama / LM Studio).
PROVIDERS = {
    "ollama":     {"label": "Ollama (local)", "api": "ollama", "base_url": "http://127.0.0.1:11434", "model": ""},
    "lmstudio":   {"label": "LM Studio (local)", "api": "openai", "base_url": "http://127.0.0.1:1234/v1", "model": ""},
    "llamacpp":   {"label": "llama.cpp server (local)", "api": "openai", "base_url": "http://127.0.0.1:8080/v1", "model": ""},
    "vllm":       {"label": "vLLM / SGLang (local)", "api": "openai", "base_url": "http://127.0.0.1:8000/v1", "model": ""},
    "typhoon":    {"label": "Typhoon (SCB 10X)", "api": "openai", "base_url": "https://api.opentyphoon.ai/v1",
                   "model": "typhoon-v2.5-30b-a3b-instruct", "key_env": "TYPHOON_API_KEY"},
    "openai":     {"label": "OpenAI", "api": "openai", "base_url": "https://api.openai.com/v1",
                   "model": "gpt-4.1-mini", "key_env": "OPENAI_API_KEY", "max_tokens_param": "max_completion_tokens"},
    "anthropic":  {"label": "Anthropic Claude", "api": "anthropic", "base_url": "https://api.anthropic.com/v1",
                   "model": "claude-haiku-5-5", "key_env": "ANTHROPIC_API_KEY"},
    "gemini":     {"label": "Google Gemini", "api": "openai",
                   "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                   "model": "gemini-2.5-flash", "key_env": "GEMINI_API_KEY"},
    "openrouter": {"label": "OpenRouter", "api": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "model": "openai/gpt-4.1-mini", "key_env": "OPENROUTER_API_KEY"},
    "groq":       {"label": "Groq", "api": "openai", "base_url": "https://api.groq.com/openai/v1",
                   "model": "llama-3.3-70b-versatile", "key_env": "GROQ_API_KEY"},
    "deepseek":   {"label": "DeepSeek", "api": "openai", "base_url": "https://api.deepseek.com/v1",
                   "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
    "together":   {"label": "Together AI", "api": "openai", "base_url": "https://api.together.xyz/v1",
                   "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "key_env": "TOGETHER_API_KEY"},
    "mistral":    {"label": "Mistral", "api": "openai", "base_url": "https://api.mistral.ai/v1",
                   "model": "mistral-small-latest", "key_env": "MISTRAL_API_KEY"},
    "custom":     {"label": "Custom (OpenAI-compatible)", "api": "openai", "base_url": "http://127.0.0.1:8000/v1",
                   "model": ""},
}
PROFILE_KEYS = ("base_url", "model", "api_key")

CHAT_AUTOCOMPLETE_SYSTEM = (
    "You are an inline autocomplete engine, like the grey ghost text in a phone keyboard or code editor. "
    "The user message is text someone is typing right now; the cursor is at its very end. "
    "Reply with ONLY the characters that should come next to continue it naturally, in the same language "
    "and style. Never repeat the given text, never answer or explain it, no quotes, no markdown."
)


def load_raw_config(path: str = CONFIG_PATH) -> dict:
    """ua_config.json as written, with old single-provider files moved to the per-provider "profiles" layout:
    {"provider": "typhoon", "profiles": {"typhoon": {"api_key": ..., "model": ...}, "ollama": {...}}, ...}"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = {}
    except Exception as e:
        print(f"[config] ignoring {path}: {e}")
        raw = {}
    provider = str(raw.get("provider", DEFAULT_CONFIG["provider"])).lower()
    if provider == "openai" and "opentyphoon" in str(raw.get("base_url", "")):
        provider = "typhoon"  # older files used "openai" + Typhoon's base_url
    if provider == "local":
        provider = DEFAULT_CONFIG["provider"]  # the bundled Qwen FIM server is no longer supported
    profiles = {k: dict(v) for k, v in raw.get("profiles", {}).items()}
    legacy = {k: raw.pop(k) for k in PROFILE_KEYS if k in raw}
    if legacy:
        profiles[provider] = {**legacy, **profiles.get(provider, {})}
    raw["provider"] = provider
    raw["profiles"] = profiles
    return raw


def resolve_config(raw: dict, provider: str = None) -> dict:
    """Effective settings: defaults < provider preset < saved profile < UA_* environment variables."""
    provider = (provider or os.environ.get("UA_PROVIDER") or raw.get("provider") or DEFAULT_CONFIG["provider"]).lower()
    if provider not in PROVIDERS:
        print(f"[config] unknown provider {provider!r}; treating it as an OpenAI-compatible server")
    preset = PROVIDERS.get(provider, PROVIDERS["custom"])
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in raw.items() if k != "profiles"})
    cfg.update({k: v for k, v in preset.items() if k != "label"})
    cfg.update({"api_key": ""})
    cfg.update(raw.get("profiles", {}).get(provider, {}))
    for key in PROFILE_KEYS:
        val = os.environ.get(f"UA_{key.upper()}")
        if val:
            cfg[key] = val
    if not cfg["api_key"] and preset.get("key_env"):
        cfg["api_key"] = os.environ.get(preset["key_env"], "")
    cfg["provider"] = provider
    return cfg


def load_config(path: str = CONFIG_PATH) -> dict:
    """ua_config.json next to this script, then UA_* environment variables, override the defaults."""
    return resolve_config(load_raw_config(path))


def save_provider_choice(provider: str, model: str, base_url: str, api_key: str, path: str = CONFIG_PATH):
    """Makes `provider` active and remembers its model / URL / key, keeping the other providers' profiles."""
    raw = load_raw_config(path)
    raw["provider"] = provider
    prof = raw["profiles"].setdefault(provider, {})
    preset = PROVIDERS.get(provider, PROVIDERS["custom"])
    for key, val in (("model", model), ("base_url", base_url)):
        if val and val != preset.get(key):
            prof[key] = val
        else:
            prof.pop(key, None)
    if api_key:
        prof["api_key"] = api_key
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def provider_label(cfg: dict) -> str:
    return f"{cfg['provider']}:{cfg.get('model') or '?'}"


class ProviderError(RuntimeError):
    pass


def _http_json(url: str, payload, headers: dict, timeout: float, method: str = None) -> dict:
    """POSTs (or GETs when payload is None) JSON; API errors are raised with the server's message."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers},
                                 method=method or ("GET" if payload is None else "POST"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise ProviderError(f"HTTP {e.code} from {url}: {body}") from None


def _auth_headers(cfg: dict) -> dict:
    if not cfg.get("api_key"):
        return {}
    if cfg["api"] == "anthropic":
        return {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {cfg['api_key']}"}


def list_models(cfg: dict) -> list:
    """Model names the provider's server offers (local runtimes: whatever is downloaded)."""
    base = cfg["base_url"].rstrip("/")
    if cfg["api"] == "ollama":
        res = _http_json(f"{base}/api/tags", None, {}, cfg["timeout"])
        return [m["name"] for m in res.get("models", [])]
    res = _http_json(f"{base}/models", None, _auth_headers(cfg), cfg["timeout"])
    return [m["id"] for m in res.get("data", [])]


def resolve_model(cfg: dict) -> str:
    if not cfg.get("model"):
        models = list_models(cfg)
        if not models:
            raise ProviderError(f"{cfg['provider']}: no models found at {cfg['base_url']} (e.g. `ollama pull qwen3`)")
        cfg["model"] = models[0]
    return cfg["model"]


def strip_reasoning(text: str) -> str:
    """Reasoning models (Qwen3, DeepSeek-R1 ...) on local runtimes may prepend <think>...</think>."""
    import re

    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S)
    return "" if "<think>" in text else text  # cut off mid-thought by max_tokens: nothing usable


_sdk_clients = {}


def _sdk_client(kind: str, factory, base: str, key: str, timeout: float):
    """One client per endpoint: SDK clients keep HTTP connections alive, so later requests skip the TLS handshake."""
    ck = (kind, base, key, timeout)
    if ck not in _sdk_clients:
        _sdk_clients[ck] = factory()
    return _sdk_clients[ck]


def _send_anthropic(cfg: dict, base: str, payload: dict) -> str:
    timeout = cfg["timeout"]
    if anthropic_sdk is not None:
        sdk_base = base[:-3] if base.endswith("/v1") else base  # the SDK appends /v1/messages itself
        client = _sdk_client("anthropic", lambda: anthropic_sdk.Anthropic(
            api_key=cfg["api_key"], base_url=sdk_base, timeout=timeout, max_retries=0), sdk_base, cfg["api_key"], timeout)
        try:
            res = client.messages.create(**payload)
        except anthropic_sdk.APIStatusError as e:
            raise ProviderError(f"HTTP {e.status_code} from {sdk_base}: {e.message}") from None
        return "".join(b.text for b in res.content if b.type == "text")
    res = _http_json(f"{base}/messages", payload, _auth_headers(cfg), timeout)
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")


def _send_ollama(cfg: dict, base: str, payload: dict) -> str:
    timeout = cfg["timeout"]
    if ollama_sdk is not None:
        client = _sdk_client("ollama", lambda: ollama_sdk.Client(host=base, timeout=timeout), base, "", timeout)
        try:
            res = client.chat(**payload)
        except ollama_sdk.ResponseError as e:
            raise ProviderError(f"HTTP {e.status_code} from {base}: {e.error}") from None
        return res.message.content or ""
    res = _http_json(f"{base}/api/chat", payload, {}, timeout)
    return res.get("message", {}).get("content") or ""


def _send_openai(cfg: dict, base: str, payload: dict) -> str:
    timeout = cfg["timeout"]
    if openai_sdk is not None:
        # Local servers (Ollama, LM Studio ...) take no key, but the SDK insists on one
        key = cfg["api_key"] or "not-needed"
        client = _sdk_client("openai", lambda: openai_sdk.OpenAI(
            api_key=key, base_url=base, timeout=timeout, max_retries=0), base, key, timeout)
        try:
            res = client.chat.completions.create(**payload)
        except openai_sdk.APIStatusError as e:
            raise ProviderError(f"HTTP {e.status_code} from {base}: {e.message}") from None
        return res.choices[0].message.content or ""
    res = _http_json(f"{base}/chat/completions", payload, _auth_headers(cfg), timeout)
    return res["choices"][0]["message"].get("content") or ""


def chat(cfg: dict, system: str, user: str, max_tokens: int, temperature: float, stop=None) -> str:
    """One non-streaming chat turn against whichever API style the provider speaks.
    Uses the official SDK (openai / anthropic / ollama) when installed, plain urllib otherwise."""
    api = cfg["api"]
    model = resolve_model(cfg)
    base = cfg["base_url"].rstrip("/")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    if api == "anthropic":
        # Current Claude models take no sampling params, and whitespace-only stop sequences are
        # rejected; callers cut at "\n" themselves
        payload = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages[1:]}
        return strip_reasoning(_send_anthropic(cfg, base, payload))

    if api == "ollama":
        options = {"temperature": temperature, "num_predict": max_tokens}
        if stop:
            options["stop"] = stop
        payload = {"model": model, "messages": messages, "stream": False, "think": False,
                   "keep_alive": "30m", "options": options}
        try:
            return strip_reasoning(_send_ollama(cfg, base, payload))
        except ProviderError as e:
            if "think" not in str(e):
                raise
            payload.pop("think")  # older Ollama / models without a thinking switch
            return strip_reasoning(_send_ollama(cfg, base, payload))

    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": False,
               cfg.get("max_tokens_param", "max_tokens"): max_tokens}
    if stop:
        payload["stop"] = stop
    # OpenAI-compatible servers disagree on a few parameters: adapt once per complaint and retry
    for _ in range(4):
        try:
            text = _send_openai(cfg, base, payload)
            break
        except ProviderError as e:
            msg = str(e)
            if "HTTP 400" not in msg:
                raise
            if "max_completion_tokens" in msg and "max_tokens" in payload:
                payload["max_completion_tokens"] = payload.pop("max_tokens")
            elif "max_tokens" in msg and "max_completion_tokens" in payload:
                payload["max_tokens"] = payload.pop("max_completion_tokens")
            elif "temperature" in msg and "temperature" in payload:
                payload.pop("temperature")
            elif "stop" in msg and "stop" in payload:
                payload.pop("stop")
            else:
                raise
    else:
        raise ProviderError(f"{cfg['provider']}: request rejected after parameter fallbacks")
    return strip_reasoning(text)


def build_chat_messages(context_text: str, is_code: bool) -> list:
    hint = "Continue this code." if is_code else "Continue this text with a few words (one line at most)."
    return [
        {"role": "system", "content": CHAT_AUTOCOMPLETE_SYSTEM},
        {"role": "user", "content": f"{hint}\n<text>{context_text}</text>"},
    ]


def clean_chat_completion(context_text: str, comp: str) -> str:
    """Chat models sometimes echo the tail of the input or wrap output in tags/fences; strip that."""
    comp = comp.replace("<text>", "").replace("</text>", "")
    if comp.startswith("```"):
        comp = comp.split("\n", 1)[1] if "\n" in comp else ""
        comp = comp.rsplit("```", 1)[0]
    tail = context_text[-60:]
    for n in range(min(len(tail), len(comp)), 2, -1):
        if comp.startswith(tail[-n:]):
            comp = comp[n:]
            break
    # Keep the word boundary consistent with what's already typed
    if context_text.endswith((" ", "\n", "\t")):
        comp = comp.lstrip(" ")
    return comp


SPELLCHECK_SYSTEM = (
    "You are a Thai and English spell checker. The user message is a line someone is typing. "
    "Return the SAME text with only spelling mistakes fixed: typos, missing or extra letters, wrong vowels "
    "or tone marks (e.g. กาทำงาน -> การทำงาน, recieve -> receive). Do not change word choice, grammar, "
    "style, punctuation or spacing, do not translate, answer or continue the text. "
    "If there is no mistake, return the text unchanged. Output only the text."
)


def spellcheck_segment(buffer: str, limit: int = 120) -> str:
    """The part of the typed buffer worth spell-checking: the current line, at most `limit` chars,
    cut at a space so we don't start mid-word."""
    seg = buffer.rsplit("\n", 1)[-1]
    if len(seg) > limit:
        seg = seg[-limit:]
        if " " in seg:
            seg = seg.split(" ", 1)[1]
    return seg


def find_spelling_fix(original: str, corrected: str):
    """Compares a segment with its spell-checked version.
    Returns (backspaces, retype, wrong, right) or None: delete `backspaces` chars before the caret and type
    `retype`; `wrong` -> `right` is the changed span (expanded to whole words) for display."""
    import difflib

    corrected = corrected.strip("\r\n").removeprefix("<text>").removesuffix("</text>")
    if not corrected.strip() or corrected == original:
        return None
    # Model rewrote / answered / translated instead of fixing typos
    if difflib.SequenceMatcher(None, original, corrected).ratio() < 0.75:
        return None
    if corrected.strip() == original.strip():
        return None  # whitespace-only difference
    p = 0
    while p < min(len(original), len(corrected)) and original[p] == corrected[p]:
        p += 1
    if p == len(original):
        return None  # pure append = a completion of an unfinished word, not a typo
    s = 0
    while s < min(len(original), len(corrected)) - p and original[-1 - s] == corrected[-1 - s]:
        s += 1
    # Thai combining vowels/tone marks can't start a span; back up to the base consonant
    while p > 0 and "ั" <= original[p] <= "๎" and original[p] not in "ำเแโใไ":
        p -= 1
    # Display span: a few chars of context each side (Thai has no spaces between words), never starting
    # or ending in the middle of a vowel/tone-mark cluster
    def is_mark(ch):
        return "ั" <= ch <= "๎" and ch not in "าำเแโใไๅๆ"

    w0 = max(original.rfind(" ", 0, p) + 1, p - 4)
    while w0 > 0 and is_mark(original[w0]):
        w0 -= 1
    end_o, end_c = len(original) - s, len(corrected) - s
    w1o = original.find(" ", end_o)
    w1o = min(len(original) if w1o < 0 else w1o, end_o + 4)
    while w1o < len(original) and is_mark(original[w1o]):
        w1o += 1
    w1c = end_c + (w1o - end_o)
    return len(original) - p, corrected[p:], original[w0:w1o], corrected[w0:w1c]


def request_spellcheck(cfg: dict, segment: str) -> str:
    """Returns the spell-corrected segment (chat endpoint of the remote provider or the local server)."""
    # Thai often costs ~1 token per character on non-Thai tokenizers, so leave room
    return chat(cfg, SPELLCHECK_SYSTEM, segment, max(16, len(segment) * 3), 0)


def request_completion(cfg: dict, context_text: str, is_code: bool, max_tokens: int) -> dict:
    """Returns {"completion": str, "stats": str}. Raises on network/API errors."""
    msgs = build_chat_messages(context_text[-2000:], is_code)
    t0 = time.perf_counter()
    comp = chat(cfg, msgs[0]["content"], msgs[1]["content"], max_tokens, cfg["temperature"],
                stop=None if is_code else ["\n"])
    ms = (time.perf_counter() - t0) * 1000.0
    return {
        "completion": clean_chat_completion(context_text, comp),
        "stats": f"{provider_label(cfg)} | {ms:.0f} ms",
    }


# =====================================================================
# Universal Autocomplete Application (Cartoon Mascot + Ghost Overlay)
# =====================================================================

class UniversalAutocompleteApp:
    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.skip_apps = {str(a).lower() for a in self.config.get("skip_apps", [])}
        self.spellcheck = bool(self.config.get("spellcheck", True))
        self._spell_cache = {}
        self.fix_seq = -1           # req_seq whose spelling fix is on screen (completion must not replace it)
        self.suggestion_backspaces = 0  # chars to delete before typing current_suggestion (spelling fix)
        self.hook_thread_id = 0
        self.last_hook_tick = kernel32.GetTickCount()
        self.enabled = True
        self.auto_trigger = True
        self.animate_typing = True
        self.auto_write = False
        self.typing_delay_ms = 18
        self.debounce_ms = 250
        self.max_tokens = 28

        self.current_hwnd = 0
        self.current_app = ""
        self.current_title = ""
        self.buffer = ""
        self.current_suggestion = ""
        self.suggestion_visible = False
        self.is_writing_anim = False
        self.abort_writing = False
        self.last_keystroke_time = 0.0
        self.req_seq = 0
        self.anim_id = None
        self.mascot_anim_id = None
        self.auto_write_timer_id = None
        self.target_x = 500
        self.target_y = 400
        self.write_progress_idx = 0
        self.write_progress_total = 1
        self.write_current_text = ""

        self.ui_queue = queue.Queue()
        self.root = tk.Tk()
        self.setup_control_center()
        self.setup_ghost_overlay()
        self.setup_cartoon_mascot_overlay()

        # Start keyboard hook thread & debounce watcher thread
        self.hook_thread = threading.Thread(target=self.run_keyboard_hook, daemon=True)
        self.hook_thread.start()

        self.watcher_thread = threading.Thread(target=self.debounce_loop, daemon=True)
        self.watcher_thread.start()

        self.hook_watchdog_thread = threading.Thread(target=self.hook_watchdog_loop, daemon=True)
        self.hook_watchdog_thread.start()

        self.root.after(20, self.process_ui_queue)

    def setup_control_center(self):
        self.root.title("🤖 Universal AI Autocomplete (ตัวการ์ตูนวิ่งมาเขียนให้ตอนกด Tab!)")
        self.root.geometry("760x720")
        self.root.configure(bg="#0f172a")

        hdr = tk.Frame(self.root, bg="#1e293b", padx=16, pady=12)
        hdr.pack(fill=tk.X)
        tk.Label(
            hdr,
            text="🤖 Universal AI Autocomplete + Cartoon Mascot Writer!",
            font=("Segoe UI", 12, "bold"),
            fg="#34d399",
            bg="#1e293b",
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text="พิมพ์ในโปรแกรมไหนก็ได้ -> พอกด [Tab] ตัวการ์ตูน Qwen-Bot จะวิ่งดุ๊กดิ๊กถือปากกาวิเศษมาเขียนข้อความให้ถึงที่เคอร์เซอร์!",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#1e293b",
        ).pack(anchor="w", pady=(2, 0))

        # Controls frame
        ctrl = tk.Frame(self.root, bg="#0f172a", padx=16, pady=8)
        ctrl.pack(fill=tk.X)

        self.var_enabled = tk.BooleanVar(value=True)
        self.var_auto = tk.BooleanVar(value=True)
        self.var_anim_type = tk.BooleanVar(value=True)
        self.var_auto_write = tk.BooleanVar(value=False)

        tk.Checkbutton(
            ctrl,
            text="เปิดใช้งาน Universal Autocomplete (Ctrl+Shift+Q)",
            variable=self.var_enabled,
            command=self.on_toggle_settings,
            font=("Segoe UI", 10, "bold"),
            fg="#f8fafc",
            bg="#0f172a",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#34d399",
        ).grid(row=0, column=0, sticky="w", padx=(0, 18))

        tk.Checkbutton(
            ctrl,
            text="แนะนำอัตโนมัติขณะพิมพ์ (หรือกด Alt + \\)",
            variable=self.var_auto,
            command=self.on_toggle_settings,
            font=("Segoe UI", 10),
            fg="#cbd5e1",
            bg="#0f172a",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#34d399",
        ).grid(row=0, column=1, sticky="w")

        tk.Checkbutton(
            ctrl,
            text="🏃‍♂️✨ ตัวการ์ตูนวิ่งออกมาเขียนให้ตอนกด [Tab]",
            variable=self.var_anim_type,
            command=self.on_toggle_settings,
            font=("Segoe UI", 10, "bold"),
            fg="#38bdf8",
            bg="#0f172a",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#38bdf8",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0), padx=(0, 18))

        tk.Checkbutton(
            ctrl,
            text="🤖 Auto-Write (ให้ตัวการ์ตูนวิ่งมาเขียนเองโดยไม่ต้องกด Tab)",
            variable=self.var_auto_write,
            command=self.on_toggle_settings,
            font=("Segoe UI", 10, "bold"),
            fg="#fbbf24",
            bg="#0f172a",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#fbbf24",
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))

        # Speed slider + Demo button row
        spd_frame = tk.Frame(self.root, bg="#0f172a", padx=16, pady=4)
        spd_frame.pack(fill=tk.X)
        tk.Label(
            spd_frame,
            text="⚡ ความเร็วตอนการ์ตูนเขียน (ms/ตัวอักษร):",
            font=("Segoe UI", 9, "bold"),
            fg="#94a3b8",
            bg="#0f172a",
        ).pack(side=tk.LEFT)

        self.speed_scale = tk.Scale(
            spd_frame,
            from_=6,
            to=40,
            orient=tk.HORIZONTAL,
            bg="#0f172a",
            fg="#34d399",
            highlightthickness=0,
            troughcolor="#1e293b",
            length=180,
            command=self.on_speed_change,
        )
        self.speed_scale.set(18)
        self.speed_scale.pack(side=tk.LEFT, padx=8)

        tk.Button(
            spd_frame,
            text="🎬 กดทดสอบดูตัวการ์ตูนวิ่งมาเขียน!",
            command=self.run_demo_cartoon,
            font=("Segoe UI", 9, "bold"),
            bg="#10b981",
            fg="#0f172a",
            activebackground="#34d399",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        self.setup_provider_panel()

        # Live Telemetry frame
        info = tk.Frame(self.root, bg="#1e293b", padx=14, pady=10)
        info.pack(fill=tk.X, padx=16, pady=6)

        self.lbl_app = tk.Label(
            info,
            text="Active App: กำลังตรวจจับหน้าต่างที่ใช้งาน...",
            font=("Consolas", 10, "bold"),
            fg="#38bdf8",
            bg="#1e293b",
        )
        self.lbl_app.pack(anchor="w")

        self.lbl_stats = tk.Label(
            info,
            text=self.provider_status_text(),
            font=("Consolas", 10),
            fg="#34d399",
            bg="#1e293b",
        )
        self.lbl_stats.pack(anchor="w", pady=(2, 0))

        self.lbl_warn = tk.Label(info, text="", font=("Segoe UI", 9, "bold"), fg="#fbbf24", bg="#1e293b")
        self.lbl_warn.pack(anchor="w")

        self.lbl_buf = tk.Label(
            info,
            text="Context Buffer: (ลองพิมพ์ข้อความในโปรแกรมใดก็ได้ หรือกดปุ่มสีเขียวด้านบนเพื่อดูตัวการ์ตูนวิ่งมาเขียน!)",
            font=("Segoe UI", 9),
            fg="#cbd5e1",
            bg="#1e293b",
            wraplength=680,
            justify="left",
        )
        self.lbl_buf.pack(anchor="w", pady=(4, 0))

        # Built-in Test Sandbox
        sandbox_frame = tk.Frame(self.root, bg="#0f172a", padx=16, pady=6)
        sandbox_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            sandbox_frame,
            text="📝 ช่องทดสอบ (คลิกบรรทัดล่างสุดแล้วกด Alt+\\ จากนั้นกด [Tab] หรือกดปุ่ม 🎬 ด้านบนได้เลย!):",
            font=("Segoe UI", 10, "bold"),
            fg="#e2e8f0",
            bg="#0f172a",
        ).pack(anchor="w", pady=(0, 6))

        self.test_box = tk.Text(
            sandbox_frame,
            height=8,
            font=("Consolas", 11),
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="#34d399",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.test_box.pack(fill=tk.BOTH, expand=True)
        self.test_box.insert(
            "1.0",
            "# ลองกดปุ่ม [🎬 กดทดสอบดูตัวการ์ตูนวิ่งมาเขียน!] ด้านบน หรือกด Alt+\\ แล้วกด Tab!\ndef calculate_circle_area(radius: float) -> float:\n    ",
        )

    def provider_status_text(self) -> str:
        cfg = self.config
        return f"Provider: {cfg['provider']} • {cfg.get('model') or '(auto)'} @ {cfg['base_url']}"

    def setup_provider_panel(self):
        """Provider picker: switch between Ollama / LM Studio and cloud APIs without restarting."""
        pf = tk.Frame(self.root, bg="#0f172a", padx=16, pady=4)
        pf.pack(fill=tk.X)
        style = {"font": ("Segoe UI", 9, "bold"), "fg": "#94a3b8", "bg": "#0f172a"}
        entry_style = {"font": ("Consolas", 9), "bg": "#1e293b", "fg": "#e2e8f0",
                       "insertbackground": "#e2e8f0", "relief": tk.FLAT}

        self._provider_labels = {p["label"]: name for name, p in PROVIDERS.items()}
        self.var_provider = tk.StringVar(value=PROVIDERS.get(self.config["provider"], PROVIDERS["custom"])["label"])
        self.var_model = tk.StringVar(value=self.config.get("model", ""))
        self.var_base_url = tk.StringVar(value=self.config.get("base_url", ""))
        self.var_api_key = tk.StringVar(value=self.config.get("api_key", ""))

        tk.Label(pf, text="🔌 Provider:", **style).grid(row=0, column=0, sticky="w")
        om = tk.OptionMenu(pf, self.var_provider, *self._provider_labels, command=self.on_provider_selected)
        om.config(font=("Segoe UI", 9), bg="#1e293b", fg="#e2e8f0", activebackground="#334155",
                  highlightthickness=0, relief=tk.FLAT, width=24, anchor="w")
        om.grid(row=0, column=1, sticky="w", padx=(6, 12))
        tk.Label(pf, text="Model:", **style).grid(row=0, column=2, sticky="w")
        tk.Entry(pf, textvariable=self.var_model, width=30, **entry_style).grid(row=0, column=3, sticky="w", padx=6)

        tk.Label(pf, text="Base URL:", **style).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.ent_base_url = tk.Entry(pf, textvariable=self.var_base_url, width=34, **entry_style)
        self.ent_base_url.grid(row=1, column=1, sticky="w", padx=(6, 12), pady=(4, 0))
        tk.Label(pf, text="API key:", **style).grid(row=1, column=2, sticky="w", pady=(4, 0))
        tk.Entry(pf, textvariable=self.var_api_key, width=30, show="•", **entry_style).grid(
            row=1, column=3, sticky="w", padx=6, pady=(4, 0))

        tk.Button(
            pf, text="💾 ใช้ & ทดสอบ", command=self.apply_provider, font=("Segoe UI", 9, "bold"),
            bg="#38bdf8", fg="#0f172a", activebackground="#7dd3fc", relief=tk.FLAT, padx=10, cursor="hand2",
        ).grid(row=0, column=4, rowspan=2, sticky="ns", padx=(6, 0))

        self.lbl_provider_status = tk.Label(pf, text="", font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a",
                                            wraplength=690, justify="left")
        self.lbl_provider_status.grid(row=2, column=0, columnspan=5, sticky="w", pady=(2, 0))

    def on_provider_selected(self, label: str):
        """Fill the fields from that provider's saved profile (or its preset) so switching back is one click."""
        name = self._provider_labels[label]
        cfg = resolve_config(load_raw_config(), name)
        self.var_model.set(cfg.get("model", ""))
        self.var_base_url.set(cfg.get("base_url", ""))
        self.var_api_key.set(cfg.get("api_key", ""))
        hint = "ว่างไว้ = ใช้โมเดลแรกที่ติดตั้งไว้" if not PROVIDERS[name]["model"] else ""
        self.lbl_provider_status.config(text=f"กด 💾 เพื่อเปลี่ยนไปใช้ {label}  {hint}", fg="#94a3b8")

    def apply_provider(self):
        name = self._provider_labels.get(self.var_provider.get(), "custom")
        save_provider_choice(name, self.var_model.get().strip(), self.var_base_url.get().strip(),
                             self.var_api_key.get().strip())
        self.config = load_config()
        self._spell_cache = {}
        self.lbl_stats.config(text=self.provider_status_text())
        self.lbl_provider_status.config(text="⏳ กำลังทดสอบการเชื่อมต่อ...", fg="#fbbf24")
        cfg = self.config

        def test():
            try:
                t0 = time.perf_counter()
                out = chat(cfg, "Reply with the single word: OK", "ping", 8, 0)
                ms = (time.perf_counter() - t0) * 1000.0
                msg = (f"✅ เชื่อมต่อ {provider_label(cfg)} ได้ ({ms:.0f} ms) — ตอบว่า {out.strip()[:20]!r}", "#34d399")
            except Exception as e:
                msg = (f"❌ {type(e).__name__}: {str(e)[:220]}", "#f87171")
            self.ui_queue.put(("provider_status", msg))

        threading.Thread(target=test, daemon=True).start()

    def setup_ghost_overlay(self):
        """Creates an animated non-activating topmost floating HUD that previews the suggestion."""
        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.attributes("-alpha", 0.0)
        self.overlay.configure(bg="#10b981")

        self.overlay_frame = tk.Frame(
            self.overlay,
            bg="#0f172a",
            padx=OVERLAY_PADX,
            pady=5,
            highlightthickness=OVERLAY_BORDER,
            highlightbackground="#10b981",
        )
        self.overlay_frame.pack(fill=tk.BOTH, expand=True)

        self.lbl_ghost_title = tk.Label(
            self.overlay_frame,
            text="⚡ AI Autocomplete  |  กด [Tab] เรียกตัวการ์ตูนวิ่งมาเขียน!  |  [Esc] ปิด",
            font=("Segoe UI", 8, "bold"),
            fg="#6ee7b7",
            bg="#0f172a",
        )
        self.lbl_ghost_title.pack(anchor="w")

        self.lbl_ghost_text = tk.Label(
            self.overlay_frame,
            text="",
            font=("Consolas", 11, "bold"),
            fg="#38bdf8",
            bg="#0f172a",
            justify="left",
            wraplength=540,
        )
        self.lbl_ghost_text.pack(anchor="w", pady=(2, 2))

        self.prog_canvas = tk.Canvas(
            self.overlay_frame,
            height=4,
            width=320,
            bg="#1e293b",
            highlightthickness=0,
        )
        self.prog_canvas.pack(fill=tk.X, pady=(2, 0))
        self.prog_bar = self.prog_canvas.create_rectangle(0, 0, 0, 4, fill="#34d399", outline="")

        self.overlay.update_idletasks()
        hwnd = user32.GetParent(self.overlay.winfo_id()) or self.overlay.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW)
        self.overlay.withdraw()

    def setup_cartoon_mascot_overlay(self):
        """Creates a 100% transparent-background non-activating topmost window for the Cartoon Mascot."""
        self.mascot_win = tk.Toplevel(self.root)
        self.mascot_win.overrideredirect(True)
        self.mascot_win.attributes("-topmost", True)
        self.mascot_win.configure(bg=TRANSPARENT_KEY)
        try:
            self.mascot_win.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass

        self.mascot_canvas = tk.Canvas(
            self.mascot_win,
            width=MASCOT_W,
            height=MASCOT_H,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
        )
        self.mascot_canvas.pack()

        self.mascot_win.update_idletasks()
        hwnd = user32.GetParent(self.mascot_win.winfo_id()) or self.mascot_win.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW)
        self.mascot_win.withdraw()

    def on_toggle_settings(self):
        self.enabled = self.var_enabled.get()
        self.auto_trigger = self.var_auto.get()
        self.animate_typing = self.var_anim_type.get()
        self.auto_write = self.var_auto_write.get()
        if not self.enabled:
            self.hide_suggestion()

    def on_speed_change(self, val):
        try:
            self.typing_delay_ms = int(float(val))
        except Exception:
            self.typing_delay_ms = 18

    def run_demo_cartoon(self):
        """Focuses the built-in sandbox and triggers the Cartoon Mascot running in to write Python code!"""
        self.test_box.focus_set()
        self.test_box.mark_set("insert", "end")
        self.root.update()
        self.current_suggestion = "return 3.14159 * (radius ** 2)"
        self.suggestion_visible = True
        self.target_x, self.target_y = get_caret_or_window_pos()
        self.trigger_accept_and_write()

    def set_progress(self, ratio: float, color: str = "#34d399"):
        self.prog_canvas.update_idletasks()
        w = max(self.prog_canvas.winfo_width(), 260)
        fill_w = int(w * max(0.0, min(1.0, ratio)))
        self.prog_canvas.coords(self.prog_bar, 0, 0, fill_w, 4)
        self.prog_canvas.itemconfig(self.prog_bar, fill=color)

    def show_suggestion(self, text: str, stats: str, backspaces: int = 0, fix=None):
        """Slide-In + Streaming Preview Animation when a new completion arrives.
        fix=(wrong, right) shows a spelling correction instead; Tab deletes `backspaces` chars then types text."""
        if not text.strip():
            self.hide_suggestion()
            return
        if self.anim_id:
            try:
                self.root.after_cancel(self.anim_id)
            except Exception:
                pass
            self.anim_id = None
        if self.auto_write_timer_id:
            try:
                self.root.after_cancel(self.auto_write_timer_id)
            except Exception:
                pass
            self.auto_write_timer_id = None

        self.current_suggestion = text
        self.suggestion_backspaces = backspaces
        self.suggestion_visible = True
        self.is_writing_anim = False

        if fix:
            full_display = f"{fix[0]}  →  {fix[1]}"
            self.overlay_frame.config(highlightbackground="#f59e0b")
            self.lbl_ghost_title.config(text="✏️ คำสะกดผิด  •  กด [Tab] แก้ให้  •  [Esc] ปิด", fg="#fcd34d")
            self.lbl_ghost_text.config(fg="#fde68a", text=full_display)
        else:
            full_display = text.replace("\n", " ↵\n")
            if len(full_display) > 180:
                full_display = full_display[:180] + "..."

            self.overlay_frame.config(highlightbackground="#10b981")
            mode_hint = "ตัวการ์ตูนกำลังจะวิ่งมาเขียน..." if self.auto_write else "กด [Tab] เรียกตัวการ์ตูนวิ่งมาเขียน!"
            self.lbl_ghost_title.config(
                text=f"⚡ {stats}  •  {mode_hint}  •  [Esc] ปิด",
                fg="#6ee7b7",
            )
            self.lbl_ghost_text.config(fg="#38bdf8", text=full_display)

        # Measure the popup at its final size, then pin it just under the caret with the
        # ghost text starting at the caret's x. Flip above the caret / nudge left at screen edges.
        self.overlay.update_idletasks()
        ow = self.overlay.winfo_reqwidth()
        oh = self.overlay.winfo_reqheight()
        caret_x, caret_top, caret_bottom, _src = get_caret_rect()
        wl, wt, wr, wb = get_work_area(caret_x, caret_bottom)
        x = int(caret_x) - OVERLAY_BORDER - OVERLAY_PADX
        y = int(caret_bottom) + OVERLAY_GAP
        if y + oh > wb:
            y = int(caret_top) - OVERLAY_GAP - oh
        self.target_x = max(wl, min(x, wr - ow))
        self.target_y = max(wt, y)

        start_x = self.target_x + 24
        start_y = self.target_y
        total_frames = 10

        self.overlay.geometry(f"+{start_x}+{start_y}")
        self.overlay.attributes("-alpha", 0.2)
        self.overlay.deiconify()
        self.overlay.lift()

        def step_slide_in(frame_idx: int):
            if not self.suggestion_visible or self.is_writing_anim:
                return
            t = frame_idx / total_frames
            ease = 1.0 - (1.0 - t) ** 3
            cur_x = int(start_x + (self.target_x - start_x) * ease)
            cur_y = int(start_y + (self.target_y - start_y) * ease)
            alpha = 0.20 + 0.76 * ease

            reveal_len = max(1, int(len(full_display) * min(1.0, t * 1.15)))
            cursor_char = "█" if frame_idx < total_frames else ""
            self.lbl_ghost_text.config(text=full_display[:reveal_len] + cursor_char)
            self.set_progress(ease, "#38bdf8")

            self.overlay.geometry(f"+{cur_x}+{cur_y}")
            self.overlay.attributes("-alpha", alpha)

            if frame_idx < total_frames:
                self.anim_id = self.root.after(12, step_slide_in, frame_idx + 1)
            else:
                self.anim_id = None
                self.set_progress(1.0, "#10b981")
                if self.auto_write and self.suggestion_visible and not fix:
                    self.auto_write_timer_id = self.root.after(400, self.trigger_accept_and_write)

        step_slide_in(1)

    def trigger_accept_and_write(self):
        """Spawns the Cute Cartoon Mascot that runs across the screen to the cursor and writes the text!"""
        if not self.suggestion_visible or not self.current_suggestion or self.is_writing_anim:
            return
        accepted = self.current_suggestion
        backspaces = self.suggestion_backspaces
        kept = self.buffer[:-backspaces] if backspaces else self.buffer
        self.buffer = trim_buffer(kept + accepted)
        self.is_writing_anim = True
        self.abort_writing = False

        # Hide the rectangular preview box so the Cartoon Mascot takes center stage!
        try:
            self.overlay.withdraw()
        except Exception:
            pass

        if not self.animate_typing:
            self.hide_suggestion()

            def write_now():
                inject_backspaces(backspaces)
                inject_unicode_string(accepted)

            threading.Thread(target=write_now, daemon=True).start()
            return

        # Position the mascot so its pen-nib tip (PEN_TIP_X, PEN_TIP_Y) sits exactly on the caret bottom
        caret_x, caret_bottom, _src = get_caret_anchor()
        end_x = caret_x - PEN_TIP_X
        end_y = caret_bottom - PEN_TIP_Y
        start_x = end_x + 160  # Starts to the right and runs in!
        run_frames = 16
        size = f"{MASCOT_W}x{MASCOT_H}"

        self.write_progress_idx = 0
        self.write_progress_total = max(1, len(accepted))
        self.write_current_text = accepted
        self.write_caret = (caret_x, caret_bottom, _src)

        self.mascot_win.geometry(f"{size}+{start_x}+{end_y}")
        self.mascot_win.deiconify()
        self.mascot_win.lift()

        # Phase 1: Cartoon Mascot runs from start_x -> end_x (16 frames, ~220ms)
        def animate_run_in(f_idx: int):
            if self.abort_writing or not self.is_writing_anim:
                self.hide_suggestion()
                return
            t = f_idx / run_frames
            ease = 1.0 - (1.0 - t) ** 2
            cur_x = int(start_x + (end_x - start_x) * ease)
            self.mascot_win.geometry(f"{size}+{cur_x}+{end_y}")
            draw_cartoon_mascot(
                self.mascot_canvas,
                state="run",
                frame=f_idx,
                bubble_text="🏃‍♂️ มาแล้วคร้าบ! กำลังวิ่งไปเขียนให้...",
            )
            if f_idx < run_frames:
                self.mascot_anim_id = self.root.after(15, animate_run_in, f_idx + 1)
            else:
                # Start Phase 2: Writing at the cursor!
                threading.Thread(
                    target=self._typewriter_worker, args=(accepted, backspaces), daemon=True
                ).start()
                animate_writing_pose(1, end_x, end_y)

        # Phase 2: Cartoon Mascot scribbles with magic pen & sparkles while moving along with the text
        def animate_writing_pose(w_frame: int, base_x: int, base_y: int):
            if self.abort_writing or not self.is_writing_anim:
                return
            done_n = self.write_progress_idx
            total_n = self.write_progress_total
            ratio = done_n / max(1, total_n)
            snippet = self.write_current_text[:done_n].replace("\n", "↵")[-14:]
            # Follow the real caret as characters are typed (falls back to the start spot if the
            # caret can't be read and we'd otherwise just chase the mouse)
            # Caret is read on the typewriter thread: MSAA calls into the target app and can stall the UI
            cx, cb, src = self.write_caret
            if src != "mouse":
                base_x, base_y = cx - PEN_TIP_X, cb - PEN_TIP_Y
            self.mascot_win.geometry(f"{size}+{base_x}+{base_y}")
            draw_cartoon_mascot(
                self.mascot_canvas,
                state="write",
                frame=w_frame,
                bubble_text=f"✍️ กำลังเขียน: {snippet}",
                progress=ratio,
            )
            self.mascot_anim_id = self.root.after(28, animate_writing_pose, w_frame + 1, base_x, base_y)

        animate_run_in(1)

    def _typewriter_worker(self, text: str, backspaces: int = 0):
        """Types characters one by one via Win32 SendInput while the Cartoon Mascot scribbles."""
        total = len(text)
        delay_sec = max(0.005, self.typing_delay_ms / 1000.0)

        for _ in range(backspaces):  # erase the misspelled part first (spelling fix)
            if self.abort_writing:
                break
            inject_backspaces(1)
            time.sleep(delay_sec)

        for idx, ch in enumerate(text):
            if self.abort_writing:
                break
            inject_unicode_string(ch)
            self.write_progress_idx = idx + 1
            self.write_caret = get_caret_anchor()
            time.sleep(delay_sec)

        if not self.abort_writing:
            self.ui_queue.put(("mascot_victory", None))
            time.sleep(0.38)
        self.ui_queue.put(("hide", None))
        self.ui_queue.put(("status", {"app": self.current_app, "title": self.current_title, "buf": self.buffer}))

    def show_mascot_victory(self):
        """Phase 3: Cartoon Mascot jumps with joy before disappearing."""
        if self.mascot_anim_id:
            try:
                self.root.after_cancel(self.mascot_anim_id)
            except Exception:
                pass
            self.mascot_anim_id = None

        def step_victory(v_frame: int):
            if not self.is_writing_anim:
                return
            draw_cartoon_mascot(
                self.mascot_canvas,
                state="victory",
                frame=v_frame,
                bubble_text="✨ เขียนให้เสร็จเรียบร้อยคร้าบ! ✌️",
            )
            if v_frame < 8:
                self.mascot_anim_id = self.root.after(35, step_victory, v_frame + 1)

        step_victory(1)

    def hide_suggestion(self):
        self.suggestion_visible = False
        self.is_writing_anim = False
        self.current_suggestion = ""
        for tid in (self.anim_id, self.mascot_anim_id, self.auto_write_timer_id):
            if tid:
                try:
                    self.root.after_cancel(tid)
                except Exception:
                    pass
        self.anim_id = None
        self.mascot_anim_id = None
        self.auto_write_timer_id = None
        try:
            self.overlay.withdraw()
            self.mascot_win.withdraw()
        except Exception:
            pass

    def process_ui_queue(self):
        try:
            while True:
                cmd, data = self.ui_queue.get_nowait()
                if cmd == "show":
                    self.show_suggestion(data["text"], data["stats"], data.get("backspaces", 0), data.get("fix"))
                    if data["stats"]:
                        self.lbl_stats.config(text=f"Last: {data['stats']}")
                elif cmd == "accept_anim":
                    self.trigger_accept_and_write()
                elif cmd == "mascot_victory":
                    self.show_mascot_victory()
                elif cmd == "hide":
                    self.hide_suggestion()
                elif cmd == "status":
                    if "Universal Autocomplete" in data["title"]:
                        try:
                            self.buffer = self.test_box.get("1.0", "insert")
                            data["buf"] = self.buffer
                        except Exception:
                            pass
                    self.lbl_app.config(text=f"Active App: {data['app']}  ({data['title'][:45]})")
                    tail_preview = data["buf"][-90:].replace("\n", " ↵ ")
                    self.lbl_buf.config(text=f"Context Buffer: ...{tail_preview}")
                elif cmd == "warn":
                    self.lbl_warn.config(text=data)
                elif cmd == "provider_status":
                    self.lbl_provider_status.config(text=data[0], fg=data[1])
                    self.lbl_stats.config(text=self.provider_status_text())
                    if self.config.get("model") and not self.var_model.get():
                        self.var_model.set(self.config["model"])
                elif cmd == "toggle":
                    self.enabled = not self.enabled
                    self.var_enabled.set(self.enabled)
                    if not self.enabled:
                        self.hide_suggestion()
        except queue.Empty:
            pass
        self.root.after(20, self.process_ui_queue)

    def debounce_loop(self):
        last_triggered_time = 0.0
        while True:
            time.sleep(0.05)
            if not self.enabled or not self.auto_trigger or self.is_writing_anim:
                continue
            now = time.perf_counter()
            if (
                self.last_keystroke_time > last_triggered_time
                and (now - self.last_keystroke_time) * 1000.0 >= self.debounce_ms
                and len(self.buffer.strip()) >= 3
            ):
                last_triggered_time = self.last_keystroke_time
                self.request_completion_async(self.buffer)

    def request_completion_async(self, context_text: str):
        self.req_seq += 1
        my_seq = self.req_seq

        def worker():
            try:
                is_code = any(tok in context_text[-300:] for tok in CODE_MARKERS)
                res = request_completion(
                    self.config, context_text, is_code, self.max_tokens if is_code else 20
                )
                if my_seq != self.req_seq or self.fix_seq == my_seq:
                    return  # stale, or a spelling fix is already showing for this text
                comp = res["completion"]
                if not is_code and "\n" in comp:
                    comp = comp.split("\n")[0]
                if comp.strip():
                    self.ui_queue.put(("show", {"text": comp, "stats": res["stats"]}))
            except Exception as e:
                print(f"[completion] {type(e).__name__}: {e}")

        def spell_worker():
            # Fixing what was typed matters more than suggesting what comes next, so a fix replaces the completion
            try:
                seg = spellcheck_segment(context_text)
                if len(seg.strip()) < 3:
                    return
                corrected = self._spell_cache.get(seg)
                if corrected is None:
                    corrected = request_spellcheck(self.config, seg)
                    if len(self._spell_cache) > 200:
                        self._spell_cache.clear()
                    self._spell_cache[seg] = corrected
                fix = find_spelling_fix(seg, corrected)
                if fix is None or my_seq != self.req_seq:
                    return
                backspaces, retype, wrong, right = fix
                self.fix_seq = my_seq
                self.ui_queue.put(("show", {
                    "text": retype, "stats": "", "backspaces": backspaces, "fix": (wrong, right),
                }))
            except Exception as e:
                print(f"[spellcheck] {type(e).__name__}: {e}")

        threading.Thread(target=worker, daemon=True).start()
        if self.spellcheck and not any(tok in context_text[-300:] for tok in CODE_MARKERS):
            threading.Thread(target=spell_worker, daemon=True).start()

    def run_keyboard_hook(self):
        def low_level_Single_proc(nCode, wParam, lParam):
            self.last_hook_tick = kernel32.GetTickCount()
            if nCode == HC_ACTION and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents

                # Ignore keys injected by our own SendInput call
                if kb.dwExtraInfo == OUR_INJECT_TAG:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                # Apps the user opted out of in ua_config.json "skip_apps" (e.g. an editor with its own extension)
                if self.skip_apps and get_active_window_info()[1].lower() in self.skip_apps:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                vk = kb.vkCode
                ctrl_down = (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0
                alt_down = (user32.GetAsyncKeyState(VK_MENU) & 0x8000) != 0
                shift_down = (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0

                # Allow Esc to stop live cartoon writing animation immediately
                if self.is_writing_anim and vk == VK_ESCAPE:
                    self.abort_writing = True
                    self.ui_queue.put(("hide", None))
                    return 1

                # Hotkey: Ctrl + Shift + Q -> Toggle Universal Autocomplete On/Off
                if ctrl_down and shift_down and vk == ord("Q"):
                    self.ui_queue.put(("toggle", None))
                    return 1

                # Hotkey: Alt + \ -> Manual Trigger Autocomplete in ANY app
                if alt_down and vk == VK_OEM_5:
                    if self.buffer.strip():
                        self.request_completion_async(self.buffer)
                    return 1

                # When suggestion popup is visible:
                if self.suggestion_visible and self.current_suggestion and not self.is_writing_anim:
                    if vk == VK_TAB and not ctrl_down and not alt_down:
                        self.ui_queue.put(("accept_anim", None))
                        return 1  # Swallow Tab key so target app doesn't jump focus!
                    elif vk == VK_ESCAPE:
                        self.ui_queue.put(("hide", None))
                        return 1

                # Track active window & typed characters. A new window or a new title (page navigation,
                # different document/chat) means the text around the caret is something we haven't seen.
                hwnd, app_name, title, tid = get_active_window_info()
                if hwnd != self.current_hwnd or normalize_title(title) != normalize_title(self.current_title):
                    self.current_hwnd = hwnd
                    self.current_app = app_name
                    self.current_title = title
                    self.reset_context()

                if vk == VK_BACK:
                    if ctrl_down:
                        self.reset_context()  # Ctrl+Backspace deletes a whole word
                    else:
                        self.buffer = self.buffer[:-1]
                        self.last_keystroke_time = time.perf_counter()
                        self.ui_queue.put(("hide", None))
                    self.ui_queue.put(("status", {"app": app_name, "title": title, "buf": self.buffer}))
                elif vk == VK_RETURN:
                    if app_name.lower() in SUBMIT_ON_ENTER_APPS and not shift_down:
                        self.reset_context()  # search submitted / message sent
                    else:
                        self.buffer += "\n"
                        self.last_keystroke_time = time.perf_counter()
                        self.ui_queue.put(("hide", None))
                    self.ui_queue.put(("status", {"app": app_name, "title": title, "buf": self.buffer}))
                elif vk == VK_DELETE:
                    self.ui_queue.put(("hide", None))  # text after the caret changed; what's before it didn't
                elif vk in CARET_MOVE_KEYS:
                    # Caret moved somewhere we can't see: the typed history no longer precedes it
                    self.reset_context()
                elif ctrl_down and vk not in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LCONTROL, VK_RCONTROL):
                    # Ctrl+V / Ctrl+Z / Ctrl+A ... change the text in ways we can't track
                    self.reset_context()
                elif not ctrl_down and not alt_down:
                    ch = vk_to_unicode(vk, kb.scanCode, tid)
                    if ch and ch.isprintable():
                        self.buffer = trim_buffer(self.buffer + ch)
                        self.last_keystroke_time = time.perf_counter()
                        self.ui_queue.put(("hide", None))
                        self.ui_queue.put(("status", {"app": app_name, "title": title, "buf": self.buffer}))

            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        def low_level_mouse_proc(nCode, wParam, lParam):
            self.last_hook_tick = kernel32.GetTickCount()
            # A click moves the caret or focuses another field (e.g. a search box): start a fresh context
            if nCode == HC_ACTION and wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                if not self.is_writing_anim:
                    self.reset_context()
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._hook_cb = HOOKPROC(low_level_Single_proc)
        self._mouse_hook_cb = HOOKPROC(low_level_mouse_proc)
        self.hook_thread_id = kernel32.GetCurrentThreadId()
        hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_cb, None, 0)
        mouse_hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_hook_cb, None, 0)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_APP_REHOOK:
                # Install the new hooks before dropping the old ones so no keystroke slips through
                new_kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_cb, None, 0)
                new_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_hook_cb, None, 0)
                user32.UnhookWindowsHookEx(hook_id)
                user32.UnhookWindowsHookEx(mouse_hook_id)
                hook_id, mouse_hook_id = new_kb, new_mouse
                self.last_hook_tick = kernel32.GetTickCount()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook_id)
        user32.UnhookWindowsHookEx(mouse_hook_id)

    def hook_watchdog_loop(self):
        """Windows silently removes a low-level hook whose callback once took too long (LowLevelHooksTimeout,
        easy to hit when Python's GIL is busy), after which typing goes undetected in every app.
        Reinstall the hooks when input arrives that they didn't see, and every 30 s as a safety net."""
        last_rehook = time.perf_counter()
        warned_elevated = False
        am_admin = is_admin()
        while True:
            time.sleep(1.0)
            if not self.hook_thread_id:
                continue
            elevated = not am_admin and foreground_is_elevated()
            if elevated != warned_elevated:
                warned_elevated = elevated
                if elevated:
                    self.ui_queue.put(("warn", "⚠ โปรแกรมนี้รันแบบ Administrator: ตั้ง \"run_as_admin\": true ใน ua_config.json เพื่อให้ใช้ได้"))
                else:
                    self.ui_queue.put(("warn", ""))
            if elevated:
                continue  # an admin window's input never reaches our hooks; that's not a dead hook
            missed = tick_newer(last_input_tick(), self.last_hook_tick, margin=1500)
            if missed or time.perf_counter() - last_rehook > 30.0:
                if missed:
                    print("[hook] keyboard/mouse hook stopped receiving input; reinstalling")
                user32.PostThreadMessageW(self.hook_thread_id, WM_APP_REHOOK, 0, 0)
                last_rehook = time.perf_counter()

    def reset_context(self):
        """Forgets the typed history (called from the hook thread) and cancels any pending suggestion."""
        self.buffer = ""
        self.req_seq += 1  # drop in-flight responses computed for the old context
        self.ui_queue.put(("hide", None))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    cfg = load_config()
    if cfg.get("run_as_admin") and not is_admin():
        # Relaunch elevated so admin programs (Task Manager, admin terminals, installers) work too
        args = " ".join(f'"{a}"' for a in [os.path.abspath(__file__)] + sys.argv[1:])
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1) > 32:
            sys.exit(0)
        print("[admin] elevation declined; running without admin rights")
    app = UniversalAutocompleteApp(cfg)
    app.run()
