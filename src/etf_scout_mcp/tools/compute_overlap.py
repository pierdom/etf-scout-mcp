"""Tool: compute_overlap — holdings-level overlap between two ETFs (top-10 approximation)."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.sources.justetf import fetch_profile


class SharedHolding(BaseModel):
    name: str
    isin: str | None = None
    weight_a: float
    weight_b: float


class OverlapResult(BaseModel):
    isin_a: str
    isin_b: str
    overlap_pct: float | None = Field(
        None, description="Sum of min(weight_a, weight_b) over shared holdings. Null if either ISIN failed to resolve."
    )
    shared_holdings: list[SharedHolding] = Field(default_factory=list)
    approximate: bool = Field(
        True,
        description="Both funds' holdings are limited to justETF's disclosed top 10 — this is not "
        "full-portfolio overlap. Two funds with low top-10 overlap can still hold materially "
        "similar portfolios once you look past the top 10.",
    )
    holdings_compared_a: int = Field(description="Number of top holdings actually available for isin_a (usually 10)")
    holdings_compared_b: int = Field(description="Number of top holdings actually available for isin_b (usually 10)")
    error: str | None = Field(None, description="Set if isin_a and/or isin_b failed to resolve")


def _key(name: str | None, isin: str | None) -> str:
    """Match holdings by ISIN when both sides have one; else fall back to a
    normalised name — justETF doesn't always carry an ISIN per holding."""
    if isin:
        return f"isin:{isin}"
    return f"name:{(name or '').strip().lower()}"


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def compute_overlap(isin_a: str, isin_b: str) -> OverlapResult:
        """Return the holdings-level overlap between two ETFs.

        Compares each fund's top-10 disclosed holdings (justETF doesn't publish full
        constituent lists) and sums min(weight_a, weight_b) over shared names — this is
        an approximation, not full-portfolio overlap (see `approximate`). Useful for a
        quick "are these two funds basically the same thing" check, e.g. before adding
        a second fund that might be redundant with one you already hold.

        isin_a, isin_b: ISINs of the two ETFs to compare, e.g. 'IE00B4L5Y983', 'IE00BK5BQT80'
        """
        profile_a, profile_b = await asyncio.gather(
            fetch_profile(isin_a), fetch_profile(isin_b), return_exceptions=True
        )

        errors = []
        if isinstance(profile_a, Exception):
            errors.append(f"{isin_a!r}: {profile_a}")
        elif profile_a is None:
            errors.append(f"{isin_a!r}: not found on justETF")
        if isinstance(profile_b, Exception):
            errors.append(f"{isin_b!r}: {profile_b}")
        elif profile_b is None:
            errors.append(f"{isin_b!r}: not found on justETF")
        if errors:
            return OverlapResult(
                isin_a=isin_a,
                isin_b=isin_b,
                holdings_compared_a=0,
                holdings_compared_b=0,
                error="Failed to fetch: " + "; ".join(errors),
            )

        holdings_a = [h for h in (profile_a.get("top_holdings") or []) if h.get("name") and h.get("weight") is not None]
        holdings_b = [h for h in (profile_b.get("top_holdings") or []) if h.get("name") and h.get("weight") is not None]

        by_key_a = {_key(h["name"], h.get("isin")): h for h in holdings_a}
        by_key_b = {_key(h["name"], h.get("isin")): h for h in holdings_b}

        shared = []
        overlap_pct = 0.0
        for key, h_a in by_key_a.items():
            h_b = by_key_b.get(key)
            if h_b is None:
                continue
            weight_a, weight_b = h_a["weight"], h_b["weight"]
            overlap_pct += min(weight_a, weight_b)
            shared.append(
                SharedHolding(name=h_a["name"], isin=h_a.get("isin"), weight_a=weight_a, weight_b=weight_b)
            )

        shared.sort(key=lambda s: -min(s.weight_a, s.weight_b))

        return OverlapResult(
            isin_a=isin_a,
            isin_b=isin_b,
            overlap_pct=round(overlap_pct, 4),
            shared_holdings=shared,
            holdings_compared_a=len(holdings_a),
            holdings_compared_b=len(holdings_b),
        )
