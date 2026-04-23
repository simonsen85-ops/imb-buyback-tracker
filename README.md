# IMB Buyback Tracker v3

Forenklet dashboard for Imperial Brands (IMB.L) FY26 aktietilbagekøbsprogram.

**Live:** https://simonsen85-ops.github.io/imb-buyback-tracker/

## Fokus

1. **Tilbagekøbets progression** — hvor meget købt, hvor mange aktier annulleret
2. **EPS-værdi skabt over tid** — accretion og implicit værdiskabelse ved P/E 10×

## Arkitektur

```
imb-buyback-tracker/
├── data.json                          ← Kildedata (auto-opdateres)
├── index.html                         ← Genereret dashboard
├── scripts/
│   ├── scraper.py                     ← Thin orchestrator
│   ├── build_html.py                  ← Genererer index.html fra data.json
│   └── sources/                       ← Modulære datakilder
│       ├── __init__.py
│       ├── base.py                    ← Announcement dataclass + HTTP helpers
│       ├── investegate.py             ← PRIMÆR: ID-enumeration scraper
│       ├── lse_co_uk.py               ← FALLBACK: listing scraper
│       └── yahoo.py                   ← Stock price
└── .github/workflows/update.yml       ← Cron: hverdage 17:30 UTC
```

## Den centrale innovation: ID-enumeration

Tidligere version havde en scraper der kiggede på Investegate's "seneste 30 filings" — det forklarer hvorfor vi kun havde 18 transaktioner og missede masser af historik.

Den nye scraper udnytter at **Investegate's RNS-IDs er sekventielle**:
- Seneste filing per 22/4-2026: ID `9534918`
- 14/4-2026: `9529xxx`  
- 27/3-2026: `9505xxx`

Scraperen henter listen, finder højeste ID, og enumererer derefter **alle IDs mellem sidst kendte ID og seneste**. Non-existent IDs (andre RNS-typer for IMB) returnerer 404 som springes over.

**Fordele:**
- **Ingen data-tab** — fanger alt mellem sidste kørsel og nu
- **Selvhelende** — hvis Actions var ned i en uge, fylder næste kørsel automatisk gapet
- **Skalerer** — `max_lookback=200` fanger ~2-3 måneders backfill ved første kørsel

## Kildestrategien

Vi vurderede fem mulige kilder (primære → sekundære):

| Kilde | Status | Vurdering |
|-------|--------|-----------|
| LSE RNS direct feed | Kræver betalt kontrakt | Afvist |
| FCA NSM | Officielt arkiv | FAQ forbyder programmatisk adgang |
| Imperial Brands' egen side | IDX/Investis widget | JS-renderet, kræver headless browser |
| **Investegate** | **Sekundær aggregator af LSE RNS** | **Valgt som primær** |
| LSE.co.uk | Sekundær aggregator | Valgt som fallback |

Investegate og LSE.co.uk henter begge fra samme LSE RNS-feed, så data er identisk — de er blot to uafhængige distributionsveje ind til den samme primære datastrøm.

## Første kørsel

1. Deploy til GitHub (se nedenfor)
2. Kør workflow manuelt: **Actions → Update IMB Buyback Tracker → Run workflow**
3. Forventet output:
```
═══ IMB Buyback Tracker — Scraper v3 ═══
1. Yahoo Finance price...
   ✓ 2781.50 GBp (-0.59%)
2. Investegate RNS filings...
   Last known RNS ID: none (first run)
   Newest RNS ID on Investegate: 9534918
   First run: scraping 30 latest IDs from listing
     ✓ 9534918: 2026-04-22 | 275,000 @ 2734.82p = £7.5M
     ✓ 9534xxx: ...
3. Merging...
   ✓ 30 new transactions added
4. Regenerating index.html...
```
4. Efterfølgende kørsler: ID-enumeration fanger alle nye filings.

## Deploy (hvis du starter fra eksisterende repo)

**Option A — Codespaces (nemmest):**

1. Gå til `github.com/simonsen85-ops/imb-buyback-tracker`
2. **Code → Codespaces → Create codespace on main**
3. Drag `imb-tracker-v3.tar.gz` ind i filtræet
4. I terminal:
   ```bash
   # Slet gamle filer først
   rm -rf index.html scraper.py data.json .github/
   # Pak nye filer ud
   tar -xzf imb-tracker-v3.tar.gz --strip-components=1
   rm imb-tracker-v3.tar.gz
   git add -A
   git commit -m "v3: modular scraper with ID enumeration"
   git push
   ```
5. Luk Codespace. Workflow kører næste 17:30 UTC, eller trigger manuelt.

**Option B — Web UI:**

Den nye `scripts/`-mappe skal oprettes med `__init__.py`, `base.py`, `investegate.py`, `lse_co_uk.py`, `yahoo.py`. Det er nemmest via Codespace, men muligt via **Add file → Create new file** med sti `scripts/sources/__init__.py` osv.

## Antagelser i modellen

- **EPS-accretion:** EPS_ny = EPS_base × (aktier_start / aktier_nu). Base = FY26E konsensus £3,41
- **Fair value:** P/E 10× på FY26E EPS = 3.410p (mid-cycle tobacco-multipel)
- **Værdi skabt:** antal annullerede aktier × (fair_value − gns_købskurs)
- **ROIC på buyback:** FY25 FCF/aktie ÷ gns. købskurs (= implicit FCF-yield vi "køber")

Justér i `data.json` under `fundamentals` og `build_html.py` (fair_pe konstant).
