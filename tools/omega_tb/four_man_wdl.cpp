#include "four_man_core.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <map>
#include <numeric>
#include <queue>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
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

constexpr std::uint32_t No_Dtm = std::numeric_limits<std::uint32_t>::max();

struct DtmCounts {
    std::uint64_t decisive = 0;
    std::uint64_t within_20 = 0;
    std::uint64_t within_40 = 0;
    std::uint64_t within_60 = 0;
    std::uint64_t within_80 = 0;
    std::uint64_t within_100 = 0;
    std::uint64_t beyond_100 = 0;
    std::uint32_t maximum = 0;
};

std::uint32_t expected_legal_count(FourManMaterial material) {
    switch (material) {
        case FourManMaterial::Krkc: return Krkc_Legal_Count;
        case FourManMaterial::Krkn: return Krkn_Legal_Count;
        case FourManMaterial::Kwkn: return Kwkn_Legal_Count;
        case FourManMaterial::Kckw: return Kckw_Legal_Count;
        case FourManMaterial::Kcck: return Kcck_Legal_Count;
    }
    throw std::logic_error("unknown four-man legal population");
}

bool material_requires_krk(FourManMaterial material) {
    return material == FourManMaterial::Krkc || material == FourManMaterial::Krkn;
}

class FourManSolver {
public:
    FourManSolver(const Geometry & geometry, const D4Indexer & four_index,
                  const KrkTable & krk, FourManMaterial material,
                  std::uint32_t limit, bool verbose)
        : geometry_(geometry), index_(four_index), krk_(krk), material_(material),
          limit_(limit), verbose_(verbose) {
        if (limit_ == 0 || limit_ > index_.state_count())
            throw std::invalid_argument("four-man state limit is out of range");
    }

    const std::vector<std::uint8_t> & solve() {
        if (!status_.empty()) throw std::logic_error("four-man solver may only run once");
        status_.assign(limit_, Invalid);
        remaining_.assign(limit_, 0);
        queue_.reserve(limit_ == index_.state_count()
            ? expected_legal_count(material_) : limit_);
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

        if (limit_ == index_.state_count() &&
            legal_count_ != expected_legal_count(material_))
            throw std::logic_error(std::string("full ") + four_man_material_name(material_) +
                                   " legal-state count changed");
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
                throw std::logic_error(std::string(four_man_material_name(material_)) +
                                       " Bellman mismatch at dense index " +
                                       std::to_string(dense));
            progress("verify", dense + 1, started);
        }
        if (legal != legal_count_) throw std::logic_error("verification legal count changed");
    }

    const std::vector<std::uint32_t> & solve_kcck_dtm() {
        if (material_ != FourManMaterial::Kcck || limit_ != index_.state_count())
            throw std::logic_error("KCCK DTM requires the complete KCCK graph");
        if (status_.empty())
            throw std::logic_error("KCCK WDL must be solved before DTM");
        if (!dtm_.empty())
            throw std::logic_error("KCCK DTM may only be solved once");

        remaining_.clear();
        remaining_.shrink_to_fit();
        queue_.clear();
        queue_.shrink_to_fit();
        dtm_.assign(limit_, No_Dtm);
        std::vector<std::uint8_t> remaining_loss(limit_, 0);
        std::vector<std::uint32_t> maximum_child(limit_, 0);
        using QueueEntry = std::pair<std::uint32_t, std::uint32_t>;
        std::priority_queue<QueueEntry, std::vector<QueueEntry>,
                            std::greater<QueueEntry>> queue;

        const auto started = std::chrono::steady_clock::now();
        for (std::uint32_t dense = 0; dense < limit_; ++dense) {
            const std::uint8_t outcome = status_[dense];
            if (outcome != Loss) continue;
            const State4 state = unrank(dense);
            const Successors moves = successors(state);
            if (!moves.has_move) {
                if (!in_check(state))
                    throw std::logic_error("KCCK DTM found a non-checkmate terminal loss");
                dtm_[dense] = 0;
                queue.push({ 0, dense });
            } else {
                if (moves.external_loss || moves.external_draw || moves.external_win ||
                    moves.outside_draw)
                    throw std::logic_error("decisive KCCK loss has an external successor");
                if (moves.same_class.empty() || moves.same_class.size() > 255)
                    throw std::logic_error("KCCK DTM loss successor count is out of range");
                for (std::uint32_t child : moves.same_class)
                    if (status_[child] != Win)
                        throw std::logic_error("KCCK WDL loss has a non-winning successor");
                remaining_loss[dense] =
                    static_cast<std::uint8_t>(moves.same_class.size());
            }
            progress("dtm-seed", dense + 1, started);
        }

        std::uint64_t resolved = queue.size();
        std::uint64_t next_progress = 1'000'000;
        while (!queue.empty()) {
            const auto [child_distance, child] = queue.top();
            queue.pop();
            if (dtm_[child] != child_distance) continue;
            if (child_distance == No_Dtm - 1)
                throw std::overflow_error("KCCK DTM exceeds the uint32 payload");

            const std::uint8_t child_outcome = status_[child];
            if (child_outcome != Loss && child_outcome != Win)
                throw std::logic_error("KCCK DTM queue contains a non-decisive state");
            const State4 child_state = unrank(child);
            for (std::uint32_t parent : predecessors(child_state)) {
                const std::uint8_t parent_outcome = status_[parent];
                if (parent_outcome == Win && child_outcome == Loss) {
                    if (dtm_[parent] == No_Dtm) {
                        dtm_[parent] = child_distance + 1;
                        queue.push({ dtm_[parent], parent });
                        ++resolved;
                    }
                } else if (parent_outcome == Loss && child_outcome == Win &&
                           dtm_[parent] == No_Dtm) {
                    if (remaining_loss[parent] == 0)
                        throw std::logic_error("KCCK DTM loss counter underflow");
                    maximum_child[parent] =
                        std::max(maximum_child[parent], child_distance);
                    --remaining_loss[parent];
                    if (remaining_loss[parent] == 0) {
                        dtm_[parent] = maximum_child[parent] + 1;
                        queue.push({ dtm_[parent], parent });
                        ++resolved;
                    }
                }
            }
            if (verbose_ && resolved >= next_progress) {
                progress("dtm-retrograde",
                         static_cast<std::uint32_t>(next_progress), started);
                next_progress = ((resolved / 1'000'000) + 1) * 1'000'000;
            }
        }

        const Counts wdl_counts = counts();
        if (resolved != wdl_counts.loss + wdl_counts.win)
            throw std::logic_error("KCCK DTM did not resolve every decisive WDL record");
        return dtm_;
    }

    void verify_kcck_dtm() const {
        if (material_ != FourManMaterial::Kcck || limit_ != index_.state_count() ||
            dtm_.size() != limit_)
            throw std::logic_error("KCCK DTM verification requires a complete distance table");
        const auto started = std::chrono::steady_clock::now();
        for (std::uint32_t dense = 0; dense < limit_; ++dense) {
            const std::uint8_t outcome = status_[dense];
            if (outcome == Invalid || outcome == Draw) {
                if (dtm_[dense] != No_Dtm)
                    throw std::logic_error("non-decisive KCCK record has a DTM value");
                continue;
            }
            if (dtm_[dense] == No_Dtm)
                throw std::logic_error("decisive KCCK record has no DTM value");

            const State4 state = unrank(dense);
            const Successors moves = successors(state);
            std::uint32_t expected = No_Dtm;
            if (outcome == Loss) {
                if (!moves.has_move) {
                    if (!in_check(state))
                        throw std::logic_error("terminal KCCK DTM loss is not checkmate");
                    expected = 0;
                } else {
                    std::uint32_t maximum = 0;
                    bool found = false;
                    for (std::uint32_t child : moves.same_class) {
                        if (status_[child] != Win || dtm_[child] == No_Dtm)
                            throw std::logic_error("KCCK DTM loss has an invalid child");
                        maximum = std::max(maximum, dtm_[child]);
                        found = true;
                    }
                    if (!found || maximum == No_Dtm - 1)
                        throw std::logic_error("KCCK DTM loss has no bounded child");
                    expected = maximum + 1;
                }
                if ((dtm_[dense] & 1U) != 0)
                    throw std::logic_error("KCCK defender-to-move loss has odd DTM");
            } else if (outcome == Win) {
                std::uint32_t minimum = No_Dtm;
                for (std::uint32_t child : moves.same_class)
                    if (status_[child] == Loss)
                        minimum = std::min(minimum, dtm_[child]);
                if (minimum == No_Dtm || minimum == No_Dtm - 1)
                    throw std::logic_error("KCCK DTM win has no losing child");
                expected = minimum + 1;
                if ((dtm_[dense] & 1U) == 0)
                    throw std::logic_error("KCCK attacker-to-move win has even DTM");
            } else {
                throw std::logic_error("KCCK DTM encountered an unsupported WDL code");
            }
            if (dtm_[dense] != expected)
                throw std::logic_error("KCCK DTM Bellman mismatch at dense index " +
                                       std::to_string(dense));
            progress("dtm-verify", dense + 1, started);
        }
    }

    void verify_kcck_dtm_symmetry() const {
        if (material_ != FourManMaterial::Kcck || limit_ != index_.state_count() ||
            dtm_.size() != limit_)
            throw std::logic_error("KCCK DTM symmetry verification requires a full table");
        const auto started = std::chrono::steady_clock::now();
        for (std::uint32_t dense = 0; dense < limit_; ++dense) {
            const State4 state = unrank(dense);
            const auto squares = state.squares();
            for (int transform = 0; transform < 8; ++transform) {
                std::array<std::uint8_t, 4> transformed{};
                for (int piece = 0; piece < 4; ++piece)
                    transformed[piece] =
                        geometry_.transforms()[transform][squares[piece]];
                const std::uint32_t transformed_dense =
                    index_.rank(transformed.data(), state.turn);
                if (transformed_dense != dense ||
                    dtm_[transformed_dense] != dtm_[dense])
                    throw std::logic_error("KCCK D4 DTM invariance failed at " +
                                           std::to_string(dense));
            }
            const State4 swapped {
                state.rook_king, state.minor, state.minor_king, state.rook, state.turn
            };
            const auto swapped_squares = swapped.squares();
            const std::uint32_t swapped_dense =
                index_.rank(swapped_squares.data(), swapped.turn);
            if (dtm_[swapped_dense] != dtm_[dense])
                throw std::logic_error("KCCK Champion-label DTM invariance failed at " +
                                       std::to_string(dense));
            progress("dtm-symmetry", dense + 1, started);
        }
        std::cout << "KCCK exhaustive D4 and Champion-label DTM invariance: PASS\n";
    }

    DtmCounts kcck_dtm_counts() const {
        if (dtm_.size() != status_.size())
            throw std::logic_error("KCCK DTM counts require a solved distance table");
        DtmCounts result;
        for (std::uint32_t distance : dtm_) {
            if (distance == No_Dtm) continue;
            ++result.decisive;
            if (distance <= 20) ++result.within_20;
            if (distance <= 40) ++result.within_40;
            if (distance <= 60) ++result.within_60;
            if (distance <= 80) ++result.within_80;
            if (distance <= 100) ++result.within_100;
            else ++result.beyond_100;
            result.maximum = std::max(result.maximum, distance);
        }
        return result;
    }

    void print_kcck_dtm_summary() const {
        const DtmCounts totals = kcck_dtm_counts();
        std::vector<std::uint64_t> win_histogram(totals.maximum + 1, 0);
        std::vector<std::uint64_t> loss_histogram(totals.maximum + 1, 0);
        std::uint32_t maximum_win_index = 0;
        std::uint32_t maximum_loss_index = 0;
        std::uint32_t maximum_win = 0;
        std::uint32_t maximum_loss = 0;
        for (std::uint32_t dense = 0; dense < limit_; ++dense) {
            if (dtm_[dense] == No_Dtm) continue;
            if (status_[dense] == Win) {
                ++win_histogram[dtm_[dense]];
                if (dtm_[dense] > maximum_win) {
                    maximum_win = dtm_[dense];
                    maximum_win_index = dense;
                }
            } else if (status_[dense] == Loss) {
                ++loss_histogram[dtm_[dense]];
                if (dtm_[dense] > maximum_loss) {
                    maximum_loss = dtm_[dense];
                    maximum_loss_index = dense;
                }
            }
        }

        std::cout << "KCCK DTM histogram distance,attacker-win,defender-loss,total\n";
        for (std::uint32_t distance = 0; distance <= totals.maximum; ++distance) {
            const std::uint64_t wins = win_histogram[distance];
            const std::uint64_t losses = loss_histogram[distance];
            if (wins + losses == 0) continue;
            std::cout << "KCCK DTM histogram " << distance << ',' << wins << ','
                      << losses << ',' << (wins + losses) << '\n';
        }

        auto safe_with_clock = [&](std::uint32_t halfmove_clock) {
            const std::uint32_t budget = 100 - halfmove_clock;
            std::uint64_t safe = 0;
            for (std::uint32_t distance = 0;
                 distance <= std::min(budget, totals.maximum); ++distance)
                safe += win_histogram[distance] + loss_histogram[distance];
            std::cout << "KCCK DTM rule-safe halfmove=" << halfmove_clock
                      << " budget=" << budget << " safe=" << safe
                      << " cursed=" << (totals.decisive - safe) << '\n';
        };
        for (std::uint32_t halfmove : { 0U, 20U, 40U, 60U, 80U, 99U })
            safe_with_clock(halfmove);

        auto percentile = [&](std::uint64_t numerator, std::uint64_t denominator) {
            const std::uint64_t target =
                (totals.decisive * numerator + denominator - 1) / denominator;
            std::uint64_t cumulative = 0;
            for (std::uint32_t distance = 0; distance <= totals.maximum; ++distance) {
                cumulative += win_histogram[distance] + loss_histogram[distance];
                if (cumulative >= target) return distance;
            }
            throw std::logic_error("KCCK DTM percentile could not be reconstructed");
        };
        std::cout << "KCCK DTM percentiles p50=" << percentile(50, 100)
                  << " p90=" << percentile(90, 100)
                  << " p95=" << percentile(95, 100)
                  << " p99=" << percentile(99, 100) << '\n';
        std::cout << "KCCK DTM maximum attacker-win=" << maximum_win
                  << " index=" << maximum_win_index
                  << " defender-loss=" << maximum_loss
                  << " index=" << maximum_loss_index << '\n';
    }

    const std::vector<std::uint32_t> & dtm() const { return dtm_; }

    void verify_kcck_symmetry(const std::vector<std::uint8_t> & payload) const {
        if (!same_side_leapers() || limit_ != index_.state_count())
            throw std::logic_error("KCCK symmetry verification requires the full table");
        if (payload.size() != limit_)
            throw std::logic_error("KCCK symmetry verification payload size changed");
        std::array<std::uint64_t, 5> swap_orbit_counts{};
        std::uint64_t fixed_swap_orbits = 0;
        const auto started = std::chrono::steady_clock::now();
        for (std::uint32_t dense = 0; dense < limit_; ++dense) {
            const State4 state = unrank(dense);
            const bool legal = is_legal(state);
            const auto squares = state.squares();
            for (int transform = 0; transform < 8; ++transform) {
                std::array<std::uint8_t, 4> transformed{};
                for (int piece = 0; piece < 4; ++piece)
                    transformed[piece] = geometry_.transforms()[transform][squares[piece]];
                const std::uint32_t transformed_dense =
                    index_.rank(transformed.data(), state.turn);
                const State4 transformed_state {
                    transformed[0], transformed[1], transformed[2], transformed[3], state.turn
                };
                if (transformed_dense != dense ||
                    is_legal(transformed_state) != legal ||
                    payload[transformed_dense] != payload[dense])
                    throw std::logic_error("KCCK D4 legality/WDL invariance failed at " +
                                           std::to_string(dense));
            }

            const State4 swapped {
                state.rook_king, state.minor, state.minor_king, state.rook, state.turn
            };
            const auto swapped_squares = swapped.squares();
            const std::uint32_t swapped_dense =
                index_.rank(swapped_squares.data(), swapped.turn);
            if (is_legal(swapped) != legal || payload[swapped_dense] != payload[dense])
                throw std::logic_error("KCCK Champion-label swap invariance failed at " +
                                       std::to_string(dense));
            if (dense <= swapped_dense) {
                if (dense == swapped_dense) ++fixed_swap_orbits;
                const std::uint8_t outcome = payload[dense];
                if (outcome >= swap_orbit_counts.size())
                    throw std::logic_error("KCCK label orbit has an unsupported WDL code");
                ++swap_orbit_counts[outcome];
            }
            progress("symmetry", dense + 1, started);
        }
        std::cout << "KCCK label-swap-orbits invalid=" << swap_orbit_counts[Invalid]
                  << " loss=" << swap_orbit_counts[Loss]
                  << " draw=" << swap_orbit_counts[Draw]
                  << " win=" << swap_orbit_counts[Win]
                  << " fixed=" << fixed_swap_orbits << '\n';
        std::cout << "KCCK exhaustive D4 and Champion-label swap invariance: PASS\n";
    }

    bool is_legal(const State4 & state) const {
        const auto squares = state.squares();
        if (state.turn > 1) return false;
        for (int i = 0; i < 4; ++i) {
            if (squares[i] >= Square_Count) return false;
            for (int j = 0; j < i; ++j)
                if (squares[i] == squares[j]) return false;
        }
        if (geometry_.king_attacks(state.rook_king, state.minor_king)) return false;
        if (same_side_leapers()) {
            if (state.turn == Minor_To_Move) return true;
            return !geometry_.champion_attacks(state.rook, state.minor_king) &&
                   !geometry_.champion_attacks(state.minor, state.minor_king);
        }
        if (state.turn == Minor_To_Move)
            return !minor_attacks(state.minor, state.rook_king);
        return !primary_attacks(state.rook, state.minor_king,
                                { state.rook_king, state.minor });
    }

    bool in_check(const State4 & state) const {
        if (same_side_leapers()) {
            if (state.turn == Rook_To_Move) return false;
            return geometry_.champion_attacks(state.rook, state.minor_king) ||
                   geometry_.champion_attacks(state.minor, state.minor_king);
        }
        if (state.turn == Rook_To_Move)
            return minor_attacks(state.minor, state.rook_king);
        return primary_attacks(state.rook, state.minor_king,
                               { state.rook_king, state.minor });
    }

    Successors successors(const State4 & state,
                          std::vector<State4> * raw_same_class = nullptr) const {
        if (!is_legal(state)) throw std::logic_error("successors requested for illegal four-man state");
        Successors result;
        result.same_class.reserve(32);

        auto add_child = [&](const State4 & child) {
            if (!is_legal(child)) return;
            result.has_move = true;
            const auto squares = child.squares();
            const std::uint32_t dense = index_.rank(squares.data(), child.turn);
            if (dense < limit_) {
                result.same_class.push_back(dense);
                if (raw_same_class != nullptr) raw_same_class->push_back(child);
            }
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
                if (target == state.rook || target == state.minor_king) continue;
                if (same_side_leapers() && target == state.minor) continue;
                if (target == state.minor) {
                    if (geometry_.king_attacks(target, state.minor_king)) continue;
                    if (primary_is_rook()) {
                        const State3 dependency {
                            target, state.rook, state.minor_king, Weak_To_Move
                        };
                        add_external(krk_.probe(dependency));
                    } else {
                        add_external(Draw); // K plus the primary leaper versus bare King.
                    }
                } else {
                    add_child(State4 { target, state.rook, state.minor_king,
                                       state.minor, Minor_To_Move });
                }
            }
            if (primary_is_rook()) {
                for (const auto & ray : geometry_.rook_rays(state.rook)) {
                    for (std::uint8_t target : ray) {
                        if (target == state.rook_king || target == state.minor_king ||
                            target == state.minor) {
                            if (target == state.minor) {
                                const State3 dependency { state.rook_king, target,
                                                          state.minor_king, Weak_To_Move };
                                add_external(krk_.probe(dependency));
                            }
                            break;
                        }
                        add_child(State4 { state.rook_king, target, state.minor_king,
                                           state.minor, Minor_To_Move });
                    }
                }
            } else {
                for (std::uint8_t target : primary_moves(state.rook)) {
                    if (target == state.rook_king || target == state.minor_king) continue;
                    if (target == state.minor) {
                        if (!same_side_leapers())
                            add_external(Draw); // K plus the primary leaper versus bare King.
                    } else {
                        add_child(State4 { state.rook_king, target, state.minor_king,
                                           state.minor, Minor_To_Move });
                    }
                }
            }
            if (same_side_leapers()) {
                for (std::uint8_t target : geometry_.champion_moves(state.minor)) {
                    if (target == state.rook_king || target == state.rook ||
                        target == state.minor_king) continue;
                    add_child(State4 { state.rook_king, state.rook, state.minor_king,
                                       target, Minor_To_Move });
                }
            }
        } else {
            for (std::uint8_t target : geometry_.king_moves(state.minor_king)) {
                if (target == state.rook_king) continue;
                if (target == state.rook) {
                    if (!geometry_.king_attacks(target, state.rook_king) &&
                        (!same_side_leapers() ||
                         !geometry_.champion_attacks(state.minor, target)))
                        add_external(Draw);
                } else if (same_side_leapers() && target == state.minor) {
                    if (!geometry_.king_attacks(target, state.rook_king) &&
                        !geometry_.champion_attacks(state.rook, target))
                        add_external(Draw);
                } else {
                    if (target == state.minor) continue;
                    add_child(State4 { state.rook_king, state.rook, target,
                                       state.minor, Rook_To_Move });
                }
            }
            if (!same_side_leapers()) {
                for (std::uint8_t target : minor_moves(state.minor)) {
                    if (target == state.minor_king || target == state.rook_king) continue;
                    if (target == state.rook) {
                        add_external(Draw); // Current one-leaper insufficient-material policy.
                    } else {
                        add_child(State4 { state.rook_king, state.rook, state.minor_king,
                                           target, Rook_To_Move });
                    }
                }
            }
        }

        std::sort(result.same_class.begin(), result.same_class.end());
        result.same_class.erase(std::unique(result.same_class.begin(), result.same_class.end()),
                                result.same_class.end());
        if (raw_same_class != nullptr) {
            auto key = [](const State4 & child) {
                return std::tie(child.rook_king, child.rook, child.minor_king,
                                child.minor, child.turn);
            };
            std::sort(raw_same_class->begin(), raw_same_class->end(),
                      [&](const State4 & first, const State4 & second) {
                          return key(first) < key(second);
                      });
            raw_same_class->erase(
                std::unique(raw_same_class->begin(), raw_same_class->end(),
                            [&](const State4 & first, const State4 & second) {
                                return key(first) == key(second);
                            }),
                raw_same_class->end());
        }
        return result;
    }

    std::vector<std::uint32_t> predecessors(const State4 & state) const {
        if (!is_legal(state)) throw std::logic_error("predecessors requested for illegal four-man state");
        std::vector<std::uint32_t> result;
        result.reserve(32);
        auto add_parent = [&](const State4 & parent) {
            if (!is_legal(parent)) return;
            const auto squares = parent.squares();
            const std::uint32_t dense = index_.rank(squares.data(), parent.turn);
            if (dense < limit_) result.push_back(dense);
        };

        if (state.turn == Minor_To_Move) {
            for (std::uint8_t origin : geometry_.king_moves(state.rook_king)) {
                if (origin == state.rook || origin == state.minor_king || origin == state.minor)
                    continue;
                add_parent(State4 { origin, state.rook, state.minor_king,
                                    state.minor, Rook_To_Move });
            }
            if (primary_is_rook()) {
                for (const auto & ray : geometry_.rook_rays(state.rook)) {
                    for (std::uint8_t origin : ray) {
                        if (origin == state.rook_king || origin == state.minor_king ||
                            origin == state.minor) break;
                        add_parent(State4 { state.rook_king, origin, state.minor_king,
                                            state.minor, Rook_To_Move });
                    }
                }
            } else {
                for (std::uint8_t origin : primary_moves(state.rook)) {
                    if (origin == state.rook_king || origin == state.minor_king ||
                        origin == state.minor) continue;
                    add_parent(State4 { state.rook_king, origin, state.minor_king,
                                        state.minor, Rook_To_Move });
                }
            }
            if (same_side_leapers()) {
                for (std::uint8_t origin : geometry_.champion_moves(state.minor)) {
                    if (origin == state.rook_king || origin == state.rook ||
                        origin == state.minor_king) continue;
                    add_parent(State4 { state.rook_king, state.rook, state.minor_king,
                                        origin, Rook_To_Move });
                }
            }
        } else {
            for (std::uint8_t origin : geometry_.king_moves(state.minor_king)) {
                if (origin == state.rook_king || origin == state.rook || origin == state.minor)
                    continue;
                add_parent(State4 { state.rook_king, state.rook, origin,
                                    state.minor, Minor_To_Move });
            }
            if (!same_side_leapers()) {
                for (std::uint8_t origin : minor_moves(state.minor)) {
                    if (origin == state.rook_king || origin == state.rook ||
                        origin == state.minor_king) continue;
                    add_parent(State4 { state.rook_king, state.rook, state.minor_king,
                                        origin, Minor_To_Move });
                }
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
        state.minor_king = squares[2];
        state.minor = squares[3];
        return state;
    }

private:
    const Geometry & geometry_;
    const D4Indexer & index_;
    const KrkTable & krk_;
    FourManMaterial material_ = FourManMaterial::Krkc;
    std::uint32_t limit_ = 0;
    bool verbose_ = true;
    std::vector<std::uint8_t> status_;
    std::vector<std::uint8_t> remaining_;
    std::vector<std::uint32_t> queue_;
    std::vector<std::uint32_t> dtm_;
    std::uint64_t legal_count_ = 0;

    bool same_side_leapers() const {
        return material_ == FourManMaterial::Kcck;
    }

    bool primary_is_rook() const {
        return material_ == FourManMaterial::Krkc ||
               material_ == FourManMaterial::Krkn;
    }

    bool primary_attacks(std::uint8_t from, std::uint8_t to,
                         const std::array<std::uint8_t, 2> & blockers) const {
        if (primary_is_rook()) return geometry_.rook_attacks(from, to, blockers);
        if (material_ == FourManMaterial::Kwkn)
            return geometry_.wizard_attacks(from, to);
        return geometry_.champion_attacks(from, to);
    }

    const std::vector<std::uint8_t> & primary_moves(std::uint8_t square) const {
        if (primary_is_rook())
            throw std::logic_error("rook moves require blocker-aware ray generation");
        return material_ == FourManMaterial::Kwkn
            ? geometry_.wizard_moves(square)
            : geometry_.champion_moves(square);
    }

    bool minor_attacks(std::uint8_t from, std::uint8_t to) const {
        if (material_ == FourManMaterial::Krkc || material_ == FourManMaterial::Kcck)
            return geometry_.champion_attacks(from, to);
        if (material_ == FourManMaterial::Kckw)
            return geometry_.wizard_attacks(from, to);
        return geometry_.knight_attacks(from, to);
    }

    const std::vector<std::uint8_t> & minor_moves(std::uint8_t square) const {
        if (material_ == FourManMaterial::Krkc || material_ == FourManMaterial::Kcck)
            return geometry_.champion_moves(square);
        if (material_ == FourManMaterial::Kckw)
            return geometry_.wizard_moves(square);
        return geometry_.knight_moves(square);
    }

    void progress(const char * phase, std::uint32_t done,
                  std::chrono::steady_clock::time_point started) const {
        if (!verbose_ || (done % 1'000'000 != 0 && done != limit_)) return;
        const double seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        std::cerr << phase << ": " << done << '/' << limit_ << " ("
                  << std::fixed << std::setprecision(1) << seconds << "s)\n";
    }
};

struct KcckDtmTable {
    std::vector<std::uint32_t> distance;
    std::string source_wdl_payload_sha256;
    DtmCounts counts;
};

std::vector<std::uint8_t> encode_u32le(const std::vector<std::uint32_t> & values) {
    std::vector<std::uint8_t> bytes(values.size() * 4);
    for (std::size_t index = 0; index < values.size(); ++index) {
        const std::uint32_t value = values[index];
        bytes[index * 4] = static_cast<std::uint8_t>(value);
        bytes[index * 4 + 1] = static_cast<std::uint8_t>(value >> 8);
        bytes[index * 4 + 2] = static_cast<std::uint8_t>(value >> 16);
        bytes[index * 4 + 3] = static_cast<std::uint8_t>(value >> 24);
    }
    return bytes;
}

KcckDtmTable read_kcck_dtm_file(const std::string & path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot open KCCK DTM table: " + path);
    std::string header;
    if (!std::getline(stream, header))
        throw std::runtime_error("KCCK DTM table has no header");
    if (!header.empty() && header.back() == '\r') header.pop_back();
    std::vector<std::uint8_t> bytes {
        std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()
    };

    if (json_string(header, "magic") != "OMTB4DTM" ||
        json_unsigned(header, "version") != 1 ||
        json_string(header, "material") != "KCCK")
        throw std::runtime_error("unsupported KCCK DTM table");
    if (json_string(header, "index") != "D4-first-piece-v1" ||
        json_unsigned(header, "square_count") != Square_Count ||
        json_unsigned(header, "dense_state_count") != Four_Man_State_Count ||
        json_unsigned(header, "state_count") != Four_Man_State_Count ||
        header.find("\"complete\":true") == std::string::npos ||
        header.find("\"boundary\":\"full\"") == std::string::npos ||
        header.find("\"labelled_order\":[\"attacker_king\",\"champion_a\","
                    "\"defender_king\",\"champion_b\",\"turn\"]") ==
            std::string::npos)
        throw std::runtime_error("KCCK DTM geometry/index metadata mismatch");
    if (json_string(header, "distance_unit") != "ply-to-checkmate" ||
        json_string(header, "payload_encoding") != "u32le" ||
        json_unsigned(header, "terminal_mate") != 0 ||
        json_unsigned(header, "no_distance") != No_Dtm ||
        json_string(header, "win_recurrence") != "1+min(loss-child)" ||
        json_string(header, "loss_recurrence") != "1+max(win-child)")
        throw std::runtime_error("KCCK DTM semantics metadata mismatch");
    if (json_string(header, "source_rules_sha256") !=
            four_man_rules_sha256(FourManMaterial::Kcck) ||
        json_string(header, "source_capture_policy_sha256") !=
            four_man_capture_policy_sha256(FourManMaterial::Kcck))
        throw std::runtime_error("KCCK DTM source-rules fingerprint mismatch");
    const std::string source_wdl = json_string(header, "source_wdl_payload_sha256");
    if (source_wdl.size() != 64)
        throw std::runtime_error("KCCK DTM source-WDL checksum is missing");
    if (bytes.size() != static_cast<std::size_t>(Four_Man_State_Count) * 4)
        throw std::runtime_error("KCCK DTM payload byte count mismatch");
    if (json_string(header, "payload_sha256") != sha256(bytes.data(), bytes.size()))
        throw std::runtime_error("KCCK DTM payload checksum mismatch");

    std::vector<std::uint32_t> distance(Four_Man_State_Count);
    DtmCounts counts;
    for (std::size_t index = 0; index < distance.size(); ++index) {
        const std::size_t offset = index * 4;
        const std::uint32_t value =
            static_cast<std::uint32_t>(bytes[offset]) |
            (static_cast<std::uint32_t>(bytes[offset + 1]) << 8) |
            (static_cast<std::uint32_t>(bytes[offset + 2]) << 16) |
            (static_cast<std::uint32_t>(bytes[offset + 3]) << 24);
        distance[index] = value;
        if (value == No_Dtm) continue;
        ++counts.decisive;
        if (value <= 20) ++counts.within_20;
        if (value <= 40) ++counts.within_40;
        if (value <= 60) ++counts.within_60;
        if (value <= 80) ++counts.within_80;
        if (value <= 100) ++counts.within_100;
        else ++counts.beyond_100;
        counts.maximum = std::max(counts.maximum, value);
    }
    if (json_unsigned(header, "decisive_count") != counts.decisive ||
        json_unsigned(header, "within_20") != counts.within_20 ||
        json_unsigned(header, "within_40") != counts.within_40 ||
        json_unsigned(header, "within_60") != counts.within_60 ||
        json_unsigned(header, "within_80") != counts.within_80 ||
        json_unsigned(header, "within_100") != counts.within_100 ||
        json_unsigned(header, "beyond_100") != counts.beyond_100 ||
        json_unsigned(header, "max_dtm") != counts.maximum)
        throw std::runtime_error("KCCK DTM summary metadata mismatch");
    return KcckDtmTable { std::move(distance), source_wdl, counts };
}

void write_kcck_dtm_file(const std::string & path,
                         const std::vector<std::uint32_t> & distance,
                         const std::string & source_wdl_payload_sha256,
                         const DtmCounts & counts) {
    if (distance.size() != Four_Man_State_Count ||
        source_wdl_payload_sha256.size() != 64)
        throw std::runtime_error("KCCK DTM output requires a complete source table");
    const std::vector<std::uint8_t> bytes = encode_u32le(distance);
    const std::string payload_sha = sha256(bytes.data(), bytes.size());
    std::ostringstream header;
    header << "{\"magic\":\"OMTB4DTM\",\"version\":1,\"material\":\"KCCK\""
           << ",\"labelled_order\":[\"attacker_king\",\"champion_a\","
              "\"defender_king\",\"champion_b\",\"turn\"]"
           << ",\"index\":\"D4-first-piece-v1\",\"square_count\":104"
           << ",\"dense_state_count\":" << Four_Man_State_Count
           << ",\"state_count\":" << distance.size()
           << ",\"complete\":true,\"boundary\":\"full\""
           << ",\"distance_unit\":\"ply-to-checkmate\",\"terminal_mate\":0"
           << ",\"win_recurrence\":\"1+min(loss-child)\""
           << ",\"loss_recurrence\":\"1+max(win-child)\""
           << ",\"no_distance\":" << No_Dtm
           << ",\"payload_encoding\":\"u32le\""
           << ",\"source_wdl_payload_sha256\":\""
           << source_wdl_payload_sha256 << "\""
           << ",\"source_rules_sha256\":\""
           << four_man_rules_sha256(FourManMaterial::Kcck) << "\""
           << ",\"source_capture_policy_sha256\":\""
           << four_man_capture_policy_sha256(FourManMaterial::Kcck) << "\""
           << ",\"decisive_count\":" << counts.decisive
           << ",\"within_20\":" << counts.within_20
           << ",\"within_40\":" << counts.within_40
           << ",\"within_60\":" << counts.within_60
           << ",\"within_80\":" << counts.within_80
           << ",\"within_100\":" << counts.within_100
           << ",\"beyond_100\":" << counts.beyond_100
           << ",\"max_dtm\":" << counts.maximum
           << ",\"payload_sha256\":\"" << payload_sha << "\"}";

    const std::string temporary = path + ".tmp";
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        if (!stream)
            throw std::runtime_error("cannot create KCCK DTM table: " + temporary);
        stream << header.str() << '\n';
        stream.write(reinterpret_cast<const char *>(bytes.data()),
                     static_cast<std::streamsize>(bytes.size()));
        if (!stream) throw std::runtime_error("failed to write KCCK DTM payload");
    }
    const KcckDtmTable checked = read_kcck_dtm_file(temporary);
    if (checked.source_wdl_payload_sha256 != source_wdl_payload_sha256)
        throw std::runtime_error("KCCK DTM round-trip source checksum mismatch");
    std::remove(path.c_str());
    if (std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(temporary.c_str());
        throw std::runtime_error("failed to atomically replace KCCK DTM table");
    }
}

void inspect_kcck_dtm_file(const std::string & path) {
    const KcckDtmTable table = read_kcck_dtm_file(path);
    const std::vector<std::uint8_t> bytes = encode_u32le(table.distance);
    std::cout << "OMTB4DTM KCCK verified: states=" << table.distance.size()
              << " decisive=" << table.counts.decisive
              << " max=" << table.counts.maximum
              << " sha256=" << sha256(bytes.data(), bytes.size())
              << " source-wdl=" << table.source_wdl_payload_sha256 << '\n';
}

void verify_kcck_dtm_wdl_binding(const KcckDtmTable & dtm,
                                  const FourManTable & wdl) {
    if (wdl.material != FourManMaterial::Kcck ||
        wdl.payload.size() != Four_Man_State_Count ||
        dtm.distance.size() != wdl.payload.size())
        throw std::runtime_error("KCCK DTM/WDL binding requires complete KCCK tables");
    if (sha256(wdl.payload.data(), wdl.payload.size()) !=
        dtm.source_wdl_payload_sha256)
        throw std::runtime_error("KCCK DTM source-WDL payload checksum mismatch");
    std::uint64_t decisive = 0;
    for (std::size_t index = 0; index < wdl.payload.size(); ++index) {
        const std::uint8_t outcome = wdl.payload[index];
        const std::uint32_t distance = dtm.distance[index];
        if (outcome == Win || outcome == Loss) {
            ++decisive;
            if (distance == No_Dtm)
                throw std::runtime_error("decisive KCCK WDL record has no DTM");
            if ((outcome == Win && (distance & 1U) == 0) ||
                (outcome == Loss && (distance & 1U) != 0))
                throw std::runtime_error("KCCK DTM/WDL turn parity mismatch");
        } else if (distance != No_Dtm) {
            throw std::runtime_error("draw/invalid KCCK WDL record has a DTM");
        }
    }
    if (decisive != dtm.counts.decisive)
        throw std::runtime_error("KCCK DTM/WDL decisive count mismatch");
    std::cout << "KCCK DTM source-WDL checksum, sentinel map, and parity: PASS\n";
}

void self_test(const Geometry & geometry, const D4Indexer & four_index,
               const D4Indexer & three_index, const KrkTable * krk,
               FourManMaterial material) {
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

    const std::array<std::array<std::uint8_t, 2>, 4> corner_knight_moves {{
        {{ 1, 10 }}, {{ 80, 91 }}, {{ 89, 98 }}, {{ 8, 19 }}
    }};
    const std::array<std::array<std::uint8_t, 3>, 4> corner_wizard_moves {{
        {{ 0, 2, 20 }}, {{ 70, 90, 92 }}, {{ 79, 97, 99 }}, {{ 7, 9, 29 }}
    }};
    const std::array<std::array<std::uint8_t, 1>, 4> corner_champion_moves {{
        {{ 11 }}, {{ 81 }}, {{ 88 }}, {{ 18 }}
    }};
    for (std::uint8_t corner = 0; corner < 4; ++corner) {
        const auto & knight_actual = geometry.knight_moves(100 + corner);
        if (knight_actual.size() != corner_knight_moves[corner].size() ||
            !std::equal(knight_actual.begin(), knight_actual.end(),
                        corner_knight_moves[corner].begin()))
            throw std::logic_error("Omega detached-corner Knight geometry changed");
        const auto & wizard_actual = geometry.wizard_moves(100 + corner);
        if (wizard_actual.size() != corner_wizard_moves[corner].size() ||
            !std::equal(wizard_actual.begin(), wizard_actual.end(),
                        corner_wizard_moves[corner].begin()))
            throw std::logic_error("Omega detached-corner Wizard geometry changed");
        const auto & champion_actual = geometry.champion_moves(100 + corner);
        if (champion_actual.size() != corner_champion_moves[corner].size() ||
            !std::equal(champion_actual.begin(), champion_actual.end(),
                        corner_champion_moves[corner].begin()))
            throw std::logic_error("Omega detached-corner Champion geometry changed");
    }
    for (std::uint8_t from = 0; from < Square_Count; ++from) {
        for (std::uint8_t to = 0; to < Square_Count; ++to) {
            const bool knight_attacks = geometry.knight_attacks(from, to);
            const bool wizard_attacks = geometry.wizard_attacks(from, to);
            const bool champion_attacks = geometry.champion_attacks(from, to);
            if (knight_attacks != geometry.knight_attacks(to, from))
                throw std::logic_error("Knight attack relation is not symmetric");
            if (wizard_attacks != geometry.wizard_attacks(to, from))
                throw std::logic_error("Wizard attack relation is not symmetric");
            if (champion_attacks != geometry.champion_attacks(to, from))
                throw std::logic_error("Champion attack relation is not symmetric");
            for (int transform = 0; transform < 8; ++transform) {
                if (knight_attacks != geometry.knight_attacks(
                        geometry.transforms()[transform][from],
                        geometry.transforms()[transform][to]))
                    throw std::logic_error("D4 transform changed Knight geometry");
                if (wizard_attacks != geometry.wizard_attacks(
                        geometry.transforms()[transform][from],
                        geometry.transforms()[transform][to]))
                    throw std::logic_error("D4 transform changed Wizard geometry");
                if (champion_attacks != geometry.champion_attacks(
                        geometry.transforms()[transform][from],
                        geometry.transforms()[transform][to]))
                    throw std::logic_error("D4 transform changed Champion geometry");
            }
        }
    }

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
    if (material == FourManMaterial::Krkc && krk != nullptr && krk->loaded()) {
        FourManSolver probe(geometry, four_index, *krk, material,
                            four_index.state_count(), false);
        const State4 mate { 25, 0, 5, 99, Minor_To_Move };
        const Successors moves = probe.successors(mate);
        if (!probe.is_legal(mate) || !probe.in_check(mate) || moves.has_move)
            throw std::logic_error("concrete KRKC checkmate move generation failed");
    }
    if (material == FourManMaterial::Kwkn) {
        FourManSolver probe(geometry, four_index, *krk, material,
                            four_index.state_count(), false);
        // Ki8/Wj7 mates Kw3 while a remote Na0 cannot answer the leaper check.
        const State4 mate { 88, 97, 102, 0, Minor_To_Move };
        const Successors moves = probe.successors(mate);
        if (!probe.is_legal(mate) || !probe.in_check(mate) || moves.has_move)
            throw std::logic_error("concrete KWKN detached-corner mate failed");
    }
    if (material == FourManMaterial::Kckw) {
        FourManSolver probe(geometry, four_index, *krk, material,
                            four_index.state_count(), false);
        // Ka1/Cb1 mates Kw1 while Wa3 cannot answer the Champion check.
        const State4 mate { 1, 11, 100, 3, Minor_To_Move };
        const Successors mate_moves = probe.successors(mate);
        if (!probe.is_legal(mate) || !probe.in_check(mate) || mate_moves.has_move)
            throw std::logic_error("concrete KCKW detached-corner mate failed");

        // Capturing either leaper leaves K+C/K+W versus K, both frozen draws.
        const State4 champion_capture { 0, 22, 99, 42, Rook_To_Move };
        const Successors champion_moves = probe.successors(champion_capture);
        const State4 wizard_capture { 0, 22, 99, 53, Minor_To_Move };
        const Successors wizard_moves = probe.successors(wizard_capture);
        if (!probe.is_legal(champion_capture) || !champion_moves.external_draw ||
            champion_moves.external_loss || champion_moves.external_win ||
            !probe.is_legal(wizard_capture) || !wizard_moves.external_draw ||
            wizard_moves.external_loss || wizard_moves.external_win)
            throw std::logic_error("KCKW capture-to-draw policy changed");
    }
    if (material == FourManMaterial::Kcck) {
        FourManSolver probe(geometry, four_index, *krk, material,
                            four_index.state_count(), false);
        const State4 mate { 1, 0, 100, 11, Minor_To_Move };
        const Successors mate_moves = probe.successors(mate);
        if (four_index.rank(mate.squares().data(), mate.turn) != 1'081'911 ||
            !probe.is_legal(mate) || !probe.in_check(mate) || mate_moves.has_move)
            throw std::logic_error("concrete KCCK detached-corner mate failed");

        const State4 stalemate { 1, 0, 100, 2, Minor_To_Move };
        const Successors stalemate_moves = probe.successors(stalemate);
        if (four_index.rank(stalemate.squares().data(), stalemate.turn) != 1'081'893 ||
            !probe.is_legal(stalemate) || probe.in_check(stalemate) ||
            stalemate_moves.has_move)
            throw std::logic_error("concrete KCCK detached-corner stalemate failed");

        const State4 illegal_history { 0, 22, 24, 55, Rook_To_Move };
        if (probe.is_legal(illegal_history))
            throw std::logic_error("KCCK illegal defender history was accepted");

        const State4 capture_a { 2, 10, 0, 1, Minor_To_Move };
        const State4 capture_b { 2, 1, 0, 10, Minor_To_Move };
        const Successors capture_a_moves = probe.successors(capture_a);
        const Successors capture_b_moves = probe.successors(capture_b);
        if (four_index.rank(capture_a.squares().data(), capture_a.turn) != 3'369'745 ||
            four_index.rank(capture_b.squares().data(), capture_b.turn) != 3'204'927 ||
            !probe.is_legal(capture_a) || !capture_a_moves.external_draw ||
            capture_a_moves.external_loss || capture_a_moves.external_win ||
            !probe.is_legal(capture_b) || !capture_b_moves.external_draw ||
            capture_b_moves.external_loss || capture_b_moves.external_win)
            throw std::logic_error("KCCK either-Champion capture boundary changed");

        const State4 parent { 0, 22, 99, 55, Rook_To_Move };
        const Successors parent_moves = probe.successors(parent);
        const std::uint32_t parent_dense = four_index.rank(
            parent.squares().data(), parent.turn);
        if (!probe.is_legal(parent) || parent_dense != 11'168)
            throw std::logic_error("KCCK both-label move fixture changed");
        auto has_move_and_predecessor = [&](bool first_label) {
            const std::uint8_t from = first_label ? parent.rook : parent.minor;
            for (std::uint8_t target : geometry.champion_moves(from)) {
                if (target == parent.rook_king || target == parent.minor_king ||
                    target == (first_label ? parent.minor : parent.rook)) continue;
                State4 child = parent;
                if (first_label) child.rook = target;
                else child.minor = target;
                child.turn = Minor_To_Move;
                if (!probe.is_legal(child)) continue;
                const std::uint32_t child_dense = four_index.rank(
                    child.squares().data(), child.turn);
                if (!std::binary_search(parent_moves.same_class.begin(),
                                        parent_moves.same_class.end(), child_dense))
                    continue;
                const std::vector<std::uint32_t> parents = probe.predecessors(child);
                if (std::binary_search(parents.begin(), parents.end(), parent_dense))
                    return true;
            }
            return false;
        };
        if (!has_move_and_predecessor(true) || !has_move_and_predecessor(false))
            throw std::logic_error("KCCK did not generate both Champion labels bidirectionally");
    }
    std::cout << "four-man indexing, geometry, SHA-256, and dependency self-test: PASS\n";
}

std::uint64_t count_legal_states(const Geometry & geometry, const D4Indexer & index,
                                 FourManMaterial material) {
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
        if (material == FourManMaterial::Kcck) {
            if (turn == Rook_To_Move &&
                (geometry.champion_attacks(squares[1], squares[2]) ||
                 geometry.champion_attacks(squares[3], squares[2])))
                continue;
            ++legal;
            continue;
        }
        if (turn == Minor_To_Move) {
            const bool attacks = material == FourManMaterial::Krkc
                ? geometry.champion_attacks(squares[3], squares[0])
                : material == FourManMaterial::Kckw
                    ? geometry.wizard_attacks(squares[3], squares[0])
                    : geometry.knight_attacks(squares[3], squares[0]);
            if (attacks) continue;
        } else {
            const bool attacks = material == FourManMaterial::Kwkn
                ? geometry.wizard_attacks(squares[1], squares[2])
                : material == FourManMaterial::Kckw
                    ? geometry.champion_attacks(squares[1], squares[2])
                    : geometry.rook_attacks(squares[1], squares[2],
                                            { squares[0], squares[3] });
            if (attacks) continue;
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

std::uint32_t parse_halfmove(const std::string & text) {
    const unsigned long long value = std::stoull(text);
    if (value > 99)
        throw std::invalid_argument("--halfmove must be in the nonterminal range 0..99");
    return static_cast<std::uint32_t>(value);
}

State4 parse_state(const std::string & text) {
    std::string normalized = text;
    std::replace(normalized.begin(), normalized.end(), ',', ' ');
    std::istringstream stream(normalized);
    unsigned primary_king = 0, primary = 0, opposing_king = 0, opposing = 0, turn = 0;
    std::string extra;
    if (!(stream >> primary_king >> primary >> opposing_king >> opposing >> turn) ||
        (stream >> extra) || primary_king >= Square_Count || primary >= Square_Count ||
        opposing_king >= Square_Count || opposing >= Square_Count || turn > 1)
        throw std::invalid_argument(
            "--state requires primary_king,primary,opposing_king,opposing,turn; "
            "squares are 0..103 and turn is 0 (primary side) or 1 (opposing side)");
    return State4 { static_cast<std::uint8_t>(primary_king),
                    static_cast<std::uint8_t>(primary),
                    static_cast<std::uint8_t>(opposing_king),
                    static_cast<std::uint8_t>(opposing),
                    static_cast<std::uint8_t>(turn) };
}

const char * outcome_name(std::uint8_t outcome) {
    if (outcome == Invalid) return "invalid";
    if (outcome == Loss) return "loss";
    if (outcome == Draw) return "draw";
    if (outcome == Win) return "win";
    throw std::logic_error("unsupported four-man WDL code");
}

std::string square_name(std::uint8_t square) {
    if (square < Regular_Squares) {
        std::string result;
        result += static_cast<char>('a' + square / 10);
        result += static_cast<char>('0' + square % 10);
        return result;
    }
    return std::string("w") + static_cast<char>('1' + square - Regular_Squares);
}

struct MaterialLabels {
    const char * primary_side;
    const char * primary_king;
    const char * primary_piece;
    const char * opposing_side;
    const char * opposing_king;
    const char * opposing_piece;
};

MaterialLabels material_labels(FourManMaterial material) {
    switch (material) {
        case FourManMaterial::Krkc:
            return { "rook", "RK", "R", "champion", "CK", "C" };
        case FourManMaterial::Krkn:
            return { "rook", "RK", "R", "knight", "NK", "N" };
        case FourManMaterial::Kwkn:
            return { "wizard", "WK", "W", "knight", "NK", "N" };
        case FourManMaterial::Kckw:
            return { "champion", "CK", "C", "wizard", "WK", "W" };
        case FourManMaterial::Kcck:
            return { "attacker", "AK", "Ca", "defender", "DK", "Cb" };
    }
    throw std::logic_error("unknown four-man material labels");
}

std::string state_name(const State4 & state, FourManMaterial material) {
    const MaterialLabels labels = material_labels(material);
    std::ostringstream out;
    out << labels.primary_king << '=' << square_name(state.rook_king)
        << ',' << labels.primary_piece << '=' << square_name(state.rook)
        << ',' << labels.opposing_king << '=' << square_name(state.minor_king)
        << ',' << labels.opposing_piece << '=' << square_name(state.minor)
        << ",turn=" << (state.turn == Rook_To_Move
            ? labels.primary_side : labels.opposing_side);
    return out.str();
}

std::string kcck_move_name(const State4 & parent, const State4 & child,
                           const FourManSolver & graph) {
    const auto before = parent.squares();
    const auto after = child.squares();
    int changed = -1;
    for (int piece = 0; piece < 4; ++piece) {
        if (before[piece] == after[piece]) continue;
        if (changed != -1)
            throw std::logic_error("KCCK raw line changed more than one piece");
        changed = piece;
    }
    if (changed == -1 || child.turn == parent.turn)
        throw std::logic_error("KCCK raw line did not contain a legal turn-changing move");
    const char * label = changed == 0 ? "K" :
                         changed == 1 ? "Ca" :
                         changed == 2 ? "k" : "Cb";
    std::string suffix;
    if (graph.in_check(child)) {
        const Successors replies = graph.successors(child);
        suffix = replies.has_move ? "+" : "#";
    }
    return std::string(label) + ':' + square_name(before[changed]) + '-' +
           square_name(after[changed]) + suffix;
}

void print_kcck_optimal_line(const State4 & raw_root,
                             const FourManTable & wdl,
                             const KcckDtmTable & dtm,
                             const D4Indexer & index,
                             const FourManSolver & graph,
                             const char * root_kind) {
    if (!graph.is_legal(raw_root))
        throw std::invalid_argument("KCCK DTM line root is historically illegal");
    State4 state = raw_root;
    const auto root_squares = state.squares();
    const std::uint32_t root_dense = index.rank(root_squares.data(), state.turn);
    const std::uint32_t root_distance = dtm.distance[root_dense];
    if (root_distance == No_Dtm)
        throw std::invalid_argument("KCCK DTM line root is draw or invalid");
    std::cout << "KCCK DTM line root-kind=" << root_kind
              << " index=" << root_dense << ' '
              << state_name(state, FourManMaterial::Kcck)
              << " wdl=" << outcome_name(wdl.payload[root_dense])
              << " dtm=" << root_distance << '\n';

    std::uint32_t ply = 0;
    while (true) {
        const auto squares = state.squares();
        const std::uint32_t dense = index.rank(squares.data(), state.turn);
        const std::uint32_t distance = dtm.distance[dense];
        const std::uint8_t outcome = wdl.payload[dense];
        if (distance == 0) {
            const Successors terminal = graph.successors(state);
            if (outcome != Loss || terminal.has_move || !graph.in_check(state) ||
                ply != root_distance)
                throw std::logic_error("KCCK DTM line did not terminate in exact checkmate");
            std::cout << "KCCK DTM line checkmate plies=" << ply << " PASS\n";
            return;
        }
        if (outcome != Win && outcome != Loss)
            throw std::logic_error("KCCK DTM line entered a non-decisive state");

        std::vector<State4> raw_children;
        const Successors canonical = graph.successors(state, &raw_children);
        std::vector<std::uint32_t> raw_ranks;
        raw_ranks.reserve(raw_children.size());
        for (const State4 & child : raw_children) {
            const auto child_squares = child.squares();
            raw_ranks.push_back(index.rank(child_squares.data(), child.turn));
        }
        std::sort(raw_ranks.begin(), raw_ranks.end());
        raw_ranks.erase(std::unique(raw_ranks.begin(), raw_ranks.end()), raw_ranks.end());
        if (raw_ranks != canonical.same_class)
            throw std::logic_error("KCCK raw/canonical successor sets disagree");

        const std::uint8_t child_outcome = outcome == Win ? Loss : Win;
        std::vector<State4> optimal;
        std::size_t preserving = 0;
        for (const State4 & child : raw_children) {
            const auto child_squares = child.squares();
            const std::uint32_t child_dense =
                index.rank(child_squares.data(), child.turn);
            if (wdl.payload[child_dense] != child_outcome) continue;
            ++preserving;
            if (dtm.distance[child_dense] == distance - 1)
                optimal.push_back(child);
        }
        if (optimal.empty())
            throw std::logic_error("KCCK DTM line has no distance-decreasing child");
        const State4 child = optimal.front();
        const auto child_squares = child.squares();
        const std::uint32_t child_dense =
            index.rank(child_squares.data(), child.turn);
        ++ply;
        std::cout << "KCCK DTM line ply=" << ply
                  << " move=" << kcck_move_name(state, child, graph)
                  << " preserving=" << preserving
                  << " optimal=" << optimal.size()
                  << " index=" << child_dense << ' '
                  << state_name(child, FourManMaterial::Kcck)
                  << " wdl=" << outcome_name(wdl.payload[child_dense])
                  << " dtm=" << dtm.distance[child_dense] << '\n';
        state = child;
    }
}

struct AtlasDistanceGroup {
    std::uint64_t count = 0;
    std::uint64_t distance_sum = 0;
    std::uint32_t maximum = 0;
    std::vector<std::uint64_t> histogram;

    explicit AtlasDistanceGroup(std::uint32_t max_distance = 0)
        : histogram(max_distance + 1, 0) {}

    void add(std::uint32_t distance) {
        if (distance >= histogram.size())
            throw std::logic_error("KCCK atlas distance exceeds the frozen maximum");
        ++count;
        distance_sum += distance;
        maximum = std::max(maximum, distance);
        ++histogram[distance];
    }

    std::uint32_t percentile(std::uint64_t numerator,
                             std::uint64_t denominator) const {
        if (count == 0) return 0;
        const std::uint64_t target =
            (count * numerator + denominator - 1) / denominator;
        std::uint64_t cumulative = 0;
        for (std::uint32_t distance = 0; distance < histogram.size(); ++distance) {
            cumulative += histogram[distance];
            if (cumulative >= target) return distance;
        }
        throw std::logic_error("KCCK atlas percentile could not be reconstructed");
    }
};

int defender_region_index(std::uint8_t square) {
    if (square >= Regular_Squares) return 3;
    const int file = square / 10;
    const int rank = square % 10;
    if ((file == 0 || file == 9) && (rank == 0 || rank == 9))
        return 2;
    if (file == 0 || file == 9 || rank == 0 || rank == 9)
        return 1;
    return 0;
}

const char * defender_region(std::uint8_t square) {
    constexpr const char * Names[] = {
        "interior", "regular-edge", "regular-corner", "detached"
    };
    return Names[defender_region_index(square)];
}

void print_atlas_group(const std::string & dimension, const std::string & value,
                       const AtlasDistanceGroup & group) {
    if (group.count == 0) return;
    const double mean = static_cast<double>(group.distance_sum) /
                        static_cast<double>(group.count);
    std::cout << "KCCK atlas attacker-win " << dimension << '=' << value
              << " count=" << group.count
              << " mean=" << std::fixed << std::setprecision(3) << mean
              << " p50=" << group.percentile(50, 100)
              << " p90=" << group.percentile(90, 100)
              << " p99=" << group.percentile(99, 100)
              << " max=" << group.maximum << '\n';
}

void print_atlas_draw_share(const std::string & dimension,
                            const std::string & value,
                            const AtlasDistanceGroup & wins,
                            std::uint64_t draws) {
    const std::uint64_t legal = wins.count + draws;
    if (legal == 0) return;
    const double draw_share =
        100.0 * static_cast<double>(draws) / static_cast<double>(legal);
    std::cout << "KCCK atlas attacker-turn " << dimension << '=' << value
              << " wins=" << wins.count
              << " draws=" << draws
              << " draw-share-percent=" << std::fixed << std::setprecision(3)
              << draw_share << '\n';
}

void summarize_kcck_atlas(const FourManTable & wdl,
                          const KcckDtmTable & dtm,
                          const Geometry & geometry,
                          const D4Indexer & index,
                          const FourManSolver & graph) {
    if (wdl.material != FourManMaterial::Kcck ||
        wdl.payload.size() != Four_Man_State_Count ||
        dtm.distance.size() != Four_Man_State_Count)
        throw std::invalid_argument("KCCK atlas requires complete WDL and DTM tables");

    std::array<AtlasDistanceGroup, 3> detached_champions {
        AtlasDistanceGroup(dtm.counts.maximum),
        AtlasDistanceGroup(dtm.counts.maximum),
        AtlasDistanceGroup(dtm.counts.maximum)
    };
    std::array<AtlasDistanceGroup, 4> regions {
        AtlasDistanceGroup(dtm.counts.maximum),
        AtlasDistanceGroup(dtm.counts.maximum),
        AtlasDistanceGroup(dtm.counts.maximum),
        AtlasDistanceGroup(dtm.counts.maximum)
    };
    std::array<std::uint64_t, 3> detached_champion_draws {};
    std::array<std::uint64_t, 4> defender_region_draws {};
    std::array<AtlasDistanceGroup, 2> attacker_king_detached {
        AtlasDistanceGroup(dtm.counts.maximum),
        AtlasDistanceGroup(dtm.counts.maximum)
    };
    std::map<std::pair<std::string, int>, std::uint64_t> mate_shells;
    std::set<std::uint32_t> mate_unlabelled_orbits;
    std::uint64_t mate_label_swap_fixed = 0;
    std::uint64_t mate_count = 0;
    std::uint64_t mate_without_king_zone_control = 0;
    std::uint64_t mate_with_mutual_champion_support = 0;
    std::vector<std::pair<std::uint32_t, State4>> maximum_attacker_roots;
    std::vector<std::pair<std::uint32_t, State4>> maximum_defender_roots;

    for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
        const std::uint8_t outcome = wdl.payload[dense];
        const std::uint32_t distance = dtm.distance[dense];
        const bool attacker_turn_draw =
            outcome == Draw && (dense & 1U) == Rook_To_Move;
        if (outcome != Win && !attacker_turn_draw &&
            !(outcome == Loss && (distance == 0 || distance == 40)))
            continue;
        std::uint8_t squares[4]{};
        std::uint8_t turn = 0;
        index.unrank(dense, squares, turn);
        const State4 state {
            squares[0], squares[1], squares[2], squares[3], turn
        };
        if (attacker_turn_draw) {
            if (state.turn != Rook_To_Move)
                throw std::logic_error("KCCK atlas draw turn/index parity changed");
            const int detached =
                static_cast<int>(state.rook >= Regular_Squares) +
                static_cast<int>(state.minor >= Regular_Squares);
            ++detached_champion_draws[detached];
            ++defender_region_draws[defender_region_index(state.minor_king)];
            continue;
        }
        if (outcome == Win && state.turn == Rook_To_Move) {
            const int detached =
                static_cast<int>(state.rook >= Regular_Squares) +
                static_cast<int>(state.minor >= Regular_Squares);
            detached_champions[detached].add(distance);
            regions[defender_region_index(state.minor_king)].add(distance);
            attacker_king_detached[state.rook_king >= Regular_Squares ? 1 : 0]
                .add(distance);
            if (distance == 39)
                maximum_attacker_roots.push_back({ dense, state });
        }
        if (outcome == Loss && distance == 40)
            maximum_defender_roots.push_back({ dense, state });
        if (outcome != Loss || distance != 0) continue;

        const Successors moves = graph.successors(state);
        if (moves.has_move || !graph.in_check(state))
            throw std::logic_error("KCCK atlas DTM-zero record is not checkmate");
        ++mate_count;
        const State4 swapped {
            state.rook_king, state.minor, state.minor_king, state.rook, state.turn
        };
        const auto swapped_squares = swapped.squares();
        const std::uint32_t swapped_dense =
            index.rank(swapped_squares.data(), swapped.turn);
        mate_unlabelled_orbits.insert(std::min(dense, swapped_dense));
        if (dense == swapped_dense) ++mate_label_swap_fixed;
        const int checkers =
            static_cast<int>(geometry.champion_attacks(state.rook,
                                                       state.minor_king)) +
            static_cast<int>(geometry.champion_attacks(state.minor,
                                                       state.minor_king));
        mate_shells[{ defender_region(state.minor_king), checkers }]++;
        int king_zone_control = 0;
        for (std::uint8_t target : geometry.king_moves(state.minor_king))
            if (geometry.king_attacks(state.rook_king, target))
                ++king_zone_control;
        if (king_zone_control == 0) ++mate_without_king_zone_control;
        if (geometry.champion_attacks(state.rook, state.minor))
            ++mate_with_mutual_champion_support;
    }

    const std::array<const char *, 3> detached_labels { "0", "1", "2" };
    for (int count = 0; count < 3; ++count) {
        print_atlas_group("detached-champions", detached_labels[count],
                          detached_champions[count]);
        print_atlas_draw_share("detached-champions", detached_labels[count],
                               detached_champions[count],
                               detached_champion_draws[count]);
    }
    const std::array<const char *, 4> region_labels {
        "interior", "regular-edge", "regular-corner", "detached"
    };
    for (int region = 0; region < 4; ++region) {
        print_atlas_group("defender-region", region_labels[region], regions[region]);
        print_atlas_draw_share("defender-region", region_labels[region],
                               regions[region], defender_region_draws[region]);
    }
    print_atlas_group("attacker-king-detached", "false",
                      attacker_king_detached[0]);
    print_atlas_group("attacker-king-detached", "true",
                      attacker_king_detached[1]);

    std::cout << "KCCK atlas mate-shells total=" << mate_count
              << " no-attacker-king-zone-control="
              << mate_without_king_zone_control
              << " mutual-champion-support="
              << mate_with_mutual_champion_support << '\n';
    std::cout << "KCCK atlas mate-orbits d4-and-label-swap="
              << mate_unlabelled_orbits.size()
              << " fixed-label-swap=" << mate_label_swap_fixed << '\n';
    for (const auto & [key, count] : mate_shells)
        std::cout << "KCCK atlas mate-shell region=" << key.first
                  << " checking-champions=" << key.second
                  << " count=" << count << '\n';
    std::cout << "KCCK atlas max-roots attacker=" << maximum_attacker_roots.size()
              << " defender=" << maximum_defender_roots.size() << '\n';
    for (const auto & [dense, state] : maximum_attacker_roots)
        std::cout << "KCCK atlas max-attacker index=" << dense << ' '
                  << state_name(state, FourManMaterial::Kcck) << '\n';
    for (const auto & [dense, state] : maximum_defender_roots)
        std::cout << "KCCK atlas max-defender index=" << dense << ' '
                  << state_name(state, FourManMaterial::Kcck) << '\n';
}

void summarize_four_man_file(const std::string & path, const std::string & krk_path) {
    const FourManTable table = read_four_man_file(path);
    if (table.payload.size() != Four_Man_State_Count)
        throw std::invalid_argument("--summary requires a complete four-man artifact");
    const MaterialLabels labels = material_labels(table.material);

    std::array<std::array<std::uint64_t, 5>, 2> counts{};
    for (std::uint32_t dense = 0; dense < table.payload.size(); ++dense) {
        const std::uint8_t outcome = table.payload[dense];
        if (outcome >= counts[0].size())
            throw std::logic_error("four-man summary encountered an unsupported WDL code");
        ++counts[dense & 1U][outcome];
    }
    const std::uint64_t legal = counts[0][Loss] + counts[0][Draw] + counts[0][Win] +
                                counts[1][Loss] + counts[1][Draw] + counts[1][Win];
    if (legal != expected_legal_count(table.material))
        throw std::logic_error(std::string(four_man_material_name(table.material)) +
                               " summary legal population changed");
    for (std::uint8_t turn = 0; turn < 2; ++turn) {
        std::cout << (turn == Rook_To_Move ? labels.primary_side : labels.opposing_side)
                  << "-to-move"
                  << " invalid=" << counts[turn][Invalid]
                  << " loss=" << counts[turn][Loss]
                  << " draw=" << counts[turn][Draw]
                  << " win=" << counts[turn][Win] << '\n';
    }

    const std::uint64_t primary_wins = counts[Rook_To_Move][Win] +
                                       counts[Minor_To_Move][Loss];
    const std::uint64_t opposing_wins = counts[Rook_To_Move][Loss] +
                                        counts[Minor_To_Move][Win];
    const std::uint64_t draws = counts[Rook_To_Move][Draw] +
                                counts[Minor_To_Move][Draw];
    std::cout << "material-side-wins " << labels.primary_side << '=' << primary_wins
              << ' ' << labels.opposing_side << '=' << opposing_wins
              << " draws=" << draws
              << " decisive=" << primary_wins + opposing_wins << '\n';

    enum MaterialWinner : std::uint8_t {
        No_Winner,
        Primary_Winner,
        Opposing_Winner,
    };
    auto material_winner = [](std::uint8_t turn, std::uint8_t outcome) {
        if (outcome == Draw) return No_Winner;
        if (outcome != Loss && outcome != Win)
            throw std::logic_error("material winner requested for an invalid state");
        const bool mover_wins = outcome == Win;
        const bool primary_to_move = turn == Rook_To_Move;
        return mover_wins == primary_to_move ? Primary_Winner : Opposing_Winner;
    };

    std::uint64_t both_legal = 0;
    std::uint64_t primary_wins_both = 0;
    std::uint64_t opposing_wins_both = 0;
    std::uint64_t split_decisive = 0;
    std::uint64_t decisive_draw_mixed = 0;
    std::uint64_t draws_both = 0;
    std::uint64_t at_least_one_invalid = 0;
    for (std::uint32_t dense = 0; dense < table.payload.size(); dense += 2) {
        const std::uint8_t primary_turn = table.payload[dense];
        const std::uint8_t opposing_turn = table.payload[dense + 1];
        if (primary_turn == Invalid || opposing_turn == Invalid) {
            ++at_least_one_invalid;
            continue;
        }
        ++both_legal;
        const MaterialWinner first = material_winner(Rook_To_Move, primary_turn);
        const MaterialWinner second = material_winner(Minor_To_Move, opposing_turn);
        if (first == No_Winner && second == No_Winner) ++draws_both;
        else if (first == Primary_Winner && second == Primary_Winner) ++primary_wins_both;
        else if (first == Opposing_Winner && second == Opposing_Winner) ++opposing_wins_both;
        else if (first != No_Winner && second != No_Winner) ++split_decisive;
        else ++decisive_draw_mixed;
    }
    std::cout << "turn-paired-placements both-legal=" << both_legal
              << ' ' << labels.primary_side << "-wins-both=" << primary_wins_both
              << ' ' << labels.opposing_side << "-wins-both=" << opposing_wins_both
              << " split-decisive=" << split_decisive
              << " decisive-draw-mixed=" << decisive_draw_mixed
              << " draws-both=" << draws_both
              << " at-least-one-invalid=" << at_least_one_invalid << '\n';

    Geometry geometry;
    D4Indexer four_index(geometry, 4);
    D4Indexer three_index(geometry, 3);
    KrkTable krk(geometry, three_index);
    if (!krk_path.empty()) krk.load(krk_path);
    if (material_requires_krk(table.material) && !krk.loaded())
        throw std::invalid_argument("--summary requires --krk for a rook material");
    if (material_requires_krk(table.material) &&
        krk.payload_sha256() != table.dependency_sha256)
        throw std::invalid_argument("--krk does not match the summarized artifact");
    FourManSolver graph(geometry, four_index, krk,
                        table.material, four_index.state_count(), false);
    if (table.material == FourManMaterial::Kcck)
        graph.verify_kcck_symmetry(table.payload);

    bool found_mate = false;
    bool found_mate_in_one = false;
    bool found_deeper_win = false;
    bool found_nonterminal_loss = false;
    bool found_robust_primary_win = false;
    bool found_robust_opposing_win = false;
    for (std::uint32_t dense = 0; dense < table.payload.size(); ++dense) {
        const std::uint8_t outcome = table.payload[dense];
        if (outcome != Loss && outcome != Win) continue;
        const State4 state = graph.unrank(dense);
        const Successors moves = graph.successors(state);
        if (outcome == Loss && !moves.has_move && graph.in_check(state) && !found_mate) {
            std::cout << "terminal-checkmate index=" << dense << ' '
                      << state_name(state, table.material) << '\n';
            found_mate = true;
        }
        if (outcome == Loss && moves.has_move && !found_nonterminal_loss) {
            std::cout << "nonterminal-forced-loss index=" << dense << ' '
                      << state_name(state, table.material) << '\n';
            found_nonterminal_loss = true;
        }
        if (outcome == Win) {
            for (std::uint32_t child : moves.same_class) {
                if (table.payload[child] != Loss) continue;
                const State4 child_state = graph.unrank(child);
                const Successors child_moves = graph.successors(child_state);
                if (!child_moves.has_move && graph.in_check(child_state) && !found_mate_in_one) {
                    std::cout << "mate-in-one-win index=" << dense << ' '
                              << state_name(state, table.material)
                              << " child=" << child << '\n';
                    found_mate_in_one = true;
                } else if (child_moves.has_move && !found_deeper_win) {
                    std::cout << "nonterminal-forced-win index=" << dense << ' '
                              << state_name(state, table.material)
                              << " child=" << child << '\n';
                    found_deeper_win = true;
                }
            }
        }
        if (found_mate && found_mate_in_one && found_deeper_win && found_nonterminal_loss)
            break;
    }
    if (!found_mate || !found_mate_in_one || !found_deeper_win || !found_nonterminal_loss)
        throw std::logic_error(std::string(four_man_material_name(table.material)) +
                               " summary could not find every decisive witness class");

    for (std::uint32_t dense = 0; dense < table.payload.size(); dense += 2) {
        const std::uint8_t primary_turn = table.payload[dense];
        const std::uint8_t opposing_turn = table.payload[dense + 1];
        const bool primary_wins_pair = primary_turn == Win && opposing_turn == Loss;
        const bool opposing_wins_pair = primary_turn == Loss && opposing_turn == Win;
        if ((!primary_wins_pair || found_robust_primary_win) &&
            (!opposing_wins_pair || found_robust_opposing_win)) continue;
        const State4 primary_state = graph.unrank(dense);
        const State4 opposing_state = graph.unrank(dense + 1);
        if (!graph.successors(primary_state).has_move ||
            !graph.successors(opposing_state).has_move) continue;
        if (primary_wins_pair && !found_robust_primary_win) {
            std::cout << "turn-independent-" << labels.primary_side
                      << "-win indices=" << dense << ',' << dense + 1 << ' '
                      << state_name(primary_state, table.material) << '\n';
            found_robust_primary_win = true;
        }
        if (opposing_wins_pair && !found_robust_opposing_win) {
            std::cout << "turn-independent-" << labels.opposing_side
                      << "-win indices=" << dense << ',' << dense + 1 << ' '
                      << state_name(primary_state, table.material) << '\n';
            found_robust_opposing_win = true;
        }
        if ((found_robust_primary_win || primary_wins_both == 0) &&
            (found_robust_opposing_win || opposing_wins_both == 0)) break;
    }
    if ((primary_wins_both > 0 && !found_robust_primary_win) ||
        (opposing_wins_both > 0 && !found_robust_opposing_win))
        throw std::logic_error(std::string(four_man_material_name(table.material)) +
                               " summary could not find a nonterminal turn-independent witness");
}

void usage() {
    std::cout
        << "four_man_wdl --self-test [--krk omega-krk-wdl-v1.omtb3]\n"
        << "four_man_wdl --verify-counts\n"
        << "four_man_wdl --material krkc|krkn|kwkn|kckw|kcck --small STATES [--krk FILE] [--verify] [--output FILE]\n"
        << "four_man_wdl --material krkc|krkn|kwkn|kckw|kcck --full [--krk FILE] [--verify] [--dtm] [--output FILE]\n"
        << "four_man_wdl --inspect FILE\n"
        << "four_man_wdl --summary FILE [--krk FILE]\n"
        << "four_man_wdl --probe FILE [--state PK,P,OK,O,TURN] [--index DENSE]...\n"
        << "four_man_wdl --inspect-dtm FILE [--dtm-wdl KCCK-WDL]\n"
        << "four_man_wdl --probe-dtm FILE [--dtm-wdl KCCK-WDL] [--halfmove 0..99] [--state AK,CA,DK,CB,TURN] [--index DENSE]...\n"
        << "four_man_wdl --line-dtm FILE --dtm-wdl KCCK-WDL [--state AK,CA,DK,CB,TURN] [--index DENSE]...\n"
        << "four_man_wdl --atlas-dtm FILE --dtm-wdl KCCK-WDL\n";
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
        bool solve_dtm = false;
        bool quiet = false;
        bool material_explicit = false;
        FourManMaterial material = FourManMaterial::Krkc;
        std::uint32_t small = 0;
        int halfmove = -1;
        std::string krk_path;
        std::string output_path;
        std::string inspect_path;
        std::string summary_path;
        std::string probe_path;
        std::string dtm_output_path;
        std::string dtm_inspect_path;
        std::string dtm_probe_path;
        std::string dtm_line_path;
        std::string dtm_atlas_path;
        std::string dtm_wdl_path;
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
            else if (option == "--material") {
                material = parse_four_man_material(value("--material"));
                material_explicit = true;
            }
            else if (option == "--krk") krk_path = value("--krk");
            else if (option == "--output") output_path = value("--output");
            else if (option == "--inspect") inspect_path = value("--inspect");
            else if (option == "--summary") summary_path = value("--summary");
            else if (option == "--probe") probe_path = value("--probe");
            else if (option == "--dtm-output") dtm_output_path = value("--dtm-output");
            else if (option == "--inspect-dtm") dtm_inspect_path = value("--inspect-dtm");
            else if (option == "--probe-dtm") dtm_probe_path = value("--probe-dtm");
            else if (option == "--line-dtm") dtm_line_path = value("--line-dtm");
            else if (option == "--atlas-dtm") dtm_atlas_path = value("--atlas-dtm");
            else if (option == "--dtm-wdl") dtm_wdl_path = value("--dtm-wdl");
            else if (option == "--state") probe_states.push_back(parse_state(value("--state")));
            else if (option == "--index") probe_indices.push_back(parse_index(value("--index"), "--index"));
            else if (option == "--halfmove")
                halfmove = static_cast<int>(parse_halfmove(value("--halfmove")));
            else if (option == "--verify") verify = true;
            else if (option == "--self-test") run_self_test = true;
            else if (option == "--verify-counts") verify_counts = true;
            else if (option == "--dtm") solve_dtm = true;
            else if (option == "--quiet") quiet = true;
            else if (option == "--help" || option == "-h") { usage(); return 0; }
            else throw std::invalid_argument("unknown option: " + option);
        }

        if (!inspect_path.empty()) {
            inspect_four_man_file(inspect_path);
            return 0;
        }
        if (!dtm_inspect_path.empty()) {
            inspect_kcck_dtm_file(dtm_inspect_path);
            if (!dtm_wdl_path.empty()) {
                const KcckDtmTable dtm = read_kcck_dtm_file(dtm_inspect_path);
                const FourManTable wdl = read_four_man_file(dtm_wdl_path);
                verify_kcck_dtm_wdl_binding(dtm, wdl);
            }
            return 0;
        }
        if (!summary_path.empty()) {
            summarize_four_man_file(summary_path, krk_path);
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
            const FourManTable table = read_four_man_file(probe_path);
            if (material_explicit && table.material != material)
                throw std::invalid_argument("--material does not match the probed table");
            const std::vector<std::uint8_t> & payload = table.payload;
            for (std::uint32_t dense : probe_indices) {
                if (dense >= payload.size())
                    throw std::out_of_range("probe index is outside this bounded table");
                std::cout << "index=" << dense << " wdl=" << outcome_name(payload[dense])
                          << " code=" << static_cast<unsigned>(payload[dense]) << '\n';
            }
            return 0;
        }
        if (!dtm_probe_path.empty()) {
            if (probe_states.empty() && probe_indices.empty())
                throw std::invalid_argument("--probe-dtm requires at least one --state or --index");
            Geometry geometry;
            D4Indexer index(geometry, 4);
            for (const State4 & state : probe_states) {
                const auto squares = state.squares();
                probe_indices.push_back(index.rank(squares.data(), state.turn));
            }
            const KcckDtmTable table = read_kcck_dtm_file(dtm_probe_path);
            FourManTable wdl;
            if (!dtm_wdl_path.empty()) {
                wdl = read_four_man_file(dtm_wdl_path);
                verify_kcck_dtm_wdl_binding(table, wdl);
            }
            if (halfmove >= 0 && dtm_wdl_path.empty())
                throw std::invalid_argument("--halfmove requires --dtm-wdl");
            for (std::uint32_t dense : probe_indices) {
                if (dense >= table.distance.size())
                    throw std::out_of_range("DTM probe index is outside the table");
                const std::uint32_t distance = table.distance[dense];
                std::uint8_t squares[4]{};
                std::uint8_t turn = 0;
                index.unrank(dense, squares, turn);
                const State4 state {
                    squares[0], squares[1], squares[2], squares[3], turn
                };
                std::cout << "index=" << dense << ' '
                          << state_name(state, FourManMaterial::Kcck)
                          << " dtm=";
                if (distance == No_Dtm) std::cout << "none";
                else std::cout << distance;
                if (halfmove >= 0) {
                    const std::uint8_t outcome = wdl.payload[dense];
                    const std::uint32_t budget =
                        100 - static_cast<std::uint32_t>(halfmove);
                    const char * rule = "invalid";
                    if (outcome == Draw) rule = "draw";
                    else if (outcome == Win || outcome == Loss) {
                        if (distance <= budget) rule = "theoretical-result-safe";
                        else if (outcome == Win) rule = "cursed-win";
                        else rule = "blessed-loss";
                    }
                    std::cout << " halfmove=" << halfmove
                              << " budget=" << budget
                              << " rule=" << rule;
                }
                std::cout << '\n';
            }
            return 0;
        }
        if (!dtm_line_path.empty()) {
            if (probe_states.empty() && probe_indices.empty())
                throw std::invalid_argument("--line-dtm requires at least one --state or --index");
            if (dtm_wdl_path.empty())
                throw std::invalid_argument("--line-dtm requires --dtm-wdl");
            const KcckDtmTable dtm = read_kcck_dtm_file(dtm_line_path);
            const FourManTable wdl = read_four_man_file(dtm_wdl_path);
            verify_kcck_dtm_wdl_binding(dtm, wdl);
            Geometry geometry;
            D4Indexer four_index(geometry, 4);
            D4Indexer three_index(geometry, 3);
            KrkTable krk(geometry, three_index);
            FourManSolver graph(geometry, four_index, krk,
                                FourManMaterial::Kcck,
                                four_index.state_count(), false);
            for (const State4 & state : probe_states)
                print_kcck_optimal_line(state, wdl, dtm, four_index, graph,
                                        "raw-state");
            for (std::uint32_t dense : probe_indices) {
                std::uint8_t squares[4]{};
                std::uint8_t turn = 0;
                four_index.unrank(dense, squares, turn);
                const State4 state {
                    squares[0], squares[1], squares[2], squares[3], turn
                };
                print_kcck_optimal_line(state, wdl, dtm, four_index, graph,
                                        "canonical-index");
            }
            return 0;
        }
        if (!dtm_atlas_path.empty()) {
            if (dtm_wdl_path.empty())
                throw std::invalid_argument("--atlas-dtm requires --dtm-wdl");
            const KcckDtmTable dtm = read_kcck_dtm_file(dtm_atlas_path);
            const FourManTable wdl = read_four_man_file(dtm_wdl_path);
            verify_kcck_dtm_wdl_binding(dtm, wdl);
            Geometry geometry;
            D4Indexer four_index(geometry, 4);
            D4Indexer three_index(geometry, 3);
            KrkTable krk(geometry, three_index);
            FourManSolver graph(geometry, four_index, krk,
                                FourManMaterial::Kcck,
                                four_index.state_count(), false);
            summarize_kcck_atlas(wdl, dtm, geometry, four_index, graph);
            return 0;
        }
        if (!probe_states.empty() || !probe_indices.empty())
            throw std::invalid_argument(
                "--state and --index require --probe, --probe-dtm, or --line-dtm");
        if (halfmove >= 0)
            throw std::invalid_argument("--halfmove requires --probe-dtm");
        if (full && small != 0) throw std::invalid_argument("choose either --full or --small");
        if (!dtm_output_path.empty()) solve_dtm = true;
        if (solve_dtm && (!full || material != FourManMaterial::Kcck))
            throw std::invalid_argument("--dtm requires --material kcck --full");

        Geometry geometry;
        D4Indexer four_index(geometry, 4);
        D4Indexer three_index(geometry, 3);
        KrkTable krk(geometry, three_index);
        if (!krk_path.empty()) krk.load(krk_path);

        if (run_self_test)
            self_test(geometry, four_index, three_index, &krk, material);
        if (verify_counts) {
            const std::uint64_t legal = count_legal_states(geometry, four_index, material);
            const std::uint64_t expected = expected_legal_count(material);
            if (legal != expected)
                throw std::logic_error(std::string(four_man_material_name(material)) +
                                       " legal-state population changed: expected " +
                                       std::to_string(expected) + ", found " +
                                       std::to_string(legal));
            std::cout << four_man_material_name(material)
                      << " legal-state count: " << legal << " PASS\n";
        }
        if (!full && small == 0) {
            if (!run_self_test && !verify_counts) usage();
            return (run_self_test || verify_counts) ? 0 : 2;
        }
        if (material_requires_krk(material) && !krk.loaded())
            throw std::invalid_argument("--krk is required for exact four-man generation");

        const std::uint32_t limit = full ? four_index.state_count() : small;
        const bool complete = limit == four_index.state_count();
        std::cout << four_man_material_name(material) << " dense slots: " << limit
                  << (complete ? " (full)" : " (outside-draw test boundary)") << '\n';
        std::cout << "working-set bound: status+remaining=" << (2ULL * limit)
                  << " bytes, queue<= " << (4ULL * limit) << " bytes\n";
        const auto started = std::chrono::steady_clock::now();
        FourManSolver solver(geometry, four_index, krk, material, limit, !quiet);
        const auto & payload = solver.solve();
        if (verify) solver.verify();
        if (verify && complete && material == FourManMaterial::Kcck)
            solver.verify_kcck_symmetry(payload);
        if (solve_dtm) {
            solver.solve_kcck_dtm();
            solver.verify_kcck_dtm();
            solver.verify_kcck_dtm_symmetry();
            const DtmCounts dtm = solver.kcck_dtm_counts();
            std::cout << "KCCK DTM decisive=" << dtm.decisive
                      << " within20=" << dtm.within_20
                      << " within40=" << dtm.within_40
                      << " within60=" << dtm.within_60
                      << " within80=" << dtm.within_80
                      << " within100=" << dtm.within_100
                      << " beyond100=" << dtm.beyond_100
                      << " max=" << dtm.maximum << '\n';
            solver.print_kcck_dtm_summary();
            std::cout << "KCCK DTM Bellman and parity verification: PASS\n";
            if (!dtm_output_path.empty()) {
                const std::string source_sha =
                    sha256(payload.data(), payload.size());
                write_kcck_dtm_file(dtm_output_path, solver.dtm(), source_sha, dtm);
                const KcckDtmTable checked = read_kcck_dtm_file(dtm_output_path);
                verify_kcck_dtm_wdl_binding(
                    checked, FourManTable { material, payload,
                        four_man_capture_policy_sha256(material) });
                std::cout << "wrote " << dtm_output_path << '\n';
            }
        }
        const Counts counts = solver.counts();
        const double seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        std::cout << four_man_material_name(material) << " legal=" << solver.legal_count()
                  << " loss=" << counts.loss
                  << " draw=" << counts.draw << " win=" << counts.win
                  << " invalid=" << counts.invalid << " time=" << std::fixed
                  << std::setprecision(2) << seconds << "s\n";
        if (verify)
            std::cout << four_man_material_name(material) << " Bellman verification: PASS\n";
        if (!output_path.empty()) {
            const std::string dependency = material == FourManMaterial::Kwkn
                ? sha256(std::string("kwk-knk-insufficient-material-v1"))
                : material == FourManMaterial::Kckw
                    ? sha256(std::string("kck-kwk-insufficient-material-v1"))
                    : material == FourManMaterial::Kcck
                        ? sha256(std::string("either-champion-capture-to-kck-draw-v1"))
                    : krk.payload_sha256();
            write_four_man_file(output_path, payload, solver.legal_count(), complete,
                                dependency, material);
            std::cout << "wrote " << output_path << '\n';
        }
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "four_man_wdl: " << error.what() << '\n';
        return 1;
    }
}
