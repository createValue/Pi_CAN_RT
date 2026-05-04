#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# Stage B: 调度策略 + CPU 绑定策略对比脚本
# - 固定 stress_threads = 4
# - 实施 B0 ~ B7 全部实验组
# - Raspberry Pi 4 核:
#     CPU0, CPU1 -> CAN 收发线程
#     CPU2, CPU3 -> stress 线程
# ==========================================

# ---------- 基本配置 ----------
BIN="./rpi_can_loop_rt"
ROOT_OUT="./stageB_sched_affinity_runs"
DURATION=20

# 固定 Stage B 干扰强度
STRESS_THREADS=4

# 实时调度优先级
FIFO_PRIO=80
RR_PRIO=80

# 线程顺序:
# tx00,rx10,tx10,rx00,tx01,rx11,tx11,rx01

# 不绑核
CPU_MAP_NONE="-1,-1,-1,-1,-1,-1,-1,-1"

# CAN 收发线程绑定到 CPU0,1
CPU_MAP_CAN01="0,1,0,1,0,1,0,1"

# stress 线程绑定起始核
# 注意：要求 main.cpp 已按建议修改为在 CPU2/3 之间轮转
STRESS_CPU_START=2

# 每组重复次数
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

get_group_config() {
  local group="$1"

  case "${group}" in
    B0)
      GROUP_NAME="B0_other_none"
      POLICY="other"
      TX_PRIO=0
      RX_PRIO=0
      CPU_MAP="${CPU_MAP_NONE}"
      CAN_BINDING="disabled"
      STRESS_BINDING="disabled"
      USE_STRESS_CPU_START=0
      ;;
    B1)
      GROUP_NAME="B1_other_separate"
      POLICY="other"
      TX_PRIO=0
      RX_PRIO=0
      CPU_MAP="${CPU_MAP_CAN01}"
      CAN_BINDING="enabled_cpu01"
      STRESS_BINDING="enabled_cpu23"
      USE_STRESS_CPU_START=1
      ;;
    B2)
      GROUP_NAME="B2_fifo_none"
      POLICY="fifo"
      TX_PRIO=${FIFO_PRIO}
      RX_PRIO=${FIFO_PRIO}
      CPU_MAP="${CPU_MAP_NONE}"
      CAN_BINDING="disabled"
      STRESS_BINDING="disabled"
      USE_STRESS_CPU_START=0
      ;;
    B3)
      GROUP_NAME="B3_fifo_separate"
      POLICY="fifo"
      TX_PRIO=${FIFO_PRIO}
      RX_PRIO=${FIFO_PRIO}
      CPU_MAP="${CPU_MAP_CAN01}"
      CAN_BINDING="enabled_cpu01"
      STRESS_BINDING="enabled_cpu23"
      USE_STRESS_CPU_START=1
      ;;
    B4)
      GROUP_NAME="B4_rr_none"
      POLICY="rr"
      TX_PRIO=${RR_PRIO}
      RX_PRIO=${RR_PRIO}
      CPU_MAP="${CPU_MAP_NONE}"
      CAN_BINDING="disabled"
      STRESS_BINDING="disabled"
      USE_STRESS_CPU_START=0
      ;;
    B5)
      GROUP_NAME="B5_rr_separate"
      POLICY="rr"
      TX_PRIO=${RR_PRIO}
      RX_PRIO=${RR_PRIO}
      CPU_MAP="${CPU_MAP_CAN01}"
      CAN_BINDING="enabled_cpu01"
      STRESS_BINDING="enabled_cpu23"
      USE_STRESS_CPU_START=1
      ;;
    B6)
      GROUP_NAME="B6_fifo_can_only"
      POLICY="fifo"
      TX_PRIO=${FIFO_PRIO}
      RX_PRIO=${FIFO_PRIO}
      CPU_MAP="${CPU_MAP_CAN01}"
      CAN_BINDING="enabled_cpu01"
      STRESS_BINDING="disabled"
      USE_STRESS_CPU_START=0
      ;;
    B7)
      GROUP_NAME="B7_rr_can_only"
      POLICY="rr"
      TX_PRIO=${RR_PRIO}
      RX_PRIO=${RR_PRIO}
      CPU_MAP="${CPU_MAP_CAN01}"
      CAN_BINDING="enabled_cpu01"
      STRESS_BINDING="disabled"
      USE_STRESS_CPU_START=0
      ;;
    *)
      echo "[ERROR] Unknown group: ${group}"
      exit 1
      ;;
  esac
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
stress_threads=${STRESS_THREADS}
repeats=${REPEATS}
fifo_prio=${FIFO_PRIO}
rr_prio=${RR_PRIO}
cpu_map_none=${CPU_MAP_NONE}
cpu_map_can01=${CPU_MAP_CAN01}
stress_cpu_start=${STRESS_CPU_START}
stageA_selected_stress=4
stageA_reason=significant_realtime_degradation_observed
groups=B0 B1 B2 B3 B4 B5 B6 B7
B0=other + none
B1=other + separate
B2=fifo + none
B3=fifo + separate
B4=rr + none
B5=rr + separate
B6=fifo + can_only
B7=rr + can_only
EOF

# ---------- 执行 ----------
GROUP_LIST=(B0 B1 B2 B3 B4 B5 B6 B7)

for group in "${GROUP_LIST[@]}"; do
  get_group_config "${group}"

  for ((rep=1; rep<=REPEATS; rep++)); do
    RUN_NAME="${GROUP_NAME}_rep_${rep}"
    OUT_DIR="${BATCH_DIR}/${RUN_NAME}"
    mkdir -p "${OUT_DIR}"

    log_info "========================================"
    log_info "Running: ${RUN_NAME}"
    log_info "Output : ${OUT_DIR}"
    log_info "========================================"

    cleanup_old

    cat > "${OUT_DIR}/run_config.txt" <<EOF
run_name=${RUN_NAME}
group=${group}
group_name=${GROUP_NAME}
stress_threads=${STRESS_THREADS}
repeat_index=${rep}
duration=${DURATION}
policy=${POLICY}
tx_prio=${TX_PRIO}
rx_prio=${RX_PRIO}
cpu_map=${CPU_MAP}
can_binding=${CAN_BINDING}
stress_binding=${STRESS_BINDING}
stress_cpu_start=$( [[ ${USE_STRESS_CPU_START} -eq 1 ]] && echo "${STRESS_CPU_START}" || echo "disabled" )
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
      --stress-threads "${STRESS_THREADS}"
    )

    if [[ "${USE_STRESS_CPU_START}" -eq 1 ]]; then
      CMD+=(
        --stress-cpu-start "${STRESS_CPU_START}"
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

log_info "All Stage-B scheduling/affinity runs completed."
log_info "Batch dir: ${BATCH_DIR}"
