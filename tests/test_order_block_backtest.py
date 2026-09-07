import numpy as np
import pandas as pd

from app.order_block_backtest import HORIZONS, build_samples, summarize_samples, summarize_segments


def test_summary_groups_quality_and_calculates_forward_metrics():
    rows = []
    for quality, result in ((35.0, -2.0), (50.0, 4.0), (60.0, 6.0), (80.0, 8.0)):
        row = {"quality_score": quality}
        for horizon in HORIZONS:
            row[f"return_{horizon}d_pct"] = result
            row[f"mfe_{horizon}d_pct"] = result + 2
            row[f"mae_{horizon}d_pct"] = result - 2
        rows.append(row)

    summary = summarize_samples(pd.DataFrame(rows))

    assert summary["quality_bucket"].tolist() == ["0-39", "40-54", "55-64", "65-74", "75-100"]
    assert summary.loc[summary.quality_bucket == "55-64", "avg_return_20d_pct"].item() == 6.0
    assert summary.loc[summary.quality_bucket == "75-100", "win_rate_40d_pct"].item() == 100.0


def test_build_samples_returns_empty_for_empty_bars():
    assert build_samples("TEST.US", pd.DataFrame()).empty


def test_forward_return_is_not_available_without_full_horizon(monkeypatch):
    bars = pd.DataFrame({
        "timestamp_utc": pd.date_range("2026-01-01", periods=12, freq="D", tz="UTC"),
        "open": np.arange(12) + 10.0, "high": np.arange(12) + 11.0,
        "low": np.arange(12) + 9.0, "close": np.arange(12) + 10.5,
        "volume": np.full(12, 1000.0),
    })
    features = pd.DataFrame(index=pd.DatetimeIndex(bars.timestamp_utc), data={
        "distance_pct": [np.nan] * 10 + [1.0, 1.0], "quality_score": [np.nan] * 10 + [70.0, 70.0],
        "formation_score": [np.nan] * 10 + [65.0, 65.0], "retest_score": [np.nan] * 10 + [60.0, 60.0],
        "touch_count": [0] * 10 + [1, 1], "age_bars": [0] * 10 + [2, 3],
        "status": [None] * 10 + ["touched", "touched"], "pierced": [False] * 12,
        "retest_confirmed": [False] * 12,
    })
    monkeypatch.setattr("app.order_block_backtest.bullish_order_block_features", lambda frame, radius: features)

    samples = build_samples("TEST.US", bars)

    assert len(samples) == 1
    assert pd.isna(samples.iloc[0]["return_5d_pct"])
    assert samples.iloc[0]["market_regime"] in {"bullish", "bearish", "neutral"}


def test_segment_summary_keeps_train_and_validation_separate():
    rows = []
    for split, quality, result in (("train", 50.0, 2.0), ("validation", 70.0, -1.0)):
        row = {
            "time_split": split, "quality_score": quality, "event_type": "near_block",
            "status": "untested", "market_regime": "bullish", "pierced": False,
            "first_touch": False, "touch_count": 0,
        }
        for horizon in HORIZONS:
            row[f"return_{horizon}d_pct"] = result
            row[f"mfe_{horizon}d_pct"] = result + 1
            row[f"mae_{horizon}d_pct"] = result - 1
        rows.append(row)

    segments = summarize_segments(pd.DataFrame(rows))

    validation_quality = segments[(segments.time_split == "validation") & (segments.dimension == "quality_bucket") & (segments.segment == "65-74")]
    assert validation_quality["avg_return_20d_pct"].item() == -1.0
