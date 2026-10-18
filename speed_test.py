"""
Catdex speed test: times every step of a scan on YOUR computer, so you can see what is slow.
It does NOT change your Catdex (nothing is saved to catdex_db).

Run (from your project folder):
  python speed_test.py emili_cat              -> a folder of cat photos (3-5 is plenty)
  python speed_test.py emili_cat --claude     -> also time the Claude personality call (costs a fraction of a cent)
"""

import os
import sys
import time
from pathlib import Path


def stopwatch():
    """Returns a function that gives the seconds since the last call."""
    last = [time.perf_counter()]
    def lap():
        now = time.perf_counter()
        seconds, last[0] = now - last[0], now
        return seconds
    return lap


if len(sys.argv) < 2 or not Path(sys.argv[1]).is_dir():
    sys.exit("Usage: python speed_test.py <folder of cat photos> [--claude]")
folder = Path(sys.argv[1])
photos = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"})[:5]
if not photos:
    sys.exit(f"No photos found in {folder}")

lap = stopwatch()
rows = []                                     # (step, seconds, how many times it ran)

# ---- Loading (happens once, when app.py starts) ---------------------------------
import torch
print(f"Computer: {os.cpu_count()} CPU threads, torch uses {torch.get_num_threads()}, "
      f"graphics card (CUDA): {'yes' if torch.cuda.is_available() else 'no'}")
print("Loading models (this part only happens when the app starts)...")
lap()
import catdex_step1;  rows.append(("Load YOLO (Step 1)", lap(), 1))
import catdex_step2;  rows.append(("Load CLIP + coat examples (Step 2)", lap(), 1))
import catdex_step3;  rows.append(("Load pet-ID model (Step 3)", lap(), 1))
import catdex_step4;  rows.append(("Load vibe prompts (Step 4)", lap(), 1))
import catdex_data;   lap()

# ---- One scan, step by step ------------------------------------------------------
print(f"Timing a scan of {len(photos)} photo(s) from {folder}...\n")
prepared = []
for p in photos:
    path, _ = catdex_data.prepare_upload(str(p))
    if path:
        prepared.append((path, p))
rows.append(("Prepare uploads (rotate, shrink, save)", lap(), len(prepared)))

crops = []
first = None
for path, original in prepared:
    result = catdex_step1.find_cat(path)
    if first is None:
        first = lap()                         # the very first model run is often slower ("warm-up")
    if result["ok"]:
        crops.append(result["crop"])
    else:
        print(f"  {original.name}: {result['message']}")
rows.append(("Step 1: find the cat (YOLO)", first + lap(), len(prepared)))
if not crops:
    sys.exit("No cats found in these photos - try other photos.")
sizes = ", ".join(f"{c.shape[1]}x{c.shape[0]}" for c in crops)

for c in crops:
    catdex_step2.classify_coat(c)
rows.append(("Step 2: coat page (CLIP)", lap(), len(crops)))

for c in crops:
    catdex_step3.fingerprint(c)
rows.append(("Step 3: fingerprints (pet-ID model)", lap(), len(crops)))
catdex_step3.identify(crops)                  # fingerprints again + compare with every cat
rows.append(("Step 3: identify (incl. comparing with Catdex)", lap(), len(crops)))

vibe, _ = catdex_step4.read_vibe(crops)
rows.append(("Step 4: vibe (CLIP again)", lap(), len(crops)))

if "--claude" in sys.argv:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            catdex_step4.personality_from_claude(crops[0], "Speedy", "tabby", vibe)
            rows.append(("Step 4: Claude personality (internet)", lap(), 1))
        except Exception as e:
            print(f"  Claude call failed: {type(e).__name__}: {e}")
    else:
        print("  (No API key found - skipped the Claude timing)")

# ---- Report --------------------------------------------------------------------
print(f"{'Step':<48}{'total':>8}{'per photo':>11}")
print("-" * 67)
for name, seconds, n in rows:
    each = f"{seconds / n:.2f}s" if n > 1 else ""
    print(f"{name:<48}{seconds:>7.2f}s{each:>11}")
scan_steps = [s for name, s, _ in rows if name.startswith(("Prepare", "Step"))]
print("-" * 67)
print(f"{'One scan + catch, in total':<48}{sum(scan_steps):>7.2f}s")
print(f"\nCat crops sent to the models: {sizes}")