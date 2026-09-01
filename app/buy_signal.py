from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings
from app.market_data import _is_closed
from app.market_structure import calculate_market_structure
from app.scanner_engine import add_indicators

# Mirrors PriceChart.tsx's buyLookback / structure_radius per timeframe so the
# server-side signal matches exactly what the chart marker shows.
BUY_LOOKBACK = {"weekly": 52, "daily": 100, "4hour": 120}
STRUCTURE_RADIUS = {"weekly": 15, "daily": 20, "4hour": 12}

TIMEFRAME_LABELS = {"weekly": "周线", "daily": "日线", "4hour": "4小时"}

LABELS = {
    "trend_start": "趋势转多",
    "trend_pullback": "趋势回踩",
    "rsi_v_bottom_buy": "V底确认",
    "rsi_breakout_buy": "买点确认",
}


@dataclass
class BuySignal:
    timeframe: str
    signal_type: str
    label: str
    timestamp: str
    last_price: float
    details: dict[str, Any] = field(default_factory=dict)


def _near_bullish_block(order_blocks: list[dict], low: float, time: pd.Timestamp, max_distance: float) -> bool:
    for block in order_blocks:
        if block["bias"] != "bullish":
            continue
        if time < pd.Timestamp(block["confirmed_at_timestamp"]):
            continue
        distance = max(low / block["top"] - 1, 0.0)
        if low >= block["bottom"] * 0.98 and distance <= max_distance:
            return True
    return False


def _build_points(chart: pd.DataFrame, tail_window: int) -> list[dict[str, Any]]:
    window = chart.tail(tail_window)
    points = []
    for timestamp, row in window.iterrows():
        points.append({
            "time": timestamp,
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["Close"]),
            "ema12": float(row["EMA12"]), "ema144": float(row["EMA144"]), "ema169": float(row["EMA169"]),
            "rsi": float(row["RSI10"]) if pd.notna(row["RSI10"]) else None,
            "rsi_v_bottom_buy": bool(row["RSI_V_BOTTOM_BUY"]),
            "rsi_breakout_buy": bool(row["RSI_BREAKOUT_BUY"]),
            "rsi_enhanced_buy": bool(row["RSI_ENHANCED_BUY"]),
            "rsi_bullish_divergence": bool(row["RSI_BULL_DIVERGENCE"]),
            "bullish_order_block_distance_pct": float(row["BULLISH_OB_DISTANCE_PCT"]) if pd.notna(row["BULLISH_OB_DISTANCE_PCT"]) else None,
        })
    return points


def _is_main_buy_candidate(points: list[dict], order_blocks: list[dict], index: int) -> bool:
    p = points[index]
    if p["rsi_breakout_buy"] or p["rsi_v_bottom_buy"]:
        return True
    if not p["rsi_enhanced_buy"] or p["bullish_order_block_distance_pct"] is None or p["bullish_order_block_distance_pct"] > 5:
        return False
    candle_gain = p["close"] / p["open"] - 1 if p["open"] > 0 else float("inf")
    if candle_gain > 0.04:
        return False
    if not _near_bullish_block(order_blocks, p["low"], p["time"], 0.05):
        return False
    previous = points[index - 1] if index > 0 else None
    trend_reclaimed = previous is not None and p["close"] >= p["ema12"] and p["ema12"] > previous["ema12"]
    if not trend_reclaimed and not p["rsi_bullish_divergence"]:
        return False
    long_trend_reclaimed = p["ema12"] > p["ema144"] and p["ema12"] > p["ema169"]
    if not long_trend_reclaimed and not p["rsi_bullish_divergence"]:
        return False
    recent = points[max(0, index - 4):index + 1]
    return any(r["bullish_order_block_distance_pct"] is not None and r["bullish_order_block_distance_pct"] <= 1 for r in recent)


def _trend_start_times(points: list[dict], order_blocks: list[dict], buy_start: int) -> set:
    times = set()
    for index in range(buy_start, len(points)):
        if index == 0:
            continue
        p, previous = points[index], points[index - 1]
        previous_long_top = max(previous["ema144"], previous["ema169"])
        current_long_top = max(p["ema144"], p["ema169"])
        crossed_long_trend = previous["ema12"] <= previous_long_top and p["ema12"] > current_long_top
        price_confirmed = p["close"] > current_long_top and p["close"] > p["ema12"]
        long_trend_extension = p["close"] / current_long_top - 1 if current_long_top > 0 else float("inf")
        candle_gain = p["close"] / p["open"] - 1 if p["open"] > 0 else float("inf")
        recent_bars = points[max(0, index - 11):index + 1]
        recent_order_block_support = any(_near_bullish_block(order_blocks, r["low"], r["time"], 0.02) for r in recent_bars)
        recent_long_tunnel_support = any(
            r["low"] <= max(r["ema144"], r["ema169"]) * 1.02 and r["low"] >= min(r["ema144"], r["ema169"]) * 0.95
            for r in recent_bars
        )
        recent_support = recent_order_block_support or recent_long_tunnel_support
        if (
            crossed_long_trend and price_confirmed and long_trend_extension <= 0.05
            and p["ema12"] > previous["ema12"] and p["rsi"] is not None and 40 <= p["rsi"] <= 70
            and candle_gain <= 0.04 and recent_support
        ):
            times.add(p["time"])
    return times


def _trend_pullback_times(points: list[dict], buy_start: int) -> set:
    times = set()
    for index in range(buy_start, len(points)):
        if index == 0:
            continue
        p, previous = points[index], points[index - 1]
        support_window = points[max(buy_start, index - 3):index + 1]
        support_anchors = [
            r for r in support_window
            if r["rsi"] is not None and 25 <= r["rsi"] <= 35
            and r["bullish_order_block_distance_pct"] is not None and r["bullish_order_block_distance_pct"] <= 5
        ]
        support_anchor = None
        for r in support_anchors:
            if support_anchor is None or (r["rsi"] or 100) < (support_anchor["rsi"] or 100):
                support_anchor = r
        if support_anchor is None:
            continue
        reached_ema144 = p["ema144"] > 0 and p["low"] <= p["ema144"] * 1.02 and p["high"] >= p["ema144"] * 0.98
        closed_above_ema144 = p["close"] >= p["ema144"]
        bullish_support_confirmation = p["close"] > p["open"] or p["close"] > previous["close"]
        rsi_recovering = p["rsi"] is not None and support_anchor["rsi"] is not None and p["rsi"] >= support_anchor["rsi"]
        if reached_ema144 and closed_above_ema144 and bullish_support_confirmation and rsi_recovering:
            times.add(p["time"])
    return times


def evaluate_signal(repository, symbol: str, timeframe: str) -> BuySignal | None:
    """Replicates the orange/gold buy-marker logic from PriceChart.tsx for the
    given timeframe, and reports a signal only if it lands on the latest closed bar."""
    bars = repository.read(symbol, timeframe)
    if bars.empty:
        return None
    now = pd.Timestamp.now(tz="UTC")
    bars = bars.copy()
    bars["is_closed"] = bars["timestamp_utc"].map(lambda value: _is_closed(value, timeframe, now))
    bars = bars[bars.is_closed]
    if bars.empty:
        return None
    chart = bars.set_index("timestamp_utc")
    chart = add_indicators(chart.rename(columns={"close": "Close"}))
    order_blocks = calculate_market_structure(chart, pivot_radius=STRUCTURE_RADIUS[timeframe])["order_blocks"]

    buy_lookback = BUY_LOOKBACK[timeframe]
    points = _build_points(chart, buy_lookback + 20)
    n = len(points)
    if n == 0:
        return None
    buy_start = max(0, n - buy_lookback)
    last_index = n - 1
    last = points[last_index]

    trend_start_times = _trend_start_times(points, order_blocks, buy_start)
    trend_pullback_times = _trend_pullback_times(points, buy_start)
    is_main_candidate = _is_main_buy_candidate(points, order_blocks, last_index)

    if trend_start_times:
        later_high_conviction_reversal = is_main_candidate and (last["rsi_v_bottom_buy"] or last["rsi_bullish_divergence"])
        is_combined = last["time"] in trend_start_times or last["time"] in trend_pullback_times or later_high_conviction_reversal
    else:
        is_combined = is_main_candidate or last["time"] in trend_pullback_times

    if not is_combined:
        return None

    if last["time"] in trend_start_times:
        signal_type = "trend_start"
    elif last["time"] in trend_pullback_times:
        signal_type = "trend_pullback"
    elif last["rsi_v_bottom_buy"]:
        signal_type = "rsi_v_bottom_buy"
    else:
        signal_type = "rsi_breakout_buy"

    row = chart.iloc[-1]
    details: dict[str, Any] = {"rsi": last["rsi"]}
    if signal_type == "rsi_breakout_buy":
        details.update({
            "neckline_price": float(row["RSI_NECKLINE"]) if pd.notna(row["RSI_NECKLINE"]) else None,
            "stop_level": float(row["RSI_STOP_LEVEL"]) if pd.notna(row["RSI_STOP_LEVEL"]) else None,
            "first_low_price": float(row["RSI_FIRST_LOW_PRICE"]) if pd.notna(row["RSI_FIRST_LOW_PRICE"]) else None,
            "second_low_price": float(row["RSI_SECOND_LOW_PRICE"]) if pd.notna(row["RSI_SECOND_LOW_PRICE"]) else None,
            "breakout_volume_ratio": float(row["RSI_BREAKOUT_VOLUME_RATIO"]) if pd.notna(row["RSI_BREAKOUT_VOLUME_RATIO"]) else None,
            "bullish_divergence": bool(row["RSI_BULL_DIVERGENCE"]),
            "order_block_confluence": bool(row["RSI_ORDER_BLOCK_CONFLUENCE"]),
        })
    elif signal_type == "rsi_v_bottom_buy":
        details.update({
            "stop_level": float(row["RSI_STOP_LEVEL"]) if pd.notna(row["RSI_STOP_LEVEL"]) else None,
            "order_block_confluence": bool(row["RSI_ORDER_BLOCK_CONFLUENCE"]),
        })
    elif signal_type == "trend_start":
        details.update({"ema144": last["ema144"], "ema169": last["ema169"]})
    elif signal_type == "trend_pullback":
        details.update({"ema144": last["ema144"]})

    return BuySignal(
        timeframe=timeframe,
        signal_type=signal_type,
        label=LABELS[signal_type],
        timestamp=last["time"].isoformat(),
        last_price=last["close"],
        details=details,
    )


def build_wecom_message(symbol: str, signal: BuySignal, total_score: float, triggered_factors: list[str]) -> str:
    timeframe_label = TIMEFRAME_LABELS[signal.timeframe]
    lines = [
        f"🟠 圆弧底扫描 · {timeframe_label}买点提示",
        f"{symbol}｜{timeframe_label}",
        f"{signal.label}",
        "",
        f"现价 {signal.last_price:.2f}　综合评分 {total_score:.1f}",
        f"触发因子：{'+'.join(triggered_factors) if triggered_factors else '无'}",
    ]
    d = signal.details
    if signal.signal_type == "rsi_breakout_buy":
        if d.get("neckline_price") is not None and d.get("stop_level") is not None:
            lines.append(f"颈线 {d['neckline_price']:.2f}　止损 {d['stop_level']:.2f}")
        if d.get("first_low_price") is not None and d.get("second_low_price") is not None:
            lines.append(f"双底 {d['first_low_price']:.2f} → {d['second_low_price']:.2f}")
        ratio = f"{d['breakout_volume_ratio']:.1f}x" if d.get("breakout_volume_ratio") is not None else "-"
        lines.append(f"突破量比 {ratio}｜背离 {'是' if d.get('bullish_divergence') else '否'}｜订单块共振 {'是' if d.get('order_block_confluence') else '否'}")
    elif signal.signal_type == "rsi_v_bottom_buy":
        if d.get("stop_level") is not None:
            lines.append(f"止损 {d['stop_level']:.2f}")
        lines.append(f"支撑类型：{'订单块' if d.get('order_block_confluence') else '极度超卖/维加斯通道'}")
    elif signal.signal_type == "trend_start":
        lines.append("EMA12 上穿长期通道（EMA144/169）")
        if d.get("rsi") is not None:
            lines.append(f"RSI {d['rsi']:.1f}")
    elif signal.signal_type == "trend_pullback":
        lines.append("回踩订单块/EMA144 支撑后收盘企稳")
        if d.get("rsi") is not None:
            lines.append(f"RSI 回升至 {d['rsi']:.1f}")
    bar_time_et = pd.Timestamp(signal.timestamp).tz_convert("America/New_York")
    bar_time_label = bar_time_et.strftime("%Y-%m-%d") if signal.timeframe == "daily" else bar_time_et.strftime("%Y-%m-%d %H:%M")
    lines += [
        "",
        f"触发K线：{bar_time_label} ET 收盘",
        f"推送时间：{pd.Timestamp.now(tz='America/New_York').strftime('%Y-%m-%d %H:%M')} ET",
        "",
        "⚠️ 仅供研究参考，非投资建议，请自行核实并控制仓位",
    ]
    return "\n".join(lines)


def _state_path(settings: Settings) -> Path:
    return settings.database_path.parent / "pushed_signals_state.json"


def load_pushed_state(settings: Settings) -> dict[str, str]:
    path = _state_path(settings)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_pushed_state(settings: Settings, state: dict[str, str]) -> None:
    _state_path(settings).write_text(json.dumps(state), encoding="utf-8")


def already_pushed(state: dict[str, str], timeframe: str, symbol: str, signal: BuySignal) -> bool:
    key = f"{timeframe}:{symbol}"
    return state.get(key) == signal.timestamp


def mark_pushed(state: dict[str, str], timeframe: str, symbol: str, signal: BuySignal) -> None:
    state[f"{timeframe}:{symbol}"] = signal.timestamp
