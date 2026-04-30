#include "app.hpp"

static std::atomic<bool> g_stop{false};

struct TaskRuntime {
    TaskConfig cfg;
    uint64_t next_release_ns = 0;
    uint32_t seq = 0;
};

struct RxLastSeqKey {
    uint8_t link_id;
    uint8_t task_id;

    bool operator<(const RxLastSeqKey& other) const {
        if (link_id != other.link_id) return link_id < other.link_id;
        return task_id < other.task_id;
    }
};

static void signal_handler(int) {
    g_stop.store(true);
}

static bool ensure_dir(const std::string& path) {
    std::string cmd = "mkdir -p " + path;
    return std::system(cmd.c_str()) == 0;
}

static void write_csv_header_tx(std::ofstream& ofs) {
    ofs << "event_type,host_event_ns,link_id,task_id,ifname,thread_name,can_id,seq,"
           "nominal_period_ns,planned_release_ns,wakeup_ns,send_call_ns,send_ret,"
           "cpu_id,sched_policy,sched_priority\n";
}

static void write_csv_header_rx(std::ofstream& ofs) {
    ofs << "event_type,host_event_ns,link_id,task_id,ifname,thread_name,can_id,seq,dlc,"
           "rx_kernel_ts_ns,rx_user_read_ns,loss_count,"
           "cpu_id,sched_policy,sched_priority\n";
}

static void write_csv_header_info(std::ofstream& ofs) {
    ofs << "event_type,host_event_ns,link_id,task_id,ifname,thread_name,can_id,seq,"
           "nominal_period_ns,planned_release_ns,wakeup_ns,send_call_ns,send_ret,"
           "rx_kernel_ts_ns,rx_user_read_ns,loss_count,"
           "cpu_id,sched_policy,sched_priority,message\n";
}

static void logger_write_tx(std::ofstream& ofs, const EventRecord& ev) {
    ofs
        << "TX" << ','
        << ev.host_event_ns << ','
        << static_cast<unsigned>(ev.link_id) << ','
        << static_cast<unsigned>(ev.task_id) << ','
        << ev.ifname << ','
        << ev.thread_name << ','
        << ev.can_id << ','
        << ev.seq << ','
        << ev.nominal_period_ns << ','
        << ev.planned_release_ns << ','
        << ev.wakeup_ns << ','
        << ev.send_call_ns << ','
        << ev.send_ret << ','
        << ev.cpu_id << ','
        << ev.sched_policy << ','
        << ev.sched_priority << '\n';
}

static void logger_write_rx(std::ofstream& ofs, const EventRecord& ev) {
    ofs
        << (ev.type == EventType::RX ? "RX" : "LOSS") << ','
        << ev.host_event_ns << ','
        << static_cast<unsigned>(ev.link_id) << ','
        << static_cast<unsigned>(ev.task_id) << ','
        << ev.ifname << ','
        << ev.thread_name << ','
        << ev.can_id << ','
        << ev.seq << ','
        << static_cast<unsigned>(ev.dlc) << ','
        << ev.rx_kernel_ts_ns << ','
        << ev.rx_user_read_ns << ','
        << ev.loss_count << ','
        << ev.cpu_id << ','
        << ev.sched_policy << ','
        << ev.sched_priority << '\n';
}

static void logger_write_info(std::ofstream& ofs, const EventRecord& ev, const std::string& msg) {
    ofs
        << "INFO" << ','
        << ev.host_event_ns << ','
        << static_cast<unsigned>(ev.link_id) << ','
        << static_cast<unsigned>(ev.task_id) << ','
        << ev.ifname << ','
        << ev.thread_name << ','
        << ev.can_id << ','
        << ev.seq << ','
        << ev.nominal_period_ns << ','
        << ev.planned_release_ns << ','
        << ev.wakeup_ns << ','
        << ev.send_call_ns << ','
        << ev.send_ret << ','
        << ev.rx_kernel_ts_ns << ','
        << ev.rx_user_read_ns << ','
        << ev.loss_count << ','
        << ev.cpu_id << ','
        << ev.sched_policy << ','
        << ev.sched_priority << ','
        << '"' << msg << '"' << '\n';
}

static void logger_thread_fn(EventQueue* q, const std::string out_dir) {
    ensure_dir(out_dir);

    std::ofstream tx_ofs(out_dir + "/tx_log.csv");
    std::ofstream rx_ofs(out_dir + "/rx_log.csv");
    std::ofstream info_ofs(out_dir + "/info_log.csv");

    write_csv_header_tx(tx_ofs);
    write_csv_header_rx(rx_ofs);
    write_csv_header_info(info_ofs);

    EventRecord ev{};
    while (!g_stop.load()) {
        if (!q->pop(ev, g_stop)) continue;

        if (ev.type == EventType::TX) {
            logger_write_tx(tx_ofs, ev);
        } else if (ev.type == EventType::RX || ev.type == EventType::LOSS) {
            logger_write_rx(rx_ofs, ev);
        } else {
            logger_write_info(info_ofs, ev, ev.thread_name);
        }
    }

    while (q->drain_one(ev)) {
        if (ev.type == EventType::TX) {
            logger_write_tx(tx_ofs, ev);
        } else if (ev.type == EventType::RX || ev.type == EventType::LOSS) {
            logger_write_rx(rx_ofs, ev);
        } else {
            logger_write_info(info_ofs, ev, ev.thread_name);
        }
    }

    tx_ofs.flush();
    rx_ofs.flush();
    info_ofs.flush();
}

static void sleep_until_ns(uint64_t target_ns) {
    while (!g_stop.load()) {
        uint64_t now = now_monotonic_ns();
        if (now >= target_ns) break;

        uint64_t remain = target_ns - now;
        struct timespec ts{};
        ts.tv_sec = remain / 1000000000ull;
        ts.tv_nsec = remain % 1000000000ull;
        clock_nanosleep(CLOCK_MONOTONIC, 0, &ts, nullptr);
    }
}

static void print_thread_status(const ThreadRtConfig& cfg, const std::string& role) {
    std::cout
        << "[THREAD] " << role
        << " name=" << cfg.name
        << " target_policy=" << sched_kind_name(cfg.policy)
        << " target_priority=" << ((cfg.policy == SchedKind::OTHER) ? 0 : cfg.priority)
        << " target_cpu=" << cfg.cpu
        << " actual_policy=" << sched_policy_name(get_sched_policy_self())
        << " actual_priority=" << get_sched_priority_self()
        << " actual_cpu=" << current_cpu()
        << "\n";
}

static void tx_thread_fn(DirectionConfig dir, GlobalConfig gcfg, EventQueue* q) {
    apply_thread_rt(dir.tx_rt);
    print_thread_status(dir.tx_rt, "TX");

    int fd = open_can_socket(dir.tx_ifname, false, gcfg.tx_sock_sndbuf, 0, false);
    if (fd < 0) {
        return;
    }

    std::vector<TaskRuntime> runtimes;
    uint64_t start_ns = now_monotonic_ns() + 100000000ull;

    for (const auto& t : dir.tasks) {
        TaskRuntime rt;
        rt.cfg = t;
        rt.next_release_ns = start_ns + t.phase_offset_ns;
        rt.seq = 0;
        runtimes.push_back(rt);
    }

    while (!g_stop.load()) {
        uint64_t nearest_ns = UINT64_MAX;
        for (const auto& rt : runtimes) {
            if (rt.next_release_ns < nearest_ns) nearest_ns = rt.next_release_ns;
        }

        sleep_until_ns(nearest_ns);
        if (g_stop.load()) break;

        uint64_t wakeup_ns = now_monotonic_ns();

        for (auto& rt : runtimes) {
            if (wakeup_ns + 1000ull < rt.next_release_ns) continue;
            if (g_stop.load()) break;

            struct can_frame frame{};
            frame.can_id = rt.cfg.can_id;
            frame.can_dlc = 8;

            Payload8 p{};
            p.seq = rt.seq;
            p.task_id = rt.cfg.task_id;
            p.link_id = dir.link_id;
            p.flags = 0;
            p.reserved = 0;

            std::memcpy(frame.data, &p, sizeof(p));

            uint64_t send_call_ns = now_monotonic_ns();
            int ret = static_cast<int>(write(fd, &frame, sizeof(frame)));

            EventRecord ev{};
            ev.type = EventType::TX;
            ev.host_event_ns = send_call_ns;
            ev.link_id = dir.link_id;
            ev.task_id = rt.cfg.task_id;
            ev.can_id = rt.cfg.can_id;
            ev.seq = rt.seq;
            ev.nominal_period_ns = rt.cfg.period_ns;
            ev.planned_release_ns = rt.next_release_ns;
            ev.wakeup_ns = wakeup_ns;
            ev.send_call_ns = send_call_ns;
            ev.send_ret = ret;
            ev.cpu_id = current_cpu();
            ev.sched_policy = get_sched_policy_self();
            ev.sched_priority = get_sched_priority_self();
            copy_cstr(ev.ifname, sizeof(ev.ifname), dir.tx_ifname);
            copy_cstr(ev.thread_name, sizeof(ev.thread_name), dir.tx_rt.name);
            q->push(ev);

            rt.seq++;
            rt.next_release_ns += rt.cfg.period_ns;

            while (rt.next_release_ns <= send_call_ns) {
                rt.next_release_ns += rt.cfg.period_ns;
            }
        }
    }

    close(fd);
}

static bool is_expected_rx_ifname(uint8_t link_id, const std::string& rx_ifname) {
    switch (link_id) {
        case 0: return rx_ifname == "can10"; // can00 -> can10
        case 1: return rx_ifname == "can00"; // can10 -> can00
        case 2: return rx_ifname == "can11"; // can01 -> can11
        case 3: return rx_ifname == "can01"; // can11 -> can01
        default: return false;
    }
}

static void rx_thread_fn(DirectionConfig dir, GlobalConfig gcfg, EventQueue* q) {
    apply_thread_rt(dir.rx_rt);
    print_thread_status(dir.rx_rt, "RX");

    int fd = open_can_socket(dir.rx_ifname, true, 0, gcfg.rx_sock_rcvbuf, true);
    if (fd < 0) {
        return;
    }

    std::map<RxLastSeqKey, uint32_t> last_seq_map;

    while (!g_stop.load()) {
        struct can_frame frame{};
        struct iovec iov{};
        iov.iov_base = &frame;
        iov.iov_len = sizeof(frame);

        char ctrlmsg[256];
        std::memset(ctrlmsg, 0, sizeof(ctrlmsg));

        struct msghdr msg{};
        msg.msg_iov = &iov;
        msg.msg_iovlen = 1;
        msg.msg_control = ctrlmsg;
        msg.msg_controllen = sizeof(ctrlmsg);

        int nbytes = static_cast<int>(recvmsg(fd, &msg, 0));
        uint64_t rx_kernel_ts_ns = now_monotonic_ns(); // recvmsg 返回后立即采样，统一 CLOCK_MONOTONIC

        if (nbytes < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
            continue;
        }

        if (nbytes < static_cast<int>(sizeof(struct can_frame))) continue;
        if (frame.can_dlc < 8) continue;

        Payload8 p{};
        std::memcpy(&p, frame.data, sizeof(p));

        if (!is_expected_rx_ifname(p.link_id, dir.rx_ifname)) {
            continue;
        }

        uint64_t rx_user_read_ns = now_monotonic_ns(); // 用户态完成基础解析后的时间

        EventRecord ev{};
        ev.type = EventType::RX;
        ev.host_event_ns = rx_user_read_ns;
        ev.link_id = p.link_id;
        ev.task_id = p.task_id;
        ev.dlc = frame.can_dlc;
        ev.can_id = frame.can_id & CAN_EFF_MASK;
        ev.seq = p.seq;
        ev.rx_kernel_ts_ns = rx_kernel_ts_ns;
        ev.rx_user_read_ns = rx_user_read_ns;
        ev.cpu_id = current_cpu();
        ev.sched_policy = get_sched_policy_self();
        ev.sched_priority = get_sched_priority_self();
        copy_cstr(ev.ifname, sizeof(ev.ifname), dir.rx_ifname);
        copy_cstr(ev.thread_name, sizeof(ev.thread_name), dir.rx_rt.name);
        q->push(ev);

        RxLastSeqKey key{p.link_id, p.task_id};
        auto it = last_seq_map.find(key);
        if (it != last_seq_map.end()) {
            uint32_t expected = it->second + 1;
            if (p.seq > expected) {
                EventRecord loss_ev{};
                loss_ev.type = EventType::LOSS;
                loss_ev.host_event_ns = rx_user_read_ns;
                loss_ev.link_id = p.link_id;
                loss_ev.task_id = p.task_id;
                loss_ev.dlc = frame.can_dlc;
                loss_ev.can_id = frame.can_id & CAN_EFF_MASK;
                loss_ev.seq = p.seq;
                loss_ev.rx_kernel_ts_ns = rx_kernel_ts_ns;
                loss_ev.rx_user_read_ns = rx_user_read_ns;
                loss_ev.loss_count = p.seq - expected;
                loss_ev.cpu_id = current_cpu();
                loss_ev.sched_policy = get_sched_policy_self();
                loss_ev.sched_priority = get_sched_priority_self();
                copy_cstr(loss_ev.ifname, sizeof(loss_ev.ifname), dir.rx_ifname);
                copy_cstr(loss_ev.thread_name, sizeof(loss_ev.thread_name), dir.rx_rt.name);
                q->push(loss_ev);
            }
        }
        last_seq_map[key] = p.seq;
    }

    close(fd);
}

static void stress_thread_fn(ThreadRtConfig rt) {
    apply_thread_rt(rt);
    print_thread_status(rt, "STRESS");

    volatile uint64_t x = 1;
    while (!g_stop.load()) {
        for (int i = 0; i < 200000; ++i) {
            x = x * 1664525ull + 1013904223ull;
        }
        std::this_thread::yield();
    }
    (void)x;
}

static SchedKind parse_sched(const std::string& s) {
    if (s == "fifo") return SchedKind::FIFO;
    if (s == "rr") return SchedKind::RR;
    return SchedKind::OTHER;
}

static void print_usage(const char* prog) {
    std::cout
        << "Usage:\n"
        << prog << " [options]\n\n"
        << "Options:\n"
        << "  --duration SEC\n"
        << "  --out-dir DIR\n"
        << "  --policy other|fifo|rr\n"
        << "  --tx-prio N\n"
        << "  --rx-prio N\n"
        << "  --cpu-map tx00,rx10,tx10,rx00,tx01,rx11,tx11,rx01\n"
        << "  --stress-threads N\n"
        << "  --stress-cpu-start N\n";
}

static std::vector<int> parse_cpu_map(const std::string& s) {
    std::vector<int> out;
    std::stringstream ss(s);
    std::string item;
    while (std::getline(ss, item, ',')) {
        out.push_back(std::stoi(item));
    }
    return out;
}

int main(int argc, char* argv[]) {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    GlobalConfig gcfg;
    SchedKind common_policy = SchedKind::OTHER;
    int tx_prio = 0;
    int rx_prio = 0;
    std::vector<int> cpu_map = {-1, -1, -1, -1, -1, -1, -1, -1};

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];

        if (a == "--duration" && i + 1 < argc) {
            gcfg.duration_sec = std::stoi(argv[++i]);
        } else if (a == "--out-dir" && i + 1 < argc) {
            gcfg.out_dir = argv[++i];
        } else if (a == "--policy" && i + 1 < argc) {
            common_policy = parse_sched(argv[++i]);
        } else if (a == "--tx-prio" && i + 1 < argc) {
            tx_prio = std::stoi(argv[++i]);
        } else if (a == "--rx-prio" && i + 1 < argc) {
            rx_prio = std::stoi(argv[++i]);
        } else if (a == "--cpu-map" && i + 1 < argc) {
            cpu_map = parse_cpu_map(argv[++i]);
            if (cpu_map.size() != 8) {
                std::cerr << "cpu-map requires 8 integers\n";
                return 1;
            }
        } else if (a == "--stress-threads" && i + 1 < argc) {
            gcfg.enable_stress = true;
            gcfg.stress_threads = std::stoi(argv[++i]);
        } else if (a == "--stress-cpu-start" && i + 1 < argc) {
            gcfg.stress_cpu_start = std::stoi(argv[++i]);
        } else if (a == "--help") {
            print_usage(argv[0]);
            return 0;
        } else {
            std::cerr << "Unknown arg: " << a << "\n";
            print_usage(argv[0]);
            return 1;
        }
    }

    if (common_policy == SchedKind::OTHER) {
        tx_prio = 0;
        rx_prio = 0;
    }

    ensure_dir(gcfg.out_dir);

    std::cout << "[CONFIG] duration_sec=" << gcfg.duration_sec << "\n";
    std::cout << "[CONFIG] out_dir=" << gcfg.out_dir << "\n";
    std::cout << "[CONFIG] policy=" << sched_kind_name(common_policy) << "\n";
    std::cout << "[CONFIG] tx_prio=" << tx_prio << "\n";
    std::cout << "[CONFIG] rx_prio=" << rx_prio << "\n";
    std::cout << "[CONFIG] stress_threads=" << gcfg.stress_threads << "\n";

    // 两条物理总线分别做“双向错相启动”
    // 总线 A: d0 <-> d1
    //   对1: 0us / 500us
    //   对2: 250us / 750us
    //
    // 总线 B: d2 <-> d3
    //   对1: 0us / 500us
    //   对2: 250us / 750us
    //
    // 其余 2ms/10ms/20ms 任务当前设为 0 相位。
    std::vector<TaskConfig> tasks_d0 = {
        {0, 0x100,  1'000'000ull,      0ull},   // 1ms 对1，正向
        {1, 0x101,  1'000'000ull, 250'000ull},   // 1ms 对2，正向
        {2, 0x102,  2'000'000ull,      0ull},
        {3, 0x103, 10'000'000ull,      0ull},
        {4, 0x104, 20'000'000ull,      0ull}
    };

    std::vector<TaskConfig> tasks_d1 = {
        {0, 0x110,  1'000'000ull, 500'000ull},   // 1ms 对1，反向
        {1, 0x111,  1'000'000ull, 750'000ull},   // 1ms 对2，反向
        {2, 0x112,  2'000'000ull,      0ull},
        {3, 0x113, 10'000'000ull,      0ull},
        {4, 0x114, 20'000'000ull,      0ull}
    };

    std::vector<TaskConfig> tasks_d2 = {
        {0, 0x120,  1'000'000ull,      0ull},   // 1ms 对1，正向
        {1, 0x121,  1'000'000ull, 250'000ull},   // 1ms 对2，正向
        {2, 0x122,  2'000'000ull,      0ull},
        {3, 0x123, 10'000'000ull,      0ull},
        {4, 0x124, 20'000'000ull,      0ull}
    };

    std::vector<TaskConfig> tasks_d3 = {
        {0, 0x130,  1'000'000ull, 500'000ull},   // 1ms 对1，反向
        {1, 0x131,  1'000'000ull, 750'000ull},   // 1ms 对2，反向
        {2, 0x132,  2'000'000ull,      0ull},
        {3, 0x133, 10'000'000ull,      0ull},
        {4, 0x134, 20'000'000ull,      0ull}
    };

    DirectionConfig d0;
    d0.link_id = 0;
    d0.tx_ifname = "can00";
    d0.rx_ifname = "can10";
    d0.direction_name = "can00_to_can10";
    d0.tx_rt = {"tx_can00", common_policy, tx_prio, cpu_map[0]};
    d0.rx_rt = {"rx_can10", common_policy, rx_prio, cpu_map[1]};
    d0.tasks = tasks_d0;

    DirectionConfig d1;
    d1.link_id = 1;
    d1.tx_ifname = "can10";
    d1.rx_ifname = "can00";
    d1.direction_name = "can10_to_can00";
    d1.tx_rt = {"tx_can10", common_policy, tx_prio, cpu_map[2]};
    d1.rx_rt = {"rx_can00", common_policy, rx_prio, cpu_map[3]};
    d1.tasks = tasks_d1;

    DirectionConfig d2;
    d2.link_id = 2;
    d2.tx_ifname = "can01";
    d2.rx_ifname = "can11";
    d2.direction_name = "can01_to_can11";
    d2.tx_rt = {"tx_can01", common_policy, tx_prio, cpu_map[4]};
    d2.rx_rt = {"rx_can11", common_policy, rx_prio, cpu_map[5]};
    d2.tasks = tasks_d2;

    DirectionConfig d3;
    d3.link_id = 3;
    d3.tx_ifname = "can11";
    d3.rx_ifname = "can01";
    d3.direction_name = "can11_to_can01";
    d3.tx_rt = {"tx_can11", common_policy, tx_prio, cpu_map[6]};
    d3.rx_rt = {"rx_can01", common_policy, rx_prio, cpu_map[7]};
    d3.tasks = tasks_d3;

    EventQueue queue(1 << 20);

    std::thread logger_thr(logger_thread_fn, &queue, gcfg.out_dir);

    std::thread tx0(tx_thread_fn, d0, gcfg, &queue);
    std::thread rx0(rx_thread_fn, d0, gcfg, &queue);

    std::thread tx1(tx_thread_fn, d1, gcfg, &queue);
    std::thread rx1(rx_thread_fn, d1, gcfg, &queue);

    std::thread tx2(tx_thread_fn, d2, gcfg, &queue);
    std::thread rx2(rx_thread_fn, d2, gcfg, &queue);

    std::thread tx3(tx_thread_fn, d3, gcfg, &queue);
    std::thread rx3(rx_thread_fn, d3, gcfg, &queue);

    std::vector<std::thread> stress_threads;
    if (gcfg.enable_stress && gcfg.stress_threads > 0) {
        for (int i = 0; i < gcfg.stress_threads; ++i) {
            ThreadRtConfig rt;
            rt.name = "stress" + std::to_string(i);
            rt.policy = SchedKind::OTHER;
            rt.priority = 0;
            rt.cpu = (gcfg.stress_cpu_start >= 0) ? (gcfg.stress_cpu_start + i) : -1;
            stress_threads.emplace_back(stress_thread_fn, rt);
        }
    }

    uint64_t t_end = now_monotonic_ns() + static_cast<uint64_t>(gcfg.duration_sec) * 1000000000ull;
    while (!g_stop.load() && now_monotonic_ns() < t_end) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    g_stop.store(true);

    tx0.join();
    rx0.join();
    tx1.join();
    rx1.join();
    tx2.join();
    rx2.join();
    tx3.join();
    rx3.join();

    for (auto& t : stress_threads) {
        t.join();
    }

    logger_thr.join();

    std::cout << "[DONE] Logs written to: " << gcfg.out_dir << "\n";
    std::cout << "[DONE] Queue dropped events: " << queue.dropped() << "\n";

    return 0;
}
