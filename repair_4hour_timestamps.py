"""One-time repair: fix 4-hour bars corrupted by a timezone bug in
LongPortProvider.fetch_bars (fixed in this same commit).

Root cause: the LongPort SDK returns candle timestamps as naive datetimes
representing the *calling process's local wall-clock time*, not UTC. The old
code assumed a naive timestamp was already UTC — correct by coincidence on a
UTC-configured host, but wrong by exactly the host's UTC offset everywhere
else. A local run from a UTC+8 machine during development produced a batch of
SLB.US 4-hour bars shifted 8 hours into the future, which then made it into
this deployment's data/ directory and silently overwrote some correct
historical bars wherever the shifted timestamp happened to collide with an
existing one (Parquet merge dedups by exact timestamp, not by content).

The contaminated batch has one unmistakable fingerprint: every row it wrote
shares the exact same updated_at value. This script:
  1. Scans every 4-hour symbol for that exact updated_at.
  2. For each affected symbol, refetches a fresh 1000-bar history with the
     now-fixed timestamp handling and merges it in (this alone does not
     remove the contaminated rows — their now-wrong timestamps generally
     don't collide with the correct ones, so dedup can't drop them).
  3. Deletes every row still carrying the contaminated updated_at.
  4. Refreshes market_cache_manifest for each repaired symbol.

Requires a live LongPort connection, so run it with the service stopped
(DuckDB only allows one writer).

Usage:
    python repair_4hour_timestamps.py
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from urllib.parse import unquote

import pandas as pd

from app.config import get_settings
from app.database import Database
from app.market_data import LongPortProvider, ParquetBarRepository

CONTAMINATED_UPDATED_AT = pd.Timestamp("2026-09-01T10:56:21.343632", tz="UTC")
REQUEST_GAP_SECONDS = 0.5


def find_affected_symbols(repository: ParquetBarRepository) -> list[str]:
    root = repository.root / "timeframe=4hour"
    if not root.is_dir():
        return []
    affected = []
    for symbol_dir in sorted(root.iterdir()):
        if not symbol_dir.is_dir() or not symbol_dir.name.startswith("symbol="):
            continue
        symbol = unquote(symbol_dir.name.removeprefix("symbol="))
        bars = repository.read(symbol, "4hour")
        if not bars.empty and (bars["updated_at"] == CONTAMINATED_UPDATED_AT).any():
            affected.append(symbol)
    return affected


def repair_symbol(repository: ParquetBarRepository, provider: LongPortProvider, database: Database, symbol: str) -> tuple[int, int]:
    fetched = provider.fetch_bars(symbol, "4hour", 1000)
    merged = repository.merge(symbol, "4hour", fetched)
    before = len(merged)
    cleaned = merged[merged["updated_at"] != CONTAMINATED_UPDATED_AT].sort_values("timestamp_utc").reset_index(drop=True)
    after = len(cleaned)
    path = repository.path_for(symbol, "4hour")
    cleaned.to_parquet(path, index=False)
    with database.connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO market_cache_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                symbol, "4hour", str(path), after,
                cleaned.timestamp_utc.min(), cleaned.timestamp_utc.max(),
                "forward", "all", "ok", datetime.now(timezone.utc), None,
            ],
        )
    return before, after


def main() -> None:
    settings = get_settings()
    repository = ParquetBarRepository(settings.parquet_root, settings.cache_retention_bars)

    affected = find_affected_symbols(repository)
    if not affected:
        print("No symbols carry the contaminated updated_at. Nothing to repair.")
        return
    print(f"Affected symbols: {affected}")

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

    for symbol in affected:
        try:
            before, after = repair_symbol(repository, provider, database, symbol)
            print(f"{symbol:10s}  {before:5d} -> {after:5d} bars (contaminated rows removed)")
        except Exception as exc:
            print(f"{symbol:10s}  FAILED: {exc}")
        time.sleep(REQUEST_GAP_SECONDS)

    database.close()
    print()
    print("Done.")


if __name__ == "__main__":
    main()
