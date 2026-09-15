"""OpenFIGI source — ISIN → exchange listings via the official Bloomberg-backed API."""
from __future__ import annotations

import asyncio
import logging
import time
from logging.handlers import RotatingFileHandler
from typing import Any

import httpx

from etf_scout_mcp.cache import cached
from etf_scout_mcp.config import config

_API_URL = "https://api.openfigi.com/v3/mapping"

_log = logging.getLogger("etf_scout_mcp.openfigi")


def _ensure_log_handler() -> None:
    if _log.handlers:
        return
    config.cache_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.cache_path.parent / "calls.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(config.log_level)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if config.openfigi_api_key:
        h["X-OPENFIGI-APIKEY"] = config.openfigi_api_key
    return h


@cached(ttl_key="profile")
async def fetch_listings(isin: str) -> list[dict[str, Any]]:
    """Fetch all exchange listings for *isin* from the OpenFIGI API.

    Returns a list of dicts with figi, composite_figi, share_class_figi,
    ticker, exchCode, name, securityType, marketSector fields. The
    /v3/mapping response does not carry micCode or currency for equity/ETF
    rows, so those are not included here.

    composite_figi/share_class_figi let callers collapse OpenFIGI's raw
    response — which includes a separate row per trade-reporting venue, not
    just per exchange listing — down to one row per real exchange listing
    (figi == composite_figi marks the exchange-level composite row).
    """
    _ensure_log_handler()
    t0 = time.monotonic()
    delays = [1.0, 4.0, 10.0]
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt, delay in enumerate(delays + [None]):
            try:
                resp = await client.post(
                    _API_URL,
                    headers=_headers(),
                    json=[{"idType": "ID_ISIN", "idValue": isin}],
                )
            except Exception as exc:
                _log.warning("error fn=fetch_listings isin=%s latency=%.3fs error=%r", isin, time.monotonic() - t0, exc)
                raise
            if resp.status_code == 429:
                if delay is None:
                    _log.warning("error fn=fetch_listings isin=%s status=429 all retries exhausted", isin)
                    resp.raise_for_status()
                _log.info("rate_limited fn=fetch_listings isin=%s attempt=%d retry_in=%.1fs", isin, attempt + 1, delay)
                await asyncio.sleep(delay)
                continue
            try:
                resp.raise_for_status()
            except Exception as exc:
                _log.warning("error fn=fetch_listings isin=%s latency=%.3fs error=%r", isin, time.monotonic() - t0, exc)
                raise
            break

    results = resp.json()
    latency = time.monotonic() - t0
    # Response is a list with one entry per requested job
    job = results[0] if results else {}
    if "error" in job or "warning" in job:
        msg = job.get("error") or job.get("warning")
        _log.info("ok fn=fetch_listings isin=%s no_results=True msg=%r latency=%.3fs", isin, msg, latency)
        return []

    rows = job.get("data", [])
    _log.info("ok fn=fetch_listings isin=%s results=%d latency=%.3fs", isin, len(rows), latency)

    return [
        {
            "figi": r.get("figi"),
            "composite_figi": r.get("compositeFIGI"),
            "share_class_figi": r.get("shareClassFIGI"),
            "ticker": r.get("ticker"),
            "name": r.get("name"),
            "exch_code": r.get("exchCode"),
            "security_type": r.get("securityType"),
            "market_sector": r.get("marketSector"),
            "security_description": r.get("securityDescription"),
        }
        for r in rows
    ]


# Bloomberg exchange code → Yahoo Finance ticker suffix.
# Ordered by preference: Xetra first (highest EUR liquidity for EU ETFs),
# then Euronext Amsterdam, LSE, and the rest.
_EXCH_YAHOO: dict[str, str] = {
    # Xetra variants
    "GR": ".DE", "GF": ".DE", "GD": ".DE", "GS": ".DE",
    "GM": ".DE", "GI": ".DE", "GH": ".DE", "GT": ".DE",
    "GZ": ".DE",
    # Euronext Amsterdam
    "NA": ".AS", "EO": ".AS",
    # LSE
    "LN": ".L",
    # Euronext Paris
    "FP": ".PA",
    # Borsa Italiana
    "IM": ".MI",
    # SIX Swiss
    "SW": ".SW", "SE": ".SW",
    # Madrid
    "SM": ".MC",
    # Brussels
    "BB": ".BR",
    # Stockholm
    "SS": ".ST",
    # Helsinki
    "FH": ".HE",
    # Oslo
    "NO": ".OL",
    # Copenhagen
    "DC": ".CO",
}

# Preference order: we pick the first listing whose exch_code is in this list.
_EXCH_PREFERENCE = [
    "GR", "GF", "GD", "GS", "GM", "GI", "GH", "GT", "GZ",  # Xetra
    "NA", "EO",   # Euronext Amsterdam
    "LN",         # LSE
    "FP",         # Euronext Paris
    "IM",         # Borsa Italiana
    "SW", "SE",   # SIX Swiss
    "SM",         # Madrid
    "BB",         # Brussels
    "SS",         # Stockholm
]


async def resolve_yahoo_ticker(isin: str) -> str | None:
    """Resolve an ISIN to the best Yahoo Finance ticker string.

    Uses OpenFIGI to get all exchange listings, then picks the most
    liquid/preferred exchange and appends its Yahoo suffix.
    Returns None if no suitable listing is found.

    Examples: 'IE00B4L5Y983' → 'EUNL.DE', 'IE00BK5BQT80' → 'VWCE.DE'
    """
    listings = await fetch_listings(isin)
    if not listings:
        return None

    # Build a map of exch_code → ticker for quick lookup
    by_exch: dict[str, str] = {}
    for listing in listings:
        ec = listing.get("exch_code")
        tk = listing.get("ticker")
        if ec and tk and ec not in by_exch:
            by_exch[ec] = tk

    for exch in _EXCH_PREFERENCE:
        if exch in by_exch:
            suffix = _EXCH_YAHOO.get(exch, "")
            return f"{by_exch[exch]}{suffix}"

    return None
