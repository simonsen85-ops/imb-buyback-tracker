"""
Deduplicate data.json.

The same buyback can appear more than once because it was picked up by
different sources over the project's life (Investegate, ADVFN, manual entry),
each writing its own rns_id — so rns_id-based dedup in scraper.py cannot
catch them. This matches on the transaction's economic identity instead:
(date, shares, average price).

Which row wins, in order:
  1. investegate — bare numeric rns_id; our primary source, re-verifiable
  2. advfn       — prefixed rns_id, scraped from the real RNS
  3. lse         — prefixed rns_id
  4. no_id       — earliest manual entries; these were found to carry
                   `aktier_efter` values that break the monotonic decline
                   (e.g. 786,473,966 on 2025-12-01 where the surrounding
                   filings read 800.9M → 800.5M → 800.1M), so they lose
                   to any scraped row.

--drop-unverified additionally removes rows that have no rns_id at all.
These are seed rows from the v1/v2 prototype, before any scraper existed.
They are demonstrably not real filings:
  * VWAP prices ending in exactly .00 (3190.00, 3215.00, ...) — a
    volume-weighted average is never exactly round; real ones read 2767.3239
  * round share counts (385,000 / 410,000 / 450,000)
  * several land on a date that already has a real RNS with different figures
  * every one of them has an `aktier_efter` that breaks the monotonic
    decline (786M where the surrounding real filings read 803M)

Run:  python scripts/dedup_data.py [--dry-run] [--drop-unverified]
"""

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data.json"

PRIORITY = {"investegate": 0, "advfn": 1, "lse": 2, "no_id": 3}


def source_of(t: dict) -> str:
    rns_id = str(t.get("rns_id") or "")
    if rns_id.startswith("advfn_"):
        return "advfn"
    if rns_id.startswith("lse_"):
        return "lse"
    if rns_id.isdigit():
        return "investegate"
    return "no_id"


def dedup_key(t: dict):
    return (t["dato"], t["antal_aktier"], round(t["gns_kurs_gbp"], 2))


def main():
    dry_run = "--dry-run" in sys.argv

    data = json.loads(DATA.read_text())
    tx = data["transaktioner"]
    before = len(tx)

    # Stable sort by source priority so the preferred row is seen first
    ordered = sorted(tx, key=lambda t: PRIORITY[source_of(t)])

    seen = {}
    kept = []
    dropped = []
    for t in ordered:
        k = dedup_key(t)
        if k in seen:
            dropped.append((t, seen[k]))
            continue
        seen[k] = t
        kept.append(t)

    unverified = []
    if "--drop-unverified" in sys.argv:
        unverified = [t for t in kept if source_of(t) == "no_id"]
        kept = [t for t in kept if source_of(t) != "no_id"]

    kept.sort(key=lambda t: t["dato"], reverse=True)

    print(f"Before: {before}   After: {len(kept)}   "
          f"Removed: {len(dropped) + len(unverified)}")

    if unverified:
        lost = sum(t.get("beloeb_gbp_mio", 0) for t in unverified)
        print(f"\nUnverified rows removed ({len(unverified)}, £{lost:.1f}M):")
        for t in sorted(unverified, key=lambda x: x["dato"]):
            print(f"  {t['dato']}  {t['antal_aktier']:>9,} "
                  f"@ {t['gns_kurs_gbp']:>9.2f}p")
    if dropped:
        print("\nRemoved rows (loser → winner):")
        for loser, winner in dropped[:60]:
            print(f"  {loser['dato']}  {loser['antal_aktier']:>9,} "
                  f"@ {loser['gns_kurs_gbp']:.2f}p   "
                  f"{source_of(loser)}/{loser.get('rns_id')} → "
                  f"{source_of(winner)}/{winner.get('rns_id')}")
        if len(dropped) > 60:
            print(f"  ... and {len(dropped) - 60} more")

    if dry_run:
        print("\n--dry-run: data.json not modified")
        return

    data["transaktioner"] = kept
    DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\n✓ data.json written ({len(kept)} transactions)")


if __name__ == "__main__":
    main()
