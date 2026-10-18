"""
Catdex - the app (Gradio). Wraps Steps 1-4 in a web page that works on a laptop or a phone.

  Catch tab:   upload photos of ONE cat -> find the cat (Step 1) -> coat page (Step 2)
               -> "already caught" / "is this ...?" / "new cat" (Step 3) -> trading card (Step 4)
  Catdex tab:  every cat you've caught, filtered by coat page, with how many pages you've filled

Needs catdex_step1.py ... catdex_step4.py in the same folder (and your .env file for Claude).

Install once:  pip install --upgrade gradio
Run:          python app.py      then open the link it prints (usually http://127.0.0.1:7860)
"""

import uuid
import tempfile
from collections import Counter
from pathlib import Path

import gradio as gr
from PIL import Image, ImageOps

print("Loading the Catdex models (takes a little while the first time)...")
from catdex_step1 import find_cat                       # Step 1: find and crop the cat
from catdex_step2 import classify_coat, COAT_NAMES      # Step 2: coat page
import catdex_step3 as step3                            # Step 3: who is it? + the database
import catdex_step4 as step4                            # Step 4: vibe, personality, card

# ---- Settings you can tune -------------------------------------------------
SHARE = False        # True = also make a temporary public link (handy for phones and judges)
MAX_PHOTOS = 8       # use at most this many photos per scan, so it stays quick
ALL_PAGES = "All pages"

try:
    from pillow_heif import register_heif_opener      # optional: reads iPhone .HEIC photos
    register_heif_opener()                             # (pip install pillow-heif)
except ImportError:
    pass

TEMP = Path(tempfile.mkdtemp(prefix="catdex_"))        # resized copies of uploads go here
step3.DB.mkdir(parents=True, exist_ok=True)


# ---- Helpers ------------------------------------------------------------------
def _prepare(upload):
    """Turn any uploaded photo into a normal, upright, not-too-big JPEG that Step 1 can read.
    Returns (path, original file name), or (None, name) if the file isn't a photo we can open."""
    path = getattr(upload, "name", upload)             # Gradio gives a path (older versions: an object)
    name = Path(path).name
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img).convert("RGB")   # phone photos: apply the "rotate me" tag
    except Exception:
        return None, name
    img.thumbnail((1600, 1600))                        # 12-megapixel phone photos are slow and not needed
    out = TEMP / f"{uuid.uuid4().hex}.jpg"             # unique name, so two people can't clash
    img.save(out, quality=92)
    return str(out), name


def _rgb(crop):
    """OpenCV crops are BGR; the web page wants RGB."""
    return crop[:, :, ::-1]


def _card_path(cat_id):
    """The cat's card - made now if it doesn't have one yet."""
    cat = step3.load_db()["cats"][cat_id]
    if cat.get("card") and (step3.DB / cat["card"]).exists():
        return str(step3.DB / cat["card"])
    return str(step4.card_for_existing_cat(cat_id))


def _who_wrote_it(cat_id):
    p = step3.load_db()["cats"][cat_id].get("personality", {})
    return "Claude" if p.get("source", "template") != "template" else "the built-in template (no Claude)"


def catdex_view(page=ALL_PAGES):
    """Gallery items (picture, caption) for the Catdex tab, plus a progress summary."""
    db = step3.load_db()
    items = []
    for cat_id, cat in db["cats"].items():
        if page != ALL_PAGES and cat.get("coat") != page:
            continue
        if cat.get("card") and (step3.DB / cat["card"]).exists():
            picture = step3.DB / cat["card"]
        else:
            picture = step3.DB / "photos" / cat_id / cat["photos"][0]
        items.append((str(picture), f"{cat['name']} · {cat.get('coat') or '?'} · seen {cat['sightings']}x"))

    pages = Counter(cat.get("coat") for cat in db["cats"].values())
    filled = [c for c in COAT_NAMES if pages.get(c)]
    summary = (f"### {len(db['cats'])} cats caught · {len(filled)} of {len(COAT_NAMES)} coat pages filled\n"
               + " · ".join(f"{'✅' if pages.get(c) else '⬜'} {c} ({pages.get(c, 0)})" for c in COAT_NAMES))
    if not db["cats"]:
        summary += "\n\nNo cats yet - go to the **Catch a cat** tab!"
    return items, summary


# Every button returns the same 9 things, in this order:
#   photos used, message, memory of this scan, "is this...?" row, "new cat" row, card,
#   Catdex gallery, Catdex summary, Catdex page filter
def _reply(message, crops=None, scan=None, ask=False, new=False, card=None):
    items, summary = catdex_view()
    return ([_rgb(c) for c in crops] if crops else None, message, scan,
            gr.update(visible=ask), gr.update(visible=new),
            gr.update(value=card, visible=card is not None),   # only show the card box when there's a card
            items, summary, ALL_PAGES)


# ---- What the buttons do ------------------------------------------------------
def scan_photos(uploads):
    if not uploads:
        return _reply("Add some photos of **one** cat first (at least "
                      f"{step3.MIN_ENROL_PHOTOS} for a new cat, from different angles).")

    # Step 1 on every photo
    crops, notes = [], []
    for upload in uploads[:MAX_PHOTOS]:
        path, name = _prepare(upload)
        if path is None:
            notes.append(f"**{name}**: couldn't open this file (if it's an iPhone .HEIC photo, see the tips below)")
            continue
        result = find_cat(path)
        if result["ok"]:
            crops.append(result["crop"])
        else:
            notes.append(f"**{name}**: {result['message']}")
    if len(uploads) > MAX_PHOTOS:
        notes.append(f"Only the first {MAX_PHOTOS} photos were used.")

    keep = step3.distinct_photos(crops)                # burst shots count once
    if len(keep) < len(crops):
        notes.append(f"{len(crops) - len(keep)} photo(s) were almost the same shot as another")
    crops = [crops[i] for i in keep]
    skipped = ("\n\nSkipped:\n" + "\n".join(f"- {n}" for n in notes)) if notes else ""
    if not crops:
        return _reply("No usable photos." + skipped)

    # Step 2: coat page (majority vote across the photos)
    coat = Counter(classify_coat(c)["coat"] for c in crops).most_common(1)[0][0]

    # Step 3: who is it?
    result = step3.identify(crops, step3.load_db())
    scan = {"crops": crops, "fps": result["fingerprints"], "coat": coat,
            "cat_id": result["cat_id"], "name": result["name"]}
    matches = ", ".join(f"{n} ({s:.2f})" for n, s in result["top3"]) or "none yet"
    header = (f"**{len(crops)} photo(s) used** · coat page: **{coat}** · closest matches: {matches}"
              + skipped + "\n\n")

    if result["decision"] == "known":
        added = step3.add_sighting(result["cat_id"], crops, result["fingerprints"])
        return _reply(header + f"## Already caught: {result['name']}!\nSighting saved"
                      f" ({added} new photo(s) added to help recognise them next time).",
                      crops, card=_card_path(result["cat_id"]))
    if result["decision"] == "ask":
        return _reply(header + f"## Is this {result['name']}?\n"
                      f"Similarity {result['similarity']:.2f} - not sure enough to decide alone.",
                      crops, scan, ask=True)
    return _new_cat_reply(header, crops, scan)


def _new_cat_reply(header, crops, scan):
    if len(crops) < step3.MIN_ENROL_PHOTOS:
        return _reply(header + f"## Looks like a new cat!\nTo catch it, scan again with at least "
                      f"{step3.MIN_ENROL_PHOTOS} **different** photos (front, side, face...).", crops)
    return _reply(header + "## New cat! 🎉\nGive it a name and catch it.", crops, scan, new=True)


def answer_yes(scan):
    if not scan:
        return _reply("Scan some photos first.")
    added = step3.add_sighting(scan["cat_id"], scan["crops"], scan["fps"])
    return _reply(f"## Saved as another sighting of {scan['name']}!\n{added} new photo(s) added.",
                  scan["crops"], card=_card_path(scan["cat_id"]))


def answer_no(scan):
    if not scan:
        return _reply("Scan some photos first.")
    return _new_cat_reply("", scan["crops"], scan)


def catch_cat(name, scan):
    if not scan:
        return _reply("Scan some photos first.")
    name = (name or "").strip()[:30] or "Mystery Cat"
    try:
        cat_id = step3.add_new_cat(scan["crops"], name, scan["coat"], scan["fps"])
    except ValueError as e:                             # not enough different photos
        return _reply(str(e), scan["crops"])
    card = step4.welcome_new_cat(cat_id, scan["crops"])     # vibe -> personality -> card
    return _reply(f"## Caught {name}! ({cat_id})\nAdded to the **{scan['coat']}** page. "
                  f"Personality written by {_who_wrote_it(cat_id)}.", scan["crops"], card=str(card))


# ---- The page -----------------------------------------------------------------
HOW_IT_WORKS = f"""
1. **Find the cat** - a pretrained YOLO detector finds and crops the cat, and rejects dogs, group photos and blurry shots.
2. **Coat page** - CLIP compares the cat with descriptions of 9 coat patterns, helped by photos we labelled by hand.
3. **Who is it?** - a pet-identification model turns each photo into a "fingerprint" and compares it with every cat
   in your Catdex: very similar = already caught, a bit similar = we ask you, otherwise = new cat.
   New cats need {step3.MIN_ENROL_PHOTOS}+ different photos so they're easier to recognise later.
4. **Trading card** - CLIP reads the cat's "vibe" from its pose, then Claude looks at the photo and writes a
   personality, stats and lore. Stats are just for fun - they say nothing real about the cat.
"""

TIPS = f"""
- Photograph **one cat at a time**, in decent light, from **{step3.MIN_ENROL_PHOTOS} or more different angles**.
- Burst shots of the same pose count as one photo.
- iPhone photos in .HEIC format: run `pip install pillow-heif` on the computer running the app,
  or set the iPhone to **Settings → Camera → Formats → Most Compatible**.
"""

with gr.Blocks(title="Catdex") as demo:
    gr.Markdown("# 🐾 Catdex\nCatch the cats you meet. Each one gets a coat page, gets recognised when "
                "you meet it again, and gets its own trading card.")
    scan_memory = gr.State(None)                       # what the last scan found (per visitor)

    with gr.Tab("Catch a cat"):
        with gr.Row():
            with gr.Column(scale=1):
                uploads = gr.File(file_count="multiple", file_types=["image"], type="filepath",
                                  label=f"Photos of ONE cat ({step3.MIN_ENROL_PHOTOS}+ for a new cat)")
                scan_btn = gr.Button("🔍 Scan", variant="primary")
                used = gr.Gallery(label="Photos used (the cat Step 1 found)", columns=4, height=200,
                                  object_fit="cover")
                with gr.Accordion("Tips for good photos", open=False):
                    gr.Markdown(TIPS)
            with gr.Column(scale=1):
                message = gr.Markdown("Upload photos and press **Scan**.")
                with gr.Row(visible=False) as ask_row:
                    yes_btn = gr.Button("✅ Yes, that's them", variant="primary")
                    no_btn = gr.Button("❌ No, it's a new cat")
                with gr.Row(visible=False) as new_row:
                    name_box = gr.Textbox(label="Name", placeholder="e.g. Biscuit", max_lines=1, scale=3)
                    catch_btn = gr.Button("🎯 Catch!", variant="primary", scale=1)
                card = gr.Image(label="Trading card", type="filepath", height=560, interactive=False,
                                visible=False)

    with gr.Tab("My Catdex"):
        dex_summary = gr.Markdown()
        page_filter = gr.Dropdown([ALL_PAGES] + COAT_NAMES, value=ALL_PAGES, label="Coat page")
        dex_gallery = gr.Gallery(label="Your cats", columns=3, height="auto", object_fit="contain")
        refresh_btn = gr.Button("🔄 Refresh")

    with gr.Accordion("How Catdex works", open=False):
        gr.Markdown(HOW_IT_WORKS)

    outputs = [used, message, scan_memory, ask_row, new_row, card, dex_gallery, dex_summary, page_filter]
    scan_btn.click(scan_photos, inputs=[uploads], outputs=outputs)
    yes_btn.click(answer_yes, inputs=[scan_memory], outputs=outputs)
    no_btn.click(answer_no, inputs=[scan_memory], outputs=outputs)
    catch_btn.click(catch_cat, inputs=[name_box, scan_memory], outputs=outputs)
    name_box.submit(catch_cat, inputs=[name_box, scan_memory], outputs=outputs)   # Enter key works too

    page_filter.change(lambda p: catdex_view(p)[0], inputs=[page_filter], outputs=[dex_gallery])
    refresh_btn.click(lambda p: catdex_view(p), inputs=[page_filter], outputs=[dex_gallery, dex_summary])
    demo.load(lambda: catdex_view(), outputs=[dex_gallery, dex_summary])


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1)            # one scan at a time: keeps the database safe
    demo.launch(share=SHARE, allowed_paths=[str(step3.DB)],
                theme=gr.themes.Soft(primary_hue="orange", secondary_hue="amber"))