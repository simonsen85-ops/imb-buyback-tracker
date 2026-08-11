"""
Add the FY23 programme and correct the FY24/FY25 boundaries + tranche detail.

WHY THIS WAS NEEDED
-------------------
1. FY23 (the first £1.0bn buyback, announced 6 Oct 2022) had no entry at all,
   so 217 real filings sat tagged "unknown" and were invisible in the
   dashboard — build_html.py only renders programmes present in programmer{}.

2. FY24's start was recorded as 2023-11-15. The programme actually began
   6 Oct 2023, so 28 filings (£127M) between those dates fell into the same
   "unknown" bucket.

3. FY24's end was recorded as 2024-10-31; tranche 2 actually ran to
   29 Oct 2024. With the corrected window FY24 totals £1,100.0M against its
   £1,100M cap — an exact match, which is what confirms both the boundary
   and the deduplicated data are right.

4. FY25's start was recorded as 2024-11-01; tranche 1 commenced 30 Oct 2024.

SOURCES
-------
FY23  £1.0bn announced 6 Oct 2022; repurchased 5.5% of capital (52.1m shares);
      completed 11 Sep 2023. HY23 statement: "In the period, we repurchased
      £500 million of shares of the £1.0 billion during FY23" → 2 × £500m.
      Adjusted EPS FY23 278.8p. Free cash flow £2.4bn (92% conversion).
FY24  £1.1bn announced 5 Oct 2023 for the period 6 Oct 2023 →
      end Sep 2024. Tranche 1 £550m via Morgan Stanley from 6 Oct 2023,
      completed 11 Mar 2024; tranche 2 £550m from 11 Mar 2024 to 29 Oct 2024.
FY25  £1.25bn announced 8 Oct 2024 for the period to 29 Oct 2025.
      Tranche 1 £625m via Morgan Stanley from 30 Oct 2024 (completed);
      tranche 2 £625m from 1 May 2025.
FY26  £1.45bn announced 7 Oct 2025 to 28 Oct 2026. Tranche 1 £725m via
      Morgan Stanley from 30 Oct 2025, completed. Tranche 2 £725m via
      Barclays announced 13 Apr 2026.

`aktier_ved_start` for FY23 is derived from the tracker's own data: the
first filing (2022-10-10) reports 949,959,522 shares remaining after buying
221,569, implying 950,181,091 before it.

Brokers are only recorded where the RNS names them; tranches whose broker
was not verified are left without one rather than guessed.

Run:  python scripts/add_fy23_program.py [--dry-run]
"""

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data.json"

FY23 = {
    "navn": "FY23 Aktietilbagekøbsprogram",
    "total_gbp_mio": 1000,
    "annonceret": "2022-10-06",
    "start": "2022-10-06",
    "frist": "2023-09-11",
    "status": "fuldført",
    "maegler_t1": "Morgan Stanley",
    "tranche_1": {
        "beloeb_mio": 500,
        "start": "2022-10-06",
        "slut": "2023-03-10",
        "status": "fuldført",
    },
    "tranche_2": {
        "beloeb_mio": 500,
        "start": "2023-04-11",
        "slut": "2023-09-11",
        "status": "fuldført",
    },
    "fundamentals": {
        "eps_adjusted_gbp": 2.788,
        "eps_source": "FY23 rapporteret (278,8p)",
        "fcf_mio_gbp": 2400,
        "aktier_ved_start": 950181091,
        "pct_af_kapital": 5.5,
        "fair_pe": 10,
    },
}

# Corrections to existing programmes: (field path, new value)
CORRECTIONS = {
    "FY24": {
        "start": "2023-10-06",
        "frist": "2024-10-29",
        "annonceret": "2023-10-05",
        "tranche_1": {
            "beloeb_mio": 550,
            "start": "2023-10-06",
            "slut": "2024-03-11",
            "status": "fuldført",
        },
        "tranche_2": {
            "beloeb_mio": 550,
            "start": "2024-03-11",
            "slut": "2024-10-29",
            "status": "fuldført",
        },
    },
    "FY25": {
        "start": "2024-10-30",
        "frist": "2025-10-29",
        "annonceret": "2024-10-08",
        "tranche_1": {
            "beloeb_mio": 625,
            "start": "2024-10-30",
            "slut": "2025-04-29",
            "status": "fuldført",
        },
        "tranche_2": {
            "beloeb_mio": 625,
            "start": "2025-05-01",
            "slut": "2025-10-29",
            "status": "fuldført",
        },
    },
    "FY26": {
        "tranche_2": {
            "beloeb_mio": 725,
            "start": "2026-04-13",
            "slut": "2026-10-28",
            "status": "aktiv",
        },
    },
}


def assign_program(dato: str, programs: dict) -> str:
    for key, p in programs.items():
        if p.get("start", "") <= dato <= p.get("frist", "9999-12-31"):
            return key
    return "unknown"


def main():
    dry_run = "--dry-run" in sys.argv
    data = json.loads(DATA.read_text())
    programs = data["programmer"]

    if "FY23" not in programs:
        programs["FY23"] = FY23
        print("+ added FY23 programme")
    else:
        print("· FY23 already present — leaving as is")

    for key, fields in CORRECTIONS.items():
        if key not in programs:
            continue
        for field, value in fields.items():
            old = programs[key].get(field)
            if old != value:
                programs[key][field] = value
                if isinstance(value, dict):
                    print(f"~ {key}.{field} updated")
                else:
                    print(f"~ {key}.{field}: {old} → {value}")

    # Re-tag every transaction against the corrected boundaries
    moved = {}
    for t in data["transaktioner"]:
        old = t.get("program")
        new = assign_program(t["dato"], programs)
        if old != new:
            moved[(old, new)] = moved.get((old, new), 0) + 1
            t["program"] = new

    print("\nRe-tagged transactions:")
    for (old, new), n in sorted(moved.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>4} × {old} → {new}")
    if not moved:
        print("   (none)")

    # Report each programme against its cap
    print("\nProgramme totals vs cap:")
    for key in sorted(programs, key=lambda k: programs[k]["start"]):
        rows = [t for t in data["transaktioner"] if t.get("program") == key]
        spend = sum(t["antal_aktier"] * t["gns_kurs_gbp"] / 100 / 1e6
                    for t in rows)
        cap = programs[key]["total_gbp_mio"]
        print(f"   {key}  {len(rows):>3} filings  £{spend:>7,.1f}M / "
              f"£{cap:>5}M  = {spend / cap * 100:5.1f}%")

    unknown = [t for t in data["transaktioner"]
               if t.get("program") == "unknown"]
    print(f"\n   still 'unknown': {len(unknown)}")

    if dry_run:
        print("\n--dry-run: data.json not modified")
        return

    DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print("\n✓ data.json written")


if __name__ == "__main__":
    main()
