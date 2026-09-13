// Numerical checks against analytic ODEs, using the actual native integrator.
#include "engine/models/yue2/flow_solver.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <limits>

using namespace engine::models::yue2;

int main() {
    for (auto method : {Yue2OdeMethod::Midpoint, Yue2OdeMethod::AdamsBashforth2}) {
        for (int steps : {1, 8, 32}) {
            int calls = 0, completed = 0;
            auto result = integrate_flow({3.0F, 5.0F}, steps, method,
                [&](const std::vector<float> &, float t) {
                    ++calls;
                    assert(t >= 0 && t <= 1);
                    return std::vector<float>{2.0F, 4.0F};
                }, [&](int64_t step) { assert(step == ++completed); });
            assert(std::abs(result[0] - 1.0F) < 1e-6F);
            assert(std::abs(result[1] - 1.0F) < 1e-6F);
            assert(completed == steps);
            assert(calls == (method == Yue2OdeMethod::Midpoint ? 2 * steps : steps + 1));
        }

        // dy/dt=y, integrated backward from y(1)=1: y(0)=exp(-1).
        float previous_error = 0;
        for (int steps : {20, 40, 80}) {
            auto result = integrate_flow({1.0F}, steps, method,
                [](const std::vector<float> & value, float) { return value; }, [](int64_t) {});
            float error = std::abs(result[0] - std::exp(-1.0F));
            assert(error < .001F);
            if (previous_error) assert(error < previous_error * .3F);
            previous_error = error;
        }

        // A time-dependent derivative exercises backward time and startup.
        auto timed = integrate_flow({0.0F}, 16, method,
            [](const std::vector<float> &, float t) { return std::vector<float>{t}; }, [](int64_t) {});
        assert(std::abs(timed[0] + .5F) < 1e-6F);
    }
    bool rejected = false;
    try { parse_ode_method("unknown"); } catch (const std::invalid_argument &) { rejected = true; }
    assert(rejected);
    rejected = false;
    try {
        integrate_flow({0.0F}, 0, Yue2OdeMethod::Midpoint,
            [](const std::vector<float> & value, float) { return value; }, [](int64_t) {});
    } catch (const std::invalid_argument &) { rejected = true; }
    assert(rejected);
    rejected = false;
    try {
        integrate_flow({0.0F}, 2, Yue2OdeMethod::AdamsBashforth2,
            [](const std::vector<float> &, float) { return std::vector<float>{}; }, [](int64_t) {});
    } catch (const std::runtime_error &) { rejected = true; }
    assert(rejected);
    rejected = false;
    try {
        integrate_flow({0.0F}, 2, Yue2OdeMethod::AdamsBashforth2,
            [](const std::vector<float> &, float) { return std::vector<float>{std::numeric_limits<float>::infinity()}; }, [](int64_t) {});
    } catch (const std::runtime_error &) { rejected = true; }
    assert(rejected);
    std::puts("Native midpoint and AB2: second-order convergence, evaluation counts, backward time and invalid-output checks pass.");
}
