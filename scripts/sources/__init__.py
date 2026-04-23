"""
IMB Buyback Tracker — Source modules

Architecture (inspired by FED and EVO refactors):
- base.py defines the Announcement dataclass and shared helpers
- investegate.py scrapes Investegate's per-company RNS listing + URL ID enumeration
- lse_co_uk.py is a fallback scraper for lse.co.uk
- yahoo.py fetches live stock price

The scraper's primary mode is ID enumeration: given the last-seen RNS ID and the
latest ID from the company listing, we fetch every ID in between. This is far more
reliable than pagination because we sidestep the 30-item listing limit.
"""

from .base import Announcement, HEADERS, fetch_html
