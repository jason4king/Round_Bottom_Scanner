from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

import duckdb


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS scan_runs (
    run_id UUID PRIMARY KEY,
    run_type VARCHAR NOT NULL CHECK (run_type IN ('official', 'preview')),
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    market_data_cutoff TIMESTAMPTZ,
    algorithm_version VARCHAR NOT NULL,
    config_version VARCHAR NOT NULL,
    config_json JSON NOT NULL,
    config_hash VARCHAR NOT NULL,
    watchlist_json JSON NOT NULL,
    symbols_total INTEGER NOT NULL,
    symbols_succeeded INTEGER NOT NULL DEFAULT 0,
    symbols_failed INTEGER NOT NULL DEFAULT 0,
    error_summary JSON
);

CREATE TABLE IF NOT EXISTS scan_results (
    run_id UUID NOT NULL,
    symbol VARCHAR NOT NULL,
    total_score DOUBLE NOT NULL,
    base_total DOUBLE NOT NULL,
    confluence_total DOUBLE NOT NULL,
    pre_multiplier_score DOUBLE NOT NULL,
    coverage_multiplier DOUBLE NOT NULL,
    triggered_factors_json JSON NOT NULL,
    weekly_score DOUBLE NOT NULL,
    daily_score DOUBLE NOT NULL,
    four_hour_score DOUBLE NOT NULL,
    weekly_bar_timestamp TIMESTAMPTZ,
    daily_bar_timestamp TIMESTAMPTZ,
    four_hour_bar_timestamp TIMESTAMPTZ,
    data_status VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS factor_results (
    run_id UUID NOT NULL,
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    factor_id VARCHAR NOT NULL,
    triggered BOOLEAN NOT NULL,
    signal_name VARCHAR NOT NULL,
    factor_tier VARCHAR,
    base_score DOUBLE NOT NULL,
    timeframe_multiplier DOUBLE NOT NULL,
    score_contribution DOUBLE NOT NULL,
    bar_timestamp TIMESTAMPTZ,
    reason VARCHAR,
    details_json JSON NOT NULL,
    PRIMARY KEY (run_id, symbol, timeframe, factor_id)
);

CREATE TABLE IF NOT EXISTS market_cache_manifest (
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    parquet_path VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    earliest_timestamp TIMESTAMPTZ,
    latest_timestamp TIMESTAMPTZ,
    adjustment_type VARCHAR NOT NULL,
    trade_session VARCHAR NOT NULL,
    sync_status VARCHAR NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    last_error VARCHAR,
    PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS scan_errors (
    error_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    symbol VARCHAR,
    timeframe VARCHAR,
    stage VARCHAR NOT NULL,
    error_code VARCHAR NOT NULL,
    message VARCHAR NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

INSERT INTO schema_migrations(version)
SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 1);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._lock = RLock()

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = duckdb.connect(str(self.path))
        return self._connection

    @contextmanager
    def connect(self, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        # DuckDB has a process-level file lock on Windows. Reuse one connection
        # and serialize its short operations across the scan and API threads.
        with self._lock:
            yield self._get_connection()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(SCHEMA_SQL)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
