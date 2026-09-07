import numpy as np
import pandas as pd

import app.elliott_wave as elliott


def _frame(size: int = 64) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=size, freq="D", tz="UTC")
    return pd.DataFrame(
        {"High": np.full(size, 101.0), "Low": np.full(size, 99.0), "Close": np.full(size, 100.0)},
        index=index,
    )


def test_short_history_returns_inactive_result():
    result = elliott.analyze_elliott_impulse(_frame(20), swing_radius=3, point_radius=1)
    assert result["active"] is False
    assert result["state"] == "scan"


def test_pivots_are_unique_and_causal_confirmation_is_exposed():
    values = np.array([1.0, 2.0, 4.0, 2.0, 1.0])
    assert elliott._pivots(values, 2, True) == {2}
    assert elliott._pivots(np.array([1.0, 4.0, 4.0, 2.0, 1.0]), 1, True) == set()


def test_builds_bullish_wave_one_after_confirmed_choch(monkeypatch):
    frame = _frame()
    frame.iloc[40, frame.columns.get_loc("Low")] = 90
    frame.iloc[50, frame.columns.get_loc("Close")] = 89
    frame.iloc[48, frame.columns.get_loc("High")] = 105
    frame.iloc[48:53, frame.columns.get_loc("Low")] = [97, 96, 95, 96, 97]
    frame.iloc[52, frame.columns.get_loc("Close")] = 106
    frame.iloc[53, frame.columns.get_loc("High")] = 110

    def pivots(_values, radius, high):
        return {(3, False): {40}, (3, True): {48}, (1, True): {53}}.get((radius, high), set())

    monkeypatch.setattr(elliott, "_pivots", pivots)
    result = elliott.analyze_elliott_impulse(frame, swing_radius=3, point_radius=1)

    assert result["active"] is True
    assert result["direction"] == "bullish"
    assert result["state"] == "wave2"
    assert [point["name"] for point in result["points"]] == ["0", "1"]
    assert result["points"][1]["confirmed_timestamp"] > result["points"][1]["pivot_timestamp"]
    assert result["target_zone"]["bottom"] < result["target_zone"]["top"]
