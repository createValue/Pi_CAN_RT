#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <cerrno>
#include <csignal>
#include <cstdlib>

#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include <linux/can.h>
#include <linux/can/raw.h>
#include <linux/if.h>
#include <net/if.h>

enum class EventType : uint8_t {
    TX = 1,
    RX = 2,
    INFO = 3,
    LOSS = 4
};

enum class SchedKind : int {
    OTHER = 0,
    FIFO = 1,
    RR = 2
};

struct Payload8 {
    uint32_t seq;
    uint8_t task_id;
    uint8_t link_id;
    uint8_t flags;
    uint8_t reserved;
} __attribute__((packed));

static_assert(sizeof(Payload8) == 8, "Payload8 must be 8 bytes");

struct TaskConfig {
    uint8_t task_id = 0;
    uint32_t can_id = 0x100;
    uint64_t period_ns = 1000000;
};

struct ThreadRtConfig {
    std::string name;
    SchedKind policy = SchedKind::OTHER;
    int priority = 0;
    int cpu = -1;
};

struct DirectionConfig {
    uint8_t link_id = 0;
    std::string tx_ifname;
    std::string rx_ifname;
    std::string direction_name;
    ThreadRtConfig tx_rt;
    ThreadRtConfig rx_rt;
    std::vector<TaskConfig> tasks;
};

struct GlobalConfig {
    int duration_sec = 20;
    std::string out_dir = "output";

    int tx_sock_sndbuf = 1 << 20;
    int rx_sock_rcvbuf = 1 << 20;

    bool enable_stress = false;
    int stress_threads = 0;
    int stress_cpu_start = -1;
};

struct EventRecord {
    EventType type = EventType::INFO;

    uint64_t host_event_ns = 0;

    uint8_t link_id = 0;
    uint8_t task_id = 0;
    uint8_t dlc = 0;
    uint8_t reserved0 = 0;

    uint32_t can_id = 0;
    uint32_t seq = 0;

    int32_t send_ret = 0;
    int32_t cpu_id = -1;
    int32_t sched_policy = 0;
    int32_t sched_priority = 0;

    uint64_t nominal_period_ns = 0;
    uint64_t planned_release_ns = 0;
    uint64_t wakeup_ns = 0;
    uint64_t send_call_ns = 0;

    uint64_t rx_kernel_ts_ns = 0;
    uint64_t rx_user_read_ns = 0;

    uint32_t loss_count = 0;

    char ifname[16]{0};
    char thread_name[32]{0};
};

class EventQueue {
public:
    explicit EventQueue(size_t max_size = 1 << 20) : max_size_(max_size) {}

    bool push(const EventRecord& e) {
        std::unique_lock<std::mutex> lk(mtx_);
        if (q_.size() >= max_size_) {
            dropped_++;
            return false;
        }
        q_.push_back(e);
        cv_.notify_one();
        return true;
    }

    bool pop(EventRecord& out, std::atomic<bool>& stop_flag) {
        std::unique_lock<std::mutex> lk(mtx_);
        cv_.wait(lk, [&] { return !q_.empty() || stop_flag.load(); });
        if (q_.empty()) return false;
        out = q_.front();
        q_.pop_front();
        return true;
    }

    uint64_t dropped() const { return dropped_.load(); }

private:
    std::deque<EventRecord> q_;
    size_t max_size_;
    mutable std::mutex mtx_;
    std::condition_variable cv_;
    std::atomic<uint64_t> dropped_{0};
};

inline uint64_t now_monotonic_ns() {
    struct timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ull + static_cast<uint64_t>(ts.tv_nsec);
}

inline int current_cpu() {
#ifdef SYS_getcpu
    unsigned cpu = 0;
    unsigned node = 0;
    syscall(SYS_getcpu, &cpu, &node, nullptr);
    return static_cast<int>(cpu);
#else
    return sched_getcpu();
#endif
}

inline int get_sched_policy_self() {
    return sched_getscheduler(0);
}

inline int get_sched_priority_self() {
    struct sched_param sp{};
    if (sched_getparam(0, &sp) != 0) return -1;
    return sp.sched_priority;
}

inline void copy_cstr(char* dst, size_t n, const std::string& s) {
    if (n == 0) return;
    std::snprintf(dst, n, "%s", s.c_str());
}

inline int sched_kind_to_native(SchedKind k) {
    switch (k) {
        case SchedKind::FIFO: return SCHED_FIFO;
        case SchedKind::RR: return SCHED_RR;
        case SchedKind::OTHER:
        default: return SCHED_OTHER;
    }
}

inline bool apply_thread_rt(const ThreadRtConfig& cfg) {
    pthread_t tid = pthread_self();

    if (cfg.cpu >= 0) {
        cpu_set_t cpuset;
        CPU_ZERO(&cpuset);
        CPU_SET(cfg.cpu, &cpuset);
        if (pthread_setaffinity_np(tid, sizeof(cpu_set_t), &cpuset) != 0) {
            std::perror("pthread_setaffinity_np");
        }
    }

    int policy = sched_kind_to_native(cfg.policy);
    struct sched_param sp{};
    sp.sched_priority = cfg.priority;

    if (pthread_setschedparam(tid, policy, &sp) != 0) {
        std::perror("pthread_setschedparam");
        return false;
    }

    if (!cfg.name.empty()) {
        pthread_setname_np(tid, cfg.name.substr(0, 15).c_str());
    }

    return true;
}

inline int open_can_socket(const std::string& ifname, bool enable_timestamp, int sndbuf, int rcvbuf) {
    int fd = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    if (fd < 0) {
        std::perror("socket(PF_CAN)");
        return -1;
    }

    int enable = 1;
    if (setsockopt(fd, SOL_CAN_RAW, CAN_RAW_RECV_OWN_MSGS, &enable, sizeof(enable)) < 0) {
        std::perror("setsockopt(CAN_RAW_RECV_OWN_MSGS)");
    }

    if (sndbuf > 0) {
        if (setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf)) < 0) {
            std::perror("setsockopt(SO_SNDBUF)");
        }
    }

    if (rcvbuf > 0) {
        if (setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf)) < 0) {
            std::perror("setsockopt(SO_RCVBUF)");
        }
    }

    if (enable_timestamp) {
        int one = 1;
        if (setsockopt(fd, SOL_SOCKET, SO_TIMESTAMPNS, &one, sizeof(one)) < 0) {
            std::perror("setsockopt(SO_TIMESTAMPNS)");
        }
    }

    struct ifreq ifr{};
    std::snprintf(ifr.ifr_name, IFNAMSIZ, "%s", ifname.c_str());
    if (ioctl(fd, SIOCGIFINDEX, &ifr) < 0) {
        std::perror("ioctl(SIOCGIFINDEX)");
        close(fd);
        return -1;
    }

    struct sockaddr_can addr{};
    addr.can_family = AF_CAN;
    addr.can_ifindex = ifr.ifr_ifindex;

    if (bind(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::perror("bind(AF_CAN)");
        close(fd);
        return -1;
    }

    return fd;
}

inline std::string sched_policy_name(int p) {
    switch (p) {
        case SCHED_FIFO: return "FIFO";
        case SCHED_RR: return "RR";
        case SCHED_OTHER: return "OTHER";
        default: return "UNKNOWN";
    }
}
