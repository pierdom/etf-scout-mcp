"""P1-3 regression: get_etf_listings must collapse OpenFIGI's raw ~190-row
response down to a handful of actionable, deduplicated exchange listings and
resolve yahoo_symbol via the single shared exch_code->suffix map."""
from __future__ import annotations

from etf_scout_mcp.tools import etf_listings as etf_listings_module


def _composite_row(figi: str, ticker: str, exch_code: str, name: str) -> dict:
    return {
        "figi": figi,
        "composite_figi": figi,
        "share_class_figi": f"SC{figi}",
        "ticker": ticker,
        "name": name,
        "exch_code": exch_code,
        "security_type": "Common Stock",
        "market_sector": "Equity",
        "security_description": ticker,
    }


def _non_composite_variant(composite_figi: str, ticker: str, exch_code: str, name: str, n: int) -> dict:
    """A trade-reporting-venue row that isn't the exchange-level composite —
    this is what makes the real OpenFIGI response for a widely-listed ETF
    balloon to ~190 near-duplicate rows."""
    return {
        "figi": f"BBGVAR{n:04d}",
        "composite_figi": composite_figi,
        "share_class_figi": f"SC{composite_figi}",
        "ticker": ticker,
        "name": name,
        "exch_code": exch_code,
        "security_type": "Common Stock",
        "market_sector": "Equity",
        "security_description": ticker,
    }


def _iwda_fixture() -> list[dict]:
    """~190-row synthetic OpenFIGI response for IWDA (IE00B4L5Y983), shaped
    like the real /v3/mapping output: a handful of true exchange composites,
    each shadowed by a dozen-plus non-composite trade-venue duplicates."""
    venues = [
        ("BBG000BBQCY0", "EUNL", "GR", "ISHARES CORE MSCI WORLD"),
        ("BBG000BBQCX1", "IWDA", "NA", "ISHARES CORE MSCI WORLD"),
        ("BBG000BBQCZ2", "SWDA", "LN", "ISHARES CORE MSCI WORLD"),
        ("BBG000BBQCW3", "IWDA", "SW", "ISHARES CORE MSCI WORLD"),
    ]
    rows = []
    n = 0
    for composite_figi, ticker, exch_code, name in venues:
        rows.append(_composite_row(composite_figi, ticker, exch_code, name))
        for _ in range(47):  # 4 venues * (1 + 47) = 192 rows total
            n += 1
            rows.append(_non_composite_variant(composite_figi, ticker, exch_code, name, n))
    return rows


async def test_iwda_collapses_to_actionable_rows_with_yahoo_symbol(monkeypatch, mcp_client):
    fixture = _iwda_fixture()
    assert len(fixture) > 100  # sanity-check the fixture reproduces the bloat

    async def fake_fetch_listings(isin: str) -> list[dict]:
        return fixture

    monkeypatch.setattr(etf_listings_module, "fetch_listings", fake_fetch_listings)

    result = await mcp_client.call_tool("get_etf_listings", {"isin": "IE00B4L5Y983"})
    rows = result.data

    assert len(rows) <= 10
    by_exch = {r.exch_code: r for r in rows}
    assert by_exch["GR"].yahoo_symbol == "EUNL.DE"
    assert by_exch["NA"].yahoo_symbol == "IWDA.AS"
    # every row is a true composite — no leftover trade-venue duplicates
    assert len({(r.ticker, r.exch_code) for r in rows}) == len(rows)


async def test_primary_only_false_returns_raw_rows(monkeypatch, mcp_client):
    fixture = _iwda_fixture()

    async def fake_fetch_listings(isin: str) -> list[dict]:
        return fixture

    monkeypatch.setattr(etf_listings_module, "fetch_listings", fake_fetch_listings)

    result = await mcp_client.call_tool(
        "get_etf_listings", {"isin": "IE00B4L5Y983", "primary_only": False, "limit": 200}
    )
    rows = result.data

    # still deduplicated on (ticker, exch_code) even with primary_only off,
    # since every non-composite variant here shares its venue's ticker
    assert len(rows) == 4


async def test_unmapped_exchange_gets_null_yahoo_symbol(monkeypatch, mcp_client):
    fixture = [_composite_row("BBG000ZZZZZZ", "XYZ", "ZZ", "Some Fund")]

    async def fake_fetch_listings(isin: str) -> list[dict]:
        return fixture

    monkeypatch.setattr(etf_listings_module, "fetch_listings", fake_fetch_listings)

    result = await mcp_client.call_tool("get_etf_listings", {"isin": "IE00XXXXXXXX"})
    rows = result.data

    assert len(rows) == 1
    assert rows[0].yahoo_symbol is None
    assert rows[0].exch_code == "ZZ"
