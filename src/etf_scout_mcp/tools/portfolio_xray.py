"""Tool: portfolio_xray — aggregated look-through exposure across ETF and stock holdings."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from etf_scout_mcp.models import Allocation, Holding
from etf_scout_mcp.sources.justetf import fetch_profile
from etf_scout_mcp.tools.stock_profile import fetch_stock

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
    funds_resolved: int = Field(description="Holdings resolved, ETFs and individual stocks together")
    stocks_resolved: int = Field(0, description="How many of funds_resolved were individual stocks (not found on justETF, resolved via Yahoo)")
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

        Individual stocks are accepted too: an ISIN justETF doesn't know is looked up
        as a stock (Yahoo), counted 100% in its own country and sector and as a
        single name at its full weight. Yahoo's sector labels can differ slightly
        from justETF's, so the same sector may appear under two names.

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
        # Keyed by ISIN when present (so an ETF's "Apple Inc" and a direct
        # "Apple Inc." stake merge), else by lower-cased name. Value: [display name, weight].
        names: dict[str, list] = {}

        def add_name(name: str, isin: str | None, weight: float) -> None:
            key = f"isin:{isin}" if isin else f"name:{name.strip().lower()}"
            entry = names.setdefault(key, [name, isin, 0.0])
            entry[2] += weight

        errors: list[XrayError] = []
        resolved = 0
        stocks_resolved = 0

        # Holdings justETF doesn't know are looked up as stocks — concurrently
        # (each is an OpenFIGI + Yahoo round-trip with retry backoff).
        misses = [
            i for i, p in enumerate(profiles) if p is None and not isinstance(p, Exception)
        ]
        stock_results = dict(zip(
            misses,
            await asyncio.gather(*[fetch_stock(None, holdings[i].isin) for i in misses], return_exceptions=True),
        ))

        for idx, (h, profile) in enumerate(zip(holdings, profiles)):
            if isinstance(profile, Exception):
                errors.append(
                    XrayError(isin=h.isin, error=f"Failed to fetch {h.isin!r} from justETF: {profile}")
                )
                continue
            fund_weight = h.weight / total_weight
            if profile is None:
                # Not a fund on justETF — treat as an individual stock: 100% in
                # its own country/sector and a single name at its full weight.
                stock = stock_results[idx]
                if isinstance(stock, Exception) or stock.error:
                    detail = stock if isinstance(stock, Exception) else stock.error
                    errors.append(
                        XrayError(isin=h.isin, error=f"ISIN {h.isin!r} not found on justETF and not resolvable as a stock: {detail}")
                    )
                    continue
                resolved += 1
                stocks_resolved += 1
                if stock.country:
                    countries[stock.country] = countries.get(stock.country, 0.0) + fund_weight * 100.0
                if stock.sector:
                    sectors[stock.sector] = sectors.get(stock.sector, 0.0) + fund_weight * 100.0
                add_name(stock.name or stock.symbol, h.isin, fund_weight * 100.0)
                continue
            resolved += 1

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
                add_name(hold["name"], hold.get("isin"), fund_weight * hold["weight"])

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
                for n, isin, w in sorted(names.values(), key=lambda e: -e[2])[:_MAX_SINGLE_NAMES]
            ],
            funds_requested=len(holdings),
            funds_resolved=resolved,
            stocks_resolved=stocks_resolved,
            errors=errors,
        )
