"""FEAT-11 regression: bid/ask/spread/market_state are opt-in (include_book),
never fabricated, and never fetched when not requested."""
from __future__ import annotations

from etf_scout_mcp.tools import quote as quote_module
from etf_scout_mcp.tools.quote import fetch_one


def _yahoo_quote(symbol: str) -> dict:
    return {
        "symbol": symbol, "currency": "EUR", "price": 100.0, "previous_close": 99.5,
        "open": 99.8, "day_high": 100.5, "day_low": 99.7, "volume": 12345,
        "as_of": "2026-09-15",
    }


async def test_include_book_false_never_calls_fetch_quote_book(monkeypatch):
    call_count = 0

    async def fake_fetch_quote(symbol: str) -> dict:
        return _yahoo_quote(symbol)

    async def counting_fetch_quote_book(symbol: str) -> dict:
        nonlocal call_count
        call_count += 1
        return {"bid": 99.9, "ask": 100.1, "spread_bps": 20.0, "market_state": "REGULAR"}

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", fake_fetch_quote)
    monkeypatch.setattr(quote_module.yahoo, "fetch_quote_book", counting_fetch_quote_book)

    result = await fetch_one(symbol="VWCE.DE", isin=None, include_book=False)

    assert call_count == 0
    assert result.bid is None
    assert result.ask is None
    assert result.spread_bps is None
    assert result.market_state is None


async def test_include_book_true_populates_book_fields(monkeypatch):
    async def fake_fetch_quote(symbol: str) -> dict:
        return _yahoo_quote(symbol)

    async def fake_fetch_quote_book(symbol: str) -> dict:
        return {"bid": 99.9, "ask": 100.1, "spread_bps": 20.0, "market_state": "REGULAR"}

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", fake_fetch_quote)
    monkeypatch.setattr(quote_module.yahoo, "fetch_quote_book", fake_fetch_quote_book)

    result = await fetch_one(symbol="VWCE.DE", isin=None, include_book=True)

    assert result.bid == 99.9
    assert result.ask == 100.1
    assert result.spread_bps == 20.0
    assert result.market_state == "REGULAR"


async def test_book_fetch_failure_does_not_fail_the_quote(monkeypatch):
    async def fake_fetch_quote(symbol: str) -> dict:
        return _yahoo_quote(symbol)

    async def failing_fetch_quote_book(symbol: str) -> dict:
        raise RuntimeError("book endpoint down")

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", fake_fetch_quote)
    monkeypatch.setattr(quote_module.yahoo, "fetch_quote_book", failing_fetch_quote_book)

    result = await fetch_one(symbol="VWCE.DE", isin=None, include_book=True)

    assert result.error is None
    assert result.price == 100.0
    assert result.bid is None


async def test_gettex_fallback_never_carries_book_data(monkeypatch):
    async def failing_fetch_quote(symbol: str) -> dict:
        raise RuntimeError("yahoo down")

    async def fake_gettex(isin: str, resolved_symbol: str) -> dict:
        return {
            "symbol": resolved_symbol, "currency": "EUR", "price": 55.5,
            "previous_close": None, "open": None, "day_high": None,
            "day_low": None, "volume": None, "as_of": "2026-09-15",
        }

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", failing_fetch_quote)
    monkeypatch.setattr(quote_module, "_fetch_gettex", fake_gettex)

    result = await fetch_one(symbol="EUNL.DE", isin="IE00B4L5Y983", include_book=True)

    assert result.source == "justetf_gettex"
    assert result.bid is None
    assert result.ask is None


def test_spread_bps_math_in_source_layer(monkeypatch):
    """Direct unit check of the spread computation via the source-layer
    helper, independent of yfinance's live .info shape."""
    import asyncio

    import etf_scout_mcp.sources.yahoo as yahoo_source

    class _FakeTicker:
        @property
        def info(self):
            return {"bid": 99.0, "ask": 101.0, "marketState": "REGULAR"}

    monkeypatch.setattr(yahoo_source, "_ticker", lambda symbol: _FakeTicker())

    result = asyncio.run(yahoo_source.fetch_quote_book.__wrapped__("VWCE.DE"))

    assert result["bid"] == 99.0
    assert result["ask"] == 101.0
    # (101-99)/100 * 10000 = 200 bps
    assert result["spread_bps"] == 200.0
    assert result["market_state"] == "REGULAR"
