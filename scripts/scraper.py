#!/usr/bin/env python3
"""
IMB Buyback Tracker — Scraper
1. Henter seneste kurs fra Yahoo Finance
2. Scraper nye "Transaction in Own Shares" RNS-filings fra Investegate
3. Merger nye transaktioner ind i data.json (dedup via dato)
4. Kalder build_html.py til at regenerere index.html

Kør lokalt: python scripts/scraper.py
Kører automatisk via GitHub Actions (man-fre kl. 17:30 UTC).
"""

import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_PATH = ROOT / "data.json"
BUILD_SCRIPT = Path(__file__).parent / "build_html.py"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IMB-Tracker/2.0; +https://github.com/simonsen85-ops/imb-buyback-tracker)"
}


# ═══════════════════════════════════════════════════════════════
# 1. YAHOO FINANCE KURS
# ═══════════════════════════════════════════════════════════════

def fetch_yahoo_price(ticker: str = "IMB.L") -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        meta = data["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0
        return {
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "currency": meta.get("currency", "GBp"),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        }
    except Exception as e:
        print(f"  ✗ Yahoo Finance fejlede: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 2. INVESTEGATE RNS SCRAPING
# ═══════════════════════════════════════════════════════════════

def get_rns_links() -> list[str]:
    """Hent liste af 'Transaction in Own Shares' links fra Investegate."""
    url = "https://www.investegate.co.uk/company/IMB"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ✗ Company page fejlede: {e}")
        return []

    pattern = r'href="(/announcement/rns/imperial-brands--imb/transaction-in-own-shares/\d+)"'
    matches = re.findall(pattern, html, re.IGNORECASE)
    # Dedup + fulde URLs
    seen = set()
    links = []
    for m in matches:
        full = "https://www.investegate.co.uk" + m
        if full not in seen:
            seen.add(full)
            links.append(full)
    return links[:30]  # max 30 seneste


def parse_rns_announcement(url: str) -> dict | None:
    """Parse én RNS 'Transaction in Own Shares' meddelelse."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    ✗ {url}: {e}")
        return None

    # Strip HTML
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    # Dato
    months_map = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                  "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}

    date_match = re.search(
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        text, re.IGNORECASE
    )
    if date_match:
        day, month_str, year = date_match.groups()
    else:
        date_match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
            text, re.IGNORECASE
        )
        if not date_match:
            return None
        month_str, day, year = date_match.groups()

    try:
        dato = f"{year}-{months_map[month_str.lower()]:02d}-{int(day):02d}"
    except (KeyError, ValueError):
        return None

    # Antal aktier
    sh_m = re.search(
        r"(?:purchased|repurchased)\s+(?:for\s+cancellation\s+)?(\d[\d,]+)\s+(?:of\s+its\s+)?ordinary",
        text, re.IGNORECASE
    )
    if not sh_m:
        sh_m = re.search(r"Number\s+of\s+securities\s+purchased\s*:?\s*(\d[\d,]+)", text, re.IGNORECASE)
    if not sh_m:
        return None
    antal = int(sh_m.group(1).replace(",", ""))

    # Gns. kurs
    px_m = re.search(
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        text, re.IGNORECASE
    )
    gns_kurs = float(px_m.group(1).replace(",", "")) if px_m else 0.0

    # Aktier efter
    rem_m = re.search(
        r"(?:remaining|total)\s+(?:number\s+of\s+)?ordinary\s+shares\s+in\s+issue\s+"
        r"(?:will\s+be|is\s+now|is)\s+(\d[\d,]+)",
        text, re.IGNORECASE
    )
    aktier_efter = int(rem_m.group(1).replace(",", "")) if rem_m else None

    beloeb = round(antal * gns_kurs / 100 / 1e6, 1) if gns_kurs else 0

    return {
        "dato": dato,
        "antal_aktier": antal,
        "gns_kurs_gbp": gns_kurs,
        "beloeb_gbp_mio": beloeb,
        "aktier_efter": aktier_efter,
    }


# ═══════════════════════════════════════════════════════════════
# 3. MERGE & SAVE
# ═══════════════════════════════════════════════════════════════

def merge_transactions(existing: list, scraped: list) -> tuple[list, int]:
    """Dedup via dato. Returnerer (merged_list, antal_nye)."""
    existing_dates = {t["dato"] for t in existing}
    new_count = 0
    for tx in scraped:
        if tx["dato"] not in existing_dates:
            existing.append(tx)
            existing_dates.add(tx["dato"])
            new_count += 1
            print(f"    + {tx['dato']}: {tx['antal_aktier']:,} aktier @ {tx['gns_kurs_gbp']:.2f}p = £{tx['beloeb_gbp_mio']}M")
    existing.sort(key=lambda t: t["dato"], reverse=True)
    return existing, new_count


def main():
    print("═══ IMB Buyback Tracker — Scraper ═══")
    print(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    # 1. Kurs
    print("1. Yahoo Finance kurs...")
    price = fetch_yahoo_price()
    if price:
        data["kurs"] = price
        print(f"   ✓ {price['price']} GBp ({price['change_pct']:+.2f}%)")
    else:
        print("   ✗ Beholder gammel kurs")

    # 2. RNS scraping
    print("\n2. Investegate RNS filings...")
    links = get_rns_links()
    print(f"   Fandt {len(links)} links")

    scraped = []
    for i, link in enumerate(links):
        tx = parse_rns_announcement(link)
        if tx:
            scraped.append(tx)
        if (i + 1) % 10 == 0:
            print(f"   Parsed {i+1}/{len(links)}...")

    print(f"   Parsed {len(scraped)} transaktioner\n")

    # 3. Merge
    print("3. Merger nye transaktioner...")
    merged, new_count = merge_transactions(data["transaktioner"], scraped)
    data["transaktioner"] = merged
    if new_count == 0:
        print("   Ingen nye transaktioner")
    else:
        print(f"   ✓ {new_count} nye transaktioner tilføjet")

    # 4. Timestamp
    data["meta"]["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 5. Gem data.json
    DATA_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n✓ data.json gemt ({len(data['transaktioner'])} transaktioner)")

    # 6. Regenerer index.html
    print("\n4. Regenererer index.html...")
    result = subprocess.run([sys.executable, str(BUILD_SCRIPT)], capture_output=True, text=True)
    if result.returncode == 0:
        print(result.stdout.strip())
    else:
        print(f"   ✗ build fejlede: {result.stderr}")
        sys.exit(1)


if __name__ == "__main__":
    main()
