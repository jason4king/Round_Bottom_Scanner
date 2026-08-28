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
    blocks: list[dict[str, Any]] = []

    for bar in range(len(sample)):
        confirmed = bar - pivot_radius
        if confirmed in high_pivots:
            latest_high = confirmed
            crossed_high = False
        if confirmed in low_pivots:
            latest_low = confirmed
            crossed_low = False

        for block in blocks:
            if not block["active"]:
                continue
            if block["bias"] == "bullish" and lows[bar] < block["bottom"]:
                block["active"] = False
                block["end_timestamp"] = sample.index[bar].isoformat()
            elif block["bias"] == "bearish" and highs[bar] > block["top"]:
                block["active"] = False
                block["end_timestamp"] = sample.index[bar].isoformat()

        if latest_high is not None and not crossed_high and closes[bar] > highs[latest_high]:
            crossed_high = True
            trend = 1
            if bar > latest_high:
                source = latest_high + int(np.nanargmin(lows[latest_high:bar]))
                blocks.append(_block(sample, source, highs[source], lows[source], "bullish", bar))
        if latest_low is not None and not crossed_low and closes[bar] < lows[latest_low]:
            crossed_low = True
            trend = -1
            if bar > latest_low:
                source = latest_low + int(np.nanargmax(highs[latest_low:bar]))
                blocks.append(_block(sample, source, highs[source], lows[source], "bearish", bar))

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
    blocks.extend(_detect_order_blocks(sample, order_block_radius))
    unique_blocks = {(block["bias"], block["start_timestamp"]): block for block in blocks}
    active: list[dict[str, Any]] = []
    for bias in ("bullish", "bearish"):
        matches = [block for block in unique_blocks.values() if block["active"] and block["bias"] == bias]
        matches.sort(key=lambda block: block["start_timestamp"])
        active.extend(matches[-max_blocks_per_side:])
    return {"trend": "bullish" if trend > 0 else "bearish" if trend < 0 else "neutral", "levels": levels, "order_blocks": active}


def _block(frame: pd.DataFrame, index: int, top: float, bottom: float, bias: str, confirmed_at: int) -> dict[str, Any]:
    return {
        "bias": bias,
        "top": float(top),
        "bottom": float(bottom),
        "start_timestamp": frame.index[index].isoformat(),
        "confirmed_at_timestamp": frame.index[confirmed_at].isoformat(),
        "end_timestamp": None,
        "active": True,
    }


def _detect_order_blocks(frame: pd.DataFrame, radius: int) -> list[dict[str, Any]]:
    high_col = "High" if "High" in frame else "high"; low_col = "Low" if "Low" in frame else "low"
    close_col = "Close" if "Close" in frame else "close"
    highs = frame[high_col].to_numpy(float); lows = frame[low_col].to_numpy(float); closes = frame[close_col].to_numpy(float)
    high_pivots = set(_pivots(highs, radius, True)); low_pivots = set(_pivots(lows, radius, False))
    latest_high: int | None = None; latest_low: int | None = None
    crossed_high = crossed_low = False; blocks: list[dict[str, Any]] = []
    for bar in range(len(frame)):
        confirmed = bar - radius
        if confirmed in high_pivots: latest_high = confirmed; crossed_high = False
        if confirmed in low_pivots: latest_low = confirmed; crossed_low = False
        for block in blocks:
            if block["active"] and ((block["bias"] == "bullish" and lows[bar] < block["bottom"]) or (block["bias"] == "bearish" and highs[bar] > block["top"])):
                block["active"] = False; block["end_timestamp"] = frame.index[bar].isoformat()
        if latest_high is not None and not crossed_high and closes[bar] > highs[latest_high] and bar > latest_high:
            crossed_high = True; source = latest_high + int(np.nanargmin(lows[latest_high:bar])); blocks.append(_block(frame, source, highs[source], lows[source], "bullish", bar))
        if latest_low is not None and not crossed_low and closes[bar] < lows[latest_low] and bar > latest_low:
            crossed_low = True; source = latest_low + int(np.nanargmax(highs[latest_low:bar])); blocks.append(_block(frame, source, highs[source], lows[source], "bearish", bar))
    return blocks


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
    highs=frame[high_col].to_numpy(float); lows=frame[low_col].to_numpy(float); closes=frame[close_col].to_numpy(float)
    high_pivots=set(_pivots(highs,radius,True)); low_pivots=set(_pivots(lows,radius,False))
    latest_high: int|None=None; latest_low: int|None=None; crossed_high=crossed_low=False; blocks: list[dict[str,Any]]=[]
    for bar in range(len(frame)):
        confirmed=bar-radius
        if confirmed in high_pivots: latest_high=confirmed; crossed_high=False
        if confirmed in low_pivots: latest_low=confirmed; crossed_low=False
        for block in blocks:
            if block["active"] and lows[bar] < block["bottom"]: block["active"]=False
        distances=[max((lows[bar]/float(block["top"])-1)*100,0.0) for block in blocks if block["active"] and block["bias"]=="bullish"]
        if distances: result.iloc[bar]=min(distances)
        if latest_high is not None and not crossed_high and closes[bar]>highs[latest_high] and bar>latest_high:
            crossed_high=True; source=latest_high+int(np.nanargmin(lows[latest_high:bar])); blocks.append(_block(frame,source,highs[source],lows[source],"bullish",bar))
        if latest_low is not None and not crossed_low and closes[bar]<lows[latest_low]: crossed_low=True
    return result
