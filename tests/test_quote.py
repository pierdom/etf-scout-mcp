"""BUG-4 regression: get_quote must never return a null price without an error,
and must never fabricate as_of. Also covers Quote/QuoteResult schema parity."""
from __future__ import annotations

from etf_scout_mcp.tools import quote as quote_module
from etf_scout_mcp.tools.batch_quote import QuoteResult
from etf_scout_mcp.tools.quote import Quote, fetch_one


async def test_unresolvable_symbol_has_honest_error(monkeypatch):
    """Repro: get_quote(symbol='NOTAREALTICKER.XX') used to return every field
    null, source='yahoo', as_of=today (fabricated), and no error at all."""

    async def fake_fetch_quote(symbol: str) -> dict:
        return {
            "symbol": symbol,
            "currency": None,
            "price": None,
            "previous_close": None,
            "open": None,
            "day_high": None,
            "day_low": None,
            "volume": None,
            "as_of": None,
        }

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", fake_fetch_quote)

    result = await fetch_one(symbol="NOTAREALTICKER.XX", isin=None)

    assert result.as_of is None
    assert result.price is None
    assert result.source == "error"
    assert result.error
    assert "not recognized" in result.error.lower()


async def test_no_data_returned_message_differs_from_not_found(monkeypatch):
    """A ticker Yahoo recognises (currency present) but has no price for right
    now (closed market / halted) must get a different message than an
    unrecognised ticker."""

    async def fake_fetch_quote(symbol: str) -> dict:
        return {
            "symbol": symbol,
            "currency": "EUR",
            "price": None,
            "previous_close": 45.12,
            "open": None,
            "day_high": None,
            "day_low": None,
            "volume": None,
            "as_of": None,
        }

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", fake_fetch_quote)

    result = await fetch_one(symbol="VWCE.DE", isin=None)

    assert result.as_of is None
    assert result.source == "error"
    assert "market may be closed" in result.error.lower()
    assert "not recognized" not in result.error.lower()


async def test_yahoo_hard_failure_falls_back_to_gettex(monkeypatch):
    async def failing_fetch_quote(symbol: str) -> dict:
        raise RuntimeError("connection reset")

    async def fake_gettex(isin: str, resolved_symbol: str) -> dict:
        return {
            "symbol": resolved_symbol,
            "currency": "EUR",
            "price": 55.5,
            "previous_close": None,
            "open": None,
            "day_high": None,
            "day_low": None,
            "volume": None,
            "as_of": "2026-09-15",
        }

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", failing_fetch_quote)
    monkeypatch.setattr(quote_module, "_fetch_gettex", fake_gettex)

    result = await fetch_one(symbol="EUNL.DE", isin="IE00B4L5Y983")

    assert result.source == "justetf_gettex"
    assert result.error is None
    assert result.price == 55.5
    assert result.as_of == "2026-09-15"


async def test_gettex_fallback_also_failing_surfaces_both_errors(monkeypatch):
    async def failing_fetch_quote(symbol: str) -> dict:
        raise RuntimeError("yahoo down")

    async def failing_gettex(isin: str, resolved_symbol: str) -> dict:
        raise RuntimeError("gettex down too")

    monkeypatch.setattr(quote_module.yahoo, "fetch_quote", failing_fetch_quote)
    monkeypatch.setattr(quote_module, "_fetch_gettex", failing_gettex)

    result = await fetch_one(symbol="EUNL.DE", isin="IE00B4L5Y983")

    assert result.source == "error"
    assert result.as_of is None
    assert "yahoo down" in result.error
    assert "gettex down too" in result.error


async def test_unresolvable_isin_returns_error_not_exception(monkeypatch):
    async def fake_resolve(isin: str) -> None:
        return None

    monkeypatch.setattr(quote_module, "resolve_yahoo_ticker", fake_resolve)

    result = await fetch_one(symbol=None, isin="IE00XXXXXXXX")

    assert result.source == "error"
    assert result.as_of is None
    assert "could not resolve" in result.error.lower()


def test_quote_and_quoteresult_schema_parity():
    """get_quote and get_quotes must return the identical row schema."""
    quote_fields = set(Quote.model_fields.keys())
    result_fields = set(QuoteResult.model_fields.keys())
    assert result_fields - quote_fields == {"requested"}
    assert quote_fields <= result_fields
    assert "market_cap" not in quote_fields
