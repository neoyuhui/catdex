"""
Catdex - Step 2: what coat pattern does this cat have? (decides its Catdex page)

How it works:
  1. CLIP turns each text description ("a photo of a black cat") into a list of numbers.
  2. CLIP turns the cat photo into a list of numbers in the SAME "space".
  3. The description whose numbers are closest to the photo's numbers wins.     (zero-shot)
  4. EXTRA (few-shot): your hand-labelled DEV photos act as examples. For each coat, CLIP
     averages the numbers of its example photos. If a new photo looks more like a coat's
     examples than average, that coat gets a nudge up; less like them, a nudge down.
     This teaches CLIP what each coat looks like in YOUR photos (e.g. black cats in shadow).
     Only "dev" photos are ever used as examples - never "test" - so your final score stays honest.

Install once:   pip install transformers torch pillow
The CLIP model (~600 MB) downloads automatically the first time you run this.
"""

import csv
from pathlib import Path
import cv2                                   # to accept crops straight from Step 1
import torch                                 # the maths library CLIP runs on
from PIL import Image                        # CLIP's processor expects PIL images
from transformers import CLIPModel, CLIPProcessor

# ---- Settings you can tune -------------------------------------------------
MODEL_NAME = "openai/clip-vit-base-patch16"
UNSURE_BELOW = 0.30     # if the best coat is less than 30% likely, call it "unsure" instead of guessing
PRIORITY_MARGIN = 0     # tie-breaker: coats within this many points of the best -> the one HIGHER in the
                        # list wins. 0 = off (it was hurting black and tuxedo on the dev set)

# Few-shot examples from your labelled dev photos (see step 4 above)
USE_LABELLED_EXAMPLES = True
EXAMPLE_STRENGTH = 50   # how strongly the examples nudge the scores. 0 = off (text only).
                        # Try 0, 25, 50, 100 on the dev set and keep whichever scores best.
LABEL_FILE = "coat_labels.csv"
CROPS_FOLDER = "cat_crops"

# Boost a coat CLIP tends to under-score, e.g. {"black": 1.3} = black's score x1.3 before choosing.
# Leave as {} for no boosts. Tune ONLY on the dev set, and keep boosts small (1.1 to 1.5).
COAT_BOOST = {"black": 2} # Model has difficulty identifying black cats

# CATDEX COAT RULES (first match wins) - the same rules used for hand-labelling:
#   1. Dark points (face, ears, paws, tail) on a pale body          -> colourpoint
#   2. Orange + black (or cream + grey) on the same cat:
#        lots of white, in clear patches                            -> calico
#        little or no white, colours mixed together                 -> tortoiseshell
#   3. Any orange/cream, no black or grey (incl. orange-and-white,
#      and orange cats with stripes)                                -> ginger
#   4. Solid black or solid grey top, white chest and/or paws       -> tuxedo
#   5. All white                                                    -> white
#   6. Visible stripes, spots or forehead "M" (incl. white chest)   -> tabby
#   7. Solid black                                                  -> black
#   8. Solid grey                                                   -> grey
#
# The pages below are in that SAME order - the order is used as the tie-breaker.
# Each page has several descriptions; their scores are averaged. Edit phrasings to improve accuracy.
COAT_PROMPTS = {
    "colourpoint":   ["a photo of a siamese cat",
                      "a pale cream cat with a dark face, dark ears, dark paws and a dark tail",
                      "a colourpoint cat with darker fur on its face, ears, legs and tail"],
    "calico":        ["a photo of a calico cat",
                      "a white cat with large separate patches of orange and black",
                      "a mostly white cat with patches of cream and grey",
                      "a calico cat with tabby stripes in its orange and black patches",
                      "a white cat with striped orange and dark patches"],
    "tortoiseshell": ["a photo of a tortoiseshell cat",
                      "a cat with black and orange fur mixed together",
                      "a tortie cat with mottled dark brown and ginger fur",
                      "a cat with grey and cream fur mixed together"],
    "ginger":        ["a photo of a ginger cat",
                      "an orange tabby cat with orange stripes",
                      "an orange and white cat",
                      "a cream coloured cat"],
    "tuxedo":        ["a photo of a black and white tuxedo cat",
                      "a black and white cat",
                      "a black cat with a white chest and white paws",
                      "a grey and white cat"],
    "white":         ["a photo of a white cat",
                      "a cat with pure white fur all over"],
    "tabby":         ["a photo of a tabby cat",
                      "a brown tabby cat with dark stripes",
                      "a grey tabby cat with dark stripes",
                      "a tabby cat with an M marking on its forehead",
                      "a spotted tabby cat"],
    "black":         ["a photo of a black cat",
                      "a black cat with yellow eyes",
                      "a black cat in dim light",
                      "a cat with solid black fur all over"],
    "grey":          ["a photo of a grey cat",
                      "a russian blue cat with smooth plain blue-grey fur"],
}

HERE = Path(__file__).resolve().parent

# Load CLIP once when this file is imported (slow the first time, then fast)
model = CLIPModel.from_pretrained(MODEL_NAME)
processor = CLIPProcessor.from_pretrained(MODEL_NAME)
model.eval()                                 # "use" mode, not "training" mode


def _as_tensor(output):
    """Newer versions of transformers wrap the numbers in an object; older ones don't. Handle both."""
    return output if torch.is_tensor(output) else output.pooler_output


def _normalise(vectors):
    """Scale each list of numbers to length 1, so only the DIRECTION matters when comparing."""
    return vectors / vectors.norm(dim=-1, keepdim=True)


# Turn every description into numbers ONCE, then average the descriptions for each coat
COAT_NAMES = list(COAT_PROMPTS.keys())
with torch.no_grad():                        # no_grad = we're not training, so skip the extra bookkeeping
    coat_vectors = []
    for coat in COAT_NAMES:
        text_inputs = processor(text=COAT_PROMPTS[coat], return_tensors="pt", padding=True)
        text_vecs = _normalise(_as_tensor(model.get_text_features(**text_inputs)))
        coat_vectors.append(_normalise(text_vecs.mean(dim=0)))   # average of this coat's descriptions
    COAT_VECTORS = torch.stack(coat_vectors)                     # one row of numbers per coat


def _to_pil(image):
    """Accept a file path, or an OpenCV image (like the crop from Step 1), and return a PIL image."""
    if isinstance(image, (str, Path)):
        return Image.open(image).convert("RGB")
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))   # OpenCV is BGR, PIL is RGB


def clip_features(image):
    """The photo's CLIP numbers."""
    with torch.no_grad():
        image_inputs = processor(images=_to_pil(image), return_tensors="pt")
        return _normalise(_as_tensor(model.get_image_features(**image_inputs)))[0]


# ---- Few-shot examples ------------------------------------------------------
def load_examples():
    """
    Read your labelled DEV photos from coat_labels.csv and get their CLIP numbers.
    The numbers are saved in a small cache file so later runs are fast.
    Returns a list of (file name, coat, numbers).
    """
    label_path = HERE / LABEL_FILE
    if not label_path.exists():
        return []
    with open(label_path, newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["split"] == "dev"                                   # NEVER use test photos
                and r["coat"].strip().lower() in COAT_NAMES]             # skip blanks, "skip", "other"

    cache_path = HERE / "coat_example_features.pt"
    cache = torch.load(cache_path) if cache_path.exists() else {}
    examples, changed = [], False
    for r in rows:
        photo = HERE / CROPS_FOLDER / r["file"]
        if not photo.exists():
            continue
        key = f"{MODEL_NAME}|{r['file']}|{photo.stat().st_mtime}"     # redo if the crop or model changes
        if key not in cache:
            cache[key] = clip_features(photo)
            changed = True
        examples.append((r["file"], r["coat"].strip().lower(), cache[key]))
    if changed:
        torch.save(cache, cache_path)
    return examples


def build_prototypes(examples, leave_out=None):
    """
    For each coat, average the numbers of its example photos (a 'prototype').
    leave_out: a file name to ignore - used when testing that same photo, so it can't 'see itself'.
    """
    prototypes = {}
    for coat in COAT_NAMES:
        feats = [feat for file, c, feat in examples if c == coat and file != leave_out]
        if feats:
            prototypes[coat] = _normalise(torch.stack(feats).mean(dim=0))
    return prototypes


EXAMPLES = load_examples() if USE_LABELLED_EXAMPLES else []
PROTOTYPES = build_prototypes(EXAMPLES)


# ---- Choosing a coat --------------------------------------------------------
def choose_coat(scores):
    """
    Apply the rules' priority order as a tie-breaker.
    scores: {coat: probability}. Any coat within PRIORITY_MARGIN of the best score is a
    'close call'; among the close calls, the coat that comes FIRST in COAT_PROMPTS wins.
    """
    best_prob = max(scores.values())
    close_calls = [coat for coat in COAT_NAMES                        # COAT_NAMES is in priority order
                   if scores[coat] >= best_prob - PRIORITY_MARGIN]
    chosen = close_calls[0]                                           # highest-priority close call
    if best_prob < UNSURE_BELOW:
        return "unsure", best_prob                                    # better to ask than to guess
    return chosen, scores[chosen]


def classify_coat(image, prototypes=None):
    """
    image: a file path, or the "crop" from Step 1's find_cat().
    prototypes: leave as None to use your labelled examples (evaluate_coat.py passes its own).
    Returns {"coat": ..., "confidence": ..., "all_scores": {...}}
    """
    if prototypes is None:
        prototypes = PROTOTYPES
    photo_vec = clip_features(image)
    with torch.no_grad():
        logits = model.logit_scale.exp() * (COAT_VECTORS @ photo_vec)    # text-based scores

        # Few-shot nudge: compare the photo with each coat's example photos.
        # Coats MORE similar than average go up, LESS similar go down, coats with no examples stay put.
        if prototypes and EXAMPLE_STRENGTH > 0:
            sims = {coat: float(proto @ photo_vec) for coat, proto in prototypes.items()}
            average = sum(sims.values()) / len(sims)
            nudge = torch.tensor([EXAMPLE_STRENGTH * (sims[c] - average) if c in sims else 0.0
                                  for c in COAT_NAMES])
            logits = logits + nudge

        probs = logits.softmax(dim=0)                                    # turn scores into percentages
    scores = {coat: float(p) * COAT_BOOST.get(coat, 1.0)                # apply any boosts
              for coat, p in zip(COAT_NAMES, probs)}
    total = sum(scores.values())
    scores = {coat: s / total for coat, s in scores.items()}             # rescale so they add up to 100% again
    scores = dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True))   # best first

    coat, confidence = choose_coat(scores)
    return {"coat": coat, "confidence": confidence, "all_scores": scores}


# ---- Try it out -------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # python catdex_step2.py                       -> classify EVERY crop in the cat_crops folder
    # python catdex_step2.py cat_crops/cat_007.jpg -> classify just that one photo
    if USE_LABELLED_EXAMPLES:
        counts = {c: sum(1 for _, coat, _ in EXAMPLES if coat == c) for c in COAT_NAMES}
        print("Example photos per coat (from dev labels):",
              ", ".join(f"{c} {n}" for c, n in counts.items()), "\n")
    if len(sys.argv) > 1:
        photos = [Path(sys.argv[1])]
    else:
        crops_folder = HERE / CROPS_FOLDER
        photos = sorted(crops_folder.glob("*.jpg"))      # whatever files are really there, gaps and all
        print(f"Classifying {len(photos)} crops in {crops_folder}\n")

    for photo in photos:
        if not photo.exists():
            print(f"{photo}: file not found - check the name (some numbers are missing because "
                  f"blurry or rejected photos were skipped in Step 1)")
            continue
        result = classify_coat(str(photo))
        top3 = ", ".join(f"{c} {p:.0%}" for c, p in list(result["all_scores"].items())[:3])
        print(f"{photo.name}: {result['coat']} ({result['confidence']:.0%})   top 3: {top3}")