"""
ADVFN UK scraper for IMB buyback filings.

LISTING URL (Regulatory News only, paginated):
  https://uk.advfn.com/p.php?pid=news&symbol=L^IMB&old_sources=RN&p_n={page}

Each listing page returns ~25 filings (all RNS types — buybacks, AGM,
director declarations, etc.). We filter to "Transaction in Own Shares"
during parsing.

FILING URL (per filing detail):
  https://uk.advfn.com/stock-market/london/imperial-brands-IMB/share-news/Imperial-Brands-PLC-Transaction-in-Own-Shares/{id}

The HTML link in listing uses /share-market/ but the canonical URL uses
/stock-market/. Both work — we use whatever the listing gives us.

DEDUP:
We use `advfn_{numeric_id}` as the rns_id. Numeric IDs are per-company
sequential, so no risk of cross-issuer contamination.
"""

import re
import time
from typing import Optional
import urllib.request
import urllib.error
from .base import Announcement, HEADERS


SCRAPER_HEADERS = {
    **HEADERS,
    "Referer": "https://uk.advfn.com/",
}

# RNS-only listing with pagination
# Note: "L^IMB" gets URL-encoded as "L%5EIMB"
LISTING_URL_TMPL = (
    "https://uk.advfn.com/p.php?pid=news&symbol=L%5EIMB"
    "&old_sources=RN&p_n={page}"
)

# Pattern for finding filing links in the listing page
# Matches: /share-market/london/imperial-brands-IMB/share-news/...Transaction-in-Own-Shares/{id}
# OR:      /stock-market/london/imperial-brands-IMB/share-news/...Transaction-in-Own-Shares/{id}
LISTING_LINK_PATTERN = (
    r'/(?:share|stock)-market/london/imperial-brands-IMB/share-news/'
    r'Imperial-Brands-PLC-Transaction-in-Own-Shares/(\d+)'
)

# URL template for individual filing pages (canonical)
FILING_URL_TMPL = (
    "https://uk.advfn.com/stock-market/london/imperial-brands-IMB/"
    "share-news/Imperial-Brands-PLC-Transaction-in-Own-Shares/{id}"
)

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


def get_filing_ids_from_listing(max_pages: int = 5,
                                 request_delay: float = 1.0) -> list[int]:
    """
    Paginate through ADVFN's listing of IMB regulatory news.
    Returns numeric filing IDs for "Transaction in Own Shares" only.

    Each page contains ~25 RNS filings. We filter to buybacks only.
    Returns IDs ordered by appearance (newest first).
    """
    all_ids = []
    seen = set()

    for page in range(1, max_pages + 1):
        url = LISTING_URL_TMPL.format(page=page)
        print(f"    Fetching listing page {page}: {url[:80]}...")
        html = fetch_advfn_html(url)
        if not html:
            print(f"    Page {page}: fetch failed, stopping")
            break

        # Find all transaction-in-own-shares filing IDs on this page
        matches = re.findall(LISTING_LINK_PATTERN, html, re.IGNORECASE)
        new_ids = []
        for m in matches:
            try:
                rns_id = int(m)
                if rns_id not in seen:
                    seen.add(rns_id)
                    new_ids.append(rns_id)
                    all_ids.append(rns_id)
            except ValueError:
                continue

        if not new_ids:
            # Empty page — likely past end of available data
            print(f"    Page {page}: no new buyback filings found, stopping")
            break

        print(f"    Page {page}: +{len(new_ids)} buyback IDs ({len(all_ids)} total)")
        time.sleep(request_delay)

    return all_ids


def parse_announcement(filing_id: int) -> Optional[Announcement]:
    """Parse a single ADVFN Transaction in Own Shares page by ID."""
    url = FILING_URL_TMPL.format(id=filing_id)
    html = fetch_advfn_html(url)
    if not html:
        return None

    # Issuer validation: must be IMB (defence in depth)
    html_lower = html.lower()
    if not (
        "549300dfvpob67jl3a42" in html_lower   # IMB LEI (most reliable)
        or "imperial brands plc" in html_lower
    ):
        return None

    # Strip script/style blocks
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)

    # ── DATE ──
    # ADVFN format: "Date of transaction: 24 April 2026"
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

    # ── AVERAGE PRICE (GBp) ──
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
        rns_id=f"advfn_{filing_id}",
        source_url=url,
    )


def scrape_filings(known_ids: set = None,
                   max_pages: int = 3,
                   request_delay: float = 1.0) -> list[Announcement]:
    """
    Main scraper entry point — works for both daily updates and backfill.

    `max_pages`: How many listing pages to fetch.
                 - 1 page  ≈ 25 RNS filings, ~10 buybacks  → daily updates
                 - 5 pages ≈ 125 RNS, ~50 buybacks → weekly catch-up
                 - 10 pages ≈ 250 RNS, ~120 buybacks → 1 year coverage
                 - 20 pages ≈ 500 RNS, ~250 buybacks → full FY24-FY26 coverage
    """
    if known_ids is None:
        known_ids = set()

    print(f"  Listing scrape: {max_pages} pages, {len(known_ids)} known IDs to skip")

    # Step 1: get all buyback IDs from listing
    filing_ids = get_filing_ids_from_listing(
        max_pages=max_pages,
        request_delay=request_delay,
    )
    print(f"  Found {len(filing_ids)} buyback filing IDs across {max_pages} pages")

    # Step 2: filter out already-known
    new_ids = [i for i in filing_ids if f"advfn_{i}" not in known_ids]
    print(f"  {len(new_ids)} new (skipping {len(filing_ids) - len(new_ids)} already in data.json)")

    if not new_ids:
        return []

    # Step 3: fetch + parse each new filing
    announcements = []
    hits = 0
    for i, filing_id in enumerate(new_ids):
        ann = parse_announcement(filing_id)
        if ann:
            announcements.append(ann)
            hits += 1
            print(f"    ✓ {filing_id}: {ann.dato} | {ann.antal_aktier:,} @ {ann.gns_kurs_gbp:.2f}p = £{ann.beloeb_gbp_mio}M")
        time.sleep(request_delay)
        if (i + 1) % 25 == 0:
            print(f"    [{i+1}/{len(new_ids)}] {hits} hits")

    print(f"  Parsed {hits}/{len(new_ids)} buyback filings")
    return announcements


# Backwards-compat aliases for old scraper.py
def scrape_new_filings(known_ids=None, max_pages=3, request_delay=1.0):
    """Daily incremental scrape — uses listing's first few pages."""
    return scrape_filings(known_ids=known_ids, max_pages=max_pages, request_delay=request_delay)


def backfill_via_id_enumeration(known_ids=None, num_ids=None, request_delay=1.5, max_pages=20):
    """
    Backfill — paginate deeper through listing.

    `max_pages` controls depth. `num_ids` and `known_ids` retained for compat.
    """
    return scrape_filings(known_ids=known_ids, max_pages=max_pages, request_delay=request_delay)
