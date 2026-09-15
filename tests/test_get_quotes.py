"""BUG-6 regression: get_quotes must let callers positionally correlate every
row back to what they asked for via `requested`, and must document its true
ordering (all symbols first, then all isins)."""
from __future__ import annotations

from etf_scout_mcp.tools import batch_quote
from etf_scout_mcp.tools.quote import Quote


async def test_requested_field_matches_input_positionally(monkeypatch, mcp_client):
    async def fake_fetch_one(symbol: str | None, isin: str | None) -> Quote:
        return Quote(
            symbol=symbol or isin,
            isin=isin,
            currency="EUR",
            price=1.0,
            previous_close=1.0,
            open=1.0,
            day_high=1.0,
            day_low=1.0,
            volume=100,
            as_of="2026-09-15",
            source="yahoo",
            error=None,
        )

    monkeypatch.setattr(batch_quote, "fetch_one", fake_fetch_one)

    result = await mcp_client.call_tool(
        "get_quotes",
        {"symbols": ["VWCE.DE", "EUNL.DE"], "isins": ["IE00BK5BQT80"]},
    )
    rows = result.data

    assert [r.requested for r in rows] == ["VWCE.DE", "EUNL.DE", "IE00BK5BQT80"]
    # documented contract: all symbols rows first (in order), then all isins rows
    assert rows[0].symbol == "VWCE.DE"
    assert rows[1].symbol == "EUNL.DE"
    assert rows[2].isin == "IE00BK5BQT80"


async def test_single_failure_does_not_abort_batch(monkeypatch, mcp_client):
    async def flaky_fetch_one(symbol: str | None, isin: str | None) -> Quote:
        if symbol == "BROKEN.XX":
            raise RuntimeError("boom")
        return Quote(symbol=symbol, source="yahoo", price=1.0, as_of="2026-09-15", error=None)

    monkeypatch.setattr(batch_quote, "fetch_one", flaky_fetch_one)

    result = await mcp_client.call_tool(
        "get_quotes", {"symbols": ["VWCE.DE", "BROKEN.XX"]}
    )
    rows = result.data

    assert len(rows) == 2
    assert rows[0].error is None
    assert rows[1].error is not None
    assert rows[1].requested == "BROKEN.XX"
