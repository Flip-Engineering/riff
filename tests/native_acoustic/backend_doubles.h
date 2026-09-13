#pragma once
#include "engine/models/yue2/ar_runtime.h"
#include <functional>

namespace e06_test {
struct Calls {
    int ar=0,nar=0,vae=0,abc=0,semantic=0,solve=0,decode=0;
    bool fail_nar=false,partial_nar=false,fail_vae=false;
    engine::assets::TensorStorageType vae_storage=engine::assets::TensorStorageType::Native;
    std::vector<float> completed;
    std::vector<int64_t> decoded_frames;
    std::function<void()> during_nar;
};
extern Calls calls;
void reset();
}
