"""OpenWebNinja "Real-Time Finance Data" source (Google Finance) — optional.

Only active when OPENWEBNINJA_API_KEY is set. Serves three things Yahoo either
doesn't or can't back up: financial statements, stock news and FX rates, plus a
quote/history/profile fallback ahead of EODHD. Every call costs quota on the
caller's plan (cache hits cost none).

Symbols use Google Finance's 'TICKER:EXCHANGE' form ('SAP:ETR', 'IWDA:AMS');
bare US tickers work too. An unknown symbol is NOT an error upstream — it comes
back as an empty `data`, which the fetchers here turn into a RuntimeError.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any

import httpx

from etf_scout_mcp.cache import cached
from etf_scout_mcp.config import config

_API_URL = "https://api.openwebninja.com/realtime-finance-data"

_log = logging.getLogger("etf_scout_mcp.openwebninja")


def _ensure_log_handler() -> None:
    if _log.handlers:
        return
    config.cache_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.cache_path.parent / "calls.log", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(config.log_level)


def enabled() -> bool:
    return bool(config.openwebninja_api_key)


# Yahoo ticker suffix -> Google Finance exchange code.
_YAHOO_TO_GOOGLE: dict[str, str] = {
    ".DE": "ETR", ".AS": "AMS", ".L": "LON", ".PA": "EPA", ".MI": "BIT",
    ".SW": "SWX", ".MC": "BME", ".BR": "EBR", ".ST": "STO", ".HE": "HEL",
    ".OL": "OSL", ".CO": "CPH",
}


def to_google_symbol(symbol: str) -> str | None:
    """Map a Yahoo ticker to Google's 'TICKER:EXCHANGE' form.

    'SAP.DE' -> 'SAP:ETR', 'AAPL' -> 'AAPL' (bare US tickers work), 'BRK-B' ->
    'BRK.B'. A symbol already containing ':' passes through. Returns None for a
    Yahoo suffix with no known Google equivalent.
    """
    if ":" in symbol:
        return symbol
    if "." not in symbol:
        return symbol.replace("-", ".")
    ticker, _, suffix = symbol.rpartition(".")
    exch = _YAHOO_TO_GOOGLE.get(f".{suffix.upper()}")
    return f"{ticker}:{exch}" if ticker and exch else None


def _num(value: Any) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


def _iso_date(value: Any) -> str | None:
    """'2026-9-26' or '2026-11-13 16:00:00' -> '2026-09-26' / '2026-11-13'."""
    try:
        y, m, d = str(value).split(" ")[0].split("-")
        return date(int(y), int(m), int(d)).isoformat()
    except (ValueError, TypeError):
        return None


async def _get(fn_name: str, path: str, params: dict[str, Any]) -> Any:
    """GET *path* and return the response's `data`.

    The key travels in a header (never in the URL), but httpx exceptions are
    still never re-raised as-is: only a status/short message reaches the caller.
    """
    _ensure_log_handler()
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{_API_URL}/{path}", params=params, headers={"x-api-key": config.openwebninja_api_key or ""}
            )
    except httpx.HTTPError as exc:
        _log.warning("error fn=%s path=%s latency=%.3fs error=%s", fn_name, path, time.monotonic() - t0, type(exc).__name__)
        raise RuntimeError(f"OpenWebNinja request failed ({type(exc).__name__})") from None
    latency = time.monotonic() - t0
    if resp.status_code != 200:
        _log.warning("error fn=%s path=%s status=%d latency=%.3fs", fn_name, path, resp.status_code, latency)
        if resp.status_code == 403:
            raise RuntimeError(
                "OpenWebNinja rejected the request (403): the API key is invalid or the account "
                "isn't subscribed to 'Real-Time Finance Data'"
            )
        if resp.status_code == 429:
            raise RuntimeError("OpenWebNinja rate/quota limit reached")
        try:
            detail = resp.json()["error"]["message"]
        except Exception:
            detail = f"HTTP {resp.status_code}"
        raise RuntimeError(f"OpenWebNinja error: {detail}")
    _log.info("ok fn=%s path=%s latency=%.3fs", fn_name, path, latency)
    body = resp.json()
    if body.get("status") != "OK":
        raise RuntimeError(f"OpenWebNinja error: {(body.get('error') or {}).get('message', 'unknown error')}")
    return body.get("data")


def _require(data: Any, what: str, symbol: str) -> dict:
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"OpenWebNinja has no {what} for {symbol!r}")
    return data


# ---------------------------------------------------------------------------
# Quote / history / profile (fallback sources — same dict shapes as Yahoo's)
# ---------------------------------------------------------------------------


@cached(ttl_key="quote")
async def fetch_quote(symbol: str) -> dict[str, Any]:
    """Latest quote for a Google-form *symbol*; keys match yahoo.fetch_quote.

    currency is always None: the quote endpoint doesn't carry it. Raises when
    the symbol is unknown or has no price.
    """
    d = _require(await _get("fetch_quote", "stock-quote", {"symbol": symbol}), "quote", symbol)
    price = _num(d.get("price"))
    if price is None:
        raise RuntimeError(f"OpenWebNinja returned no price for {symbol!r}")
    return {
        "symbol": symbol,
        "currency": None,
        "price": price,
        "previous_close": _num(d.get("previous_close")),
        "open": _num(d.get("open")),
        "day_high": _num(d.get("high")),
        "day_low": _num(d.get("low")),
        "volume": _int(d.get("volume")),
        "as_of": _iso_date(d.get("last_update_utc")),
    }


# yfinance period -> (OpenWebNinja period, days to trim to, or None for no trim)
_PERIODS: dict[str, tuple[str, int | None]] = {
    "1mo": ("1M", None), "3mo": ("6M", 92), "6mo": ("6M", None), "1y": ("1Y", None),
    "2y": ("5Y", 731), "5y": ("5Y", None), "10y": ("MAX", 3653), "max": ("MAX", None),
}


def resample(bars: list[dict[str, Any]], interval: str) -> list[dict[str, Any]]:
    """Coarsen close-only *bars* to '1wk' or '1mo' (last close wins, volume sums).
    '1d' (or bars already coarser) is returned as-is."""
    if interval == "1d":
        return bars
    groups: dict[str, dict[str, Any]] = {}
    for b in bars:
        y, w, _ = date.fromisoformat(b["date"]).isocalendar()
        key = f"{y}-W{w:02d}" if interval == "1wk" else b["date"][:7]
        prev = groups.get(key)
        vol = (prev["volume"] or 0) + (b["volume"] or 0) if prev and (prev["volume"] or b["volume"]) else b["volume"]
        groups[key] = {**b, "volume": vol}
    return list(groups.values())


@cached(ttl_key="history")
async def fetch_history(symbol: str, period: str = "1y") -> dict[str, Any]:
    """Close-only price series for a Google-form *symbol*, by yfinance-style period.

    Returns {"bars": [...], "warning": str}. Bars carry date/close/volume with
    open/high/low null. Upstream granularity depends on the period (daily up to
    1y, roughly weekly beyond), which is why `interval` isn't a parameter here
    — callers resample with resample() so the cached series is reused.
    """
    api_period, trim_days = _PERIODS[period]
    d = _require(
        await _get("fetch_history", "stock-time-series", {"symbol": symbol, "period": api_period}),
        "time series", symbol,
    )
    cutoff = (date.today() - timedelta(days=trim_days)).isoformat() if trim_days else None
    bars = []
    for ts, row in sorted((d.get("time_series") or {}).items()):
        day = _iso_date(ts)
        close = _num(row.get("price")) if isinstance(row, dict) else None
        if day is None or close is None or (cutoff and day < cutoff):
            continue
        bars.append({"date": day, "open": None, "high": None, "low": None, "close": close, "volume": _int(row.get("volume"))})
    return {
        "bars": bars,
        "warning": "OpenWebNinja (Google Finance): close and volume only (open/high/low null); "
        "price adjustment for dividends/splits is not specified; bars are daily up to 1y and coarser beyond.",
    }


@cached(ttl_key="profile")
async def fetch_overview(symbol: str) -> dict[str, Any]:
    """Company overview for a Google-form *symbol*, keyed like yahoo.fetch_stock_info
    (a subset: no margins/growth/analyst fields). Raises when the symbol is unknown."""
    d = _require(await _get("fetch_overview", "stock-overview", {"symbol": symbol}), "overview", symbol)
    kind = str(d.get("type") or "").lower()
    return {
        "symbol": symbol,
        "data_as_of": date.today().isoformat(),
        "quote_type": "EQUITY" if kind == "stock" else (d.get("type") or None),
        "name": d.get("name"),
        "sector": d.get("company_sector"),
        "industry": d.get("company_industry"),
        "country": d.get("company_country"),
        "exchange": d.get("exchange"),
        "currency": d.get("currency"),
        "market_cap": _num(d.get("company_market_cap")),
        "pe_trailing": _num(d.get("company_pe_ratio")),
        "dividend_yield_pct": _num(d.get("company_dividend_yield")),
        "fifty_two_week_high": _num(d.get("year_high")),
        "fifty_two_week_low": _num(d.get("year_low")),
        "description": d.get("about"),
        "employees": _int(d.get("company_employees")),
    }


# ---------------------------------------------------------------------------
# Financial statements, news, FX (OpenWebNinja-only features)
# ---------------------------------------------------------------------------

_STATEMENTS = {
    "income_statement": "company-income-statement",
    "balance_sheet": "company-balance-sheet",
    "cash_flow": "company-cash-flow",
}
_RENAME = {
    "net_profit_margin": "net_profit_margin_pct",
    "earnings_per_share": "eps",
    "EBITDA": "ebitda",
    "effective_task_rate_percent": "effective_tax_rate_pct",  # sic: upstream typo
    "return_on_assets_percent": "return_on_assets_pct",
    "return_on_capital_percent": "return_on_capital_pct",
}


@cached(ttl_key="profile")
async def fetch_statement(symbol: str, statement: str, period: str) -> list[dict[str, Any]]:
    """One financial statement for a Google-form *symbol*, newest period first.

    statement: 'income_statement' | 'balance_sheet' | 'cash_flow'.
    period: 'ANNUAL' | 'QUARTERLY'. Rows have an ISO `date`, `year`, `quarter`
    (quarterly only), `currency` and the statement's own fields (money in
    actual units, not millions; ratios in percent).
    """
    d = _require(
        await _get("fetch_statement", _STATEMENTS[statement], {"symbol": symbol, "period": period}),
        statement, symbol,
    )
    rows = []
    for raw in d.get(statement) or []:
        row = {_RENAME.get(k, k): v for k, v in raw.items() if k not in ("month", "day")}
        row["date"] = _iso_date(raw.get("date"))
        rows.append(row)
    return rows


@cached(ttl_key="history")
async def fetch_news(symbol: str) -> list[dict[str, Any]]:
    """Latest news for a Google-form *symbol*: title, url, source, published_at (UTC ISO)."""
    d = _require(await _get("fetch_news", "stock-news", {"symbol": symbol}), "news", symbol)
    return [
        {
            "title": n.get("article_title"),
            "url": n.get("article_url"),
            "source": n.get("source"),
            "published_at": (str(n["post_time_utc"]).replace(" ", "T") + "Z") if n.get("post_time_utc") else None,
        }
        for n in d.get("news") or []
    ]


@cached(ttl_key="quote")
async def fetch_fx_rate(from_symbol: str, to_symbol: str) -> dict[str, Any]:
    """Spot exchange rate: {rate, previous_close, as_of}. Raises for an unknown pair."""
    d = _require(
        await _get("fetch_fx_rate", "currency-exchange-rate", {"from_symbol": from_symbol, "to_symbol": to_symbol}),
        "exchange rate", f"{from_symbol}/{to_symbol}",
    )
    rate = _num(d.get("exchange_rate"))
    if rate is None:
        raise RuntimeError(f"OpenWebNinja returned no rate for {from_symbol}/{to_symbol}")
    return {"rate": rate, "previous_close": _num(d.get("previous_close")), "as_of": d.get("last_update_utc")}


@cached(ttl_key="history")
async def fetch_fx_series(from_symbol: str, to_symbol: str, period: str) -> list[dict[str, Any]]:
    """FX rate series for period '1D'|'5D'|'1M'|'6M'|'YTD'|'1Y'|'5Y'|'MAX', oldest first."""
    d = _require(
        await _get("fetch_fx_series", "currency-time-series", {"from_symbol": from_symbol, "to_symbol": to_symbol, "period": period}),
        "time series", f"{from_symbol}/{to_symbol}",
    )
    return [
        {"time": ts, "rate": _num(row.get("exchange_rate"))}
        for ts, row in sorted((d.get("time_series") or {}).items())
        if isinstance(row, dict) and _num(row.get("exchange_rate")) is not None
    ]
