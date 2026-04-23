#!/usr/bin/env python3
"""
IMB Buyback Tracker — Scraper orchestrator (v4)

Modes:
  Normal (default)  : Incremental forward scraping (enumerate IDs > last_known_id)
  --backfill N      : Historical backward scraping (enumerate N IDs before lowest_known_id)

Usage:
  python scripts/scraper.py              # daily update
  python scripts/scraper.py --backfill 500  # historical backfill (one-off)

Auto-runs via GitHub Actions (weekdays 17:30 UTC, or manual backfill trigger).
"""

import argparse
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


def get_highest_known_id(data: dict):
    """Highest rns_id — used for incremental forward scraping."""
    ids = []
    for t in data.get("transaktioner", []):
        rns_id = t.get("rns_id")
        if rns_id:
            try:
                ids.append(int(rns_id))
            except (TypeError, ValueError):
                pass
    return max(ids) if ids else None


def get_lowest_known_id(data: dict):
    """Lowest rns_id — used for backfill starting point."""
    ids = []
    for t in data.get("transaktioner", []):
        rns_id = t.get("rns_id")
        if rns_id:
            try:
                ids.append(int(rns_id))
            except (TypeError, ValueError):
                pass
    return min(ids) if ids else None


def assign_program(dato: str, programs: dict) -> str:
    """
    Assign transaction to the correct buyback program based on date.
    Programs are identified by their start/frist date range.
    """
    for prog_key, prog in programs.items():
        start = prog.get("start")
        frist = prog.get("frist")
        if start and frist and start <= dato <= frist:
            return prog_key
    return "unknown"


def merge_transactions(existing: list, new: list, programs: dict):
    """Merge new announcements, dedup by rns_id, tag with program."""
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
        # Tag with program
        ann_dict["program"] = assign_program(dato, programs)
        existing.append(ann_dict)
        if rns_id:
            existing_ids.add(rns_id)
        existing_dates.add(dato)
        added += 1

    # Re-tag existing transactions that may lack a program field (migration)
    for t in existing:
        if not t.get("program"):
            t["program"] = assign_program(t["dato"], programs)

    existing.sort(key=lambda t: t["dato"], reverse=True)
    return existing, added


def normal_scrape(data: dict) -> list:
    """Incremental forward scrape: new filings since last run."""
    last_id = get_highest_known_id(data)
    print(f"   Last known RNS ID: {last_id or 'none (first run)'}")
    return investegate.scrape_new_filings(last_known_id=last_id, max_lookback=200)


def backfill_scrape(data: dict, n: int) -> list:
    """
    Backward scrape: enumerate N IDs backwards from the lowest known ID.
    Used for one-off historical data recovery.
    """
    lowest = get_lowest_known_id(data)
    if not lowest:
        print("   No existing data — use normal mode first to establish baseline")
        return []

    start = lowest - 1      # start one below lowest known
    end = max(1, start - n) # go back N IDs
    print(f"   Backfill: enumerating IDs {end}..{start} ({start - end + 1} candidates)")

    # Use probe_recent_ids in reverse direction by iterating manually
    announcements = []
    hits = 0
    misses = 0
    total = start - end + 1
    for i, rns_id in enumerate(range(start, end - 1, -1)):
        ann = investegate.parse_rns_page(rns_id)
        if ann:
            announcements.append(ann)
            hits += 1
            print(f"    ✓ {rns_id}: {ann.dato} | {ann.antal_aktier:,} @ {ann.gns_kurs_gbp:.2f}p = £{ann.beloeb_gbp_mio}M")
        else:
            misses += 1
        if (i + 1) % 50 == 0:
            print(f"    [{i+1}/{total}] {hits} hits, {misses} misses")

    print(f"  Backfill parsed {hits} buyback filings ({misses} skipped)")
    return announcements


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, default=0,
                        help="Historical backfill mode: enumerate N IDs backwards from lowest known")
    args = parser.parse_args()

    mode = "BACKFILL" if args.backfill else "NORMAL"
    print(f"═══ IMB Buyback Tracker — Scraper v4 [{mode}] ═══")
    print(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    programs = data.get("programmer", {})

    # 1. Yahoo price (always)
    print("1. Yahoo Finance price...")
    price = yahoo.fetch_price()
    if price:
        data["kurs"] = price
        print(f"   ✓ {price['price']} GBp ({price['change_pct']:+.2f}%)")
    else:
        print("   ⚠ Keeping previous price")

    # 2. RNS scraping (mode-dependent)
    if args.backfill:
        print(f"\n2. Backfill mode: fetching {args.backfill} historical IDs...")
        new_announcements = backfill_scrape(data, args.backfill)
    else:
        print("\n2. Normal mode: Investegate RNS filings...")
        new_announcements = normal_scrape(data)
        # Fallback to LSE.co.uk if first-run and Investegate empty
        if not new_announcements and not data["transaktioner"]:
            print("\n2b. Investegate empty + no existing data → trying LSE.co.uk fallback...")
            new_announcements = lse_co_uk.scrape_listing()

    # 3. Merge with program-tagging
    print("\n3. Merging and tagging with programs...")
    if new_announcements:
        new_dicts = [a.to_dict() for a in new_announcements]
        merged, added = merge_transactions(data["transaktioner"], new_dicts, programs)
        data["transaktioner"] = merged
        print(f"   ✓ {added} new transactions added")
    else:
        # Even without new data, ensure existing transactions are tagged
        merged, _ = merge_transactions(data["transaktioner"], [], programs)
        data["transaktioner"] = merged
        print("   No new filings (existing transactions re-tagged)")

    # Show program breakdown
    prog_counts = {}
    for t in data["transaktioner"]:
        p = t.get("program", "unknown")
        prog_counts[p] = prog_counts.get(p, 0) + 1
    print(f"   Program breakdown: {prog_counts}")

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
