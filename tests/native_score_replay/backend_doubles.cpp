#include "backend_doubles.h"
#include "engine/models/yue2/nar_runtime.h"
#include "engine/framework/codecs/oobleck_audio_vae_runtime.h"
#include <stdexcept>

// Test-only sampler and execution boundary. The production executable never
// links this object. A model graph or backend is neither created nor executed.
namespace u07_test {
Calls calls;
void reset(const std::vector<int32_t> & score) {
    calls = {};
    calls.sampled_score = score;
}
[[noreturn]] void forbidden() { throw std::runtime_error("U07 test attempted a model/backend operation"); }
}

namespace engine::core {
ExecutionContext::ExecutionContext(const BackendConfig & config) : config_(config) {}
ExecutionContext::~ExecutionContext() = default;
}

namespace engine::models::yue2 {
struct Yue2ArRuntime::Impl {};
Yue2ArRuntime::Yue2ArRuntime(core::ExecutionContext &, std::shared_ptr<const Yue2Assets>,
    assets::TensorStorageType, size_t, size_t, size_t) : impl_(std::make_unique<Impl>()) {
    ++u07_test::calls.ar_constructed;
}
Yue2ArRuntime::~Yue2ArRuntime() = default;
std::vector<int32_t> Yue2ArRuntime::generate(const std::vector<int32_t> &,
    const Yue2ArSamplingWindow & window, uint64_t) {
    ++u07_test::calls.abc;
    u07_test::calls.abc_window = window;
    return u07_test::calls.sampled_score;
}
std::vector<int32_t> Yue2ArRuntime::generate_cfg(const std::vector<int32_t> & positive,
    const std::vector<int32_t> & negative, const Yue2ArSamplingWindow & window,
    float guidance, uint64_t seed) {
    if (window.begin != kCodecOffset || window.end != kCodecOffset + kCodecSize || window.stop_token != kMusicEndToken)
        throw std::runtime_error("Unexpected semantic sampling window");
    ++u07_test::calls.semantic;
    u07_test::calls.positive = positive;
    u07_test::calls.negative = negative;
    u07_test::calls.seed = seed;
    u07_test::calls.guidance = guidance;
    return {kCodecOffset + 17, kCodecOffset + 41};
}
runtime::TransformerKVState Yue2ArRuntime::prefill_state(const std::vector<int32_t> &) { u07_test::forbidden(); }
Yue2ArDevicePrefixState Yue2ArRuntime::prefill_device_state(const std::vector<int32_t> &) { u07_test::forbidden(); }
void Yue2ArRuntime::release_runtime_graphs() {}

struct Yue2NarRuntime::Impl {};
Yue2NarRuntime::Yue2NarRuntime(core::ExecutionContext &, std::shared_ptr<const Yue2Assets>,
    assets::TensorStorageType, size_t, size_t) { u07_test::forbidden(); }
Yue2NarRuntime::~Yue2NarRuntime() = default;
std::vector<float> Yue2NarRuntime::synthesize(const std::vector<int32_t> &, const std::vector<int32_t> &,
    const std::function<Yue2ArDevicePrefixState(const std::vector<int32_t> &)> &,
    const std::vector<float> &, uint64_t, int64_t, int64_t, Yue2OdeMethod) { u07_test::forbidden(); }
void Yue2NarRuntime::release_runtime_graphs() {}
}

namespace engine::codecs {
struct OobleckAudioVaeRuntime::Impl {};
OobleckAudioVaeRuntime::OobleckAudioVaeRuntime(std::shared_ptr<const assets::TensorSource>,
    core::ExecutionContext &, OobleckAudioVaeConfig, OobleckAudioVaeRuntimeOptions) { u07_test::forbidden(); }
OobleckAudioVaeRuntime::~OobleckAudioVaeRuntime() = default;
std::vector<runtime::AudioBuffer> OobleckAudioVaeRuntime::decode(const std::vector<float> &, int64_t, int64_t) {
    u07_test::forbidden();
}
void OobleckAudioVaeRuntime::release_runtime_graphs() {}
}
