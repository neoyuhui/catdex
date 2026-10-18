"""
Why is the Claude call slow? Times 4 kinds of call so we can tell network, schema, image and
retries apart. Doesn't touch your Catdex. Costs well under one US cent in total.

Run (from your project folder, where your .env file is):
  python claude_speed.py                      -> without a photo
  python claude_speed.py some_cat_photo.jpg   -> also time the real "photo + card" request
"""

import base64
import io
import os
import sys
import time
from pathlib import Path

import anthropic

MODEL = "claude-haiku-4-5-20251001"                   # same model as catdex_step4.py


# ---- The API key, read the same way catdex_step4.py does ---------------------------
def load_key():
    env = Path(__file__).resolve().parent / ".env"
    if env.exists():
        raw = env.read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "ignore")
        for line in text.splitlines():
            name, sep, value = line.partition("=")
            name = name.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'").strip()
            if sep and value and name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
                os.environ[name] = value
    base = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    if base.endswith("/v1"):                          # the SDK adds /v1/messages itself
        os.environ["ANTHROPIC_BASE_URL"] = base[:-3]
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("No ANTHROPIC_API_KEY found (check with: python catdex_step4.py --check-key)")


# A copy of the card schema in catdex_step4.py (same shape, so the same compiled grammar is reused)
STATS = ["stealth", "zoomies", "charm", "chill", "mischief"]
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "traits": {"type": "array", "items": {"type": "string"}},
        "stats": {"type": "object", "properties": {s: {"type": "integer"} for s in STATS},
                  "required": STATS, "additionalProperties": False},
        "lore": {"type": "string"},
        "favourite_thing": {"type": "string"},
    },
    "required": ["title", "traits", "stats", "lore", "favourite_thing"],
    "additionalProperties": False,
}
CARD_PROMPT = ("You write playful trading cards for Catdex. This cat is called Speedy, its coat page is 'tabby' "
               "and its vibe is 'Curious Explorer'. Write its card: a 3-6 word title, exactly 3 traits, stats "
               "1-99, 1-2 sentences of lore, and a favourite thing. Keep it kind and family-friendly.")


def image_block(path, max_side):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}}


def timed(label, **request):
    """Run one request with NO automatic retries, so a slow or failed attempt shows up honestly."""
    start = time.perf_counter()
    try:
        r = client.messages.create(model=MODEL, **request)
        took = time.perf_counter() - start
        print(f"{label:<44}{took:>6.1f}s   in {r.usage.input_tokens:>5} / out {r.usage.output_tokens:>4} tokens"
              f"   {r.stop_reason}")
        return took
    except anthropic.APIStatusError as e:
        print(f"{label:<44}{time.perf_counter() - start:>6.1f}s   FAILED: HTTP {e.status_code} {type(e).__name__}"
              f" (request id {getattr(e, 'request_id', '?')})")
    except anthropic.APIConnectionError as e:
        print(f"{label:<44}{time.perf_counter() - start:>6.1f}s   FAILED: can't reach the API ({type(e).__name__})")
    return None


load_key()
start = time.perf_counter()
client = anthropic.Anthropic(max_retries=0, timeout=60)
print(f"anthropic SDK {anthropic.__version__}, model {MODEL}  (client set up in {time.perf_counter() - start:.2f}s)")
print(f"Sending requests to: {client.base_url}\n")

schema = {"format": {"type": "json_schema", "schema": SCHEMA}}
results = {
    "plain": timed("1. Tiny text reply (network + model)", max_tokens=10,
                   messages=[{"role": "user", "content": "Say hi in one word."}]),
    "schema1": timed("2. Card text with the JSON schema", max_tokens=600, output_config=schema,
                     messages=[{"role": "user", "content": CARD_PROMPT}]),
    "schema2": timed("3. Same again (schema now cached)", max_tokens=600, output_config=schema,
                     messages=[{"role": "user", "content": CARD_PROMPT}]),
}
if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
    for side in (768, 512):
        results[f"img{side}"] = timed(f"4. Photo ({side}px) + schema - the real request", max_tokens=600,
                                      output_config=schema, messages=[{"role": "user", "content": [
                                          image_block(sys.argv[1], side), {"type": "text", "text": CARD_PROMPT}]}])

# ---- What the numbers mean -----------------------------------------------------------
print("\nWhat this suggests:")
plain, s1, s2 = results["plain"], results["schema1"], results["schema2"]
if plain is None:
    print("- The simplest call failed: it's the connection, the key or the API itself, not Catdex.")
elif plain > 5:
    print("- Even a one-word reply is slow: likely your network (VPN, school/office Wi-Fi filter, proxy)"
          " or the API being busy. Try another network, and check status.claude.com.")
if s1 and s2 and s1 > s2 + 3:
    print("- The first schema call was much slower than the second: that's the one-off schema compile."
          " It's cached for 24 hours, so a warm-up call when the app starts would hide it.")
if s2 and s2 > 8 and plain and plain < 5:
    print("- Writing the card itself is slow even when cached: the API may be busy right now - try again later.")
if any(v is None for v in results.values()):
    print("- At least one call FAILED. In the app, the SDK quietly retries failures (up to 2 times, with"
          " pauses), which can easily add 10-20 seconds before you see anything.")
print("Paste this whole output to me if you're not sure.")