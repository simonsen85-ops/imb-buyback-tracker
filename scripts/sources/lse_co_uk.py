"""
LSE.co.uk fallback scraper for IMB buyback filings.

Used when Investegate fails or to cross-verify data. LSE.co.uk shows RNS
filings in a table format without the per-company ID enumeration we get
from Investegate. Scope is intentionally narrower: listing page only.

Note: LSE.co.uk requires a "private investor" cookie which may be set via
a GET parameter. We send it as a cookie header to bypass the popup.
"""

import re
from typing import Optional
from .base import Announcement, fetch_html, HEADERS


LISTING_URL = "https://www.lse.co.uk/rns/IMB/transaction-in-own-shares.html"

# Send the private-investor consent as a cookie so we bypass the popup
FALLBACK_HEADERS = {**HEADERS, "Cookie": "lse_private_investor=1"}


def scrape_listing() -> list[Announcement]:
    """Fetch LSE.co.uk listing of IMB 'Transaction in Own Shares' filings."""
    import urllib.request
    req = urllib.request.Request(LISTING_URL, headers=FALLBACK_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ✗ LSE.co.uk fallback failed: {e}")
        return []

    # LSE.co.uk embeds announcement links like:
    # /rns/IMB/transaction-in-own-shares-NNNNNNNN.html
    link_pattern = r'/rns/IMB/transaction-in-own-shares[^"\']*?(\d{8,})\.html'
    ids = sorted({m for m in re.findall(link_pattern, html)}, reverse=True)

    announcements = []
    for ann_id in ids[:20]:
        url = f"https://www.lse.co.uk/rns/IMB/transaction-in-own-shares-{ann_id}.html"
        ann = _parse_page(url, ann_id)
        if ann:
            announcements.append(ann)

    return announcements


def _parse_page(url: str, ann_id: str) -> Optional[Announcement]:
    """Parse a single LSE.co.uk RNS page."""
    import urllib.request
    req = urllib.request.Request(url, headers=FALLBACK_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    # Same regex patterns as investegate.py - RNS content is identical
    months = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
              "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}

    date_match = re.search(
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        text, re.IGNORECASE
    )
    if not date_match:
        return None
    day, month_str, year = date_match.groups()
    try:
        dato = f"{year}-{months[month_str.lower()]:02d}-{int(day):02d}"
    except (KeyError, ValueError):
        return None

    sh_m = re.search(
        r"(?:purchased|repurchased)\s+(?:for\s+cancellation\s+)?(\d[\d,]+)\s+(?:of\s+its\s+)?ordinary",
        text, re.IGNORECASE
    )
    if not sh_m:
        return None
    antal = int(sh_m.group(1).replace(",", ""))

    px_m = re.search(
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        text, re.IGNORECASE
    )
    gns_kurs = float(px_m.group(1).replace(",", "")) if px_m else 0.0

    rem_m = re.search(
        r"(?:remaining\s+)?(?:number\s+of\s+)?ordinary\s+shares\s+in\s+issue\s+(?:will\s+be|is\s+now|is)\s+(\d[\d,]+)",
        text, re.IGNORECASE
    )
    aktier_efter = int(rem_m.group(1).replace(",", "")) if rem_m else None

    beloeb = round(antal * gns_kurs / 100 / 1e6, 1) if gns_kurs else 0

    return Announcement(
        dato=dato,
        antal_aktier=antal,
        gns_kurs_gbp=gns_kurs,
        beloeb_gbp_mio=beloeb,
        aktier_efter=aktier_efter,
        rns_id=f"lse_{ann_id}",  # Prefix to distinguish from Investegate IDs
        source_url=url,
    )
