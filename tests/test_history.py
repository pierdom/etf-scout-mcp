"""P1-4 regression: get_history must respect max_rows via auto-coarsening
(never silent truncation), support summary=True, and validate period/interval."""
from __future__ import annotations

from etf_scout_mcp.sources import yahoo as yahoo_module


def _daily_bars(n: int, start_price: float = 100.0) -> list[dict]:
    from datetime import date, timedelta

    bars = []
    price = start_price
    d = date(2024, 1, 1)
    for i in range(n):
        price *= 1.0005
        bars.append(
            {
                "date": (d + timedelta(days=i)).isoformat(),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000 + i,
            }
        )
    return bars


async def test_max_rows_triggers_coarsening_not_truncation(monkeypatch, mcp_client):
    """1300 daily bars with max_rows=400 must come back coarsened to weekly
    (or coarser), with downsampled_from set — never just the first/last 400
    daily bars silently cut."""

    calls: list[str] = []

    async def fake_fetch_history(symbol, period="1y", interval="1d"):
        calls.append(interval)
        if interval == "1d":
            return _daily_bars(1300)
        if interval == "1wk":
            return _daily_bars(186)  # 1300/7 ish
        return _daily_bars(43)  # 1mo

    monkeypatch.setattr(yahoo_module, "fetch_history", fake_fetch_history)

    result = await mcp_client.call_tool(
        "get_history", {"symbol": "VWCE.DE", "period": "5y", "interval": "1d", "max_rows": 400}
    )
    data = result.data

    assert data.downsampled_from == "1d"
    assert data.interval == "1wk"
    assert len(data.bars) <= 400
    assert calls == ["1d", "1wk"]  # coarsened exactly once, no wasted extra calls


async def test_no_coarsening_when_under_budget(monkeypatch, mcp_client):
    async def fake_fetch_history(symbol, period="1y", interval="1d"):
        return _daily_bars(200)

    monkeypatch.setattr(yahoo_module, "fetch_history", fake_fetch_history)

    result = await mcp_client.call_tool(
        "get_history", {"symbol": "VWCE.DE", "period": "1y", "max_rows": 400}
    )
    data = result.data

    assert data.downsampled_from is None
    assert data.interval == "1d"
    assert len(data.bars) == 200


async def test_coarsening_stops_at_monthly_even_if_still_over_budget(monkeypatch, mcp_client):
    async def fake_fetch_history(symbol, period="1y", interval="1d"):
        return _daily_bars(50)  # even "monthly" data here exceeds a tiny max_rows

    monkeypatch.setattr(yahoo_module, "fetch_history", fake_fetch_history)

    result = await mcp_client.call_tool(
        "get_history", {"symbol": "VWCE.DE", "period": "1y", "max_rows": 10}
    )
    data = result.data

    assert data.interval == "1mo"  # coarsened all the way, then gave up rather than truncate
    assert len(data.bars) == 50  # full series returned, not cut to 10


async def test_summary_mode_returns_stats_not_bars(monkeypatch, mcp_client):
    async def fake_fetch_history(symbol, period="1y", interval="1d"):
        return _daily_bars(30)

    monkeypatch.setattr(yahoo_module, "fetch_history", fake_fetch_history)

    result = await mcp_client.call_tool(
        "get_history", {"symbol": "VWCE.DE", "period": "1mo", "summary": True}
    )
    data = result.data

    assert not hasattr(data, "bars")
    assert data.bar_count == 30
    assert data.total_return_pct is not None
    assert data.first_date == "2024-01-01"
    assert data.max_drawdown_pct is not None
    assert data.max_drawdown_pct <= 0


async def test_summary_mode_empty_series(monkeypatch, mcp_client):
    async def fake_fetch_history(symbol, period="1y", interval="1d"):
        return []

    monkeypatch.setattr(yahoo_module, "fetch_history", fake_fetch_history)

    result = await mcp_client.call_tool(
        "get_history", {"symbol": "NOTAREAL.XX", "summary": True}
    )
    data = result.data

    assert data.bar_count == 0
    assert data.total_return_pct is None
    assert data.first_date is None


async def test_invalid_period_rejected_with_clear_message(mcp_client):
    result = await mcp_client.call_tool(
        "get_history", {"symbol": "VWCE.DE", "period": "18mo"}, raise_on_error=False
    )
    assert result.is_error
    text = result.content[0].text.lower()
    assert "18mo" in text
    assert "1mo" in text  # lists valid values


async def test_invalid_interval_rejected_with_clear_message(mcp_client):
    result = await mcp_client.call_tool(
        "get_history", {"symbol": "VWCE.DE", "interval": "4h"}, raise_on_error=False
    )
    assert result.is_error
    text = result.content[0].text.lower()
    assert "4h" in text
