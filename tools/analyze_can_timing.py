#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import math
import os
import re
import statistics
from collections import defaultdict


def parse_int_auto(s):
    s = str(s).strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(float(s))


def parse_busmaster_time_to_us(s):
    """
    BUSMASTER 时间格式示例:
    00:00:08:0939
    解释为:
    HH:MM:SS:ffff
    其中最后 4 位按 1/10000 秒处理，即 0.1 ms = 100 us 分辨率
    总返回值: 微秒 us

    例如:
    00:00:08:0939 = 8.0939 s = 8,093,900 us
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
    """
    最小二乘拟合:
    y = a*x + b
    """
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
    """
    解析类似:
    00:00:08:0939 Rx 1 0x100 s 8 00 00 00 00 00 01 01 00
    """
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

        # 取前 4 字节 little-endian 作为 seq
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


def build_unique_map(rows, key_fields):
    tmp = defaultdict(list)
    for r in rows:
        key = tuple(r[k] for k in key_fields)
        tmp[key].append(r)
    return tmp


def pick_best_matches(internal_rows, bus_rows):
    """
    按 (can_id, seq) 匹配。
    若存在重复，按出现顺序一一配对。
    """
    internal_map = build_unique_map(internal_rows, ["can_id", "seq"])
    bus_map = build_unique_map(bus_rows, ["can_id", "seq"])

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


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main():
    parser = argparse.ArgumentParser(description="Analyze internal CAN timing CSV and BUSMASTER CAN log.")
    parser.add_argument("--internal-csv", required=True, help="Program internal CSV log path")
    parser.add_argument("--bus-log", required=True, help="BUSMASTER CAN log path")
    parser.add_argument("--out-dir", default="analysis_output", help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

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
        print("[ERR] less than 2 matched samples, cannot perform time alignment.")
        print("Possible reasons:")
        print("  1) bus log payload format differs from internal sender format")
        print("  2) seq extraction mismatch")
        print("  3) bus log file contains too few matching frames")
        return

    # 用 bus_time_us -> send_call_ns 做拟合
    fit_x = [b["bus_time_us"] for (_, b) in matched]
    fit_y = [i["send_call_ns"] for (i, _) in matched]

    a, b = linear_fit(fit_x, fit_y)
    print(f"[INFO] alignment model: internal_ns ~= {a:.9f} * bus_us + {b:.3f}")

    matched_rows = []
    global_wakeup_latency = []
    global_send_path_delay = []
    global_app_lateness = []
    global_bus_queue_delay = []
    global_bus_lateness = []
    global_alignment_residual = []

    per_can = defaultdict(lambda: {
        "wakeup_latency_ns": [],
        "send_path_delay_ns": [],
        "app_lateness_ns": [],
        "bus_queue_delay_ns": [],
        "bus_lateness_ns": [],
        "alignment_residual_ns": [],
    })

    for irow, brow in matched:
        bus_aligned_ns = a * brow["bus_time_us"] + b
        bus_queue_delay_ns = bus_aligned_ns - irow["send_call_ns"]
        bus_lateness_ns = bus_aligned_ns - irow["planned_release_ns"]

        # 拟合残差，用于衡量时基对齐误差
        alignment_residual_ns = bus_aligned_ns - irow["send_call_ns"]

        out = {
            "exp_id": irow["exp_id"],
            "thread_name": irow["thread_name"],
            "ifname": irow["ifname"],
            "can_id": hex(irow["can_id"]),
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
            "channel": brow["channel"],
            "bus_direction": brow["direction"],
            "dlc": brow["dlc"],
            "bus_data_hex": " ".join(f"{x:02X}" for x in brow["data_bytes"]),
        }
        matched_rows.append(out)

        cid = irow["can_id"]
        per_can[cid]["wakeup_latency_ns"].append(irow["wakeup_latency_ns"])
        per_can[cid]["send_path_delay_ns"].append(irow["send_path_delay_ns"])
        per_can[cid]["app_lateness_ns"].append(irow["app_lateness_ns"])
        per_can[cid]["bus_queue_delay_ns"].append(bus_queue_delay_ns)
        per_can[cid]["bus_lateness_ns"].append(bus_lateness_ns)
        per_can[cid]["alignment_residual_ns"].append(alignment_residual_ns)

        global_wakeup_latency.append(irow["wakeup_latency_ns"])
        global_send_path_delay.append(irow["send_path_delay_ns"])
        global_app_lateness.append(irow["app_lateness_ns"])
        global_bus_queue_delay.append(bus_queue_delay_ns)
        global_bus_lateness.append(bus_lateness_ns)
        global_alignment_residual.append(alignment_residual_ns)

    matched_csv = os.path.join(args.out_dir, "matched_timing.csv")
    write_csv(
        matched_csv,
        [
            "exp_id", "thread_name", "ifname", "can_id", "seq", "nominal_period_ns",
            "planned_release_ns", "wakeup_ns", "send_call_ns",
            "bus_time_us", "bus_time_aligned_ns",
            "wakeup_latency_ns", "send_path_delay_ns", "app_lateness_ns",
            "bus_queue_delay_ns", "bus_lateness_ns",
            "channel", "bus_direction", "dlc", "bus_data_hex",
        ],
        matched_rows
    )

    summary_rows = []

    def add_summary(scope, can_id, metric_name, values):
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

    add_summary("global", "ALL", "wakeup_latency_ns", global_wakeup_latency)
    add_summary("global", "ALL", "send_path_delay_ns", global_send_path_delay)
    add_summary("global", "ALL", "app_lateness_ns", global_app_lateness)
    add_summary("global", "ALL", "bus_queue_delay_ns", global_bus_queue_delay)
    add_summary("global", "ALL", "bus_lateness_ns", global_bus_lateness)
    add_summary("global", "ALL", "alignment_residual_ns", global_alignment_residual)

    for cid in sorted(per_can.keys()):
        c = per_can[cid]
        add_summary("per_can_id", hex(cid), "wakeup_latency_ns", c["wakeup_latency_ns"])
        add_summary("per_can_id", hex(cid), "send_path_delay_ns", c["send_path_delay_ns"])
        add_summary("per_can_id", hex(cid), "app_lateness_ns", c["app_lateness_ns"])
        add_summary("per_can_id", hex(cid), "bus_queue_delay_ns", c["bus_queue_delay_ns"])
        add_summary("per_can_id", hex(cid), "bus_lateness_ns", c["bus_lateness_ns"])
        add_summary("per_can_id", hex(cid), "alignment_residual_ns", c["alignment_residual_ns"])

    summary_csv = os.path.join(args.out_dir, "summary_stats.csv")
    write_csv(
        summary_csv,
        [
            "scope", "can_id", "metric", "count",
            "min_ns", "max_ns", "mean_ns", "std_ns",
            "p50_ns", "p90_ns", "p99_ns"
        ],
        summary_rows
    )

    unmatched_internal_csv = os.path.join(args.out_dir, "unmatched_internal.csv")
    write_csv(
        unmatched_internal_csv,
        [
            "exp_id", "thread_name", "ifname", "can_id", "seq",
            "nominal_period_ns", "planned_release_ns", "wakeup_ns",
            "send_call_ns", "send_ret", "cpu_id", "sched_policy",
            "sched_priority", "wakeup_latency_ns", "send_path_delay_ns",
            "app_lateness_ns",
        ],
        [
            {
                **r,
                "can_id": hex(r["can_id"]),
            } for r in unmatched_internal
        ]
    )

    unmatched_bus_csv = os.path.join(args.out_dir, "unmatched_bus.csv")
    write_csv(
        unmatched_bus_csv,
        ["bus_time_us", "direction", "channel", "can_id", "type", "dlc", "seq", "data_bytes"],
        [
            {
                "bus_time_us": r["bus_time_us"],
                "direction": r["direction"],
                "channel": r["channel"],
                "can_id": hex(r["can_id"]),
                "type": r["type"],
                "dlc": r["dlc"],
                "seq": r["seq"],
                "data_bytes": " ".join(f"{x:02X}" for x in r["data_bytes"]),
            } for r in unmatched_bus
        ]
    )

    print("\n========== Analysis Summary ==========")
    for metric_name, values in [
        ("wakeup_latency_ns", global_wakeup_latency),
        ("send_path_delay_ns", global_send_path_delay),
        ("app_lateness_ns", global_app_lateness),
        ("bus_queue_delay_ns", global_bus_queue_delay),
        ("bus_lateness_ns", global_bus_lateness),
        ("alignment_residual_ns", global_alignment_residual),
    ]:
        d = describe(values)
        print(
            f"{metric_name:22s} "
            f"count={d['count']:6d} "
            f"min={int(round(d['min'])):10d} "
            f"max={int(round(d['max'])):10d} "
            f"mean={int(round(d['mean'])):10d} "
            f"p99={int(round(d['p99'])):10d}"
        )

    print("\n[INFO] output files:")
    print(f"  matched details : {matched_csv}")
    print(f"  summary stats   : {summary_csv}")
    print(f"  unmatched int   : {unmatched_internal_csv}")
    print(f"  unmatched bus   : {unmatched_bus_csv}")


if __name__ == "__main__":
    main()
