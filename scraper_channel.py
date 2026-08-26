"""
Kashafdeals Channel Scraper — Telethon edition (GitHub Actions)
Reads source channels, resolves short Amazon links, swaps affiliate tag,
cleans captions, and reposts to destination channel.
"""
import asyncio, json, os, re, time
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto

API_ID        = int(os.environ["TELEGRAM_API_ID"])
API_HASH      = os.environ["TELEGRAM_API_HASH"]
SESSION_STR   = os.environ["TELEGRAM_SESSION"]
BOT_TOKEN     = os.environ["BOT_TOKEN"]
DEST_CHANNEL  = os.environ.get("DEST_CHANNEL", "@kashafdeals")
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "arkhashom-21")
CHANNELS      = [
    c.strip().lstrip("@").replace("https://t.me/", "")
    for c in os.environ.get("CHANNELS", "EgyptOffersHunter").split(",")
    if c.strip()
]

STATE_FILE   = "state_channel.json"
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

# -- Short link resolution -----------------------------------------------------
_SHORT_LINK_RE = re.compile(
    r'https?://(?:link\.amazon|amzn\.to|amzn\.eu|a\.co)[^\s)\]>"\n]*',
    re.IGNORECASE
)

def resolve_short_link(url):
    """Follow redirects to get the full Amazon URL."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
        final = resp.url
        if "amazon" in final:
            return final
        # Try GET if HEAD didn't resolve
        resp = requests.get(url, allow_redirects=True, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        final = resp.url
        if "amazon" in final:
            return final
    except Exception as e:
        print(f"  [WARN] Failed to resolve {url}: {e}")
    return url

def resolve_all_short_links(text):
    """Find and resolve all short Amazon links in text."""
    short_links = _SHORT_LINK_RE.findall(text)
    for short_url in short_links:
        full_url = resolve_short_link(short_url)
        if full_url != short_url:
            text = text.replace(short_url, full_url)
            print(f"  Resolved: {short_url} -> {full_url[:70]}...")
    return text

# -- Affiliate tag swap --------------------------------------------------------
_TAG_RE    = re.compile(r'\btag=[^&\s)\]>\n]+')
_AMAZON_RE = re.compile(
    r'(https?://(?:www\.)?amazon\.[a-z.]+/[^\s)\]>"\n]*)',
    re.IGNORECASE
)

def swap_tag(text):
    """Replace or add affiliate tag in all Amazon URLs."""
    if not text:
        return text
    result = _TAG_RE.sub(f"tag={AFFILIATE_TAG}", text)
    def _add(m):
        url = m.group(1)
        if "tag=" not in url:
            return url + ("&" if "?" in url else "?") + f"tag={AFFILIATE_TAG}"
        return url
    return _AMAZON_RE.sub(_add, result)

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
    """Remove spam (WhatsApp/Noon/follow-us). Keep title + discount + Amazon link."""
    if not text:
        return text

    for pattern in _SPAM_PATTERNS:
        text = pattern.sub("", text)

    # Remove non-Amazon links
    text = re.sub(
        r'https?://(?!(?:www\.)?amazon\.|link\.amazon|amzn)[^\s)\]>"\n]*',
        "", text
    )

    # Clean up whitespace
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()

# -- Post to destination -------------------------------------------------------
def send_post(text, photo_bytes=None):
    """Process text (resolve + swap + clean) and post to destination."""
    # Step 1: Resolve short links
    resolved = resolve_all_short_links(text)

    # Step 2: Swap affiliate tags
    tagged = swap_tag(resolved)

    # Step 3: Clean caption
    caption = clean_caption(tagged)

    # Skip if no Amazon link after processing
    if not _AMAZON_RE.search(caption) and "amazon" not in caption:
        print("  [SKIP] No Amazon link after processing")
        return False

    # Skip if too short
    if len(caption.strip()) < 10:
        print("  [SKIP] Caption too short after cleaning")
        return False

    if photo_bytes:
        r = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": DEST_CHANNEL, "caption": caption},
            files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
            timeout=60,
        )
        if r.json().get("ok"):
            return True

    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": DEST_CHANNEL, "text": caption,
              "disable_web_page_preview": False},
        timeout=30,
    )
    return r.json().get("ok", False)

# -- Main loop -----------------------------------------------------------------
async def run():
    state = load_state()
    total = 0

    async with TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
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
                photo_bytes = None
                if isinstance(msg.media, MessageMediaPhoto):
                    try:
                        photo_bytes = await client.download_media(msg.media, bytes)
                    except Exception as e:
                        print(f"  Photo error: {e}")

                ok = send_post(text, photo_bytes)
                print(f"  Msg {msg.id}: {'OK' if ok else 'SKIPPED/FAILED'}")
                if ok:
                    state[channel] = msg.id
                    total += 1
                time.sleep(2)

    save_state(state)
    print(f"\nDone. Posted: {total}")

if __name__ == "__main__":
    asyncio.run(run())
