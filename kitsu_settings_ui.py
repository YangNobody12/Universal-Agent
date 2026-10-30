"""
The Universal Autocomplete settings window, drawn with Flet in its own process (the classic Tk control
center stays available as a fallback). Like kitsu_chat_ui.py it is only a view: npc_companion.py reads and
changes the running app and drives this window over a localhost socket, one JSON object per line.

    Kitsu -> window: state{...all settings...} status{app,stats,warn,buf,provider_status,enabled}
                     fields{model,base_url,api_key} show{x,y} hide quit
    window -> Kitsu: hello{token,width,height} set{key,value} provider{label}
                     apply{label,model,base_url,api_key} demo focus{field} close

Started by Kitsu as:  python kitsu_settings_ui.py <port> <token>
"""

import asyncio
import json
import os
import sys

import flet as ft

HERE = os.path.dirname(os.path.abspath(__file__))
AVATAR = os.path.join(HERE, "assets", "npc", "idle.png")
W, H = 600, 780
TITLE = "Kitsu Settings"

BG = "#0b1220"
CARD = "#111a2e"
LINE = "#24324d"
FIELD = "#16213a"
ACCENT = "#38bdf8"
GREEN = "#34d399"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
AMBER = "#fbbf24"

DEMO_TEXT = "def calculate_circle_area(radius: float) -> float:\n    "


def card(title: str, icon, *controls) -> ft.Container:
    return ft.Container(
        ft.Column(
            [ft.Row([ft.Icon(icon, color=ACCENT, size=18), ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=TEXT)],
                    spacing=8),
             *controls],
            spacing=10,
        ),
        bgcolor=CARD, border=ft.Border.all(1, LINE), border_radius=16, padding=16,
    )


def field(label: str, **kw) -> ft.TextField:
    kw.setdefault("text_style", ft.TextStyle(color=TEXT, size=13))
    return ft.TextField(
        label=label, label_style=ft.TextStyle(color=MUTED, size=12),
        filled=True, bgcolor=FIELD, border_color=ft.Colors.TRANSPARENT, focused_border_color=ACCENT,
        border_radius=10, dense=True, cursor_color=ACCENT, **kw)


def switch_row(title: str, hint: str, on_change) -> tuple:
    sw = ft.Switch(value=False, active_color=ACCENT, on_change=on_change)
    row = ft.Row(
        [ft.Column([ft.Text(title, size=13, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Text(hint, size=11, color=MUTED)], spacing=0, expand=True),
         sw],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return sw, row


async def main(page: ft.Page):
    port, token = int(sys.argv[1]), sys.argv[2]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    def send(**msg):
        writer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))

    page.title = TITLE
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(font_family="Segoe UI")
    page.padding = 0
    page.bgcolor = ft.Colors.TRANSPARENT
    page.window.bgcolor = ft.Colors.TRANSPARENT
    page.window.frameless = True
    page.window.always_on_top = True
    page.window.resizable = False
    page.window.width, page.window.height = W, H
    # The window starts hidden (FLET_APP_HIDDEN); say so here, or a later visible=True is not a change Flet sends
    page.window.visible = False

    # ---- การทำงาน
    sw_enabled, row_enabled = switch_row("เปิดใช้งาน Universal Autocomplete", "ทุกโปรแกรม • Ctrl+Shift+Q เปิด/ปิด",
                                         lambda e: send(type="set", key="enabled", value=e.control.value))
    sw_auto, row_auto = switch_row("แนะนำอัตโนมัติขณะพิมพ์", "ปิดไว้ก็ยังกด Alt+\\ ขอคำแนะนำได้",
                                   lambda e: send(type="set", key="auto", value=e.control.value))
    sw_anim, row_anim = switch_row("ตัวการ์ตูนวิ่งมาเขียนให้ตอนกด Tab", "ปิด = พิมพ์ทันทีไม่มีอนิเมชัน",
                                   lambda e: send(type="set", key="animate", value=e.control.value))
    sw_write, row_write = switch_row("Auto-Write", "ให้ตัวการ์ตูนเขียนเองโดยไม่ต้องกด Tab",
                                     lambda e: send(type="set", key="auto_write", value=e.control.value))
    speed_label = ft.Text("", size=12, color=MUTED)

    def on_speed(e):
        speed_label.value = f"{int(e.control.value)} ms/ตัวอักษร"
        page.update()

    speed = ft.Slider(min=6, max=40, divisions=34, value=18, label="{value} ms", active_color=ACCENT, expand=True,
                      on_change=on_speed,
                      on_change_end=lambda e: send(type="set", key="speed", value=int(e.control.value)))
    demo_btn = ft.FilledButton(content=ft.Text("🎬 ดูตัวการ์ตูนวิ่งมาเขียน", size=13), bgcolor=GREEN, color=BG,
                               on_click=lambda e: page.run_task(run_demo))

    # ---- AI Provider
    provider = ft.Dropdown(label="Provider", options=[], filled=True, bgcolor=FIELD, border_color=ft.Colors.TRANSPARENT,
                           border_radius=10, dense=True, expand=True, menu_height=380,
                           text_style=ft.TextStyle(color=TEXT, size=13),
                           on_select=lambda e: send(type="provider", label=provider.value))
    model = field("Model (ว่างไว้ = ใช้โมเดลแรกที่มี)", expand=True)
    base_url = field("Base URL", expand=True)
    api_key = field("API key", password=True, can_reveal_password=True, expand=True,
                    on_focus=lambda e: send(type="focus", field="key"))
    apply_btn = ft.FilledButton(content=ft.Text("💾 บันทึก & ทดสอบ", size=13), bgcolor=ACCENT, color=BG,
                                on_click=lambda e: send(type="apply", label=provider.value, model=model.value,
                                                        base_url=base_url.value, api_key=api_key.value))
    provider_status = ft.Text("", size=12, color=MUTED, selectable=True)

    # ---- สถานะ
    st_app = ft.Text("", size=12, color=ACCENT, selectable=True, no_wrap=True)
    st_stats = ft.Text("", size=12, color=GREEN, selectable=True)
    st_warn = ft.Text("", size=12, color=AMBER, visible=False)
    st_buf = ft.Text("", size=12, color=MUTED, font_family="Consolas", selectable=True)

    # ---- ช่องทดสอบ (typing here goes through the real autocomplete, just like in any other app)
    test_box = field("ลองพิมพ์ตรงนี้ แล้วรอคำแนะนำ / กด Tab", multiline=True, min_lines=4, max_lines=8,
                     text_style=ft.TextStyle(color=TEXT, size=13, font_family="Consolas"),
                     on_focus=lambda e: send(type="focus", field="test"),
                     on_blur=lambda e: send(type="focus", field="other"))

    async def run_demo():
        test_box.value = (test_box.value or "") + ("\n" if test_box.value else "") + DEMO_TEXT
        test_box.selection = ft.TextSelection(base_offset=len(test_box.value), extent_offset=len(test_box.value))
        page.update()
        await test_box.focus()
        send(type="demo")

    header = ft.WindowDragArea(
        ft.Container(
            ft.Row(
                [ft.CircleAvatar(foreground_image_src=AVATAR, bgcolor="#1e3a5f", radius=22),
                 ft.Column([ft.Text("Universal AI Autocomplete", size=17, weight=ft.FontWeight.BOLD, color=ACCENT),
                            ft.Text("ตั้งค่า • พิมพ์ในโปรแกรมไหนก็ได้ แล้วกด Tab ให้ Kitsu วิ่งมาเขียนให้",
                                    size=11, color=MUTED)], spacing=0, expand=True),
                 ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_color=MUTED, icon_size=20, tooltip="ปิด",
                               on_click=lambda e: send(type="close"))],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=18, top=14, right=10, bottom=12),
        )
    )
    body = ft.ListView(
        [
            card("การทำงาน", ft.Icons.TUNE_ROUNDED, row_enabled, row_auto, row_anim, row_write,
                 ft.Divider(height=1, color=LINE),
                 ft.Text("ความเร็วตอนตัวการ์ตูนเขียน", size=13, color=TEXT, weight=ft.FontWeight.W_500),
                 ft.Row([speed, speed_label], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                 ft.Row([demo_btn], alignment=ft.MainAxisAlignment.END)),
            card("AI Provider", ft.Icons.HUB_ROUNDED, ft.Row([provider]), ft.Row([model]), ft.Row([base_url]),
                 ft.Row([api_key]), ft.Row([ft.Container(provider_status, expand=True), apply_btn],
                                           vertical_alignment=ft.CrossAxisAlignment.CENTER)),
            card("สถานะ", ft.Icons.MONITOR_HEART_ROUNDED, st_app, st_stats, st_warn, st_buf),
            card("ช่องทดสอบ", ft.Icons.KEYBOARD_ROUNDED, test_box),
        ],
        spacing=12, expand=True, padding=ft.Padding.only(left=16, right=16, bottom=16),
    )
    page.add(ft.Container(ft.Column([header, ft.Divider(height=1, color=LINE), body], spacing=8, expand=True),
                          bgcolor=BG, border_radius=20, border=ft.Border.all(1, LINE), expand=True))

    def apply_state(s: dict):
        sw_enabled.value, sw_auto.value = s["enabled"], s["auto"]
        sw_anim.value, sw_write.value = s["animate"], s["auto_write"]
        speed.value = max(6, min(40, s["speed"]))
        speed_label.value = f"{int(speed.value)} ms/ตัวอักษร"
        provider.options = [ft.DropdownOption(key=label, text=label) for label in s["providers"]]
        provider.value = s["provider"]
        apply_fields(s)

    def apply_fields(s: dict):
        model.value, base_url.value, api_key.value = s.get("model", ""), s.get("base_url", ""), s.get("api_key", "")
        base_url.disabled = False

    def apply_status(s: dict):
        st_app.value = s.get("app", "")
        st_stats.value = s.get("stats", "")
        st_warn.value = s.get("warn", "")
        st_warn.visible = bool(st_warn.value)
        st_buf.value = s.get("buf", "")
        ps = s.get("provider_status", "")
        provider_status.value = ps
        provider_status.color = GREEN if ps.startswith("✅") else ("#f87171" if ps.startswith("❌") else MUTED)
        if "enabled" in s:
            sw_enabled.value = s["enabled"]

    page.update()
    # The native window appears a moment after the first update; a "show" sent before that is lost
    await asyncio.sleep(1.0)
    send(type="hello", token=token, width=W, height=H)

    while True:
        line = await reader.readline()
        if not line:
            break
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "state":
            apply_state(msg)
        elif kind == "fields":
            apply_fields(msg)
        elif kind == "status":
            apply_status(msg)
        elif kind == "show":
            page.window.left, page.window.top = msg["x"], msg["y"]
            page.window.visible = True
            page.update()
            await page.window.to_front()
        elif kind == "hide":
            page.window.visible = False
        elif kind == "quit":
            break
        page.update()

    await page.window.destroy()


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN)
