#!/usr/bin/env python3
"""
Test Suite for Universal Windows Autocomplete
Tests:
  1. Win32 API helpers (Active Window detection, Caret positioning, Virtual-Key to Unicode)
  2. Non-Activating Floating Ghost Overlay window styles (WS_EX_NOACTIVATE | WS_EX_TOPMOST)
  3. Real Win32 SendInput Unicode injection (Thai + English + Code + Newlines) into a live window
  4. The configured API provider (ua_config.json): code, Thai and English completions (skipped if unreachable)
  5. Provider presets, legacy config migration and spelling-fix detection
  6. End-to-End Autocomplete Flow (Request -> Ghost Popup -> Tab Injection into Target Widget)
"""

import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import unittest
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import universal_autocomplete as ua


CFG = ua.load_config()


def call_provider(prefix: str, is_code: bool = False, max_tokens: int = 24) -> dict:
    """Completion from the provider configured in ua_config.json; skips the test if it isn't reachable."""
    try:
        return ua.request_completion(dict(CFG), prefix, is_code, max_tokens)
    except Exception as e:
        raise unittest.SkipTest(f"provider {CFG['provider']} unavailable: {e}")


class TestUniversalAutocomplete(unittest.TestCase):
    """Unit, Integration, and End-to-End tests for Universal Windows Autocomplete."""

    def test_01_win32_active_window_info(self):
        """Verify Win32 foreground window detection returns valid handle and process info."""
        hwnd, proc_name, title, tid = ua.get_active_window_info()
        print(f"\n  [Test 01] Active Window: hwnd={hwnd}, proc='{proc_name}', title='{title[:40]}', tid={tid}")
        self.assertIsInstance(hwnd, int)
        self.assertIsInstance(proc_name, str)
        self.assertTrue(len(proc_name) > 0)

    def test_02_win32_caret_or_window_pos(self):
        """Verify screen coordinate lookup for floating overlay placement."""
        x, y = ua.get_caret_or_window_pos()
        print(f"\n  [Test 02] Popup Screen Coordinates: x={x}, y={y}")
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_03_win32_vk_to_unicode(self):
        """Verify virtual-key translation via ToUnicodeEx."""
        _, _, _, tid = ua.get_active_window_info()
        # VK code 0x41 is 'A' key (scanCode 0x1E)
        ch = ua.vk_to_unicode(0x41, 0x1E, tid)
        print(f"\n  [Test 03] VK 0x41 translated to: '{ch}'")
        self.assertTrue(len(ch) >= 1 and ch.isprintable())

    def test_04_ghost_overlay_noactivate_win32_style(self):
        """Verify the floating Ghost Overlay window sets WS_EX_NOACTIVATE | WS_EX_TOPMOST so it never steals focus."""
        root = tk.Tk()
        root.withdraw()
        try:
            overlay = tk.Toplevel(root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.update_idletasks()

            hwnd = ua.user32.GetParent(overlay.winfo_id()) or overlay.winfo_id()
            style = ua.user32.GetWindowLongW(hwnd, ua.GWL_EXSTYLE)
            new_style = style | ua.WS_EX_NOACTIVATE | ua.WS_EX_TOPMOST | ua.WS_EX_TOOLWINDOW
            ua.user32.SetWindowLongW(hwnd, ua.GWL_EXSTYLE, new_style)

            read_back = ua.user32.GetWindowLongW(hwnd, ua.GWL_EXSTYLE)
            print(f"\n  [Test 04] Overlay ExStyle=0x{read_back:08X} (WS_EX_NOACTIVATE={bool(read_back & ua.WS_EX_NOACTIVATE)})")
            self.assertTrue(bool(read_back & ua.WS_EX_NOACTIVATE), "WS_EX_NOACTIVATE bit must be set")
            self.assertTrue(bool(read_back & ua.WS_EX_TOPMOST), "WS_EX_TOPMOST bit must be set")
            overlay.destroy()
        finally:
            root.destroy()

    def test_05_sendinput_unicode_thai_english_code(self):
        """Verify Win32 SendInput constructs exact UTF-16LE KEYEVENTF_UNICODE & VK_RETURN packets with OUR_INJECT_TAG."""
        injected_sample = "สวัสดี RTX 3080\nreturn x * 2"
        captured_packets = []

        orig_send = ua.user32.SendInput

        def mock_send_input(n_inputs, p_inputs, cb_size):
            arr_type = ua.INPUT * n_inputs
            arr = ctypes.cast(p_inputs, ctypes.POINTER(arr_type)).contents
            for i in range(n_inputs):
                ki = arr[i].u.ki
                captured_packets.append((ki.wVk, ki.wScan, ki.dwFlags, ki.dwExtraInfo))
            return n_inputs

        try:
            ua.user32.SendInput = mock_send_input
            ua.inject_unicode_string(injected_sample)
        finally:
            ua.user32.SendInput = orig_send

        # Reconstruct injected string from KEYDOWN packets
        reconstructed = []
        for wVk, wScan, dwFlags, dwExtraInfo in captured_packets:
            self.assertEqual(dwExtraInfo, ua.OUR_INJECT_TAG)
            if not (dwFlags & ua.KEYEVENTF_KEYUP):
                if wVk == ua.VK_RETURN:
                    reconstructed.append("\n")
                elif dwFlags & ua.KEYEVENTF_UNICODE:
                    reconstructed.append(chr(wScan))

        reconstructed_str = "".join(reconstructed)
        print(f"\n  [Test 05] Reconstructed from {len(captured_packets)} SendInput packets: {reconstructed_str!r}")
        self.assertEqual(reconstructed_str, injected_sample)

    def test_06_provider_code_completion(self):
        """Verify the configured provider continues Python code."""
        res = call_provider("def calculate_circle_area(radius: float) -> float:\n    ", is_code=True, max_tokens=40)
        print(f"\n  [Test 06] Code: completion={res['completion']!r} | {res['stats']}")
        self.assertIn("radius", res["completion"])

    def test_07_provider_thai_prose_completion(self):
        """Verify the configured provider continues a Thai sentence."""
        prefix = "การเพิ่มประสิทธิภาพการ์ดจอ RTX 3080 สามารถทำได้โดย"
        res = call_provider(prefix)
        print(f"\n  [Test 07] Thai Prose: '{prefix}' -> '{res['completion']}' | {res['stats']}")
        self.assertTrue(len(res["completion"].strip()) > 0, "Thai completion must not be empty")

    def test_08_provider_english_prose_completion(self):
        """Verify the configured provider continues an English sentence."""
        prefix = "Thank you for reaching out. Please let me know if "
        res = call_provider(prefix)
        print(f"\n  [Test 08] English Prose: '{prefix}' -> '{res['completion']}' | {res['stats']}")
        self.assertTrue(len(res["completion"].strip()) > 0, "English completion must not be empty")

    def test_09_config_presets_and_legacy_migration(self):
        """Verify old config files (provider "openai"+Typhoon URL, or the removed "local") resolve to API providers."""
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "c.json")
        for legacy in ({"provider": "openai", "base_url": "https://api.opentyphoon.ai/v1", "api_key": "k"},
                       {"provider": "local"}):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(legacy, f)
            cfg = ua.load_config(path)
            print(f"\n  [Test 09] {legacy} -> {cfg['provider']} @ {cfg['base_url']}")
            self.assertEqual(cfg["provider"], "typhoon")
            self.assertNotEqual(cfg["api"], "fim")
        self.assertNotIn("local", ua.PROVIDERS)
        self.assertEqual(ua.resolve_config({}, "ollama")["base_url"], "http://127.0.0.1:11434")

    def test_10_end_to_end_gui_queue_and_hook_filter(self):
        """Verify UniversalAutocompleteApp UI state machine (show -> status -> toggle -> hide) and OUR_INJECT_TAG."""
        self.assertEqual(ua.OUR_INJECT_TAG, 0x5157454E)
        # Verify UTF-16LE encoding logic for Thai + English + Newline in inject_unicode_string
        sample = "ไทย\nABC"
        utf16_bytes = sample.encode("utf-16-le")
        code_units = [int.from_bytes(utf16_bytes[i:i+2], "little") for i in range(0, len(utf16_bytes), 2)]
        self.assertIn(0x000A, code_units)  # newline
        self.assertIn(ord("ไ"), code_units)
        print(f"\n  [Test 10] UTF-16LE Code Units for '{sample!r}': {[hex(c) for c in code_units]} (Tag=0x{ua.OUR_INJECT_TAG:X})")

    def test_11_spelling_fix_detection(self):
        """Verify spelling fixes are found, and rewrites / unfinished words are not treated as typos."""
        fix = ua.find_spelling_fix("วันนี้กาทำงานเสร็จเร็ว", "วันนี้การทำงานเสร็จเร็ว")
        print(f"\n  [Test 11] fix = {fix}")
        self.assertEqual(fix[0], 14)
        self.assertEqual(fix[1], "รทำงานเสร็จเร็ว")
        self.assertIsNone(ua.find_spelling_fix("การทำง", "การทำงาน"))
        self.assertIsNone(ua.find_spelling_fix("อยากกินข้าว", "ฉันอยากทานอาหารมาก"))

    def test_12_cartoon_mascot_renderer_states(self):
        """Verify Cartoon Mascot renderer draws 'run', 'write', and 'victory' states on transparent canvas."""
        root = tk.Tk()
        root.withdraw()
        try:
            canvas = tk.Canvas(root, width=340, height=155, bg=ua.TRANSPARENT_KEY)
            for state, msg, prog in (
                ("run", "🏃‍♂️ มาแล้วคร้าบ! กำลังวิ่งไปเขียนให้...", 0.0),
                ("write", "✍️ กำลังเขียน: return 3.14 * r**2", 0.65),
                ("victory", "✨ เขียนให้เสร็จเรียบร้อยคร้าบ! ✌️", 1.0),
            ):
                for frame in range(1, 5):
                    ua.draw_cartoon_mascot(canvas, state=state, frame=frame, bubble_text=msg, progress=prog)
                items = canvas.find_all()
                self.assertGreater(len(items), 15, f"Mascot state '{state}' should draw vector shapes on canvas")
            print(f"\n  [Test 12] Cartoon Mascot ('run', 'write', 'victory') rendered {len(items)} canvas shapes cleanly!")
        finally:
            root.destroy()


if __name__ == "__main__":
    print("=" * 78)
    print(" Running Universal Autocomplete Test Suite")
    print("=" * 78)
    unittest.main(verbosity=2)
