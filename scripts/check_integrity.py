"""
Data integrity checks for data.json.

The key test is SHARE-COUNT CONTINUITY. Every buyback RNS reports the shares
remaining in issue after cancellation, so between two consecutive filings the
reported count should fall by exactly the number of shares bought in the
later one. Where it falls by more, filings are missing between them — and
this catches gaps that nothing else does, because a missing filing leaves no
trace in the data itself.

This is how the FY23 gap was found: the share count fell 51,658,464 over
FY23 (matching the 52.1m reported by the company) while the stored rows only
summed to 36,150,401 — ~15.7m shares of filings absent, concentrated in
Nov 2022 – Feb 2023.

Note a genuine pause between tranches is NOT a gap: the share count simply
does not move, so no discrepancy appears. Only real missing filings show up.

Run:  python scripts/check_integrity.py
Exit code 1 if any check fails, so it can gate a workflow.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data.json"

# A missing filing is worth flagging above this many shares; below it the
# difference is usually a treasury-share transfer rather than a lost buyback.
SHARE_TOLERANCE = 1_000


def main():
    data = json.loads(DATA.read_text())
    tx = sorted(data["transaktioner"], key=lambda t: t["dato"])
    programs = data.get("programmer", {})
    failures = []

    print(f"Transactions: {len(tx)}   "
          f"range {tx[0]['dato']} → {tx[-1]['dato']}\n")

    # ── 1. Duplicates ────────────────────────────────────────────────────
    keys = [(t["dato"], t["antal_aktier"], round(t["gns_kurs_gbp"], 2))
            for t in tx]
    dupes = {k: c for k, c in Counter(keys).items() if c > 1}
    if dupes:
        failures.append(f"{len(dupes)} duplicate transaction(s)")
        print(f"✗ duplicates: {len(dupes)}")
        for k, c in list(dupes.items())[:10]:
            print(f"    {k[0]}  {k[1]:,} @ {k[2]}p  ×{c}")
    else:
        print("✓ no duplicates")

    # ── 2. Rows without provenance ───────────────────────────────────────
    no_id = [t for t in tx if not t.get("rns_id")]
    if no_id:
        failures.append(f"{len(no_id)} row(s) with no rns_id")
        print(f"✗ rows without rns_id: {len(no_id)}")
        for t in no_id[:10]:
            print(f"    {t['dato']}  {t['antal_aktier']:,} "
                  f"@ {t['gns_kurs_gbp']:.2f}p")
    else:
        print("✓ every row has an rns_id")

    # ── 3. Untagged rows ─────────────────────────────────────────────────
    unknown = [t for t in tx if t.get("program") in (None, "unknown")]
    if unknown:
        failures.append(f"{len(unknown)} row(s) not assigned to a programme")
        print(f"✗ untagged rows: {len(unknown)} "
              f"({unknown[0]['dato']} → {unknown[-1]['dato']})")
        print("    these are invisible in the dashboard — add or widen a "
              "programme in programmer{}")
    else:
        print("✓ every row is assigned to a programme")

    # ── 4. Share-count continuity ────────────────────────────────────────
    gaps = []
    prev = None
    for t in tx:
        ae = t.get("aktier_efter")
        if ae is None:
            continue
        if prev is not None:
            drop = prev["aktier_efter"] - ae
            diff = drop - t["antal_aktier"]
            if diff > SHARE_TOLERANCE:
                gaps.append((prev["dato"], t["dato"], diff))
        prev = t

    if gaps:
        total = sum(g[2] for g in gaps)
        failures.append(f"{total:,} shares unaccounted for across "
                        f"{len(gaps)} gap(s)")
        print(f"✗ share-count continuity: {len(gaps)} gap(s), "
              f"{total:,} shares unaccounted")
        by_month = defaultdict(int)
        for _, b, diff in gaps:
            by_month[b[:7]] += diff
        print("    by month:")
        for m in sorted(by_month):
            print(f"      {m}  {by_month[m]:>12,} shares")
        print("    → run the backfill deep enough to cover these months")
    else:
        print("✓ share-count continuity intact — no missing filings")

    # ── 5. Programme spend vs cap ────────────────────────────────────────
    print("\nProgramme totals:")
    for key in sorted(programs, key=lambda k: programs[k].get("start", "")):
        rows = [t for t in tx if t.get("program") == key]
        spend = sum(t["antal_aktier"] * t["gns_kurs_gbp"] / 100 / 1e6
                    for t in rows)
        cap = programs[key]["total_gbp_mio"]
        pct = spend / cap * 100
        active = programs[key].get("status") == "aktiv"
        if pct > 100.5:
            flag = "✗ OVER CAP"
            failures.append(f"{key} is {pct:.1f}% of its cap")
        elif pct < 95 and not active:
            flag = "✗ short — likely missing filings"
            failures.append(f"{key} completed at only {pct:.1f}% of its cap")
        else:
            flag = "✓"
        print(f"  {flag:34s} {key}  {len(rows):>3} filings  "
              f"£{spend:>7,.1f}M / £{cap:>5}M  = {pct:5.1f}%")

    print()
    if failures:
        print(f"FAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  • {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
