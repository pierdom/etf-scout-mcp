# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). This project
doesn't cut versioned releases yet — entries are grouped by the audit phase that
produced them.

## Unreleased

### Post-deploy audit fixes
- `get_etf_profile` (and everything built on it — `portfolio_xray`, `compute_overlap`,
  `find_alternatives`) no longer fabricates a profile for an ISIN that doesn't exist on
  justETF. justETF's single-ISIN scrape doesn't reliably raise/404 for a bad ISIN — it
  can return a generic fallback page, which was previously parsed into a profile with
  `name: "ETF Screener"` and defaulted booleans instead of an error. Found via live
  production testing after the Phase 0-3 deploy; `EtfProfile` gains an `error` field to
  match `EtfSummary`/`Quote`.
- `search_etfs(limit=-5)` crashed with a raw `"boolean value of NA is ambiguous"`
  exception — pandas' `pd.NA` sentinel raises on a bare `bool()`/truthiness check, and
  the screener row conversion used plain Python truthiness on several fields. Fixed at
  the root (a `_row_get` helper normalises `pd.NA`/`NaN`/`NaT` to `None` before any
  field is touched) rather than patched at the one call site the negative limit
  happened to expose — `currency_hedged`/`sustainability` could have hit the same crash
  on a positive-`limit` query that included a fund with genuinely missing data; both
  are now correctly `null` instead of always defaulting to `false` when unknown.
  `limit`/`offset` also gain input validation (`limit >= 1`, `offset >= 0`) instead of
  silently producing a confusing pandas slice on a negative value.
- `search_etfs(sort_by=...)` now rejects an invalid value with a `ValueError` listing
  the valid ones, instead of silently falling back to default (fund-size-descending)
  order with no signal to the caller that their sort didn't apply — same class of gap
  `get_history`'s `period`/`interval` validation already covers elsewhere.
- `get_quote`'s docstring now documents (previously undocumented) that `symbol` and
  `isin` are not cross-validated against each other when both are given — a mismatched
  pair doesn't raise, it just echoes back the `isin` you passed alongside the
  `symbol`'s quote. Behaviour unchanged; this was a documentation gap, not a bug —
  locked in with a test so a future change doesn't silently start (or stop)
  cross-validating without an explicit decision.
- `portfolio_xray` now rejects a negative `weight` with a `ValueError` naming the
  offending ISIN. Verified in production that a mix of `weight: 0` and `weight: -10`
  produced mathematically-valid-looking but semantically nonsensical output — the
  negative holding ended up silently contributing 100% of the aggregate (double-negative
  division: `-10 / (0 + -10) = 1.0`) while the zero-weight holding contributed nothing.

  Zero is still allowed (contributes nothing, harmless).

### Phase 3 — new tools
- New tool `portfolio_xray(holdings: list[{isin, weight}])`: aggregated look-through
  country/sector/single-name exposure across a set of ETF holdings. Country/sector data
  is complete; single-name concentration is approximate (justETF only discloses each
  fund's top 10 holdings) — always flagged via `concentration_approximate`. A holding
  that fails to resolve lands in `errors`, never a silent drop.
- New tool `compute_overlap(isin_a, isin_b)`: holdings-level overlap between two ETFs,
  `sum(min(weight_a, weight_b))` over shared top-10 holdings. Always flagged
  `approximate` for the same top-10-disclosure reason as `portfolio_xray`. A failed
  ISIN sets `error` and `overlap_pct: null` rather than raising.
- New tool `find_alternatives(isin, limit=10)`: funds tracking a similar index,
  matched by index-name substring against other funds' names, ranked cheapest-first by
  TER (`ranked_by: "ter"` — no tracking-difference data available, so not a full
  cost-of-ownership ranking). Excludes the source ISIN from its own results.
- `get_quote`/`get_quotes` gain `include_book: bool = False` (FEAT-11) —
  `bid`/`ask`/`spread_bps`/`market_state` via a second, heavier Yahoo request
  (`.info`, not `.fast_info`), opt-in rather than default given the doubled request
  cost and observed stale book data for European-listed ETFs outside continuous
  auction windows. Never populated for Gettex-sourced quotes.
- FEAT-5 (tracking difference) and FEAT-6 (`compare_costs`) dropped from this phase —
  not sourceable from the pinned scraper, its current upstream HEAD, or justETF's
  public profile page HTML. FEAT-8 (Spanish-resident fields) dropped for the same
  reason.

### Phase 2 — schema honesty
- `search_etfs`/`compare_etfs` rows gain `fund_currency`, `data_as_of`,
  `return_3y_annualised_pct`, `return_5y_annualised_pct`, and `leverage_factor`.
- `get_etf_profile` gains `return_1y`/`return_3y`/`return_5y` (+ annualised siblings)
  and `data_as_of`.
- `search_etfs` gains `offset` (pagination) and `exclude_leveraged` (best-effort
  name-heuristic filter).
- Documented, not changed: `return_1y`/`return_3y`/`return_5y` are cumulative, not
  annualised, and reported in the fund's own currency, not converted.

### Phase 1 — payload discipline
- `get_etf_listings` gains `primary_only` (default `True`) and `limit`, deduplicating
  OpenFIGI's raw response (often ~190 rows) down to one row per real exchange listing,
  and adds a derived `yahoo_symbol` per row.
- `get_history` gains `max_rows` (auto-coarsens `1d → 1wk → 1mo` rather than silently
  truncating, reporting the original interval via `downsampled_from`) and `summary`
  (computed statistics instead of the OHLCV series). Its return shape changes from a
  bare array to `HistoryResult`/`HistorySummary` objects to carry this metadata.
  `period`/`interval` are now validated against documented value sets.
- The TTL cache gains a disable switch (`ETF_SCOUT_MCP_CACHE_ENABLED`) and hit/miss
  logging to `calls.log`; the default quote TTL changes from 300s to 60s.

### Phase 0 — correctness bugs
- `get_quote`/`get_quotes` no longer return a null price without a populated `error`;
  `as_of` is never fabricated when price is null. `Quote` gains an `error` field so
  `get_quote` and `get_quotes` share one schema; `get_quotes` rows gain `requested` to
  positionally correlate a batch row back to its input.
- `compare_etfs` returns exactly one row per requested ISIN — an unresolvable ISIN
  gets a row with `error` set instead of vanishing from the list.
- justETF's placeholder strings (`"-"`, `""`, `"n/a"`, `"–"`) now normalise to `null`
  across every scraped field, not just `distribution_frequency`.
- Monetary fields (quote prices, OHLCV bars, fund size) round consistently to 4dp.
- `get_etf_listings` drops the always-null `mic_code`/`currency` fields — confirmed
  unavailable from OpenFIGI's `/v3/mapping` response, not a bug in extraction.
- `get_quote`/`get_quotes` drop `market_cap` — always null for ETFs from Yahoo, and
  fund AUM already lives in the justETF-backed tools as `fund_size_eur`.
