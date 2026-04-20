#ifndef APP_HPP
#define APP_HPP

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

#include <pthread.h>
#include <linux/can.h>

struct CanTask {
    uint32_t can_id = 0;
    uint32_t period_ns = 10000000;   // 默认 10ms
    uint8_t dlc = 8;
    bool observed = false;

    uint32_t seq = 0;
    uint64_t next_release_ns = 0;
};

struct ThreadConfig {
    std::string name;
    int sched_policy = 0;
    int sched_priority = 0;
    int cpu_core = -1;               // -1 表示不绑定
};

struct StressConfig {
    int thread_count = 0;
    int busy_ratio = 100;            // 100 表示一直忙
    std::vector<int> cpu_cores;
};

struct TxRecord {
    uint64_t exp_id = 0;
    std::string thread_name;
    std::string ifname;

    uint32_t can_id = 0;
    uint32_t seq = 0;
    uint32_t nominal_period_ns = 0;

    uint64_t planned_release_ns = 0;
    uint64_t wakeup_ns = 0;
    uint64_t send_call_ns = 0;

    int send_ret = 0;
    int cpu_id = -1;
    int sched_policy = 0;
    int sched_priority = 0;
};

struct AppConfig {
    uint64_t exp_id = 1;
    int duration_sec = 10;

    std::string can0_ifname = "can00";
    std::string can1_ifname = "can01";

    ThreadConfig can0_thread_cfg;
    ThreadConfig can1_thread_cfg;
    StressConfig stress_cfg;

    std::vector<CanTask> can0_tasks;
    std::vector<CanTask> can1_tasks;

    std::string log_path = "output/output.csv";
};

struct TxWorkerArgs {
    uint64_t exp_id;
    std::string ifname;
    ThreadConfig thread_cfg;
    std::vector<CanTask> tasks;
};

struct StressWorkerArgs {
    int index = 0;
    int cpu_core = -1;
    int busy_ratio = 100;
};

#endif
