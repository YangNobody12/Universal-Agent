"""
Kitsu's chat window, drawn with Flet (Flutter) in its own process.

It is only a view: npc_companion.py (the Kitsu process) owns all chat logic and drives this window over a
localhost socket, one JSON object per line.

    Kitsu -> window: show{x,y} hide append{who,text} thinking{on} quick{buttons} suggestions{items,index}
                     entry{text} provider{label} quit
    window -> Kitsu: hello{token,width,height} typed{text} submit{text} key{key} button{id} pick{index} close

Started by Kitsu as:  python kitsu_chat_ui.py <port> <token>
"""

import asyncio
import json
import os
import sys

import flet as ft

HERE = os.path.dirname(os.path.abspath(__file__))
AVATAR = os.path.join(HERE, "assets", "npc", "idle.png")
W, H = 460, 640
MAX_MESSAGES = 200

BG = "#0b1220"
SURFACE = "#111a2e"
LINE = "#24324d"
NPC_BUBBLE = "#1a2944"
ME_BUBBLE = "#3a2f14"
ACCENT = "#38bdf8"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
AMBER = "#fcd34d"
CHIP_TONES = {"default": ("#1e293b", TEXT), "warn": ("#fbbf24", "#0f172a"), "ok": ("#34d399", "#0f172a"),
              "danger": ("#f87171", "#0f172a")}


def markdown(text: str, color: str) -> ft.Markdown:
    body = ft.TextStyle(color=color, size=14, height=1.45)
    return ft.Markdown(
        text,
        selectable=True,
        soft_line_break=True,  # a single newline is a line break in chat, not a space
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
        md_style_sheet=ft.MarkdownStyleSheet(
            p_text_style=body,
            list_bullet_text_style=body,
            strong_text_style=ft.TextStyle(color=color, size=14, weight=ft.FontWeight.BOLD),
            code_text_style=ft.TextStyle(font_family="Consolas", size=13, color="#a5f3fc", bgcolor="#0b1220"),
            codeblock_padding=ft.Padding.all(10),
            block_spacing=6,
        ),
    )


def bubble(who: str, text: str) -> ft.Control:
    if who == "sys":
        return ft.Container(
            ft.Text(text, size=11, italic=True, color=MUTED, selectable=True),
            padding=ft.Padding.symmetric(horizontal=10, vertical=2),
        )
    if who == "npc":
        return ft.Row(
            [
                ft.CircleAvatar(foreground_image_src=AVATAR, bgcolor="#1e3a5f", radius=15),
                ft.Container(
                    markdown(text, TEXT),
                    bgcolor=NPC_BUBBLE,
                    padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                    border_radius=ft.BorderRadius.only(top_left=4, top_right=16, bottom_left=16, bottom_right=16),
                    expand=True,
                ),
                ft.Container(width=24),
            ],
            vertical_alignment=ft.CrossAxisAlignment.START,
            spacing=8,
        )
    return ft.Row(
        [
            ft.Container(width=48),
            ft.Container(
                markdown(text, "#fef3c7"),
                bgcolor=ME_BUBBLE,
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                border_radius=ft.BorderRadius.only(top_left=16, top_right=4, bottom_left=16, bottom_right=16),
                expand=True,
            ),
        ],
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


async def main(page: ft.Page):
    port, token = int(sys.argv[1]), sys.argv[2]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    def send(**msg):
        writer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))

    page.title = "Kitsu Chat"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(font_family="Segoe UI")
    page.padding = 0
    page.bgcolor = ft.Colors.TRANSPARENT
    page.window.bgcolor = ft.Colors.TRANSPARENT
    page.window.frameless = True
    page.window.always_on_top = True
    page.window.skip_task_bar = True
    page.window.resizable = False
    page.window.width, page.window.height = W, H
    page.window.visible = False

    provider = ft.Text("", size=11, color=MUTED, no_wrap=True)
    header = ft.WindowDragArea(
        ft.Container(
            ft.Row(
                [
                    ft.CircleAvatar(foreground_image_src=AVATAR, bgcolor="#1e3a5f", radius=20),
                    ft.Column([ft.Text("Kitsu", size=17, weight=ft.FontWeight.BOLD, color=ACCENT), provider],
                              spacing=0, expand=True),
                    ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_color=MUTED, icon_size=20, tooltip="ปิด (Esc)",
                                  on_click=lambda e: send(type="close")),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=16, top=12, right=8, bottom=10),
        )
    )
    messages = ft.ListView(expand=True, spacing=12, auto_scroll=True,
                           padding=ft.Padding.symmetric(horizontal=14, vertical=10))
    thinking = ft.Row(
        [ft.ProgressRing(width=14, height=14, stroke_width=2, color=ACCENT),
         ft.Text("Kitsu กำลังคิด…", size=12, italic=True, color=MUTED)],
        spacing=8, visible=False,
    )
    chips = ft.Row(wrap=True, spacing=6, run_spacing=6)
    sugg_list = ft.Column(spacing=2)
    sugg = ft.Container(sugg_list, visible=False, bgcolor="#0f1a30", border_radius=12,
                        border=ft.Border.all(1, LINE), padding=4)
    field = ft.TextField(
        hint_text="พิมพ์ข้อความ หรือ / เพื่อดูคำสั่ง",
        hint_style=ft.TextStyle(color="#64748b"),
        text_style=ft.TextStyle(color=TEXT, size=14),
        border_radius=22, filled=True, bgcolor="#16213a", border_color=ft.Colors.TRANSPARENT,
        focused_border_color=ACCENT, cursor_color=ACCENT, dense=True, expand=True, autofocus=True,
        content_padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        on_change=lambda e: send(type="typed", text=field.value),
        on_submit=lambda e: send(type="submit", text=field.value),
    )
    send_btn = ft.IconButton(ft.Icons.SEND_ROUNDED, icon_color=BG, bgcolor=ACCENT, tooltip="ส่ง",
                             on_click=lambda e: send(type="submit", text=field.value))

    page.add(
        ft.Container(
            ft.Column(
                [
                    header,
                    ft.Divider(height=1, color=LINE),
                    messages,
                    ft.Container(thinking, padding=ft.Padding.only(left=20)),
                    ft.Container(chips, padding=ft.Padding.symmetric(horizontal=14)),
                    ft.Container(sugg, padding=ft.Padding.symmetric(horizontal=14)),
                    ft.Container(ft.Row([field, send_btn], spacing=8),
                                 padding=ft.Padding.only(left=14, right=14, bottom=14)),
                ],
                spacing=8,
                expand=True,
            ),
            bgcolor=BG,
            border_radius=20,
            border=ft.Border.all(1, LINE),
            expand=True,
        )
    )

    def on_key(e: ft.KeyboardEvent):
        key = {"Arrow Up": "up", "Arrow Down": "down", "Tab": "tab", "Escape": "escape"}.get(e.key)
        if key:
            send(type="key", key=key)

    page.on_keyboard_event = on_key

    def show_suggestions(items, index):
        rows = []
        for i, (value, desc) in enumerate(items):
            sel = i == index
            shown = value[4:] if desc.startswith("📁") else value.strip()
            rows.append(ft.Container(
                ft.Row([ft.Text(shown, font_family="Consolas", size=13, weight=ft.FontWeight.BOLD,
                                color=ACCENT if sel else TEXT, expand=True, no_wrap=True),
                        ft.Text(desc, size=11, color=MUTED, no_wrap=True)]),
                bgcolor="#1e3a5f" if sel else None, border_radius=8,
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                on_click=lambda e, k=i: send(type="pick", index=k), ink=True,
            ))
        sugg_list.controls = rows
        sugg.visible = bool(rows)

    def show_chips(buttons):
        out = []
        for b in buttons:
            bg, fg = CHIP_TONES.get(b.get("tone", "default"), CHIP_TONES["default"])
            if b.get("kind") == "label":
                out.append(ft.Text(b["label"], size=12, color=MUTED))
                continue
            out.append(ft.Container(
                ft.Text(b["label"], size=12, color=fg, weight=ft.FontWeight.W_500),
                bgcolor=bg, border_radius=16, border=ft.Border.all(1, LINE),
                padding=ft.Padding.symmetric(horizontal=12, vertical=6), ink=True,
                on_click=lambda e, i=b["id"]: send(type="button", id=i),
            ))
        chips.controls = out

    page.update()
    # The native window appears a moment after the first update; a "show" sent before that is lost
    await asyncio.sleep(1.0)
    send(type="hello", token=token, width=W, height=H)

    while True:
        line = await reader.readline()
        if not line:  # Kitsu went away: close with it
            break
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "show":
            page.window.left, page.window.top = msg["x"], msg["y"]
            page.window.visible = True
            page.update()
            await page.window.to_front()
            await field.focus()
        elif kind == "hide":
            page.window.visible = False
        elif kind == "append":
            messages.controls.append(bubble(msg["who"], msg["text"]))
            del messages.controls[:-MAX_MESSAGES]
        elif kind == "thinking":
            thinking.visible = bool(msg["on"])
        elif kind == "quick":
            show_chips(msg["buttons"])
        elif kind == "suggestions":
            show_suggestions(msg["items"], msg.get("index", 0))
        elif kind == "entry":
            field.value = msg["text"]
            field.selection = ft.TextSelection(base_offset=len(field.value), extent_offset=len(field.value))
            page.update()
            await field.focus()
        elif kind == "provider":
            provider.value = msg["label"]
        elif kind == "quit":
            break
        page.update()

    await page.window.destroy()


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN)  # never flash / grab focus on start-up
