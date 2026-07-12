#include "four_man_core.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace omega_tb4 {
namespace {

struct Successors {
    std::vector<std::uint32_t> same_class;
    bool has_move = false;
    bool external_loss = false;
    bool external_draw = false;
    bool external_win = false;
    bool outside_draw = false;
};

struct Counts {
    std::uint64_t invalid = 0;
    std::uint64_t loss = 0;
    std::uint64_t draw = 0;
    std::uint64_t win = 0;
};

class FourManSolver {
public:
    FourManSolver(const Geometry & geometry, const D4Indexer & four_index,
                  const KrkTable & krk, std::uint32_t limit, bool verbose)
        : geometry_(geometry), index_(four_index), krk_(krk), limit_(limit), verbose_(verbose) {
        if (limit_ == 0 || limit_ > index_.state_count())
            throw std::invalid_argument("four-man state limit is out of range");
        status_.assign(limit_, Invalid);
        remaining_.assign(limit_, 0);
        queue_.reserve(limit_ == index_.state_count() ? Four_Man_Legal_Count : limit_);
    }

    const std::vector<std::uint8_t> & solve() {
        const auto started = std::chrono::steady_clock::now();
        legal_count_ = 0;
        for (std::uint32_t dense = 0; dense < limit_; ++dense) {
            State4 state = unrank(dense);
            if (!is_legal(state)) continue;
            ++legal_count_;
            status_[dense] = Unknown;
            const Successors moves = successors(state);
            if (!moves.has_move) {
                status_[dense] = in_check(state) ? Loss : Draw;
                if (status_[dense] == Loss) queue_.push_back(dense);
            } else if (moves.external_loss) {
                status_[dense] = Win;
                queue_.push_back(dense);
            } else {
                const std::size_t unresolved = moves.same_class.size() +
                    static_cast<std::size_t>(moves.external_draw || moves.outside_draw);
                if (unresolved > 255) throw std::logic_error("successor counter exceeds one byte");
                remaining_[dense] = static_cast<std::uint8_t>(unresolved);
                if (unresolved == 0) {
                    // At least one move exists, and every external successor is a win.
                    status_[dense] = Loss;
                    queue_.push_back(dense);
                }
            }
            progress("enumerate", dense + 1, started);
        }

        std::size_t head = 0;
        while (head < queue_.size()) {
            const std::uint32_t child = queue_[head++];
            const std::uint8_t child_outcome = status_[child];
            if (child_outcome != Loss && child_outcome != Win)
                throw std::logic_error("retrograde queue contains a non-decisive state");
            const State4 state = unrank(child);
            for (std::uint32_t parent : predecessors(state)) {
                if (status_[parent] != Unknown) continue;
                if (child_outcome == Loss) {
                    status_[parent] = Win;
                    queue_.push_back(parent);
                } else {
                    if (remaining_[parent] == 0)
                        throw std::logic_error("retrograde successor counter underflow");
                    --remaining_[parent];
                    if (remaining_[parent] == 0) {
                        status_[parent] = Loss;
                        queue_.push_back(parent);
                    }
                }
            }
            progress("retrograde", static_cast<std::uint32_t>(head), started);
        }

        for (std::uint8_t & outcome : status_)
            if (outcome == Unknown) outcome = Draw;

        if (limit_ == index_.state_count() && legal_count_ != Four_Man_Legal_Count)
            throw std::logic_error("full KRKC legal-state count changed");
        return status_;
    }

    Counts counts() const {
        Counts result;
        for (std::uint8_t outcome : status_) {
            if (outcome == Invalid) ++result.invalid;
            else if (outcome == Loss) ++result.loss;
            else if (outcome == Draw) ++result.draw;
            else if (outcome == Win) ++result.win;
            else throw std::logic_error("unresolved four-man outcome");
        }
        return result;
    }

    std::uint64_t legal_count() const { return legal_count_; }

    void verify() const {
        const auto started = std::chrono::steady_clock::now();
        std::uint64_t legal = 0;
        for (std::uint32_t dense = 0; dense < limit_; ++dense) {
            const State4 state = unrank(dense);
            if (!is_legal(state)) {
                if (status_[dense] != Invalid)
                    throw std::logic_error("illegal state has a WDL outcome");
                continue;
            }
            ++legal;
            const Successors moves = successors(state);
            std::uint8_t expected = Draw;
            if (!moves.has_move) {
                expected = in_check(state) ? Loss : Draw;
            } else {
                bool child_loss = moves.external_loss;
                bool child_draw = moves.external_draw || moves.outside_draw;
                bool all_win = !child_draw && !child_loss;
                for (std::uint32_t child : moves.same_class) {
                    const std::uint8_t outcome = status_[child];
                    child_loss = child_loss || outcome == Loss;
                    child_draw = child_draw || outcome == Draw;
                    all_win = all_win && outcome == Win;
                }
                if (child_loss) expected = Win;
                else if (all_win) expected = Loss;
                else expected = Draw;
            }
            if (status_[dense] != expected)
                throw std::logic_error("KRKC Bellman mismatch at dense index " +
                                       std::to_string(dense));
            progress("verify", dense + 1, started);
        }
        if (legal != legal_count_) throw std::logic_error("verification legal count changed");
    }

    bool is_legal(const State4 & state) const {
        const auto squares = state.squares();
        if (state.turn > 1) return false;
        for (int i = 0; i < 4; ++i) {
            if (squares[i] >= Square_Count) return false;
            for (int j = 0; j < i; ++j)
                if (squares[i] == squares[j]) return false;
        }
        if (geometry_.king_attacks(state.rook_king, state.champion_king)) return false;
        if (state.turn == Champion_To_Move)
            return !geometry_.champion_attacks(state.champion, state.rook_king);
        return !geometry_.rook_attacks(state.rook, state.champion_king,
                                       { state.rook_king, state.champion });
    }

    bool in_check(const State4 & state) const {
        if (state.turn == Rook_To_Move)
            return geometry_.champion_attacks(state.champion, state.rook_king);
        return geometry_.rook_attacks(state.rook, state.champion_king,
                                      { state.rook_king, state.champion });
    }

    Successors successors(const State4 & state) const {
        if (!is_legal(state)) throw std::logic_error("successors requested for illegal KRKC state");
        Successors result;
        result.same_class.reserve(32);

        auto add_child = [&](const State4 & child) {
            if (!is_legal(child)) return;
            result.has_move = true;
            const auto squares = child.squares();
            const std::uint32_t dense = index_.rank(squares.data(), child.turn);
            if (dense < limit_) result.same_class.push_back(dense);
            else result.outside_draw = true;
        };
        auto add_external = [&](std::uint8_t outcome) {
            result.has_move = true;
            if (outcome == Loss) result.external_loss = true;
            else if (outcome == Draw) result.external_draw = true;
            else if (outcome == Win) result.external_win = true;
            else throw std::logic_error("external dependency returned no WDL result");
        };

        if (state.turn == Rook_To_Move) {
            for (std::uint8_t target : geometry_.king_moves(state.rook_king)) {
                if (target == state.rook || target == state.champion_king) continue;
                if (target == state.champion) {
                    if (geometry_.king_attacks(target, state.champion_king)) continue;
                    const State3 dependency { target, state.rook, state.champion_king, Weak_To_Move };
                    add_external(krk_.probe(dependency));
                } else {
                    add_child(State4 { target, state.rook, state.champion_king,
                                       state.champion, Champion_To_Move });
                }
            }
            for (const auto & ray : geometry_.rook_rays(state.rook)) {
                for (std::uint8_t target : ray) {
                    if (target == state.rook_king || target == state.champion_king ||
                        target == state.champion) {
                        if (target == state.champion) {
                            const State3 dependency { state.rook_king, target,
                                                      state.champion_king, Weak_To_Move };
                            add_external(krk_.probe(dependency));
                        }
                        break;
                    }
                    add_child(State4 { state.rook_king, target, state.champion_king,
                                       state.champion, Champion_To_Move });
                }
            }
        } else {
            for (std::uint8_t target : geometry_.king_moves(state.champion_king)) {
                if (target == state.champion || target == state.rook_king) continue;
                if (target == state.rook) {
                    if (!geometry_.king_attacks(target, state.rook_king)) add_external(Draw);
                } else {
                    add_child(State4 { state.rook_king, state.rook, target,
                                       state.champion, Rook_To_Move });
                }
            }
            for (std::uint8_t target : geometry_.champion_moves(state.champion)) {
                if (target == state.champion_king || target == state.rook_king) continue;
                if (target == state.rook) {
                    add_external(Draw); // Current KCK insufficient-material policy.
                } else {
                    add_child(State4 { state.rook_king, state.rook, state.champion_king,
                                       target, Rook_To_Move });
                }
            }
        }

        std::sort(result.same_class.begin(), result.same_class.end());
        result.same_class.erase(std::unique(result.same_class.begin(), result.same_class.end()),
                                result.same_class.end());
        return result;
    }

    std::vector<std::uint32_t> predecessors(const State4 & state) const {
        if (!is_legal(state)) throw std::logic_error("predecessors requested for illegal KRKC state");
        std::vector<std::uint32_t> result;
        result.reserve(32);
        auto add_parent = [&](const State4 & parent) {
            if (!is_legal(parent)) return;
            const auto squares = parent.squares();
            const std::uint32_t dense = index_.rank(squares.data(), parent.turn);
            if (dense < limit_) result.push_back(dense);
        };

        if (state.turn == Champion_To_Move) {
            for (std::uint8_t origin : geometry_.king_moves(state.rook_king)) {
                if (origin == state.rook || origin == state.champion_king || origin == state.champion)
                    continue;
                add_parent(State4 { origin, state.rook, state.champion_king,
                                    state.champion, Rook_To_Move });
            }
            for (const auto & ray : geometry_.rook_rays(state.rook)) {
                for (std::uint8_t origin : ray) {
                    if (origin == state.rook_king || origin == state.champion_king ||
                        origin == state.champion) break;
                    add_parent(State4 { state.rook_king, origin, state.champion_king,
                                        state.champion, Rook_To_Move });
                }
            }
        } else {
            for (std::uint8_t origin : geometry_.king_moves(state.champion_king)) {
                if (origin == state.rook_king || origin == state.rook || origin == state.champion)
                    continue;
                add_parent(State4 { state.rook_king, state.rook, origin,
                                    state.champion, Champion_To_Move });
            }
            for (std::uint8_t origin : geometry_.champion_moves(state.champion)) {
                if (origin == state.rook_king || origin == state.rook ||
                    origin == state.champion_king) continue;
                add_parent(State4 { state.rook_king, state.rook, state.champion_king,
                                    origin, Champion_To_Move });
            }
        }
        std::sort(result.begin(), result.end());
        result.erase(std::unique(result.begin(), result.end()), result.end());
        return result;
    }

    State4 unrank(std::uint32_t dense) const {
        State4 state;
        std::uint8_t squares[4]{};
        index_.unrank(dense, squares, state.turn);
        state.rook_king = squares[0];
        state.rook = squares[1];
        state.champion_king = squares[2];
        state.champion = squares[3];
        return state;
    }

private:
    const Geometry & geometry_;
    const D4Indexer & index_;
    const KrkTable & krk_;
    std::uint32_t limit_ = 0;
    bool verbose_ = true;
    std::vector<std::uint8_t> status_;
    std::vector<std::uint8_t> remaining_;
    std::vector<std::uint32_t> queue_;
    std::uint64_t legal_count_ = 0;

    void progress(const char * phase, std::uint32_t done,
                  std::chrono::steady_clock::time_point started) const {
        if (!verbose_ || (done % 1'000'000 != 0 && done != limit_)) return;
        const double seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        std::cerr << phase << ": " << done << '/' << limit_ << " ("
                  << std::fixed << std::setprecision(1) << seconds << "s)\n";
    }
};

void self_test(const Geometry & geometry, const D4Indexer & four_index,
               const D4Indexer & three_index, const KrkTable * krk) {
    if (sha256(std::string("abc")) !=
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        throw std::logic_error("SHA-256 self-test failed");
    if (four_index.raw_state_count() != 220'710'048 ||
        four_index.state_count() != Four_Man_State_Count ||
        three_index.raw_state_count() != 2'185'248 ||
        three_index.state_count() != Three_Man_State_Count)
        throw std::logic_error("retained D4 population changed");

    const State4 reported { 101, 96, 45, 74, Rook_To_Move };
    const auto reported_squares = reported.squares();
    if (four_index.rank(reported_squares.data(), reported.turn) != 26'750'996)
        throw std::logic_error("reported KRKC position changed dense index");

    std::mt19937 random(0x4F4D4547U);
    for (int sample = 0; sample < 2000; ++sample) {
        std::array<std::uint8_t, 4> squares{};
        do {
            for (auto & square : squares) square = static_cast<std::uint8_t>(random() % Square_Count);
        } while (std::adjacent_find(squares.begin(), squares.end()) != squares.end() ||
                 [&]() {
                     for (int i = 0; i < 4; ++i)
                         for (int j = 0; j < i; ++j)
                             if (squares[i] == squares[j]) return true;
                     return false;
                 }());
        const std::uint8_t turn = static_cast<std::uint8_t>(random() & 1U);
        const std::uint32_t dense = four_index.rank(squares.data(), turn);
        std::array<std::uint8_t, 4> canonical{};
        std::uint8_t canonical_turn = 0;
        four_index.unrank(dense, canonical.data(), canonical_turn);
        if (canonical_turn != turn || four_index.rank(canonical.data(), canonical_turn) != dense)
            throw std::logic_error("D4 rank/unrank round trip failed");
        for (int transform = 0; transform < 8; ++transform) {
            std::array<std::uint8_t, 4> transformed{};
            for (int i = 0; i < 4; ++i)
                transformed[i] = geometry.transforms()[transform][squares[i]];
            if (four_index.rank(transformed.data(), turn) != dense)
                throw std::logic_error("D4 transform changed dense index");
        }
    }

    if (krk != nullptr && krk->loaded()) {
        const State3 g100_capture { 101, 80, 56, Weak_To_Move };
        if (krk->probe(g100_capture) != Draw)
            throw std::logic_error("reported Rxi0 KRK dependency is not an exact draw");
    }

    // Concrete KRKC mate from the evaluator regression: Kc5/Ra0 mates Ka5
    // while the remote Cj9 cannot interpose or capture.
    if (krk != nullptr && krk->loaded()) {
        FourManSolver probe(geometry, four_index, *krk, four_index.state_count(), false);
        const State4 mate { 25, 0, 5, 99, Champion_To_Move };
        const Successors moves = probe.successors(mate);
        if (!probe.is_legal(mate) || !probe.in_check(mate) || moves.has_move)
            throw std::logic_error("concrete KRKC checkmate move generation failed");
    }
    std::cout << "four-man indexing, geometry, SHA-256, and dependency self-test: PASS\n";
}

std::uint64_t count_legal_states(const Geometry & geometry, const D4Indexer & index) {
    std::uint64_t legal = 0;
    for (std::uint32_t dense = 0; dense < index.state_count(); ++dense) {
        std::uint8_t squares[4]{};
        std::uint8_t turn = 0;
        index.unrank(dense, squares, turn);
        bool distinct = true;
        for (int i = 0; i < 4; ++i)
            for (int j = 0; j < i; ++j)
                distinct = distinct && squares[i] != squares[j];
        if (!distinct || geometry.king_attacks(squares[0], squares[2])) continue;
        if (turn == Champion_To_Move) {
            if (geometry.champion_attacks(squares[3], squares[0])) continue;
        } else if (geometry.rook_attacks(squares[1], squares[2], { squares[0], squares[3] })) {
            continue;
        }
        ++legal;
    }
    return legal;
}

std::uint32_t parse_u32(const std::string & text, const char * option) {
    const unsigned long long value = std::stoull(text);
    if (value == 0 || value > Four_Man_State_Count)
        throw std::invalid_argument(std::string(option) + " is out of range");
    return static_cast<std::uint32_t>(value);
}

std::uint32_t parse_index(const std::string & text, const char * option) {
    const unsigned long long value = std::stoull(text);
    if (value >= Four_Man_State_Count)
        throw std::invalid_argument(std::string(option) + " is out of range");
    return static_cast<std::uint32_t>(value);
}

State4 parse_state(const std::string & text) {
    std::string normalized = text;
    std::replace(normalized.begin(), normalized.end(), ',', ' ');
    std::istringstream stream(normalized);
    unsigned rook_king = 0, rook = 0, champion_king = 0, champion = 0, turn = 0;
    std::string extra;
    if (!(stream >> rook_king >> rook >> champion_king >> champion >> turn) ||
        (stream >> extra) || rook_king >= Square_Count || rook >= Square_Count ||
        champion_king >= Square_Count || champion >= Square_Count || turn > 1)
        throw std::invalid_argument(
            "--state requires rook_king,rook,champion_king,champion,turn; "
            "squares are 0..103 and turn is 0 (rook side) or 1 (Champion side)");
    return State4 { static_cast<std::uint8_t>(rook_king), static_cast<std::uint8_t>(rook),
                    static_cast<std::uint8_t>(champion_king),
                    static_cast<std::uint8_t>(champion), static_cast<std::uint8_t>(turn) };
}

const char * outcome_name(std::uint8_t outcome) {
    if (outcome == Invalid) return "invalid";
    if (outcome == Loss) return "loss";
    if (outcome == Draw) return "draw";
    if (outcome == Win) return "win";
    throw std::logic_error("unsupported four-man WDL code");
}

void usage() {
    std::cout
        << "four_man_wdl --self-test [--krk omega-krk-wdl-v1.omtb3]\n"
        << "four_man_wdl --verify-counts\n"
        << "four_man_wdl --small STATES --krk FILE [--verify] [--output FILE]\n"
        << "four_man_wdl --full --krk FILE [--verify] [--output FILE]\n"
        << "four_man_wdl --inspect FILE\n"
        << "four_man_wdl --probe FILE [--state RK,R,CK,C,TURN] [--index DENSE]...\n";
}

} // namespace
} // namespace omega_tb4

int main(int argc, char ** argv) {
    using namespace omega_tb4;
    try {
        bool full = false;
        bool verify = false;
        bool run_self_test = false;
        bool verify_counts = false;
        bool quiet = false;
        std::uint32_t small = 0;
        std::string krk_path;
        std::string output_path;
        std::string inspect_path;
        std::string probe_path;
        std::vector<State4> probe_states;
        std::vector<std::uint32_t> probe_indices;

        for (int i = 1; i < argc; ++i) {
            const std::string option = argv[i];
            auto value = [&](const char * name) -> std::string {
                if (++i >= argc) throw std::invalid_argument(std::string(name) + " requires a value");
                return argv[i];
            };
            if (option == "--full") full = true;
            else if (option == "--small") small = parse_u32(value("--small"), "--small");
            else if (option == "--krk") krk_path = value("--krk");
            else if (option == "--output") output_path = value("--output");
            else if (option == "--inspect") inspect_path = value("--inspect");
            else if (option == "--probe") probe_path = value("--probe");
            else if (option == "--state") probe_states.push_back(parse_state(value("--state")));
            else if (option == "--index") probe_indices.push_back(parse_index(value("--index"), "--index"));
            else if (option == "--verify") verify = true;
            else if (option == "--self-test") run_self_test = true;
            else if (option == "--verify-counts") verify_counts = true;
            else if (option == "--quiet") quiet = true;
            else if (option == "--help" || option == "-h") { usage(); return 0; }
            else throw std::invalid_argument("unknown option: " + option);
        }

        if (!inspect_path.empty()) {
            inspect_four_man_file(inspect_path);
            return 0;
        }
        if (!probe_path.empty()) {
            if (probe_states.empty() && probe_indices.empty())
                throw std::invalid_argument("--probe requires at least one --state or --index");
            Geometry geometry;
            D4Indexer index(geometry, 4);
            for (const State4 & state : probe_states) {
                const auto squares = state.squares();
                probe_indices.push_back(index.rank(squares.data(), state.turn));
            }
            const std::vector<std::uint8_t> payload = read_four_man_file(probe_path);
            for (std::uint32_t dense : probe_indices) {
                if (dense >= payload.size())
                    throw std::out_of_range("probe index is outside this bounded table");
                std::cout << "index=" << dense << " wdl=" << outcome_name(payload[dense])
                          << " code=" << static_cast<unsigned>(payload[dense]) << '\n';
            }
            return 0;
        }
        if (!probe_states.empty() || !probe_indices.empty())
            throw std::invalid_argument("--state and --index require --probe FILE");
        if (full && small != 0) throw std::invalid_argument("choose either --full or --small");

        Geometry geometry;
        D4Indexer four_index(geometry, 4);
        D4Indexer three_index(geometry, 3);
        KrkTable krk(geometry, three_index);
        if (!krk_path.empty()) krk.load(krk_path);

        if (run_self_test) self_test(geometry, four_index, three_index, krk.loaded() ? &krk : nullptr);
        if (verify_counts) {
            const std::uint64_t legal = count_legal_states(geometry, four_index);
            if (legal != Four_Man_Legal_Count)
                throw std::logic_error("KRKC legal-state population changed");
            std::cout << "four-man legal-state count: " << legal << " PASS\n";
        }
        if (!full && small == 0) {
            if (!run_self_test && !verify_counts) usage();
            return (run_self_test || verify_counts) ? 0 : 2;
        }
        if (!krk.loaded()) throw std::invalid_argument("--krk is required for exact KRKC generation");

        const std::uint32_t limit = full ? four_index.state_count() : small;
        const bool complete = limit == four_index.state_count();
        std::cout << "KRKC dense slots: " << limit << (complete ? " (full)" : " (outside-draw test boundary)") << '\n';
        std::cout << "working-set bound: status+remaining=" << (2ULL * limit)
                  << " bytes, queue<= " << (4ULL * limit) << " bytes\n";
        const auto started = std::chrono::steady_clock::now();
        FourManSolver solver(geometry, four_index, krk, limit, !quiet);
        const auto & payload = solver.solve();
        if (verify) solver.verify();
        const Counts counts = solver.counts();
        const double seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        std::cout << "KRKC legal=" << solver.legal_count() << " loss=" << counts.loss
                  << " draw=" << counts.draw << " win=" << counts.win
                  << " invalid=" << counts.invalid << " time=" << std::fixed
                  << std::setprecision(2) << seconds << "s\n";
        if (verify) std::cout << "KRKC Bellman verification: PASS\n";
        if (!output_path.empty()) {
            write_four_man_file(output_path, payload, solver.legal_count(), complete,
                                krk.payload_sha256());
            std::cout << "wrote " << output_path << '\n';
        }
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "four_man_wdl: " << error.what() << '\n';
        return 1;
    }
}
