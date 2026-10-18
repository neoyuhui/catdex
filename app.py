"""
Catdex - the app (Gradio 6). A cute, phone-first web page around Steps 1-4.

  Home     the cat-ball (tap it to catch a cat), cat of the day, recently seen cats, photo tips
  Catch    take photos or pick them from the gallery -> scan -> new cat / welcome back / "is it one of these?"
  Catdex   every cat you've caught, on 9 coat pages, with search and sort
  Badges   stickers you unlock, stats, and (tucked away) "reset for demo"
  Profile  opens when you tap any cat: card, personality, photo album, sightings, rename/remake/remove

How the files fit together:
  app.py          - this file: ONLY the interface (what you see and what the buttons do)
  catdex_data.py  - every non-interface helper (scanning, the collection, badges, managing cats)
  catdex_ui.css   - the look (colours, fonts, the bottom tab bar, animations)
  catdex_ui.js    - small browser extras (confetti, force light mode, camera re-open)
  catdex_step1..4 - the AI steps

Install once:  pip install --upgrade gradio      (optional for iPhone photos: pip install pillow-heif)
Run:          python app.py      then open the link it prints (usually http://127.0.0.1:7860)
"""

import base64
import html
import io
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import gradio as gr
from PIL import Image

print("Loading the Catdex models (takes a little while the first time)...")
import catdex_data                                    # loads Steps 1-4 too

# =============================================================================
# 1. Settings you can tune
# =============================================================================
SHARE = False         # True = also make a temporary public link (handy for phones and judges)
STEP_PAUSE = 0.35     # seconds to pause between scan steps, so people can read the progress lines
RECENT_COUNT = 6      # how many "recently seen" cats to show on Home
MAX_TRAY = catdex_data.MAX_PHOTOS          # most photos you can add before one scan
NEED = catdex_data.MIN_ENROL_PHOTOS        # a NEW cat needs this many different photos (3)
COAT_NAMES = catdex_data.COAT_NAMES
MATCH = catdex_data.step3.MATCH_THRESHOLD   # Step 3: at or above = "same cat"
ASK = catdex_data.step3.ASK_THRESHOLD       # Step 3: between ASK and MATCH = "ask the user"
SHOW_CANDIDATE_FROM = ASK - 0.15             # "Is it one of these?" hides cats less similar than this

HERE = Path(__file__).resolve().parent     # the folder this file is in (the CSS/JS live here too)
CSS_FILE = HERE / "catdex_ui.css"
JS_FILE = HERE / "catdex_ui.js"

# Fonts: a friendly rounded font from Google Fonts. If there's no internet, the CSS falls back
# to rounded system fonts, so the app still looks fine.
HEAD = """
<meta name="theme-color" content="#FFF8F0">
<meta name="color-scheme" content="light only">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@600;700;800&family=Nunito:wght@500;700;800&display=swap">
"""

# Gradio's own colours and shapes, matched to the peach & cream look (catdex_ui.css does the rest)
THEME = gr.themes.Base(
    primary_hue="orange", neutral_hue="stone", radius_size="lg",
    font=["Nunito", "ui-rounded", "Arial Rounded MT Bold", "Segoe UI", "system-ui", "sans-serif"],
).set(
    body_background_fill="#FFF8F0", body_text_color="#5C3A21",
    background_fill_primary="#FFFDF9", background_fill_secondary="#FFF3E6",
    block_background_fill="#FFFDF9", block_border_color="#F0DFCF", block_radius="20px",
    block_label_text_color="#7A4E2D", block_title_text_color="#7A4E2D",
    border_color_primary="#F0DFCF", color_accent="#F6A57A", color_accent_soft="#FDE7DA",
    input_background_fill="#FFFFFF", input_border_color="#EBD3BF", input_border_color_focus="#F6A57A",
    input_radius="14px",
    button_primary_background_fill="#F6A57A", button_primary_background_fill_hover="#F39463",
    button_primary_text_color="#4E2F1A", button_primary_border_color="#E8915F",
    button_secondary_background_fill="#FFFFFF", button_secondary_background_fill_hover="#FFF3E6",
    button_secondary_text_color="#7A4E2D", button_secondary_border_color="#EBD3BF",
    button_large_radius="18px", button_medium_radius="16px", button_small_radius="12px",
    checkbox_label_background_fill_selected="#F6A57A", checkbox_label_text_color_selected="#4E2F1A",
    loader_color="#F6A57A", slider_color="#F6A57A",
)

# One small piece of browser code shared by every clickable HTML block.
# Any tap on something with data-cat="cat_0001" or data-action="..." sends that to Python as a
# "click" event; the Python function reads it with  evt.cat_id / evt.action / evt.index.
# (The listener sits on the block itself, so it keeps working when Python redraws the inside.)
CLICK_JS = """
element.addEventListener('click', (event) => {
  const target = event.target.closest('[data-cat], [data-action]');
  if (!target || !element.contains(target)) return;
  trigger('click', {cat_id: target.dataset.cat || '', action: target.dataset.action || '',
                    index: target.dataset.index || ''});
});
"""


# =============================================================================
# 2. Small helpers
# =============================================================================
def esc(text):
    """Make text safe to put inside HTML (a cat called <b>Bob</b> stays as plain text)."""
    return html.escape(str(text or ""), quote=True)


def file_url(path):
    """Web address for a picture in the Catdex folder. The ?v=... part changes when the file
    changes, so the browser shows a remade card instead of the old one it remembered."""
    if not path:
        return ""
    version = int(os.path.getmtime(path)) if os.path.exists(path) else 0
    return f"/gradio_api/file={quote(str(path).replace(os.sep, '/'), safe='/:')}?v={version}"


def thumb_uri(picture, size=240):
    """A small JPEG built into the page as text (a "data URI"). Used for photos that aren't in the
    Catdex folder yet, like the photos waiting to be scanned. `picture` = a file path or an RGB array."""
    try:
        img = Image.open(picture) if isinstance(picture, (str, Path)) else Image.fromarray(picture)
        img = img.convert("RGB")
        img.thumbnail((size, size))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        return ""


def stars(n):
    return "★" * n + "☆" * (3 - n)


def coat_label(coat):
    return "Not sure yet" if coat in (None, "", catdex_data.UNSURE) else coat.capitalize()


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def closeness(score):
    """A friendly word for a similarity score (they aren't percentages)."""
    return "Very close" if score >= MATCH else "Close" if score >= ASK else "Maybe"


def short_date(iso):
    """'2026-10-17T14:05:00' -> '17 Oct' (adds the year only if it isn't this year)."""
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{when.day} {when:%b}" + ("" if when.year == datetime.now().year else f" {when.year}")


def greeting():
    hour = datetime.now().hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


# =============================================================================
# 3. Little drawings (SVG = pictures written as code, so they stay sharp at any size)
# =============================================================================
def cat_ball_svg():
    """The cat-ball: a capture ball with cat ears, a happy face, whiskers and a paw button.
    The top half is its own group (class "ball-top") so CSS can make it pop open."""
    return """
<svg class="catball-svg" viewBox="0 0 200 200" aria-hidden="true">
  <g class="ball-top">
    <path d="M36 74 L44 12 L94 42 Z" fill="#F6A57A" stroke="#7A4E2D" stroke-width="5" stroke-linejoin="round"/>
    <path d="M51 58 L55 31 L79 45 Z" fill="#F9C9B4"/>
    <path d="M164 74 L156 12 L106 42 Z" fill="#F6A57A" stroke="#7A4E2D" stroke-width="5" stroke-linejoin="round"/>
    <path d="M149 58 L145 31 L121 45 Z" fill="#F9C9B4"/>
    <path d="M18 112 A82 82 0 0 1 182 112 Z" fill="#F6A57A" stroke="#7A4E2D" stroke-width="5" stroke-linejoin="round"/>
    <ellipse cx="62" cy="62" rx="15" ry="8" fill="#FFFFFF" opacity=".45" transform="rotate(-32 62 62)"/>
    <path d="M60 84 q10 -11 20 0" stroke="#7A4E2D" stroke-width="5" fill="none" stroke-linecap="round"/>
    <path d="M120 84 q10 -11 20 0" stroke="#7A4E2D" stroke-width="5" fill="none" stroke-linecap="round"/>
    <path d="M64 100 L30 92 M64 104 L28 104" stroke="#7A4E2D" stroke-width="3" stroke-linecap="round"/>
    <path d="M136 100 L170 92 M136 104 L172 104" stroke="#7A4E2D" stroke-width="3" stroke-linecap="round"/>
  </g>
  <g class="ball-bottom">
    <path d="M18 112 A82 82 0 0 0 182 112 Z" fill="#FFF3E6" stroke="#7A4E2D" stroke-width="5" stroke-linejoin="round"/>
    <ellipse cx="50" cy="138" rx="13" ry="7" fill="#F9C9B4"/>
    <ellipse cx="150" cy="138" rx="13" ry="7" fill="#F9C9B4"/>
    <path d="M64 122 L30 130 M136 122 L170 130" stroke="#7A4E2D" stroke-width="3" stroke-linecap="round"/>
    <path d="M20 112 H180" stroke="#7A4E2D" stroke-width="9" stroke-linecap="round"/>
    <circle cx="100" cy="112" r="27" fill="#FFF8F0" stroke="#7A4E2D" stroke-width="6"/>
    <g class="ball-paw" fill="#EE8F78">
      <ellipse cx="100" cy="119" rx="10" ry="8"/>
      <circle cx="87.5" cy="106" r="4.4"/><circle cx="95.5" cy="100.5" r="4.4"/>
      <circle cx="104.5" cy="100.5" r="4.4"/><circle cx="112.5" cy="106" r="4.4"/>
    </g>
  </g>
</svg>"""


PAW = ('<svg class="paw" viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="15.5" rx="5" ry="4.2"/>'
       '<circle cx="5.5" cy="9.5" r="2.3"/><circle cx="9.5" cy="5.8" r="2.3"/><circle cx="14.5" cy="5.8" r="2.3"/>'
       '<circle cx="18.5" cy="9.5" r="2.3"/></svg>')

# A sitting cat, used greyed-out for coat pages with no cats yet
SILHOUETTE = ('<svg class="silhouette" viewBox="0 0 64 64" aria-hidden="true">'
              '<path d="M20 9 L26 21 Q32 19 38 21 L44 9 L46 26 Q49 31 47 37 Q55 43 53 54 Q52 60 45 60 '
              'L21 60 Q14 60 13 54 Q11 43 19 37 Q16 31 18 26 Z"/>'
              '<path d="M45 58 Q60 57 58 45" fill="none" stroke-width="4" stroke-linecap="round"/></svg>')


# =============================================================================
# 4. HTML builders - turn data from catdex_data into pieces of page
# =============================================================================
def cat_tile(cat, extra=""):
    """A small square tile (photo + name). Tapping it opens the cat's profile."""
    photo = (f'<img src="{file_url(cat["thumb_path"])}" alt="" loading="lazy">' if cat.get("thumb_path")
             else f'<span class="tile-blank">{PAW}</span>')
    return (f'<button class="cat-tile rarity-{cat["rarity"].lower()}" data-cat="{esc(cat["id"])}" '
            f'aria-label="Open {esc(cat["name"])}\'s profile">'
            f'<span class="tile-photo">{photo}</span>'
            f'<span class="tile-name">{esc(cat["name"])}</span>'
            f'<span class="tile-meta">{extra or "Seen " + str(cat["sightings"]) + "×"}</span></button>')


def card_img(cat, css_class="card-img"):
    """The cat's trading card picture (or its photo if the card is missing)."""
    path = cat.get("card_path") or cat.get("thumb_path")
    if not path:
        return f'<div class="{css_class} card-missing">{PAW}</div>'
    return f'<img class="{css_class}" src="{file_url(path)}" alt="Trading card for {esc(cat["name"])}">'


def friendship_meter(friend):
    """The friendship bar: sightings towards 10 = Best friends."""
    return (f'<div class="friend-meter" role="img" aria-label="Friendship {friend["value"]} of {friend["target"]}">'
            f'<div class="friend-top"><span>Friendship</span><b>{esc(friend["label"])}</b></div>'
            f'<div class="friend-track"><div class="friend-fill" style="width:{friend["percent"]}%"></div></div>'
            f'<div class="friend-note">{min(friend["value"], friend["target"])} of {friend["target"]} sightings'
            f'{" 💞" if friend["value"] >= friend["target"] else " to best friends"}</div></div>')


def badge_sticker(badge, tilt=0, small=False):
    """A round sticker. Locked badges are grey with a hint of how to unlock them."""
    state = "unlocked" if badge["unlocked"] else "locked"
    under = esc(badge["detail"]) if badge["unlocked"] else esc(badge["hint"])
    progress = (f'<span class="sticker-progress">{esc(badge["progress"])}</span>'
                if not badge["unlocked"] and badge.get("progress") else "")
    return (f'<div class="sticker {state}{" small" if small else ""}" style="--tilt:{tilt}deg">'
            f'<div class="sticker-face"><span class="sticker-emoji">{badge["emoji"]}</span>'
            f'{"" if badge["unlocked"] else "<span class=sticker-lock>🔒</span>"}</div>'
            f'<div class="sticker-name">{esc(badge["name"])}</div>'
            f'<div class="sticker-detail">{under}</div>{progress}</div>')


# ---- Home ---------------------------------------------------------------------
TIPS = [("🐱", "One cat at a time", "Two cats in a photo confuses the scanner."),
        ("📐", f"{NEED}+ different angles", "Front, side and face, so it can recognise them later."),
        ("☀️", "Decent light", "Blurry or very dark photos get skipped."),
        ("📸", "Burst shots count once", "Near-identical photos only count as one.")]


def home_html(tips_hidden=False):
    count = catdex_data.progress()["cats"]
    parts = [f'<section class="home-hero">'
             f'<p class="hello">{greeting()}! {"Ready to catch your first cat?" if not count else "Spotted a cat?"}</p>'
             f'<button class="catball catball-big is-idle" data-action="catch" aria-label="Catch a cat">'
             f'{cat_ball_svg()}</button>'
             f'<p class="ball-hint">Tap the cat-ball to catch a cat</p></section>']

    # Photo tips: always shown while the Catdex is empty, otherwise until dismissed
    if count == 0 or not tips_hidden:
        close = ('' if count == 0 else
                 '<button class="tips-close" data-action="hide-tips" aria-label="Hide tips">Got it</button>')
        items = "".join(f'<li><span class="tip-icon">{icon}</span><div><b>{title}</b><span>{text}</span></div></li>'
                        for icon, title, text in TIPS)
        parts.append(f'<section class="panel tips"><div class="panel-head"><h2>Tips for great cat photos</h2>'
                     f'{close}</div><ul>{items}</ul></section>')

    # Cat of the day (same cat all day)
    cat = catdex_data.cat_of_the_day()
    if cat:
        title = f'<p class="cotd-title">{esc(cat["title"])}</p>' if cat["title"] else ""
        parts.append(
            f'<section class="panel cotd"><h2>Cat of the day</h2>'
            f'<button class="cotd-body" data-cat="{esc(cat["id"])}" aria-label="Open {esc(cat["name"])}\'s profile">'
            f'{card_img(cat, "cotd-card")}<span class="cotd-text"><b class="cotd-name">{esc(cat["name"])}</b>{title}'
            f'<span class="chip coat-chip"><i class="swatch swatch-{esc(cat["coat"] or "unsure")}"></i>'
            f'{coat_label(cat["coat"])}</span>'
            f'<span class="chip rarity-chip rarity-{cat["rarity"].lower()}">{stars(cat["stars"])} {cat["rarity"]}</span>'
            f'<span class="cotd-seen">Seen {plural(cat["sightings"], "time")} · last on '
            f'{catdex_data.nice_date(cat["last_seen"])}</span>'
            f'<span class="link-ish">Open profile</span></span></button></section>')
    else:
        parts.append(f'<section class="panel cotd empty"><h2>Cat of the day</h2>'
                     f'<div class="empty-state">{SILHOUETTE}<p><b>No cats yet</b>Your first catch will be '
                     f'featured here. Tap the cat-ball to start.</p></div></section>')

    recent = catdex_data.recent_cats(RECENT_COUNT)
    if recent:
        tiles = "".join(cat_tile(c, extra=f'Seen {short_date(c["last_seen"])}') for c in recent)
        parts.append(f'<section class="panel recent"><h2>Recently seen</h2><div class="tile-row">{tiles}</div></section>')
    return f'<div class="page home">{"".join(parts)}</div>'


# ---- Catch: the ball, progress lines and the photo tray -------------------------
RESULT_PLACEHOLDER = (f'<div class="result-placeholder">{SILHOUETTE}<p>Your catch will appear here. '
                      f'Add photos and press Scan.</p></div>')     # only shown on laptops (see CSS)


def catch_top_html(state="idle", lines=(), message=""):
    """The cat-ball at the top of the Catch page plus what it's doing.
    state: "idle", "scanning" (wobbles) or "open" (popped open). lines: progress lines so far."""
    steps = ""
    if lines:
        items = []
        for i, line in enumerate(lines):
            current = state == "scanning" and i == len(lines) - 1
            items.append(f'<li class="{"now" if current else "done"}"><span class="step-dot">'
                         f'{"" if current else PAW}</span>{esc(line)}</li>')
        steps = f'<ol class="scan-steps" aria-live="polite">{"".join(items)}</ol>'
    note = f'<div class="catch-message">{message}</div>' if message else ""
    if not lines and not message:
        note = ('<p class="catch-intro">Add photos of <b>one</b> cat, then scan. '
                f'A new cat needs <b>{NEED} different photos</b>; a cat you\'ve met before needs just 1.</p>')
    return (f'<div class="catch-top"><div class="catball catball-mid is-{state}">{cat_ball_svg()}'
            f'<span class="pop-sparks" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span></div>'
            f'<div class="catch-status">{steps}{note}</div></div>')


def tray_html(photos):
    """The photos waiting to be scanned: thumbnails with a remove button, plus a counter.
    We can't know which photos are usable until the scan has looked at them, so before a scan the
    counter only says how many were added. After a scan, skipped photos are taken out of the tray,
    and the ones left are marked as checked ("2 of 3 good photos")."""
    good = [p for p in photos if p.get("path")]
    n = len(good)
    if not photos:
        return ('<div class="tray empty"><div class="tray-empty">' + PAW +
                '<span>No photos yet. Take some or choose them from your gallery.</span></div></div>')
    checked = n > 0 and all(p.get("checked") for p in good)
    if checked:
        label = f"<b>{n} of {NEED}</b> good photos"
        hint = (f"Add {NEED - n} more from a different angle for a new cat." if n < NEED
                else "Enough good photos for a new cat.")
    else:
        label = f"<b>{n}</b> {'photo' if n == 1 else 'photos'} added"
        hint = (f"A new cat needs {NEED} good, different photos; a cat you've met before needs just 1. "
                "The scan checks each one.")
    dots = "".join(f'<i class="{"on" if i < n else ""}"></i>' for i in range(max(NEED, min(n, MAX_TRAY))))
    thumbs = []
    for i, p in enumerate(photos):
        inside = (f'<img src="{p["thumb"]}" alt="Photo {i + 1}">' if p.get("path") and p.get("thumb")
                  else f'<span class="thumb-bad">Can\'t open<br>{esc(p["name"])[:18]}</span>')
        tick = '<span class="thumb-ok" title="Checked: a usable cat photo">✓</span>' if p.get("checked") else ""
        thumbs.append(f'<div class="thumb">{inside}{tick}<button class="thumb-x" data-action="remove" data-index="{i}" '
                      f'aria-label="Remove photo {i + 1}">×</button></div>')
    return (f'<div class="tray"><div class="tray-count"><span>{label}</span>'
            f'<span class="tray-dots">{dots}</span></div>'
            f'<p class="tray-hint">{hint}</p><div class="thumbs">{"".join(thumbs)}</div></div>')


def notes_html(notes):
    """Friendly list of skipped photos."""
    if not notes:
        return ""
    items = "".join(f"<li>{esc(n)}</li>" for n in notes)
    return (f'<details class="skipped" open><summary>Skipped {plural(len(notes), "photo")} '
            f'(taken out of your tray)</summary><ul>{items}</ul></details>')


# ---- Catch: outcomes ----------------------------------------------------------
def badge_unlock_html(new_ids):
    """Celebrate badges unlocked by this catch."""
    if not new_ids:
        return ""
    unlocked = [b for b in catdex_data.badges() if b["id"] in new_ids]
    stickers = "".join(badge_sticker(b, tilt=(-6, 5, -3, 7)[i % 4]) for i, b in enumerate(unlocked))
    title = "New badge unlocked!" if len(unlocked) == 1 else f"{len(unlocked)} new badges unlocked!"
    return (f'<div class="badge-unlock fx-confetti" data-fx="{uuid.uuid4().hex[:10]}"><h3>{title}</h3><div class="sticker-row">{stickers}</div></div>')


def outcome_new_pending(crops, coat):
    """A new cat was found and there are enough photos: ask for a name."""
    rarity = catdex_data._rarity(coat) if coat else "Common"
    pics = "".join(f'<img src="{thumb_uri(catdex_data.rgb(c), 200)}" alt="">' for c in crops[:4])
    return (f'<div class="outcome new-pending"><h2>A new cat!</h2>'
            f'<p>It looks like a <b>{esc(coat_label(coat).lower())}</b> cat '
            f'<span class="chip rarity-chip rarity-{rarity.lower()}">{stars({"Common": 1, "Uncommon": 2, "Rare": 3}[rarity])} {rarity}</span>. '
            f'Give it a name, then catch it!</p><div class="crop-row">{pics}</div></div>')


def outcome_too_few(n_distinct, dropped):
    """New cat, but not enough different photos. The photos stay in the tray."""
    extra = f" ({plural(dropped, 'photo')} looked almost the same as another, so they count once)" if dropped else ""
    need = NEED - n_distinct
    return (f'<b>Almost there!</b> This looks like a new cat, but I only got '
            f'{plural(n_distinct, "good, different photo")}{esc(extra)}. Add <b>{need} more</b> from a different angle '
            f'(side, face, sitting…) and scan again. Your good photos are still in the tray.')


def outcome_caught(cat, new_badges):
    """The big reveal for a brand-new cat."""
    return (f'<div class="outcome caught fx-confetti" data-fx="{uuid.uuid4().hex[:10]}">'
            f'<div class="reveal"><span class="new-sticker">NEW!</span>'
            f'<span class="sparkles" aria-hidden="true"><i>✦</i><i>✧</i><i>✦</i><i>✧</i><i>✦</i></span>'
            f'<button class="reveal-card" data-cat="{esc(cat["id"])}" aria-label="Open {esc(cat["name"])}\'s profile">'
            f'{card_img(cat)}</button></div>'
            f'<h2>Gotcha! {esc(cat["name"])} was caught!</h2>'
            f'<p>Filed on the <b>{esc(coat_label(cat["coat"]).lower())}</b> page · '
            f'<span class="chip rarity-chip rarity-{cat["rarity"].lower()}">{stars(cat["stars"])} {cat["rarity"]}</span></p>'
            f'<p class="small-print">Tap the card to see {esc(cat["name"])}\'s profile.</p></div>'
            + badge_unlock_html(new_badges))


def outcome_known(cat_id, added, new_badges, picked=False):
    """The smaller "Welcome back!" moment for a cat we've met before."""
    p = catdex_data.profile(cat_id)
    photos = f" · {plural(added, 'new photo')} saved to help recognise them" if added else ""
    head = "Thanks! Sighting saved." if picked else "Welcome back!"
    return (f'<div class="outcome known fx-hearts"><p class="kicker">{head}</p>'
            f'<h2>{esc(p["name"])} again! Sighting #{p["sightings"]}</h2>'
            f'<p class="small-print">{esc(coat_label(p["coat"]))} page{photos}</p>'
            f'<button class="known-card" data-cat="{esc(p["id"])}" aria-label="Open {esc(p["name"])}\'s profile">'
            f'{card_img(p)}</button>{friendship_meter(p["friendship"])}</div>'
            + badge_unlock_html(new_badges))


def outcome_unsure(result):
    """Not sure: show the 3 closest cats as tappable mini-cards."""
    minis = []
    pairs = list(zip(result.get("top3_cats", []), result.get("top3_scores", [])))
    # Only show cats that are reasonably close (always the best one). Similarity scores aren't
    # percentages, so we show a friendly word and the raw score small.
    pairs = [(cat, score) for i, (cat, score) in enumerate(pairs) if i == 0 or score >= SHOW_CANDIDATE_FROM]
    for cat, score in pairs:
        photo = f'<img src="{file_url(cat["thumb_path"])}" alt="">' if cat.get("thumb_path") else PAW
        minis.append(f'<button class="mini-card rarity-{cat["rarity"].lower()}" data-cat="{esc(cat["id"])}">'
                     f'<span class="mini-photo">{photo}</span><b>{esc(cat["name"])}</b>'
                     f'<span class="mini-coat"><i class="swatch swatch-{esc(cat["coat"] or "unsure")}"></i>'
                     f'{esc(coat_label(cat["coat"]))}</span>'
                     f'<span class="mini-score">{closeness(score)} <small>{score:.2f}</small></span></button>')
    question = "Is it this cat?" if len(minis) == 1 else "Is it one of these?"
    return (f'<div class="outcome unsure"><h2>{question}</h2>'
            f'<p>I\'m not quite sure. Tap the cat you saw.</p>'
            f'<div class="mini-row n{len(minis)}">{"".join(minis)}</div>'
            f'<button class="none-btn" data-action="none">{"No" if len(minis) == 1 else "None of these"} – it\'s a new cat!</button></div>')


# ---- Catdex (collection) ------------------------------------------------------
def dex_header_html():
    p = catdex_data.progress()
    pct = round(100 * p["pages_filled"] / p["pages_total"])
    return (f'<div class="dex-header"><div class="dex-numbers"><b>{plural(p["cats"], "cat")} caught</b>'
            f'<span>{p["pages_filled"]} of {p["pages_total"]} coat pages</span></div>'
            f'<div class="progress" role="progressbar" aria-valuenow="{p["pages_filled"]}" aria-valuemin="0" '
            f'aria-valuemax="{p["pages_total"]}"><div class="progress-fill" style="width:{pct}%"></div></div></div>')


def dex_html(search="", sort="newest"):
    searching = bool((search or "").strip())
    sections = []
    for coat, cats in catdex_data.collection(search, sort):
        if searching and not cats:
            continue                       # while searching, only show pages with matching cats
        rarity = catdex_data._rarity(coat) if coat != catdex_data.UNSURE else "Common"
        star_text = "" if coat == catdex_data.UNSURE else f'<span class="page-stars rarity-{rarity.lower()}">{stars({"Common": 1, "Uncommon": 2, "Rare": 3}[rarity])}</span>'
        head = (f'<div class="page-head"><i class="swatch swatch-{esc(coat)}"></i><h3>{esc(coat_label(coat))}</h3>'
                f'{star_text}<span class="page-count">{len(cats) or ""}</span></div>')
        if cats:
            body = '<div class="tile-grid">' + "".join(cat_tile(c) for c in cats) + "</div>"
        else:
            body = f'<div class="page-empty">{SILHOUETTE}<span>Not caught yet</span></div>'
        sections.append(f'<section class="coat-page{"" if cats else " is-empty"}">{head}{body}</section>')
    if not sections:
        sections.append(f'<div class="panel empty-state">{SILHOUETTE}<p><b>No cats called “{esc(search)}”</b>'
                        f'Try another name.</p></div>')
    return f'<div class="dex-pages">{"".join(sections)}</div>'


# ---- Badges & stats -----------------------------------------------------------
def badges_html():
    all_badges = catdex_data.badges()
    got = sum(b["unlocked"] for b in all_badges)
    groups = [("milestone", "Milestones"), ("coat", "Coat pages"), ("special", "Special")]
    parts = [f'<div class="badge-summary"><b>{got} of {len(all_badges)}</b> badges collected</div>']
    for key, title in groups:
        stickers = "".join(badge_sticker(b, tilt=((i * 37) % 11) - 5)
                           for i, b in enumerate(all_badges) if b["group"] == key)
        parts.append(f'<section class="panel badge-group"><h2>{title}</h2><div class="sticker-grid">{stickers}</div></section>')

    s = catdex_data.stats()
    tiles = "".join(f'<div class="stat"><b>{v}</b><span>{label}</span></div>' for v, label in
                    [(s["total_cats"], "cats"), (s["total_sightings"], "sightings"), (s["total_photos"], "photos")])
    highlights = ""
    rarest = s["rarest"]
    only_common = rarest is not None and rarest["stars"] == 1
    for label, cat in [("Most seen", s["most_sighted"]),
                       ("Rarest so far" if only_common else "Rarest catch", rarest)]:
        if cat:
            extra = f'Seen {cat["sightings"]}×' if label == "Most seen" else f'{stars(cat["stars"])} {cat["rarity"]}'
            tip = ('<p class="small-print">Catch an uncommon or rare cat to beat it!</p>'
                   if label == "Rarest so far" else "")
            highlights += f'<div class="highlight"><h3>{label}</h3>{cat_tile(cat, extra=extra)}{tip}</div>'
    counts = [(coat, coat.capitalize(), n) for coat, n in s["coat_counts"].items()]
    if s.get("unsure_count"):                       # cats whose coat Step 2 couldn't decide on
        counts.append((catdex_data.UNSURE, "Not sure yet", s["unsure_count"]))
    top = max([n for _, _, n in counts] + [1])
    bars = "".join(
        f'<div class="bar-row"><span class="bar-label"><i class="swatch swatch-{coat}"></i>{label}</span>'
        f'<span class="bar-track"><span class="bar-fill" style="width:{round(100 * n / top)}%"></span></span>'
        f'<b class="bar-n">{n}</b></div>' for coat, label, n in counts)
    parts.append(f'<section class="panel stats"><h2>Stats</h2><div class="stat-row">{tiles}</div>'
                 f'{"<div class=highlight-row>" + highlights + "</div>" if highlights else ""}'
                 f'<h3 class="bars-title">Cats per coat page</h3><div class="bars">{bars}</div></section>')
    return f'<div class="page badges">{"".join(parts)}</div>'


# ---- Profile ------------------------------------------------------------------
def profile_html(cat_id, selecting=False, picked=()):
    """The profile page. selecting=True: the photo album is in "Select photos" mode (tap photos to
    tick them; `picked` = the ticked photos' file names), to fix a different cat that got mixed in."""
    p = catdex_data.profile(cat_id) if cat_id else None
    back = '<button class="back-btn" data-action="back" aria-label="Go back">‹ Back</button>'
    if not p:
        return f'<div class="page profile">{back}<div class="panel empty-state">{SILHOUETTE}<p><b>This cat isn\'t in the Catdex any more.</b></p></div></div>'
    per = p["personality"]
    traits = "".join(f'<span class="chip trait">{esc(t)}</span>' for t in per["traits"])
    stat_bars = "".join(
        f'<div class="pstat"><span>{esc(name.capitalize())}</span><span class="pstat-track">'
        f'<span class="pstat-fill" style="width:{max(0, min(100, int(v)))}%"></span></span><b>{int(v)}</b></div>'
        for name, v in per["stats"].items())
    fav = f'<p class="fav"><b>Favourite thing:</b> {esc(per["favourite_thing"])}</p>' if per["favourite_thing"] else ""
    vibe = f'<span class="chip vibe-chip">{esc(p["vibe"])}</span>' if p["vibe"] else ""
    written = f'<p class="small-print">Personality written by {esc(p["written_by"])}.</p>' if p["written_by"] else ""

    dates = p["sighting_dates"]
    shown = list(enumerate(dates, start=1))[-8:][::-1]          # newest first, at most 8
    timeline = "".join(f'<li><span class="tl-dot"></span><b>Sighting #{n}</b>'
                       f'<span>{catdex_data.nice_date(d, with_time=True)}</span></li>' for n, d in shown)
    if len(dates) > 8:
        timeline += f'<li class="tl-more">…and {len(dates) - 8} earlier</li>'
    if selecting:                                   # tap = tick / untick (the file name travels in data-index)
        album = "".join(
            f'<button class="album-photo pickable{" picked" if Path(ph).name in picked else ""}" '
            f'data-action="pick" data-index="{esc(Path(ph).name)}" aria-pressed="{str(Path(ph).name in picked).lower()}" '
            f'aria-label="Photo {i + 1}"><img src="{file_url(ph)}" alt="" loading="lazy"><span class="tick">✓</span></button>'
            for i, ph in enumerate(p["photo_paths"]))
        album_tools = '<button class="mini-btn" data-action="select-done">Done</button>'
        album_hint = (f'<p class="album-hint">Tap any photos that <b>aren\'t {esc(p["name"])}</b> – for example a '
                      f'different cat that got mixed in by mistake. Then remove them, or make them a new cat below.</p>')
    else:
        album = "".join(f'<a class="album-photo" href="{file_url(ph)}" target="_blank" rel="noopener">'
                        f'<img src="{file_url(ph)}" alt="Photo {i + 1} of {esc(p["name"])}" loading="lazy"></a>'
                        for i, ph in enumerate(p["photo_paths"]))
        album_tools = ('<button class="mini-btn" data-action="select-photos">Select</button>'
                       if len(p["photo_paths"]) > 1 else "")
        album_hint = ""
    return f"""
<div class="page profile">{back}
  <div class="profile-grid">
    <div class="profile-card">{card_img(p)}</div>
    <div class="profile-info">
      <h1 class="profile-name">{esc(p["name"])}</h1>
      {f'<p class="profile-title">{esc(per["title"])}</p>' if per["title"] else ''}
      <div class="chip-row"><span class="chip coat-chip"><i class="swatch swatch-{esc(p["coat"] or "unsure")}"></i>{esc(coat_label(p["coat"]))}</span>
        <span class="chip rarity-chip rarity-{p["rarity"].lower()}">{stars(p["stars"])} {p["rarity"]}</span>{vibe}</div>
      <div class="chip-row traits">{traits}</div>
      {f'<p class="lore">{esc(per["lore"])}</p>' if per["lore"] else ''}
      {fav}
      <div class="pstats">{stat_bars}</div>
      {written}
    </div>
  </div>
  <section class="panel">
    <h2>Sightings</h2>
    <div class="seen-facts"><div><span>First seen</span><b>{catdex_data.nice_date(p["first_seen"])}</b></div>
      <div><span>Last seen</span><b>{catdex_data.nice_date(p["last_seen"])}</b></div>
      <div><span>Sightings</span><b>{p["sightings"]}</b></div></div>
    {friendship_meter(p["friendship"])}
    <ol class="timeline">{timeline}</ol>
  </section>
  <section class="panel{' selecting' if selecting else ''}"><div class="album-head">
    <h2>Photo album <span class="count">{len(p["photo_paths"])}</span></h2>{album_tools}</div>{album_hint}
    <div class="album">{album or '<p class="small-print">No photos stored.</p>'}</div></section>
</div>"""


# =============================================================================
# 5. The page layout
# =============================================================================
SORTS = [("Newest", "newest"), ("Most seen", "most_seen"), ("A–Z", "az")]

with gr.Blocks(title="Catdex", analytics_enabled=False) as demo:
    # Memory for each visitor (gr.State = per browser tab; gr.BrowserState = remembered by the browser)
    photos = gr.State([])            # photos waiting to be scanned: [{"path", "name", "thumb"}, ...]
    scan = gr.State(None)            # what the last scan found, while we wait for the user's answer
    profile_id = gr.State(None)      # which cat the profile page is showing
    came_from = gr.State("dex")      # which tab "Back" on the profile returns to
    selecting = gr.State(False)      # profile photo album in "Select photos" mode?
    picked = gr.State([])            # the ticked photos' file names
    tips_hidden = gr.BrowserState(False, storage_key="catdex_tips_hidden")

    gr.HTML(f'<header class="app-header"><span class="logo">{PAW}</span><span class="wordmark">Catdex</span></header>',
            elem_id="app-header", apply_default_css=False)

    # The four tabs. catdex_ui.css turns their buttons into a bottom tab bar on phones.
    with gr.Tabs(selected="home", elem_id="catdex-nav", overflow_behavior="wrap") as tabs:

        with gr.Tab("Home", id="home", elem_id="tab-home"):
            home_view = gr.HTML(home_html(), js_on_load=CLICK_JS, apply_default_css=False, elem_id="home-view")

        with gr.Tab("Catch", id="catch", elem_id="tab-catch"):
            with gr.Row(elem_id="catch-layout", equal_height=False):
                with gr.Column(scale=1, min_width=300, elem_id="catch-left"):
                    catch_top = gr.HTML(catch_top_html(), apply_default_css=False, elem_id="catch-top")
                    with gr.Column(elem_id="picker", elem_classes="panel-like") as picker:
                        with gr.Row(elem_id="add-buttons"):
                            camera_btn = gr.Button("📸 Take photos", elem_id="camera-btn", size="lg")
                            gallery_btn = gr.UploadButton("🖼️ Choose from gallery", file_count="multiple",
                                                          file_types=["image"], elem_id="gallery-btn", size="lg")
                        camera = gr.Image(sources=["webcam"], type="filepath", label="Camera", show_label=False, visible=False,
                                          elem_id="cat-cam", height=320,
                                          # start with the BACK camera on phones (catdex_ui.js adds the 🔄 switch)
                                          webcam_options=gr.WebcamOptions(mirror=False, constraints={"video": {
                                              "facingMode": {"ideal": "environment"},
                                              "width": {"ideal": 1920}, "height": {"ideal": 1440}}}))
                        tray = gr.HTML(tray_html([]), js_on_load=CLICK_JS, apply_default_css=False, elem_id="tray")
                        scan_btn = gr.Button("Scan photos", variant="primary", size="lg", elem_id="scan-btn")
                        clear_btn = gr.Button("Clear photos", size="sm", elem_id="clear-btn")
                with gr.Column(scale=1, min_width=300, elem_id="catch-right"):
                    result = gr.HTML(RESULT_PLACEHOLDER, js_on_load=CLICK_JS, apply_default_css=False, elem_id="result")
                    with gr.Row(visible=False, elem_id="name-row") as name_row:
                        name_box = gr.Textbox(label="Name", placeholder="e.g. Biscuit", max_lines=1,
                                              max_length=30, scale=3, elem_id="name-box")
                        catch_btn = gr.Button("Catch!", variant="primary", scale=1, min_width=110,
                                              elem_id="catch-btn", size="lg")
                    with gr.Row(visible=False, elem_id="after-row") as after_row:
                        again_btn = gr.Button("Catch another cat", variant="primary", elem_id="again-btn")
                        to_dex_btn = gr.Button("Open my Catdex", elem_id="to-dex-btn")

        with gr.Tab("Catdex", id="dex", elem_id="tab-dex"):
            dex_header = gr.HTML(dex_header_html(), apply_default_css=False, elem_id="dex-header")
            with gr.Row(elem_id="dex-controls"):
                search = gr.Textbox(placeholder="Search by name", show_label=False, max_lines=1,
                                    elem_id="dex-search", scale=2, min_width=180)
                sort = gr.Radio(SORTS, value="newest", show_label=False, elem_id="dex-sort",
                                scale=3, min_width=240)
            dex_view = gr.HTML(dex_html(), js_on_load=CLICK_JS, apply_default_css=False, elem_id="dex-view")

        with gr.Tab("Badges", id="badges", elem_id="tab-badges"):
            badges_view = gr.HTML(badges_html(), js_on_load=CLICK_JS, apply_default_css=False, elem_id="badges-view")
            with gr.Accordion("Settings", open=False, elem_id="settings"):
                gr.Markdown("**Reset for demo** – empties the Catdex. The old one is kept as a backup folder "
                            "next to it. Type **RESET** to confirm.")
                with gr.Row():
                    reset_box = gr.Textbox(placeholder="Type RESET", show_label=False, max_lines=1, scale=2,
                                           elem_id="reset-box")
                    reset_btn = gr.Button("Reset Catdex", variant="stop", scale=1, elem_id="reset-btn")

        # The profile page: a normal tab whose button is hidden (catdex_ui.css), opened by Python
        with gr.Tab("Profile", id="profile", elem_id="tab-profile"):
            profile_view = gr.HTML(profile_html(None), js_on_load=CLICK_JS, apply_default_css=False,
                                   elem_id="profile-view")
            # Fixing a mix-up: shown while photos are being selected in the album
            with gr.Column(visible=False, elem_id="photo-tools", elem_classes="panel-like") as photo_tools:
                tools_info = gr.Markdown("Tap photos to select them.", elem_id="tools-info")
                remove_pics_btn = gr.Button("🗑️ Remove selected photos", variant="stop", elem_id="remove-pics-btn")
                with gr.Row(visible=False, elem_id="pics-confirm-row") as pics_confirm_row:
                    pics_confirm_text = gr.Markdown(elem_id="pics-confirm-text")
                    pics_yes = gr.Button("Yes, remove", variant="stop", elem_id="pics-yes")
                    pics_no = gr.Button("Keep", elem_id="pics-no")
                gr.Markdown(f"**Or make them a new cat** – if they show a different cat that got merged in "
                            f"by mistake (needs {catdex_data.MIN_ENROL_PHOTOS}+ different photos).",
                            elem_id="split-help")
                with gr.Row(elem_id="split-row"):
                    split_name = gr.Textbox(label="New cat's name", placeholder="e.g. Mochi", max_lines=1,
                                            max_length=30, scale=3, elem_id="split-name")
                    split_btn = gr.Button("🐱 Make new cat", variant="primary", scale=2, min_width=150,
                                          elem_id="split-btn")
            with gr.Accordion("Manage this cat", open=False, elem_id="manage"):
                with gr.Row():
                    rename_box = gr.Textbox(label="New name", max_lines=1, max_length=30, scale=3,
                                            elem_id="rename-box")
                    rename_btn = gr.Button("Rename", scale=1, min_width=110, elem_id="rename-btn")
                remake_btn = gr.Button("✨ Remake card (new personality)", elem_id="remake-btn")
                remove_btn = gr.Button("Remove this cat", variant="stop", elem_id="remove-btn")
                with gr.Row(visible=False, elem_id="confirm-row") as confirm_row:
                    confirm_text = gr.Markdown(elem_id="confirm-text")
                    confirm_yes = gr.Button("Yes, remove", variant="stop", elem_id="confirm-yes")
                    confirm_no = gr.Button("Keep", elem_id="confirm-no")


# =============================================================================
# 6. What happens when you tap things
#    Each function returns a dictionary {component: new value}; anything left out stays as it is.
# =============================================================================
def refresh_pages(search_text, sort_by, hidden):
    """Redraw Home, Catdex and Badges from the database (after anything changes)."""
    return {home_view: home_html(hidden), dex_header: dex_header_html(),
            dex_view: dex_html(search_text, sort_by), badges_view: badges_html()}


def open_profile(cat_id, origin):
    """Switch to the profile page for one cat."""
    cat = catdex_data.get_cat(cat_id)
    return {tabs: gr.Tabs(selected="profile"), profile_view: profile_html(cat_id), profile_id: cat_id,
            came_from: origin, rename_box: cat["name"] if cat else "", confirm_row: gr.Row(visible=False),
            **photo_mode(cat_id, False, [])}


def photo_mode(cat_id, on, chosen):
    """Switch the album's "Select photos" mode on or off, and show the tools to match."""
    n = len(chosen)
    info = (f"**{n} photo{'s' if n != 1 else ''} selected.**" if n
            else "Tap the photos that **aren't** this cat.")
    return {selecting: on, picked: list(chosen), profile_view: profile_html(cat_id, on, chosen),
            photo_tools: gr.Column(visible=on), tools_info: info, pics_confirm_row: gr.Row(visible=False)}


# ---- Home ---------------------------------------------------------------------
def home_click(hidden, evt: gr.EventData):
    if evt.action == "catch":
        return {tabs: gr.Tabs(selected="catch")}
    if evt.action == "hide-tips":
        return {tips_hidden: True, home_view: home_html(True)}
    if evt.cat_id:
        return open_profile(evt.cat_id, "home")
    return {home_view: gr.skip()}


# ---- Catch: adding and removing photos ----------------------------------------
def add_photos(files, current):
    """Photos from the gallery or the camera go into the tray (prepared: upright, resized)."""
    current = list(current or [])
    files = files if isinstance(files, list) else [files] if files else []
    for f in files:
        if len(current) >= MAX_TRAY:
            gr.Info(f"That's plenty! At most {MAX_TRAY} photos per scan.")
            break
        path, name = catdex_data.prepare_upload(f)
        current.append({"path": path, "name": name, "thumb": thumb_uri(path) if path else ""})
    return current, tray_html(current)


def camera_snap(picture, current):
    """One photo from the camera: add it, then empty the camera so the next photo can be taken."""
    if not picture:
        return gr.skip(), gr.skip(), gr.skip()
    current, tray = add_photos([picture], current)
    return current, tray, None


def tray_click(current, evt: gr.EventData):
    """The × on a thumbnail removes that photo."""
    current = list(current or [])
    if evt.action == "remove" and evt.index.isdigit() and int(evt.index) < len(current):
        current.pop(int(evt.index))
    return current, tray_html(current)


def clear_photos():
    return [], tray_html([]), catch_top_html()


# ---- Catch: scanning ----------------------------------------------------------
def catch_screen(ball="idle", lines=(), message="", outcome="", ask_name=False, done=False, picker_on=True):
    """Everything on the Catch page at once, so every step can redraw it the same way.
    A message with no outcome (e.g. "add more photos") is shown under the ball on phones, and in the
    result area on laptops (CSS picks which one), so it's always right next to where you look."""
    if message and not outcome:
        outcome = f'<div class="result-note">{message}</div>'
        message = f'<div class="phone-only">{message}</div>'
    return {catch_top: catch_top_html(ball, lines, message), result: outcome or RESULT_PLACEHOLDER,
            name_row: gr.Row(visible=ask_name), after_row: gr.Row(visible=done), picker: gr.Column(visible=picker_on)}


def new_cat_screen(info, lines):
    """Next step for a new cat: ask for a name - or ask for more photos if there aren't enough."""
    if len(info["crops"]) < NEED:
        return {**catch_screen("idle", lines, outcome_too_few(len(info["crops"]), info["dropped"])
                               + notes_html(info["notes"])), scan: None}
    info["stage"] = "name"
    return {**catch_screen("open", lines, notes_html(info["notes"]), outcome_new_pending(info["crops"], info["coat"]),
                           ask_name=True, picker_on=False), scan: info, name_box: ""}


def scan_photos(current):
    """The Scan button. A generator: each `yield` updates the page, so the progress lines appear
    one by one while the AI works."""
    usable = [p for p in current or [] if p.get("path")]
    if not usable:
        yield catch_screen(message="Add at least one photo first.")
        return
    lines = ["Sniffing out the cat…"]
    yield catch_screen("scanning", lines, picker_on=False)

    # Step 1, one photo at a time, so we know exactly which photos were usable
    crops, notes, kept = [], [], []
    for p in current:
        found_crops, found_notes = catdex_data.find_cats([(p["path"], p["name"])])
        notes += found_notes
        if found_crops:
            crops.append(found_crops[0])
            kept.append({**p, "checked": True})
    # Burst shots count once: near-duplicates leave the tray too
    keep = catdex_data.step3.distinct_photos(crops)
    dropped = len(crops) - len(keep)
    if dropped:
        notes.append(f"{plural(dropped, 'photo')} looked almost the same as another, so counted once")
    crops, kept = [crops[i] for i in keep], [kept[i] for i in keep]
    tray_now = {photos: kept, tray: tray_html(kept)}      # skipped photos are taken out of the tray
    if not crops:
        yield {**catch_screen("idle", lines, "<b>No cat found in these photos.</b> Try a clearer photo of one cat."
                              + notes_html(notes)), **tray_now}
        return

    lines.append("Checking its coat…")
    yield catch_screen("scanning", lines, picker_on=False)
    time.sleep(STEP_PAUSE)
    coat = catdex_data.coat_vote(crops)                                               # Step 2

    lines.append("Flipping through the Catdex…")
    yield catch_screen("scanning", lines, picker_on=False)
    time.sleep(STEP_PAUSE)
    found = catdex_data.identify(crops)                                               # Step 3
    info = {"crops": crops, "fps": found["fingerprints"], "coat": coat, "notes": notes, "dropped": dropped}

    if found["decision"] == "known":
        before = catdex_data.unlocked_ids()
        added = catdex_data.record_sighting(found["cat_id"], crops, found["fingerprints"])["added_photos"]
        new_badges = catdex_data.unlocked_ids() - before
        yield {**catch_screen("open", lines, notes_html(notes),
                              outcome_known(found["cat_id"], added, new_badges), done=True, picker_on=False),
               photos: [], tray: tray_html([]), scan: None}
    elif found["decision"] == "ask":
        info["stage"] = "ask"
        yield {**catch_screen("idle", lines, notes_html(notes), outcome_unsure(found), picker_on=False),
               scan: info, **tray_now}
    else:
        yield {**new_cat_screen(info, lines), **tray_now}


def catch_new_cat(name, info):
    """The Catch! button: add the new cat, write its card, then the big reveal."""
    if not info or info.get("stage") != "name":
        yield catch_screen(message="Scan some photos first.")
        return
    lines = ["Writing its trading card…"]
    yield catch_screen("scanning", lines, picker_on=False)
    before = catdex_data.unlocked_ids()
    try:
        cat_id = catdex_data.catch_new(info["crops"], info["fps"], name, info["coat"])   # Steps 3 + 4
    except ValueError:                       # not enough different photos after all
        yield {**catch_screen("idle", [], outcome_too_few(len(info["crops"]), info["dropped"])), scan: None}
        return
    new_badges = catdex_data.unlocked_ids() - before
    yield {**catch_screen("open", ["Caught!"], "", outcome_caught(catdex_data.get_cat(cat_id), new_badges),
                          done=True, picker_on=False),
           photos: [], tray: tray_html([]), scan: None, name_box: ""}


def result_click(info, evt: gr.EventData):
    """Taps inside the result: pick one of the top 3, say "none of these", or open a cat's profile."""
    if info and info.get("stage") == "ask":
        if evt.action == "none":
            return new_cat_screen(info, ["Okay, a new cat then!"])
        if evt.cat_id:
            before = catdex_data.unlocked_ids()
            added = catdex_data.record_sighting(evt.cat_id, info["crops"], info["fps"])["added_photos"]
            new_badges = catdex_data.unlocked_ids() - before
            return {**catch_screen("open", ["Picked from the top matches"], "",
                                   outcome_known(evt.cat_id, added, new_badges, picked=True),
                                   done=True, picker_on=False),
                    photos: [], tray: tray_html([]), scan: None}
    if evt.cat_id:
        return open_profile(evt.cat_id, "catch")
    return {result: gr.skip()}


def start_again():
    """'Catch another cat': back to an empty Catch page."""
    return {**catch_screen(), photos: [], tray: tray_html([]), scan: None}


# ---- Catdex / Badges / Profile --------------------------------------------------
def list_click(origin):
    """Tapping a cat tile on the Catdex or Badges page opens its profile."""
    def handler(evt: gr.EventData):
        return open_profile(evt.cat_id, origin) if evt.cat_id else {tabs: gr.skip()}
    return handler


def profile_click(origin, cat_id, on, chosen, evt: gr.EventData):
    """Taps on the profile page: Back, and the photo album's Select / Done / tap-a-photo."""
    if evt.action == "back":
        return {tabs: gr.Tabs(selected=origin or "dex"), **photo_mode(cat_id, False, [])}
    if evt.action == "select-photos":
        return photo_mode(cat_id, True, [])
    if evt.action == "select-done":
        return photo_mode(cat_id, False, [])
    if evt.action == "pick" and on:
        chosen = list(chosen or [])
        if evt.index in chosen:              # tapped again: untick it
            chosen.remove(evt.index)
        else:
            chosen.append(evt.index)
        return photo_mode(cat_id, True, chosen)
    return {tabs: gr.skip()}


def ask_remove_photos(cat_id, chosen):
    cat = catdex_data.get_cat(cat_id) if cat_id else None
    if not cat or not chosen:
        gr.Warning("Select at least one photo first.")
        return {pics_confirm_row: gr.skip()}
    if len(chosen) >= cat["photo_count"]:
        gr.Warning("A cat needs at least one photo. To remove all of them, remove the whole cat instead.")
        return {pics_confirm_row: gr.skip()}
    one = len(chosen) == 1
    return {pics_confirm_row: gr.Row(visible=True),
            pics_confirm_text: f"Remove **{len(chosen)} photo{'' if one else 's'}** from **{cat['name']}**? "
                               f"Catdex stops using {'it' if one else 'them'} to recognise this cat."}


def remove_photos(cat_id, chosen):
    """Take the ticked photos (and their fingerprints) out of this cat."""
    try:
        done = catdex_data.remove_photos(cat_id, chosen)
    except ValueError as e:
        gr.Warning(str(e))
        return {pics_confirm_row: gr.Row(visible=False)}
    undone = (f" {done['sightings_undone']} sighting{'s' if done['sightings_undone'] != 1 else ''} "
              f"that only brought in those photos {'were' if done['sightings_undone'] != 1 else 'was'} undone too."
              if done["sightings_undone"] else "")
    gr.Info(f"Removed {done['removed']} photo{'s' if done['removed'] != 1 else ''}.{undone}", duration=6)
    return photo_mode(cat_id, False, [])


def split_photos(cat_id, chosen, new_name):
    """Make the ticked photos into a cat of their own (a different cat that got merged in)."""
    if not chosen:
        gr.Warning("Select the photos of the other cat first.")
        return {photo_tools: gr.skip()}
    try:
        new_id = catdex_data.move_photos_to_new_cat(cat_id, chosen, new_name)    # also writes its card
    except ValueError as e:
        gr.Warning(str(e))
        return {photo_tools: gr.skip()}
    cat = catdex_data.get_cat(new_id)
    gr.Info(f"{cat['name']} is now a cat of their own, on the {coat_label(cat['coat'])} page!", duration=6)
    return {**open_profile(new_id, "dex"), split_name: ""}


def rename_cat(cat_id, new_name):
    if not cat_id or not catdex_data.get_cat(cat_id):
        return {profile_view: gr.skip()}
    catdex_data.rename(cat_id, new_name)
    cat = catdex_data.get_cat(cat_id)
    gr.Info(f"Renamed to {cat['name']} – card redrawn.", duration=4)
    return {**photo_mode(cat_id, False, []), rename_box: cat["name"]}


def remake_card(cat_id):
    if not cat_id or not catdex_data.get_cat(cat_id):
        return {profile_view: gr.skip()}
    catdex_data.remake_card(cat_id)
    gr.Info("New personality, new card!", duration=4)
    return photo_mode(cat_id, False, [])


def ask_remove(cat_id):
    cat = catdex_data.get_cat(cat_id) if cat_id else None
    if not cat:
        return {confirm_row: gr.skip()}
    return {confirm_row: gr.Row(visible=True),
            confirm_text: f"Remove **{cat['name']}** and all their photos? This can't be undone."}


def remove_cat(cat_id):
    if cat_id and catdex_data.get_cat(cat_id):
        name = catdex_data.remove(cat_id)
        gr.Info(f"{name} was removed from the Catdex.", duration=4)
    return {confirm_row: gr.Row(visible=False), tabs: gr.Tabs(selected="dex"), profile_id: None}


def reset_catdex(typed):
    if (typed or "").strip() != "RESET":
        gr.Warning("Type RESET (in capitals) to confirm.")
        return {reset_box: gr.skip()}
    backup = catdex_data.reset()
    gr.Info(f"The Catdex is empty again. The old one was saved in the folder {Path(backup).name}."
            if backup else "The Catdex was already empty.", duration=6)
    return {reset_box: "", tips_hidden: False, profile_id: None, **start_again()}


# =============================================================================
# 7. Connecting buttons to functions
# =============================================================================
# Anything that changes the database shares one queue ("catdex_db"), so two scans can never
# write at the same time. show_progress="hidden": we draw our own progress (the wobbling ball).
DB = dict(concurrency_id="catdex_db", concurrency_limit=1, show_progress="hidden")
QUIET = dict(show_progress="hidden")

with demo:
    page_inputs = [search, sort, tips_hidden]
    page_outputs = [home_view, dex_header, dex_view, badges_view]
    profile_outputs = [tabs, profile_view, profile_id, came_from, rename_box, confirm_row,
                       selecting, picked, photo_tools, tools_info, pics_confirm_row]
    catch_outputs = [catch_top, result, name_row, after_row, picker, photos, tray, scan, name_box]

    demo.load(refresh_pages, page_inputs, page_outputs, **QUIET)

    # Home
    home_view.click(home_click, [tips_hidden], [tabs, home_view, tips_hidden] + profile_outputs, **QUIET)

    # Catch: photos in and out
    camera_btn.click(lambda: gr.Image(visible=True), None, camera, **QUIET)
    gallery_btn.upload(add_photos, [gallery_btn, photos], [photos, tray], **QUIET)
    camera.input(camera_snap, [camera, photos], [photos, tray, camera], **QUIET)
    tray.click(tray_click, [photos], [photos, tray], **QUIET)
    clear_btn.click(clear_photos, None, [photos, tray, catch_top], **QUIET)

    # Catch: scan -> answer -> catch
    scan_btn.click(scan_photos, [photos], catch_outputs, **DB).then(refresh_pages, page_inputs, page_outputs, **QUIET)
    for event in (catch_btn.click, name_box.submit):                     # button or Enter key
        event(catch_new_cat, [name_box, scan], catch_outputs, **DB).then(
            refresh_pages, page_inputs, page_outputs, **QUIET)
    result.click(result_click, [scan], catch_outputs + profile_outputs, **DB).then(
        refresh_pages, page_inputs, page_outputs, **QUIET)
    again_btn.click(start_again, None, catch_outputs, **QUIET)
    to_dex_btn.click(lambda: gr.Tabs(selected="dex"), None, tabs, **QUIET)

    # Catdex: search and sort redraw the list
    search.change(dex_html, [search, sort], dex_view, **QUIET)
    sort.change(dex_html, [search, sort], dex_view, **QUIET)
    dex_view.click(list_click("dex"), None, profile_outputs, **QUIET)
    badges_view.click(list_click("badges"), None, profile_outputs, **QUIET)

    # Profile
    profile_view.click(profile_click, [came_from, profile_id, selecting, picked], profile_outputs, **QUIET)

    # Profile: fixing a mix-up (remove photos, or make them a new cat)
    remove_pics_btn.click(ask_remove_photos, [profile_id, picked], [pics_confirm_row, pics_confirm_text], **QUIET)
    pics_no.click(lambda: gr.Row(visible=False), None, pics_confirm_row, **QUIET)
    pics_yes.click(remove_photos, [profile_id, picked], profile_outputs, **DB).then(
        refresh_pages, page_inputs, page_outputs, **QUIET)
    for event in (split_btn.click, split_name.submit):
        event(split_photos, [profile_id, picked, split_name], profile_outputs + [split_name], **DB).then(
            refresh_pages, page_inputs, page_outputs, **QUIET)
    rename_btn.click(rename_cat, [profile_id, rename_box], profile_outputs, **DB).then(
        refresh_pages, page_inputs, page_outputs, **QUIET)
    rename_box.submit(rename_cat, [profile_id, rename_box], profile_outputs, **DB).then(
        refresh_pages, page_inputs, page_outputs, **QUIET)
    remake_btn.click(remake_card, [profile_id], profile_outputs,
                     concurrency_id="catdex_db", concurrency_limit=1).then(
        refresh_pages, page_inputs, page_outputs, **QUIET)
    remove_btn.click(ask_remove, [profile_id], [confirm_row, confirm_text], **QUIET)
    confirm_no.click(lambda: gr.Row(visible=False), None, confirm_row, **QUIET)
    confirm_yes.click(remove_cat, [profile_id], [confirm_row, tabs, profile_id], **DB).then(
        refresh_pages, page_inputs, page_outputs, **QUIET)

    # Settings: reset for demo
    reset_btn.click(reset_catdex, [reset_box], [reset_box, tips_hidden, profile_id] + catch_outputs, **DB).then(
        refresh_pages, page_inputs, page_outputs, **QUIET)


# =============================================================================
# 8. Start the app
# =============================================================================
if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1)            # one scan at a time: keeps the database safe
    demo.launch(
        share=SHARE,                                   # port: 7860, or set GRADIO_SERVER_PORT
        allowed_paths=[catdex_data.DB_DIR],            # lets the page show cards and photos from catdex_db
        theme=THEME, css_paths=[CSS_FILE], js=JS_FILE.read_text(encoding="utf-8"), head=HEAD,
        footer_links=[],                               # hide the "Built with Gradio" footer
    )