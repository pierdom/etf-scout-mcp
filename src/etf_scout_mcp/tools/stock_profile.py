"""Tools: get_stock_profile, compare_stocks — single-stock fundamentals (Yahoo, EODHD fallback)."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from etf_scout_mcp.models import StockProfile, StockSummary
from etf_scout_mcp.sources import eodhd, yahoo
from etf_scout_mcp.sources.openfigi import resolve_yahoo_ticker


def _stamp(data: dict, source: str, isin: str | None) -> dict:
    from datetime import date

    row = {k: v for k, v in data.items() if k != "quote_type"}
    row.update(source=source, isin=isin, data_as_of=date.today().isoformat())
    return row


async def fetch_stock(symbol: str | None, isin: str | None) -> StockProfile:
    """Resolve + fetch one stock. Never raises for a failed lookup: returns a
    StockProfile with `error` set and all data fields null."""
    if not symbol and not isin:
        raise ValueError("Provide at least one of: symbol, isin")

    resolved = symbol
    if not resolved:
        resolved = await resolve_yahoo_ticker(isin)  # type: ignore[arg-type]
        if not resolved:
            return StockProfile(
                symbol=isin,  # type: ignore[arg-type]
                isin=isin,
                error=(
                    f"Could not resolve a Yahoo Finance ticker for ISIN {isin!r} via OpenFIGI. "
                    "Pass the ticker directly (e.g. 'AAPL', 'SAP.DE') or use search_stocks."
                ),
            )

    error: str | None = None
    try:
        data = await yahoo.fetch_stock_info(resolved)
        if data.get("name") is None:
            error = f"Ticker {resolved!r} was not recognized by Yahoo Finance. Use search_stocks to find it."
        elif data.get("quote_type") != "EQUITY":
            return StockProfile(
                symbol=resolved,
                isin=isin,
                error=f"{resolved!r} is of type {data.get('quote_type')!r}, not a stock — use get_etf_profile for ETFs.",
            )
        else:
            return StockProfile(**_stamp(data, "yahoo", isin))
    except Exception as exc:
        error = f"Yahoo Finance request failed for {resolved!r}: {exc}"

    eodhd_symbol = eodhd.to_eodhd_symbol(resolved) if eodhd.enabled() else None
    if eodhd_symbol:
        try:
            data = await eodhd.fetch_fundamentals(eodhd_symbol)
            if data.get("quote_type") == "EQUITY":
                data["symbol"] = resolved
                return StockProfile(**_stamp(data, "eodhd", isin))
            error = f"{error} EODHD classifies {eodhd_symbol!r} as {data.get('quote_type')}, not a stock."
        except Exception as exc:
            error = f"{error} EODHD fallback also failed: {exc}"

    return StockProfile(symbol=resolved, isin=isin, error=error)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_stock_profile(symbol: str | None = None, isin: str | None = None) -> StockProfile:
        """Return fundamentals for a single listed company (not an ETF).

        Valuation (P/E, P/B, P/S, EV/EBITDA), profitability (margins, ROE/ROA),
        growth, dividend yield/payout, balance-sheet ratios, beta, 52-week range,
        sector/industry/country, business description and analyst consensus.
        Sourced from Yahoo Finance; falls back to EODHD (fewer fields) when
        Yahoo fails and the server has an EODHD key.

        Units: ratios such as margins, growth, ROE and yield are PERCENT
        (27.6 = 27.6%). Money is in the stock's own currency — NOT converted
        to EUR. market_cap/revenue are actual values, not millions.

        Use get_quote for a live price, get_history for a price series,
        compare_stocks to see several companies side by side, and search_stocks
        to find a ticker from a company name. For ETFs use get_etf_profile.
        Research only — pair with a portfolio tool such as Ghostfolio.

        symbol: Yahoo Finance ticker, e.g. 'AAPL', 'SAP.DE', 'ASML.AS'.
                Optional when isin is provided.
        isin:   ISIN, e.g. 'US0378331005'. Auto-resolved to a ticker via OpenFIGI
                (Xetra/Euronext/LSE etc. preferred, US listing as last resort)
                when symbol is absent. Not cross-checked against symbol.
        """
        return await fetch_stock(symbol, isin)

    @mcp.tool()
    async def compare_stocks(symbols: list[str]) -> list[StockSummary]:
        """Return a side-by-side comparison of several stocks by Yahoo ticker.

        One row per requested symbol, in the order given, even when a lookup
        fails — a failed symbol carries a populated `error` and null data
        fields rather than being dropped. Same fields and units as
        get_stock_profile, minus the description and analyst detail.

        Pass tickers, not ISINs (use get_stock_profile per ISIN, or
        get_etf_listings, to find the ticker). Compare like with like:
        multiples and margins differ structurally across sectors, and money
        fields are in each stock's own currency.

        symbols: e.g. ['AAPL', 'MSFT', 'SAP.DE']. At most 20.
        """
        if not symbols:
            raise ValueError("Provide at least one symbol")
        if len(symbols) > 20:
            raise ValueError("compare_stocks accepts at most 20 symbols per call")
        rows = await asyncio.gather(*[fetch_stock(s, None) for s in symbols])
        fields = StockSummary.model_fields
        return [StockSummary(**{k: v for k, v in r.model_dump().items() if k in fields}) for r in rows]
