"""
Kashafdeals Amazon.eg Direct Scraper
Scrapes discounted items from Amazon.eg deals + category pages.
Posts to @kashafdeals via Telegram Bot API.
"""
import json, os, re, time, requests
from bs4 import BeautifulSoup

# ── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ["BOT_TOKEN"]
DEST_CHANNEL  = os.environ.get("DEST_CHANNEL", "@kashafdeals")
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "kashafdeals-21")
MAX_PER_RUN   = int(os.environ.get("MAX_PER_RUN", "3"))

TELEGRAM_API  = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE    = "state.json"

CATEGORIES = {
    "deals":       "https://www.amazon.eg/gp/goldbox/",
    "electronics": "https://www.amazon.eg/gp/bestsellers/electronics/",
    "beauty":      "https://www.amazon.eg/gp/bestsellers/beauty/",
    "fashion":     "https://www.amazon.eg/gp/bestsellers/apparel/",
    "home":        "https://www.amazon.eg/gp/bestsellers/home-kitchen/",
    "supermarket": "https://www.amazon.eg/gp/bestsellers/grocery/",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

CATEGORY_LABELS = {
    "deals":       "🔥 عروض اليوم",
    "electronics": "📱 إلكترونيات",
    "beauty":      "💄 جمال وعناية",
    "fashion":     "👗 أزياء وموضة",
    "home":        "🏠 المنزل والمطبخ",
    "supermarket": "🛒 سوبرماركت",
}

# ── State ──────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {"posted": []}

def save_state(state):
    state["posted"] = list(set(state["posted"]))[-1000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# ── Helpers ────────────────────────────────────────────────────────────────
def make_link(asin):
    return f"https://www.amazon.eg/dp/{asin}?tag={AFFILIATE_TAG}"

def extract_asin(url_or_card):
    m = re.search(r"/dp/([A-Z0-9]{10})", str(url_or_card))
    return m.group(1) if m else None

def parse_price(text):
    if not text:
        return None
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬،,", "0123456789...  "))
    text = re.sub(r"[^\d.]", "", text.replace("EGP", "").replace("ج.م", "").strip())
    try:
        return float(text) if text else None
    except ValueError:
        return None

def fetch_html(url):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code}")
        except Exception as e:
            print(f"  Fetch error (attempt {attempt+1}): {e}")
        time.sleep(4)
    return None

# ── Parsers ────────────────────────────────────────────────────────────────
def parse_bestsellers(html, category):
    soup  = BeautifulSoup(html, "html.parser")
    items = []

    title = soup.title.get_text(strip=True) if soup.title else "no title"
    print(f"  Page title: {title}")

    cards = (
        soup.select("div[id^='p13n-asin-index-']") or
        soup.select("li[id^='p13n-asin-index-']") or
        soup.select("[data-asin]")
    )
    print(f"  Cards found: {len(cards)}")

    for card in cards:
        try:
            asin = card.get("data-asin") or ""
            if not asin:
                link = card.select_one("a[href*='/dp/']")
                if link:
                    asin = extract_asin(link.get("href", "")) or ""
            if not asin or len(asin) != 10:
                continue

            title_el = (
                card.select_one("._cDEzb_p13n-sc-css-line-clamp-3_g3dy1") or
                card.select_one("div[class*='line-clamp']") or
                card.select_one(".a-link-normal span") or
                card.select_one("span.a-text-normal")
            )
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            current_price = original_price = None
            for p in card.select(".a-price"):
                raw = p.get_text(" ", strip=True)
                val = parse_price(raw)
                if val is None:
                    continue
                if p.get("data-a-strike") == "true" or "a-text-strike" in p.get("class", []):
                    original_price = val
                else:
                    if current_price is None:
                        current_price = val

            disc_el = (
                card.select_one(".savingsPercentage") or
                card.select_one("[class*='discount']") or
                card.select_one("[class*='saving']")
            )
            discount_pct = 0
            if disc_el:
                m = re.search(r"(\d+)", disc_el.get_text())
                if m:
                    discount_pct = int(m.group(1))
            if not discount_pct and current_price and original_price and original_price > current_price:
                discount_pct = round((original_price - current_price) / original_price * 100)

            if discount_pct < 5:
                continue

            img_el  = card.select_one("img")
            img_url = ""
            if img_el:
                img_url = img_el.get("src") or img_el.get("data-src") or ""
                if img_url.startswith("//"):
                    img_url = "https:" + img_url

            items.append({
                "asin": asin, "title": title,
                "current_price": current_price,
                "original_price": original_price,
                "discount_pct": discount_pct,
                "img_url": img_url,
                "category": category,
            })
        except Exception:
            continue

    return items

def parse_deals_page(html):
    soup  = BeautifulSoup(html, "html.parser")
    items = []

    title = soup.title.get_text(strip=True) if soup.title else "no title"
    print(f"  Page title: {title}")

    cards = soup.select("[data-asin]")
    print(f"  Cards found: {len(cards)}")

    for card in cards:
        asin = card.get("data-asin", "")
        if not asin or len(asin) != 10:
            continue
        try:
            title_el = (
                card.select_one(".a-truncate-cut") or
                card.select_one("[class*='DealTitle']") or
                card.select_one("span.a-text-normal")
            )
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            disc_el = (
                card.select_one(".savingsPercentage") or
                card.select_one("[class*='savings']") or
                card.select_one("[class*='discount']")
            )
            discount_pct = 0
            if disc_el:
                m = re.search(r"(\d+)", disc_el.get_text())
                if m:
                    discount_pct = int(m.group(1))

            current_price = original_price = None
            for p in card.select(".a-price"):
                val = parse_price(p.get_text(" ", strip=True))
                if val is None:
                    continue
                if p.get("data-a-strike") == "true":
                    original_price = val
                elif current_price is None:
                    current_price = val

            if not discount_pct and current_price and original_price and original_price > current_price:
                discount_pct = round((original_price - current_price) / original_price * 100)

            if discount_pct < 5:
                continue

            img_el  = card.select_one("img")
            img_url = ""
            if img_el:
                img_url = img_el.get("src") or img_el.get("data-src") or ""
                if img_url.startswith("//"):
                    img_url = "https:" + img_url

            items.append({
                "asin": asin, "title": title,
                "current_price": current_price,
                "original_price": original_price,
                "discount_pct": discount_pct,
                "img_url": img_url,
                "category": "deals",
            })
        except Exception:
            continue

    return items

# ── Telegram ───────────────────────────────────────────────────────────────
def build_caption(item):
    label = CATEGORY_LABELS.get(item["category"], "🛍️ عرض")
    link  = make_link(item["asin"])
    lines = [label, "", f"📦 {item['title']}", ""]

    if item.get("current_price") and item.get("original_price"):
        lines += [
            f"💰 السعر: {item['current_price']:,.0f} ج.م",
            f"~~كان: {item['original_price']:,.0f} ج.م~~",
        ]
    elif item.get("current_price"):
        lines.append(f"💰 السعر: {item['current_price']:,.0f} ج.م")

    if item.get("discount_pct"):
        lines.append(f"🏷️ خصم {item['discount_pct']}%")

    lines += ["", f"🛒 {link}"]
    return "\n".join(lines)

def post_item(item):
    caption = build_caption(item)
    photo_bytes = None

    if item.get("img_url"):
        try:
            r = requests.get(item["img_url"], headers=HEADERS, timeout=15)
            if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                photo_bytes = r.content
        except Exception:
            pass

    if photo_bytes:
        r = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": DEST_CHANNEL, "caption": caption},
            files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
            timeout=60,
        )
        if r.json().get("ok"):
            return True
        print(f"  sendPhoto failed: {r.json().get('description')} — falling back to text")

    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": DEST_CHANNEL, "text": caption},
        timeout=30,
    )
    return r.json().get("ok", False)

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    state      = load_state()
    posted_set = set(state.get("posted", []))
    total      = 0

    for category, url in CATEGORIES.items():
        if total >= MAX_PER_RUN:
            break

        print(f"\n{'='*40}")
        print(f"Category: {category}")
        print(f"URL: {url}")

        html = fetch_html(url)
        if not html:
            print("  SKIPPED — fetch failed")
            continue

        items = parse_deals_page(html) if category == "deals" else parse_bestsellers(html, category)
        print(f"  Discounted items found: {len(items)}")

        for item in items:
            if total >= MAX_PER_RUN:
                break
            if item["asin"] in posted_set:
                print(f"  Skip {item['asin']} — already posted")
                continue

            ok = post_item(item)
            label = "OK" if ok else "FAILED"
            print(f"  [{label}] {item['asin']} | {item['discount_pct']}% off | {item['title'][:50]}")

            if ok:
                posted_set.add(item["asin"])
                state["posted"] = list(posted_set)
                total += 1

            time.sleep(3)

    save_state(state)
    print(f"\n{'='*40}")
    print(f"Done. Posted: {total}/{MAX_PER_RUN}")

if __name__ == "__main__":
    main()
