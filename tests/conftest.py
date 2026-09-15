"""Shared pytest fixtures.

Tests exercise the MCP tools either directly (calling the tool-module's
`fetch_one`/pure helpers) or, for the closures registered onto the FastMCP
instance via `register(mcp)`, through FastMCP's in-memory `Client` against
`etf_scout_mcp.server.mcp`. Either way, the actual network-facing source
functions (`sources/yahoo.py`, `sources/justetf.py`, `sources/openfigi.py`)
are monkeypatched per-test — no live network calls happen in this suite.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastmcp import Client

from etf_scout_mcp.server import mcp


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as client:
        yield client


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Point the SQLite TTL cache at a throwaway file for this test only.

    Needed for any test that exercises a @cached(...) source function directly
    (rather than via a monkeypatched replacement) — otherwise it would read/
    write the real ~/.cache/etf-scout-mcp/cache.db and could return stale
    cached data instead of calling the (monkeypatched) upstream fetch.
    """
    from etf_scout_mcp import cache as cache_module

    monkeypatch.setattr(cache_module.config, "cache_path", tmp_path / "test-cache.db")
    monkeypatch.setattr(cache_module, "_conn", None)
    _reset_log_handlers(cache_module._log)
    yield
    monkeypatch.setattr(cache_module, "_conn", None)
    _reset_log_handlers(cache_module._log)


def _reset_log_handlers(logger) -> None:
    """Module-level loggers only attach a handler once (`if _log.handlers:
    return`) — close and clear it so _ensure_log_handler() re-attaches
    against the test's tmp_path instead of a previous test's."""
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
