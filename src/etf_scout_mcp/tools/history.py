"""Tool: get_history — OHLCV price history via yfinance, with a row budget."""
from __future__ import annotations

import math
import statistics
from datetime import date

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources import yahoo
from etf_scout_mcp.sources.openfigi import resolve_yahoo_ticker

# yfinance's documented period/interval vocabularies. Interval is deliberately
# restricted to the three bar sizes this tool's downsampling logic knows how
# to coarsen between — intraday bars aren't a fit for ETF research anyway.
_VALID_PERIODS = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"}
_INTERVAL_ORDER = ["1d", "1wk", "1mo"]
_VALID_INTERVALS = set(_INTERVAL_ORDER)


class OhlcvBar(BaseModel):
    date: str = Field(description="ISO 8601 date string")
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None


class MonthReturn(BaseModel):
    month: str = Field(description="'YYYY-MM'")
    return_pct: float


class YearReturn(BaseModel):
    year: str = Field(description="'YYYY'; the first and last years in the series may be partial")
    return_pct: float


class HistoryResult(BaseModel):
    symbol: str
    period: str
    interval: str
    downsampled_from: str | None = Field(
        None,
        description="The interval originally requested, if it was auto-coarsened to "
        "stay under max_rows. Null when no coarsening happened.",
    )
    bars: list[OhlcvBar]


class HistorySummary(BaseModel):
    symbol: str
    period: str
    interval: str
    downsampled_from: str | None = Field(
        None,
        description="The interval originally requested, if it was auto-coarsened to "
        "stay under max_rows before these stats were computed. Null when no coarsening happened.",
    )
    first_date: str | None = None
    last_date: str | None = None
    bar_count: int
    total_return_pct: float | None = Field(None, description="Cumulative return over the series")
    cagr_pct: float | None = Field(None, description="Annualised return, compounded")
    annualised_volatility_pct: float | None = Field(
        None, description="Stdev of period-over-period returns, annualised"
    )
    max_drawdown_pct: float | None = Field(None, description="Negative number; largest peak-to-trough decline")
    max_drawdown_peak_date: str | None = None
    max_drawdown_trough_date: str | None = None
    best_month: MonthReturn | None = None
    worst_month: MonthReturn | None = None
    yearly_returns: list[YearReturn] = Field(default_factory=list)


def _compute_summary(bars: list[dict]) -> dict:
    if not bars:
        return {
            "first_date": None, "last_date": None, "bar_count": 0,
            "total_return_pct": None, "cagr_pct": None,
            "annualised_volatility_pct": None, "max_drawdown_pct": None,
            "max_drawdown_peak_date": None, "max_drawdown_trough_date": None,
            "best_month": None, "worst_month": None, "yearly_returns": [],
        }

    dates = [b["date"] for b in bars]
    closes = [b["close"] for b in bars]

    first_close, last_close = closes[0], closes[-1]
    total_return_pct = (last_close / first_close - 1) * 100 if first_close else None

    d0, d1 = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    years = (d1 - d0).days / 365.25
    cagr_pct = (
        ((last_close / first_close) ** (1 / years) - 1) * 100
        if first_close and years > 0
        else None
    )

    period_returns = [
        closes[i] / closes[i - 1] - 1
        for i in range(1, len(closes))
        if closes[i - 1]
    ]
    annualised_volatility_pct = None
    if len(period_returns) >= 2:
        periods_per_year = {"1d": 252, "1wk": 52, "1mo": 12}
        bars_per_year = periods_per_year.get(_infer_interval(dates), 252)
        annualised_volatility_pct = statistics.stdev(period_returns) * math.sqrt(bars_per_year) * 100

    peak, peak_date = closes[0], dates[0]
    max_dd, max_dd_peak_date, max_dd_trough_date = 0.0, dates[0], dates[0]
    for price, dt in zip(closes, dates):
        if price > peak:
            peak, peak_date = price, dt
        dd = (price - peak) / peak if peak else 0.0
        if dd < max_dd:
            max_dd, max_dd_peak_date, max_dd_trough_date = dd, peak_date, dt

    month_end_close: dict[str, float] = {}
    for b in bars:
        month_end_close[b["date"][:7]] = b["close"]  # bars are date-ordered; last write per month wins
    months = sorted(month_end_close)
    month_returns = [
        (months[i], (month_end_close[months[i]] / month_end_close[months[i - 1]] - 1) * 100)
        for i in range(1, len(months))
        if month_end_close[months[i - 1]]
    ]
    best_month = max(month_returns, key=lambda x: x[1]) if month_returns else None
    worst_month = min(month_returns, key=lambda x: x[1]) if month_returns else None

    year_bars: dict[str, list[dict]] = {}
    for b in bars:
        year_bars.setdefault(b["date"][:4], []).append(b)
    yearly_returns = [
        {"year": y, "return_pct": round((yb[-1]["close"] / yb[0]["close"] - 1) * 100, 4)}
        for y, yb in sorted(year_bars.items())
        if yb[0]["close"]
    ]

    return {
        "first_date": dates[0],
        "last_date": dates[-1],
        "bar_count": len(bars),
        "total_return_pct": round(total_return_pct, 4) if total_return_pct is not None else None,
        "cagr_pct": round(cagr_pct, 4) if cagr_pct is not None else None,
        "annualised_volatility_pct": (
            round(annualised_volatility_pct, 4) if annualised_volatility_pct is not None else None
        ),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "max_drawdown_peak_date": max_dd_peak_date,
        "max_drawdown_trough_date": max_dd_trough_date,
        "best_month": {"month": best_month[0], "return_pct": round(best_month[1], 4)} if best_month else None,
        "worst_month": {"month": worst_month[0], "return_pct": round(worst_month[1], 4)} if worst_month else None,
        "yearly_returns": yearly_returns,
    }


def _infer_interval(dates: list[str]) -> str:
    """Best-effort bar-size inference from consecutive dates, for the
    volatility annualisation factor — cheaper than threading the effective
    interval through every call site."""
    if len(dates) < 2:
        return "1d"
    gap_days = (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days
    if gap_days >= 25:
        return "1mo"
    if gap_days >= 5:
        return "1wk"
    return "1d"


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_history(
        symbol: str | None = None,
        isin: str | None = None,
        period: str = "1y",
        interval: str = "1d",
        max_rows: int = 400,
        summary: bool = False,
    ) -> HistoryResult | HistorySummary:
        """Return OHLCV price history for an ETF from Yahoo Finance.

        Accepts a Yahoo Finance ticker, an ISIN, or both. When only an ISIN
        is given, the ticker is auto-resolved via OpenFIGI (Xetra preferred,
        then Euronext Amsterdam, LSE, etc.).

        Use this to chart performance or compute custom metrics over time.
        Use get_quote for the latest price only.
        This tool is for research only — for portfolio return calculations,
        pair it with a portfolio tool such as Ghostfolio (ghostfolio-mcp).

        symbol:   Yahoo Finance ticker, e.g. 'VWCE.DE' or 'CSPX.L'.
                  Optional when isin is provided.
        isin:     ISIN, e.g. 'IE00B4L5Y983'. Used for ticker auto-resolution.
        period:   One of '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'max'.
        interval: One of '1d' (daily), '1wk' (weekly), '1mo' (monthly).
        max_rows: If the requested period/interval would exceed this many
                  bars, the interval is automatically coarsened (1d -> 1wk ->
                  1mo) until the series fits — the series is never silently
                  truncated. `downsampled_from` on the response reports the
                  interval that was actually requested when this happens.
                  Default 400.
        summary:  When true, return computed statistics (total return, CAGR,
                  annualised volatility, max drawdown with peak/trough dates,
                  best/worst calendar month, per-calendar-year returns,
                  first/last bar dates, bar count) instead of the OHLCV
                  series — much smaller payload when you don't need every bar.
        """
        if not symbol and not isin:
            raise ValueError("Provide at least one of: symbol, isin")
        if period not in _VALID_PERIODS:
            raise ValueError(f"Invalid period {period!r}. Valid values: {sorted(_VALID_PERIODS)}")
        if interval not in _VALID_INTERVALS:
            raise ValueError(f"Invalid interval {interval!r}. Valid values: {sorted(_VALID_INTERVALS)}")

        resolved_symbol = symbol
        if not resolved_symbol:
            resolved_symbol = await resolve_yahoo_ticker(isin)  # type: ignore[arg-type]
            if not resolved_symbol:
                raise RuntimeError(
                    f"Could not resolve a Yahoo Finance ticker for ISIN {isin!r}. "
                    "Try passing the ticker directly, e.g. 'EUNL.DE' or 'VWCE.DE'."
                )

        requested_interval = interval
        effective_interval = interval
        rows = await yahoo.fetch_history(resolved_symbol, period=period, interval=effective_interval)

        while len(rows) > max_rows and effective_interval != _INTERVAL_ORDER[-1]:
            effective_interval = _INTERVAL_ORDER[_INTERVAL_ORDER.index(effective_interval) + 1]
            rows = await yahoo.fetch_history(resolved_symbol, period=period, interval=effective_interval)

        downsampled_from = requested_interval if effective_interval != requested_interval else None

        if summary:
            return HistorySummary(
                symbol=resolved_symbol,
                period=period,
                interval=effective_interval,
                downsampled_from=downsampled_from,
                **_compute_summary(rows),
            )

        return HistoryResult(
            symbol=resolved_symbol,
            period=period,
            interval=effective_interval,
            downsampled_from=downsampled_from,
            bars=[OhlcvBar(**r) for r in rows],
        )
