"""Normalize cached US-market bars to New York calendar labels and wall time.

Parquet keeps UTC-aware instants; daily/weekly candles use NY midnight labels,
and 4-hour bars preserve NY local wall time with the correct historical offset.
DuckDB scan references and cache manifests are updated to match.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import exchange_calendars as xcals
import pandas as pd

from app.config import get_settings
from app.market_data import BAR_COLUMNS, ParquetBarRepository, _is_closed
from app.watchlist import load_watchlist


NY = ZoneInfo("America/New_York")
CAL = xcals.get_calendar("XNYS", start="1900-01-01", end="2035-12-31")


def _session_close(session_date: str) -> pd.Timestamp:
    label = pd.Timestamp(session_date)
    if not CAL.is_session(label):
        raise ValueError(f"Not an NYSE trading date: {session_date}")
    return pd.Timestamp(CAL.session_close(label)).tz_convert("UTC")


def _canonical_bar_timestamp(timestamp: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    ts = pd.Timestamp(timestamp).tz_convert("UTC")
    ny = ts.tz_convert(NY)
    if timeframe == "4hour":
        return ts
    if timeframe == "daily":
        # LongPort labels a daily candle by its NY trading date at 00:00.
        # Older cache builds stored this wall time as 08:00 or previous-day 23:00.
        session = pd.Timestamp(ny.date())
        if ny.hour == 23:
            session += pd.Timedelta(days=1)
        return session.tz_localize(NY).tz_convert("UTC")
    if timeframe == "weekly":
        # LongPort labels weekly candles at Monday 00:00 NY; legacy data has
        # the same label at 08:00 or Sunday 23:00.
        date = pd.Timestamp(ny.date())
        if ny.hour == 23:
            date += pd.Timedelta(days=1)
        monday = date - pd.Timedelta(days=date.weekday())
        sessions = CAL.sessions_in_range(monday, monday + pd.Timedelta(days=6))
        if sessions.empty:
            raise ValueError(f"No NYSE sessions in ISO week {monday.isocalendar().year}-W{monday.isocalendar().week:02d}")
        return monday.tz_localize(NY).tz_convert("UTC")
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _normalize_frame(frame: pd.DataFrame, timeframe: str, now: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, int]]:
    if frame.empty:
        return frame, {"rows": 0, "shifted_dst": 0, "deduped": 0}
    frame = frame.copy()
    source = frame["data_source"].fillna("").astype(str)
    sessions = frame["trade_session"].fillna("").astype(str)
    changed = 0
    if timeframe == "4hour":
        pass  # Exchange instants from LongPort are unambiguous; retain them.
    else:
        converted = []
        old_ny = pd.to_datetime(frame["timestamp_utc"], utc=True).dt.tz_convert(NY)
        changed = int(old_ny.dt.hour.isin([8, 23]).sum())
        for timestamp in frame["timestamp_utc"]:
            converted.append(_canonical_bar_timestamp(timestamp, timeframe))
        frame["timestamp_utc"] = pd.to_datetime(converted, utc=True)

    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    # Coalesce rows that represented one NY trading date / ISO week under two
    # timestamp conventions. Prefer current LongPort intraday bars, then history.
    ny = frame["timestamp_utc"].dt.tz_convert(NY)
    if timeframe == "daily":
        frame["_key"] = ny.dt.strftime("%Y-%m-%d")
    elif timeframe == "weekly":
        iso = ny.dt.isocalendar()
        frame["_key"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
    else:
        frame["_key"] = frame["timestamp_utc"].astype(str)
    frame["_priority"] = source.eq("LongPort").astype(int) * 2 + sessions.eq("intraday").astype(int)
    before = len(frame)
    frame = frame.sort_values(["_key", "_priority", "updated_at"])
    frame = frame.drop_duplicates(["symbol", "timeframe", "_key", "adjustment_type"], keep="last")
    deduped = before - len(frame)
    frame = frame.drop(columns=["_key", "_priority"]).sort_values("timestamp_utc").reset_index(drop=True)
    if timeframe == "4hour":
        frame["is_closed"] = frame["timestamp_utc"] + pd.Timedelta(hours=4) <= now
    else:
        ny_dates = frame["timestamp_utc"].dt.tz_convert(NY).dt.date
        today = now.tz_convert(NY).date()
        frame["is_closed"] = ny_dates < today
    return frame.loc[:, BAR_COLUMNS], {"rows": len(frame), "shifted_dst": changed, "deduped": deduped}


def _fix_db_timestamp(value, timeframe: str):
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    if timeframe == "4hour":
        return _canonical_bar_timestamp(ts, timeframe).to_pydatetime()
    return _canonical_bar_timestamp(ts, timeframe).to_pydatetime()


def _migrate_database(database_path: Path, summaries: dict[tuple[str, str], dict[str, int]], repository: ParquetBarRepository) -> None:
    con = duckdb.connect(str(database_path))
    try:
        now = pd.Timestamp.now(tz="UTC").to_pydatetime()
        for (symbol, timeframe), stats in summaries.items():
            bars = repository.read(symbol, timeframe)
            if bars.empty:
                continue
            path = str(repository.path_for(symbol, timeframe))
            con.execute(
                """INSERT OR REPLACE INTO market_cache_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [symbol, timeframe, path, len(bars), bars.timestamp_utc.min().to_pydatetime(),
                 bars.timestamp_utc.max().to_pydatetime(), "forward",
                str(bars.trade_session.iloc[-1]), "ok", now, None],
            )

        for timeframe, column in (("weekly", "weekly_bar_timestamp"), ("daily", "daily_bar_timestamp"), ("4hour", "four_hour_bar_timestamp")):
            rows = con.execute(f"SELECT run_id, symbol, {column} FROM scan_results WHERE {column} IS NOT NULL").fetchall()
            for run_id, symbol, value in rows:
                corrected = _fix_db_timestamp(value, timeframe)
                con.execute(f"UPDATE scan_results SET {column}=? WHERE run_id=? AND symbol=?", [corrected, run_id, symbol])
        rows = con.execute("SELECT run_id, symbol, timeframe, bar_timestamp FROM factor_results WHERE bar_timestamp IS NOT NULL").fetchall()
        for run_id, symbol, timeframe, value in rows:
            corrected = _fix_db_timestamp(value, timeframe)
            con.execute(
                "UPDATE factor_results SET bar_timestamp=? WHERE run_id=? AND symbol=? AND timeframe=?",
                [corrected, run_id, symbol, timeframe],
            )
        con.execute("""
            UPDATE scan_runs AS runs
            SET market_data_cutoff = (
                SELECT max(v.ts) FROM (
                    SELECT greatest(r.weekly_bar_timestamp, r.daily_bar_timestamp, r.four_hour_bar_timestamp) AS ts
                    FROM scan_results r WHERE r.run_id = runs.run_id
                ) v
            )
            WHERE EXISTS (SELECT 1 FROM scan_results r WHERE r.run_id = runs.run_id)
        """)
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write normalized timestamps; default only audits the plan")
    parser.add_argument("--report", type=Path, default=Path("reports/ny_timestamp_repair.json"))
    args = parser.parse_args()
    settings = get_settings()
    repository = ParquetBarRepository(settings.parquet_root)
    now = pd.Timestamp.now(tz="UTC")
    summary: dict[str, object] = {"timezone": "America/New_York", "apply": args.apply, "timeframes": {}}
    normalized: dict[tuple[str, str], pd.DataFrame] = {}
    stats_by_key: dict[tuple[str, str], dict[str, int]] = {}
    for timeframe in ("daily", "weekly", "4hour"):
        before_rows = after_rows = shifted = deduped = invalid_sessions = weekend = duplicate_keys = 0
        paths = sorted((repository.root / f"timeframe={timeframe}").glob("symbol=*/bars.parquet"))
        for path in paths:
            symbol = path.parent.name.removeprefix("symbol=")
            bars = pd.read_parquet(path)
            if bars.empty:
                continue
            before_rows += len(bars)
            clean, stats = _normalize_frame(bars, timeframe, now)
            after_rows += len(clean)
            shifted += stats["shifted_dst"]
            deduped += stats["deduped"]
            stats_by_key[(symbol, timeframe)] = stats
            normalized[(symbol, timeframe)] = clean
            ny = clean.timestamp_utc.dt.tz_convert(NY)
            if timeframe == "daily":
                invalid_sessions += int((ny.dt.weekday >= 5).sum())
                weekend += int((ny.dt.weekday >= 5).sum())
                duplicate_keys += int(ny.dt.strftime("%Y-%m-%d").duplicated().sum())
            elif timeframe == "weekly":
                iso = ny.dt.isocalendar()
                keys = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
                duplicate_keys += int(keys.duplicated().sum())
            else:
                duplicate_keys += int(clean.timestamp_utc.duplicated().sum())
        summary["timeframes"][timeframe] = {
            "rows_before": before_rows,
            "rows_after": after_rows,
            "dst_adjusted_rows": shifted,
            "deduplicated_rows": deduped,
            "invalid_nyse_sessions": invalid_sessions,
            "weekend_daily_rows": weekend,
            "duplicate_session_keys_after": duplicate_keys,
        }

    if args.apply:
        for key, frame in normalized.items():
            path = repository.path_for(*key)
            frame.to_parquet(path, index=False)
        _migrate_database(settings.database_path, stats_by_key, repository)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
