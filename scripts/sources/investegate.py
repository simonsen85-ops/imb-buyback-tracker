"""
Investegate scraper for IMB buyback filings.

Strategy:
1. Fetch company listing to get the latest RNS IDs
2. Enumerate backwards from newest ID until we hit the last known ID (from data.json)
3. Parse each RNS page for buyback transaction data

Why ID enumeration beats pagination:
- Investegate's company listing page shows max 30 items
- Paginating is fragile (URL pattern changes, server errors compound)
- RNS IDs are sequential — if we know we have ID 9500000 and newest is 9534918,
  we just iterate the range. Non-existent IDs return 404 (which we skip).
- This makes first-run historical backfill possible and ongoing updates trivial.
"""

import re
from typing import Optional
from .base import Announcement, fetch_html


COMPANY_URL = "https://www.investegate.co.uk/company/IMB"
ANNOUNCEMENT_URL = "https://www.investegate.co.uk/announcement/rns/imperial-brands--imb/transaction-in-own-shares/{id}"

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def get_latest_rns_ids(max_ids: int = 30) -> list[int]:
    """Fetch the most recent Transaction in Own Shares RNS IDs from Investegate's company page."""
    html = fetch_html(COMPANY_URL)
    if not html:
        print("  ✗ Could not fetch Investegate company page")
        return []

    # Match any /transaction-in-own-shares/NNNN link
    pattern = r'/announcement/rns/imperial-brands--imb/transaction-in-own-shares/(\d+)'
    matches = re.findall(pattern, html, re.IGNORECASE)
    # Dedup and sort newest first (highest ID)
    unique = sorted({int(m) for m in matches}, reverse=True)
    return unique[:max_ids]


def parse_rns_page(rns_id: int) -> Optional[Announcement]:
    """Parse a single RNS Transaction in Own Shares page into an Announcement."""
    url = ANNOUNCEMENT_URL.format(id=rns_id)
    html = fetch_html(url)
    if not html:
        return None

    # Verify this is actually a Transaction in Own Shares page (not a redirect/404 page)
    if "transaction in own shares" not in html.lower() and "purchased for cancellation" not in html.lower():
        return None

    # Strip HTML for cleaner regex matching
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    # ── DATE ──
    # Patterns: "on 22 April 2026" OR "April 22, 2026" OR "on April 22 2026"
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
            if groups[0].isdigit():  # "22 April 2026"
                day, month_str, year = groups
            else:                     # "April 22, 2026"
                month_str, day, year = groups
            dato = f"{year}-{MONTHS[month_str.lower()]:02d}-{int(day):02d}"
            break
        except (KeyError, ValueError):
            continue

    if not dato:
        return None

    # ── SHARES PURCHASED ──
    share_patterns = [
        r"(?:purchased|repurchased)\s+(?:for\s+cancellation\s+)?(\d[\d,]+)\s+(?:of\s+its\s+)?ordinary",
        r"repurchased\s+(\d[\d,]+)\s+ordinary",
        r"purchased\s+(?:the\s+following\s+number\s+of\s+its\s+)?(?:ordinary\s+shares.*?:\s*)?(\d[\d,]+)",
        r"Number\s+of\s+securities\s+purchased\s*:?\s*(\d[\d,]+)",
    ]
    antal = None
    for pat in share_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                antal = int(m.group(1).replace(",", ""))
                if antal > 100:  # Sanity: ignore tiny matches like "10 pence each"
                    break
            except ValueError:
                continue
    if not antal:
        return None

    # ── AVERAGE PRICE (GBp) ──
    price_patterns = [
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        r"GBp\s+(\d[\d,]*\.\d+)",
        r"pence\s+per\s+share\s+(?:was|of)?\s+(\d[\d,]*\.\d+)",
    ]
    gns_kurs = 0.0
    for pat in price_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                gns_kurs = float(m.group(1).replace(",", ""))
                if 100 < gns_kurs < 10000:  # Sanity: IMB trades 2000-4000p
                    break
                gns_kurs = 0.0
            except ValueError:
                continue

    # ── SHARES IN ISSUE AFTER ──
    after_patterns = [
        r"(?:remaining\s+)?(?:total\s+)?(?:number\s+of\s+)?ordinary\s+shares\s+in\s+issue\s+"
        r"(?:will\s+be|is\s+now|is)\s+(\d[\d,]+)",
        r"shares\s+in\s+issue.*?(\d{3}[\d,]+)",
    ]
    aktier_efter = None
    for pat in after_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                n = int(m.group(1).replace(",", ""))
                if 500_000_000 < n < 1_000_000_000:  # Sanity: IMB ~780M shares
                    aktier_efter = n
                    break
            except ValueError:
                continue

    beloeb = round(antal * gns_kurs / 100 / 1e6, 1) if gns_kurs else 0

    return Announcement(
        dato=dato,
        antal_aktier=antal,
        gns_kurs_gbp=gns_kurs,
        beloeb_gbp_mio=beloeb,
        aktier_efter=aktier_efter,
        rns_id=str(rns_id),
        source_url=url,
    )


def scrape_new_filings(last_known_id: Optional[int] = None,
                        max_lookback: int = 200) -> list[Announcement]:
    """
    Scrape IMB buyback RNS filings newer than `last_known_id`.

    If last_known_id is None (first run): fetch all ~30 from company page listing.
    Otherwise: enumerate backwards from newest visible ID to last_known_id.

    max_lookback caps total IDs to try (safety for first run).
    """
    latest_ids = get_latest_rns_ids(max_ids=30)
    if not latest_ids:
        return []

    newest = latest_ids[0]
    print(f"  Newest RNS ID on Investegate: {newest}")

    if last_known_id and last_known_id >= newest:
        print(f"  Already up-to-date (last known: {last_known_id})")
        return []

    # Build target ID list
    if last_known_id is None:
        # First run — only scrape what we see in the listing
        target_ids = latest_ids
        print(f"  First run: scraping {len(target_ids)} latest IDs from listing")
    else:
        # Enumerate the gap, capped by max_lookback
        start = last_known_id + 1
        end = newest
        gap = end - start + 1
        if gap > max_lookback:
            print(f"  Gap {gap} > max_lookback {max_lookback} — capping")
            start = end - max_lookback + 1
        target_ids = list(range(end, start - 1, -1))  # newest first
        print(f"  Enumerating IDs {start}..{end} ({len(target_ids)} candidates)")

    # Parse each
    announcements = []
    hits = 0
    misses = 0
    for i, rns_id in enumerate(target_ids):
        ann = parse_rns_page(rns_id)
        if ann:
            announcements.append(ann)
            hits += 1
            print(f"    ✓ {rns_id}: {ann.dato} | {ann.antal_aktier:,} @ {ann.gns_kurs_gbp:.2f}p = £{ann.beloeb_gbp_mio}M")
        else:
            misses += 1
        if (i + 1) % 20 == 0:
            print(f"    [{i+1}/{len(target_ids)}] {hits} hits, {misses} misses")

    print(f"  Parsed {hits} buyback filings ({misses} skipped — other RNS types or 404)")
    return announcements
