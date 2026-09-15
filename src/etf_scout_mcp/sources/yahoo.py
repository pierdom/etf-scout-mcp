"""Yahoo Finance source — wraps yfinance with retry and a real User-Agent session."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from logging.handlers import RotatingFileHandler
from typing import Any

import yfinance as yf
from curl_cffi import requests as cffi_requests

from etf_scout_mcp.cache import cached
from etf_scout_mcp.config import config

# ---------------------------------------------------------------------------
# Logging — rotating file so Tuesday's 429 trail survives a restart
# ---------------------------------------------------------------------------

_log = logging.getLogger("etf_scout_mcp.yahoo")


def _ensure_log_handler() -> None:
    if _log.handlers:
        return
    config.cache_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = config.cache_path.parent / "calls.log"
    handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _log.addHandler(handler)
    _log.setLevel(config.log_level)


# ---------------------------------------------------------------------------
# Shared curl_cffi Session — browser impersonation keeps Yahoo happy
# ---------------------------------------------------------------------------

# yfinance ≥ 1.3 requires a curl_cffi session (not requests.Session) so it
# can set a matching TLS fingerprint. impersonate="firefox" mimics Firefox 128.
_SESSION: cffi_requests.Session | None = None

_RETRY_DELAYS = (1.0, 2.0, 4.0)  # seconds between attempts


def _round_money(value: Any) -> float | None:
    """Round a monetary/price field to 4 decimal places; pass through None as-is."""
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _get_session() -> cffi_requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = cffi_requests.Session(impersonate="firefox")
    return _SESSION


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol, session=_get_session())


def _fetch_with_retry(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call fn(*args, **kwargs), retrying on transient errors with backoff."""
    _ensure_log_handler()
    last_exc: Exception | None = None
    for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
        t0 = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            latency = time.monotonic() - t0
            _log.info("ok attempt=%d latency=%.3fs fn=%s args=%s", attempt, latency, fn.__name__, args)
            return result
        except Exception as exc:
            latency = time.monotonic() - t0
            _log.warning(
                "error attempt=%d latency=%.3fs fn=%s args=%s error=%r",
                attempt, latency, fn.__name__, args, exc,
            )
            last_exc = exc
            if delay is not None:
                time.sleep(delay)
    raise RuntimeError(f"all retries exhausted for {fn.__name__}{args}") from last_exc


# ---------------------------------------------------------------------------
# Public async API (sync yfinance wrapped in asyncio.to_thread)
# ---------------------------------------------------------------------------


@cached(ttl_key="quote")
async def fetch_quote(symbol: str) -> dict[str, Any]:
    """Fetch latest quote fields for *symbol* from Yahoo Finance.

    Returns a flat dict with keys: symbol, currency, price, previous_close,
    open, day_high, day_low, volume, as_of (ISO date string).

    as_of is only set when a price was actually returned — callers must not
    fabricate a date when price is None. Raises on a hard fetch failure;
    a "resolved but empty" ticker instead comes back with price=None and
    currency=None (distinguishable from "resolved, but market closed", where
    currency is populated and price alone is None).
    """
    def _inner() -> dict[str, Any]:
        t = _ticker(symbol)
        info = _fetch_with_retry(lambda: t.fast_info)
        price = getattr(info, "last_price", None)
        return {
            "symbol": symbol,
            "currency": getattr(info, "currency", None),
            "price": _round_money(price),
            "previous_close": _round_money(getattr(info, "previous_close", None)),
            "open": _round_money(getattr(info, "open", None)),
            "day_high": _round_money(getattr(info, "day_high", None)),
            "day_low": _round_money(getattr(info, "day_low", None)),
            "volume": getattr(info, "three_month_average_volume", None),
            "as_of": date.today().isoformat() if price is not None else None,
        }

    return await asyncio.to_thread(_inner)


@cached(ttl_key="history")
async def fetch_history(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> list[dict[str, Any]]:
    """Fetch OHLCV history for *symbol* from Yahoo Finance.

    period:   yfinance period string, e.g. "1y", "6mo", "5y"
    interval: yfinance interval string, e.g. "1d", "1wk", "1mo"

    Returns a list of dicts with keys: date, open, high, low, close, volume.
    Dates are ISO 8601 strings.
    """
    def _inner() -> list[dict[str, Any]]:
        t = _ticker(symbol)
        df = _fetch_with_retry(t.history, period=period, interval=interval)
        if df.empty:
            return []
        df = df.reset_index()
        # Timestamp index → string date
        date_col = df.columns[0]  # "Date" or "Datetime"
        rows = []
        for _, row in df.iterrows():
            # Skip the live/forming bar that yfinance appends during market hours:
            # it has NaN OHLC (→ None) but a non-zero partial volume, making it
            # look like a data gap rather than an in-progress session.
            if row["Close"] != row["Close"]:  # NaN check
                continue
            dt = row[date_col]
            rows.append({
                "date": dt.date().isoformat() if hasattr(dt, "date") else str(dt),
                "open": _round_money(row["Open"]) if row["Open"] == row["Open"] else None,
                "high": _round_money(row["High"]) if row["High"] == row["High"] else None,
                "low": _round_money(row["Low"]) if row["Low"] == row["Low"] else None,
                "close": _round_money(row["Close"]),
                "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else None,
            })
        return rows

    return await asyncio.to_thread(_inner)
