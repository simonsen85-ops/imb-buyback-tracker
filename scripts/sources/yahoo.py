"""Yahoo Finance stock price fetcher for IMB.L."""

import json
import urllib.request
from datetime import datetime, timezone
from typing import Optional
from .base import HEADERS


def fetch_price(ticker: str = "IMB.L") -> Optional[dict]:
    """Fetch latest stock price from Yahoo Finance v8 chart API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        meta = data["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0
        return {
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "currency": meta.get("currency", "GBp"),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        }
    except Exception as e:
        print(f"  ✗ Yahoo Finance: {e}")
        return None
