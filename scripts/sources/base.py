"""Shared infrastructure for IMB scrapers."""

from dataclasses import dataclass, field
from typing import Optional
import urllib.request
import urllib.error
import time


# Browser-like User-Agent — mandatory for Investegate and LSE.co.uk
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
}


@dataclass
class Announcement:
    """A buyback transaction parsed from an RNS filing."""
    dato: str                       # ISO date YYYY-MM-DD (transaction date, not filing date)
    antal_aktier: int               # Shares purchased
    gns_kurs_gbp: float             # Average price in pence
    beloeb_gbp_mio: float           # Amount in £M (computed)
    aktier_efter: Optional[int]     # Shares in issue after cancellation
    rns_id: Optional[str] = None    # RNS announcement ID (for dedup)
    source_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "dato": self.dato,
            "antal_aktier": self.antal_aktier,
            "gns_kurs_gbp": self.gns_kurs_gbp,
            "beloeb_gbp_mio": self.beloeb_gbp_mio,
            "aktier_efter": self.aktier_efter,
            "rns_id": self.rns_id,
        }


def fetch_html(url: str, timeout: int = 20, retries: int = 2) -> Optional[str]:
    """GET a URL with browser headers, retrying on transient failures."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if len(body) < 500:
                    print(f"    ⚠ Suspiciously short response ({len(body)} chars) from {url}")
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # Expected during ID enumeration
            if e.code in (429, 503) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            # Log 403 and other errors loudly — they're the ones we can't ignore
            print(f"    ✗ HTTP {e.code} ({e.reason}) for {url}")
            return None
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"    ✗ {type(e).__name__}: {e} for {url}")
            return None
    return None
