"""Extend cached daily bars to a target history length with LongPort history paging.

Run locally after configuring LongPort credentials. Existing cache files are only
written after a complete, validated per-symbol merge.
"""
from __future__ import annotations

import argparse
import math
import time
from datetime import datetime

import pandas as pd
from dateutil import tz
from longport.openapi import AdjustType, Period, TradeSessions

from app.config import get_settings
from app.market_data import BAR_COLUMNS, LongPortProvider, ParquetBarRepository, _is_closed
from app.watchlist import load_watchlist


def _history_page(provider: LongPortProvider, symbol: str, count: int, before: datetime | None) -> pd.DataFrame:
    provider.ensure_authenticated()
    candles = provider._context.history_candlesticks_by_offset(
        symbol,
        Period.Day,
        AdjustType.ForwardAdjust,
        True if before is None else False,
        count,
        before,
        TradeSessions.Intraday,
    )
    now = pd.Timestamp.now(tz="UTC")
    rows = []
    for candle in candles:
        # Match LongPortProvider.fetch_bars: the SDK returns naive datetimes as
        # local wall time, so localize them before converting to UTC. This also
        # matches the timestamps used by the existing daily cache.
        wall = pd.Timestamp(candle.timestamp)
        if wall.tzinfo is None:
            wall = wall.tz_localize(tz.tzlocal())
        timestamp = wall.tz_convert("UTC")
        rows.append({
            "symbol": symbol,
            "timeframe": "daily",
            "timestamp_utc": timestamp,
            "open": float(candle.open),
            "high": float(candle.high),
            "low": float(candle.low),
            "close": float(candle.close),
            "volume": float(candle.volume),
            "is_closed": _is_closed(timestamp, "daily", now),
            "adjustment_type": "forward",
            "trade_session": "intraday",
            "data_source": "LongPort-history",
            "updated_at": now,
        })
    return pd.DataFrame(rows, columns=BAR_COLUMNS)


def _merge_by_session_date(existing: pd.DataFrame, historical: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if existing.empty:
        combined = historical.copy()
    elif historical.empty:
        combined = existing.copy()
    else:
        combined = pd.concat([existing, historical], ignore_index=True)
    combined["timestamp_utc"] = pd.to_datetime(combined["timestamp_utc"], utc=True)
    combined["updated_at"] = pd.to_datetime(combined["updated_at"], utc=True)
    combined["session_date"] = combined["timestamp_utc"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    # Prefer current-format intraday data on overlapping dates; for old-only
    # dates, the newly fetched page supplies the bar.
    combined["session_priority"] = combined["data_source"].eq("LongPort-history").astype(int)
    combined = combined.sort_values(["session_date", "session_priority", "updated_at"])
    combined = combined.drop_duplicates(["symbol", "timeframe", "session_date", "adjustment_type"], keep="last")
    combined = combined.drop(columns=["session_date", "session_priority"])
    combined = combined.sort_values("timestamp_utc").reset_index(drop=True)
    if combined["symbol"].nunique() != 1 or combined["symbol"].iloc[0] != symbol:
        raise ValueError(f"Invalid merged symbol data for {symbol}")
    return combined.loc[:, BAR_COLUMNS]


def extend_symbol(provider: LongPortProvider, repository: ParquetBarRepository, symbol: str, target_bars: int, page_size: int, dry_run: bool = False) -> tuple[int, str, str]:
    existing = repository.read(symbol, "daily")
    if existing.empty:
        return 0, "missing_cache", ""
    current = _merge_by_session_date(existing, pd.DataFrame(columns=BAR_COLUMNS), symbol)
    existing_start = current["timestamp_utc"].min()
    need = max(0, target_bars - len(current))
    history_parts: list[pd.DataFrame] = []
    before = existing_start.to_pydatetime()
    previous_min = existing_start
    pages = 0
    while need > 0:
        page = _history_page(provider, symbol, min(page_size, need), before)
        if page.empty:
            break
        page = _merge_by_session_date(pd.DataFrame(columns=BAR_COLUMNS), page, symbol)
        page = page.loc[page["timestamp_utc"] < existing_start]
        if page.empty:
            break
        page_min = page["timestamp_utc"].min()
        if page_min >= previous_min:
            raise RuntimeError(f"History pages did not move backward for {symbol}")
        previous_min = page_min
        history_parts.append(page)
        pages += 1
        need = max(0, target_bars - len(current) - sum(map(len, history_parts)))
        before = page_min.to_pydatetime()
        if len(page) < min(page_size, target_bars):
            break
        time.sleep(0.05)
    historical = pd.concat(history_parts, ignore_index=True) if history_parts else pd.DataFrame(columns=BAR_COLUMNS)
    merged = _merge_by_session_date(current, historical, symbol)
    if not dry_run and not historical.empty:
        path = repository.path_for(symbol, "daily")
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(path, index=False)
    return len(merged), f"pages={pages};added={len(merged)-len(current)}", merged["timestamp_utc"].min().date().isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-bars", type=int, default=1800)
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--symbol", action="append", help="Limit to selected symbol(s), e.g. AAPL.US")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without writing Parquet files")
    parser.add_argument("--sleep", type=float, default=0.1, help="Delay between symbols in seconds")
    args = parser.parse_args()
    if args.target_bars < 1000 or args.page_size < 1 or args.page_size > 1000:
        parser.error("target-bars must be >=1000; page-size must be between 1 and 1000")

    settings = get_settings()
    provider = LongPortProvider(
        settings.longport_configured,
        settings.longport_app_key,
        settings.longport_app_secret,
        settings.longport_access_token,
        "apikey",  # Avoid interactive OAuth callback on a local history batch.
        settings.longport_oauth_client_id,
        settings.longport_region,
        settings.network_proxy_enabled,
        settings.network_proxy_host,
        settings.network_proxy_port,
    )
    repository = ParquetBarRepository(settings.parquet_root)
    symbols = args.symbol or load_watchlist(settings.watchlist_path).symbols
    provider.ensure_authenticated()
    summary = {"ok": 0, "errors": 0}
    for index, symbol in enumerate(symbols, 1):
        try:
            count, detail, earliest = extend_symbol(provider, repository, symbol, args.target_bars, args.page_size, args.dry_run)
            print(f"[{index}/{len(symbols)}] {symbol}: {count} daily bars, {detail}, earliest={earliest}", flush=True)
            summary["ok"] += 1
        except Exception as exc:
            print(f"[{index}/{len(symbols)}] {symbol}: ERROR {type(exc).__name__}: {exc}", flush=True)
            summary["errors"] += 1
        time.sleep(args.sleep)
    print(f"Completed: {summary['ok']} ok, {summary['errors']} errors")


if __name__ == "__main__":
    main()
