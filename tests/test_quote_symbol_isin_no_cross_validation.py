"""Documents the intentional (now explicitly documented) behaviour found
during a live audit: get_quote does not cross-validate symbol against isin
when both are given — isin is only consulted for resolution when symbol is
absent, or for Gettex fallback if Yahoo fails. A mismatched pair doesn't
raise; the response echoes back whatever isin was passed. This test locks
in that documented contract so a future change doesn't silently start
(or stop) validating without an explicit decision."""
from __future__ import annotations

from etf_scout_mcp.tools import quote as quote_module
from etf_scout_mcp.tools.quote import fetch_one


async def test_mismatched_symbol_and_isin_not_cross_validated(monkeypatch):
    async def fake_fetch_quote(symbol: str) -> dict:
        return {
            "symbol": symbol, "currency": "EUR", "price": 165.26, "previous_close": 166.02,
            "open": 165.68, "day_high": 165.7, "day_low": 164.98, "volume": 243651,
            "as_of": "2026-09-15",
        }

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", fake_fetch_quote)

    # VWCE.DE (Vanguard FTSE All-World) paired with IWDA's ISIN (a different fund) —
    # the tool must not raise or silently "correct" this, per the documented contract.
    result = await fetch_one(symbol="VWCE.DE", isin="IE00B4L5Y983")

    assert result.symbol == "VWCE.DE"
    assert result.isin == "IE00B4L5Y983"
    assert result.error is None
    assert result.price == 165.26
