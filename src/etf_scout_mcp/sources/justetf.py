"""justETF source — wraps justetf-scraping for profiles, overviews, and screener."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

import pandas as pd
import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter

import justetf_scraping
from justetf_scraping.overview import load_overview

from etf_scout_mcp.cache import cached
from etf_scout_mcp.config import config

# ---------------------------------------------------------------------------
# Force a 30 s timeout on every requests.Session that justetf-scraping
# creates internally. The library doesn't expose a session parameter, so
# patching Session.__init__ is the least-invasive option. Without this,
# a slow or unresponsive justETF server blocks threads indefinitely, which
# starves the asyncio thread pool and causes unrelated tools to hang too.
# ---------------------------------------------------------------------------

class _TimeoutAdapter(_HTTPAdapter):
    def send(self, request, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", 30)
        return super().send(request, **kwargs)

_orig_session_init = _requests.Session.__init__

def _patched_session_init(self, *args, **kwargs):  # type: ignore[misc]
    _orig_session_init(self, *args, **kwargs)
    self.mount("https://", _TimeoutAdapter())
    self.mount("http://", _TimeoutAdapter())

_requests.Session.__init__ = _patched_session_init  # type: ignore[method-assign]

# ---------------------------------------------------------------------------
# Logging — same rotating-file pattern as sources/yahoo.py
# ---------------------------------------------------------------------------

_log = logging.getLogger("etf_scout_mcp.justetf")


def _ensure_log_handler() -> None:
    if _log.handlers:
        return
    config.cache_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = config.cache_path.parent / "calls.log"
    handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _log.addHandler(handler)
    _log.setLevel(config.log_level)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Ordered longest-first so compound names match before their single-word prefix.
_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("BNP Paribas", "BNP Paribas"),
    ("Legal & General", "Legal & General"),
    ("First Trust", "First Trust"),
    ("Global X", "Global X"),
    ("Goldman Sachs", "Goldman Sachs"),
    ("State Street SPDR", "SPDR"),
    ("State Street", "SPDR"),
    ("Franklin Templeton", "Franklin Templeton"),
    ("Van Eck", "VanEck"),
    ("VanEck", "VanEck"),
    ("JPMorgan", "JPMorgan"),
    ("J.P. Morgan", "JPMorgan"),
    ("JP Morgan", "JPMorgan"),
    ("WisdomTree", "WisdomTree"),
    ("iShares", "iShares"),
    ("Vanguard", "Vanguard"),
    ("Xtrackers", "Xtrackers"),
    ("Amundi", "Amundi"),
    ("Invesco", "Invesco"),
    ("Franklin", "Franklin Templeton"),
    ("Fidelity", "Fidelity"),
    ("Lyxor", "Lyxor"),
    ("Ossiam", "Ossiam"),
    ("HANetf", "HANetf"),
    ("Tabula", "Tabula"),
    ("Robeco", "Robeco"),
    ("Pictet", "Pictet"),
    ("Natixis", "Natixis"),
    ("Nikko", "Nikko"),
    ("Mirae", "Mirae"),
    ("Rize", "Rize"),
    ("SPDR", "SPDR"),
    ("PIMCO", "PIMCO"),
    ("HSBC", "HSBC"),
    ("UBS", "UBS"),
    ("AXA", "AXA"),
    ("DWS", "DWS"),
]


def _extract_provider(name: str | None) -> str | None:
    """Infer fund provider from the ETF name prefix."""
    if not name:
        return None
    name_lower = name.lower()
    for prefix, canonical in _PROVIDER_PREFIXES:
        if name_lower.startswith(prefix.lower()):
            return canonical
    return None


_DATE_FORMATS = (
    "%d %B %Y",    # "25 September 2009"
    "%d/%m/%Y",    # "31/03/2026"
    "%Y-%m-%d",    # already ISO
)


def _parse_date(value: str | None) -> str | None:
    if not value or value == "-":
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return value  # return as-is if unparseable


def _safe_float(value: Any) -> float | None:
    """Return None for NaN/None, else round to 6 decimal places."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


def _round_money(value: Any) -> float | None:
    """Return None for NaN/None, else round to 4 decimal places (monetary fields)."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _ter_to_decimal(pct: Any) -> float | None:
    """Convert TER from percent (library) to decimal (our convention).
    0.20 (percent) → 0.002 (decimal, 20 bps).
    """
    f = _safe_float(pct)
    return round(f / 100, 8) if f is not None else None


# justETF renders missing data as one of these placeholder strings rather than
# omitting the field — normalise them to None so callers never see them leak through.
_PLACEHOLDER_STRINGS = {"-", "n/a", "–"}


def _normalise(value: Any) -> str | None:
    """Normalise a scraped string field: map justETF's placeholder tokens to None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in _PLACEHOLDER_STRINGS:
        return None
    return s


def _today() -> str:
    """The current UTC date, captured once per upstream fetch (not per cache
    read) so data_as_of reflects when the data was actually scraped."""
    return datetime.now(timezone.utc).date().isoformat()


def _annualise(cumulative_pct: float | None, years: float) -> float | None:
    """Convert a cumulative return over *years* to an annualised (CAGR) one.
    (1y is skipped by callers — cumulative and annualised are identical when
    years == 1, so a separate field would just duplicate return_1y.)
    """
    if cumulative_pct is None:
        return None
    try:
        factor = (1 + cumulative_pct / 100) ** (1 / years)
    except (ValueError, ZeroDivisionError):
        return None
    return round((factor - 1) * 100, 4)


# Best-effort leverage detection from fund name — justETF's screener has no
# leverage column (only a strategy filter, "long-only" vs "short & leveraged",
# and leveraged-long products are still categorised "long-only"). Never
# treated as ground truth: leverage_factor is only set on a confident numeric
# match, never fabricated as 1.0 for an undetected fund.
_LEVERAGE_FACTOR_PATTERN = re.compile(r"(?<![\w.])(\d(?:\.\d)?)\s*x\b", re.IGNORECASE)
_LEVERAGE_KEYWORDS = ("leveraged", "short & leveraged", "daily short", "ultra short", "ultra long", "inverse")


def _detect_leverage_factor(name: str | None) -> float | None:
    if not name:
        return None
    match = _LEVERAGE_FACTOR_PATTERN.search(name)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _is_leveraged(name: str | None) -> bool:
    if not name:
        return False
    if _detect_leverage_factor(name) is not None:
        return True
    name_lower = name.lower()
    return any(kw in name_lower for kw in _LEVERAGE_KEYWORDS)


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


@cached(ttl_key="profile")
async def fetch_profile(isin: str) -> dict[str, Any] | None:
    """Fetch full ETF profile for *isin* from justETF.

    Returns a dict suitable for building an EtfProfile model, or None if
    *isin* doesn't resolve to a real fund on justETF. Also merges in
    return_1y/3y/5y (+ 3y/5y annualised) from the screener scrape
    (fetch_summary) — the profile scrape itself has no return fields.

    TER is decimal (0.002 = 0.20%). fund_size_eur is in EUR (not millions).
    """
    def _inner() -> dict[str, Any] | None:
        _ensure_log_handler()
        t0 = time.monotonic()
        try:
            ov = justetf_scraping.get_etf_overview(isin, include_gettex=False, expand_allocations=True)
        except Exception as exc:
            _log.warning("error fn=get_etf_overview isin=%s latency=%.3fs error=%r", isin, time.monotonic() - t0, exc)
            raise
        # get_etf_overview doesn't raise for an ISIN that doesn't exist on
        # justETF — it silently parses whatever page comes back (observed:
        # justETF's generic "ETF Screener" page). A real fund profile always
        # has at least these three; their joint absence is the signal that
        # this isn't a real fund page rather than a fund missing this data.
        if ov.get("ter") is None and ov.get("fund_size_eur") is None and ov.get("inception_date") is None:
            _log.warning("not_found fn=get_etf_overview isin=%s latency=%.3fs (no ter/fund_size/inception_date)", isin, time.monotonic() - t0)
            return None
        _log.info("ok fn=get_etf_overview isin=%s latency=%.3fs", isin, time.monotonic() - t0)
        return {
            "isin": ov["isin"],
            "name": _normalise(ov.get("name")),
            "description": _normalise(ov.get("description")),
            "index": _normalise(ov.get("index")),
            "investment_focus": _normalise(ov.get("investment_focus")),
            # fund_size from library is in EUR millions → convert to EUR
            "fund_size_eur": _round_money(ov["fund_size_eur"] * 1_000_000) if ov.get("fund_size_eur") else None,
            "ter": _ter_to_decimal(ov.get("ter")),
            "replication": _normalise(ov.get("replication")),
            "distribution_policy": _normalise(ov.get("distribution_policy")),
            "distribution_frequency": _normalise(ov.get("distribution_frequency")),
            "fund_currency": _normalise(ov.get("fund_currency")),
            "currency_hedged": ov.get("currency_hedged"),
            "fund_domicile": _normalise(ov.get("fund_domicile")),
            "fund_provider": _normalise(ov.get("fund_provider")),
            "legal_structure": _normalise(ov.get("legal_structure")),
            "sustainability": ov.get("sustainability"),
            "volatility_1y": _safe_float(ov.get("volatility_1y")),
            "inception_date": _parse_date(ov.get("inception_date")),
            "holdings_date": _parse_date(ov.get("holdings_date")),
            "top_holdings": [
                {
                    "name": _normalise(h["name"]),
                    "isin": _normalise(h.get("isin")),
                    "weight": _safe_float(h["percentage"]),
                }
                for h in (ov.get("top_holdings") or [])
            ],
            "countries": [
                {"name": _normalise(c["name"]), "weight": _safe_float(c["percentage"])}
                for c in (ov.get("countries") or [])
            ],
            "sectors": [
                {"name": _normalise(s["name"]), "weight": _safe_float(s["percentage"])}
                for s in (ov.get("sectors") or [])
            ],
        }

    try:
        profile = await asyncio.wait_for(asyncio.to_thread(_inner), timeout=45.0)
    except asyncio.TimeoutError:
        _log.warning("timeout fn=get_etf_overview isin=%s after 45s", isin)
        raise RuntimeError(f"justETF request timed out for {isin}") from None

    if profile is None:
        return None

    profile["data_as_of"] = _today()

    try:
        summary = await fetch_summary(isin)
    except Exception as exc:
        _log.warning("error fn=fetch_summary(for profile returns) isin=%s error=%r", isin, exc)
        summary = None

    profile["return_1y"] = summary.get("return_1y") if summary else None
    profile["return_3y"] = summary.get("return_3y") if summary else None
    profile["return_5y"] = summary.get("return_5y") if summary else None
    profile["return_3y_annualised_pct"] = _annualise(profile["return_3y"], 3)
    profile["return_5y_annualised_pct"] = _annualise(profile["return_5y"], 5)

    return profile


def _row_get(row: Any, key: str) -> Any:
    """row.get(key), normalising pandas' NA sentinels (NaN, pd.NA, pd.NaT) to
    plain None. pd.NA in particular raises TypeError on a bare bool()/if
    check ("boolean value of NA is ambiguous") — routing every field through
    this first means every downstream helper can use plain Python
    truthiness safely instead of each needing its own NA guard."""
    value = row.get(key)
    if value is None:
        return None
    try:
        is_na = pd.isna(value)
    except (TypeError, ValueError):
        return value
    return None if is_na else value


def _row_to_summary(isin: str, row: Any, data_as_of: str) -> dict[str, Any]:
    """Convert a load_overview DataFrame row to a summary dict."""
    inc = _row_get(row, "inception_date")
    return_3y = _safe_float(_row_get(row, "last_three_years"))
    return_5y = _safe_float(_row_get(row, "last_five_years"))
    name = _row_get(row, "name")
    size = _safe_float(_row_get(row, "size"))
    hedged = _row_get(row, "hedged")
    is_sustainable = _row_get(row, "is_sustainable")
    return {
        "isin": isin,
        "name": _normalise(name),
        "ticker": _normalise(_row_get(row, "ticker")),
        "fund_provider": _extract_provider(name),
        "fund_domicile": _normalise(_row_get(row, "domicile_country")),
        "fund_currency": _normalise(_row_get(row, "currency")),
        "fund_size_eur": _round_money(size * 1_000_000) if size is not None else None,
        "ter": _ter_to_decimal(_row_get(row, "ter")),
        "replication": _normalise(_row_get(row, "replication")),
        "distribution_policy": _normalise(_row_get(row, "dividends")),
        "currency_hedged": bool(hedged) if hedged is not None else None,
        "sustainability": bool(is_sustainable) if is_sustainable is not None else None,
        "inception_date": inc.date().isoformat() if hasattr(inc, "date") else None,
        "return_1y": _safe_float(_row_get(row, "last_year")),
        "return_3y": return_3y,
        "return_5y": return_5y,
        "return_3y_annualised_pct": _annualise(return_3y, 3),
        "return_5y_annualised_pct": _annualise(return_5y, 5),
        "volatility_1y": _safe_float(_row_get(row, "last_year_volatility")),
        "leverage_factor": _detect_leverage_factor(name),
        "data_as_of": data_as_of,
    }


@cached(ttl_key="profile")
async def fetch_summary(isin: str) -> dict[str, Any] | None:
    """Fetch a lightweight summary row for a single ETF by ISIN from justETF."""
    def _inner() -> dict[str, Any] | None:
        _ensure_log_handler()
        t0 = time.monotonic()
        try:
            df = load_overview(isin=isin)
        except Exception as exc:
            _log.warning("error fn=load_overview isin=%s latency=%.3fs error=%r", isin, time.monotonic() - t0, exc)
            raise
        _log.info("ok fn=load_overview isin=%s rows=%d latency=%.3fs", isin, len(df), time.monotonic() - t0)
        if df.empty:
            return None
        return _row_to_summary(df.index[0], df.iloc[0], _today())

    try:
        return await asyncio.wait_for(asyncio.to_thread(_inner), timeout=45.0)
    except asyncio.TimeoutError:
        _log.warning("timeout fn=load_overview isin=%s after 45s", isin)
        raise RuntimeError(f"justETF request timed out for {isin}") from None


_SORT_COLS = {
    "ter": ("ter", True),               # ascending — lower cost is better
    "fund_size": ("size", False),        # descending — largest first
    "return_1y": ("last_year", False),
    "return_3y": ("last_three_years", False),
    "return_5y": ("last_five_years", False),
}


@cached(ttl_key="profile")
async def fetch_screener(
    asset_class: str | None = None,
    region: str | None = None,
    max_ter: float | None = None,
    min_fund_size_eur: float | None = None,
    distribution: str | None = None,
    query: str | None = None,
    provider: str | None = None,
    currency: str | None = None,
    currency_hedged: bool | None = None,
    replication: str | None = None,
    sustainability: bool | None = None,
    sort_by: str | None = None,
    exclude_leveraged: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query the justETF screener and return matching ETFs.

    max_ter is decimal (0.002 = 0.20%). min_fund_size_eur is in EUR.
    Returns are percentages (24.76 means +24.76%).
    query maps to justETF's &query= parameter (accepts ISIN or name substring).
    provider is a post-filter by fund provider (e.g. "iShares", "Amundi").
    sort_by: 'ter' | 'fund_size' | 'return_1y' | 'return_3y' | 'return_5y'.
    exclude_leveraged drops funds whose name matches a leverage heuristic
    (justETF's screener has no leverage column — see _is_leveraged).
    offset/limit paginate the (post-filtered, sorted) result set.
    """
    # Map friendly strings to justETF query values
    _asset_map = {
        "equity": "class-equity",
        "bonds": "class-bonds",
        "commodities": "class-commodities",
        "real_estate": "class-realEstate",
        "money_market": "class-moneyMarket",
        "precious_metals": "class-preciousMetals",
        "currency": "class-currency",
    }
    _region_map = {
        "world": "World",
        "europe": "Europe",
        "north_america": "North%2BAmerica",
        "asia_pacific": "Asia%2BPacific",
        "emerging_markets": "Emerging%2BMarkets",
        "eastern_europe": "Eastern%2BEurope",
        "latin_america": "Latin%2BAmerica",
        "africa": "Africa",
    }

    def _inner() -> list[dict[str, Any]]:
        _ensure_log_handler()
        ac = _asset_map.get((asset_class or "").lower(), asset_class)
        rg = _region_map.get((region or "").lower(), region)
        t0 = time.monotonic()
        try:
            # provider / index_provider are NOT passed to load_overview — the
            # &ic= / &indexProvider= API params require undocumented internal IDs
            # (e.g. "Amundi" and "S&P" return 0 rows). Post-filter instead.
            df = load_overview(asset_class=ac, region=rg, isin=query)
        except Exception as exc:
            _log.warning(
                "error fn=load_overview asset_class=%s region=%s query=%s latency=%.3fs error=%r",
                ac, rg, query, time.monotonic() - t0, exc,
            )
            raise
        _log.info(
            "ok fn=load_overview asset_class=%s region=%s query=%s rows=%d latency=%.3fs",
            ac, rg, query, len(df), time.monotonic() - t0,
        )

        if df.empty:
            return []

        # Post-filters
        if provider is not None:
            df = df[df["name"].apply(_extract_provider).str.lower() == provider.lower()]
        if max_ter is not None:
            df = df[df["ter"].notna() & (df["ter"] / 100 <= max_ter)]
        if min_fund_size_eur is not None:
            df = df[df["size"].notna() & (df["size"] * 1_000_000 >= min_fund_size_eur)]
        if distribution is not None:
            df = df[df["dividends"].astype(str).str.lower() == distribution.lower()]
        if currency is not None:
            df = df[df["currency"].astype(str).str.upper() == currency.upper()]
        if currency_hedged is not None:
            df = df[df["hedged"] == currency_hedged]
        if replication is not None:
            df = df[df["replication"].astype(str).str.lower().str.contains(replication.lower(), na=False)]
        if sustainability is not None:
            df = df[df["is_sustainable"] == sustainability]
        if exclude_leveraged:
            df = df[~df["name"].apply(_is_leveraged)]

        # Sort
        if sort_by is not None and sort_by in _SORT_COLS:
            col, ascending = _SORT_COLS[sort_by]
            df = df.sort_values(col, ascending=ascending, na_position="last")

        df = df.iloc[offset : offset + limit]

        data_as_of = _today()
        return [_row_to_summary(isin, row, data_as_of) for isin, row in df.iterrows()]

    try:
        return await asyncio.wait_for(asyncio.to_thread(_inner), timeout=60.0)
    except asyncio.TimeoutError:
        _log.warning("timeout fn=load_overview asset_class=%s region=%s after 60s", asset_class, region)
        raise RuntimeError("justETF screener request timed out") from None
