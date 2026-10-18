"""
Catdex - Step 4: a new cat gets a vibe, a personality and a trading card.

  1. VIBE (zero-shot, CLIP from Step 2): which posture does the cat show? loaf, sprawled, alert,
     crouching, grooming, exploring... -> a vibe like "Cozy Royalty". With several photos, majority vote.
  2. PERSONALITY (generated ONCE, then kept): Claude looks at the photo and writes a title, 3 traits,
     5 fun stats, 2 cute "moves", a weakness/resistance, Pokedex-style lore, a favourite thing and
     picks a pastel colour theme - returned as structured JSON, so the card never breaks.
     No API key / no internet? A template personality is used instead, automatically.
  3. CARD: Pillow draws a Pokemon-style trading card PNG: pastel gradient, paw-print pattern, cat ears
     on the photo frame, HP + paw-type badge, moves with paw energy costs, stats, weakness/resistance,
     lore box. Common = gold border, Uncommon = silver, Rare = holo rainbow.
Everything is stored with the cat in catdex_db (cats.json + cards/ folder).

Install once:  pip install anthropic pillow
API key: set an environment variable called ANTHROPIC_API_KEY, or put it in a .env file next to this
script (never paste the key into this file).

Try it:
  python catdex_step4.py --card cat_0001     -> (re)make the card for one cat already in your Catdex (id or name)
  python catdex_step4.py --card Bluestar --redo  -> same, but writes a NEW personality (e.g. after adding your API key)
  python catdex_step4.py --all               -> make cards for every cat that doesn't have one yet
New cats caught with catdex_step3.py get their card automatically.
"""

import base64
import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path
from collections import Counter
from functools import lru_cache

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

import sys

# ---- API key from a .env file -------------------------------------------------
# Put a file called .env next to this script containing ONE line:  ANTHROPIC_API_KEY=sk-ant-...
# (No extra installs needed. Keep .env out of GitHub!)
HERE = Path(__file__).resolve().parent


def _load_env_file():
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    raw = env_file.read_bytes()
    # PowerShell's  echo ... > .env  saves UTF-16, Notepad saves UTF-8 - accept both
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", errors="ignore")
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip().removeprefix("export ").strip() == "ANTHROPIC_API_KEY":
            value = value.strip().strip('"').strip("'").strip()
            if value:
                os.environ["ANTHROPIC_API_KEY"] = value       # the .env file wins over an old/empty variable


_load_env_file()

# python catdex_step4.py --check-key   -> says whether the key can be found (never prints the key itself)
if __name__ == "__main__" and "--check-key" in sys.argv:
    print(f"Script folder:   {HERE}")
    lookalikes = [p.name for p in HERE.iterdir() if p.name.lower().lstrip(".").startswith("env")]
    print(f".env file there: {'yes' if (HERE / '.env').exists() else 'NO'}"
          f"   (files with a similar name: {', '.join(lookalikes) or 'none'})")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("API key:         NOT found")
    else:
        hint = "" if key.startswith("sk-ant-") else "   <- a real key starts with sk-ant-"
        print(f"API key:         found ({len(key)} characters, starts with {key[:7]}){hint}")
    sys.exit()

import catdex_step2 as step2                    # reuse its CLIP model for the vibe
import catdex_step3 as step3                    # the Catdex database

# ---- Settings you can tune -------------------------------------------------
LLM_MODEL = "claude-haiku-4-5-20251001"         # e.g. "claude-haiku-5-5" or "claude-sonnet-5-5" to compare
USE_LLM = True                                  # False = always use the template personality
STAT_NAMES = ["stealth", "zoomies", "charm", "chill", "mischief"]

# Vibes: posture descriptions for CLIP (several per vibe, averaged - same trick as the coat pages)
VIBE_PROMPTS = {
    "Cozy Royalty":        ["a photo of a cat sitting in a loaf position with its paws tucked under",
                            "a cat curled up in a ball, sleeping"],
    "Professional Napper": ["a cat lying stretched out on its side",
                            "a cat sprawled on its back showing its belly"],
    "Dignified Sentinel":  ["a cat sitting upright and alert, looking around",
                            "a cat sitting tall with its ears pointed forward"],
    "Chaos Gremlin":       ["a cat crouched low, ready to pounce",
                            "a playful cat jumping or batting at a toy"],
    "Self-Care Icon":      ["a cat licking and grooming its fur",
                            "a cat washing its face with its paw"],
    "Curious Explorer":    ["a cat walking and exploring",
                            "a cat sniffing something curiously"],
}

# Rarity by coat page (how often you'd expect to meet one)
RARITY = {"tabby": "Common", "ginger": "Common", "black": "Common", "grey": "Common",
          "tuxedo": "Uncommon", "white": "Uncommon",
          "calico": "Rare", "tortoiseshell": "Rare", "colourpoint": "Rare"}
RARITY_STARS = {"Common": 1, "Uncommon": 2, "Rare": 3}

# Pastel card themes: (background top, background bottom, accent). Claude picks one per cat.
THEMES = {
    "Strawberry Milk": ((255, 233, 240), (255, 199, 218), (232, 92, 140)),
    "Peach Sorbet":    ((255, 241, 228), (255, 206, 170), (238, 122, 62)),
    "Lemon Meringue":  ((255, 251, 224), (255, 228, 142), (214, 150, 16)),
    "Matcha Latte":    ((239, 249, 228), (198, 230, 170), (92, 152, 58)),
    "Blueberry Soda":  ((231, 244, 255), (180, 213, 255), (62, 124, 226)),
    "Lavender Mochi":  ((245, 237, 255), (212, 195, 255), (136, 100, 204)),
    "Cotton Candy":    ((255, 236, 248), (200, 222, 255), (218, 94, 174)),
    "Twilight Purr":   ((226, 222, 248), (168, 160, 218), (86, 72, 166)),
}
THEME_HINTS = {
    "Strawberry Milk": "pinks, for white, cream or pink-nosed cats",
    "Peach Sorbet":    "warm oranges, for ginger and orange tabby cats",
    "Lemon Meringue":  "sunny yellows, for golden, brown tabby or sunbathing cats",
    "Matcha Latte":    "soft greens, for green-eyed cats or cats in gardens and plants",
    "Blueberry Soda":  "cool blues, for grey or blue-eyed cats",
    "Lavender Mochi":  "lilacs, for colourpoint, fluffy or dreamy cats",
    "Cotton Candy":    "pink-to-blue, for calico, tortoiseshell and patchy cats",
    "Twilight Purr":   "dusky purples, for black or tuxedo cats and night owls",
}
COAT_THEMES = {"tabby": "Lemon Meringue", "ginger": "Peach Sorbet", "black": "Twilight Purr",
               "grey": "Blueberry Soda", "tuxedo": "Twilight Purr", "white": "Strawberry Milk",
               "calico": "Cotton Candy", "tortoiseshell": "Cotton Candy", "colourpoint": "Lavender Mochi"}

CARDS = step3.DB / "cards"


# ---- 1. Vibe ------------------------------------------------------------------
VIBE_NAMES = list(VIBE_PROMPTS)
with torch.no_grad():
    _vecs = []
    for vibe in VIBE_NAMES:
        inputs = step2.processor(text=VIBE_PROMPTS[vibe], return_tensors="pt", padding=True)
        v = step2._normalise(step2._as_tensor(step2.model.get_text_features(**inputs)))
        _vecs.append(step2._normalise(v.mean(dim=0)))
    VIBE_VECTORS = torch.stack(_vecs)


def read_vibe(crops):
    """Majority vote of the posture vibe across the photos. Returns (vibe, share of photos that agreed)."""
    if not isinstance(crops, list):
        crops = [crops]
    votes = []
    for crop in crops:
        photo = step2.clip_features(crop)
        with torch.no_grad():
            votes.append(VIBE_NAMES[int(torch.argmax(VIBE_VECTORS @ photo))])
    vibe, count = Counter(votes).most_common(1)[0]
    return vibe, count / len(votes)


# ---- 2. Personality -----------------------------------------------------------
PERSONALITY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "traits": {"type": "array", "items": {"type": "string"}},
        "stats": {"type": "object",
                  "properties": {s: {"type": "integer"} for s in STAT_NAMES},
                  "required": STAT_NAMES, "additionalProperties": False},
        "moves": {"type": "array",
                  "items": {"type": "object",
                            "properties": {"name": {"type": "string"}, "cost": {"type": "integer"},
                                           "power": {"type": "integer"}, "text": {"type": "string"}},
                            "required": ["name", "cost", "power", "text"], "additionalProperties": False}},
        "weakness": {"type": "string"},
        "resistance": {"type": "string"},
        "lore": {"type": "string"},
        "favourite_thing": {"type": "string"},
        "theme": {"type": "string", "enum": list(THEMES)},
    },
    "required": ["title", "traits", "stats", "moves", "weakness", "resistance", "lore", "favourite_thing", "theme"],
    "additionalProperties": False,
}


def _image_block(crop):
    """Turn an OpenCV crop into the image format the Claude API expects (small JPEG, base64)."""
    h, w = crop.shape[:2]
    scale = min(1.0, 768 / max(h, w))                    # smaller image = fewer tokens = cheaper
    small = cv2.resize(crop, (int(w * scale), int(h * scale)))
    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpg.tobytes()).decode()}}


def _cut(text, limit):
    """Shorten text to at most `limit` characters, at a word boundary, adding '...' if it was cut."""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return text[:limit - 3].rsplit(" ", 1)[0].rstrip(",;:-") + "..."


def _tidy(p):
    """Make sure the personality fits on the card, whatever came back."""
    p["traits"] = [_cut(t, 28) for t in p.get("traits", [])][:3] or ["Mysterious"]
    p["stats"] = {s: max(1, min(99, int(p.get("stats", {}).get(s, 50)))) for s in STAT_NAMES}
    p["title"] = _cut(p.get("title", "The Enigma"), 40)
    p["lore"] = _cut(p.get("lore", ""), 200)
    p["favourite_thing"] = _cut(p.get("favourite_thing", ""), 40)
    moves = []
    for m in list(p.get("moves") or [])[:2]:
        if isinstance(m, dict) and m.get("name"):
            moves.append({"name": _cut(m["name"], 22),
                          "cost": max(1, min(3, int(m.get("cost", 1)))),
                          "power": max(10, min(150, round(int(m.get("power", 30)) / 10) * 10)),
                          "text": _cut(m.get("text", ""), 80)})
    if moves:
        p["moves"] = moves
    else:
        p.pop("moves", None)                              # filled in later by _complete()
    for key in ("weakness", "resistance"):
        if p.get(key):
            p[key] = _cut(p[key], 20)
    if p.get("theme") not in THEMES:
        p.pop("theme", None)
    return p


def personality_from_claude(crop, name, coat, vibe):
    """Ask Claude for a personality. Returns a dict, or raises an error if anything goes wrong."""
    import anthropic                                      # imported here so the template works without it
    client = anthropic.Anthropic()                        # reads the ANTHROPIC_API_KEY environment variable
    themes = "; ".join(f"'{t}' ({hint})" for t, hint in THEME_HINTS.items())
    prompt = (
        f"You write cute, colourful trading cards for Catdex, an app where people collect the cats they meet. "
        f"The cards look like Pokemon cards, but cosier and sillier: every cat is a little hero.\n"
        f"This cat is called {name}. Its Catdex coat page is '{coat}' and its vibe in this photo is '{vibe}'.\n"
        f"First look closely at the photo (coat colours and pattern, eyes, expression, pose, where it is) and let "
        f"those real details shape everything you write, so the card could only belong to this cat. Then fill in:\n"
        f"- title: its 'species line', 2 to 5 words, like 'Windowsill Philosopher' or 'Sunbeam Collector'\n"
        f"- traits: exactly 3, each 1 to 3 words\n"
        f"- stats: whole numbers from 1 to 99 for {', '.join(STAT_NAMES)}, fitting the photo and vibe; "
        f"make one or two clearly stand out\n"
        f"- moves: exactly 2 Pokemon-style attacks with punny cat names of 1 to 3 words (e.g. 'Biscuit Kneading', "
        f"'Slow Blink', 'Midnight Zoomies'). cost: 1 to 3 paw energies. power: 10 to 120 in steps of 10. "
        f"The second move costs more and is stronger. text: what the move does, at most 12 words, "
        f"silly and gentle - nothing violent\n"
        f"- weakness and resistance: 1 to 3 words each, funny everyday cat things "
        f"(e.g. 'Cucumbers', 'Bath time', 'Vacuum cleaner')\n"
        f"- lore: Pokedex-style flavour text, 1 or 2 sentences, at most 30 words\n"
        f"- favourite_thing: a short phrase, at most 5 words\n"
        f"- theme: the card's colour theme - pick the one that best suits this cat's coat and mood: {themes}\n"
        f"Keep it kind, funny and family-friendly. No claims about the cat's health or real behaviour."
    )
    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": [_image_block(crop), {"type": "text", "text": prompt}]}],
        output_config={"format": {"type": "json_schema", "schema": PERSONALITY_SCHEMA}},
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stopped early ({response.stop_reason})")
    text = next(block.text for block in response.content if block.type == "text")
    raw = json.loads(text)
    missing = set(PERSONALITY_SCHEMA["required"]) - set(raw)
    if missing:
        raise ValueError(f"reply was missing {sorted(missing)}")
    p = _tidy(raw)
    p["source"] = LLM_MODEL
    return p


TEMPLATES = {
    "Cozy Royalty":        (["The Loaf Monarch", "Duchess of the Cushion", "The Warm Spot Collector"],
                            ["Expert loafer", "Unbothered royalty", "Blanket connoisseur", "Slow blinker"],
                            "the sunniest cushion", dict(stealth=40, zoomies=15, charm=80, chill=95, mischief=25)),
    "Professional Napper": (["The Horizontal Champion", "Sleeper of Many Sofas", "The Belly Trap"],
                            ["Nap specialist", "Floor puddle", "Dreams of tuna", "Hard to wake"],
                            "a warm laptop", dict(stealth=30, zoomies=10, charm=75, chill=99, mischief=30)),
    "Dignified Sentinel":  (["The Windowsill Philosopher", "Keeper of the Corridor", "The Watchful One"],
                            ["Always on duty", "Judges quietly", "Ears like radar", "Very observant"],
                            "a good lookout spot", dict(stealth=75, zoomies=35, charm=70, chill=60, mischief=40)),
    "Chaos Gremlin":       (["The Midnight Sprinter", "Agent of Chaos", "The Pounce Prodigy"],
                            ["Zoomies on demand", "Fearless hunter", "Knocks things over", "Tiny tornado"],
                            "anything that moves", dict(stealth=65, zoomies=95, charm=70, chill=20, mischief=90)),
    "Self-Care Icon":      (["The Fluff Perfectionist", "Grooming Guru", "The Spa Regular"],
                            ["Immaculate coat", "Takes their time", "Fashionably late", "Glows up daily"],
                            "a long grooming session", dict(stealth=50, zoomies=30, charm=90, chill=75, mischief=35)),
    "Curious Explorer":    (["The Neighbourhood Inspector", "Seeker of Boxes", "The Wandering Scholar"],
                            ["Sniffs everything", "Box enthusiast", "Never lost", "Asks questions"],
                            "a brand-new cardboard box", dict(stealth=60, zoomies=60, charm=75, chill=50, mischief=65)),
}


TEMPLATE_MOVES = {
    "Cozy Royalty":        [("Slow Blink", "The opponent feels so loved they forget to move."),
                            ("Royal Loaf", "Becomes a bread shape. Cannot be moved for 1 turn."),
                            ("Biscuit Kneading", "Kneads the nearest blanket into perfect softness.")],
    "Professional Napper": [("Belly Trap", "Shows the fluffy belly. Touching it is a mistake."),
                            ("Power Nap", "Heals 20 damage while snoring softly."),
                            ("Sofa Melt", "Turns into a puddle and slips out of reach.")],
    "Dignified Sentinel":  [("Window Watch", "Spots a bird. The opponent looks too, distracted."),
                            ("Judging Stare", "The opponent questions all their life choices."),
                            ("Radar Ears", "Hears the treat bag from three rooms away.")],
    "Chaos Gremlin":       [("Midnight Zoomies", "Sprints down the hallway at 3am. Very loud."),
                            ("Sneaky Pounce", "Leaps out of nowhere onto a wiggling toe."),
                            ("Table Swipe", "Knocks one thing off the table. Keeps eye contact.")],
    "Self-Care Icon":      [("Fluff Polish", "Grooms until the coat sparkles. Charm goes up."),
                            ("Tongue Bath", "Cleans the opponent too, whether they like it or not."),
                            ("Paw Wash", "Washes face with one paw. Looks extra fabulous.")],
    "Curious Explorer":    [("Box Dive", "Hides in a box. 50% chance to become invisible."),
                            ("Sniff Check", "Investigates the opponent very, very thoroughly."),
                            ("Shelf Climb", "Climbs the highest shelf to look down on everyone.")],
}
WEAKNESSES = ["Cucumbers", "Bath time", "Vacuum cleaner", "Vet trip", "Closed doors"]
RESISTANCES = ["Bedtime", "Being ignored", "Alarm clocks", "Cold floors", "Rainy days"]


def personality_from_template(name, coat, vibe):
    """No API needed: pick from lists, with small random changes. Same cat name -> same result."""
    rng = random.Random(f"{name}-{coat}-{vibe}")
    titles, traits, favourite, base = TEMPLATES.get(vibe, TEMPLATES["Curious Explorer"])
    title = rng.choice(titles)
    (n1, t1), (n2, t2) = rng.sample(TEMPLATE_MOVES.get(vibe, TEMPLATE_MOVES["Curious Explorer"]), 2)
    return _tidy({
        "title": title,
        "traits": rng.sample(traits, 3),
        "stats": {s: base[s] + rng.randint(-8, 8) for s in STAT_NAMES},
        "moves": [{"name": n1, "cost": 1, "power": rng.choice([10, 20, 30]), "text": t1},
                  {"name": n2, "cost": 2, "power": rng.choice([50, 60, 70, 80]), "text": t2}],
        "weakness": rng.choice(WEAKNESSES),
        "resistance": rng.choice(RESISTANCES),
        "lore": f"{name} is a {coat} cat with {vibe.lower()} energy, best known as {title.lower()}. "
                f"Rumour has it {name} can't resist {favourite}.",
        "favourite_thing": favourite,
        "theme": COAT_THEMES.get(coat, "Strawberry Milk"),
        "source": "template",
    })


def _complete(p, name, coat, vibe):
    """Cats saved before cards had moves/themes: fill in only the missing parts from the template."""
    extra = personality_from_template(name, coat, vibe)
    for key in ("moves", "weakness", "resistance", "theme"):
        if not p.get(key):
            p[key] = extra[key]
    return p


def make_personality(crop, name, coat, vibe):
    """Claude if possible, otherwise the template - so the app never breaks."""
    if USE_LLM and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return personality_from_claude(crop, name, coat, vibe)
        except Exception as e:                            # no internet, bad key, model busy...
            print(f"  (Claude unavailable: {type(e).__name__} - using the template personality)")
    elif USE_LLM:
        print("  (No ANTHROPIC_API_KEY found - using the template personality)")
    return personality_from_template(name, coat, vibe)


# ---- 3. Trading card ----------------------------------------------------------
SS = 2                                                    # draw at 2x, then shrink = smooth anti-aliased edges
W, H = 750 * SS, 1050 * SS                                # real trading card shape (63 x 88 mm)
INK = (58, 42, 64)                                        # soft plum instead of harsh black
WHITE = (255, 255, 255)
EAR_PINK = (255, 184, 204)
RARITY_BORDERS = {"Common": (255, 210, 80), "Uncommon": (200, 214, 232)}   # Rare gets a holo rainbow

# Rounded, friendly fonts first; falls back to Arial / DejaVu / Pillow's own font on other systems
FONT_FILES = {
    "display": ["comicbd.ttf", "Comic Sans MS Bold.ttf", "seguibl.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "bold":    ["segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"],
    "italic":  ["segoeuii.ttf", "ariali.ttf", "Arial Italic.ttf", "DejaVuSans-Oblique.ttf"],
    "regular": ["segoeui.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf"],
}


@lru_cache(maxsize=None)
def _font(size, style="regular"):
    """A font in the given style ('display', 'bold', 'italic' or 'regular') on Windows, Mac or Linux."""
    for n in FONT_FILES[style]:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                     # very old Pillow
        return ImageFont.load_default()


def _fit(draw, text, max_width, size, style="regular", min_size=14):
    """Shrink the font until the text fits; if it still doesn't, shorten it at a word with '...'."""
    while size > min_size and draw.textlength(text, font=_font(size, style)) > max_width:
        size -= 1
    font = _font(size, style)
    if draw.textlength(text, font=font) > max_width:
        words = text.split()
        while words and draw.textlength(" ".join(words) + "...", font=font) > max_width:
            words.pop()
        text = " ".join(words) + "..."
    return text, font


def _wrap(draw, text, font, max_width, max_lines):
    """Split text into lines that fit max_width pixels; if it runs over max_lines, end with '...'."""
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if not line or draw.textlength(test, font=font) <= max_width:
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        while last and draw.textlength(last + "...", font=font) > max_width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines = lines[:max_lines - 1] + [last + "..."]
    return lines


def _mix(c1, c2, t):
    """Blend two colours: t=0 gives c1, t=1 gives c2."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _gradient(w, h, top, bottom):
    """A vertical colour gradient image."""
    t = np.linspace(0, 1, h)[:, None, None]
    rows = (np.array(top) * (1 - t) + np.array(bottom) * t).astype(np.uint8)
    return Image.fromarray(np.repeat(rows, w, axis=1), "RGB")


def _holo(w, h):
    """A pastel diagonal rainbow, like a holographic rare card."""
    yy, xx = np.mgrid[0:h, 0:w]
    hue = ((xx + yy) / (w + h) * 1.5 % 1.0 * 255).astype(np.uint8)
    sat, val = np.full_like(hue, 105), np.full_like(hue, 255)
    return Image.merge("HSV", [Image.fromarray(c, "L") for c in (hue, sat, val)]).convert("RGB")


def _rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def _paw(d, cx, cy, r, fill):
    """A paw print: one big pad and four toe beans."""
    d.ellipse([cx - r * 0.62, cy - r * 0.15, cx + r * 0.62, cy + r * 0.75], fill=fill)
    for dx, dy in ((-0.66, -0.38), (-0.24, -0.74), (0.24, -0.74), (0.66, -0.38)):
        tx, ty = cx + dx * r, cy + dy * r
        d.ellipse([tx - r * 0.22, ty - r * 0.27, tx + r * 0.22, ty + r * 0.27], fill=fill)


def _star(d, cx, cy, r, fill, points=5, inner=0.45):
    """A star; points=4 with a thin inner radius makes a sparkle."""
    pts = []
    for i in range(points * 2):
        a = math.pi * i / points - math.pi / 2
        rr = r if i % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def _heart(d, cx, cy, r, fill):
    d.ellipse([cx - r, cy - r * 0.75, cx, cy + r * 0.25], fill=fill)
    d.ellipse([cx, cy - r * 0.75, cx + r, cy + r * 0.25], fill=fill)
    d.polygon([(cx - r * 0.98, cy - r * 0.15), (cx + r * 0.98, cy - r * 0.15), (cx, cy + r * 0.9)], fill=fill)


def _hp(stats):
    """Card HP from the stats: 40 to 120, in steps of 10."""
    return int(round((40 + sum(stats.values()) / len(stats) * 0.8) / 10) * 10)


def draw_card(cat_id, cat, crop):
    """Draw a cute Pokemon-style trading card as a PNG. Returns its path."""
    rarity = RARITY.get(cat.get("coat") or "", "Common")
    n_stars = RARITY_STARS[rarity]
    p = cat["personality"]
    top, bottom, accent = THEMES.get(p.get("theme"), THEMES["Strawberry Milk"])
    soft = _mix(accent, WHITE, 0.72)
    rng = random.Random(cat_id)                           # same cat -> same paw/sparkle pattern

    # Border (gold / silver / holo rainbow) and the pastel inner panel
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    border = _holo(W, H) if rarity == "Rare" else Image.new("RGB", (W, H), RARITY_BORDERS[rarity])
    card.paste(border, (0, 0), _rounded_mask((W, H), 64))
    m = 34
    card.paste(_gradient(W - 2 * m, H - 2 * m, top, bottom), (m, m), _rounded_mask((W - 2 * m, H - 2 * m), 44))

    # Faint paw prints scattered over the background, plus see-through panels for stats and lore
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for _ in range(30):
        _paw(ld, rng.randint(80, W - 80), rng.randint(80, H - 80), rng.randint(20, 40), accent + (30,))
    ld.rounded_rectangle([78, 1468, W - 78, 1614], radius=26, fill=WHITE + (150,))
    ld.rounded_rectangle([78, 1740, W - 78, 1948], radius=26, fill=WHITE + (130,))
    card.alpha_composite(layer)
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([m, m, W - m, H - m], radius=44, outline=WHITE, width=6)   # crisp edge vs the border

    # Header: rarity pill (stars + label), name, HP and a paw "type" badge
    f = _font(26, "bold")
    label_x = 98 + (n_stars - 1) * 34 + 26
    d.rounded_rectangle([70, 56, label_x + d.textlength(rarity.upper(), font=f) + 22, 102],
                        radius=23, fill=WHITE, outline=accent, width=3)
    for i in range(n_stars):
        _star(d, 98 + i * 34, 79, 15, accent)
    d.text((label_x, 79), rarity.upper(), font=f, fill=accent, anchor="lm")

    bx, by = W - 112, 124
    d.ellipse([bx - 52, by - 52, bx + 52, by + 52], fill=accent, outline=WHITE, width=6)
    _paw(d, bx, by + 4, 30, WHITE)
    hp_font = _font(76, "display")
    hp = str(_hp(p["stats"]))
    hp_right = bx - 70
    d.text((hp_right, 172), hp, font=hp_font, fill=accent, anchor="rs", stroke_width=4, stroke_fill=WHITE)
    hp_left = hp_right - d.textlength(hp, font=hp_font) - 10
    d.text((hp_left, 172), "HP", font=_font(32, "bold"), fill=INK, anchor="rs")

    name, name_font = _fit(d, cat["name"], hp_left - 40 - 72, 86, "display", min_size=44)
    d.text((72, 172), name, font=name_font, fill=INK, anchor="ls", stroke_width=6, stroke_fill=WHITE)

    # Photo frame with cat ears peeking over the top
    fx0, fy0, fx1, fy1 = 78, 262, W - 78, 1000
    frame_col = _mix(accent, WHITE, 0.12)
    for ex in (fx0 + 160, fx1 - 160):
        d.polygon([(ex - 82, fy0 + 30), (ex, fy0 - 62), (ex + 82, fy0 + 30)], fill=frame_col)
        d.polygon([(ex - 44, fy0 + 20), (ex, fy0 - 28), (ex + 44, fy0 + 20)], fill=EAR_PINK)
    d.rounded_rectangle([fx0, fy0, fx1, fy1], radius=34, fill=frame_col)
    d.rounded_rectangle([fx0 + 13, fy0 + 13, fx1 - 13, fy1 - 13], radius=26, outline=WHITE, width=5)
    pw, ph = fx1 - fx0 - 36, fy1 - fy0 - 36
    img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    scale = max(pw / img.width, ph / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    left, top_ = (img.width - pw) // 2, (img.height - ph) // 2
    card.paste(img.crop((left, top_, left + pw, top_ + ph)), (fx0 + 18, fy0 + 18), _rounded_mask((pw, ph), 22))

    # Sparkles on the photo corners - more for rarer cats
    spots = [(fx0 + 64, fy0 + 66), (fx1 - 60, fy0 + 70), (fx1 - 70, fy1 - 110), (fx0 + 70, fy1 - 120),
             (fx0 + 130, fy0 + 120), (fx1 - 128, fy0 + 132)]
    for sx, sy in spots[:2 + 2 * n_stars]:
        r = rng.randint(18, 32)
        _star(d, sx, sy, r + 5, accent, points=4, inner=0.3)
        _star(d, sx, sy, r, WHITE, points=4, inner=0.28)

    # Trait pills sitting on the bottom edge of the frame
    traits = p["traits"]
    for size in range(30, 19, -1):
        f = _font(size, "bold")
        widths = [d.textlength(t, font=f) + 96 for t in traits]
        total = sum(widths) + 18 * (len(traits) - 1)
        if total <= W - 200:
            break
    x = (W - total) / 2
    for t, w in zip(traits, widths):
        d.rounded_rectangle([x, fy1 - 30, x + w, fy1 + 30], radius=30, fill=WHITE, outline=accent, width=4)
        _heart(d, x + 38, fy1, 14, accent)
        d.text((x + 64, fy1), t, font=f, fill=INK, anchor="lm")
        x += w + 18

    # Species ribbon: title, coat page and vibe
    d.rounded_rectangle([110, 1050, W - 110, 1104], radius=27, fill=soft)
    line = f"{p['title']}  ·  {(cat.get('coat') or 'mystery').title()} coat  ·  {cat.get('vibe', '?')}"
    line, f = _fit(d, line, W - 280, 32, "italic", min_size=22)
    d.text((W // 2, 1077), line, font=f, fill=_mix(accent, INK, 0.45), anchor="mm")

    # Moves: paw energy cost, name, power, description
    y = 1128
    for i, mv in enumerate(p.get("moves", [])[:2]):
        for k in range(mv["cost"]):
            cx, cy = 108 + k * 58, y + 50
            d.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], fill=accent, outline=WHITE, width=4)
            _paw(d, cx, cy + 3, 15, WHITE)
        name_x = 108 + mv["cost"] * 58 + 4
        mv_name, f = _fit(d, mv["name"], W - name_x - 200, 50, "display", min_size=30)
        d.text((name_x, y + 50), mv_name, font=f, fill=INK, anchor="lm")
        d.text((W - 96, y + 50), str(mv["power"]), font=_font(66, "display"), fill=INK, anchor="rm")
        f = _font(31)
        for j, text in enumerate(_wrap(d, mv["text"], f, W - 220, 2)):
            d.text((108, y + 100 + j * 38), text, font=f, fill=_mix(INK, WHITE, 0.15), anchor="lm")
        if i == 0:
            for dx in range(110, W - 100, 26):                # dotted line between the two moves
                d.ellipse([dx - 4, y + 168, dx + 4, y + 176], fill=soft)
        y += 172

    # Stats: five little meters
    col = (W - 156) / len(STAT_NAMES)
    for i, s in enumerate(STAT_NAMES):
        cx = 78 + col * (i + 0.5)
        value = p["stats"][s]
        d.text((cx, 1500), s.upper(), font=_font(24, "bold"), fill=_mix(INK, WHITE, 0.2), anchor="mm")
        d.text((cx, 1544), str(value), font=_font(42, "display"), fill=accent, anchor="mm")
        d.rounded_rectangle([cx - 92, 1578, cx + 92, 1594], radius=8, fill=soft)
        d.rounded_rectangle([cx - 92, 1578, cx - 92 + max(16, 184 * value / 99), 1594], radius=8, fill=accent)

    # Weakness / resistance / loves, like the bottom row of a real card
    col = (W - 156) / 3
    for i, (label, value) in enumerate([("weakness", p.get("weakness", "")),
                                        ("resistance", p.get("resistance", "")),
                                        ("loves", p.get("favourite_thing", ""))]):
        cx = 78 + col * (i + 0.5)
        d.text((cx, 1648), label, font=_font(26), fill=_mix(INK, WHITE, 0.35), anchor="mm")
        value, f = _fit(d, value or "?", col - 40, 32, "bold", min_size=20)
        d.text((cx, 1694), value, font=f, fill=INK, anchor="mm")
        if i:
            d.line([78 + col * i, 1632, 78 + col * i, 1712], fill=soft, width=4)

    # Lore box, Pokedex style
    d.rounded_rectangle([78, 1740, W - 78, 1948], radius=26, outline=accent, width=4)
    f = _font(32, "italic")
    lines = _wrap(d, p.get("lore", ""), f, W - 156 - 70, 4)
    ly = 1844 - (len(lines) - 1) * 21                     # centre the lines vertically in the box
    for j, text in enumerate(lines):
        d.text((W // 2, ly + j * 42), text, font=f, fill=INK, anchor="mm")

    # Footer: Catdex number, a little paw-heart-paw, date
    seen = (cat.get("first_seen") or "")[:10]
    d.text((88, 1998), f"Catdex {cat_id.replace('cat_', '#')}", font=_font(26, "bold"), fill=INK, anchor="lm")
    _paw(d, W // 2 - 44, 2000, 16, accent)
    _heart(d, W // 2, 1998, 15, EAR_PINK)
    _paw(d, W // 2 + 44, 2000, 16, accent)
    d.text((W - 88, 1998), f"first seen {seen}  ·  stats just for fun", font=_font(24),
           fill=_mix(INK, WHITE, 0.3), anchor="rm")

    CARDS.mkdir(parents=True, exist_ok=True)
    path = CARDS / f"{cat_id}.png"
    _save_safely(card.resize((W // SS, H // SS), Image.LANCZOS), path)
    return path


def _save_safely(img, path, tries=6):
    """Save to a temporary file, then swap it in. Retries for a few seconds if the old card is locked
    (OneDrive syncing it, or it is open in an image viewer) instead of crashing."""
    tmp = path.with_name(path.stem + ".tmp.png")
    img.save(tmp)
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == tries - 1:
                tmp.unlink(missing_ok=True)
                raise OSError(f"Could not save {path.name}: it is locked. Close any window showing it, "
                              f"or pause OneDrive syncing, then try again.") from None
            time.sleep(0.5 * (attempt + 1))                # waits 0.5s, 1s, 1.5s... (~7s in total)


# ---- Putting it together ------------------------------------------------------
def welcome_new_cat(cat_id, crops, db=None):
    """Called right after a new cat is caught: vibe -> personality (once) -> card. Returns the card path."""
    db = db or step3.load_db()
    cat = db["cats"][cat_id]
    if not isinstance(crops, list):
        crops = [crops]
    if "vibe" not in cat:
        cat["vibe"], _ = read_vibe(crops)
    if "personality" not in cat:                          # generated ONCE, then kept forever
        cat["personality"] = make_personality(crops[0], cat["name"], cat.get("coat") or "mystery", cat["vibe"])
    _complete(cat["personality"], cat["name"], cat.get("coat") or "mystery", cat["vibe"])
    path = draw_card(cat_id, cat, crops[0])
    cat["card"] = str(path.relative_to(step3.DB))
    step3.save_db(db)
    return path


def card_for_existing_cat(cat_id, db=None):
    """Make a card for a cat that's already in the Catdex, using its stored photos."""
    db = db or step3.load_db()
    cat = db["cats"][cat_id]
    crops = [cv2.imread(str(step3.DB / "photos" / cat_id / p)) for p in cat["photos"]]
    crops = [c for c in crops if c is not None]
    if not crops:
        raise SystemExit(f"No photos found for {cat_id}")
    return welcome_new_cat(cat_id, crops, db)


if __name__ == "__main__":
    import sys
    db = step3.load_db()
    redo = "--redo" in sys.argv                               # also write a brand-new personality
    args = [a for a in sys.argv[1:] if a != "--redo"]
    if len(args) >= 2 and args[0] == "--card":
        wanted = " ".join(args[1:])                           # an id (cat_0001) or a name (Bluestar)
        matches = step3.find_cat_id(wanted, db)
        if not matches:
            sys.exit(f"No cat called '{wanted}'. Run  python catdex_step3.py --list  to see the ids.")
        if len(matches) > 1:
            sys.exit(f"{len(matches)} cats are called '{wanted}' ({', '.join(matches)}). Use the id instead.")
        if redo:
            db["cats"][matches[0]].pop("personality", None)   # forget the old one, so a new one is written
        print(f"Card saved: {card_for_existing_cat(matches[0], db)}")
    elif len(sys.argv) == 2 and sys.argv[1] == "--all":
        todo = [cid for cid, c in db["cats"].items() if "card" not in c]
        for cid in todo:
            print(f"{cid} ({db['cats'][cid]['name']}): {card_for_existing_cat(cid, db)}")
        print(f"Made {len(todo)} card(s).")
    else:
        sys.exit("Usage: python catdex_step4.py --card <cat id or name> [--redo]   or   python catdex_step4.py --all")