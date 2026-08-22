"""
Daily price checker.

Reads products.json (your list of items), fetches the current price + photo
for each one, and appends today's reading to data/history.json.

Platforms handled:
  - shopify   : brand stores built on Shopify (Timex, Casio Store, etc).
                Reliable — uses Shopify's public {product}.json endpoint.
  - flipkart  : best-effort HTML scraping. Flipkart actively blocks bots,
                so this WILL fail sometimes. On failure we just keep
                yesterday's price and flag it as "stale" on the page.
  - myntra    : same best-effort approach as Flipkart, same caveat.
  - amazon    : same best-effort approach, resolves amzn.in short links first.

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


def fetch_generic(url):
    """
    Best-effort path for Flipkart / Myntra / Amazon.
    Tries JSON-LD structured data first, then falls back to meta tags.
    No guarantees — these sites actively resist scraping.
    """
    r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

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

    if price is None:
        raise ValueError("Could not find a price on this page")

    return price, image, title


def fetch_price(product):
    platform = product["platform"]
    if platform == "shopify":
        return fetch_shopify(product["url"])
    return fetch_generic(product["url"])


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

    save_json(HISTORY_FILE, history)
    print(f"\nDone. {ok} succeeded, {failed} failed. Data saved to {HISTORY_FILE}")

    if failed and ok == 0:
        sys.exit(1)  # only fail the whole run if literally everything broke


if __name__ == "__main__":
    main()
