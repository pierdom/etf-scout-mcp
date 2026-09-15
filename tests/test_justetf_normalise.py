"""BUG-7 regression: justETF placeholder strings ('-', '', 'n/a', '–') must
normalise to None everywhere, not just distribution_frequency; monetary
fields round to 4dp."""
from __future__ import annotations

from etf_scout_mcp.sources import justetf as justetf_module
from etf_scout_mcp.sources.justetf import _normalise, _round_money, _row_to_summary


def test_normalise_maps_all_documented_placeholders_to_none():
    assert _normalise("-") is None
    assert _normalise("n/a") is None
    assert _normalise("N/A") is None
    assert _normalise("–") is None  # en dash
    assert _normalise("") is None
    assert _normalise("   ") is None
    assert _normalise(None) is None


def test_normalise_passes_through_real_values():
    assert _normalise("Accumulating") == "Accumulating"
    assert _normalise("  Quarterly  ") == "Quarterly"


def test_round_money():
    assert _round_money(166.05999755859375) == 166.06
    assert _round_money(None) is None
    assert _round_money(float("nan")) is None


def test_row_to_summary_normalises_placeholder_fields():
    """Repro shape: justETF's screener DataFrame renders missing string
    fields as '-' or '' rather than omitting them."""
    row = {
        "name": "iShares Core MSCI World UCITS ETF",
        "ticker": "-",
        "domicile_country": "n/a",
        "size": 50_000.0,
        "ter": 0.20,
        "replication": "-",
        "dividends": "",
        "hedged": False,
        "is_sustainable": False,
        "inception_date": None,
        "last_year": 24.76,
        "last_three_years": None,
        "last_five_years": None,
        "last_year_volatility": 10.6,
    }
    summary = _row_to_summary("IE00B4L5Y983", row, "2026-09-15")

    assert summary["ticker"] is None
    assert summary["fund_domicile"] is None
    assert summary["replication"] is None
    assert summary["distribution_policy"] is None
    assert summary["name"] == "iShares Core MSCI World UCITS ETF"
    assert summary["fund_size_eur"] == 50_000_000_000.0
    assert summary["data_as_of"] == "2026-09-15"


async def test_fetch_profile_normalises_distribution_frequency(monkeypatch, isolated_cache):
    """The exact BUG-7 repro: distribution_frequency was leaking justETF's
    raw '-' placeholder straight through get_etf_profile."""

    def fake_overview(isin, include_gettex=False, expand_allocations=True):
        return {
            "isin": isin,
            "name": "Test Fund",
            "description": "-",
            "index": "n/a",
            "investment_focus": None,
            "fund_size_eur": 100.0,
            "ter": 0.20,
            "replication": "Full replication",
            "distribution_policy": "Distributing",
            "distribution_frequency": "-",
            "fund_currency": "EUR",
            "currency_hedged": False,
            "fund_domicile": "Ireland",
            "fund_provider": "iShares",
            "legal_structure": "ETF",
            "sustainability": False,
            "volatility_1y": 12.3,
            "inception_date": "25 September 2009",
            "holdings_date": "29/10/2025",
            "top_holdings": [],
            "countries": [],
            "sectors": [],
        }

    monkeypatch.setattr(justetf_module.justetf_scraping, "get_etf_overview", fake_overview)

    profile = await justetf_module.fetch_profile("IE00B4L5Y983")

    assert profile["distribution_frequency"] is None
    assert profile["description"] is None
    assert profile["index"] is None
    assert profile["replication"] == "Full replication"
