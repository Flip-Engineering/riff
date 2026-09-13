// Deterministic comparison of the existing and wider Metal attention kernels.
#include "ggml.h"
#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml-metal.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

int main(int argc, char ** argv) {
    if (argc != 5) return 2;
    const int queries=std::atoi(argv[1]), keys=std::atoi(argv[2]), runs=std::atoi(argv[3]);
    if (queries<1 || keys<1 || runs<1) return 2;
    auto backend=ggml_backend_metal_init();
    if (!backend) return 3;
    auto ctx=ggml_init({32*1024*1024,nullptr,true});
    auto q=ggml_new_tensor_3d(ctx,GGML_TYPE_F32,128,queries,16);
    auto k=ggml_new_tensor_3d(ctx,GGML_TYPE_F16,128,8,keys);
    auto v=ggml_new_tensor_3d(ctx,GGML_TYPE_F16,128,8,keys);
    ggml_set_input(q);
    ggml_set_input(k);
    ggml_set_input(v);
    auto buffer=ggml_backend_alloc_ctx_tensors(ctx,backend);
    std::vector<float> qdata(128*queries*16);
    for (size_t i=0;i<qdata.size();++i) qdata[i]=std::sin(i*.17)*.8;
    ggml_backend_tensor_set(q,qdata.data(),0,qdata.size()*4);
    std::vector<ggml_fp16_t> kvdata(128*keys*8);
    for (size_t i=0;i<kvdata.size();++i) kvdata[i]=ggml_fp32_to_fp16(std::sin(i*.073));
    ggml_backend_tensor_set(k,kvdata.data(),0,kvdata.size()*2);
    for (size_t i=0;i<kvdata.size();++i) kvdata[i]=ggml_fp32_to_fp16(std::cos(i*.037));
    ggml_backend_tensor_set(v,kvdata.data(),0,kvdata.size()*2);
    auto kp = ggml_permute(ctx, k, 0, 2, 1, 3);
    auto vp = ggml_permute(ctx, v, 0, 2, 1, 3);
    auto output=ggml_flash_attn_ext(ctx,q,kp,vp,nullptr,1/std::sqrt(128.f),0,0);
    ggml_flash_attn_ext_set_prec(output, GGML_PREC_F32);
    ggml_set_output(output);
    auto graph = ggml_new_graph_custom(ctx, 128, false);
    ggml_build_forward_expand(graph, output);
    auto alloc=ggml_gallocr_new(ggml_backend_get_default_buffer_type(backend));
    if (!ggml_gallocr_alloc_graph(alloc,graph)) return 4;
    // First round warms both pipelines. ABBA ordering reduces temperature/order bias.
    std::vector<double> milliseconds[2];
    for (int round=0;round<=runs;++round) for (int slot=0;slot<4;++slot) {
        const int variant=(slot==1 || slot==2) ? 1 : 0;
        if (variant) {
            unsetenv("GGML_METAL_WIDE_ATTENTION_DISABLE");
        } else {
            setenv("GGML_METAL_WIDE_ATTENTION_DISABLE", "1", 1);
        }
        const auto start=std::chrono::steady_clock::now();
        if (ggml_backend_graph_compute(backend,graph)!=GGML_STATUS_SUCCESS) return 5;
        ggml_backend_synchronize(backend);
        const double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
        if (round) milliseconds[variant].push_back(ms);
        if (round==runs && (slot==2 || slot==3)) {
            std::vector<float> result(128*queries*16);
            ggml_backend_tensor_get(output,result.data(),0,result.size()*4);
            for (float value:result) if (!std::isfinite(value)) return 6;
            std::ofstream file(std::string(argv[4]) + "." + std::to_string(variant), std::ios::binary);
            file.write(reinterpret_cast<const char *>(result.data()), result.size() * sizeof(float));
            if (!file) return 7;
        }
    }
    for (int variant=0;variant<2;++variant) {
        auto & times = milliseconds[variant];
        std::sort(times.begin(), times.end());
        std::printf("{\"queries\":%d,\"keys\":%d,\"wide\":%d,\"median_ms\":%.6f,\"min_ms\":%.6f,\"max_ms\":%.6f}\n",queries,keys,variant,times[times.size()/2],times.front(),times.back());
    }
    ggml_gallocr_free(alloc);
    ggml_backend_buffer_free(buffer);
    ggml_free(ctx);
    ggml_backend_free(backend);
    return 0;
}
