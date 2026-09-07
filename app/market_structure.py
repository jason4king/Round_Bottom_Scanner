from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _pivots(values: np.ndarray, radius: int, high: bool) -> list[int]:
    found: list[int] = []
    for index in range(radius, len(values) - radius):
        window = values[index - radius : index + radius + 1]
        extreme = np.nanmax(window) if high else np.nanmin(window)
        if values[index] == extreme and int(np.sum(window == extreme)) == 1:
            found.append(index)
    return found


def calculate_market_structure(
    frame: pd.DataFrame,
    *,
    pivot_radius: int,
    lookback: int = 500,
    max_blocks_per_side: int = 5,
    order_block_radius: int = 5,
) -> dict[str, Any]:
    """Return current swing levels and unmitigated order blocks.

    Breaks are used internally to establish trend and order blocks, but are not
    exposed as BOS/CHoCH chart annotations.
    """
    sample = frame.tail(lookback).copy()
    if len(sample) < pivot_radius * 2 + 3:
        return {"trend": "neutral", "levels": [], "order_blocks": []}
    high_col = "High" if "High" in sample else "high"
    low_col = "Low" if "Low" in sample else "low"
    open_col = "Open" if "Open" in sample else "open"
    close_col = "Close" if "Close" in sample else "close"
    highs = sample[high_col].to_numpy(float)
    lows = sample[low_col].to_numpy(float)
    opens = sample[open_col].to_numpy(float)
    closes = sample[close_col].to_numpy(float)
    high_pivots = set(_pivots(highs, pivot_radius, True))
    low_pivots = set(_pivots(lows, pivot_radius, False))
    latest_high: int | None = None
    latest_low: int | None = None
    crossed_high = crossed_low = False
    trend = 0

    for bar in range(len(sample)):
        confirmed = bar - pivot_radius
        if confirmed in high_pivots:
            latest_high = confirmed
            crossed_high = False
        if confirmed in low_pivots:
            latest_low = confirmed
            crossed_low = False

        if latest_high is not None and not crossed_high and closes[bar] > highs[latest_high]:
            crossed_high = True
            trend = 1
        if latest_low is not None and not crossed_low and closes[bar] < lows[latest_low]:
            crossed_low = True
            trend = -1

    levels: list[dict[str, Any]] = []
    if trend >= 0:
        if latest_low is not None:
            trailing_high = latest_low + int(np.nanargmax(highs[latest_low:]))
            levels.append({"kind": "weak_high", "price": float(highs[trailing_high]), "start_timestamp": sample.index[trailing_high].isoformat()})
            levels.append({"kind": "strong_low", "price": float(lows[latest_low]), "start_timestamp": sample.index[latest_low].isoformat()})
    elif latest_high is not None:
        trailing_low = latest_high + int(np.nanargmin(lows[latest_high:]))
        levels.append({"kind": "strong_high", "price": float(highs[latest_high]), "start_timestamp": sample.index[latest_high].isoformat()})
        levels.append({"kind": "weak_low", "price": float(lows[trailing_low]), "start_timestamp": sample.index[trailing_low].isoformat()})
    blocks = _detect_order_blocks(sample, pivot_radius)
    if order_block_radius != pivot_radius:
        blocks.extend(_detect_order_blocks(sample, order_block_radius))
    unique_blocks = {(block["bias"], block["start_timestamp"]): block for block in blocks}
    active: list[dict[str, Any]] = []
    for bias in ("bullish", "bearish"):
        matches = [block for block in unique_blocks.values() if block["active"] and block["bias"] == bias]
        matches.sort(key=lambda block: block["start_timestamp"])
        active.extend(matches[-max_blocks_per_side:])
    return {"trend": "bullish" if trend > 0 else "bearish" if trend < 0 else "neutral", "levels": levels, "order_blocks": active}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(value, high))


def _overlap_ratio(first: dict[str, Any], second: dict[str, Any]) -> float:
    intersection = max(0.0, min(first["top"], second["top"]) - max(first["bottom"], second["bottom"]))
    smaller_height = min(first["top"] - first["bottom"], second["top"] - second["bottom"])
    return intersection / smaller_height if smaller_height > 0 else 0.0


def _block(
    frame: pd.DataFrame,
    index: int,
    top: float,
    bottom: float,
    bias: str,
    confirmed_at: int,
    *,
    atr: np.ndarray,
    volume_ratio: np.ndarray,
    opens: np.ndarray,
    closes: np.ndarray,
    broken_level: float,
) -> dict[str, Any]:
    current_atr = atr[confirmed_at]
    displacement = (
        (closes[confirmed_at] - broken_level) / current_atr
        if bias == "bullish"
        else (broken_level - closes[confirmed_at]) / current_atr
    ) if np.isfinite(current_atr) and current_atr > 0 else 0.0
    body_atr = abs(closes[confirmed_at] - opens[confirmed_at]) / current_atr if np.isfinite(current_atr) and current_atr > 0 else 0.0
    source_ratio = volume_ratio[index] if np.isfinite(volume_ratio[index]) else 1.0
    breakout_ratio = volume_ratio[confirmed_at] if np.isfinite(volume_ratio[confirmed_at]) else 1.0
    source_direction = closes[index] <= opens[index] if bias == "bullish" else closes[index] >= opens[index]
    source_structure_score = 75.0 if source_direction else 45.0
    displacement_score = _clamp(displacement / 1.5 * 100)
    breakout_volume_score = _clamp((breakout_ratio - 0.5) / 1.5 * 100)
    body_score = _clamp(body_atr / 1.2 * 100)
    base_quality = (
        displacement_score * 0.35
        + breakout_volume_score * 0.30
        + body_score * 0.25
        + source_structure_score * 0.10
    )
    return {
        "bias": bias,
        "top": float(top),
        "bottom": float(bottom),
        "start_timestamp": frame.index[index].isoformat(),
        "confirmed_at_timestamp": frame.index[confirmed_at].isoformat(),
        "end_timestamp": None,
        "active": True,
        "status": "untested",
        "source_volume_ratio": round(float(source_ratio), 3),
        "breakout_volume_ratio": round(float(breakout_ratio), 3),
        "displacement_atr": round(float(displacement), 3),
        "body_atr": round(float(body_atr), 3),
        "formation_score": round(float(base_quality), 1),
        "freshness_score": 100.0,
        "integrity_score": 100.0,
        "retest_score": 0.0,
        "quality_score": round(float(base_quality * 0.75 + 25.0), 1),
        "age_bars": 0,
        "touch_count": 0,
        "penetration_ratio": 0.0,
        "close_location": 0.0,
        "rejection_wick_ratio": 0.0,
        "retest_volume_ratio": 1.0,
        "pierced": False,
        "first_retest_timestamp": None,
        "retest_timestamp": None,
        "retest_confirmed": False,
        "_confirmed_index": confirmed_at,
        "_base_quality": base_quality,
        "_inside": False,
    }


def _update_block_quality(block: dict[str, Any], age_bars: int) -> None:
    freshness = max(0.0, 100.0 - age_bars * 1.5)
    integrity = max(20.0, 100.0 - max(block["touch_count"] - 1, 0) * 20.0 - (15.0 if block["pierced"] else 0.0))
    if block["touch_count"]:
        quality = block["_base_quality"] * 0.45 + block["retest_score"] * 0.35 + freshness * 0.10 + integrity * 0.10
    else:
        quality = block["_base_quality"] * 0.75 + freshness * 0.25
    block["age_bars"] = age_bars
    block["freshness_score"] = round(freshness, 1)
    block["integrity_score"] = round(integrity, 1)
    block["quality_score"] = round(float(quality), 1)


def _record_retest(
    block: dict[str, Any], bar: int, frame: pd.DataFrame, highs: np.ndarray, lows: np.ndarray,
    opens: np.ndarray, closes: np.ndarray, volume_ratio: np.ndarray,
) -> None:
    height = block["top"] - block["bottom"]
    penetration = max((block["top"] - lows[bar]) / height, 0.0) if height > 0 else 0.0
    candle_range = highs[bar] - lows[bar]
    close_location = (closes[bar] - lows[bar]) / candle_range if candle_range > 0 else 0.5
    if block["bias"] == "bullish":
        rejection = (min(opens[bar], closes[bar]) - lows[bar]) / candle_range if candle_range > 0 else 0.0
        direction_score = 100.0 if closes[bar] > opens[bar] else 30.0
        confirmed = closes[bar] >= block["top"] and closes[bar] > opens[bar]
        close_score = _clamp(close_location * 100)
    else:
        rejection = (highs[bar] - max(opens[bar], closes[bar])) / candle_range if candle_range > 0 else 0.0
        direction_score = 100.0 if closes[bar] < opens[bar] else 30.0
        confirmed = closes[bar] <= block["bottom"] and closes[bar] < opens[bar]
        close_score = _clamp((1.0 - close_location) * 100)
    penetration_score = _clamp((1.0 - penetration) * 100)
    retest_volume = volume_ratio[bar] if np.isfinite(volume_ratio[bar]) else 1.0
    volume_score = _clamp((retest_volume - 0.5) / 1.5 * 100)
    score = penetration_score * 0.30 + close_score * 0.25 + _clamp(rejection * 200) * 0.20 + volume_score * 0.15 + direction_score * 0.10
    block["penetration_ratio"] = round(float(penetration), 3)
    block["close_location"] = round(float(close_location), 3)
    block["rejection_wick_ratio"] = round(float(rejection), 3)
    block["retest_volume_ratio"] = round(float(retest_volume), 3)
    block["retest_score"] = round(float(score), 1)
    if confirmed:
        block["retest_confirmed"] = True
        block["retest_timestamp"] = frame.index[bar].isoformat()
        block["status"] = "confirmed_retest"


def _detect_order_blocks(frame: pd.DataFrame, radius: int) -> list[dict[str, Any]]:
    high_col = "High" if "High" in frame else "high"; low_col = "Low" if "Low" in frame else "low"
    open_col = "Open" if "Open" in frame else "open"; close_col = "Close" if "Close" in frame else "close"
    highs = frame[high_col].to_numpy(float); lows = frame[low_col].to_numpy(float)
    opens = frame[open_col].to_numpy(float); closes = frame[close_col].to_numpy(float)
    previous_close = np.r_[np.nan, closes[:-1]]
    true_range = np.nanmax(np.vstack((highs - lows, np.abs(highs - previous_close), np.abs(lows - previous_close))), axis=0)
    atr = pd.Series(true_range).rolling(14, min_periods=1).mean().to_numpy(float)
    if "volume" in frame:
        volumes = frame["volume"].to_numpy(float)
    elif "Volume" in frame:
        volumes = frame["Volume"].to_numpy(float)
    else:
        volumes = np.full(len(frame), np.nan)
    volume_mean = pd.Series(volumes).rolling(20, min_periods=1).mean().to_numpy(float)
    volume_ratio = np.divide(volumes, volume_mean, out=np.full(len(frame), np.nan), where=np.isfinite(volume_mean) & (volume_mean > 0))
    high_pivots = set(_pivots(highs, radius, True)); low_pivots = set(_pivots(lows, radius, False))
    latest_high: int | None = None; latest_low: int | None = None
    crossed_high = crossed_low = False; blocks: list[dict[str, Any]] = []
    for bar in range(len(frame)):
        confirmed = bar - radius
        if confirmed in high_pivots: latest_high = confirmed; crossed_high = False
        if confirmed in low_pivots: latest_low = confirmed; crossed_low = False
        for block in blocks:
            if not block["active"] or bar <= block["_confirmed_index"]:
                continue
            invalidated = (block["bias"] == "bullish" and closes[bar] < block["bottom"]) or (block["bias"] == "bearish" and closes[bar] > block["top"])
            if invalidated:
                block["active"] = False; block["status"] = "invalidated"; block["end_timestamp"] = frame.index[bar].isoformat()
                continue
            pierced = (block["bias"] == "bullish" and lows[bar] < block["bottom"]) or (block["bias"] == "bearish" and highs[bar] > block["top"])
            if pierced:
                block["pierced"] = True
                block["status"] = "pierced"
            inside = lows[bar] <= block["top"] and highs[bar] >= block["bottom"]
            if inside and not block["_inside"]:
                block["touch_count"] += 1
                if block["first_retest_timestamp"] is None:
                    block["first_retest_timestamp"] = frame.index[bar].isoformat()
                block["status"] = "touched"
            if inside:
                _record_retest(block, bar, frame, highs, lows, opens, closes, volume_ratio)
            if block["pierced"] and not block["retest_confirmed"]:
                block["status"] = "pierced"
            block["_inside"] = inside
            _update_block_quality(block, bar - block["_confirmed_index"])
        if latest_high is not None and not crossed_high and closes[bar] > highs[latest_high] and bar > latest_high:
            crossed_high = True; source = latest_high + int(np.nanargmin(lows[latest_high:bar]))
            candidate = _block(frame, source, highs[source], lows[source], "bullish", bar, atr=atr, volume_ratio=volume_ratio, opens=opens, closes=closes, broken_level=highs[latest_high])
            _append_best_block(blocks, candidate)
        if latest_low is not None and not crossed_low and closes[bar] < lows[latest_low] and bar > latest_low:
            crossed_low = True; source = latest_low + int(np.nanargmax(highs[latest_low:bar]))
            candidate = _block(frame, source, highs[source], lows[source], "bearish", bar, atr=atr, volume_ratio=volume_ratio, opens=opens, closes=closes, broken_level=lows[latest_low])
            _append_best_block(blocks, candidate)
    last_index = len(frame) - 1
    for block in blocks:
        _update_block_quality(block, max(0, last_index - block["_confirmed_index"]))
        for private_key in ("_confirmed_index", "_base_quality", "_inside"):
            block.pop(private_key, None)
    return blocks


def _append_best_block(blocks: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    overlaps = [
        block
        for block in blocks
        if block["active"]
        and block["bias"] == candidate["bias"]
        and _overlap_ratio(block, candidate) >= 0.60
    ]
    if any(block["_base_quality"] >= candidate["_base_quality"] for block in overlaps):
        return
    for block in overlaps:
        block["active"] = False
        block["status"] = "superseded"
        block["end_timestamp"] = candidate["confirmed_at_timestamp"]
    blocks.append(candidate)


def bullish_order_block_features(frame: pd.DataFrame, radius: int = 5) -> pd.DataFrame:
    """Return causal per-bar bullish order-block features for shadow evaluation."""
    result = pd.DataFrame(index=frame.index)
    result["distance_pct"] = np.nan
    result["quality_score"] = np.nan
    result["formation_score"] = np.nan
    result["retest_score"] = np.nan
    result["touch_count"] = 0
    result["age_bars"] = 0
    result["retest_confirmed"] = False
    result["pierced"] = False
    result["status"] = None
    if len(frame) < radius * 2 + 3:
        return result
    high_col = "High" if "High" in frame else "high" if "high" in frame else None
    low_col = "Low" if "Low" in frame else "low" if "low" in frame else None
    open_col = "Open" if "Open" in frame else "open" if "open" in frame else None
    close_col = "Close" if "Close" in frame else "close" if "close" in frame else None
    if high_col is None or low_col is None or open_col is None or close_col is None:
        return result
    highs = frame[high_col].to_numpy(float); lows = frame[low_col].to_numpy(float)
    opens = frame[open_col].to_numpy(float); closes = frame[close_col].to_numpy(float)
    previous_close = np.r_[np.nan, closes[:-1]]
    true_range = np.nanmax(np.vstack((highs - lows, np.abs(highs - previous_close), np.abs(lows - previous_close))), axis=0)
    atr = pd.Series(true_range).rolling(14, min_periods=1).mean().to_numpy(float)
    volume_col = "volume" if "volume" in frame else "Volume" if "Volume" in frame else None
    volumes = frame[volume_col].to_numpy(float) if volume_col else np.full(len(frame), np.nan)
    volume_mean = pd.Series(volumes).rolling(20, min_periods=1).mean().to_numpy(float)
    volume_ratio = np.divide(volumes, volume_mean, out=np.full(len(frame), np.nan), where=np.isfinite(volume_mean) & (volume_mean > 0))
    high_pivots = set(_pivots(highs, radius, True)); low_pivots = set(_pivots(lows, radius, False))
    latest_high: int | None = None; latest_low: int | None = None
    crossed_high = crossed_low = False; blocks: list[dict[str, Any]] = []
    for bar in range(len(frame)):
        confirmed = bar - radius
        if confirmed in high_pivots: latest_high = confirmed; crossed_high = False
        if confirmed in low_pivots: latest_low = confirmed; crossed_low = False
        for block in blocks:
            if not block["active"] or bar <= block["_confirmed_index"]:
                continue
            if closes[bar] < block["bottom"]:
                block["active"] = False; block["status"] = "invalidated"
                continue
            if lows[bar] < block["bottom"]:
                block["pierced"] = True; block["status"] = "pierced"
            inside = lows[bar] <= block["top"] and highs[bar] >= block["bottom"]
            if inside and not block["_inside"]:
                block["touch_count"] += 1
                block["status"] = "touched"
            if inside:
                _record_retest(block, bar, frame, highs, lows, opens, closes, volume_ratio)
            if block["pierced"] and not block["retest_confirmed"]:
                block["status"] = "pierced"
            block["_inside"] = inside
            _update_block_quality(block, bar - block["_confirmed_index"])
        active = [block for block in blocks if block["active"] and block["bias"] == "bullish"]
        if active:
            selected = min(active, key=lambda block: max((lows[bar] / block["top"] - 1) * 100, 0.0))
            result.iat[bar, result.columns.get_loc("distance_pct")] = max((lows[bar] / selected["top"] - 1) * 100, 0.0)
            for column in ("quality_score", "formation_score", "retest_score", "touch_count", "age_bars", "retest_confirmed", "pierced", "status"):
                result.iat[bar, result.columns.get_loc(column)] = selected[column]
        if latest_high is not None and not crossed_high and closes[bar] > highs[latest_high] and bar > latest_high:
            crossed_high = True
            source = latest_high + int(np.nanargmin(lows[latest_high:bar]))
            candidate = _block(frame, source, highs[source], lows[source], "bullish", bar, atr=atr, volume_ratio=volume_ratio, opens=opens, closes=closes, broken_level=highs[latest_high])
            _append_best_block(blocks, candidate)
        if latest_low is not None and not crossed_low and closes[bar] < lows[latest_low]:
            crossed_low = True
    return result


def bullish_order_block_distance(frame: pd.DataFrame, radius: int = 5) -> pd.Series:
    """Distance to a bullish block that already existed at each bar; no backfill."""
    result = pd.Series(np.nan, index=frame.index, dtype=float)
    if len(frame) < radius * 2 + 3:
        return result
    high_col = "High" if "High" in frame else "high" if "high" in frame else None
    low_col = "Low" if "Low" in frame else "low" if "low" in frame else None
    close_col = "Close" if "Close" in frame else "close" if "close" in frame else None
    if high_col is None or low_col is None or close_col is None:
        return result
    highs = frame[high_col].to_numpy(float); lows = frame[low_col].to_numpy(float); closes = frame[close_col].to_numpy(float)
    high_pivots = set(_pivots(highs, radius, True)); low_pivots = set(_pivots(lows, radius, False))
    latest_high: int | None = None; latest_low: int | None = None
    crossed_high = crossed_low = False; blocks: list[dict[str, Any]] = []
    for bar in range(len(frame)):
        confirmed = bar - radius
        if confirmed in high_pivots: latest_high = confirmed; crossed_high = False
        if confirmed in low_pivots: latest_low = confirmed; crossed_low = False
        for block in blocks:
            if block["active"] and lows[bar] < block["bottom"]: block["active"] = False
        distances = [max((lows[bar] / float(block["top"]) - 1) * 100, 0.0) for block in blocks if block["active"]]
        if distances: result.iloc[bar] = min(distances)
        if latest_high is not None and not crossed_high and closes[bar] > highs[latest_high] and bar > latest_high:
            crossed_high = True; source = latest_high + int(np.nanargmin(lows[latest_high:bar])); blocks.append({"top": float(highs[source]), "bottom": float(lows[source]), "active": True})
        if latest_low is not None and not crossed_low and closes[bar] < lows[latest_low]: crossed_low = True
    return result
