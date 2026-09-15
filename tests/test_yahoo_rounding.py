"""BUG-7 regression: all quote/history monetary fields round to 4dp
consistently — previously only price/previous_close looked rounded (an
artifact of Yahoo's own tick-size rounding) while open/day_high/day_low
leaked raw float64 noise."""
from __future__ import annotations

from types import SimpleNamespace

from etf_scout_mcp.sources import yahoo as yahoo_module


async def test_fetch_quote_rounds_all_monetary_fields(monkeypatch):
    fake_info = SimpleNamespace(
        currency="USD",
        last_price=166.06,
        previous_close=165.5,
        open=166.05999755859375,
        day_high=167.30000305175781,
        day_low=165.90000152587890625,
        three_month_average_volume=1_234_567,
    )

    def fake_ticker(symbol):
        return SimpleNamespace(fast_info=fake_info)

    monkeypatch.setattr(yahoo_module, "_ticker", fake_ticker)

    data = await yahoo_module.fetch_quote.__wrapped__("CSPX.L")

    assert data["open"] == 166.06
    assert data["day_high"] == 167.3
    assert data["day_low"] == 165.9
    assert data["as_of"] is not None  # price was present


async def test_fetch_quote_as_of_null_when_price_missing(monkeypatch):
    fake_info = SimpleNamespace(
        currency=None,
        last_price=None,
        previous_close=None,
        open=None,
        day_high=None,
        day_low=None,
        three_month_average_volume=None,
    )

    def fake_ticker(symbol):
        return SimpleNamespace(fast_info=fake_info)

    monkeypatch.setattr(yahoo_module, "_ticker", fake_ticker)

    data = await yahoo_module.fetch_quote.__wrapped__("NOTAREALTICKER.XX")

    assert data["price"] is None
    assert data["as_of"] is None
    assert "market_cap" not in data
