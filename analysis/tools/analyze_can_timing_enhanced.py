#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import math
import os
import re
import statistics
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def parse_int_auto(s):
    s = str(s).strip()
    if s == "":
        return 0
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(float(s))


def parse_busmaster_time_to_us(s):
    """
    BUSMASTER 时间格式示例:
    00:00:08:0939

    这里按:
    HH:MM:SS:ffff
    最后 4 位表示 1/10000 秒
    即 0.1ms = 100us
    """
    s = s.strip()
    m = re.match(r"(\d+):(\d+):(\d+):(\d+)", s)
    if not m:
        raise ValueError(f"Invalid BUSMASTER time format: {s}")
    hh, mm, ss, frac = map(int, m.groups())
    total_us = ((hh * 3600 + mm * 60 + ss) * 1_000_000) + frac * 100
    return total_us


def safe_mean(x):
    return statistics.mean(x) if x else None


def safe_stdev(x):
    if len(x) < 2:
        return 0.0
    return statistics.stdev(x)


def percentile(data, p):
    if not data:
        return None
    xs = sorted(data)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    d0 = xs[f] * (c - k)
    d1 = xs[c] * (k - f)
    return d0 + d1


def describe(values):
    if not values:
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
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": safe_mean(values),
        "std": safe_stdev(values),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p99": percentile(values, 99),
    }


def linear_fit(xs, ys):
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("Need at least 2 matched samples for linear fit")

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)

    if den == 0:
        raise ValueError("Degenerate fit: all x are identical")

    a = num / den
    b = mean_y - a * mean_x
    return a, b


def read_internal_csv(path):
    rows = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                row = {
                    "exp_id": parse_int_auto(r["exp_id"]),
                    "thread_name": r["thread_name"].strip(),
                    "ifname": r["ifname"].strip(),
                    "can_id": parse_int_auto(r["can_id"]),
                    "seq": parse_int_auto(r["seq"]),
                    "nominal_period_ns": parse_int_auto(r["nominal_period_ns"]),
                    "planned_release_ns": parse_int_auto(r["planned_release_ns"]),
                    "wakeup_ns": parse_int_auto(r["wakeup_ns"]),
                    "send_call_ns": parse_int_auto(r["send_call_ns"]),
                    "send_ret": parse_int_auto(r["send_ret"]),
                    "cpu_id": parse_int_auto(r["cpu_id"]),
                    "sched_policy": parse_int_auto(r["sched_policy"]),
                    "sched_priority": parse_int_auto(r["sched_priority"]),
                }
                row["wakeup_latency_ns"] = row["wakeup_ns"] - row["planned_release_ns"]
                row["send_path_delay_ns"] = row["send_call_ns"] - row["wakeup_ns"]
                row["app_lateness_ns"] = row["send_call_ns"] - row["planned_release_ns"]
                rows.append(row)
            except Exception as e:
                print(f"[WARN] skip internal CSV row due to parse error: {e}; row={r}")
    return rows


def parse_busmaster_line(line):
    line = line.strip()
    if not line:
        return None
    if line.startswith("***"):
        return None

    parts = line.split()
    if len(parts) < 7:
        return None

    try:
        time_str = parts[0]
        direction = parts[1]
        channel = int(parts[2])
        can_id = int(parts[3], 16)
        frame_type = parts[4]
        dlc = int(parts[5])

        data_bytes = []
        for x in parts[6:6 + dlc]:
            data_bytes.append(int(x, 16))

        if len(data_bytes) != dlc:
            return None

        bus_time_us = parse_busmaster_time_to_us(time_str)

        if dlc >= 4:
            seq = (
                data_bytes[0]
                | (data_bytes[1] << 8)
                | (data_bytes[2] << 16)
                | (data_bytes[3] << 24)
            )
        else:
            seq = None

        return {
            "bus_time_us": bus_time_us,
            "direction": direction,
            "channel": channel,
            "can_id": can_id,
            "type": frame_type,
            "dlc": dlc,
            "data_bytes": data_bytes,
            "seq": seq,
        }
    except Exception:
        return None


def read_busmaster_log(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            item = parse_busmaster_line(line)
            if item is not None:
                rows.append(item)
    return rows


def build_map(rows, key_fields):
    tmp = defaultdict(list)
    for r in rows:
        key = tuple(r[k] for k in key_fields)
        tmp[key].append(r)
    return tmp


def pick_best_matches(internal_rows, bus_rows):
    internal_map = build_map(internal_rows, ["can_id", "seq"])
    bus_map = build_map(bus_rows, ["can_id", "seq"])

    matched = []
    unmatched_internal = []
    unmatched_bus = []

    all_keys = set(internal_map.keys()) | set(bus_map.keys())

    for key in sorted(all_keys):
        ilist = internal_map.get(key, [])
        blist = bus_map.get(key, [])

        n = min(len(ilist), len(blist))
        for i in range(n):
            matched.append((ilist[i], blist[i]))

        if len(ilist) > n:
            unmatched_internal.extend(ilist[n:])
        if len(blist) > n:
            unmatched_bus.extend(blist[n:])

    return matched, unmatched_internal, unmatched_bus


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def plot_hist(data, title, xlabel, out_png):
    if not data:
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


def add_summary(summary_rows, scope, can_id, metric_name, values):
    d = describe(values)
    summary_rows.append({
        "scope": scope,
        "can_id": can_id,
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


def calc_period_jitter(df, time_col, nominal_col="nominal_period_ns"):
    """
    对每个 can_id 单独计算相邻帧实际周期和 jitter
    """
    out_rows = []

    grouped = df.sort_values(["can_id", "seq"]).groupby("can_id")
    for can_id, g in grouped:
        g = g.sort_values("seq").reset_index(drop=True)
        for i in range(1, len(g)):
            prev_t = g.loc[i - 1, time_col]
            curr_t = g.loc[i, time_col]
            nominal = g.loc[i, nominal_col]
            actual_period = curr_t - prev_t
            jitter = actual_period - nominal
            out_rows.append({
                "can_id": g.loc[i, "can_id"],
                "seq": int(g.loc[i, "seq"]),
                "time_col": time_col,
                "nominal_period_ns": int(nominal),
                "actual_period_ns": int(round(actual_period)),
                "period_jitter_ns": int(round(jitter)),
            })
    return pd.DataFrame(out_rows)


def main():
    parser = argparse.ArgumentParser(description="Enhanced timing analysis for internal CSV and BUSMASTER log.")
    parser.add_argument("--internal-csv", required=True, help="Program internal CSV log path")
    parser.add_argument("--bus-log", required=True, help="BUSMASTER CAN log path")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    args = parser.parse_args()

    ensure_dir(args.out_dir)
    plots_dir = os.path.join(args.out_dir, "plots")
    ensure_dir(plots_dir)

    print("[INFO] reading internal CSV ...")
    internal_rows = read_internal_csv(args.internal_csv)
    print(f"[INFO] internal rows: {len(internal_rows)}")

    print("[INFO] reading BUSMASTER log ...")
    bus_rows = read_busmaster_log(args.bus_log)
    print(f"[INFO] bus rows: {len(bus_rows)}")

    print("[INFO] matching by (can_id, seq) ...")
    matched, unmatched_internal, unmatched_bus = pick_best_matches(internal_rows, bus_rows)

    print(f"[INFO] matched pairs      : {len(matched)}")
    print(f"[INFO] unmatched internal : {len(unmatched_internal)}")
    print(f"[INFO] unmatched bus      : {len(unmatched_bus)}")

    if len(matched) < 2:
        print("[ERR] less than 2 matched samples, cannot perform alignment")
        return

    fit_x = [b["bus_time_us"] for (_, b) in matched]
    fit_y = [i["send_call_ns"] for (i, _) in matched]

    a, b = linear_fit(fit_x, fit_y)
    print(f"[INFO] alignment model: internal_ns ~= {a:.9f} * bus_us + {b:.3f}")

    matched_rows = []
    per_can_metrics = defaultdict(lambda: defaultdict(list))

    global_metrics = {
        "wakeup_latency_ns": [],
        "send_path_delay_ns": [],
        "app_lateness_ns": [],
        "bus_queue_delay_ns": [],
        "bus_lateness_ns": [],
        "alignment_residual_ns": [],
    }

    for irow, brow in matched:
        bus_aligned_ns = a * brow["bus_time_us"] + b
        bus_queue_delay_ns = bus_aligned_ns - irow["send_call_ns"]
        bus_lateness_ns = bus_aligned_ns - irow["planned_release_ns"]
        alignment_residual_ns = bus_aligned_ns - irow["send_call_ns"]

        row = {
            "exp_id": irow["exp_id"],
            "thread_name": irow["thread_name"],
            "ifname": irow["ifname"],
            "can_id": irow["can_id"],
            "can_id_hex": hex(irow["can_id"]),
            "seq": irow["seq"],
            "nominal_period_ns": irow["nominal_period_ns"],
            "planned_release_ns": irow["planned_release_ns"],
            "wakeup_ns": irow["wakeup_ns"],
            "send_call_ns": irow["send_call_ns"],
            "bus_time_us": brow["bus_time_us"],
            "bus_time_aligned_ns": int(round(bus_aligned_ns)),
            "wakeup_latency_ns": irow["wakeup_latency_ns"],
            "send_path_delay_ns": irow["send_path_delay_ns"],
            "app_lateness_ns": irow["app_lateness_ns"],
            "bus_queue_delay_ns": int(round(bus_queue_delay_ns)),
            "bus_lateness_ns": int(round(bus_lateness_ns)),
            "alignment_residual_ns": int(round(alignment_residual_ns)),
            "channel": brow["channel"],
            "bus_direction": brow["direction"],
            "dlc": brow["dlc"],
            "bus_data_hex": " ".join(f"{x:02X}" for x in brow["data_bytes"]),
        }
        matched_rows.append(row)

        cid = irow["can_id"]
        for k in global_metrics.keys():
            global_metrics[k].append(row[k])
            per_can_metrics[cid][k].append(row[k])

    matched_df = pd.DataFrame(matched_rows)
    matched_df = matched_df.sort_values(["can_id", "seq"]).reset_index(drop=True)

    matched_csv = os.path.join(args.out_dir, "matched_timing.csv")
    matched_df.to_csv(matched_csv, index=False, encoding="utf-8-sig")

    internal_jitter_df = calc_period_jitter(matched_df, "send_call_ns")
    internal_jitter_df["can_id_hex"] = internal_jitter_df["can_id"].apply(hex)
    internal_jitter_csv = os.path.join(args.out_dir, "internal_period_jitter.csv")
    internal_jitter_df.to_csv(internal_jitter_csv, index=False, encoding="utf-8-sig")

    bus_jitter_df = calc_period_jitter(matched_df, "bus_time_aligned_ns")
    bus_jitter_df["can_id_hex"] = bus_jitter_df["can_id"].apply(hex)
    bus_jitter_csv = os.path.join(args.out_dir, "bus_period_jitter.csv")
    bus_jitter_df.to_csv(bus_jitter_csv, index=False, encoding="utf-8-sig")

    summary_rows = []

    for metric_name, values in global_metrics.items():
        add_summary(summary_rows, "global", "ALL", metric_name, values)

    for cid in sorted(per_can_metrics.keys()):
        for metric_name, values in per_can_metrics[cid].items():
            add_summary(summary_rows, "per_can_id", hex(cid), metric_name, values)

    if not internal_jitter_df.empty:
        for cid, g in internal_jitter_df.groupby("can_id_hex"):
            add_summary(summary_rows, "per_can_id", cid, "internal_period_jitter_ns", g["period_jitter_ns"].tolist())
        add_summary(summary_rows, "global", "ALL", "internal_period_jitter_ns", internal_jitter_df["period_jitter_ns"].tolist())

    if not bus_jitter_df.empty:
        for cid, g in bus_jitter_df.groupby("can_id_hex"):
            add_summary(summary_rows, "per_can_id", cid, "bus_period_jitter_ns", g["period_jitter_ns"].tolist())
        add_summary(summary_rows, "global", "ALL", "bus_period_jitter_ns", bus_jitter_df["period_jitter_ns"].tolist())

    summary_df = pd.DataFrame(summary_rows)
    summary_csv = os.path.join(args.out_dir, "summary_stats.csv")
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")

    unmatched_internal_csv = os.path.join(args.out_dir, "unmatched_internal.csv")
    pd.DataFrame(unmatched_internal).to_csv(unmatched_internal_csv, index=False, encoding="utf-8-sig")

    unmatched_bus_csv = os.path.join(args.out_dir, "unmatched_bus.csv")
    if unmatched_bus:
        tmp = []
        for r in unmatched_bus:
            rr = dict(r)
            rr["can_id_hex"] = hex(rr["can_id"])
            rr["data_bytes_hex"] = " ".join(f"{x:02X}" for x in rr["data_bytes"])
            tmp.append(rr)
        pd.DataFrame(tmp).to_csv(unmatched_bus_csv, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["bus_time_us", "direction", "channel", "can_id", "can_id_hex", "type", "dlc", "seq", "data_bytes_hex"]).to_csv(
            unmatched_bus_csv, index=False, encoding="utf-8-sig"
        )

    # 全局直方图
    plot_hist(global_metrics["wakeup_latency_ns"], "Global Wakeup Latency Histogram", "wakeup_latency_ns", os.path.join(plots_dir, "global_wakeup_latency_hist.png"))
    plot_hist(global_metrics["app_lateness_ns"], "Global App Lateness Histogram", "app_lateness_ns", os.path.join(plots_dir, "global_app_lateness_hist.png"))
    plot_hist(global_metrics["bus_lateness_ns"], "Global Bus Lateness Histogram", "bus_lateness_ns", os.path.join(plots_dir, "global_bus_lateness_hist.png"))

    # 每个 CAN ID 的时间序列图
    for cid, g in matched_df.groupby("can_id"):
        cid_hex = hex(cid)

        g1 = g.sort_values("seq")
        plot_series(
            g1["seq"].tolist(),
            g1["wakeup_latency_ns"].tolist(),
            f"{cid_hex} Wakeup Latency vs Seq",
            "seq",
            "wakeup_latency_ns",
            os.path.join(plots_dir, f"{cid_hex}_wakeup_latency_vs_seq.png")
        )

        plot_series(
            g1["seq"].tolist(),
            g1["app_lateness_ns"].tolist(),
            f"{cid_hex} App Lateness vs Seq",
            "seq",
            "app_lateness_ns",
            os.path.join(plots_dir, f"{cid_hex}_app_lateness_vs_seq.png")
        )

        plot_series(
            g1["seq"].tolist(),
            g1["bus_lateness_ns"].tolist(),
            f"{cid_hex} Bus Lateness vs Seq",
            "seq",
            "bus_lateness_ns",
            os.path.join(plots_dir, f"{cid_hex}_bus_lateness_vs_seq.png")
        )

    for cid, g in internal_jitter_df.groupby("can_id"):
        cid_hex = hex(cid)
        g = g.sort_values("seq")
        plot_series(
            g["seq"].tolist(),
            g["period_jitter_ns"].tolist(),
            f"{cid_hex} Internal Period Jitter vs Seq",
            "seq",
            "internal_period_jitter_ns",
            os.path.join(plots_dir, f"{cid_hex}_internal_period_jitter_vs_seq.png")
        )

    for cid, g in bus_jitter_df.groupby("can_id"):
        cid_hex = hex(cid)
        g = g.sort_values("seq")
        plot_series(
            g["seq"].tolist(),
            g["period_jitter_ns"].tolist(),
            f"{cid_hex} Bus Period Jitter vs Seq",
            "seq",
            "bus_period_jitter_ns",
            os.path.join(plots_dir, f"{cid_hex}_bus_period_jitter_vs_seq.png")
        )

    print("\n========== Analysis Summary ==========")
    for _, row in summary_df[summary_df["scope"] == "global"].iterrows():
        print(
            f"{row['metric']:26s} "
            f"count={int(row['count']):6d} "
            f"min={str(row['min_ns']):>12s} "
            f"max={str(row['max_ns']):>12s} "
            f"mean={str(row['mean_ns']):>12s} "
            f"p99={str(row['p99_ns']):>12s}"
        )

    print("\n[INFO] output files:")
    print(f"  matched details       : {matched_csv}")
    print(f"  internal jitter       : {internal_jitter_csv}")
    print(f"  bus jitter            : {bus_jitter_csv}")
    print(f"  summary stats         : {summary_csv}")
    print(f"  unmatched internal    : {unmatched_internal_csv}")
    print(f"  unmatched bus         : {unmatched_bus_csv}")
    print(f"  plots dir             : {plots_dir}")


if __name__ == "__main__":
    main()
