from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from app.json_utils import json_safe


@dataclass
class FactorResult:
    factor_id: str
    signal_name: str
    triggered: bool
    timestamp: str | None
    details: dict[str, Any]
    tier: str | None = None


BASE_SCORES = {"F2": 3, "F3": 4, "F4": 1, "F5": 4, "F6": 1}
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
    ema26 = bars["Close"].ewm(span=26, adjust=False).mean()
    bars["MACD_DIF"] = bars["EMA12"] - ema26
    bars["MACD_DEA"] = bars["MACD_DIF"].ewm(span=9, adjust=False).mean()
    bars["MACD_HIST"] = 2 * (bars["MACD_DIF"] - bars["MACD_DEA"])
    return bars


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
    if len(b) < 60: return _result("F3", "Round Bottom", b, False, {"reason": "insufficient_history", "required": 60, "actual": len(b)})
    y = b.Close.iloc[-60:].to_numpy(float); x = np.arange(60, dtype=float); a, slope, intercept = np.polyfit(x, y, 2); fitted = np.polyval((a, slope, intercept), x)
    total = float(((y-y.mean())**2).sum()); r2 = 1-float(((y-fitted)**2).sum())/total if total else 0.0
    vertex = float(-slope/(2*a)) if a else float("inf")
    vertex_price = float(np.polyval((a, slope, intercept), vertex)) if np.isfinite(vertex) else float("nan")
    current_price = float(y[-1])
    rise_from_vertex = (current_price / vertex_price - 1.0) if np.isfinite(vertex_price) and vertex_price > 0 else float("nan")
    bars_since_vertex = float(59 - vertex) if np.isfinite(vertex) else float("nan")
    triggered = a > 0 and r2 >= .7 and 12 <= vertex <= 48
    return _result("F3", "Round Bottom", b, triggered, {
        "curvature": float(a), "r_squared": r2, "vertex_x": vertex,
        "vertex_price": vertex_price, "current_price": current_price,
        "rise_from_vertex_pct": rise_from_vertex * 100,
        "bars_since_vertex": bars_since_vertex, "window": 60,
        "reason": None if triggered else "fit_requirements_not_met",
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


DETECTORS=(detect_f1,detect_f2,detect_f3,detect_f4,detect_f5,detect_f6)


def scan_symbol(symbol: str, timeframe_bars: dict[str,pd.DataFrame]) -> dict[str,Any]:
    tf_results={}
    for timeframe,bars in timeframe_bars.items():
        enriched=add_indicators(bars); factors={r.factor_id:r for r in (fn(enriched) for fn in DETECTORS)}
        tf_results[timeframe]={"factors":factors,"triggered":{fid for fid,r in factors.items() if r.triggered},"bar_timestamp":enriched.index[-1].isoformat()}
    periods_by_factor={fid:frozenset(tf for tf,v in tf_results.items() if fid in v["triggered"]) for fid in (f"F{i}" for i in range(1,7))}
    contributions={}; base_total=0.
    for fid,periods in periods_by_factor.items():
        if not periods: continue
        if fid=="F1":
            tiers=[tf_results[tf]["factors"][fid].tier for tf in periods]; base=max(F1_SCORES[t] for t in tiers if t)
        else: base=BASE_SCORES[fid]
        multiplier=TIMEFRAME_MULTIPLIERS[periods]; contributions[fid]={"base_score":base,"multiplier":multiplier,"score":base*multiplier}; base_total+=base*multiplier
    confluence={tf:_confluence(v["triggered"],v["factors"].get("F1")) for tf,v in tf_results.items()}; confluence_total=sum(x["score"] for x in confluence.values()); all_factors=set().union(*(v["triggered"] for v in tf_results.values())); coverage=10. if all_factors=={f"F{i}" for i in range(1,7)} else 1.; pre=base_total+confluence_total
    return {"symbol":symbol,"timeframes":{tf:{"bar_timestamp":v["bar_timestamp"],"factors":{k:asdict(x) for k,x in v["factors"].items()}} for tf,v in tf_results.items()},"scoring":{"contributions":contributions,"confluence":confluence,"base_total":base_total,"confluence_total":confluence_total,"pre_multiplier_score":pre,"coverage_multiplier":coverage,"total_score":pre*coverage},"triggered_factors":sorted(all_factors)}


def _confluence(factors:set[str], f1:FactorResult|None)->dict[str,Any]:
    tier=f1.tier if f1 and f1.triggered else None; candidates=[]
    rules=[(set("F1 F2 F3 F4 F5 F6".split()),None,"S",30),(set("F3 F4 F5 F6".split()),None,"A+",24),(set("F3 F5 F6".split()),None,"A",22),(set("F1 F2 F3 F4 F5".split()),"A","A",22),(set("F1 F2 F3 F4 F5".split()),"B","B+",18),(set("F1 F2 F3 F4 F5".split()),"C","B",15),(set("F1 F2 F3 F4".split()),"A","C+",12),(set("F1 F2 F3 F4".split()),"B","C",10),(set("F1 F2 F3 F4".split()),"C","C-",8),(set("F1 F2".split()),None,"Early",3)]
    for required,needed,label,score in rules:
        if required<=factors and (needed is None or tier==needed): candidates.append((score,label))
    score,label=max(candidates,default=(0,None)); return {"level":label,"score":score}
