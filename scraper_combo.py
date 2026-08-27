"""
Arkhashom Combo Scraper — Forwards multi-item/combo posts.
Detects: 2+ Amazon links, combo keywords, OR single link resolving to PSP/promotion page.
"""
import asyncio, json, os, re, time
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto, MessageEntityTextUrl

API_ID        = int(os.environ["TELEGRAM_API_ID"])
API_HASH      = os.environ["TELEGRAM_API_HASH"]
SESSION_STR   = os.environ["TELEGRAM_SESSION"]
BOT_TOKEN     = os.environ["BOT_TOKEN"]
DEST_CHANNEL  = os.environ.get("DEST_CHANNEL", "@arkhashomoffers")
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "arkhashom-21")
CHANNELS      = [
    c.strip().lstrip("@").replace("https://t.me/", "")
    for c in os.environ.get("CHANNELS", "EgyptOffersHunter").split(",")
    if c.strip()
]

STATE_FILE   = "state_combo.json"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# -- State management ----------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        try: return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception: pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# -- Link patterns -------------------------------------------------------------
_SHORT_LINK_RE = re.compile(
    r'https?://(?:link\.amazon|amzn\.to|amzn\.eu|a\.co)/[^\s)\]>"\n]+',
    re.IGNORECASE
)
_FULL_AMAZON_RE = re.compile(
    r'https?://(?:www\.)?amazon\.[a-z.]+/[^\s)\]>"\n]*',
    re.IGNORECASE
)

# -- Combo detection (quick, no resolution needed) -----------------------------
_COMBO_KEYWORDS = [
    r'اشتر[يى]\s*\d+.*(?:وو?فر|واحصل|بسعر)',
    r'\d+\s*بسعر\s*\d+',
    r'خصم.*عند شراء\s*\d+',
    r'عرض.*(?:من|على)\s+\w+.*\n.*عرض.*(?:من|على)',
    r'اشتري\s*\d+\s*واحصل',
    r'buy\s*\d+.*get',
    r'عروض متعددة',
]
_COMBO_PATTERNS = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in _COMBO_KEYWORDS]


def is_combo_post(text, entity_urls=None):
    """Quick check: 2+ links or Arabic combo keywords."""
    short_count  = len(_SHORT_LINK_RE.findall(text))
    full_count   = len(_FULL_AMAZON_RE.findall(text))
    entity_count = len(entity_urls) if entity_urls else 0
    if short_count + full_count + entity_count >= 2:
        return True
    for pattern in _COMBO_PATTERNS:
        if pattern.search(text):
            return True
    return False


# -- Resolve short links -------------------------------------------------------
def resolve_short_link(url):
    """Resolve short Amazon link to full URL via HTTP redirect."""
    try:
        resp = requests.get(url, allow_redirects=True, timeout=15,
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                                "Accept": "text/html,application/xhtml+xml",
                                "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
                            })
        final = resp.url
        if "amazon" in final:
            cleaned = clean_amazon_url(final)
            print(f"  [OK] Resolved: {url} -> {cleaned}")
            return cleaned
    except Exception as e:
        print(f"  [WARN] HTTP resolve failed for {url}: {e}")
    return url


def is_psp_url(url):
    """Check if a resolved URL is a PSP/promotion combo page."""
    return "/psp/" in url or "/promotion/" in url


def resolve_and_check_psp(text, entity_urls):
    """Resolve short links from text/entities and check if any is a PSP page.
    Returns (is_psp, resolved_url_or_None)."""
    text = rejoin_split_urls(text)
    short_links = _SHORT_LINK_RE.findall(text)
    entity_short = [u for u in (entity_urls or [])
                    if any(x in u for x in ["link.amazon", "amzn.to", "amzn.eu", "a.co"])]
    # Deduplicate, preserve order
    candidates = list(dict.fromkeys(short_links + entity_short))

    for url in candidates[:3]:  # check up to 3 links
        resolved = resolve_short_link(url)
        if is_psp_url(resolved):
            return True, resolved
    return False, None


def clean_amazon_url(url):
    """For product pages: strip to dp/ASIN + tag.
    For search/events/other pages: preserve query params, just swap tag."""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(url)

    # Product page
    asin_match = re.search(r'/dp/([A-Z0-9]{10})', parsed.path)
    if asin_match:
        asin = asin_match.group(1)
        return f"https://www.amazon.eg/dp/{asin}?tag={AFFILIATE_TAG}"

    # Promotion/PSP pages
    promo_match = re.search(r'(/promotion/psp/[A-Za-z0-9]+)', parsed.path)
    if promo_match:
        return f"https://www.amazon.eg{promo_match.group(1)}?tag={AFFILIATE_TAG}"

    # Search, events, category pages — keep all params, just swap tag
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["tag"] = [AFFILIATE_TAG]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query, netloc="www.amazon.eg"))


def rejoin_split_urls(text):
    """Fix URLs split across lines."""
    text = re.sub(
        r'(https?://(?:link\.amazon|amzn\.to|amzn\.eu|a\.co))\s*\n\s*(/[A-Za-z0-9_-]+)',
        r'\1\2',
        text
    )
    return text


def extract_entity_urls(msg):
    """Extract Amazon URLs hidden in TextUrl entities."""
    urls = []
    if msg.entities:
        for ent in msg.entities:
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                if any(x in ent.url for x in ["link.amazon", "amzn.to", "amzn.eu", "a.co", "amazon.eg", "amazon.com"]):
                    urls.append(ent.url)
    return urls


def resolve_all_short_links(text, entity_urls=None):
    """Find and resolve all short Amazon links in text + entities."""
    text = rejoin_split_urls(text)
    if entity_urls:
        for eu in entity_urls:
            if eu not in text:
                text = text + "\n" + eu
    short_links = _SHORT_LINK_RE.findall(text)
    for short_url in short_links:
        full_url = resolve_short_link(short_url)
        if full_url != short_url:
            text = text.replace(short_url, full_url)
    return text


# -- Affiliate tag swap --------------------------------------------------------
_AMAZON_RE = re.compile(
    r'(https?://(?:www\.)?amazon\.[a-z.]+/[^\s)\]>"\n]*)',
    re.IGNORECASE
)

def swap_tag(text):
    if not text:
        return text
    return _AMAZON_RE.sub(lambda m: clean_amazon_url(m.group(1)), text)

# -- Caption cleaning ----------------------------------------------------------
_SPAM_PATTERNS = [
    re.compile(r'تابعنا على جميع منصات التواصل[:\s]*', re.IGNORECASE),
    re.compile(r'قناتنا على واتساب[^\n]*', re.IGNORECASE),
    re.compile(r'قناتنا لعروض نون[^\n]*', re.IGNORECASE),
    re.compile(r'اضغط هنا للانضمام[^\n]*', re.IGNORECASE),
    re.compile(r'تابعونا[^\n]*', re.IGNORECASE),
    re.compile(r'https?://(?:wa\.me|chat\.whatsapp\.com|t\.me/(?!arkhashom|kashaf))[^\s)\n]*', re.IGNORECASE),
    re.compile(r'https?://(?:www\.)?noon\.com[^\s)\n]*', re.IGNORECASE),
    re.compile(r'\U0001F4F1[^\n]*واتساب[^\n]*', re.IGNORECASE),
    re.compile(r'\U0001F4F1[^\n]*\n', re.IGNORECASE),
]

def clean_caption(text):
    if not text:
        return text
    for pattern in _SPAM_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(
        r'https?://(?!(?:www\.)?amazon\.|link\.amazon|amzn)[^\s)\]>"\n]*',
        "", text
    )
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()

# -- Post to destination -------------------------------------------------------
def send_post(text, photo_bytes=None):
    tagged  = swap_tag(text)
    caption = clean_caption(tagged)

    if not _AMAZON_RE.search(caption) and "amazon" not in caption:
        print("  [SKIP] No Amazon link after processing")
        return False

    if len(caption.strip()) < 10:
        print("  [SKIP] Caption too short")
        return False

    print(f"  Caption ({len(caption)} chars): {caption[:300]}")

    if photo_bytes:
        r = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": DEST_CHANNEL, "caption": caption},
            files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
            timeout=60,
        )
        resp = r.json()
        if resp.get("ok"):
            return True
        print(f"  [ERROR] sendPhoto: {resp.get('description', resp)}")

    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": DEST_CHANNEL, "text": caption,
              "disable_web_page_preview": False},
        timeout=30,
    )
    resp = r.json()
    if not resp.get("ok"):
        print(f"  [ERROR] sendMessage: {resp.get('description', resp)}")
    return resp.get("ok", False)

# -- Main loop -----------------------------------------------------------------
async def run():
    state   = load_state()
    total   = 0
    skipped = 0

    async with TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
        print(f"Mode: COMBO (2+ links / keywords / PSP promotion links)")
        print(f"Affiliate tag: {AFFILIATE_TAG}")
        print(f"Destination: {DEST_CHANNEL}")

        for channel in CHANNELS:
            print(f"\n── @{channel} ──")
            last_id = state.get(channel, 0)

            try:
                messages = await client.get_messages(channel, limit=20)
            except Exception as e:
                print(f"  Error: {e}")
                continue

            if not messages:
                print("  No messages found")
                continue

            if last_id == 0:
                new_last = max(m.id for m in messages)
                state[channel] = new_last
                print(f"  First run — saved latest ID: {new_last}")
                continue

            new_msgs = [m for m in reversed(messages) if m.id > last_id]
            print(f"  {len(new_msgs)} new message(s) since ID {last_id}")

            for msg in new_msgs:
                text        = msg.message or ""
                entity_urls = extract_entity_urls(msg)
                text_for_check = rejoin_split_urls(text)

                combo_type = None

                # Check 1: quick combo (2+ links or Arabic keywords)
                if is_combo_post(text_for_check, entity_urls):
                    combo_type = "MULTI-LINK/KEYWORD"
                else:
                    # Check 2: single link that resolves to PSP promotion page
                    is_psp, psp_url = resolve_and_check_psp(text, entity_urls)
                    if is_psp:
                        combo_type = f"PSP ({psp_url[:60]})"

                if not combo_type:
                    print(f"  Msg {msg.id}: SKIP (single item)")
                    state[channel] = msg.id
                    skipped += 1
                    continue

                print(f"  Msg {msg.id}: COMBO detected! [{combo_type}]")

                photo_bytes = None
                if isinstance(msg.media, MessageMediaPhoto):
                    try:
                        photo_bytes = await client.download_media(msg.media, bytes)
                    except Exception as e:
                        print(f"  Photo error: {e}")

                resolved_text = resolve_all_short_links(text, entity_urls)

                ok = send_post(resolved_text, photo_bytes)
                print(f"  Msg {msg.id}: {'OK' if ok else 'FAILED'}")
                if ok:
                    total += 1
                state[channel] = msg.id
                time.sleep(2)

    save_state(state)
    print(f"\nDone. Posted: {total} combos | Skipped: {skipped} single-item posts")

if __name__ == "__main__":
    asyncio.run(run())
