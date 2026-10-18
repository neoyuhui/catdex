"""
How accurate is Step 2? Compares CLIP's coat guesses with YOUR labels in coat_labels.csv.

Use SPLIT = "dev" while tuning prompts (as often as you like).
Use SPLIT = "test" ONCE at the very end - that's the number you report.

About the labelled examples (few-shot): on the dev set, each photo is checked with ITSELF hidden
from the examples ("leave-one-out"), so it can't just look up its own label. On the test set,
all dev examples are used - test photos were never examples, so that score is honest.

Install once:  pip install pandas
"""
from pathlib import Path
import pandas as pd
from catdex_step2 import classify_coat, build_prototypes, EXAMPLES, EXAMPLE_STRENGTH, USE_LABELLED_EXAMPLES

# ---- Settings ---------------------------------------------------------------
SPLIT = "dev"                # "dev" while tuning, "test" once at the end
CROPS_FOLDER = "cat_crops"
LABEL_FILE = "coat_labels.csv"

here = Path(__file__).resolve().parent
labels = pd.read_csv(here / LABEL_FILE)
labels["coat"] = labels["coat"].fillna("").str.strip().str.lower()   # tidy up typing differences

# Keep only this split's photos that you've labelled (and didn't mark "skip")
todo = labels[(labels["split"] == SPLIT) & (labels["coat"] != "") & (labels["coat"] != "skip")]
print(f"Checking {len(todo)} labelled photos in the '{SPLIT}' set...")
if todo.empty:
    raise SystemExit("No labelled photos found. Fill in the 'coat' column of coat_labels.csv first.")
if USE_LABELLED_EXAMPLES and EXAMPLE_STRENGTH > 0:
    print(f"Using {len(EXAMPLES)} labelled dev photos as examples (strength {EXAMPLE_STRENGTH})"
          + (", leaving each photo out of its own check" if SPLIT == "dev" else ""))
else:
    print("Text descriptions only (examples switched off)")

rows = []
for _, row in todo.iterrows():
    photo = here / CROPS_FOLDER / row["file"]
    if not photo.exists():
        print(f"  Skipping {row['file']}: it's in the spreadsheet but no longer in {CROPS_FOLDER}")
        continue
    if SPLIT == "dev":
        protos = build_prototypes(EXAMPLES, leave_out=row["file"])   # hide this photo from the examples
    else:
        protos = None                                                # test: use all dev examples
    result = classify_coat(str(photo), prototypes=protos)
    guesses = list(result["all_scores"].items())                    # all coats, best first
    rows.append({"file": row["file"], "true": row["coat"], "predicted": result["coat"],
                 "confidence": round(result["confidence"], 2),
                 "second_guess": f"{guesses[1][0]} {guesses[1][1]:.0%}"})
results = pd.DataFrame(rows)
if results.empty:
    raise SystemExit("None of the labelled photos could be found in the crops folder.")

# 1. Overall accuracy
correct = results["true"] == results["predicted"]
print(f"\nAccuracy: {correct.mean():.0%}  ({correct.sum()} of {len(results)} correct)")
print(f"Marked 'unsure': {(results['predicted'] == 'unsure').sum()}")

# 2. Accuracy for each coat (some coats are much harder than others)
print("\nAccuracy by coat:")
by_coat = results.assign(correct=correct).groupby("true")["correct"].agg(["mean", "count"])
for coat, (acc, n) in by_coat.iterrows():
    print(f"  {coat:<14} {acc:>4.0%}  ({int(n)} photos)")

# 3. Confusion matrix: rows = the real coat, columns = what CLIP said
print("\nConfusion matrix (rows = true coat, columns = CLIP's guess):")
print(pd.crosstab(results["true"], results["predicted"]))

# 4. Save every result, so you can look at the mistakes
out = here / f"coat_results_{SPLIT}.csv"
results.to_csv(out, index=False)
print(f"\nAll results saved to {out}. Sort by the 'predicted' column to find mistakes.")