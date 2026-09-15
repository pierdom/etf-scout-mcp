"""FEAT-7 regression: portfolio_xray aggregates look-through exposure and
never silently drops a holding that failed to resolve."""
from __future__ import annotations

from etf_scout_mcp.tools import portfolio_xray as portfolio_xray_module


def _fake_profile(countries, sectors, top_holdings) -> dict:
    return {
        "isin": "X", "name": "Fund", "countries": countries, "sectors": sectors,
        "top_holdings": top_holdings,
    }


async def test_aggregates_weighted_country_and_sector_exposure(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str) -> dict:
        if isin == "FUND_A":
            return _fake_profile(
                countries=[{"name": "United States", "weight": 100.0}],
                sectors=[{"name": "Technology", "weight": 100.0}],
                top_holdings=[{"name": "Apple Inc", "isin": "US0378331005", "weight": 10.0}],
            )
        return _fake_profile(
            countries=[{"name": "United States", "weight": 50.0}, {"name": "Japan", "weight": 50.0}],
            sectors=[{"name": "Technology", "weight": 20.0}, {"name": "Financials", "weight": 80.0}],
            top_holdings=[{"name": "Apple Inc", "isin": "US0378331005", "weight": 5.0}],
        )

    monkeypatch.setattr(portfolio_xray_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool(
        "portfolio_xray",
        {"holdings": [{"isin": "FUND_A", "weight": 50}, {"isin": "FUND_B", "weight": 50}]},
    )
    data = result.data

    countries = {c.name: c.weight for c in data.countries}
    # FUND_A contributes 0.5*100=50 US, FUND_B contributes 0.5*50=25 US -> 75 total
    assert abs(countries["United States"] - 75.0) < 0.01
    assert abs(countries["Japan"] - 25.0) < 0.01

    # Apple appears in both funds' top holdings -> aggregated into one entry
    apple = next(n for n in data.top_single_names if n.isin == "US0378331005")
    assert abs(apple.weight - (0.5 * 10.0 + 0.5 * 5.0)) < 0.01

    assert data.funds_requested == 2
    assert data.funds_resolved == 2
    assert data.errors == []
    assert data.concentration_approximate is True


async def test_failed_holding_lands_in_errors_not_silently_dropped(monkeypatch, mcp_client):
    async def flaky_fetch_profile(isin: str) -> dict:
        if isin == "BROKEN":
            raise RuntimeError("justETF timed out")
        return _fake_profile(
            countries=[{"name": "United States", "weight": 100.0}],
            sectors=[{"name": "Technology", "weight": 100.0}],
            top_holdings=[],
        )

    monkeypatch.setattr(portfolio_xray_module, "fetch_profile", flaky_fetch_profile)

    result = await mcp_client.call_tool(
        "portfolio_xray",
        {"holdings": [{"isin": "OK", "weight": 50}, {"isin": "BROKEN", "weight": 50}]},
    )
    data = result.data

    assert data.funds_requested == 2
    assert data.funds_resolved == 1
    assert len(data.errors) == 1
    assert data.errors[0].isin == "BROKEN"
    assert "justETF timed out" in data.errors[0].error
