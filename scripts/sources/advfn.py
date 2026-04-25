"""
ADVFN UK scraper for IMB buyback filings.

Per-company URL structure:
  Listing:  https://uk.advfn.com/stock-market/london/imperial-brands-IMB/share-news?type=transaction-in-own-shares
  Filing:   https://uk.advfn.com/stock-market/london/imperial-brands-IMB/share-news/.../{numeric_id}

The numeric IDs are sequential per-company (not global like Investegate),
so we cannot accidentally hit other companies' filings.

ADVFN delivers the FULL RNS text in the HTML response (not JS-rendered),
which makes parsing reliable.
"""

import re
import time
from typing import Optional
import urllib.request
import urllib.error
from .base import Announcement, HEADERS


SCRAPER_HEADERS = {
    **HEADERS,
    # ADVFN does not require special cookies, but a real-looking referer helps
    "Referer": "https://uk.advfn.com/",
}

# Listing page (filtered to Transaction in Own Shares is via query param)
LISTING_URL = "https://uk.advfn.com/stock-market/london/imperial-brands-IMB/share-news"

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch_advfn_html(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch ADVFN HTML."""
    try:
        req = urllib.request.Request(url, headers=SCRAPER_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"    ! HTTP {e.code} for {url}")
        return None
    except Exception as e:
        print(f"    ! {type(e).__name__}: {e}")
        return None


def get_listing_filing_urls(max_pages: int = 5,
                             request_delay: float = 1.0) -> list[str]:
    """
    Paginate ADVFN's listing page to get filing URLs.

    Returns full URLs of "Transaction in Own Shares" filings.
    """
    all_urls = []
    seen = set()

    # ADVFN uses ?p=N for pagination
    for page in range(1, max_pages + 1):
        page_url = f"{LISTING_URL}?p={page}"
        html = fetch_advfn_html(page_url)
        if not html:
            print(f"    Page {page}: fetch failed, stopping")
            break

        # Match: /stock-market/london/imperial-brands-IMB/share-news/.../{NUMERIC_ID}
        # Specifically only "Transaction-in-Own-Shares" links
        link_pattern = (
            r'(/stock-market/london/imperial-brands-IMB/share-news/'
            r'[^"\']*?[Tt]ransaction[^"\']*?[Oo]wn[^"\']*?[Ss]hares[^"\']*?/\d+)'
        )
        matches = re.findall(link_pattern, html)
        new_urls = []
        for path in matches:
            full = f"https://uk.advfn.com{path}"
            if full not in seen:
                seen.add(full)
                new_urls.append(full)
                all_urls.append(full)

        if not new_urls:
            print(f"    Page {page}: no new filings, reached end")
            break

        print(f"    Page {page}: {len(new_urls)} new ({len(all_urls)} total)")
        time.sleep(request_delay)

    return all_urls


def parse_announcement(url: str) -> Optional[Announcement]:
    """Parse a single ADVFN Transaction in Own Shares page."""
    html = fetch_advfn_html(url)
    if not html:
        return None

    # Issuer validation
    html_lower = html.lower()
    if not (
        "549300dfvpob67jl3a42" in html_lower   # IMB LEI
        or "imperial brands plc" in html_lower
    ):
        return None

    # Strip script/style blocks before tag-stripping (defense, even though ADVFN is server-rendered)
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)

    # Extract URL ID for stable rns_id
    id_match = re.search(r"/(\d+)/?$", url)
    advfn_id = id_match.group(1) if id_match else url.split("/")[-1]

    # ── DATE ──
    # ADVFN uses very explicit formatting: "Date of transaction: 24 April 2026"
    date_patterns = [
        r"Date of transaction\s*:?\s*(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\s+it\s+purchased",
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
    ]
    dato = None
    for pat in date_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        try:
            day, month_str, year = m.groups()
            year_int = int(year)
            if 2010 <= year_int <= 2030:
                dato = f"{year}-{MONTHS[month_str.lower()]:02d}-{int(day):02d}"
                break
        except (KeyError, ValueError):
            continue
    if not dato:
        return None

    # ── SHARES PURCHASED ──
    # ADVFN: "Number of shares repurchased: 179,206"
    share_patterns = [
        r"Number of shares (?:re)?purchased\s*:?\s*(\d[\d,]+)",
        r"Number of securities purchased\s*:?\s*(\d[\d,]+)",
        r"purchased for cancellation[^.]*?(\d[\d,]+)\s+(?:of its )?ordinary shares",
    ]
    antal = None
    for pat in share_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cand = int(m.group(1).replace(",", ""))
                if cand > 100:
                    antal = cand
                    break
            except ValueError:
                continue
    if not antal:
        return None

    # ── AVERAGE PRICE ──
    # ADVFN: "Average price paid per share: GBp 2,768.2686"
    price_patterns = [
        r"Average price paid per share\s*:?\s*(?:GBp?\s+)?(\d[\d,]*\.\d+)",
        r"Volume[\- ]weighted average price[^:]*:\s*(?:GBp?\s+)?(\d[\d,]*\.\d+)",
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
    ]
    gns_kurs = 0.0
    for pat in price_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cand = float(m.group(1).replace(",", ""))
                if 500 < cand < 10000:
                    gns_kurs = cand
                    break
            except ValueError:
                continue
    if gns_kurs == 0:
        return None

    # ── SHARES IN ISSUE AFTER ──
    after_patterns = [
        r"remaining number of ordinary shares in issue will be\s+(\d[\d,]+)",
        r"ordinary shares in issue\s+(?:will be|is now|is)\s+(\d[\d,]+)",
        r"shares in issue.*?(\d{3}[\d,]{5,})",
    ]
    aktier_efter = None
    for pat in after_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                n = int(m.group(1).replace(",", ""))
                if 500_000_000 < n < 1_500_000_000:
                    aktier_efter = n
                    break
            except ValueError:
                continue

    beloeb = round(antal * gns_kurs / 100 / 1e6, 1)

    return Announcement(
        dato=dato,
        antal_aktier=antal,
        gns_kurs_gbp=gns_kurs,
        beloeb_gbp_mio=beloeb,
        aktier_efter=aktier_efter,
        rns_id=f"advfn_{advfn_id}",
        source_url=url,
    )


def scrape_new_filings(known_ids: set = None,
                       max_pages: int = 5,
                       request_delay: float = 1.0) -> list[Announcement]:
    """
    Scrape ADVFN listing for IMB Transaction in Own Shares filings.

    `max_pages` paginates through listing (each page ~20 filings).
    Default 5 = ~100 filings (good for most updates and partial backfill).
    For full FY24+ coverage use max_pages=20.
    """
    if known_ids is None:
        known_ids = set()

    print(f"  Fetching listing from ADVFN (max {max_pages} pages)...")
    urls = get_listing_filing_urls(max_pages=max_pages, request_delay=request_delay)
    print(f"  Found {len(urls)} filing URLs in listing")

    # Skip already-known
    new_urls = [u for u in urls
                if f"advfn_{u.rstrip('/').split('/')[-1]}" not in known_ids]
    print(f"  {len(new_urls)} new (skipping {len(urls) - len(new_urls)} already in data.json)")

    announcements = []
    hits = 0
    for i, url in enumerate(new_urls):
        ann = parse_announcement(url)
        if ann:
            announcements.append(ann)
            hits += 1
            print(f"    ✓ {ann.dato} | {ann.antal_aktier:,} @ {ann.gns_kurs_gbp:.2f}p = £{ann.beloeb_gbp_mio}M")
        time.sleep(request_delay)
        if (i + 1) % 10 == 0:
            print(f"    [{i+1}/{len(new_urls)}] {hits} hits")

    print(f"  Parsed {hits}/{len(new_urls)} buyback filings")
    return announcements
