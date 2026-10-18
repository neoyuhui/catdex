"""
Catdex - Step 3: is this a cat we've caught before, or a new one?

How it works:
  1. A pet-identification model turns a cat crop into a "fingerprint": 512 numbers that describe
     what makes THIS cat look like itself (face, markings), rather than just "a cat".
  2. We compare the new photo(s) with every stored photo of every cat already in your Catdex.
     Similarity runs from about 0 (unrelated) to 1 (identical photo).
     With several new photos, each cat's score is the AVERAGE over them - more reliable than one photo.
  3. Decision:
       similarity >= MATCH_THRESHOLD  -> "known":  "Already caught: Biscuit!"
       ASK_THRESHOLD..MATCH_THRESHOLD -> "ask":    "Is this Biscuit?" (the user decides)
       below ASK_THRESHOLD            -> "new":    a new cat for the Catdex
  4. A NEW cat needs at least MIN_ENROL_PHOTOS genuinely different photos (near-duplicates, like
     burst shots, don't count). Known cats can be sighted with any number of photos.
  5. Everything is saved in the catdex_db folder (photos + fingerprints + a cats.json file).
     Each cat also keeps "sighting_dates": the date and time of every sighting (for the app's timeline).

Pick the two thresholds with evaluate_recognition.py - the values below are only starting guesses.

Install once:  pip install transformers torch pillow
The model (~600 MB) downloads automatically the first time.

Try it:
  python catdex_step3.py photo1.jpg photo2.jpg photo3.jpg   -> runs Steps 1-3 on the photos, then asks you what to do
  python catdex_step3.py biscuit_photos                     -> same, using every photo in that folder (one cat per folder)
  python catdex_step3.py --list                             -> shows every cat in your Catdex
  python catdex_step3.py --remove cat_0002                  -> deletes one cat (by id or by name)
  python catdex_step3.py --reset                            -> empties the Catdex (old one kept as a backup)
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

# ---- Settings you can tune -------------------------------------------------
MODEL_NAME = "AvitoTech/CLIP-ViT-base-for-animal-identification"   # trained to tell individual pets apart
PROCESSOR_NAME = "openai/clip-vit-base-patch32"                     # how photos are prepared for it
MATCH_THRESHOLD = 0.920   # at or above: confident it's the same cat   (tune with evaluate_recognition.py)
ASK_THRESHOLD = 0.840     # between the two: ask the user "Is this ...?"
MIN_ENROL_PHOTOS = 3     # a new cat needs at least this many DIFFERENT photos
DUPLICATE_LIMIT = 12     # two photos closer than this (average pixel difference, 0-255, on a tiny grey
                         # copy) count as the same shot. Raise it to demand more different photos.
DB_FOLDER = "catdex_db"

# The folder this script is in (or the current folder, if the code is pasted straight into a notebook cell)
HERE = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DB = HERE / DB_FOLDER

model = CLIPModel.from_pretrained(MODEL_NAME)
processor = CLIPProcessor.from_pretrained(PROCESSOR_NAME)
model.eval()


# ---- Photos: loading and spotting near-duplicates ---------------------------
def _to_bgr(image):
    """Accept a file path or an OpenCV image; return an OpenCV image."""
    return cv2.imread(str(image)) if isinstance(image, (str, Path)) else image


def _thumbnail(image):
    """A tiny 32x32 grey copy - enough to tell whether two photos are basically the same shot."""
    img = _to_bgr(image)
    if img is None:
        return None
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(grey, (32, 32), interpolation=cv2.INTER_AREA).astype(float)


def near_duplicate(a, b):
    """True if two photos (paths or images) are almost the same shot, e.g. burst photos.
    This looks at the pixels only - NOT the identification model - so it's a fair, independent check."""
    ta, tb = _thumbnail(a), _thumbnail(b)
    if ta is None or tb is None:
        return False
    return np.abs(ta - tb).mean() < DUPLICATE_LIMIT


def distinct_photos(images, compare_with=()):
    """Keep only photos that aren't near-duplicates of each other (or of any photo in compare_with).
    Returns the positions of the photos that were kept."""
    kept = []
    for i, img in enumerate(images):
        if any(near_duplicate(img, images[k]) for k in kept):
            continue
        if any(near_duplicate(img, old) for old in compare_with):
            continue
        kept.append(i)
    return kept


# ---- Fingerprints -----------------------------------------------------------
def fingerprint(image):
    """512 numbers describing this individual cat, scaled to length 1 (so comparisons are fair)."""
    img = _to_bgr(image)
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))       # OpenCV is BGR, PIL is RGB
    with torch.no_grad():
        inputs = processor(images=pil, return_tensors="pt")
        out = model.get_image_features(**inputs)
        vec = out if torch.is_tensor(out) else out.pooler_output     # handles old and new transformers
        vec = vec[0] / vec[0].norm()
    return vec.numpy().astype(np.float32)


# ---- The Catdex database ----------------------------------------------------
def load_db():
    """Returns {"cats": {cat_id: {...}}}. Each cat's fingerprints are loaded as 'fingerprints'."""
    info_file = DB / "cats.json"
    if not info_file.exists():
        return {"cats": {}}
    db = json.loads(info_file.read_text(encoding="utf-8"))
    for cat_id, cat in db["cats"].items():
        cat["fingerprints"] = np.load(DB / "fingerprints" / f"{cat_id}.npy")
    return db


def save_db(db):
    (DB / "fingerprints").mkdir(parents=True, exist_ok=True)
    plain = {"cats": {}}
    for cat_id, cat in db["cats"].items():
        np.save(DB / "fingerprints" / f"{cat_id}.npy", cat["fingerprints"])
        plain["cats"][cat_id] = {k: v for k, v in cat.items() if k != "fingerprints"}   # JSON can't hold arrays
    (DB / "cats.json").write_text(json.dumps(plain, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_photo(cat_id, crop):
    folder = DB / "photos" / cat_id
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    cv2.imwrite(str(folder / name), crop)
    return name


def identify(crops, db=None):
    """
    Compare one or more crops of the SAME cat with every cat in the Catdex.
    Each cat's score = average over the crops of (similarity to that cat's closest stored photo).
    Returns {"decision": "known"/"ask"/"new", "cat_id", "name", "similarity", "fingerprints", "top3",
             "top3_ids", "top3_scores"}
      top3        = [(name, score), ...]  the 3 closest cats (printed by the command line)
      top3_ids    = [cat_id, ...]         the same 3 cats' ids, in the same order (the app needs ids,
                                          because two cats can have the same name)
      top3_scores = [score, ...]          the same 3 cats' similarity scores
    """
    if not isinstance(crops, list):
        crops = [crops]
    db = db or load_db()
    fps = [fingerprint(c) for c in crops]
    ranking = []
    for cat_id, cat in db["cats"].items():
        per_photo_best = [float(np.max(cat["fingerprints"] @ fp)) for fp in fps]   # closest stored photo
        ranking.append((float(np.mean(per_photo_best)), cat_id, cat["name"]))
    ranking.sort(reverse=True)

    if not ranking:
        return {"decision": "new", "cat_id": None, "name": None, "similarity": 0.0, "fingerprints": fps,
                "top3": [], "top3_ids": [], "top3_scores": []}
    best_sim, best_id, best_name = ranking[0]
    if best_sim >= MATCH_THRESHOLD:
        decision = "known"
    elif best_sim >= ASK_THRESHOLD:
        decision = "ask"
    else:
        decision = "new"
    return {"decision": decision, "cat_id": best_id, "name": best_name, "similarity": best_sim,
            "fingerprints": fps, "top3": [(name, round(s, 3)) for s, _, name in ranking[:3]],
            "top3_ids": [cat_id for _, cat_id, _ in ranking[:3]],
            "top3_scores": [round(s, 3) for s, _, _ in ranking[:3]]}


def add_new_cat(crops, name, coat=None, fps=None, db=None):
    """
    Enrol a brand-new cat from at least MIN_ENROL_PHOTOS different crops.
    Near-duplicates are dropped first. Returns the new id, e.g. 'cat_0007'.
    """
    db = db or load_db()
    fps = fps if fps is not None else [fingerprint(c) for c in crops]
    keep = distinct_photos(crops)
    if len(keep) < MIN_ENROL_PHOTOS:
        raise ValueError(f"Need at least {MIN_ENROL_PHOTOS} different photos to enrol a new cat "
                         f"(got {len(keep)} after removing near-duplicates).")
    # Next free number. (Not "number of cats + 1": after a cat is removed that could reuse an existing id.)
    used = [int(cid.split("_")[1]) for cid in db["cats"]]
    cat_id = f"cat_{max(used, default=0) + 1:04d}"
    now = datetime.now().isoformat(timespec="seconds")
    db["cats"][cat_id] = {"name": name, "coat": coat, "first_seen": now, "last_seen": now, "sightings": 1,
                          "sighting_dates": [now],                  # one entry per sighting (for the timeline)
                          "photos": [_save_photo(cat_id, crops[i]) for i in keep],
                          "fingerprints": np.stack([fps[i] for i in keep])}
    save_db(db)
    return cat_id


def add_sighting(cat_id, crops, fps=None, db=None):
    """
    A known cat was seen again. Counts one sighting, and stores any photos that are genuinely
    different from what we already have - so recognition keeps improving.
    Returns how many new photos were stored.
    """
    if not isinstance(crops, list):
        crops = [crops]
    db = db or load_db()
    fps = fps if fps is not None else [fingerprint(c) for c in crops]
    cat = db["cats"][cat_id]
    stored = [DB / "photos" / cat_id / p for p in cat["photos"]]
    keep = distinct_photos(crops, compare_with=stored)
    for i in keep:
        cat["photos"].append(_save_photo(cat_id, crops[i]))
        cat["fingerprints"] = np.vstack([cat["fingerprints"], fps[i]])
    # Cats caught before "sighting_dates" existed don't have the list yet: start it from what we know
    if "sighting_dates" not in cat:
        cat["sighting_dates"] = [cat["first_seen"]]
        if cat["sightings"] > 1 and cat["last_seen"] != cat["first_seen"]:
            cat["sighting_dates"].append(cat["last_seen"])
    now = datetime.now().isoformat(timespec="seconds")
    cat["sightings"] += 1
    cat["last_seen"] = now
    cat["sighting_dates"].append(now)
    save_db(db)
    return len(keep)


# ---- Renaming cats ------------------------------------------------------------
def rename_cat(cat_id, new_name, db=None):
    """Give a cat a new name and save it. Returns the name actually used.
    (The trading card still shows the old name until it's redrawn - catdex_data.rename() does both.)"""
    db = db or load_db()
    name = (new_name or "").strip()[:30].strip() or "Mystery Cat"   # same rules as naming a new cat
    db["cats"][cat_id]["name"] = name
    save_db(db)
    return name


# ---- Removing some photos (fixing a mix-up) ---------------------------------------
def _photo_time(name):
    """When a stored photo was saved, read from its file name (e.g. 20261018_113012_123456.jpg)."""
    try:
        return datetime.strptime(Path(name).stem[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def remove_photos(cat_id, photo_names, db=None):
    """
    Remove some photos - and their fingerprints - from one cat. Use it when photos of a DIFFERENT cat
    were merged in by mistake: their fingerprints would otherwise keep causing wrong matches.
    A sighting that brought in ONLY removed photos is undone too (the first sighting - when the cat
    was caught - always stays). At least one photo must stay; to remove everything, remove the cat.
    Returns {"removed": n, "sightings_undone": n, "first_photo_changed": True/False}.
    """
    db = db or load_db()
    cat = db["cats"][cat_id]
    names = set(photo_names) & set(cat["photos"])
    if not names:
        return {"removed": 0, "sightings_undone": 0, "first_photo_changed": False}
    if len(names) >= len(cat["photos"]):
        raise ValueError("A cat needs at least one photo. To remove all of them, remove the whole cat instead.")
    first_photo_changed = cat["photos"][0] in names         # the card is drawn from the first photo
    keep = [i for i, p in enumerate(cat["photos"]) if p not in names]

    # Undo sightings that only brought in removed photos. Each photo belongs to the sighting closest
    # in time (photos are saved a moment before their sighting is recorded).
    undone = 0
    dates = cat.get("sighting_dates")
    if dates and len(dates) > 1:
        times = [datetime.fromisoformat(d) for d in dates]

        def which_sighting(name):
            t = _photo_time(name)
            if t is None:
                return None
            j = min(range(len(times)), key=lambda k: abs((times[k] - t).total_seconds()))
            return j if abs((times[j] - t).total_seconds()) <= 120 else None

        removed_from = {which_sighting(n) for n in names} - {None, 0}
        still_used = {which_sighting(cat["photos"][i]) for i in keep}
        for j in sorted(removed_from - still_used, reverse=True):
            dates.pop(j)
            undone += 1
        cat["sightings"] = max(1, cat["sightings"] - undone)
        cat["last_seen"] = dates[-1]

    # Photos and fingerprints are stored in the same order, so keep the same rows of both
    old_photos = cat["photos"]
    cat["photos"] = [old_photos[i] for i in keep]
    if len(cat["fingerprints"]) == len(old_photos):
        cat["fingerprints"] = cat["fingerprints"][keep]
    else:                                                    # out of step (shouldn't happen): rebuild them
        cat["fingerprints"] = np.stack([fingerprint(DB / "photos" / cat_id / p) for p in cat["photos"]])
    for name in names:
        (DB / "photos" / cat_id / name).unlink(missing_ok=True)
    save_db(db)
    return {"removed": len(names), "sightings_undone": undone, "first_photo_changed": first_photo_changed}


# ---- Removing cats ------------------------------------------------------------
def find_cat_id(id_or_name, db=None):
    """Turn 'cat_0003' or a name like 'Biscuit' into a cat id. Returns a list of matching ids."""
    db = db or load_db()
    if id_or_name in db["cats"]:
        return [id_or_name]
    return [cid for cid, cat in db["cats"].items() if cat["name"].lower() == id_or_name.lower()]


def remove_cat(cat_id, db=None):
    """Delete ONE cat: its entry in cats.json, its fingerprints, its photos and its card."""
    db = db or load_db()
    cat = db["cats"].pop(cat_id)                                    # take it out of the list
    (DB / "fingerprints" / f"{cat_id}.npy").unlink(missing_ok=True)
    shutil.rmtree(DB / "photos" / cat_id, ignore_errors=True)
    (DB / "cards" / f"{cat_id}.png").unlink(missing_ok=True)
    save_db(db)
    return cat["name"]


def reset_db():
    """Start the Catdex from empty. The old database is MOVED to a backup folder, not deleted,
    so a mistake can be undone. Returns the backup folder (or None if there was nothing to reset)."""
    if not DB.exists():
        return None
    backup = DB.with_name(f"{DB.name}_backup_{datetime.now():%Y%m%d_%H%M%S}")
    number = 2
    while backup.exists():                       # two resets in the same second: add _2, _3, ...
        backup = DB.with_name(f"{DB.name}_backup_{datetime.now():%Y%m%d_%H%M%S}_{number}")
        number += 1
    DB.rename(backup)
    return backup


# ---- Try it out -------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from collections import Counter

    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        db = load_db()
        print(f"Your Catdex has {len(db['cats'])} cats:")
        for cat_id, cat in db["cats"].items():
            print(f"  {cat_id}  {cat['name']:<15} {cat['coat'] or '?':<14} seen {cat['sightings']}x, "
                  f"{len(cat['photos'])} photos, last seen {cat['last_seen']}")
        sys.exit()
    if len(sys.argv) > 2 and sys.argv[1] == "--remove":
        db = load_db()
        wanted = " ".join(sys.argv[2:])                     # so names with spaces work: --remove Mr Whiskers
        matches = find_cat_id(wanted, db)
        if not matches:
            sys.exit(f"No cat called '{wanted}'. Run  python catdex_step3.py --list  to see the ids.")
        if len(matches) > 1:
            sys.exit(f"{len(matches)} cats are called '{wanted}' ({', '.join(matches)}). Remove one by its id.")
        cat = db["cats"][matches[0]]
        answer = input(f"Remove {cat['name']} ({matches[0]}, {len(cat['photos'])} photos)? This can't be undone. [y/n]: ")
        if answer.strip().lower().startswith("y"):
            print(f"Removed {remove_cat(matches[0], db)}. Their photos, fingerprints and card are gone.")
        else:
            print("Nothing removed.")
        sys.exit()
    if len(sys.argv) == 2 and sys.argv[1] == "--reset":
        n = len(load_db()["cats"])
        answer = input(f"Empty the whole Catdex ({n} cats)? The old one is kept as a backup folder. [y/n]: ")
        if answer.strip().lower().startswith("y"):
            backup = reset_db()
            print(f"Catdex emptied. Old database moved to: {backup}" if backup else "The Catdex was already empty.")
        else:
            print("Nothing changed.")
        sys.exit()
    if len(sys.argv) < 2:
        sys.exit("Usage: python catdex_step3.py photo1.jpg [photo2.jpg ...]\n"
                 "       python catdex_step3.py --list | --remove <cat id or name> | --reset")

    from catdex_step1 import find_cat          # Step 1: find and crop the cat
    from catdex_step2 import classify_coat     # Step 2: coat pattern

    # Which photos? Each argument can be a photo OR a folder (then every photo inside it is used).
    # Everything you give in one run is treated as photos of the SAME cat.
    PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    paths = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            found = sorted(f for f in p.iterdir() if f.suffix.lower() in PHOTO_TYPES)
            print(f"  Folder {p}: {len(found)} photo(s)")
            paths += [str(f) for f in found]
        elif not p.exists():
            print(f"  {arg}: not found - check the spelling, and that the terminal is in the right folder")
        else:
            paths.append(arg)
    if not paths:
        sys.exit("No photos found. Check the folder name (it's relative to the terminal's current folder).")

    # Step 1 on every photo
    crops = []
    for path in paths:
        r = find_cat(path)
        if r["ok"]:
            crops.append(r["crop"])
        else:
            print(f"  {path}: skipped - {r['message']}")
    if not crops:
        sys.exit("No usable photos.")
    keep = distinct_photos(crops)
    if len(keep) < len(crops):
        print(f"  {len(crops) - len(keep)} photo(s) were almost the same shot as another and were dropped")
    crops = [crops[i] for i in keep]

    # Step 2: coat - majority vote across the photos
    coats = [classify_coat(c)["coat"] for c in crops]
    coat = Counter(coats).most_common(1)[0][0]

    # Step 3: who is it?
    db = load_db()
    result = identify(crops, db)
    print(f"{len(crops)} photo(s)  |  coat: {coat}  |  closest matches: {result['top3'] or 'none yet'}")

    if result["decision"] == "known":
        n = add_sighting(result["cat_id"], crops, result["fingerprints"], db)
        card = db["cats"][result["cat_id"]].get("card")
        sys.exit(f"Already caught: {result['name']}! (similarity {result['similarity']:.2f}) "
                 f"Sighting saved, {n} new photo(s) added." + (f"\nTheir card: {DB / card}" if card else ""))
    if result["decision"] == "ask":
        answer = input(f"Is this {result['name']}? (similarity {result['similarity']:.2f}) [y/n]: ")
        if answer.strip().lower().startswith("y"):
            n = add_sighting(result["cat_id"], crops, result["fingerprints"], db)
            sys.exit(f"Saved as another sighting of {result['name']}, {n} new photo(s) added.")
    if len(crops) < MIN_ENROL_PHOTOS:
        sys.exit(f"Looks like a new cat! To catch it, run again with at least {MIN_ENROL_PHOTOS} "
                 f"different photos (different angles), e.g.:\n"
                 f"  python catdex_step3.py front.jpg side.jpg face.jpg")
    name = input("New cat! What's its name? ").strip() or "Mystery Cat"
    cat_id = add_new_cat(crops, name, coat, result["fingerprints"], db)
    print(f"Caught {name} ({cat_id}) with {len(crops)} photos and added it to the {coat} page!")
    try:
        from catdex_step4 import welcome_new_cat          # Step 4: vibe, personality and trading card
    except ImportError:
        sys.exit()                                        # Step 4 not added yet - that's fine
    print(f"Trading card saved: {welcome_new_cat(cat_id, crops)}")