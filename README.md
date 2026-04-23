# IMB Buyback Tracker

Forenklet dashboard for Imperial Brands (IMB.L) FY26 aktietilbagekøbsprogram.

**Live:** https://simonsen85-ops.github.io/imb-buyback-tracker/

## Fokus

Tracker to ting:
1. **Tilbagekøbets progression** — hvor meget købt, hvor mange aktier annulleret
2. **EPS-værdi skabt over tid** — accretion og implicit værdiskabelse

## Arkitektur

```
imb-buyback-tracker/
├── data.json                  ← kildedata (auto-opdateres af scraper)
├── index.html                 ← genereret dashboard (auto-regenereret)
├── scripts/
│   ├── scraper.py             ← Yahoo kurs + Investegate RNS scraping
│   └── build_html.py          ← Genererer index.html fra data.json
└── .github/workflows/update.yml ← Cron: hverdage 17:30 UTC
```

Samme build-pattern som FED-trackeren: **al beregning sker server-side i `build_html.py`**.
Det betyder at transaktionstabel, KPI'er, charts og value-creation-flow alle er bagt ind
i den statiske HTML — ingen CORS-problemer, ingen runtime-afhængigheder.

## Lokalt kør

```bash
# Tjek at data.json og build_html.py er opdaterede:
python scripts/build_html.py

# Fuld scraper-kørsel (henter kurs + nye RNS + regenererer HTML):
python scripts/scraper.py
```

## Deploy første gang

1. Opret repo `imb-buyback-tracker` på GitHub (public)
2. Upload alle filer (behold mappestruktur)
3. **Settings → Pages** → Source: `main` / `/` (root) → Save
4. **Settings → Actions → General → Workflow permissions** → Read and write → Save
5. **Actions → Update IMB Buyback Tracker → Run workflow** for at teste

## Antagelser i modellen

- **EPS-accretion:** EPS_ny = EPS_base × (aktier_start / aktier_nu). Base = FY26E konsensus £3.41
- **Fair value:** P/E 15× på FY26E EPS = 5.115p (konservativt tobacco-multipel)
- **Værdi skabt:** antal annullerede aktier × (fair_value − gns_købskurs)
- **ROIC på buyback:** FY25 FCF/aktie ÷ gns. købskurs (= implicit FCF-yield vi "køber")

Justér P/E-antagelsen og FCF-input i `data.json` under `fundamentals`.
