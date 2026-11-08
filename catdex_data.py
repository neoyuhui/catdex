"""
Catdex - data helpers for the app.

Everything the web page needs that ISN'T about how it looks lives here, so app.py can stay
"just the interface":
  1. Upload + scan helpers  - prepare photos, find the cat (Step 1), coat page (Step 2), who is it (Step 3)
  2. Catching               - add a new cat (Step 3) and give it a card (Step 4), or record a sighting
  3. Reading the Catdex     - cat summaries, the collection by coat page, cat of the day, profiles
  4. Badges & stats         - worked out fresh from the database every time, so they're always right
  5. Managing               - rename, remake card, remove, reset

No Gradio in this file - you can test every function from a normal Python script.

Every cat summary ("CatInfo") is a plain dictionary like this:
  {"id": "cat_0001", "name": "Biscuit", "coat": "ginger", "rarity": "Common", "stars": 1,
   "sightings": 3, "photo_count": 4, "first_seen": "2026-10-17T10:00:00", "last_seen": "...",
   "title": "The Loaf Monarch", "card_path": "C:/.../cards/cat_0001.png", "thumb_path": "C:/.../photos/..."}
card_path / thumb_path are None when the file is missing.
"""

import hashlib
import json
import random
import re
import tempfile
import uuid
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from catdex_step1 import find_cat                       # Step 1: find and crop the cat
from catdex_step2 import classify_coat, COAT_NAMES      # Step 2: coat page
import catdex_step3 as step3                            # Step 3: who is it? + the database
import catdex_step4 as step4                            # Step 4: vibe, personality, card
import catdex_safety as safety                          # guardrails: name and card checks

# ---- Settings you can tune -------------------------------------------------
MAX_PHOTOS = 10          # use at most this many photos per scan, so it stays quick
MAX_SIDE = 1600          # uploads are shrunk to at most this many pixels (phone photos are huge)
MAX_NAME = 30            # longest cat name allowed
DEFAULT_NAME = "Mystery Cat"
FRIEND_TARGET = 10       # sightings needed to become "Best friends"
FRIEND_LEVELS = [        # (sightings needed, label) - checked from the top down
    (FRIEND_TARGET, "Best friends"),
    (6, "Good pals"),
    (3, "Pals"),
    (2, "Acquaintance"),
    (0, "Just met"),
]
UNSURE = "unsure"        # coat page for cats Step 2 couldn't decide on (and old cats with no coat)
# Ear-tipping: a small notch on the ear that shows a community cat has been sterilised (TNR).
# Caretakers record it themselves - the AI never guesses it.
EAR_TIPPED = {"yes": "Ear-tipped", "no": "Not ear-tipped", "unsure": "Not checked yet"}
MAX_NOTE = 300           # longest caretaker note allowed

# Handy names for the app
MIN_ENROL_PHOTOS = step3.MIN_ENROL_PHOTOS
DB_DIR = str(step3.DB)

try:
    from pillow_heif import register_heif_opener      # optional: reads iPhone .HEIC photos
    register_heif_opener()                             # (pip install pillow-heif)
except ImportError:
    pass

TEMP = Path(tempfile.mkdtemp(prefix="catdex_"))        # resized copies of uploads go here
step3.DB.mkdir(parents=True, exist_ok=True)            # so the app can show files from it straight away


# =============================================================================
# 1. Upload + scan helpers
# =============================================================================
def prepare_upload(upload):
    """Turn any uploaded photo into a normal, upright, not-too-big JPEG that Step 1 can read.
    Returns (path, original file name), or (None, name) if the file isn't a photo we can open."""
    path = getattr(upload, "name", upload)             # Gradio gives a path (older versions: an object)
    name = Path(str(path)).name
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img).convert("RGB")   # phone photos: apply the "rotate me" tag
    except Exception:
        return None, name
    img.thumbnail((MAX_SIDE, MAX_SIDE))                # 12-megapixel phone photos are slow and not needed
    out = TEMP / f"{uuid.uuid4().hex}.jpg"             # unique name, so two people can't clash
    img.save(out, quality=92)
    return str(out), name


def _friendly_reason(result):
    """Turn Step 1's message into a short, kind reason for skipping a photo."""
    message = result.get("message", "")
    if result.get("is_dog"):
        return "that looks like a dog, not a cat!"
    if message.startswith("Found "):                   # "Found 2 cats. Please photograph one cat at a time."
        return "there's more than one cat in this photo - one cat at a time, please"
    if "dog in the photo" in message:
        return "there's a dog in the photo too - try one with just the cat"
    if "blurry" in message:
        return "it's a bit blurry - try holding still"
    if message.startswith("No cat found"):
        return "couldn't spot a cat in this one"
    if message.startswith("Could not open"):
        return "couldn't open this file"
    return message or "couldn't use this photo"


def find_cats(paths):
    """Step 1 on every photo. Returns (crops, notes): the cat crops (OpenCV BGR images) and a
    friendly note for every photo that was skipped.
    Each item in `paths` can be a prepared path, OR the (path, original name) pair that
    prepare_upload() returns - then notes use the real file name, and unreadable files
    (path None) get a note too. Tip: find_cats([prepare_upload(p) for p in uploads])"""
    crops, notes = [], []
    items = list(paths or [])
    if len(items) > MAX_PHOTOS:
        notes.append(f"Only the first {MAX_PHOTOS} photos were used.")
        items = items[:MAX_PHOTOS]
    for number, item in enumerate(items, start=1):
        path, name = item if isinstance(item, (tuple, list)) else (item, None)
        label = name or f"Photo {number}"
        if path is None:
            notes.append(f"{label}: couldn't open this file - is it a photo? "
                         f"(iPhone: Settings > Camera > Formats > Most Compatible)")
            continue
        try:
            result = find_cat(str(path))
        except Exception as e:                         # a strange file shouldn't stop the whole scan
            print(f"  (Step 1 failed on {label}: {type(e).__name__}: {e})")
            notes.append(f"{label}: couldn't read this photo")
            continue
        if result["ok"]:
            crops.append(result["crop"])
        else:
            notes.append(f"{label}: {_friendly_reason(result)}")
    return crops, notes


def dedupe(crops):
    """Burst shots count once. Returns (distinct crops, how many near-duplicates were dropped)."""
    keep = step3.distinct_photos(crops)
    return [crops[i] for i in keep], len(crops) - len(keep)


def coat_vote(crops):
    """Step 2 on every crop, then a majority vote. A tie between a real coat and "unsure" goes to the coat."""
    if not crops:
        return UNSURE
    votes = Counter(classify_coat(c)["coat"] for c in crops)
    best = max(votes.values())
    winners = [coat for coat, n in votes.items() if n == best]
    real = [coat for coat in winners if coat != UNSURE]
    return real[0] if real else winners[0]


def identify(crops):
    """Step 3: who is it? Returns step3.identify()'s result plus "top3_cats": a CatInfo for each of
    the closest cats (same order as "top3_ids" and "top3_scores")."""
    db = step3.load_db()
    result = step3.identify(list(crops), db)
    cats = _read_cats()
    result["top3_cats"] = [_cat_info(cid, cats[cid]) for cid in result.get("top3_ids", []) if cid in cats]
    return result


def rgb(crop):
    """OpenCV crops are BGR (blue first); web pages want RGB."""
    return np.ascontiguousarray(crop[:, :, ::-1])


# =============================================================================
# 2. Catching
# =============================================================================
def clean_name(name):
    """Tidy a name typed by the user: no extra spaces, not too long, never empty."""
    return (name or "").strip()[:MAX_NAME].strip() or DEFAULT_NAME


def catch_new(crops, fps, name, coat):
    """Add a brand-new cat (Step 3), then give it a vibe, personality and card (Step 4).
    Returns the new cat's id. Raises ValueError (with a message you can show) if there aren't
    MIN_ENROL_PHOTOS different photos."""
    crops = list(crops)
    name = safety.check_name(clean_name(name), where="catch")     # raises safety.BlockedName if not allowed
    cat_id = step3.add_new_cat(crops, name, coat, fps)
    step4.welcome_new_cat(cat_id, crops)
    return cat_id


def record_sighting(cat_id, crops, fps):
    """A known cat was seen again. Returns {"added_photos": new photos stored, "sightings": new total}."""
    added = step3.add_sighting(cat_id, list(crops), fps)
    ensure_card(cat_id)                                # old cats might not have a card yet
    cat = _read_cats().get(cat_id, {})
    return {"added_photos": added, "sightings": int(cat.get("sightings", 1))}


def ensure_card(cat_id):
    """The cat's card path - drawn now if the file is missing. None if it can't be drawn (no photos)."""
    cat = _read_cats().get(cat_id)
    if cat is None:
        return None
    path = _card_file(cat_id, cat)
    return path if path else _draw_card(cat_id)


# =============================================================================
# 3. Reading the Catdex
# =============================================================================
def _read_cats():
    """All cats from cats.json as {cat_id: {...}} - WITHOUT the fingerprints, so it's quick and
    still works if a fingerprint file has gone missing. Empty Catdex -> {}."""
    info_file = step3.DB / "cats.json"
    if not info_file.exists():
        return {}
    return json.loads(info_file.read_text(encoding="utf-8")).get("cats", {})


def _card_file(cat_id, cat):
    """The card's full path if the file really exists, otherwise None."""
    candidates = []
    if cat.get("card"):
        candidates.append(step3.DB / cat["card"].replace("\\", "/"))   # saved on Windows -> backslashes
    candidates.append(step4.CARDS / f"{cat_id}.png")
    for path in candidates:
        if path.exists():
            return str(path.resolve())
    return None


def _photo_files(cat_id, cat):
    """Full paths of the cat's stored photos that really exist."""
    folder = step3.DB / "photos" / cat_id
    return [str((folder / p).resolve()) for p in cat.get("photos", []) if (folder / p).exists()]


def _rarity(coat):
    return step4.RARITY.get(coat or "", "Common")


def _cat_info(cat_id, cat):
    """The short summary of one cat that every page uses (see "CatInfo" at the top)."""
    rarity = _rarity(cat.get("coat"))
    photos = _photo_files(cat_id, cat)
    first_seen = cat.get("first_seen") or ""
    return {
        "id": cat_id,
        "name": cat.get("name") or DEFAULT_NAME,
        "coat": cat.get("coat"),
        "rarity": rarity,
        "stars": step4.RARITY_STARS.get(rarity, 1),
        "sightings": int(cat.get("sightings", 1)),
        "photo_count": len(cat.get("photos", [])),
        "first_seen": first_seen,
        "last_seen": cat.get("last_seen") or first_seen,
        "title": (cat.get("personality") or {}).get("title", ""),
        "card_path": _card_file(cat_id, cat),
        "thumb_path": photos[0] if photos else None,
        "ear_tipped": cat.get("ear_tipped") if cat.get("ear_tipped") in EAR_TIPPED else "unsure",
    }


def _sorted(infos, sort):
    """Sort CatInfo dicts: "newest" (most recently caught first), "most_seen" or "az" (by name)."""
    if sort == "most_seen":
        return sorted(infos, key=lambda c: (-c["sightings"], c["name"].lower(), c["id"]))
    if sort == "az":
        return sorted(infos, key=lambda c: (c["name"].lower(), c["id"]))
    return sorted(infos, key=lambda c: (c["first_seen"], c["id"]), reverse=True)     # "newest"


def get_cat(cat_id):
    """One cat's CatInfo, or None if there's no such cat."""
    cat = _read_cats().get(cat_id)
    return _cat_info(cat_id, cat) if cat else None


def list_cats(search="", sort="newest", needs_tnr=False):
    """Every cat (optionally only names containing `search`, ignoring upper/lower case), sorted.
    needs_tnr=True: only cats NOT recorded as ear-tipped (the ones caretakers may still need to sterilise)."""
    search = (search or "").strip().lower()
    infos = [_cat_info(cid, cat) for cid, cat in _read_cats().items()]
    if search:
        infos = [c for c in infos if search in c["name"].lower()]
    if needs_tnr:
        infos = [c for c in infos if c["ear_tipped"] != "yes"]
    return _sorted(infos, sort)


def _page_of(coat):
    """Which collection page a coat belongs on (cats with no coat or an unknown one -> "unsure")."""
    return coat if coat in COAT_NAMES else UNSURE


def collection(search="", sort="newest", needs_tnr=False):
    """The Catdex by coat page: [(coat, [CatInfo, ...]), ...] for every page in COAT_NAMES order
    (empty pages included), plus a final ("unsure", [...]) page only if some cats have no clear coat."""
    cats = list_cats(search, sort, needs_tnr)
    pages = [(coat, [c for c in cats if c["coat"] == coat]) for coat in COAT_NAMES]
    any_unsure = any(_page_of(cat.get("coat")) == UNSURE for cat in _read_cats().values())
    if any_unsure:
        pages.append((UNSURE, [c for c in cats if _page_of(c["coat"]) == UNSURE]))
    return pages


def progress():
    """Numbers for the progress header: cats, pages filled (of 9), sightings, photos."""
    cats = _read_cats().values()
    filled = {cat.get("coat") for cat in cats} & set(COAT_NAMES)
    return {"cats": len(cats),
            "pages_filled": len(filled),
            "pages_total": len(COAT_NAMES),
            "sightings": sum(int(cat.get("sightings", 1)) for cat in cats),
            "photos": sum(len(cat.get("photos", [])) for cat in cats)}


def cat_of_the_day(today=None):
    """Pick one cat from today's date, so it's the same cat all day (and different tomorrow).
    `today` can be a date or a "2026-10-17" string; leave it out to use the real date.
    Returns a CatInfo, or None if the Catdex is empty."""
    cats = _read_cats()
    if not cats:
        return None
    day = today.isoformat() if isinstance(today, (date, datetime)) else str(today or date.today().isoformat())
    number = int(hashlib.sha256(day.encode("utf-8")).hexdigest(), 16)   # same date -> same big number
    ids = sorted(cats)
    chosen = ids[number % len(ids)]
    return _cat_info(chosen, cats[chosen])


def recent_cats(n=6):
    """The n cats seen most recently (newest first)."""
    infos = [_cat_info(cid, cat) for cid, cat in _read_cats().items()]
    return sorted(infos, key=lambda c: (c["last_seen"], c["id"]), reverse=True)[:n]


def friendship(sightings):
    """How close you are to this cat: {"value", "target", "label", "percent"}."""
    label = next(text for needed, text in FRIEND_LEVELS if sightings >= needed)
    return {"value": sightings, "target": FRIEND_TARGET, "label": label,
            "percent": min(100, round(100 * sightings / FRIEND_TARGET))}


def _sighting_dates(cat):
    """Every sighting's date and time. Old cats (before sighting_dates existed): first + last seen."""
    if cat.get("sighting_dates"):
        return list(cat["sighting_dates"])
    dates = [cat.get("first_seen") or ""]
    if int(cat.get("sightings", 1)) > 1 and cat.get("last_seen") and cat.get("last_seen") != dates[0]:
        dates.append(cat["last_seen"])
    return [d for d in dates if d]


def profile(cat_id):
    """Everything for the cat's profile page: its CatInfo plus personality, vibe, photo album,
    sighting history and friendship meter. None if there's no such cat."""
    cat = _read_cats().get(cat_id)
    if cat is None:
        return None
    info = _cat_info(cat_id, cat)
    p = cat.get("personality") or {}
    info["personality"] = {
        "title": p.get("title", ""),
        "traits": list(p.get("traits", [])),
        "stats": dict(p.get("stats", {})),
        "lore": p.get("lore", ""),
        "favourite_thing": p.get("favourite_thing", ""),
        "source": p.get("source", ""),
    }
    info["written_by"] = ("" if not p else "the built-in template" if p.get("source") == "template"
                          else "Claude")
    info["vibe"] = cat.get("vibe", "")
    info["photo_paths"] = _photo_files(cat_id, cat)
    info["sighting_dates"] = _sighting_dates(cat)
    info["friendship"] = friendship(info["sightings"])
    info["notes"] = list(cat.get("notes", []))       # caretaker notes, oldest first: [{"date", "text"}]
    return info


def nice_date(iso, with_time=False):
    """'2026-10-17T14:05:00' -> '17 Oct 2026' (or '17 Oct 2026, 2:05 pm'). Bad input -> returned as it is."""
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    text = f"{when.day} {when:%b %Y}"
    if with_time:
        text += f", {when.hour % 12 or 12}:{when:%M} {'am' if when.hour < 12 else 'pm'}"
    return text


# =============================================================================
# 4. Badges & stats
# =============================================================================
# Coat page badges: (badge name, emoji)
COAT_BADGES = {
    "colourpoint":   ("Pointy Pal", "🎭"),
    "calico":        ("Patchwork Pal", "🧩"),
    "tortoiseshell": ("Tortie Treasure", "🐢"),
    "ginger":        ("Ginger Snap", "🧡"),
    "tuxedo":        ("Dapper Tux", "🎩"),
    "white":         ("Snowball", "☁️"),
    "tabby":         ("Stripe Spotter", "🐯"),
    "black":         ("Midnight Friend", "🌙"),
    "grey":          ("Grey Cloud", "🐭"),
}
# Milestones: (badge id, name, emoji, how many cats)
CAT_MILESTONES = [("first_cat", "First Catch", "🐾", 1),
                  ("5_cats", "Cat Collector", "🧺", 5),
                  ("10_cats", "Cat Whisperer", "✨", 10),
                  ("25_cats", "Catdex Legend", "👑", 25)]
RARE_COATS = ["calico", "tortoiseshell", "colourpoint"]


def _badge(badge_id, name, emoji, group, hint, unlocked, detail="", progress_text=""):
    return {"id": badge_id, "name": name, "emoji": emoji, "group": group, "hint": hint,
            "unlocked": bool(unlocked), "detail": detail if unlocked else "", "progress": progress_text}


def badges():
    """Every badge, locked or not, worked out from the database right now.
    Each: {"id", "name", "emoji", "group" ("milestone"/"coat"/"special"), "hint", "unlocked", "detail", "progress"}"""
    cats = _read_cats()
    in_order = sorted(cats.items(), key=lambda item: (item[1].get("first_seen") or "", item[0]))  # oldest first
    n = len(in_order)
    result = []

    # Milestones: 1st, 5th, 10th, 25th cat - the detail says which cat it was
    for badge_id, name, emoji, needed in CAT_MILESTONES:
        detail = f"{in_order[needed - 1][1].get('name')} was cat #{needed}" if n >= needed else ""
        hint = "Catch your first cat" if needed == 1 else f"Catch {needed} different cats"
        result.append(_badge(badge_id, name, emoji, "milestone", hint, n >= needed, detail,
                             f"{min(n, needed)} of {needed}"))
    best = [cat for _, cat in in_order if int(cat.get("sightings", 1)) >= FRIEND_TARGET]
    most = max((int(cat.get("sightings", 1)) for cat in cats.values()), default=0)
    result.append(_badge("best_friends", "Best Friends", "💞", "milestone",
                         f"See the same cat {FRIEND_TARGET} times", best,
                         " & ".join(cat.get("name", "") for cat in best[:3]) + (" - best friends!" if best else ""),
                         f"{min(most, FRIEND_TARGET)} of {FRIEND_TARGET}"))

    # Coat pages: the first cat caught on each page
    first_of = {}
    for _, cat in in_order:
        first_of.setdefault(cat.get("coat"), cat)
    for coat in COAT_NAMES:
        name, emoji = COAT_BADGES.get(coat, (coat.title(), "🐱"))
        cat = first_of.get(coat)
        result.append(_badge(f"coat_{coat}", name, emoji, "coat", f"Catch a {coat} cat", cat,
                             f"First: {cat.get('name')}" if cat else ""))

    # Specials
    have = [coat for coat in RARE_COATS if coat in first_of]
    result.append(_badge("rare_hunter", "Rare Hunter", "💎", "special",
                         "Catch a calico, a tortoiseshell and a colourpoint", len(have) == len(RARE_COATS),
                         " + ".join(first_of[c].get("name", "") for c in RARE_COATS if c in first_of),
                         f"{len(have)} of {len(RARE_COATS)}"))
    filled = [coat for coat in COAT_NAMES if coat in first_of]
    result.append(_badge("complete", "Complete Catdex", "🏆", "special",
                         f"Fill all {len(COAT_NAMES)} coat pages", len(filled) == len(COAT_NAMES),
                         "Every coat page filled!", f"{len(filled)} of {len(COAT_NAMES)}"))
    return result


def unlocked_ids():
    """The ids of every unlocked badge. Compare before and after a catch to spot new ones."""
    return {b["id"] for b in badges() if b["unlocked"]}


def stats():
    """Numbers for the stats dashboard."""
    cats = _read_cats()
    infos = [_cat_info(cid, cat) for cid, cat in cats.items()]
    oldest_first = sorted(infos, key=lambda c: (c["first_seen"], c["id"]))
    # max() keeps the FIRST of equal values, so ties go to the cat caught earliest
    most_sighted = max(oldest_first, key=lambda c: c["sightings"], default=None)
    rarest = max(oldest_first, key=lambda c: c["stars"], default=None)
    counts = Counter(cat.get("coat") for cat in cats.values())
    return {"total_cats": len(infos),
            "total_sightings": sum(c["sightings"] for c in infos),
            "total_photos": sum(c["photo_count"] for c in infos),
            "most_sighted": most_sighted,
            "rarest": rarest,
            "coat_counts": {coat: counts.get(coat, 0) for coat in COAT_NAMES},
            "unsure_count": sum(1 for cat in cats.values() if _page_of(cat.get("coat")) == UNSURE),
            "ear_tipped_count": sum(1 for c in infos if c["ear_tipped"] == "yes"),
            "safety": safety.summary()}


# =============================================================================
# 5. Managing
# =============================================================================
def _draw_card(cat_id, db=None):
    """(Re)draw the card from the cat's stored photos. Returns the path, or None if it has no photos."""
    try:
        return str(Path(step4.card_for_existing_cat(cat_id, db)).resolve())
    except SystemExit:                                 # Step 4 stops like this when there are no photos
        print(f"  (Can't draw a card for {cat_id}: no photos found)")
        return None


def rename(cat_id, new_name):
    """Give a cat a new name and redraw its card. The personality is kept - only the old name
    inside its text (e.g. the lore) is swapped for the new one. Returns the card path (or None)."""
    safety.check_name(clean_name(new_name), where="rename")    # raises safety.BlockedName if not allowed
    db = step3.load_db()
    old = db["cats"][cat_id].get("name") or ""
    new = step3.rename_cat(cat_id, new_name, db)       # saves the new name
    p = db["cats"][cat_id].get("personality")
    if p and old and old != new:
        swap = lambda text: re.sub(rf"\b{re.escape(old)}\b", new, text) if isinstance(text, str) else text
        for key in ("title", "lore", "favourite_thing"):
            p[key] = swap(p.get(key, ""))
        p["traits"] = [swap(t) for t in p.get("traits", [])]
        step3.save_db(db)
    return _draw_card(cat_id, db)


def _new_template_personality(cat, old):
    """The template personality is the same every time for the same cat (that's on purpose in
    Step 4). For "remake card" without Claude, roll the dice again so the card really changes."""
    name, coat, vibe = cat["name"], cat.get("coat") or "mystery", cat.get("vibe", "Curious Explorer")
    titles, traits, favourite, base = step4.TEMPLATES.get(vibe, step4.TEMPLATES["Curious Explorer"])
    rng = random.Random()                              # no fixed seed: different every time
    for _ in range(20):
        title = rng.choice(titles)
        p = step4._tidy({
            "title": title,
            "traits": rng.sample(traits, 3),
            "stats": {s: base[s] + rng.randint(-8, 8) for s in step4.STAT_NAMES},
            "lore": f"{name} is a {coat} cat with {vibe.lower()} energy, best known as {title.lower()}. "
                    f"Rumour has it {name} can't resist {favourite}.",
            "favourite_thing": favourite,
            "source": "template",
        })
        if p["title"] != old.get("title") or p["traits"] != old.get("traits"):
            break
    return p


def remake_card(cat_id):
    """Write a brand-new personality (Claude if there's an API key, otherwise the template) and
    redraw the card - like  python catdex_step4.py --card <name> --redo. Returns the card path (or None)."""
    db = step3.load_db()
    cat = db["cats"][cat_id]
    old = cat.pop("personality", None) or {}
    cat.pop("card", None)
    old_card = step4.CARDS / f"{cat_id}.png"
    old_card.unlink(missing_ok=True)
    step3.save_db(db)

    photos = [step3.DB / "photos" / cat_id / p for p in cat.get("photos", [])]
    photos = [p for p in photos if p.exists()]
    if photos:
        crop = cv2.imread(str(photos[0]))
        if "vibe" not in cat:
            cat["vibe"], _ = step4.read_vibe([crop])
        p = step4.make_personality(crop, cat["name"], cat.get("coat") or "mystery", cat["vibe"])
        if p.get("source") == "template" and old:
            p = _new_template_personality(cat, old)
        cat["personality"] = p
        step3.save_db(db)
    return _draw_card(cat_id, db)


def remove_photos(cat_id, photo_names):
    """Take some photos (and their fingerprints) out of one cat - e.g. a different cat got mixed in.
    Redraws the card if its photo was one of them. Returns step3.remove_photos()'s summary.
    Raises ValueError if that would leave the cat with no photos."""
    result = step3.remove_photos(cat_id, photo_names)
    if result["first_photo_changed"]:
        _draw_card(cat_id)                             # same personality, new photo
    return result


def move_photos_to_new_cat(cat_id, photo_names, new_name):
    """Photos of a DIFFERENT cat that were merged in by mistake become a cat of their own:
    a new Catdex entry (coat page, fingerprints, personality, card) made from those photos, which
    are then taken out of the old cat. Needs MIN_ENROL_PHOTOS different photos, like any new cat.
    Returns the new cat's id. Raises ValueError if there aren't enough different photos."""
    new_name = safety.check_name(clean_name(new_name), where="new cat from photos")
    cat = step3.load_db()["cats"][cat_id]
    wanted = set(photo_names)
    names = [p for p in cat["photos"] if p in wanted]
    crops = [cv2.imread(str(step3.DB / "photos" / cat_id / n)) for n in names]
    crops = [c for c in crops if c is not None]
    if len(step3.distinct_photos(crops)) < MIN_ENROL_PHOTOS:
        raise ValueError(f"A new cat needs at least {MIN_ENROL_PHOTOS} different photos "
                         f"({len(crops)} selected). You can remove them instead.")
    if len(names) >= len(cat["photos"]):
        raise ValueError("Leave at least one photo with the old cat (or rename the cat instead).")
    new_id = step3.add_new_cat(crops, clean_name(new_name), coat_vote(crops))   # Steps 2 + 3
    remove_photos(cat_id, names)                                                # take them out of the old cat
    step4.welcome_new_cat(new_id, crops)                                        # Step 4: vibe, personality, card
    return new_id


# ---- Caretaker records (typed by people, never guessed by the AI) -------------------
def set_ear_tipped(cat_id, value):
    """Record whether the cat is ear-tipped (sterilised): "yes", "no" or "unsure"."""
    if value not in EAR_TIPPED:
        raise ValueError(f"ear_tipped must be one of {list(EAR_TIPPED)}")
    db = step3.load_db()
    db["cats"][cat_id]["ear_tipped"] = value
    step3.save_db(db)


def add_note(cat_id, text):
    """Add a dated caretaker note (e.g. "limping on the left paw - told the vet"). Returns the cleaned
    text, or "" if the note was empty. Notes are shown as plain text, so typed HTML can't do anything."""
    text = " ".join((text or "").split())[:MAX_NOTE]
    if not text:
        return ""
    db = step3.load_db()
    db["cats"][cat_id].setdefault("notes", []).append(
        {"date": datetime.now().isoformat(timespec="seconds"), "text": text})
    step3.save_db(db)
    return text


def remove_note(cat_id, index):
    """Delete one caretaker note (index = its position, oldest first). Returns True if one was removed."""
    db = step3.load_db()
    notes = db["cats"][cat_id].get("notes", [])
    if not 0 <= index < len(notes):
        return False
    notes.pop(index)
    step3.save_db(db)
    return True


def remove(cat_id):
    """Delete one cat (its entry, fingerprints, photos and card). Returns its name."""
    return step3.remove_cat(cat_id)


def reset():
    """Empty the Catdex. The old one is MOVED to a backup folder (not deleted).
    Returns the backup folder's path, or None if there was nothing to reset."""
    if not _read_cats():                               # already empty: no need for an (empty) backup
        step3.DB.mkdir(parents=True, exist_ok=True)
        return None
    backup = step3.reset_db()
    step3.DB.mkdir(parents=True, exist_ok=True)        # the app shows files from here, so keep it existing
    return str(backup) if backup else None