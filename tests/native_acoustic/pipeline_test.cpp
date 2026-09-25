#include "backend_doubles.h"
#include "engine/models/yue2/session.h"
#include "engine/models/yue2/acoustic_checkpoint.h"
#include "engine/framework/io/json.h"
#include <gguf.h>

#include <algorithm>
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>

using namespace engine::models::yue2;
namespace cp=engine::models::yue2::checkpoint;
namespace fs=std::filesystem;
namespace {
int passed=0;
void check(bool value,const char * message){if(!value)throw std::runtime_error(message);}
void pass(const std::string & name){++passed;std::cout<<"PASS "<<name<<'\n';}
template<class F>void rejects(const std::string & name,F fn){
    try{fn();}catch(const std::runtime_error &){pass(name);return;}throw std::runtime_error("Expected rejection: "+name);
}
void text(const fs::path & path,const std::string & value){
    std::ofstream f(path,std::ios::binary);f<<value;f.close();check(bool(f),"fixture write");
}
Yue2Request parse(std::unordered_map<std::string,std::string> options){
    engine::runtime::TaskRequest r;r.options=std::move(options);return parse_yue2_request(r,{});
}
void gguf(const fs::path & path,const std::map<std::string,std::vector<int64_t>> & tensors){
    auto * ctx=ggml_init({1024*1024,nullptr,true});check(ctx,"metadata context");
    auto * out=gguf_init_empty();check(out,"GGUF metadata");
    gguf_set_val_str(out,"audiocpp.tensor_name_format","native");
    std::vector<std::vector<float>> data;data.reserve(tensors.size());
    const std::string prefix=path.filename().string().find("vae")!=std::string::npos?"vae_weights/":"model_weights/";
    for(const auto & [name,shape]:tensors){
        auto dims=shape;std::reverse(dims.begin(),dims.end());
        auto * tensor=ggml_new_tensor(ctx,GGML_TYPE_F32,int(dims.size()),dims.data());check(tensor,"metadata tensor");
        ggml_set_name(tensor,(prefix+name).c_str());data.emplace_back(size_t(ggml_nelements(tensor)),0.0F);tensor->data=data.back().data();
        gguf_add_tensor(out,tensor);
    }
    check(gguf_write_to_file(out,path.c_str(),false),"GGUF fixture write");gguf_free(out);ggml_free(ctx);
}
fs::path model_fixture(const fs::path & root,const std::string & name,const fs::path & tokenizer){
    const auto model=root/name;fs::create_directories(model/"sidecars");
    text(model/"sidecars/yue2-model-config.json",R"({"hidden_size":2,"num_hidden_layers":1,"num_attention_heads":1,"num_key_value_heads":1,"head_dim":2,"intermediate_size":4,"vocab_size":32,"max_position_embeddings":24576})");
    text(model/"sidecars/yue2-vae-config.json",R"({"sample_rate":48000,"audio_channels":2,"latent_dim":64,"decode_core_frames":2,"decode_halo_frames":1})");
    fs::create_symlink(tokenizer,model/"sidecars/yue2-qwen.tiktoken");
    gguf(model/"yue2-3b-q8_0.gguf",{{"model.embed_tokens.weight",{32,2}},{"model.layers.0.self_attn.q_proj.weight",{2,2}},
        {"model.layers.0.nar_self_attn.q_proj.weight",{2,2}},{"vae2llm.weight",{2,64}},{"llm2vae.weight",{64,2}}});
    gguf(model/"yue2-vae-f16.gguf",{{"decoder.layers.0.weight_g",{2048,1,1}}});
    return model;
}
std::unique_ptr<Yue2Session> session(const fs::path & model,const std::string & precision="",const std::string & keep_resident=""){
    engine::runtime::SessionOptions options;
    if(!precision.empty())options.options["yue2.vae_weight_type"]=precision;
    if(!keep_resident.empty())options.options["yue2.keep_resident"]=keep_resident;
    engine::runtime::TaskSpec task;task.task=engine::runtime::VoiceTaskKind::AudioGeneration;task.mode=engine::runtime::RunMode::Offline;
    auto out=std::make_unique<Yue2Session>(task,options,load_yue2_assets(model));out->prepare({});return out;
}
engine::runtime::TaskResult run(Yue2Session & session,std::unordered_map<std::string,std::string> options){
    engine::runtime::TaskRequest r;r.options=std::move(options);return session.run(r);
}
bool equal(const engine::runtime::AudioBuffer & a,const engine::runtime::AudioBuffer & b){
    return a.sample_rate==b.sample_rate && a.channels==b.channels && a.samples.size()==b.samples.size() &&
        !std::memcmp(a.samples.data(),b.samples.data(),a.samples.size()*4);
}
}
int main(int argc,char ** argv){
    try{
        check(argc==3,"Usage: pipeline-test TOKENIZER NEW_FIXTURE_DIR");const fs::path root(argv[2]);
        check(fs::create_directory(root),"fresh fixture directory");const fs::path tokenizer(argv[1]);
        check(parse({{"acoustic_latents_file","owned.yac"},{"style","retained description"},{"lyrics","retained lyrics"}}).acoustic_latents_file=="owned.yac","decode parse");
        pass("dedicated decode input accepts retained descriptive recipe fields");
        rejects("empty decode filename",[]{parse({{"acoustic_latents_file",""}});});
        for(const auto * key:{"abc","abc_file","score_tokens_file","score_tokens_out","semantic_codes_file","semantic_codes_out","nar_noise_file","acoustic_latents_out"})
            rejects(std::string("decode excludes active ")+key,[&]{parse({{"acoustic_latents_file","owned.yac"},{key,""}});});
        for(const auto * key:{"plan_only","semantic_only","acoustic_only"}){
            rejects(std::string("decode excludes ")+key,[&]{parse({{"acoustic_latents_file","owned.yac"},{key,"true"}});});
            check(parse({{"acoustic_latents_file","owned.yac"},{key,"false"}}).acoustic_latents_file=="owned.yac","false stage flag");
        }
        rejects("acoustic-only requires completed output",[]{parse({{"acoustic_only","true"}});});
        rejects("empty capture filename",[]{parse({{"acoustic_latents_out",""}});});
        rejects("capture excludes plan-only",[]{parse({{"acoustic_latents_out","out.yac"},{"plan_only","true"},{"cot","full"}});});
        rejects("capture excludes semantic-only",[]{parse({{"acoustic_latents_out","out.yac"},{"semantic_only","true"},{"semantic_codes_out","out.codes"}});});
        rejects("invalid acoustic-only bool",[]{parse({{"acoustic_only","1"},{"acoustic_latents_out","out.yac"}});});
        rejects("zero decoder core",[]{parse({{"acoustic_latents_file","in.yac"},{"acoustic_decode_core_frames","0"}});});
        rejects("negative decoder halo",[]{parse({{"acoustic_latents_file","in.yac"},{"acoustic_decode_halo_frames","-1"}});});
        rejects("decoder refinement without replay",[]{parse({{"acoustic_decode_core_frames","4"}});});
        auto refinement=parse({{"acoustic_latents_file","in.yac"},{"acoustic_decode_core_frames","4"},{"acoustic_decode_halo_frames","0"}});
        check(refinement.acoustic_decode_core_frames==4 && refinement.acoustic_decode_halo_frames==0,"explicit refinements");
        pass("decoder refinements preserve valid explicit settings");
        const auto model=model_fixture(root,"normal",tokenizer);
        e06_test::reset();auto normal=session(model,"f16");
        check(e06_test::calls.ar==0 && e06_test::calls.nar==0 && e06_test::calls.vae==0,"session eagerly created runtime");
        pass("session construction and preparation do not create model runtimes");
        const auto captured=root/"capture.yac";
        auto actual=run(*normal,{{"cot","off"},{"seed","71"},{"num_inference_steps","4"},{"acoustic_latents_out",captured.string()}});
        check(actual.audio_output.has_value(),"normal audio missing");
        check(e06_test::calls.ar==1 && e06_test::calls.nar==1 && e06_test::calls.vae==1 && e06_test::calls.solve==1 && e06_test::calls.decode==2,"normal stage behavior");
        const auto saved=cp::load_completed(captured);
        check(saved.metadata.frames==3 && saved.metadata.semantic_frames==3 && saved.metadata.vae_storage==2 &&
              saved.metadata.vae.decode_core_frames==2 && saved.metadata.vae.decode_halo_frames==1 && saved.latents==e06_test::calls.completed,"checkpoint captured wrong completed data/settings");
        check(saved.metadata.model==cp::hash_file(model/"yue2-3b-q8_0.gguf") && saved.metadata.decoder==cp::hash_file(model/"yue2-vae-f16.gguf") &&
              saved.metadata.tokenizer==cp::hash_file(tokenizer) && saved.metadata.producer_binary==cp::executable_hash(),"actual source hashes differ");
        pass("normal generation captures completed NAR before unchanged tiled VAE decoding");
        pass("capture hashes actual selected model, VAE, tokenizer and producer executable");
        normal.reset();
        fs::rename(model/"yue2-3b-q8_0.gguf",model/"retained-main.gguf");
        fs::rename(model/"sidecars/yue2-qwen.tiktoken",model/"sidecars/retained-tokenizer");
        fs::rename(model/"sidecars/yue2-model-config.json",model/"sidecars/retained-model-config.json");
        text(model/"sidecars/yue2-vae-config.json",R"({"sample_rate":48000,"audio_channels":2,"latent_dim":64,"decode_core_frames":1000,"decode_halo_frames":7})");
        e06_test::reset();auto decoder=session(model);
        auto replay=run(*decoder,{{"acoustic_latents_file",captured.string()}});
        check(replay.audio_output.has_value() && equal(*actual.audio_output,*replay.audio_output),"replay audio bits differ");
        check(e06_test::calls.ar==0 && e06_test::calls.nar==0 && e06_test::calls.abc==0 && e06_test::calls.semantic==0 &&
              e06_test::calls.solve==0 && e06_test::calls.vae==1 && e06_test::calls.decode==2 &&
              e06_test::calls.vae_storage==engine::assets::TensorStorageType::F16,"decode ran other stages or ignored captured defaults");
        pass("actual VAE-only session succeeds with main GGUF, model config and tokenizer absent");
        pass("replay restores captured tile/halo/precision and exact boundary-double PCM");
        auto refined=run(*decoder,{{"acoustic_latents_file",captured.string()},{"acoustic_decode_core_frames","10"},{"acoustic_decode_halo_frames","0"}});
        check(refined.audio_output.has_value() && e06_test::calls.decode==3 && e06_test::calls.decoded_frames.back()==3,"tile refinement not applied");
        pass("explicit tile refinement changes only decoder work");
        decoder.reset();e06_test::reset();auto precise=session(model,"f32");
        check(run(*precise,{{"acoustic_latents_file",captured.string()}}).audio_output.has_value() && e06_test::calls.vae_storage==engine::assets::TensorStorageType::F32,"precision refinement");
        pass("explicit supported VAE precision overrides captured default");precise.reset();
        rejects("generation still requires missing main config",[&]{auto s=session(model);run(*s,{{"cot","off"}});});
        const auto failure_model=model_fixture(root,"failure",tokenizer);
        const auto failed=root/"vae-failed.yac";
        e06_test::reset();e06_test::calls.fail_vae=true;
        {auto s=session(failure_model);rejects("VAE failure after completed capture",[&]{run(*s,{{"cot","off"},{"acoustic_latents_out",failed.string()}});});}
        check(cp::load_completed(failed).metadata.frames==3,"VAE failure lost completed acoustic result");
        e06_test::reset();{auto s=session(failure_model);check(run(*s,{{"acoustic_latents_file",failed.string()}}).audio_output.has_value(),"recovery failed");}
        check(e06_test::calls.solve==0 && e06_test::calls.ar==0 && e06_test::calls.nar==0,"recovery recomputed completed work");
        pass("VAE failure retains reusable checkpoint and retry skips completed AR/NAR");
        e06_test::reset();e06_test::calls.fail_nar=true;
        {auto s=session(failure_model);rejects("NAR exception prevents publication",[&]{run(*s,{{"cot","off"},{"acoustic_latents_out",(root/"nar-failed.yac").string()}});});}
        check(!fs::exists(root/"nar-failed.yac") && e06_test::calls.vae==0,"NAR failure reached VAE or checkpoint");
        e06_test::reset();e06_test::calls.partial_nar=true;
        {auto s=session(failure_model);rejects("partial NAR return cannot masquerade as completed",[&]{run(*s,{{"cot","off"},{"acoustic_latents_out",(root/"nar-partial.yac").string()}});});}
        check(!fs::exists(root/"nar-partial.yac") && e06_test::calls.vae==0,"partial NAR was accepted");
        e06_test::reset();{auto s=session(failure_model);auto result=run(*s,{{"cot","off"},{"acoustic_only","true"},{"acoustic_latents_out",(root/"acoustic-only.yac").string()}});
            check(!result.audio_output.has_value() && e06_test::calls.solve==1 && e06_test::calls.vae==0,"acoustic-only did not stop before VAE");}
        pass("acoustic-only completes capture without constructing VAE or reporting empty audio");
        e06_test::reset();{auto s=session(failure_model);rejects("existing output fails before any generation",[&]{run(*s,{{"cot","off"},{"acoustic_latents_out",failed.string()}});});}
        check(e06_test::calls.ar==0 && e06_test::calls.nar==0 && e06_test::calls.vae==0,"collision wasted generation");
        const auto changed_model=model_fixture(root,"changed-during-nar",tokenizer);
        e06_test::reset();e06_test::calls.during_nar=[&]{std::ofstream f(changed_model/"yue2-3b-q8_0.gguf",std::ios::binary|std::ios::app);f<<'X';};
        {auto s=session(changed_model);rejects("main source mutation across actual NAR boundary",[&]{run(*s,{{"cot","off"},{"acoustic_latents_out",(root/"changed-main.yac").string()}});});}
        check(!fs::exists(root/"changed-main.yac") && e06_test::calls.vae==0,"changed model provenance published");
        const auto changed_vae=model_fixture(root,"changed-vae-during-nar",tokenizer);
        e06_test::reset();e06_test::calls.during_nar=[&]{std::ofstream f(changed_vae/"yue2-vae-f16.gguf",std::ios::binary|std::ios::app);f<<'X';};
        {auto s=session(changed_vae);rejects("VAE source mutation across actual NAR boundary",[&]{run(*s,{{"cot","off"},{"acoustic_latents_out",(root/"changed-vae.yac").string()}});});}
        check(!fs::exists(root/"changed-vae.yac"),"changed VAE provenance published");
        auto mismatch=saved.metadata;auto wrong=saved.latents;
        mismatch.decoder[0]^=1;cp::save_completed(root/"different-vae.yac",mismatch,wrong);
        e06_test::reset();{auto s=session(failure_model);rejects("decoder mismatch rejected before VAE construction",[&]{run(*s,{{"acoustic_latents_file",(root/"different-vae.yac").string()}});});}
        check(e06_test::calls.vae==0 && e06_test::calls.ar==0,"mismatch allocated a runtime");
        e06_test::reset();{auto s=session(failure_model);auto result=run(*s,{{"cot","off"}});check(result.audio_output.has_value() && e06_test::calls.solve==1 && e06_test::calls.decode==2,"ordinary path changed");}
        pass("ordinary unsaved generation retains AR/NAR/VAE stage behavior");
        const auto score=root/"empty-score.json";text(score,"{\"tokens\":[]}");
        e06_test::reset();{auto s=session(failure_model);auto result=run(*s,{{"cot","full"},{"score_tokens_file",score.string()},{"plan_only","true"},{"score_tokens_out",(root/"plan.json").string()}});
            check(!result.audio_output.has_value() && e06_test::calls.ar==0 && e06_test::calls.solve==0 && e06_test::calls.vae==0,"plan-only changed");}
        pass("exact empty-score plan-only behavior remains intact");
        // Warm engine: yue2.keep_resident keeps AR/NAR between requests of one session.
        const auto warm_model=model_fixture(root,"warm",tokenizer);
        const std::unordered_map<std::string,std::string> warm_request={{"cot","off"},{"seed","71"},{"num_inference_steps","4"}};
        engine::runtime::AudioBuffer reference;
        e06_test::reset();{auto s=session(warm_model);reference=*run(*s,warm_request).audio_output;run(*s,warm_request);
            check(e06_test::calls.ar==2 && e06_test::calls.nar==2 && e06_test::calls.vae==1,"default session kept AR/NAR between requests");}
        pass("default session still releases AR/NAR after every request");
        e06_test::reset();{auto s=session(warm_model,"","true");
            auto first=run(*s,warm_request);auto second=run(*s,warm_request);
            check(first.audio_output && second.audio_output && equal(*first.audio_output,reference) && equal(*second.audio_output,reference),"resident output differs from a fresh session");
            check(e06_test::calls.ar==1 && e06_test::calls.nar==1 && e06_test::calls.vae==1 && e06_test::calls.solve==2,"resident runtimes were rebuilt");
            pass("keep_resident reuses AR/NAR/VAE across requests with identical output");
            e06_test::calls.fail_nar=true;
            rejects("resident session reports a failed request",[&]{run(*s,warm_request);});
            e06_test::calls.fail_nar=false;
            auto recovered=run(*s,warm_request);
            check(recovered.audio_output && equal(*recovered.audio_output,reference) && e06_test::calls.ar==1 && e06_test::calls.nar==1,"failed request changed the next resident request");
            pass("keep_resident request after a failed request matches a fresh session");
            auto performance=run(*s,{{"cot","off"},{"seed","71"},{"semantic_only","true"},{"semantic_codes_out",(root/"warm.codes").string()}});
            check(!performance.audio_output.has_value() && e06_test::calls.ar==1 && fs::exists(root/"warm.codes"),"performance-only rebuilt AR or lost codes");}
        rejects("keep_resident requires a boolean",[&]{session(warm_model,"","sometimes");});
        std::cout<<"{\"passed\":"<<passed<<",\"failed\":0,\"model_weights_loaded\":false,\"backend_initialized\":false,\"boundary_doubles\":true}\n";return 0;
    }catch(const std::exception & e){std::cerr<<"FAIL "<<e.what()<<'\n';return 1;}
}
