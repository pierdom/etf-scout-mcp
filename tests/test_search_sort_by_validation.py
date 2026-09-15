"""Regression: search_etfs silently ignored an invalid sort_by (typo'd or
hallucinated value fell back to default order with no signal to the
caller) — same class of gap get_history's period/interval validation
already covers elsewhere in this codebase."""
from __future__ import annotations


async def test_invalid_sort_by_rejected_with_clear_message(mcp_client):
    result = await mcp_client.call_tool(
        "search_etfs", {"sort_by": "bogus_sort_value", "limit": 3}, raise_on_error=False
    )
    assert result.is_error
    text = result.content[0].text.lower()
    assert "bogus_sort_value" in text
    assert "ter" in text  # lists valid values


async def test_valid_sort_by_values_all_accepted(monkeypatch, mcp_client):
    from etf_scout_mcp.tools import search as search_module

    async def fake_fetch_screener(**kwargs):
        return []

    monkeypatch.setattr(search_module, "fetch_screener", fake_fetch_screener)

    for value in ("ter", "fund_size", "return_1y", "return_3y", "return_5y"):
        result = await mcp_client.call_tool(
            "search_etfs", {"sort_by": value, "limit": 3}, raise_on_error=False
        )
        assert not result.is_error, f"{value!r} should be accepted"


async def test_none_sort_by_still_accepted(monkeypatch, mcp_client):
    from etf_scout_mcp.tools import search as search_module

    async def fake_fetch_screener(**kwargs):
        return []

    monkeypatch.setattr(search_module, "fetch_screener", fake_fetch_screener)

    result = await mcp_client.call_tool("search_etfs", {"limit": 3}, raise_on_error=False)
    assert not result.is_error
