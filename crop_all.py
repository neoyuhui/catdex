"""
Crop every photo in a folder using Step 1 (find_cat), and save the crops.

  cat/cat_001.jpg  ->  cat_crops/cat_001.jpg   (just the cat, background trimmed away)

Photos that fail a check (no cat, a dog, two cats, too blurry) are NOT cropped;
they are listed at the end so you can look at them.
Needs catdex_step1.py in the same folder as this script.
"""
from pathlib import Path
import cv2
from catdex_step1 import find_cat   # reuse the Step 1 function instead of rewriting it

# ---- Settings you change ----------------------------------------------------
INPUT_FOLDER = "cats_only"          # folder of photos to crop
OUTPUT_FOLDER = "cat_crops"   # where the cropped cats are saved

SCRIPT_DIR = Path(__file__).resolve().parent         # use folders next to this script
inp = SCRIPT_DIR / INPUT_FOLDER
out = SCRIPT_DIR / OUTPUT_FOLDER
out.mkdir(exist_ok=True)

for old in out.glob("*.jpg"):    # every crop left over from a previous run
    old.unlink()                 # delete it, so the folder only holds this run's results

photos = sorted(inp.glob("*.jpg"))                   # every .jpg in the input folder, in order
print(f"Found {len(photos)} photos in {inp}")

cropped = 0
skipped = []
for photo in photos:
    result = find_cat(str(photo))                    # run all the Step 1 checks
    if result["ok"]:
        cv2.imwrite(str(out / photo.name), result["crop"])   # save the crop with the SAME file name
        cropped += 1
    else:
        skipped.append((photo.name, result["message"]))      # remember why it was skipped

print(f"Cropped {cropped} photos into {out}")
if skipped:
    print(f"Skipped {len(skipped)}:")
    for name, why in skipped:
        print(f"  {name}: {why}")

