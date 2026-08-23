from pathlib import Path
import warnings
import pandas as pd
from app.market_data import BAR_COLUMNS, ParquetBarRepository, _is_closed, cache_needs_sync

def sample_frame() -> pd.DataFrame:
    now=pd.Timestamp("2026-08-21T20:00:00Z")
    return pd.DataFrame([{
        "symbol":"AAPL.US","timeframe":"daily","timestamp_utc":now,
        "open":100.0,"high":102.0,"low":99.0,"close":101.0,"volume":1000.0,
        "is_closed":True,"adjustment_type":"forward","trade_session":"all",
        "data_source":"LongPort","updated_at":now,
    }],columns=BAR_COLUMNS)

def test_first_cache_merge_does_not_emit_future_warning(tmp_path: Path):
    repository=ParquetBarRepository(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error",FutureWarning)
        merged=repository.merge("AAPL.US","daily",sample_frame())
    assert len(merged)==1

def test_existing_cache_is_overwritten_by_newer_copy(tmp_path: Path):
    repository=ParquetBarRepository(tmp_path)
    original=sample_frame(); repository.merge("AAPL.US","daily",original)
    revised=original.copy(); revised.loc[0,"close"]=105.0; revised.loc[0,"updated_at"]=pd.Timestamp("2026-08-21T21:00:00Z")
    merged=repository.merge("AAPL.US","daily",revised)
    assert len(merged)==1
    assert merged.iloc[0].close==105.0

def test_has_cache_changes_after_first_write(tmp_path: Path):
    repository=ParquetBarRepository(tmp_path)
    assert not repository.has_cache("AAPL.US","daily")
    repository.merge("AAPL.US","daily",sample_frame())
    assert repository.has_cache("AAPL.US","daily")

def test_default_repository_does_not_trim_history(tmp_path: Path):
    repository=ParquetBarRepository(tmp_path)
    base=sample_frame()
    frames=[]
    for offset in range(1601):
        row=base.copy()
        row["timestamp_utc"]=row["timestamp_utc"]+pd.Timedelta(days=offset)
        row["updated_at"]=row["updated_at"]+pd.Timedelta(days=offset)
        frames.append(row)
    merged=repository.merge("AAPL.US","daily",pd.concat(frames,ignore_index=True))
    assert len(merged)==1601

def test_current_week_closes_after_friday_extended_session():
    bar=pd.Timestamp("2026-08-17T12:00:00Z")
    assert not _is_closed(bar,"weekly",pd.Timestamp("2026-08-21T22:00:00Z"))
    assert _is_closed(bar,"weekly",pd.Timestamp("2026-08-22T01:00:00Z"))
    assert _is_closed(bar,"weekly",pd.Timestamp("2026-08-23T03:00:00Z"))

def test_daily_cache_skips_sync_when_latest_completed_day_exists():
    frame=sample_frame()
    frame["timestamp_utc"]=pd.Timestamp("2026-08-21T12:00:00Z")
    frame["updated_at"]=pd.Timestamp("2026-08-22T01:00:00Z")
    assert not cache_needs_sync(frame,"daily",pd.Timestamp("2026-08-23T03:00:00Z"))

def test_weekly_cache_requests_sync_when_completed_week_is_missing():
    frame=sample_frame()
    frame["timestamp_utc"]=pd.Timestamp("2026-08-10T12:00:00Z")
    frame["updated_at"]=pd.Timestamp("2026-08-21T12:00:00Z")
    assert cache_needs_sync(frame,"weekly",pd.Timestamp("2026-08-22T01:00:00Z"))

def test_four_hour_cache_uses_three_hour_ttl():
    frame=sample_frame(); frame["timeframe"]="4hour"
    frame["updated_at"]=pd.Timestamp("2026-08-21T14:00:00Z")
    assert not cache_needs_sync(frame,"4hour",pd.Timestamp("2026-08-21T16:00:00Z"))
    assert cache_needs_sync(frame,"4hour",pd.Timestamp("2026-08-21T17:01:00Z"))

def test_no_timeframe_syncs_on_us_weekend():
    frame=sample_frame()
    for timeframe in ("weekly", "daily", "4hour"):
        candidate=frame.copy(); candidate["timeframe"]=timeframe
        assert not cache_needs_sync(candidate,timeframe,pd.Timestamp("2026-08-23T16:00:00Z"))

def test_no_timeframe_syncs_on_nyse_holiday():
    frame=sample_frame()
    # 2026-09-07 is US Labor Day and XNYS is closed.
    for timeframe in ("weekly", "daily", "4hour"):
        candidate=frame.copy(); candidate["timeframe"]=timeframe
        assert not cache_needs_sync(candidate,timeframe,pd.Timestamp("2026-09-07T16:00:00Z"))

def test_empty_cache_backfills_even_on_nyse_holiday():
    assert cache_needs_sync(
        pd.DataFrame(columns=BAR_COLUMNS), "daily", pd.Timestamp("2026-09-07T16:00:00Z")
    )

def test_empty_cache_backfills_even_on_us_weekend():
    assert cache_needs_sync(
        pd.DataFrame(columns=BAR_COLUMNS), "weekly", pd.Timestamp("2026-08-23T16:00:00Z")
    )

def test_weekly_does_not_sync_before_final_session_of_week():
    frame=sample_frame()
    frame["timeframe"]="weekly"
    frame["timestamp_utc"]=pd.Timestamp("2026-08-17T12:00:00Z")
    frame["updated_at"]=pd.Timestamp("2026-08-18T00:00:00Z")
    assert not cache_needs_sync(frame,"weekly",pd.Timestamp("2026-08-19T21:00:00Z"))
