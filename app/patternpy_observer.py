from __future__ import annotations

import warnings
from typing import Any, Callable

import pandas as pd

from tradingpatterns.tradingpatterns import (
    detect_channel,
    detect_double_top_bottom,
    detect_head_shoulder,
    detect_multiple_tops_bottoms,
    detect_triangle_pattern,
    detect_wedge,
)


PATTERNPY_REVISION = "2b1f29b17986acafffeb32c83e772417d540c100"

DETECTORS: tuple[tuple[str, str, Callable[[pd.DataFrame], pd.DataFrame], str], ...] = (
    ("P1", "Head & Shoulders", detect_head_shoulder, "head_shoulder_pattern"),
    ("P2", "Multiple Tops & Bottoms", detect_multiple_tops_bottoms, "multiple_top_bottom_pattern"),
    ("P3", "Triangle", detect_triangle_pattern, "triangle_pattern"),
    ("P4", "Wedge", detect_wedge, "wedge_pattern"),
    ("P5", "Channel", detect_channel, "channel_pattern"),
    ("P6", "Double Top & Bottom", detect_double_top_bottom, "double_pattern"),
)


def observe_classic_patterns(bars: pd.DataFrame, confirmation_bars: int = 3) -> list[dict[str, Any]]:
    """Run PatternPy as a non-scoring observer on already closed candles.

    PatternPy uses the following candle for confirmation in some detectors. We
    therefore ignore signals on the final row and only expose recent confirmed
    observations.
    """
    source = bars[["Open", "High", "Low", "Close", "Volume"]].copy()
    observations: list[dict[str, Any]] = []
    for pattern_id, label, detector, output_column in DETECTORS:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                detected = detector(source.copy(), window=3)
            series = detected[output_column]
            start = max(0, len(series) - confirmation_bars - 1)
            confirmed = series.iloc[start : max(len(series) - 1, 0)].dropna()
            if confirmed.empty:
                observations.append({"pattern_id": pattern_id, "signal_name": label, "triggered": False, "details": {"reason": "no_recent_confirmed_pattern", "source": "PatternPy", "scored": False}})
                continue
            timestamp = confirmed.index[-1]
            position = int(series.index.get_loc(timestamp))
            observations.append({
                "pattern_id": pattern_id,
                "signal_name": label,
                "triggered": True,
                "details": {
                    "pattern_name": str(confirmed.iloc[-1]),
                    "detected_timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                    "bars_ago": len(series) - 1 - position,
                    "confirmation_window": confirmation_bars,
                    "source": "PatternPy",
                    "source_revision": PATTERNPY_REVISION,
                    "scored": False,
                },
            })
        except Exception as exc:
            observations.append({"pattern_id": pattern_id, "signal_name": label, "triggered": False, "details": {"reason": "observer_error", "error": str(exc)[:300], "source": "PatternPy", "scored": False}})
    return observations
