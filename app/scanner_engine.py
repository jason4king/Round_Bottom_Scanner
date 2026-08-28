from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from app.json_utils import json_safe
from app.market_structure import bullish_order_block_distance
from app.patternpy_observer import observe_classic_patterns


@dataclass
class FactorResult:
    factor_id: str
    signal_name: str
    triggered: bool
    timestamp: str | None
    details: dict[str, Any]
    tier: str | None = None


BASE_SCORES = {"F2": 3, "F3": 4, "F4": 1, "F5": 4, "F6": 1}
SCORING_FACTOR_IDS = tuple(f"F{i}" for i in range(1, 7))
F1_SCORES = {"A": 4, "B": 3, "C": 2}
TIMEFRAME_MULTIPLIERS = {
    frozenset(("weekly", "daily", "4hour")): 6.0,
    frozenset(("weekly", "daily")): 4.5,
    frozenset(("weekly", "4hour")): 3.5,
    frozenset(("daily", "4hour")): 3.0,
    frozenset(("weekly",)): 2.5,
    frozenset(("daily",)): 1.5,
    frozenset(("4hour",)): 1.0,
}


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    bars = frame.copy()
    for period in (12, 144, 169, 576, 676):
        bars[f"EMA{period}"] = bars["Close"].ewm(span=period, adjust=False).mean()
    bars["RSI10"] = _wilder_rsi(bars["Close"], 10)
    bars["RSI_SIGNAL10"] = bars["RSI10"].rolling(10).mean()
    bull_cross = (bars["RSI10"] > bars["RSI_SIGNAL10"]) & (bars["RSI10"].shift(1) <= bars["RSI_SIGNAL10"].shift(1))
    bars["RSI_BULL_CROSS"] = bull_cross
    bars["BULLISH_OB_DISTANCE_PCT"] = bullish_order_block_distance(bars)
    structure = _rsi_bottom_structure(bars)
    for column in structure:
        bars[column] = structure[column]
    return bars


def _wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """TradingView-compatible RSI using Wilder's smoothed averages."""
    values = close.astype(float)
    change = values.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    average_gain = pd.Series(np.nan, index=values.index, dtype=float)
    average_loss = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) <= period:
        return average_gain
    average_gain.iloc[period] = gains.iloc[1 : period + 1].mean()
    average_loss.iloc[period] = losses.iloc[1 : period + 1].mean()
    for index in range(period + 1, len(values)):
        average_gain.iloc[index] = (average_gain.iloc[index - 1] * (period - 1) + gains.iloc[index]) / period
        average_loss.iloc[index] = (average_loss.iloc[index - 1] * (period - 1) + losses.iloc[index]) / period
    ratio = average_gain / average_loss
    rsi = 100 - 100 / (1 + ratio)
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100.0)
    return rsi.mask((average_gain == 0) & (average_loss > 0), 0.0)


def _rsi_bottom_structure(bars: pd.DataFrame, pivot_radius: int = 3) -> pd.DataFrame:
    """Confirm price W-bottoms and bullish RSI divergence without backdating."""
    result = pd.DataFrame(False, index=bars.index, columns=["RSI_W_BOTTOM", "RSI_BULL_DIVERGENCE", "RSI_ORDER_BLOCK_CONFLUENCE", "RSI_ENHANCED_BUY", "RSI_BREAKOUT_BUY", "RSI_V_BOTTOM_BUY"])
    for column in ("RSI_FIRST_LOW_PRICE", "RSI_SECOND_LOW_PRICE", "RSI_FIRST_LOW_VALUE", "RSI_SECOND_LOW_VALUE", "RSI_NECKLINE", "RSI_STOP_LEVEL", "RSI_BREAKOUT_VOLUME_RATIO"):
        result[column] = np.nan
    low_column = "Low" if "Low" in bars else "low" if "low" in bars else None
    high_column = "High" if "High" in bars else "high" if "high" in bars else None
    if low_column is None or high_column is None:
        return result
    lows = bars[low_column].to_numpy(float)
    highs = bars[high_column].to_numpy(float)
    closes = bars["Close"].to_numpy(float)
    open_column = "Open" if "Open" in bars else "open" if "open" in bars else None
    opens = bars[open_column].to_numpy(float) if open_column else closes.copy()
    ema12 = bars["EMA12"].to_numpy(float) if "EMA12" in bars else closes.copy()
    ema576 = bars["EMA576"].to_numpy(float) if "EMA576" in bars else np.full(len(bars), np.nan)
    ema676 = bars["EMA676"].to_numpy(float) if "EMA676" in bars else np.full(len(bars), np.nan)
    ema144 = bars["EMA144"].to_numpy(float) if "EMA144" in bars else np.full(len(bars), np.nan)
    ema169 = bars["EMA169"].to_numpy(float) if "EMA169" in bars else np.full(len(bars), np.nan)
    ob_distance = bars["BULLISH_OB_DISTANCE_PCT"].to_numpy(float) if "BULLISH_OB_DISTANCE_PCT" in bars else np.full(len(bars), np.nan)
    volume_column = "Volume" if "Volume" in bars else "volume" if "volume" in bars else None
    volumes = bars[volume_column].to_numpy(float) if volume_column else np.full(len(bars), np.nan)
    previous_close = np.r_[np.nan, closes[:-1]]
    true_range = np.nanmax(np.c_[highs - lows, abs(highs - previous_close), abs(lows - previous_close)], axis=1)
    atr14 = pd.Series(true_range).rolling(14).mean().to_numpy()
    rsi = bars["RSI10"].to_numpy(float)
    crosses = bars["RSI_BULL_CROSS"].to_numpy(bool)
    rsi_signal = bars["RSI_SIGNAL10"].to_numpy(float) if "RSI_SIGNAL10" in bars else pd.Series(rsi).rolling(10, min_periods=1).mean().to_numpy()
    pivots: list[int] = []
    active_setup: dict[str, float | int | bool] | None = None
    breakout_setup: dict[str, float | int | bool] | None = None
    v_setup: dict[str, float | int | bool] | None = None
    for confirmation in range(2 * pivot_radius, len(bars)):
        recent_low_start = max(0, confirmation - 9)
        vegas_low = min(ema576[confirmation], ema676[confirmation])
        vegas_high = max(ema576[confirmation], ema676[confirmation])
        near_vegas_support = np.isfinite(vegas_low) and vegas_low * .98 <= lows[confirmation] <= vegas_high * 1.02
        vegas_oversold = near_vegas_support and np.isfinite(rsi[confirmation]) and rsi[confirmation] <= 27
        v_order_block = np.isfinite(ob_distance[confirmation]) and ob_distance[confirmation] <= 1.0
        order_block_oversold = v_order_block and np.isfinite(rsi[confirmation]) and rsi[confirmation] <= 28
        deeply_oversold = np.isfinite(rsi[confirmation]) and rsi[confirmation] <= 25
        is_capitulation_low = (deeply_oversold or vegas_oversold or order_block_oversold) and lows[confirmation] <= np.nanmin(lows[recent_low_start : confirmation + 1])
        extreme_oversold = np.isfinite(rsi[confirmation]) and rsi[confirmation] <= 20
        if is_capitulation_low and (v_order_block or extreme_oversold or vegas_oversold):
            stop = lows[confirmation] - .5 * atr14[confirmation] if np.isfinite(atr14[confirmation]) else lows[confirmation] * .99
            confirmation_window = 4 if v_order_block else 8
            v_setup = {"anchor": confirmation, "expires": confirmation + confirmation_window, "stop": stop, "order_block": v_order_block, "extreme": extreme_oversold, "vegas": vegas_oversold}

        if v_setup is not None:
            anchor = int(v_setup["anchor"])
            expired = confirmation > int(v_setup["expires"]) or lows[confirmation] < lows[anchor] * .98
            recent_cross = bool(crosses[anchor : confirmation + 1].any())
            breakout_lookback = 1 if bool(v_setup["extreme"]) else 3
            high_breakout = confirmation > anchor and confirmation >= breakout_lookback and closes[confirmation] > float(np.nanmax(highs[confirmation - breakout_lookback : confirmation]))
            close_breakout = confirmation > anchor and confirmation >= breakout_lookback and closes[confirmation] > float(np.nanmax(closes[confirmation - breakout_lookback : confirmation]))
            short_breakout = close_breakout if bool(v_setup["order_block"]) else high_breakout
            candle_gain = closes[confirmation] / opens[confirmation] - 1.0 if opens[confirmation] else np.inf
            ema_distance = closes[confirmation] / ema12[confirmation] - 1.0 if ema12[confirmation] else np.inf
            risk = (closes[confirmation] - float(v_setup["stop"])) / closes[confirmation] if closes[confirmation] else np.inf
            trend_reclaimed = bool(v_setup["extreme"]) or closes[confirmation] >= ema12[confirmation] or (bool(v_setup["order_block"]) and closes[confirmation] >= ema12[confirmation] * .99)
            v_rsi_limit = 60 if bool(v_setup["extreme"]) else 50
            candle_limit = .07 if bool(v_setup["order_block"]) else .04
            risk_limit = .16 if bool(v_setup["order_block"]) else .07
            v_quality = trend_reclaimed and rsi[confirmation] <= v_rsi_limit and rsi[confirmation] > rsi_signal[confirmation] and candle_gain <= candle_limit and ema_distance <= .05 and risk <= risk_limit
            if expired:
                v_setup = None
            elif recent_cross and short_breakout and v_quality:
                result.at[bars.index[confirmation], "RSI_V_BOTTOM_BUY"] = True
                result.at[bars.index[confirmation], "RSI_ORDER_BLOCK_CONFLUENCE"] = bool(v_setup["order_block"])
                result.at[bars.index[confirmation], "RSI_SECOND_LOW_PRICE"] = lows[anchor]
                result.at[bars.index[confirmation], "RSI_SECOND_LOW_VALUE"] = rsi[anchor]
                result.at[bars.index[confirmation], "RSI_NECKLINE"] = float(np.nanmax(highs[confirmation - breakout_lookback : confirmation]))
                result.at[bars.index[confirmation], "RSI_STOP_LEVEL"] = float(v_setup["stop"])
                v_setup = None

        pivot = confirmation - pivot_radius
        window = lows[pivot - pivot_radius : pivot + pivot_radius + 1]
        if np.isfinite(rsi[pivot]) and lows[pivot] == np.nanmin(window) and int(np.nanargmin(window)) == pivot_radius:
            pivots.append(pivot)
            for first in reversed(pivots[:-1]):
                spacing = pivot - first
                if spacing > 40:
                    break
                if spacing < 5 or not np.isfinite(rsi[first]):
                    continue
                price_difference = abs(lows[pivot] / lows[first] - 1.0)
                neckline = float(np.nanmax(highs[first : pivot + 1]))
                rebound = neckline / min(lows[first], lows[pivot]) - 1.0
                is_w = price_difference <= .05 and rebound >= .03
                divergence = lows[pivot] <= lows[first] * 1.02 and rsi[pivot] >= rsi[first] + 2.0
                order_block_confluence = np.isfinite(ob_distance[pivot]) and ob_distance[pivot] <= 1.0
                if not is_w:
                    continue
                result.at[bars.index[confirmation], "RSI_W_BOTTOM"] = True
                result.at[bars.index[confirmation], "RSI_BULL_DIVERGENCE"] = divergence
                result.at[bars.index[confirmation], "RSI_ORDER_BLOCK_CONFLUENCE"] = order_block_confluence
                stop = lows[pivot] - .5 * atr14[confirmation] if np.isfinite(atr14[confirmation]) else lows[pivot] * .99
                active_setup = {"expires": confirmation + 10, "first": first, "second": pivot, "neckline": neckline, "stop": stop, "divergence": divergence, "order_block": order_block_confluence}
                break
        if active_setup is not None:
            momentum_confirmed = False
            setup_rsi_limit = 65 if bool(active_setup["order_block"]) else 60
            if confirmation > int(active_setup["expires"]) or rsi[confirmation] > setup_rsi_limit:
                active_setup = None
            else:
                recent_cross = bool(crosses[max(0, confirmation - 4) : confirmation + 1].any())
                rsi_above_signal = rsi[confirmation] > rsi_signal[confirmation]
                divergence_confirmation = bool(active_setup["divergence"]) and rsi_above_signal and rsi_signal[confirmation] > rsi_signal[confirmation - 1]
                rsi_confirmation_limit = 65 if bool(active_setup["order_block"]) else 55
                momentum_confirmed = rsi[confirmation] < rsi_confirmation_limit and rsi_above_signal and (recent_cross or divergence_confirmation)
            if active_setup is not None and momentum_confirmed:
                first = int(active_setup["first"]); second = int(active_setup["second"])
                result.at[bars.index[confirmation], "RSI_ENHANCED_BUY"] = True
                result.at[bars.index[confirmation], "RSI_BULL_DIVERGENCE"] = bool(active_setup["divergence"])
                result.at[bars.index[confirmation], "RSI_ORDER_BLOCK_CONFLUENCE"] = bool(active_setup["order_block"])
                result.at[bars.index[confirmation], "RSI_FIRST_LOW_PRICE"] = lows[first]
                result.at[bars.index[confirmation], "RSI_SECOND_LOW_PRICE"] = lows[second]
                result.at[bars.index[confirmation], "RSI_FIRST_LOW_VALUE"] = rsi[first]
                result.at[bars.index[confirmation], "RSI_SECOND_LOW_VALUE"] = rsi[second]
                result.at[bars.index[confirmation], "RSI_NECKLINE"] = float(active_setup["neckline"])
                result.at[bars.index[confirmation], "RSI_STOP_LEVEL"] = float(active_setup["stop"])
                breakout_setup = {**active_setup, "created": confirmation, "expires": confirmation + 20}
                active_setup = None
        current_setup = breakout_setup or active_setup
        if current_setup is not None and not bool(result.at[bars.index[confirmation], "RSI_V_BOTTOM_BUY"]):
            result.at[bars.index[confirmation], "RSI_NECKLINE"] = float(current_setup["neckline"])
            result.at[bars.index[confirmation], "RSI_STOP_LEVEL"] = float(current_setup["stop"])
        if breakout_setup is not None:
            neckline = float(breakout_setup["neckline"]); second = int(breakout_setup["second"])
            prior_volume = float(np.nanmean(volumes[max(0, confirmation - 20) : confirmation])) if confirmation else np.nan
            volume_ratio = volumes[confirmation] / prior_volume if np.isfinite(prior_volume) and prior_volume > 0 else 0.0
            crossed_neckline = closes[confirmation] >= neckline * 1.005 and (closes[confirmation - 1] < neckline * 1.005 or confirmation == int(breakout_setup["created"]))
            candle_gain = closes[confirmation] / opens[confirmation] - 1.0 if opens[confirmation] else np.inf
            ema_distance = closes[confirmation] / ema12[confirmation] - 1.0 if ema12[confirmation] else np.inf
            risk = (closes[confirmation] - float(breakout_setup["stop"])) / closes[confirmation] if closes[confirmation] else np.inf
            long_emas = (ema144[confirmation], ema169[confirmation], ema576[confirmation], ema676[confirmation])
            overhead_emas = [value for value in long_emas if np.isfinite(value) and value > closes[confirmation]]
            overhead_room = min(overhead_emas) / closes[confirmation] - 1.0 if overhead_emas else np.inf
            overhead_clear = not overhead_emas or overhead_room >= .05
            quality_filter = (bool(breakout_setup["divergence"]) or bool(breakout_setup["order_block"])) and rsi[confirmation] <= 65 and candle_gain <= .04 and ema_distance <= .05 and risk <= .07 and overhead_clear
            if confirmation > int(breakout_setup["expires"]) or closes[confirmation] < lows[second]:
                breakout_setup = None
            elif crossed_neckline and quality_filter:
                result.at[bars.index[confirmation], "RSI_BREAKOUT_BUY"] = True
                result.at[bars.index[confirmation], "RSI_BREAKOUT_VOLUME_RATIO"] = volume_ratio
                first = int(breakout_setup["first"])
                result.at[bars.index[confirmation], "RSI_BULL_DIVERGENCE"] = bool(breakout_setup["divergence"])
                result.at[bars.index[confirmation], "RSI_ORDER_BLOCK_CONFLUENCE"] = bool(breakout_setup["order_block"])
                result.at[bars.index[confirmation], "RSI_FIRST_LOW_PRICE"] = lows[first]
                result.at[bars.index[confirmation], "RSI_SECOND_LOW_PRICE"] = lows[second]
                result.at[bars.index[confirmation], "RSI_FIRST_LOW_VALUE"] = rsi[first]
                result.at[bars.index[confirmation], "RSI_SECOND_LOW_VALUE"] = rsi[second]
                breakout_setup = None
    return result


def _result(fid: str, name: str, bars: pd.DataFrame, triggered: bool, details: dict[str, Any], tier: str | None = None) -> FactorResult:
    timestamp = bars.index[-1].isoformat() if len(bars) else None
    return FactorResult(fid, name, bool(triggered), timestamp, json_safe(details), tier)


def detect_f1(b: pd.DataFrame) -> FactorResult:
    if len(b) < 676: return _result("F1", "Vegas Alignment", b, False, {"reason": "insufficient_history", "required": 676, "actual": len(b)})
    r = b.iloc[-1]; close = float(r.Close)
    yl, yh = sorted((float(r.EMA144), float(r.EMA169))); gl, gh = sorted((float(r.EMA576), float(r.EMA676)))
    spread = (max(yh, gh) - min(yl, gl)) / close * 100
    overlap = max(yl, gl) <= min(yh, gh)
    distance = max(gl - yh, yl - gh, 0) / close * 100
    mode = tier = None
    if overlap and spread < .6: mode, tier = "full_overlap", "A"
    elif spread < 1.5: mode, tier = "tight_compression", "B"
    elif distance <= .8: mode, tier = "parallel_close", "C"
    elif overlap: mode, tier = "nested_interlaced", "A"
    details = {"mode": mode, "overall_spread_pct": spread, "distance_pct": distance, "overlap": overlap}
    if not tier: details["reason"] = "tunnels_not_aligned"
    return _result("F1", "Vegas Alignment", b, tier is not None, details, tier)


def detect_f2(b: pd.DataFrame) -> FactorResult:
    if len(b) < 676: return _result("F2", "EMA12 Lift-Off", b, False, {"reason": "insufficient_history", "required": 676, "actual": len(b)})
    lower = b[["EMA144", "EMA169", "EMA576", "EMA676"]].min(axis=1); upper = b[["EMA144", "EMA169", "EMA576", "EMA676"]].max(axis=1)
    ema = b.EMA12; close = b.Close
    distance = pd.Series(np.where(ema > upper, (ema-upper)/close, np.where(ema < lower, (lower-ema)/close, 0)), index=b.index)
    x = np.arange(5, dtype=float); coeff = np.polyfit(x, ema.iloc[-5:].to_numpy(float), 2); predicted = np.polyval(coeff, x)
    values = ema.iloc[-5:].to_numpy(float); ss_tot = float(((values-values.mean())**2).sum()); r2 = 1-float(((values-predicted)**2).sum())/ss_tot if ss_tot else 1.0
    conditions = {"recently_attached": bool((distance.iloc[-10:] <= .005).any()), "positive_curvature": bool(coeff[0] > 0), "above_tunnel": bool(ema.iloc[-1] > upper.iloc[-1]), "distance_increasing": bool(distance.iloc[-1] > distance.iloc[-4])}
    triggered = all(conditions.values())
    return _result("F2", "EMA12 Lift-Off", b, triggered, {**conditions, "curvature": float(coeff[0]), "r_squared": r2, "distance": float(distance.iloc[-1]), "reason": None if triggered else "conditions_not_met"})


def detect_f3(b: pd.DataFrame) -> FactorResult:
    windows = (60, 120, 180)
    if len(b) < windows[0]: return _result("F3", "Round Bottom", b, False, {"reason": "insufficient_history", "required": windows[0], "actual": len(b)})
    candidates=[]
    for window in windows:
        if len(b) < window: continue
        sample=b.iloc[-window:]; y=sample.Close.to_numpy(float); x=np.arange(window,dtype=float)
        a,slope,intercept=np.polyfit(x,y,2); fitted=np.polyval((a,slope,intercept),x)
        total=float(((y-y.mean())**2).sum()); r2=1-float(((y-fitted)**2).sum())/total if total else 0.0
        vertex=float(-slope/(2*a)) if a else float("inf")
        vertex_price=float(np.polyval((a,slope,intercept),vertex)) if np.isfinite(vertex) else float("nan")
        current_price=float(y[-1]); rise=(current_price/vertex_price-1.0) if np.isfinite(vertex_price) and vertex_price>0 else float("nan")
        bars_since_vertex=float(window-1-vertex) if np.isfinite(vertex) else float("nan")
        valid=bool(a>0 and r2>=.7 and .2*window<=vertex<=.8*window)
        vertex_position=int(np.clip(round(vertex),0,window-1)) if np.isfinite(vertex) else None
        candidates.append({
            "window":window,"curvature":float(a),"r_squared":r2,"vertex_x":vertex,
            "vertex_price":vertex_price,"current_price":current_price,
            "rise_from_vertex_pct":rise*100,"bars_since_vertex":bars_since_vertex,
            "vertex_timestamp":sample.index[vertex_position].isoformat() if vertex_position is not None else None,
            "window_start_timestamp":sample.index[0].isoformat(),"valid":valid,
        })
    valid_candidates=[candidate for candidate in candidates if candidate["valid"]]
    selected=max(valid_candidates or candidates,key=lambda candidate:candidate["r_squared"])
    return _result("F3", "Round Bottom", b, bool(valid_candidates), {
        **{key:value for key,value in selected.items() if key!="valid"},
        "candidate_windows":candidates,
        "reason":None if valid_candidates else "fit_requirements_not_met",
    })


def _pivots(values: np.ndarray, radius: int, high: bool) -> list[int]:
    found = []
    for i in range(radius, len(values)-radius):
        neighbors = np.r_[values[i-radius:i], values[i+1:i+radius+1]]
        if (values[i] > neighbors).all() if high else (values[i] < neighbors).all(): found.append(i)
    return found


def detect_f4(b: pd.DataFrame) -> FactorResult:
    if len(b) < 30: return _result("F4", "Triangle Consolidation", b, False, {"reason": "insufficient_history", "required": 30, "actual": len(b)})
    w=b.iloc[-30:]; highs=w.High.to_numpy(float); lows=w.Low.to_numpy(float); hp=_pivots(highs,3,True); lp=_pivots(lows,3,False)
    if len(hp)<2 or len(lp)<2: return _result("F4", "Triangle Consolidation", b, False, {"reason":"insufficient_pivots","high_pivots":len(hp),"low_pivots":len(lp)})
    sh, ih=np.polyfit(hp,highs[hp],1); sl, il=np.polyfit(lp,lows[lp],1); eps=.01; kind=None
    if sh < -eps and sl > eps: kind="symmetric"
    elif abs(sh)<eps and sl>eps: kind="ascending"
    elif sh < -eps and abs(sl)<eps: kind="descending"
    start=min(hp+lp); end=max(hp+lp); start_range=max((sh*start+ih)-(sl*start+il),0); end_range=max((sh*end+ih)-(sl*end+il),0); ratio=end_range/start_range if start_range else float("inf")
    triggered=kind is not None and ratio<.6
    return _result("F4","Triangle Consolidation",b,triggered,{"triangle_type":kind,"slope_high":float(sh),"slope_low":float(sl),"contraction_ratio":float(ratio),"reason":None if triggered else "triangle_requirements_not_met"})


def detect_f5(b: pd.DataFrame) -> FactorResult:
    if len(b)<21: return _result("F5","Big Bullish Candle",b,False,{"reason":"insufficient_history","required":21,"actual":len(b)})
    r=b.iloc[-1]; body=float(r.Close-r.Open); avg=float((b.Close-b.Open).abs().iloc[-21:-1].mean()); gain=body/float(r.Open); ratio=body/avg if avg else float("inf"); clearance=float(r.Close)>max(float(r.EMA12),float(r.EMA144),float(r.EMA169))*1.015
    long_tunnel_top=max(float(r.EMA144),float(r.EMA169),float(r.EMA576),float(r.EMA676))
    price_above_tunnel=(float(r.Close)/long_tunnel_top-1.0) if long_tunnel_top>0 else float("nan")
    ema12_above_tunnel=(float(r.EMA12)/long_tunnel_top-1.0) if long_tunnel_top>0 else float("nan")
    triggered=float(r.Close)>float(r.Open) and gain>.025 and ratio>1.5 and clearance
    return _result("F5","Big Bullish Candle",b,triggered,{"gain_pct":gain,"body_ratio":ratio,"ema_clearance":clearance,"price_above_long_tunnel_pct":price_above_tunnel*100,"ema12_above_long_tunnel_pct":ema12_above_tunnel*100,"reason":None if triggered else "candle_requirements_not_met"})


def detect_f6(b: pd.DataFrame) -> FactorResult:
    if len(b)<21: return _result("F6","Volume Surge",b,False,{"reason":"insufficient_history","required":21,"actual":len(b)})
    avg=float(b.Volume.iloc[-21:-1].mean()); ratio=float(b.Volume.iloc[-1])/avg if avg else 0.; triggered=ratio>1.5
    return _result("F6","Volume Surge",b,triggered,{"actual_ratio":ratio,"reason":None if triggered else "volume_ratio_not_met"})


def detect_f7(b: pd.DataFrame) -> FactorResult:
    """Detect a textbook cup-and-handle structure; observational only."""
    if len(b)<60:return _result("F7","Cup and Handle",b,False,{"reason":"insufficient_history","required":60,"actual":len(b)})
    candidates=[]
    for window in (60,120,180):
        if len(b)<window:continue
        whole=b.iloc[-window:]
        for handle_length in (5,10,15,20,25,30):
            if handle_length>=window*.3:continue
            cup=whole.iloc[:-handle_length]; handle=whole.iloc[-handle_length:]
            close=cup.Close.to_numpy(float); n=len(cup)
            left_end=max(3,int(n*.25)); right_start=min(n-2,int(n*.75))
            left_idx=int(np.argmax(close[:left_end])); right_idx=right_start+int(np.argmax(close[right_start:]))
            cup_span=right_idx-left_idx
            if cup_span<30:continue
            segment=close[left_idx:right_idx+1]; bottom_rel=int(np.argmin(segment)); bottom_idx=left_idx+bottom_rel
            if not left_idx+.2*cup_span<=bottom_idx<=left_idx+.8*cup_span:continue
            left_rim=float(close[left_idx]);right_rim=float(close[right_idx]);rim=(left_rim+right_rim)/2;bottom=float(close[bottom_idx])
            if rim<=0:continue
            depth=(rim-bottom)/rim;rim_diff=abs(left_rim-right_rim)/rim;recovery=right_rim/left_rim if left_rim else 0
            x=np.arange(len(segment),dtype=float);a,slope,intercept=np.polyfit(x,segment,2);fit=np.polyval((a,slope,intercept),x)
            total=float(((segment-segment.mean())**2).sum());r2=1-float(((segment-fit)**2).sum())/total if total else 0
            left_duration=bottom_idx-left_idx;right_duration=right_idx-bottom_idx
            symmetry=min(left_duration,right_duration)/max(left_duration,right_duration) if max(left_duration,right_duration) else 0
            bottom_threshold=bottom+(rim-bottom)*.08
            bottom_dwell=int((segment<=bottom_threshold).sum());minimum_dwell=max(3,int(cup_span*.10))
            handle_low=float(handle.Low.min());handle_drawdown=max((right_rim-handle_low)/right_rim,0) if right_rim else 1
            consolidation=handle.iloc[:-1] if len(handle)>5 else handle
            pre_handle_volume=float(cup.Volume.iloc[-min(20,len(cup)):].mean());handle_volume=float(consolidation.Volume.mean())
            handle_volume_ratio=handle_volume/pre_handle_volume if pre_handle_volume else float("inf")
            cup_valid=a>0 and r2>=.55 and .12<=depth<=.33 and rim_diff<=.10 and .90<=recovery<=1.05 and symmetry>=.5 and bottom_dwell>=minimum_dwell
            handle_valid=handle_drawdown<=min(.15,depth/3) and float(handle.Close.min())>bottom+(rim-bottom)*.5 and handle_volume_ratio<=.85
            if not cup_valid:continue
            handle_for_line=handle.iloc[:-1] if len(handle)>2 else handle
            hx=np.arange(len(handle_for_line),dtype=float);handle_slope,handle_intercept=np.polyfit(hx,handle_for_line.High.to_numpy(float),1)
            handle_resistance=float(handle_slope*(len(handle)-1)+handle_intercept)
            neckline=max(left_rim,right_rim);breakout_level=max(neckline,handle_resistance)
            current=float(whole.Close.iloc[-1]);prior_volume=float(whole.Volume.iloc[-21:-1].mean()) if len(whole)>=21 else 0
            volume_ratio=float(whole.Volume.iloc[-1])/prior_volume if prior_volume else 0
            if handle_valid and current>=breakout_level*1.01 and volume_ratio>=1.5:stage="breakout_confirmed"
            elif handle_valid and current>=breakout_level*.97:stage="breakout_ready"
            elif handle_valid:stage="handle_forming"
            else:stage="cup_complete"
            confidence=min(100,max(0,20*min(r2/.8,1)+15*max(0,1-rim_diff/.10)+10*symmetry+10*min(bottom_dwell/max(minimum_dwell,1),1)+10*(1 if .12<=depth<=.33 else 0)+15*(1 if handle_valid else 0)+10*min(max(1-handle_volume_ratio,0)/.3,1)+10*min(volume_ratio/1.5,1)))
            candidates.append({"window":window,"stage":stage,"confidence":confidence,"cup_r_squared":r2,"cup_depth_pct":depth*100,"rim_difference_pct":rim_diff*100,"cup_symmetry":symmetry,"bottom_dwell_bars":bottom_dwell,"left_rim_price":left_rim,"right_rim_price":right_rim,"bottom_price":bottom,"neckline_price":neckline,"handle_resistance":handle_resistance,"breakout_level":breakout_level,"handle_length":handle_length,"handle_drawdown_pct":handle_drawdown*100,"handle_volume_ratio":handle_volume_ratio,"breakout_volume_ratio":volume_ratio,"left_rim_timestamp":cup.index[left_idx].isoformat(),"bottom_timestamp":cup.index[bottom_idx].isoformat(),"right_rim_timestamp":cup.index[right_idx].isoformat()})
    if not candidates:return _result("F7","Cup and Handle",b,False,{"reason":"cup_requirements_not_met"})
    stage_rank={"cup_complete":1,"handle_forming":2,"breakout_ready":3,"breakout_confirmed":4}
    selected=max(candidates,key=lambda c:(stage_rank[c["stage"]],c["confidence"]))
    return _result("F7","Cup and Handle",b,selected["confidence"]>=60,{**selected,"candidate_count":len(candidates),"scored":False,"reason":None if selected["confidence"]>=60 else "confidence_not_met"})


def detect_f8(b: pd.DataFrame) -> FactorResult:
    """Observe confirmed price W-bottoms with RSI momentum confirmation."""
    if len(b) < 30 or "RSI_ENHANCED_BUY" not in b:
        return _result("F8", "RSI W-Bottom", b, False, {"reason": "insufficient_history", "required": 30, "actual": len(b), "scored": False})
    recent = b.iloc[-3:]
    signals = recent[recent["RSI_BREAKOUT_BUY"] | recent["RSI_V_BOTTOM_BUY"]]
    if signals.empty:
        return _result("F8", "RSI W-Bottom", b, False, {"reason": "no_recent_confirmed_signal", "scored": False})
    timestamp = signals.index[-1]
    row = signals.iloc[-1]
    details = {
        "signal_type": "v_bottom" if bool(row["RSI_V_BOTTOM_BUY"]) else "w_bottom",
        "detected_timestamp": timestamp.isoformat(),
        "bars_ago": len(b) - 1 - int(b.index.get_loc(timestamp)),
        "first_low_price": float(row["RSI_FIRST_LOW_PRICE"]),
        "second_low_price": float(row["RSI_SECOND_LOW_PRICE"]),
        "first_low_rsi": float(row["RSI_FIRST_LOW_VALUE"]),
        "second_low_rsi": float(row["RSI_SECOND_LOW_VALUE"]),
        "neckline_price": float(row["RSI_NECKLINE"]),
        "stop_level": float(row["RSI_STOP_LEVEL"]),
        "breakout_volume_ratio": float(row["RSI_BREAKOUT_VOLUME_RATIO"]),
        "bullish_divergence": bool(row["RSI_BULL_DIVERGENCE"]),
        "order_block_confluence": bool(row["RSI_ORDER_BLOCK_CONFLUENCE"]),
        "scored": False,
    }
    return _result("F8", "RSI W-Bottom", b, True, details)


DETECTORS=(detect_f1,detect_f2,detect_f3,detect_f4,detect_f5,detect_f6,detect_f7,detect_f8)


def scan_symbol(symbol: str, timeframe_bars: dict[str,pd.DataFrame]) -> dict[str,Any]:
    tf_results={}
    for timeframe,bars in timeframe_bars.items():
        enriched=add_indicators(bars); factors={r.factor_id:r for r in (fn(enriched) for fn in DETECTORS)}
        for observed in observe_classic_patterns(enriched):
            result=_result(observed["pattern_id"],observed["signal_name"],enriched,observed["triggered"],observed["details"])
            factors[result.factor_id]=result
        tf_results[timeframe]={"factors":factors,"triggered":{fid for fid,r in factors.items() if r.triggered},"bar_timestamp":enriched.index[-1].isoformat()}
    periods_by_factor={fid:frozenset(tf for tf,v in tf_results.items() if fid in v["triggered"]) for fid in SCORING_FACTOR_IDS}
    contributions={}; base_total=0.
    for fid,periods in periods_by_factor.items():
        if not periods: continue
        if fid=="F1":
            tiers=[tf_results[tf]["factors"][fid].tier for tf in periods]; base=max(F1_SCORES[t] for t in tiers if t)
        else: base=BASE_SCORES[fid]
        multiplier=TIMEFRAME_MULTIPLIERS[periods]; contributions[fid]={"base_score":base,"multiplier":multiplier,"score":base*multiplier}; base_total+=base*multiplier
    scoring_triggered={tf:v["triggered"]&set(SCORING_FACTOR_IDS) for tf,v in tf_results.items()}
    confluence={tf:_confluence(scoring_triggered[tf],v["factors"].get("F1")) for tf,v in tf_results.items()}; confluence_total=sum(x["score"] for x in confluence.values()); all_factors=set().union(*scoring_triggered.values()); coverage=10. if all_factors==set(SCORING_FACTOR_IDS) else 1.; pre=base_total+confluence_total
    return {"symbol":symbol,"timeframes":{tf:{"bar_timestamp":v["bar_timestamp"],"factors":{k:asdict(x) for k,x in v["factors"].items()}} for tf,v in tf_results.items()},"scoring":{"contributions":contributions,"confluence":confluence,"base_total":base_total,"confluence_total":confluence_total,"pre_multiplier_score":pre,"coverage_multiplier":coverage,"total_score":pre*coverage},"triggered_factors":sorted(all_factors)}


def _confluence(factors:set[str], f1:FactorResult|None)->dict[str,Any]:
    tier=f1.tier if f1 and f1.triggered else None; candidates=[]
    rules=[(set("F1 F2 F3 F4 F5 F6".split()),None,"S",30),(set("F3 F4 F5 F6".split()),None,"A+",24),(set("F3 F5 F6".split()),None,"A",22),(set("F1 F2 F3 F4 F5".split()),"A","A",22),(set("F1 F2 F3 F4 F5".split()),"B","B+",18),(set("F1 F2 F3 F4 F5".split()),"C","B",15),(set("F1 F2 F3 F4".split()),"A","C+",12),(set("F1 F2 F3 F4".split()),"B","C",10),(set("F1 F2 F3 F4".split()),"C","C-",8),(set("F1 F2".split()),None,"Early",3)]
    for required,needed,label,score in rules:
        if required<=factors and (needed is None or tier==needed): candidates.append((score,label))
    score,label=max(candidates,default=(0,None)); return {"level":label,"score":score}
