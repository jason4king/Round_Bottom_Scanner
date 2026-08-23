from pathlib import Path
from app.database import Database

def test_database_reuses_connection_and_closes(tmp_path: Path):
    database=Database(tmp_path/"test.duckdb")
    database.initialize()
    with database.connect() as first:
        first_id=id(first)
    with database.connect(read_only=True) as second:
        assert id(second)==first_id
        assert second.execute("SELECT 1").fetchone()==(1,)
    database.close()
    with database.connect() as reopened:
        assert id(reopened)!=first_id
    database.close()
