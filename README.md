# rpi_can_loop_rt

树莓派 4 通道 SocketCAN 双链路双向实时性实验程序。

## 物理连接
- can00 <-> can10
- can01 <-> can11

## 功能
- 四个方向同时周期发送：
  - can00 -> can10
  - can10 -> can00
  - can01 -> can11
  - can11 -> can01
- 四个接收线程持续接收
- 接收采用 `recvmsg()` + `SO_TIMESTAMPNS`
- payload 内嵌：
  - seq
  - task_id
  - link_id
- 异步 logger 写盘
- 支持调度策略、优先级、绑核、stress

## 构建
```bash
mkdir -p build
cd build
cmake ..
make -j
```

## 运行前配置 CAN
例如：

```bash
sudo ip link set can00 down
sudo ip link set can01 down
sudo ip link set can10 down
sudo ip link set can11 down

sudo ip link set can00 up type can bitrate 1000000
sudo ip link set can01 up type can bitrate 1000000
sudo ip link set can10 up type can bitrate 1000000
sudo ip link set can11 up type can bitrate 1000000
```

## 运行示例

### 普通调度
```bash
./rpi_can_loop_rt --duration 20 --out-dir ../output/run_other
```

### FIFO 调度 + 绑核
```bash
sudo ./rpi_can_loop_rt \
  --duration 20 \
  --out-dir ../output/run_fifo \
  --policy fifo \
  --tx-prio 80 \
  --rx-prio 70 \
  --cpu-map 0,1,2,3,0,1,2,3
```

### 加 CPU 干扰
```bash
sudo ./rpi_can_loop_rt \
  --duration 20 \
  --out-dir ../output/run_fifo_stress \
  --policy fifo \
  --tx-prio 80 \
  --rx-prio 70 \
  --cpu-map 0,1,2,3,0,1,2,3 \
  --stress-threads 2 \
  --stress-cpu-start 2
```

## 输出文件
- `tx_log.csv`
- `rx_log.csv`
- `info_log.csv`

## TX 日志字段
- `link_id`
- `task_id`
- `ifname`
- `thread_name`
- `can_id`
- `seq`
- `nominal_period_ns`
- `planned_release_ns`
- `wakeup_ns`
- `send_call_ns`
- `send_ret`
- `cpu_id`
- `sched_policy`
- `sched_priority`

## RX 日志字段
- `link_id`
- `task_id`
- `ifname`
- `thread_name`
- `can_id`
- `seq`
- `dlc`
- `rx_kernel_ts_ns`
- `rx_user_read_ns`
- `loss_count`
- `cpu_id`
- `sched_policy`
- `sched_priority`

## 方法学说明
### 主指标
建议优先分析：
- `rx_kernel_period_jitter_ns`
- `rx_user_period_jitter_ns`

### 原因解释指标
- `tx_wakeup_latency_ns`
- `tx_path_delay_ns = send_call_ns - wakeup_ns`
- `tx_lateness_ns = send_call_ns - planned_release_ns`
- `tx2rx_kernel_latency_ns = rx_kernel_ts_ns - send_call_ns`
- `rx_delivery_delay_ns = rx_user_read_ns - rx_kernel_ts_ns`
- `end2end_user_lateness_ns = rx_user_read_ns - planned_release_ns`

### 丢帧处理
仅当 `seq(k) - seq(k-1) = 1` 时，才把该样本纳入周期 jitter 统计。  
若 `seq` 跳变大于 1，则记为丢帧事件，并将该区间从正常 jitter 统计中剔除。
