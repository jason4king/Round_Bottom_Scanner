import numpy as np
import pandas as pd

from app.market_structure import (
    _append_best_block,
    _detect_order_blocks,
    bullish_order_block_features,
    calculate_market_structure,
)


def _order_block_frame(close):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "Close": close,
            "volume": np.arange(len(close), dtype=float) + 100,
        },
        index=pd.date_range("2026-01-01", periods=len(close), freq="D", tz="UTC"),
    )


def test_market_structure_returns_levels_without_bos_or_choch_annotations():
    close=np.r_[np.linspace(100,90,12),np.linspace(90,110,18),np.linspace(110,103,8),np.linspace(103,116,18)]
    frame=pd.DataFrame({
        "open":close-.2,
        "high":close+1,
        "low":close-1,
        "Close":close,
    },index=pd.date_range("2026-01-01",periods=len(close),freq="D",tz="UTC"))
    result=calculate_market_structure(frame,pivot_radius=3)
    assert result["trend"] in {"bullish","bearish","neutral"}
    assert all(level["kind"] in {"strong_high","weak_high","strong_low","weak_low"} for level in result["levels"])
    assert "bos" not in result and "choch" not in result


def test_broken_order_blocks_are_not_returned_as_active():
    close=np.array([10,9,8,9,10,11,12,11,10,9,8,7,6,7,8,9,10,11,12,13],dtype=float)
    frame=pd.DataFrame({"open":close-.1,"high":close+.5,"low":close-.5,"Close":close},index=pd.date_range("2026-01-01",periods=len(close),freq="D",tz="UTC"))
    result=calculate_market_structure(frame,pivot_radius=2)
    assert all(block["active"] for block in result["order_blocks"])
    assert all(
        pd.Timestamp(block["confirmed_at_timestamp"])
        >= pd.Timestamp(block["start_timestamp"])
        for block in result["order_blocks"]
    )


def test_order_block_exposes_quality_and_lifecycle_metrics():
    frame = _order_block_frame([10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 13])

    bullish = next(block for block in _detect_order_blocks(frame, 2) if block["bias"] == "bullish")

    assert 0 <= bullish["quality_score"] <= 100
    assert bullish["source_volume_ratio"] > 0
    assert bullish["breakout_volume_ratio"] > 0
    assert bullish["displacement_atr"] >= 0
    assert bullish["body_atr"] >= 0
    assert bullish["status"] == "untested"
    assert bullish["touch_count"] == 0
    assert bullish["retest_confirmed"] is False


def test_bullish_order_block_tracks_one_entry_and_confirmed_retest():
    frame = _order_block_frame([10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 13, 9, 6.2, 7])

    bullish = next(block for block in _detect_order_blocks(frame, 2) if block["bias"] == "bullish")

    assert bullish["active"] is True
    assert bullish["status"] == "confirmed_retest"
    assert bullish["touch_count"] == 1
    assert bullish["first_retest_timestamp"] == frame.index[21].isoformat()
    assert bullish["retest_timestamp"] == frame.index[22].isoformat()
    assert bullish["retest_confirmed"] is True


def test_future_bars_do_not_change_historical_order_block_creation():
    frame = _order_block_frame([10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 13, 9, 6.2, 7])
    prefix = frame.iloc[:20]

    historical = next(block for block in _detect_order_blocks(prefix, 2) if block["bias"] == "bullish")
    complete = next(block for block in _detect_order_blocks(frame, 2) if block["bias"] == "bullish")

    for field in ("top", "bottom", "start_timestamp", "confirmed_at_timestamp", "displacement_atr", "body_atr"):
        assert complete[field] == historical[field]


def test_order_block_quality_uses_neutral_volume_when_volume_is_missing():
    frame = _order_block_frame([10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 13]).drop(columns="volume")

    bullish = next(block for block in _detect_order_blocks(frame, 2) if block["bias"] == "bullish")

    assert bullish["source_volume_ratio"] == 1.0
    assert bullish["breakout_volume_ratio"] == 1.0


def test_overlapping_order_blocks_keep_the_higher_quality_candidate():
    existing = {
        "bias": "bullish", "top": 10.0, "bottom": 8.0, "active": True,
        "_base_quality": 40.0, "status": "untested", "end_timestamp": None,
    }
    candidate = {
        "bias": "bullish", "top": 10.5, "bottom": 8.5, "active": True,
        "_base_quality": 70.0, "confirmed_at_timestamp": "2026-02-01T00:00:00+00:00",
    }
    blocks = [existing]

    _append_best_block(blocks, candidate)

    assert blocks[-1] is candidate
    assert existing["active"] is False
    assert existing["status"] == "superseded"


def test_wick_pierce_reduces_integrity_without_invalidating_block():
    frame = _order_block_frame([10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 13, 6])
    frame.loc[frame.index[-1], "low"] = 5.0

    bullish = next(block for block in _detect_order_blocks(frame, 2) if block["bias"] == "bullish")

    assert bullish["active"] is True
    assert bullish["status"] == "pierced"
    assert bullish["pierced"] is True
    assert bullish["integrity_score"] == 85.0


def test_close_below_block_invalidates_it():
    frame = _order_block_frame([10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 13, 5])

    bullish = next(block for block in _detect_order_blocks(frame, 2) if block["bias"] == "bullish")

    assert bullish["active"] is False
    assert bullish["status"] == "invalidated"


def test_enhanced_features_are_causal_at_historical_cutoff():
    frame = _order_block_frame([10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 13, 9, 6.2, 7])
    cutoff = 21

    historical = bullish_order_block_features(frame.iloc[:cutoff], 2).iloc[-1]
    complete = bullish_order_block_features(frame, 2).iloc[cutoff - 1]

    for field in ("distance_pct", "quality_score", "formation_score", "retest_score", "touch_count", "age_bars", "retest_confirmed", "pierced", "status"):
        assert complete[field] == historical[field]
