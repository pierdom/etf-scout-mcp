# etf-scout-mcp

[![Docker](https://github.com/pierdom/etf-scout-mcp/actions/workflows/docker.yml/badge.svg)](https://github.com/pierdom/etf-scout-mcp/actions/workflows/docker.yml)

A [Model Context Protocol](https://modelcontextprotocol.io) server for ETF research,
built for one consumer: an LLM agent. Every tool response is shaped for that — small,
unambiguous, explicitly typed, and with failures that are impossible to mistake for
real data (a null price always carries a populated `error`; nothing is ever fabricated
to fill a gap).

**What this is:** fund discovery, profile depth, cost/comparison, exchange-ticker
mapping, quotes, and price history — read-only research against justETF, Yahoo
Finance, and OpenFIGI.

**What this is not:** a portfolio tracker. It has no concept of your holdings,
transactions, or account balances, and it will never place, modify, or simulate a
trade. It's designed to pair with a portfolio tool that *does* know what you hold —
e.g. [Ghostfolio](https://ghostfol.io) via a `ghostfolio-mcp` server: this server
answers "what should I know about this fund," Ghostfolio answers "what do I actually
hold and how has it performed."

## Data sources — read this before relying on anything here

| Source | Status | Limits |
|---|---|---|
| **justETF** | Scraped, unofficial | HTML scraping via the pinned [`justetf-scraping`](https://github.com/druzsan/justetf-scraping) library. Breaks silently when justETF changes their page structure; can rate-limit or block. See "Fragile dependencies" below. |
| **Yahoo Finance** | Unofficial API | No published SLA or terms for this usage; can change or start blocking without notice. `curl_cffi` Firefox TLS impersonation is what currently keeps it working. |
| **OpenFIGI** | Official Bloomberg-backed API | Real rate limits (25 req/min unauthenticated, 25 req/6s with a free API key) — see `OPENFIGI_API_KEY`. |

This is a personal tool scraping and re-serving data from services whose terms may not
permit that use at scale. Don't point it at production traffic, don't redistribute the
scraped data, and check justETF's and Yahoo's current terms yourself if you're unsure.

## The 10 tools

| Tool | Source | Purpose |
|---|---|---|
| `search_etfs` | justETF screener | Discover ETFs by filter |
| `get_etf_profile` | justETF | Full single-fund profile |
| `compare_etfs` | justETF | Side-by-side summary for multiple ISINs |
| `get_etf_listings` | OpenFIGI | Exchange listings + Yahoo ticker for an ISIN |
| `get_quote` | Yahoo → justETF Gettex | Latest price, one instrument |
| `get_quotes` | Yahoo → justETF Gettex | Latest price, many instruments |
| `get_history` | Yahoo | OHLCV series or computed summary stats |
| `portfolio_xray` | justETF | Aggregated look-through country/sector/single-name exposure across a set of ETF holdings |
| `compute_overlap` | justETF | Top-10-holdings overlap between two ETFs |
| `find_alternatives` | justETF | Funds tracking a similar index, ranked cheapest-first by TER |

**Conventions used throughout:**
- TER is a **decimal**, not a percentage — `0.002` = 0.20% (20 bps)
- Money fields are in **EUR** unless the field name says otherwise (`fund_size_eur`)
- Dates are **ISO 8601** strings (`2009-09-25`)
- `return_1y`/`return_3y`/`return_5y` are **cumulative** total returns over the stated
  period, not annualised — `return_3y_annualised_pct`/`return_5y_annualised_pct` give
  the CAGR siblings. Currency basis: the fund's own reporting currency as justETF
  displays it (not converted to EUR or any other common currency) — if you're comparing
  funds with different `fund_currency`, the return figures are not on a common basis.
- A `data_as_of` field means "when this row was scraped," not "as of today" — on a
  cache hit it reflects the original fetch, never `date.today()`.

---

## Tool reference

### `search_etfs`

Filter the justETF screener. The primary discovery tool.

| Param | Type | Default | Notes |
|---|---|---|---|
| `asset_class` | `str \| None` | `None` | `equity`, `bonds`, `commodities`, `real_estate`, `money_market`, `precious_metals`, `currency` |
| `region` | `str \| None` | `None` | `world`, `europe`, `north_america`, `asia_pacific`, `emerging_markets`, `eastern_europe`, `latin_america`, `africa` |
| `max_ter` | `float \| None` | `None` | Decimal, e.g. `0.002` = 20bps |
| `min_fund_size_eur` | `float \| None` | `None` | e.g. `1_000_000_000` = €1B |
| `distribution` | `str \| None` | `None` | `Accumulating` \| `Distributing` |
| `query` | `str \| None` | `None` | Free-text name substring or ISIN |
| `provider` | `str \| None` | `None` | e.g. `iShares`, `Vanguard` |
| `currency` | `str \| None` | `None` | Fund base currency |
| `currency_hedged` | `bool \| None` | `None` | |
| `replication` | `str \| None` | `None` | Substring match: `full`, `sampling`, `swap` |
| `sustainability` | `bool \| None` | `None` | |
| `sort_by` | `str \| None` | `None` | `ter` \| `fund_size` \| `return_1y` \| `return_3y` \| `return_5y` |
| `exclude_leveraged` | `bool` | `False` | Best-effort name-regex heuristic — see below |
| `limit` | `int` | `20` | Must be >= 1; rejected with a `ValueError` otherwise |
| `offset` | `int` | `0` | Pagination; must be >= 0; rejected with a `ValueError` otherwise |

Returns `list[EtfSummary]` — same schema `compare_etfs` uses (see below). `currency_hedged`
and `sustainability` are `null` (not `false`) when justETF's screener data doesn't have
a value for that fund — never fabricated as a default.

**Known limitation:** rows don't carry `distribution_frequency` — that field only
exists in the single-ISIN profile scrape (`get_etf_profile`), not the screener, and
adding it here would mean one extra justETF scrape per result row.

```
search_etfs(region="europe", asset_class="equity", max_ter=0.002, sort_by="return_1y", exclude_leveraged=True, limit=3)
→ [
    {"isin": "IE00B4L5Y983", "name": "iShares Core MSCI World UCITS ETF", "ticker": "IWDA",
     "fund_provider": "iShares", "fund_domicile": "Ireland", "fund_currency": "USD",
     "fund_size_eur": 91234500000.0, "ter": 0.002, "replication": "Full replication",
     "distribution_policy": "Accumulating", "currency_hedged": false, "sustainability": false,
     "inception_date": "2009-09-25", "return_1y": 24.76, "return_3y": 33.1, "return_5y": 90.02,
     "return_3y_annualised_pct": 9.98, "return_5y_annualised_pct": 13.68, "volatility_1y": 10.6,
     "leverage_factor": null, "data_as_of": "2026-09-15", "error": null},
    ...
  ]
```

Before this fix, this same query silently returned a 3x daily-leveraged EURO STOXX
Banks ETP at the top with nothing marking it as such — `exclude_leveraged=True` (or
inspecting `leverage_factor`) is the fix.

### `get_etf_profile`

Full single-fund profile from justETF: TER, replication, distribution, size, domicile,
returns, top holdings, country/sector breakdown.

| Param | Type | Default |
|---|---|---|
| `isin` | `str` | required |

Returns `EtfProfile`:

```
get_etf_profile(isin="IE00B4L5Y983")
→ {
    "isin": "IE00B4L5Y983", "name": "iShares Core MSCI World UCITS ETF",
    "description": "...", "index": "MSCI World", "investment_focus": "...",
    "fund_size_eur": 91234500000.0, "ter": 0.002, "replication": "Full replication",
    "distribution_policy": "Accumulating", "distribution_frequency": null,
    "fund_currency": "USD", "currency_hedged": false, "fund_domicile": "Ireland",
    "fund_provider": "iShares", "legal_structure": "ETF", "sustainability": false,
    "volatility_1y": 10.6, "inception_date": "2009-09-25", "holdings_date": "2025-10-29",
    "data_as_of": "2026-09-15",
    "return_1y": 24.76, "return_3y": 33.1, "return_5y": 90.02,
    "return_3y_annualised_pct": 9.98, "return_5y_annualised_pct": 13.68,
    "top_holdings": [{"name": "Apple Inc", "isin": "US0378331005", "weight": 5.3}, ...],
    "countries": [{"name": "United States", "weight": 67.3}, ...],
    "sectors": [{"name": "Information Technology", "weight": 24.1}, ...],
    "error": null
  }
```

`distribution_frequency` (and every other scraped string field) is `null`, not `"-"`,
when justETF doesn't have the data — see "Error model."

When `isin` doesn't resolve to a real fund on justETF, every field is `null` except
`isin` and `error` (`"ISIN '...' not found on justETF."`) — justETF's own scraper
doesn't reliably signal "not found" for a single-ISIN profile lookup (it can return a
generic fallback page instead of a 404), so this is detected by checking that the
scrape came back with no `ter`, `fund_size_eur`, or `inception_date` at all, which no
real fund profile is ever missing all three of.

### `compare_etfs`

Side-by-side `EtfSummary` rows (same schema as `search_etfs`) for a list of ISINs, in
one call.

| Param | Type | Default |
|---|---|---|
| `isins` | `list[str]` | required |

Returns exactly one row per requested ISIN, in order — an unresolvable ISIN gets a row
with `error` set and every other field `null`, never a silently shorter list:

```
compare_etfs(isins=["IE00B4L5Y983", "IE00XXXXXXXX"])
→ [
    {"isin": "IE00B4L5Y983", "name": "iShares Core MSCI World UCITS ETF", ..., "error": null},
    {"isin": "IE00XXXXXXXX", "name": null, ..., "error": "ISIN 'IE00XXXXXXXX' not found on justETF."}
  ]
```

### `get_etf_listings`

Exchange listings for an ISIN via OpenFIGI, with the Yahoo Finance ticker for each.

| Param | Type | Default | Notes |
|---|---|---|---|
| `isin` | `str` | required | |
| `primary_only` | `bool` | `True` | Collapse OpenFIGI's raw response (a row per trade-reporting venue, not per real exchange listing) to one row per exchange, deduplicated on `(ticker, exch_code)`. Set `False` to see every raw row — can be 100+. |
| `limit` | `int` | `20` | |

Returns `list[EtfListing]`:

```
get_etf_listings(isin="IE00B4L5Y983")
→ [
    {"figi": "BBG000BBQCY0", "ticker": "EUNL", "name": "ISHARES CORE MSCI WORLD",
     "exch_code": "GR", "yahoo_symbol": "EUNL.DE", "security_type": "Common Stock",
     "market_sector": "Equity", "security_description": "EUNL"},
    {"figi": "BBG000BBQCX1", "ticker": "IWDA", "name": "ISHARES CORE MSCI WORLD",
     "exch_code": "NA", "yahoo_symbol": "IWDA.AS", ...},
    ...  # ~8 rows for a widely-listed fund, not ~190
  ]
```

`yahoo_symbol` is `null` when `exch_code` isn't in our exch_code→suffix map — fall back
to `ticker`/`exch_code` for those. `mic_code`/`currency` were removed entirely: the
OpenFIGI `/v3/mapping` response never carries them for equity/ETF rows, so "populate
them" wasn't possible.

### `get_quote`

Latest price for one instrument. Yahoo Finance first; falls back to justETF's Gettex
live quote (EUR, European trading hours) when Yahoo fails and an `isin` was given.

| Param | Type | Default |
|---|---|---|
| `symbol` | `str \| None` | `None` — Yahoo ticker, e.g. `'IWDA.AS'` |
| `isin` | `str \| None` | `None` — used for auto-resolution and Gettex fallback |
| `include_book` | `bool` | `False` — fetch `bid`/`ask`/`spread_bps`/`market_state` too (see below) |

At least one of `symbol`/`isin` is required. Returns `Quote`:

```
get_quote(symbol="IWDA.AS")
→ {"symbol": "IWDA.AS", "isin": null, "currency": "USD", "price": 102.34,
   "previous_close": 101.9, "open": 101.95, "day_high": 102.5, "day_low": 101.8,
   "volume": 1234567, "as_of": "2026-09-15", "source": "yahoo", "error": null,
   "bid": null, "ask": null, "spread_bps": null, "market_state": null}

get_quote(symbol="NOTAREALTICKER.XX")
→ {"symbol": "NOTAREALTICKER.XX", "isin": null, "currency": null, "price": null,
   "previous_close": null, "open": null, "day_high": null, "day_low": null,
   "volume": null, "as_of": null, "source": "error",
   "error": "Ticker 'NOTAREALTICKER.XX' was not recognized by Yahoo Finance. Verify the symbol, or call get_etf_listings to find the correct one."}
```

`market_cap` was removed — it's always `null` for ETFs from Yahoo's `fast_info`, and
fund AUM already lives in `get_etf_profile`/`compare_etfs`/`search_etfs` as
`fund_size_eur`; duplicating it here for a Yahoo-sourced tool would mean an extra
justETF call per quote for data that already has a home.

`include_book=True` fetches `bid`/`ask`/`spread_bps`/`market_state` via a **second,
heavier** Yahoo request (`yfinance`'s full `.info`, not the lightweight `fast_info` the
base quote uses) — opt-in, not default, because it roughly doubles Yahoo request volume
per quote. Only populated when the quote resolves via Yahoo (never for a Gettex-sourced
quote), and Yahoo's book data for European-listed ETFs has been observed stale/
unreliable outside continuous auction windows — treat it accordingly, don't assume it's
a live tradeable spread. A book-fetch failure never fails the underlying quote; the
book fields just stay `null`.

```
get_quote(symbol="VWCE.DE", include_book=True)
→ {..., "source": "yahoo", "bid": 165.8, "ask": 165.86, "spread_bps": 3.6, "market_state": "REGULAR"}
```

### `get_quotes`

Same as `get_quote`, batched and concurrent.

| Param | Type | Default |
|---|---|---|
| `symbols` | `list[str] \| None` | `None` |
| `isins` | `list[str] \| None` | `None` |
| `include_book` | `bool` | `False` — applies to every row; one extra Yahoo request per row |

Returns `list[QuoteResult]` — `Quote` plus `requested: str` (the exact input string
this row corresponds to). **Ordering:** all `symbols` rows first (in the order given),
then all `isins` rows (in the order given) — use `requested` to correlate a row back to
its input rather than relying on position when both are combined:

```
get_quotes(symbols=["VWCE.DE"], isins=["IE00XXXXXXXX"])
→ [
    {"requested": "VWCE.DE", "symbol": "VWCE.DE", ..., "source": "yahoo", "error": null},
    {"requested": "IE00XXXXXXXX", "symbol": "IE00XXXXXXXX", ..., "source": "error",
     "error": "Could not resolve a Yahoo Finance ticker for ISIN 'IE00XXXXXXXX' via OpenFIGI. ..."}
  ]
```

`get_quote` and `get_quotes` return the **identical** row schema (`QuoteResult` is
`Quote` plus `requested`) — a client can use the same parsing code for both.

### `get_history`

OHLCV price history, or computed summary statistics, from Yahoo Finance.

| Param | Type | Default | Notes |
|---|---|---|---|
| `symbol` | `str \| None` | `None` | |
| `isin` | `str \| None` | `None` | |
| `period` | `str` | `"1y"` | One of `1mo`, `3mo`, `6mo`, `1y`, `2y`, `5y`, `10y`, `max` — rejected with the valid list otherwise |
| `interval` | `str` | `"1d"` | One of `1d`, `1wk`, `1mo` — rejected with the valid list otherwise |
| `max_rows` | `int` | `400` | See below |
| `summary` | `bool` | `False` | See below |

Returns `HistoryResult` (bars) or `HistorySummary` (stats), both carrying
`downsampled_from`:

```
get_history(symbol="VWCE.DE", period="1y", interval="1d")
→ {"symbol": "VWCE.DE", "period": "1y", "interval": "1d", "downsampled_from": null,
   "bars": [{"date": "2025-09-15", "open": 118.5, "high": 119.0, "low": 118.2, "close": 118.9, "volume": 45123}, ...]}
```

If the requested `period`/`interval` would exceed `max_rows` bars, the interval is
**automatically coarsened** `1d → 1wk → 1mo` until the series fits (or `1mo` is
reached, at which point the full monthly series is returned even if still over
budget — the series is never silently truncated). `downsampled_from` then reports the
interval that was actually requested:

```
get_history(symbol="VWCE.DE", period="5y", interval="1d", max_rows=400)
→ {"symbol": "VWCE.DE", "period": "5y", "interval": "1wk", "downsampled_from": "1d", "bars": [...]}
```

`summary=True` replaces `bars` with computed statistics — much smaller when you don't
need every bar:

```
get_history(symbol="VWCE.DE", period="5y", summary=True)
→ {"symbol": "VWCE.DE", "period": "5y", "interval": "1d", "downsampled_from": null,
   "first_date": "2020-09-15", "last_date": "2026-09-15", "bar_count": 1305,
   "total_return_pct": 92.4, "cagr_pct": 14.0, "annualised_volatility_pct": 15.2,
   "max_drawdown_pct": -24.1, "max_drawdown_peak_date": "2021-11-08", "max_drawdown_trough_date": "2022-10-13",
   "best_month": {"month": "2023-11", "return_pct": 11.4},
   "worst_month": {"month": "2022-09", "return_pct": -9.8},
   "yearly_returns": [{"year": "2021", "return_pct": 28.1}, {"year": "2022", "return_pct": -13.9}, ...]}
```

### `portfolio_xray`

Aggregated look-through country, sector, and top single-name exposure across a set of
ETF holdings, weighted by portfolio weight. Pairs with a portfolio tool that knows what
you actually hold (e.g. Ghostfolio via `ghostfolio-mcp`) — that tool answers "what do I
hold," this answers "what am I exposed to" once you look through each fund.

| Param | Type | Default |
|---|---|---|
| `holdings` | `list[{isin: str, weight: float}]` | required — weights are relative, don't need to sum to 100 |

Returns `PortfolioXray`:

```
portfolio_xray(holdings=[{"isin": "IE00B4L5Y983", "weight": 60}, {"isin": "IE00BK5BQT80", "weight": 40}])
→ {
    "countries": [{"name": "United States", "weight": 61.2}, {"name": "Japan", "weight": 5.8}, ...],
    "sectors": [{"name": "Information Technology", "weight": 22.4}, ...],
    "top_single_names": [{"name": "Apple Inc", "isin": "US0378331005", "weight": 4.7}, ...],
    "concentration_approximate": true,
    "funds_requested": 2, "funds_resolved": 2, "errors": []
  }
```

`countries`/`sectors` use each fund's full published breakdown — real, complete
look-through data. `top_single_names` is **approximate**: justETF only discloses each
fund's top 10 holdings, not the full constituent list, so real single-name
concentration may be higher than shown — `concentration_approximate` is always `true`
today as a reminder. Like `compare_etfs`, a holding that fails to resolve lands in
`errors`, never a silent drop from the aggregation.

### `compute_overlap`

Holdings-level overlap between two ETFs — a quick "are these two funds basically the
same thing" check.

| Param | Type | Default |
|---|---|---|
| `isin_a` | `str` | required |
| `isin_b` | `str` | required |

Returns `OverlapResult`:

```
compute_overlap(isin_a="IE00B4L5Y983", isin_b="IE00BK5BQT80")
→ {
    "isin_a": "IE00B4L5Y983", "isin_b": "IE00BK5BQT80", "overlap_pct": 8.4,
    "shared_holdings": [{"name": "Apple Inc", "isin": "US0378331005", "weight_a": 5.3, "weight_b": 3.9}, ...],
    "approximate": true, "holdings_compared_a": 10, "holdings_compared_b": 10, "error": null
  }
```

`overlap_pct` is `sum(min(weight_a, weight_b))` over holdings both funds disclose in
their **top 10** — `approximate` is always `true` (justETF doesn't publish full
constituent lists, so two funds could hold near-identical portfolios past the top 10
and still show low overlap here). If either ISIN fails to resolve, `error` is set and
`overlap_pct` is `null` — the tool never raises for a bad ISIN.

### `find_alternatives`

Funds tracking a similar index to a given fund, cheapest-first by TER.

| Param | Type | Default |
|---|---|---|
| `isin` | `str` | required |
| `limit` | `int` | `10` |

Returns `AlternativesResult`:

```
find_alternatives(isin="IE00B4L5Y983", limit=3)
→ {
    "isin": "IE00B4L5Y983", "index": "MSCI World", "ranked_by": "ter",
    "alternatives": [
      {"isin": "IE00BJ0KDQ92", "name": "SPDR MSCI World UCITS ETF", "ter": 0.0012, ...},
      ...
    ],
    "error": null
  }
```

Matches on the source fund's `index` name as a substring against other funds' names
(the same free-text match `search_etfs`'s `query` uses) — best-effort text matching,
not a guaranteed same-index match. `ranked_by` is always `"ter"` — justETF doesn't
publish tracking-difference data (see CHANGELOG), so this isn't a full
total-cost-of-ownership ranking, only a cheapest-advertised-cost one. `error` is set
(with an empty `alternatives` list) when the ISIN fails to resolve or justETF has no
recorded index for it.

---

## Error model

- **Any tool that can partially fail returns one entry per requested input** —
  `compare_etfs`, `get_quotes` — never a silently shorter list. A failed row carries a
  populated `error` and every other field `null`.
- **A `null` price (`get_quote`/`get_quotes`) always carries a populated `error`** and
  `source: "error"`; `as_of` is `null` alongside it — never fabricated. `source` is
  `"yahoo"`, `"justetf_gettex"`, or `"error"`.
- The `get_quote` error message distinguishes, where Yahoo's `fast_info` lets it be
  told apart: **"ticker not recognized"** (no `currency` came back at all) from
  **"no price data, market may be closed/instrument halted"** (`currency` present,
  `price` absent).
- The Gettex fallback never masks a hard failure: if both Yahoo and Gettex fail, both
  error messages are in the final `error` string.
- A scraped field justETF renders as a placeholder (`"-"`, `""`, `"n/a"`, `"–"`) always
  comes back as `null`, never the raw placeholder string.
- Invalid `period`/`interval` on `get_history` raise a `ValueError` naming the value you
  passed and listing what's valid — they are not silently passed through to Yahoo.
- **An ISIN that doesn't exist on justETF never comes back as a fabricated profile.**
  `get_etf_profile`/`portfolio_xray`/`compute_overlap`/`find_alternatives` all detect
  this (justETF's single-ISIN scrape doesn't reliably 404 — it can return a generic
  fallback page instead) and surface a populated `error` naming the bad ISIN, rather
  than a profile with a plausible-looking but meaningless `name`.

## Payload-size guidance for agent callers

- `get_history`: pass `summary=True` when you need statistics, not a chart — it's a
  fraction of the token cost of the OHLCV series. Otherwise rely on the `max_rows`
  default (400) rather than requesting `interval="1d"` over a multi-year `period`.
- `get_etf_listings`: leave `primary_only=True` (default) unless you specifically need
  every raw OpenFIGI row.
- `search_etfs`: use `limit`/`offset` to page through results instead of requesting a
  large `limit` up front.

## Caching

Every outbound call (justETF, Yahoo, OpenFIGI) is cached in a local SQLite database,
keyed on the function and its arguments, with per-source TTLs:

| Data | TTL | Env var |
|---|---|---|
| justETF profile/screener, OpenFIGI listings | 24h | `ETF_SCOUT_MCP_CACHE_TTL_PROFILE` |
| Yahoo quotes | 60s | `ETF_SCOUT_MCP_CACHE_TTL_QUOTE` |
| Yahoo history | 1h | `ETF_SCOUT_MCP_CACHE_TTL_HISTORY` |

Set `ETF_SCOUT_MCP_CACHE_ENABLED=false` to disable caching entirely (every call hits
the upstream source). Cache hits and misses are logged to `calls.log` alongside every
other outbound call — see below. Delete the cache file (`ETF_SCOUT_MCP_CACHE`) to force
a full refresh.

Every outbound call and every cache hit/miss is logged with latency and status to
`~/.cache/etf-scout-mcp/calls.log` (rotating, 5 MB × 3 files). Check this file first
when diagnosing a data-source problem.

## Local development

```bash
git clone https://github.com/pierdom/etf-scout-mcp
cd etf-scout-mcp
uv sync --group dev
```

Run over stdio (what Claude Desktop uses):

```bash
uv run etf-scout-mcp
```

Run over HTTP:

```bash
ETF_SCOUT_MCP_TRANSPORT=http ETF_SCOUT_MCP_HTTP_BEARER_TOKEN=dev-token uv run etf-scout-mcp
```

Call a single tool directly:

```bash
uv run fastmcp call --server-spec src/etf_scout_mcp/server.py \
  --target get_etf_profile --input-json '{"isin": "IE00B4L5Y983"}'
```

Inspect/interactively test all tools:

```bash
uv run fastmcp inspect src/etf_scout_mcp/server.py
uv run fastmcp dev src/etf_scout_mcp/server.py   # opens MCP Inspector in browser
```

Run the offline unit test suite (fixtures, no live network — see `tests/conftest.py`):

```bash
uv run pytest tests/
```

Run the live-network smoke tests manually before a release (not part of the pytest
suite or CI):

```bash
uv run python tests/smoke_justetf.py
uv run python tests/smoke_yahoo.py
uv run python tests/smoke_openfigi.py
```

There is no configured linter/formatter in this repo (no `ruff`/`black` config) —
match the existing style (double quotes, `from __future__ import annotations`,
dataclasses for config, Pydantic for response models).

### Claude Desktop (stdio)

```json
{
  "mcpServers": {
    "etf-scout-mcp": {
      "command": "/home/<you>/.local/bin/uv",
      "args": [
        "run",
        "--project", "/home/<you>/Workspace/etf-scout-mcp",
        "python", "-m", "etf_scout_mcp"
      ],
      "env": { "ETF_SCOUT_MCP_TRANSPORT": "stdio" }
    }
  }
}
```

Replace `<you>` with your username. Use `which uv` to confirm the uv path, and **fully
quit and relaunch Claude Desktop** — it does not hot-reload this config. The hammer icon
should show 7 tools. Claude Desktop only supports stdio servers directly; an HTTP entry
in this file is silently skipped. Remote HTTP MCP in Claude Desktop instead goes
through Anthropic's cloud (Settings → Integrations) and needs a publicly reachable
server — see OIDC below.

## Deployment

A multi-arch image (amd64, arm64) publishes to GHCR on every push to `main`.

```bash
docker pull ghcr.io/pierdom/etf-scout-mcp:edge
```

`docker-compose.yml` in this repo is a complete example — pinned image with a
commented build-from-source alternative, a named volume for `/data` (cache, logs, and
the OIDCProxy store), a healthcheck, and the explicit `1000:1000` non-root user the
image's Dockerfile pins.

```bash
cp .env.example .env
# edit .env — set ETF_SCOUT_MCP_HTTP_BEARER_TOKEN (openssl rand -hex 32) at minimum
docker compose up -d
```

Point Claude Code CLI (which supports HTTP MCP servers directly) at it:

```json
{
  "mcpServers": {
    "etf-scout-mcp": {
      "type": "http",
      "url": "http://<host>:8765/mcp",
      "headers": { "Authorization": "Bearer <your-token>" }
    }
  }
}
```

The connector URL **must end in `/mcp`** — a bare host makes the client POST to `/`,
which 404s and shows as "couldn't connect" with no more specific error.

### Reverse proxy (nginx-proxy-manager or similar)

- Enable **WebSockets** support.
- `proxy_buffering off;` — streamable-http needs the response unbuffered.
- `proxy_read_timeout 3600s; proxy_send_timeout 3600s;` — the streaming transport holds
  connections open far longer than a typical HTTP request.
- If the proxy is on a different Docker network than this container, publish the port
  bound to the proxy's network-gateway IP (e.g. `172.20.0.1:8765:8765`), not `0.0.0.0` —
  see the comment in `docker-compose.yml`.

## OIDC / remote OAuth

Claude's remote connector (mobile/desktop, via Settings → Integrations) requires Dynamic
Client Registration (DCR). Most self-hosted IdPs — including PocketID, this server's
reference deployment target — don't implement DCR. FastMCP's `OIDCProxy` presents a
DCR-compliant interface to the client and brokers the actual login to your real IdP, so
you don't have to hand-roll OAuth (and this server doesn't — `OIDCProxy` only, see
CLAUDE.md non-goals).

**Precedence** (`server.py`): `OIDCProxy` when `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`, and `OIDC_BASE_URL` are **all** set; the static bearer token
otherwise; the server refuses to start with no auth at all under `http` transport.
`stdio` transport needs neither and ignores both.

All `OIDC_*` env vars are documented inline in `.env.example` — required
(`OIDC_CONFIG_URL`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`/`OIDC_BASE_URL`) vs. optional
(`OIDC_REDIRECT_PATH`, `OIDC_REQUIRED_SCOPES`, `OIDC_ALLOWED_REDIRECT_URIS`,
`OIDC_VERIFY_ID_TOKEN`, `OIDC_FORWARD_RESOURCE`).

**Non-obvious, load-bearing constructor args** (`server.py:_make_auth`):
- `config_url` — the IdP's `.well-known/openid-configuration` URL, fetched at startup.
- `base_url` + `redirect_path` together form the redirect URI registered on the IdP.
- `forward_resource=False` (default here) — PocketID and many IdPs reject the RFC 8707
  `resource=` param; only flip this for an IdP you've confirmed supports it.
- `verify_id_token` — set `true` only if your IdP issues opaque (non-JWT) access
  tokens.

### IdP setup checklist (PocketID or similar)

1. Register a **confidential** client.
2. Enable **PKCE**.
3. Redirect URI: `https://<OIDC_BASE_URL>/<OIDC_REDIRECT_PATH>` exactly (default path
   `/auth/callback`).
4. **Assign the client an allowed user group** — skipping this is the single most
   common cause of a login that reaches the IdP and then fails; symptom is
   `access_denied` after you approve the login.

### Gotchas

- **`forward_resource=True` against an IdP that doesn't support RFC 8707`:** login fails
  immediately after consent with `invalid_request`. Fix: leave it `false` (the default)
  unless you've confirmed the IdP supports resource indicators.
- **Connector URL without a trailing `/mcp`:** the client POSTs to `/`, gets a 404, and
  Claude reports a generic "couldn't connect" with no further detail.
- **The OIDCProxy client/token store is wiped on every redeploy** if
  `XDG_DATA_HOME` isn't pinned to a persisted path — FastMCP derives its data
  directory from `platformdirs`, which defaults somewhere ephemeral inside the
  container. Set `XDG_DATA_HOME=/data` (already in `docker-compose.yml`) and mount
  `/data` as a volume, or every container recreation forces every client to
  re-register (DCR) and every user to re-authenticate.
- **Named-volume permission footgun:** a non-root container image plus a volume whose
  contents were first created under an older/different image can leave the volume
  root-owned, so the (now non-root) process gets `Permission denied` writing the cache
  or the OIDCProxy store. Fix:
  `docker compose exec -u 0 etf-scout-mcp chown -R 1000:1000 /data`, then
  `docker compose restart etf-scout-mcp` — this forces one re-auth for every client,
  since it touches the OIDCProxy store.
- **Reverse proxy config** — see the Deployment section above; the OAuth flow itself
  rides over the same streamable-http connection and needs the same WebSocket/buffering/
  timeout settings.
- **FastMCP version:** this server currently runs FastMCP **3.2.4** (`pyproject.toml`
  pins `fastmcp>=2.0` with no upper bound, so `uv sync` resolves to the latest 3.x) —
  not v2, despite earlier assumptions in this repo's history. I could not find a
  dedicated inbound Host/Origin "DNS rebinding" guard in the installed 3.2.4 source
  (there's SSRF protection for *outbound* OIDC/JWKS fetches, which is a different
  thing) — if you hit an unexpected `421 Misdirected Request` or similar host-validation
  error, check the FastMCP release notes for the version actually installed rather than
  assuming the v2 behaviour this repo's docs previously described.

## Known limitations

- `search_etfs` rows don't carry `distribution_frequency` (screener-level data doesn't
  have it — see the tool reference above).
- `exclude_leveraged`/`leverage_factor` are a **name-regex heuristic**, not justETF
  ground truth — justETF's screener has no leverage column, and its "long-only"
  strategy category includes leveraged-long products. Don't treat an unset
  `leverage_factor` as proof a fund is unleveraged.
- Per-source request timeouts and retry delays (`sources/justetf.py`,
  `sources/yahoo.py`, `sources/openfigi.py`) are hardcoded, not configurable via env.
- No dedicated FastMCP v2→v3 migration work was done here (see the Gotchas note above);
  this server was already on v3 in its lockfile when this pass started.
- `get_etf_profile`'s `distribution_frequency` accuracy depends entirely on what the
  pinned `justetf-scraping` commit extracts from justETF's HTML — see "Fragile
  dependencies" below.

## Fragile dependencies

- **`justetf-scraping`** is pinned to a specific Git commit (HTML scraping breaks when
  justETF changes its page structure). If scraping fails, check
  `git ls-remote https://github.com/druzsan/justetf-scraping HEAD` for a newer commit,
  re-pin it in `pyproject.toml`, then `uv lock && uv sync && uv run python tests/smoke_justetf.py`.
- **Yahoo Finance** uses an unofficial API that changes without notice. The `curl_cffi`
  Firefox TLS fingerprint is what keeps it working; if quote fetches regress, check for
  a `yfinance` update first.

## Troubleshooting

**Yahoo Finance returning stale data or errors** — check `tail -f ~/.cache/etf-scout-mcp/calls.log`.
If Yahoo is consistently failing, pass `isin` to `get_quote` so it can fall back to Gettex.

**justETF screener or profile returning nothing** — results are cached up to 24h
(`ETF_SCOUT_MCP_CACHE_TTL_PROFILE`). Delete `ETF_SCOUT_MCP_CACHE` to force a fresh
fetch, or set `ETF_SCOUT_MCP_CACHE_ENABLED=false` temporarily.

**`get_etf_listings` returns no results** — OpenFIGI may have no mapping for a very new
or obscure ISIN; check `calls.log` for the `warning` field from the API response.

**Bearer auth rejected (HTTP transport)** — confirm `ETF_SCOUT_MCP_HTTP_BEARER_TOKEN`
is set server-side and the client sends an identical value. A wrong/missing token gets
`HTTP 401` with `WWW-Authenticate: Bearer error="invalid_token"`.
