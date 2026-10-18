"""
Get ready to hand-label coat patterns.

Makes two things:
  1. coat_labels.csv  - a spreadsheet with one row per crop. You fill in the "coat" column.
                        Each photo is randomly put in the "dev" set (for tuning) or "test" set
                        (for your final, honest score).
  2. contact_sheets/  - grid images showing 20 crops at a time, each with its file name,
                        so you can label quickly without opening every photo.

Safe to run again: if coat_labels.csv already exists, your labels are kept, and any NEW crops
(e.g. after picking more photos) are added at the bottom with a blank coat for you to fill in.
"""
import csv
import random
from pathlib import Path
import cv2
import numpy as np

# ---- Settings ---------------------------------------------------------------
CROPS_FOLDER = "cat_crops"
LABEL_FILE = "coat_labels.csv"
SHEETS_FOLDER = "contact_sheets"
TEST_SHARE = 0.5          # half the photos go in the test set
RANDOM_SEED = 42
TILE = 220                # size of each photo in the grid (pixels)
PER_ROW, PER_SHEET = 5, 20

# Label with the Catdex coat rules, FIRST MATCH WINS (same order as in catdex_step2.py):
# colourpoint > calico > tortoiseshell > ginger > tuxedo > white > tabby > black > grey
ALLOWED = ["colourpoint", "calico", "tortoiseshell", "ginger", "tuxedo", "white",
           "tabby", "black", "grey", "other", "skip"]

here = Path(__file__).resolve().parent
crops = sorted((here / CROPS_FOLDER).glob("*.jpg"))
print(f"Found {len(crops)} crops in {here / CROPS_FOLDER}")
if not crops:
    raise SystemExit("No crops found. Run crop_all.py first.")

# 1. The label spreadsheet
label_path = here / LABEL_FILE
already_listed = set()
if label_path.exists():
    with open(label_path, newline="") as f:
        already_listed = {row["file"] for row in csv.DictReader(f)}   # files that already have a row

new_crops = [c for c in crops if c.name not in already_listed]       # only crops not in the sheet yet
random.seed(RANDOM_SEED + len(already_listed))   # different split choices for each batch of new photos
with open(label_path, "a", newline="") as f:     # "a" = append: add to the end, never erase
    writer = csv.writer(f)
    if not already_listed:
        writer.writerow(["file", "coat", "split"])                   # column headings (new file only)
    for crop in new_crops:
        split = "test" if random.random() < TEST_SHARE else "dev"
        writer.writerow([crop.name, "", split])                      # coat left blank for you to fill in

if already_listed:
    print(f"Kept your existing {len(already_listed)} rows and added {len(new_crops)} new crops to {LABEL_FILE}.")
else:
    print(f"Created {LABEL_FILE} with {len(new_crops)} crops.")
print("Fill the 'coat' column with one of:\n  " + ", ".join(ALLOWED))

# 2. Contact sheets
sheets = here / SHEETS_FOLDER
sheets.mkdir(exist_ok=True)
for start in range(0, len(crops), PER_SHEET):
    batch = crops[start:start + PER_SHEET]
    rows = (len(batch) + PER_ROW - 1) // PER_ROW
    sheet = np.full((rows * (TILE + 30), PER_ROW * TILE, 3), 255, np.uint8)   # white background
    for n, crop in enumerate(batch):
        img = cv2.imread(str(crop))
        h, w = img.shape[:2]
        scale = TILE / max(h, w)                                # shrink to fit, keeping its shape
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
        r, c = divmod(n, PER_ROW)
        y, x = r * (TILE + 30), c * TILE
        sheet[y:y + img.shape[0], x:x + img.shape[1]] = img
        cv2.putText(sheet, crop.stem, (x + 5, y + TILE + 22),   # file name under each photo
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    name = sheets / f"sheet_{start // PER_SHEET + 1:02d}.jpg"
    cv2.imwrite(str(name), sheet)
print(f"Saved contact sheets in {sheets}")