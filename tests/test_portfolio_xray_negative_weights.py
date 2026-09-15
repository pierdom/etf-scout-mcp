"""Regression: portfolio_xray accepted negative weights, producing
mathematically-valid-looking but semantically nonsensical output — verified
in production that a mix of weight=0 and weight=-10 caused the negative
holding to silently contribute 100% of the aggregate (via double-negative
division: -10 / (0 + -10) = 1.0) while weight=0 contributed nothing."""
from __future__ import annotations


async def test_negative_weight_rejected_with_clear_message(mcp_client):
    result = await mcp_client.call_tool(
        "portfolio_xray",
        {"holdings": [{"isin": "IE00B4L5Y983", "weight": 0}, {"isin": "IE00BK5BQT80", "weight": -10}]},
        raise_on_error=False,
    )
    assert result.is_error
    text = result.content[0].text.lower()
    assert "negative" in text or ">= 0" in text
    assert "ie00bk5bqt80" in text.lower()  # names the offending holding


async def test_zero_weight_still_allowed(monkeypatch, mcp_client):
    from etf_scout_mcp.tools import portfolio_xray as portfolio_xray_module

    async def fake_fetch_profile(isin: str) -> dict:
        return {
            "isin": isin,
            "countries": [{"name": "United States", "weight": 100.0}],
            "sectors": [],
            "top_holdings": [],
        }

    monkeypatch.setattr(portfolio_xray_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool(
        "portfolio_xray",
        {"holdings": [{"isin": "A", "weight": 0}, {"isin": "B", "weight": 100}]},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data.funds_resolved == 2
