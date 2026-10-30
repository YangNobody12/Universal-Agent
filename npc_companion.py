"""
Kitsu — the on-screen companion for Universal Autocomplete.

A cartoon fox that lives at the bottom of the screen while the app runs: it strolls around, breathes,
blinks, looks around, stretches, hops, dances, sits down and dozes off when ignored, and talks. It waves
when the mouse comes by, dangles when dragged and falls back to the ground when dropped, thinks while
waiting for the AI and jumps for joy when a key works. Click it to chat (answers come from the configured AI provider), drag it anywhere,
right-click for the menu. Paste an API key into the chat and Kitsu saves it to ua_config.json itself;
keys are handled locally and never sent to the AI.

Sprites: assets/npc/*.png, drawn by make_character.py.
"""

import ctypes
import importlib.util
import json
import math
import os
import queue
import random
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk

import harness as harness_mod
import universal_autocomplete as ua

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "npc")
FRAME_NAMES = [
    *[f"walk_{i}" for i in range(8)],                                                     # walk sheet
    "idle", "blink", "talk", "wave",                                                       # base sheet
    "crouch", "jump", "cheer", "laugh", "surprised", "think", "point", "shrug",           # actions sheet
    "look_left", "look_right", "stretch", "sit", "sleep_0", "sleep_1", "dance_0", "dance_1",  # rest sheet
]
KEY = ua.TRANSPARENT_KEY
TICK_MS = 40
WALK_FRAMES = 8
WALK_TICKS = 3      # ticks per walk frame: one full stride cycle = 8 x 3 x 40 ms ~ 1 s
WALK_SPEED = 3.0    # px per tick (~75 px/s), matched to the stride so the feet don't skate
WALK_STOPS = (2, 6)  # passing poses (legs together, arm down): the natural frames to stop on
GRAVITY = 1.1       # px per tick^2
JUMP_VY = -11.0     # px per tick at take-off (~55 px hop)
SLEEP_AFTER = 75.0  # seconds without any interaction before Kitsu dozes off
LAUGH_RE = re.compile(r"555|ฮ่า|haha|lol|😂|🤣|😆|😄", re.I)
NAME = "Kitsu"

PERSONA = (
    f"You are {NAME}, a cool, cheerful cartoon cyber-ninja FOX (จิ้งจอก, never a cat) with an orange tail, a navy "
    "hoodie with glowing cyan trim and cyan goggles. You live on the user's Windows desktop as the "
    "companion of the Universal Autocomplete app. Use male polite particles in Thai (ครับ). Keep replies short (1-3 sentences), warm and a little playful, "
    "and always answer in the user's language (Thai if they write Thai). You can't see the screen or files.\n"
    "Facts about the app you can explain: it suggests text as the user types in any program; Tab accepts a "
    "suggestion (you run over and write it), Esc dismisses, Alt+\\ asks for a suggestion now, Ctrl+Shift+Q turns "
    "autocomplete on/off. It also spots misspelled words and Tab fixes them. To change AI provider the user can "
    "say e.g. 'use ollama' / 'เปลี่ยนเป็น typhoon'. To set an API key they just paste it into this chat and you "
    "save it locally. Never ask the user to send a key anywhere else. You can also hand coding tasks to AI agents "
    "installed on the PC: '/claude <task>', '/codex <task>', '/opencode <task>' (read-only), add '+' to let the agent "
    "edit files (e.g. '/claude+ fix the bug'), '/cd <folder>' sets their working folder, '/stop' stops, '/harness' "
    "lists them. Current provider: {provider}."
)

# Recognisable key formats -> provider. Plain "sk-..." is shared by several providers, so we ask.
KEY_RE = re.compile(r"(sk-or-v1-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|"
                    r"AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9_-]{20,}|[A-Za-z0-9]{40,})")
KEY_PREFIX_PROVIDER = [("sk-or-", "openrouter"), ("sk-ant-", "anthropic"), ("gsk_", "groq"), ("AIza", "gemini")]
PROVIDER_ALIASES = {
    "typhoon": "typhoon", "ไต้ฝุ่น": "typhoon", "openai": "openai", "gpt": "openai", "chatgpt": "openai",
    "claude": "anthropic", "anthropic": "anthropic", "gemini": "gemini", "google": "gemini",
    "openrouter": "openrouter", "groq": "groq", "deepseek": "deepseek", "together": "together",
    "mistral": "mistral", "ollama": "ollama", "lmstudio": "lmstudio", "lm studio": "lmstudio",
    "llama.cpp": "llamacpp", "llamacpp": "llamacpp", "vllm": "vllm", "custom": "custom",
}
SWITCH_RE = re.compile(r"(?:ใช้|เปลี่ยน(?:เป็น|ไป(?:ใช้)?)?|สลับ(?:เป็น|ไป)?|switch\s+to|use)\s*([A-Za-z.฀-๿ ]+)",
                       re.I)
AMBIGUOUS_SK = ["typhoon", "openai", "deepseek", "mistral", "together", "custom"]

# Coding agents: "/claude task" (read-only), "/claude+ task" (may edit files), or "ให้ claude task"
HARNESS_CMD_RE = re.compile(r"^/(claude|codex|opencode)(\+)?\s+(.+)$", re.I | re.S)
HARNESS_NL_RE = re.compile(r"^(?:ให้|ถาม|สั่ง|ask)\s*(claude|codex|opencode)\s*(?:ช่วย)?\s*(.+)$", re.I | re.S)
HARNESS_SHOWN_LINES = 40  # progress lines echoed into the chat per run


PRIVATE_HWNDS = set()  # Kitsu's chat window: the autocomplete hook must not read what's typed there
PRIVATE_APP = "kitsu-chat"
FLET_APP = "flet.exe"  # process of Kitsu's Flet windows (kitsu_chat_ui.py, kitsu_settings_ui.py)


def normalize_title(title: str) -> str:
    return title.strip()


class FletChat:
    """A Kitsu window drawn with Flet in a child process (kitsu_chat_ui.py, kitsu_settings_ui.py). All logic
    stays in Kitsu; this just ships JSON lines over a localhost socket guarded by a one-time token. Messages
    from the window arrive on `events` as (channel, msg); (channel + "_failed", reason) if it never comes up."""

    def __init__(self, events: queue.Queue, script: str = "kitsu_chat_ui.py", channel: str = "ui",
                 title: str = "Kitsu Chat"):
        self.events = events
        self.channel, self.title = channel, title
        self.visible = False
        self.text = ""          # what's in the window's input box (mirrored from "typed")
        self.size = (460, 640)
        self.conn = None
        self.lock = threading.Lock()
        self.token = secrets.token_hex(16)
        self.prev_foreground = ua.user32.GetForegroundWindow()  # the starting window grabs focus; give it back
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
        log_dir = os.environ.get("KITSU_UI_LOG")  # set to a folder to capture the windows' output for debugging
        out = open(os.path.join(log_dir, f"{channel}.log"), "w", encoding="utf-8") if log_dir else subprocess.DEVNULL
        self.proc = subprocess.Popen([sys.executable, script, str(srv.getsockname()[1]), self.token],
                                     stdout=out, stderr=out, creationflags=0x08000000)  # CREATE_NO_WINDOW
        threading.Thread(target=self._serve, args=(srv,), daemon=True).start()

    def _serve(self, srv):
        srv.settimeout(1.0)
        deadline = time.time() + 90  # first run unpacks the Flet client, later starts take ~3-5 s
        conn = None
        while conn is None:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                if self.proc.poll() is not None or time.time() > deadline:
                    srv.close()
                    self.events.put((self.channel + "_failed", f"exit code {self.proc.poll()}"))
                    return
        srv.close()
        f = conn.makefile("r", encoding="utf-8")
        try:
            hello = json.loads(f.readline() or "{}")
        except ValueError:
            hello = {}
        if hello.get("token") != self.token:
            conn.close()
            self.events.put((self.channel + "_failed", "bad handshake"))
            return
        self.size = (hello.get("width", 460), hello.get("height", 640))
        self.conn = conn
        self.events.put((self.channel, {"type": "ready"}))
        try:
            for line in f:
                self.events.put((self.channel, json.loads(line)))
        except (OSError, ValueError):
            pass
        self.conn = None
        self.events.put((self.channel, {"type": "gone"}))

    def send(self, **msg):
        conn = self.conn
        if not conn:
            return
        try:
            with self.lock:
                conn.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def return_focus(self):
        """Flet activates its (still hidden) window on start-up, which would swallow the user's typing.
        Hand the foreground back to whatever had it; the Alt tap lifts Windows' foreground lock."""
        hwnd = ua.user32.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(64)
        ua.user32.InternalGetWindowText(hwnd, title, 64)
        if title.value == self.title and self.prev_foreground and ua.user32.IsWindow(self.prev_foreground):
            ua.user32.keybd_event(0x12, 0, 0, 0)
            ua.user32.keybd_event(0x12, 0, 2, 0)
            ua.user32.SetForegroundWindow(self.prev_foreground)

    def close(self):
        self.send(type="quit")
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class SettingsPanel:
    """The Flet settings window (kitsu_settings_ui.py). It edits the running app through the same variables
    and handlers as the classic Tk control center, which stays as the fallback."""

    TOGGLES = {"enabled": "var_enabled", "auto": "var_auto", "animate": "var_anim_type", "auto_write": "var_auto_write"}

    def __init__(self, npc):
        self.npc, self.app = npc, npc.app
        self.win = None
        self.ready = False
        self.visible = False
        self.want_show = False
        self.last_status = None

    def open(self):
        if self.win is None:
            if not importlib.util.find_spec("flet"):
                self.npc.show_classic_control_center()
                return
            self.win = FletChat(self.npc.events, "kitsu_settings_ui.py", "settings_ui", "Kitsu Settings")
        self.want_show = True
        if self.ready:
            self._show()

    def _show(self):
        self.want_show = False
        self.win.send(type="state", **self.state())
        self.push_status(force=True)
        wl, wt, wr, wb = ua.get_work_area(int(self.npc.x), int(self.npc.y))
        w, h = self.win.size
        ua.user32.AllowSetForegroundWindow(-1)  # we just got the user's click: let the window take focus
        self.win.send(type="show", x=int(wl + (wr - wl - w) / 2), y=int(max(wt, wt + (wb - wt - h) / 2)))
        self.visible = True

    def hide(self):
        if self.win:
            self.win.send(type="hide")
        self.visible = False
        self.npc.settings_public = False

    def close(self):
        if self.win:
            self.win.close()

    def state(self) -> dict:
        app = self.app
        return {
            **{k: bool(getattr(app, v).get()) for k, v in self.TOGGLES.items()},
            "speed": int(app.typing_delay_ms),
            "providers": [p["label"] for p in ua.PROVIDERS.values()],
            "provider": app.var_provider.get(),
            **self.fields(),
        }

    def fields(self) -> dict:
        app = self.app
        return {"model": app.var_model.get(), "base_url": app.var_base_url.get(), "api_key": app.var_api_key.get()}

    def push_status(self, force: bool = False):
        """Mirrors the control center's live labels (active app, speed, buffer, provider test result)."""
        app = self.app
        status = {
            "app": app.lbl_app.cget("text"), "stats": app.lbl_stats.cget("text"), "warn": app.lbl_warn.cget("text"),
            "buf": app.lbl_buf.cget("text"), "provider_status": app.lbl_provider_status.cget("text"),
            "enabled": bool(app.var_enabled.get()),
        }
        if force or status != self.last_status:
            self.last_status = status
            self.win.send(type="status", **status)

    def on_message(self, msg: dict):
        app, kind = self.app, msg.get("type")
        if kind == "ready":
            self.ready = True
            self.win.return_focus()
            if self.want_show:
                self._show()
        elif kind == "set":
            key, value = msg.get("key"), msg.get("value")
            if key in self.TOGGLES:
                getattr(app, self.TOGGLES[key]).set(bool(value))
                app.on_toggle_settings()
            elif key == "speed":
                app.speed_scale.set(int(value))
                app.on_speed_change(value)
        elif kind == "provider":  # picking a provider fills in its saved model / URL / key
            app.var_provider.set(msg.get("label", ""))
            app.on_provider_selected(msg.get("label", ""))
            self.win.send(type="fields", **self.fields())
        elif kind == "apply":
            app.var_provider.set(msg.get("label") or app.var_provider.get())
            app.var_model.set(msg.get("model") or "")
            app.var_base_url.set(msg.get("base_url") or "")
            app.var_api_key.set(msg.get("api_key") or "")
            app.apply_provider()  # saves ua_config.json and tests the connection (result shows in the status)
            self.npc.set_provider_label(ua.provider_label(app.config))
        elif kind == "demo":  # the window put a function header in its test box and focused it
            app.root.after(250, self.run_demo)
        elif kind == "focus":
            # Typing in the test box goes through autocomplete like any app; everywhere else (API key!) it doesn't
            self.npc.settings_public = msg.get("field") == "test"
        elif kind == "close":
            self.hide()
        elif kind == "gone":
            self.win, self.ready, self.visible = None, False, False

    def run_demo(self):
        app = self.app
        app.current_suggestion = "return 3.14159 * (radius ** 2)"
        app.suggestion_backspaces = 0
        app.suggestion_visible = True
        app.trigger_accept_and_write()


def attach(app):
    """Puts Kitsu on screen for a running UniversalAutocompleteApp without changing the autocomplete code:
    the control center is hidden (Kitsu's menu reopens it), and the chat box is reported to the keyboard hook
    as an app in skip_apps so nothing typed there (API keys included) is sent for completion."""
    original_info = ua.get_active_window_info
    holder = {}

    def window_info():
        info = original_info()
        hwnd, proc, title = info[0], info[1].lower(), normalize_title(info[2])
        private = hwnd in PRIVATE_HWNDS or (proc == FLET_APP and (
            title == "Kitsu Chat" or (title == "Kitsu Settings" and not getattr(holder.get("npc"), "settings_public", False))))
        return (hwnd, PRIVATE_APP, info[2], info[3]) if private else info

    ua.get_active_window_info = window_info
    app.skip_apps = set(app.skip_apps) | {PRIVATE_APP}
    npc = holder["npc"] = NpcCompanion(app)
    app.root.withdraw()
    app.root.protocol("WM_DELETE_WINDOW", app.root.withdraw)
    return npc


def save_api_key(provider: str, api_key: str):
    """Stores the key in that provider's profile in ua_config.json and makes it the active provider."""
    raw = ua.load_raw_config()
    raw["profiles"].setdefault(provider, {})["api_key"] = api_key
    raw["provider"] = provider
    _write_config(raw)


def set_active_provider(provider: str):
    raw = ua.load_raw_config()
    raw["provider"] = provider
    _write_config(raw)


def _write_config(raw: dict):
    with open(ua.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def with_history(history: list, text: str) -> str:
    """ua.chat takes one user message, so earlier turns ride along as a short transcript."""
    if not history:
        return text
    lines = [f"{'User' if m['role'] == 'user' else NAME}: {m['content']}" for m in history]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nUser: {text}\n(Reply as {NAME}.)"


def mask_key(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "…"


def provider_in_text(text: str):
    low = text.lower()
    for alias, name in sorted(PROVIDER_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in low:
            return name
    return None


class NpcCompanion:
    def __init__(self, app):
        self.app = app
        root = app.root
        self.frames = {}
        for name in FRAME_NAMES:
            for suffix in ("", "_left"):
                path = os.path.join(ASSET_DIR, f"{name}{suffix}.png")
                if not os.path.exists(path):
                    raise FileNotFoundError(f"{path} (run make_character.py)")
                self.frames[name + suffix] = tk.PhotoImage(file=path)
        self.w = self.frames["idle"].width()
        self.h = self.frames["idle"].height()

        # Sprite window: transparent, topmost, never takes focus from the app the user is typing in
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        self.win.wm_attributes("-transparentcolor", KEY)
        self.canvas = tk.Canvas(self.win, width=self.w, height=self.h, bg=KEY, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.sprite = self.canvas.create_image(0, 0, anchor="nw", image=self.frames["idle"])
        self.win.update_idletasks()
        hwnd = ua.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        style = ua.user32.GetWindowLongW(hwnd, ua.GWL_EXSTYLE)
        ua.user32.SetWindowLongW(hwnd, ua.GWL_EXSTYLE,
                                 style | ua.WS_EX_NOACTIVATE | ua.WS_EX_TOPMOST | ua.WS_EX_TOOLWINDOW)

        wl, wt, wr, wb = ua.get_work_area(100, 100)
        self.x = float(wr - self.w - 80)
        self.y = float(wb - self.h)
        self.state = "idle"        # base behaviour: idle | walk | sit | sleep | dance
        self.facing = "left"
        self.state_until = time.perf_counter() + 2.0
        self.target_x = self.x
        self.talk_until = 0.0
        self.blink_until = 0.0
        self.next_blink = time.perf_counter() + 3.0
        self.tick_n = 0
        self.drag = None
        self.dragged = False
        self.walk_tick = 0         # ticks since this walk started (drives the stride cycle)
        self.vy = 0.0              # vertical speed while jumping / falling
        self.airborne = False
        self.seq = []              # one-shot pose sequence [(frame, seconds), ...] played over the base state
        self.seq_end = 0.0
        self.thinking = False      # waiting for the AI's reply
        self.last_interaction = time.perf_counter()

        self.events = queue.Queue()  # results from worker threads -> Tk thread
        self.history = []
        self.pending_key = None  # a pasted key waiting for "which provider?"
        self.busy = False
        self.harness_run = None      # running coding-agent task
        self.pending_harness = None  # (name, task) waiting for "allow file edits?"
        self.harness_lines = 0
        self.last_answer = ""
        self.ui = None               # FletChat once its window is up; until then (or without Flet) the Tk chat
        self.ui_actions = {}         # quick-action button id -> callback, for the Flet window
        self.transcript = []         # (who, text) so far, replayed into the Flet window when it connects

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_menu)
        self.canvas.bind("<Enter>", self.on_hover)
        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_command(label=f"💬 คุยกับ {NAME}", command=self.open_chat)
        self.menu.add_command(label="🔑 ใส่ API key", command=self.ask_for_key)
        self.menu.add_command(label="⚙️ เปิดหน้าตั้งค่า", command=self.show_control_center)
        self.menu.add_command(label="⏯ เปิด/ปิด Autocomplete (Ctrl+Shift+Q)",
                              command=lambda: app.ui_queue.put(("toggle", None)))
        self.menu.add_separator()
        self.menu.add_command(label="❌ ออกจากโปรแกรม", command=app.root.destroy)

        self.build_chat()
        self.flet = None
        self.settings = SettingsPanel(self)
        self.settings_public = False  # the settings window's test box has focus: let autocomplete see it
        if self.app.config.get("chat_ui", "flet") == "flet" and importlib.util.find_spec("flet"):
            try:
                self.flet = FletChat(self.events)
            except OSError as e:
                print(f"[kitsu] Flet chat unavailable ({e}); using the Tk chat")
        self.place()
        self.win.after(TICK_MS, self.tick)
        greeting = "สวัสดี! ผม Kitsu เอง 🦊 คลิกที่ผมเพื่อคุยกันได้นะ"
        if not self.app.config.get("api_key") and self.app.config.get("api") != "ollama" \
                and self.app.config["provider"] not in ("lmstudio", "llamacpp", "vllm", "custom"):
            greeting += " — ยังไม่มี API key เลย วาง key ในแชทได้ เดี๋ยวผมจำให้!"
        self.win.after(600, lambda: self.say(greeting, open_panel=False))

    # ------------------------------------------------------------------ app integration

    def show_control_center(self):
        if self.app.config.get("chat_ui", "flet") == "flet":
            self.settings.open()
        else:
            self.show_classic_control_center()

    def show_classic_control_center(self):
        root = self.app.root
        root.deiconify()
        root.lift()
        root.focus_force()

    def reload_provider(self):
        """Re-reads ua_config.json after a key / provider change and syncs the control center's provider row."""
        app = self.app
        app.config = ua.load_config()
        app._spell_cache = {}
        preset = ua.PROVIDERS.get(app.config["provider"], ua.PROVIDERS["custom"])
        app.var_provider.set(preset["label"])
        app.var_model.set(app.config.get("model", ""))
        app.var_base_url.set(app.config.get("base_url", ""))
        app.var_api_key.set(app.config.get("api_key", ""))
        app.lbl_stats.config(text=app.provider_status_text())

    # ------------------------------------------------------------------ movement & animation

    def ground_y(self) -> float:
        return float(ua.get_work_area(int(self.x), int(self.y))[3] - self.h)

    def place(self):
        self.win.geometry(f"{self.w}x{self.h}+{int(self.x)}+{int(self.y)}")
        if not self.ui and self.chat.winfo_viewable():  # the Flet window stays where it was opened / dragged
            self.place_chat()

    def play(self, *steps):
        """Plays poses one after another over whatever Kitsu is doing, e.g. play(("surprised", .5), ("shrug", 1))."""
        self.seq = list(steps)
        self.seq_end = time.perf_counter() + self.seq[0][1]

    def hop(self, pose_in_air: str = "jump"):
        if self.airborne:
            return
        self.play(("crouch", 0.12))
        self.win.after(120, lambda: self._take_off(pose_in_air))

    def _take_off(self, pose):
        self.airborne, self.vy, self.air_pose = True, JUMP_VY, pose
        if self.state in ("sit", "sleep"):
            self.state = "idle"

    def frame_name(self, now: float) -> str:
        sfx = "_left" if self.facing == "left" else ""
        if self.drag is not None and self.dragged:
            return "surprised" + sfx                       # dangling from the cursor
        if self.airborne:
            return getattr(self, "air_pose", "jump") + sfx
        if self.seq:
            name = self.seq[0][0]
            return name if name.startswith("look_") else name + sfx
        if self.thinking:
            return "think" + sfx
        if now < self.talk_until:
            if self.state in ("sit", "sleep"):
                self.state = "idle"
            return ("talk" if (self.tick_n // 3) % 2 == 0 else "idle") + sfx
        if self.state in ("walk", "walk_stop"):
            return f"walk_{(self.walk_tick // WALK_TICKS) % WALK_FRAMES}" + sfx
        if self.state == "sit":
            return "sit" + sfx
        if self.state == "sleep":
            return ("sleep_0" if (self.tick_n // 30) % 2 == 0 else "sleep_1") + sfx
        if self.state == "dance":
            return ("dance_0" if (self.tick_n // 7) % 2 == 0 else "dance_1") + sfx
        if now < self.blink_until:
            return "blink" + sfx
        return "idle" + sfx

    def tick(self):
        now = time.perf_counter()
        self.tick_n += 1
        self.drain_events()
        chatting = self.chat_visible() or now < self.talk_until or self.thinking

        if self.seq and now >= self.seq_end:
            self.seq.pop(0)
            if self.seq:
                self.seq_end = now + self.seq[0][1]

        if self.drag is None:
            ground = self.ground_y()
            if self.airborne or self.y < ground - 1:      # jumping, or dropped mid-air: fall with gravity
                if not self.airborne:
                    self.airborne, self.vy, self.air_pose = True, 0.0, "surprised"
                self.vy += GRAVITY
                self.y = min(ground, self.y + self.vy)
                if self.y >= ground:
                    self.airborne, self.vy = False, 0.0
                    self.play(("crouch", 0.1), *([("cheer", 0.6)] if self.air_pose == "cheer" else []))
                self.place()
            elif self.state in ("walk", "walk_stop") and not self.seq:
                # Keep stepping until the cycle reaches a legs-together pose, then stand
                self.walk_tick += 1
                self.x += WALK_SPEED if self.facing == "right" else -WALK_SPEED
                if self.state == "walk" and (chatting or abs(self.target_x - self.x) <= WALK_SPEED * WALK_TICKS * 3):
                    self.state = "walk_stop"
                if self.state == "walk_stop" and self.walk_tick % WALK_TICKS == 0 \
                        and (self.walk_tick // WALK_TICKS) % WALK_FRAMES in WALK_STOPS:
                    self.state, self.state_until = "idle", now + random.uniform(3, 8)
                self.place()
            elif chatting and self.state in ("dance", "sleep"):
                self.state = "idle"
            elif now >= self.state_until and not chatting and not self.seq:
                self.next_activity(now)

        if now >= self.next_blink:  # blink every few seconds (plus the odd double blink)
            self.blink_until = now + 0.14
            self.next_blink = now + (0.3 if random.random() < 0.15 else random.uniform(2.5, 5.5))

        # Breathing: a slow 0-2 px rise and fall while standing still
        bob = 0
        if self.state in ("idle", "sit") and not self.airborne and not self.seq:
            bob = -round(1 + math.sin(now * 2.6))
        self.canvas.coords(self.sprite, 0, bob)
        self.canvas.itemconfig(self.sprite, image=self.frames[self.frame_name(now)])
        if self.settings.visible and self.tick_n % 15 == 0:  # ~0.6 s: live status in the settings window
            self.settings.push_status()
        if self.tick_n % 125 == 0:  # ~5 s: stay above windows that grabbed topmost after us
            self.win.attributes("-topmost", True)
        self.win.after(TICK_MS, self.tick)

    def next_activity(self, now: float):
        """Picks what Kitsu does next while left alone; dozes off after a while without attention."""
        if self.state == "sleep":
            self.state_until = now + 5
            return
        if now - self.last_interaction > SLEEP_AFTER:
            if self.state == "sit":
                self.state, self.state_until = "sleep", now + 5
            else:
                self.play(("stretch", 1.2))
                self.state, self.state_until = "sit", now + 3
            return
        if self.state in ("sit", "dance"):
            if self.state == "sit" and random.random() < 0.5:
                self.play(("stretch", 0.9))
            self.state = "idle"
        wl, wt, wr, wb = ua.get_work_area(int(self.x), int(self.y))
        roll = random.random()
        if roll < 0.40:
            self.target_x = random.uniform(wl + 20, wr - self.w - 20)
            self.facing = "right" if self.target_x > self.x else "left"
            self.state = "walk"
            self.walk_tick = 0  # start every walk at the first contact pose
            return
        self.state = "idle"
        if roll < 0.52:
            first = random.choice(["look_left", "look_right"])
            other = "look_right" if first == "look_left" else "look_left"
            self.play((first, 1.0), ("idle", 0.3), (other, 1.0))
        elif roll < 0.60:
            self.play(("wave", 1.3))
        elif roll < 0.67:
            self.hop()
        elif roll < 0.73:
            self.state = "dance"
            self.state_until = now + random.uniform(2.0, 3.5)
            return
        elif roll < 0.83:
            self.state = "sit"
            self.state_until = now + random.uniform(6, 14)
            return
        elif roll < 0.88:
            self.play(("stretch", 1.2))
        elif roll < 0.94:
            self.play(("point", 1.0))
        else:
            self.play(("shrug", 1.0))
        self.state_until = now + random.uniform(3, 7)

    def touched(self):
        """Any attention from the user: wake up if dozing."""
        self.last_interaction = time.perf_counter()
        if self.state in ("sleep", "sit"):
            woke = self.state == "sleep"
            self.state = "idle"
            if woke:
                self.play(("surprised", 0.5))

    def on_hover(self, _e):
        if self.drag is None and not self.airborne:
            sleeping = self.state == "sleep"
            self.touched()
            if not sleeping and not self.seq and random.random() < 0.6:
                self.play(("wave", 0.9))

    def on_press(self, e):
        self.drag = (e.x_root - self.x, e.y_root - self.y)
        self.dragged = False
        self.touched()

    def on_drag(self, e):
        if self.drag is None:
            return
        nx, ny = e.x_root - self.drag[0], e.y_root - self.drag[1]
        if abs(nx - self.x) + abs(ny - self.y) > 3:
            self.dragged = True
        self.x, self.y = nx, ny
        self.airborne, self.vy, self.seq = False, 0.0, []
        self.state = "idle"
        self.place()

    def on_release(self, _e):
        self.drag = None
        if not self.dragged:
            self.toggle_chat()
        self.state_until = time.perf_counter() + random.uniform(4, 8)

    def on_menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    # ------------------------------------------------------------------ chat panel

    def build_chat(self):
        bg, panel, fg, accent = "#0f172a", "#1e293b", "#e2e8f0", "#38bdf8"
        self.chat = tk.Toplevel(self.app.root)
        self.chat.withdraw()
        self.chat.overrideredirect(True)
        self.chat.attributes("-topmost", True)
        self.chat.configure(bg=accent)
        frame = tk.Frame(self.chat, bg=bg, padx=10, pady=8)
        frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        head = tk.Frame(frame, bg=bg)
        head.pack(fill=tk.X)
        tk.Label(head, text=f"🦊 {NAME}", font=("Segoe UI", 11, "bold"), fg=accent, bg=bg).pack(side=tk.LEFT)
        self.lbl_provider = tk.Label(head, text="", font=("Segoe UI", 8), fg="#94a3b8", bg=bg)
        self.lbl_provider.pack(side=tk.LEFT, padx=8)
        close = tk.Label(head, text="✕", font=("Segoe UI", 11, "bold"), fg="#94a3b8", bg=bg, cursor="hand2")
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda _e: self.close_chat())

        self.log = tk.Text(frame, width=52, height=15, wrap="word", font=("Segoe UI", 10), bg=bg, fg=fg,
                           relief=tk.FLAT, padx=4, pady=6, state=tk.DISABLED, cursor="arrow",
                           spacing1=1, spacing2=3, spacing3=1, highlightthickness=0)
        self.log.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        # Message blocks: a small name line, then the text on a tinted card (tag background + margins)
        self.log.tag_configure("npc_head", foreground=accent, font=("Segoe UI", 9, "bold"), spacing1=10, lmargin1=6)
        self.log.tag_configure("npc_body", foreground=fg, background="#1c2a42", lmargin1=6, lmargin2=6, rmargin=34,
                               spacing1=2, spacing3=2, lmargincolor=bg, rmargincolor=bg)
        self.log.tag_configure("me_head", foreground="#fcd34d", font=("Segoe UI", 9, "bold"), spacing1=10,
                               justify="right", rmargin=6)
        self.log.tag_configure("me_body", foreground="#fef3c7", background="#3a321c", lmargin1=70, lmargin2=70,
                               rmargin=6, spacing1=2, spacing3=2, lmargincolor=bg, rmargincolor=bg)
        self.log.tag_configure("sys", foreground="#94a3b8", font=("Segoe UI", 8, "italic"), lmargin1=10, lmargin2=10,
                               spacing1=4)
        self.log.tag_configure("code", font=("Consolas", 9), foreground="#a5f3fc", background="#0b1220")
        self.log.tag_configure("codeblock", font=("Consolas", 9), foreground="#e2e8f0", background="#0b1220",
                               lmargin1=14, lmargin2=14, rmargin=34, spacing1=0, spacing3=0)
        self.log.tag_configure("bold", font=("Segoe UI", 10, "bold"))
        for t in ("code", "bold", "codeblock"):
            self.log.tag_raise(t)

        self.quick = tk.Frame(frame, bg=bg)
        self.quick.pack(fill=tk.X)

        # Command suggestions (shown above the input while typing "/...", "/cd <path>", "ใช้ <provider>")
        self.sugg_frame = tk.Frame(frame, bg="#0b1220", highlightthickness=1, highlightbackground="#334155")
        self.sugg_items, self.sugg_index = [], 0

        row = self.entry_row = tk.Frame(frame, bg=bg)
        row.pack(fill=tk.X, pady=(6, 0))
        self.entry = tk.Entry(row, font=("Segoe UI", 10), bg=panel, fg=fg, insertbackground=fg, relief=tk.FLAT)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.entry.bind("<Return>", self.on_return)
        self.entry.bind("<Tab>", lambda _e: self.accept_suggestion() or "break")
        self.entry.bind("<Down>", lambda _e: self.move_suggestion(1) or "break")
        self.entry.bind("<Up>", lambda _e: self.move_suggestion(-1) or "break")
        self.entry.bind("<Escape>", self.on_entry_escape)
        self.entry.bind("<KeyRelease>", self.on_entry_key)
        tk.Button(row, text="ส่ง", command=self.send, font=("Segoe UI", 9, "bold"), bg=accent, fg=bg,
                  relief=tk.FLAT, padx=12, cursor="hand2").pack(side=tk.LEFT, padx=(6, 0))

        self.chat.bind("<Escape>", lambda _e: self.close_chat())
        self.chat.update_idletasks()
        PRIVATE_HWNDS.add(ua.user32.GetParent(self.chat.winfo_id()) or self.chat.winfo_id())
        self.show_quick_actions()

    def chat_position(self, cw: int, ch: int):
        """Top-left for a cw x ch chat window: above Kitsu, kept on Kitsu's monitor."""
        wl, wt, wr, wb = ua.get_work_area(int(self.x), int(self.y))
        x = int(self.x + self.w / 2 - cw / 2)
        y = int(self.ground_y() - ch - 6)
        if y < wt:
            y = int(self.ground_y() + self.h + 6)
        return max(wl + 4, min(x, wr - cw - 4)), max(wt, y)

    def place_chat(self):
        self.chat.update_idletasks()
        x, y = self.chat_position(self.chat.winfo_reqwidth(), self.chat.winfo_reqheight())
        self.chat.geometry(f"+{x}+{y}")

    def chat_visible(self) -> bool:
        return self.ui.visible if self.ui else bool(self.chat.winfo_viewable())

    def set_provider_label(self, text: str):
        self.lbl_provider.config(text=text)
        if self.ui:
            self.ui.send(type="provider", label=text)

    def open_chat(self):
        if not self.chat_visible():
            self.set_provider_label(ua.provider_label(self.app.config))
            if self.ui:
                # The window's DPI scale is unknown here; size is in logical px, close enough for placement
                x, y = self.chat_position(*self.ui.size)
                # We just got the user's click, so Windows lets us pass the right to take focus on to the window
                ua.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
                self.ui.send(type="show", x=x, y=y)
                self.ui.visible = True
            else:
                self.chat.deiconify()
                self.place_chat()
            self.touched()
            if not self.seq:
                self.play(("wave", 0.8))
        self.state = "idle"
        self.facing = "right"
        if not self.ui:
            self.chat.lift()
            self.chat.focus_force()
            self.entry.focus_set()

    def close_chat(self):
        if self.ui:
            self.ui.send(type="hide")
            self.ui.visible = False
        else:
            self.chat.withdraw()

    def toggle_chat(self):
        if self.chat_visible():
            self.close_chat()
        else:
            self.open_chat()

    def append(self, who: str, text: str):
        self.transcript = (self.transcript + [(who, text)])[-200:]
        if self.ui:
            if who == "sys" and text == "…กำลังคิด":
                self.ui.send(type="thinking", on=True)
            else:
                self.ui.send(type="append", who=who, text=text)
            return
        self.log.config(state=tk.NORMAL)
        if who == "sys":
            self.log.insert(tk.END, f"{text}\n", "sys")
        else:
            self.log.insert(tk.END, (f"🦊 {NAME}" if who == "npc" else "คุณ") + "\n", f"{who}_head")
            self.insert_rich(text, f"{who}_body")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def insert_rich(self, text: str, base: str):
        """Light markdown: ```code blocks```, `inline code`, **bold**, and '- ' bullets."""
        for part in re.split(r"(```[\s\S]*?```)", text.strip()):
            if not part:
                continue
            if part.startswith("```"):
                code = re.sub(r"^```[\w+-]*\n?|```$", "", part).strip("\n")
                self.log.insert(tk.END, code + "\n", (base, "codeblock"))
                continue
            lines = [re.sub(r"^(\s*)[-*] ", r"\1• ", ln) for ln in part.strip("\n").split("\n")]
            for piece in re.split(r"(`[^`\n]+`|\*\*[^*\n]+\*\*)", "\n".join(lines)):
                if piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
                    self.log.insert(tk.END, piece[1:-1], (base, "code"))
                elif piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
                    self.log.insert(tk.END, piece[2:-2], (base, "bold"))
                elif piece:
                    self.log.insert(tk.END, piece, base)
            self.log.insert(tk.END, "\n", base)

    # ------------------------------------------------------------------ input suggestions

    COMMANDS = [
        ("/claude ", "ถาม Claude Code (อ่านอย่างเดียว)"), ("/claude+ ", "ให้ Claude Code แก้ไฟล์ได้"),
        ("/codex ", "ถาม Codex (อ่านอย่างเดียว)"), ("/codex+ ", "ให้ Codex แก้ไฟล์ได้"),
        ("/opencode ", "ถาม OpenCode (อ่านอย่างเดียว)"), ("/opencode+ ", "ให้ OpenCode แก้ไฟล์ได้"),
        ("/cd ", "เปลี่ยนโฟลเดอร์ทำงานของ agent"), ("/stop", "หยุดงานที่กำลังรัน"),
        ("/harness", "ดู agent ที่มีในเครื่อง"), ("/help", "วิธีใช้คำสั่ง"),
    ]

    def suggestions_for(self, text: str) -> list:
        """[(value to insert, description)] for what's typed so far."""
        if text.lower().startswith("/cd "):
            return self.path_suggestions(text[4:])
        if text.startswith("/") and " " not in text:
            if not hasattr(self, "_installed"):
                self._installed = set(harness_mod.available())
            out = []
            for value, desc in self.COMMANDS:
                agent = value.strip("/+ ")
                if agent in harness_mod.HARNESSES and agent not in self._installed:
                    continue
                if value.lower().startswith(text.lower()) and value.strip() != text:
                    out.append((value, desc))
            return out
        m = re.match(r"^(ใช้|เปลี่ยนเป็น|เปลี่ยนไปใช้|สลับเป็น|use|switch to)\s+(\S*)$", text, re.I)
        if m:
            return [(f"{m.group(1)} {name}", p["label"]) for name, p in ua.PROVIDERS.items()
                    if name.startswith(m.group(2).lower()) and name != m.group(2).lower()]
        m = re.match(r"^(ให้|ถาม)\s*(\S*)$", text)
        if m:
            if not hasattr(self, "_installed"):
                self._installed = set(harness_mod.available())
            return [(f"{m.group(1)} {n} ", f"ส่งงานให้ {harness_mod.HARNESSES[n]['label']} (อ่านอย่างเดียว)")
                    for n in harness_mod.HARNESSES if n in self._installed and n.startswith(m.group(2).lower())
                    and n != m.group(2).lower()]
        return []

    def path_suggestions(self, typed: str) -> list:
        typed = typed.strip('"')
        base = os.path.expandvars(os.path.expanduser(typed)) if typed else self.harness_settings()[0] + os.sep
        folder, prefix = os.path.split(base)
        try:
            names = sorted(e.name for e in os.scandir(folder or ".") if e.is_dir() and not e.name.startswith((".", "$")))
        except OSError:
            return []
        hits = [n for n in names if n.lower().startswith(prefix.lower())][:8]
        return [(f"/cd {os.path.normpath(os.path.join(folder, n))}{os.sep}", "📁 โฟลเดอร์") for n in hits]

    def input_text(self) -> str:
        return self.ui.text if self.ui else self.entry.get()

    def set_input_text(self, text: str):
        if self.ui:
            self.ui.text = text
            self.ui.send(type="entry", text=text)
        else:
            self.entry.delete(0, tk.END)
            self.entry.insert(0, text)
            self.entry.icursor(tk.END)
            self.entry.focus_set()

    def refresh_suggestions(self):
        dismissed = getattr(self, "sugg_dismissed", False)
        self.sugg_items = [] if dismissed else self.suggestions_for(self.input_text())[:10]
        self.sugg_index = min(self.sugg_index, max(0, len(self.sugg_items) - 1))
        if self.ui:
            self.ui.send(type="suggestions", items=self.sugg_items, index=self.sugg_index)
            return
        for w in self.sugg_frame.winfo_children():
            w.destroy()
        if not self.sugg_items:
            self.sugg_frame.pack_forget()
            return
        for i, (value, desc) in enumerate(self.sugg_items):
            sel = i == self.sugg_index
            rowbg = "#1e3a5f" if sel else "#0b1220"
            r = tk.Frame(self.sugg_frame, bg=rowbg, cursor="hand2")
            r.pack(fill=tk.X)
            shown = value[4:] if desc.startswith("📁") else value.strip()
            for w in (tk.Label(r, text=shown, font=("Consolas", 9, "bold"), fg="#7dd3fc" if sel else "#cbd5e1",
                               bg=rowbg, anchor="w", padx=6),
                      tk.Label(r, text=desc, font=("Segoe UI", 8), fg="#94a3b8", bg=rowbg, anchor="e", padx=6)):
                w.pack(side=tk.LEFT if w.cget("anchor") == "w" else tk.RIGHT)
                w.bind("<Button-1>", lambda _e, k=i: (setattr(self, "sugg_index", k), self.accept_suggestion()))
        if not self.sugg_frame.winfo_ismapped():
            self.sugg_frame.pack(fill=tk.X, pady=(6, 0), before=self.entry_row)
        if self.chat.winfo_viewable():
            self.place_chat()

    def on_entry_key(self, e):
        if e.keysym not in ("Up", "Down", "Tab", "Return", "Escape"):
            self.sugg_index = 0
            self.sugg_dismissed = False
            self.refresh_suggestions()

    def move_suggestion(self, step: int):
        if self.sugg_items:
            self.sugg_index = (self.sugg_index + step) % len(self.sugg_items)
            self.refresh_suggestions()

    def accept_suggestion(self) -> bool:
        if not self.sugg_items:
            return False
        value = self.sugg_items[self.sugg_index][0]
        self.set_input_text(value)
        self.sugg_index = 0
        self.refresh_suggestions()  # e.g. keep drilling into folders after "/cd "
        return True

    def on_return(self, _e):
        if not self.accept_suggestion():
            self.send()
        return "break"

    def on_entry_escape(self, _e):
        if self.sugg_items:
            self.sugg_dismissed = True
            self.refresh_suggestions()
            return "break"
        self.close_chat()
        return "break"

    def on_ui_message(self, msg: dict):
        """Events from the Flet chat window (see kitsu_chat_ui.py)."""
        kind = msg.get("type")
        if kind == "ready":
            self.ui = self.flet
            self.ui.return_focus()
            if self.chat.winfo_viewable():  # the Tk chat was standing in while Flet started: swap it out
                self.chat.withdraw()
                self.open_chat()
            for who, text in self.transcript:
                if not (who == "sys" and text == "…กำลังคิด"):
                    self.ui.send(type="append", who=who, text=text)
            self.ui.send(type="thinking", on=self.thinking)
            self.set_provider_label(ua.provider_label(self.app.config))
            self.show_quick_actions()
        elif not self.ui:
            return
        elif kind == "typed":
            self.ui.text = msg.get("text", "")
            self.sugg_index = 0
            self.sugg_dismissed = False
            self.refresh_suggestions()
        elif kind == "submit":
            self.ui.text = msg.get("text", "")
            if not self.accept_suggestion():
                self.send(self.ui.text)
        elif kind == "key":
            key = msg.get("key")
            if key in ("up", "down"):
                self.move_suggestion(-1 if key == "up" else 1)
            elif key == "tab":
                self.accept_suggestion()
            elif key == "escape":
                self.on_entry_escape(None)
        elif kind == "pick":
            self.sugg_index = int(msg.get("index", 0))
            self.accept_suggestion()
        elif kind == "button":
            action = self.ui_actions.get(msg.get("id"))
            if action:
                action()
        elif kind == "close":
            self.close_chat()
        elif kind == "gone":  # the window died: fall back to the Tk chat
            self.ui = self.flet = None

    def shutdown(self):
        if self.flet:
            self.flet.close()
        self.settings.close()

    def say(self, text: str, open_panel: bool = True):
        """Kitsu talks: mouth animation for roughly as long as the line takes to read."""
        if open_panel:
            self.open_chat()
        self.append("npc", text)
        self.talk_until = time.perf_counter() + min(4.0, 0.6 + len(text) * 0.04)
        if not open_panel:
            if not self.seq and not self.airborne:
                self.play(("wave", 0.9))
            self.show_toast(text)

    def show_toast(self, text: str):
        """A small speech bubble above Kitsu when the chat panel is closed."""
        tip = tk.Toplevel(self.app.root)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        plain = re.sub(r"`|\*\*|```", "", text)
        tk.Label(tip, text=plain if len(plain) <= 160 else plain[:160] + "…", font=("Segoe UI", 10), bg="#f8fafc", fg="#0f172a", wraplength=260,
                 justify="left", padx=10, pady=6, relief=tk.SOLID, bd=1).pack()
        tip.update_idletasks()
        hwnd = ua.user32.GetParent(tip.winfo_id()) or tip.winfo_id()
        style = ua.user32.GetWindowLongW(hwnd, ua.GWL_EXSTYLE)
        ua.user32.SetWindowLongW(hwnd, ua.GWL_EXSTYLE, style | ua.WS_EX_NOACTIVATE | ua.WS_EX_TOOLWINDOW)
        tip.deiconify()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        wl, wt, wr, wb = ua.get_work_area(int(self.x), int(self.y))
        tx = max(wl + 4, min(int(self.x + self.w / 2 - tw / 2), wr - tw - 4))
        tip.geometry(f"+{tx}+{max(wt, int(self.y - th - 4))}")
        tip.bind("<Button-1>", lambda _e: (tip.destroy(), self.open_chat()))
        tip.after(min(12000, int(2500 + len(text) * 60)), tip.destroy)

    def show_quick_actions(self, providers=None):
        """The button row under the chat: [(label, callback or None for a plain label, tone)]."""
        if providers:  # "which provider is this key for?"
            specs = [("key นี้ของ:", None, "label")] + [
                (name, lambda n=name: self.store_pending_key(n), "warn") for name in providers]
        elif self.pending_harness:  # "allow the agent to edit files?"
            specs = [("✅ อนุญาตให้แก้ไฟล์", lambda: self.confirm_harness(True), "ok"),
                     ("✖ ยกเลิก", lambda: self.confirm_harness(False), "danger")]
        else:
            specs = [("🔑 ใส่ API key", self.ask_for_key, "default"), ("🔌 provider", self.list_providers, "default"),
                     ("🛠 Harness", self.harness_help, "default"), ("⚙️ ตั้งค่า", self.show_control_center, "default")]
            if self.last_answer:
                specs.insert(0, ("📋 คัดลอกคำตอบ", self.copy_answer, "default"))

        if self.ui:
            self.ui_actions = {}
            buttons = []
            for i, (label, cb, tone) in enumerate(specs):
                if cb is None:
                    buttons.append({"kind": "label", "label": label})
                else:
                    self.ui_actions[str(i)] = cb
                    buttons.append({"id": str(i), "label": label, "tone": tone})
            self.ui.send(type="quick", buttons=buttons)

        for w in self.quick.winfo_children():
            w.destroy()
        style = {"font": ("Segoe UI", 8, "bold"), "relief": tk.FLAT, "cursor": "hand2", "padx": 6}
        colors = {"default": ("#334155", "#e2e8f0"), "warn": ("#fbbf24", "#0f172a"), "ok": ("#34d399", "#0f172a"),
                  "danger": ("#f87171", "#0f172a")}
        for label, cb, tone in specs:
            if cb is None:
                tk.Label(self.quick, text=label, font=("Segoe UI", 8), fg="#94a3b8", bg="#0f172a").pack(side=tk.LEFT)
            else:
                bg, fg = colors[tone]
                tk.Button(self.quick, text=label, bg=bg, fg=fg, command=cb, **style).pack(side=tk.LEFT, padx=2)

    def ask_for_key(self):
        self.open_chat()
        self.say("วาง API key ลงในช่องด้านล่างได้เลย (ถ้าบอกชื่อเจ้าด้วย เช่น \"typhoon sk-...\" จะยิ่งเร็ว) "
                 "ผมเก็บไว้ในเครื่องคุณเท่านั้น ไม่ส่งให้ AI 🔒")

    def list_providers(self):
        names = ", ".join(n for n in ua.PROVIDERS)
        self.say(f"ตอนนี้ใช้ {ua.provider_label(self.app.config)} อยู่ — พิมพ์ \"ใช้ <ชื่อ>\" เพื่อเปลี่ยนได้เลย: {names}")

    # ------------------------------------------------------------------ conversation logic

    def send(self, text: str = None):
        text = (self.input_text() if text is None else text).strip()
        if not text or (self.busy and text.lower() != "/stop"):
            return
        self.set_input_text("")
        self.sugg_dismissed = False
        self.refresh_suggestions()

        m = KEY_RE.search(text)
        if m:  # API key: handled entirely here, never logged in full or sent to the model
            key = m.group(1)
            self.append("me", text.replace(key, mask_key(key)))
            self.handle_key(key, text)
            return

        self.append("me", text)
        if self.handle_harness_command(text):
            return
        sw = SWITCH_RE.search(text)
        target = provider_in_text(sw.group(1)) if sw else None
        if target:
            set_active_provider(target)
            self.reload_provider()
            self.say(f"โอเค! เปลี่ยนไปใช้ {ua.PROVIDERS[target]['label']} แล้ว ขอเช็กการเชื่อมต่อแป๊บ…")
            self.test_connection()
            return
        self.ask_model(text)

    # ------------------------------------------------------------------ coding agents (harness.py)

    def harness_settings(self):
        h = ua.load_raw_config().get("harness", {})
        return h.get("cwd") or os.path.expanduser("~"), h.get("models", {})

    def harness_help(self):
        self.open_chat()
        found = harness_mod.available()
        names = ", ".join(harness_mod.HARNESSES[n]["label"] for n in found) or "ไม่พบเลย"
        cwd, _ = self.harness_settings()
        self.say(f"ผมสั่ง AI agent ในเครื่องให้ได้ครับ\n"
                 f"**พบ:** {names}\n"
                 f"**โฟลเดอร์ทำงาน:** `{cwd}`\n\n"
                 "- `/claude <งาน>` หรือ `ให้ claude …` — อ่าน/ตอบอย่างเดียว\n"
                 "- `/claude+ <งาน>` — ให้แก้ไฟล์ได้ (ถามยืนยันก่อน)\n"
                 "- `/codex`, `/opencode` — ใช้แบบเดียวกัน\n"
                 "- `/cd <โฟลเดอร์>` — เปลี่ยนที่ทำงาน\n"
                 "- `/stop` — หยุดงาน\n\n"
                 "พิมพ์ `/` แล้วจะมีรายการคำสั่งให้เลือก (↑↓ แล้วกด Tab)")

    def handle_harness_command(self, text: str) -> bool:
        low = text.lower()
        if low == "/stop":
            if self.harness_run:
                self.harness_run.cancel()
                self.append("sys", "…กำลังหยุด")
            else:
                self.say("ตอนนี้ไม่มีงานที่รันอยู่ครับ")
            return True
        if low in ("/harness", "/help"):
            self.harness_help()
            return True
        if low.startswith("/cd"):
            path = os.path.expandvars(os.path.expanduser(text[3:].strip().strip('"')))
            if not path or not os.path.isdir(path):
                self.say(f"ไม่เจอโฟลเดอร์นี้ครับ: {path or '(ว่าง)'}")
                return True
            raw = ua.load_raw_config()
            raw.setdefault("harness", {})["cwd"] = os.path.abspath(path)
            _write_config(raw)
            self.say(f"📁 ต่อไป agent จะทำงานใน {os.path.abspath(path)}")
            return True
        m = HARNESS_CMD_RE.match(text)
        if m:
            name, edit, task = m.group(1).lower(), bool(m.group(2)), m.group(3).strip()
        else:
            m = HARNESS_NL_RE.match(text)
            if not m:
                return False
            name, edit, task = m.group(1).lower(), False, m.group(2).strip()
        if name not in harness_mod.available():
            self.play(("shrug", 1.0))
            self.say(f"ไม่พบ {harness_mod.HARNESSES[name]['label']} ในเครื่องนี้ครับ")
            return True
        if edit:
            cwd, _ = self.harness_settings()
            self.pending_harness = (name, task)
            self.show_quick_actions()
            self.play(("think", 1.0))
            self.say(f"⚠️ {harness_mod.HARNESSES[name]['label']} จะแก้ไฟล์ใน {cwd} ได้ — อนุญาตไหมครับ?")
            return True
        self.start_harness(name, task, "ask")
        return True

    def confirm_harness(self, allow: bool):
        pending, self.pending_harness = self.pending_harness, None
        self.show_quick_actions()
        if pending and allow:
            self.start_harness(*pending, "edit")
        elif pending:
            self.say("ยกเลิกแล้วครับ ไม่มีไฟล์ไหนถูกแก้")

    def start_harness(self, name: str, task: str, mode: str):
        cwd, models = self.harness_settings()
        label = harness_mod.HARNESSES[name]["label"]
        self.busy = self.thinking = True
        self.harness_lines = 0
        self.touched()
        self.append("sys", f"🛠 {label} · {'แก้ไฟล์ได้' if mode == 'edit' else 'อ่านอย่างเดียว'} · {cwd}  "
                           f"(พิมพ์ /stop เพื่อหยุด)")
        self.harness_run = harness_mod.HarnessRun(
            name, task, cwd, mode, model=models.get(name),
            on_line=lambda line: self.events.put(("harness_line", (name, line))),
            on_done=lambda ok, answer, secs: self.events.put(("harness_done", (name, ok, answer, secs))),
        ).start()

    def copy_answer(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(self.last_answer)
        self.append("sys", "📋 คัดลอกคำตอบแล้ว")

    def handle_key(self, key: str, text: str):
        provider = provider_in_text(text.replace(key, ""))
        if not provider:
            provider = next((p for pre, p in KEY_PREFIX_PROVIDER if key.startswith(pre)), None)
        if not provider:
            self.pending_key = key
            self.say("ได้ key แล้ว! แต่ key แบบ sk-... ใช้ได้หลายเจ้า อันนี้ของเจ้าไหนเหรอ?")
            self.show_quick_actions(AMBIGUOUS_SK)
            return
        self.store_key(provider, key)

    def store_pending_key(self, provider: str):
        key, self.pending_key = self.pending_key, None
        self.show_quick_actions()
        if key:
            self.store_key(provider, key)

    def store_key(self, provider: str, key: str):
        save_api_key(provider, key)
        self.reload_provider()
        self.say(f"จำไว้แล้ว! 🔑 บันทึก key ของ {ua.PROVIDERS[provider]['label']} ({mask_key(key)}) ลง "
                 f"ua_config.json และเปลี่ยนไปใช้เจ้านี้ — ขอทดสอบแป๊บนะ…")
        self.test_connection()

    def test_connection(self):
        cfg = dict(self.app.config)
        self.set_provider_label(ua.provider_label(cfg))

        def work():
            try:
                t0 = time.perf_counter()
                ua.chat(cfg, "Reply with the single word: OK", "ping", 8, 0)
                ms = (time.perf_counter() - t0) * 1000
                self.events.put(("celebrate", f"✅ เชื่อมต่อ {ua.provider_label(cfg)} ได้แล้ว ({ms:.0f} ms) พร้อมลุย!"))
            except Exception as e:
                self.events.put(("oops", f"❌ ยังต่อไม่ได้: {str(e)[:160]}"))

        threading.Thread(target=work, daemon=True).start()

    def ask_model(self, text: str):
        cfg = dict(self.app.config)
        history = list(self.history[-12:])
        self.busy = True
        self.thinking = True
        self.talk_until = 0
        self.touched()
        self.append("sys", "…กำลังคิด")

        def work():
            try:
                system = PERSONA.replace("{provider}", ua.provider_label(cfg))
                reply = ua.chat(cfg, system, with_history(history, text), 300, 0.7).strip()
                self.events.put(("reply", (text, reply or "…")))
            except Exception as e:
                hint = " ถ้ายังไม่ได้ใส่ API key วางลงมาในแชทได้เลยนะ" if "401" in str(e) or "auth" in str(e).lower() else ""
                self.events.put(("error", f"อุ๊ย ถาม AI ไม่สำเร็จ: {str(e)[:140]}{hint}"))

        threading.Thread(target=work, daemon=True).start()

    def drain_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "ui":
                    self.on_ui_message(data)
                elif kind == "ui_failed":
                    print(f"[kitsu] Flet chat window didn't start ({data}); using the Tk chat")
                    self.flet = None
                elif kind == "settings_ui":
                    self.settings.on_message(data)
                elif kind == "settings_ui_failed":
                    print(f"[kitsu] Flet settings window didn't start ({data}); using the classic one")
                    self.settings.win = None
                    if self.settings.want_show:
                        self.show_classic_control_center()
                elif kind == "reply":
                    self.busy = self.thinking = False
                    self.drop_thinking()
                    user, reply = data
                    self.history += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
                    if LAUGH_RE.search(reply):
                        self.play(("laugh", 0.7))
                    self.say(reply, open_panel=self.chat_visible())
                elif kind == "error":
                    self.busy = self.thinking = False
                    self.drop_thinking()
                    self.play(("surprised", 0.6), ("shrug", 1.0))
                    self.say(data, open_panel=self.chat_visible())
                elif kind == "celebrate":
                    self.hop("cheer")
                    self.say(data, open_panel=self.chat_visible())
                elif kind == "oops":
                    self.play(("surprised", 0.6), ("shrug", 1.0))
                    self.say(data, open_panel=self.chat_visible())
                elif kind == "harness_line":
                    name, line = data
                    # Agents that print only the final answer would show it twice; echo progress for the rest
                    if harness_mod.HARNESSES[name].get("answer_file"):
                        if self.harness_lines < HARNESS_SHOWN_LINES:
                            self.append("sys", "│ " + line[:200])
                        elif self.harness_lines == HARNESS_SHOWN_LINES:
                            self.append("sys", "│ …")
                    self.harness_lines += 1
                elif kind == "harness_done":
                    name, ok, answer, secs = data
                    label = harness_mod.HARNESSES[name]["label"]
                    self.busy = self.thinking = False
                    self.harness_run = None
                    if ok:
                        self.last_answer = answer
                        self.hop("cheer")
                        shown = answer if len(answer) <= 3000 else answer[:3000] + "\n… (กด 📋 เพื่อคัดลอกทั้งหมด)"
                        self.say(f"✅ {label} เสร็จแล้ว ({secs:.0f} วิ):\n{shown}", open_panel=self.chat_visible())
                    else:
                        self.play(("surprised", 0.6), ("shrug", 1.0))
                        errors = [ln for ln in answer.splitlines() if "error" in ln.lower()]
                        reason = errors[-1] if errors else answer[-600:]
                        self.say(f"❌ {label}: {reason[:600]}", open_panel=self.chat_visible())
                    self.show_quick_actions()
                elif kind == "say":
                    self.say(data, open_panel=self.chat_visible())
        except queue.Empty:
            pass

    def drop_thinking(self):
        self.transcript = [m for m in self.transcript if m != ("sys", "…กำลังคิด")]
        if self.ui:
            self.ui.send(type="thinking", on=False)
            return
        self.log.config(state=tk.NORMAL)
        idx = self.log.search("…กำลังคิด", "1.0", tk.END, backwards=True)
        if idx:
            self.log.delete(idx, f"{idx} lineend +1c")
        self.log.config(state=tk.DISABLED)
