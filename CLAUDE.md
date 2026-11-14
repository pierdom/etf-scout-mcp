# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`etf-scout-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server for ETF research. It exposes 16 MCP tools (ETFs and individual stocks) backed by three data sources: **justETF** (scraping), **Yahoo Finance** (yfinance + curl_cffi), and **OpenFIGI** (REST API), plus two optional REST sources: **OpenWebNinja** (Google Finance data: statements, news, FX, and a quote/history/profile fallback) and **EODHD** (quote/history/profile fallback).

## Commands

```bash
uv sync                          # install / sync dependencies
uv run etf-scout-mcp             # run server (stdio transport)

# smoke tests (hit real network)
uv run python tests/smoke_justetf.py
uv run python tests/smoke_yahoo.py
uv run python tests/smoke_openfigi.py

# unit tests
uv run pytest tests/

# docker
docker build -t etf-scout-mcp .
docker compose up -d
```

To call a single tool locally:
```bash
uv run fastmcp call --server-spec src/etf_scout_mcp/server.py \
  --target get_etf_profile --input-json '{"isin": "IE00B4L5Y983"}'
```

To inspect or interactively test all registered tools:
```bash
uv run fastmcp inspect src/etf_scout_mcp/server.py
uv run fastmcp dev src/etf_scout_mcp/server.py   # opens MCP Inspector in browser
```

## Architecture

```
src/etf_scout_mcp/
├── server.py          # FastMCP instance; transport selection (stdio/http); registers tools
├── config.py          # Config dataclass loaded from env vars
├── cache.py           # @cached decorator — SQLite TTL cache, per-key TTLs
├── models.py          # Pydantic response models (EtfProfile, EtfSummary, Holding, …)
├── sources/
│   ├── justetf.py     # justETF scraping: profile, screener, overview
│   ├── yahoo.py       # yfinance with curl_cffi Firefox TLS impersonation; quote + history
│   ├── openfigi.py    # httpx async; OpenFIGI exchange listings; handles 429 rate-limit
│   ├── openwebninja.py # httpx async; Google-Finance data: statements/news/FX + fallback (OPENWEBNINJA_API_KEY)
│   └── eodhd.py       # httpx async; optional quote/history fallback (EODHD_API_KEY)
└── tools/
    ├── etf_profile.py, search.py, etf_compare.py
    ├── quote.py, batch_quote.py, history.py, etf_listings.py
    ├── stock_profile.py (get_stock_profile + compare_stocks), stock_search.py
    ├── financials.py, stock_news.py, fx.py (OpenWebNinja-only), _google_symbol.py (shared resolver)
    └── portfolio_xray.py, compute_overlap.py, find_alternatives.py
```

**The 13 MCP tools:** `get_etf_profile`, `search_etfs`, `compare_etfs`, `get_quote`, `get_quotes`, `get_history`, `get_etf_listings`, `portfolio_xray`, `compute_overlap`, `find_alternatives`, plus the stock tools `get_stock_profile`, `compare_stocks`, `search_stocks`, and the OpenWebNinja-only tools `get_financials`, `get_stock_news`, `get_fx_rate` (these error clearly when `OPENWEBNINJA_API_KEY` is unset).

**Stocks:** quote/history/listings are instrument-agnostic (any Yahoo ticker or ISIN). `get_stock_profile` tries Yahoo `.info` → OpenWebNinja overview → EODHD fundamentals (each with fewer fields; an unrecognised Yahoo ticker skips the fallbacks to save quota); `portfolio_xray` treats an ISIN unknown to justETF as a stock. Unlike ETF models, stock money fields are in the stock's own currency (not EUR) and ratios are percent. `compute_overlap` and `find_alternatives` remain ETF-only.

**Request flow:** MCP client → `server.py` (tool dispatch) → `tools/` (input validation) → `cache.py` (@cached check) → `sources/` (network fetch) → Pydantic model → client.

**Fallbacks:** `quote.py` tries Yahoo → OpenWebNinja → EODHD (each only if its key is set) → justETF Gettex (if an ISIN was passed); `history.py` tries Yahoo → OpenWebNinja → EODHD.

**OpenWebNinja:** symbols are Google-form (`SAP:ETR`, `IWDA:AMS`; bare US tickers work) — `to_google_symbol()` maps Yahoo suffixes, and unmapped suffixes just skip this source. An unknown symbol returns an empty `data` upstream (not an error), which the source turns into a `RuntimeError`. History is close+volume only (open/high/low null), daily up to 1y and coarser beyond; `resample()` coarsens to the requested interval. Quotes carry no currency. `tests/conftest.py` forces the key off for the suite. The key goes in an `x-api-key` header; still never surface raw httpx exceptions.

**EODHD:** free tier is 20 calls/day, so `tests/conftest.py` forces the key off for the suite. The token is a URL query param — never surface raw httpx exceptions from `sources/eodhd.py` (they embed the URL).

**Adding a new tool:** Create `tools/new_tool.py` with a `register(mcp: FastMCP) -> None` function, then call `new_tool.register(mcp)` in `server.py`. Tools are registered at import time so `fastmcp inspect/dev` sees them without calling `main()`.

**`@cached` decorator:** Only three valid `ttl_key` values — `"quote"`, `"profile"`, `"history"`. Adding a source function that uses a different key will raise a `KeyError` at runtime.

**Logging:** All outbound calls (latency, status) are written to `~/.cache/etf-scout-mcp/calls.log` (5 MB × 3 rotating). Check here first when debugging data-source failures.

## Key env vars

| Variable | Default | Notes |
|---|---|---|
| `ETF_SCOUT_MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `ETF_SCOUT_MCP_HTTP_BEARER_TOKEN` | — | Required when transport is `http` |
| `ETF_SCOUT_MCP_HTTP_HOST` / `_HTTP_PORT` | `127.0.0.1` / `8765` | HTTP bind |
| `ETF_SCOUT_MCP_CACHE` | `~/.cache/etf-scout-mcp/cache.db` | SQLite path |
| `ETF_SCOUT_MCP_CACHE_ENABLED` | `true` | `false` disables the cache entirely |
| `ETF_SCOUT_MCP_CACHE_TTL_QUOTE` / `_PROFILE` / `_HISTORY` | `60` / `86400` / `3600` | Per-type TTLs in seconds |
| `ETF_SCOUT_MCP_LOG_LEVEL` | `INFO` | Standard Python log level |
| `OPENFIGI_API_KEY` | — | Optional; raises rate limit from 25/min to 25/6s |
| `OPENWEBNINJA_API_KEY` | — | Optional; enables `get_financials`/`get_stock_news`/`get_fx_rate` and the OpenWebNinja fallback (needs a subscription to *Real-Time Finance Data*) |
| `EODHD_API_KEY` | — | Optional; enables the EODHD quote/history fallback |

Copy `.env.example` to `.env` before running with HTTP transport. `OIDC_*` vars (optional,
`http` transport only) enable OAuth login for clients that require it — see README.

## Data conventions

- TER as a decimal fraction (e.g. `0.0020` = 0.20%), not a percentage
- Monetary values in EUR
- Dates as ISO 8601
- Returns and volatility as percentages

## Deployment

**Claude Desktop** only supports stdio servers directly (`claude_desktop_config.json`;
HTTP entries are silently skipped). **Claude Code CLI** and self-hosted clients can use
`http` transport (Docker, port 8765). **claude.ai / Claude mobile & desktop apps'
remote connectors** require OAuth, not a bearer token — that's what `OIDC_*` is for.
Full setup instructions for all three: README.

## Fragile dependencies

- **justetf-scraping** is pinned to a specific Git commit (HTML scraping breaks when justETF changes its page structure). If scraping fails, check `git ls-remote` on the upstream repo for a newer commit and re-pin in `pyproject.toml`.
- **Yahoo Finance** uses an unofficial API that changes without notice. The curl_cffi Firefox TLS fingerprint is what keeps it working; if quote fetches regress, check for yfinance updates first.
