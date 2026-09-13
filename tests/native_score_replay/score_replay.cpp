#include "backend_doubles.h"
#include "original_pipeline.h"
#include "engine/models/yue2/pipeline.h"
#include "engine/models/yue2/request.h"
#include "engine/framework/io/filesystem.h"
#include "engine/framework/io/json.h"

#include <algorithm>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>

using namespace engine::models::yue2;
namespace json = engine::io::json;
namespace {
int passed = 0;
std::ofstream evidence;
void require(bool value, const std::string & message) { if (!value) throw std::runtime_error(message); }
void pass(const std::string & name) { ++passed; std::cout << "PASS " << name << '\n'; }
template<class F> void rejects(const std::string & name, F operation) {
    try { operation(); } catch (const std::runtime_error &) { pass(name); return; }
    throw std::runtime_error("Expected rejection: " + name);
}
json::Value ids(const std::vector<int32_t> & values) {
    json::Value::Array out;
    for (auto value : values) out.push_back(json::Value::make_number(value));
    return json::Value::make_array(std::move(out));
}
void record(const std::string & name, const Yue2Plan & plan, const u07_test::Calls & calls) {
    evidence << json::stringify(json::Value::make_object({
        {"case", json::Value::make_string(name)}, {"abc_ids", ids(plan.abc_ids)},
        {"positive", ids(plan.prefix)}, {"cfg_negative", ids(calls.negative)},
        {"abc_calls", json::Value::make_number(calls.abc)},
        {"semantic_calls", json::Value::make_number(calls.semantic)},
        {"truncated", json::Value::make_bool(plan.truncated)}
    })) << '\n';
}
std::filesystem::path write(const std::filesystem::path & root, const std::string & name, const std::string & text) {
    const auto path = root / name;
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    file.write(text.data(), text.size());
    file.close();
    require(bool(file), "fixture write failed");
    return path;
}
Yue2Request parse(const std::unordered_map<std::string, std::string> & options) {
    engine::runtime::TaskRequest request;
    request.options = options;
    return parse_yue2_request(request, Yue2GenerationConfig{});
}
template<class Pipeline> std::unique_ptr<Pipeline> make_pipeline(engine::core::ExecutionContext & execution,
    const std::shared_ptr<const Yue2Assets> & assets) {
    return std::make_unique<Pipeline>(execution, assets, engine::assets::TensorStorageType::Native,
        engine::assets::TensorStorageType::Native, 0, 0, 0, 0, 0, 0);
}
void delimiters(const Yue2Plan & plan, const u07_test::Calls & calls, const std::vector<int32_t> & score) {
    for (const auto * prefix : {&plan.prefix, &calls.negative}) {
        for (auto token : {kEodToken, kAbcStartToken, kAbcEndToken, kMusicStartToken})
            require(std::count(prefix->begin(), prefix->end(), token) == 1, "Delimiter multiplicity changed");
        require(prefix->at(prefix->size() - 2) == kAbcEndToken && prefix->back() == kMusicStartToken,
            "Missing final score/music delimiters");
        const auto begin = std::find(prefix->begin(), prefix->end(), kAbcStartToken) + 1;
        const auto end = prefix->end() - 2;
        require(std::vector<int32_t>(begin, end) == score, "Raw token sequence changed inside delimiters");
    }
}
}

int main(int argc, char ** argv) {
    try {
        require(argc == 5, "Usage: score-replay-test TOKENIZER COUNTEREXAMPLE FIXTURE_DIR EVIDENCE");
        evidence.open(argv[4], std::ios::trunc);
        require(bool(evidence), "Could not open evidence");
        const std::filesystem::path fixtures(argv[3]);
        const auto counterexample = json::parse_file(argv[2]);
        const auto raw = json::number_array_as<int32_t>(counterexample.require("original_tokens"));
        Yue2TextTokenizer tokenizer(argv[1]);
        const std::string visible = "X:1\nT:Echo\nM:4/4\nL:1/8\nK:C\nCDEF GABc|";
        const auto encoded = json::number_array_as<int32_t>(counterexample.require("reencoded_tokens"));
        require(raw.size() == 37 && encoded.size() == 29 && tokenizer.encode(visible) == encoded && encoded != raw,
            "The noncanonical token counterexample stopped reproducing");
        pass("actual native tokenizer reproduces the 37-to-29 same-text counterexample");

        auto mutable_assets = std::make_shared<Yue2Assets>();
        mutable_assets->tiktoken_path = argv[1];
        std::shared_ptr<const Yue2Assets> assets = mutable_assets;
        engine::core::ExecutionContext execution(engine::core::BackendConfig{});
        auto original = make_pipeline<OriginalYue2PipelineRuntime>(execution, assets);
        auto candidate = make_pipeline<Yue2PipelineRuntime>(execution, assets);

        const auto score_file = write(fixtures, "score.json", json::stringify(json::Value::make_object({
            {"tokens", ids(raw)}, {"truncated", json::Value::make_bool(true)}})));
        const auto parsed = parse({{"score_tokens_file", score_file.string()}, {"cot", "full"}});
        require(parsed.score_tokens.has_value() && parsed.score_tokens->ids == raw && parsed.score_tokens->truncated,
            "File parser lost raw IDs or truncation provenance");
        pass("saved-plan JSON retains raw IDs and truncation flag");
        engine::runtime::SessionPreparationRequest preparation;
        preparation.options = {{"score_tokens_file", score_file.string()}, {"cot", "melody"}};
        require(parse_yue2_preparation_request(preparation, {}).score_tokens->ids == raw, "Preparation parser differs");
        pass("session preparation accepts the same exact-score input");

        const std::string decorated = " \nX:2\nT:夜の港 — ميناء\nM:4/4\nL:1/8\nK:Dm\nD2 EF | G2 A2 |\n  ";
        auto decorated_ids = tokenizer.encode(decorated);
        const std::vector<std::pair<std::string, std::vector<int32_t>>> scores = {
            {"noncanonical", raw}, {"empty", {}}, {"whitespace-unicode", decorated_ids},
            {"sampling-range-edges", {0, kEodToken - 1}}
        };
        std::vector<int32_t> unchanged_positive, unchanged_negative;
        for (const auto mode : {Yue2CotMode::Melody, Yue2CotMode::Full}) {
            for (const auto & [label, score] : scores) {
                Yue2Request request;
                request.cot = mode;
                request.style = "low drums; gathered voices";
                request.lyrics = "우리는 항구의 빛\nيا بحر يا صوتنا\n";
                request.seed = 987654321;
                request.cfg_scale = 1.17F;
                // Match a deliberately truncated sampled plan as well as ordinary endings.
                request.generation.abc.max_tokens = label == "noncanonical" ? score.size() : 4096;
                request.generation.abc.min_tokens = 0;
                u07_test::reset(score);
                auto expected = original->generate_semantic(request, original->plan(request));
                const auto expected_calls = u07_test::calls;
                require(expected_calls.abc == 1 && expected_calls.semantic == 1, "Original generated branch was not exercised");
                require(expected_calls.abc_window.begin == 0 && expected_calls.abc_window.end == kEodToken &&
                    expected_calls.abc_window.stop_token == kAbcEndToken, "Raw-token validation no longer matches sampling window");

                request.score_tokens = Yue2ScoreTokens{score, expected.plan.truncated};
                u07_test::reset();
                auto actual = candidate->generate_semantic(request, candidate->plan(request));
                const auto actual_calls = u07_test::calls;
                require(actual_calls.abc == 0 && actual_calls.semantic == 1, "Replay did not bypass only ABC generation");
                require(actual.plan.abc_ids == expected.plan.abc_ids && actual.plan.prefix == expected.plan.prefix &&
                    actual_calls.positive == expected_calls.positive && actual_calls.negative == expected_calls.negative,
                    "Replay prefix differs from original generated-plan prefix");
                require(actual.plan.truncated == expected.plan.truncated && actual.tokens == expected.tokens &&
                    actual_calls.seed == expected_calls.seed && actual_calls.guidance == expected_calls.guidance,
                    "Replay changed stage metadata or semantic generation inputs");
                delimiters(actual.plan, actual_calls, score);
                const auto name = std::string(cot_mode_name(mode)) + "/" + label;
                record("original/" + name, expected.plan, expected_calls);
                record("replay/" + name, actual.plan, actual_calls);
                pass(name + ": exact positive and CFG-negative arrays, delimiters and sampler arguments");
                if (mode == Yue2CotMode::Full && label == "noncanonical") {
                    unchanged_positive = actual.plan.prefix;
                    unchanged_negative = actual_calls.negative;
                }
            }
        }

        for (const std::string change : {"lyrics", "style", "mode"}) {
            Yue2Request request;
            request.cot = change == "mode" ? Yue2CotMode::Melody : Yue2CotMode::Full;
            request.style = change == "style" ? "muted strings and reeds" : "low drums; gathered voices";
            request.lyrics = change == "lyrics" ? "새벽의 항구\nيا ليل\n" : "우리는 항구의 빛\nيا بحر يا صوتنا\n";
            u07_test::reset(raw);
            auto expected = original->generate_semantic(request, original->plan(request));
            const auto baseline_calls = u07_test::calls;
            request.score_tokens = Yue2ScoreTokens{raw, false};
            u07_test::reset();
            auto actual = candidate->generate_semantic(request, candidate->plan(request));
            require(actual.plan.prefix == expected.plan.prefix && u07_test::calls.negative == baseline_calls.negative,
                "Changed conditioning differs from the original branch");
            require(actual.plan.prefix != unchanged_positive, "Intentional conditioning change was ignored");
            require((u07_test::calls.negative != unchanged_negative) == (change == "mode"),
                "Lyrics/style changed unconditional CFG or mode did not");
            delimiters(actual.plan, u07_test::calls, raw);
            record("changed-" + change, actual.plan, u07_test::calls);
            pass("changed " + change + " affects only original conditioning while retaining raw score");
        }

        for (const auto mode : {Yue2CotMode::Off, Yue2CotMode::Melody, Yue2CotMode::Full}) {
            for (bool supplied_text : {false, true}) {
                if (mode == Yue2CotMode::Off && supplied_text) continue;
                Yue2Request request;
                request.cot = mode;
                request.abc = supplied_text ? visible : "";
                request.style = "";
                request.lyrics = "";
                u07_test::reset(raw);
                auto expected = original->generate_semantic(request, original->plan(request));
                const auto original_calls = u07_test::calls;
                u07_test::reset(raw);
                auto actual = candidate->generate_semantic(request, candidate->plan(request));
                require(actual.plan.prefix == expected.plan.prefix && actual.plan.abc_ids == expected.plan.abc_ids &&
                    u07_test::calls.negative == original_calls.negative && u07_test::calls.abc == original_calls.abc,
                    "An existing text/direct/generated mode changed");
                pass(std::string("existing ") + cot_mode_name(mode) + (supplied_text ? " supplied-text" : " absent-score") + " path unchanged");
            }
        }

        for (bool empty : {false, true}) {
            auto standalone = make_pipeline<Yue2PipelineRuntime>(execution, assets);
            auto request = parsed;
            request.plan_only = true;
            request.score_tokens = Yue2ScoreTokens{empty ? std::vector<int32_t>{} : raw, true};
            request.score_tokens_out = (fixtures / (empty ? "empty-replayed.plan.json" : "replayed.plan.json")).string();
            u07_test::reset();
            auto output = standalone->run(request);
            const auto saved = json::parse_file(request.score_tokens_out);
            require(output.samples.empty() && u07_test::calls.ar_constructed == 0 && u07_test::calls.abc == 0 &&
                u07_test::calls.semantic == 0, "Exact plan-only replay initialized or invoked AR");
            require(json::number_array_as<int32_t>(saved.require("tokens")) == request.score_tokens->ids &&
                saved.require("truncated").as_bool(), "Plan-only round trip changed raw IDs or metadata");
            pass(std::string(empty ? "empty" : "nonempty") + " exact plan-only round trip creates no AR/backend/model");
        }

        const auto empty_file = write(fixtures, "empty.json", "{\"tokens\":[]}");
        require(parse({{"score_tokens_file", empty_file.string()}}).score_tokens.has_value(), "Empty score was treated as absent");
        require(!parse({{"abc", ""}}).score_tokens.has_value() && !parse({}).score_tokens.has_value(), "Absent score became present");
        pass("explicit empty token vector remains distinct from absent tokens and empty text");
        for (const std::string key : {"abc", "abc_file"}) {
            rejects("score/text ambiguity: " + key + " even when empty", [&] {
                parse({{"score_tokens_file", score_file.string()}, {key, ""}});
            });
        }
        rejects("missing score file", [&] { parse({{"score_tokens_file", (fixtures / "missing.json").string()}}); });
        rejects("empty score file path", [&] { parse({{"score_tokens_file", ""}}); });
        rejects("score replay in direct mode", [&] { parse({{"score_tokens_file", empty_file.string()}, {"cot", "off"}}); });
        const std::vector<std::pair<std::string, std::string>> bad = {
            {"null-root", "null"}, {"array-root", "[]"}, {"missing-tokens", "{}"},
            {"null-tokens", "{\"tokens\":null}"}, {"non-array", "{\"tokens\":3}"},
            {"fraction", "{\"tokens\":[0.5]}"}, {"negative", "{\"tokens\":[-1]}"},
            {"rounded-fraction", "{\"tokens\":[1.0000000000000000001]}"},
            {"positive-underflow", "{\"tokens\":[1e-999]}"},
            {"negative-underflow", "{\"tokens\":[-1e-999]}"},
            {"decimal-whole", "{\"tokens\":[1.0]}"}, {"exponent-whole", "{\"tokens\":[1e0]}"},
            {"uppercase-exponent", "{\"tokens\":[1E0]}"}, {"leading-zero", "{\"tokens\":[01]}"},
            {"leading-zero-negative", "{\"tokens\":[-01]}"},
            {"big-integer", "{\"tokens\":[99999999999999999999999999999999999]}"},
            {"huge", "{\"tokens\":[1e100]}"}, {"nonfinite", "{\"tokens\":[1e999]}"},
            {"boolean", "{\"tokens\":[true]}"}, {"string", "{\"tokens\":[\"1\"]}"},
            {"nested", "{\"tokens\":[[1]]}"}, {"truncated-type", "{\"tokens\":[],\"truncated\":1}"},
            {"duplicate-tokens", "{\"tokens\":[1],\"tokens\":[2]}"},
            {"duplicate-truncated", "{\"tokens\":[],\"truncated\":true,\"truncated\":false}"},
            {"trailing", "{\"tokens\":[]} []"}, {"incomplete", "{\"tokens\":[1]"},
            {"embedded-nul", std::string("{\"tokens\":[]}") + '\0' + "{}"}
        };
        for (const auto & [name, text] : bad) {
            auto file = write(fixtures, "bad-" + name + ".json", text);
            rejects("malformed score: " + name, [&] { parse({{"score_tokens_file", file.string()}}); });
        }
        const std::vector<std::pair<std::string, std::string>> valid_lexemes = {
            {"decimal-boundaries", "{\"tokens\":[0,1,151642,-0]}"},
            {"escaped-field-name", "{\"\\u0074okens\":[0,1,151642,-0]}"},
            {"unrelated-numeric-metadata", "{\"duration\":1.0000000000000000001,\"nested\":{\"tokens\":[1e-999]},\"tokens\":[0,1,151642,-0]}"},
            {"quoted-decoy-metadata", "{\"text\":\"\\\"tokens\\\":[1e-999]\",\"array\":[null,{\"x\":1.1}],\"tokens\":[0,1,151642,-0],\"after\":1e-999}"}
        };
        for (const auto & [name, text] : valid_lexemes) {
            auto file = write(fixtures, "valid-" + name + ".json", text);
            auto value = parse({{"score_tokens_file", file.string()}});
            require(value.score_tokens->ids == std::vector<int32_t>({0, 1, 151642, 0}), "Exact integer lexemes changed");
            pass("exact decimal ID parser: " + name);
        }
        const std::vector<std::pair<std::string, std::string>> bad_keys = {
            {"nul-suffixed-tokens-only", R"({"tokens\u0000suffix":[1]})"},
            {"escaped-equivalent-tokens", R"({"tokens":[1],"\u0074okens":[2]})"},
            {"escaped-equivalent-truncated", R"({"tokens":[],"truncated":true,"\u0074runcated":false})"},
            {"same-full-nul-key", R"({"x\u0000a":1,"\u0078\u0000\u0061":2,"tokens":[1]})"},
            {"same-two-byte-key", R"({"é\u0000x":1,"\u00e9\u0000x":2,"tokens":[1]})"},
            {"same-three-byte-key", R"({"한\u0000x":1,"\ud55c\u0000x":2,"tokens":[1]})"},
            {"same-supplementary-key", R"({"🙂\u0000x":1,"\ud83d\ude42\u0000x":2,"tokens":[1]})"},
            {"same-escaped-controls", R"({"q\/\"\\\b\f\n\r\t\u0000x":1,"\u0071\u002f\u0022\u005c\u0008\u000c\u000a\u000d\u0009\u0000\u0078":2,"tokens":[1]})"},
            {"invalid-key-hex", R"({"x\uQQ00":1,"tokens":[1]})"}
        };
        for (const auto & [name, text] : bad_keys) {
            auto file = write(fixtures, "bad-key-" + name + ".json", text);
            rejects("full JSON field identity: " + name, [&] { parse({{"score_tokens_file", file.string()}}); });
        }
        struct KeyCase { std::string name, text; bool truncated; };
        const std::vector<KeyCase> valid_keys = {
            {"nul-suffixed-truncated-is-metadata", R"({"tokens":[1],"truncated\u0000suffix":true})", false},
            {"distinct-full-nul-keys", R"({"x\u0000a":1,"x\u0000b":2,"tokens":[1]})", false},
            {"decoys-before-fields", R"({"tokens\u0000suffix":[1e-999],"truncated\u0000suffix":null,"tokens":[1],"truncated":true})", true},
            {"decoys-after-fields", R"({"tokens":[1],"truncated":true,"tokens\u0000suffix":null,"truncated\u0000suffix":false})", true},
            {"empty-and-nul-keys", R"({"":1,"\u0000":2,"tokens":[1]})", false},
            {"nul-before-reserved-key", R"({"\u0000tokens":null,"tokens":[1]})", false},
            {"literal-backslash-u-decoy", R"({"tokens\\u0000suffix":[1e-999],"tokens":[1]})", false},
            {"distinct-supplementary-suffixes", R"({"x\u0000🙂":1,"x\u0000\ud83d\ude00":2,"tokens":[1]})", false},
            {"unrelated-metadata-unchanged", R"({"metadata":{"same":1,"same":2,"nul":"a\u0000b"},"tokens":[1]})", false},
            {"repeated-nul-key-bytes", R"({"x\u0000\u0000a":1,"x\u0000a":2,"tokens":[1]})", false}
        };
        for (const auto & fixture : valid_keys) {
            auto file = write(fixtures, "valid-key-" + fixture.name + ".json", fixture.text);
            auto value = parse({{"score_tokens_file", file.string()}});
            require(value.score_tokens->ids == std::vector<int32_t>({1}) &&
                    value.score_tokens->truncated == fixture.truncated, "Metadata key aliased a score field");
            pass("full JSON field identity: " + fixture.name);
        }
        for (int32_t token : {kEodToken, kAbcStartToken, kAbcEndToken, kMusicStartToken, kMusicEndToken, kCodecOffset, kVocabSize,
                std::numeric_limits<int32_t>::min(), std::numeric_limits<int32_t>::max()}) {
            auto file = write(fixtures, "bad-token-" + std::to_string(token) + ".json", "{\"tokens\":[" + std::to_string(token) + "]}");
            rejects("out-of-window structural/range ID " + std::to_string(token), [&] { parse({{"score_tokens_file", file.string()}}); });
        }
        for (auto invalid : {Yue2ScoreTokens{{kAbcEndToken}, false}, Yue2ScoreTokens{{-1}, false}}) {
            auto request = parsed;
            request.score_tokens = invalid;
            rejects("typed pipeline rejects invalid score IDs", [&] { candidate->plan(request); });
            rejects("typed continuation rejects invalid score IDs", [&] { candidate->generate_semantic(request, {}); });
        }
        auto conflicting = parsed;
        conflicting.abc = visible;
        rejects("typed pipeline rejects simultaneous text", [&] { candidate->plan(conflicting); });
        conflicting.abc.clear();
        conflicting.cot = Yue2CotMode::Off;
        rejects("typed pipeline rejects direct-mode score input", [&] { candidate->plan(conflicting); });

        evidence.flush();
        require(bool(evidence), "Failed to flush prefix evidence");
        std::cout << "{\"passed\":" << passed << ",\"failed\":0,\"native_pipeline_pairs\":16,"
                  << "\"sampler_boundary_double\":true,\"backend_initialized\":false,\"model_weights_loaded\":false}\n";
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "FAIL " << error.what() << '\n';
        return 1;
    }
}
