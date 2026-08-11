# IMB Buyback Tracker — v8.2

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

## Known outstanding gap

`check_integrity.py` reports ~16.1m shares unaccounted, almost all in
Nov 2022 – Feb 2023 (FY23 sits at 67.3% of its £1.0bn cap). These are filings
ADVFN's old pagination never returned. Fix by running the backfill deep
enough to reach them:

    Actions → Backfill Historical → from_page: 18, num_pages: 15

Then re-run `check_integrity.py`; FY23 should approach 100%.
