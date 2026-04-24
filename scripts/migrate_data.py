#!/usr/bin/env python3
"""
One-shot migration: Convert old data.json (v3) → new multi-program v4 format.

What it does:
1. Detects if data.json has old `program{}` (single) or new `programmer{}` (multi)
2. If old: builds the full `programmer{}` dict with FY24 + FY25 + FY26 fundamentals
3. Removes transactions with gns_kurs_gbp == 0 (bad regex matches from earlier run)
4. Re-tags remaining transactions with correct program based on date

Run once: python scripts/migrate_data.py
After: delete this file (or keep for reference — harmless to rerun).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_PATH = ROOT / "data.json"


NEW_PROGRAMS = {
    "FY26": {
        "navn": "FY26 Aktietilbagekøbsprogram",
        "total_gbp_mio": 1450,
        "annonceret": "2025-10-07",
        "start": "2025-10-30",
        "frist": "2026-10-28",
        "status": "aktiv",
        "maegler_t1": "Morgan Stanley",
        "maegler_t2": "Barclays",
        "tranche_1": {"beloeb_mio": 725, "start": "2025-10-30", "slut": "2026-04-30", "status": "fuldført"},
        "tranche_2": {"beloeb_mio": 725, "start": "2026-04-15", "slut": "2026-10-28", "status": "aktiv"},
        "fundamentals": {
            "eps_adjusted_gbp": 3.41,
            "eps_source": "FY26E konsensus",
            "fcf_mio_gbp": 2700,
            "aktier_ved_start": 783274508,
            "pct_af_kapital": 5.7,
            "fair_pe": 10
        }
    },
    "FY25": {
        "navn": "FY25 Aktietilbagekøbsprogram",
        "total_gbp_mio": 1250,
        "annonceret": "2024-10-08",
        "start": "2024-11-01",
        "frist": "2025-10-29",
        "status": "fuldført",
        "maegler_t1": "Morgan Stanley",
        "fundamentals": {
            "eps_adjusted_gbp": 3.044,
            "eps_source": "FY25 rapporteret",
            "fcf_mio_gbp": 2700,
            "aktier_ved_start": 827887000,
            "pct_af_kapital": 7.0,
            "fair_pe": 10
        }
    },
    "FY24": {
        "navn": "FY24 Aktietilbagekøbsprogram",
        "total_gbp_mio": 1100,
        "annonceret": "2023-11-14",
        "start": "2023-11-15",
        "frist": "2024-10-31",
        "status": "fuldført",
        "maegler_t1": "Morgan Stanley",
        "fundamentals": {
            "eps_adjusted_gbp": 2.763,
            "eps_source": "FY24 rapporteret",
            "fcf_mio_gbp": 2430,
            "aktier_ved_start": 876000000,
            "pct_af_kapital": 7.0,
            "fair_pe": 10
        }
    }
}


def assign_program(dato: str, programs: dict) -> str:
    for prog_key, prog in programs.items():
        start = prog.get("start")
        frist = prog.get("frist")
        if start and frist and start <= dato <= frist:
            return prog_key
    return "unknown"


def main():
    print("═══ Migrating data.json to v4 multi-program format ═══\n")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    # Step 1: Convert structure if needed
    if "programmer" not in data:
        print("1. Old structure detected — converting...")
        if "program" in data:
            print(f"   Old program: {data['program'].get('navn', 'unknown')}")
            del data["program"]
        data["programmer"] = NEW_PROGRAMS
        data["aktuel_program"] = "FY26"
        print(f"   ✓ Added {len(NEW_PROGRAMS)} programs: {list(NEW_PROGRAMS.keys())}")
    else:
        print("1. Already in new format — refreshing program definitions...")
        data["programmer"] = NEW_PROGRAMS  # Ensure consistent fundamentals
        data["aktuel_program"] = "FY26"

    # Step 2: Clean out old shared fundamentals if present
    if "fundamentals" in data:
        print("2. Removing top-level fundamentals (now per-program)...")
        del data["fundamentals"]

    # Step 3: Filter out bad transactions (price = 0)
    txs = data.get("transaktioner", [])
    original_count = len(txs)
    clean_txs = [t for t in txs if t.get("gns_kurs_gbp", 0) > 0]
    removed = original_count - len(clean_txs)
    print(f"3. Cleaning transactions...")
    print(f"   Original: {original_count}")
    print(f"   Removed (price=0 or bad): {removed}")
    print(f"   Kept: {len(clean_txs)}")

    # Step 4: Re-tag all with correct program
    print("\n4. Re-tagging transactions with programs...")
    tag_counts = {}
    for t in clean_txs:
        prog = assign_program(t["dato"], data["programmer"])
        t["program"] = prog
        tag_counts[prog] = tag_counts.get(prog, 0) + 1

    for prog, cnt in sorted(tag_counts.items()):
        print(f"   {prog}: {cnt} transactions")

    # Step 5: Sort and save
    clean_txs.sort(key=lambda t: t["dato"], reverse=True)
    data["transaktioner"] = clean_txs

    DATA_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n✓ Migration complete. {len(clean_txs)} transactions saved.")
    print(f"  Next: run 'python scripts/build_html.py' to regenerate dashboard")
    print(f"        or trigger backfill workflow to re-fetch removed transactions")


if __name__ == "__main__":
    main()
