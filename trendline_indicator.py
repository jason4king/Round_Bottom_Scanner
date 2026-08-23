"""Fractal-pivot trendline channel indicator."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


DEFAULT_LOOKBACK = 240
DEFAULT_PEAK_DISTANCE = 5


def _filter_sparse_points(
    indices: np.ndarray,
    values: np.ndarray,
    distance: int,
    mode: Literal["high", "low"],
) -> np.ndarray:
    if len(indices) == 0:
        return np.array([], dtype=int)

    kept = [int(indices[0])]
    for index in indices[1:]:
        index = int(index)
        if index - kept[-1] <= distance:
            previous = kept[-1]
            if mode == "high" and values[index] >= values[previous]:
                kept[-1] = index
            elif mode == "low" and values[index] <= values[previous]:
                kept[-1] = index
        else:
            kept.append(index)
    return np.array(kept, dtype=int)


def _fractal_points(
    highs: np.ndarray,
    lows: np.ndarray,
    distance: int,
) -> tuple[np.ndarray, np.ndarray]:
    if len(highs) < 2 * distance + 1:
        return np.array([], dtype=int), np.array([], dtype=int)

    high_indices = []
    low_indices = []
    for index in range(distance, len(highs) - distance):
        high_window = highs[index - distance:index + distance + 1]
        low_window = lows[index - distance:index + distance + 1]
        if highs[index] >= np.max(high_window):
            high_indices.append(index)
        if lows[index] <= np.min(low_window):
            low_indices.append(index)

    return (
        _filter_sparse_points(np.array(high_indices), highs, distance, "high"),
        _filter_sparse_points(np.array(low_indices), lows, distance, "low"),
    )


def _alternating_pivots(
    high_indices: np.ndarray,
    low_indices: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
) -> list[tuple[int, str, float]]:
    events = [(int(index), "H", float(highs[index])) for index in high_indices]
    events += [(int(index), "L", float(lows[index])) for index in low_indices]
    events.sort(key=lambda item: item[0])

    pivots: list[tuple[int, str, float]] = []
    for index, point_type, price in events:
        if not pivots:
            pivots.append((index, point_type, price))
            continue

        _, previous_type, previous_price = pivots[-1]
        if point_type == previous_type:
            is_more_extreme = (
                point_type == "H" and price >= previous_price
            ) or (point_type == "L" and price <= previous_price)
            if is_more_extreme:
                pivots[-1] = (index, point_type, price)
        else:
            pivots.append((index, point_type, price))
    return pivots


def _line_from_points(
    point1: tuple[int, str, float],
    point2: tuple[int, str, float],
) -> tuple[float, float]:
    x1, _, y1 = point1
    x2, _, y2 = point2
    if x1 == x2:
        return np.nan, np.nan
    slope = (y2 - y1) / (x2 - x1)
    return float(slope), float(y1 - slope * x1)


def calculate_trendline_channels(
    df: pd.DataFrame,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    peak_distance: int = DEFAULT_PEAK_DISTANCE,
) -> tuple[np.ndarray, np.ndarray]:
    """Return support and resistance values for every row in ``df``.

    Required columns are ``high`` and ``low``. Rows must be ordered from
    oldest to newest. The returned arrays have the same length as ``df``.
    """
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    if "high" not in df.columns or "low" not in df.columns:
        raise ValueError("df must contain 'high' and 'low' columns")
    if len(df) == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    if lookback < 1 or peak_distance < 1:
        raise ValueError("lookback and peak_distance must be positive")

    start_index = max(0, len(df) - int(lookback))
    sub_df = df.iloc[start_index:]
    highs = pd.to_numeric(sub_df["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(sub_df["low"], errors="coerce").to_numpy(dtype=float)
    if np.isnan(highs).any() or np.isnan(lows).any():
        raise ValueError("'high' and 'low' must contain valid numeric values")

    high_indices, low_indices = _fractal_points(highs, lows, int(peak_distance))
    if len(low_indices) < 2:
        low_indices = np.array(
            [
                int(np.argmin(lows[:max(1, len(lows) // 2)])),
                int(np.argmin(lows)),
            ],
            dtype=int,
        )
    if len(high_indices) < 2:
        high_indices = np.array(
            [
                int(np.argmax(highs[:max(1, len(highs) // 2)])),
                int(np.argmax(highs)),
            ],
            dtype=int,
        )

    pivots = _alternating_pivots(high_indices, low_indices, highs, lows)
    high_pivots = [pivot for pivot in pivots if pivot[1] == "H"]
    low_pivots = [pivot for pivot in pivots if pivot[1] == "L"]

    if len(low_pivots) >= 2:
        low_point1, low_point2 = low_pivots[-2:]
    else:
        low_point1 = (int(low_indices[-2]), "L", float(lows[low_indices[-2]]))
        low_point2 = (int(low_indices[-1]), "L", float(lows[low_indices[-1]]))

    if len(high_pivots) >= 2:
        high_point1, high_point2 = high_pivots[-2:]
    else:
        high_point1 = (int(high_indices[-2]), "H", float(highs[high_indices[-2]]))
        high_point2 = (int(high_indices[-1]), "H", float(highs[high_indices[-1]]))

    support_slope, support_intercept = _line_from_points(low_point1, low_point2)
    resistance_slope, resistance_intercept = _line_from_points(high_point1, high_point2)

    support_intercept -= support_slope * start_index
    resistance_intercept -= resistance_slope * start_index
    x_values = np.arange(len(df), dtype=float)
    support = support_slope * x_values + support_intercept
    resistance = resistance_slope * x_values + resistance_intercept

    # A trendline is meaningful from its first confirmed anchor onward. Do not
    # extrapolate it backwards across unrelated older price history.
    support[: start_index + low_point1[0]] = np.nan
    resistance[: start_index + high_point1[0]] = np.nan
    return support, resistance


def add_trendline_channels(
    df: pd.DataFrame,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    peak_distance: int = DEFAULT_PEAK_DISTANCE,
    support_column: str = "trend_support",
    resistance_column: str = "trend_resistance",
) -> pd.DataFrame:
    """Return a copy of ``df`` with support/resistance columns added."""
    result = df.copy()
    support, resistance = calculate_trendline_channels(
        result,
        lookback=lookback,
        peak_distance=peak_distance,
    )
    result[support_column] = support
    result[resistance_column] = resistance
    return result


__all__ = [
    "DEFAULT_LOOKBACK",
    "DEFAULT_PEAK_DISTANCE",
    "calculate_trendline_channels",
    "add_trendline_channels",
]
