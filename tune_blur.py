"""
Pick a good BLUR_THRESHOLD for YOUR photos.

For every photo it finds the cat (blur check switched off), then scores the crop two ways:
  old score = the original blur check (dark cats scored unfairly low)
  new score = the fixed blur check (contrast stretched first)
It then suggests a new threshold that keeps the SAME strictness you had before for normally lit
photos, and makes a contact sheet of the lowest-scoring crops so you can check by eye.

Needs catdex_step1.py in the same folder.
"""
from pathlib import Path
import cv2
import numpy as np
import catdex_step1
from catdex_step1 import find_cat, blur_score

# ---- Settings ---------------------------------------------------------------
INPUT_FOLDER = "cats_only"        # your photo folder (e.g. "cats_only")
OLD_THRESHOLD = 100         # the threshold you were using with the OLD blur check

here = Path(__file__).resolve().parent
catdex_step1.BLUR_THRESHOLD = 0      # switch the blur check off for this script, so we see every crop


def old_blur_score(image):
    """The original blur check, before the fix."""
    gray = cv2.cvtColor(cv2.resize(image, (300, 300)), cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


rows = []
photos = sorted((here / INPUT_FOLDER).glob("*.jpg"))
print(f"Scoring {len(photos)} photos (this runs YOLO on each, so give it a minute)...")
for p in photos:
    result = find_cat(str(p))
    if not result["ok"]:
        continue                                        # no cat / dog / two cats - not a blur question
    crop = result["crop"]
    brightness = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean()
    rows.append((p.name, old_blur_score(crop), blur_score(crop), brightness))
if not rows:
    raise SystemExit("No cats found. Check INPUT_FOLDER.")

old = np.array([r[1] for r in rows])
new = np.array([r[2] for r in rows])
bright = np.array([r[3] for r in rows])

# Suggested threshold: for normally-lit crops, how much higher is the new score than the old one?
normal = bright > 80                                    # leave out dark crops - they're the ones we fixed
ratio = np.median(new[normal] / np.maximum(old[normal], 1)) if normal.any() else np.median(new / np.maximum(old, 1))
suggested = round(OLD_THRESHOLD * ratio, -1)            # round to the nearest 10

print(f"\nCats found: {len(rows)}  (normally lit: {normal.sum()}, dark: {(~normal).sum()})")
print(f"New scores are about {ratio:.1f}x the old ones for normally lit photos.")
print(f"\n  Old check at {OLD_THRESHOLD}: {(old >= OLD_THRESHOLD).sum()} of {len(rows)} pass "
      f"(dark crops passing: {(old[~normal] >= OLD_THRESHOLD).sum()} of {(~normal).sum()})")
for t in sorted({suggested, 100, 150, 200, 250, 350}):
    tag = "   <- suggested" if t == suggested else ""
    print(f"  New check at {t:>4.0f}: {(new >= t).sum()} of {len(rows)} pass "
          f"(dark crops passing: {(new[~normal] >= t).sum()} of {(~normal).sum()}){tag}")
print(f"\nSet BLUR_THRESHOLD = {suggested:.0f} in catdex_step1.py, then check the contact sheet below.")

# Contact sheet: the 20 lowest-scoring crops, with their new score, so you can see where blur starts
lowest = sorted(rows, key=lambda r: r[2])[:20]
TILE, PER_ROW = 220, 5
sheet = np.full((((len(lowest) + PER_ROW - 1) // PER_ROW) * (TILE + 30), PER_ROW * TILE, 3), 255, np.uint8)
for k, (name, o, n, b) in enumerate(lowest):
    crop = find_cat(str(here / INPUT_FOLDER / name))["crop"]
    h, w = crop.shape[:2]
    s = TILE / max(h, w)
    crop = cv2.resize(crop, (int(w * s), int(h * s)))
    r, c = divmod(k, PER_ROW)
    y, x = r * (TILE + 30), c * TILE
    sheet[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
    cv2.putText(sheet, f"{Path(name).stem} {n:.0f}", (x + 5, y + TILE + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
out = here / "blur_check_lowest.jpg"
cv2.imwrite(str(out), sheet)
print(f"Saved {out.name}: the 20 lowest-scoring crops with their new score.")
print("Look at it: the threshold should sit between the ones that look blurry and the ones that look fine.")