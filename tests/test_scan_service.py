from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.database import Database
from app.market_data import LongPortProvider, ParquetBarRepository
from app.scan_service import ScanService


def test_save_symbol_error_uses_all_scan_error_columns(tmp_path: Path):
    database=Database(tmp_path/"scanner.duckdb")
    database.initialize()
    settings=Settings(database_path=tmp_path/"scanner.duckdb",parquet_root=tmp_path/"market",watchlist_path=tmp_path/"watchlist.txt")
    service=ScanService(settings,database,LongPortProvider(False),ParquetBarRepository(tmp_path/"market"))
    run_id=uuid4()
    with database.connect() as connection:
        connection.execute("INSERT INTO scan_runs (run_id,run_type,status,started_at,algorithm_version,config_version,config_json,config_hash,watchlist_json,symbols_total) VALUES (?,'official','running',current_timestamp,'a','c','{}','h','[]',1)",[run_id])
    service._save_error(run_id,"SPY.US","weekly 没有可用 K 线")
    with database.connect(read_only=True) as connection:
        row=connection.execute("SELECT symbol,message FROM scan_errors WHERE run_id=?",[run_id]).fetchone()
    assert row==("SPY.US","weekly 没有可用 K 线")
    database.close()
