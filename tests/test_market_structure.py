import numpy as np
import pandas as pd

from app.market_structure import calculate_market_structure


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
