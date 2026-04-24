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

    print(f"  Fetched {len(html):,} chars from {COMPANY_URL}")

    # Try primary pattern (slash-separated with --imb slug)
    pattern = r'/announcement/rns/imperial-brands--imb/transaction-in-own-shares/(\d+)'
    matches = re.findall(pattern, html, re.IGNORECASE)

    # If no matches with primary pattern, try fallback patterns
    if not matches:
        print("  ⚠ Primary URL pattern found 0 matches — trying fallbacks")
        # Fallback 1: any RNS link mentioning transaction-in-own-shares
        fallback1 = re.findall(r'transaction-in-own-shares[/-](\d{7,})', html, re.IGNORECASE)
        # Fallback 2: broader RNS link for imperial-brands
        fallback2 = re.findall(r'imperial-brands[^"\']*?/(\d{7,})', html, re.IGNORECASE)
        # Diagnostic: count how many /announcement/ links in total
        all_announcements = re.findall(r'/announcement/rns/([^"\']+)', html, re.IGNORECASE)

        print(f"    Fallback 1 (transaction-in-own-shares): {len(fallback1)} matches")
        print(f"    Fallback 2 (imperial-brands/ID): {len(fallback2)} matches")
        print(f"    Any /announcement/rns/ links: {len(all_announcements)} matches")

        if all_announcements:
            # Show first 3 for debugging
            print(f"    Sample announcement paths:")
            for sample in all_announcements[:3]:
                print(f"      /announcement/rns/{sample[:80]}")

        # Use best fallback available
        matches = fallback1 or fallback2 or []

    if not matches:
        # Final diagnostic: what does the HTML actually contain?
        has_imb = "IMB" in html or "imperial" in html.lower()
        has_rns = "RNS" in html or "announcement" in html.lower()
        has_cookie_wall = "cookie" in html.lower() and "accept" in html.lower()
        has_captcha = "captcha" in html.lower() or "cloudflare" in html.lower()
        print(f"    HTML content check: IMB={has_imb}, RNS={has_rns}, cookie_wall={has_cookie_wall}, captcha={has_captcha}")

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

    # CRITICAL: Verify this is Imperial Brands, not another issuer.
    # Investegate renders ANY RNS page regardless of URL slug — content must confirm issuer.
    # Use LEI + ISIN as primary (regulatory identifiers), fall back to full company name.
    html_lower = html.lower()
    is_imperial = (
        "549300dfvpob67jl3a42" in html_lower  # IMB LEI (ISO 17442)
        or "gb0004544929" in html_lower        # IMB ISIN
        or "imperial brands plc" in html_lower  # Full legal name (fallback)
    )
    if not is_imperial:
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
    # Try many patterns — RNS formatting has evolved over years
    price_patterns = [
        # Modern (2024+): "average price paid per share was 3050.44 pence"
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        # Table format: "Weighted average price (pence) | 3050.44"
        r"weighted\s+average\s+price\s*\([^)]+\)\s*[:\|]?\s*(\d[\d,]*\.\d+)",
        # Older format: "at an average price of 3050.44 pence"
        r"at\s+an?\s+average\s+price\s+of\s+(\d[\d,]*\.\d+)\s*(?:pence|GBp|p\b)",
        # "volume weighted average price of 3050.44p"
        r"volume\s+weighted\s+average\s+price\s+of\s+(\d[\d,]*\.\d+)",
        # "price per share: 3050.44"
        r"price\s+per\s+share\s*[:\-]?\s*(?:GBp\s+)?(\d[\d,]*\.\d+)",
        # Prefix: "GBp 3050.44" or "GBX 3050.44"
        r"GB[pPxX]\s+(\d[\d,]*\.\d+)",
        # Suffix: "3050.44 pence per share"
        r"(\d[\d,]*\.\d+)\s*(?:pence|p)\s+per\s+(?:ordinary\s+)?share",
        # Fallback: "3050.44p" near "average"
        r"average[^.]{0,80}?(\d[\d,]*\.\d+)\s*p\b",
    ]
    gns_kurs = 0.0
    for pat in price_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cand = float(m.group(1).replace(",", ""))
                # Sanity check: IMB trades historically between 1500-4500p
                if 1000 < cand < 5000:
                    gns_kurs = cand
                    break
            except ValueError:
                continue

    # ── SHARES IN ISSUE AFTER ──
    after_patterns = [
        r"(?:remaining\s+)?(?:total\s+)?(?:number\s+of\s+)?ordinary\s+shares\s+in\s+issue\s+"
        r"(?:will\s+be|is\s+now|is)\s+(\d[\d,]+)",
        r"shares\s+in\s+issue.*?(\d{3}[\d,]+)",
    ]
    aktier_efter = None
    any_shares_in_issue = None  # Track if we found ANY number that looked like share count
    for pat in after_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                n = int(m.group(1).replace(",", ""))
                if 500_000_000 < n < 1_000_000_000:  # Sanity: IMB ~780M shares
                    aktier_efter = n
                    break
                else:
                    any_shares_in_issue = n  # Found a share count, but wrong magnitude
            except ValueError:
                continue

    # If we found a share count but it's not IMB-sized, this is another issuer. Reject.
    if any_shares_in_issue is not None and aktier_efter is None:
        return None

    beloeb = round(antal * gns_kurs / 100 / 1e6, 1) if gns_kurs else 0

    # Require a valid price — reject rather than save garbage data.
    if gns_kurs == 0:
        return None

    return Announcement(
        dato=dato,
        antal_aktier=antal,
        gns_kurs_gbp=gns_kurs,
        beloeb_gbp_mio=beloeb,
        aktier_efter=aktier_efter,
        rns_id=str(rns_id),
        source_url=url,
    )


def probe_recent_ids(start_id: int = 9535000, window: int = 50) -> list[int]:
    """
    Fallback when listing page fails: probe a range of recent IDs directly.
    We know IDs are sequential (~9534918 at 22/4-2026), so probing
    start_id-window..start_id finds recent transaction-in-own-shares filings.
    """
    print(f"  Probing IDs {start_id-window}..{start_id} directly...")
    found_ids = []
    for rns_id in range(start_id, start_id - window, -1):
        url = ANNOUNCEMENT_URL.format(id=rns_id)
        html = fetch_html(url)
        if html and ("transaction in own shares" in html.lower() or "purchased for cancellation" in html.lower()):
            found_ids.append(rns_id)
    print(f"    Probe found {len(found_ids)} buyback filings in window")
    return found_ids


def scrape_new_filings(last_known_id: Optional[int] = None,
                        max_lookback: int = 200) -> list[Announcement]:
    """
    Scrape IMB buyback RNS filings newer than `last_known_id`.
    """
    latest_ids = get_latest_rns_ids(max_ids=30)

    # Fallback: probe recent IDs if listing failed
    if not latest_ids:
        print("  ⚠ Listing page gave nothing — falling back to ID probing")
        # Start probe at a known-good recent ID + some headroom for newer filings
        probe_start = max(last_known_id + 30, 9535000) if last_known_id else 9535000
        latest_ids = probe_recent_ids(start_id=probe_start, window=100)

    if not latest_ids:
        print("  ✗ No RNS IDs discovered by any method")
        return []

    newest = latest_ids[0]
    print(f"  Newest RNS ID discovered: {newest}")

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
