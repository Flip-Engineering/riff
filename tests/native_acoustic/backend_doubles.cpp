#include "backend_doubles.h"
#include "engine/models/yue2/nar_runtime.h"
#include "engine/framework/codecs/oobleck_audio_vae_runtime.h"
#include "engine/framework/runtime/graph_executor.h"
#include <stdexcept>

// Only the CPU regression executable links this file. Actual parsing, asset
// selection, tokenization, stage orchestration, packing and checkpoint IO stay
// production code. Neural operators and backend allocation are replaced here.
namespace e06_test { Calls calls;void reset(){calls={};} }
namespace engine::core {
ExecutionContext::ExecutionContext(const BackendConfig & config):config_(config){}
ExecutionContext::~ExecutionContext()=default;
ExecutionContext::ExecutionContext(ExecutionContext && other) noexcept:config_(other.config_){}
ExecutionContext & ExecutionContext::operator=(ExecutionContext && other) noexcept {config_=other.config_;return *this;}
ggml_backend_t ExecutionContext::backend() const noexcept{return nullptr;}
const BackendConfig & ExecutionContext::config() const noexcept{return config_;}
BackendType ExecutionContext::backend_type() const noexcept{return config_.type;}
bool ExecutionContext::uses_host_graph_plan() const noexcept{return false;}
BackendMemorySnapshot ExecutionContext::memory_snapshot() const{throw std::runtime_error("unexpected backend query");}
}
namespace engine::runtime {
GraphExecutor::GraphExecutor(core::ExecutionContext & execution):execution_context_(execution){}
GraphExecutor::~GraphExecutor()=default;
}
namespace engine::models::yue2 {
struct Yue2ArRuntime::Impl {};
Yue2ArRuntime::Yue2ArRuntime(core::ExecutionContext &,std::shared_ptr<const Yue2Assets>,assets::TensorStorageType,
    size_t,size_t,size_t):impl_(std::make_unique<Impl>()){++e06_test::calls.ar;}
Yue2ArRuntime::~Yue2ArRuntime()=default;
std::vector<int32_t> Yue2ArRuntime::generate(const std::vector<int32_t> &,const Yue2ArSamplingWindow &,uint64_t){
    ++e06_test::calls.abc;return {11,23};
}
std::vector<int32_t> Yue2ArRuntime::generate_cfg(const std::vector<int32_t> &,const std::vector<int32_t> &,
    const Yue2ArSamplingWindow &,float,uint64_t){++e06_test::calls.semantic;return{kCodecOffset+17,kCodecOffset+41,kCodecOffset+9};}
runtime::TransformerKVState Yue2ArRuntime::prefill_state(const std::vector<int32_t> &){throw std::runtime_error("unexpected prefill");}
Yue2ArDevicePrefixState Yue2ArRuntime::prefill_device_state(const std::vector<int32_t> &){throw std::runtime_error("unexpected device prefill");}
void Yue2ArRuntime::release_runtime_graphs(){}
struct Yue2NarRuntime::Impl {};
Yue2NarRuntime::Yue2NarRuntime(core::ExecutionContext &,std::shared_ptr<const Yue2Assets>,assets::TensorStorageType,
    size_t,size_t):impl_(std::make_unique<Impl>()){++e06_test::calls.nar;}
Yue2NarRuntime::~Yue2NarRuntime()=default;
std::vector<float> Yue2NarRuntime::synthesize(const std::vector<int32_t> &,const std::vector<int32_t> & codes,
    const std::function<Yue2ArDevicePrefixState(const std::vector<int32_t> &)> &,const std::vector<float> &,
    uint64_t,int64_t,int64_t,Yue2OdeMethod){
    auto & c=e06_test::calls;++c.solve;if(c.during_nar)c.during_nar();
    if(c.fail_nar)throw std::runtime_error("injected incomplete acoustic solve");
    std::vector<float> latents((codes.size()-(c.partial_nar?1:0))*64);
    for(size_t i=0;i<latents.size();++i)latents[i]=float(int(i%67)-33)/37;
    c.completed=latents;return latents;
}
void Yue2NarRuntime::release_runtime_graphs(){}
}
namespace engine::codecs {
struct OobleckAudioVaeRuntime::Impl { OobleckAudioVaeConfig config; };
OobleckAudioVaeRuntime::OobleckAudioVaeRuntime(std::shared_ptr<const assets::TensorSource>,core::ExecutionContext &,
    OobleckAudioVaeConfig config,OobleckAudioVaeRuntimeOptions options):impl_(std::make_unique<Impl>()){
    impl_->config=std::move(config);++e06_test::calls.vae;e06_test::calls.vae_storage=options.weight_storage_type;
}
OobleckAudioVaeRuntime::~OobleckAudioVaeRuntime()=default;
std::vector<runtime::AudioBuffer> OobleckAudioVaeRuntime::decode(const std::vector<float> & planar,int64_t batch,int64_t frames){
    auto & c=e06_test::calls;++c.decode;c.decoded_frames.push_back(frames);
    if(c.fail_vae)throw std::runtime_error("injected VAE failure after completed acoustic synthesis");
    if(batch!=1 || planar.size()!=size_t(frames*64))throw std::runtime_error("invalid actual planar packing");
    runtime::AudioBuffer out;out.sample_rate=int(impl_->config.sample_rate);out.channels=int(impl_->config.audio_channels);
    out.samples.resize(size_t((frames*1920-64)*out.channels));
    for(int64_t t=0;t<frames*1920-64;++t)for(int ch=0;ch<out.channels;++ch)
        out.samples[size_t(t*out.channels+ch)]=planar[size_t(ch*frames+t/1920)];
    return {std::move(out)};
}
void OobleckAudioVaeRuntime::release_runtime_graphs(){}
}
