"""Single-stock support: get_stock_profile, compare_stocks, search_stocks,
US ticker resolution, EODHD fundamentals fallback, and stocks in portfolio_xray."""
from __future__ import annotations

import pytest

from etf_scout_mcp.sources import eodhd, yahoo
from etf_scout_mcp.sources import openfigi
from etf_scout_mcp.tools import portfolio_xray as xray_module
from etf_scout_mcp.tools import stock_profile as sp


def _info(**over) -> dict:
    base = {
        "symbol": "AAPL", "quote_type": "EQUITY", "data_as_of": "2026-11-13", "name": "Apple Inc.", "sector": "Technology",
        "industry": "Consumer Electronics", "country": "United States", "exchange": "NMS",
        "currency": "USD", "pe_trailing": 39.9, "profit_margin_pct": 27.6, "dividend_yield_pct": 0.3,
        "description": "Makes phones.", "analyst_count": 40,
    }
    base.update(over)
    return base


async def test_profile_from_yahoo(monkeypatch, mcp_client):
    async def fake(symbol):
        return _info()

    monkeypatch.setattr(yahoo, "fetch_stock_info", fake)
    d = (await mcp_client.call_tool("get_stock_profile", {"symbol": "AAPL"})).data
    assert d.source == "yahoo" and d.name == "Apple Inc." and d.profit_margin_pct == 27.6
    assert d.error is None and d.data_as_of == "2026-11-13"
    assert not hasattr(d, "quote_type")


async def test_profile_resolves_isin_via_openfigi(monkeypatch):
    seen = {}

    async def fake_resolve(isin):
        return "APC.DE"

    async def fake(symbol):
        seen["symbol"] = symbol
        return _info(symbol=symbol, currency="EUR")

    monkeypatch.setattr(sp, "resolve_yahoo_ticker", fake_resolve)
    monkeypatch.setattr(yahoo, "fetch_stock_info", fake)
    p = await sp.fetch_stock(None, "US0378331005")
    assert seen["symbol"] == "APC.DE" and p.isin == "US0378331005" and p.currency == "EUR"


async def test_profile_rejects_etf(monkeypatch):
    async def fake(symbol):
        return _info(quote_type="ETF")

    monkeypatch.setattr(yahoo, "fetch_stock_info", fake)
    p = await sp.fetch_stock("IWDA.AS", None)
    assert "get_etf_profile" in p.error and p.name is None


async def test_profile_unknown_ticker(monkeypatch):
    async def fake(symbol):
        raise yahoo.UnknownTicker(symbol)

    async def eodhd_must_not_run(symbol):
        raise AssertionError("EODHD quota must not be spent on an unknown ticker")

    from etf_scout_mcp.config import config

    monkeypatch.setattr(config, "eodhd_api_key", "secret")
    monkeypatch.setattr(eodhd, "fetch_fundamentals", eodhd_must_not_run)
    monkeypatch.setattr(yahoo, "fetch_stock_info", fake)
    p = await sp.fetch_stock("ZZZZ", None)
    assert "not recognized" in p.error


async def test_profile_requires_symbol_or_isin():
    with pytest.raises(ValueError):
        await sp.fetch_stock(None, None)


async def test_profile_falls_back_to_eodhd(monkeypatch):
    from etf_scout_mcp.config import config

    async def boom(symbol):
        raise RuntimeError("yahoo down")

    async def fake_eodhd(symbol):
        assert symbol == "SAP.XETRA"
        return _info(symbol=symbol, name="SAP SE", currency="EUR")

    monkeypatch.setattr(config, "eodhd_api_key", "secret")
    monkeypatch.setattr(yahoo, "fetch_stock_info", boom)
    monkeypatch.setattr(eodhd, "fetch_fundamentals", fake_eodhd)
    p = await sp.fetch_stock("SAP.DE", None)
    assert p.source == "eodhd" and p.name == "SAP SE" and p.symbol == "SAP.DE"


async def test_profile_both_sources_fail_reports_error(monkeypatch):
    from etf_scout_mcp.config import config

    async def boom(symbol):
        raise RuntimeError("down")

    monkeypatch.setattr(config, "eodhd_api_key", "secret")
    monkeypatch.setattr(yahoo, "fetch_stock_info", boom)
    monkeypatch.setattr(eodhd, "fetch_fundamentals", boom)
    p = await sp.fetch_stock("SAP.DE", None)
    assert "Yahoo" in p.error and "EODHD fallback also failed" in p.error and p.source is None


async def test_compare_stocks_keeps_order_and_failed_rows(monkeypatch, mcp_client):
    async def fake(symbol):
        if symbol == "BAD":
            raise yahoo.UnknownTicker(symbol)
        return _info(symbol=symbol)

    monkeypatch.setattr(yahoo, "fetch_stock_info", fake)
    rows = (await mcp_client.call_tool("compare_stocks", {"symbols": ["AAPL", "BAD", "MSFT"]})).data
    assert [r.symbol for r in rows] == ["AAPL", "BAD", "MSFT"]
    assert rows[1].error and rows[0].error is None


async def test_compare_stocks_limits(mcp_client):
    with pytest.raises(Exception):
        await mcp_client.call_tool("compare_stocks", {"symbols": []})
    with pytest.raises(Exception):
        await mcp_client.call_tool("compare_stocks", {"symbols": [f"S{i}" for i in range(21)]})


async def test_search_stocks(monkeypatch, mcp_client):
    async def fake(query, limit):
        assert (query, limit) == ("apple", 5)
        return [{"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "sector": "Technology", "industry": None}]

    monkeypatch.setattr(yahoo, "search_equities", fake)
    rows = (await mcp_client.call_tool("search_stocks", {"query": " apple ", "limit": 5})).data
    assert rows[0].symbol == "AAPL"
    with pytest.raises(Exception):
        await mcp_client.call_tool("search_stocks", {"query": "  "})
    with pytest.raises(Exception):
        await mcp_client.call_tool("search_stocks", {"query": "x", "limit": 0})


async def test_us_isin_resolves_to_bare_ticker(monkeypatch):
    async def fake(isin):
        return [{"exch_code": "UW", "ticker": "AAPL"}, {"exch_code": "UN", "ticker": "AAPL"}]

    monkeypatch.setattr(openfigi, "fetch_listings", fake)
    assert await openfigi.resolve_yahoo_ticker("US0378331005") == "AAPL"


async def test_european_listing_still_preferred_over_us(monkeypatch):
    async def fake(isin):
        return [{"exch_code": "UW", "ticker": "AAPL"}, {"exch_code": "GR", "ticker": "APC"}]

    monkeypatch.setattr(openfigi, "fetch_listings", fake)
    assert await openfigi.resolve_yahoo_ticker("US0378331005") == "APC.DE"


async def test_listings_tool_gives_yahoo_symbol_for_us_exchange(monkeypatch, mcp_client):
    async def fake(isin):
        return [{"figi": "F", "composite_figi": "F", "ticker": "AAPL", "exch_code": "UW", "name": "APPLE"}]

    from etf_scout_mcp.tools import etf_listings

    monkeypatch.setattr(etf_listings, "fetch_listings", fake)
    rows = (await mcp_client.call_tool("get_etf_listings", {"isin": "US0378331005"})).data
    assert rows[0].yahoo_symbol == "AAPL"


async def test_eodhd_fundamentals_normalised(monkeypatch):
    payload = {
        "General": {"Name": "SAP SE", "Sector": "Technology", "Industry": "Software", "CountryName": "Germany",
                    "CurrencyCode": "EUR", "Exchange": "XETRA", "Type": "Common Stock", "FullTimeEmployees": 100000},
        "Highlights": {"MarketCapitalization": 300e9, "DividendYield": 0.012, "ProfitMargin": "NA"},
        "Valuation": {"TrailingPE": 45.5},
        "Technicals": {"Beta": 1.1},
    }

    async def fake_get(fn, path, params):
        assert path == "fundamentals/SAP.XETRA"
        return payload

    monkeypatch.setattr(eodhd, "_get", fake_get)
    monkeypatch.setattr(eodhd.config, "cache_enabled", False)
    d = await eodhd.fetch_fundamentals("SAP.XETRA")
    assert d["name"] == "SAP SE" and d["dividend_yield_pct"] == 1.2 and d["profit_margin_pct"] is None
    assert d["quote_type"] == "EQUITY" and d["employees"] == 100000 and d["pe_trailing"] == 45.5


async def test_xray_counts_unknown_isin_as_stock(monkeypatch, mcp_client):
    async def fake_profile(isin):
        if isin == "FUND":
            return {"isin": isin, "countries": [{"name": "United States", "weight": 100.0}],
                    "sectors": [{"name": "Technology", "weight": 100.0}], "top_holdings": []}
        return None

    async def fake_stock(symbol, isin):
        if isin == "DE0007164600":
            return sp.StockProfile(symbol="SAP.DE", isin=isin, name="SAP SE", country="Germany", sector="Technology")
        return sp.StockProfile(symbol=isin, isin=isin, error="nope")

    monkeypatch.setattr(xray_module, "fetch_profile", fake_profile)
    monkeypatch.setattr(xray_module, "fetch_stock", fake_stock)
    d = (await mcp_client.call_tool("portfolio_xray", {"holdings": [
        {"isin": "FUND", "weight": 50}, {"isin": "DE0007164600", "weight": 50}, {"isin": "XX0000000000", "weight": 0},
    ]})).data
    countries = {c.name: c.weight for c in d.countries}
    assert countries == {"United States": 50.0, "Germany": 50.0}
    assert d.top_single_names[0].name == "SAP SE" and d.top_single_names[0].weight == 50.0
    assert d.funds_resolved == 2 and d.stocks_resolved == 1
    assert [e.isin for e in d.errors] == ["XX0000000000"]


async def test_data_as_of_comes_from_fetcher_not_today(monkeypatch):
    async def fake(symbol):
        return _info(data_as_of="2020-01-02")

    monkeypatch.setattr(yahoo, "fetch_stock_info", fake)
    assert (await sp.fetch_stock("AAPL", None)).data_as_of == "2020-01-02"


async def test_compare_stocks_blank_and_padded_symbols(monkeypatch, mcp_client):
    seen = []

    async def fake(symbol):
        seen.append(symbol)
        return _info(symbol=symbol)

    monkeypatch.setattr(yahoo, "fetch_stock_info", fake)
    rows = (await mcp_client.call_tool("compare_stocks", {"symbols": [" AAPL", "", "MSFT"]})).data
    assert seen == ["AAPL", "MSFT"]
    assert [r.symbol for r in rows] == ["AAPL", "", "MSFT"]
    assert rows[1].error and rows[0].error is None and rows[2].error is None


async def test_empty_yahoo_info_is_not_cached(monkeypatch, isolated_cache):
    class FakeTicker:
        calls = 0

        @property
        def info(self):
            FakeTicker.calls += 1
            return {} if FakeTicker.calls == 1 else {"longName": "Apple Inc.", "quoteType": "EQUITY"}

    monkeypatch.setattr(yahoo, "_ticker", lambda s: FakeTicker())
    monkeypatch.setattr(yahoo.config, "cache_enabled", True)
    with pytest.raises(yahoo.UnknownTicker):
        await yahoo.fetch_stock_info("AAPL")
    assert (await yahoo.fetch_stock_info("AAPL"))["name"] == "Apple Inc."


def test_yahoo_pct_keeps_precision():
    assert yahoo._pct(0.46321) == 46.321
    assert yahoo._pct(0.00004) == 0.004
    assert yahoo._pct(None) is None


def test_share_class_ticker_normalised_for_yahoo():
    assert openfigi.yahoo_symbol("BRK/B", "UN") == "BRK-B"
    assert openfigi.yahoo_symbol("SAP", "GR") == "SAP.DE"
    assert openfigi.yahoo_symbol("X", "ZZ") is None


async def test_xray_merges_stock_and_etf_holding_by_isin(monkeypatch, mcp_client):
    async def fake_profile(isin):
        if isin == "FUND":
            return {"isin": isin, "countries": [], "sectors": [],
                    "top_holdings": [{"name": "Apple Inc", "isin": "US0378331005", "weight": 10.0}]}
        return None

    async def fake_stock(symbol, isin):
        return sp.StockProfile(symbol="AAPL", isin=isin, name="Apple Inc.", country="United States", sector="Technology")

    monkeypatch.setattr(xray_module, "fetch_profile", fake_profile)
    monkeypatch.setattr(xray_module, "fetch_stock", fake_stock)
    d = (await mcp_client.call_tool("portfolio_xray", {"holdings": [
        {"isin": "FUND", "weight": 50}, {"isin": "US0378331005", "weight": 50}]})).data
    apples = [n for n in d.top_single_names if n.isin == "US0378331005"]
    assert len(apples) == 1 and apples[0].weight == 55.0


async def test_xray_looks_up_stocks_concurrently(monkeypatch, mcp_client):
    import asyncio

    running = peak = 0

    async def fake_profile(isin):
        return None

    async def fake_stock(symbol, isin):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return sp.StockProfile(symbol=isin, isin=isin, name=isin, country="X", sector="Y")

    monkeypatch.setattr(xray_module, "fetch_profile", fake_profile)
    monkeypatch.setattr(xray_module, "fetch_stock", fake_stock)
    await mcp_client.call_tool("portfolio_xray", {"holdings": [{"isin": f"S{i}", "weight": 10} for i in range(5)]})
    assert peak == 5
