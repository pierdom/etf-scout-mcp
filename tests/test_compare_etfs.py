"""BUG-5 regression: compare_etfs must return one row per requested ISIN,
never silently drop an unresolvable one."""
from __future__ import annotations

from etf_scout_mcp.tools import etf_compare


def _fake_summary(isin: str) -> dict:
    return {
        "isin": isin,
        "name": f"Fund {isin}",
        "ticker": None,
        "fund_provider": None,
        "fund_domicile": None,
        "fund_size_eur": None,
        "ter": 0.002,
        "replication": None,
        "distribution_policy": None,
        "currency_hedged": False,
        "sustainability": False,
        "inception_date": None,
        "return_1y": None,
        "return_3y": None,
        "return_5y": None,
        "volatility_1y": None,
    }


async def test_compare_etfs_keeps_bogus_isin_with_error(monkeypatch, mcp_client):
    """Repro: 3 valid ISINs + 1 bogus one used to come back as 3 rows with no
    indication which vanished. Must come back as 4 rows, bogus one carrying
    `error`."""

    valid = {"IE00B4L5Y983", "IE00BK5BQT80", "IE00B60SX394"}
    bogus = "IE00XXXXXXXX"

    async def fake_fetch_summary(isin: str) -> dict | None:
        return _fake_summary(isin) if isin in valid else None

    monkeypatch.setattr(etf_compare, "fetch_summary", fake_fetch_summary)

    result = await mcp_client.call_tool(
        "compare_etfs",
        {"isins": ["IE00B4L5Y983", "IE00BK5BQT80", "IE00B60SX394", bogus]},
    )
    rows = result.data

    assert len(rows) == 4
    by_isin = {r.isin: r for r in rows}
    assert by_isin[bogus].error is not None
    assert "not found" in by_isin[bogus].error.lower()
    for isin in valid:
        assert by_isin[isin].error is None
        assert by_isin[isin].name == f"Fund {isin}"


async def test_compare_etfs_surfaces_fetch_exception_as_error(monkeypatch, mcp_client):
    async def flaky_fetch_summary(isin: str) -> dict:
        if isin == "IE00BROKEN000":
            raise RuntimeError("justETF timed out")
        return _fake_summary(isin)

    monkeypatch.setattr(etf_compare, "fetch_summary", flaky_fetch_summary)

    result = await mcp_client.call_tool(
        "compare_etfs", {"isins": ["IE00B4L5Y983", "IE00BROKEN000"]}
    )
    rows = result.data

    assert len(rows) == 2
    by_isin = {r.isin: r for r in rows}
    assert by_isin["IE00B4L5Y983"].error is None
    assert "justETF timed out" in by_isin["IE00BROKEN000"].error
