"""One-time maintenance: purge stale trade_session="all" rows left behind by
the daily/weekly TradeSessions.All -> TradeSessions.Intraday migration.

Before the fix in ParquetBarRepository.merge(), a session-type switch merged
old "all"-session bars with newly-fetched "intraday"-session bars without
dropping the stale ones, since they carry different timestamps for the same
calendar bar. Symbols resynced during that window ended up with roughly
double the expected bar count (e.g. ~2000 instead of ~1000 daily bars).

This also refreshes market_cache_manifest (the row_count/timestamps shown on
the "数据管理" tab), which only gets written on a live sync — without this,
the Parquet file would be fixed but the UI would keep showing the stale
pre-cleanup counts until the next scan.

Run once after pulling the fix:
    python cleanup_session_cache.py            # dry run, reports what would change
    python cleanup_session_cache.py --apply    # actually rewrites the affected files
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from urllib.parse import unquote

from app.config import get_settings
from app.database import Database
from app.market_data import ParquetBarRepository

AFFECTED_TIMEFRAMES = ("weekly", "daily")
TARGET_SESSION = "intraday"


def clean_symbol(repository: ParquetBarRepository, symbol: str, timeframe: str, apply: bool):
    bars = repository.read(symbol, timeframe)
    if bars.empty:
        return None
    sessions = set(bars["trade_session"].unique())
    if TARGET_SESSION not in sessions or len(sessions) <= 1:
        return None  # not migrated yet, or already clean
    before = len(bars)
    cleaned = bars[bars["trade_session"] == TARGET_SESSION].sort_values("timestamp_utc").reset_index(drop=True)
    after = len(cleaned)
    if apply:
        path = repository.path_for(symbol, timeframe)
        cleaned.to_parquet(path, index=False)
    return before, after, cleaned


def update_manifest(database: Database, repository: ParquetBarRepository, symbol: str, timeframe: str, bars) -> None:
    with database.connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO market_cache_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                symbol, timeframe, str(repository.path_for(symbol, timeframe)),
                len(bars), bars.timestamp_utc.min(), bars.timestamp_utc.max(),
                "forward", TARGET_SESSION, "ok", datetime.now(timezone.utc), None,
            ],
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually rewrite affected Parquet files and refresh the cache manifest (default: dry run)")
    args = parser.parse_args()

    settings = get_settings()
    repository = ParquetBarRepository(settings.parquet_root, settings.cache_retention_bars)
    database = Database(settings.database_path)
    if args.apply:
        database.initialize()

    total_affected = 0
    for timeframe in AFFECTED_TIMEFRAMES:
        root = settings.parquet_root / f"timeframe={timeframe}"
        if not root.is_dir():
            continue
        for symbol_dir in sorted(root.iterdir()):
            if not symbol_dir.is_dir() or not symbol_dir.name.startswith("symbol="):
                continue
            symbol = unquote(symbol_dir.name.removeprefix("symbol="))
            result = clean_symbol(repository, symbol, timeframe, args.apply)
            if result:
                before, after, cleaned = result
                total_affected += 1
                action = "rewrote" if args.apply else "would rewrite"
                print(f"{symbol:10s} {timeframe:6s}  {before:5d} -> {after:5d} bars  ({action})")
                if args.apply:
                    update_manifest(database, repository, symbol, timeframe, cleaned)

    if args.apply:
        database.close()

    print()
    if total_affected == 0:
        print("No affected symbols found.")
    elif args.apply:
        print(f"Cleaned {total_affected} symbol/timeframe caches and refreshed their cache manifest rows.")
    else:
        print(f"{total_affected} symbol/timeframe caches would be cleaned. Re-run with --apply to write changes.")


if __name__ == "__main__":
    main()
