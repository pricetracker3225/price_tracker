"""
Daily price checker.

Reads products.json (your list of items), fetches the current price + photo
for each one, and appends today's reading to data/history.json.

Platforms handled:
  - shopify   : brand stores built on Shopify (Timex, Casio Store, etc).
                Reliable — uses Shopify's public {product}.json endpoint.
  - flipkart  : rendered with a real headless browser (Playwright), since
                Flipkart also blocks based on the requesting IP address.
                GitHub Actions IPs are commonly on that blocklist, so this
                may still fail with a 529 even though the code is correct.
  - myntra    : rendered with a real headless browser. Myntra loads its
                price via JavaScript, so this was the actual fix needed —
                should work reliably now.
  - amazon    : same headless-browser approach, resolves amzn.in short
                links automatically since the browser follows redirects.

Run manually:  python scraper.py
Run daily via: .github/workflows/track-prices.yml (GitHub Actions cron)
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.json"
HISTORY_FILE = ROOT / "data" / "history.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

# Known CSS selectors as a fallback when structured data (JSON-LD) isn't
# present. These WILL go stale when a site redesigns its page — that's
# normal, not a sign something is broken.
PRICE_SELECTORS = [
    "div._30jeq3",                                   # Flipkart (older layout)
    "div.Nx9bqj",                                     # Flipkart (newer layout)
    ".pdp-price strong",                               # Myntra
    "span.pdp-price",                                  # Myntra (alt)
    "#corePrice_feature_div .a-price .a-offscreen",    # Amazon
    ".a-price .a-offscreen",                           # Amazon (generic)
    "#priceblock_ourprice",                            # Amazon (older layout)
]
IMAGE_SELECTORS = [
    "img._396cs4",     # Flipkart
    "img._2r_T1I",     # Flipkart (alt)
    ".image-grid-image",  # Myntra (background-image, handled separately)
    "#landingImage",   # Amazon
    "#imgTagWrapperId img",  # Amazon (alt)
]


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def fetch_shopify(url):
    """Reliable path: Shopify stores expose /products/<handle>.json"""
    parsed = urlparse(url)
    handle = parsed.path.rstrip("/").split("/products/")[-1]
    json_url = f"{parsed.scheme}://{parsed.netloc}/products/{handle}.json"
    r = requests.get(json_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    product = r.json()["product"]
    variant = product["variants"][0]
    price = float(variant["price"])
    image = product["images"][0]["src"] if product.get("images") else None
    title = product.get("title")
    return price, image, title


def _extract_from_html(html, soup_hint_url=""):
    """Shared parsing logic: JSON-LD -> meta tags -> known CSS selectors."""
    soup = BeautifulSoup(html, "html.parser")
    price, image, title = None, None, None

    # 1. Try JSON-LD (schema.org Product data), when present
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string)
        except (TypeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            if entry.get("@type") == "Product":
                title = title or entry.get("name")
                image = image or (
                    entry["image"][0] if isinstance(entry.get("image"), list)
                    else entry.get("image")
                )
                offers = entry.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict) and offers.get("price"):
                    price = float(offers["price"])

    # 2. Fall back to meta tags
    if price is None:
        for prop in ["product:price:amount", "og:price:amount"]:
            tag = soup.find("meta", {"property": prop})
            if tag and tag.get("content"):
                price = float(re.sub(r"[^\d.]", "", tag["content"]))
                break

    if image is None:
        tag = soup.find("meta", {"property": "og:image"})
        if tag:
            image = tag.get("content")

    if title is None:
        tag = soup.find("meta", {"property": "og:title"}) or soup.find("title")
        title = tag.get("content") if tag and tag.get("content") else (tag.text if tag else None)

    # 3. Fall back to known CSS selectors (site-specific, may go stale)
    if price is None:
        for sel in PRICE_SELECTORS:
            tag = soup.select_one(sel)
            if tag and tag.get_text(strip=True):
                digits = re.sub(r"[^\d.]", "", tag.get_text())
                if digits:
                    price = float(digits)
                    break

    # 4. Last resort: SPAs (Myntra especially) often embed the page's data
    # as raw JSON inside a <script> tag for hydration. Search the whole
    # rendered HTML source for common price keys even if they never made
    # it into a visible DOM element.
    if price is None:
        for key in ["discountedPrice", "sellingPrice", "finalPrice", "offerPrice", "price"]:
            m = re.search(rf'"{key}"\s*:\s*"?(\d{{2,7}}(?:\.\d+)?)"?', html)
            if m:
                price = float(m.group(1))
                break

    if image is None:
        for sel in IMAGE_SELECTORS:
            tag = soup.select_one(sel)
            if tag:
                image = tag.get("src") or tag.get("data-src")
                if image:
                    break

    if price is None:
        raise ValueError("Could not find a price on this page")

    return price, image, title


_browser_ctx = {"pw": None, "browser": None}


def _get_browser():
    """Reuse one headless browser instance across all JS-rendered fetches."""
    if _browser_ctx["browser"] is None:
        _browser_ctx["pw"] = sync_playwright().start()
        _browser_ctx["browser"] = _browser_ctx["pw"].chromium.launch(headless=True)
    return _browser_ctx["browser"]


def close_browser():
    if _browser_ctx["browser"]:
        _browser_ctx["browser"].close()
    if _browser_ctx["pw"]:
        _browser_ctx["pw"].stop()


def fetch_rendered(url):
    """
    Loads the page in a real headless Chrome browser so JavaScript-rendered
    prices (Myntra, Amazon) actually appear before we read the page.
    Flipkart may still return a 529 here — that's IP-based blocking, which
    rendering JS doesn't get around.
    """
    browser = _get_browser()
    page = browser.new_page(
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 1280, "height": 1600},
        locale="en-IN",
        extra_http_headers={"Accept-Language": "en-IN,en;q=0.9"},
    )
    # Hide the most obvious automation fingerprint (navigator.webdriver),
    # which some sites (Myntra included) check before deciding whether to
    # serve a full page or a stripped-down one.
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass  # some pages never go fully idle (ads/trackers); proceed anyway
        page.wait_for_timeout(2000)  # small extra buffer for late-rendering price
        html = page.content()
    finally:
        page.close()

    return _extract_from_html(html)


def fetch_price(product):
    platform = product["platform"]
    if platform == "shopify":
        return fetch_shopify(product["url"])
    return fetch_rendered(product["url"])


def main():
    products = load_json(PRODUCTS_FILE, [])
    history = load_json(HISTORY_FILE, {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    ok, failed = 0, 0

    for product in products:
        pid = product["id"]
        record = history.setdefault(pid, {
            "name": product["name"],
            "url": product["url"],
            "platform": product["platform"],
            "image": None,
            "readings": []  # list of {date, price}
        })

        try:
            price, image, live_title = fetch_price(product)
            if image:
                record["image"] = image
            if live_title and "confirm name" in record["name"].lower():
                record["name"] = live_title.strip()

            # Only append if it's a new day or price changed
            if not record["readings"] or record["readings"][-1]["date"] != today:
                record["readings"].append({"date": today, "price": price})
            else:
                record["readings"][-1]["price"] = price

            record["last_ok"] = today
            record["last_status"] = "ok"
            ok += 1
            print(f"[OK]     {product['name'][:50]:50s} -> Rs.{price}")

        except Exception as e:
            record["last_status"] = "failed"
            failed += 1
            print(f"[FAILED] {product['name'][:50]:50s} -> {e}")

    close_browser()
    save_json(HISTORY_FILE, history)
    print(f"\nDone. {ok} succeeded, {failed} failed. Data saved to {HISTORY_FILE}")

    if failed and ok == 0:
        sys.exit(1)  # only fail the whole run if literally everything broke


if __name__ == "__main__":
    main()
