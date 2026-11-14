"""Tool: get_stock_news — latest headlines for a stock or ETF (OpenWebNinja)."""
from __future__ import annotations

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources import openwebninja
from etf_scout_mcp.tools._google_symbol import resolve_google_symbol


class NewsItem(BaseModel):
    title: str | None = None
    url: str | None = None
    source: str | None = Field(None, description="Publisher, e.g. 'Reuters'")
    published_at: str | None = Field(None, description="ISO 8601 UTC timestamp")


class StockNews(BaseModel):
    symbol: str = Field(description="Google Finance symbol used, e.g. 'AAPL:NASDAQ'")
    items: list[NewsItem]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_stock_news(
        symbol: str | None = None, isin: str | None = None, limit: int = 10
    ) -> StockNews:
        """Return the latest news headlines for a stock (or ETF) — title, link, publisher, time.

        Sourced from OpenWebNinja (Google Finance); requires the server to have an
        OPENWEBNINJA_API_KEY. Headlines and links only, no article text: use the
        urls if you need the body. Cached for an hour.

        symbol: Yahoo ticker ('AAPL', 'SAP.DE') or Google-form symbol ('SAP:ETR').
                Optional when isin is provided.
        isin:   ISIN, auto-resolved to a ticker via OpenFIGI.
        limit:  Maximum headlines, 1-50 (default 10).
        """
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        google = await resolve_google_symbol(symbol, isin)
        items = await openwebninja.fetch_news(google)
        return StockNews(symbol=google, items=[NewsItem(**i) for i in items[:limit]])
