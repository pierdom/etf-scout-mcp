"""Tool: get_quotes — fetch multiple ETF quotes concurrently."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from pydantic import Field

from etf_scout_mcp.tools.quote import Quote, fetch_one


class QuoteResult(Quote):
    requested: str = Field(
        description="Exact input string this row corresponds to (a symbol or an ISIN)."
    )


async def _safe_fetch(symbol: str | None, isin: str | None, requested: str) -> QuoteResult:
    """Wrap fetch_one so a single failure doesn't abort the whole batch."""
    try:
        q = await fetch_one(symbol, isin)
        return QuoteResult(requested=requested, **q.model_dump())
    except Exception as exc:
        fallback_symbol = symbol or isin or "unknown"
        return QuoteResult(
            requested=requested, symbol=fallback_symbol, isin=isin, source="error", error=str(exc)
        )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_quotes(
        symbols: list[str] | None = None,
        isins: list[str] | None = None,
    ) -> list[QuoteResult]:
        """Return the latest price quotes for multiple ETFs in a single call.

        Fetches all quotes concurrently. Returns one row per requested symbol
        and one row per requested isin — all `symbols` rows first (in the order
        given), then all `isins` rows (in the order given). Each row's `requested`
        field echoes the exact input string it corresponds to, so callers don't
        need to rely on position when symbols and isins are combined. If a single
        lookup fails, that row is still returned with price=null, source="error",
        and a populated `error` — the rest of the batch is unaffected.

        Use get_quote for a single ETF, or this tool when you need prices for
        several ETFs at once (e.g. comparing a shortlist, marking a portfolio).

        symbols: Yahoo Finance tickers, e.g. ['VWCE.DE', 'EUNL.DE', 'CSPX.L'].
                 Optional when isins is provided.
        isins:   ISINs, e.g. ['IE00B4L5Y983', 'IE00BK5BQT80']. Each is
                 auto-resolved to a Yahoo ticker via OpenFIGI (Xetra preferred).
                 Can be combined with symbols.
        """
        if not symbols and not isins:
            raise ValueError("Provide at least one of: symbols, isins")

        coros = []
        for sym in (symbols or []):
            coros.append(_safe_fetch(sym, None, requested=sym))
        for isin in (isins or []):
            coros.append(_safe_fetch(None, isin, requested=isin))

        results = await asyncio.gather(*coros)
        return list(results)
