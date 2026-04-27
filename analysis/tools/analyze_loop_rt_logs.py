#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import math
import os
import statistics
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def safe_mean(x):
    if len(x) == 0:
        return None
    return statistics.mean(x)


def safe_std(x):
    if len(x) < 2:
        return 0.0
    return statistics.stdev(x)


def percentile(data, p):
    if len(data) == 0:
        return None
    return float(np.percentile(np.array(data, dtype=np.float64), p))


def describe(values):
    vals = [float(v) for v in values if pd.notna(v)]
    if len(vals) == 0:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "p50": None,
            "p90": None,
            "p99": None,
        }
    return {
        "count": len(vals),
        "min": min(vals),
        "max": max(vals),
        "mean": safe_mean(vals),
        "std": safe_std(vals),
        "p50": percentile(vals, 50),
        "p90": percentile(vals, 90),
        "p99": percentile(vals, 99),
    }


def add_summary(summary_rows, scope, link_id, task_id, metric_name, values):
    d = describe(values)
    summary_rows.append({
        "scope": scope,
        "link_id": link_id,
        "task_id": task_id,
        "metric": metric_name,
        "count": d["count"],
        "min_ns": "" if d["min"] is None else int(round(d["min"])),
        "max_ns": "" if d["max"] is None else int(round(d["max"])),
        "mean_ns": "" if d["mean"] is None else int(round(d["mean"])),
        "std_ns": "" if d["std"] is None else int(round(d["std"])),
        "p50_ns": "" if d["p50"] is None else int(round(d["p50"])),
        "p90_ns": "" if d["p90"] is None else int(round(d["p90"])),
        "p99_ns": "" if d["p99"] is None else int(round(d["p99"])),
    })


def plot_hist(data, title, xlabel, out_png):
    data = [x for x in data if pd.notna(x)]
    if len(data) == 0:
        return
    plt.figure(figsize=(10, 6))
    plt.hist(data, bins=60)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(out_png, dpi=150)
    plt.close()


def plot_series(x, y, title, xlabel, ylabel, out_png):
    if len(x) == 0 or len(y) == 0:
        return
    plt.figure(figsize=(12, 6))
    plt.plot(x, y, linewidth=1.0)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_png, dpi=150)
    plt.close()


def load_tx_csv(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    numeric_cols = [
        "host_event_ns", "link_id", "task_id", "can_id", "seq",
        "nominal_period_ns", "planned_release_ns", "wakeup_ns", "send_call_ns",
        "send_ret", "cpu_id", "sched_policy", "sched_priority"
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_rx_csv(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    numeric_cols = [
        "host_event_ns", "link_id", "task_id", "can_id", "seq", "dlc",
        "rx_kernel_ts_ns", "rx_user_read_ns", "loss_count",
        "cpu_id", "sched_policy", "sched_priority"
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def build_tx_metrics(tx_df):
    tx = tx_df.copy()
    tx["tx_wakeup_latency_ns"] = tx["wakeup_ns"] - tx["planned_release_ns"]
    tx["tx_path_delay_ns"] = tx["send_call_ns"] - tx["wakeup_ns"]
    tx["tx_lateness_ns"] = tx["send_call_ns"] - tx["planned_release_ns"]
    return tx


def build_rx_metrics(rx_df):
    rx = rx_df.copy()
    rx = rx[rx["event_type"] == "RX"].copy()
    rx = rx.sort_values(["link_id", "task_id", "seq"]).reset_index(drop=True)

    all_rows = []

    for (link_id, task_id), g in rx.groupby(["link_id", "task_id"]):
        g = g.sort_values("seq").reset_index(drop=True)

        prev_seq = None
        prev_kernel = None
        prev_user = None

        for i in range(len(g)):
            row = g.loc[i].to_dict()
            row["seq_gap"] = np.nan
            row["rx_kernel_actual_period_ns"] = np.nan
            row["rx_kernel_period_jitter_ns"] = np.nan
            row["rx_user_actual_period_ns"] = np.nan
            row["rx_user_period_jitter_ns"] = np.nan
            row["continuous_seq"] = 0

            curr_seq = int(g.loc[i, "seq"])
            curr_kernel = g.loc[i, "rx_kernel_ts_ns"]
            curr_user = g.loc[i, "rx_user_read_ns"]

            if prev_seq is not None:
                seq_gap = curr_seq - prev_seq
                row["seq_gap"] = seq_gap

                if seq_gap == 1:
                    row["continuous_seq"] = 1

                    nominal = None
                    # 名义周期在 RX 里没有，后面 merge TX 后再补
                    if prev_kernel is not None and pd.notna(curr_kernel) and pd.notna(prev_kernel):
                        row["rx_kernel_actual_period_ns"] = curr_kernel - prev_kernel
                    if prev_user is not None and pd.notna(curr_user) and pd.notna(prev_user):
                        row["rx_user_actual_period_ns"] = curr_user - prev_user

            prev_seq = curr_seq
            prev_kernel = curr_kernel
            prev_user = curr_user
            all_rows.append(row)

    return pd.DataFrame(all_rows)


def merge_tx_rx(tx_df, rx_metrics_df):
    keys = ["link_id", "task_id", "seq"]
    tx_cols = [
        "link_id", "task_id", "seq", "can_id", "ifname", "thread_name",
        "nominal_period_ns", "planned_release_ns", "wakeup_ns", "send_call_ns",
        "tx_wakeup_latency_ns", "tx_path_delay_ns", "tx_lateness_ns"
    ]
    tx_sub = tx_df[tx_cols].copy()

    merged = pd.merge(
        rx_metrics_df,
        tx_sub,
        on=keys,
        how="left",
        suffixes=("_rx", "_tx")
    )

    merged["tx2rx_kernel_latency_ns"] = merged["rx_kernel_ts_ns"] - merged["send_call_ns"]
    merged["rx_delivery_delay_ns"] = merged["rx_user_read_ns"] - merged["rx_kernel_ts_ns"]
    merged["end2end_user_lateness_ns"] = merged["rx_user_read_ns"] - merged["planned_release_ns"]

    # 连续样本才计算 jitter
    merged["rx_kernel_period_jitter_ns"] = np.where(
        merged["continuous_seq"] == 1,
        merged["rx_kernel_actual_period_ns"] - merged["nominal_period_ns"],
        np.nan
    )
    merged["rx_user_period_jitter_ns"] = np.where(
        merged["continuous_seq"] == 1,
        merged["rx_user_actual_period_ns"] - merged["nominal_period_ns"],
        np.nan
    )

    return merged


def collect_loss_stats(rx_df):
    loss_df = rx_df[rx_df["event_type"] == "LOSS"].copy()
    if loss_df.empty:
        return pd.DataFrame(columns=["link_id", "task_id", "loss_events", "loss_frames"])

    grouped = loss_df.groupby(["link_id", "task_id"], as_index=False).agg(
        loss_events=("loss_count", "count"),
        loss_frames=("loss_count", "sum")
    )
    return grouped


def build_summary(merged_df, loss_stats_df):
    summary_rows = []

    metrics = [
        "rx_kernel_actual_period_ns",
        "rx_kernel_period_jitter_ns",
        "rx_user_actual_period_ns",
        "rx_user_period_jitter_ns",
        "tx_wakeup_latency_ns",
        "tx_path_delay_ns",
        "tx_lateness_ns",
        "tx2rx_kernel_latency_ns",
        "rx_delivery_delay_ns",
        "end2end_user_lateness_ns",
    ]

    for m in metrics:
        add_summary(summary_rows, "global", "ALL", "ALL", m, merged_df[m].dropna().tolist())

    for (link_id, task_id), g in merged_df.groupby(["link_id", "task_id"]):
        for m in metrics:
            add_summary(summary_rows, "per_task", int(link_id), int(task_id), m, g[m].dropna().tolist())

    summary_df = pd.DataFrame(summary_rows)

    if not loss_stats_df.empty:
        loss_rows = []
        for _, r in loss_stats_df.iterrows():
            loss_rows.append({
                "scope": "per_task",
                "link_id": int(r["link_id"]),
                "task_id": int(r["task_id"]),
                "metric": "loss_frames",
                "count": int(r["loss_events"]),
                "min_ns": "",
                "max_ns": int(r["loss_frames"]),
                "mean_ns": "",
                "std_ns": "",
                "p50_ns": "",
                "p90_ns": "",
                "p99_ns": "",
            })
        summary_df = pd.concat([summary_df, pd.DataFrame(loss_rows)], ignore_index=True)

    return summary_df


def save_plots(merged_df, out_dir):
    plots_dir = Path(out_dir) / "plots"
    ensure_dir(plots_dir)

    plot_hist(
        merged_df["rx_kernel_period_jitter_ns"].dropna().tolist(),
        "Global RX Kernel Period Jitter Histogram",
        "rx_kernel_period_jitter_ns",
        plots_dir / "global_rx_kernel_jitter_hist.png"
    )

    plot_hist(
        merged_df["rx_user_period_jitter_ns"].dropna().tolist(),
        "Global RX User Period Jitter Histogram",
        "rx_user_period_jitter_ns",
        plots_dir / "global_rx_user_jitter_hist.png"
    )

    plot_hist(
        merged_df["tx_lateness_ns"].dropna().tolist(),
        "Global TX Lateness Histogram",
        "tx_lateness_ns",
        plots_dir / "global_tx_lateness_hist.png"
    )

    for (link_id, task_id), g in merged_df.groupby(["link_id", "task_id"]):
        g = g.sort_values("seq")

        prefix = f"link{int(link_id)}_task{int(task_id)}"

        plot_series(
            g["seq"].tolist(),
            g["rx_kernel_period_jitter_ns"].tolist(),
            f"{prefix} RX Kernel Period Jitter vs Seq",
            "seq",
            "rx_kernel_period_jitter_ns",
            plots_dir / f"{prefix}_rx_kernel_period_jitter_vs_seq.png"
        )

        plot_series(
            g["seq"].tolist(),
            g["rx_user_period_jitter_ns"].tolist(),
            f"{prefix} RX User Period Jitter vs Seq",
            "seq",
            "rx_user_period_jitter_ns",
            plots_dir / f"{prefix}_rx_user_period_jitter_vs_seq.png"
        )

        plot_series(
            g["seq"].tolist(),
            g["tx_lateness_ns"].tolist(),
            f"{prefix} TX Lateness vs Seq",
            "seq",
            "tx_lateness_ns",
            plots_dir / f"{prefix}_tx_lateness_vs_seq.png"
        )

        plot_series(
            g["seq"].tolist(),
            g["tx2rx_kernel_latency_ns"].tolist(),
            f"{prefix} TX to RX Kernel Latency vs Seq",
            "seq",
            "tx2rx_kernel_latency_ns",
            plots_dir / f"{prefix}_tx2rx_kernel_latency_vs_seq.png"
        )


def main():
    parser = argparse.ArgumentParser(description="Analyze Pi CAN loop RT tx/rx logs.")
    parser.add_argument("--tx-log", required=True, help="Path to tx_log.csv")
    parser.add_argument("--rx-log", required=True, help="Path to rx_log.csv")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    args = parser.parse_args()

    ensure_dir(args.out_dir)

    print("[INFO] loading tx log ...")
    tx_df = load_tx_csv(args.tx_log)
    print(f"[INFO] tx rows: {len(tx_df)}")

    print("[INFO] loading rx log ...")
    rx_df = load_rx_csv(args.rx_log)
    print(f"[INFO] rx rows: {len(rx_df)}")

    tx_metrics_df = build_tx_metrics(tx_df)
    rx_metrics_df = build_rx_metrics(rx_df)
    merged_df = merge_tx_rx(tx_metrics_df, rx_metrics_df)
    loss_stats_df = collect_loss_stats(rx_df)
    summary_df = build_summary(merged_df, loss_stats_df)

    merged_csv = Path(args.out_dir) / "merged_analysis.csv"
    rx_metrics_csv = Path(args.out_dir) / "rx_period_metrics.csv"
    summary_csv = Path(args.out_dir) / "summary_stats.csv"
    loss_csv = Path(args.out_dir) / "loss_stats.csv"

    merged_df.to_csv(merged_csv, index=False, encoding="utf-8-sig")
    rx_metrics_df.to_csv(rx_metrics_csv, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    loss_stats_df.to_csv(loss_csv, index=False, encoding="utf-8-sig")

    save_plots(merged_df, args.out_dir)

    print("\n========== Global Summary ==========")
    for _, row in summary_df[summary_df["scope"] == "global"].iterrows():
        print(
            f"{row['metric']:28s} "
            f"count={int(row['count']):6d} "
            f"min={str(row['min_ns']):>12s} "
            f"max={str(row['max_ns']):>12s} "
            f"mean={str(row['mean_ns']):>12s} "
            f"p99={str(row['p99_ns']):>12s}"
        )

    if not loss_stats_df.empty:
        print("\n========== Loss Summary ==========")
        print(loss_stats_df.to_string(index=False))

    print("\n[INFO] output files:")
    print(f"  merged analysis : {merged_csv}")
    print(f"  rx metrics      : {rx_metrics_csv}")
    print(f"  summary stats   : {summary_csv}")
    print(f"  loss stats      : {loss_csv}")
    print(f"  plots dir       : {Path(args.out_dir) / 'plots'}")


if __name__ == "__main__":
    main()
