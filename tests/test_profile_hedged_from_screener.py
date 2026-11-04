"""Regression: justETF renamed the profile's "Currency risk" row to
"Currency hedged", so justetf-scraping reports currency_hedged=True for every
fund (and lost volatility_1y). fetch_profile must take both from the screener
row instead, and never pass the profile's always-True value through."""
from __future__ import annotations

from etf_scout_mcp.sources import justetf as justetf_module


def _overview(isin: str, include_gettex=False, expand_allocations=True) -> dict:
    return {
        "isin": isin, "name": "iShares Core MSCI World", "ter": 0.20, "fund_size_eur": 100.0,
        "inception_date": "25 September 2009", "currency_hedged": True, "volatility_1y": None,
    }


async def test_hedged_and_volatility_come_from_screener(monkeypatch, isolated_cache):
    async def fake_summary(isin: str) -> dict:
        return {"currency_hedged": False, "volatility_1y": 10.43, "return_1y": 15.0}

    monkeypatch.setattr(justetf_module.justetf_scraping, "get_etf_overview", _overview)
    monkeypatch.setattr(justetf_module, "fetch_summary", fake_summary)

    result = await justetf_module.fetch_profile("IE00B4L5Y983")
    assert result["currency_hedged"] is False
    assert result["volatility_1y"] == 10.43


async def test_no_screener_row_means_unknown_not_hedged(monkeypatch, isolated_cache):
    async def no_summary(isin: str) -> None:
        return None

    monkeypatch.setattr(justetf_module.justetf_scraping, "get_etf_overview", _overview)
    monkeypatch.setattr(justetf_module, "fetch_summary", no_summary)

    result = await justetf_module.fetch_profile("IE00B4L5Y983")
    assert result["currency_hedged"] is None
