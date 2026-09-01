import numpy as np
import pandas as pd
from app.scanner_engine import _rsi_bottom_structure, add_indicators, detect_f3, detect_f7, detect_f9, scan_symbol

def bars(count: int = 700) -> pd.DataFrame:
    x=np.arange(count,dtype=float); close=100+0.0005*(x-count/2)**2
    index=pd.date_range("2020-01-01",periods=count,freq="D",tz="UTC")
    return pd.DataFrame({"Open":close-.2,"High":close+.5,"Low":close-.5,"Close":close,"Volume":np.full(count,1000.)},index=index)


def test_rsi_indicator_is_bounded_and_uses_a_ten_bar_signal_line():
    frame=bars(80)
    enriched=add_indicators(frame)
    valid=enriched["RSI10"].dropna()
    assert valid.between(0,100).all()
    assert enriched["RSI10"].first_valid_index()==frame.index[10]
    assert enriched["RSI_SIGNAL10"].first_valid_index()==frame.index[19]
    assert enriched["RSI_ENHANCED_BUY"].dtype==bool

def test_macd_xd_matches_ema_formulas_and_exposes_crosses():
    frame=bars(220);enriched=add_indicators(frame);close=frame.Close
    expected=close.ewm(span=12,adjust=False).mean()-close.ewm(span=26,adjust=False).mean()
    expected_signal=expected.ewm(span=9,adjust=False).mean()
    assert np.allclose(enriched.MACD_XD,expected)
    assert np.allclose(enriched.MACD_XD_SIGNAL,expected_signal)
    assert np.allclose(enriched.MACD_XD_HIST,expected-expected_signal)
    assert {"MACD_XD_AREA","MACD_XD_BULL_DIVERGENCE","MACD_XD_BEAR_DIVERGENCE"} <= set(enriched.columns)
    assert "MACD_XD_TREND" not in enriched.columns

def test_macd_xd_does_not_keep_a_provisional_divergence_in_a_growing_segment():
    first=np.r_[np.linspace(100,110,20),np.linspace(110,105,8)]
    stronger=np.linspace(105,125,24)
    close=np.r_[first,stronger]
    frame=pd.DataFrame({"Open":close-.2,"High":close+.5,"Low":close-.5,"Close":close,"Volume":1000.},index=pd.date_range("2026-01-01",periods=len(close),freq="4h",tz="UTC"))
    enriched=add_indicators(frame)
    assert not enriched.iloc[-10:].MACD_XD_BEAR_DIVERGENCE.any()


def test_rsi_bottom_structure_confirms_w_and_divergence_at_confirmation_bar():
    count=30; index=pd.date_range("2026-01-01",periods=count,freq="D",tz="UTC")
    low=np.full(count,98.); high=np.full(count,100.); rsi=np.full(count,40.)
    low[8]=96.; low[18]=95.5; rsi[8]=25.; rsi[18]=30.
    close=np.full(count,98.); volume=np.full(count,1000.); close[21]=100.5; volume[21]=2000.
    frame=pd.DataFrame({"Open":close-.5,"Low":low,"High":high,"Close":close,"Volume":volume,"EMA12":np.full(count,99.),"RSI10":rsi,"RSI_BULL_CROSS":False},index=index)
    frame.loc[index[21],"RSI_BULL_CROSS"]=True
    result=_rsi_bottom_structure(frame)
    assert result.loc[index[21],"RSI_W_BOTTOM"]
    assert result.loc[index[21],"RSI_BULL_DIVERGENCE"]
    assert result.loc[index[21],"RSI_ENHANCED_BUY"]
    assert result.loc[index[21],"RSI_BREAKOUT_BUY"]
    assert result.loc[index[21],"RSI_BREAKOUT_VOLUME_RATIO"]==2.0
    assert not result.iloc[:21]["RSI_W_BOTTOM"].any()


def test_rsi_bottom_structure_accepts_market_cache_lowercase_columns():
    count=30; index=pd.date_range("2026-01-01",periods=count,freq="D",tz="UTC")
    low=np.full(count,100.); high=np.full(count,102.); rsi=np.full(count,40.)
    low[8]=80.; low[18]=79.; rsi[8]=25.; rsi[18]=30.
    close=np.full(count,90.); volume=np.full(count,1000.); close[21]=103.; volume[21]=2000.
    frame=pd.DataFrame({"low":low,"high":high,"Close":close,"volume":volume,"RSI10":rsi,"RSI_BULL_CROSS":False},index=index)
    frame.loc[index[21],"RSI_BULL_CROSS"]=True
    assert _rsi_bottom_structure(frame).loc[index[21],"RSI_ENHANCED_BUY"]

def test_rsi_bottom_structure_confirms_order_block_v_reversal_without_a_w_bottom():
    count=22; index=pd.date_range("2026-01-01",periods=count,freq="D",tz="UTC")
    close=np.full(count,101.); low=np.full(count,100.); high=np.full(count,102.); rsi=np.full(count,40.)
    close[10]=91.; low[10]=90.; high[10]=92.; rsi[10]=24.
    close[11:14]=[92.,92.5,93.]; low[11:14]=[91.,91.5,92.]; high[11:14]=[92.5,93.,93.5]; rsi[11:14]=[34.,38.,42.]
    close[14]=94.; low[14]=92.8; high[14]=94.5; rsi[14]=46.
    frame=pd.DataFrame({"Open":close-.5,"Low":low,"High":high,"Close":close,"EMA12":np.full(count,93.),"BULLISH_OB_DISTANCE_PCT":np.full(count,np.nan),"RSI10":rsi,"RSI_BULL_CROSS":False},index=index)
    frame.loc[index[10],"BULLISH_OB_DISTANCE_PCT"]=.5
    frame.loc[index[11],"RSI_BULL_CROSS"]=True
    below_falling_trend=frame.copy(); below_falling_trend["EMA12"]=100.
    assert not _rsi_bottom_structure(below_falling_trend)["RSI_V_BOTTOM_BUY"].any()
    result=_rsi_bottom_structure(frame)
    assert result.loc[index[13],"RSI_V_BOTTOM_BUY"]
    assert result.loc[index[13],"RSI_ORDER_BLOCK_CONFLUENCE"]
    assert not result["RSI_BREAKOUT_BUY"].any()

def test_rsi_bottom_structure_confirms_extreme_oversold_v_reversal_without_order_block():
    count=20; index=pd.date_range("2026-01-01",periods=count,freq="D",tz="UTC")
    close=np.full(count,100.); low=np.full(count,99.); high=np.full(count,101.); rsi=np.full(count,40.)
    rsi[3:10]=[30.,28.,26.,24.,22.,21.,20.]
    close[10]=91.; low[10]=90.; high[10]=92.; rsi[10]=19.
    close[11]=92.5; low[11]=91.; high[11]=93.; rsi[11]=30.
    frame=pd.DataFrame({"Open":close-.4,"Low":low,"High":high,"Close":close,"EMA12":np.full(count,93.),"BULLISH_OB_DISTANCE_PCT":np.full(count,np.nan),"RSI10":rsi,"RSI_BULL_CROSS":False},index=index)
    frame.loc[index[11],"RSI_BULL_CROSS"]=True
    result=_rsi_bottom_structure(frame)
    assert result.loc[index[11],"RSI_V_BOTTOM_BUY"]
    assert not result.loc[index[11],"RSI_ORDER_BLOCK_CONFLUENCE"]

def test_scan_returns_all_timeframes_and_factors():
    frame=bars(); result=scan_symbol("TEST.US",{"weekly":frame,"daily":frame,"4hour":frame})
    assert set(result["timeframes"])=={"weekly","daily","4hour"}
    assert set(result["timeframes"]["daily"]["factors"])=={f"F{i}" for i in range(1,10)}|{f"P{i}" for i in range(1,7)}
    assert not (set(result["triggered_factors"])&{f"P{i}" for i in range(1,7)})
    assert result["scoring"]["total_score"]>=0

def test_f3_finds_a_large_round_bottom_outside_the_short_window():
    count=180; x=np.arange(count,dtype=float); close=20+0.001*(x-65)**2
    frame=pd.DataFrame({"Close":close},index=pd.date_range("2026-01-01",periods=count,freq="4h",tz="UTC"))
    result=detect_f3(frame)
    assert result.triggered is True
    assert result.details["window"]==180
    assert result.details["r_squared"]>.99
    assert 64 <= result.details["vertex_x"] <= 66

def test_f3_detects_smooth_ema12_arc_despite_noisy_closes():
    count=120; x=np.arange(count,dtype=float)
    ema12=80*np.exp(.00008*(x-58)**2)
    close=100+8*np.sin(x*1.7)+5*np.sin(x*.43)
    frame=pd.DataFrame({"Close":close,"EMA12":ema12},index=pd.date_range("2026-01-01",periods=count,freq="4h",tz="UTC"))
    result=detect_f3(frame)
    assert result.triggered is True
    assert result.details["source"]=="ema12"
    assert result.details["ema12_arc"] is True
    assert result.details["close_arc"] is False
    assert result.details["stage"] in {"confirmed","breakout","extended"}

def test_f7_detects_cup_handle_without_entering_scoring_factors():
    cup_x=np.linspace(-1,1,90);cup=80+20*cup_x**2;handle=np.linspace(99,96,8).tolist()+[97,99,102]
    close=np.r_[np.full(20,100.),cup,handle];volume=np.full(len(close),1000.);volume[-len(handle):]=700.;volume[-1]=2200.
    frame=pd.DataFrame({"Open":close-.2,"High":close+.5,"Low":close-.5,"Close":close,"Volume":volume},index=pd.date_range("2026-01-01",periods=len(close),freq="4h",tz="UTC"))
    factor=detect_f7(frame)
    assert factor.triggered is True
    assert factor.details["stage"]=="breakout_confirmed"
    assert factor.details["cup_depth_pct"]>=12
    assert factor.details["handle_volume_ratio"]<=.85
    scanned=scan_symbol("TEST.US",{"4hour":frame})
    assert "F7" not in scanned["triggered_factors"]

def test_f7_rejects_a_sharp_v_bottom():
    cup=np.r_[np.linspace(100,80,45),np.linspace(80,100,45)];handle=np.r_[np.linspace(99,96,8),97,99,102]
    close=np.r_[np.full(20,100.),cup,handle];volume=np.full(len(close),700.);volume[:-len(handle)]=1000.;volume[-1]=2200.
    frame=pd.DataFrame({"Open":close-.2,"High":close+.5,"Low":close-.5,"Close":close,"Volume":volume},index=pd.date_range("2026-01-01",periods=len(close),freq="4h",tz="UTC"))
    assert detect_f7(frame).triggered is False

def test_f9_marks_a_flat_base_breakout_with_volume_as_buy_candidate():
    close=np.r_[np.linspace(80,100,20),100+np.sin(np.arange(20))*.8,103.]
    volume=np.r_[np.full(40,1000.),2000.]
    frame=pd.DataFrame({"Open":close-.3,"High":close+.4,"Low":close-.5,"Close":close,"Volume":volume},index=pd.date_range("2026-01-01",periods=len(close),freq="D",tz="UTC"))
    result=detect_f9(frame)
    assert result.triggered is True
    assert result.details["base_type"]=="flat_base"
    assert result.details["stage"]=="breakout_confirmed"
    assert result.details["buy_candidate"] is True
