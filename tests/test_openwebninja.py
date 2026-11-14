"""OpenWebNinja source: symbol mapping, normalisation, fallbacks and the
get_financials / get_stock_news / get_fx_rate tools. No live network."""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from etf_scout_mcp.config import config
from etf_scout_mcp.sources import eodhd, openwebninja as own, yahoo
from etf_scout_mcp.tools import history as history_module
from etf_scout_mcp.tools import quote as quote_module
from etf_scout_mcp.tools import stock_profile as sp


_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def own_key(monkeypatch):
    monkeypatch.setattr(config, "openwebninja_api_key", "ownkey")
    monkeypatch.setattr(config, "cache_enabled", False)


def _fake_get(monkeypatch, payloads: dict):
    """Patch own._get; payloads maps path -> data (or Exception to raise)."""
    calls = []

    async def fake(fn_name, path, params):
        calls.append((path, params))
        res = payloads[path]
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr(own, "_get", fake)
    return calls


def test_to_google_symbol():
    assert own.to_google_symbol("SAP.DE") == "SAP:ETR"
    assert own.to_google_symbol("IWDA.AS") == "IWDA:AMS"
    assert own.to_google_symbol("AAPL") == "AAPL"
    assert own.to_google_symbol("BRK-B") == "BRK.B"
    assert own.to_google_symbol("SAP:ETR") == "SAP:ETR"
    assert own.to_google_symbol("X.ZZ") is None


async def test_fetch_quote_normalised_and_empty(monkeypatch, own_key):
    _fake_get(monkeypatch, {"stock-quote": {
        "price": 213.45, "previous_close": 212.55, "open": 212.55, "high": 214.75, "low": 212,
        "volume": 1409698, "last_update_utc": "2026-11-13 21:06:00"}})
    q = await own.fetch_quote("SAP:ETR")
    assert q["price"] == 213.45 and q["day_high"] == 214.75 and q["currency"] is None
    assert q["as_of"] == "2026-11-13" and q["volume"] == 1409698
    _fake_get(monkeypatch, {"stock-quote": {}})
    with pytest.raises(RuntimeError, match="no quote"):
        await own.fetch_quote("ZZZZ")


async def test_fetch_history_trims_and_is_close_only(monkeypatch, own_key):
    today = date.today()
    old, recent = (today - timedelta(days=200)).isoformat(), (today - timedelta(days=10)).isoformat()
    _fake_get(monkeypatch, {"stock-time-series": {"time_series": {
        f"{recent} 16:00:00": {"price": 11.0, "volume": 5},
        f"{old} 16:00:00": {"price": 10.0, "volume": 7},
    }}})
    data = await own.fetch_history("AAPL", period="3mo")  # 3mo asks for 6M then trims to 92 days
    assert [b["date"] for b in data["bars"]] == [recent]
    assert data["bars"][0] == {"date": recent, "open": None, "high": None, "low": None, "close": 11.0, "volume": 5}
    assert "close and volume only" in data["warning"]


def test_resample_weekly_and_monthly():
    bars = [{"date": d, "open": None, "high": None, "low": None, "close": c, "volume": 1}
            for d, c in [("2026-01-05", 1), ("2026-01-06", 2), ("2026-01-13", 3), ("2026-02-02", 4)]]
    assert [b["close"] for b in own.resample(bars, "1wk")] == [2, 3, 4]
    monthly = own.resample(bars, "1mo")
    assert [b["close"] for b in monthly] == [3, 4] and monthly[0]["volume"] == 3
    assert own.resample(bars, "1d") is bars


async def test_fetch_statement_renames_and_iso_dates(monkeypatch, own_key):
    _fake_get(monkeypatch, {"company-income-statement": {"income_statement": [
        {"date": "2026-9-26", "year": 2026, "month": 9, "day": 26, "currency": "USD", "revenue": 1,
         "EBITDA": 2, "earnings_per_share": 3.5, "net_profit_margin": 27.6, "effective_task_rate_percent": 17.0}]}})
    rows = await own.fetch_statement("AAPL", "income_statement", "ANNUAL")
    assert rows == [{"date": "2026-09-26", "year": 2026, "currency": "USD", "revenue": 1, "ebitda": 2,
                     "eps": 3.5, "net_profit_margin_pct": 27.6, "effective_tax_rate_pct": 17.0}]


async def test_get_http_errors_never_leak_key(monkeypatch, own_key):
    def make(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(own.httpx, "AsyncClient", lambda **kw: _REAL_CLIENT(transport=transport, **kw))

    make(lambda req: httpx.Response(403, json={"status": "ERROR", "error": {"message": "nope", "code": 403}}))
    with pytest.raises(RuntimeError, match="isn't subscribed") as e:
        await own._get("t", "stock-quote", {"symbol": "X"})
    assert "ownkey" not in str(e.value)

    make(lambda req: httpx.Response(429, json={}))
    with pytest.raises(RuntimeError, match="limit"):
        await own._get("t", "stock-quote", {"symbol": "X"})

    def boom(req):
        raise httpx.ConnectError("connect failed for key ownkey", request=req)

    make(boom)
    with pytest.raises(RuntimeError) as e:
        await own._get("t", "stock-quote", {"symbol": "X"})
    assert "ownkey" not in str(e.value) and "ConnectError" in str(e.value)

    seen = {}

    def ok(req):
        seen["key"] = req.headers.get("x-api-key")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"status": "OK", "data": {"a": 1}})

    make(ok)
    assert await own._get("t", "stock-quote", {"symbol": "X"}) == {"a": 1}
    assert seen["key"] == "ownkey" and "ownkey" not in seen["url"]


# ---- fallbacks ------------------------------------------------------------


async def _yahoo_down(*a, **k):
    raise RuntimeError("yahoo down")


async def test_quote_falls_back_to_openwebninja_before_eodhd(monkeypatch, own_key):
    seen = {}

    async def fake_quote(symbol):
        seen["symbol"] = symbol
        return {"symbol": symbol, "currency": None, "price": 213.45, "previous_close": None, "open": None,
                "day_high": None, "day_low": None, "volume": None, "as_of": "2026-11-13"}

    async def eodhd_must_not_run(symbol):
        raise AssertionError("EODHD should not be tried when OpenWebNinja answered")

    monkeypatch.setattr(config, "eodhd_api_key", "k")
    monkeypatch.setattr(yahoo, "fetch_quote", _yahoo_down)
    monkeypatch.setattr(own, "fetch_quote", fake_quote)
    monkeypatch.setattr(eodhd, "fetch_quote", eodhd_must_not_run)
    q = await quote_module.fetch_one("SAP.DE", None)
    assert seen["symbol"] == "SAP:ETR" and q.source == "openwebninja" and q.symbol == "SAP.DE" and q.price == 213.45


async def test_quote_chain_continues_to_eodhd_when_openwebninja_fails(monkeypatch, own_key):
    async def own_fail(symbol):
        raise RuntimeError("quota")

    async def eodhd_ok(symbol):
        return {"symbol": symbol, "currency": None, "price": 1.0, "previous_close": None, "open": None,
                "day_high": None, "day_low": None, "volume": None, "as_of": "2026-11-13"}

    monkeypatch.setattr(config, "eodhd_api_key", "k")
    monkeypatch.setattr(yahoo, "fetch_quote", _yahoo_down)
    monkeypatch.setattr(own, "fetch_quote", own_fail)
    monkeypatch.setattr(eodhd, "fetch_quote", eodhd_ok)
    assert (await quote_module.fetch_one("SAP.DE", None)).source == "eodhd"


async def test_quote_error_mentions_both_fallbacks(monkeypatch, own_key):
    async def own_fail(symbol):
        raise RuntimeError("quota")

    monkeypatch.setattr(yahoo, "fetch_quote", _yahoo_down)
    monkeypatch.setattr(own, "fetch_quote", own_fail)
    q = await quote_module.fetch_one("SAP.DE", None)
    assert q.source == "error" and "OpenWebNinja fallback also failed: quota" in q.error


async def test_history_falls_back_and_resamples(monkeypatch, own_key):
    bars = [{"date": d, "open": None, "high": None, "low": None, "close": float(i), "volume": 1}
            for i, d in enumerate(["2026-01-05", "2026-01-12", "2026-02-02"])]

    async def fake_history(symbol, period="1y"):
        assert symbol == "AAPL" and period == "5y"
        return {"bars": bars, "warning": "W."}

    monkeypatch.setattr(yahoo, "fetch_history", _yahoo_down)
    monkeypatch.setattr(own, "fetch_history", fake_history)
    rows, src, note = await history_module._fetch_rows("AAPL", "5y", "1mo")
    assert src == "openwebninja" and [r["date"] for r in rows] == ["2026-01-12", "2026-02-02"]
    rows, src, note = await history_module._fetch_rows("AAPL", "5y", "1d")
    assert len(rows) == 3 and "coarser than the requested '1d'" in note


async def test_history_error_chain_when_all_fail(monkeypatch, own_key):
    async def fail(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(config, "eodhd_api_key", "k")
    monkeypatch.setattr(yahoo, "fetch_history", _yahoo_down)
    monkeypatch.setattr(own, "fetch_history", fail)
    monkeypatch.setattr(eodhd, "fetch_history", fail)
    with pytest.raises(RuntimeError) as e:
        await history_module._fetch_rows("AAPL", "1y", "1d")
    assert "OpenWebNinja fallback also failed" in str(e.value) and "EODHD fallback also failed" in str(e.value)


async def test_stock_profile_falls_back_to_openwebninja(monkeypatch, own_key):
    async def fake_overview(symbol):
        assert symbol == "SAP:ETR"
        return {"symbol": symbol, "data_as_of": "2026-11-14", "quote_type": "EQUITY", "name": "SAP SE",
                "country": "Germany", "currency": "EUR"}

    monkeypatch.setattr(yahoo, "fetch_stock_info", _yahoo_down)
    monkeypatch.setattr(own, "fetch_overview", fake_overview)
    p = await sp.fetch_stock("SAP.DE", None)
    assert p.source == "openwebninja" and p.symbol == "SAP.DE" and p.name == "SAP SE" and p.data_as_of == "2026-11-14"


async def test_stock_profile_unknown_ticker_skips_openwebninja(monkeypatch, own_key):
    async def unknown(symbol):
        raise yahoo.UnknownTicker(symbol)

    async def must_not_run(symbol):
        raise AssertionError("quota must not be spent on an unknown ticker")

    monkeypatch.setattr(yahoo, "fetch_stock_info", unknown)
    monkeypatch.setattr(own, "fetch_overview", must_not_run)
    assert "not recognized" in (await sp.fetch_stock("ZZZZ", None)).error


# ---- tools ----------------------------------------------------------------


async def test_new_tools_require_key(mcp_client):
    for tool, args in [("get_financials", {"symbol": "AAPL"}), ("get_stock_news", {"symbol": "AAPL"}),
                       ("get_fx_rate", {"from_currency": "USD", "to_currency": "EUR"})]:
        with pytest.raises(Exception, match="OPENWEBNINJA_API_KEY"):
            await mcp_client.call_tool(tool, {**args})


async def test_get_financials(monkeypatch, own_key, mcp_client):
    seen = []

    async def fake(symbol, statement, period):
        seen.append((symbol, statement, period))
        if statement == "cash_flow":
            raise RuntimeError("boom")
        return [{"date": f"202{i}-12-31", "year": 2020 + i, "currency": "EUR", "revenue": i, "total_assets": i}
                for i in range(5, 0, -1)]

    monkeypatch.setattr(own, "fetch_statement", fake)
    d = (await mcp_client.call_tool("get_financials", {"symbol": "SAP.DE", "period": "quarterly", "limit": 2})).data
    assert d.symbol == "SAP:ETR" and d.period == "quarterly"
    assert ("SAP:ETR", "income_statement", "QUARTERLY") in seen
    assert len(d.income_statement) == 2 and d.income_statement[0].revenue == 5
    assert d.balance_sheet[0].total_assets == 5
    assert d.cash_flow is None and d.errors == {"cash_flow": "boom"}


async def test_get_financials_validation_and_isin(monkeypatch, own_key, mcp_client):
    async def fake_resolve(isin):
        return "SAP.DE"

    async def fake(symbol, statement, period):
        return []

    from etf_scout_mcp.tools import _google_symbol

    monkeypatch.setattr(_google_symbol, "resolve_yahoo_ticker", fake_resolve)
    monkeypatch.setattr(own, "fetch_statement", fake)
    d = (await mcp_client.call_tool("get_financials", {"isin": "DE0007164600", "statements": ["income_statement"]})).data
    assert d.symbol == "SAP:ETR" and d.income_statement == [] and d.balance_sheet is None
    with pytest.raises(Exception):
        await mcp_client.call_tool("get_financials", {"symbol": "SAP.DE", "limit": 0})
    with pytest.raises(Exception, match="No Google Finance exchange mapping"):
        await mcp_client.call_tool("get_financials", {"symbol": "X.ZZ"})
    with pytest.raises(Exception):
        await mcp_client.call_tool("get_financials", {"symbol": "SAP.DE", "period": "weekly"})


async def test_get_stock_news(monkeypatch, own_key, mcp_client):
    async def fake(symbol):
        return [{"title": f"T{i}", "url": f"u{i}", "source": "S", "published_at": "2026-11-12T11:05:20Z"} for i in range(5)]

    monkeypatch.setattr(own, "fetch_news", fake)
    d = (await mcp_client.call_tool("get_stock_news", {"symbol": "ASML.AS", "limit": 2})).data
    assert d.symbol == "ASML:AMS" and [i.title for i in d.items] == ["T0", "T1"]
    with pytest.raises(Exception):
        await mcp_client.call_tool("get_stock_news", {"symbol": "ASML.AS", "limit": 51})


async def test_get_fx_rate_with_history_thinning(monkeypatch, own_key, mcp_client):
    async def rate(f, t):
        assert (f, t) == ("USD", "EUR")
        return {"rate": 0.9, "previous_close": 0.89, "as_of": "2026-11-14 09:37:00"}

    series_calls = []

    async def series(f, t, period):
        series_calls.append(period)
        return [{"time": f"t{i:03d}", "rate": 0.8 + i / 1000} for i in range(50)]

    monkeypatch.setattr(own, "fetch_fx_rate", rate)
    monkeypatch.setattr(own, "fetch_fx_series", series)
    d = (await mcp_client.call_tool("get_fx_rate", {"from_currency": "usd", "to_currency": " eur", "period": "1M", "max_points": 5})).data
    assert d.rate == 0.9 and d.from_currency == "USD" and len(d.history) == 5
    assert d.history[0].time == "t000" and d.history[-1].time == "t049"
    d = (await mcp_client.call_tool("get_fx_rate", {"from_currency": "USD", "to_currency": "EUR"})).data
    assert d.history == [] and series_calls == ["1M"]  # no series call without a period
    for bad in ({"from_currency": "US D", "to_currency": "EUR"}, {"from_currency": "USD", "to_currency": "EUR", "max_points": 1},
                {"from_currency": "USD", "to_currency": "EUR", "period": "2W"}):
        with pytest.raises(Exception):
            await mcp_client.call_tool("get_fx_rate", bad)
