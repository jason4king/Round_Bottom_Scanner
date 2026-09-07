from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


STATE_LABELS = {
    "scan": "Scanning", "choch": "CHoCH · 等待 Wave 1", "wave2": "Wave 2",
    "wave3": "Wave 3", "wave4": "Wave 4", "wave5": "Wave 5",
    "correction_a": "Correction A", "correction_b": "Correction B", "correction_c": "Correction C",
}


def _pivots(values: np.ndarray, radius: int, high: bool) -> set[int]:
    found = set()
    for index in range(radius, len(values) - radius):
        window = values[index - radius:index + radius + 1]
        extreme = np.nanmax(window) if high else np.nanmin(window)
        if values[index] == extreme and np.count_nonzero(window == extreme) == 1:
            found.add(index)
    return found


def analyze_elliott_impulse(
    frame: pd.DataFrame, *, swing_radius: int = 10, point_radius: int = 5, lookback: int = 500,
) -> dict[str, Any]:
    """Build one causal Elliott impulse hypothesis for chart display only."""
    sample = frame.tail(lookback).copy()
    empty = {"active": False, "state": "scan", "state_label": STATE_LABELS["scan"], "direction": None, "points": [], "target_zone": None, "invalidation_price": None, "warnings": [], "last_reset": None}
    if point_radius > swing_radius or len(sample) < max(3 * swing_radius, 50):
        return empty
    high_col = "High" if "High" in sample else "high"; low_col = "Low" if "Low" in sample else "low"
    close_col = "Close" if "Close" in sample else "close"
    highs = sample[high_col].to_numpy(float); lows = sample[low_col].to_numpy(float); closes = sample[close_col].to_numpy(float)
    slow_highs = _pivots(highs, swing_radius, True); slow_lows = _pivots(lows, swing_radius, False)
    fast_highs = _pivots(highs, point_radius, True); fast_lows = _pivots(lows, point_radius, False)
    state = "scan"; direction = 0; internal_trend = 0; choch_level = np.nan
    slow_high = slow_low = None; high_broken = low_broken = False
    points: dict[str, dict[str, Any]] = {}; broke1 = broke3 = False
    warnings: list[str] = []; last_reset = None

    def price(name: str) -> float:
        return float(points[name]["price"])

    def set_directional_point(name: str, value: float, pivot_index: int, confirmed_index: int, soft: bool = False) -> None:
        points[name] = {"name": name, "price": float(value), "pivot_timestamp": sample.index[pivot_index].isoformat(), "confirmed_timestamp": sample.index[confirmed_index].isoformat(), "soft": soft}

    def reset(reason: str, bar: int) -> None:
        nonlocal state, direction, points, broke1, broke3, warnings, last_reset, choch_level
        last_reset = {"reason": reason, "timestamp": sample.index[bar].isoformat()}
        state = "scan"; direction = 0; points = {}; broke1 = broke3 = False; warnings = []; choch_level = np.nan

    def adverse(value: float, level: float) -> bool:
        return value < level if direction == 1 else value > level

    def beyond(value: float, level: float) -> bool:
        return value > level if direction == 1 else value < level

    for bar in range(len(sample)):
        if state != "scan":
            critical = price("0") if state in {"choch", "wave2"} or state == "wave3" and not broke1 else price("2") if state in {"wave3", "wave4"} or state == "wave5" and not broke3 or state.startswith("correction") else price("4")
            if adverse(closes[bar], critical):
                reset("STRUCTURE_INVALIDATED", bar)

        slow_pivot = bar - swing_radius
        if slow_pivot in slow_highs:
            slow_high = slow_pivot; high_broken = False
        if slow_pivot in slow_lows:
            slow_low = slow_pivot; low_broken = False
        warmed = bar >= max(3 * swing_radius, 50)
        if slow_high is not None and not high_broken and closes[bar] > highs[slow_high] and warmed:
            high_broken = True; was_bear = internal_trend == -1; internal_trend = 1
            if was_bear and state == "scan":
                direction = 1; state = "choch"; choch_level = highs[slow_high]
                # Wave 0 belongs to the last opposing major swing before the
                # CHoCH. Starting at the broken high would discard the actual
                # impulse origin and often anchor 0 to a later minor low.
                start = slow_low if slow_low is not None and slow_low < bar else slow_high
                p0_index = start + int(np.nanargmin(lows[start:bar + 1]))
                set_directional_point("0", lows[p0_index], p0_index, bar)
        if slow_low is not None and not low_broken and closes[bar] < lows[slow_low] and warmed:
            low_broken = True; was_bull = internal_trend == 1; internal_trend = -1
            if was_bull and state == "scan":
                direction = -1; state = "choch"; choch_level = lows[slow_low]
                start = slow_high if slow_high is not None and slow_high < bar else slow_low
                p0_index = start + int(np.nanargmax(highs[start:bar + 1]))
                set_directional_point("0", highs[p0_index], p0_index, bar)

        pivot_index = bar - point_radius
        with_trend = pivot_index in (fast_highs if direction == 1 else fast_lows)
        counter = pivot_index in (fast_lows if direction == 1 else fast_highs)
        wt_value = (highs[pivot_index] if direction == 1 else lows[pivot_index]) if with_trend else None
        ctr_value = (lows[pivot_index] if direction == 1 else highs[pivot_index]) if counter else None
        if state == "choch" and wt_value is not None and beyond(wt_value, choch_level):
            set_directional_point("1", wt_value, pivot_index, bar); state = "wave2"
        elif state == "wave2":
            if wt_value is not None and beyond(wt_value, price("1")): set_directional_point("1", wt_value, pivot_index, bar)
            if ctr_value is not None:
                ratio = abs(price("1") - ctr_value) / abs(price("1") - price("0"))
                if ratio >= 1: reset("BELOW_0", bar)
                elif ratio > 0: set_directional_point("2", ctr_value, pivot_index, bar, not 0.5 <= ratio <= 0.705); state = "wave3"
        elif state == "wave3":
            if not broke1 and beyond(closes[bar], price("1")): broke1 = True
            if ctr_value is not None and not broke1 and adverse(ctr_value, price("2")):
                ratio = abs(price("1") - ctr_value) / abs(price("1") - price("0"))
                if ratio >= 1: reset("BELOW_0", bar)
                else: set_directional_point("2", ctr_value, pivot_index, bar, not 0.5 <= ratio <= 0.705)
            if state == "wave3" and broke1 and wt_value is not None and beyond(wt_value, price("1")):
                set_directional_point("3", wt_value, pivot_index, bar); state = "wave4"
        elif state == "wave4":
            if wt_value is not None and beyond(wt_value, price("3")): set_directional_point("3", wt_value, pivot_index, bar)
            if ctr_value is not None:
                if adverse(ctr_value, price("2")): reset("BELOW_W2", bar)
                elif adverse(ctr_value, price("1")): reset("W4_OVERLAPS_W1", bar)
                else:
                    ratio = abs(price("3") - ctr_value) / abs(price("3") - price("2"))
                    set_directional_point("4", ctr_value, pivot_index, bar, not 0.5 <= ratio <= 0.705); state = "wave5"
        elif state == "wave5":
            if not broke3 and beyond(closes[bar], price("3")): broke3 = True
            if ctr_value is not None and not broke3 and adverse(ctr_value, price("4")):
                if adverse(ctr_value, price("1")): reset("W4_OVERLAPS_W1", bar)
                else: set_directional_point("4", ctr_value, pivot_index, bar)
            if state == "wave5" and broke3 and wt_value is not None and beyond(wt_value, price("3")):
                len1 = abs(price("1") - price("0")); len3 = abs(price("3") - price("2")); len5 = abs(wt_value - price("4"))
                if len3 < len1 and len3 < len5: reset("W3_SHORTEST", bar)
                else: set_directional_point("5", wt_value, pivot_index, bar); state = "correction_a"
        elif state == "correction_a":
            if wt_value is not None and beyond(wt_value, price("5")):
                set_directional_point("5", wt_value, pivot_index, bar)
                len1 = abs(price("1") - price("0")); len3 = abs(price("3") - price("2")); len5 = abs(price("5") - price("4"))
                if len3 < len1 and len3 < len5: reset("W3_SHORTEST", bar)
            if state == "correction_a" and ctr_value is not None:
                if adverse(ctr_value, price("2")): reset("DEEP_CORRECTION", bar)
                else: set_directional_point("A", ctr_value, pivot_index, bar); state = "correction_b"
        elif state == "correction_b":
            if ctr_value is not None and adverse(ctr_value, price("A")): set_directional_point("A", ctr_value, pivot_index, bar)
            if wt_value is not None:
                if beyond(wt_value, price("5")) or wt_value == price("5"): reset("B_BEYOND_W5", bar)
                else: set_directional_point("B", wt_value, pivot_index, bar); state = "correction_c"
        elif state == "correction_c":
            if wt_value is not None and beyond(wt_value, price("B")):
                if beyond(wt_value, price("5")) or wt_value == price("5"): reset("B_BEYOND_W5", bar)
                else: set_directional_point("B", wt_value, pivot_index, bar)
            if state == "correction_c" and ctr_value is not None:
                if adverse(ctr_value, price("2")): reset("DEEP_CORRECTION", bar)
                else: set_directional_point("C", ctr_value, pivot_index, bar); reset("DONE", bar)

    if state == "scan":
        return {**empty, "last_reset": last_reset}
    target_zone = _target_zone(state, direction, points)
    critical = price("0") if state in {"choch", "wave2"} or state == "wave3" and not broke1 else price("2") if state in {"wave3", "wave4"} or state == "wave5" and not broke3 or state.startswith("correction") else price("4")
    return {"active": True, "state": state, "state_label": STATE_LABELS[state], "direction": "bullish" if direction == 1 else "bearish", "points": list(points.values()), "target_zone": target_zone, "invalidation_price": critical, "warnings": warnings, "last_reset": last_reset}


def _target_zone(state: str, direction: int, points: dict[str, dict[str, Any]]) -> dict[str, float] | None:
    sign = 1 if direction == 1 else -1
    if state == "wave2" and {"0", "1"} <= points.keys():
        length = abs(points["1"]["price"] - points["0"]["price"]); anchor = points["1"]["price"]
        values = [anchor - sign * length * ratio for ratio in (0.5, 0.705)]
    elif state == "wave3" and {"0", "1", "2"} <= points.keys():
        length = abs(points["1"]["price"] - points["0"]["price"]); anchor = points["1"]["price"]
        values = [anchor + sign * length * ratio for ratio in (0.5, 0.618)]
    elif state == "wave4" and {"2", "3"} <= points.keys():
        length = abs(points["3"]["price"] - points["2"]["price"]); anchor = points["3"]["price"]
        values = [anchor - sign * length * ratio for ratio in (0.5, 0.705)]
    elif state == "wave5" and {"0", "1", "4"} <= points.keys():
        length = abs(points["1"]["price"] - points["0"]["price"]); anchor = points["4"]["price"]
        values = [anchor + sign * length * ratio for ratio in (0.618, 1.0)]
    else:
        return None
    return {"bottom": float(min(values)), "top": float(max(values)), "center": float(sum(values) / 2)}
