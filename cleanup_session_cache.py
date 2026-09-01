"""One-time maintenance: purge stale trade_session="all" rows left behind by
the daily/weekly TradeSessions.All -> TradeSessions.Intraday migration, and
resync market_cache_manifest so the "数据管理" tab shows the real bar count.

Before the fix in ParquetBarRepository.merge(), a session-type switch merged
old "all"-session bars with newly-fetched "intraday"-session bars without
dropping the stale ones, since they carry different timestamps for the same
calendar bar. Symbols resynced during that window ended up with roughly
double the expected bar count (e.g. ~2000 instead of ~1000 daily bars).

market_cache_manifest only gets written on a live sync, so even after the
Parquet file itself is fixed, the manifest row (and therefore the UI) keeps
showing the stale pre-cleanup count until something writes a fresh row. This
script does that for every daily/weekly symbol, not just the ones that had a
session mix — safe to re-run any time, including with nothing left to clean.

Stop the app first: DuckDB only allows one writer, so this will fail with a
lock error while the server process is holding data/scanner.duckdb open.

Run once after pulling the fix:
    python cleanup_session_cache.py            # dry run, reports what would change
    python cleanup_session_cache.py --apply    # actually rewrites files + manifest
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from urllib.parse import unquote

import pandas as pd

from app.config import get_settings
from app.database import Database
from app.market_data import ParquetBarRepository

AFFECTED_TIMEFRAMES = ("weekly", "daily")
TARGET_SESSION = "intraday"


def clean_bars(bars: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Drop stale non-target-session rows if the file has a session mix.
    Returns (possibly cleaned bars, whether a session-mix cleanup happened)."""
    sessions = set(bars["trade_session"].unique())
    if TARGET_SESSION not in sessions or len(sessions) <= 1:
        return bars, False
    cleaned = bars[bars["trade_session"] == TARGET_SESSION].sort_values("timestamp_utc").reset_index(drop=True)
    return cleaned, True


def update_manifest(database: Database, repository: ParquetBarRepository, symbol: str, timeframe: str, bars: pd.DataFrame) -> None:
    with database.connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO market_cache_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                symbol, timeframe, str(repository.path_for(symbol, timeframe)),
                len(bars), bars.timestamp_utc.min(), bars.timestamp_utc.max(),
                "forward", str(bars["trade_session"].iloc[-1]), "ok", datetime.now(timezone.utc), None,
            ],
        )


def manifest_row_count(database: Database, symbol: str, timeframe: str) -> int | None:
    with database.connect(read_only=True) as c:
        row = c.execute(
            "SELECT row_count FROM market_cache_manifest WHERE symbol=? AND timeframe=?",
            [symbol, timeframe],
        ).fetchone()
    return row[0] if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually rewrite affected Parquet files and refresh the cache manifest (default: dry run)")
    args = parser.parse_args()

    settings = get_settings()
    repository = ParquetBarRepository(settings.parquet_root, settings.cache_retention_bars)
    database = Database(settings.database_path)
    if args.apply:
        database.initialize()

    cleaned_count = 0
    manifest_fixed_count = 0
    for timeframe in AFFECTED_TIMEFRAMES:
        root = settings.parquet_root / f"timeframe={timeframe}"
        if not root.is_dir():
            continue
        for symbol_dir in sorted(root.iterdir()):
            if not symbol_dir.is_dir() or not symbol_dir.name.startswith("symbol="):
                continue
            symbol = unquote(symbol_dir.name.removeprefix("symbol="))
            bars = repository.read(symbol, timeframe)
            if bars.empty:
                continue
            cleaned, did_clean = clean_bars(bars)
            if did_clean:
                cleaned_count += 1
                action = "rewrote" if args.apply else "would rewrite"
                print(f"{symbol:10s} {timeframe:6s}  {len(bars):5d} -> {len(cleaned):5d} bars  ({action})")
                if args.apply:
                    path = repository.path_for(symbol, timeframe)
                    cleaned.to_parquet(path, index=False)

            if args.apply:
                stored = manifest_row_count(database, symbol, timeframe)
                if stored != len(cleaned):
                    update_manifest(database, repository, symbol, timeframe, cleaned)
                    manifest_fixed_count += 1
                    if not did_clean:
                        print(f"{symbol:10s} {timeframe:6s}  manifest {stored} -> {len(cleaned)} (file already clean, manifest was stale)")

    if args.apply:
        database.close()

    print()
    if cleaned_count == 0 and manifest_fixed_count == 0:
        print("Nothing to do: no session-mix files and no stale manifest rows found.")
    elif args.apply:
        print(f"Cleaned {cleaned_count} symbol/timeframe caches; refreshed {manifest_fixed_count} manifest rows.")
    else:
        print(f"{cleaned_count} symbol/timeframe caches would be cleaned. Re-run with --apply to write changes (manifest rows are only checked/fixed in --apply mode).")


if __name__ == "__main__":
    main()
