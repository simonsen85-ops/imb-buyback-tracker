# IMB Buyback Tracker — v8.3

## What changed in this release

### Source: ADVFN → Investegate
ADVFN added site-wide bot detection (HTTP 403 to any non-browser client), so
it can no longer be scraped. Investegate is now the primary source, reading
its paginated per-company listing (`/company/IMB?page=N`, 50 announcements per
page, ~32 pages of history). ADVFN remains in the fallback chain and will
resume automatically if the block is ever lifted.

The previous Investegate code blind-enumerated RNS IDs at roughly a 1% hit
rate, and its share-count regex was matching the site's "Summary by AI BETA"
block rather than the real RNS fields. Both are fixed.

### Programmes
FY23 (the first £1.0bn buyback, announced 6 Oct 2022) has been added — 189
filings that were previously tagged `unknown` and invisible in the dashboard.
FY24's window was corrected from 2023-11-15…2024-10-31 to 2023-10-06…2024-10-29,
and FY25's start from 2024-11-01 to 2024-10-30. Real tranche splits are now
recorded for every programme.

### Data
- 48 cross-source duplicates removed
- 11 prototype placeholder rows removed (£131.2M of phantom volume)
- `aktier_efter` is now monotonic across all 850 rows

### Dashboard
Tranche bars are clamped to their own cap, so a data error can no longer
render "£918M / £725M". Any genuine excess is surfaced as a separate
overspend note.

## Scripts

| Script | Purpose |
|---|---|
| `scraper.py` | Daily update (`python scripts/scraper.py`) or backfill (`--backfill N` pages) |
| `build_html.py` | Regenerate index.html from data.json |
| `check_integrity.py` | Duplicates, provenance, programme tagging, share-count continuity |
| `dedup_data.py` | Re-run dedup (`--dry-run`, `--drop-unverified`) |
| `add_fy23_program.py` | One-shot migration, already applied |

### Cross-source duplicate prevention (v8.2)
`merge_transactions` previously deduplicated on `rns_id` alone. The same
filing carries a different id depending on which source found it
(`advfn_98357343` vs Investegate's `9537970`), so whenever the primary
source changes the same transaction gets stored twice — that is how the 48
duplicates cleaned up in v8.1 arose, and a deep Investegate backfill would
have re-added ~800 more. Dedup is now on economic identity
(date + shares + average price) as well as id.

`--from-page N` lets a backfill start partway down the listing, so you can
target a known gap instead of re-walking pages whose filings are already
stored.

### Truncated share counts repaired (v8.3)
48 filings between Nov 2022 and Feb 2023 had `antal_aktier` stored as the
first three digits of the real figure — 285 where the RNS says 285,950. The
number pattern `(\d[\d,]+)` stopped at that era's thousands separator.
Both scrapers made the same mistake, so re-running the backfill could not
correct it: the freshly-parsed value matched the stored one and dedup
skipped it.

Those rows contributed ~£0.0M each, which was the whole FY23 shortfall.
`repair_truncated_shares.py` rebuilds them from the shares-in-issue figures
(which parsed correctly), and the pattern now accepts comma, space and
non-breaking-space separators, with regression tests for all three.

FY23 went from £673.1M (67.3%) to £981.0M (98.1%) against its £1.0bn
programme, at an implied average of 1,919p — exactly £1.0bn ÷ 52.1m shares
as reported by the company.

## Known outstanding gap

`check_integrity.py` reports ~1.18m shares unaccounted across just 3 gaps —
one filing each around Apr 2023, Jul 2025 and Sep 2025. These are genuinely
absent from the tracker rather than misparsed, so they need fetching:

    Actions → Backfill Historical → from_page: 1,  num_pages: 12   (2025 gaps)
    Actions → Backfill Historical → from_page: 16, num_pages: 4    (Apr 2023 gap)

All four programmes already pass their cap check, so this is a completeness
tidy-up rather than a correctness problem.
