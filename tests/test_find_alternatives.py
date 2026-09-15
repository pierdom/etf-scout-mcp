"""FEAT-9 regression: find_alternatives excludes the source ISIN, ranks by TER,
and degrades to an error field rather than raising on a bad/indexless ISIN."""
from __future__ import annotations

from etf_scout_mcp.tools import find_alternatives as find_alternatives_module


async def test_excludes_source_isin_and_reports_ranked_by(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str) -> dict:
        return {"isin": isin, "index": "MSCI World"}

    async def fake_fetch_screener(**kwargs) -> list[dict]:
        assert kwargs["query"] == "MSCI World"
        assert kwargs["sort_by"] == "ter"
        return [
            {"isin": "IE00B4L5Y983", "name": "Source Fund", "ter": 0.002},  # the source itself
            {"isin": "IE00BJ0KDQ92", "name": "Cheaper Alt", "ter": 0.0012},
            {"isin": "IE00B60SX394", "name": "Pricier Alt", "ter": 0.0025},
        ]

    monkeypatch.setattr(find_alternatives_module, "fetch_profile", fake_fetch_profile)
    monkeypatch.setattr(find_alternatives_module, "fetch_screener", fake_fetch_screener)

    result = await mcp_client.call_tool(
        "find_alternatives", {"isin": "IE00B4L5Y983", "limit": 10}
    )
    data = result.data

    assert data.index == "MSCI World"
    assert data.ranked_by == "ter"
    assert [a.isin for a in data.alternatives] == ["IE00BJ0KDQ92", "IE00B60SX394"]
    assert data.error is None


async def test_respects_limit_after_excluding_source(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str) -> dict:
        return {"isin": isin, "index": "MSCI World"}

    async def fake_fetch_screener(**kwargs) -> list[dict]:
        return [
            {"isin": "SOURCE", "name": "Source Fund", "ter": 0.002},
            {"isin": "A", "name": "Alt A", "ter": 0.001},
            {"isin": "B", "name": "Alt B", "ter": 0.0015},
            {"isin": "C", "name": "Alt C", "ter": 0.002},
        ]

    monkeypatch.setattr(find_alternatives_module, "fetch_profile", fake_fetch_profile)
    monkeypatch.setattr(find_alternatives_module, "fetch_screener", fake_fetch_screener)

    result = await mcp_client.call_tool("find_alternatives", {"isin": "SOURCE", "limit": 2})
    data = result.data

    assert len(data.alternatives) == 2
    assert [a.isin for a in data.alternatives] == ["A", "B"]


async def test_no_index_returns_error_not_exception(monkeypatch, mcp_client):
    async def fake_fetch_profile(isin: str) -> dict:
        return {"isin": isin, "index": None}

    monkeypatch.setattr(find_alternatives_module, "fetch_profile", fake_fetch_profile)

    result = await mcp_client.call_tool("find_alternatives", {"isin": "IE00NOINDEX0"})
    data = result.data

    assert data.alternatives == []
    assert data.error is not None
    assert "no index" in data.error.lower()


async def test_failed_profile_lookup_returns_error(monkeypatch, mcp_client):
    async def failing_fetch_profile(isin: str) -> dict:
        raise RuntimeError("justETF timed out")

    monkeypatch.setattr(find_alternatives_module, "fetch_profile", failing_fetch_profile)

    result = await mcp_client.call_tool("find_alternatives", {"isin": "IE00XXXXXXXX"})
    data = result.data

    assert data.error is not None
    assert "justETF timed out" in data.error
