"""Regression: justETF's get_etf_overview doesn't raise for a nonexistent
ISIN — it silently parses whatever fallback page comes back (observed in
production: name="ETF Screener", everything else null). fetch_profile must
detect this and return None instead of fabricating a profile, and every
direct caller (get_etf_profile, portfolio_xray, compute_overlap,
find_alternatives) must surface a real error instead of a silent
success/wrong-answer."""
from __future__ import annotations

from etf_scout_mcp.sources import justetf as justetf_module
from etf_scout_mcp.tools import compute_overlap as compute_overlap_module
from etf_scout_mcp.tools import find_alternatives as find_alternatives_module
from etf_scout_mcp.tools import portfolio_xray as portfolio_xray_module


def _fallback_page_overview(isin: str, include_gettex=False, expand_allocations=True) -> dict:
    """Shape observed in production for a nonexistent ISIN."""
    return {
        "isin": isin,
        "name": "ETF Screener",
        "description": None,
        "index": None,
        "investment_focus": None,
        "fund_size_eur": None,
        "ter": None,
        "replication": None,
        "legal_structure": None,
        "sustainability": False,
        "fund_currency": None,
        "currency_hedged": True,
        "volatility_1y": None,
        "inception_date": None,
        "distribution_policy": None,
        "distribution_frequency": None,
        "fund_domicile": None,
        "fund_provider": None,
        "top_holdings": [],
        "countries": [],
        "sectors": [],
        "holdings_date": None,
    }


def _real_overview(isin: str, include_gettex=False, expand_allocations=True) -> dict:
    return {
        "isin": isin, "name": "Real Fund", "description": None, "index": "MSCI World",
        "investment_focus": None, "fund_size_eur": 100.0, "ter": 0.20,
        "replication": "Full replication", "distribution_policy": "Accumulating",
        "distribution_frequency": None, "fund_currency": "EUR", "currency_hedged": False,
        "fund_domicile": "Ireland", "fund_provider": "iShares", "legal_structure": "ETF",
        "sustainability": False, "volatility_1y": 12.3, "inception_date": "25 September 2009",
        "holdings_date": "29/10/2025",
        "top_holdings": [{"name": "Apple", "isin": "US0378331005", "percentage": 5.0}],
        "countries": [{"name": "United States", "percentage": 60.0}],
        "sectors": [{"name": "Technology", "percentage": 30.0}],
    }


async def test_fetch_profile_returns_none_for_fallback_page(monkeypatch, isolated_cache):
    monkeypatch.setattr(justetf_module.justetf_scraping, "get_etf_overview", _fallback_page_overview)
    monkeypatch.setattr(justetf_module, "load_overview", lambda **kwargs: __import__("pandas").DataFrame())

    result = await justetf_module.fetch_profile("IE00NOTREAL0")

    assert result is None


async def test_fetch_profile_still_returns_real_data_for_a_real_isin(monkeypatch, isolated_cache):
    import pandas as pd

    monkeypatch.setattr(justetf_module.justetf_scraping, "get_etf_overview", _real_overview)
    monkeypatch.setattr(justetf_module, "load_overview", lambda **kwargs: pd.DataFrame())

    result = await justetf_module.fetch_profile("IE00REAL00001")

    assert result is not None
    assert result["name"] == "Real Fund"
    assert result["ter"] == 0.002


async def test_get_etf_profile_tool_surfaces_error_not_fabricated_data(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str):
        return None

    from etf_scout_mcp.tools import etf_profile as etf_profile_module

    monkeypatch.setattr(etf_profile_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool("get_etf_profile", {"isin": "IE00NOTREAL0"})
    data = result.data

    assert data.error is not None
    assert "not found" in data.error.lower()
    assert data.name is None  # never fabricated


async def test_portfolio_xray_surfaces_error_for_not_found_isin(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str):
        if isin == "IE00NOTREAL0":
            return None
        return {"isin": isin, "countries": [{"name": "United States", "weight": 100.0}], "sectors": [], "top_holdings": []}

    monkeypatch.setattr(portfolio_xray_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool(
        "portfolio_xray",
        {"holdings": [{"isin": "OK", "weight": 50}, {"isin": "IE00NOTREAL0", "weight": 50}]},
    )
    data = result.data

    assert data.funds_resolved == 1
    assert len(data.errors) == 1
    assert data.errors[0].isin == "IE00NOTREAL0"
    assert "not found" in data.errors[0].error.lower()


async def test_compute_overlap_surfaces_error_for_not_found_isin(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str):
        if isin == "IE00NOTREAL0":
            return None
        return {"isin": isin, "top_holdings": [{"name": "Apple", "isin": "US0378331005", "weight": 5.0}]}

    monkeypatch.setattr(compute_overlap_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool(
        "compute_overlap", {"isin_a": "OK", "isin_b": "IE00NOTREAL0"}
    )
    data = result.data

    assert data.overlap_pct is None
    assert data.error is not None
    assert "not found" in data.error.lower()


async def test_find_alternatives_surfaces_error_for_not_found_isin(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str):
        return None

    monkeypatch.setattr(find_alternatives_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool("find_alternatives", {"isin": "IE00NOTREAL0"})
    data = result.data

    assert data.alternatives == []
    assert data.error is not None
    assert "not found" in data.error.lower()
