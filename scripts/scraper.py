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
    matches = []
    for prog_key, prog in programs.items():
        start = prog.get("start")
        end = prog.get("afsluttet") or prog.get("frist")
        if start and end and start <= dato <= end:
            matches.append((start, prog_key))
    # FY26's deadline (28 Oct 2026) overlaps FY27's start (8 Oct 2026):
    # the newest programme wins.
    return max(matches)[1] if matches else "unknown"


# Programme facts from the RNS filings. ensure_programs() applies them once,
# so data.json never has to be edited by hand when a new programme starts.
FY27_PROGRAM = {
    "navn": "FY27 Aktietilbagekøbsprogram",
    "total_gbp_mio": 1500,
    "annonceret": "2026-10-08",
    "start": "2026-10-08",
    "frist": "2027-10-29",
    "status": "aktiv",
    "maegler_t1": "HSBC",
    "tranche_1": {"beloeb_mio": 750, "start": "2026-10-08",
                  "slut": "2027-05-04", "status": "aktiv"},
    "tranche_2": {"beloeb_mio": 750, "start": "2027-05-05",
                  "slut": "2027-10-29", "status": "kommende"},
    "fundamentals": {
        # PLACEHOLDER: FY26E consensus/FY25 FCF carried over until the FY26
        # results on 17 Nov 2026 give a proper base — update by hand then.
        "eps_adjusted_gbp": 3.41,
        "eps_source": "Placeholder: FY26E konsensus (opdatér efter FY26-resultat 17. nov 2026)",
        "fcf_mio_gbp": 2700,
        # Shares in issue after the last FY26 purchase (6 Oct 2026)
        "aktier_ved_start": 758131896,
        "pct_af_kapital": 6.7,
        "fair_pe": 10,
    },
}


def ensure_programs(data: dict) -> None:
    programs = data.setdefault("programmer", {})
    fy26 = programs.get("FY26")
    if fy26 and fy26.get("status") == "aktiv":
        # £1,456M of the £1,450M bought by 6 Oct 2026; completion confirmed
        # in the 8 Oct 2026 trading update.
        fy26["status"] = "fuldført"
        fy26["afsluttet"] = "2026-10-06"
        if isinstance(fy26.get("tranche_2"), dict):
            fy26["tranche_2"]["status"] = "fuldført"
            fy26["tranche_2"]["slut"] = "2026-10-06"
        print("   migration: FY26 marked fuldført (afsluttet 2026-10-06)")
    if "FY27" not in programs:
        programs["FY27"] = json.loads(json.dumps(FY27_PROGRAM))
        print("   migration: FY27 programme added")
    if data.get("aktuel_program") in (None, "FY26"):
        data["aktuel_program"] = "FY27"


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
    # A weekly HSBC filing yields one row per trade date under the SAME
    # rns_id, so id-dedup must be per (rns_id, dato), not per rns_id.
    existing_ids = {(t.get("rns_id"), t.get("dato")) for t in existing if t.get("rns_id")}
    existing_keys = {transaction_key(t) for t in existing}
    added = 0
    skipped_id = 0
    skipped_key = 0
    for ann_dict in new:
        rns_id = ann_dict.get("rns_id")
        dato = ann_dict.get("dato")

        if rns_id and (rns_id, dato) in existing_ids:
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
            existing_ids.add((rns_id, dato))
        existing_keys.add(key)
        added += 1

    if skipped_id or skipped_key:
        print(f"   dedup: {skipped_id} skipped by rns_id, "
              f"{skipped_key} skipped as same transaction from another source")

    # Re-tag existing transactions that may lack a program field (migration)
    for t in existing:
        if not t.get("program") or t.get("program") == "unknown":
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
    if new_ann or investegate.LAST_LISTING_OK:
        # Investegate answered. "Nothing new" or "could not parse" must NOT
        # trigger the fallbacks: they would re-read the same filings with
        # parsers that know only the single-day format.
        return new_ann

    print("   Investegate unreachable — trying ADVFN fallback")
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
    if new_ann or investegate.LAST_LISTING_OK:
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
    ensure_programs(data)
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
    # Unparsed buyback filings are written down instead of being dropped
    # silently; the workflow's last step fails the run when this is non-empty
    # (GitHub then emails the repo owner). Rewritten every run, so it clears
    # itself once the filings parse.
    data["meta"]["parse_failures"] = [
        {"rns_id": rid, "reason": why} for rid, why in investegate.LAST_FAILURES
    ]
    if investegate.LAST_FAILURES:
        print(f"\n   ⚠ {len(investegate.LAST_FAILURES)} buyback filing(s) could not be parsed: "
              + ", ".join(rid for rid, _ in investegate.LAST_FAILURES))

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
