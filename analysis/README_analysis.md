# Pi CAN Loop RT 日志分析

本目录用于分析树莓派双链路双向 CAN 实验程序生成的日志：

- `tx_log.csv`
- `rx_log.csv`

并输出：

- 接收周期波动统计
- 发送调度迟到统计
- 端到端延迟统计
- 丢帧统计
- PNG 图表

## 依赖安装

建议 Python 3.10+

```bash
pip install -r requirements.txt
```

## Windows 运行

修改 `run_analysis_windows.bat` 中的路径后，直接运行：

```bash
run_analysis_windows.bat
```

或者手工运行：

```bash
python tools\analyze_loop_rt_logs.py ^
  --tx-log ..\output\run_other\tx_log.csv ^
  --rx-log ..\output\run_other\rx_log.csv ^
  --out-dir results\run_other_analysis
```

## 输出文件

- `merged_analysis.csv`
- `rx_period_metrics.csv`
- `summary_stats.csv`
- `loss_stats.csv`
- `plots/*.png`

## 主指标

### 1. RX Kernel Period Jitter
更接近“报文到达内核接收路径”的时间波动：

- `rx_kernel_actual_period_ns`
- `rx_kernel_period_jitter_ns`

### 2. RX User Period Jitter
反映“应用实际感知到”的时间波动：

- `rx_user_actual_period_ns`
- `rx_user_period_jitter_ns`

## 原因解释指标

- `tx_wakeup_latency_ns`
- `tx_path_delay_ns`
- `tx_lateness_ns`
- `tx2rx_kernel_latency_ns`
- `rx_delivery_delay_ns`
- `end2end_user_lateness_ns`

## 丢帧处理

仅当连续两个样本满足：

- `seq(k) - seq(k-1) = 1`

时，才计算周期 jitter。

如果存在跳号，则该区间视为丢帧，不纳入正常 jitter 统计。
