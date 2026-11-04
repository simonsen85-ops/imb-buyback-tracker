# IMB Buyback Tracker — notes for Claude

Share-buyback tracker for Imperial Brands PLC (IMB.L, London Stock Exchange).
Live: https://simonsen85-ops.github.io/imb-buyback-tracker/
Owner: Rasmus (Surface Capital). Talk to him in English, in plain language.
Dashboard text is Danish.

## Pipeline

`scripts/scraper.py` → `data.json` → `scripts/build_html.py` (called by the
scraper) → `index.html`. `index.html` is generated: never edit it by hand.

Workflow `.github/workflows/update.yml` runs weekdays 17:30 UTC and on manual
trigger: scraper → `check_integrity.py` (informational) → commit & push →
**fail the run if `meta.parse_failures` is non-empty**. GitHub Pages serves
`main` directly.

## Working from a Claude Code session

- **Commit code, not data.** Don't commit `data.json` or `index.html`: the
  Actions bot commits both to `main` every weekday, so a branch that touches
  them will conflict. Change data through code (migrations in `scraper.py`)
  and let the workflow regenerate both files.
- Work on a branch and open a pull request; Rasmus merges it on GitHub. After
  a merge, the dashboard only changes when the workflow next runs. Tell him to
  run it from the Actions tab if the change should show today.
- **Cloud sessions have a network allowlist.** This repo needs: www.investegate.co.uk, *.advfn.com, www.lse.co.uk, *.finance.yahoo.com.
  If a fetch fails from a cloud session, check the allowlist before concluding
  that the site or its format has changed.

## Before changing anything

- `git pull --rebase` first. The Actions bot commits `data.json` and
  `index.html` every weekday, so a local copy goes stale within a day.
- Standard library only (urllib); Python 3.12.
- Commands:
  - `python scripts/scraper.py` — daily run
  - `python scripts/scraper.py --backfill N [--from-page P]` — N listing pages
  - `python scripts/check_integrity.py` — data checks
- Test parser changes offline: save the filing HTML as a fixture and
  monkeypatch `sources.investegate.fetch_html`. Run the full scraper against
  the fixtures, then open `index.html` and check every programme view
  (including "ALL") for JavaScript errors before pushing.

## Sources (`scripts/sources/`)

- `investegate.py` — **primary**. Listing:
  `https://www.investegate.co.uk/company/IMB?page=N` (50 per page).
  Filing links: `/announcement/rns/imperial-brands--imb/transaction-in-own-shares/{id}`.
  The trailing `/` excludes `transaction-in-own-shares-treasury-share-transfer`
  (treasury transfers, not buybacks). Hrefs are absolute URLs.
  Investegate inserts an "AI Summary" that restates the figures above the real
  RNS text; `_clean_text()` strips it so regexes hit the filing, not the summary.
- `advfn.py` — fallback. ADVFN has returned HTTP 403 to non-browser clients
  since Aug 2026.
- `lse_co_uk.py` — last resort; its RNS body is JS-rendered and rarely parses.
- `yahoo.py` — price for IMB.L in GBp.
- Fallbacks run **only when the Investegate listing is unreachable**
  (`investegate.LAST_LISTING_OK`). "Nothing new" or "could not parse" must not
  trigger them: they only know the single-day format.

## Filing formats — the main failure point

1. **Single-day** (Morgan Stanley / Barclays, FY23–FY26): labelled fields
   "Date of transaction", "Number of shares repurchased",
   "Average price paid per share: GBp …".
2. **Weekly table** (HSBC, FY27 from 8 Oct 2026): prose total
   ("purchased for cancellation 977,781 of its ordinary shares … as detailed in
   the table below") and a table
   `Date of purchase | VWAP (GBp) | Number of Shares purchased | Lowest | Highest`,
   one row per trading day.

Both state "the remaining number of ordinary shares in issue will be N".

- `_is_multi_day()` routes between the parsers. The single-day parser must never
  see a weekly filing: its prose-date fallback grabs "announced on 8 October 2026".
- Weekly checks: rows must sum to the prose total; lowest ≤ VWAP ≤ highest on
  every row; plausibility bands on volume and price.
- A weekly filing becomes one `data.json` row per trade date, all sharing the
  filing's `rns_id`. `aktier_efter` for earlier days is rebuilt by adding back
  the later days' purchases.
- When the broker or format changes again: fetch one real filing, add a parser
  branch plus a fixture test. Don't loosen existing regexes to make it fit.
- **History:** in Oct 2026 the HSBC format failed the price regex and every
  filing was dropped silently. The tracker froze at 6 Oct for four weeks while
  every run stayed green. Hence `meta.parse_failures` and the failing workflow
  step. Never reintroduce a silent drop.
- Thousands separators can be a comma OR a space / non-breaking space in older
  filings. `(\d[\d,]+)` once truncated "285 950" to 285 and lost ~£327M of FY23
  volume.

## data.json

- `transaktioner[]`: `dato` (trade date, not filing date), `antal_aktier`,
  `gns_kurs_gbp` (pence), `beloeb_gbp_mio`, `aktier_efter`, `rns_id`, `program`.
  `rns_id` is the bare number for Investegate rows and `advfn_…` / `lse_…` for
  older sources — don't re-prefix existing rows.
- Dedup on `(rns_id, dato)` AND on the economic key
  `(dato, antal_aktier, round(gns_kurs_gbp, 2))`. The same filing carries
  different ids on different sources.
- `programmer{FYxx}` contains:
  - `total_gbp_mio`, `annonceret`, `start`, `frist`, optional `afsluttet`, `status`
  - `maegler_t1` / `maegler_t2`, `tranche_1` / `tranche_2`
  - `fundamentals{eps_adjusted_gbp, fcf_mio_gbp, aktier_ved_start, pct_af_kapital, fair_pe}`
- **`fundamentals` is required.** `build_html.py`'s JavaScript reads it, and a
  programme without it renders a blank page.
- `assign_program()`: a trade belongs to the programme whose
  `start..(afsluttet or frist)` contains it. If ranges overlap, the newest start
  wins (FY26's deadline overlaps FY27's start).
- New programme: add it through a migration in `scraper.py` (pattern:
  `FY27_PROGRAM` + `ensure_programs()`) and set `aktuel_program`. Don't
  hand-edit `data.json`; the bot commit will race you.
- `meta.parse_failures` is rewritten every run and clears itself once filings parse.
- **Open item:** FY27 `fundamentals` are placeholders (FY26E EPS 3.41, FCF
  2,700). Update them from the FY26 results (17 Nov 2026).

## Integrity

- `check_integrity.py` checks:
  - duplicates
  - provenance
  - share-count continuity: `aktier_efter` must fall by exactly the shares bought
  - programme totals
- Pre-existing continuity gaps — not regressions, don't chase them as new bugs:
  - 2023-04: 764,566 shares
  - 2025-07: 168,722 shares
  - 2025-09: 250,000 shares
- FY26 completed 6 Oct 2026 at £1,456M of £1,450M.

## Downstream use

`data.json` is a source for Rasmus's IMB valuation model (share count S₁, the
buyback execution record). Don't rename keys or change units without telling
him. Annual-report figures override tracker figures where both exist.

## Dashboard (`build_html.py`)

- Programme selector plus an "ALL" aggregate view.
- EPS accretion = EPS × shares_start / shares_now.
- Value created is measured against fair value (`fair_pe` × EPS).
- ROIC = FCF per share ÷ average buyback price.
- Chart.js comes from cdnjs; the data is embedded as JSON in the page.
