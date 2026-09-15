"""Tool: get_etf_profile — full ETF profile from justETF by ISIN."""
from __future__ import annotations

from fastmcp import FastMCP

from etf_scout_mcp.models import Allocation, EtfProfile, Holding
from etf_scout_mcp.sources.justetf import fetch_profile


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_etf_profile(isin: str) -> EtfProfile:
        """Return a comprehensive profile for a single ETF identified by ISIN.

        Sourced from justETF. Use this for in-depth research on one fund:
        TER, replication method, distribution policy, fund size, domicile,
        1/3/5-year returns (cumulative, plus 3y/5y annualised CAGR), top
        holdings, and full country/sector breakdowns. data_as_of reports
        when this was scraped.

        Use search_etfs instead if you need to discover or filter ETFs.
        Use get_quote for a live price. Use compare_etfs to see multiple funds
        side by side. This tool is for research only — for portfolio tracking,
        pair it with a portfolio tool such as Ghostfolio (ghostfolio-mcp).

        isin: ISIN of the ETF, e.g. 'IE00B4L5Y983'
        """
        data = await fetch_profile(isin)
        return EtfProfile(
            **{
                k: v for k, v in data.items()
                if k not in ("top_holdings", "countries", "sectors")
            },
            top_holdings=[Holding(**h) for h in data.get("top_holdings", [])],
            countries=[Allocation(**c) for c in data.get("countries", [])],
            sectors=[Allocation(**s) for s in data.get("sectors", [])],
        )
