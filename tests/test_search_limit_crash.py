"""Regression: search_etfs(limit=-5) crashed with a raw, unhandled
'boolean value of NA is ambiguous' — pandas' pd.NA sentinel raises on a
bare bool()/truthiness check, and _row_to_summary used plain Python
truthiness on several row.get(...) values. limit/offset also need input
validation so a negative value fails fast with a clear message instead of
producing a confusing pandas slice."""
from __future__ import annotations

import pandas as pd

from etf_scout_mcp.sources import justetf as justetf_module
from etf_scout_mcp.sources.justetf import _row_get, _row_to_summary


def test_row_get_normalises_pd_na_to_none():
    row = pd.Series({"size": pd.NA, "hedged": pd.NA, "name": "Real Fund"})
    assert _row_get(row, "size") is None
    assert _row_get(row, "hedged") is None
    assert _row_get(row, "name") == "Real Fund"
    assert _row_get(row, "missing_key") is None


def test_row_to_summary_handles_pd_na_without_raising():
    """This exact row shape (pd.NA in numeric/bool columns) is what crashed
    search_etfs(limit=-5) in production — the negative limit just happened
    to select a row like this one."""
    row = pd.Series({
        "name": "Newly Listed Fund",
        "ticker": pd.NA,
        "domicile_country": "Ireland",
        "currency": "EUR",
        "size": pd.NA,
        "ter": 0.20,
        "replication": "Full replication",
        "dividends": "Accumulating",
        "hedged": pd.NA,
        "is_sustainable": pd.NA,
        "inception_date": pd.NaT,
        "last_year": pd.NA,
        "last_three_years": pd.NA,
        "last_five_years": pd.NA,
        "last_year_volatility": pd.NA,
    })

    summary = _row_to_summary("IE00NEWFUND0", row, "2026-09-15")

    assert summary["fund_size_eur"] is None
    assert summary["currency_hedged"] is None
    assert summary["sustainability"] is None
    assert summary["ticker"] is None
    assert summary["inception_date"] is None
    assert summary["name"] == "Newly Listed Fund"


async def test_fetch_screener_survives_pd_na_rows(monkeypatch, isolated_cache):
    df = pd.DataFrame.from_dict(
        {
            "IE00NEWFUND0": {
                "name": "Newly Listed Fund", "ticker": pd.NA, "domicile_country": "Ireland",
                "currency": "EUR", "size": pd.NA, "ter": 0.20, "replication": "Full replication",
                "dividends": "Accumulating", "hedged": pd.NA, "is_sustainable": pd.NA,
                "inception_date": pd.NaT, "last_year": pd.NA, "last_three_years": pd.NA,
                "last_five_years": pd.NA, "last_year_volatility": pd.NA,
            },
        },
        orient="index",
    )
    df.index.name = "isin"
    monkeypatch.setattr(justetf_module, "load_overview", lambda **kwargs: df)

    rows = await justetf_module.fetch_screener(limit=20)

    assert len(rows) == 1
    assert rows[0]["fund_size_eur"] is None


async def test_search_etfs_rejects_negative_limit(mcp_client):
    result = await mcp_client.call_tool("search_etfs", {"limit": -5}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text.lower()
    assert "limit" in text
    assert "-5" in text


async def test_search_etfs_rejects_negative_offset(mcp_client):
    result = await mcp_client.call_tool("search_etfs", {"offset": -1}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text.lower()
    assert "offset" in text


async def test_search_etfs_rejects_zero_limit(mcp_client):
    result = await mcp_client.call_tool("search_etfs", {"limit": 0}, raise_on_error=False)
    assert result.is_error
