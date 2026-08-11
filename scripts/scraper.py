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
from sources import advfn
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


def transaction_key(t: dict):
    """
    Economic identity of a buyback: date + shares + average price.

    rns_id alone is NOT sufficient for dedup. The same filing carries a
    different id depending on which source found it (`advfn_98357343` vs
    Investegate's bare `9537970`), so an id-only check lets the same
    transaction in twice whenever the primary source changes. That is
    exactly how the 48 duplicates cleaned up in v8.1 were created — and it
    would have re-added ~800 of them on the first deep Investegate backfill,
    since 809 of the stored rows still carry ADVFN ids.
    """
    return (t["dato"], t["antal_aktier"], round(t["gns_kurs_gbp"], 2))


def merge_transactions(existing: list, new: list, programs: dict):
    """Merge new announcements, dedup by rns_id AND economic identity."""
    existing_ids = {t.get("rns_id") for t in existing if t.get("rns_id")}
    existing_keys = {transaction_key(t) for t in existing}
    added = 0
    skipped_id = 0
    skipped_key = 0
    for ann_dict in new:
        rns_id = ann_dict.get("rns_id")
        dato = ann_dict.get("dato")

        if rns_id and rns_id in existing_ids:
            skipped_id += 1
            continue

        key = transaction_key(ann_dict)
        if key in existing_keys:
            # Already held under a different source's rns_id — keep the
            # stored row (its id is what dedup/backfill state is built on).
            skipped_key += 1
            continue

        ann_dict["program"] = assign_program(dato, programs)
        existing.append(ann_dict)
        if rns_id:
            existing_ids.add(rns_id)
        existing_keys.add(key)
        added += 1

    if skipped_id or skipped_key:
        print(f"   dedup: {skipped_id} skipped by rns_id, "
              f"{skipped_key} skipped as same transaction from another source")

    # Re-tag existing transactions that may lack a program field (migration)
    for t in existing:
        if not t.get("program"):
            t["program"] = assign_program(t["dato"], programs)

    existing.sort(key=lambda t: t["dato"], reverse=True)
    return existing, added


def get_known_lse_hashes(data: dict) -> set:
    """Collect all LSE.co.uk-style rns_id's ('lse_{hash}') for dedup."""
    hashes = set()
    for t in data.get("transaktioner", []):
        rns_id = t.get("rns_id", "")
        if rns_id and rns_id.startswith("lse_"):
            hashes.add(rns_id)
    return hashes


def get_known_advfn_ids(data: dict) -> set:
    """Collect all ADVFN-style rns_id's ('advfn_{id}') for dedup."""
    ids = set()
    for t in data.get("transaktioner", []):
        rns_id = t.get("rns_id", "")
        if rns_id and rns_id.startswith("advfn_"):
            ids.add(rns_id)
    return ids


def get_known_investegate_ids(data: dict) -> set:
    """
    Collect bare-numeric rns_id's (Investegate style, e.g. '9713069') for dedup.
    Prefixed IDs from other sources are excluded.
    """
    ids = set()
    for t in data.get("transaktioner", []):
        rns_id = t.get("rns_id")
        if rns_id and str(rns_id).isdigit():
            ids.add(str(rns_id))
    return ids


def normal_scrape(data: dict) -> list:
    """
    Daily incremental scrape.

    SOURCE PRIORITY (changed Aug 2026):
      1. Investegate — server-rendered, per-company listing, paginated.
      2. ADVFN       — was primary until it added site-wide bot detection
                       (HTTP 403 to every non-browser client). Kept in the
                       chain so it resumes automatically if that is relaxed.
      3. LSE.co.uk   — last resort; its RNS body is JS-rendered so it has
                       never parsed reliably.
    """
    known_ig = get_known_investegate_ids(data)
    print(f"   Known Investegate ids: {len(known_ig)}")

    new_ann = investegate.scrape_filings(
        known_ids=known_ig,
        max_pages=2,        # ~3 months of cover — survives a long outage
        request_delay=1.0,
    )
    if new_ann:
        return new_ann

    print("   Investegate returned nothing — trying ADVFN fallback")
    known_advfn = get_known_advfn_ids(data)
    new_ann = advfn.scrape_filings(
        known_ids=known_advfn,
        max_pages=3,
        request_delay=1.0,
    )
    if new_ann:
        return new_ann

    print("   ADVFN returned nothing — trying LSE.co.uk fallback")
    known_lse = get_known_lse_hashes(data)
    return lse_co_uk.scrape_new_filings(
        known_hashes=known_lse,
        max_filings=50,
        request_delay=1.0,
    )


def backfill_scrape(data: dict, n: int, start_page: int = 1) -> list:
    """
    Backfill via Investegate listing pagination — `n` is page count.

    Each page = 50 announcements (~35 of them buybacks).
      n=2  → ~3 months
      n=10 → ~1.5 years
      n=32 → full archive (Investegate holds 1,566 IMB announcements)
    """
    known_ig = get_known_investegate_ids(data)
    print(f"   Known Investegate ids: {len(known_ig)}")
    print(f"   Paginating {n} listing pages from page {start_page}...")

    new_ann = investegate.scrape_filings(
        known_ids=known_ig,
        max_pages=n,
        request_delay=1.2,
        start_page=start_page,
    )
    if new_ann:
        return new_ann

    print("   Investegate returned nothing — falling back to ADVFN")
    known_advfn = get_known_advfn_ids(data)
    new_ann = advfn.scrape_filings(
        known_ids=known_advfn,
        max_pages=n,
        request_delay=1.5,
    )
    if new_ann:
        return new_ann

    print("   ADVFN returned nothing — falling back to LSE.co.uk")
    known_lse = get_known_lse_hashes(data)
    return lse_co_uk.scrape_new_filings(
        known_hashes=known_lse,
        max_filings=200,
        request_delay=1.5,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-page", type=int, default=1,
                        help="Listing page to start the backfill from (default 1)")
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
        new_announcements = backfill_scrape(data, args.backfill, start_page=args.from_page)
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
