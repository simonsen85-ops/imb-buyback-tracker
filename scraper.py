"""
IMB Buyback Tracker — Scraper v2
- Henter aktiekurs fra Yahoo Finance (fallback i data.json)
- Scraper Investegate for nye "Transaction in Own Shares" RNS-filings
- HTML'en henter selv live kurs via JavaScript ved pageload

GitHub Actions kører dette dagligt (man–fre).
"""

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IMB-Tracker/1.0)"}


def fetch_yahoo_price(ticker: str = "IMB.L") -> dict | None:
    """Hent seneste kurs fra Yahoo Finance v8 chart API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        meta = data["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta.get("chartPreviousClose", meta.get("previousClose", price))
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0

        return {
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "currency": meta.get("currency", "GBp"),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
    except Exception as e:
        print(f"  Yahoo Finance fejl: {e}")
        return None


def scrape_investegate_rns() -> list:
    """
    Scrape Investegate for Imperial Brands 'Transaction in Own Shares' RNS-filings.
    Henter oversigten og parser individuelle meddelelser.
    """
    base_url = "https://www.investegate.co.uk"
    # Søg efter IMB announcements
    search_url = f"{base_url}/Index.aspx?searchtype=3&words=transaction+in+own+shares&company=IMB"
    req = urllib.request.Request(search_url, headers=HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Investegate søgning fejlede: {e}")
        # Fallback: prøv direkte company announcements
        return scrape_investegate_company_page()

    # Parse announcement links
    announcements = []
    # Find links til "Transaction in Own Shares" announcements
    pattern = r'href="(/announcement/rns/imperial-brands--imb/transaction-in-own-shares/\d+)"'
    matches = re.findall(pattern, html, re.IGNORECASE)

    if not matches:
        print("  Ingen RNS-links fundet i søgeresultat, prøver company page...")
        return scrape_investegate_company_page()

    for link in matches[:30]:  # Max 30 seneste
        full_url = base_url + link
        tx = parse_rns_announcement(full_url)
        if tx:
            announcements.append(tx)

    return announcements


def scrape_investegate_company_page() -> list:
    """Fallback: scrape IMB's company page for RNS filings."""
    url = "https://www.investegate.co.uk/company/IMB"
    req = urllib.request.Request(url, headers=HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Company page fejlede: {e}")
        return []

    announcements = []
    pattern = r'href="(/announcement/rns/imperial-brands--imb/transaction-in-own-shares/(\d+))"'
    matches = re.findall(pattern, html, re.IGNORECASE)

    for link, ann_id in matches[:20]:
        full_url = "https://www.investegate.co.uk" + link
        tx = parse_rns_announcement(full_url)
        if tx:
            announcements.append(tx)

    return announcements


def parse_rns_announcement(url: str) -> dict | None:
    """
    Parse en individuel RNS 'Transaction in Own Shares' meddelelse.
    Returnerer dict med dato, antal, gns. kurs, aktier_efter.
    """
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Kunne ikke hente {url}: {e}")
        return None

    # Fjern HTML-tags for lettere parsing
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    # Parse dato: "on DD Month YYYY" eller "on March 27, 2026"
    date_match = re.search(
        r"on\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        text, re.IGNORECASE
    )
    if not date_match:
        # Alternativt format
        date_match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
            text, re.IGNORECASE
        )
        if date_match:
            month_str, day, year = date_match.groups()
        else:
            return None
    else:
        day, month_str, year = date_match.groups()

    months = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
              "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
    try:
        dato = f"{year}-{months[month_str.lower()]:02d}-{int(day):02d}"
    except (KeyError, ValueError):
        return None

    # Parse antal aktier: "purchased ... NNN,NNN ... ordinary shares"
    # eller "repurchased NNN,NNN ordinary shares"
    shares_match = re.search(
        r"(?:purchased|repurchased)\s+(?:for\s+cancellation\s+)?(?:the\s+following\s+number\s+of\s+)?(\d[\d,]+)\s+(?:of\s+its\s+)?ordinary\s+shares",
        text, re.IGNORECASE
    )
    # Alternativt: "Number of securities purchased: NNN,NNN"
    if not shares_match:
        shares_match = re.search(r"Number\s+of\s+securities\s+purchased\s*:?\s*(\d[\d,]+)", text, re.IGNORECASE)
    if not shares_match:
        return None

    antal = int(shares_match.group(1).replace(",", ""))

    # Parse gennemsnitskurs: "average price of GBp NNN.NN" eller "GBp 3,050.44"
    price_match = re.search(
        r"average\s+price\s+(?:paid\s+)?(?:per\s+share\s+)?(?:was\s+)?(?:of\s+)?(?:GBp?\s*)?(\d[\d,]*\.?\d*)\s*(?:pence|p|GBp)?",
        text, re.IGNORECASE
    )
    if not price_match:
        price_match = re.search(r"(?:Average|Avg).*?(\d[\d,]*\.\d{2})\s*(?:pence|p|GBp)", text, re.IGNORECASE)
    
    gns_kurs = 0.0
    if price_match:
        gns_kurs = float(price_match.group(1).replace(",", ""))

    # Parse aktier efter: "remaining number of ordinary shares in issue will be NNN,NNN,NNN"
    remaining_match = re.search(
        r"(?:remaining|total)\s+(?:number\s+of\s+)?ordinary\s+shares\s+in\s+issue\s+(?:will\s+be|is\s+now|is)\s+(\d[\d,]+)",
        text, re.IGNORECASE
    )
    aktier_efter = None
    if remaining_match:
        aktier_efter = int(remaining_match.group(1).replace(",", ""))

    # Beregn beløb
    beloeb = round(antal * gns_kurs / 100 / 1e6, 1)  # GBp → £ → £M

    return {
        "dato": dato,
        "antal_aktier": antal,
        "gns_kurs_gbp": gns_kurs,
        "beloeb_gbp_mio": beloeb,
        "aktier_efter": aktier_efter,
        "kilde": url,
    }


def merge_transactions(existing: list, scraped: list) -> list:
    """Merge nye transaktioner ind i eksisterende, undgå dubletter (via dato)."""
    existing_dates = {t["dato"] for t in existing}
    new_count = 0

    for tx in scraped:
        if tx["dato"] not in existing_dates:
            existing.append(tx)
            existing_dates.add(tx["dato"])
            new_count += 1
            print(f"  Ny transaktion: {tx['dato']} — {tx['antal_aktier']:,} aktier @ {tx['gns_kurs_gbp']:.2f}p")

    if new_count == 0:
        print("  Ingen nye transaktioner fundet")
    else:
        print(f"  {new_count} nye transaktioner tilføjet")

    # Sortér faldende efter dato
    existing.sort(key=lambda t: t["dato"], reverse=True)
    return existing


def load_data() -> dict:
    """Indlæs data.json."""
    data_path = Path(__file__).parent / "data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict):
    """Gem data.json."""
    data_path = Path(__file__).parent / "data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    print("═══ IMB Buyback Tracker — Scraper v2 ═══")
    data = load_data()

    # 1. Hent kurs
    print("\n1. Henter kurs fra Yahoo Finance...")
    price = fetch_yahoo_price()
    if price:
        data["kurs"] = price
        print(f"  Kurs: {price['price']} GBp ({price['change_pct']:+.2f}%)")
    else:
        print("  Beholder gammel kursdata")

    # 2. Scrape nye RNS-filings
    print("\n2. Scraper Investegate for nye RNS-filings...")
    scraped = scrape_investegate_rns()
    if scraped:
        print(f"  Fandt {len(scraped)} transaktioner fra Investegate")
        data["transaktioner"] = merge_transactions(data.get("transaktioner", []), scraped)
    else:
        print("  Ingen transaktioner scraped (kan skyldes netværk/CORS)")

    # 3. Opdater metadata
    data["sidst_opdateret"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 4. Beregn summerede metrics
    txs = data.get("transaktioner", [])
    if txs:
        total_shares = sum(t["antal_aktier"] for t in txs)
        total_spent = sum(t["beloeb_gbp_mio"] for t in txs)
        shares_now = txs[0].get("aktier_efter") if txs[0].get("aktier_efter") else (807300000 - total_shares)

        data["summary"] = {
            "total_aktier_tilbagekoebt": total_shares,
            "total_brugt_mio": round(total_spent, 1),
            "aktier_nu": shares_now,
            "fremgang_pct": round(total_spent / 1450 * 100, 1),
        }
        print(f"\n  Summary: {total_shares:,} aktier, £{total_spent:.1f}M brugt, {total_spent/1450*100:.0f}% af program")

    # 5. Gem
    save_data(data)
    print("\n✓ data.json gemt")


if __name__ == "__main__":
    main()
