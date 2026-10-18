"""Check how well Step 1 tells cats from dogs, using labelled test folders."""
import glob
from catdex_step1 import find_cat

def count(folder):
    results = {"cat found": 0, "said dog": 0, "other rejection": 0}
    for path in glob.glob(f"{folder}/*.jpg"):
        r = find_cat(path)
        if r["ok"]:
            results["cat found"] += 1
        elif r.get("is_dog"):
            results["said dog"] += 1
        else:
            results["other rejection"] += 1            # blurry, no animal, two animals...
            print(f"  {path}: {r['message']}")       # show these so you can look at them
    return results

print("Cat photos:", count("cats_only"))   # you want almost all of these in "cat found"
print("Dog photos:", count("dogs_only"))   # you want almost all of these in "said dog"