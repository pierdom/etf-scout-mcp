"""P1-9 regression: cache disable switch and hit/miss logging to calls.log."""
from __future__ import annotations

from etf_scout_mcp import cache as cache_module


async def test_cache_disabled_always_calls_through(isolated_cache, monkeypatch):
    monkeypatch.setattr(cache_module.config, "cache_enabled", False)

    call_count = 0

    @cache_module.cached(ttl_key="quote")
    async def fake_fetch(symbol: str) -> dict:
        nonlocal call_count
        call_count += 1
        return {"symbol": symbol}

    await fake_fetch("A")
    await fake_fetch("A")

    assert call_count == 2  # never cached


async def test_cache_enabled_logs_hit_and_miss(isolated_cache, tmp_path):
    @cache_module.cached(ttl_key="quote")
    async def fake_fetch(symbol: str) -> dict:
        return {"symbol": symbol}

    await fake_fetch("A")  # miss
    await fake_fetch("A")  # hit

    log_path = cache_module.config.cache_path.parent / "calls.log"
    log_text = log_path.read_text()

    assert "miss fn=fake_fetch" in log_text
    assert "hit fn=fake_fetch" in log_text
