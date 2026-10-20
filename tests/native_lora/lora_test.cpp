// Synthetic CPU numerical checks against the shipping tensor loader; no model weights.
#include "engine/framework/assets/weight_adapter.h"
#include "engine/framework/core/backend.h"
#include "engine/framework/io/safetensors.h"
#include "engine/models/yue2/assets.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace fs = std::filesystem;
namespace a = engine::assets;
namespace io = engine::io;
using Entry = io::SafeTensorWriteEntry;
using engine::models::yue2::make_yue2_lora_source;
int passed = 0;
void check(bool value, const std::string & message) { if (!value) throw std::runtime_error(message); }
void pass(const std::string & name) { ++passed; std::cout << "PASS " << name << '\n'; }
template<class F> void rejects(const std::string & name, F fn) {
    try { fn(); } catch (const std::exception &) { pass(name); return; }
    throw std::runtime_error("Expected rejection: " + name);
}
template<class T> std::vector<unsigned char> bytes(const std::vector<T> & values) {
    std::vector<unsigned char> result(values.size() * sizeof(T));
    std::memcpy(result.data(), values.data(), result.size()); return result;
}
Entry entry(const std::string & name, const std::vector<int64_t> & shape, const std::vector<float> & values) {
    return {name, "F32", shape, bytes(values)};
}
float bf16(float value) {
    uint32_t bits; std::memcpy(&bits, &value, 4);
    bits = (bits + 0x7fffU + ((bits >> 16) & 1U)) & 0xffff0000U;
    std::memcpy(&value, &bits, 4); return value;
}
std::vector<float> values(size_t count, int phase = 0) {
    std::vector<float> result(count);
    for (size_t i = 0; i < count; ++i) result[i] = (int((i * 13 + phase) % 61) - 30) / 64.0F;
    return result;
}
std::vector<Entry> pair(const std::string & target, int64_t out = 32, int64_t in = 32,
                         const std::string & suffix_a = ".lora_A", const std::string & suffix_b = ".lora_B") {
    return {entry(target + suffix_a, {2, in}, values(2 * in, 3)),
            entry(target + suffix_b, {out, 2}, values(out * 2, 7))};
}
std::vector<float> reference(const std::vector<float> & base, const std::vector<float> & down,
                             const std::vector<float> & up, int64_t out, int64_t in, float scale) {
    auto expected = base;
    for (int64_t o = 0; o < out; ++o) for (int64_t i = 0; i < in; ++i) {
        float product = 0;
        for (int k = 0; k < 2; ++k) product += up[o * 2 + k] * down[k * in + i];
        expected[o * in + i] = bf16(base[o * in + i] + bf16(scale * product));
    }
    return expected;
}
void write(const fs::path & path, std::vector<Entry> entries) { io::write_safetensors_file(path, entries); }
void upload(const a::TensorSource & source, const a::TensorSource & expected,
            const std::string & name, a::TensorStorageType storage, const std::vector<int64_t> & shape) {
    const auto resolved = a::resolve_tensor_storage_type(source, name, storage);
    const auto type = a::ggml_type_for_tensor_storage(resolved);
    auto backend = engine::core::init_backend({engine::core::BackendType::Cpu, 0, 1});
    auto context = ggml_init({ggml_tensor_overhead() * 4, nullptr, true});
    auto tensor = shape.size() == 1 ? ggml_new_tensor_1d(context, type, shape[0]) :
        ggml_new_tensor_2d(context, type, shape[1], shape[0]);
    auto buffer = ggml_backend_alloc_ctx_tensors(context, backend);
    check(buffer != nullptr, "CPU tensor allocation failed");
    source.set_backend_tensor(tensor, name, storage, shape);
    std::vector<std::byte> actual(ggml_nbytes(tensor));
    ggml_backend_tensor_get(tensor, actual.data(), 0, actual.size());
    const auto expected_bytes = expected.require_tensor(name, resolved, shape).bytes;
    ggml_backend_buffer_free(buffer); ggml_free(context); ggml_backend_free(backend);
    check(actual == expected_bytes, "CPU upload or requantization differs from independent reference");
}
std::shared_ptr<const a::TensorSource> expected_source(const fs::path & root, const std::string & name,
                                                     const std::vector<int64_t> & shape, const std::vector<float> & data) {
    const auto path = root / "expected.safetensors";
    write(path, {entry(name, shape, data)}); return a::open_tensor_source(path);
}
void raw_file(const fs::path & path, const std::string & header, const std::vector<unsigned char> & data) {
    std::ofstream out(path, std::ios::binary); uint64_t length = header.size();
    out.write(reinterpret_cast<const char *>(&length), 8); out << header;
    out.write(reinterpret_cast<const char *>(data.data()), data.size());
}
int main(int argc, char ** argv) {
    try {
        check(argc == 2, "Usage: lora-test new-fixture-directory");
        const fs::path root(argv[1]); check(fs::create_directory(root), "fixture directory must be new");
        const std::string ar = "model.layers.0.self_attn.q_proj.weight";
        const std::string nar = "model.layers.0.nar_self_attn.q_proj.weight";
        std::vector<Entry> base_entries;
        for (bool acoustic : {false, true}) for (const auto & name : {
            "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj",
            "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj"}) {
            const std::string projection(name);
            const int64_t in = projection == "mlp.down_proj" ? 64 : 32;
            const int64_t out = projection == "self_attn.k_proj" || projection == "self_attn.v_proj" ? 16 :
                projection == "mlp.gate_proj" || projection == "mlp.up_proj" ? 64 : 32;
            base_entries.push_back(entry("model.layers.0." + std::string(acoustic ? "nar_" : "") + name + ".weight",
                                         {out, in}, values(out * in, acoustic ? 5 : 2)));
        }
        for (const auto & name : {"vae2llm", "llm2vae"}) {
            base_entries.push_back(entry(std::string(name) + ".weight", {32, 32}, values(32 * 32, 10)));
            base_entries.push_back(entry(std::string(name) + ".bias", {32}, values(32, 11)));
        }
        write(root / "base.safetensors", base_entries);
        const auto base = a::open_tensor_source(root / "base.safetensors");
        const auto ar_path = root / "ar.safetensors", nar_path = root / "nar.safetensors";
        write(ar_path, pair("layers.0.self_attn.q_proj"));
        write(nar_path, pair("layers.0.nar_self_attn.q_proj"));
        const auto down = values(64, 3), up = values(64, 7);
        const auto base_ar = base->require_f32(ar);
        auto positive = make_yue2_lora_source(base, ar_path, 1, 1);
        check(positive->require_f32(ar) == reference(base_ar, down, up, 32, 32, 1), "native BF16 merge parity");
        check(positive->require_tensor_data(nar).bytes == base->require_tensor_data(nar).bytes, "AR changed NAR");
        pass("native AR applies B@A with BF16-rounded delta and preserves NAR");
        for (const auto & dtype : {std::string("BF16"), std::string("F16")}) {
            auto typed = pair("layers.0.self_attn.q_proj");
            for (auto & tensor : typed) {
                std::vector<float> f32(tensor.data.size() / 4);
                std::memcpy(f32.data(), tensor.data.data(), tensor.data.size());
                std::vector<uint16_t> encoded(f32.size());
                for (size_t i = 0; i < f32.size(); ++i) {
                    if (dtype == "F16") encoded[i] = ggml_fp32_to_fp16(f32[i]);
                    else { uint32_t bits; std::memcpy(&bits, &f32[i], 4); encoded[i] = bits >> 16; }
                }
                tensor.dtype = dtype; tensor.data = bytes(encoded);
            }
            const auto path = root / (dtype + ".safetensors"); write(path, typed);
            check(make_yue2_lora_source(base, path, 1, 1)->require_f32(ar) == positive->require_f32(ar), "typed adapter differs");
            pass(dtype + " safetensors adapter factors decode and merge correctly");
        }
        auto both = make_yue2_lora_source(base, ar_path, .5F, 1, nar_path, -1);
        check(both->require_f32(ar) == reference(base_ar, down, up, 32, 32, .5F), "AR strength mismatch");
        check(both->require_f32(nar) == reference(base->require_f32(nar), down, up, 32, 32, -1), "NAR strength mismatch");
        pass("independent AR and NAR strengths, including negative strength");
        check(base->require_f32(ar) == base_ar, "base was modified");
        const auto before = positive->require_f32(ar); positive->release_storage();
        check(positive->require_f32(ar) == before && positive->require_f32(ar) == before, "reloaded adapter accumulated");
        auto changed = make_yue2_lora_source(base, ar_path, 2, 1);
        check(changed->require_f32(ar) == reference(base_ar, down, up, 32, 32, 2), "new strength reused old merge");
        check(positive->require_f32(ar) == before, "new selection changed prior session");
        pass("cold unload/reload, repeat read and changed-strength isolation preserve immutable base");
        auto zero = make_yue2_lora_source(base, ar_path, 0, 1, root / "missing.safetensors", 0);
        check(zero.get() == base.get(), "zero must be source identity");
        pass("zero bypasses files and all adaptation, including unavailable adapters");

        // Native default alpha/r=1; explicit alpha multiplies user strength.
        auto alpha_entries = pair("layers.0.self_attn.q_proj");
        alpha_entries.push_back(entry("layers.0.self_attn.q_proj.alpha", {}, {4}));
        write(root / "alpha.safetensors", alpha_entries);
        check(make_yue2_lora_source(base, root / "alpha.safetensors", .5F, 1)->require_f32(ar) == before, "alpha/r scaling");
        pass("explicit scalar alpha divided by rank");
        fs::create_directory(root / "peft");
        write(root / "peft/adapter_model.safetensors", pair("base_model.model.model.layers.0.self_attn.q_proj",32,32,".lora_A.weight",".lora_B.weight"));
        std::ofstream(root / "peft/adapter_config.json") << "{\"r\":2,\"lora_alpha\":4}";
        check(make_yue2_lora_source(base, root / "peft", .5F, 1)->require_f32(ar) == before, "PEFT config scaling");
        pass("PEFT prefix and adapter_config alpha/r");
        std::ofstream(root / "peft/adapter_config.json") << "{\"r\":2,\"lora_alpha\":2,\"use_dora\":true}";
        rejects("DoRA fails explicitly", [&] { make_yue2_lora_source(base, root / "peft", 1, 1); });
        std::ofstream(root / "peft/adapter_config.json") << "{\"r\":3,\"lora_alpha\":3}";
        rejects("PEFT rank mismatch fails", [&] { make_yue2_lora_source(base, root / "peft", 1, 1); });

        for (bool acoustic : {false, true}) {
            const auto prefix = acoustic ? "diffusion_model.model.layers.0." : "text_encoders.model.layers.0.";
            const auto path = root / (acoustic ? "comfy-nar.safetensors" : "comfy-ar.safetensors");
            auto fused = pair(std::string(prefix) + "self_attn.qkv_proj",64,32,".lora_down.weight",".lora_up.weight");
            auto gate = pair(std::string(prefix) + "mlp.gate_up_proj",128,32,".lora_down.weight",".lora_up.weight");
            fused.insert(fused.end(), gate.begin(), gate.end()); write(path, fused);
            const auto adapted = acoustic ? make_yue2_lora_source(base, {},1,1,path,1) : make_yue2_lora_source(base,path,1,1);
            const auto bqkv = values(128,7); int64_t offset = 0;
            for (const auto & projection : {"q_proj", "k_proj", "v_proj"}) {
                const int rows = std::string(projection) == "q_proj" ? 32 : 16;
                const auto name = "model.layers.0." + std::string(acoustic ? "nar_" : "") + "self_attn." + projection + ".weight";
                const std::vector<float> sliced(bqkv.begin()+offset*2,bqkv.begin()+(offset+rows)*2);
                check(adapted->require_f32(name) == reference(base->require_f32(name), down, sliced, rows,32,1), "fused QKV slice");
                offset += rows;
            }
            pass(acoustic ? "real ComfyUI NAR prefix and fused QKV/GQA binding" : "real ComfyUI AR prefix and fused QKV/GQA binding");
            const auto bgate = values(256,7); offset=0;
            for (const auto & projection : {"gate_proj", "up_proj"}) {
                const auto name="model.layers.0."+std::string(acoustic?"nar_":"")+"mlp."+projection+".weight";
                const std::vector<float> sliced(bgate.begin()+offset*2,bgate.begin()+(offset+64)*2);
                check(adapted->require_f32(name)==reference(base->require_f32(name),down,sliced,64,32,1),"fused gate/up slice");
                offset+=64;
            }
            pass(acoustic ? "NAR fused gate/up parity" : "AR fused gate/up parity");
        }
        auto replacement = values(1024,22);
        write(root/"replace.safetensors", {entry("vae2llm.weight",{32,32},replacement),entry("vae2llm.bias",{32},values(32,20))});
        auto replaced=make_yue2_lora_source(base,{},1,1,root/"replace.safetensors",.25F);
        for (auto & value:replacement) value=bf16(value);
        check(replaced->require_f32("vae2llm.weight")==replacement,"full replacement was scaled");
        check(make_yue2_lora_source(base,{},1,1,root/"replace.safetensors",0).get()==base.get(),"zero applied full replacement");
        pass("NAR full projection replacements remain unscaled; zero disables replacements");
        write(root/"dense.safetensors", {entry("diffusion_model.vae2llm.diff",{32,32},values(1024,4)),entry("diffusion_model.vae2llm.diff_b",{32},values(32,4))});
        auto dense=make_yue2_lora_source(base,{},1,1,root/"dense.safetensors",.5F);
        for(const auto & name:{"vae2llm.weight","vae2llm.bias"}) {
            auto expected=base->require_f32(name);const auto difference=values(expected.size(),4);
            for(size_t i=0;i<expected.size();++i) expected[i]=bf16(expected[i]+bf16(.5F*difference[i]));
            check(dense->require_f32(name)==expected,"Comfy difference became a full replacement");
        }
        pass("ComfyUI dense projection weight and bias differences scale independently of replacements");

        // Real GGUF quantization, immutable base and CPU backend uploads.
        a::convert_tensor_source_to_gguf(root/"base.safetensors",root/"base-q4.gguf",a::TensorStorageType::Q4_0,false,false);
        const auto quantized=a::open_tensor_source(root/"base-q4.gguf");
        const auto q4_raw=quantized->require_tensor_data(ar).bytes;
        auto q4adapt=make_yue2_lora_source(quantized,ar_path,1,1);
        const auto q4expected=reference(quantized->require_f32(ar),down,up,32,32,1);
        check(q4adapt->require_f32(ar)==q4expected,"Q4 merge differs from decoded base reference");
        pass("Q4 GGUF merge matches delta over the actual dequantized base");
        auto expected=expected_source(root,ar,{32,32},q4expected);
        for (const auto storage:{a::TensorStorageType::F32,a::TensorStorageType::F16,a::TensorStorageType::BF16,a::TensorStorageType::Q8_0,a::TensorStorageType::Q4_0,a::TensorStorageType::Native}) {
            upload(*q4adapt,*expected,ar,storage,{32,32});
            q4adapt->release_storage(); upload(*q4adapt,*expected,ar,storage,{32,32});
            pass("CPU upload/reload parity storage="+std::to_string(static_cast<int>(storage)));
        }
        check(quantized->require_tensor_data(ar).bytes==q4_raw,"quantized base bytes changed");
        auto q4zero=make_yue2_lora_source(quantized,ar_path,0,1,root/"replace.safetensors",0);
        check(q4zero.get()==quantized.get() && q4zero->require_tensor_data(ar).bytes==q4_raw,"zero requantized base");
        upload(*q4zero,*quantized,ar,a::TensorStorageType::Q4_0,{32,32});
        pass("Q4 zero strength preserves original bytes and uploaded tensor exactly");
        auto q4changed=make_yue2_lora_source(quantized,ar_path,.25F,1);
        check(q4changed->require_f32(ar)==reference(quantized->require_f32(ar),down,up,32,32,.25F),"Q4 changed strength drift");
        pass("Q4 strength change starts from original base, without accumulated quantization");

        auto malformed=pair("layers.0.self_attn.q_proj");malformed.pop_back();write(root/"missing-pair.safetensors",malformed);
        rejects("unpaired factor",[&]{make_yue2_lora_source(base,root/"missing-pair.safetensors",1,1);});
        write(root/"unknown.safetensors",pair("layers.1.self_attn.q_proj"));
        rejects("unknown or out-of-range layer",[&]{make_yue2_lora_source(base,root/"unknown.safetensors",1,1);});
        rejects("opposite AR role is rejected in NAR slot",[&]{make_yue2_lora_source(base,{},1,1,ar_path,1);});
        rejects("opposite NAR role is rejected in AR slot",[&]{make_yue2_lora_source(base,nar_path,1,1);});
        write(root/"wrong-shape.safetensors",pair("layers.0.self_attn.q_proj",31,32));
        rejects("target dimensions",[&]{make_yue2_lora_source(base,root/"wrong-shape.safetensors",1,1);});
        malformed=pair("layers.0.self_attn.q_proj");auto bad=values(64,3);bad[1]=std::numeric_limits<float>::quiet_NaN();malformed[0]=entry("layers.0.self_attn.q_proj.lora_A",{2,32},bad);write(root/"nonfinite.safetensors",malformed);
        rejects("nonfinite factor",[&]{make_yue2_lora_source(base,root/"nonfinite.safetensors",1,1);});
        bad=values(1024,3);bad[0]=std::numeric_limits<float>::infinity();write(root/"nonfinite-full.safetensors",{entry("vae2llm.weight",{32,32},bad)});
        rejects("nonfinite full replacement",[&]{make_yue2_lora_source(base,{},1,1,root/"nonfinite-full.safetensors",1);});
        rejects("nonfinite strength even with empty paths",[&]{make_yue2_lora_source(base,{},std::numeric_limits<float>::infinity(),1);});
        rejects("missing enabled path",[&]{make_yue2_lora_source(base,root/"missing.safetensors",1,1);});
        malformed=pair("layers.0.self_attn.q_proj");auto fused=pair("layers.0.self_attn.qkv_proj",64,32);malformed.insert(malformed.end(),fused.begin(),fused.end());write(root/"overlap.safetensors",malformed);
        rejects("fused and split updates conflict",[&]{make_yue2_lora_source(base,root/"overlap.safetensors",1,1);});
        write(root/"dense-conflict.safetensors",{entry("vae2llm.diff",{32,32},values(1024)),entry("vae2llm.weight",{32,32},values(1024))});
        rejects("dense delta and replacement conflict",[&]{make_yue2_lora_source(base,{},1,1,root/"dense-conflict.safetensors",1);});
        write(root/"ar-full.safetensors",{entry("layers.0.self_attn.q_proj.weight",{32,32},values(1024))});
        rejects("unsupported transformer full replacement",[&]{make_yue2_lora_source(base,root/"ar-full.safetensors",1,1);});
        const auto header="{\"x.alpha\":{\"dtype\":\"F32\",\"shape\":[],\"data_offsets\":[0,4]}}";
        raw_file(root/"truncated.safetensors",header,{});
        rejects("truncated data",[&]{a::read_weight_adapter(root/"truncated.safetensors");});
        raw_file(root/"duplicate.safetensors","{\"x.alpha\":{\"dtype\":\"F32\",\"shape\":[],\"data_offsets\":[0,4]},\"x.alpha\":{\"dtype\":\"F32\",\"shape\":[],\"data_offsets\":[4,8]}}",bytes(std::vector<float>{1,2}));
        rejects("duplicate safetensors names",[&]{a::read_weight_adapter(root/"duplicate.safetensors");});
        raw_file(root/"offset-overlap.safetensors","{\"x.alpha\":{\"dtype\":\"F32\",\"shape\":[],\"data_offsets\":[0,4]},\"y.alpha\":{\"dtype\":\"F32\",\"shape\":[],\"data_offsets\":[0,4]}}",bytes(std::vector<float>{1,2}));
        rejects("overlapping data offsets",[&]{a::read_weight_adapter(root/"offset-overlap.safetensors");});
        raw_file(root/"integer.safetensors","{\"x.alpha\":{\"dtype\":\"I64\",\"shape\":[],\"data_offsets\":[0,8]}}",bytes(std::vector<int64_t>{1}));
        rejects("unsupported dtype",[&]{a::read_weight_adapter(root/"integer.safetensors");});
        std::ofstream huge(root/"huge.safetensors",std::ios::binary);uint64_t too_large=~uint64_t(0);huge.write(reinterpret_cast<char*>(&too_large),8);huge.close();
        rejects("oversized header before allocation",[&]{a::read_weight_adapter(root/"huge.safetensors");});
        write(root/"lokr.safetensors",{entry("layers.0.self_attn.q_proj.lokr_w1_a",{2,1},values(2)),entry("layers.0.self_attn.q_proj.lokr_w1_b",{1,2},values(2))});
        rejects("unsupported LoKr before decomposed matrix expansion",[&]{a::read_weight_adapter(root/"lokr.safetensors");});
        raw_file(root/"overflow-dimension.safetensors","{\"x.alpha\":{\"dtype\":\"F32\",\"shape\":[1e200],\"data_offsets\":[0,4]}}",bytes(std::vector<float>{1}));
        rejects("integer overflow before shape conversion",[&]{a::read_weight_adapter(root/"overflow-dimension.safetensors");});
        std::cout<<"{\"passed\":"<<passed<<",\"failed\":0,\"backend\":\"cpu\",\"model_weights_loaded\":false,\"synthetic_gguf\":true}"<<std::endl;
        return 0;
    } catch(const std::exception & error) {std::cerr<<"FAIL "<<error.what()<<std::endl;return 1;}
}
