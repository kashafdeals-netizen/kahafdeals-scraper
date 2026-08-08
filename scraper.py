"""
Kashafdeals Channel Scraper — GitHub Actions edition
Polls Telegram channels every 10 min, swaps affiliate tags, posts to @kashafdeals.
No server needed — runs entirely on GitHub's free infrastructure.
"""
import json, os, re, sys, time
import requests
from bs4 import BeautifulSoup, NavigableString

# ── Config from GitHub Secrets ────────────────────────────────────────────────
BOT_TOKEN     = os.environ["BOT_TOKEN"]
DEST_CHANNEL  = os.environ.get("DEST_CHANNEL", "@kashafdeals")
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "kashafdeals-21")
CHANNELS      = [
    c.strip().lstrip("@").replace("https://t.me/", "")
    for c in os.environ.get("CHANNELS", "EgyptOffersHunter").split(",")
    if c.strip()
]

STATE_FILE   = "state.json"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
HEADERS      = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ── State (tracks last seen post ID per channel) ──────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try: return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception: pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# ── Affiliate tag replacement ─────────────────────────────────────────────────
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

# ── HTML → plain text (preserving actual link URLs) ───────────────────────────
def extract_text(el):
    parts = []
    for child in el.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif child.name == "a":
            href = child.get("href", "")
            parts.append(href if href.startswith("http") else child.get_text())
        elif child.name == "br":
            parts.append("\n")
        else:
            parts.append(extract_text(child))
    return "".join(parts)

# ── Scrape t.me/s/channel ─────────────────────────────────────────────────────
def fetch_posts(channel):
    try:
        r = requests.get(f"https://t.me/s/{channel}", headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[{channel}] Fetch error: {e}"); return []

    soup  = BeautifulSoup(r.text, "html.parser")
    posts = []

    for wrap in soup.select(".tgme_widget_message_wrap"):
        msg = wrap.select_one(".tgme_widget_message")
        if not msg: continue

        data_post = msg.get("data-post", "")
        if "/" not in data_post: continue
        try:
            post_id = int(data_post.split("/")[-1])
        except ValueError:
            continue

        # Text — use actual href for links, not display text
        text_el = msg.select_one(".tgme_widget_message_text")
        text    = extract_text(text_el).strip() if text_el else ""

        # Image — from CSS background-image on the photo wrap
        image_url = None
        photo = msg.select_one("a.tgme_widget_message_photo_wrap")
        if photo:
            m = re.search(r"url\(['\"]?(.+?)['\"]?\)", photo.get("style", ""))
            if m: image_url = m.group(1)

        posts.append({"id": post_id, "text": text, "image_url": image_url})

    return sorted(posts, key=lambda x: x["id"])

# ── Post to Telegram via Bot API ──────────────────────────────────────────────
def send_post(text, image_url=None):
    caption = swap_tag(text)

    if image_url:
        try:
            img = requests.get(image_url, headers=HEADERS, timeout=30)
            img.raise_for_status()
            r = requests.post(
                f"{TELEGRAM_API}/sendPhoto",
                data={"chat_id": DEST_CHANNEL, "caption": caption},
                files={"photo": ("photo.jpg", img.content, "image/jpeg")},
                timeout=60,
            )
            resp = r.json()
            if resp.get("ok"): return True
            print(f"  sendPhoto failed: {resp.get('description')} — falling back to text")
        except Exception as e:
            print(f"  Image error: {e} — falling back to text")

    # Text-only fallback
    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": DEST_CHANNEL, "text": caption},
        timeout=30,
    )
    return r.json().get("ok", False)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    state  = load_state()
    total  = 0
    errors = 0

    for channel in CHANNELS:
        print(f"\n── @{channel} ──")
        posts = fetch_posts(channel)

        if not posts:
            print("  No posts found (channel private or scrape failed)")
            continue

        last_id = state.get(channel, 0)

        # First run: record current latest ID without posting old content
        if last_id == 0:
            new_last = max(p["id"] for p in posts)
            state[channel] = new_last
            print(f"  First run — saving latest ID: {new_last} (no posting yet)")
            continue

        new_posts = [p for p in posts if p["id"] > last_id]
        print(f"  {len(new_posts)} new post(s) since ID {last_id}")

        for post in new_posts:
            has_tag = "tag=" in post["text"]
            ok = send_post(post["text"], post["image_url"])
            print(f"  Post {post['id']}: {'OK' if ok else 'FAILED'} | "
                  f"has_affiliate: {has_tag} | has_image: {post['image_url'] is not None}")
            if ok:
                state[channel] = post["id"]
                total += 1
            else:
                errors += 1
            time.sleep(2)  # avoid hitting Telegram rate limits

    save_state(state)
    print(f"\n── Done: posted={total}, errors={errors} ──")
    if errors: sys.exit(1)

if __name__ == "__main__":
    main()
