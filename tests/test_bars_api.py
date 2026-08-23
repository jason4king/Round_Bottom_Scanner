from pathlib import Path
import pandas as pd
from fastapi.testclient import TestClient
from app.main import app, repository
from app.market_data import BAR_COLUMNS

def test_bars_endpoint_reads_local_cache(tmp_path: Path):
    original_root=repository.root
    repository.root=tmp_path
    try:
        timestamps=pd.date_range("2026-01-01",periods=30,freq="D",tz="UTC")
        frame=pd.DataFrame([{ "symbol":"TEST.US","timeframe":"daily","timestamp_utc":ts,"open":100.,"high":102.,"low":99.,"close":101.,"volume":1000.,"is_closed":True,"adjustment_type":"forward","trade_session":"all","data_source":"LongPort","updated_at":ts } for ts in timestamps],columns=BAR_COLUMNS)
        repository.merge("TEST.US","daily",frame)
        with TestClient(app) as client:
            response=client.get("/api/v1/symbols/TEST.US/bars?timeframe=daily&limit=20")
        assert response.status_code==200
        assert response.json()["source"]=="local_parquet"
        assert response.json()["count"]==20
    finally:
        repository.root=original_root
