# Watchlist Price Tracker

A personal page that checks the price of your watches (and anything else)
once a day and shows you the current price, photo, and how it compares to
when you started tracking.

## Honest read on your product list first

Your 16 links split into two buckets:

**Reliable, fully automatic (4 items)** — Timex (shop.timexindia.com) and
Casio Store (casiostore.bhawar.com) are both built on Shopify, which
publishes a clean, public price feed for every product. These will update
every day without issue.

**Best-effort (12 items)** — Flipkart, Myntra, and Amazon links. These
sites actively try to block automated checking (captchas, bot detection).
The scraper will still try every day and usually succeed, but expect it to
fail silently on some days — that's normal, not a bug. When it fails, the
page just keeps showing the last price it saw and marks the card "stale"
so you know not to fully trust that number that day.

If you want rock-solid tracking specifically for the Flipkart/Myntra/Amazon
items, keep using Buyhatke's Wishlist (buyhatke.com/wishlist) or Keepa (for
Amazon) alongside this — they have infrastructure dedicated to fighting
that blocking that a personal script won't match. This page is still useful
as your single daily glance across everything.

## What's in this folder

- `products.json` — your product list. Edit this anytime to add/remove items.
- `scraper.py` — fetches today's price for every product.
- `data/history.json` — where price history builds up, day by day (starts empty).
- `index.html` — the actual page you'll look at.
- `.github/workflows/track-prices.yml` — runs the scraper automatically, daily.

## Setup (15 minutes, one-time)

1. **Create a free GitHub account** at github.com if you don't have one.

2. **Create a new repository**: click the "+" top-right → "New repository".
   Name it something like `price-tracker`. Set it to **Public** (required
   for free GitHub Pages). Don't add a README/gitignore — leave it empty.

3. **Upload these files**: on your new repo's page, click "Add file" →
   "Upload files", then drag in everything from this folder (keep the
   folder structure — `.github/workflows/track-prices.yml` and
   `data/history.json` need to land in those exact subfolders). Commit.

4. **Turn on GitHub Pages**: go to repo Settings → Pages → under "Build and
   deployment", set Source to "Deploy from a branch", Branch to `main`,
   folder `/ (root)`. Save. GitHub will give you a URL like
   `https://yourusername.github.io/price-tracker/` — that's your page,
   bookmark it.

5. **Run the scraper for the first time**: go to the "Actions" tab in your
   repo → click "Daily Price Check" on the left → click "Run workflow" →
   "Run workflow" again to confirm. Wait ~30 seconds, refresh — it should
   show a green checkmark. This fills in `data/history.json` with today's
   prices.

6. **Check your page**: open the GitHub Pages URL from step 4. You should
   see all 16 products with today's price. Photos and prices for the
   Flipkart/Myntra/Amazon items will only appear once the scraper
   successfully reads them (may take a couple of days for stubborn ones).

That's it — from tomorrow, it checks prices on its own every day at
8:30 AM IST, and your page always shows the latest.

## Adjusting things later

- **Add/remove a product**: edit `products.json`, commit the change.
- **Change the check time**: edit the `cron` line in
  `.github/workflows/track-prices.yml` (currently `0 3 * * *` = 3:00 UTC =
  8:30 AM IST).
- **A Flipkart/Myntra link keeps failing**: open the Actions tab → click the
  latest run → read the log for that product's error. Sites change their
  page structure sometimes; ping me with the error and I'll patch `scraper.py`.
