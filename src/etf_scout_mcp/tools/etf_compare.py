"""Tool: compare_etfs — side-by-side comparison of multiple ETFs by ISIN."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from etf_scout_mcp.models import EtfSummary
from etf_scout_mcp.sources.justetf import fetch_summary


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def compare_etfs(isins: list[str]) -> list[EtfSummary]:
        """Return a side-by-side comparison of multiple ETFs identified by ISIN.

        Fetches TER, fund size, replication, distribution policy, 1/3/5-year
        returns, and volatility for each fund from justETF. Useful for
        choosing between similar ETFs (e.g. IWDA vs VWCE vs SPDR ACWI).

        Use get_etf_profile for deeper detail on a single fund (holdings,
        country/sector breakdowns). Use search_etfs to discover candidates
        before comparing. This tool is for research only — pair it with a
        portfolio tool such as Ghostfolio (ghostfolio-mcp) for tracking.

        Returns one row per requested ISIN, in the order given, even when a
        lookup fails — a failed ISIN carries a populated `error` and null
        data fields rather than being silently dropped from the list.

        isins: List of ISINs to compare, e.g. ['IE00B4L5Y983', 'IE00BK5BQT80']
        """
        results = await asyncio.gather(
            *[fetch_summary(isin) for isin in isins], return_exceptions=True
        )
        rows = []
        for isin, data in zip(isins, results):
            if isinstance(data, Exception):
                rows.append(EtfSummary(isin=isin, error=f"Failed to fetch {isin!r} from justETF: {data}"))
            elif data is None:
                rows.append(EtfSummary(isin=isin, error=f"ISIN {isin!r} not found on justETF."))
            else:
                rows.append(EtfSummary(**data))
        return rows
