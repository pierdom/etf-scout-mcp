# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). This project
doesn't cut versioned releases yet — entries are grouped by the audit phase that
produced them.

## Unreleased

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
