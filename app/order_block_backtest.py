from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.market_structure import bullish_order_block_features


HORIZONS = (5, 10, 20, 40)
QUALITY_BINS = [-np.inf, 40, 55, 65, 75, np.inf]
QUALITY_LABELS = ["0-39", "40-54", "55-64", "65-74", "75-100"]


@dataclass(frozen=True)
class BacktestResult:
    samples: pd.DataFrame
    summary: pd.DataFrame
    segments: pd.DataFrame
    candidate_rules: pd.DataFrame
    validation_cutoff: str | None


def build_samples(symbol: str, bars: pd.DataFrame, *, radius: int = 5, max_distance_pct: float = 2.0) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame()
    frame = bars.copy().sort_values("timestamp_utc") if "timestamp_utc" in bars else bars.copy()
    if "timestamp_utc" in frame:
        frame = frame.set_index("timestamp_utc")
    close_col = "Close" if "Close" in frame else "close"
    high_col = "High" if "High" in frame else "high"
    low_col = "Low" if "Low" in frame else "low"
    features = bullish_order_block_features(frame, radius)
    ema50 = frame[close_col].ewm(span=50, adjust=False).mean()
    ema200 = frame[close_col].ewm(span=200, adjust=False).mean()
    near = features["distance_pct"].le(max_distance_pct) & features["quality_score"].notna()
    newly_near = near & ~near.shift(1, fill_value=False)
    newly_confirmed = features["retest_confirmed"] & ~features["retest_confirmed"].shift(1, fill_value=False)
    event_indices = np.flatnonzero((newly_near | newly_confirmed).to_numpy(bool))
    closes = frame[close_col].to_numpy(float); highs = frame[high_col].to_numpy(float); lows = frame[low_col].to_numpy(float)
    rows: list[dict] = []
    for index in event_indices:
        row = features.iloc[index]
        sample = {
            "symbol": symbol,
            "timestamp": frame.index[index].isoformat(),
            "event_type": "confirmed_retest" if bool(newly_confirmed.iloc[index]) else "near_block",
            "entry_close": closes[index],
            "distance_pct": row["distance_pct"],
            "quality_score": row["quality_score"],
            "formation_score": row["formation_score"],
            "retest_score": row["retest_score"],
            "touch_count": int(row["touch_count"]),
            "age_bars": int(row["age_bars"]),
            "status": row["status"],
            "pierced": bool(row["pierced"]),
            "first_touch": int(row["touch_count"]) == 1,
            "market_regime": (
                "bullish" if closes[index] > ema200.iloc[index] and ema50.iloc[index] > ema200.iloc[index]
                else "bearish" if closes[index] < ema200.iloc[index] and ema50.iloc[index] < ema200.iloc[index]
                else "neutral"
            ),
        }
        for horizon in HORIZONS:
            end = index + horizon
            if end >= len(frame):
                sample[f"return_{horizon}d_pct"] = np.nan
                sample[f"mfe_{horizon}d_pct"] = np.nan
                sample[f"mae_{horizon}d_pct"] = np.nan
                continue
            future_slice = slice(index + 1, end + 1)
            sample[f"return_{horizon}d_pct"] = (closes[end] / closes[index] - 1) * 100
            sample[f"mfe_{horizon}d_pct"] = (np.nanmax(highs[future_slice]) / closes[index] - 1) * 100
            sample[f"mae_{horizon}d_pct"] = (np.nanmin(lows[future_slice]) / closes[index] - 1) * 100
        rows.append(sample)
    return pd.DataFrame(rows)


def summarize_samples(samples: pd.DataFrame) -> pd.DataFrame:
    if samples.empty:
        return pd.DataFrame()
    data = samples.copy()
    data["quality_bucket"] = pd.cut(data["quality_score"], QUALITY_BINS, labels=QUALITY_LABELS, right=False)
    rows: list[dict] = []
    for bucket, group in data.groupby("quality_bucket", observed=False):
        row: dict = {"quality_bucket": str(bucket), "samples": len(group)}
        for horizon in HORIZONS:
            returns = group[f"return_{horizon}d_pct"].dropna()
            row[f"available_{horizon}d"] = len(returns)
            row[f"avg_return_{horizon}d_pct"] = returns.mean() if len(returns) else np.nan
            row[f"median_return_{horizon}d_pct"] = returns.median() if len(returns) else np.nan
            row[f"win_rate_{horizon}d_pct"] = returns.gt(0).mean() * 100 if len(returns) else np.nan
            row[f"avg_mfe_{horizon}d_pct"] = group[f"mfe_{horizon}d_pct"].mean()
            row[f"avg_mae_{horizon}d_pct"] = group[f"mae_{horizon}d_pct"].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def _segment_metrics(group: pd.DataFrame) -> dict:
    row: dict = {"samples": len(group)}
    for horizon in HORIZONS:
        returns = group[f"return_{horizon}d_pct"].dropna()
        row[f"available_{horizon}d"] = len(returns)
        row[f"avg_return_{horizon}d_pct"] = returns.mean() if len(returns) else np.nan
        row[f"median_return_{horizon}d_pct"] = returns.median() if len(returns) else np.nan
        row[f"win_rate_{horizon}d_pct"] = returns.gt(0).mean() * 100 if len(returns) else np.nan
        row[f"avg_mfe_{horizon}d_pct"] = group[f"mfe_{horizon}d_pct"].mean()
        row[f"avg_mae_{horizon}d_pct"] = group[f"mae_{horizon}d_pct"].mean()
    return row


def summarize_segments(samples: pd.DataFrame) -> pd.DataFrame:
    if samples.empty:
        return pd.DataFrame()
    data = samples.copy()
    data["quality_bucket"] = pd.cut(data["quality_score"], QUALITY_BINS, labels=QUALITY_LABELS, right=False).astype(str)
    data["touch_bucket"] = data["touch_count"].clip(upper=3).map({0: "0", 1: "1", 2: "2", 3: "3+"})
    dimensions = ("quality_bucket", "event_type", "status", "market_regime", "pierced", "first_touch", "touch_bucket")
    rows = []
    for split in ("train", "validation"):
        split_data = data[data["time_split"] == split]
        for dimension in dimensions:
            for segment, group in split_data.groupby(dimension, observed=False):
                rows.append({"time_split": split, "dimension": dimension, "segment": str(segment), **_segment_metrics(group)})
    return pd.DataFrame(rows)


def summarize_candidate_rules(samples: pd.DataFrame) -> pd.DataFrame:
    if samples.empty:
        return pd.DataFrame()
    rows = []
    for split, data in samples.groupby("time_split"):
        rules = {
            "all": pd.Series(True, index=data.index),
            "intact_touch_le_1": ~data["pierced"] & data["touch_count"].le(1),
            "intact_untested": ~data["pierced"] & data["touch_count"].eq(0),
            "quality_ge_65": data["quality_score"].ge(65),
        }
        for name, mask in rules.items():
            rows.append({"time_split": split, "rule": name, **_segment_metrics(data[mask])})
    return pd.DataFrame(rows)


def run_backtest(repository, symbols: list[str], *, radius: int = 5, max_distance_pct: float = 2.0) -> BacktestResult:
    frames = []
    for symbol in symbols:
        samples = build_samples(symbol, repository.read(symbol, "daily"), radius=radius, max_distance_pct=max_distance_pct)
        if not samples.empty:
            frames.append(samples)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    cutoff_text = None
    if not combined.empty:
        timestamps = pd.to_datetime(combined["timestamp"], utc=True)
        cutoff = timestamps.quantile(0.70)
        cutoff_text = cutoff.isoformat()
        combined["time_split"] = np.where(timestamps <= cutoff, "train", "validation")
    return BacktestResult(combined, summarize_samples(combined), summarize_segments(combined), summarize_candidate_rules(combined), cutoff_text)


def _markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for _, row in frame.iterrows():
        values = []
        for column in columns:
            value = row[column]
            values.append("—" if pd.isna(value) else f"{value:.2f}" if isinstance(value, float) else str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator, *rows])


def write_report(result: BacktestResult, output_dir: Path) -> tuple[Path, Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    samples_path = output_dir / "order_block_samples.csv"
    summary_path = output_dir / "order_block_summary.csv"
    report_path = output_dir / "order_block_report.md"
    segments_path = output_dir / "order_block_segments.csv"
    rules_path = output_dir / "order_block_candidate_rules.csv"
    result.samples.to_csv(samples_path, index=False, encoding="utf-8-sig")
    result.summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    result.segments.to_csv(segments_path, index=False, encoding="utf-8-sig")
    result.candidate_rules.to_csv(rules_path, index=False, encoding="utf-8-sig")
    columns = [
        "quality_bucket", "samples", "avg_return_5d_pct", "win_rate_5d_pct",
        "avg_return_20d_pct", "median_return_20d_pct", "win_rate_20d_pct",
        "avg_mfe_20d_pct", "avg_mae_20d_pct", "avg_return_40d_pct",
    ]
    report = "\n".join([
        "# 订单块质量影子回测",
        "",
        f"- 样本数：{len(result.samples)}",
        "- 周期：日线",
        "- 事件：首次进入订单块 2% 距离或首次回踩确认",
        "- 前瞻窗口：5、10、20、40 个交易日",
        f"- 样本外分割点：{result.validation_cutoff or '无'}（此前为训练集，此后为验证集）",
        "- 注意：当前观察池存在幸存者偏差，样本事件也可能在时间上重叠。结果用于校准，不代表可交易收益。",
        "",
        "## 质量分层结果",
        "",
        _markdown_table(result.summary, columns) if not result.summary.empty else "没有可用样本。",
        "",
        "## 样本外关键分层（20日）",
        "",
        _markdown_table(
            result.segments[result.segments["dimension"].isin(["quality_bucket", "market_regime", "pierced", "touch_bucket"])],
            ["time_split", "dimension", "segment", "samples", "avg_return_20d_pct", "median_return_20d_pct", "win_rate_20d_pct", "avg_mae_20d_pct"],
        ) if not result.segments.empty else "没有可用样本。",
        "",
        "## 候选规则对比（20日）",
        "",
        _markdown_table(
            result.candidate_rules,
            ["time_split", "rule", "samples", "avg_return_20d_pct", "median_return_20d_pct", "win_rate_20d_pct", "avg_mae_20d_pct"],
        ) if not result.candidate_rules.empty else "没有可用样本。",
        "",
        "## 启用判定",
        "",
        "只有当质量分与中位收益、胜率及回撤呈较稳定的单调改善，并通过样本外验证后，才应影响正式买点。",
        "",
    ])
    report_path.write_text(report, encoding="utf-8")
    return samples_path, summary_path, segments_path, rules_path, report_path
