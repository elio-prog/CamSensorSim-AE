#include "log.h"

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <thread>

namespace camsim {

namespace {
std::mutex g_mu;
std::string g_path;
const bool g_stderr = std::getenv("CAMSIM_TIMING") != nullptr;
const auto g_start = std::chrono::steady_clock::now();
}  // namespace

void set_log_file(const char* path) {
    std::lock_guard<std::mutex> lk(g_mu);
    g_path = path ? path : "";
    if (g_path.empty()) return;
    // Le journal repart de zero s'il devient gros (plus de 2 Mo).
    if (FILE* f = std::fopen(g_path.c_str(), "rb")) {
        std::fseek(f, 0, SEEK_END);
        const long n = std::ftell(f);
        std::fclose(f);
        if (n > (2L << 20)) std::remove(g_path.c_str());
    }
}

bool log_enabled() {
    std::lock_guard<std::mutex> lk(g_mu);
    return g_stderr || !g_path.empty();
}

void log(const char* fmt, ...) {
    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    const double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - g_start).count();
    const unsigned tid = (unsigned)(std::hash<std::thread::id>()(std::this_thread::get_id()) % 10000);
    std::lock_guard<std::mutex> lk(g_mu);
    if (g_stderr) std::fprintf(stderr, "%s\n", msg);
    if (!g_path.empty())
        if (FILE* f = std::fopen(g_path.c_str(), "a")) {
            std::fprintf(f, "%9.3f s  fil %04u  %s\n", t, tid, msg);
            std::fclose(f);
        }
}

void Timer::lap(const char* name) {
    const auto n = std::chrono::steady_clock::now();
    if (on) log("  %s %.1f ms", name, std::chrono::duration<double, std::milli>(n - t).count());
    t = n;
}

double Timer::total_ms() const {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
}

}  // namespace camsim
