"""
How well does Step 3 recognise individual cats - and which MATCH_THRESHOLD should you use?

Needs a folder with ONE SUBFOLDER PER CAT, several photos each:
    recog_data/
        biscuit/   1.jpg 2.jpg 3.jpg ...
        mochi/     ...
(Many cat re-identification datasets on Kaggle come like this - or use your own cats' photos.)

What it does (nothing is added to your real Catdex):
  1. Splits the CATS into two halves: "dev" (to choose the threshold) and "test" (to report, once).
  2. Within a half, some cats are ENROLLED (their first few photos saved, like catching them),
     and the rest are STRANGERS (never enrolled - Catdex should call them "new").
  3. Every other photo is a "sighting". For each threshold it counts:
       correct      - an enrolled cat matched to the right cat
       wrong cat    - an enrolled cat matched to a DIFFERENT cat   (the worst mistake)
       missed       - an enrolled cat called "new"                  (annoying: a duplicate entry)
       false match  - a stranger matched to an enrolled cat         (bad: two cats merged)
       correct new  - a stranger correctly called "new"
Needs catdex_step3.py in the same folder. Install once: pip install pandas
"""
import pickle
import random
from pathlib import Path
import numpy as np
import cv2
from catdex_step3 import fingerprint, near_duplicate, MODEL_NAME, MATCH_THRESHOLD, MIN_ENROL_PHOTOS

# ---- Settings ---------------------------------------------------------------
RECOG_FOLDER = "recog_data"
SPLIT = "test"              # "dev" while choosing the threshold, "test" ONCE at the end
ENROL_PHOTOS = MIN_ENROL_PHOTOS   # photos used to enrol each cat - the same as the app (catdex_step3.py),
                                  # and they must be genuinely DIFFERENT photos, not near-duplicates
SKIP_NEAR_DUPLICATES = True  # leave out sightings that are almost the same shot as an enrolment photo
                             # (what counts as "almost the same" = DUPLICATE_LIMIT in catdex_step3.py)
STRANGER_SHARE = 0.3       # 30% of cats are never enrolled
MIN_PHOTOS = 4             # cats with fewer photos than this are left out
MAX_CATS = 100             # only use this many cats (a random sample) - plenty for a reliable result
MAX_PHOTOS_PER_CAT = 10    # and at most this many photos of each - keeps the run to a few minutes
USE_STEP1_CROP = False     # True: run Step 1 (YOLO crop) first - use for full photos of whole cats.
                           # False: use photos as they are - use for datasets that are already cropped faces.
RANDOM_SEED = 42

here = Path(__file__).resolve().parent
root = here / RECOG_FOLDER
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# 1. Find cats and photos
cats = {}
for folder in sorted(p for p in root.iterdir() if p.is_dir()):
    photos = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_TYPES)
    if len(photos) >= MIN_PHOTOS:
        cats[folder.name] = photos
print(f"Found {len(cats)} cats with at least {MIN_PHOTOS} photos in {root}")
if len(cats) < 4:
    raise SystemExit("Need at least 4 cats. Check RECOG_FOLDER has one subfolder per cat.")

# Take a random sample of cats and photos, so big datasets don't take hours
random.seed(RANDOM_SEED)
names = sorted(cats)
random.shuffle(names)
names = names[:MAX_CATS]
for name in names:
    photos = cats[name][:]
    random.shuffle(photos)
    cats[name] = sorted(photos[:MAX_PHOTOS_PER_CAT])
print(f"Using a sample of {len(names)} cats, up to {MAX_PHOTOS_PER_CAT} photos each")

# 2. Split cats into dev/test, then enrolled/strangers (cats never appear in both halves)
random.shuffle(names)
half = names[: len(names) // 2] if SPLIT == "dev" else names[len(names) // 2:]
n_strangers = max(1, round(len(half) * STRANGER_SHARE))
strangers, enrolled = half[:n_strangers], half[n_strangers:]
print(f"'{SPLIT}' half: {len(enrolled)} enrolled cats, {len(strangers)} strangers")

# 3. Fingerprints (saved in a cache file so re-runs are fast)
cache_file = here / "recognition_fingerprints.pkl"
cache = pickle.loads(cache_file.read_bytes()) if cache_file.exists() else {}
step1 = None
if USE_STEP1_CROP:
    from catdex_step1 import find_cat
    step1 = find_cat


def fp_of(photo):
    key = f"{MODEL_NAME}|{USE_STEP1_CROP}|{photo.relative_to(root).as_posix()}"
    if key not in cache:
        if step1:
            r = step1(str(photo))
            if not r["ok"]:
                return None                              # Step 1 rejected it (blurry, no cat...)
            cache[key] = fingerprint(r["crop"])
        else:
            img = cv2.imread(str(photo))
            if img is None:
                return None
            cache[key] = fingerprint(img)
    return cache[key]


gallery = []      # (cat name, fingerprint) for enrolled photos
queries = []      # (true cat name or None for stranger, fingerprint)
skipped = 0
cant_enrol = 0
for name in enrolled:
    photos = cats[name][:]
    random.shuffle(photos)
    pairs = [(p, f) for p, f in ((p, fp_of(p)) for p in photos) if f is not None]
    # Enrol with the first ENROL_PHOTOS photos that are genuinely different from each other
    enrol, rest = [], []
    for p, f in pairs:
        if len(enrol) < ENROL_PHOTOS and not any(near_duplicate(p, e) for e, _ in enrol):
            enrol.append((p, f))
        else:
            rest.append((p, f))
    if len(enrol) < ENROL_PHOTOS:
        cant_enrol += 1                                   # not enough different photos - like the app, refuse
        continue
    gallery += [(name, f) for _, f in enrol]
    for p, f in rest:
        if SKIP_NEAR_DUPLICATES and any(near_duplicate(p, e) for e, _ in enrol):
            skipped += 1                                  # almost the same shot as an enrolment photo
            continue
        queries.append((name, f))
for name in strangers:
    queries += [(None, f) for f in (fp_of(p) for p in cats[name]) if f is not None]
cache_file.write_bytes(pickle.dumps(cache))
print(f"Enrolled photos: {len(gallery)}   |   sightings to test: {len(queries)} "
      f"({sum(q[0] is not None for q in queries)} of enrolled cats, {sum(q[0] is None for q in queries)} of strangers)")
if cant_enrol:
    print(f"{cant_enrol} cat(s) couldn't be enrolled: fewer than {ENROL_PHOTOS} genuinely different photos")
if SKIP_NEAR_DUPLICATES:
    print(f"Left out {skipped} near-duplicate sightings (almost the same shot as an enrolment photo)")
if not gallery or not any(q[0] is not None for q in queries):
    raise SystemExit("No enrolled cats with sightings left to test - try lowering DUPLICATE_LIMIT in "
                     "catdex_step3.py, or use a dataset with more varied photos.")

# 4. Best match for every sighting (closest enrolled PHOTO, like catdex_step3.identify)
G = np.stack([f for _, f in gallery])
g_names = [n for n, _ in gallery]
results = []
for true_name, f in queries:
    sims = G @ f
    best = int(np.argmax(sims))
    results.append((true_name, g_names[best], float(sims[best])))

# 5. Count outcomes at a threshold
def outcomes(t):
    c = w = m = fm = cn = 0
    for true_name, guess, s in results:
        if true_name is not None:                # an enrolled cat
            if s < t:
                m += 1
            elif guess == true_name:
                c += 1
            else:
                w += 1
        else:                                    # a stranger
            if s >= t:
                fm += 1
            else:
                cn += 1
    return c, w, m, fm, cn, (c + cn) / len(results)


header = f"{'threshold':>9} | {'correct':>7} {'wrong cat':>9} {'missed':>6} | {'false match':>11} {'correct new':>11} | overall"

# How far apart are enrolled cats and strangers? (the bigger the gap, the easier the job)
known_sims = [s for t, _, s in results if t is not None]
stranger_sims = [s for t, _, s in results if t is None]
print(f"\nClosest-match similarity - enrolled cats: typical {np.median(known_sims):.3f}  |  "
      f"strangers: typical {np.median(stranger_sims):.3f}")
print("(Enrolled cats should score clearly HIGHER than strangers - the threshold goes in the gap.)")

if SPLIT == "dev":
    # Try thresholds across the range of similarities actually seen, and suggest the best one
    print("\n" + header)
    all_sims = [s for _, _, s in results]
    best_t, best_acc = None, -1
    for t in np.unique(np.round(np.linspace(min(all_sims), max(all_sims), 20), 3)):
        c, w, m, fm, cn, overall = outcomes(t)
        if overall > best_acc:
            best_t, best_acc = t, overall
        print(f"{t:>9.3f} | {c:>7} {w:>9} {m:>6} | {fm:>11} {cn:>11} | {overall:>6.0%}")
    print(f"\nBest on the dev cats: threshold {best_t:.3f} -> {best_acc:.0%} of sightings handled correctly")
    print(f"Set MATCH_THRESHOLD = {best_t:.3f} in catdex_step3.py, and ASK_THRESHOLD a little lower")
    print("(e.g. the threshold a few rows up the table) - sightings in between get 'Is this ...?'.")
    print("Prefer a slightly HIGHER threshold if 'false match' or 'wrong cat' is high: merging two cats is worse")
    print("than asking the user. When you're done, set SPLIT = 'test' and run ONCE.")
else:
    # Test: report ONLY at the threshold you already chose - no re-tuning on test cats
    c, w, m, fm, cn, overall = outcomes(MATCH_THRESHOLD)
    print("\n" + header)
    print(f"{MATCH_THRESHOLD:>9.3f} | {c:>7} {w:>9} {m:>6} | {fm:>11} {cn:>11} | {overall:>6.0%}")
    known = c + w + m
    print(f"\nTEST RESULT at MATCH_THRESHOLD {MATCH_THRESHOLD}: {overall:.0%} of sightings handled correctly")
    print(f"  Enrolled cats recognised: {c}/{known} ({c / max(known, 1):.0%})   |   "
          f"strangers correctly called new: {cn}/{fm + cn} ({cn / max(fm + cn, 1):.0%})")
    print("This is the number to report. Don't change the threshold because of it.")