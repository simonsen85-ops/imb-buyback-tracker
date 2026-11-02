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

TWO FILING FORMATS (Oct 2026):
  * Single-day (Morgan Stanley / Barclays, FY23-FY26): one trade date per
    filing, labelled fields ("Date of transaction:", "Number of shares
    repurchased:", "Average price paid per share: GBp ...").
  * Multi-day table (HSBC, FY27 tranche 1 from 8 Oct 2026): one filing per
    week with a row per trade date:
      Date of purchase | VWAP (GBp) | Number of Shares purchased | Lowest | Highest
    plus a prose total ("purchased for cancellation 977,781 of its ordinary
    shares") that we use as a checksum against the summed rows.
  The single-day parser must never see a multi-day filing: its prose-date
  fallback would pick up "announced on 8 October 2026" as the trade date.
  Until v5 these filings failed the price regex and were dropped SILENTLY,
  which froze the tracker at 6 Oct 2026 while every workflow run stayed green.

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


MONTH_RE = (r"(January|February|March|April|May|June|July|August|September|"
            r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|"
            r"Oct|Nov|Dec)")
MONTHS_SHORT = {k[:3]: v for k, v in MONTHS.items()}
ROW_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+" + MONTH_RE + r"\.?\s+(\d{4})\b"      # 26 October 2026
    r"|\b(\d{1,2})/(\d{1,2})/(\d{4})\b",                     # 26/10/2026
    re.IGNORECASE,
)
NUM_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
SHARE_TOLERANCE = 1_000

# Set by scrape_filings() so the orchestrator can tell "Investegate is
# reachable but had nothing new" apart from "Investegate is down".
LAST_LISTING_OK = False
# (rns_id, reason) for every buyback filing that could not be parsed.
LAST_FAILURES: list = []


def _num(tok: str) -> float:
    return float(tok.replace(",", ""))


def _row_date(m) -> Optional[str]:
    try:
        if m.group(1):
            day, mon, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            month = MONTHS.get(mon) or MONTHS_SHORT.get(mon[:3])
        else:
            day, month, year = int(m.group(4)), int(m.group(5)), int(m.group(6))
        if not (2010 <= year <= 2035 and 1 <= month <= 12 and 1 <= day <= 31):
            return None
        return f"{year}-{month:02d}-{day:02d}"
    except (TypeError, ValueError, KeyError):
        return None


def _shares_after(text: str) -> Optional[int]:
    NUM = r"(\d{1,3}(?:[,\s\u00a0]\d{3})+|\d+)"
    for pat in (
        r"remaining number of ordinary shares in issue will be\s+" + NUM,
        r"ordinary shares in issue\s+(?:will be|is now|is)\s+" + NUM,
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            n = int(re.sub(r"[,\s\u00a0]", "", m.group(1)))
            if 500_000_000 < n < 1_500_000_000:
                return n
    return None


def _is_multi_day(text: str) -> bool:
    return bool(re.search(r"date of purchase|aggregated volume|as detailed in the table",
                          text, re.IGNORECASE))


def _column_order(header: str) -> list:
    """
    Map numeric columns from the table header, in the order they appear.
    "volume weighted average price" contains "volume", so the VWAP span is
    blanked out before looking for the volume column.
    """
    h = header.lower()
    found = {}
    m = re.search(r"volume[\s\-]*weighted average price|\bvwap\b|average price", h)
    if m:
        found["vwap"] = m.start()
        h = h[:m.start()] + " " * (m.end() - m.start()) + h[m.end():]
    m = re.search(r"number of (?:ordinary )?shares (?:re)?purchased|aggregated? volume|\bvolume\b", h)
    if m:
        found["vol"] = m.start()
    m = re.search(r"lowest", h)
    if m:
        found["low"] = m.start()
    m = re.search(r"highest", h)
    if m:
        found["high"] = m.start()
    return [k for k, _ in sorted(found.items(), key=lambda kv: kv[1])]


def _parse_multi_day(text: str):
    """
    Parse an HSBC-style weekly table. Returns (rows, reason); rows is a list
    of dicts {dato, antal, vwap}, reason is None on success.
    """
    m_hdr = re.search(r"date of purchase|date of transactions?\b|aggregated volume", text, re.IGNORECASE)
    if not m_hdr:
        return [], "multi-day: table header not found"

    m_end = re.search(r"intends to cancel|following the purchase|remaining number of ordinary shares"
                      r"|individual (?:information|transactions|purchases)|schedule of purchases",
                      text[m_hdr.start():], re.IGNORECASE)
    region = text[m_hdr.start(): m_hdr.start() + (m_end.start() if m_end else 6000)]

    dates = list(ROW_DATE_RE.finditer(region))
    if not dates:
        return [], "multi-day: no dated rows in table"

    cols = _column_order(region[:dates[0].start()])
    per_day = {}
    for i, dm in enumerate(dates):
        dato = _row_date(dm)
        if not dato:
            return [], f"multi-day: unreadable date '{dm.group(0)}'"
        seg_end = dates[i + 1].start() if i + 1 < len(dates) else len(region)
        toks = NUM_TOKEN_RE.findall(region[dm.end():seg_end])

        vol = vwap = low = high = None
        if len(cols) == len(toks) and "vol" in cols and "vwap" in cols:
            vals = dict(zip(cols, toks))
            vol = vals["vol"]
            vwap = vals["vwap"]
            low, high = vals.get("low"), vals.get("high")
        else:
            # Header unreadable: volume is the only integer, VWAP is the
            # median price (it must lie between lowest and highest).
            ints = [t for t in toks if "." not in t]
            prices = sorted(_num(t) for t in toks if "." in t and 500 < _num(t) < 10_000)
            if len(ints) != 1 or not prices:
                return [], (f"multi-day: cannot map row {dato} "
                            f"(cols={cols}, tokens={toks})")
            vol = ints[0]
            vwap = str(prices[len(prices) // 2])
            if len(prices) == 3:
                low, high = str(prices[0]), str(prices[2])

        try:
            v, px = int(_num(vol)), _num(vwap)
        except (TypeError, ValueError):
            return [], f"multi-day: non-numeric row {dato} ({toks})"
        if not (100 <= v <= 20_000_000) or not (500 < px < 10_000):
            return [], f"multi-day: implausible row {dato}: {v:,} @ {px}"
        if low and high and not (_num(low) - 0.01 <= px <= _num(high) + 0.01):
            return [], f"multi-day: VWAP {px} outside {low}-{high} on {dato} (column mix-up?)"

        d = per_day.setdefault(dato, {"antal": 0, "value": 0.0})
        d["antal"] += v                      # several venues on one day are summed
        d["value"] += v * px

    rows = [{"dato": k, "antal": d["antal"], "vwap": d["value"] / d["antal"]}
            for k, d in sorted(per_day.items())]

    # Checksum against the prose total
    NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"
    m_tot = re.search(r"purchased for cancellation (?:a total of )?" + NUM + r"\s+(?:of its )?ordinary",
                      text, re.IGNORECASE)
    parsed_total = sum(r["antal"] for r in rows)
    if m_tot:
        stated = int(m_tot.group(1).replace(",", ""))
        if stated != parsed_total:
            return [], (f"multi-day: rows sum to {parsed_total:,} but filing states "
                        f"{stated:,} shares")
    return rows, None


def _parse_single_day(text: str):
    """Original single-day parser. Returns (Announcement fields, reason)."""
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
            if 2010 <= int(year) <= 2035:
                dato = f"{year}-{MONTHS[month_str.lower()]:02d}-{int(day):02d}"
                break
        except (KeyError, ValueError):
            continue
    if not dato:
        return None, "single-day: no transaction date"

    # NUM accepts comma OR space thousands separators — "(\d[\d,]+)" once
    # truncated "285 950" to "285" and cost ~£327M of FY23 volume.
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
            if cand > 100:
                antal = cand
                break
    if not antal:
        return None, "single-day: no share count"

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
            if 500 < cand < 10000:
                gns_kurs = cand
                break
    if gns_kurs == 0:
        return None, "single-day: no average price"

    return {"dato": dato, "antal": antal, "vwap": gns_kurs}, None


def _debug_snippet(text: str, n: int = 900) -> str:
    m = re.search(r"Transactions? in own shares", text, re.IGNORECASE)
    i = m.start() if m else 0
    return text[i:i + n]


def parse_rns_filing(rns_id: int):
    """
    Parse one Transaction in Own Shares filing.
    Returns (announcements, reason). One announcement per trade date, so a
    weekly HSBC filing becomes five daily rows that share the filing's rns_id.
    """
    url = ANNOUNCEMENT_URL.format(id=rns_id)
    html = fetch_html(url)
    if not html:
        return [], "fetch failed"

    html_lower = html.lower()
    if ("transaction in own shares" not in html_lower
            and "transactions in own shares" not in html_lower
            and "purchased for cancellation" not in html_lower):
        return [], "not a buyback page"
    if not ("549300dfvpob67jl3a42" in html_lower or "gb0004544929" in html_lower
            or "imperial brands plc" in html_lower):
        return [], "issuer check failed"

    text = _clean_text(html)
    after = _shares_after(text)

    if _is_multi_day(text):
        rows, reason = _parse_multi_day(text)
    else:
        row, reason = _parse_single_day(text)
        rows = [row] if row else []
    if not rows:
        print(f"    ✗ {rns_id}: {reason}\n      text: {_debug_snippet(text)!r}")
        return [], reason

    # Shares in issue after each day: the filing only states the figure after
    # the LAST day, so earlier days are rebuilt by adding back later purchases.
    anns = []
    later = 0
    for r in reversed(rows):
        anns.append(Announcement(
            dato=r["dato"],
            antal_aktier=r["antal"],
            gns_kurs_gbp=round(r["vwap"], 4),
            beloeb_gbp_mio=round(r["antal"] * r["vwap"] / 100 / 1e6, 1),
            aktier_efter=(after + later) if after else None,
            rns_id=str(rns_id),          # bare numeric — matches existing rows
            source_url=url,
        ))
        later += r["antal"]
    anns.reverse()
    return anns, None


def parse_rns_page(rns_id: int) -> Optional[Announcement]:
    """Legacy single-result wrapper (returns the last trade date only)."""
    anns, _ = parse_rns_filing(rns_id)
    return anns[-1] if anns else None


def scrape_filings(known_ids: set = None,
                   max_pages: int = 2,
                   request_delay: float = 1.0,
                   start_page: int = 1) -> list[Announcement]:
    """
    Main entry point for both daily updates and backfill.

    max_pages guide (50 announcements/page):
      1  → ~5 weeks of daily filings (much longer now filings are weekly)
      2  → safe default — survives a long outage
      32 → full archive back to 2022

    Side effects: sets LAST_LISTING_OK and LAST_FAILURES.
    """
    global LAST_LISTING_OK, LAST_FAILURES
    LAST_LISTING_OK = False
    LAST_FAILURES = []
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
    LAST_LISTING_OK = True

    print(f"  Found {len(filing_ids)} buyback filing IDs")
    new_ids = [i for i in filing_ids if str(i) not in known_ids]
    print(f"  {len(new_ids)} new "
          f"(skipping {len(filing_ids) - len(new_ids)} already in data.json)")
    if not new_ids:
        return []

    announcements = []
    for i, rns_id in enumerate(new_ids):
        anns, reason = parse_rns_filing(rns_id)
        if anns:
            announcements.extend(anns)
            for a in anns:
                print(f"    ✓ {rns_id}: {a.dato} | {a.antal_aktier:,} "
                      f"@ {a.gns_kurs_gbp:.2f}p = £{a.beloeb_gbp_mio}M")
        else:
            LAST_FAILURES.append((str(rns_id), reason))
        time.sleep(request_delay)
        if (i + 1) % 25 == 0:
            print(f"    [{i+1}/{len(new_ids)}] {len(announcements)} rows")

    print(f"  Parsed {len(new_ids) - len(LAST_FAILURES)}/{len(new_ids)} filings "
          f"→ {len(announcements)} daily rows"
          + (f"  ⚠ {len(LAST_FAILURES)} FAILED" if LAST_FAILURES else ""))
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
