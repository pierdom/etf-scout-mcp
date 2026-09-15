"""Tool: find_alternatives — ETFs tracking the same (or a similarly-named) index."""
from __future__ import annotations

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.models import EtfSummary
from etf_scout_mcp.sources.justetf import fetch_profile, fetch_screener


class AlternativesResult(BaseModel):
    isin: str
    index: str | None = Field(None, description="The source fund's tracked index, as reported by justETF")
    ranked_by: str = Field(
        "ter",
        description="How `alternatives` is ordered. Always 'ter' today — no tracking-difference "
        "data is available from justETF (see CHANGELOG), so this is cheapest-first by TER, not a "
        "full cost-of-ownership ranking.",
    )
    alternatives: list[EtfSummary] = Field(default_factory=list)
    error: str | None = Field(None, description="Set if isin failed to resolve, or has no known index")


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def find_alternatives(isin: str, limit: int = 10) -> AlternativesResult:
        """Find ETFs tracking the same index as a given fund, ranked cheapest-first by TER.

        Matches on the source fund's index name as a substring against other funds'
        names on justETF (the same free-text match search_etfs' `query` param uses) —
        a best-effort text match, not a guaranteed same-index match; check each result's
        own profile if precision matters. Ranked by TER only — justETF doesn't publish
        tracking-difference data, so this isn't a full total-cost-of-ownership ranking
        (see `ranked_by`).

        Use this when comparing a fund you're considering against cheaper ways to get
        the same exposure, e.g. before choosing which MSCI World tracker to buy.

        isin: ISIN of the fund to find alternatives for, e.g. 'IE00B4L5Y983'
        limit: Maximum number of alternatives to return (default 10)
        """
        try:
            profile = await fetch_profile(isin)
        except Exception as exc:
            return AlternativesResult(isin=isin, error=f"Failed to fetch {isin!r} from justETF: {exc}")

        if profile is None:
            return AlternativesResult(isin=isin, error=f"ISIN {isin!r} not found on justETF.")

        index = profile.get("index")
        if not index:
            return AlternativesResult(
                isin=isin,
                error=f"justETF has no index recorded for {isin!r} — can't search for alternatives.",
            )

        rows = await fetch_screener(query=index, sort_by="ter", limit=limit + 1)
        alternatives = [EtfSummary(**r) for r in rows if r["isin"] != isin][:limit]

        return AlternativesResult(isin=isin, index=index, alternatives=alternatives)
