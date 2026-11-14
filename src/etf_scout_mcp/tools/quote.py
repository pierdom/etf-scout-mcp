"""Tool: get_quote — latest price via Yahoo Finance, fallback to EODHD then justETF Gettex."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources import eodhd, openwebninja, yahoo
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
    source: str = Field(
        description="'yahoo', 'openwebninja', 'eodhd', 'justetf_gettex', or 'error' (price is null)"
    )
    error: str | None = Field(
        None,
        description="Set whenever price is null: what failed and what to try instead.",
    )
    bid: float | None = Field(
        None,
        description="Only populated when include_book=True and source='yahoo'. Yahoo's book "
        "data for European-listed ETFs can be stale outside continuous auction windows.",
    )
    ask: float | None = Field(None, description="See bid.")
    spread_bps: float | None = Field(
        None, description="(ask - bid) / mid * 10000. Null whenever bid or ask is null."
    )
    market_state: str | None = Field(
        None, description="e.g. 'REGULAR', 'CLOSED', 'PRE'. Only populated when include_book=True and source='yahoo'."
    )


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


async def _fetch_gettex(isin: str, resolved_symbol: str) -> dict:
    """Fetch the justETF Gettex live quote for *isin*. Raises on any failure."""
    import justetf_scraping

    def _inner() -> dict:
        # load_live_quote returns the initial quote and closes the socket;
        # iterate_live_quote would keep the thread waiting for updates forever.
        q = justetf_scraping.load_live_quote(isin, timeout=15.0)
        return {
            "symbol": resolved_symbol,
            "currency": q.currency or "EUR",
            "price": _round(q.last),
            "previous_close": None,
            "open": None,
            "day_high": None,
            "day_low": None,
            "volume": None,
            "as_of": q.timestamp.date().isoformat() if (q.last is not None and q.timestamp) else None,
        }

    return await asyncio.wait_for(asyncio.to_thread(_inner), timeout=20.0)


async def fetch_one(symbol: str | None, isin: str | None, include_book: bool = False) -> Quote:
    """Fetch a single quote, resolving ISIN→ticker if needed, with EODHD
    (when EODHD_API_KEY is set) then Gettex fallback.

    Never raises for a resolvable-but-priceless lookup — returns a Quote with
    price=None, as_of=None, source="error", and a human-readable error instead.

    include_book: when True and the quote resolves via Yahoo, also fetches
    bid/ask/spread_bps/market_state via a second, heavier Yahoo request
    (sources.yahoo.fetch_quote_book). A book-fetch failure never fails the
    quote itself — bid/ask/etc just stay null.
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
        book: dict = {}
        if include_book:
            try:
                book = await yahoo.fetch_quote_book(resolved_symbol)
            except Exception:
                book = {}  # never fail an otherwise-successful quote over book data
        return Quote(source="yahoo", isin=isin, error=None, **data, **book)  # type: ignore[arg-type]

    # OpenWebNinja (Google Finance) fallback — only when a key is configured.
    # currency is null on these quotes; symbol stays the Yahoo ticker.
    google_symbol = openwebninja.to_google_symbol(resolved_symbol) if openwebninja.enabled() else None
    if google_symbol:
        try:
            own_data = await openwebninja.fetch_quote(google_symbol)
            own_data["symbol"] = resolved_symbol
            return Quote(source="openwebninja", isin=isin, error=None, **own_data)
        except Exception as own_exc:
            yahoo_error = f"{yahoo_error} OpenWebNinja fallback also failed: {own_exc}"

    # EODHD fallback — delayed quote, only when a key is configured. symbol
    # stays the Yahoo ticker so callers can pass it on to other tools.
    eodhd_symbol = eodhd.to_eodhd_symbol(resolved_symbol) if eodhd.enabled() else None
    if eodhd_symbol:
        try:
            eodhd_data = await eodhd.fetch_quote(eodhd_symbol)
            eodhd_data["symbol"] = resolved_symbol
            return Quote(source="eodhd", isin=isin, error=None, **eodhd_data)
        except Exception as eodhd_exc:
            yahoo_error = f"{yahoo_error} EODHD fallback also failed: {eodhd_exc}"

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
    async def get_quote(
        symbol: str | None = None, isin: str | None = None, include_book: bool = False
    ) -> Quote:
        """Return the latest price quote for an ETF or an individual stock.

        Accepts a Yahoo Finance ticker, an ISIN, or both. When only an ISIN
        is given, the ticker is auto-resolved via OpenFIGI (Xetra preferred,
        then Euronext Amsterdam, LSE, etc.). If Yahoo fails, falls back to
        OpenWebNinja/Google Finance (only when the server has an
        OPENWEBNINJA_API_KEY; currency is null), then to
        EODHD (~15 min delayed, only when the server has an EODHD key;
        currency is null on EODHD quotes), then to the justETF Gettex live
        quote (EUR, European hours only).

        Use this for a current price check. Use get_quotes for multiple ETFs
        in one call, or get_history for OHLCV series.
        This tool is for research only — for portfolio valuation, pair it
        with a portfolio tool such as Ghostfolio (ghostfolio-mcp).

        symbol: Yahoo Finance ticker, e.g. 'IWDA.AS' or 'VWCE.DE'.
                Optional when isin is provided.
        isin:   ISIN, e.g. 'IE00B4L5Y983'. Used for ticker auto-resolution
                and as Gettex fallback when Yahoo fails. When symbol is also
                given, isin is NOT cross-checked against it — isin is only
                consulted if symbol is absent, or if Yahoo fails and a
                Gettex fallback is attempted. Passing a symbol and isin that
                refer to different funds will not raise; the response
                echoes back whatever isin you passed alongside the symbol's
                quote. Get isin right from get_etf_listings if precision
                matters, e.g. when passing it on to another tool.
        include_book: When True, also fetch bid/ask/spread_bps/market_state —
                a second, heavier Yahoo request than the base quote (only
                fired when this is True). Only populated when the quote
                resolves via Yahoo; Gettex-sourced quotes never carry book
                data. Yahoo's book data for European-listed ETFs can be
                stale outside continuous auction windows — treat accordingly.
        """
        return await fetch_one(symbol, isin, include_book=include_book)
