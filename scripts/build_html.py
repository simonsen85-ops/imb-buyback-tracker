#!/usr/bin/env python3
"""
IMB Buyback Tracker — HTML Generator
Genererer index.html fra data.json med alle beregninger bagt ind server-side.
Samme mønster som FED-trackeren.

Kør lokalt: python scripts/build_html.py
Kører også automatisk i GitHub Actions efter scraper.py.
"""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════
# BEREGNINGER — alt computeres server-side, bages ind i HTML
# ═══════════════════════════════════════════════════════════════

def compute_metrics(data: dict) -> dict:
    """Beregn alle key metrics for dashboardet."""
    tx = sorted(data["transaktioner"], key=lambda t: t["dato"])
    prog = data["program"]
    fund = data["fundamentals"]

    # Grundtal
    total_shares = sum(t["antal_aktier"] for t in tx)
    total_gbp_mio = sum(t["beloeb_gbp_mio"] for t in tx)
    # Gns. købskurs i GBp (pence): total £M × 100 (→ pence-mio) × 1e6 (→ pence) / antal aktier
    avg_price = (total_gbp_mio * 1e6 * 100) / total_shares if total_shares else 0  # GBp

    # Program progress
    pct_program = (total_gbp_mio / prog["total_gbp_mio"]) * 100

    # Shares outstanding
    shares_start = fund["aktier_ved_program_start"]
    shares_now = shares_start - total_shares
    pct_reduction = (total_shares / shares_start) * 100

    # EPS-accretion (det centrale tal for brugeren)
    # Modellen: EPS_ny = EPS * (shares_start / shares_now)
    eps_base = fund["fy26e_eps_consensus_gbp"]
    eps_new = eps_base * (shares_start / shares_now)
    eps_accretion_pct = ((eps_new / eps_base) - 1) * 100
    eps_uplift_gbp = eps_new - eps_base

    # Værdi skabt pr. aktie (i pence) = EPS-løft × 10 × P/E
    # Men vi bruger den enkle: EPS-løft × antal aktier = total værdi skabt
    # Værdi skabt i £M = EPS_uplift_pence × shares_now / 1e6 / 100
    # Nej — værdi skabt = aktier annulleret × fair value pr. aktie
    # Fair value proxy: FCF pr. aktie × implicit multiple
    # Simpelt mål: købspris → hvis aktierne havde P/E = 15, er værdi skabt = antal × (15×EPS×100 - avg_price)
    # Værdi skabt pr. aktie = (fair value − købspris) × antal aktier
    # Fair value = P/E × EPS. For tobacco er 10× et realistisk mid-cycle multipel.
    # Aktuel P/E (ved £3.41 EPS og 2.781p): ~8.2× — markedet priser betydelig risiko ind.
    fair_pe = 10  # konservativt tobacco mid-cycle multipel
    fair_price_pence = (eps_base * 100) * fair_pe  # £3.41 × 100 = 341p × 10 = 3.410p
    value_created_mio = total_shares * (fair_price_pence - avg_price) / 100 / 1e6

    # ROIC på buyback = FCF yield på købte aktier
    # Annual FCF per share = FY25 FCF / shares_now
    fcf_per_share = (fund["fy25_fcf_mio_gbp"] * 1e6 * 100) / shares_now  # pence
    roic_on_buyback = (fcf_per_share / avg_price) * 100 if avg_price else 0

    # Live kurs analyse
    live_price = data["kurs"]["price"]
    price_vs_avg = ((live_price / avg_price) - 1) * 100 if avg_price else 0

    # Tranche progress
    t1_spent = min(total_gbp_mio, prog["tranche_1"]["beloeb_mio"])
    t2_spent = max(0, total_gbp_mio - prog["tranche_1"]["beloeb_mio"])
    t1_pct = (t1_spent / prog["tranche_1"]["beloeb_mio"]) * 100
    t2_pct = (t2_spent / prog["tranche_2"]["beloeb_mio"]) * 100

    return {
        "total_shares": total_shares,
        "total_gbp_mio": total_gbp_mio,
        "avg_price_pence": avg_price,
        "pct_program": pct_program,
        "shares_start": shares_start,
        "shares_now": shares_now,
        "pct_reduction": pct_reduction,
        "eps_base": eps_base,
        "eps_new": eps_new,
        "eps_accretion_pct": eps_accretion_pct,
        "eps_uplift_gbp": eps_uplift_gbp,
        "value_created_mio": value_created_mio,
        "fair_price_pence": fair_price_pence,
        "fair_pe": fair_pe,
        "roic_on_buyback": roic_on_buyback,
        "live_price": live_price,
        "price_vs_avg": price_vs_avg,
        "t1_spent": t1_spent,
        "t2_spent": t2_spent,
        "t1_pct": t1_pct,
        "t2_pct": t2_pct,
        "tx_count": len(tx),
    }


def build_chart_series(data: dict, metrics: dict) -> dict:
    """Forbered tidsserier til de to charts."""
    tx = sorted(data["transaktioner"], key=lambda t: t["dato"])
    fund = data["fundamentals"]

    labels = []
    price_series = []  # købskurs ved hver transaktion
    fair_value_series = []  # konstant fair value
    cumulative_eps_accretion = []  # % akkumuleret EPS-løft

    cum_shares = 0
    shares_start = fund["aktier_ved_program_start"]

    for t in tx:
        labels.append(t["dato"])
        price_series.append(t["gns_kurs_gbp"])
        fair_value_series.append(metrics["fair_price_pence"])

        cum_shares += t["antal_aktier"]
        shares_after = shares_start - cum_shares
        eps_factor = shares_start / shares_after
        cum_accretion = (eps_factor - 1) * 100
        cumulative_eps_accretion.append(cum_accretion)

    return {
        "labels": labels,
        "price": price_series,
        "fair_value": fair_value_series,
        "eps_accretion": cumulative_eps_accretion,
    }


# ═══════════════════════════════════════════════════════════════
# HTML RENDERING
# ═══════════════════════════════════════════════════════════════

def fmt_int(n: int) -> str:
    """1.234.567"""
    return f"{n:,}".replace(",", ".")


def fmt_gbp_mio(n: float) -> str:
    """£1.234M"""
    return f"£{n:,.0f}M".replace(",", ".")


def fmt_pct(n: float, decimals: int = 2) -> str:
    return f"{n:+.{decimals}f}%" if n != 0 else f"{n:.{decimals}f}%"


def fmt_pence(n: float) -> str:
    """3.050,44p (dansk format)"""
    return f"{n:,.2f}p".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_date_da(iso: str) -> str:
    """2026-03-27 → 27. mar 2026"""
    d = datetime.fromisoformat(iso)
    months = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
    return f"{d.day}. {months[d.month - 1]} {d.year}"


def render_html(data: dict) -> str:
    m = compute_metrics(data)
    series = build_chart_series(data, m)
    prog = data["program"]
    kurs = data["kurs"]

    # Tabel-rows (nyeste først)
    tx_sorted = sorted(data["transaktioner"], key=lambda t: t["dato"], reverse=True)
    tx_rows = []
    for i, t in enumerate(tx_sorted):
        tx_rows.append(
            f'<tr>'
            f'<td class="n">{len(tx_sorted) - i}</td>'
            f'<td>{fmt_date_da(t["dato"])}</td>'
            f'<td class="num">{fmt_int(t["antal_aktier"])}</td>'
            f'<td class="num">{fmt_pence(t["gns_kurs_gbp"])}</td>'
            f'<td class="num">{fmt_gbp_mio(t["beloeb_gbp_mio"])}</td>'
            f'<td class="num">{fmt_int(t["aktier_efter"])}</td>'
            f'</tr>'
        )
    tx_table_html = "\n".join(tx_rows)

    # Live price badge klasser
    change_class = "up" if kurs["change"] >= 0 else "dn"
    change_sign = "+" if kurs["change"] >= 0 else ""

    # Price vs avg badge
    vs_avg_class = "dn" if m["price_vs_avg"] < 0 else "up"
    vs_avg_label = "UNDER" if m["price_vs_avg"] < 0 else "OVER"

    html = f"""<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IMB · Buyback Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{{
  /* Tre-farve system — samme som FED */
  --bg1:#0a0e17;
  --bg2:#111827;
  --t1:#e8ecf2;        /* hvid — primære tal */
  --t2:#b0bac9;        /* lys grå — labels */
  --t3:#8b99ad;        /* grå — sekundært */
  --t4:#3d4a5c;        /* mørk grå — borders */
  --g1:#10b981;        /* grøn — værdiskabelse */
  --g2:#34d399;
  --g3:#6ee7b7;
  --g4:rgba(16,185,129,0.15);
  --red:#ef4444;
  --amber:#f59e0b;
  --mono:'JetBrains Mono',monospace;
  --sans:'Outfit',sans-serif;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:var(--sans);background:var(--bg1);color:var(--t1);min-height:100vh;font-weight:400;-webkit-font-smoothing:antialiased}}

.c{{max-width:1360px;margin:0 auto;padding:0 24px 48px}}

/* HEADER */
.hdr{{display:flex;justify-content:space-between;align-items:center;padding:20px 24px;border-bottom:1px solid var(--t4);max-width:1360px;margin:0 auto}}
.hdr-l{{display:flex;align-items:center;gap:16px}}
.ticker{{font-family:var(--mono);font-size:26px;font-weight:700;color:var(--t1);letter-spacing:-0.5px}}
.badge{{display:inline-block;padding:2px 8px;background:var(--t4);color:var(--t2);font-size:10px;font-weight:600;letter-spacing:1px;border-radius:3px;margin-left:6px;vertical-align:middle}}
.sub{{font-family:var(--sans);font-size:12px;color:var(--t3);margin-top:2px;letter-spacing:0.3px}}
.hdr-r{{text-align:right}}
.price{{font-family:var(--mono);font-size:24px;font-weight:700;color:var(--t1)}}
.price .cur{{font-size:12px;color:var(--t3);font-weight:400;margin-left:4px}}
.chg{{font-family:var(--mono);font-size:13px;margin-top:2px}}
.chg.up{{color:var(--g1)}}
.chg.dn{{color:var(--red)}}
.up-ts{{font-size:11px;color:var(--t4);margin-top:4px}}

/* PROGRAM BANNER */
.prog{{background:var(--bg2);border:1px solid var(--t4);border-radius:4px;padding:18px 22px;margin:20px 0 16px;display:flex;justify-content:space-between;align-items:center}}
.prog-t{{font-size:13px;font-weight:600;color:var(--t1);letter-spacing:0.3px}}
.prog-d{{font-size:11px;color:var(--t3);margin-top:4px}}
.prog-d strong{{color:var(--t2);font-weight:600}}
.status{{display:flex;align-items:center;gap:8px;padding:6px 12px;background:var(--g4);border:1px solid var(--g1);border-radius:3px}}
.dot{{width:6px;height:6px;background:var(--g1);border-radius:50%;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.4}}}}
.status-t{{font-family:var(--mono);font-size:10px;font-weight:600;color:var(--g1);letter-spacing:1px}}

/* TRANCHE PROGRESS — erstatter historik-kort */
.tranches{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--t4);border:1px solid var(--t4);border-radius:4px;margin-bottom:16px;overflow:hidden}}
.tr{{background:var(--bg2);padding:14px 18px}}
.tr-h{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}}
.tr-n{{font-size:11px;color:var(--t3);letter-spacing:0.5px;text-transform:uppercase}}
.tr-amt{{font-family:var(--mono);font-size:15px;font-weight:700;color:var(--t1)}}
.tr-bar{{height:4px;background:var(--bg1);border-radius:2px;overflow:hidden;margin:6px 0 4px}}
.tr-bar-f{{height:100%;background:var(--g1);border-radius:2px;transition:width 0.6s ease}}
.tr-meta{{font-size:10px;color:var(--t3);font-family:var(--mono)}}

/* KPI GRID — 4 kerne-tal */
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--t4);border:1px solid var(--t4);border-radius:4px;margin-bottom:16px;overflow:hidden}}
.kpi{{background:var(--bg2);padding:14px 18px}}
.kpi-l{{font-size:10px;color:var(--t3);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;font-weight:500}}
.kpi-v{{font-family:var(--mono);font-size:22px;font-weight:700;color:var(--t1);letter-spacing:-0.3px}}
.kpi-v.grn{{color:var(--g1)}}
.kpi-s{{font-size:11px;color:var(--t3);margin-top:4px;font-family:var(--mono)}}

/* VALUE CREATION FLOW — det centrale narrativ */
.vc-h{{font-size:10px;font-weight:600;color:var(--t3);letter-spacing:1.5px;text-transform:uppercase;margin:8px 0 10px}}
.vc{{display:grid;grid-template-columns:1fr auto 1fr auto 1fr auto 1fr;gap:8px;align-items:stretch;margin-bottom:16px}}
.vc-b{{background:var(--bg2);border:1px solid var(--t4);border-radius:4px;padding:14px 16px;display:flex;flex-direction:column;justify-content:center}}
.vc-b.end{{border-color:var(--g1);background:linear-gradient(135deg,var(--bg2) 0%,var(--g4) 100%)}}
.vc-bl{{font-size:9px;color:var(--t3);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;font-weight:600}}
.vc-bv{{font-family:var(--mono);font-size:18px;font-weight:700;color:var(--t1);line-height:1.1}}
.vc-b.end .vc-bv{{color:var(--g1)}}
.vc-bs{{font-size:10px;color:var(--t3);margin-top:4px;font-family:var(--mono)}}
.vc-ar{{display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:16px;color:var(--t4);font-weight:300}}

/* CHARTS — kun to */
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}}
.ch{{background:var(--bg2);border:1px solid var(--t4);border-radius:4px;padding:14px 16px}}
.ch-h{{font-size:10px;font-weight:600;color:var(--t3);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px}}
.ch-h span{{color:var(--t4);font-weight:400;margin-left:8px}}
.ch-w{{position:relative;height:240px}}

/* TABLE */
.tc{{background:var(--bg2);border:1px solid var(--t4);border-radius:4px;padding:14px 16px;margin-bottom:16px;overflow:hidden}}
.ts{{overflow-x:auto;max-width:100%}}
.t-h{{font-size:10px;font-weight:600;color:var(--t3);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px}}
table{{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px}}
th{{text-align:left;padding:8px 10px;font-size:10px;color:var(--t3);letter-spacing:0.5px;text-transform:uppercase;border-bottom:1px solid var(--t4);font-weight:500;white-space:nowrap}}
th.num{{text-align:right}}
td{{padding:8px 10px;border-bottom:1px solid rgba(61,74,92,0.35);color:var(--t1);white-space:nowrap}}
td.num{{text-align:right}}
td.n{{color:var(--t3);font-size:10px;text-align:center;width:28px}}
tr:hover td{{background:rgba(16,185,129,0.03)}}

/* ASSUMPTION BOX */
.as{{background:var(--bg2);border:1px dashed var(--t4);border-radius:4px;padding:10px 14px;margin-bottom:16px;font-size:11px;color:var(--t3);font-family:var(--mono);line-height:1.7}}
.as strong{{color:var(--t2);font-weight:600}}

/* FOOTER */
.foot{{text-align:center;padding:20px;border-top:1px solid var(--t4);font-size:11px;color:var(--t4);margin-top:24px}}
.foot a{{color:var(--t3);text-decoration:none}}

@media (max-width:900px){{
  .charts,.tranches{{grid-template-columns:1fr}}
  .kpis{{grid-template-columns:repeat(2,1fr)}}
  .vc{{grid-template-columns:1fr;gap:6px}}
  .vc-ar{{transform:rotate(90deg);padding:4px 0}}
  .ticker{{font-size:22px}}
  .price{{font-size:20px}}
}}
@media (max-width:600px){{
  .kpis{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>

<header class="hdr">
  <div class="hdr-l">
    <div>
      <div class="ticker">IMB<span class="badge">LSE</span></div>
      <div class="sub">Imperial Brands PLC · Buyback Tracker</div>
    </div>
  </div>
  <div class="hdr-r">
    <div class="price" id="live-price">{kurs["price"]:,.2f}<span class="cur">GBp</span></div>
    <div class="chg {change_class}" id="live-chg">{change_sign}{kurs["change"]:.2f} ({change_sign}{kurs["change_pct"]:.2f}%)</div>
    <div class="up-ts" id="live-ts">Opdateret {fmt_date_da(data["meta"]["last_updated"][:10])}</div>
  </div>
</header>

<div class="c">

  <!-- PROGRAM BANNER -->
  <div class="prog">
    <div>
      <div class="prog-t">{prog["navn"]}</div>
      <div class="prog-d">Annonceret <strong>{fmt_date_da(prog["annonceret"])}</strong> · Frist <strong>{fmt_date_da(prog["frist"])}</strong> · Mægler T1: <strong>{prog["maegler_t1"]}</strong></div>
    </div>
    <div class="status"><div class="dot"></div><span class="status-t">AKTIV</span></div>
  </div>

  <!-- TRANCHE PROGRESS -->
  <div class="tranches">
    <div class="tr">
      <div class="tr-h">
        <span class="tr-n">Tranche 1 · Morgan Stanley</span>
        <span class="tr-amt">£{m["t1_spent"]:.0f}M / £{prog["tranche_1"]["beloeb_mio"]}M</span>
      </div>
      <div class="tr-bar"><div class="tr-bar-f" style="width:{min(m["t1_pct"],100):.1f}%"></div></div>
      <div class="tr-meta">{fmt_date_da(prog["tranche_1"]["start"])} → {fmt_date_da(prog["tranche_1"]["slut"])} · {m["t1_pct"]:.1f}% fuldført</div>
    </div>
    <div class="tr">
      <div class="tr-h">
        <span class="tr-n">Tranche 2</span>
        <span class="tr-amt">£{m["t2_spent"]:.0f}M / £{prog["tranche_2"]["beloeb_mio"]}M</span>
      </div>
      <div class="tr-bar"><div class="tr-bar-f" style="width:{min(m["t2_pct"],100):.1f}%"></div></div>
      <div class="tr-meta">Start {fmt_date_da(prog["tranche_2"]["start"])} · Slut {fmt_date_da(prog["tranche_2"]["slut"])}</div>
    </div>
  </div>

  <!-- KPI GRID — 4 kerne-tal -->
  <div class="kpis">
    <div class="kpi">
      <div class="kpi-l">Tilbagekøbt</div>
      <div class="kpi-v grn">£{m["total_gbp_mio"]:.0f}M</div>
      <div class="kpi-s">{m["pct_program"]:.1f}% af £{prog["total_gbp_mio"]}M program</div>
    </div>
    <div class="kpi">
      <div class="kpi-l">Aktier annulleret</div>
      <div class="kpi-v">{m["total_shares"]/1e6:.2f}M</div>
      <div class="kpi-s">{m["pct_reduction"]:.2f}% af kapital</div>
    </div>
    <div class="kpi">
      <div class="kpi-l">EPS-accretion</div>
      <div class="kpi-v grn">+{m["eps_accretion_pct"]:.2f}%</div>
      <div class="kpi-s">£{m["eps_uplift_gbp"]:.3f}/aktie</div>
    </div>
    <div class="kpi">
      <div class="kpi-l">ROIC på buyback</div>
      <div class="kpi-v grn">{m["roic_on_buyback"]:.1f}%</div>
      <div class="kpi-s">FCF-yield på købte aktier</div>
    </div>
  </div>

  <!-- VALUE CREATION FLOW -->
  <div class="vc-h">Værdiskabelse · sekventielt flow</div>
  <div class="vc">
    <div class="vc-b">
      <div class="vc-bl">Købt tilbage</div>
      <div class="vc-bv">£{m["total_gbp_mio"]:.0f}M</div>
      <div class="vc-bs">gns. {fmt_pence(m["avg_price_pence"])}</div>
    </div>
    <div class="vc-ar">→</div>
    <div class="vc-b">
      <div class="vc-bl">Aktier væk</div>
      <div class="vc-bv">{m["total_shares"]/1e6:.2f}M</div>
      <div class="vc-bs">{m["pct_reduction"]:.2f}% af kapital</div>
    </div>
    <div class="vc-ar">→</div>
    <div class="vc-b">
      <div class="vc-bl">EPS-løft</div>
      <div class="vc-bv">+{m["eps_accretion_pct"]:.2f}%</div>
      <div class="vc-bs">fra £{m["eps_base"]:.2f} til £{m["eps_new"]:.3f}</div>
    </div>
    <div class="vc-ar">→</div>
    <div class="vc-b end">
      <div class="vc-bl">Værdi skabt</div>
      <div class="vc-bv">£{m["value_created_mio"]:.0f}M</div>
      <div class="vc-bs">ved P/E {m["fair_pe"]}x på EPS</div>
    </div>
  </div>

  <!-- CHARTS — kun to -->
  <div class="charts">
    <div class="ch">
      <div class="ch-h">Købskurs vs. Fair Value<span>P/E {m["fair_pe"]}× på FY26E EPS</span></div>
      <div class="ch-w"><canvas id="priceChart"></canvas></div>
    </div>
    <div class="ch">
      <div class="ch-h">Akkumuleret EPS-accretion<span>% løft over programperioden</span></div>
      <div class="ch-w"><canvas id="epsChart"></canvas></div>
    </div>
  </div>

  <!-- TRANSACTION TABLE -->
  <div class="tc">
    <div class="t-h">Transaktioner · {m["tx_count"]} RNS-filings (nyeste først)</div>
    <div class="ts">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Dato</th>
            <th class="num">Antal aktier</th>
            <th class="num">Gns. kurs</th>
            <th class="num">Beløb</th>
            <th class="num">Aktier efter</th>
          </tr>
        </thead>
        <tbody>{tx_table_html}</tbody>
      </table>
    </div>
  </div>

  <!-- ASSUMPTION BOX -->
  <div class="as">
    <strong>Antagelser:</strong> EPS-accretion beregnet som EPS_ny / EPS_base hvor EPS_base = FY26E konsensus £{m["eps_base"]:.2f}.
    Værdi skabt = antal annullerede aktier × (fair value − gns. købskurs), fair value = {m["fair_pe"]}× FY26E EPS = {fmt_pence(m["fair_price_pence"])}.
    ROIC på buyback = FY25 FCF/aktie ÷ gns. købskurs = implicit FCF-yield vi "køber".
    <strong>Aktier ved programstart:</strong> {fmt_int(m["shares_start"])}. <strong>Aktier nu:</strong> {fmt_int(m["shares_now"])}.
  </div>

</div>

<div class="foot">
  Sidst opdateret {data["meta"]["last_updated"]} · Data: {data["meta"]["data_kilde"]} ·
  <a href="https://github.com/simonsen85-ops/imb-buyback-tracker">GitHub</a>
</div>

<script>
const series = {json.dumps(series)};

Chart.defaults.font.family = "'JetBrains Mono', monospace";
Chart.defaults.font.size = 10;
Chart.defaults.color = '#8b99ad';
Chart.defaults.borderColor = '#3d4a5c';

const gridCfg = {{ color: 'rgba(61,74,92,0.3)', drawBorder: false }};
const ticksCfg = {{ color: '#8b99ad', font: {{ size: 9 }} }};

// Chart 1 — Købskurs vs Fair Value
new Chart(document.getElementById('priceChart'), {{
  type: 'line',
  data: {{
    labels: series.labels,
    datasets: [
      {{
        label: 'Købskurs (GBp)',
        data: series.price,
        borderColor: '#e8ecf2',
        backgroundColor: 'rgba(232,236,242,0.05)',
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: '#e8ecf2',
        tension: 0.2,
        fill: false
      }},
      {{
        label: 'Fair Value (P/E {m["fair_pe"]}×)',
        data: series.fair_value,
        borderColor: '#10b981',
        borderWidth: 1.5,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false
      }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ display: true, position: 'bottom', labels: {{ color: '#b0bac9', boxWidth: 12, boxHeight: 2, font: {{ size: 10 }} }} }},
      tooltip: {{
        backgroundColor: '#0a0e17',
        titleColor: '#e8ecf2',
        bodyColor: '#b0bac9',
        borderColor: '#3d4a5c',
        borderWidth: 1,
        callbacks: {{
          label: ctx => `${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(2)}}p`
        }}
      }}
    }},
    scales: {{
      x: {{ grid: gridCfg, ticks: {{ ...ticksCfg, maxRotation: 0, maxTicksLimit: 8 }} }},
      y: {{ grid: gridCfg, ticks: {{ ...ticksCfg, callback: v => v.toFixed(0) + 'p' }} }}
    }}
  }}
}});

// Chart 2 — Akkumuleret EPS-accretion
new Chart(document.getElementById('epsChart'), {{
  type: 'line',
  data: {{
    labels: series.labels,
    datasets: [{{
      label: 'Kumulativ EPS-accretion (%)',
      data: series.eps_accretion,
      borderColor: '#10b981',
      backgroundColor: 'rgba(16,185,129,0.12)',
      borderWidth: 2,
      pointRadius: 3,
      pointBackgroundColor: '#10b981',
      tension: 0.2,
      fill: true
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        backgroundColor: '#0a0e17',
        titleColor: '#e8ecf2',
        bodyColor: '#b0bac9',
        borderColor: '#3d4a5c',
        borderWidth: 1,
        callbacks: {{
          label: ctx => `+${{ctx.parsed.y.toFixed(3)}}% EPS-løft`
        }}
      }}
    }},
    scales: {{
      x: {{ grid: gridCfg, ticks: {{ ...ticksCfg, maxRotation: 0, maxTicksLimit: 8 }} }},
      y: {{ grid: gridCfg, ticks: {{ ...ticksCfg, callback: v => '+' + v.toFixed(2) + '%' }} }}
    }}
  }}
}});

// Live kurs-opdatering fra Yahoo Finance
(async () => {{
  try {{
    const res = await fetch('https://query1.finance.yahoo.com/v8/finance/chart/IMB.L?range=1d&interval=1d');
    const data = await res.json();
    const meta = data.chart.result[0].meta;
    const price = meta.regularMarketPrice;
    const prev = meta.chartPreviousClose || meta.previousClose || price;
    const change = price - prev;
    const pct = (change / prev) * 100;
    const cls = change >= 0 ? 'up' : 'dn';
    const sign = change >= 0 ? '+' : '';
    document.getElementById('live-price').innerHTML = `${{price.toFixed(2)}}<span class="cur">GBp</span>`;
    const chgEl = document.getElementById('live-chg');
    chgEl.className = 'chg ' + cls;
    chgEl.textContent = `${{sign}}${{change.toFixed(2)}} (${{sign}}${{pct.toFixed(2)}}%)`;
    document.getElementById('live-ts').textContent = 'Live · ' + new Date().toLocaleString('da-DK');
  }} catch (e) {{
    // Behold server-side-rendered værdi
    console.warn('Yahoo Finance live-kurs fejlede, bruger cached værdi', e);
  }}
}})();
</script>
</body>
</html>
"""
    return html


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    html = render_html(DATA)
    out = ROOT / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"✓ Genereret {out} ({len(html):,} tegn)")


if __name__ == "__main__":
    main()
