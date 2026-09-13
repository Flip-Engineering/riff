#pragma once
#include "engine/models/yue2/ar_runtime.h"

namespace u07_test {
struct Calls {
    int ar_constructed = 0;
    int abc = 0;
    int semantic = 0;
    uint64_t seed = 0;
    float guidance = 0;
    engine::models::yue2::Yue2ArSamplingWindow abc_window;
    std::vector<int32_t> sampled_score;
    std::vector<int32_t> positive;
    std::vector<int32_t> negative;
};
extern Calls calls;
void reset(const std::vector<int32_t> & score = {});
}
