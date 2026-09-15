"""Tool: get_etf_listings — all exchange listings for an ETF by ISIN via OpenFIGI."""
from __future__ import annotations

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources.openfigi import _EXCH_YAHOO, fetch_listings


class EtfListing(BaseModel):
    figi: str | None = Field(None, description="Financial Instrument Global Identifier (Bloomberg)")
    ticker: str | None = Field(None, description="Ticker symbol on this exchange, e.g. 'IWDA' or 'EUNL'")
    name: str | None = None
    exch_code: str | None = Field(None, description="Bloomberg exchange code, e.g. 'GR' (Xetra), 'EO' (Euronext Amsterdam), 'LN' (LSE), 'SW' (SIX Swiss Exchange)")
    yahoo_symbol: str | None = Field(
        None,
        description="Ticker + Yahoo Finance suffix for this exchange, e.g. 'EUNL.DE'. "
        "Null when this exchange isn't in our exch_code→suffix map — use ticker/exch_code instead.",
    )
    security_type: str | None = None
    market_sector: str | None = None
    security_description: str | None = None


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_etf_listings(
        isin: str, primary_only: bool = True, limit: int = 20
    ) -> list[EtfListing]:
        """Return known exchange listings for an ETF identified by ISIN.

        Uses the OpenFIGI API (official Bloomberg-backed mapping service) to
        find exchanges where the ETF trades — Xetra, Euronext Amsterdam, LSE,
        and others — along with the ticker (and, where mappable, the Yahoo
        Finance symbol) for each.

        Use this when you need the right Yahoo Finance ticker for a given ISIN
        (e.g. to pass to get_quote or get_history), or when advising on which
        exchange listing to use for a broker order. Tickers vary by exchange:
        iShares MSCI World is IWDA on Euronext Amsterdam but EUNL on Xetra.

        Do not use this for price data — use get_quote or get_history instead.

        isin: ISIN of the ETF, e.g. 'IE00B4L5Y983'
        primary_only: When true (default), collapse OpenFIGI's raw response —
                      which includes a row per trade-reporting venue, not just
                      per exchange listing — to one row per real exchange
                      listing, deduplicated on (ticker, exch_code). Set false
                      to see every raw row OpenFIGI returns (can be 100+).
        limit: Maximum number of rows to return (default 20).
        """
        rows = await fetch_listings(isin)

        if primary_only:
            rows = [r for r in rows if r.get("figi") and r.get("figi") == r.get("composite_figi")]

        seen: set[tuple[str | None, str | None]] = set()
        deduped = []
        for r in rows:
            key = (r.get("ticker"), r.get("exch_code"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)

        listings = []
        for r in deduped[:limit]:
            ticker = r.get("ticker")
            exch_code = r.get("exch_code")
            suffix = _EXCH_YAHOO.get(exch_code) if exch_code else None
            yahoo_symbol = f"{ticker}{suffix}" if ticker and suffix else None
            listings.append(
                EtfListing(
                    figi=r.get("figi"),
                    ticker=ticker,
                    name=r.get("name"),
                    exch_code=exch_code,
                    yahoo_symbol=yahoo_symbol,
                    security_type=r.get("security_type"),
                    market_sector=r.get("market_sector"),
                    security_description=r.get("security_description"),
                )
            )
        return listings
