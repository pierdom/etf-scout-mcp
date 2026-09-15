"""Tool: search_etfs — justETF screener with filters for asset class, region, TER, etc."""
from __future__ import annotations

from fastmcp import FastMCP

from etf_scout_mcp.models import EtfSummary
from etf_scout_mcp.sources.justetf import fetch_screener


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def search_etfs(
        asset_class: str | None = None,
        region: str | None = None,
        max_ter: float | None = None,
        min_fund_size_eur: float | None = None,
        distribution: str | None = None,
        query: str | None = None,
        provider: str | None = None,
        currency: str | None = None,
        currency_hedged: bool | None = None,
        replication: str | None = None,
        sustainability: bool | None = None,
        sort_by: str | None = None,
        exclude_leveraged: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EtfSummary]:
        """Search and filter ETFs using the justETF screener.

        This is the headline discovery tool — use it when you need to find
        ETFs matching specific criteria.

        Results include TER, fund size, fund currency, replication,
        distribution policy, and 1/3/5-year returns (cumulative — see
        return_3y_annualised_pct/return_5y_annualised_pct for CAGR; figures
        are in the fund's own reporting currency as shown on justETF, not
        converted). Use get_etf_profile to drill into a result, or
        compare_etfs to put two or more side by side.

        Known limitation: distribution_frequency is not available at this
        screener level (only get_etf_profile scrapes it) — adding it here
        would mean one extra justETF scrape per row.

        asset_class:  'equity', 'bonds', 'commodities', 'real_estate',
                      'money_market', 'precious_metals', 'currency'
        region:       'world', 'europe', 'north_america', 'asia_pacific',
                      'emerging_markets', 'eastern_europe', 'latin_america',
                      'africa'
        max_ter:      Maximum TER as a decimal (0.002 = 0.20%, 20 bps)
        min_fund_size_eur: Minimum fund size in EUR (1_000_000_000 = €1B)
        distribution: 'Accumulating' or 'Distributing'
        query:        Free-text search — fund name substring or ISIN,
                      e.g. 'S&P 500', 'MSCI World ex USA', 'IE00B4L5Y983'
        provider:     Filter by fund provider, e.g. 'iShares', 'Vanguard',
                      'Amundi', 'Xtrackers', 'SPDR', 'Invesco'
        currency:     Fund base currency: 'EUR', 'USD', 'CHF', 'GBP'
        currency_hedged: True for hedged share classes only, False for unhedged
        replication:  Replication method substring: 'full', 'sampling', 'swap'
        sustainability: True for ESG/SRI funds only, False to exclude them
        sort_by:      'ter' (cheapest first) | 'fund_size' (largest first) |
                      'return_1y' | 'return_3y' | 'return_5y' (best first).
                      Default: justETF's fund-size-descending order.
        exclude_leveraged: Drop funds whose name matches a leverage heuristic
                      (regex on the name — justETF's screener has no leverage
                      column, and leveraged-long products are not excluded by
                      any other filter here). Best-effort, not authoritative;
                      see leverage_factor on each row.
        limit:        Maximum number of results to return (default 20)
        offset:       Number of results to skip, for pagination (default 0)
        """
        rows = await fetch_screener(
            asset_class=asset_class,
            region=region,
            max_ter=max_ter,
            min_fund_size_eur=min_fund_size_eur,
            distribution=distribution,
            query=query,
            provider=provider,
            currency=currency,
            currency_hedged=currency_hedged,
            replication=replication,
            sustainability=sustainability,
            sort_by=sort_by,
            exclude_leveraged=exclude_leveraged,
            limit=limit,
            offset=offset,
        )
        return [EtfSummary(**r) for r in rows]
