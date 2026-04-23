#!/usr/bin/env python3
"""
IMB Buyback Tracker — Scraper orchestrator (v3)

Modular design inspired by EVO refactor:
1. sources/yahoo.py     — live price
2. sources/investegate.py — RNS filings (with ID enumeration for robust backfill)
3. Merges into data.json (dedup via rns_id, fallback dato)
4. Regenerates index.html via build_html.py

Run: python scripts/scraper.py
Auto-runs via GitHub Actions (weekdays 17:30 UTC).
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sources import yahoo
from sources import investegate
from sources import lse_co_uk

ROOT = Path(__file__).parent.parent
DATA_PATH = ROOT / "data.json"
BUILD_SCRIPT = Path(__file__).parent / "build_html.py"


def get_last_known_id(data: dict):
    """Find highest rns_id in existing transactions for incremental scraping."""
    ids = []
    for t in data.get("transaktioner", []):
        rns_id = t.get("rns_id")
        if rns_id:
            try:
                ids.append(int(rns_id))
            except (TypeError, ValueError):
                pass
    return max(ids) if ids else None


def merge_transactions(existing: list, new: list):
    """Merge new announcements into existing, dedup by rns_id OR dato."""
    existing_ids = {t.get("rns_id") for t in existing if t.get("rns_id")}
    existing_dates = {t["dato"] for t in existing}
    added = 0
    for ann_dict in new:
        rns_id = ann_dict.get("rns_id")
        dato = ann_dict.get("dato")
        if rns_id and rns_id in existing_ids:
            continue
        if not rns_id and dato in existing_dates:
            continue
        existing.append(ann_dict)
        if rns_id:
            existing_ids.add(rns_id)
        existing_dates.add(dato)
        added += 1
    existing.sort(key=lambda t: t["dato"], reverse=True)
    return existing, added


def main():
    print("═══ IMB Buyback Tracker — Scraper v3 ═══")
    print(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    # 1. Yahoo price
    print("1. Yahoo Finance price...")
    price = yahoo.fetch_price()
    if price:
        data["kurs"] = price
        print(f"   ✓ {price['price']} GBp ({price['change_pct']:+.2f}%)")
    else:
        print("   ⚠ Keeping previous price")

    # 2. Investegate RNS scraping (primary)
    print("\n2. Investegate RNS filings...")
    last_id = get_last_known_id(data)
    print(f"   Last known RNS ID: {last_id or 'none (first run)'}")

    new_announcements = investegate.scrape_new_filings(
        last_known_id=last_id,
        max_lookback=200,
    )

    # 2b. LSE.co.uk fallback — only if Investegate returned nothing AND we have no data
    if not new_announcements and not data["transaktioner"]:
        print("\n2b. Investegate empty + no existing data → trying LSE.co.uk fallback...")
        new_announcements = lse_co_uk.scrape_listing()
        if new_announcements:
            print(f"   ✓ {len(new_announcements)} filings from LSE.co.uk")

    # 3. Merge
    print("\n3. Merging...")
    if new_announcements:
        new_dicts = [a.to_dict() for a in new_announcements]
        merged, added = merge_transactions(data["transaktioner"], new_dicts)
        data["transaktioner"] = merged
        print(f"   ✓ {added} new transactions added")
    else:
        print("   No new filings")

    # 4. Save
    data["meta"]["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data["meta"]["data_kilde"] = "Investegate RNS (primary), Yahoo Finance (price)"

    DATA_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n✓ data.json saved ({len(data['transaktioner'])} total transactions)")

    # 5. Rebuild HTML
    print("\n4. Regenerating index.html...")
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"   {result.stdout.strip()}")
    else:
        print(f"   ✗ Build failed: {result.stderr}")
        sys.exit(1)


if __name__ == "__main__":
    main()
