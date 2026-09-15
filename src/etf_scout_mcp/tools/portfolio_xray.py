"""Tool: portfolio_xray — aggregated look-through exposure across ETF holdings."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.models import Allocation, Holding
from etf_scout_mcp.sources.justetf import fetch_profile

_MAX_SINGLE_NAMES = 25


class HoldingInput(BaseModel):
    isin: str
    weight: float = Field(description="Portfolio weight for this fund, e.g. 40.0 = 40%. Weights don't need to sum to 100 — used as relative weights.")


class XrayError(BaseModel):
    isin: str
    error: str


class PortfolioXray(BaseModel):
    countries: list[Allocation] = Field(default_factory=list, description="Look-through country exposure, weighted across all resolved holdings")
    sectors: list[Allocation] = Field(default_factory=list, description="Look-through sector exposure, weighted across all resolved holdings")
    top_single_names: list[Holding] = Field(
        default_factory=list,
        description=f"Aggregated single-name concentration, top {_MAX_SINGLE_NAMES} — approximate, see concentration_approximate",
    )
    concentration_approximate: bool = Field(
        True,
        description="top_single_names is built from each fund's disclosed top-10 holdings only "
        "(justETF doesn't publish full constituent lists) — true look-through single-name "
        "concentration may be higher than shown here, especially for funds with little overlap "
        "in their top 10.",
    )
    funds_requested: int
    funds_resolved: int
    errors: list[XrayError] = Field(
        default_factory=list, description="One entry per holding that failed to resolve — never a silent drop"
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def portfolio_xray(holdings: list[HoldingInput]) -> PortfolioXray:
        """Aggregate look-through country, sector, and top single-name exposure across
        a set of ETF holdings, weighted by portfolio weight.

        Pairs with a portfolio tool that knows what you hold (e.g. Ghostfolio via
        ghostfolio-mcp) — that tool tells you *what* you hold, this tells you what
        you're *exposed to* once you look through each fund's underlying assets.
        E.g. two funds that look diversified individually can carry a large combined
        US or single-stock exposure once you look through both.

        Country/sector look-through uses each fund's full published breakdown.
        top_single_names is approximate: justETF only discloses each fund's top 10
        holdings, not its full constituent list, so real single-name concentration may
        be understated — see concentration_approximate (always true today).

        holdings: list of {isin, weight}, e.g.
                  [{"isin": "IE00B4L5Y983", "weight": 60}, {"isin": "IE00BK5BQT80", "weight": 40}].
                  Each weight must be >= 0.
        """
        negative = [h.isin for h in holdings if h.weight < 0]
        if negative:
            raise ValueError(
                f"weight must be >= 0 for every holding; got a negative weight for: {negative}"
            )

        profiles = await asyncio.gather(
            *[fetch_profile(h.isin) for h in holdings], return_exceptions=True
        )

        total_weight = sum(h.weight for h in holdings) or 1.0

        countries: dict[str, float] = {}
        sectors: dict[str, float] = {}
        names: dict[tuple[str, str | None], float] = {}
        errors: list[XrayError] = []
        resolved = 0

        for h, profile in zip(holdings, profiles):
            if isinstance(profile, Exception):
                errors.append(
                    XrayError(isin=h.isin, error=f"Failed to fetch {h.isin!r} from justETF: {profile}")
                )
                continue
            if profile is None:
                errors.append(XrayError(isin=h.isin, error=f"ISIN {h.isin!r} not found on justETF."))
                continue
            resolved += 1
            fund_weight = h.weight / total_weight

            for c in profile.get("countries") or []:
                if c.get("name") is None or c.get("weight") is None:
                    continue
                countries[c["name"]] = countries.get(c["name"], 0.0) + fund_weight * c["weight"]
            for s in profile.get("sectors") or []:
                if s.get("name") is None or s.get("weight") is None:
                    continue
                sectors[s["name"]] = sectors.get(s["name"], 0.0) + fund_weight * s["weight"]
            for hold in profile.get("top_holdings") or []:
                if hold.get("name") is None or hold.get("weight") is None:
                    continue
                key = (hold["name"], hold.get("isin"))
                names[key] = names.get(key, 0.0) + fund_weight * hold["weight"]

        return PortfolioXray(
            countries=[
                Allocation(name=n, weight=round(w, 4))
                for n, w in sorted(countries.items(), key=lambda kv: -kv[1])
            ],
            sectors=[
                Allocation(name=n, weight=round(w, 4))
                for n, w in sorted(sectors.items(), key=lambda kv: -kv[1])
            ],
            top_single_names=[
                Holding(name=n, isin=isin, weight=round(w, 4))
                for (n, isin), w in sorted(names.items(), key=lambda kv: -kv[1])[:_MAX_SINGLE_NAMES]
            ],
            funds_requested=len(holdings),
            funds_resolved=resolved,
            errors=errors,
        )
