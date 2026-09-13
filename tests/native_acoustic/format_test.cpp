#include "engine/models/yue2/acoustic_checkpoint.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <vector>
#if !defined(_WIN32)
#include <csignal>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace cp=engine::models::yue2::checkpoint;
using engine::models::yue2::Yue2OdeMethod;
namespace fs=std::filesystem;
namespace {
int passed=0;
void check(bool value,const char * message) { if(!value) throw std::runtime_error(message); }
void pass(const std::string & name) { ++passed;std::cout<<"PASS "<<name<<'\n'; }
template<class F> void rejects(const std::string & name,F fn) {
    try { fn(); } catch(const std::runtime_error & e) { pass(name);return; }
    throw std::runtime_error("Expected rejection: "+name);
}
std::string hex(const cp::Digest & digest) {
    std::ostringstream out;for(auto c:digest) out<<std::hex<<std::setfill('0')<<std::setw(2)<<unsigned(c);return out.str();
}
std::vector<uint8_t> read(const fs::path & p) {
    std::ifstream input(p,std::ios::binary);return {std::istreambuf_iterator<char>(input),{}};
}
void write(const fs::path & p,const std::vector<uint8_t> & bytes) {
    std::ofstream out(p,std::ios::binary);out.write(reinterpret_cast<const char *>(bytes.data()),bytes.size());
    out.close();check(bool(out),"fixture write");
}
void put(std::vector<uint8_t> & b,size_t offset,uint64_t value,size_t n) {
    for(size_t i=0;i<n;++i) b[offset+i]=static_cast<uint8_t>(value>>(8*i));
}
void rehash_header(std::vector<uint8_t> & b) {
    const auto hash=cp::hash_bytes(b.data(),480);std::copy(hash.begin(),hash.end(),b.begin()+480);
}
cp::Metadata metadata(size_t frames=3) {
    cp::Metadata m;m.frames=frames;m.latent_dim=64;m.semantic_frames=frames;
    m.seed=9223372036854775807ULL;m.ode_steps=4;m.context=24576;m.guidance=1.1F;
    m.ode_method=Yue2OdeMethod::AdamsBashforth2;m.semantic_truncated=true;m.vae_storage=2;m.model_storage=5;
    unsigned n=0;
    for(auto * d:{&m.model,&m.decoder,&m.model_config,&m.decoder_config,&m.tokenizer,
                 &m.generation_config,&m.conditioning,&m.semantic_codes,&m.producer_binary}) {
        const auto text="fixture-provenance-"+std::to_string(++n);*d=cp::hash_bytes(text.data(),text.size());
    }
    return m;
}
}
int main(int argc,char ** argv) {
    try {
        check(argc==2,"Usage: format-test NEW_FIXTURE_DIRECTORY");const fs::path root(argv[1]);
        check(fs::create_directory(root),"fixture directory must be new");
        const std::vector<std::pair<std::string,std::string>> vectors={
            {"","e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
            {"abc","ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
            {"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq","248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"},
            {std::string(1000000,'a'),"cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"}};
        for(size_t i=0;i<vectors.size();++i) {
            check(hex(cp::hash_bytes(vectors[i].first.data(),vectors[i].first.size()))==vectors[i].second,"SHA256 vector");
            pass("SHA256 known vector "+std::to_string(i));
        }
        std::ofstream hash_evidence(root/"hashes.jsonl");
        for(size_t n:{0,1,55,56,57,63,64,65,127,128,129,65535,65536,65537}) {
            std::vector<uint8_t> bytes(n);for(size_t i=0;i<n;++i) bytes[i]=uint8_t(i*131+17);
            const auto path=root/("hash-"+std::to_string(n)+".bin");write(path,bytes);
            const auto digest=cp::hash_bytes(bytes.data(),bytes.size());
            check(cp::hash_file(path)==digest,"streamed SHA differs");
            hash_evidence<<"{\"bytes\":"<<n<<",\"sha256\":\""<<hex(digest)<<"\"}\n";
            pass("SHA256 streamed block/padding boundary "+std::to_string(n));
        }
        hash_evidence.close();
        std::vector<float> values(3*64);
        for(size_t i=0;i<values.size();++i) values[i]=(int(i)-80)/71.0F;
        const uint32_t bits[]={0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x7f7fffff,0xff7fffff};
        std::memcpy(values.data(),bits,sizeof(bits));
        const auto original_bits=values;
        const auto path=root/"roundtrip.yac";const auto m=metadata();
        cp::save_completed(path,m,values);const auto saved=cp::load_completed(path);
        check(saved.latents.size()==values.size() && !std::memcmp(saved.latents.data(),values.data(),values.size()*4),"latent bits changed");
        check(!std::memcmp(values.data(),original_bits.data(),values.size()*4),"writer modified source buffer");
        pass("completed F32 roundtrip preserves signed zero, subnormals, and finite extremes");
        check(saved.metadata.frames==3 && saved.metadata.latent_dim==64 && saved.metadata.seed==m.seed &&
              saved.metadata.model==m.model && saved.metadata.decoder==m.decoder && saved.metadata.producer_binary==m.producer_binary &&
              saved.metadata.semantic_truncated && saved.metadata.ode_method==Yue2OdeMethod::AdamsBashforth2 &&
              saved.metadata.vae_storage==2 && saved.metadata.guidance==m.guidance,"metadata changed");
        pass("exact integer/solver/precision/source provenance roundtrip");
        const auto bytes=read(path);check(bytes.size()==512+values.size()*4,"file size");
        check(std::equal(bytes.begin()+512,bytes.begin()+512+sizeof(bits),reinterpret_cast<const uint8_t *>(bits)),"wire layout differs");
        pass("fixed 512-byte header and contiguous frame-major little-endian wire data");
        rejects("existing output collision",[&]{cp::save_completed(path,m,values);});
        check(read(path)==bytes,"collision changed original");pass("collision preserves original bytes");
        fs::create_symlink(root/"does-not-exist",root/"dangling.yac");
        rejects("dangling output symlink",[&]{cp::save_completed(root/"dangling.yac",m,values);});
        check(fs::is_symlink(root/"dangling.yac"),"replaced dangling symlink");
        rejects("missing output directory",[&]{cp::save_completed(root/"absent"/"a.yac",m,values);});
        rejects("directory as checkpoint input",[&]{(void)cp::load_completed(root);});
        rejects("missing checkpoint input",[&]{(void)cp::load_completed(root/"absent.yac");});
        auto mutate=[&](const std::string & name,std::function<void(std::vector<uint8_t> &)> fn,bool reseal=true) {
            auto modified=bytes;fn(modified);if(reseal) rehash_header(modified);
            const auto file=root/(name+".yac");write(file,modified);
            rejects(name,[&]{(void)cp::load_completed(file);});
        };
        mutate("bad-magic",[](auto & b){b[0]='X';});
        mutate("wrong-version",[](auto & b){put(b,8,2,4);});
        mutate("wrong-header-length",[](auto & b){put(b,12,256,4);});
        mutate("wrong-dtype",[](auto & b){put(b,16,2,4);});
        mutate("wrong-layout",[](auto & b){put(b,20,2,4);});
        mutate("not-completed",[](auto & b){put(b,24,0,4);});
        mutate("unknown-flags",[](auto & b){put(b,28,2,4);});
        mutate("zero-frames",[](auto & b){put(b,32,0,8);});
        mutate("overflow-shape",[](auto & b){put(b,32,INT64_MAX,8);put(b,136,INT64_MAX,8);});
        mutate("negative-shape-wire",[](auto & b){put(b,40,UINT64_MAX,8);});
        mutate("wrong-payload-bytes",[](auto & b){put(b,48,4,8);});
        mutate("rate-narrowing",[](auto & b){put(b,56,UINT64_C(1)<<32,8);});
        mutate("channel-narrowing",[](auto & b){put(b,64,UINT64_C(1)<<32,8);});
        mutate("zero-ratio",[](auto & b){put(b,72,0,8);});
        mutate("zero-core",[](auto & b){put(b,80,0,8);});
        mutate("negative-halo-wire",[](auto & b){put(b,88,UINT64_MAX,8);});
        mutate("unknown-storage",[](auto & b){put(b,104,7,4);});
        mutate("invalid-seed",[](auto & b){put(b,112,UINT64_C(1)<<63,8);});
        mutate("zero-solver-steps",[](auto & b){put(b,120,0,8);});
        mutate("unknown-solver",[](auto & b){put(b,128,3,4);});
        mutate("unknown-decoder-contract",[](auto & b){put(b,132,2,4);});
        mutate("partial-nar-frame-count",[](auto & b){put(b,136,4,8);});
        mutate("nonfinite-guidance",[](auto & b){put(b,152,0x7fc00000,4);});
        mutate("nonzero-reserved-bytes",[](auto & b){put(b,156,1,4);});
        mutate("missing-model-provenance",[](auto & b){std::fill(b.begin()+192,b.begin()+224,0);});
        mutate("corrupt-header-digest",[](auto & b){b[480]^=1;},false);
        mutate("corrupt-payload",[](auto & b){b.back()^=1;});
        mutate("nonfinite-payload",[](auto & b){put(b,512,0x7fc00000,4);const auto h=cp::hash_bytes(b.data()+512,b.size()-512);std::copy(h.begin(),h.end(),b.begin()+160);});
        mutate("payload-infinity",[](auto & b){put(b,512,0x7f800000,4);const auto h=cp::hash_bytes(b.data()+512,b.size()-512);std::copy(h.begin(),h.end(),b.begin()+160);});
        mutate("trailing-payload",[](auto & b){b.push_back(0);});
        mutate("truncated-payload",[](auto & b){b.pop_back();});
        write(root/"partial-header.yac",std::vector<uint8_t>(511));
        rejects("partial header",[&]{(void)cp::load_completed(root/"partial-header.yac");});
        auto invalid=values;invalid[100]=std::numeric_limits<float>::quiet_NaN();
        rejects("writer rejects nonfinite latents",[&]{cp::save_completed(root/"nonfinite-output.yac",m,invalid);});
        check(!fs::exists(root/"nonfinite-output.yac"),"published nonfinite output");
        for(const auto & entry:fs::directory_iterator(root)) check(entry.path().string().find(".partial-")==std::string::npos,"leaked owned partial");
        pass("failed save publishes nothing and cleans only its owned partial");
        auto incomplete=m;incomplete.semantic_frames=4;
        rejects("writer rejects partial NAR shape",[&]{cp::save_completed(root/"incomplete.yac",incomplete,values);});
        cp::require_decoder(saved.metadata,m.vae,m.decoder);pass("matching decoder accepted");
        auto different=m.decoder;different[0]^=1;
        rejects("different decoder bytes",[&]{cp::require_decoder(saved.metadata,m.vae,different);});
        auto v=m.vae;v.latent_dim=32;
        rejects("incompatible decoder latent shape",[&]{cp::require_decoder(saved.metadata,v,m.decoder);});
        v=m.vae;v.sample_rate=44100;
        rejects("incompatible decoder rate",[&]{cp::require_decoder(saved.metadata,v,m.decoder);});
        v=m.vae;v.decode_core_frames=16;v.decode_halo_frames=8;
        cp::require_decoder(saved.metadata,v,m.decoder);pass("compatible tile refinement is not mistaken for incompatible model identity");
        auto other_producer=saved.metadata;other_producer.producer_binary={};other_producer.model={};
        cp::require_decoder(other_producer,m.vae,m.decoder);pass("decoder compatibility does not require historical main weights or executable");
        const auto stamp=cp::file_identity(path);cp::require_unchanged(path,stamp);
        fs::rename(path,root/"original-roundtrip.yac");write(path,bytes);
        rejects("replaced equal-sized source identity",[&]{cp::require_unchanged(path,stamp);});
#if !defined(_WIN32)
        check(::mkfifo((root/"input.fifo").c_str(),0600)==0,"FIFO fixture");
        rejects("nonregular FIFO input rejects without waiting for a writer",[&]{(void)cp::load_completed(root/"input.fifo");});
        rejects("source hash also rejects FIFO without blocking",[&]{(void)cp::hash_file(root/"input.fifo");});
        const pid_t child=fork();check(child>=0,"fork failed");
        if(child==0) {
            std::signal(SIGXFSZ,SIG_IGN);struct rlimit limit{650,650};
            if(setrlimit(RLIMIT_FSIZE,&limit)!=0) _exit(3);
            try { cp::save_completed(root/"short-write.yac",m,values);_exit(4); }
            catch(const std::runtime_error &) { _exit(fs::exists(root/"short-write.yac")?5:0); }
        }
        int status=0;check(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0,"short write handling failed");
        pass("actual OS short-write limit leaves no published checkpoint");
        const auto race=root/"race.yac";pid_t children[2];
        for(int i=0;i<2;++i) {
            children[i]=fork();check(children[i]>=0,"fork failed");
            if(children[i]==0) { auto input=values;input.back()=float(i+10);
                try { cp::save_completed(race,m,input);_exit(0); }catch(const std::runtime_error &) { _exit(1); } }
        }
        int winners=0;
        for(auto pid:children) { int s=0;check(waitpid(pid,&s,0)==pid && WIFEXITED(s) && WEXITSTATUS(s)<=1,"save race child failed");winners+=WEXITSTATUS(s)==0; }
        check(winners==1,"atomic publication did not have exactly one winner");
        const auto winner=cp::load_completed(race);check(winner.latents.back()==10 || winner.latents.back()==11,"race corrupted payload");
        pass("two concurrent publishers preserve exactly one complete winner");
#endif
        std::cout<<"{\"passed\":"<<passed<<",\"failed\":0,\"model_weights_loaded\":false,\"backend_initialized\":false}\n";
        return 0;
    } catch(const std::exception & e) { std::cerr<<"FAIL "<<e.what()<<'\n';return 1; }
}
