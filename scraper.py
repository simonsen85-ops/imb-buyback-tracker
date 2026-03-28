"""
IMB Buyback Tracker — Scraper v4
Kilder:
  1. Investegate.co.uk — scraper IMB's RNS-oversigt for "Transaction in Own Shares"
  2. Yahoo Finance — live kurs til fallback i data.json

Fix v4: Investegate kræver "Private Investor" accept-cookie (ig_acc=1).
        Bruger http.cookiejar til at sætte denne automatisk.
        Tilføjer debug-logging hvis 0 links findes.

GitHub Actions kører dette dagligt (man-fre kl. 17:30 UTC).
"""

import json
import re
import http.cookiejar
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data.json"


def make_opener():
    """Opret urllib opener med cookie-support og Investegate accept-cookie."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "en-GB,en;q=0.9"),
    ]
    # Sæt Investegate "Private Investor" accept-cookie
    cookie = http.cookiejar.Cookie(
        version=0, name="ig_acc", value="1",
        port=None, port_specified=False,
        domain=".investegate.co.uk", domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True,
        secure=False, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )
    cj.set_cookie(cookie)
    return opener


OPENER = make_opener()


# ═══════════════════════════════════════════════
# 1. YAHOO FINANCE
# ═══════════════════════════════════════════════

def fetch_yahoo_price(ticker="IMB.L"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        meta = data["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta.get("chartPreviousClose", meta.get("previousClose", price))
        return {
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change": round(price - prev, 2),
            "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
            "currency": meta.get("currency", "GBp"),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
    except Exception as e:
        print(f"  Yahoo Finance fejl: {e}")
        return None


# ═══════════════════════════════════════════════
# 2. INVESTEGATE — scrape RNS-filings
# ═══════════════════════════════════════════════

def get_rns_links():
    """Hent Investegate IMB company page, find alle Transaction in Own Shares links."""
    url = "https://www.investegate.co.uk/company/IMB"
    try:
        with OPENER.open(url, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Investegate fejl: {e}")
        return []

    print(f"  HTML hentet: {len(html)} bytes")

    # Find alle "Transaction in Own Shares" links
    pattern = r'href="(/announcement/rns/imperial-brands--imb/transaction-in-own-shares/(\d+))"'
    matches = re.findall(pattern, html, re.IGNORECASE)

    links = []
    seen = set()
    for path, ann_id in matches:
        if ann_id not in seen:
            links.append(f"https://www.investegate.co.uk{path}")
            seen.add(ann_id)

    print(f"  Fandt {len(links)} 'Transaction in Own Shares'-links")

    if len(links) == 0:
        # Debug: tjek hvad vi faktisk fik
        all_ann = re.findall(r'href="(/announcement/[^"]+)"', html)
        print(f"  DEBUG: {len(all_ann)} announcement-links totalt")
        for l in all_ann[:5]:
            print(f"    {l}")

        if "Transaction in Own Shares" in html:
            print("  DEBUG: Teksten 'Transaction in Own Shares' FINDES i HTML")
            idx = html.find("Transaction in Own Shares")
            # Vis rå HTML omkring den (med tags)
            raw = html[max(0, idx-300):idx+100]
            print(f"  DEBUG raw HTML context:\n{raw}")
        elif "transaction" in html.lower():
            print("  DEBUG: 'transaction' (lowercase) fundet i HTML")
            idx = html.lower().find("transaction")
            raw = html[max(0, idx-100):idx+200]
            print(f"  DEBUG context:\n{raw}")
        else:
            print("  DEBUG: Ingen 'transaction' fundet. Cookie-gate?")
            print(f"  DEBUG: Foerste 1000 chars:\n{html[:1000]}")

    return links


def parse_rns_page(url):
    """Parse en enkelt RNS Transaction in Own Shares meddelelse."""
    try:
        with OPENER.open(url, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    Kunne ikke hente {url}: {e}")
        return None

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12
    }

    # DATO
    date_m = re.search(
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        text, re.IGNORECASE
    )
    if not date_m:
        return None
    day, mon, year = date_m.groups()
    dato = f"{year}-{months[mon.lower()]:02d}-{int(day):02d}"

    # ANTAL AKTIER
    sh_m = re.search(
        r"(?:purchased|repurchased)\s+(?:for\s+cancellation\s+)?(?:the\s+following\s+number\s+of\s+its\s+)?(\d[\d,]+)\s+(?:of\s+its\s+)?ordinary",
        text, re.IGNORECASE
    )
    if not sh_m:
        sh_m = re.search(r"Number\s+of\s+securities\s+purchased\s*:?\s*(\d[\d,]+)", text, re.IGNORECASE)
    if not sh_m:
        return None
    antal = int(sh_m.group(1).replace(",", ""))

    # GNS. KURS
    px_m = re.search(
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.\d+)",
        text, re.IGNORECASE
    )
    gns_kurs = float(px_m.group(1).replace(",", "")) if px_m else 0.0

    # AKTIER EFTER
    rem_m = re.search(
        r"(?:remaining|total)\s+(?:number\s+of\s+)?ordinary\s+shares\s+in\s+issue\s+(?:will\s+be|is\s+now|is)\s+(\d[\d,]+)",
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
        "kilde": url,
    }


def scrape_all_rns():
    """Hent alle Transaction in Own Shares fra Investegate og parse dem."""
    links = get_rns_links()
    results = []
    for i, link in enumerate(links):
        tx = parse_rns_page(link)
        if tx:
            results.append(tx)
        if (i + 1) % 10 == 0:
            print(f"    Parsed {i+1}/{len(links)}...")
    print(f"  Parsed {len(results)} transaktioner succesfuldt")
    return results


# ═══════════════════════════════════════════════
# 3. MERGE & SAVE
# ═══════════════════════════════════════════════

def merge_transactions(existing, scraped):
    existing_dates = {t["dato"] for t in existing}
    new_count = 0
    for tx in scraped:
        if tx["dato"] not in existing_dates:
            existing.append(tx)
            existing_dates.add(tx["dato"])
            new_count += 1
            print(f"    + {tx['dato']}: {tx['antal_aktier']:,} aktier @ {tx['gns_kurs_gbp']:.2f}p = {tx['beloeb_gbp_mio']}M GBP")
    if new_count == 0:
        print("  Ingen nye transaktioner")
    else:
        print(f"  {new_count} nye transaktioner tilfojet")
    existing.sort(key=lambda t: t["dato"], reverse=True)
    return existing


def main():
    print("=== IMB Buyback Tracker - Scraper v4 ===")
    print(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Kurs
    print("1. Yahoo Finance kurs...")
    price = fetch_yahoo_price()
    if price:
        data["kurs"] = price
        print(f"  OK: {price['price']} GBp ({price['change_pct']:+.2f}%)")
    else:
        print("  Beholder gammel kurs")

    # 2. RNS-filings
    print("\n2. Investegate RNS-filings...")
    scraped = scrape_all_rns()
    if scraped:
        data["transaktioner"] = merge_transactions(
            data.get("transaktioner", []), scraped
        )

    # 3. Summary
    txs = data.get("transaktioner", [])
    if txs:
        total_shares = sum(t["antal_aktier"] for t in txs)
        total_spent = sum(t["beloeb_gbp_mio"] for t in txs)
        shares_now = next(
            (t["aktier_efter"] for t in sorted(txs, key=lambda x: x["dato"], reverse=True) if t.get("aktier_efter")),
            807300000 - total_shares
        )
        data["summary"] = {
            "total_aktier_tilbagekoebt": total_shares,
            "total_brugt_mio": round(total_spent, 1),
            "aktier_nu": shares_now,
            "fremgang_pct": round(total_spent / 1450 * 100, 1),
        }
        print(f"\n  {total_shares:,} aktier tilbagekoebt")
        print(f"  GBP {total_spent:.1f}M brugt ({total_spent/1450*100:.0f}% af 1,450M program)")
        print(f"  {shares_now:,} aktier i omloeb")

    # 4. Gem
    data["sidst_opdateret"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("\nOK: data.json gemt")


if __name__ == "__main__":
    main()
