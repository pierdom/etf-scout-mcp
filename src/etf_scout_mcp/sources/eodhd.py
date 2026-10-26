"""EODHD source — optional quote/history fallback via the official eodhd.com REST API.

Only active when EODHD_API_KEY is set. The free tier allows 20 calls/day and
1 year of EOD history, so this is a fallback for when Yahoo fails, never a
primary source. Each fetch_* call costs one API call (cache hits cost none).
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

import httpx

from etf_scout_mcp.cache import cached
from etf_scout_mcp.config import config

_API_URL = "https://eodhd.com/api"

_log = logging.getLogger("etf_scout_mcp.eodhd")


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


def enabled() -> bool:
    return bool(config.eodhd_api_key)


# Yahoo Finance ticker suffix → EODHD exchange code. A bare Yahoo ticker
# (no suffix) is a US listing.
_YAHOO_EXCH: dict[str, str] = {
    ".DE": "XETRA",
    ".F": "F",
    ".AS": "AS",
    ".L": "LSE",
    ".PA": "PA",
    ".MI": "MI",
    ".SW": "SW",
    ".MC": "MC",
    ".BR": "BR",
    ".ST": "ST",
    ".HE": "HE",
    ".OL": "OL",
    ".CO": "CO",
}


def to_eodhd_symbol(yahoo_symbol: str) -> str | None:
    """Map a Yahoo ticker to EODHD's TICKER.EXCHANGE form.

    'EUNL.DE' → 'EUNL.XETRA', 'IWDA.L' → 'IWDA.LSE', 'VTI' → 'VTI.US'.
    Returns None for a suffix with no known EODHD equivalent.
    """
    if "." not in yahoo_symbol:
        return f"{yahoo_symbol}.US"
    ticker, _, suffix = yahoo_symbol.rpartition(".")
    exch = _YAHOO_EXCH.get(f".{suffix.upper()}")
    return f"{ticker}.{exch}" if ticker and exch else None


def _num(value: Any, scale: float = 1.0) -> float | None:
    """EODHD sends 'NA' (a string) for missing numeric fields."""
    try:
        return round(float(value) * scale, 4)
    except (TypeError, ValueError):
        return None


async def _get(fn_name: str, path: str, params: dict[str, Any]) -> Any:
    """GET *path* and return parsed JSON.

    The API token travels as a query parameter, so httpx exceptions (whose
    message includes the full URL) are never re-raised as-is — only the
    status code reaches the logs and the caller.
    """
    _ensure_log_handler()
    t0 = time.monotonic()
    query = {**params, "api_token": config.eodhd_api_key, "fmt": "json"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{_API_URL}/{path}", params=query)
    except httpx.HTTPError as exc:
        _log.warning("error fn=%s path=%s latency=%.3fs error=%s", fn_name, path, time.monotonic() - t0, type(exc).__name__)
        raise RuntimeError(f"EODHD request failed ({type(exc).__name__})") from None
    latency = time.monotonic() - t0
    if resp.status_code != 200:
        _log.warning("error fn=%s path=%s status=%d latency=%.3fs", fn_name, path, resp.status_code, latency)
        if resp.status_code in (402, 429):
            raise RuntimeError("EODHD API call limit reached (free tier: 20/day)")
        raise RuntimeError(f"EODHD returned HTTP {resp.status_code}")
    _log.info("ok fn=%s path=%s latency=%.3fs", fn_name, path, latency)
    return resp.json()


@cached(ttl_key="quote")
async def fetch_quote(symbol: str) -> dict[str, Any]:
    """Fetch the delayed (~15 min) live quote for an EODHD *symbol* ('EUNL.XETRA').

    Returns the same keys as yahoo.fetch_quote. currency is always None: the
    real-time endpoint doesn't carry it, and it can't be inferred from the
    exchange (LSE lists the same fund in USD and GBX). volume is the
    session's volume so far. Raises when no price comes back.
    """
    data = await _get("fetch_quote", f"real-time/{symbol}", {})
    price = _num(data.get("close")) if isinstance(data, dict) else None
    if price is None:
        raise RuntimeError(f"EODHD returned no price for {symbol!r}")
    ts = data.get("timestamp")
    as_of = (
        datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        if isinstance(ts, (int, float))
        else None
    )
    volume = _num(data.get("volume"))
    return {
        "symbol": symbol,
        "currency": None,
        "price": price,
        "previous_close": _num(data.get("previousClose")),
        "open": _num(data.get("open")),
        "day_high": _num(data.get("high")),
        "day_low": _num(data.get("low")),
        "volume": int(volume) if volume is not None else None,
        "as_of": as_of,
    }


_PERIOD_DAYS = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653}
_INTERVAL_PERIOD = {"1d": "d", "1wk": "w", "1mo": "m"}


@cached(ttl_key="history")
async def fetch_history(symbol: str, period: str = "1y", interval: str = "1d") -> dict[str, Any]:
    """Fetch EOD OHLCV bars for an EODHD *symbol*, using yfinance-style period/interval.

    Returns {"bars": [...], "warning": str | None}. Bars have the same keys
    as yahoo.fetch_history. On the free tier, history older than one year is
    cut off and EODHD reports that as a warning row mixed into the bar list —
    it is split out into "warning" here.
    """
    params: dict[str, Any] = {"period": _INTERVAL_PERIOD[interval]}
    if period in _PERIOD_DAYS:
        params["from"] = (date.today() - timedelta(days=_PERIOD_DAYS[period])).isoformat()
    data = await _get("fetch_history", f"eod/{symbol}", params)

    bars: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in data if isinstance(data, list) else []:
        if "warning" in row:
            warnings.append(str(row["warning"]))
            continue
        raw_close = _num(row.get("close"))
        if raw_close is None or not row.get("date"):
            continue
        # Match yfinance's auto_adjust=True: scale OHLC by adjusted/raw close
        # so distributing funds' returns include dividends, as with Yahoo.
        adj_close = _num(row.get("adjusted_close")) or raw_close
        ratio = adj_close / raw_close if raw_close else 1.0
        volume = _num(row.get("volume"))
        bars.append({
            "date": row["date"],
            "open": _num(row.get("open"), ratio),
            "high": _num(row.get("high"), ratio),
            "low": _num(row.get("low"), ratio),
            "close": adj_close,
            "volume": int(volume) if volume is not None else None,
        })
    return {"bars": bars, "warning": "; ".join(warnings) or None}
