"""Shared symbol resolution for the OpenWebNinja-only tools (financials, news, FX)."""
from __future__ import annotations

from etf_scout_mcp.sources import openwebninja
from etf_scout_mcp.sources.openfigi import resolve_yahoo_ticker


async def resolve_google_symbol(symbol: str | None, isin: str | None) -> str:
    """Return a Google-Finance-form symbol ('SAP:ETR') for a Yahoo ticker,
    Google symbol or ISIN. Raises ValueError with an actionable message."""
    if not openwebninja.enabled():
        raise ValueError(
            "This tool needs an OpenWebNinja key: set OPENWEBNINJA_API_KEY on the server "
            "(subscribe to 'Real-Time Finance Data' at openwebninja.com; a free tier exists)."
        )
    symbol = symbol.strip() if symbol else None
    isin = isin.strip() if isin else None
    if not symbol and not isin:
        raise ValueError("Provide at least one of: symbol, isin")
    resolved = symbol
    if not resolved:
        resolved = await resolve_yahoo_ticker(isin)  # type: ignore[arg-type]
        if not resolved:
            raise ValueError(
                f"Could not resolve a ticker for ISIN {isin!r} via OpenFIGI. Pass the symbol directly."
            )
    google = openwebninja.to_google_symbol(resolved)
    if not google:
        raise ValueError(
            f"No Google Finance exchange mapping for {resolved!r}. Pass a Google-form symbol "
            "such as 'SAP:ETR' or 'IWDA:AMS' instead."
        )
    return google
