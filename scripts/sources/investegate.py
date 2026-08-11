"""
Investegate scraper for IMB buyback filings — PRIMARY SOURCE.

WHY THIS IS PRIMARY (Aug 2026):
ADVFN added site-wide bot detection and now returns HTTP 403 to any
non-browser client. Investegate's company page is server-rendered plain
HTML with per-company URLs, so it is both accessible and safe.

LISTING (paginated, 50 entries/page, ~32 pages of history):
  https://www.investegate.co.uk/company/IMB?page={n}

FILING DETAIL:
  https://www.investegate.co.uk/announcement/rns/imperial-brands--imb/
  transaction-in-own-shares/{id}

The listing links are per-company (`imperial-brands--imb` slug), so we
cannot pick up another issuer's filings by accident. We still validate
the LEI on every detail page as defence in depth.

IMPORTANT — the link pattern requires a literal "/" immediately after
"transaction-in-own-shares", which deliberately excludes the separate
"transaction-in-own-shares-treasury-share-transfer" announcements.
Those are treasury transfers, not market purchases for cancellation.

DEDUP:
rns_id is the bare numeric string (e.g. "9713069"), matching the
40 Investegate-sourced rows already in data.json. Do NOT add a prefix
here or every historical row will be re-fetched as a duplicate.
"""

import re
import time
from typing import Optional
from .base import Announcement, fetch_html


COMPANY_URL_TMPL = "https://www.investegate.co.uk/company/IMB?page={page}"
ANNOUNCEMENT_URL = (
    "https://www.investegate.co.uk/announcement/rns/imperial-brands--imb/"
    "transaction-in-own-shares/{id}"
)

# Trailing "/" after the slug excludes the treasury-share-transfer variant.
LISTING_LINK_PATTERN = (
    r'/announcement/rns/imperial-brands--imb/transaction-in-own-shares/(\d+)'
)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _clean_text(html: str) -> str:
    """
    Strip everything that could poison a regex match, in order:
      1. <script>/<style> blocks (inline JS contains stray numbers)
      2. The "Summary by AI BETA" block — it restates the figures in prose
         and sits ABOVE the real RNS text, so re.search would hit it first.
      3. Remaining tags and HTML entities.
    """
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", html,
                     flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned,
                     flags=re.IGNORECASE | re.DOTALL)

    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text)

    # Drop the AI summary: starts at "Summary by AI", ends at the disclaimer
    # link that always follows it. If the end marker is missing, drop nothing
    # rather than risk cutting the real announcement.
    m_start = re.search(r"Summary by AI", text, re.IGNORECASE)
    if m_start:
        m_end = re.search(r"Disclaimer\s*\*?", text[m_start.end():],
                          re.IGNORECASE)
        if m_end:
            cut_to = m_start.end() + m_end.end()
            text = text[:m_start.start()] + " " + text[cut_to:]

    return text


def get_filing_ids_from_listing(max_pages: int = 2,
                                request_delay: float = 1.0,
                                start_page: int = 1) -> list[int]:
    """
    Paginate Investegate's IMB company listing and return buyback filing IDs.

    Each page holds 50 announcements of all RNS types; typically ~35 of them
    are Transaction in Own Shares. Returns IDs newest-first.

    start_page lets a backfill jump straight to the era it needs instead of
    re-walking pages already covered. Page 1 is the newest ~5 weeks, and
    each page is roughly 2-3 weeks further back.
    """
    all_ids = []
    seen = set()

    for page in range(start_page, start_page + max_pages):
        url = COMPANY_URL_TMPL.format(page=page)
        html = fetch_html(url)
        if not html:
            print(f"    Page {page}: fetch failed, stopping")
            break

        matches = re.findall(LISTING_LINK_PATTERN, html, re.IGNORECASE)
        if not matches:
            # A page with zero buyback links means we ran past the end of
            # the archive (or the layout changed) — either way, stop.
            print(f"    Page {page}: no buyback links found, stopping "
                  f"({len(html):,} chars fetched)")
            break

        new_on_page = 0
        for m in matches:
            try:
                rns_id = int(m)
            except ValueError:
                continue
            if rns_id not in seen:
                seen.add(rns_id)
                all_ids.append(rns_id)
                new_on_page += 1

        print(f"    Page {page}: +{new_on_page} buyback IDs "
              f"({len(all_ids)} total)")
        time.sleep(request_delay)

    return all_ids


def parse_rns_page(rns_id: int) -> Optional[Announcement]:
    """Parse a single Transaction in Own Shares page into an Announcement."""
    url = ANNOUNCEMENT_URL.format(id=rns_id)
    html = fetch_html(url)
    if not html:
        return None

    html_lower = html.lower()

    # Must be a buyback page, not a redirect or error shell
    if ("transaction in own shares" not in html_lower
            and "purchased for cancellation" not in html_lower):
        return None

    # Issuer validation (defence in depth — LEI/ISIN are regulatory IDs)
    if not (
        "549300dfvpob67jl3a42" in html_lower      # IMB LEI (ISO 17442)
        or "gb0004544929" in html_lower           # IMB ISIN
        or "imperial brands plc" in html_lower    # legal name fallback
    ):
        return None

    text = _clean_text(html)

    # ── DATE ──
    # Labelled pattern FIRST: the RNS always carries "Date of transaction:".
    # The unlabelled fallbacks exist for older filings with prose-only wording.
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
            if 2010 <= year_int <= 2035:
                dato = f"{year}-{MONTHS[month_str.lower()]:02d}-{int(day):02d}"
                break
        except (KeyError, ValueError):
            continue
    if not dato:
        return None

    # ── SHARES PURCHASED ──
    # NUM matches a thousands-grouped figure with EITHER a comma or a space
    # (incl. non-breaking) as the separator. `(\d[\d,]+)` was used before and
    # truncated "285 950" to "285" — that single character class cost the
    # tracker ~£327M of FY23 volume across 48 filings before it was caught.
    # Each group after the first must be exactly 3 digits, so this cannot
    # run on into an unrelated adjacent number.
    NUM = r"(\d{1,3}(?:[,\s\u00a0]\d{3})+|\d+)"
    share_patterns = [
        r"Number of shares (?:re)?purchased\s*:?\s*" + NUM,
        r"Number of securities purchased\s*:?\s*" + NUM,
        r"(?:purchased|repurchased)\s+(?:for\s+cancellation\s+)?" + NUM +
        r"\s+(?:of\s+its\s+)?ordinary",
    ]
    antal = None
    for pat in share_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cand = int(re.sub(r"[,\s\u00a0]", "", m.group(1)))
            except ValueError:
                continue
            if cand > 100:          # guards against "10 pence each"
                antal = cand
                break
    if not antal:
        return None

    # ── AVERAGE PRICE (GBp) ──
    price_patterns = [
        r"Average price paid per share\s*:?\s*(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        r"Volume[\- ]weighted average price[^:]*:\s*(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
    ]
    gns_kurs = 0.0
    for pat in price_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cand = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 500 < cand < 10000:   # IMB trades in the 1500-3600 GBp band
                gns_kurs = cand
                break
    if gns_kurs == 0:
        return None

    # ── SHARES IN ISSUE AFTER CANCELLATION ──
    after_patterns = [
        r"remaining number of ordinary shares in issue will be\s+" + NUM,
        r"ordinary shares in issue\s+(?:will be|is now|is)\s+" + NUM,
        r"shares in issue.*?(\d{3}[\d,]{5,})",
    ]
    aktier_efter = None
    for pat in after_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                n = int(re.sub(r"[,\s\u00a0]", "", m.group(1)))
            except ValueError:
                continue
            if 500_000_000 < n < 1_500_000_000:
                aktier_efter = n
                break

    beloeb = round(antal * gns_kurs / 100 / 1e6, 1)

    return Announcement(
        dato=dato,
        antal_aktier=antal,
        gns_kurs_gbp=gns_kurs,
        beloeb_gbp_mio=beloeb,
        aktier_efter=aktier_efter,
        rns_id=str(rns_id),          # bare numeric — matches existing rows
        source_url=url,
    )


def scrape_filings(known_ids: set = None,
                   max_pages: int = 2,
                   request_delay: float = 1.0,
                   start_page: int = 1) -> list[Announcement]:
    """
    Main entry point for both daily updates and backfill.

    max_pages guide (50 announcements/page, ~35 buybacks/page):
      1  → ~5 weeks   (daily updates; 1 page is plenty)
      2  → ~3 months  (safe default — survives a long outage)
      10 → ~1.5 years
      32 → full archive back to 2022

    start_page skips ahead — use it to target a known gap rather than
    re-walking pages whose filings are already stored under another
    source's rns_id (those get discarded at merge, but still cost a fetch).
    """
    known_ids = known_ids or set()

    span = (f"pages {start_page}-{start_page + max_pages - 1}"
            if max_pages > 1 else f"page {start_page}")
    print(f"  Listing scrape: {span}, {len(known_ids)} known IDs to skip")

    filing_ids = get_filing_ids_from_listing(
        max_pages=max_pages,
        request_delay=request_delay,
        start_page=start_page,
    )
    if not filing_ids:
        print("  ✗ No filing IDs found in listing")
        return []

    print(f"  Found {len(filing_ids)} buyback filing IDs")

    new_ids = [i for i in filing_ids if str(i) not in known_ids]
    print(f"  {len(new_ids)} new "
          f"(skipping {len(filing_ids) - len(new_ids)} already in data.json)")
    if not new_ids:
        return []

    announcements = []
    hits = 0
    for i, rns_id in enumerate(new_ids):
        ann = parse_rns_page(rns_id)
        if ann:
            announcements.append(ann)
            hits += 1
            print(f"    ✓ {rns_id}: {ann.dato} | {ann.antal_aktier:,} "
                  f"@ {ann.gns_kurs_gbp:.2f}p = £{ann.beloeb_gbp_mio}M")
        time.sleep(request_delay)
        if (i + 1) % 25 == 0:
            print(f"    [{i+1}/{len(new_ids)}] {hits} hits")

    print(f"  Parsed {hits}/{len(new_ids)} buyback filings")
    return announcements


# ── Backwards-compatible shims for older scraper.py call sites ──────────────

def get_latest_rns_ids(max_ids: int = 50) -> list[int]:
    """Legacy helper — newest IDs from page 1 of the listing."""
    return get_filing_ids_from_listing(max_pages=1, request_delay=0.5)[:max_ids]


def scrape_new_filings(last_known_id=None, max_lookback=None,
                       known_ids=None, max_pages=2,
                       request_delay=1.0) -> list[Announcement]:
    """
    Legacy signature. `last_known_id`/`max_lookback` are ignored — the
    listing tells us exactly which filings exist, so blind ID enumeration
    (which ran at a ~1% hit rate) is no longer used.
    """
    return scrape_filings(known_ids=known_ids, max_pages=max_pages,
                          request_delay=request_delay)
