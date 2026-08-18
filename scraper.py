import os
import json
import time
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# ─── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
CHANNEL      = "@kashafdeals"
AFFILIATE    = "kashafdeals-21"
STATE_FILE   = "state.json"
MAX_PER_RUN  = int(os.environ.get("MAX_PER_RUN", "5"))
EXPIRY_HOURS = 48
MIN_DISCOUNT = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# ─── 14 departments × 2 pages each ───────────────────────────────────────────
DEAL_FILTER = "p_n_deal_type%3A26462622031"

CATEGORIES = {
    "إلكترونيات": [
        f"https://www.amazon.eg/s?i=electronics&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=electronics&rh={DEAL_FILTER}&page=2",
    ],
    "موبايل وتابلت": [
        f"https://www.amazon.eg/s?i=mobile&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=mobile&rh={DEAL_FILTER}&page=2",
    ],
    "كمبيوتر ولابتوب": [
        f"https://www.amazon.eg/s?i=computers&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=computers&rh={DEAL_FILTER}&page=2",
    ],
    "منزل ومطبخ": [
        f"https://www.amazon.eg/s?i=kitchen&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=kitchen&rh={DEAL_FILTER}&page=2",
    ],
    "جمال وعناية": [
        f"https://www.amazon.eg/s?i=beauty&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=beauty&rh={DEAL_FILTER}&page=2",
    ],
    "أطفال ورضع": [
        f"https://www.amazon.eg/s?i=baby&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=baby&rh={DEAL_FILTER}&page=2",
    ],
    "رياضة وهواء طلق": [
        f"https://www.amazon.eg/s?i=sporting-goods&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=sporting-goods&rh={DEAL_FILTER}&page=2",
    ],
    "كتب": [
        f"https://www.amazon.eg/s?i=stripbooks&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=stripbooks&rh={DEAL_FILTER}&page=2",
    ],
    "ألعاب أطفال": [
        f"https://www.amazon.eg/s?i=toys&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=toys&rh={DEAL_FILTER}&page=2",
    ],
    "ملابس وأزياء": [
        f"https://www.amazon.eg/s?i=fashion&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=fashion&rh={DEAL_FILTER}&page=2",
    ],
    "بقالة وطعام": [
        f"https://www.amazon.eg/s?i=grocery&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=grocery&rh={DEAL_FILTER}&page=2",
    ],
    "سيارات": [
        f"https://www.amazon.eg/s?i=automotive&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=automotive&rh={DEAL_FILTER}&page=2",
    ],
    "صحة وعناية شخصية": [
        f"https://www.amazon.eg/s?i=hpc&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=hpc&rh={DEAL_FILTER}&page=2",
    ],
    "مستلزمات مكتبية": [
        f"https://www.amazon.eg/s?i=office-products&rh={DEAL_FILTER}&page=1",
        f"https://www.amazon.eg/s?i=office-products&rh={DEAL_FILTER}&page=2",
    ],
}

# ─── State helpers ────────────────────────────────────────────────────────────

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return data.get("posted", {})
        except Exception:
            return {}

def save_state(posted: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"posted": posted}, f, ensure_ascii=False, indent=2)

def is_posted(asin: str, posted: dict) -> bool:
    if asin not in posted:
        return False
    ts = posted[asin]
    try:
        t = datetime.fromisoformat(ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - t < timedelta(hours=EXPIRY_HOURS)
    except Exception:
        return False

def mark_posted(asin: str):
    posted = load_state()
    posted[asin] = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc)
    clean = {}
    for k, v in posted.items():
        try:
            t = datetime.fromisoformat(v)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if now - t < timedelta(hours=EXPIRY_HOURS * 2):
                clean[k] = v
        except Exception:
            pass
    save_state(clean)

# ─── Scrape one search-results page ──────────────────────────────────────────

def parse_price(text: str) -> float:
    text = (text
            .replace("EGP", "")
            .replace("ج.م", "")
            .replace(",", "")
            .replace("٬", "")
            .strip())
    text = re.sub(r"[^\d.]", "", text)
    try:
        return float(text)
    except ValueError:
        return 0.0

def scrape_category(url: str) -> list:
    candidates = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] fetch failed {url}: {e}")
        return candidates

    soup = BeautifulSoup(resp.text, "html.parser")

    for item in soup.select("[data-asin]"):
        asin = item.get("data-asin", "").strip()
        if not asin or len(asin) < 8:
            continue

        price_el = item.select_one(".a-price .a-offscreen")
        if not price_el:
            continue
        price = parse_price(price_el.get_text())
        if price <= 0:
            continue

        orig_price = 0.0
        for el in item.select(".a-price.a-text-price .a-offscreen"):
            v = parse_price(el.get_text())
            if v > price:
                orig_price = v
                break

        if orig_price <= price:
            badge = item.select_one(".savingsPercentage, .a-badge-text")
            if badge:
                m = re.search(r"(\d+)%", badge.get_text())
                if m:
                    pct = int(m.group(1))
                    if pct >= MIN_DISCOUNT:
                        orig_price = round(price / (1 - pct / 100), 2)
            if orig_price <= price:
                continue

        discount_pct = round((orig_price - price) / orig_price * 100)
        if discount_pct < MIN_DISCOUNT:
            continue

        candidates.append({
            "asin":         asin,
            "price":        price,
            "orig_price":   orig_price,
            "discount_pct": discount_pct,
        })

    return candidates

# ─── Product page: Arabic title + screenshot via Playwright ──────────────────

def get_product_details(asin: str, page):
    url = (
        f"https://www.amazon.eg/dp/{asin}"
        f"?tag={AFFILIATE}&language=ar_AE"
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)

        title = ""
        for sel in ["#productTitle", "#title span", "h1.a-size-large"]:
            el = page.query_selector(sel)
            if el:
                t = el.inner_text().strip()
                if re.search(r"[\u0600-\u06FF]", t):
                    title = t
                    break

        if not title:
            print(f"  [SKIP] {asin} — no Arabic title")
            return None

        screenshot_path = f"/tmp/{asin}.png"
        page.screenshot(path=screenshot_path, full_page=False)

        return {"title": title, "screenshot": screenshot_path}

    except Exception as e:
        print(f"  [WARN] product page error {asin}: {e}")
        return None

# ─── Telegram post ────────────────────────────────────────────────────────────

def send_telegram(asin, title, price, orig_price, discount_pct, screenshot):
    affiliate_url = f"https://www.amazon.eg/dp/{asin}?tag={AFFILIATE}"
    caption = (
        f"🔥 خصم {discount_pct}% 🔥\n"
        f"\n"
        f"👑 عرض على {title}\n"
        f"\n"
        f"💰 السعر: {price:,.0f} جنيه بدلا من {orig_price:,.0f} جنيه\n"
        f"\n"
        f"🛒 لينك الشراء: {affiliate_url}"
    )

    api = f"https://api.telegram.org/bot{BOT_TOKEN}"

    try:
        with open(screenshot, "rb") as photo:
            resp = requests.post(
                f"{api}/sendPhoto",
                data={"chat_id": CHANNEL, "caption": caption},
                files={"photo": photo},
                timeout=30,
            )
        if resp.status_code == 200 and resp.json().get("ok"):
            print(f"  [OK] {asin} — sent with photo")
            return True
        print(f"  [WARN] sendPhoto failed: {resp.text[:200]}")
    except Exception as e:
        print(f"  [WARN] sendPhoto exception: {e}")

    try:
        resp = requests.post(
            f"{api}/sendMessage",
            json={
                "chat_id": CHANNEL,
                "text": caption,
                "disable_web_page_preview": False,
            },
            timeout=30,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            print(f"  [OK] {asin} — sent text-only")
            return True
        print(f"  [ERR] sendMessage failed: {resp.text[:200]}")
    except Exception as e:
        print(f"  [ERR] sendMessage exception: {e}")

    return False

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[START] {datetime.now(timezone.utc).isoformat()}  MAX_PER_RUN={MAX_PER_RUN}")

    posted_snapshot = load_state()

    seen: set = set()
    candidates: list = []

    for cat_name, urls in CATEGORIES.items():
        print(f"[CAT] {cat_name}")
        for url in urls:
            items = scrape_category(url)
            print(f"  page {url.split('page=')[-1]} → {len(items)} hits")
            for item in items:
                asin = item["asin"]
                if asin in seen:
                    continue
                if is_posted(asin, posted_snapshot):
                    continue
                seen.add(asin)
                item["category"] = cat_name
                candidates.append(item)
            time.sleep(1.5)

    candidates.sort(key=lambda x: x["discount_pct"], reverse=True)
    print(f"[INFO] {len(candidates)} unique new candidates")

    if not candidates:
        print("[DONE] Nothing new to post.")
        return

    posted_count = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ar-EG",
            extra_http_headers={"Accept-Language": "ar-EG,ar;q=0.9"},
        )
        page = ctx.new_page()

        for c in candidates:
            if posted_count >= MAX_PER_RUN:
                break

            asin         = c["asin"]
            price        = c["price"]
            orig_price   = c["orig_price"]
            discount_pct = c["discount_pct"]

            if is_posted(asin, load_state()):
                print(f"  [SKIP] {asin} — posted in a parallel check")
                continue

            print(f"[ITEM] {asin}  {discount_pct}% off  {price} ← was {orig_price}")

            details = get_product_details(asin, page)
            if not details:
                continue

            ok = send_telegram(
                asin, details["title"],
                price, orig_price, discount_pct,
                details["screenshot"],
            )
            if ok:
                mark_posted(asin)
                posted_count += 1
                time.sleep(3)

        ctx.close()
        browser.close()

    print(f"[DONE] Posted {posted_count} of {MAX_PER_RUN} allowed.")


if __name__ == "__main__":
    main()
