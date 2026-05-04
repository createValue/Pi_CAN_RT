#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


NUMERIC_COLS_TX = [
    "host_event_ns", "link_id", "task_id", "can_id", "seq",
    "nominal_period_ns", "planned_release_ns", "wakeup_ns", "send_call_ns",
    "send_ret", "cpu_id", "sched_policy", "sched_priority"
]

NUMERIC_COLS_RX = [
    "host_event_ns", "link_id", "task_id", "can_id", "seq", "dlc",
    "rx_kernel_ts_ns", "rx_user_read_ns", "loss_count",
    "cpu_id", "sched_policy", "sched_priority"
]


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def read_csv_safe(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    return pd.read_csv(path, encoding="utf-8-sig")


def to_numeric_cols(df: pd.DataFrame, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def quantile_or_nan(series: pd.Series, q: float):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.quantile(q))


def mean_or_nan(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.mean())


def std_or_nan(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.std())


def min_or_nan(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.min())


def max_or_nan(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return np.nan
    return float(s.max())


def build_metric_rows(df: pd.DataFrame, scope: str, group_info: dict, metrics: list):
    rows = []

    for metric in metrics:
        if metric not in df.columns:
            continue

        s = pd.to_numeric(df[metric], errors="coerce")
        non_na = s.dropna()

        row = {
            "scope": scope,
            "metric": metric,
            "count": int(len(non_na)),
            "mean_ns": mean_or_nan(non_na),
            "std_ns": std_or_nan(non_na),
            "min_ns": min_or_nan(non_na),
            "p50_ns": quantile_or_nan(non_na, 0.50),
            "p95_ns": quantile_or_nan(non_na, 0.95),
            "p99_ns": quantile_or_nan(non_na, 0.99),
            "max_ns": max_or_nan(non_na),
        }

        row.update(group_info)
        rows.append(row)

    return rows


def main():
    parser = argparse.ArgumentParser(description="Analyze loop RT logs")
    parser.add_argument("--tx-log", required=True, help="Path to tx_log.csv")
    parser.add_argument("--rx-log", required=True, help="Path to rx_log.csv")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    args = parser.parse_args()

    tx_log = Path(args.tx_log)
    rx_log = Path(args.rx_log)
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)

    # 1) 读取原始日志
    tx_df = read_csv_safe(tx_log)
    rx_df = read_csv_safe(rx_log)

    tx_df = to_numeric_cols(tx_df, NUMERIC_COLS_TX)
    rx_df = to_numeric_cols(rx_df, NUMERIC_COLS_RX)

    if "event_type" in tx_df.columns:
        tx_df = tx_df[tx_df["event_type"] == "TX"].copy()

    if "event_type" in rx_df.columns:
        rx_only_df = rx_df[rx_df["event_type"] == "RX"].copy()
        loss_df = rx_df[rx_df["event_type"] == "LOSS"].copy()
    else:
        rx_only_df = rx_df.copy()
        loss_df = pd.DataFrame()

    # 去重：同一 (link_id, task_id, seq) 保留第一条
    tx_df = tx_df.sort_values(["link_id", "task_id", "seq", "send_call_ns"]).drop_duplicates(
        subset=["link_id", "task_id", "seq"], keep="first"
    )

    rx_only_df = rx_only_df.sort_values(["link_id", "task_id", "seq", "rx_user_read_ns"]).drop_duplicates(
        subset=["link_id", "task_id", "seq"], keep="first"
    )

    # 2) 合并 TX / RX
    merge_keys = ["link_id", "task_id", "seq"]
    merged = pd.merge(
        tx_df,
        rx_only_df,
        on=merge_keys,
        how="left",
        suffixes=("_tx", "_rx")
    )

    if "can_id_tx" in merged.columns:
        merged["can_id"] = pd.to_numeric(merged["can_id_tx"], errors="coerce")
    elif "can_id" not in merged.columns and "can_id_rx" in merged.columns:
        merged["can_id"] = pd.to_numeric(merged["can_id_rx"], errors="coerce")

    numeric_after_merge = [
        "nominal_period_ns", "planned_release_ns", "wakeup_ns", "send_call_ns",
        "send_ret", "host_event_ns_tx", "host_event_ns_rx",
        "rx_kernel_ts_ns", "rx_user_read_ns"
    ]
    for col in numeric_after_merge:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

    # 3) 单帧指标
    merged["tx_lateness_ns"] = merged["send_call_ns"] - merged["planned_release_ns"]
    merged["tx_wakeup_delay_ns"] = merged["wakeup_ns"] - merged["planned_release_ns"]
    merged["tx2rx_kernel_latency_ns"] = merged["rx_kernel_ts_ns"] - merged["send_call_ns"]
    merged["rx_delivery_delay_ns"] = merged["rx_user_read_ns"] - merged["rx_kernel_ts_ns"]
    merged["end2end_user_lateness_ns"] = merged["rx_user_read_ns"] - merged["planned_release_ns"]
    merged["rx_received"] = np.where(merged["rx_user_read_ns"].notna(), 1, 0)

    # 4) LOSS 汇总
    if not loss_df.empty:
        if "loss_count" in loss_df.columns:
            loss_df["loss_count"] = pd.to_numeric(loss_df["loss_count"], errors="coerce").fillna(0)
        else:
            loss_df["loss_count"] = 0

        loss_sum_df = (
            loss_df.groupby(["link_id", "task_id"], as_index=False)["loss_count"]
            .sum()
            .rename(columns={"loss_count": "loss_frames"})
        )
    else:
        loss_sum_df = pd.DataFrame(columns=["link_id", "task_id", "loss_frames"])

    merged = pd.merge(
        merged,
        loss_sum_df,
        on=["link_id", "task_id"],
        how="left"
    )

    # 修复 FutureWarning：先转 numeric 再 fillna
    merged["loss_frames"] = pd.to_numeric(merged["loss_frames"], errors="coerce").fillna(0)

    # 5) 周期指标
    merged = merged.sort_values(["link_id", "task_id", "seq"]).reset_index(drop=True)

    merged["rx_kernel_period_ns"] = merged.groupby(["link_id", "task_id"])["rx_kernel_ts_ns"].diff()
    merged["rx_user_period_ns"] = merged.groupby(["link_id", "task_id"])["rx_user_read_ns"].diff()

    merged["rx_kernel_period_error_ns"] = merged["rx_kernel_period_ns"] - merged["nominal_period_ns"]
    merged["rx_user_period_error_ns"] = merged["rx_user_period_ns"] - merged["nominal_period_ns"]

    merged["abs_rx_kernel_period_error_ns"] = merged["rx_kernel_period_error_ns"].abs()
    merged["abs_rx_user_period_error_ns"] = merged["rx_user_period_error_ns"].abs()

    merged["rx_kernel_period_jitter_ns"] = merged["rx_kernel_period_error_ns"]
    merged["rx_user_period_jitter_ns"] = merged["rx_user_period_error_ns"]

    # 6) 输出 merged_analysis.csv
    wanted_cols = [
        "link_id", "task_id", "seq", "can_id",
        "nominal_period_ns",
        "planned_release_ns", "wakeup_ns", "send_call_ns",
        "rx_kernel_ts_ns", "rx_user_read_ns",
        "send_ret", "rx_received",
        "tx_wakeup_delay_ns", "tx_lateness_ns",
        "tx2rx_kernel_latency_ns", "rx_delivery_delay_ns", "end2end_user_lateness_ns",
        "rx_kernel_period_ns", "rx_user_period_ns",
        "rx_kernel_period_error_ns", "rx_user_period_error_ns",
        "abs_rx_kernel_period_error_ns", "abs_rx_user_period_error_ns",
        "rx_kernel_period_jitter_ns", "rx_user_period_jitter_ns",
        "loss_frames"
    ]

    for c in wanted_cols:
        if c not in merged.columns:
            merged[c] = np.nan

    merged_out = merged[wanted_cols].copy()
    merged_csv = out_dir / "merged_analysis.csv"
    merged_out.to_csv(merged_csv, index=False, encoding="utf-8-sig")

    # 7) 输出 summary_stats.csv
    summary_rows = []

    metrics_main = [
        "tx_wakeup_delay_ns",
        "tx_lateness_ns",
        "tx2rx_kernel_latency_ns",
        "rx_delivery_delay_ns",
        "end2end_user_lateness_ns",
        "rx_kernel_period_ns",
        "rx_user_period_ns",
        "rx_kernel_period_error_ns",
        "rx_user_period_error_ns",
        "abs_rx_kernel_period_error_ns",
        "abs_rx_user_period_error_ns",
        "rx_kernel_period_jitter_ns",
        "rx_user_period_jitter_ns",
        "loss_frames",
    ]

    # global
    summary_rows.extend(build_metric_rows(
        merged_out,
        scope="global",
        group_info={
            "link_id": np.nan,
            "task_id": np.nan,
            "nominal_period_ns": np.nan,
        },
        metrics=metrics_main
    ))

    # per_period
    if "nominal_period_ns" in merged_out.columns:
        for period_ns, g in merged_out.groupby("nominal_period_ns"):
            summary_rows.extend(build_metric_rows(
                g,
                scope="per_period",
                group_info={
                    "link_id": np.nan,
                    "task_id": np.nan,
                    "nominal_period_ns": period_ns,
                },
                metrics=metrics_main
            ))

    # per_task
    for (link_id, task_id), g in merged_out.groupby(["link_id", "task_id"]):
        nominal_period_ns = np.nan
        valid_periods = g["nominal_period_ns"].dropna() if "nominal_period_ns" in g.columns else pd.Series(dtype=float)
        if not valid_periods.empty:
            nominal_period_ns = valid_periods.iloc[0]

        summary_rows.extend(build_metric_rows(
            g,
            scope="per_task",
            group_info={
                "link_id": link_id,
                "task_id": task_id,
                "nominal_period_ns": nominal_period_ns,
            },
            metrics=metrics_main
        ))

    summary_df = pd.DataFrame(summary_rows)

    sort_cols = [c for c in ["scope", "nominal_period_ns", "link_id", "task_id", "metric"] if c in summary_df.columns]
    if sort_cols:
        summary_df = summary_df.sort_values(sort_cols).reset_index(drop=True)

    summary_csv = out_dir / "summary_stats.csv"
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")

    print(f"[INFO] written: {merged_csv}")
    print(f"[INFO] written: {summary_csv}")


if __name__ == "__main__":
    main()
