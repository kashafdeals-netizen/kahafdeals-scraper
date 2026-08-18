"""
Kashafdeals Channel Scraper — Telethon edition
Reads @EgyptOffersHunter, swaps affiliate tag, reposts to @kashafdeals
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
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "kashafdeals-21")
CHANNELS      = [
    c.strip().lstrip("@").replace("https://t.me/", "")
    for c in os.environ.get("CHANNELS", "EgyptOffersHunter").split(",")
    if c.strip()
]

STATE_FILE   = "state_channel.json"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def load_state():
    if os.path.exists(STATE_FILE):
        try: return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception: pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

_TAG_RE    = re.compile(r'\btag=[^&\s\)\]\>\n]+')
_AMAZON_RE = re.compile(r'(https?://(?:www\.)?amazon\.[a-z.]+/(?:dp|gp|s)[^\s\)\]\>\n]*)')

def swap_tag(text):
    if not text: return text
    result = _TAG_RE.sub(f"tag={AFFILIATE_TAG}", text)
    def _add(m):
        url = m.group(1)
        if "tag=" not in url:
            return url + ("&" if "?" in url else "?") + f"tag={AFFILIATE_TAG}"
        return url
    return _AMAZON_RE.sub(_add, result)

def send_post(text, photo_bytes=None):
    caption = swap_tag(text)
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
        json={"chat_id": DEST_CHANNEL, "text": swap_tag(text or ""),
              "disable_web_page_preview": False},
        timeout=30,
    )
    return r.json().get("ok", False)

async def run():
    state = load_state()
    total = 0

    async with TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")

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
                print(f"  Msg {msg.id}: {'OK' if ok else 'FAILED'}")
                if ok:
                    state[channel] = msg.id
                    total += 1
                time.sleep(2)

    save_state(state)
    print(f"\nDone. Posted: {total}")

if __name__ == "__main__":
    asyncio.run(run())
