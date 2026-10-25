// Investigation harness: link the existing queued engine's unmodified
// acoustic_checkpoint.cpp.o, so timings exercise the shipping hash_file.
// Usage: acoustic-pin-probe FILE [FILE ...]
#include <array>
#include <cstdint>
#include <chrono>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <sys/resource.h>

// Exact declaration from the pinned acoustic_checkpoint.h; avoid pulling
// engine model headers into this otherwise standalone measurement harness.
namespace engine::models::yue2::checkpoint {
std::array<uint8_t, 32> hash_file(const std::filesystem::path & path);
}
namespace cp = engine::models::yue2::checkpoint;
double milliseconds(const timeval & t) { return t.tv_sec * 1000.0 + t.tv_usec / 1000.0; }

int main(int argc, char ** argv) {
    try {
        if (argc < 2) return 2;
        for (int i = 1; i < argc; ++i) {
            const std::filesystem::path path(argv[i]);
            const auto bytes = std::filesystem::file_size(path);
            rusage start{}, end{};
            if (getrusage(RUSAGE_SELF, &start)) return 2;
            const auto time = std::chrono::steady_clock::now();
            const auto digest = cp::hash_file(path);
            const double wall = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - time).count();
            if (getrusage(RUSAGE_SELF, &end)) return 2;
            std::cout << std::setprecision(12)
                      << "{\"file\":" << std::quoted(path.filename().string())
                      << ",\"bytes\":" << bytes
                      << ",\"wall_ms\":" << wall
                      << ",\"cpu_ms\":" << milliseconds(end.ru_utime) + milliseconds(end.ru_stime)
                                                  - milliseconds(start.ru_utime) - milliseconds(start.ru_stime)
                      << ",\"input_blocks\":" << end.ru_inblock - start.ru_inblock
                      << ",\"output_blocks\":" << end.ru_oublock - start.ru_oublock
                      << ",\"sha256\":\"";
            for (auto byte : digest)
                std::cout << std::hex << std::setw(2) << std::setfill('0') << unsigned(byte);
            std::cout << std::dec << "\"}" << std::endl;
        }
    } catch (const std::exception & e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
