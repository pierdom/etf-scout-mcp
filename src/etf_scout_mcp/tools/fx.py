"""Tool: get_fx_rate — currency/crypto exchange rate, optionally with history (OpenWebNinja)."""
from __future__ import annotations

import re
from typing import Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources import openwebninja

_CODE = re.compile(r"^[A-Za-z0-9]{2,10}$")


class FxPoint(BaseModel):
    time: str = Field(description="UTC timestamp, 'YYYY-MM-DD HH:MM:SS'")
    rate: float


class FxRate(BaseModel):
    from_currency: str
    to_currency: str
    rate: float = Field(description="Units of to_currency per 1 unit of from_currency")
    previous_close: float | None = None
    as_of: str | None = Field(None, description="UTC timestamp of the quote")
    period: str | None = Field(None, description="Set when history was requested")
    history: list[FxPoint] = Field(default_factory=list, description="Oldest first; evenly thinned to max_points, always keeping the latest point")
    source: str = "openwebninja"


def _thin(points: list[dict], max_points: int) -> list[dict]:
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    idx = sorted({round(i * step) for i in range(max_points)})
    return [points[i] for i in idx]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_fx_rate(
        from_currency: str,
        to_currency: str,
        period: Literal["1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX"] | None = None,
        max_points: int = 100,
    ) -> FxRate:
        """Return the exchange rate between two currencies (or a crypto pair), optionally with history.

        Sourced from OpenWebNinja (Google Finance); requires the server to have an
        OPENWEBNINJA_API_KEY. Useful for converting the stock tools' money fields
        (market cap, price, revenue — in each stock's own currency) to EUR.
        A history request costs a second upstream call.

        from_currency, to_currency: ISO codes or crypto tickers, e.g. 'USD', 'EUR', 'GBP', 'BTC'.
        period: if set, also return the rate history for that window.
        max_points: cap on history points, 2-1000 (default 100); the series is
                evenly thinned, never truncated at the end.
        """
        if not openwebninja.enabled():
            raise ValueError(
                "This tool needs an OpenWebNinja key: set OPENWEBNINJA_API_KEY on the server "
                "(subscribe to 'Real-Time Finance Data' at openwebninja.com; a free tier exists)."
            )
        for code in (from_currency, to_currency):
            if not _CODE.match(code.strip()):
                raise ValueError(f"Invalid currency code {code!r}; use e.g. 'USD', 'EUR', 'BTC'")
        if not 2 <= max_points <= 1000:
            raise ValueError("max_points must be between 2 and 1000")
        f, t = from_currency.strip().upper(), to_currency.strip().upper()

        spot = await openwebninja.fetch_fx_rate(f, t)
        history: list[dict] = []
        if period:
            history = _thin(await openwebninja.fetch_fx_series(f, t, period), max_points)
        return FxRate(
            from_currency=f, to_currency=t, period=period,
            history=[FxPoint(**p) for p in history], **spot,
        )
