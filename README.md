# etf-scout-mcp

[![Docker](https://github.com/pierdom/etf-scout-mcp/actions/workflows/docker.yml/badge.svg)](https://github.com/pierdom/etf-scout-mcp/actions/workflows/docker.yml)

A [Model Context Protocol](https://modelcontextprotocol.io) server for ETF research,
built for one consumer: an LLM agent. Responses are small, explicitly typed, and never
fabricate a value to fill a gap — a null price always carries a populated `error`.

**What it is:** fund discovery, profiles, comparison, exchange-ticker mapping, quotes,
and price history — read-only, against justETF, Yahoo Finance, and OpenFIGI.

**What it isn't:** a portfolio tracker. No concept of your holdings or trades, and it
never places one. Pair it with something that knows what you hold — e.g.
[Ghostfolio](https://ghostfol.io) via `ghostfolio-mcp`: this server answers "what
should I know about this fund," Ghostfolio answers "what do I actually hold."

## Data sources

| Source | Status | Notes |
|---|---|---|
| justETF | Scraped, unofficial | Breaks if justETF changes their HTML; see "Fragile dependencies" |
| Yahoo Finance | Unofficial API | No published SLA; can start blocking without notice |
| OpenFIGI | Official API | 25 req/min unauthenticated, 25 req/6s with a free `OPENFIGI_API_KEY` |

Personal tool, scraping and re-serving data from services whose terms may not permit
that at scale. Don't point it at production traffic or redistribute the scraped data.

## Tools

| Tool | Source | Purpose |
|---|---|---|
| `search_etfs` | justETF | Discover funds by filter |
| `get_etf_profile` | justETF | Full single-fund profile |
| `compare_etfs` | justETF | Side-by-side summary for multiple ISINs |
| `get_etf_listings` | OpenFIGI | Exchange listings + Yahoo ticker for an ISIN |
| `get_quote` / `get_quotes` | Yahoo → justETF Gettex | Latest price, one or many |
| `get_history` | Yahoo | OHLCV series or computed summary stats |
| `portfolio_xray` | justETF | Look-through country/sector/single-name exposure across holdings |
| `compute_overlap` | justETF | Top-10-holdings overlap between two funds |
| `find_alternatives` | justETF | Funds tracking a similar index, cheapest-first by TER |

Every parameter, response field, and edge case is documented in the tool's own
docstring — that's the source of truth an MCP client reads directly (`fastmcp inspect
src/etf_scout_mcp/server.py` to browse them, or just ask the connected agent).

**Conventions:** TER is a decimal (`0.002` = 20 bps). Money is in EUR unless the field
name says otherwise. Dates are ISO 8601. `return_1y/3y/5y` are **cumulative**, not
annualised (`return_3y_annualised_pct`/`return_5y_annualised_pct` give the CAGR), in
the fund's own reporting currency — not converted. `data_as_of` is when a row was
scraped, never today's date on a cache hit.

---

## Install — Claude Desktop (local, no hosting needed)

1. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it.
2. `git clone https://github.com/pierdom/etf-scout-mcp && cd etf-scout-mcp && uv sync`
3. Run `which uv` and copy the full path it prints.
4. Open `~/.config/Claude/claude_desktop_config.json` (create it if it doesn't exist)
   and add:
   ```json
   {
     "mcpServers": {
       "etf-scout-mcp": {
         "command": "<paste the path from step 3>",
         "args": ["run", "--project", "<paste the full path to the repo you cloned>", "etf-scout-mcp"]
       }
     }
   }
   ```
5. **Fully quit Claude Desktop (not just close the window) and reopen it** — it does not
   hot-reload this file.
6. Check the tools/hammer icon in the chat input — it should list 10 tools under
   "etf-scout-mcp". If it doesn't, re-check the two paths in step 4 are absolute and
   exist.

This is the whole setup for personal use. Nothing below this point is required unless
you specifically need one of the two things it covers: a shared HTTP server, or access
from a client that can't run a local process (see OIDC below).

## Install — Docker / HTTP (Claude Code CLI, or any client that accepts a custom header)

Use this if you want the server running once on a machine (e.g. a home server) and
reachable from multiple devices/clients, instead of a local process per machine.

1. `cp .env.example .env`
2. Generate a token and put it in `.env`: `openssl rand -hex 32` → set
   `ETF_SCOUT_MCP_HTTP_BEARER_TOKEN=<that value>`
3. `docker compose up -d` (uses the `docker-compose.yml` in this repo — pulls the
   published image, no build needed)
4. Point your client at `http://<host>:8765/mcp` with header
   `Authorization: Bearer <your-token>`. The URL **must** end in `/mcp` — a bare host
   404s with a generic "couldn't connect."

This is enough for Claude Code CLI and anything else that lets you set a header. It is
**not** enough for claude.ai's Connectors UI or the Claude mobile/desktop apps' remote
integrations — those require OAuth and won't accept a bearer token. That's what OIDC
below is for.

## Optional: OAuth login for clients that require it (e.g. claude.ai)

Skip this section entirely if you're using stdio (Claude Desktop, local) or a client
that accepts the bearer token above (Claude Code CLI). It exists for one reason:
**claude.ai's remote-connector UI and the Claude apps only support servers that offer
a real OAuth login — they won't take a static header.**

**What it requires that the setups above don't:** the server has to be reachable from
the public internet at a fixed URL. The cheapest VPS with a domain pointed at it (or
even a free dynamic-DNS hostname) is enough — it doesn't need to be powerful, just
reachable. If you don't have that, you can't use this path; use HTTP+bearer with a
client that supports it instead, or stdio locally.

**Why it's not just "add an OAuth provider":** claude.ai requires Dynamic Client
Registration (DCR), which most self-hosted identity providers (PocketID, Authelia,
etc.) don't implement. FastMCP's `OIDCProxy` sits in front of your real IdP and
presents a DCR-compliant login to the client — so you still need an IdP (anything
OIDC-compliant), but this server brokers the DCR gap for you. It's a few env vars and
one client registration, not custom OAuth code.

**Setup:**
1. Register a confidential OIDC client on your IdP, with PKCE enabled and redirect URI
   `https://<your-domain>/auth/callback`.
2. **Assign that client an allowed user group on the IdP.** Skipping this is the most
   common setup mistake — login reaches the IdP, you approve it, then it fails with
   `access_denied`.
3. Set in `.env`: `OIDC_CONFIG_URL` (the IdP's `.well-known/openid-configuration` URL),
   `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_BASE_URL` (your public URL). All four
   must be set together — the server falls back to the bearer token if any is missing.
   Full var list with defaults: `.env.example`.
4. Set `XDG_DATA_HOME=/data` and keep the volume mount in `docker-compose.yml` — this
   is where the encrypted OAuth client/token store lives. If you lose this volume,
   every client has to re-register and every user has to log in again.
5. Reverse proxy in front of it needs WebSockets enabled, `proxy_buffering off`, and a
   long `proxy_read_timeout`/`proxy_send_timeout` (3600s) — the MCP streaming
   transport holds connections open much longer than a typical request.
6. In Claude, add the connector with URL `https://<your-domain>/mcp` — you'll be sent
   through a login flow instead of a token prompt.

That's it — not hard, just needs the public URL as a prerequisite.

---

## Caching

Every outbound call is cached in SQLite with per-source TTLs: justETF/OpenFIGI 24h,
Yahoo quotes 60s, Yahoo history 1h (`ETF_SCOUT_MCP_CACHE_TTL_*`). Set
`ETF_SCOUT_MCP_CACHE_ENABLED=false` to disable it. Every call and cache hit/miss is
logged to `~/.cache/etf-scout-mcp/calls.log` — check here first for data-source issues.

## Development

```bash
uv sync --group dev
uv run pytest tests/                 # offline unit tests (fixtures, no network)
uv run python tests/smoke_justetf.py # live-network smoke tests, run manually
uv run fastmcp dev src/etf_scout_mcp/server.py   # MCP Inspector in browser
```

No configured linter — match the existing style (double quotes, `from __future__
import annotations`, Pydantic for response models).

## Known limitations

- `search_etfs` rows lack `distribution_frequency` — only the single-ISIN profile
  scrape has it; adding it to the screener would mean one extra scrape per row.
- `exclude_leveraged`/`leverage_factor` are a name-regex heuristic, not justETF ground
  truth. An unset `leverage_factor` is not proof a fund is unleveraged.
- `compute_overlap`/`portfolio_xray`'s single-name figures are capped at each fund's
  disclosed top 10 holdings (justETF doesn't publish full constituent lists) — always
  flagged via `approximate`/`concentration_approximate`.
- Per-source timeouts/retry delays are hardcoded, not configurable via env.

## Fragile dependencies

- **justetf-scraping** is pinned to a Git commit. If scraping breaks, check
  `git ls-remote https://github.com/druzsan/justetf-scraping HEAD` for a newer commit,
  re-pin it in `pyproject.toml`, then `uv lock && uv sync && uv run python tests/smoke_justetf.py`.
- **Yahoo Finance** is unofficial and can change without notice. The `curl_cffi`
  Firefox TLS fingerprint is what keeps it working; check for a `yfinance` update
  first if quotes regress.

## Troubleshooting

- **Yahoo errors/stale data** — check `calls.log`; pass `isin` to `get_quote` so it can
  fall back to justETF's Gettex quote.
- **justETF returning nothing** — results cache up to 24h; delete `ETF_SCOUT_MCP_CACHE`
  or set `ETF_SCOUT_MCP_CACHE_ENABLED=false` to force a fresh fetch.
- **`get_etf_listings` empty** — OpenFIGI may have no mapping for a very new ISIN;
  check `calls.log` for a `warning` from the API.
- **401 on HTTP transport** — confirm `ETF_SCOUT_MCP_HTTP_BEARER_TOKEN` matches on both
  ends, and that `.env` is actually being loaded (same directory as `docker-compose.yml`).
