from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.prediction_research import run_research


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline 3/5/10-session prediction research.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory (default: reports/prediction_research/<UTC timestamp>).",
    )
    args = parser.parse_args()
    output_dir = args.output_dir or (
        Path("reports") / "prediction_research" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    report = run_research(get_settings(), output_dir)
    print(f"Research complete. Report: {report.resolve()}", flush=True)


if __name__ == "__main__":
    main()
