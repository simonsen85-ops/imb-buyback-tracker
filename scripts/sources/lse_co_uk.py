"""
LSE.co.uk primary scraper for IMB buyback filings.

URL STRUCTURE:
  Individual announcement:
    https://www.lse.co.uk/rns/IMB/transaction-in-own-shares-{hash}.html

The {hash} is alphanumeric (~15 chars), NOT sequential. There is no
listing/category page that works (those return 404). However, EACH
individual filing page contains a sidebar of ~50 related filings.

CRAWL STRATEGY:
1. Start from ONE known filing URL (a "seed")
2. Fetch it, extract all sibling filing URLs from its sidebar
3. Add new URLs to a queue, fetch them, extract more
4. Continue BFS-style until no new URLs found OR max_filings limit hit

Since URLs are scoped to /rns/IMB/, we cannot accidentally scrape
other companies. Authentication via lse_private_investor=1 cookie.

DEDUP:
URL hash acts as stable ID — we use it as rns_id with "lse_" prefix.
"""

import re
import time
from typing import Optional
import urllib.request
import urllib.error
from .base import Announcement, HEADERS


SCRAPER_HEADERS = {**HEADERS, "Cookie": "lse_private_investor=1"}

# Pattern for finding sibling filing URLs on any IMB filing page
# Matches: /rns/IMB/transaction-in-own-shares-{hash}.html
HASH_PATTERN = r'/rns/IMB/transaction-in-own-shares-([a-z0-9]{10,})\.html'

# Seed URLs — known-valid filing pages we use as crawl starting points.
# Multiple seeds give us robustness if one page returns fewer links.
SEED_URLS = [
    "https://www.lse.co.uk/rns/IMB/transaction-in-own-shares-luhoc5juf5zdhzq.html",
]

ANNOUNCEMENT_URL_TMPL = "https://www.lse.co.uk/rns/IMB/transaction-in-own-shares-{hash}.html"

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


def extract_hashes_from_page(html: str) -> list[str]:
    """Extract all sibling filing-URL hashes from a filing page."""
    matches = re.findall(HASH_PATTERN, html, re.IGNORECASE)
    seen = set()
    unique = []
    for h in matches:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def crawl_all_hashes(seeds: list[str] = None,
                     max_filings: int = 100,
                     request_delay: float = 1.0) -> list[str]:
    """
    BFS crawl from seed URLs. Returns hashes ordered by discovery.

    Each LSE.co.uk filing page links to ~50 sibling filings, so we:
    - Visit seeds first
    - Extract their links
    - Visit unseen links, extract more
    - Continue until queue empty OR max_filings hit
    """
    if seeds is None:
        seeds = SEED_URLS

    visited_urls = set()
    found_hashes = []  # ordered by discovery
    seen_hashes = set()

    # Initialize queue with seed URLs
    queue = list(seeds)

    while queue and len(found_hashes) < max_filings:
        url = queue.pop(0)
        if url in visited_urls:
            continue
        visited_urls.add(url)

        html = fetch_lse_html(url)
        if not html:
            continue

        # Extract hashes from this page
        page_hashes = extract_hashes_from_page(html)
        new_count = 0
        for h in page_hashes:
            if h not in seen_hashes:
                seen_hashes.add(h)
                found_hashes.append(h)
                new_count += 1
                # Add to queue if we haven't hit limit (so we can crawl from this page too)
                if len(found_hashes) < max_filings:
                    queue.append(ANNOUNCEMENT_URL_TMPL.format(hash=h))

        if new_count:
            print(f"    Crawled {url.split('/')[-1][:30]}: +{new_count} new ({len(found_hashes)} total)")

        time.sleep(request_delay)

    return found_hashes


def parse_announcement(url_hash: str) -> Optional[Announcement]:
    """Parse a single LSE.co.uk Transaction in Own Shares page."""
    url = ANNOUNCEMENT_URL_TMPL.format(hash=url_hash)
    html = fetch_lse_html(url)
    if not html:
        return None

    # Issuer validation: must be IMB (defence in depth)
    html_lower = html.lower()
    if not (
        "549300dfvpob67jl3a42" in html_lower   # IMB LEI
        or "gb0004544929" in html_lower         # IMB ISIN
        or "imperial brands plc" in html_lower
        or "imperial tobacco group" in html_lower  # Pre-2016 name
    ):
        return None

    # Remove <script> and <style> blocks entirely (incl. their contents)
    # This is critical — LSE.co.uk has lots of inline JS that pollutes our regex matches
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    # Now strip remaining tags (keep their text content)
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"&nbsp;|&#160;", " ", text)  # HTML entities
    text = re.sub(r"\s+", " ", text)

    # ── DATE ──
    date_patterns = [
        # "on 22 April 2026"
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        # "April 22, 2026"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
        # "Date of transaction: 22 April 2026" or "Transaction date: 22 April 2026"
        r"(?:Date of transaction|Transaction date|Trade date)\s*:?\s*(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        # "22-Apr-2026" or "22/04/2026"
        r"(\d{1,2})[\-/](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[\-/](\d{4})",
        # ISO format "2026-04-22" (some pages have this)
        r"(\d{4})-(\d{2})-(\d{2})",
    ]
    dato = None
    for pat in date_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        groups = m.groups()
        try:
            # Detect ISO format (year first, all numeric)
            if len(groups) == 3 and groups[0].isdigit() and len(groups[0]) == 4 and groups[1].isdigit():
                year, month_num, day = groups
                cand = f"{year}-{int(month_num):02d}-{int(day):02d}"
            elif groups[0].isdigit():  # "22 April 2026"
                day, month_str, year = groups
                month_key = month_str.lower()[:3]
                month_num = next((v for k, v in MONTHS.items() if k.startswith(month_key)), None)
                if not month_num:
                    continue
                cand = f"{year}-{month_num:02d}-{int(day):02d}"
            else:  # "April 22, 2026"
                month_str, day, year = groups
                month_key = month_str.lower()[:3]
                month_num = next((v for k, v in MONTHS.items() if k.startswith(month_key)), None)
                if not month_num:
                    continue
                cand = f"{year}-{month_num:02d}-{int(day):02d}"

            # Sanity check: date should be 2010-2030 (IMB has been buying back since 2014)
            year_int = int(cand[:4])
            if 2010 <= year_int <= 2030:
                dato = cand
                break
        except (KeyError, ValueError, StopIteration):
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
                if cand > 100:
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
        r"shares\s+in\s+issue.*?(\d{3}[\d,]{5,})",
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
        rns_id=f"lse_{url_hash}",
        source_url=url,
    )


def scrape_new_filings(known_hashes: set = None,
                       max_filings: int = 50,
                       request_delay: float = 1.0) -> list[Announcement]:
    """
    Crawl LSE.co.uk for IMB filings, skip already-known hashes.

    max_filings: total filing pages to crawl (covers ~max_filings transactions).
                 Default 50 ≈ 4 months FY26. For full backfill use 300.
    """
    if known_hashes is None:
        known_hashes = set()

    print(f"  Crawling LSE.co.uk (max {max_filings} filings)...")
    hashes = crawl_all_hashes(max_filings=max_filings, request_delay=request_delay)
    print(f"  Crawl found {len(hashes)} unique filing hashes")

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
        time.sleep(request_delay)
        if (i + 1) % 10 == 0:
            print(f"    [{i+1}/{len(new_hashes)}] {hits} hits")

    print(f"  Parsed {hits}/{len(new_hashes)} buyback filings")
    return announcements
