"""Offline, point-in-time research for 3/5/10-session return forecasts."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import duckdb

from app.config import Settings
from app.market_data import ParquetBarRepository
from app.watchlist import load_watchlist


HORIZONS = (3, 5, 10)
MARKET_SYMBOL = "SPY.US"
FEATURE_COLUMNS = (
    "ret_1", "ret_2", "ret_3", "ret_5", "ret_10", "ret_20", "ret_60",
    "sma_gap_10", "sma_gap_20", "sma_gap_50", "sma_gap_100", "sma_gap_200",
    "vol_10", "vol_20", "vol_60", "atr_pct", "drawdown_20", "drawdown_60",
    "range_pct", "gap_pct", "rsi_14", "macd_hist_pct", "bb_z_20",
    "log_turnover", "volume_rel_20", "volume_change_20",
    "relative_ret_5", "relative_ret_20", "relative_ret_60",
    "market_ret_5", "market_ret_20", "market_ret_60", "market_vol_20",
    "market_sma_gap_50", "market_sma_gap_200",
)


@dataclass(frozen=True)
class ResearchConfig:
    initial_train_sessions: int = 504
    test_sessions: int = 63
    ridge_alpha: float = 10.0
    logistic_c: float = 0.1
    top_fraction: float = 0.10
    round_trip_cost_bps: float = 20.0


def _closed_daily_bars(repository: ParquetBarRepository, symbol: str) -> pd.DataFrame:
    bars = repository.read(symbol, "daily")
    if bars.empty:
        return pd.DataFrame()
    bars = bars.loc[bars["is_closed"].astype(bool)].copy()
    if bars.empty:
        return pd.DataFrame()
    closed_input_rows = len(bars)
    numeric = ["open", "high", "low", "close", "volume"]
    bars[numeric] = bars[numeric].apply(pd.to_numeric, errors="coerce")
    valid = np.isfinite(bars[numeric]).all(axis=1)
    valid &= (bars[["open", "high", "low", "close"]] > 0).all(axis=1)
    valid &= bars["volume"].ge(0)
    valid &= bars["high"].ge(bars[["open", "close", "low"]].max(axis=1))
    valid &= bars["low"].le(bars[["open", "close", "high"]].min(axis=1))
    bars = bars.loc[valid].copy()
    if bars.empty:
        return pd.DataFrame()
    timestamps = pd.to_datetime(bars["timestamp_utc"], utc=True)
    bars["signal_date"] = timestamps.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    bars = bars.sort_values("timestamp_utc").drop_duplicates("signal_date", keep="last")
    bars = bars.reset_index(drop=True)
    bars.attrs["quality_audit"] = {
        "closed_input_rows": closed_input_rows,
        "invalid_rows_removed": closed_input_rows - len(bars),
    }
    return bars


def _features_for_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute causal features; every row uses this bar or earlier bars only."""
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    open_ = bars["open"].astype(float)
    volume = bars["volume"].astype(float)
    log_return = np.log(close / close.shift(1))
    features = pd.DataFrame(index=bars.index)

    for window in (1, 2, 3, 5, 10, 20, 60):
        features[f"ret_{window}"] = close.pct_change(window)
    for window in (10, 20, 50, 100, 200):
        average = close.rolling(window, min_periods=window).mean()
        features[f"sma_gap_{window}"] = close / average - 1
    for window in (10, 20, 60):
        features[f"vol_{window}"] = log_return.rolling(window, min_periods=window).std(ddof=0) * math.sqrt(252)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    features["atr_pct"] = true_range.rolling(14, min_periods=14).mean() / close
    for window in (20, 60):
        features[f"drawdown_{window}"] = close / close.rolling(window, min_periods=window).max() - 1
    features["range_pct"] = (high - low) / previous_close
    features["gap_pct"] = open_ / previous_close - 1

    change = close.diff()
    average_gain = change.clip(lower=0).rolling(14, min_periods=14).mean()
    average_loss = -change.clip(upper=0).rolling(14, min_periods=14).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + relative_strength)).where(average_loss.ne(0), 100.0)
    features["rsi_14"] = rsi.where(average_gain.ne(0), 50.0)
    macd = close.ewm(span=12, adjust=False, min_periods=26).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    features["macd_hist_pct"] = (macd - macd_signal) / close
    mean20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std(ddof=0)
    features["bb_z_20"] = (close - mean20) / std20.replace(0, np.nan)

    turnover = (close * volume).where((close > 0) & (volume >= 0))
    features["log_turnover"] = np.log1p(turnover)
    median_volume = volume.rolling(20, min_periods=20).median()
    features["volume_rel_20"] = volume / median_volume.replace(0, np.nan)
    features["volume_change_20"] = volume / volume.shift(20).replace(0, np.nan) - 1
    return features.replace([np.inf, -np.inf], np.nan)


def build_dataset(settings: Settings, config: ResearchConfig | None = None) -> tuple[pd.DataFrame, dict[str, object]]:
    config = config or ResearchConfig()
    repository = ParquetBarRepository(settings.parquet_root, settings.cache_retention_bars)
    symbols = load_watchlist(settings.watchlist_path).symbols
    raw = {symbol: _closed_daily_bars(repository, symbol) for symbol in symbols}
    raw = {symbol: bars for symbol, bars in raw.items() if not bars.empty}
    if MARKET_SYMBOL not in raw:
        raise RuntimeError(f"Market benchmark {MARKET_SYMBOL} has no closed daily bars")

    market_bars = raw[MARKET_SYMBOL]
    market_features = _features_for_bars(market_bars)
    market = pd.DataFrame({"signal_date": market_bars["signal_date"].to_numpy()})
    market["market_ret_5"] = market_features["ret_5"].to_numpy()
    market["market_ret_20"] = market_features["ret_20"].to_numpy()
    market["market_ret_60"] = market_features["ret_60"].to_numpy()
    market["market_vol_20"] = market_features["vol_20"].to_numpy()
    market["market_sma_gap_50"] = market_features["sma_gap_50"].to_numpy()
    market["market_sma_gap_200"] = market_features["sma_gap_200"].to_numpy()
    market_returns = market.set_index("signal_date")[["market_ret_5", "market_ret_20", "market_ret_60"]]

    parts: list[pd.DataFrame] = []
    market_dates = raw[MARKET_SYMBOL]["signal_date"].tolist()
    market_positions = {date: index for index, date in enumerate(market_dates)}
    for symbol_number, (symbol, bars) in enumerate(raw.items(), 1):
        features = _features_for_bars(bars)
        part = features.copy()
        part["symbol"] = symbol
        part["signal_date"] = bars["signal_date"].to_numpy()
        part["close_now"] = bars["close"].to_numpy()
        positions = bars["signal_date"].map(market_positions)
        entry_dates = positions.map(lambda pos: market_dates[int(pos) + 1] if pd.notna(pos) and int(pos) + 1 < len(market_dates) else None)
        close_by_date = bars.set_index("signal_date")["close"]
        open_by_date = bars.set_index("signal_date")["open"]
        part["feature_ready"] = features["sma_gap_200"].notna().to_numpy()
        part = part.merge(market, on="signal_date", how="left", validate="many_to_one")
        for window in (5, 20, 60):
            part[f"relative_ret_{window}"] = part[f"ret_{window}"] - part[f"market_ret_{window}"]
        for horizon in HORIZONS:
            exit_dates = positions.map(lambda pos: market_dates[int(pos) + horizon] if pd.notna(pos) and int(pos) + horizon < len(market_dates) else None)
            entry = entry_dates.map(open_by_date).to_numpy(dtype=float)
            exit_price = exit_dates.map(close_by_date).to_numpy(dtype=float)
            gross = exit_price / entry - 1
            half_cost = config.round_trip_cost_bps / 20_000.0
            part[f"gross_executable_return_{horizon}"] = gross
            part[f"net_executable_return_{horizon}"] = (1 + gross) * (1 - half_cost) / (1 + half_cost) - 1
            part[f"target_entry_date_{horizon}"] = entry_dates.to_numpy()
            part[f"target_end_date_{horizon}"] = exit_dates.to_numpy()
        parts.append(part.reset_index(drop=True))
        if symbol_number % 50 == 0 or symbol_number == len(raw):
            print(f"feature build {symbol_number}/{len(raw)} symbols", flush=True)

    dataset = pd.concat(parts, ignore_index=True)
    dataset["signal_date"] = pd.to_datetime(dataset["signal_date"])
    for horizon in HORIZONS:
        dataset[f"target_entry_date_{horizon}"] = pd.to_datetime(dataset[f"target_entry_date_{horizon}"])
        dataset[f"target_end_date_{horizon}"] = pd.to_datetime(dataset[f"target_end_date_{horizon}"])
    dataset = dataset.loc[dataset["feature_ready"]].drop(columns=["feature_ready", "close_now"])
    dataset = dataset.sort_values(["signal_date", "symbol"]).reset_index(drop=True)
    coverage = dataset.groupby("signal_date")["symbol"].nunique()
    breadth_threshold = max(50, math.ceil(len(symbols) * 0.5))
    broad_dates = coverage.loc[coverage >= breadth_threshold]
    if broad_dates.empty:
        raise RuntimeError(f"No date reaches the minimum cross-sectional coverage of {breadth_threshold} symbols")
    meta: dict[str, object] = {
        "watchlist_symbols": len(symbols),
        "symbols_with_closed_daily_bars": len(raw),
        "market_symbol": MARKET_SYMBOL,
        "first_signal_date": dataset["signal_date"].min().date().isoformat(),
        "last_signal_date": dataset["signal_date"].max().date().isoformat(),
        "signal_dates": int(dataset["signal_date"].nunique()),
        "feature_rows": len(dataset),
        "breadth_threshold_symbols": breadth_threshold,
        "first_broad_cross_sectional_date": broad_dates.index.min().date().isoformat(),
        "symbols_per_date": {
            "min": int(coverage.min()),
            "median": float(coverage.median()),
            "max": int(coverage.max()),
        },
        "feature_columns": list(FEATURE_COLUMNS),
        "bar_cleaning": {
            "rule": "finite OHLCV; positive OHLC; nonnegative volume; high/low contain open and close",
            "valid_rows": int(sum(len(bars) for bars in raw.values())),
            "closed_input_rows": int(sum(bars.attrs.get("quality_audit", {}).get("closed_input_rows", len(bars)) for bars in raw.values())),
            "invalid_rows_removed": int(sum(bars.attrs.get("quality_audit", {}).get("invalid_rows_removed", 0) for bars in raw.values())),
            "round_trip_cost_bps": config.round_trip_cost_bps,
        },
    }
    return dataset, meta


def _clip_logistic_targets(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    config: ResearchConfig,
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=config.logistic_c, max_iter=500, solver="lbfgs")),
    ])
    model.fit(train[list(feature_columns)], train[target].astype(int))
    return model.predict_proba(test[list(feature_columns)])[:, list(model.named_steps["model"].classes_).index(1)]


def _classification_metrics(
    actual: np.ndarray, probability: np.ndarray, baseline_probability: float | np.ndarray
) -> dict[str, float]:
    from sklearn.metrics import log_loss, roc_auc_score

    probability = np.clip(probability, 1e-6, 1 - 1e-6)
    baseline = np.broadcast_to(np.asarray(baseline_probability, dtype=float), (len(actual),)).copy()
    baseline = np.clip(baseline, 1e-6, 1 - 1e-6)
    result = {
        "brier_model": float(np.mean((probability - actual) ** 2)),
        "brier_base_rate": float(np.mean((baseline - actual) ** 2)),
        "log_loss_model": float(log_loss(actual, probability, labels=[0, 1])),
        "log_loss_base_rate": float(log_loss(actual, baseline, labels=[0, 1])),
        "accuracy_at_50pct": float(np.mean((probability >= 0.5) == actual)),
        "base_rate": float(np.mean(actual)),
        "mean_probability": float(np.mean(probability)),
    }
    result["roc_auc"] = float(roc_auc_score(actual, probability)) if len(np.unique(actual)) == 2 else float("nan")
    return result


def _regression_metrics(actual: np.ndarray, predicted: np.ndarray, baseline: np.ndarray) -> dict[str, float]:
    actual_series = pd.Series(actual)
    predicted_series = pd.Series(predicted)
    return {
        "mae_model": float(np.mean(np.abs(predicted - actual))),
        "mae_train_mean": float(np.mean(np.abs(baseline - actual))),
        "rmse_model": float(np.sqrt(np.mean((predicted - actual) ** 2))),
        "rmse_train_mean": float(np.sqrt(np.mean((baseline - actual) ** 2))),
        "mean_actual_return": float(np.mean(actual)),
        "mean_predicted_return": float(np.mean(predicted)),
        "pearson_correlation": float(actual_series.corr(predicted_series, method="pearson")),
        "spearman_correlation": float(actual_series.corr(predicted_series, method="spearman")),
    }


def _audit_dataset(dataset: pd.DataFrame, config: ResearchConfig) -> dict[str, object]:
    audit: dict[str, object] = {
        "duplicate_symbol_date_rows": int(dataset.duplicated(["symbol", "signal_date"]).sum()),
        "feature_missing_fraction": dataset[list(FEATURE_COLUMNS)].isna().mean().to_dict(),
        "rows_per_symbol": {
            "min": int(dataset.groupby("symbol").size().min()),
            "median": float(dataset.groupby("symbol").size().median()),
            "max": int(dataset.groupby("symbol").size().max()),
        },
        "targets": {},
    }
    for horizon in HORIZONS:
        target = f"net_executable_return_{horizon}"
        values = dataset[target].dropna()
        audit["targets"][str(horizon)] = {
            "valid_rows": len(values),
            "missing_rows": int(dataset[target].isna().sum()),
            "close_up_fraction": float((values > 0).mean()),
            "quantiles": values.quantile([0.01, 0.05, 0.5, 0.95, 0.99]).to_dict(),
            "abs_return_over_20pct_rows": int((values.abs() > 0.20).sum()),
        }
    return audit


def _score_top_fraction(
    predictions: pd.DataFrame, config: ResearchConfig, score_column: str = "predicted_return"
) -> dict[str, float | int | str]:
    selections = []
    for _, group in predictions.groupby("signal_date", sort=True):
        count = max(1, int(math.ceil(len(group) * config.top_fraction)))
        selections.append(group.nlargest(count, score_column))
    chosen = pd.concat(selections, ignore_index=True)
    result: dict[str, float | int] = {
        "selection_score": score_column,
        "top_fraction": config.top_fraction,
        "selected_samples": len(chosen),
        "top_bucket_mean_net_return": float(chosen["net_executable_return"].mean()),
        "top_bucket_win_rate_net": float((chosen["net_executable_return"] > 0).mean()),
    }
    result["market_mean_net_return"] = float(predictions.loc[predictions["symbol"] == MARKET_SYMBOL, "net_executable_return"].mean()) if (predictions["symbol"] == MARKET_SYMBOL).any() else float("nan")
    result["universe_mean_net_return"] = float(predictions["net_executable_return"].mean())
    return result


def _load_scanner_scores(settings: Settings) -> pd.DataFrame:
    con = duckdb.connect(str(settings.database_path), read_only=True)
    try:
        rows = con.execute(
            """SELECT r.symbol,
                      CAST(r.daily_bar_timestamp AT TIME ZONE 'America/New_York' AS DATE) AS signal_date,
                      r.total_score AS scanner_score,
                      s.completed_at,
                      CAST(s.run_id AS VARCHAR) AS scan_run_id
               FROM scan_results r JOIN scan_runs s USING (run_id)
               WHERE s.status IN ('completed','completed_with_errors')
                 AND s.run_type='official' AND s.algorithm_version=?
                 AND r.daily_bar_timestamp IS NOT NULL
               QUALIFY row_number() OVER (
                   PARTITION BY r.symbol, CAST(r.daily_bar_timestamp AT TIME ZONE 'America/New_York' AS DATE)
                   ORDER BY s.completed_at DESC
               )=1""",
            [settings.algorithm_version],
        ).fetchdf()
    finally:
        con.close()
    if not rows.empty:
        rows["signal_date"] = pd.to_datetime(rows["signal_date"])
    return rows


def _same_sample_comparison(predictions: pd.DataFrame, config: ResearchConfig) -> dict[str, object]:
    common = predictions.dropna(subset=["scanner_score", "net_executable_return"]).copy()
    if common.empty:
        return {"status": "no_matching_archived_scanner_scores", "rows": 0, "dates": 0}
    from sklearn.metrics import roc_auc_score
    actual = (common["net_executable_return"].to_numpy() > 0).astype(int)
    result: dict[str, object] = {
        "status": "complete",
        "rows": len(common),
        "dates": int(common["signal_date"].nunique()),
        "first_date": common["signal_date"].min().date().isoformat(),
        "last_date": common["signal_date"].max().date().isoformat(),
        "symbols_per_date_median": float(common.groupby("signal_date").size().median()),
        "same_sample_net_return_baseline": float(common["net_executable_return"].mean()),
        "ranking": {},
    }
    for label, column in (("model", "predicted_return"), ("momentum", "momentum_predicted_return"), ("scanner", "scanner_score")):
        selected = []
        for _, group in common.groupby("signal_date", sort=True):
            n = max(1, int(math.ceil(len(group) * config.top_fraction)))
            selected.append(group.nlargest(n, column))
        chosen = pd.concat(selected, ignore_index=True)
        scores = common[column].to_numpy(dtype=float)
        result["ranking"][label] = {
            "top_fraction": config.top_fraction,
            "selected_rows": len(chosen),
            "top_bucket_mean_net_return": float(chosen["net_executable_return"].mean()),
            "top_bucket_win_rate_net": float((chosen["net_executable_return"] > 0).mean()),
            "roc_auc_net_positive": float(roc_auc_score(actual, scores)) if len(np.unique(actual)) == 2 else None,
        }
    return result


def _make_folds(dates: pd.DatetimeIndex, config: ResearchConfig):
    start = config.initial_train_sessions
    while start < len(dates) - HORIZONS[-1]:
        end = min(start + config.test_sessions, len(dates))
        yield start, end
        start = end


def run_research(settings: Settings, output_dir: Path, config: ResearchConfig | None = None) -> Path:
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler

    config = config or ResearchConfig()
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset, data_meta = build_dataset(settings, config)
    scanner_scores = _load_scanner_scores(settings)
    data_meta["scanner_score_archive"] = {
        "algorithm_version": settings.algorithm_version,
        "rows": len(scanner_scores),
        "dates": int(scanner_scores["signal_date"].nunique()) if not scanner_scores.empty else 0,
        "first_date": scanner_scores["signal_date"].min().date().isoformat() if not scanner_scores.empty else None,
        "last_date": scanner_scores["signal_date"].max().date().isoformat() if not scanner_scores.empty else None,
    }
    data_audit = _audit_dataset(dataset, config)
    dataset_path = output_dir / "features_and_labels.parquet"
    dataset.to_parquet(dataset_path, index=False)
    all_dates = pd.DatetimeIndex(sorted(dataset["signal_date"].dropna().unique()))
    cv_start_date = pd.Timestamp(data_meta["first_broad_cross_sectional_date"])
    dates = all_dates[all_dates >= cv_start_date]
    date_positions = {pd.Timestamp(date): index for index, date in enumerate(dates)}
    dataset["date_index"] = dataset["signal_date"].map(date_positions)
    for horizon in HORIZONS:
        dataset[f"target_end_index_{horizon}"] = dataset[f"target_end_date_{horizon}"].map(date_positions)

    all_predictions: dict[int, list[pd.DataFrame]] = {h: [] for h in HORIZONS}
    fold_summaries: list[dict[str, object]] = []
    scanner_md_rows: list[str] = []
    probability_md_rows: list[str] = []
    used_folds = 0
    for fold_number, (start, end) in enumerate(_make_folds(dates, config), 1):
        fold_had_predictions = False
        test_date_start, test_date_end = dates[start], dates[end - 1]
        base_train_mask = dataset["date_index"] < start
        test_mask = dataset["date_index"].between(start, end - 1)
        for horizon in HORIZONS:
            target = f"net_executable_return_{horizon}"
            end_index = f"target_end_index_{horizon}"
            valid = dataset[target].notna() & dataset[end_index].notna()
            train = dataset.loc[base_train_mask & valid & (dataset[end_index] < start)].copy()
            test = dataset.loc[test_mask & valid].copy()
            if len(train) < 5000 or test.empty:
                continue

            x_train, x_test = train[list(FEATURE_COLUMNS)], test[list(FEATURE_COLUMNS)]
            y_train, y_test = train[target].astype(float), test[target].astype(float)
            target_lo, target_hi = y_train.quantile([0.01, 0.99])
            y_fit = y_train.clip(target_lo, target_hi)
            reg = Pipeline([
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scale", StandardScaler()),
                ("model", Ridge(alpha=config.ridge_alpha)),
            ])
            reg.fit(x_train, y_fit)
            predicted_return = reg.predict(x_test)

            momentum_features = ("ret_20", "ret_60")
            momentum_reg = Pipeline([
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scale", StandardScaler()),
                ("model", Ridge(alpha=config.ridge_alpha)),
            ])
            momentum_reg.fit(train[list(momentum_features)], y_fit)
            momentum_predicted_return = momentum_reg.predict(test[list(momentum_features)])

            positive_column = f"label_close_up_{horizon}"
            train[positive_column] = (train[target] > 0).astype(int)
            test[positive_column] = (test[target] > 0).astype(int)
            labels = train[positive_column]
            if labels.nunique() > 1:
                probability = _clip_logistic_targets(train, test, positive_column, config)
                momentum_probability = _clip_logistic_targets(
                    train, test, positive_column, config, momentum_features
                )
            else:
                probability = np.full(len(test), float(labels.mean()))
                momentum_probability = np.full(len(test), float(labels.mean()))

            mean_baseline = np.full(len(test), float(y_train.mean()))
            classification = _classification_metrics(test[positive_column].to_numpy(), probability, float(labels.mean()))
            momentum_classification = _classification_metrics(
                test[positive_column].to_numpy(), momentum_probability, float(labels.mean())
            )
            regression = _regression_metrics(y_test.to_numpy(), predicted_return, mean_baseline)
            momentum_regression = _regression_metrics(y_test.to_numpy(), momentum_predicted_return, mean_baseline)
            fold_summaries.append({
                "horizon": horizon,
                "test_start": test_date_start.date().isoformat(),
                "test_end": test_date_end.date().isoformat(),
                "train_rows": len(train),
                "test_rows": len(test),
                **classification,
                **regression,
                "brier_momentum": momentum_classification["brier_model"],
                "log_loss_momentum": momentum_classification["log_loss_model"],
                "roc_auc_momentum": momentum_classification["roc_auc"],
                "mae_momentum": momentum_regression["mae_model"],
                "rmse_momentum": momentum_regression["rmse_model"],
            })
            out = test[["symbol", "signal_date", target, f"gross_executable_return_{horizon}"]].rename(
                columns={target: "net_executable_return", f"gross_executable_return_{horizon}": "gross_executable_return"}
            ).copy()
            out["horizon"] = horizon
            out["probability_close_up"] = probability
            out["momentum_probability_close_up"] = momentum_probability
            out["training_positive_rate"] = float(labels.mean())
            out["predicted_return"] = predicted_return
            out["momentum_predicted_return"] = momentum_predicted_return
            out["training_mean_return"] = float(y_train.mean())
            out["fold_start"] = test_date_start
            all_predictions[horizon].append(out)
            fold_had_predictions = True
            print(
                f"fold {fold_number}: {horizon}d fit/test rows={len(train)}/{len(test)} "
                f"dates={test_date_start.date()}..{test_date_end.date()}",
                flush=True,
            )
        if fold_had_predictions:
            used_folds += 1

    summary: dict[str, object] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "algorithm_version": settings.algorithm_version,
        "config_version": settings.config_version,
        "data": data_meta,
        "data_audit": data_audit,
        "research_config": asdict(config),
        "fold_count": used_folds,
        "folds": fold_summaries,
        "horizons": {},
        "limitations": [
            "Universe is the current watchlist; the history has survivorship bias and is not a point-in-time constituent universe.",
            "Labels overlap in time; training rows whose outcome reaches the test window are purged.",
            "Scanner comparison uses only archived point-in-time total scores from the current algorithm; coverage may be sparse.",
            "Top-decile figures are per-signal-cohort diagnostics, not a fully capital-constrained overlapping portfolio backtest.",
        ],
    }
    md = [
        "# 预测模型滚动样本外研究报告",
        "",
        f"生成时间（UTC）：{summary['created_at_utc']}",
        f"覆盖股票：{data_meta['symbols_with_closed_daily_bars']} / {data_meta['watchlist_symbols']}",
        f"有效日期：{data_meta['first_signal_date']} 至 {data_meta['last_signal_date']}（{data_meta['signal_dates']} 个交易日）",
        f"点时特征行数：{data_meta['feature_rows']}；滚动测试折数：{used_folds}",
        f"滚动验证起点：{data_meta['first_broad_cross_sectional_date']}（该日及以后至少有 {data_meta['breadth_threshold_symbols']} 只股票有有效日线）",
        f"每日横截面覆盖数：最少/中位/最多 {data_meta['symbols_per_date']['min']} / {data_meta['symbols_per_date']['median']:.0f} / {data_meta['symbols_per_date']['max']}",
        f"数据审计：重复证券-日期行 {data_audit['duplicate_symbol_date_rows']}；每只股票有效特征行中位数 {data_audit['rows_per_symbol']['median']:.0f}",
        f"日线质量过滤：保留 {data_meta['bar_cleaning']['valid_rows']} / {data_meta['bar_cleaning']['closed_input_rows']} 条（剔除 {data_meta['bar_cleaning']['invalid_rows_removed']} 条）",
        f"可成交标签：信号日收盘后，次交易日开盘买入，第 h 个交易日收盘卖出；往返成本 {config.round_trip_cost_bps:.0f} bps",
        f"当前扫描器历史分数：{data_meta['scanner_score_archive']['rows']} 条，覆盖 {data_meta['scanner_score_archive']['dates']} 个日期",
        "",
        "### 标签审计",
        "",
        "| 期限 | 有效标签 | 缺失（期限尾部） | 未来收盘价高于当前收盘价比例 | 收益 P1 / P50 / P99 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for horizon in HORIZONS:
        audit = data_audit["targets"][str(horizon)]
        quantiles = audit["quantiles"]
        md.append(
            f"| {horizon} 日 | {audit['valid_rows']} | {audit['missing_rows']} | "
            f"{audit['close_up_fraction']:.3f} | "
            f"{quantiles[0.01]:.3f} / {quantiles[0.5]:.3f} / {quantiles[0.99]:.3f} |"
        )
    md.extend([
        "",
        "## 汇总",
        "",
        "| 期限 | OOS 样本 | MAE（全特征 / 训练均值 / 动量） | Brier（全特征 / 先验 / 动量） | AUC（全特征 / 动量） | 预测收益最高 10% 的平均收盘到收盘收益（全特征 / 动量） | SPY 平均收益 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for horizon in HORIZONS:
        batches = all_predictions[horizon]
        if not batches:
            summary["horizons"][str(horizon)] = {"status": "insufficient_data"}
            md.append(f"| {horizon} 日 | — | — | — | — | — | — |")
            continue
        pred = pd.concat(batches, ignore_index=True).sort_values(["signal_date", "symbol"]).reset_index(drop=True)
        predictions_path = output_dir / f"predictions_{horizon}d.parquet"
        actual = (pred["net_executable_return"].to_numpy() > 0).astype(int)
        probability = pred["probability_close_up"].to_numpy()
        training_rate = pred["training_positive_rate"].to_numpy()
        class_metrics = _classification_metrics(actual, probability, training_rate)
        momentum_class_metrics = _classification_metrics(
            actual, pred["momentum_probability_close_up"].to_numpy(), training_rate
        )
        return_metrics = _regression_metrics(
            pred["net_executable_return"].to_numpy(), pred["predicted_return"].to_numpy(), pred["training_mean_return"].to_numpy()
        )
        momentum_return_metrics = _regression_metrics(
            pred["net_executable_return"].to_numpy(), pred["momentum_predicted_return"].to_numpy(), pred["training_mean_return"].to_numpy()
        )
        selection = _score_top_fraction(pred, config)
        momentum_selection = _score_top_fraction(pred, config, "momentum_predicted_return")
        probability_selection = _score_top_fraction(pred, config, "probability_close_up")
        momentum_probability_selection = _score_top_fraction(pred, config, "momentum_probability_close_up")
        if not scanner_scores.empty:
            pred = pred.merge(
                scanner_scores[["symbol", "signal_date", "scanner_score", "scan_run_id"]],
                on=["symbol", "signal_date"], how="left", validate="one_to_one",
            )
        else:
            pred["scanner_score"] = np.nan
            pred["scan_run_id"] = None
        scanner_comparison = _same_sample_comparison(pred, config)
        if scanner_comparison.get("status") == "complete":
            ranked = scanner_comparison["ranking"]
            scanner_md_rows.append(
                f"| {horizon} | {scanner_comparison['rows']} | {scanner_comparison['dates']} "
                f"| {ranked['model']['top_bucket_mean_net_return']:.4f} / {ranked['momentum']['top_bucket_mean_net_return']:.4f} / {ranked['scanner']['top_bucket_mean_net_return']:.4f} "
                f"| {ranked['model']['roc_auc_net_positive']:.3f} / {ranked['momentum']['roc_auc_net_positive']:.3f} / {ranked['scanner']['roc_auc_net_positive']:.3f} |"
            )
        else:
            scanner_md_rows.append(f"| {horizon} | 0 | 0 | unavailable | unavailable |")
        pred.to_parquet(predictions_path, index=False)
        horizon_summary = {
            "status": "complete",
            "oos_rows": len(pred),
            "oos_dates": int(pred["signal_date"].nunique()),
            **class_metrics,
            **return_metrics,
            "brier_momentum": momentum_class_metrics["brier_model"],
            "log_loss_momentum": momentum_class_metrics["log_loss_model"],
            "roc_auc_momentum": momentum_class_metrics["roc_auc"],
            "mae_momentum": momentum_return_metrics["mae_model"],
            "rmse_momentum": momentum_return_metrics["rmse_model"],
            **selection,
            "momentum_top_bucket_mean_net_return": momentum_selection["top_bucket_mean_net_return"],
            "momentum_top_bucket_win_rate_net": momentum_selection["top_bucket_win_rate_net"],
            "logistic_probability_top_bucket_mean_net_return": probability_selection["top_bucket_mean_net_return"],
            "logistic_probability_top_bucket_win_rate_net": probability_selection["top_bucket_win_rate_net"],
            "momentum_probability_top_bucket_mean_net_return": momentum_probability_selection["top_bucket_mean_net_return"],
            "momentum_probability_top_bucket_win_rate_net": momentum_probability_selection["top_bucket_win_rate_net"],
            "scanner_same_sample_comparison": scanner_comparison,
            "predictions_file": predictions_path.name,
        }
        summary["horizons"][str(horizon)] = horizon_summary
        probability_md_rows.append(
            f"| {horizon} | {probability_selection['top_bucket_mean_net_return']:.2%} | "
            f"{probability_selection['top_bucket_win_rate_net']:.2%} | "
            f"{momentum_probability_selection['top_bucket_mean_net_return']:.2%} | "
            f"{momentum_probability_selection['top_bucket_win_rate_net']:.2%} |"
        )
        md.append(
            f"| {horizon} 日 | {len(pred)} | {return_metrics['mae_model']:.4f} / {return_metrics['mae_train_mean']:.4f} / {horizon_summary['mae_momentum']:.4f} "
            f"| {class_metrics['brier_model']:.4f} / {class_metrics['brier_base_rate']:.4f} / {horizon_summary['brier_momentum']:.4f} "
            f"| {class_metrics['roc_auc']:.3f} / {horizon_summary['roc_auc_momentum']:.3f} "
            f"| {selection['top_bucket_mean_net_return']:.4f} / {horizon_summary['momentum_top_bucket_mean_net_return']:.4f} "
            f"| {selection['market_mean_net_return']:.4f} |"
        )
    md.extend([
        "",
        "## Logistic probability ranking (top 10% per date)",
        "",
        "| Horizon | Logistic mean net return | Logistic win rate | Momentum probability mean net return | Momentum win rate |",
        "|---:|---:|---:|---:|---:|",
        *probability_md_rows,
    ])
    md.extend(["", "## 解释与限制", ""])
    md.extend([
        "### Same-sample scanner comparison",
        "",
        "| Horizon | Common rows | Dates | Top-decile mean net return: model / momentum / scanner | AUC: model / momentum / scanner |",
        "|---:|---:|---:|---:|---:|",
        *scanner_md_rows,
        "",
    ])
    md.extend(f"- {limitation}" for limitation in summary["limitations"])
    md.extend([
        "- 分类标签定义为未来第 h 个交易日收盘价高于信号日收盘价；收盘后形成信号并不代表可按信号日收盘价成交。",
        "- 本轮没有根据 OOS 结果调参；所有阈值和模型参数记录在 `summary.json`。",
        "- 指标是研究诊断，不能仅凭单次历史回测判定模型可交易或可上线。",
        "",
        "## 文件",
        "",
        "- `features_and_labels.parquet`：逐证券逐日的点时特征及 3/5/10 日标签。",
        "- `predictions_3d.parquet`、`predictions_5d.parquet`、`predictions_10d.parquet`：滚动样本外预测。",
        "- `summary.json`：完整折级指标、配置与限制。",
    ])
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return report_path
