#include "app.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <thread>
#include <vector>

#include <pthread.h>
#include <sched.h>
#include <time.h>
#include <unistd.h>

#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/types.h>

// =========================
// 全局变量
// =========================
static std::atomic<bool> g_stop{false};

static std::mutex g_log_mtx;
static std::condition_variable g_log_cv;
static std::vector<TxRecord> g_log_buffer;

// =========================
// 时间工具
// =========================
static uint64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}

static timespec ns_to_timespec(uint64_t ns) {
    timespec ts{};
    ts.tv_sec = ns / 1000000000ULL;
    ts.tv_nsec = ns % 1000000000ULL;
    return ts;
}

static void sleep_until_ns(uint64_t abs_ns) {
    timespec ts = ns_to_timespec(abs_ns);
    while (!g_stop.load()) {
        int ret = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr);
        if (ret == 0) break;
        if (ret != EINTR) break;
    }
}

// =========================
// 调度与 affinity
// =========================
static int parse_sched_policy(const std::string& s) {
    if (s == "other") return SCHED_OTHER;
    if (s == "fifo")  return SCHED_FIFO;
    if (s == "rr")    return SCHED_RR;
    return SCHED_OTHER;
}

static const char* sched_policy_name(int policy) {
    switch (policy) {
        case SCHED_OTHER: return "OTHER";
        case SCHED_FIFO:  return "FIFO";
        case SCHED_RR:    return "RR";
        default:          return "UNKNOWN";
    }
}

static bool set_thread_sched(pthread_t tid, int policy, int priority) {
    sched_param sp{};
    sp.sched_priority = priority;
    int ret = pthread_setschedparam(tid, policy, &sp);
    if (ret != 0) {
        std::cerr << "[WARN] pthread_setschedparam failed, ret=" << ret
                  << " policy=" << sched_policy_name(policy)
                  << " priority=" << priority << "\n";
        return false;
    }
    return true;
}

static bool set_thread_affinity(pthread_t tid, int core_id) {
    if (core_id < 0) return true;

    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(core_id, &cpuset);

    int ret = pthread_setaffinity_np(tid, sizeof(cpu_set_t), &cpuset);
    if (ret != 0) {
        std::cerr << "[WARN] pthread_setaffinity_np failed, ret=" << ret
                  << " core=" << core_id << "\n";
        return false;
    }
    return true;
}

static int current_cpu() {
#ifdef __linux__
    return sched_getcpu();
#else
    return -1;
#endif
}

// =========================
// SocketCAN 工具
// =========================
static int open_can_socket(const std::string& ifname) {
    int sock = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    if (sock < 0) {
        perror("socket(PF_CAN) failed");
        return -1;
    }

    ifreq ifr{};
    std::strncpy(ifr.ifr_name, ifname.c_str(), IFNAMSIZ - 1);

    if (ioctl(sock, SIOCGIFINDEX, &ifr) < 0) {
        perror("ioctl(SIOCGIFINDEX) failed");
        close(sock);
        return -1;
    }

    sockaddr_can addr{};
    addr.can_family = AF_CAN;
    addr.can_ifindex = ifr.ifr_ifindex;

    if (bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        perror("bind(AF_CAN) failed");
        close(sock);
        return -1;
    }

    return sock;
}

static int send_can_frame(int sock, uint32_t can_id, const uint8_t* data, uint8_t dlc) {
    can_frame frame{};
    frame.can_id = can_id;
    frame.can_dlc = dlc;
    std::memcpy(frame.data, data, dlc);

    return write(sock, &frame, sizeof(frame));
}

// =========================
// 信号处理
// =========================
static void signal_handler(int) {
    g_stop.store(true);
    g_log_cv.notify_all();
}

// =========================
// 命令行解析
// =========================
static std::string get_arg(int argc, char* argv[], const std::string& key, const std::string& def) {
    for (int i = 1; i < argc - 1; ++i) {
        if (argv[i] == key) return argv[i + 1];
    }
    return def;
}

static int get_arg_int(int argc, char* argv[], const std::string& key, int def) {
    for (int i = 1; i < argc - 1; ++i) {
        if (argv[i] == key) return std::stoi(argv[i + 1]);
    }
    return def;
}

// =========================
// demo 任务表
// 注意：这里只是第一版骨架，不是最终 >60% 负载正式表
// =========================
static std::vector<CanTask> make_demo_tasks_can00() {
    uint64_t t0 = now_ns() + 1000000000ULL;

    std::vector<CanTask> tasks = {
        {0x100, 10000000, 8, true,  0, t0},
        {0x101, 20000000, 8, true,  0, t0},
        {0x110, 1000000,  8, false, 0, t0},
        {0x111, 2000000,  8, false, 0, t0},
    };
    return tasks;
}

static std::vector<CanTask> make_demo_tasks_can01() {
    uint64_t t0 = now_ns() + 1000000000ULL;

    std::vector<CanTask> tasks = {
        {0x200, 10000000, 8, true,  0, t0},
        {0x201, 20000000, 8, true,  0, t0},
        {0x210, 1000000,  8, false, 0, t0},
        {0x211, 2000000,  8, false, 0, t0},
    };
    return tasks;
}

// =========================
// 工具函数
// =========================
static size_t find_earliest_task_index(const std::vector<CanTask>& tasks) {
    size_t idx = 0;
    uint64_t min_t = tasks[0].next_release_ns;
    for (size_t i = 1; i < tasks.size(); ++i) {
        if (tasks[i].next_release_ns < min_t) {
            min_t = tasks[i].next_release_ns;
            idx = i;
        }
    }
    return idx;
}

// =========================
// CAN 发送线程
// =========================
static void tx_worker(TxWorkerArgs args) {
    pthread_t tid = pthread_self();

    set_thread_affinity(tid, args.thread_cfg.cpu_core);
    set_thread_sched(tid, args.thread_cfg.sched_policy, args.thread_cfg.sched_priority);

    int sock = open_can_socket(args.ifname);
    if (sock < 0) {
        std::cerr << "[ERR] failed to open CAN socket on " << args.ifname << "\n";
        return;
    }

    std::cout << "[INFO] tx_worker start: " << args.thread_cfg.name
              << " if=" << args.ifname
              << " policy=" << sched_policy_name(args.thread_cfg.sched_policy)
              << " prio=" << args.thread_cfg.sched_priority
              << " core=" << args.thread_cfg.cpu_core << "\n";

    while (!g_stop.load()) {
        if (args.tasks.empty()) break;

        size_t idx = find_earliest_task_index(args.tasks);
        CanTask& task = args.tasks[idx];

        uint64_t planned_release_ns = task.next_release_ns;
        sleep_until_ns(planned_release_ns);

        if (g_stop.load()) break;

        uint64_t wakeup_ns = now_ns();

        uint8_t data[8] = {0};
        std::memcpy(&data[0], &task.seq, sizeof(task.seq));
        data[4] = static_cast<uint8_t>(task.can_id & 0xFF);
        data[5] = static_cast<uint8_t>((task.can_id >> 8) & 0xFF);
        data[6] = static_cast<uint8_t>(task.observed ? 1 : 0);
        data[7] = static_cast<uint8_t>(args.thread_cfg.name == "tx_can00" ? 0 : 1);

        int send_ret = send_can_frame(sock, task.can_id, data, task.dlc);
        uint64_t send_call_ns = now_ns();

        TxRecord rec;
        rec.exp_id = args.exp_id;
        rec.thread_name = args.thread_cfg.name;
        rec.ifname = args.ifname;
        rec.can_id = task.can_id;
        rec.seq = task.seq;
        rec.nominal_period_ns = task.period_ns;
        rec.planned_release_ns = planned_release_ns;
        rec.wakeup_ns = wakeup_ns;
        rec.send_call_ns = send_call_ns;
        rec.send_ret = send_ret;
        rec.cpu_id = current_cpu();
        rec.sched_policy = args.thread_cfg.sched_policy;
        rec.sched_priority = args.thread_cfg.sched_priority;

        {
            std::lock_guard<std::mutex> lock(g_log_mtx);
            g_log_buffer.push_back(std::move(rec));
        }
        g_log_cv.notify_one();

        task.seq++;
        task.next_release_ns += task.period_ns;

        uint64_t now = now_ns();
        while (task.next_release_ns < now - task.period_ns * 10ULL) {
            task.next_release_ns += task.period_ns;
        }
    }

    close(sock);
    std::cout << "[INFO] tx_worker stop: " << args.thread_cfg.name << "\n";
}

// =========================
// stress 线程
// =========================
static void busy_spin_ns(uint64_t duration_ns) {
    uint64_t start = now_ns();
    volatile double x = 1.0;
    while (!g_stop.load()) {
        for (int i = 0; i < 1000; ++i) {
            x = x * 1.000001 + 0.000001;
        }
        if (now_ns() - start >= duration_ns) break;
    }
}

static void stress_worker(StressWorkerArgs args) {
    pthread_t tid = pthread_self();

    set_thread_affinity(tid, args.cpu_core);

    std::cout << "[INFO] stress_worker start: idx=" << args.index
              << " core=" << args.cpu_core
              << " busy_ratio=" << args.busy_ratio << "\n";

    const uint64_t window_ns = 10ULL * 1000000ULL;
    uint64_t busy_ns = window_ns * static_cast<uint64_t>(args.busy_ratio) / 100ULL;
    uint64_t idle_ns = window_ns - busy_ns;

    while (!g_stop.load()) {
        if (busy_ns > 0) {
            busy_spin_ns(busy_ns);
        }
        if (idle_ns > 0 && !g_stop.load()) {
            std::this_thread::sleep_for(std::chrono::nanoseconds(idle_ns));
        }
    }

    std::cout << "[INFO] stress_worker stop: idx=" << args.index << "\n";
}

// =========================
// logger 线程
// =========================
static void logger_worker(const std::string& log_path) {
    std::ofstream ofs(log_path);
    if (!ofs.is_open()) {
        std::cerr << "[ERR] failed to open log file: " << log_path << "\n";
        return;
    }

    ofs << "exp_id,thread_name,ifname,can_id,seq,nominal_period_ns,"
        << "planned_release_ns,wakeup_ns,send_call_ns,send_ret,cpu_id,"
        << "sched_policy,sched_priority\n";

    std::cout << "[INFO] logger start: " << log_path << "\n";

    while (!g_stop.load()) {
        std::vector<TxRecord> local;

        {
            std::unique_lock<std::mutex> lock(g_log_mtx);
            g_log_cv.wait_for(lock, std::chrono::milliseconds(200), [] {
                return !g_log_buffer.empty() || g_stop.load();
            });
            local.swap(g_log_buffer);
        }

        for (const auto& r : local) {
            ofs << r.exp_id << ","
                << r.thread_name << ","
                << r.ifname << ","
                << "0x" << std::hex << std::uppercase << r.can_id << std::dec << ","
                << r.seq << ","
                << r.nominal_period_ns << ","
                << r.planned_release_ns << ","
                << r.wakeup_ns << ","
                << r.send_call_ns << ","
                << r.send_ret << ","
                << r.cpu_id << ","
                << r.sched_policy << ","
                << r.sched_priority
                << "\n";
        }

        ofs.flush();
    }

    {
        std::vector<TxRecord> local;
        {
            std::lock_guard<std::mutex> lock(g_log_mtx);
            local.swap(g_log_buffer);
        }
        for (const auto& r : local) {
            ofs << r.exp_id << ","
                << r.thread_name << ","
                << r.ifname << ","
                << "0x" << std::hex << std::uppercase << r.can_id << std::dec << ","
                << r.seq << ","
                << r.nominal_period_ns << ","
                << r.planned_release_ns << ","
                << r.wakeup_ns << ","
                << r.send_call_ns << ","
                << r.send_ret << ","
                << r.cpu_id << ","
                << r.sched_policy << ","
                << r.sched_priority
                << "\n";
        }
        ofs.flush();
    }

    std::cout << "[INFO] logger stop\n";
}

// =========================
// 构建配置
// =========================
static AppConfig build_config_from_args(int argc, char* argv[]) {
    AppConfig cfg;

    cfg.exp_id = static_cast<uint64_t>(get_arg_int(argc, argv, "--exp-id", 1));
    cfg.duration_sec = get_arg_int(argc, argv, "--duration", 10);

    cfg.can0_ifname = get_arg(argc, argv, "--can0", "can00");
    cfg.can1_ifname = get_arg(argc, argv, "--can1", "can01");
    cfg.log_path = get_arg(argc, argv, "--log", "output/output.csv");

    std::string policy_str = get_arg(argc, argv, "--policy", "other");
    int policy = parse_sched_policy(policy_str);

    std::string affinity = get_arg(argc, argv, "--affinity", "none");
    std::string stress = get_arg(argc, argv, "--stress", "off");
    int stress_threads = get_arg_int(argc, argv, "--stress-threads", 2);

    cfg.can0_thread_cfg.name = "tx_can00";
    cfg.can0_thread_cfg.sched_policy = policy;
    cfg.can0_thread_cfg.sched_priority = get_arg_int(argc, argv, "--priority0", policy == SCHED_OTHER ? 0 : 80);

    cfg.can1_thread_cfg.name = "tx_can01";
    cfg.can1_thread_cfg.sched_policy = policy;
    cfg.can1_thread_cfg.sched_priority = get_arg_int(argc, argv, "--priority1", policy == SCHED_OTHER ? 0 : 78);

    if (affinity == "split") {
        cfg.can0_thread_cfg.cpu_core = 0;
        cfg.can1_thread_cfg.cpu_core = 1;
        cfg.stress_cfg.cpu_cores = {2, 3};
    } else {
        cfg.can0_thread_cfg.cpu_core = -1;
        cfg.can1_thread_cfg.cpu_core = -1;
        cfg.stress_cfg.cpu_cores = {};
    }

    if (stress == "high") {
        cfg.stress_cfg.thread_count = stress_threads;
        cfg.stress_cfg.busy_ratio = 100;
    } else {
        cfg.stress_cfg.thread_count = 0;
        cfg.stress_cfg.busy_ratio = 0;
    }

    cfg.can0_tasks = make_demo_tasks_can00();
    cfg.can1_tasks = make_demo_tasks_can01();

    return cfg;
}

// =========================
// main
// =========================
int main(int argc, char* argv[]) {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    AppConfig cfg = build_config_from_args(argc, argv);

    std::cout << "========== dual CAN realtime sender ==========\n";
    std::cout << "exp_id      : " << cfg.exp_id << "\n";
    std::cout << "duration    : " << cfg.duration_sec << " s\n";
    std::cout << "can0        : " << cfg.can0_ifname << "\n";
    std::cout << "can1        : " << cfg.can1_ifname << "\n";
    std::cout << "policy      : " << sched_policy_name(cfg.can0_thread_cfg.sched_policy) << "\n";
    std::cout << "priority0   : " << cfg.can0_thread_cfg.sched_priority << "\n";
    std::cout << "priority1   : " << cfg.can1_thread_cfg.sched_priority << "\n";
    std::cout << "core(can0)  : " << cfg.can0_thread_cfg.cpu_core << "\n";
    std::cout << "core(can1)  : " << cfg.can1_thread_cfg.cpu_core << "\n";
    std::cout << "stress_cnt  : " << cfg.stress_cfg.thread_count << "\n";
    std::cout << "stress_busy : " << cfg.stress_cfg.busy_ratio << "\n";
    std::cout << "log_path    : " << cfg.log_path << "\n";
    std::cout << "=============================================\n";

    std::thread logger_th(logger_worker, cfg.log_path);

    TxWorkerArgs can0_args;
    can0_args.exp_id = cfg.exp_id;
    can0_args.ifname = cfg.can0_ifname;
    can0_args.thread_cfg = cfg.can0_thread_cfg;
    can0_args.tasks = cfg.can0_tasks;

    TxWorkerArgs can1_args;
    can1_args.exp_id = cfg.exp_id;
    can1_args.ifname = cfg.can1_ifname;
    can1_args.thread_cfg = cfg.can1_thread_cfg;
    can1_args.tasks = cfg.can1_tasks;

    std::thread can0_th(tx_worker, can0_args);
    std::thread can1_th(tx_worker, can1_args);

    std::vector<std::thread> stress_threads;
    for (int i = 0; i < cfg.stress_cfg.thread_count; ++i) {
        StressWorkerArgs sargs;
        sargs.index = i;
        sargs.busy_ratio = cfg.stress_cfg.busy_ratio;

        if (!cfg.stress_cfg.cpu_cores.empty()) {
            sargs.cpu_core = cfg.stress_cfg.cpu_cores[i % cfg.stress_cfg.cpu_cores.size()];
        } else {
            sargs.cpu_core = -1;
        }

        stress_threads.emplace_back(stress_worker, sargs);
    }

    std::this_thread::sleep_for(std::chrono::seconds(cfg.duration_sec));

    g_stop.store(true);
    g_log_cv.notify_all();

    for (auto& th : stress_threads) {
        if (th.joinable()) th.join();
    }
    if (can0_th.joinable()) can0_th.join();
    if (can1_th.joinable()) can1_th.join();
    if (logger_th.joinable()) logger_th.join();

    std::cout << "[INFO] experiment finished.\n";
    return 0;
}
