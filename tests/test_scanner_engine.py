import numpy as np
import pandas as pd
from app.scanner_engine import detect_f3, detect_f7, scan_symbol

def bars(count: int = 700) -> pd.DataFrame:
    x=np.arange(count,dtype=float); close=100+0.0005*(x-count/2)**2
    index=pd.date_range("2020-01-01",periods=count,freq="D",tz="UTC")
    return pd.DataFrame({"Open":close-.2,"High":close+.5,"Low":close-.5,"Close":close,"Volume":np.full(count,1000.)},index=index)

def test_scan_returns_all_timeframes_and_factors():
    frame=bars(); result=scan_symbol("TEST.US",{"weekly":frame,"daily":frame,"4hour":frame})
    assert set(result["timeframes"])=={"weekly","daily","4hour"}
    assert set(result["timeframes"]["daily"]["factors"])=={f"F{i}" for i in range(1,8)}|{f"P{i}" for i in range(1,7)}
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
