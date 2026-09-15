"""Tool: get_etf_listings — all exchange listings for an ETF by ISIN via OpenFIGI."""
from __future__ import annotations

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources.openfigi import fetch_listings


class EtfListing(BaseModel):
    figi: str | None = Field(None, description="Financial Instrument Global Identifier (Bloomberg)")
    ticker: str | None = Field(None, description="Ticker symbol on this exchange, e.g. 'IWDA' or 'EUNL'")
    name: str | None = None
    exch_code: str | None = Field(None, description="Bloomberg exchange code, e.g. 'GR' (Xetra), 'EO' (Euronext Amsterdam), 'LN' (LSE), 'SW' (SIX Swiss Exchange)")
    security_type: str | None = None
    market_sector: str | None = None
    security_description: str | None = None


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_etf_listings(isin: str) -> list[EtfListing]:
        """Return all known exchange listings for an ETF identified by ISIN.

        Uses the OpenFIGI API (official Bloomberg-backed mapping service) to
        find every exchange where the ETF trades — Xetra, Euronext Amsterdam,
        LSE, and others — along with the ticker symbol for each.

        Use this when you need the right Yahoo Finance ticker for a given ISIN
        (e.g. to pass to get_quote or get_history), or when advising on which
        exchange listing to use for a broker order. Tickers vary by exchange:
        iShares MSCI World is IWDA on Euronext Amsterdam but EUNL on Xetra.

        Do not use this for price data — use get_quote or get_history instead.

        isin: ISIN of the ETF, e.g. 'IE00B4L5Y983'
        """
        rows = await fetch_listings(isin)
        return [EtfListing(**r) for r in rows]
