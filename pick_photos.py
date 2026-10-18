"""
Pick 100 random photos from a downloaded dataset folder and save them as
cat/cat_001.jpg, cat/cat_002.jpg, ... cat/cat_100.jpg

Your original dataset is NOT changed: the photos are copied, not moved.
Works with .jpg, .jpeg, .png, .webp and .bmp (any capitals), in any subfolders.
Non-JPG photos are converted to JPG so they match cat/*.jpg.
"""

import random
from pathlib import Path   # pathlib: a tidy way to work with folders and file names
import cv2                 # OpenCV: opens images and saves them as .jpg
import csv

# ---- Settings you change ----------------------------------------------------
SOURCE_FOLDER = "downloaded_dataset"   # the folder you unzipped the Kaggle dataset into
OUTPUT_FOLDER = "cats_only"                  # where the 100 renamed photos go
HOW_MANY = 200                         # how many photos to pick
NAME_PREFIX = "cat"                    # files become cat_001.jpg, cat_002.jpg, ...
RANDOM_SEED = 42                       # same seed = same 100 photos every time you run it
NAME_MUST_CONTAIN = ""                 # e.g. "cat" to keep only files like cat.123.jpg (skips dog.123.jpg)
                                       # leave as "" to keep every photo

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# choose photos by the folder they are in
FOLDER_NAME = "Cat"

# Folder names are treated as being next to THIS script, wherever the terminal happens to be
SCRIPT_DIR = Path(__file__).resolve().parent
source = SCRIPT_DIR / SOURCE_FOLDER
print(f"Looking for photos in: {source}")
if not source.is_dir():
    raise SystemExit(f"That folder doesn't exist. Check SOURCE_FOLDER (currently '{SOURCE_FOLDER}').")
 
 
def wanted(p):
    """Decide whether one file should be included."""
    if p.suffix.lower() not in IMAGE_TYPES:                     # not a photo (e.g. a .csv or .txt)
        return False
    if FOLDER_NAME and p.parent.name.lower() != FOLDER_NAME.lower():
        return False                                           # p.parent.name = the folder the photo sits in
    if NAME_MUST_CONTAIN and NAME_MUST_CONTAIN.lower() not in p.name.lower():
        return False
    return True
 
 
# 1. Find every wanted photo inside the source folder, including subfolders
all_photos = [p for p in source.rglob("*") if wanted(p)]       # rglob("*") = every file, at any depth
print(f"Found {len(all_photos)} matching photos")
if len(all_photos) == 0:
    raise SystemExit("No photos found, so nothing was saved. "
                     "Check SOURCE_FOLDER and FOLDER_NAME match your folder names exactly.")
 
# 2. Shuffle them so you get a random mix, not just the first 100
all_photos.sort()                       # sort first so the shuffle is repeatable on any computer
random.seed(RANDOM_SEED)
random.shuffle(all_photos)
 
# 3. Make the output folder (no error if it already exists)
out = SCRIPT_DIR / OUTPUT_FOLDER
out.mkdir(exist_ok=True)
 
# 4. Copy photos across with new names, keeping a record of where each came from
saved = 0
with open(out / "where_from.csv", "w", newline="") as f:
    record = csv.writer(f)
    record.writerow(["new_name", "original_file"])            # column headings
    for photo in all_photos:
        if saved == HOW_MANY:
            break                                              # stop once we have enough
        image = cv2.imread(str(photo))                         # open the photo
        if image is None:
            print(f"  Skipped (couldn't open): {photo.name}")  # broken files get skipped
            continue
        saved += 1
        new_name = out / f"{NAME_PREFIX}_{saved:03d}.jpg"      # :03d pads the number -> 001, 002 ... 100
        cv2.imwrite(str(new_name), image)                      # save as .jpg
        record.writerow([new_name.name, str(photo.relative_to(source))])
 
print(f"Saved {saved} photos into: {out}")
print(f"Record of original file names: {out / 'where_from.csv'}")
if saved < HOW_MANY:
    print(f"  Note: only {saved} usable photos were available.")