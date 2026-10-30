# ⚡ Universal Windows AI Autocomplete

> **ระบบ AI พิมพ์เติมคำอัตโนมัติระดับ OS สำหรับ Windows ที่ทำงานได้กับทุกโปรแกรม**  
> เชื่อมต่อกับ AI ผ่าน API ได้หลายเจ้า ทั้งบนคลาวด์ (Typhoon, OpenAI, Claude, Gemini ฯลฯ) และ AI ในเครื่อง (Ollama, LM Studio ฯลฯ) พร้อม **Animation ตัวการ์ตูนวิ่งออกมาเขียนข้อความให้ถึงเคอร์เซอร์** แบบโปร่งใส ไม่แย่ง Focus หน้าต่าง

---

## 🌟 จุดเด่นหลัก (Key Features)

- 🌐 **ใช้งานได้กับทุกโปรแกรมบน Windows (Universal System-Wide)**:
  - **เว็บเบราว์เซอร์**: Google Chrome, Microsoft Edge, Firefox, Brave (Gmail, Google Docs, LINE, Discord, Facebook, ChatGPT, Notion ฯลฯ)
  - **โปรแกรมเอกสาร & ข้อความ**: Notepad, Microsoft Word, Excel, PowerPoint, Obsidian
  - **IDE & เทอร์มินัล**: VS Code, Antigravity IDE, Cursor, PyCharm, Windows Terminal, PowerShell
  - **โปรแกรมที่รันแบบ Administrator**: ใส่ `"run_as_admin": true` ใน `ua_config.json`
- 🔌 **เลือก AI Provider ได้หลายแบบ**:

  | ประเภท | Provider |
  | :--- | :--- |
  | AI ในเครื่อง | Ollama, LM Studio, llama.cpp server, vLLM / SGLang |
  | คลาวด์ | Typhoon, OpenAI, Anthropic Claude, Google Gemini, OpenRouter, Groq, DeepSeek, Together AI, Mistral |
  | อื่น ๆ | Custom: เซิร์ฟเวอร์ใดก็ได้ที่ใช้ API แบบ OpenAI |

- ✏️ **ตรวจคำสะกดผิด (ไทย + อังกฤษ)**: เช่น `กาทำงาน → การทำงาน` กด `Tab` เพื่อแก้ให้อัตโนมัติ
- 🏃‍♂️✨ **Cartoon Mascot Writer (ตัวการ์ตูนวิ่งออกมาเขียนให้ตอนกด Tab)**:
  - หน้าต่างโปร่งใส 100% (`Chroma-Key Transparent Window`) ไม่มีกรอบสี่เหลี่ยมบดบังสายตา
  - **3 จังหวะ Animation**:
    1. **วิ่งเข้ามาหาเคอร์เซอร์ (`run`)**
    2. **ตวัดปากกาเขียนสด (`write`)**: ตัวอักษรถูกพิมพ์ลงในโปรแกรมจริงทีละตัว
    3. **กระโดดดีใจ (`victory`)** เมื่อเขียนเสร็จ
- 🎯 **รองรับ 2 ภาษา & โค้ด**: เติมโค้ดหลายบรรทัด และเติมประโยคภาษาไทย/อังกฤษ
- 🛡️ **Win32 Zero-Focus-Stealing Architecture**:
  - ใช้ `WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW` หน้าต่างลอยจะไม่แย่ง Focus ออกจากโปรแกรมที่กำลังพิมพ์อยู่
  - ฉีดตัวอักษร Unicode โดยตรงผ่าน `SendInput` (`KEYEVENTF_UNICODE`) พร้อมแท็กพิเศษ `0x5157454E` ป้องกัน Hook ดักปุ่มที่ตัวเองพิมพ์
  - มีตัวเฝ้า Hook: ถ้า Windows ถอด Keyboard Hook ออกเอง จะติดตั้งใหม่อัตโนมัติ

---

## ⌨️ คีย์ลัดหลัก (Keyboard Shortcuts)

| คีย์ลัด | การทำงาน |
| :--- | :--- |
| **`Tab`** | ยืนยันรับคำแนะนำ / แก้คำผิด -> เรียกตัวการ์ตูนวิ่งเข้ามาเขียนข้อความลงในโปรแกรมทันที |
| **`Esc`** | ปิดกล่องคำแนะนำ / สั่งหยุดตัวการ์ตูนเขียนกลางคันทันที |
| **`Alt + \` (Backslash)** | สั่งให้ AI แนะนำข้อความทันทีในทุกโปรแกรม (Manual Trigger) |
| **`Ctrl + Shift + Q`** | เปิด / ปิด การทำงานของ Universal Autocomplete ทั้งระบบ |

---

## 🚀 วิธีเริ่มต้นใช้งาน (Quick Start)

### ความต้องการของระบบ

- Windows 10 / 11
- Python 3.9 ขึ้นไป (64-bit) ที่มี Tcl/Tk: ตอนติดตั้งจาก python.org ให้ติ๊ก "tcl/tk and IDLE"
- แพ็กเกจใน [requirements.txt](requirements.txt):

  | แพ็กเกจ | ใช้กับ |
  | :--- | :--- |
  | `openai` | Typhoon, OpenAI, Gemini, OpenRouter, Groq, DeepSeek, Together, Mistral, LM Studio, llama.cpp, vLLM |
  | `anthropic` | Anthropic Claude |
  | `ollama` | Ollama |
  | `pytest` | รันชุดทดสอบ (ไม่บังคับ) |

  SDK ช่วยให้ตอบเร็วขึ้น (ใช้ connection เดิมซ้ำ) และแสดง error ชัดขึ้น ถ้าไม่ได้ติดตั้งตัวไหน โปรแกรมจะใช้ `urllib` ที่มากับ Python แทนให้อัตโนมัติ

```powershell
git clone https://github.com/YangNobody12/Universal-Agent.git
cd Universal-Agent
pip install -r requirements.txt
copy ua_config.example.json ua_config.json   # แล้วใส่ API key ของคุณ
```

### เปิดใช้งาน

1. **เปิดโปรแกรม**: ดับเบิลคลิก `start_universal_autocomplete.bat` หรือรัน
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start_universal_autocomplete.ps1
   ```
2. **เลือก Provider**: ในแถว **🔌 Provider** ของหน้าต่างควบคุม เลือกผู้ให้บริการ กรอก Model / API key แล้วกด **💾 ใช้ & ทดสอบ**

### ใช้กับ Ollama (AI ในเครื่อง)

1. ติดตั้ง [Ollama](https://ollama.com) แล้วดาวน์โหลดโมเดล เช่น `ollama pull qwen3:4b`
2. เลือก **Ollama (local)** ช่อง Model เว้นว่างได้ (จะใช้โมเดลแรกที่ติดตั้งไว้)

### ตั้งค่าผ่านไฟล์ `ua_config.json`

```json
{
  "provider": "typhoon",
  "profiles": {
    "typhoon": { "api_key": "sk-..." },
    "ollama": { "model": "qwen3:4b" }
  },
  "spellcheck": true,
  "run_as_admin": false,
  "skip_apps": []
}
```

- API key ใส่ผ่าน Environment Variable แทนได้: `TYPHOON_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` ฯลฯ
- ⚠️ เมื่อใช้ Provider บนคลาวด์ ข้อความที่พิมพ์ (สูงสุด 2,000 ตัวอักษรล่าสุด) จะถูกส่งไปยังผู้ให้บริการนั้น

---

## 🖥️ หน้าต่างควบคุม (Control Center GUI)

1. **Toggle Switches**: เปิด/ปิดทั้งระบบ, แนะนำอัตโนมัติขณะพิมพ์, ตัวการ์ตูนวิ่งมาเขียน, Auto-Write
2. **Speed Slider**: ปรับความเร็วการเขียนของตัวการ์ตูน `6–40 ms/ตัวอักษร`
3. **🔌 Provider**: เลือกผู้ให้บริการ / โมเดล / Base URL / API key และทดสอบการเชื่อมต่อ
4. **🎬 ปุ่มทดสอบด่วน**: ดูตัวการ์ตูนวิ่งออกมาเขียนในช่องทดสอบ
5. **Live Status**: Provider ที่ใช้, เวลาตอบกลับ, โปรแกรมที่กำลังพิมพ์ (`Active App`), Context Buffer

---

## 🏗️ โครงสร้างระบบ (Architecture)

```
[แป้นพิมพ์ผู้ใช้ (Any Windows App)]
               │
               ▼ (WH_KEYBOARD_LL + ToUnicodeEx)
   [Low-Level Win32 Hook]
               │ (รวบรวมตัวอักษรไทย/อังกฤษ/โค้ด)
               ▼
     [Debounce Watcher] (หน่วงเวลา 250ms หลังหยุดพิมพ์)
               │
               ▼ (HTTP: /chat/completions | Ollama /api/chat | Anthropic /messages)
[AI Provider: Typhoon / Ollama / OpenAI / Claude / Gemini / ...]
  ├── เติมข้อความต่อ (Autocomplete)
  └── ตรวจคำสะกดผิด (Spellcheck)
               │
               ▼
[Ghost Overlay / Cartoon Mascot HUD] (WS_EX_NOACTIVATE)
               │ (ผู้ใช้กดปุ่ม [Tab])
               ▼
 [Cartoon Mascot Animation]
  ├── Phase 1: วิ่งเข้าหาเคอร์เซอร์ (run)
  ├── Phase 2: ลบคำผิด (ถ้ามี) + พิมพ์ทีละตัวอักษรผ่าน SendInput (write)
  └── Phase 3: กระโดดดีใจหลังพิมพ์เสร็จ (victory)
```

---

## 🧪 ชุดทดสอบอัตโนมัติ (Automated Test Suite)

| ลำดับ | รายการทดสอบ (Test Case) | สิ่งที่ตรวจสอบ |
| :---: | :--- | :--- |
| **01** | `test_01_win32_active_window_info` | ตรวจจับ Handle และ Process ของหน้าต่างที่ใช้งาน |
| **02** | `test_02_win32_caret_or_window_pos` | คำนวณพิกัดหน้าจอสำหรับวางกล่องคำแนะนำ |
| **03** | `test_03_win32_vk_to_unicode` | แปลง Virtual-Key ตามภาษาคีย์บอร์ดปัจจุบัน |
| **04** | `test_04_ghost_overlay_noactivate_win32_style` | ตรวจสอบบิต `WS_EX_NOACTIVATE \| WS_EX_TOPMOST` |
| **05** | `test_05_sendinput_unicode_thai_english_code` | ทดสอบแพ็กเก็ต `SendInput` พร้อมแท็ก `OUR_INJECT_TAG` |
| **06** | `test_06_provider_code_completion` | เติมโค้ด Python ผ่าน Provider ที่ตั้งค่าไว้ |
| **07** | `test_07_provider_thai_prose_completion` | เติมประโยคภาษาไทย |
| **08** | `test_08_provider_english_prose_completion` | เติมประโยคภาษาอังกฤษ |
| **09** | `test_09_config_presets_and_legacy_migration` | Preset ของ Provider และการแปลงไฟล์ config เก่า |
| **10** | `test_10_end_to_end_gui_queue_and_hook_filter` | ตรวจสอบ State Machine และการแปลง UTF-16LE |
| **11** | `test_11_spelling_fix_detection` | ตรวจจับคำผิด และไม่นับคำที่ยังพิมพ์ไม่จบ |
| **12** | `test_12_cartoon_mascot_renderer_states` | วาดตัวการ์ตูนทั้ง 3 ท่า (`run`, `write`, `victory`) |

ทดสอบ 06–08 เรียก Provider จริงตาม `ua_config.json` (ข้ามอัตโนมัติถ้าเชื่อมต่อไม่ได้)

```powershell
python test_universal_autocomplete.py
```

---

## 📁 รายละเอียดไฟล์ในโฟลเดอร์

- **`universal_autocomplete.py`**: ตัวโปรแกรมหลัก (Keyboard Hook, HUD, Cartoon Mascot, Provider, หน้าต่างควบคุม)
- **`start_universal_autocomplete.ps1`** / **`.bat`**: สคริปต์เปิดโปรแกรม
- **`ua_config.json`**: การตั้งค่า Provider / API key (สร้างอัตโนมัติเมื่อกด 💾 ในโปรแกรม)
- **`test_universal_autocomplete.py`**: ชุดทดสอบ 12 รายการ
