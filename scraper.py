"""
Kashafdeals Amazon.eg Direct Scraper
- Scrapes discounted items from Amazon.eg search pages
- Takes real browser screenshot of each product page
- Extracts Arabic title from rendered page
- Posts in old-style caption format
- State expires after 48h so items can recycle
"""
import json, os, re, time, requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BOT_TOKEN     = os.environ["BOT_TOKEN"]
DEST_CHANNEL  = os.environ.get("DEST_CHANNEL", "@kashafdeals")
AFFILIATE_TAG = os.environ.get("AFFILIATE_TAG", "kashafdeals-21")
MAX_PER_RUN   = int(os.environ.get("MAX_PER_RUN", "3"))

TELEGRAM_API  = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE    = "state.json"
EXPIRY_HOURS  = 48

CATEGORIES = {
    "deals":       "https://www.amazon.eg/s?s=discount-rank&rh=p_n_pct-off-with-tax%3A10-100",
    "electronics": "https://www.amazon.eg/s?i=electronics&s=discount-rank&rh=p_n_pct-off-with-tax%3A10-100",
    "beauty":      "https://www.amazon.eg/s?i=beauty&s=discount-rank&rh=p_n_pct-off-with-tax%3A10-100",
    "fashion":     "https://www.amazon.eg/s?i=apparel&s=discount-rank&rh=p_n_pct-off-with-tax%3A10-100",
    "home":        "https://www.amazon.eg/s?i=home-kitchen&s=discount-rank&rh=p_n_pct-off-with-tax%3A10-100",
    "supermarket": "https://www.amazon.eg/s?i=grocery&s=discount-rank&rh=p_n_pct-off-with-tax%3A10-100",
}

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            data = json.load(open(STATE_FILE, encoding="utf-8"))
            if isinstance(data.get("posted"), list):
                return {"posted": {}}
            if "posted" not in data:
                return {"posted": {}}
            return data
        except Exception:
            pass
    return {"posted": {}}

def save_state(state):
    now = datetime.now(timezone.utc)
    pruned = {}
    for asin, ts in state.get("posted", {}).items():
        try:
            posted_at = datetime.fromisoformat(ts)
            if (now - posted_at).total_seconds() < EXPIRY_HOURS * 3600:
                pruned[asin] = ts
        except Exception:
            pass
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"posted": pruned}, f, indent=2)

def is_already_posted(posted_dict, asin):
    if asin not in posted_dict:
        return False
    try:
        posted_at = datetime.fromisoformat(posted_dict[asin])
        hours_ago = (datetime.now(timezone.utc) - posted_at).total_seconds() / 3600
        return hours_ago < EXPIRY_HOURS
    except Exception:
        return False

def make_link(asin):
    return f"https://www.amazon.eg/dp/{asin}?tag={AFFILIATE_TAG}"

def parse_price(text):
    if not text:
        return None
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    text = re.sub(r"[^\d.]", "", text.replace("EGP", "").replace("ج.م", "").replace(",", "").strip())
    try:
        return float(text) if text else None
    except ValueError:
        return None

def fetch_html(url):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=SCRAPE_HEADERS, timeout=25)
            print(f"  HTTP {r.status_code}")
            if r.status_code == 200:
                return r.text
        except Exception as e:
            print(f"  Fetch error (attempt {attempt+1}): {e}")
        time.sleep(4)
    return None

def get_product_screenshot_and_title(asin):
    url = f"https://www.amazon.eg/dp/{asin}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="ar-EG",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Extract Arabic title
            arabic_title = ""
            for sel in ["#productTitle", "h1 span", "h1"]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t:
                            arabic_title = t
                            break
                except Exception:
                    pass

            # Scroll to top so screenshot shows title + image + price
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            screenshot_bytes = page.screenshot(full_page=False)

            browser.close()
            return screenshot_bytes, arabic_title

    except Exception as e:
        print(f"  Browser error for {asin}: {e}")
        return None, ""

def parse_search_results(html, category):
    soup  = BeautifulSoup(html, "html.parser")
    items = []

    page_title = soup.title.get_text(strip=True) if soup.title else "no title"
    print(f"  Page title: {page_title}")

    cards = soup.select("div[data-component-type='s-search-result']")
    print(f"  Result cards found: {len(cards)}")

    for card in cards:
        try:
            asin = card.get("data-asin", "")
            if not asin or len(asin) != 10:
                continue

            title_el = card.select_one("h2 span") or card.select_one("h2 a span")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            current_price = None
            for price_el in card.select(".a-price:not(.a-text-price)"):
                raw = price_el.select_one(".a-offscreen")
                if raw:
                    val = parse_price(raw.get_text())
                    if val and val > 0:
                        current_price = val
                        break

            original_price = None
            for price_el in card.select(".a-text-price"):
                raw = price_el.select_one(".a-offscreen")
                if raw:
                    val = parse_price(raw.get_text())
                    if val and val > 0:
                        original_price = val
                        break

            discount_pct = 0
            badge = card.select_one(".a-badge-text") or card.select_one("[class*='savingsPercentage']")
            if badge:
                m = re.search(r"(\d+)", badge.get_text())
                if m:
                    discount_pct = int(m.group(1))
            if not discount_pct and current_price and original_price and original_price > current_price:
                discount_pct = round((original_price - current_price) / original_price * 100)

            if discount_pct < 5:
                continue

            img_el  = card.select_one("img.s-image") or card.select_one("img")
            img_url = ""
            if img_el:
                img_url = img_el.get("src", "")
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

def build_caption(item, arabic_title):
    name = arabic_title if arabic_title else item["title"]
    link = make_link(item["asin"])
    lines = []

    if item.get("discount_pct", 0) >= 10:
        lines.append(f"🔥 خصم {item['discount_pct']}% 🔥")

    lines.append(f"👑 عرض على {name}")
    lines.append("")

    if item.get("current_price") and item.get("original_price"):
        lines.append(
            f"💰 السعر: {item['current_price']:,.0f} جنيه"
            f" بدلا من {item['original_price']:,.0f} جنيه في موقعهم الرسمي"
        )
    elif item.get("current_price"):
        lines.append(f"💰 السعر: {item['current_price']:,.0f} جنيه")

    lines.append("")
    lines.append(f"لينك الشراء: {link}")
    return "\n".join(lines)

def post_item(item):
    print(f"  Launching browser for {item['asin']}...")
    screenshot_bytes, arabic_title = get_product_screenshot_and_title(item["asin"])
    print(f"  Arabic title: {arabic_title[:60] if arabic_title else 'not found'}")

    caption = build_caption(item, arabic_title)

    # Only use screenshot if we got a real product title (not a login/captcha page)
    photo_bytes = screenshot_bytes if arabic_title else None

    if not photo_bytes and item.get("img_url"):
        try:
            r = requests.get(item["img_url"], headers=SCRAPE_HEADERS, timeout=15)
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
        print(f"  sendPhoto failed: {r.json().get('description')} — trying text")

    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": DEST_CHANNEL, "text": caption},
        timeout=30,
    )
    return r.json().get("ok", False)

def main():
    state       = load_state()
    posted_dict = state.get("posted", {})
    total       = 0

    for category, url in CATEGORIES.items():
        if total >= MAX_PER_RUN:
            break

        print(f"\n{'='*40}")
        print(f"Category: {category}")

        html = fetch_html(url)
        if not html:
            print("  SKIPPED — fetch failed")
            continue

        items = parse_search_results(html, category)
        print(f"  Discounted items found: {len(items)}")

        for item in items:
            if total >= MAX_PER_RUN:
                break
            if is_already_posted(posted_dict, item["asin"]):
                print(f"  Skip {item['asin']} — posted within {EXPIRY_HOURS}h")
                continue

            ok = post_item(item)
            status = "OK" if ok else "FAILED"
            print(f"  [{status}] {item['asin']} | {item['discount_pct']}% off | {item['title'][:50]}")

            if ok:
                posted_dict[item["asin"]] = now_iso()
                state["posted"] = posted_dict
                total += 1

            time.sleep(2)

    save_state(state)
    print(f"\n{'='*40}")
    print(f"Done. Posted: {total}/{MAX_PER_RUN}")

if __name__ == "__main__":
    main()
