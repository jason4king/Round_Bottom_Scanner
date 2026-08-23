import numpy as np
import pandas as pd
from trendline_indicator import add_trendline_channels

def test_trendline_indicator_preserves_rows_and_adds_finite_lines():
    x=np.arange(80,dtype=float)
    frame=pd.DataFrame({"high":100+np.sin(x/4)*5+x*.05,"low":90+np.sin(x/4)*5+x*.05})
    result=add_trendline_channels(frame)
    assert len(result)==len(frame)
    assert result["trend_support"].notna().sum() >= 2
    assert result["trend_resistance"].notna().sum() >= 2
    assert result["trend_support"].first_valid_index() > 0
    assert result["trend_resistance"].first_valid_index() > 0
