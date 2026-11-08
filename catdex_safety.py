"""
Catdex - safety checks (guardrails) for the parts people and the AI can write.

  1. Names    - cat names are typed by users and shown on cards (and sent to Claude), so rude names
                and "instructions to the AI" hidden in a name are blocked before anything is saved.
  2. Cards    - every personality Claude writes is checked BEFORE it goes on a card. Anything that
                mentions health, injury or sterilisation (which Claude can't know from a photo),
                rude words, or AI/instruction talk is rejected, and the app uses its built-in
                template card instead.
  3. A log    - every blocked name or card is written to catdex_db/safety_log.jsonl, so you can
                see how often the guardrails fire (shown on the Badges page).

Edit the word lists below to tune what's blocked. No AI is used here on purpose: simple, predictable
rules are easy to test and explain.
"""

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

SAFETY_LOG = Path(__file__).resolve().parent / "catdex_db" / "safety_log.jsonl"

# ---- 1. Word lists you can edit ----------------------------------------------------
# Rude words, blocked as WHOLE words only (so "Cassie" or "Scunthorpe" are fine).
RUDE_WORDS = {
    "ass", "arse", "asshole", "bastard", "bitch", "bollocks", "bullshit", "crap", "cock", "cunt", "dick",
    "damn", "douche", "fag", "fuck", "fucker", "fucking", "idiot", "moron", "piss", "prick", "pussy",
    "retard", "shit", "shitty", "slut", "stupid", "twat", "wanker", "whore", "dumbass", "jackass",
    "hitler", "nazi", "porn", "sex", "sexy", "kill", "rape",
    # Singlish / local vulgarities
    "knn", "cb", "cheebai", "chibai", "lanjiao", "nabei", "kanina", "pukimak", "babi",
}
# Rude words that are blocked even INSIDE another word (they practically never appear in real names)
RUDE_INSIDE = ["fuck", "nigg", "fagg", "shithead", "motherf"]

# Names that try to give the AI instructions ("prompt injection")
INSTRUCTION_PATTERNS = [
    r"\bignore\b.*\b(instruction|rule|prompt|previous|above)",
    r"\b(disregard|forget)\b.*\b(instruction|rule|prompt)",
    r"\bsystem prompt\b", r"\byou are now\b", r"\bact as\b", r"\bpretend to be\b",
    r"\bnew instructions?\b", r"\bjailbreak\b",
]

# Things a card must NOT say. Claude only sees one photo, so it can't know any of this - and a wrong
# claim could mislead caretakers (e.g. "she's pregnant", "he's been neutered") or upset people.
HEALTH_WORDS = [
    r"sick\w*", r"ill", r"illness\w*", r"injur\w*", r"wound\w*", r"hurt\w*", r"diseas\w*", r"infect\w*",
    r"virus\w*", r"parasit\w*", r"fleas?", r"mange", r"rabies", r"fiv", r"felv", r"tumou?rs?",
    r"cancer\w*", r"pregnan\w*", r"starv\w*", r"malnourish\w*", r"emaciat\w*", r"underweight",
    r"skinny", r"limp\w*", r"blind", r"deaf", r"dying", r"dead", r"death", r"die[sd]?", r"abus\w*",
    r"neglect\w*", r"vets?", r"veterinar\w*", r"medic\w*", r"surger\w*", r"disabl\w*",
    # other ways of saying "pregnant" (found by redteam_cards.py)
    r"(have|having|expecting|due|carrying) (her |some |a few )?(kittens|a litter)", r"kittens (soon|on the way)",
    r"litters?", r"expectant", r"expecting queen", r"(mum|mom|mother)[- ]to[- ]be", r"in the family way",
]
TNR_WORDS = [r"neuter\w*", r"spay\w*", r"steril\w*", r"ear[- ]?tip\w*", r"castrat\w*"]
AI_TALK = [r"as an ai", r"language model", r"instructions?", r"system prompt", r"i cannot", r"i can't",
           r"i'm sorry", r"i am sorry", r"i'm unable", r"ignore (all|previous|the|your)"]


# ---- 2. Helpers ----------------------------------------------------------------------
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})


def _plain(text):
    """Lowercase, accents removed, look-alike digits/symbols turned into letters ("sh1t" -> "shit")."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return text.lower().translate(LEET)


def _find(patterns, text):
    """The first pattern (a whole word or phrase) found in the text, or None."""
    for pattern in patterns:
        match = re.search(rf"\b(?:{pattern})\b", text)
        if match:
            return match.group(0)
    return None


def log_event(kind, **details):
    """Write one line to the safety log (never crashes the app if it can't)."""
    try:
        SAFETY_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, **details}
        with SAFETY_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def summary():
    """How often the guardrails have fired: {"names_blocked": n, "cards_replaced": n}."""
    counts = {"names_blocked": 0, "cards_replaced": 0}
    if SAFETY_LOG.exists():
        for line in SAFETY_LOG.read_text(encoding="utf-8").splitlines():
            try:
                kind = json.loads(line).get("kind")
            except json.JSONDecodeError:
                continue
            if kind == "name_blocked":
                counts["names_blocked"] += 1
            elif kind == "card_blocked":
                counts["cards_replaced"] += 1
    return counts


# ---- 3. The checks -----------------------------------------------------------------
class BlockedName(ValueError):
    """Raised when a cat name isn't allowed. The message is friendly enough to show to the user."""


def name_problem(name):
    """Why this cat name isn't allowed (a friendly message), or None if it's fine."""
    plain = _plain(name)
    words = set(re.findall(r"[a-z]+", plain))
    squashed = re.sub(r"[^a-z]", "", plain)                   # "f.u.c.k" -> "fuck"
    if words & RUDE_WORDS or any(bad in squashed for bad in RUDE_INSIDE):
        return "That name isn't allowed – please choose a kind name for this cat."
    if any(re.search(p, plain) for p in INSTRUCTION_PATTERNS):
        return "Names can't contain instructions to the AI – please use a normal cat name."
    hidden = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")   # text-direction tricks
    if any(unicodedata.category(ch) == "Cc" or ch in hidden for ch in str(name or "")):
        return "That name has hidden characters in it – please type it again."
    return None


def check_name(name, where="name"):
    """Raise BlockedName (and log it) if the name isn't allowed. Returns the name if it's fine."""
    problem = name_problem(name)
    if problem:
        log_event("name_blocked", where=where, name=str(name)[:60], reason=problem)
        raise BlockedName(problem)
    return name


def card_problem(personality):
    """Why this card text isn't allowed (a short reason), or None if it's fine.
    personality: the dict Claude returned (title, traits, lore, favourite_thing)."""
    parts = [personality.get("title", ""), personality.get("lore", ""), personality.get("favourite_thing", "")]
    parts += list(personality.get("traits", []))
    text = _plain(" ".join(str(p) for p in parts))
    for label, patterns in [("mentions health or injury", HEALTH_WORDS),
                            ("claims to know if the cat is sterilised", TNR_WORDS),
                            ("talks about AI or instructions", AI_TALK)]:
        found = _find(patterns, text)
        if found:
            return f"{label} ('{found}')"
    rude = set(re.findall(r"[a-z]+", text)) & RUDE_WORDS
    if rude or any(bad in re.sub(r"[^a-z]", "", text) for bad in RUDE_INSIDE):
        return "contains a rude word"
    return None