"""Arkhashom Combo Scraper v8 - combo / forward-all, banner, competitor-card filter."""
import asyncio, json, os, re, time
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (MessageMediaPhoto, MessageMediaDocument,
    MessageMediaWebPage, MessageEntityTextUrl)

API_ID        = int(os.environ["TELEGRAM_API_ID"])
API_HASH      = os.environ["TELEGRAM_API_HASH"]
SESSION_STR   = os.environ["TELEGRAM_SESSION"]
BOT_TOKEN     = os.environ["BOT_TOKEN"]
DEST_CHANNEL  = os.environ.get("DEST_CHANNEL", "@arkhashomoffers")
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "arkhashom-21")
def _parse_channels(raw):
    return [
        c.strip().lstrip("@").replace("https://t.me/", "")
        for c in (raw or "").split(",")
        if c.strip()
    ]

CHANNELS      = _parse_channels(os.environ.get("CHANNELS", "EgyptOffersHunter"))
ALL_CHANNELS  = _parse_channels(os.environ.get("ALL_CHANNELS", ""))

NO_PHOTO_CHANNELS = _parse_channels(os.environ.get("NO_PHOTO_CHANNELS", ""))

SCRAPE_MODE   = os.environ.get("SCRAPE_MODE", "both").strip().lower()
if SCRAPE_MODE not in ("combo", "all", "both"):
    SCRAPE_MODE = "both"

STATE_FILE   = os.environ.get("STATE_FILE", "state_combo.json")
FETCH_LIMIT  = int(os.environ.get("FETCH_LIMIT", "50"))
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def load_state():
    if os.path.exists(STATE_FILE):
        try: return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception: pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

_EXCLUDE_PATTERNS = [
    re.compile(r'عروض لا تفوت', re.IGNORECASE),
]

def is_excluded(text):
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.search(text):
            return True
    return False

_SHORT_DOMS = r'link\.amazon|amzn\.to|amzn\.eu|a\.co|(?:www\.)?amazon-eg\.net'
_SHORT_HINTS = ["link.amazon", "amzn.to", "amzn.eu", "a.co", "amazon-eg.net"]
_SHORT_LINK_RE = re.compile(
    r'https?://(?:' + _SHORT_DOMS + r')/[^\s)\]>"\n]+',
    re.IGNORECASE
)
_FULL_AMAZON_RE = re.compile(
    r'https?://(?:www\.)?amazon\.[a-z.]+/[^\s)\]>"\n]*',
    re.IGNORECASE
)

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
    short_count  = len(_SHORT_LINK_RE.findall(text))
    full_count   = len(_FULL_AMAZON_RE.findall(text))
    entity_count = len(entity_urls) if entity_urls else 0
    if short_count + full_count + entity_count >= 2:
        return True
    for pattern in _COMBO_PATTERNS:
        if pattern.search(text):
            return True
    return False

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
}

def resolve_short_link(url):
    try:
        resp = requests.get(url, allow_redirects=True, timeout=15, headers=UA)
        final = resp.url
        if "amazon" in final:
            cleaned = clean_amazon_url(final)
            print(f"  [OK] Resolved: {url} -> {cleaned}")
            return cleaned
    except Exception as e:
        print(f"  [WARN] HTTP resolve failed for {url}: {e}")
    return url

def is_psp_url(url):
    return "/psp/" in url or "/promotion/" in url

def resolve_and_check_psp(text, entity_urls):
    text = rejoin_split_urls(text)
    short_links  = _SHORT_LINK_RE.findall(text)
    entity_short = [u for u in (entity_urls or [])
                    if any(x in u for x in _SHORT_HINTS)]
    candidates = list(dict.fromkeys(short_links + entity_short))

    for url in candidates[:3]:
        resolved = resolve_short_link(url)
        if is_psp_url(resolved):
            return True, resolved
    return False, None

_KEEP_PARAMS = {"k", "rh", "i", "node", "bbn", "s", "field-keywords", "me", "fs"}

def clean_amazon_url(url):
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(url)

    asin_match = re.search(r'/dp/([A-Z0-9]{10})', parsed.path)
    if asin_match:
        asin = asin_match.group(1)
        return f"https://www.amazon.eg/dp/{asin}?tag={AFFILIATE_TAG}"

    promo_match = re.search(r'(/promotion/psp/[A-Za-z0-9]+)', parsed.path)
    if promo_match:
        return f"https://www.amazon.eg{promo_match.group(1)}?tag={AFFILIATE_TAG}"

    params = {k: v for k, v in parse_qs(parsed.query, keep_blank_values=True).items() if k in _KEEP_PARAMS}
    params["tag"] = [AFFILIATE_TAG]
    path = re.sub(r'/ref=[^/]*$', '', parsed.path)
    return urlunparse(parsed._replace(path=path, query=urlencode(params, doseq=True),
                                      netloc="www.amazon.eg", fragment=""))

def rejoin_split_urls(text):
    text = re.sub(
        r'(https?://(?:' + _SHORT_DOMS + r'))\s*\n\s*(/[A-Za-z0-9_-]+)',
        r'\1\2',
        text
    )
    return text

def extract_entity_urls(msg):
    urls = []
    if msg.entities:
        for ent in msg.entities:
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                if any(x in ent.url for x in _SHORT_HINTS + ["amazon.eg", "amazon.com"]):
                    urls.append(ent.url)
    return urls

def resolve_all_short_links(text, entity_urls=None):
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

_AMAZON_RE = re.compile(
    r'(https?://(?:www\.)?amazon\.[a-z.]+/[^\s)\]>"\n]*)',
    re.IGNORECASE
)

def swap_tag(text):
    if not text:
        return text
    return _AMAZON_RE.sub(lambda m: clean_amazon_url(m.group(1)), text)

_SPAM_PATTERNS = [
    re.compile(r'تابعنا على جميع منصات التواصل[:\s]*', re.IGNORECASE),
    re.compile(r'قناتنا على واتساب[^\n]*', re.IGNORECASE),
    re.compile(r'قناتنا لعروض نون[^\n]*', re.IGNORECASE),
    re.compile(r'اضغط هنا للانضمام[^\n]*', re.IGNORECASE),
    re.compile(r'تابعونا[^\n]*', re.IGNORECASE),
    re.compile(r'https?://(?:wa\.me|chat\.whatsapp\.com|t\.me/(?!arkhashom|kashaf))[^\s)\n]*', re.IGNORECASE),
    re.compile(r'https?://(?:www\.)?noon\.com[^\s)\n]*', re.IGNORECASE),
    re.compile(r'\U0001F4F1[^\n]*واتساب[^\n]*', re.IGNORECASE),
    re.compile(r'\U0001F4F1[^\n]*', re.IGNORECASE),
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

BANNER_PATH = os.environ.get("BANNER_PATH", "arkhashom_banner.png")

def brand_photo(photo_bytes):
    if not photo_bytes or not os.path.exists(BANNER_PATH):
        return photo_bytes
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(photo_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        tw = min(max(w, 640), 1280)
        if tw != w:
            img = img.resize((tw, max(1, int(h * tw / w))), Image.LANCZOS)
            w, h = img.size
        banner = Image.open(BANNER_PATH).convert("RGB")
        bw, bh = banner.size
        nh = max(1, int(bh * w / bw))
        banner = banner.resize((w, nh), Image.LANCZOS)
        out = Image.new("RGB", (w, h + nh), (0, 0, 0))
        out.paste(img, (0, 0))
        out.paste(banner, (0, h))
        buf = BytesIO()
        out.save(buf, "JPEG", quality=88, optimize=True)
        print(f"  [OK] Banner added ({w}x{h} -> {w}x{h + nh})")
        return buf.getvalue()
    except Exception as e:
        print(f"  [WARN] Banner overlay failed: {e}")
        return photo_bytes

def _parse_sizes(raw):
    out = set()
    for part in (raw or "").split(","):
        part = part.strip().lower().replace(" ", "")
        m = re.fullmatch(r"(\d+)x(\d+)", part)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out

CARD_SIZES      = _parse_sizes(os.environ.get("CARD_SIZES", "1536x1024"))
CARD_NAVY_MIN   = float(os.environ.get("CARD_NAVY_MIN", "0.30"))

AMAZON_IMG = os.environ.get("AMAZON_IMG_FALLBACK", "1").strip() in ("1", "true", "yes")

async def get_source_photo(client, msg):
    m = msg.media
    target = None
    if isinstance(m, MessageMediaPhoto):
        target = m
    elif isinstance(m, MessageMediaDocument):
        mime = getattr(m.document, "mime_type", "") or ""
        if mime.startswith("image/") and "gif" not in mime:
            target = m
    elif isinstance(m, MessageMediaWebPage):
        target = getattr(m.webpage, "photo", None)
    if target is None:
        return None
    try:
        b = await client.download_media(target, bytes)
        print(f"  [IMG] source photo downloaded ({type(m).__name__})")
        return b
    except Exception as e:
        print(f"  Photo error: {e}")
        return None

def amazon_photo(text):
    m = re.search(r'amazon\.eg/(?:[^\s?]*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', text or "")
    if not m:
        return None
    try:
        h = requests.get(f"https://www.amazon.eg/dp/{m.group(1)}", headers=UA, timeout=15).text
        u = (re.search(r'"hiRes":"(https://[^"]+)"', h) or re.search(r'data-old-hires="(https://[^"]+)"', h)
             or re.search(r'"large":"(https://[^"]+)"', h) or re.search(r'og:image" content="([^"]+)"', h))
        if not u:
            print(f"  [IMG] no Amazon photo for {m.group(1)} (page blocked?) - text only")
            return None
        b = requests.get(u.group(1), timeout=20).content
        print(f"  [IMG] using Amazon product photo for {m.group(1)}")
        return b
    except Exception as e:
        print(f"  [IMG] Amazon photo failed: {e}")
        return None

FORWARD_WITHOUT_LINK = os.environ.get("FORWARD_WITHOUT_LINK", "0").strip() in ("1", "true", "yes")

def looks_like_branded_card(img):
    import colorsys
    w, h = img.size
    if (w, h) in CARD_SIZES:
        print(f"  [CARD] size {w}x{h} matches a known card template")
        return True
    try:
        sm = img.resize((300, max(1, int(300 * h / w))))
        sw, sh = sm.size
        px = sm.load()
        def navy_frac(x0, y0, x1, y1):
            n = c = 0
            for x in range(int(x0 * sw), int(x1 * sw)):
                for y in range(int(y0 * sh), int(y1 * sh)):
                    r, g, b = px[x, y]
                    hh, s_, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                    n += 1
                    c += (v < 0.45 and s_ > 0.35 and 0.55 <= hh <= 0.72)
            return c / max(n, 1)
        tr = navy_frac(0.62, 0.0, 1.0, 0.25)
        bl = navy_frac(0.0, 0.8, 0.3, 1.0)
        if tr >= CARD_NAVY_MIN or bl >= CARD_NAVY_MIN:
            print(f"  [CARD] navy card corners (top-right {tr:.2f}, bottom-left {bl:.2f})")
            return True
        print(f"  [PLAIN] normal screenshot (navy {tr:.2f}/{bl:.2f}) - banner will be added")
    except Exception as e:
        print(f"  [WARN] card detection failed, keeping photo: {e}")
    return False

def is_branded_card_bytes(photo_bytes):
    if not photo_bytes:
        return False
    try:
        from io import BytesIO
        from PIL import Image
        return looks_like_branded_card(Image.open(BytesIO(photo_bytes)).convert("RGB"))
    except Exception as e:
        print(f"  [WARN] could not open photo for card check: {e}")
        return False

LINK_TEXT = os.environ.get("LINK_TEXT", "اضغط هنا 🛒").strip()

def to_html(caption):
    import html
    out, last = [], 0
    for m in _AMAZON_RE.finditer(caption):
        before = caption[last:m.start()]
        out.append(html.escape(before, quote=False))
        if before and not before[-1].isspace():
            out.append(" ")
        out.append(f'<a href="{html.escape(m.group(1))}">{html.escape(LINK_TEXT)}</a>')
        last = m.end()
    out.append(html.escape(caption[last:], quote=False))
    return "".join(out)

def _tg(method, payload, files=None):
    if files:
        r = requests.post(f"{TELEGRAM_API}/{method}", data=payload, files=files, timeout=60)
    else:
        r = requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=30)
    resp = r.json()
    if not resp.get("ok"):
        print(f"  [ERROR] {method}: {resp.get('description', resp)}")
    return bool(resp.get("ok"))

def send_post(text, photo_bytes=None, allow_no_link=False):
    """Returns "sent" | "skip" (never postable) | "error" (transient, retry)."""
    tagged  = swap_tag(text)
    caption = clean_caption(tagged)

    if not _AMAZON_RE.search(caption) and "amazon" not in caption:
        if not allow_no_link:
            print(f"  [SKIP] No Amazon link. Text was: {text[:120]!r}")
            return "skip"
        print("  [WARN] No Amazon link, forwarding anyway (FORWARD_WITHOUT_LINK=1)")

    if len(caption.strip()) < 10:
        print("  [SKIP] Caption too short")
        return "skip"

    print(f"  Caption ({len(caption)} chars): {caption[:300]}")

    if photo_bytes:
        photo_bytes = brand_photo(photo_bytes)
    variants = [(to_html(caption), {"parse_mode": "HTML"})] if LINK_TEXT else []
    variants.append((caption, {}))
    for body, extra in variants:
        if photo_bytes and _tg("sendPhoto", {"chat_id": DEST_CHANNEL, "caption": body, **extra},
                               files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")}):
            return "sent"
        if _tg("sendMessage", {"chat_id": DEST_CHANNEL, "text": body,
                               "disable_web_page_preview": False, **extra}):
            return "sent"
    return "error"

async def run():
    state   = load_state()
    total   = 0
    skipped = 0

    async with TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
        print(f"Filter: combo channels = 2+ links / keywords / PSP; forward-all = every post. Excludes: عروض لا تفوت")
        print(f"Affiliate tag: {AFFILIATE_TAG}")
        print(f"Destination: {DEST_CHANNEL}")
        print(f"Run mode: {SCRAPE_MODE.upper()}  |  state file: {STATE_FILE}")
        print(f"Banner: {BANNER_PATH if os.path.exists(BANNER_PATH) else '(not found - photos sent unbranded)'}")
        print(f"No-photo channels: {NO_PHOTO_CHANNELS or '(none)'}")
        print(f"Amazon photo fallback: {'ON' if AMAZON_IMG else 'OFF'}")
        print(f"Card sizes dropped: {sorted(CARD_SIZES) or '(none)'}  |  navy threshold: {CARD_NAVY_MIN}")
        print(f"Combo-only channels: {CHANNELS or '(none)'}")
        print(f"Forward-all channels: {ALL_CHANNELS or '(none)'}")

        targets = []
        if SCRAPE_MODE in ("combo", "both"):
            targets += [(ch, "combo") for ch in CHANNELS]
        if SCRAPE_MODE in ("all", "both"):
            skip = set(CHANNELS) if SCRAPE_MODE == "both" else set()
            targets += [(ch, "all") for ch in ALL_CHANNELS if ch not in skip]

        if not targets:
            print("No channels to scrape for this mode - nothing to do.")

        for channel, mode in targets:
            label = "COMBO-ONLY" if mode == "combo" else "FORWARD-ALL"
            print(f"\n── @{channel} [{label}] ──")
            last_id = state.get(channel, 0)

            try:
                messages = await client.get_messages(channel, limit=FETCH_LIMIT)
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

                if is_excluded(text):
                    print(f"  Msg {msg.id}: SKIP (excluded phrase)")
                    state[channel] = msg.id
                    skipped += 1
                    continue

                combo_type = None

                if mode == "all":
                    combo_type = "FORWARD-ALL"
                elif is_combo_post(text_for_check, entity_urls):
                    combo_type = "MULTI-LINK/KEYWORD"
                else:
                    is_psp, psp_url = resolve_and_check_psp(text, entity_urls)
                    if is_psp:
                        combo_type = f"PSP ({psp_url[:60]})"

                if not combo_type:
                    print(f"  Msg {msg.id}: SKIP (single item)")
                    state[channel] = msg.id
                    skipped += 1
                    continue

                print(f"  Msg {msg.id}: {'FORWARDING' if mode == 'all' else 'COMBO detected!'} [{combo_type}]")

                photo_bytes = None
                if channel in NO_PHOTO_CHANNELS:
                    print(f"  Photo skipped - @{channel} is in NO_PHOTO_CHANNELS")
                else:
                    photo_bytes = await get_source_photo(client, msg)
                    if photo_bytes and is_branded_card_bytes(photo_bytes):
                        print("  Photo dropped - source's own designed card")
                        photo_bytes = None

                resolved_text = resolve_all_short_links(text, entity_urls)
                if not photo_bytes and AMAZON_IMG and channel not in NO_PHOTO_CHANNELS:
                    photo_bytes = amazon_photo(resolved_text)

                status = send_post(
                    resolved_text, photo_bytes,
                    allow_no_link=(mode == "all" and FORWARD_WITHOUT_LINK),
                )

                if status == "sent":
                    print(f"  Msg {msg.id}: OK")
                    total += 1
                    state[channel] = msg.id
                elif status == "skip":
                    print(f"  Msg {msg.id}: SKIPPED (not postable)")
                    skipped += 1
                    state[channel] = msg.id
                    continue
                else:
                    print(f"  Msg {msg.id}: FAILED - state NOT advanced, will retry next run")
                    break
                time.sleep(2)

    save_state(state)
    print(f"\nDone. Posted: {total} | Skipped: {skipped}")

if __name__ == "__main__":
    asyncio.run(run())
