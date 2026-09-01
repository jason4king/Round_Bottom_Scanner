"""Rebuild every watchlist symbol's 4-hour cache from scratch.

Simpler and more robust than detecting/repairing specific corruption
patterns: deletes each symbol's 4-hour Parquet file outright and refetches a
fresh bars_per_timeframe-sized history with the now-fixed timestamp handling
(see market_data.py's fetch_bars fix), so there's no ambiguity left about
which rows were correct. Older history beyond bars_per_timeframe (~1000
bars, same depth a brand-new symbol starts with) is not preserved.

Requires a live LongPort connection, so run it with the service stopped
(DuckDB only allows one writer).

Usage:
    python rebuild_4hour_cache.py
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from app.config import get_settings
from app.database import Database
from app.market_data import LongPortProvider, ParquetBarRepository
from app.watchlist import load_watchlist

REQUEST_GAP_SECONDS = 0.5


def main() -> None:
    settings = get_settings()
    repository = ParquetBarRepository(settings.parquet_root, settings.cache_retention_bars)
    provider = LongPortProvider(
        settings.longport_configured, settings.longport_app_key, settings.longport_app_secret,
        settings.longport_access_token, settings.longport_auth_mode, settings.longport_oauth_client_id,
        settings.longport_region, settings.network_proxy_enabled, settings.network_proxy_host, settings.network_proxy_port,
    )
    if not provider.configured:
        raise SystemExit("LongPort 凭据尚未配置")
    if settings.longport_auth_mode.lower() == "oauth":
        provider.ensure_authenticated()

    database = Database(settings.database_path)
    database.initialize()

    symbols = load_watchlist(settings.watchlist_path).symbols
    succeeded = failed = 0
    for symbol in symbols:
        try:
            path = repository.path_for(symbol, "4hour")
            before = len(repository.read(symbol, "4hour"))
            if path.is_file():
                path.unlink()
            fetched = provider.fetch_bars(symbol, "4hour", settings.bars_per_timeframe)
            merged = repository.merge(symbol, "4hour", fetched)
            with database.connect() as c:
                c.execute(
                    "INSERT OR REPLACE INTO market_cache_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        symbol, "4hour", str(path), len(merged),
                        merged.timestamp_utc.min(), merged.timestamp_utc.max(),
                        "forward", "all", "ok", datetime.now(timezone.utc), None,
                    ],
                )
            succeeded += 1
            print(f"{symbol:10s}  {before:5d} -> {len(merged):5d} bars")
        except Exception as exc:
            failed += 1
            print(f"{symbol:10s}  FAILED: {exc}")
        time.sleep(REQUEST_GAP_SECONDS)

    database.close()
    print()
    print(f"Done: {succeeded} succeeded, {failed} failed out of {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
