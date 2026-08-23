import numpy as np
import pandas as pd
from app.scanner_engine import scan_symbol

def bars(count: int = 700) -> pd.DataFrame:
    x=np.arange(count,dtype=float); close=100+0.0005*(x-count/2)**2
    index=pd.date_range("2020-01-01",periods=count,freq="D",tz="UTC")
    return pd.DataFrame({"Open":close-.2,"High":close+.5,"Low":close-.5,"Close":close,"Volume":np.full(count,1000.)},index=index)

def test_scan_returns_all_timeframes_and_factors():
    frame=bars(); result=scan_symbol("TEST.US",{"weekly":frame,"daily":frame,"4hour":frame})
    assert set(result["timeframes"])=={"weekly","daily","4hour"}
    assert set(result["timeframes"]["daily"]["factors"])=={f"F{i}" for i in range(1,7)}
    assert result["scoring"]["total_score"]>=0
