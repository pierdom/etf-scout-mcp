"""EODHD fallback: symbol mapping, response normalisation, fallback order in
get_quote/get_history, and that the API token never leaks into errors."""
from __future__ import annotations

import httpx
import pytest

from etf_scout_mcp.config import config
from etf_scout_mcp.sources import eodhd
from etf_scout_mcp.sources import yahoo as yahoo_module
from etf_scout_mcp.tools import quote as quote_module
from etf_scout_mcp.tools.quote import fetch_one


@pytest.fixture
def eodhd_key(monkeypatch):
    monkeypatch.setattr(config, "eodhd_api_key", "SECRET-TOKEN")


@pytest.mark.parametrize(
    "yahoo, expected",
    [
        ("EUNL.DE", "EUNL.XETRA"),
        ("IWDA.L", "IWDA.LSE"),
        ("IWDA.AS", "IWDA.AS"),
        ("VTI", "VTI.US"),
        ("FOO.XX", None),
    ],
)
def test_to_eodhd_symbol(yahoo, expected):
    assert eodhd.to_eodhd_symbol(yahoo) == expected


async def test_fetch_quote_normalises_na_and_timestamp(monkeypatch, isolated_cache, eodhd_key):
    async def fake_get(fn_name, path, params):
        assert path == "real-time/EUNL.XETRA"
        return {
            "code": "EUNL.XETRA", "timestamp": 1793016840, "open": 129.485, "high": "NA",
            "low": 129.375, "close": 130.075, "volume": 92471, "previousClose": 129.84,
        }

    monkeypatch.setattr(eodhd, "_get", fake_get)
    q = await eodhd.fetch_quote("EUNL.XETRA")
    assert q["price"] == 130.075
    assert q["day_high"] is None
    assert q["currency"] is None
    assert q["as_of"] == "2026-10-26"


async def test_fetch_quote_without_price_raises(monkeypatch, isolated_cache, eodhd_key):
    async def fake_get(fn_name, path, params):
        return {"code": "NOPE.XETRA", "timestamp": "NA", "close": "NA"}

    monkeypatch.setattr(eodhd, "_get", fake_get)
    with pytest.raises(RuntimeError, match="no price"):
        await eodhd.fetch_quote("NOPE.XETRA")


async def test_fetch_history_splits_warning_and_adjusts(monkeypatch, isolated_cache, eodhd_key):
    async def fake_get(fn_name, path, params):
        assert params["period"] == "w"
        return [
            {"warning": "Data is limited by one year as you have free subscription"},
            {"date": "2026-10-23", "open": 100, "high": 102, "low": 99, "close": 101,
             "adjusted_close": 50.5, "volume": 1000},
        ]

    monkeypatch.setattr(eodhd, "_get", fake_get)
    data = await eodhd.fetch_history("VTI.US", period="2y", interval="1wk")
    assert data["warning"].startswith("Data is limited")
    assert data["bars"] == [
        {"date": "2026-10-23", "open": 50.0, "high": 51.0, "low": 49.5, "close": 50.5, "volume": 1000}
    ]


async def test_http_error_does_not_leak_token(monkeypatch, isolated_cache, eodhd_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        eodhd.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(RuntimeError) as excinfo:
        await eodhd.fetch_quote("EUNL.XETRA")
    assert "403" in str(excinfo.value)
    assert "SECRET-TOKEN" not in str(excinfo.value)


def _yahoo_down(monkeypatch):
    async def failing(symbol, *a, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", failing)
    monkeypatch.setattr(yahoo_module, "fetch_history", failing)


async def test_quote_falls_back_to_eodhd_keeping_yahoo_symbol(monkeypatch, eodhd_key):
    _yahoo_down(monkeypatch)

    async def fake_eodhd_quote(symbol):
        assert symbol == "EUNL.XETRA"
        return {"symbol": symbol, "currency": None, "price": 130.0, "previous_close": 129.8,
                "open": None, "day_high": None, "day_low": None, "volume": None,
                "as_of": "2026-10-26"}

    monkeypatch.setattr(eodhd, "fetch_quote", fake_eodhd_quote)
    q = await fetch_one(symbol="EUNL.DE", isin=None)
    assert q.source == "eodhd"
    assert q.symbol == "EUNL.DE"
    assert q.price == 130.0


async def test_quote_eodhd_failure_falls_through_to_gettex(monkeypatch, eodhd_key):
    _yahoo_down(monkeypatch)

    async def failing_eodhd(symbol):
        raise RuntimeError("EODHD API call limit reached (free tier: 20/day)")

    async def fake_gettex(isin, resolved_symbol):
        return {"symbol": resolved_symbol, "currency": "EUR", "price": 55.5,
                "previous_close": None, "open": None, "day_high": None, "day_low": None,
                "volume": None, "as_of": "2026-10-26"}

    monkeypatch.setattr(eodhd, "fetch_quote", failing_eodhd)
    monkeypatch.setattr(quote_module, "_fetch_gettex", fake_gettex)
    q = await fetch_one(symbol="EUNL.DE", isin="IE00B4L5Y983")
    assert q.source == "justetf_gettex"


async def test_quote_no_key_never_calls_eodhd(monkeypatch):
    _yahoo_down(monkeypatch)

    async def must_not_run(symbol):
        raise AssertionError("EODHD called without a key")

    monkeypatch.setattr(eodhd, "fetch_quote", must_not_run)
    q = await fetch_one(symbol="EUNL.DE", isin=None)
    assert q.source == "error"
    assert "EODHD" not in q.error


async def test_history_falls_back_to_eodhd_with_note(monkeypatch, mcp_client, eodhd_key):
    _yahoo_down(monkeypatch)

    async def fake_eodhd_history(symbol, period="1y", interval="1d"):
        return {
            "bars": [{"date": "2026-10-23", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}],
            "warning": "Data is limited by one year as you have free subscription",
        }

    monkeypatch.setattr(eodhd, "fetch_history", fake_eodhd_history)
    result = await mcp_client.call_tool("get_history", {"symbol": "EUNL.DE", "period": "5y"})
    assert result.data.source == "eodhd"
    assert "one year" in result.data.note
    assert len(result.data.bars) == 1


async def test_history_both_failing_reports_both(monkeypatch, mcp_client, eodhd_key):
    _yahoo_down(monkeypatch)

    async def failing_eodhd(symbol, period="1y", interval="1d"):
        raise RuntimeError("EODHD returned HTTP 404")

    monkeypatch.setattr(eodhd, "fetch_history", failing_eodhd)
    result = await mcp_client.call_tool(
        "get_history", {"symbol": "EUNL.DE"}, raise_on_error=False
    )
    assert result.is_error
    text = result.content[0].text
    assert "connection reset" in text and "HTTP 404" in text
