#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# Stage A: CPU 干扰强度标定脚本
# - CAN 收发线程不绑核
# - stress 线程不绑核
# - 调度策略使用 OTHER
# - 仅通过 stress_threads 增加系统负载
# ==========================================

# ---------- 基本配置 ----------
BIN="./rpi_can_loop_rt"
ROOT_OUT="./stageA_calibration_runs"
DURATION=20

POLICY="other"
TX_PRIO=0
RX_PRIO=0

# 8 个线程都不绑核
# 顺序:
# tx00,rx10,tx10,rx00,tx01,rx11,tx11,rx01
CPU_MAP="-1,-1,-1,-1,-1,-1,-1,-1"

# 标定档位
STRESS_LIST=(0 1 2 3 4 5 6)

# 每档重复次数
REPEATS=3

# ---------- 工具函数 ----------
timestamp() {
  date +"%Y%m%d_%H%M%S"
}

log_info() {
  echo "[INFO] $*"
}

cleanup_old() {
  pkill -9 -f rpi_can_loop_rt || true
  sleep 1
}

# ---------- 检查 ----------
if [[ ! -x "${BIN}" ]]; then
  echo "[ERROR] binary not found or not executable: ${BIN}"
  exit 1
fi

mkdir -p "${ROOT_OUT}"

RUN_TAG="$(timestamp)"
BATCH_DIR="${ROOT_OUT}/batch_${RUN_TAG}"
mkdir -p "${BATCH_DIR}"

log_info "Batch dir: ${BATCH_DIR}"

cat > "${BATCH_DIR}/batch_config.txt" <<EOF
run_tag=${RUN_TAG}
bin=${BIN}
duration=${DURATION}
policy=${POLICY}
tx_prio=${TX_PRIO}
rx_prio=${RX_PRIO}
cpu_map=${CPU_MAP}
can_binding=disabled
stress_binding=disabled
stress_list=${STRESS_LIST[*]}
repeats=${REPEATS}
EOF

# ---------- 执行 ----------
for stress in "${STRESS_LIST[@]}"; do
  for ((rep=1; rep<=REPEATS; rep++)); do
    RUN_NAME="stress_${stress}_rep_${rep}"
    OUT_DIR="${BATCH_DIR}/${RUN_NAME}"
    mkdir -p "${OUT_DIR}"

    log_info "========================================"
    log_info "Running: ${RUN_NAME}"
    log_info "Output : ${OUT_DIR}"
    log_info "========================================"

    cleanup_old

    cat > "${OUT_DIR}/run_config.txt" <<EOF
run_name=${RUN_NAME}
stress_threads=${stress}
repeat_index=${rep}
duration=${DURATION}
policy=${POLICY}
tx_prio=${TX_PRIO}
rx_prio=${RX_PRIO}
cpu_map=${CPU_MAP}
can_binding=disabled
stress_binding=disabled
start_time=$(date +"%F %T")
EOF

    CMD=(
      "${BIN}"
      --duration "${DURATION}"
      --out-dir "${OUT_DIR}"
      --policy "${POLICY}"
      --tx-prio "${TX_PRIO}"
      --rx-prio "${RX_PRIO}"
      --cpu-map "${CPU_MAP}"
    )

    if [[ "${stress}" -gt 0 ]]; then
      CMD+=(
        --stress-threads "${stress}"
      )
    fi

    log_info "Command: ${CMD[*]}"

    {
      echo "[CMD] ${CMD[*]}"
      "${CMD[@]}"
    } | tee "${OUT_DIR}/run_stdout.log"

    echo "end_time=$(date +"%F %T")" >> "${OUT_DIR}/run_config.txt"

    sleep 2
  done
done

cleanup_old

log_info "All Stage-A calibration runs completed."
log_info "Batch dir: ${BATCH_DIR}"
