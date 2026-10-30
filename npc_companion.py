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

import json
import math
import os
import queue
import random
import re
import threading
import time
import tkinter as tk

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
    "save it locally. Never ask the user to send a key anywhere else. Current provider: {provider}."
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


PRIVATE_HWNDS = set()  # Kitsu's chat window: the autocomplete hook must not read what's typed there
PRIVATE_APP = "kitsu-chat"


def attach(app):
    """Puts Kitsu on screen for a running UniversalAutocompleteApp without changing the autocomplete code:
    the control center is hidden (Kitsu's menu reopens it), and the chat box is reported to the keyboard hook
    as an app in skip_apps so nothing typed there (API keys included) is sent for completion."""
    original_info = ua.get_active_window_info

    def window_info():
        info = original_info()
        return (info[0], PRIVATE_APP, info[2], info[3]) if info[0] in PRIVATE_HWNDS else info

    ua.get_active_window_info = window_info
    app.skip_apps = set(app.skip_apps) | {PRIVATE_APP}
    npc = NpcCompanion(app)
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
        self.place()
        self.win.after(TICK_MS, self.tick)
        greeting = "สวัสดี! ผม Kitsu เอง 🦊 คลิกที่ผมเพื่อคุยกันได้นะ"
        if not self.app.config.get("api_key") and self.app.config.get("api") != "ollama" \
                and self.app.config["provider"] not in ("lmstudio", "llamacpp", "vllm", "custom"):
            greeting += " — ยังไม่มี API key เลย วาง key ในแชทได้ เดี๋ยวผมจำให้!"
        self.win.after(600, lambda: self.say(greeting, open_panel=False))

    # ------------------------------------------------------------------ app integration

    def show_control_center(self):
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
        if self.chat.winfo_viewable():
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
        chatting = self.chat.winfo_viewable() or now < self.talk_until or self.thinking

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

        self.log = tk.Text(frame, width=44, height=11, wrap="word", font=("Segoe UI", 10), bg=panel, fg=fg,
                           relief=tk.FLAT, padx=8, pady=6, state=tk.DISABLED, cursor="arrow")
        self.log.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        self.log.tag_configure("npc", foreground="#7dd3fc")
        self.log.tag_configure("me", foreground="#fde68a", justify="right")
        self.log.tag_configure("sys", foreground="#94a3b8", font=("Segoe UI", 9, "italic"))

        self.quick = tk.Frame(frame, bg=bg)
        self.quick.pack(fill=tk.X)

        row = tk.Frame(frame, bg=bg)
        row.pack(fill=tk.X, pady=(6, 0))
        self.entry = tk.Entry(row, font=("Segoe UI", 10), bg=panel, fg=fg, insertbackground=fg, relief=tk.FLAT)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _e: self.send())
        tk.Button(row, text="ส่ง", command=self.send, font=("Segoe UI", 9, "bold"), bg=accent, fg=bg,
                  relief=tk.FLAT, padx=12, cursor="hand2").pack(side=tk.LEFT, padx=(6, 0))

        self.chat.bind("<Escape>", lambda _e: self.close_chat())
        self.chat.update_idletasks()
        PRIVATE_HWNDS.add(ua.user32.GetParent(self.chat.winfo_id()) or self.chat.winfo_id())
        self.show_quick_actions()

    def place_chat(self):
        self.chat.update_idletasks()
        cw, ch = self.chat.winfo_reqwidth(), self.chat.winfo_reqheight()
        wl, wt, wr, wb = ua.get_work_area(int(self.x), int(self.y))
        x = int(self.x + self.w / 2 - cw / 2)
        y = int(self.ground_y() - ch - 6)
        if y < wt:
            y = int(self.ground_y() + self.h + 6)
        x = max(wl + 4, min(x, wr - cw - 4))
        self.chat.geometry(f"+{x}+{max(wt, y)}")

    def open_chat(self):
        if not self.chat.winfo_viewable():
            self.lbl_provider.config(text=ua.provider_label(self.app.config))
            self.chat.deiconify()
            self.place_chat()
            self.touched()
            if not self.seq:
                self.play(("wave", 0.8))
        self.state = "idle"
        self.facing = "right"
        self.chat.lift()
        self.chat.focus_force()
        self.entry.focus_set()

    def close_chat(self):
        self.chat.withdraw()

    def toggle_chat(self):
        if self.chat.winfo_viewable():
            self.close_chat()
        else:
            self.open_chat()

    def append(self, who: str, text: str):
        self.log.config(state=tk.NORMAL)
        prefix = {"npc": f"{NAME}: ", "me": "", "sys": ""}[who]
        self.log.insert(tk.END, f"{prefix}{text}\n", who)
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

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
        tk.Label(tip, text=text, font=("Segoe UI", 10), bg="#f8fafc", fg="#0f172a", wraplength=260,
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
        tip.after(int(2500 + len(text) * 60), tip.destroy)

    def show_quick_actions(self, providers=None):
        for w in self.quick.winfo_children():
            w.destroy()
        style = {"font": ("Segoe UI", 8, "bold"), "relief": tk.FLAT, "cursor": "hand2", "padx": 6}
        if providers:  # "which provider is this key for?"
            tk.Label(self.quick, text="key นี้ของ:", font=("Segoe UI", 8), fg="#94a3b8", bg="#0f172a").pack(side=tk.LEFT)
            for name in providers:
                tk.Button(self.quick, text=name, bg="#fbbf24", fg="#0f172a",
                          command=lambda n=name: self.store_pending_key(n), **style).pack(side=tk.LEFT, padx=2)
            return
        for label, cmd in (("🔑 ใส่ API key", self.ask_for_key), ("🔌 เปลี่ยน provider", self.list_providers),
                           ("⚙️ ตั้งค่า", self.show_control_center)):
            tk.Button(self.quick, text=label, bg="#334155", fg="#e2e8f0", command=cmd, **style).pack(side=tk.LEFT, padx=2)

    def ask_for_key(self):
        self.open_chat()
        self.say("วาง API key ลงในช่องด้านล่างได้เลย (ถ้าบอกชื่อเจ้าด้วย เช่น \"typhoon sk-...\" จะยิ่งเร็ว) "
                 "ผมเก็บไว้ในเครื่องคุณเท่านั้น ไม่ส่งให้ AI 🔒")

    def list_providers(self):
        names = ", ".join(n for n in ua.PROVIDERS)
        self.say(f"ตอนนี้ใช้ {ua.provider_label(self.app.config)} อยู่ — พิมพ์ \"ใช้ <ชื่อ>\" เพื่อเปลี่ยนได้เลย: {names}")

    # ------------------------------------------------------------------ conversation logic

    def send(self):
        text = self.entry.get().strip()
        if not text or self.busy:
            return
        self.entry.delete(0, tk.END)

        m = KEY_RE.search(text)
        if m:  # API key: handled entirely here, never logged in full or sent to the model
            key = m.group(1)
            self.append("me", text.replace(key, mask_key(key)))
            self.handle_key(key, text)
            return

        self.append("me", text)
        sw = SWITCH_RE.search(text)
        target = provider_in_text(sw.group(1)) if sw else None
        if target:
            set_active_provider(target)
            self.reload_provider()
            self.say(f"โอเค! เปลี่ยนไปใช้ {ua.PROVIDERS[target]['label']} แล้ว ขอเช็กการเชื่อมต่อแป๊บ…")
            self.test_connection()
            return
        self.ask_model(text)

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
        self.lbl_provider.config(text=ua.provider_label(cfg))

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
                if kind == "reply":
                    self.busy = self.thinking = False
                    self.drop_thinking()
                    user, reply = data
                    self.history += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
                    if LAUGH_RE.search(reply):
                        self.play(("laugh", 0.7))
                    self.say(reply, open_panel=self.chat.winfo_viewable())
                elif kind == "error":
                    self.busy = self.thinking = False
                    self.drop_thinking()
                    self.play(("surprised", 0.6), ("shrug", 1.0))
                    self.say(data, open_panel=self.chat.winfo_viewable())
                elif kind == "celebrate":
                    self.hop("cheer")
                    self.say(data, open_panel=self.chat.winfo_viewable())
                elif kind == "oops":
                    self.play(("surprised", 0.6), ("shrug", 1.0))
                    self.say(data, open_panel=self.chat.winfo_viewable())
                elif kind == "say":
                    self.say(data, open_panel=self.chat.winfo_viewable())
        except queue.Empty:
            pass

    def drop_thinking(self):
        self.log.config(state=tk.NORMAL)
        idx = self.log.search("…กำลังคิด", "1.0", tk.END, backwards=True)
        if idx:
            self.log.delete(idx, f"{idx} lineend +1c")
        self.log.config(state=tk.DISABLED)
