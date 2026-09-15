"""Tool: get_quote — latest price via Yahoo Finance, fallback to justETF Gettex."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources import yahoo
from etf_scout_mcp.sources.openfigi import resolve_yahoo_ticker


class Quote(BaseModel):
    symbol: str
    isin: str | None = None
    currency: str | None = None
    price: float | None = None
    previous_close: float | None = None
    open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: int | None = None
    as_of: str | None = Field(
        None,
        description="ISO 8601 date string. Always null when price is null — never fabricated.",
    )
    source: str = Field(description="'yahoo', 'justetf_gettex', or 'error' (price is null)")
    error: str | None = Field(
        None,
        description="Set whenever price is null: what failed and what to try instead.",
    )


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


async def _fetch_gettex(isin: str, resolved_symbol: str) -> dict:
    """Fetch the justETF Gettex live quote for *isin*. Raises on any failure."""
    import justetf_scraping

    def _inner() -> dict:
        quotes = list(justetf_scraping.iterate_live_quote(isin))
        if not quotes:
            raise RuntimeError("no gettex quote received")
        q = quotes[0]
        last = q.get("last")
        return {
            "symbol": resolved_symbol,
            "currency": q.get("currency", "EUR"),
            "price": _round(last),
            "previous_close": None,
            "open": None,
            "day_high": None,
            "day_low": None,
            "volume": None,
            "as_of": q["timestamp"].date().isoformat() if (last is not None and q.get("timestamp")) else None,
        }

    return await asyncio.wait_for(asyncio.to_thread(_inner), timeout=20.0)


async def fetch_one(symbol: str | None, isin: str | None) -> Quote:
    """Fetch a single quote, resolving ISIN→ticker if needed, with Gettex fallback.

    Never raises for a resolvable-but-priceless lookup — returns a Quote with
    price=None, as_of=None, source="error", and a human-readable error instead.
    """
    if not symbol and not isin:
        raise ValueError("Provide at least one of: symbol, isin")

    resolved_symbol = symbol
    if not resolved_symbol:
        resolved_symbol = await resolve_yahoo_ticker(isin)  # type: ignore[arg-type]
        if not resolved_symbol:
            return Quote(
                symbol=isin,  # type: ignore[arg-type]
                isin=isin,
                source="error",
                error=(
                    f"Could not resolve a Yahoo Finance ticker for ISIN {isin!r} via "
                    "OpenFIGI. Try passing the ticker directly (e.g. 'EUNL.DE' or "
                    "'IWDA.AS'), or call get_etf_listings to find one."
                ),
            )

    yahoo_error: str | None = None
    data: dict | None = None
    try:
        data = await yahoo.fetch_quote(resolved_symbol)
        if data.get("price") is None:
            if data.get("currency") is None:
                yahoo_error = (
                    f"Ticker {resolved_symbol!r} was not recognized by Yahoo Finance. "
                    "Verify the symbol, or call get_etf_listings to find the correct one."
                )
            else:
                yahoo_error = (
                    f"Yahoo Finance returned no price for {resolved_symbol!r} — the "
                    "market may be closed or the instrument halted. Try again during "
                    "exchange trading hours."
                )
    except Exception as exc:
        yahoo_error = f"Yahoo Finance request failed for {resolved_symbol!r}: {exc}"

    if yahoo_error is None:
        return Quote(source="yahoo", isin=isin, error=None, **data)  # type: ignore[arg-type]

    if not isin:
        return Quote(symbol=resolved_symbol, isin=isin, source="error", error=yahoo_error)

    # Gettex fallback — justETF live quote (EUR, Gettex only)
    try:
        gettex_data = await _fetch_gettex(isin, resolved_symbol)
        if gettex_data.get("price") is None:
            raise RuntimeError("gettex quote had no price")
        return Quote(source="justetf_gettex", isin=isin, error=None, **gettex_data)
    except asyncio.TimeoutError:
        return Quote(
            symbol=resolved_symbol,
            isin=isin,
            source="error",
            error=f"{yahoo_error} Gettex fallback also timed out for {isin}.",
        )
    except Exception as gettex_exc:
        return Quote(
            symbol=resolved_symbol,
            isin=isin,
            source="error",
            error=f"{yahoo_error} Gettex fallback also failed: {gettex_exc}",
        )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_quote(symbol: str | None = None, isin: str | None = None) -> Quote:
        """Return the latest price quote for an ETF.

        Accepts a Yahoo Finance ticker, an ISIN, or both. When only an ISIN
        is given, the ticker is auto-resolved via OpenFIGI (Xetra preferred,
        then Euronext Amsterdam, LSE, etc.). If Yahoo fails, falls back to
        the justETF Gettex live quote (EUR, European hours only).

        Use this for a current price check. Use get_quotes for multiple ETFs
        in one call, or get_history for OHLCV series.
        This tool is for research only — for portfolio valuation, pair it
        with a portfolio tool such as Ghostfolio (ghostfolio-mcp).

        symbol: Yahoo Finance ticker, e.g. 'IWDA.AS' or 'VWCE.DE'.
                Optional when isin is provided.
        isin:   ISIN, e.g. 'IE00B4L5Y983'. Used for ticker auto-resolution
                and as Gettex fallback when Yahoo fails.
        """
        return await fetch_one(symbol, isin)
