"""One-time repair: refetch full 4-hour history for every watchlist symbol.

Root cause (see scan_service.py's FOUR_HOUR_TAIL_REFRESH_BARS comment): once
a symbol's initial 1000-bar backfill aged out of freshness, every following
4-hour sync only requested the most recent settings.tail_refresh_bars (3)
bars. 4-hour bars close irregularly and can land up to ~5-6 per trading day,
so a sync that only asks for the last 3 permanently skipped whichever bars
fell outside that window — in practice the regular-session/after-hours bars
(UTC ~16:00/20:00) kept getting silently dropped from ~2026-08-25 onward.

This script re-requests a full history window (bars_per_timeframe, same as a
fresh symbol's initial backfill) for the 4-hour timeframe only and merges it
in. Ordinary timestamp-keyed dedup handles the merge correctly here — unlike
the trade_session migration, 4-hour never changed session type, so there's
no cross-session timestamp mismatch to worry about; this just fills in the
bars earlier tail-only syncs missed.

Stop the app first: DuckDB only allows one writer, and this needs a live
LongPort connection, so run it with the service down.

Usage:
    python backfill_4hour_gap.py
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
            fetched = provider.fetch_bars(symbol, "4hour", settings.bars_per_timeframe)
            before = len(repository.read(symbol, "4hour"))
            merged = repository.merge(symbol, "4hour", fetched)
            after = len(merged)
            with database.connect() as c:
                c.execute(
                    "INSERT OR REPLACE INTO market_cache_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        symbol, "4hour", str(repository.path_for(symbol, "4hour")),
                        after, merged.timestamp_utc.min(), merged.timestamp_utc.max(),
                        "forward", "all", "ok", datetime.now(timezone.utc), None,
                    ],
                )
            succeeded += 1
            note = f"  (+{after - before} bars)" if after != before else "  (no change)"
            print(f"{symbol:10s}  {before:5d} -> {after:5d}{note}")
        except Exception as exc:
            failed += 1
            print(f"{symbol:10s}  FAILED: {exc}")
        time.sleep(REQUEST_GAP_SECONDS)

    database.close()
    print()
    print(f"Done: {succeeded} succeeded, {failed} failed out of {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
