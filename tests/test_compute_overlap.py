"""FEAT-10 regression: compute_overlap sums min(weight_a, weight_b) over shared
top-10 holdings, always flags approximate, and degrades to an error field
rather than raising when a lookup fails."""
from __future__ import annotations

from etf_scout_mcp.tools import compute_overlap as compute_overlap_module


def _profile(top_holdings: list[dict]) -> dict:
    return {"isin": "X", "top_holdings": top_holdings}


async def test_overlap_sums_min_weight_over_shared_holdings(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str) -> dict:
        if isin == "FUND_A":
            return _profile([
                {"name": "Apple Inc", "isin": "US0378331005", "weight": 5.0},
                {"name": "Microsoft Corp", "isin": "US5949181045", "weight": 4.0},
                {"name": "Only In A", "isin": "US0000000001", "weight": 1.0},
            ])
        return _profile([
            {"name": "Apple Inc", "isin": "US0378331005", "weight": 3.0},
            {"name": "Microsoft Corp", "isin": "US5949181045", "weight": 6.0},
            {"name": "Only In B", "isin": "US0000000002", "weight": 2.0},
        ])

    monkeypatch.setattr(compute_overlap_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool("compute_overlap", {"isin_a": "FUND_A", "isin_b": "FUND_B"})
    data = result.data

    # min(5,3) + min(4,6) = 3 + 4 = 7
    assert abs(data.overlap_pct - 7.0) < 0.001
    assert len(data.shared_holdings) == 2
    assert data.approximate is True
    assert data.holdings_compared_a == 3
    assert data.holdings_compared_b == 3
    assert data.error is None


async def test_no_overlap_when_no_shared_holdings(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str) -> dict:
        if isin == "FUND_A":
            return _profile([{"name": "Only In A", "isin": "US0000000001", "weight": 10.0}])
        return _profile([{"name": "Only In B", "isin": "US0000000002", "weight": 10.0}])

    monkeypatch.setattr(compute_overlap_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool("compute_overlap", {"isin_a": "FUND_A", "isin_b": "FUND_B"})
    data = result.data

    assert data.overlap_pct == 0.0
    assert data.shared_holdings == []


async def test_failed_lookup_returns_error_field_not_exception(monkeypatch, mcp_client):
    async def flaky_fetch_profile(isin: str) -> dict:
        if isin == "BROKEN":
            raise RuntimeError("justETF timed out")
        return _profile([])

    monkeypatch.setattr(compute_overlap_module, "fetch_profile", flaky_fetch_profile)

    result = await mcp_client.call_tool("compute_overlap", {"isin_a": "OK", "isin_b": "BROKEN"})
    data = result.data

    assert data.overlap_pct is None
    assert data.error is not None
    assert "justETF timed out" in data.error
