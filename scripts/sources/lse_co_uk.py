"""
LSE.co.uk primary scraper for IMB buyback filings.

URL STRUCTURE (much cleaner than Investegate):
  Listing (all RNS for IMB):
    https://www.lse.co.uk/rns/IMB.html?page=1

  Category-filtered (just Transaction in Own Shares):
    https://www.lse.co.uk/rns/IMB/rns-category.html?category=Transaction%20in%20Own%20Shares&page=1

  Individual announcement:
    https://www.lse.co.uk/rns/IMB/transaction-in-own-shares-{hash}.html

The {hash} is a random alphanumeric string — NOT sequential like Investegate.
But since URLs are scoped to /rns/IMB/, we CANNOT accidentally scrape
another company's filings. This alone makes LSE.co.uk far more robust.

AUTHENTICATION:
LSE.co.uk shows a "private investor" popup. We bypass it by sending
a cookie `lse_private_investor=1`.

STRATEGY:
1. Fetch listing page
2. Extract all announcement URLs (hash strings)
3. Fetch each individual page, parse transaction data
4. Dedup via URL hash (acts as our stable ID)
"""

import re
import time
from typing import Optional
import urllib.request
from .base import Announcement, HEADERS


# Send private-investor consent cookie to bypass popup
SCRAPER_HEADERS = {**HEADERS, "Cookie": "lse_private_investor=1"}

# Listing URL with category filter
LISTING_URL_TMPL = "https://www.lse.co.uk/rns/IMB/rns-category.html?category=Transaction%20in%20Own%20Shares&page={page}"

# Individual announcement URL pattern (for parsing)
ANNOUNCEMENT_URL_TMPL = "https://www.lse.co.uk/rns/IMB/transaction-in-own-shares-{hash}.html"

# Pattern for finding announcement URLs within the listing HTML
LINK_PATTERN = r'/rns/IMB/transaction-in-own-shares-([a-z0-9]{10,})\.html'

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch_lse_html(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch LSE.co.uk HTML with private-investor cookie."""
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


def get_all_listing_hashes(max_pages: int = 20, delay: float = 1.0) -> list[str]:
    """
    Paginate through LSE.co.uk's Transaction in Own Shares listings for IMB.
    Returns all unique hash strings newest-first.

    max_pages: Safety cap. 20 pages × 15 filings/page ≈ 300 filings, enough for FY24 onwards.
    delay: Seconds between page requests (LSE.co.uk is more lenient than Investegate,
           but we're still polite).
    """
    all_hashes = []
    seen = set()

    for page in range(1, max_pages + 1):
        url = LISTING_URL_TMPL.format(page=page)
        html = fetch_lse_html(url)
        if not html:
            print(f"    Page {page}: fetch failed, stopping")
            break

        matches = re.findall(LINK_PATTERN, html, re.IGNORECASE)
        new_hashes = [m for m in matches if m not in seen]

        if not new_hashes:
            # Empty page — we've gone past the last real page
            print(f"    Page {page}: no new announcements, reached end")
            break

        for h in new_hashes:
            seen.add(h)
            all_hashes.append(h)

        print(f"    Page {page}: {len(new_hashes)} new ({len(all_hashes)} total)")
        time.sleep(delay)

    return all_hashes


def parse_announcement(url_hash: str) -> Optional[Announcement]:
    """Parse a single LSE.co.uk Transaction in Own Shares page."""
    url = ANNOUNCEMENT_URL_TMPL.format(hash=url_hash)
    html = fetch_lse_html(url)
    if not html:
        return None

    # Issuer validation (defence in depth — URL is already /rns/IMB/ but sanity check)
    html_lower = html.lower()
    if not (
        "549300dfvpob67jl3a42" in html_lower   # IMB LEI
        or "gb0004544929" in html_lower         # IMB ISIN
        or "imperial brands plc" in html_lower  # Full name
        or "imperial tobacco group" in html_lower  # Old name (pre-2016) for historical filings
    ):
        return None

    # Strip HTML tags for clean regex matching
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    # ── DATE ──
    date_patterns = [
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
    ]
    dato = None
    for pat in date_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        groups = m.groups()
        try:
            if groups[0].isdigit():
                day, month_str, year = groups
            else:
                month_str, day, year = groups
            dato = f"{year}-{MONTHS[month_str.lower()]:02d}-{int(day):02d}"
            break
        except (KeyError, ValueError):
            continue
    if not dato:
        return None

    # ── SHARES PURCHASED ──
    share_patterns = [
        r"(?:Number of shares (?:re)?purchased|Number of securities purchased)\s*:?\s*(\d[\d,]+)",
        r"(?:purchased|repurchased)\s+(?:for\s+cancellation\s+)?(\d[\d,]+)\s+(?:of\s+its\s+)?ordinary",
        r"repurchased\s+(\d[\d,]+)\s+ordinary",
        r"purchased\s+(?:the\s+following\s+number\s+of\s+its\s+)?(?:ordinary\s+shares.*?:\s*)?(\d[\d,]+)",
    ]
    antal = None
    for pat in share_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cand = int(m.group(1).replace(",", ""))
                if cand > 100:  # Sanity: real transactions are in thousands+
                    antal = cand
                    break
            except ValueError:
                continue
    if not antal:
        return None

    # ── AVERAGE PRICE (GBp) ──
    price_patterns = [
        r"(?:Volume\s+[Ww]eighted\s+)?[Aa]verage\s+price\s+(?:paid\s+)?(?:per\s+[Ss]hare\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        r"weighted\s+average\s+price\s*\([^)]+\)\s*[:\|]?\s*(\d[\d,]*\.\d+)",
        r"at\s+an?\s+average\s+price\s+of\s+(\d[\d,]*\.\d+)\s*(?:pence|GBp|p\b)",
        r"GB[pPxX]\s+(\d[\d,]*\.\d+)",
        r"(\d[\d,]*\.\d+)\s*(?:pence|p)\s+per\s+(?:ordinary\s+)?share",
    ]
    gns_kurs = 0.0
    for pat in price_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cand = float(m.group(1).replace(",", ""))
                # IMB has historically traded 1500-4500p — loose sanity check only
                if 500 < cand < 10000:
                    gns_kurs = cand
                    break
            except ValueError:
                continue
    if gns_kurs == 0:
        return None

    # ── SHARES IN ISSUE AFTER ──
    after_patterns = [
        r"(?:remaining\s+)?(?:total\s+)?(?:number\s+of\s+)?ordinary\s+shares\s+in\s+issue\s+"
        r"(?:will\s+be|is\s+now|is)\s+(\d[\d,]+)",
        r"shares\s+in\s+issue.*?(\d{3}[\d,]{5,})",  # Require at least 9 digits
    ]
    aktier_efter = None
    for pat in after_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                n = int(m.group(1).replace(",", ""))
                if 500_000_000 < n < 1_500_000_000:  # IMB historically 800-900M
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
        rns_id=f"lse_{url_hash}",  # Stable ID based on URL hash
        source_url=url,
    )


def scrape_new_filings(known_hashes: set = None,
                       max_pages: int = 3,
                       request_delay: float = 1.0) -> list[Announcement]:
    """
    Scrape IMB buyback filings from LSE.co.uk, skipping already-known hashes.

    Default max_pages=3 is enough for normal daily updates (each page has ~15 filings).
    For backfill, increase max_pages up to 20.
    """
    if known_hashes is None:
        known_hashes = set()

    print(f"  Fetching listing from LSE.co.uk (max {max_pages} pages)...")
    hashes = get_all_listing_hashes(max_pages=max_pages, delay=request_delay)
    print(f"  Found {len(hashes)} total announcements in listing")

    # Skip already-known
    new_hashes = [h for h in hashes if f"lse_{h}" not in known_hashes]
    print(f"  {len(new_hashes)} new (skipping {len(hashes) - len(new_hashes)} already in data.json)")

    announcements = []
    hits = 0
    for i, url_hash in enumerate(new_hashes):
        ann = parse_announcement(url_hash)
        if ann:
            announcements.append(ann)
            hits += 1
            print(f"    ✓ {ann.dato} | {ann.antal_aktier:,} @ {ann.gns_kurs_gbp:.2f}p = £{ann.beloeb_gbp_mio}M")
        else:
            print(f"    ✗ Parse failed for {url_hash}")
        time.sleep(request_delay)
        if (i + 1) % 10 == 0:
            print(f"    [{i+1}/{len(new_hashes)}] {hits} hits")

    print(f"  Parsed {hits}/{len(new_hashes)} buyback filings")
    return announcements
