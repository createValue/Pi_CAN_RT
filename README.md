# rpi_can_loop_rt

任务调度及CPU亲和性对CAN总线实时性性能影响研究程序。
## 硬件环境：树莓派 4B + 2块CAN扩展卡
4通道 SocketCAN 双链路双向CAN。

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

## CAN 配置
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

### 1. 基线：普通调度
```bash
./rpi_can_loop_rt --duration 20 --out-dir ../output/run_other
```

### 2. FIFO
```bash
sudo ./rpi_can_loop_rt \
  --duration 20 \
  --out-dir ../output/run_fifo \
  --policy fifo \
  --tx-prio 80 \
  --rx-prio 70
```

### 3. RR
```bash
sudo ./rpi_can_loop_rt \
  --duration 20 \
  --out-dir ../output/run_rr \
  --policy rr \
  --tx-prio 80 \
  --rx-prio 70
```

### 4. FIFO + 绑核
```bash
sudo ./rpi_can_loop_rt \
  --duration 20 \
  --out-dir ../output/run_fifo_aff \
  --policy fifo \
  --tx-prio 80 \
  --rx-prio 70 \
  --cpu-map 0,1,2,3,0,1,2,3
```

### 5. FIFO + stress
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

## 日志文件
- `tx_log.csv`
- `rx_log.csv`
- `info_log.csv`

## 方法学说明

### 主指标
优先分析：
- `rx_kernel_period_jitter_ns`
- `rx_user_period_jitter_ns`

### 解释指标
- `tx_wakeup_latency_ns = wakeup_ns - planned_release_ns`
- `tx_path_delay_ns = send_call_ns - wakeup_ns`
- `tx_lateness_ns = send_call_ns - planned_release_ns`
- `tx2rx_kernel_latency_ns = rx_kernel_ts_ns - send_call_ns`
- `rx_delivery_delay_ns = rx_user_read_ns - rx_kernel_ts_ns`
- `end2end_user_lateness_ns = rx_user_read_ns - planned_release_ns`

### 丢帧处理
只在 `seq` 连续时统计周期 jitter。  
若 `seq` 跳变，则该区间视为丢帧，不纳入正常 jitter 统计。
