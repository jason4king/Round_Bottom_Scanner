from __future__ import annotations

import argparse
from pathlib import Path

from app.config import get_settings
from app.market_data import ParquetBarRepository
from app.order_block_backtest import run_backtest, write_report
from app.watchlist import load_watchlist


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest daily bullish order-block quality in shadow mode")
    parser.add_argument("--output", type=Path, default=Path("reports/order_blocks"))
    parser.add_argument("--max-distance", type=float, default=2.0)
    parser.add_argument("--radius", type=int, default=5)
    args = parser.parse_args()
    settings = get_settings()
    repository = ParquetBarRepository(settings.parquet_root, settings.cache_retention_bars)
    symbols = load_watchlist(settings.watchlist_path).symbols
    result = run_backtest(repository, symbols, radius=args.radius, max_distance_pct=args.max_distance)
    samples_path, summary_path, segments_path, rules_path, report_path = write_report(result, args.output)
    print(f"samples={len(result.samples)}")
    print(f"samples_csv={samples_path}")
    print(f"summary_csv={summary_path}")
    print(f"segments_csv={segments_path}")
    print(f"candidate_rules_csv={rules_path}")
    print(f"report={report_path}")
    if not result.summary.empty:
        print(result.summary.to_string(index=False))


if __name__ == "__main__":
    main()
