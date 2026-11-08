# Catdex — Project Status & Handoff Brief

_Last updated: 8 Nov 2026. First written from chat history; **§2, §4, §5 and §7 were then checked against the code by Claude Code (8 Nov 2026)**. Items that were marked [VERIFY] are now marked **✅ Verified** or **⚠️ Corrected**. New findings are in §2a._

---

## 1. What Catdex is

A web app that turns real-life cat encounters into a collectible "Catdex" (Pokédex-style):

1. **Identify** a cat from a photo
2. **File** it into the user's Catdex (a saved collection)
3. **Generate a personality** for the cat
4. **Read its vibe** from body language in the photo

- **Audience:** mainly laypeople, "just for funsies". A community cat caretaker angle is still being weighed.
- **Context:** solo build for the AI Lodge hackathon, with a Devpost submission in preparation. A professor has expressed interest, and the long-term goal is a public deployment.
- **Developer:** beginner Python skills. Explain changes in plain language, keep code simple, and avoid heavy frameworks unless clearly needed.

> Note: the project folder's `decision-log.md` describes an earlier **cattle weight-estimation app**. That idea was **dropped**. Ignore it for Catdex.

---

## 2. Current status

| Area | Status |
|---|---|
| UI framework | **Gradio 6** (venv has 6.30.0; `requirements.txt` says `gradio>=6.0`). Five tabs: Home, Catch, Catdex, Badges, Profile |
| Cat identification | ✅ **Built and working, all local models, no API.** Three steps: (1) `catdex_step1.py`: **YOLO11s** (Ultralytics, `yolo11s.pt`) finds exactly one cat, rejects dogs, groups and blurry crops (OpenCV blur check). (2) `catdex_step2.py`: **CLIP `openai/clip-vit-base-patch16`** picks one of 9 coat pages zero-shot, nudged by 92 hand-labelled example photos. (3) `catdex_step3.py`: **`AvitoTech/CLIP-ViT-base-for-animal-identification`** makes a 512-number "fingerprint" and compares it with every stored photo: "known" ≥ 0.92, "ask the user" ≥ 0.84, otherwise "new". A new cat needs 3+ different photos. Smoke-tested 8 Nov on `cat_001.jpg`: cat found, coat = tabby |
| Filing into a Catdex | ⚠️ **Corrected: it IS saved permanently, but only on the local disk.** Everything goes into `catdex_db/` (`cats.json` plus fingerprints, photos and cards), which survives restarts on a laptop. It would be lost on a Hugging Face Space, because the Space's disk is wiped on restart. The local Catdex has 7 cats right now |
| Personality generation | ✅ **Built: LLM API, Anthropic SDK, model `claude-haiku-4-5-20251001`** (`LLM_MODEL` in `catdex_step4.py`). Claude sees the photo and returns structured JSON (title, 3 traits, 5 stats, lore, favourite thing). Every card goes through `catdex_safety.py` before it is shown. If the key is missing, Claude is slow (20 s limit) or the card fails the check, the app uses a built-in template card instead. All 7 saved cats have Claude-written cards. **Note:** `.env` sets `ANTHROPIC_BASE_URL` to a third-party reseller (`api.apiyi.com`), not Anthropic directly. See §2a |
| Body-language "vibe" reading | ✅ **Built and working: local, no API.** `read_vibe()` in `catdex_step4.py` reuses the CLIP model from step 2 to pick one of 6 posture vibes (Cozy Royalty, Professional Napper, Dignified Sentinel, Chaos Gremlin, Self-Care Icon, Curious Explorer), with a majority vote across photos. Smoke test: "Cozy Royalty" in 2.4 s |
| Extras already built | Trading-card PNG (Pillow), badges and stats, sighting timeline, rename / remake card / remove, fixing mix-ups (remove or split photos into a new cat), caretaker records (ear-tipped status and dated notes), name filter, safety log, red-team script (`redteam_cards.py`) |
| Login / access control | Not implemented (no `auth=` in `demo.launch`) |
| Deployment | **Blocked**: Hugging Face now charges for standard Gradio Spaces (see §3). `app.py` has `SHARE = True`, so a laptop run already makes a temporary public link |
| Code structure | ✅ **Mostly separated already.** `app.py` is UI only, and `catdex_data.py` holds all the non-UI helpers (no Gradio imports). The AI steps are in `catdex_step1.py` to `catdex_step4.py`, and the guardrails are in `catdex_safety.py`. Gaps compared with §5: the files sit flat in the root rather than in a `catdex/` package, the storage code (`load_db` / `save_db`) is mixed into `catdex_step3.py`, and models load as soon as a module is imported |

### 2a. Other findings from the audit (worth fixing before deploying)

1. **The latest code isn't committed yet.** `catdex_safety.py` is untracked, but `catdex_step3.py`, `catdex_step4.py` and `catdex_data.py` (all modified, also uncommitted) now import it. Anyone cloning the repo, including a HF Space, gets code that crashes. `README.MD`, `redteam_cards.py` and the red-team results are untracked as well. Commit these before deploying.
2. **Coat accuracy will silently drop when deployed.** The few-shot nudge in step 2 needs `coat_labels.csv`, the `cat_crops/` folder and `coat_example_features.pt`. The last two are gitignored, so they won't be uploaded. Without them the app still runs, but quietly falls back to zero-shot CLIP, which scored worse on your dev set. Fix: ship the `.pt` cache, and make `load_examples()` use the cache without needing the original crops.
3. **Everything runs on the CPU.** No code moves a model to the GPU (`.to("cuda")`), and the venv has the CPU-only build of torch (`2.14.1+cpu`). See §7.
4. **The README names the wrong coat model.** It says `clip-vit-base-patch32`, but step 2 uses `patch16`. (Step 3 uses the patch32 *processor* only, to prepare images for the AvitoTech model.)
5. **The API goes through a third-party reseller.** Card requests, cat photos included, go to `api.apiyi.com` rather than Anthropic. Decide whether that is acceptable for a public deployment and what the privacy note says. Switching to Anthropic directly only means removing `ANTHROPIC_BASE_URL` and using an Anthropic key.
6. **Downloads on first start total about 1.2 GB:** YOLO11s (~19 MB) plus two CLIP-sized models (~600 MB each). Expect a slow cold start on any host. A local import took 46 s with the models already cached.
7. **Minor:** fonts fall back from Arial to DejaVu or Pillow's default on Linux, so cards may look slightly different on a Space. `.gradio/certificate.pem` is committed (it's Gradio's public share-link certificate, so it's harmless).

---

## 3. Deployment decisions (made 8 Nov 2026)

### Background: Hugging Face paywall
Since mid-2026, Hugging Face requires a paid plan for Gradio and Docker Spaces on CPU hardware. Only Static Spaces are free for everyone. **Switching to another Python UI SDK (e.g. Streamlit) does NOT fix this**, because Streamlit on HF runs via Docker, which is also paywalled. Decision: **do not rewrite the UI SDK.**

### Short term (hackathon / Devpost): HF ZeroGPU Space, free
- Free personal accounts (verified email, **account older than 30 days**) can host up to **2 ZeroGPU Spaces**. ZeroGPU works only with the Gradio SDK.
- Visitors get about **5 min of GPU time per day** on a free account, which is fine for a demo.
- Required code change: `import spaces`, then add the `@spaces.GPU` decorator to the function(s) that run a PyTorch model on the GPU.
- **[VERIFY: can't be checked from the code]** whether the account is eligible (age and verified email). Check at huggingface.co/settings/account. The HF CLI isn't logged in on this machine.
- ⚠️ ZeroGPU only helps if the models actually run on the GPU, and right now none of them do (see §2a #3). Making it work needs `.to("cuda")` inside the `@spaces.GPU` functions as well as the decorator. Because everything already runs on the CPU, also confirm with HF's ZeroGPU docs how a Space behaves when little or no GPU work happens.

### Demo-day backup
- `demo.launch(share=True)` from a laptop or Colab gives a public link with no rewrite. The link expires after about 72 hours, so it's a fallback only, not the Devpost link.

### Long term (public deployment)
1. Ask the professor whether SMU or the lab can provide hosting or cloud credits (preferred).
2. Fallback: **Hugging Face PRO (~US$9/month)**, which keeps Gradio and needs no code change. Free-hardware Spaces sleep when idle, so expect a cold start.
3. Alternative: **Google Cloud Run** (Gradio in a Docker container). It scales to zero, and the free tier covers 180k vCPU-seconds and 2M requests per month. It needs a billing account, so set a budget alert.
4. Student credits (GitHub Student Pack: US$100 Azure) can bridge the gap but expire. The DigitalOcean offer ended on 31 Jul 2026.

---

## 4. Persistence plan: single user first

**Goal now:** one account (the developer's own), with the collection saved permanently.
**Goal later:** separate saved collections for each user.

### Chosen approach
- **Storage:** a **private Hugging Face dataset repo** (e.g. `<username>/catdex-data`). The Space's own disk is wiped on restart, so data must live elsewhere.
- **Sync:** `huggingface_hub.CommitScheduler` pushes a local data folder to the dataset repo every few minutes. `snapshot_download` pulls the existing collection on startup.
- **Access control:** Gradio's built-in login: `demo.launch(auth=("me", os.environ["CATDEX_PASSWORD"]))`.
- **Future-proofing:** every saved record includes a `user_id` field (always `"me"` for now), so the move to multiple users needs no data migration.

### Proposed data layout (in the dataset repo)
```
catdex_data/
├── cats.jsonl        # one JSON object per line, one line per cat
└── images/
    └── <cat_id>.jpg
```

### Proposed record schema (`cats.jsonl`)
```json
{"cat_id": "uuid4", "user_id": "me", "name": "...", "personality": "...",
 "vibe": "...", "identified_as": "...", "created_at": "ISO-8601 timestamp"}
```
⚠️ **Adjusted: the app already has its own storage format, and it doesn't match the proposal above.** What `catdex_step3.py` actually writes:
```
catdex_db/
├── cats.json                 # ONE JSON file: {"cats": {"cat_0001": {...}, ...}}, rewritten on every change
├── fingerprints/<cat_id>.npy # recognition fingerprints, one row per stored photo (REQUIRED for recognition)
├── photos/<cat_id>/<timestamp>.jpg   # several photos per cat, not one
├── cards/<cat_id>.png        # the drawn trading card
└── safety_log.jsonl          # blocked names / replaced cards
```
Real record (the value under each `cat_id` key in `cats.json`):
```json
{"name": "Biscuit", "coat": "tabby", "vibe": "Cozy Royalty",
 "first_seen": "2026-10-17T10:00:00", "last_seen": "...", "sightings": 3,
 "sighting_dates": ["...", "..."], "photos": ["20261017_100000_123456.jpg", "..."],
 "card": "cards/cat_0001.png",
 "personality": {"title": "...", "traits": ["..."], "stats": {"stealth": 40, "zoomies": 15,
                 "charm": 80, "chill": 95, "mischief": 25}, "lore": "...",
                 "favourite_thing": "...", "source": "claude-haiku-4-5-20251001"},
 "ear_tipped": "yes|no|unsure",            // optional, set by the caretaker
 "notes": [{"date": "...", "text": "..."}]} // optional
```
Differences from the proposal: ids are sequential (`cat_0001`), not uuid4. There's one `cats.json`, not `cats.jsonl`. `first_seen` plays the role of `created_at`, and `coat` plays the role of `identified_as`. There's **no `user_id` yet**. Fingerprints and multiple photos per cat aren't in the proposal at all. Some older cats also have extra personality keys (`design`, `moves`, `resistance`, `weakness`) left over from an earlier card design.

**Recommendation:** don't switch to `cats.jsonl`. Records get edited all the time (renaming, sightings, notes, removing photos), and an append-only file can't handle that. Instead, point `CommitScheduler` at the whole existing `catdex_db/` folder, and add `"user_id": "me"` to each record in `add_new_cat()`. The reference sketch below would need to change to match this.

### Reference sketch (agreed in chat, not yet integrated)
```python
import json, os, uuid
from pathlib import Path
from huggingface_hub import CommitScheduler, snapshot_download

REPO = "your-username/catdex-data"        # private dataset repo
TOKEN = os.environ["HF_TOKEN"]            # stored in Space Secrets
DATA = Path("catdex_data")

snapshot_download(REPO, repo_type="dataset", local_dir=DATA, token=TOKEN)
scheduler = CommitScheduler(repo_id=REPO, repo_type="dataset",
                            folder_path=DATA, every=2, token=TOKEN)

def save_cat(image, name, personality, user_id="me"):
    cat_id = str(uuid.uuid4())
    with scheduler.lock:
        (DATA / "images").mkdir(exist_ok=True)
        image.save(DATA / "images" / f"{cat_id}.jpg")   # gr.Image(type="pil")
        with open(DATA / "cats.jsonl", "a") as f:
            f.write(json.dumps({"cat_id": cat_id, "user_id": user_id,
                                "name": name, "personality": personality}) + "\n")
```
Caveats: uploads are batched, so data saved just before a restart may be lost. Handle the empty-repo case on first startup.

### Later: multi-user
Move to **Supabase** (Postgres database + file storage + user auth in one service). Its free-tier projects **pause after 7 days of inactivity**, which is why it isn't used for the single-user phase.

---

## 5. Target code structure

Keep the logic separate from the UI, so the interface can be swapped later (e.g. Gradio → web frontend + API) without touching the model code.

```
catdex/
├── app.py              # Gradio UI only: layout, buttons, wiring
├── catdex/
│   ├── identify.py     # cat identification
│   ├── personality.py  # personality generation
│   ├── vibe.py         # body-language reading
│   └── storage.py      # save/load collection (HF dataset repo)
├── requirements.txt
└── README.md           # includes the HF Spaces YAML header (sdk: gradio, etc.)
```

### Secrets: never hard-code these. Use HF Space Secrets or a local `.env` that is gitignored.
- `HF_TOKEN`: write access to the private dataset repo
- `CATDEX_PASSWORD`: Gradio login
- ✅ `ANTHROPIC_API_KEY`: the only API key the app uses (for Claude cards). It's optional, because without it the cards use the template.
- `ANTHROPIC_BASE_URL`: only needed when using a third-party Anthropic-compatible platform (currently set to `api.apiyi.com`). Leave it out if using Anthropic directly. (`ANTHROPIC_AUTH_TOKEN` is also supported, for platforms that want a Bearer token.)
- `catdex_step4.py` reads these from `.env` itself, and on a Space it reads them from environment variables (Space Secrets), so no code change is needed. `.env` is gitignored and has never been committed ✅

---

## 6. Next steps (suggested order)

1. ~~**Audit the repo** and fill in all [VERIFY] items in this file.~~ Done 8 Nov 2026 (HF account eligibility still needs checking by hand).
1a. **Commit the current work**, including `catdex_safety.py` (see §2a #1).
2. **Refactor** so the logic lives in `catdex/` modules and `app.py` is UI only. (Already mostly separated, so this is now a light tidy-up rather than a rewrite. See §2.)
3. `requirements.txt` ✅ exists. The README ❌ has **no** HF Spaces YAML header yet, so add one. Also add `huggingface_hub` (for persistence) and, if using ZeroGPU, `spaces`.
4. **Add persistence** (`storage.py`) using the dataset-repo approach plus the `user_id` field.
5. **Add a "My Catdex" view** in Gradio to browse saved cats.
6. **Add the Gradio login** (`auth=`) with the password from env/Secrets.
7. **Make it ZeroGPU-ready** (`import spaces`, `@spaces.GPU` on GPU functions) and test locally.
8. **Deploy** to a ZeroGPU Space, then test on a phone browser.
9. If an LLM API is used: add simple rate limiting and set a spending cap with the provider.
10. Prepare the `share=True` laptop backup for demo day.

---

## 7. Open questions
- ~~Does Catdex run models locally (PyTorch on GPU) or mostly call external APIs?~~ **Answered by the audit:** three PyTorch models run locally (YOLO11s and two CLIP models), but **on the CPU only**. Only the card text uses an API (Claude). New question: is CPU speed acceptable on a free CPU host (run `speed_test.py` to measure), or is it worth adding GPU code just to get ZeroGPU's free hosting?
- Is it OK to send cat photos through the `api.apiyi.com` reseller, or should the public version call Anthropic directly?
- Should uploaded photos be stored, and what should a privacy note to users say?
- Laypeople "for funsies" vs. community cat caretaker focus?
- Does the professor or SMU have hosting or credits available?

---

## First task for Claude Code
Read the whole repo, then update §2 and the [VERIFY] items above with what the code actually does: models/APIs used, features working vs. stubbed, file structure, and dependencies. Report back in plain language before making changes.