<p align="center">
  <img src="catball.png" alt="Catdex cat-ball logo" width="160">
</p>

<h1 align="center">Catdex</h1>
<p align="center"><b>Catch them all, one cat at a time.</b><br>
Helping community cat caretakers recognise and keep track of every neighbourhood cat.</p>

---

## What it does

Catdex turns everyday cat-spotting into a collection, like a Pokédex for real cats.

- **Catch** a cat with your phone camera (front or back) or photos from your gallery
- **Detect** the cat in each photo, rejecting dogs, group shots and blurry images
- **File** it under one of nine coat pages: colourpoint, calico, tortoiseshell, ginger, tuxedo, white, tabby, black and grey
- **Recognise** individual cats you've met before, and ask you when it isn't sure
- **Create a trading card** for every new cat, with an AI-written title, personality, stats and lore
- **Track** your collection with profiles, photo albums, sighting timelines, badges and stats
- **Fix mix-ups** by removing wrongly merged photos or moving them to a new cat
- **Caretaker records**: mark cats as ear-tipped (sterilised through TNR), keep dated notes, and filter the
  Catdex to the cats that aren't ear-tipped yet

## Who it's for

Community cat caretakers and volunteers often look after dozens of neighbourhood cats, keeping track of
who's who through memory, photos and group chats. Catdex recognises individual cats from photos and
keeps each one's sightings, ear-tipping status and notes in one place.

## How it works

| Step | What happens | Tools |
|---|---|---|
| 1. Find the cat | Detects and crops one cat per photo; rejects dogs, multiple cats and blurry photos | YOLO11 (Ultralytics), OpenCV |
| 2. Coat page | Classifies the coat pattern zero-shot, nudged by hand-labelled examples | CLIP (`openai/clip-vit-base-patch32`) |
| 3. Who is it? | Turns each photo into a "fingerprint" and compares it with every cat in the Catdex: **known**, **ask the user** or **new** | Pet re-identification model (`AvitoTech/CLIP-ViT-base-for-animal-identification`) |
| 4. Trading card | Reads the cat's "vibe" from its pose, then writes the personality as structured JSON and draws the card | CLIP, Claude Haiku 4.5, Pillow |

New cats need **3+ different photos** (near-duplicate burst shots count once), so they're easier to recognise later. Known cats can be sighted with a single photo, and each sighting adds new photos to improve future matches.

No custom model training is needed: Catdex combines pretrained models with careful evaluation.

## Getting started

**Requirements:** Python 3.10+, and an API key for Claude (optional; without one, cards use a built-in template personality).

```bash
# 1. Install the dependencies
python -m pip install -r requirements.txt

# 2. Add your API key: create a file called .env next to app.py containing
#    ANTHROPIC_API_KEY=your-key-here
#    (Key from another Anthropic-compatible platform? Also add its address:)
#    ANTHROPIC_BASE_URL=https://your-platform-address

# 3. Check the key is found (never prints the key itself)
python catdex_step4.py --check-key

# 4. Start the app
python app.py
```

Then open the link it prints (usually http://127.0.0.1:7860). The models download automatically the first time (around 1 GB in total).

**On a phone:** set `SHARE = True` at the top of `app.py` for a temporary public HTTPS link. The camera only works over HTTPS or on localhost.

> ⚠️ Never commit your `.env` file. It's already listed in `.gitignore`.

## Using the app

| Page | What you can do |
|---|---|
| **Home** | Tap the cat-ball to start a catch; see the cat of the day and recent sightings |
| **Catch** | Take or upload photos, scan, then name a new cat or confirm a sighting |
| **Catdex** | Browse cats by coat page, search by name, sort by newest, most seen or A–Z |
| **Profile** | View the card, personality, photo album and sighting timeline; rename, remake the card, fix mixed-up photos or remove the cat |
| **Badges** | Coat-page and milestone badges, plus collection stats. *Settings → Reset for demo* empties the Catdex (a backup is kept) |

## Responsible AI and guardrails

All checks live in `catdex_safety.py` and are deliberately simple rules, so they're predictable and testable.

| Risk | Safeguard |
|---|---|
| Claude invents health facts ("she's injured", "pregnant") that could mislead caretakers | The prompt forbids it, and **every card is checked before it's shown**. Cards that mention health, injury, pregnancy or weight are replaced by a built-in template card |
| Claude guesses whether a cat is sterilised | Forbidden in the prompt and blocked by the card check. Ear-tipping is only ever recorded by a person |
| Rude names, or instructions hidden in a name ("ignore your rules…") | Names are checked when catching, renaming or splitting a cat, before anything is saved or sent to Claude |
| Instructions written inside a photo | The prompt tells Claude that text in the photo is part of the picture, and the card check catches the result if it slips through |
| Claude is slow, down, or the key is wrong | 20-second time limit, then the template card, so the app never hangs |
| Two lookalike cats get merged | Borderline matches go to the user ("Is it one of these?"), and wrong photos can be removed or split into a new cat |
| Stats or lore taken as fact | Every card says "stats just for fun" |

Every blocked name and replaced card is logged (`catdex_db/safety_log.jsonl`), and the Badges page shows the counts.

**Red-team testing:** `python redteam_cards.py` checks the filters against rude and tricky names (including
names that must stay allowed, like "Cassie" and "Scunthorpe") and against made-up unsafe cards. Add
`--live cat.jpg` to send tricky requests to Claude: a photo with an instruction written on it, an instruction
hidden in the name, and a name that invites a health claim. Results are saved to `redteam_results.md`.
The offline run already found and fixed one gap (cards saying a cat "will have kittens soon").

## Command-line tools

```bash
python catdex_step3.py photo1.jpg photo2.jpg photo3.jpg   # catch from photos (or a folder of one cat)
python catdex_step3.py --list                             # list every cat
python catdex_step3.py --remove Biscuit                   # remove one cat (by name or id)
python catdex_step3.py --reset                            # empty the Catdex (keeps a backup)
python catdex_step4.py --card Biscuit --redo              # remake a card with a new personality
python speed_test.py some_cat_folder --claude             # time each step on your computer
python claude_speed.py some_cat_photo.jpg                 # diagnose slow Claude calls
python redteam_cards.py --live some_cat_photo.jpg         # test the guardrails (see above)
```

## Project structure

```
app.py               Gradio web app (the interface)
catdex_ui.css/.js    Styling, animations, confetti and camera switch
catdex_data.py       Helpers behind the app: catching, collection, badges, stats, managing cats
catdex_step1.py      Step 1: find and crop the cat (YOLO11 + blur check)
catdex_step2.py      Step 2: coat page (CLIP + labelled examples)
catdex_step3.py      Step 3: recognition and the database (catdex_db/)
catdex_step4.py      Step 4: vibe, Claude personality and trading card
catdex_safety.py     Guardrails: name filter, card check and safety log
redteam_cards.py     Red-team test for the guardrails
requirements.txt     Python packages

Development and evaluation scripts:
pick_photos.py, crop_all.py, check_catvsdog.py, check_blur.py, tune_blur.py,
make_label_sheet.py, evaluate_coat.py, evaluate_recognition.py, coat_labels.csv
```

The Catdex itself (photos, fingerprints, cards) is stored locally in `catdex_db/` and isn't part of the repository.

## Tuning

| Setting | File | What it controls |
|---|---|---|
| `BLUR_THRESHOLD` | `catdex_step1.py` | How sharp a photo must be |
| `UNSURE_BELOW`, `EXAMPLE_STRENGTH` | `catdex_step2.py` | When the coat is "unsure"; how much labelled examples count |
| `MATCH_THRESHOLD`, `ASK_THRESHOLD` | `catdex_step3.py` | When a cat counts as known, when to ask, and when it's new (tune with `evaluate_recognition.py`) |
| `MIN_ENROL_PHOTOS` | `catdex_step3.py` | Photos needed for a new cat |
| `LLM_MODEL`, `CLAUDE_TIMEOUT` | `catdex_step4.py` | Which Claude model writes the cards, and how long to wait |

## Limitations

- Coat classification struggles most with dark cats and similar patterns (e.g. tortoiseshell vs calico).
- Photos with overlapping cats may be counted as one cat.
- Recognition can occasionally merge two similar-looking cats. Use **Profile → Photo album → Select** to split them again.
- Card stats and personalities are just for fun and say nothing real about the cat.

## What's next

- Online hosting (e.g. Hugging Face Spaces) with persistent storage
- A shared community Catdex for neighbourhood cat caretakers

## Acknowledgements

Built with [Ultralytics YOLO11](https://github.com/ultralytics/ultralytics), [OpenAI CLIP](https://huggingface.co/openai/clip-vit-base-patch32), [AvitoTech's animal-identification model](https://huggingface.co/AvitoTech/CLIP-ViT-base-for-animal-identification), [Claude](https://www.anthropic.com/claude) and [Gradio](https://www.gradio.app/).