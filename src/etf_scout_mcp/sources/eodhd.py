"""EODHD source — optional quote/history fallback via the official eodhd.com REST API.

Only active when EODHD_API_KEY is set. The free tier allows 20 calls/day and
1 year of EOD history, so this is a fallback for when Yahoo fails, never a
primary source (quotes, history and single-stock fundamentals). Each fetch_* call costs one API call (cache hits cost none).
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


def _dig(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _pct(value: Any) -> float | None:
    return _num(value, 100.0)


@cached(ttl_key="profile")
async def fetch_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch single-stock fundamentals for an EODHD *symbol* ('AAPL.US', 'SAP.XETRA').

    Returns a flat dict keyed like yahoo.fetch_stock_info (same units: ratios
    as percent, debt_to_equity unavailable). Costs one API call per symbol
    (filtered to the four sections used). Raises when EODHD has no such stock.
    """
    data = await _get("fetch_fundamentals", f"fundamentals/{symbol}", {"filter": "General,Highlights,Valuation,Technicals"})
    name = _dig(data, "General", "Name")
    if not name:
        raise RuntimeError(f"EODHD has no fundamentals for {symbol!r}")
    employees = _num(_dig(data, "General", "FullTimeEmployees"))
    hl = lambda k: _dig(data, "Highlights", k)  # noqa: E731
    val = lambda k: _dig(data, "Valuation", k)  # noqa: E731
    return {
        "symbol": symbol,
        "quote_type": "EQUITY" if _dig(data, "General", "Type") in (None, "Common Stock", "Preferred Stock") else _dig(data, "General", "Type"),
        "name": name,
        "sector": _dig(data, "General", "Sector") or None,
        "industry": _dig(data, "General", "Industry") or None,
        "country": _dig(data, "General", "CountryName") or None,
        "exchange": _dig(data, "General", "Exchange"),
        "currency": _dig(data, "General", "CurrencyCode"),
        "financial_currency": None,
        "market_cap": _num(hl("MarketCapitalization")),
        "pe_trailing": _num(val("TrailingPE")),
        "pe_forward": _num(val("ForwardPE")),
        "price_to_book": _num(val("PriceBookMRQ")),
        "price_to_sales": _num(val("PriceSalesTTM")),
        "ev_to_ebitda": _num(val("EnterpriseValueEbitda")),
        "eps_trailing": _num(hl("EarningsShare")),
        "dividend_yield_pct": _pct(hl("DividendYield")),
        "dividend_rate": _num(hl("DividendShare")),
        "payout_ratio_pct": None,
        "gross_margin_pct": None,
        "operating_margin_pct": _pct(hl("OperatingMarginTTM")),
        "profit_margin_pct": _pct(hl("ProfitMargin")),
        "roe_pct": _pct(hl("ReturnOnEquityTTM")),
        "roa_pct": _pct(hl("ReturnOnAssetsTTM")),
        "revenue_growth_pct": _pct(hl("QuarterlyRevenueGrowthYOY")),
        "earnings_growth_pct": _pct(hl("QuarterlyEarningsGrowthYOY")),
        "debt_to_equity": None,
        "current_ratio": None,
        "beta": _num(_dig(data, "Technicals", "Beta")),
        "fifty_two_week_high": _num(_dig(data, "Technicals", "52WeekHigh")),
        "fifty_two_week_low": _num(_dig(data, "Technicals", "52WeekLow")),
        "description": _dig(data, "General", "Description"),
        "website": _dig(data, "General", "WebURL") or None,
        "employees": int(employees) if employees is not None else None,
        "revenue": _num(hl("RevenueTTM")),
        "ebitda": _num(hl("EBITDA")),
        "free_cash_flow": None,
        "shares_outstanding": None,
        "analyst_target_mean": None,
        "analyst_recommendation": None,
        "analyst_count": None,
    }
