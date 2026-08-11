"""
Repair share counts that were truncated at the thousands separator.

THE BUG
-------
For filings roughly between 21 Nov 2022 and 17 Feb 2023 the stored
`antal_aktier` is the first three digits of the real figure — 285 where the
RNS says 285,950. The number-matching pattern `(\\d[\\d,]+)` stops at
whatever separates the thousands group in that era's markup, so it captured
only the leading group.

Both the old ADVFN scraper and the Investegate scraper make the same
mistake, which is why re-running the backfill could not correct it: the
freshly-parsed value was identical to the stored one, so dedup treated it
as the same transaction and skipped it.

Left in place these rows contribute ~£0.0M each, which is the entire FY23
shortfall (£673M booked against a £1.0bn programme).

THE REPAIR
----------
Every buyback RNS also reports the shares remaining in issue after
cancellation, and those values parsed correctly — they are consistent and
monotonic. So the true share count is simply the fall in that figure
between one filing and the next.

A row is repaired only when BOTH hold:
  * the stored count is under 10,000 — implausible next to the surrounding
    filings, and
  * the reported share count fell by more than 10,000 across it

The second test is what makes this safe: 26 genuinely small filings exist
in the data (the smallest legitimate one is 5,469 shares), and for those
the reported count falls by exactly that small amount, so they are left
alone.

VALIDATION
----------
After repair FY23 totals 51,115,467 shares and £981.0M, against the
52.1m shares / £1.0bn the company reported — 98.1% on both, at an implied
average of 1,919p, which is exactly £1.0bn ÷ 52.1m. The residual 1.9% is
the few filings between the programme's start on 6 Oct 2022 and the
tracker's earliest record on 10 Oct 2022.

Rows where the count fell by more than the stored value but the stored
value is itself plausible are NOT repaired — those are genuinely missing
neighbouring filings and want a backfill, not arithmetic.

Run:  python scripts/repair_truncated_shares.py [--dry-run]
"""

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data.json"

IMPLAUSIBLE_BELOW = 10_000   # stored counts under this are suspect
MIN_DROP = 10_000            # ...but only if the reported count fell this far


def main():
    dry_run = "--dry-run" in sys.argv

    data = json.loads(DATA.read_text())
    tx = sorted(data["transaktioner"], key=lambda t: t["dato"])

    repairs = []
    prev = None
    for t in tx:
        ae = t.get("aktier_efter")
        if ae is None:
            prev = t
            continue
        if prev is not None:
            drop = prev["aktier_efter"] - ae
            if t["antal_aktier"] < IMPLAUSIBLE_BELOW and drop > MIN_DROP:
                exact = str(drop).startswith(str(t["antal_aktier"]))
                repairs.append((t, t["antal_aktier"], drop, exact))
        prev = t

    if not repairs:
        print("Nothing to repair.")
        return

    exact_n = sum(1 for r in repairs if r[3])
    print(f"Repairing {len(repairs)} rows "
          f"({exact_n} where the stored value is an exact prefix of the true "
          f"figure; {len(repairs) - exact_n} that also absorb a missing "
          f"neighbouring filing)\n")

    for t, old, drop, exact in repairs:
        mark = "" if exact else "   (absorbs a missing neighbour)"
        print(f"  {t['dato']}  {old:>7,} → {drop:>9,}"
              f"  £{old * t['gns_kurs_gbp'] / 100 / 1e6:>4.1f}M → "
              f"£{drop * t['gns_kurs_gbp'] / 100 / 1e6:>5.1f}M{mark}")
        if not dry_run:
            t["antal_aktier"] = drop
            t["beloeb_gbp_mio"] = round(drop * t["gns_kurs_gbp"] / 100 / 1e6, 1)

    if dry_run:
        print("\n--dry-run: data.json not modified")
        return

    data["transaktioner"] = sorted(tx, key=lambda t: t["dato"], reverse=True)
    DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\n✓ data.json written")


if __name__ == "__main__":
    main()
