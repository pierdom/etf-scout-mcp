"""P2-5/P2-6/P2-8/P2-10 regression: fund_currency, data_as_of, annualised
returns, offset pagination, and the leverage heuristic on search_etfs."""
from __future__ import annotations

import pandas as pd

from etf_scout_mcp.sources import justetf as justetf_module
from etf_scout_mcp.sources.justetf import _annualise, _detect_leverage_factor, _is_leveraged


def test_annualise_cagr():
    # 3 years cumulative +33.1% -> ~10%/yr CAGR
    assert abs(_annualise(33.1, 3) - 10.0) < 0.1
    assert _annualise(None, 3) is None


def test_leverage_detection_from_name():
    assert _detect_leverage_factor("Leverage Shares 3x Long US Tech 100") == 3.0
    assert _detect_leverage_factor("WisdomTree EURO STOXX Banks 3x Daily Leveraged") == 3.0
    assert _detect_leverage_factor("iShares Core MSCI World UCITS ETF") is None
    assert _is_leveraged("Xtrackers EURO STOXX 50 2x Leveraged Daily Swap") is True
    assert _is_leveraged("iShares Core MSCI World UCITS ETF") is False


def _fake_screener_df() -> pd.DataFrame:
    rows = {
        "IE00NORMAL01": {
            "name": "iShares Core MSCI World UCITS ETF", "ticker": "IWDA",
            "domicile_country": "Ireland", "currency": "USD", "size": 100_000.0,
            "ter": 0.20, "replication": "Full replication", "dividends": "Accumulating",
            "hedged": False, "is_sustainable": False, "inception_date": None,
            "last_year": 24.76, "last_three_years": 33.1, "last_five_years": 90.0,
            "last_year_volatility": 10.6,
        },
        "IE00NORMAL02": {
            "name": "Vanguard FTSE All-World UCITS ETF", "ticker": "VWCE",
            "domicile_country": "Ireland", "currency": "EUR", "size": 80_000.0,
            "ter": 0.22, "replication": "Full replication", "dividends": "Accumulating",
            "hedged": False, "is_sustainable": False, "inception_date": None,
            "last_year": 22.0, "last_three_years": 30.0, "last_five_years": 85.0,
            "last_year_volatility": 11.1,
        },
        "LU00LEVERAGE1": {
            "name": "WisdomTree EURO STOXX Banks 3x Daily Leveraged", "ticker": "3BNK",
            "domicile_country": "Ireland", "currency": "EUR", "size": 500.0,
            "ter": 0.75, "replication": "Swap", "dividends": "Accumulating",
            "hedged": False, "is_sustainable": False, "inception_date": None,
            "last_year": 180.0, "last_three_years": 400.0, "last_five_years": None,
            "last_year_volatility": 55.0,
        },
    }
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "isin"
    return df


async def test_fetch_screener_exposes_fund_currency_and_data_as_of(monkeypatch, isolated_cache):
    monkeypatch.setattr(justetf_module, "load_overview", lambda **kwargs: _fake_screener_df())

    rows = await justetf_module.fetch_screener(asset_class="equity", limit=20)

    normal_rows = [r for r in rows if r["isin"] != "LU00LEVERAGE1"]
    assert all(r["fund_currency"] in {"USD", "EUR"} for r in normal_rows)
    assert all(r["data_as_of"] for r in rows)
    iwda = next(r for r in rows if r["isin"] == "IE00NORMAL01")
    assert iwda["return_3y_annualised_pct"] is not None
    assert abs(iwda["return_3y_annualised_pct"] - 10.0) < 0.1


async def test_fetch_screener_exclude_leveraged(monkeypatch, isolated_cache):
    monkeypatch.setattr(justetf_module, "load_overview", lambda **kwargs: _fake_screener_df())

    all_rows = await justetf_module.fetch_screener(exclude_leveraged=False, limit=20)
    filtered_rows = await justetf_module.fetch_screener(exclude_leveraged=True, limit=20)

    assert any(r["isin"] == "LU00LEVERAGE1" for r in all_rows)
    assert not any(r["isin"] == "LU00LEVERAGE1" for r in filtered_rows)
    leveraged = next(r for r in all_rows if r["isin"] == "LU00LEVERAGE1")
    assert leveraged["leverage_factor"] == 3.0


async def test_fetch_screener_offset_pagination(monkeypatch, isolated_cache):
    monkeypatch.setattr(justetf_module, "load_overview", lambda **kwargs: _fake_screener_df())

    page1 = await justetf_module.fetch_screener(limit=1, offset=0)
    page2 = await justetf_module.fetch_screener(limit=1, offset=1)

    assert len(page1) == 1
    assert len(page2) == 1
    assert page1[0]["isin"] != page2[0]["isin"]


async def test_fetch_profile_merges_returns_from_summary(monkeypatch, isolated_cache):
    def fake_overview(isin, include_gettex=False, expand_allocations=True):
        return {
            "isin": isin, "name": "Test Fund", "description": None, "index": None,
            "investment_focus": None, "fund_size_eur": 100.0, "ter": 0.20,
            "replication": "Full replication", "distribution_policy": "Accumulating",
            "distribution_frequency": None, "fund_currency": "EUR",
            "currency_hedged": False, "fund_domicile": "Ireland",
            "fund_provider": "iShares", "legal_structure": "ETF",
            "sustainability": False, "volatility_1y": 12.3,
            "inception_date": "25 September 2009", "holdings_date": "29/10/2025",
            "top_holdings": [], "countries": [], "sectors": [],
        }

    monkeypatch.setattr(justetf_module.justetf_scraping, "get_etf_overview", fake_overview)
    monkeypatch.setattr(justetf_module, "load_overview", lambda **kwargs: _fake_screener_df().loc[["IE00NORMAL01"]])

    profile = await justetf_module.fetch_profile("IE00NORMAL01")

    assert profile["return_1y"] == 24.76
    assert profile["return_3y"] == 33.1
    assert profile["return_5y"] == 90.0
    assert abs(profile["return_3y_annualised_pct"] - 10.0) < 0.1
    assert profile["data_as_of"] is not None


async def test_fetch_profile_returns_null_when_summary_unavailable(monkeypatch, isolated_cache):
    def fake_overview(isin, include_gettex=False, expand_allocations=True):
        return {
            "isin": isin, "name": "Test Fund", "description": None, "index": None,
            "investment_focus": None, "fund_size_eur": 100.0, "ter": 0.20,
            "replication": None, "distribution_policy": None,
            "distribution_frequency": None, "fund_currency": "EUR",
            "currency_hedged": False, "fund_domicile": "Ireland",
            "fund_provider": None, "legal_structure": None,
            "sustainability": False, "volatility_1y": None,
            "inception_date": None, "holdings_date": None,
            "top_holdings": [], "countries": [], "sectors": [],
        }

    def empty_overview(**kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(justetf_module.justetf_scraping, "get_etf_overview", fake_overview)
    monkeypatch.setattr(justetf_module, "load_overview", empty_overview)

    profile = await justetf_module.fetch_profile("IE00UNKNOWN0")

    assert profile["return_1y"] is None
    assert profile["return_3y_annualised_pct"] is None
