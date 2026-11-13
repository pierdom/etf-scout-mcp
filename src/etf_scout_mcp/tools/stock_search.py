"""Tool: search_stocks — find a listed company's ticker by name or symbol (Yahoo Finance)."""
from __future__ import annotations

from fastmcp import FastMCP

from etf_scout_mcp.models import StockSearchResult
from etf_scout_mcp.sources import yahoo


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def search_stocks(query: str, limit: int = 10) -> list[StockSearchResult]:
        """Search listed companies by name or ticker and return matching Yahoo symbols.

        Yahoo's own search: free-text, fuzzy, returns equities only (ETFs,
        futures and crypto are filtered out — use search_etfs for ETFs). The
        same company can appear once per exchange listing (e.g. 'SAP' and
        'SAP.DE'); the suffix tells you the exchange. This is a lookup, not a
        fundamentals screener — it cannot filter by P/E, sector, etc.

        Feed a returned `symbol` to get_stock_profile, get_quote or get_history.

        query: company name or ticker fragment, e.g. 'asml' or 'novo nordisk'
        limit: maximum rows to return, 1-50 (default 10)
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        rows = await yahoo.search_equities(query.strip(), limit)
        return [StockSearchResult(**r) for r in rows]
