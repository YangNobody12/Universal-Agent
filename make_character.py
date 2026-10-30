#!/usr/bin/env python3
"""
Draws the desktop companion's sprite sheets with an image model (OpenRouter Image API, openai/gpt-image-2)
and cuts them into transparent animation frames for npc_companion.py.

    python make_character.py                       # draw any missing sheets, then cut all of them
    python make_character.py --env-file path/.env  # OPENROUTER_API_KEY from a .env file
    python make_character.py --redraw actions      # draw that sheet again even if it exists
    python make_character.py --cut-only            # re-cut the existing sheets without calling the API

Sheets (assets/npc/sheet*.png):
  base     idle, blink, talk, wave (+ an older 4-frame walk, walk4_*)
  walk     walk_0..7: 8-frame walk cycle with arms swinging opposite the legs
  actions  crouch, jump, cheer, laugh, surprised, think, point, shrug
  rest     look_left, look_right, stretch, sit, sleep_0, sleep_1, dance_0, dance_1
Every frame is also saved mirrored as *_left.png. The later sheets are drawn with the base sheet as a
reference image so the character stays the same, and each starts with a plain idle pose that is used to
scale it to the base sheet's size. The model can't paint transparency, so it draws on flat magenta and
we key that colour out here.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "assets", "npc")
TARGET_HEIGHT = 150  # on-screen height of the standing idle pose, in pixels

CHARACTER = (
    "a cool chibi cyber-ninja fox: orange fur, short dark-navy hooded jacket with glowing cyan trim, "
    "cyan visor goggles pushed up on the head, black fingerless gloves, white sneakers, fluffy tail, "
    "confident friendly smile"
)

STYLE = """Style: clean bold cartoon, thick dark outlines, flat cel shading, vivid colours.
Background: one flat solid pure magenta colour (#FF00FF) everywhere, no gradient, no floor, no shadows,
no text, no letters, no symbols, no grid lines, no borders. Do not use magenta or pink anywhere on the character."""

SHEETS = [
    {
        "key": "base", "file": "sheet.png", "cols": 4, "rows": 2, "aspect": "3:2", "ref": "idle",
        # its 4-frame walk (stiff arms) is superseded by the 8-frame "walk" sheet below
        "names": ["walk4_0", "walk4_1", "walk4_2", "walk4_3", "idle", "blink", "talk", "wave"],
        "prompt": """2D game sprite sheet for a desktop pet. The same character in every cell: {character}.
Layout: exactly 8 poses in a 4 columns x 2 rows grid, evenly spaced, each pose fully separated from its
neighbours by plenty of empty background, full body visible, same size and scale in every cell,
feet on the same baseline within each row.
Row 1 (left to right): 4-frame walk cycle, side view facing RIGHT (contact, down, passing, up).
Row 2 (left to right): idle standing facing the viewer with mouth closed; same pose with eyes closed
(blink); same pose with mouth wide open as if talking; same pose waving hello with one hand.
{style}""",
    },
    {
        "key": "walk", "file": "sheet_walk.png", "cols": 3, "rows": 3, "aspect": "1:1", "ref": "ref",
        "names": ["ref"] + [f"walk_{i}" for i in range(8)],
        "prompt": """2D game sprite sheet for a desktop pet, continuing the attached reference sheet: draw
EXACTLY the same character ({character}) with identical colours, outfit, proportions and art style.
Layout: exactly 9 poses in a 3 columns x 3 rows grid, evenly spaced, each pose fully separated from its
neighbours by plenty of empty background, full body visible including feet and tail, all at the same scale,
feet on the same baseline. Read the cells left to right, top to bottom.
Cell 1: plain idle standing facing the viewer, mouth closed - same as the reference.
Cells 2-9: an 8-frame WALK CYCLE in strict side view (profile), the character facing to the RIGHT.
"Front arm" = the arm nearest the viewer, drawn ON TOP of the jacket and fully visible.
"Back arm" = the other arm, mostly hidden behind the body. Same for "front leg" / "back leg".
Arms swing in opposition to the legs like a real walk: the front arm swings the OPPOSITE way to the front leg.
The front arm must move clearly between two extremes: FRONT FIST AHEAD (fist out in front of the chest,
toward the right edge) and FRONT FIST BEHIND (fist back behind the hip/tail, toward the left edge).
 Cell 2: front leg stepping far FORWARD, back leg far behind. Front fist BEHIND the hip; back fist ahead of the chest.
 Cell 3: same stride, body slightly lower. Front fist still BEHIND the hip.
 Cell 4: legs passing (front leg straight under the body, back leg passing). Front arm hanging straight down at the side.
 Cell 5: back leg swinging forward, body slightly higher. Front fist moving AHEAD, in front of the belly.
 Cell 6: back leg stepping far FORWARD, front leg far behind. Front fist far AHEAD in front of the chest; back fist behind.
 Cell 7: same stride, body slightly lower. Front fist still AHEAD of the chest.
 Cell 8: legs passing (back leg straight under the body, front leg passing). Front arm hanging straight down at the side.
 Cell 9: front leg swinging forward, body slightly higher. Front fist moving BEHIND, past the hip.
So: cells 2-3 front fist BEHIND, cells 6-7 front fist AHEAD. The tail sways gently.
{style}""",
    },
    {
        "key": "actions", "file": "sheet_actions.png", "cols": 3, "rows": 3, "aspect": "1:1", "ref": "ref",
        "names": ["ref", "crouch", "jump", "cheer", "laugh", "surprised", "think", "point", "shrug"],
        "prompt": """2D game sprite sheet for a desktop pet, continuing the attached reference sheet: draw
EXACTLY the same character ({character}) with identical colours, outfit, proportions and art style.
Layout: exactly 9 poses in a 3 columns x 3 rows grid, evenly spaced, each pose fully separated from its
neighbours by plenty of empty background, full body visible including feet and tail, all at the same scale.
All poses face the viewer.
Row 1: (1) plain idle standing, mouth closed - same as the reference; (2) crouching low with bent knees,
ready to jump; (3) mid-air jump, legs tucked, both arms raised.
Row 2: (4) cheering, both fists raised high, big open smile; (5) laughing, eyes squeezed shut, mouth wide open,
hand on belly; (6) surprised, eyes wide, mouth a small 'o', ears up, hands up near the chest.
Row 3: (7) thinking, one hand on chin, eyes looking up to the side; (8) pointing to the right with one arm
stretched out, cheeky grin; (9) shrugging, both palms up, playful smile.
{style}""",
    },
    {
        "key": "rest", "file": "sheet_rest.png", "cols": 3, "rows": 3, "aspect": "1:1", "ref": "ref",
        "names": ["ref", "look_left", "look_right", "stretch", "sit", "sleep_0", "sleep_1", "dance_0", "dance_1"],
        "prompt": """2D game sprite sheet for a desktop pet, continuing the attached reference sheet: draw
EXACTLY the same character ({character}) with identical colours, outfit, proportions and art style.
Layout: exactly 9 poses in a 3 columns x 3 rows grid, evenly spaced, each pose fully separated from its
neighbours by plenty of empty background, full body visible including feet and tail, all at the same scale.
Row 1: (1) plain idle standing facing the viewer, mouth closed - same as the reference; (2) standing,
body facing the viewer but head and eyes turned to look to the viewer's LEFT; (3) same but looking to the
viewer's RIGHT.
Row 2: (4) big stretch / yawn, both arms straight up, eyes closed, mouth open; (5) sitting on the ground
cross-legged facing the viewer, relaxed smile, tail curled around; (6) the same sitting pose asleep, eyes
closed, head tilted to one side, peaceful.
Row 3: (7) asleep sitting, head nodded forward the other way, mouth slightly open; (8) dancing, one arm up
one down, one knee raised, happy; (9) dancing, mirrored moves (other arm up, other knee raised).
{style}""",
    },
]


def load_key(env_file: str = None) -> str:
    if env_file:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    try:
        sys.path.insert(0, HERE)
        import universal_autocomplete as ua

        return ua.resolve_config(ua.load_raw_config(), "openrouter").get("api_key", "")
    except Exception:
        return ""


def data_url(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")


def generate_sheet(key: str, sheet: dict, quality: str, reference: str = None):
    payload = {
        "model": "openai/gpt-image-2",
        "prompt": sheet["prompt"].format(character=CHARACTER, style=STYLE),
        "aspect_ratio": sheet["aspect"],
        "quality": quality,
        "background": "opaque",
        "n": 1,
    }
    if reference:
        payload["input_references"] = [{"type": "image_url", "image_url": {"url": data_url(reference)}}]
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/images",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    print(f"Drawing '{sheet['key']}' sheet with openai/gpt-image-2 ({quality})... ~30-120 s")
    try:
        with urllib.request.urlopen(req, timeout=400) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Image API error {e.code}: {e.read().decode('utf-8', 'replace')[:600]}")
    data = res.get("data") or []
    if not data or "b64_json" not in data[0]:
        sys.exit(f"Unexpected response: {json.dumps(res)[:400]}")
    path = os.path.join(OUT_DIR, sheet["file"])
    with open(path, "wb") as f:
        f.write(base64.b64decode(data[0]["b64_json"]))
    print(f"Saved sheet -> {path}")


def foreground_mask(img: Image.Image):
    """True where the pixel is character, False where it's the magenta backdrop (incl. anti-aliased halo)."""
    w, h = img.size
    px = img.load()
    mask = [[False] * w for _ in range(h)]
    for y in range(h):
        row = mask[y]
        for x in range(w):
            r, g, b = px[x, y][:3]
            # magenta = red and blue both well above green; halo pixels are magenta blended with outline
            row[x] = min(r, b) - g < 60
    return mask


def runs(flags, min_gap):
    """[start, end) spans of True values, merging gaps shorter than min_gap."""
    spans, start, gap = [], None, 0
    for i, v in enumerate(flags + [False] * (min_gap + 1)):
        if v:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap > min_gap:
                spans.append((start, i - gap + 1))
                start, gap = None, 0
    return spans


def cut_sheet(sheet: dict) -> dict:
    """Returns {frame name: RGBA image scaled so the sheet's reference pose is TARGET_HEIGHT tall}."""
    path = os.path.join(OUT_DIR, sheet["file"])
    img = Image.open(path).convert("RGB")
    scale = img.width / 800 if img.width > 800 else 1
    small = img.resize((800, round(img.height / scale)), Image.NEAREST) if scale != 1 else img
    mask = foreground_mask(small)
    h, w = len(mask), len(mask[0])

    rows, cols = sheet["rows"], sheet["cols"]
    row_spans = [s for s in runs([sum(r) > 2 for r in mask], 6) if s[1] - s[0] > h * 0.08]
    boxes = []
    for y0, y1 in row_spans:
        col_flags = [sum(mask[y][x] for y in range(y0, y1)) > 2 for x in range(w)]
        boxes += [(x0, y0, x1, y1) for x0, x1 in runs(col_flags, 4) if x1 - x0 > w * 0.04]
    if len(row_spans) != rows or len(boxes) != rows * cols:
        sys.exit(f"'{sheet['key']}': expected {rows} rows x {cols} poses, found {len(row_spans)} rows / "
                 f"{len(boxes)} poses. Look at {path} and redraw it (--redraw {sheet['key']}).")

    frames = {}
    for name, (x0, y0, x1, y1) in zip(sheet["names"], boxes):
        crop = img.crop(tuple(round(v * scale) for v in (x0, y0, x1, y1))).convert("RGBA")
        cm = foreground_mask(crop.convert("RGB"))
        px = crop.load()
        for y in range(crop.height):
            for x in range(crop.width):
                if not cm[y][x]:
                    px[x, y] = (0, 0, 0, 0)
        frames[name] = crop.crop(crop.getbbox())
    k = TARGET_HEIGHT / frames[sheet["ref"]].height
    return {n: f.resize((max(1, round(f.width * k)), max(1, round(f.height * k))), Image.LANCZOS)
            for n, f in frames.items() if n != "ref"}


def save_frames(frames: dict):
    """All frames share one canvas (the NPC window size), feet on the bottom-centre."""
    cw = max(f.width for f in frames.values()) + 4
    ch = max(f.height for f in frames.values()) + 2
    for name, f in frames.items():
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        canvas.paste(f, ((cw - f.width) // 2, ch - f.height - 1), f)
        # Binary alpha: Tk's -transparentcolor window can't blend partial alpha
        canvas.putalpha(canvas.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
        canvas.save(os.path.join(OUT_DIR, f"{name}.png"))
        canvas.transpose(Image.FLIP_LEFT_RIGHT).save(os.path.join(OUT_DIR, f"{name}_left.png"))
    print(f"Saved {len(frames)} frames ({cw}x{ch}) + mirrored copies -> {OUT_DIR}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", help=".env file containing OPENROUTER_API_KEY=...")
    ap.add_argument("--redraw", nargs="*", default=[], help="sheet keys to draw again: base actions rest")
    ap.add_argument("--cut-only", action="store_true", help="don't call the API, just cut existing sheets")
    ap.add_argument("--quality", default="medium", choices=["low", "medium", "high"])
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    todo = [] if args.cut_only else [
        s for s in SHEETS if s["key"] in args.redraw or not os.path.exists(os.path.join(OUT_DIR, s["file"]))]
    if todo:
        key = load_key(args.env_file)
        if not key:
            sys.exit("No OpenRouter API key: set OPENROUTER_API_KEY, pass --env-file, or save one via the NPC chat.")
        base_sheet = os.path.join(OUT_DIR, SHEETS[0]["file"])
        for s in todo:
            ref = base_sheet if s["key"] != "base" and os.path.exists(base_sheet) else None
            generate_sheet(key, s, args.quality, ref)

    frames = {}
    for s in SHEETS:
        if os.path.exists(os.path.join(OUT_DIR, s["file"])):
            frames.update(cut_sheet(s))
    save_frames(frames)


if __name__ == "__main__":
    main()
