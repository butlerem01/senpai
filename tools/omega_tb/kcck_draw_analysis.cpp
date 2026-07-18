#include "four_man_core.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace omega_tb4 {
namespace {

constexpr const char * Expected_Wdl_Payload =
    "35f9d8bcb3b283dec2f918fcc4c5d62355edc2dee3ee297e16dd42a91940678e";
constexpr const char * Expected_Wdl_Container =
    "bff047fab9141666ae13f80db17ae2d5d5a808bdd115b131180e529bf493a244";
constexpr const char * Analysis_Identity =
    "kcck-draw-analysis-v1;draw-preserving-edges-v1;"
    "kosaraju-normalized-min-v1;target-cycle-reachability-v1;"
    "defender-terminal-attractor-v1;inverse-edge-parity-v1;"
    "champion-label-swap-parity-v1;robust-both-turn-pairs-v1";
constexpr std::uint32_t No_Component = std::numeric_limits<std::uint32_t>::max();

constexpr std::uint8_t Flag_Stalemate = 1U << 0;
constexpr std::uint8_t Flag_Capture_A = 1U << 1;
constexpr std::uint8_t Flag_Capture_B = 1U << 2;
constexpr std::uint8_t Flag_Detached = 1U << 3;
constexpr std::uint8_t Flag_Both_Turn = 1U << 4;
constexpr std::uint8_t Flag_In_Check = 1U << 5;

constexpr std::uint8_t Reach_Stalemate = 1U << 0;
constexpr std::uint8_t Reach_Capture_A = 1U << 1;
constexpr std::uint8_t Reach_Capture_B = 1U << 2;
constexpr std::uint8_t Reach_Cycle = 1U << 3;

constexpr std::uint8_t Attractor_Stalemate = 1U << 0;
constexpr std::uint8_t Attractor_Capture_A = 1U << 1;
constexpr std::uint8_t Attractor_Capture_B = 1U << 2;
constexpr std::uint8_t Attractor_Any_Capture = 1U << 3;
constexpr std::uint8_t Attractor_Terminal = 1U << 4;

class Bit_Set {
public:
    explicit Bit_Set(std::size_t size = 0) : words_((size + 63) / 64, 0) {}

    bool test(std::uint32_t index) const {
        return (words_[index >> 6] >> (index & 63)) & 1ULL;
    }

    bool set(std::uint32_t index) {
        const std::uint64_t mask = 1ULL << (index & 63);
        std::uint64_t & word = words_[index >> 6];
        const bool changed = (word & mask) == 0;
        word |= mask;
        return changed;
    }

private:
    std::vector<std::uint64_t> words_;
};

struct Graph_Edges {
    std::vector<std::uint32_t> internal;
    bool has_move = false;
    bool capture_a = false;
    bool capture_b = false;
};

class Kcck_Graph {
public:
    Kcck_Graph(const Geometry & geometry, const D4Indexer & index,
               const std::vector<std::uint8_t> & wdl)
        : geometry_(geometry), index_(index), wdl_(wdl) {
        if (wdl_.size() != Four_Man_State_Count)
            throw std::invalid_argument("KCCK draw graph requires a complete WDL payload");
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

    std::uint32_t rank(const State4 & state) const {
        const auto squares = state.squares();
        return index_.rank(squares.data(), state.turn);
    }

    bool is_legal(const State4 & state) const {
        const auto squares = state.squares();
        if (state.turn > 1) return false;
        for (int first = 0; first < 4; ++first) {
            if (squares[first] >= Square_Count) return false;
            for (int second = 0; second < first; ++second)
                if (squares[first] == squares[second]) return false;
        }
        if (geometry_.king_attacks(state.rook_king, state.minor_king))
            return false;
        if (state.turn == Minor_To_Move) return true;
        return !geometry_.champion_attacks(state.rook, state.minor_king) &&
               !geometry_.champion_attacks(state.minor, state.minor_king);
    }

    bool in_check(const State4 & state) const {
        if (!is_legal(state) || state.turn == Rook_To_Move) return false;
        return geometry_.champion_attacks(state.rook, state.minor_king) ||
               geometry_.champion_attacks(state.minor, state.minor_king);
    }

    Graph_Edges successors(const State4 & state, bool draw_only) const {
        if (!is_legal(state))
            throw std::logic_error("KCCK successors requested for an illegal state");
        Graph_Edges result;
        result.internal.reserve(32);

        auto add_child = [&](const State4 & child) {
            if (!is_legal(child)) return;
            result.has_move = true;
            const std::uint32_t dense = rank(child);
            if (!draw_only || wdl_[dense] == Draw)
                result.internal.push_back(dense);
        };

        if (state.turn == Rook_To_Move) {
            for (std::uint8_t target : geometry_.king_moves(state.rook_king)) {
                if (target == state.rook || target == state.minor_king ||
                    target == state.minor)
                    continue;
                add_child(State4 { target, state.rook, state.minor_king,
                                   state.minor, Minor_To_Move });
            }
            for (std::uint8_t target : geometry_.champion_moves(state.rook)) {
                if (target == state.rook_king || target == state.minor_king ||
                    target == state.minor)
                    continue;
                add_child(State4 { state.rook_king, target, state.minor_king,
                                   state.minor, Minor_To_Move });
            }
            for (std::uint8_t target : geometry_.champion_moves(state.minor)) {
                if (target == state.rook_king || target == state.rook ||
                    target == state.minor_king)
                    continue;
                add_child(State4 { state.rook_king, state.rook, state.minor_king,
                                   target, Minor_To_Move });
            }
        } else {
            for (std::uint8_t target : geometry_.king_moves(state.minor_king)) {
                if (target == state.rook_king) continue;
                if (target == state.rook) {
                    if (!geometry_.king_attacks(target, state.rook_king) &&
                        !geometry_.champion_attacks(state.minor, target)) {
                        result.has_move = true;
                        result.capture_a = true;
                    }
                } else if (target == state.minor) {
                    if (!geometry_.king_attacks(target, state.rook_king) &&
                        !geometry_.champion_attacks(state.rook, target)) {
                        result.has_move = true;
                        result.capture_b = true;
                    }
                } else {
                    add_child(State4 { state.rook_king, state.rook, target,
                                       state.minor, Rook_To_Move });
                }
            }
        }

        std::sort(result.internal.begin(), result.internal.end());
        result.internal.erase(
            std::unique(result.internal.begin(), result.internal.end()),
            result.internal.end());
        return result;
    }

    std::vector<std::uint32_t> draw_predecessors(const State4 & state) const {
        if (!is_legal(state))
            throw std::logic_error("KCCK predecessors requested for an illegal state");
        std::vector<std::uint32_t> result;
        result.reserve(32);
        auto add_parent = [&](const State4 & parent) {
            if (!is_legal(parent)) return;
            const std::uint32_t dense = rank(parent);
            if (wdl_[dense] == Draw) result.push_back(dense);
        };

        if (state.turn == Minor_To_Move) {
            for (std::uint8_t origin : geometry_.king_moves(state.rook_king)) {
                if (origin == state.rook || origin == state.minor_king ||
                    origin == state.minor)
                    continue;
                add_parent(State4 { origin, state.rook, state.minor_king,
                                    state.minor, Rook_To_Move });
            }
            for (std::uint8_t origin : geometry_.champion_moves(state.rook)) {
                if (origin == state.rook_king || origin == state.minor_king ||
                    origin == state.minor)
                    continue;
                add_parent(State4 { state.rook_king, origin, state.minor_king,
                                    state.minor, Rook_To_Move });
            }
            for (std::uint8_t origin : geometry_.champion_moves(state.minor)) {
                if (origin == state.rook_king || origin == state.rook ||
                    origin == state.minor_king)
                    continue;
                add_parent(State4 { state.rook_king, state.rook, state.minor_king,
                                    origin, Rook_To_Move });
            }
        } else {
            for (std::uint8_t origin : geometry_.king_moves(state.minor_king)) {
                if (origin == state.rook_king || origin == state.rook ||
                    origin == state.minor)
                    continue;
                add_parent(State4 { state.rook_king, state.rook, origin,
                                    state.minor, Minor_To_Move });
            }
        }

        std::sort(result.begin(), result.end());
        result.erase(std::unique(result.begin(), result.end()), result.end());
        return result;
    }

private:
    const Geometry & geometry_;
    const D4Indexer & index_;
    const std::vector<std::uint8_t> & wdl_;
};

struct Local_Result {
    std::vector<std::uint8_t> flags;
    std::vector<std::uint8_t> draw_degree;
    std::vector<std::uint64_t> draw_edge_keys;
    std::string draw_edge_digest;
    std::array<std::uint64_t, 64> combinations{};
    std::array<std::uint64_t, 5> precedence{};
    std::uint64_t draws = 0;
    std::uint64_t attacker_draws = 0;
    std::uint64_t defender_draws = 0;
    std::uint64_t stalemate = 0;
    std::uint64_t capture_a = 0;
    std::uint64_t capture_b = 0;
    std::uint64_t capture_both = 0;
    std::uint64_t detached = 0;
    std::uint64_t both_turn_records = 0;
    std::uint64_t both_turn_placements = 0;
    std::uint64_t in_check = 0;
    std::uint64_t draw_edges = 0;
    std::uint64_t canonical_self_edges = 0;
};

Local_Result analyze_local(const Kcck_Graph & graph,
                           const std::vector<std::uint8_t> & wdl) {
    Local_Result result;
    result.flags.assign(Four_Man_State_Count, 0);
    result.draw_degree.assign(Four_Man_State_Count, 0);

    for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
        if (wdl[dense] != Draw) continue;
        ++result.draws;
        if ((dense & 1U) == Rook_To_Move) ++result.attacker_draws;
        else ++result.defender_draws;

        const State4 state = graph.unrank(dense);
        if (!graph.is_legal(state))
            throw std::logic_error("draw payload contains a historically illegal state");
        const Graph_Edges all = graph.successors(state, false);
        const Graph_Edges drawing = graph.successors(state, true);
        if (drawing.internal.size() > 255)
            throw std::logic_error("KCCK draw degree exceeds one byte");
        result.draw_degree[dense] =
            static_cast<std::uint8_t>(drawing.internal.size());
        result.draw_edges += drawing.internal.size();
        for (std::uint32_t child : drawing.internal)
            result.draw_edge_keys.push_back(
                (static_cast<std::uint64_t>(dense) << 32) | child);
        if (std::binary_search(drawing.internal.begin(), drawing.internal.end(), dense))
            ++result.canonical_self_edges;

        std::uint8_t flags = 0;
        if (!all.has_move) {
            if (graph.in_check(state))
                throw std::logic_error("draw record is terminal checkmate");
            flags |= Flag_Stalemate;
            ++result.stalemate;
        }
        if (all.capture_a) {
            flags |= Flag_Capture_A;
            ++result.capture_a;
        }
        if (all.capture_b) {
            flags |= Flag_Capture_B;
            ++result.capture_b;
        }
        if (all.capture_a && all.capture_b) ++result.capture_both;
        if (state.minor_king >= Regular_Squares) {
            flags |= Flag_Detached;
            ++result.detached;
        }
        if (wdl[dense ^ 1U] == Draw) {
            flags |= Flag_Both_Turn;
            ++result.both_turn_records;
            if ((dense & 1U) == Rook_To_Move) ++result.both_turn_placements;
        }
        if (graph.in_check(state)) {
            flags |= Flag_In_Check;
            ++result.in_check;
            if (state.turn != Minor_To_Move || wdl[dense ^ 1U] != Invalid)
                throw std::logic_error("KCCK checked-draw turn legality changed");
        } else if (wdl[dense ^ 1U] == Invalid && state.turn == Minor_To_Move) {
            throw std::logic_error("opposite-turn invalid draw is not check");
        }

        if (state.turn == Rook_To_Move) {
            if (all.capture_a || all.capture_b ||
                drawing.internal.size() != all.internal.size())
                throw std::logic_error("attacker draw has a non-draw successor");
        } else if (!(flags & (Flag_Stalemate | Flag_Capture_A |
                              Flag_Capture_B)) &&
                   drawing.internal.empty()) {
            throw std::logic_error(
                "nonterminal defender draw has no draw-preserving resource");
        }

        result.flags[dense] = flags;
        ++result.combinations[flags];
        if (flags & Flag_Stalemate) ++result.precedence[0];
        else if (flags & (Flag_Capture_A | Flag_Capture_B)) ++result.precedence[1];
        else if (flags & Flag_Detached) ++result.precedence[2];
        else if (flags & Flag_Both_Turn) ++result.precedence[3];
        else ++result.precedence[4];
    }

    if (result.draws != 1'852'083 ||
        result.attacker_draws != 93'247 ||
        result.defender_draws != 1'758'836 ||
        result.capture_a != result.capture_b ||
        result.both_turn_records != 186'494 ||
        result.both_turn_placements != 93'247 ||
        result.in_check != 898'234)
        throw std::logic_error("frozen KCCK local draw invariants changed");
    std::uint64_t precedence_total = 0;
    for (std::uint64_t count : result.precedence) precedence_total += count;
    if (precedence_total != result.draws)
        throw std::logic_error("KCCK draw precedence partition does not sum");
    std::vector<std::uint8_t> edge_bytes;
    edge_bytes.reserve(result.draw_edge_keys.size() * 8);
    for (std::uint64_t edge : result.draw_edge_keys)
        for (int byte = 0; byte < 8; ++byte)
            edge_bytes.push_back(static_cast<std::uint8_t>(edge >> (byte * 8)));
    result.draw_edge_digest = sha256(edge_bytes.data(), edge_bytes.size());
    return result;
}

void verify_inverse_edges(const Kcck_Graph & graph,
                          const std::vector<std::uint8_t> & wdl,
                          const Local_Result & local) {
    std::vector<std::uint64_t> inverse;
    inverse.reserve(local.draw_edge_keys.size());
    for (std::uint32_t child = 0; child < Four_Man_State_Count; ++child) {
        if (wdl[child] != Draw) continue;
        for (std::uint32_t parent :
             graph.draw_predecessors(graph.unrank(child)))
            inverse.push_back((static_cast<std::uint64_t>(parent) << 32) | child);
    }
    std::sort(inverse.begin(), inverse.end());
    if (inverse != local.draw_edge_keys)
        throw std::logic_error("KCCK draw successor/predecessor edge sets disagree");
}

struct Component_Info {
    std::uint32_t minimum = No_Component;
    std::uint64_t size = 0;
    bool self_edge = false;
    bool any_detached = false;
    bool all_detached = true;
};

struct Scc_Result {
    std::vector<std::uint32_t> component;
    std::vector<Component_Info> info;
    std::string digest;
    std::uint64_t component_count = 0;
    std::uint64_t cyclic_components = 0;
    std::uint64_t cyclic_members = 0;
    std::uint64_t largest_component = 0;
    std::uint64_t detached_corner_cycles = 0;
    std::uint64_t detached_corner_cycle_members = 0;
    std::uint64_t corner_associated_cycles = 0;
    std::uint64_t corner_associated_cycle_members = 0;
};

std::vector<std::uint32_t> ordered_edges(std::vector<std::uint32_t> edges,
                                         bool reverse_iteration) {
    if (reverse_iteration) std::reverse(edges.begin(), edges.end());
    return edges;
}

Scc_Result solve_scc(const Kcck_Graph & graph,
                     const std::vector<std::uint8_t> & wdl,
                     bool reverse_iteration) {
    Bit_Set visited(Four_Man_State_Count);
    std::vector<std::uint32_t> finish;
    finish.reserve(1'852'083);
    std::vector<std::pair<std::uint32_t, bool>> stack;
    stack.reserve(4096);

    auto first_pass_root = [&](std::uint32_t root) {
        stack.push_back({ root, false });
        while (!stack.empty()) {
            const auto [dense, expanded] = stack.back();
            stack.pop_back();
            if (expanded) {
                finish.push_back(dense);
                continue;
            }
            if (!visited.set(dense)) continue;
            stack.push_back({ dense, true });
            std::vector<std::uint32_t> edges =
                ordered_edges(graph.successors(graph.unrank(dense), true).internal,
                              reverse_iteration);
            for (auto iterator = edges.rbegin(); iterator != edges.rend(); ++iterator)
                if (!visited.test(*iterator)) stack.push_back({ *iterator, false });
        }
    };

    if (!reverse_iteration) {
        for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense)
            if (wdl[dense] == Draw && !visited.test(dense))
                first_pass_root(dense);
    } else {
        for (std::uint32_t dense = Four_Man_State_Count; dense-- > 0;)
            if (wdl[dense] == Draw && !visited.test(dense))
                first_pass_root(dense);
    }
    if (finish.size() != 1'852'083)
        throw std::logic_error("KCCK SCC first pass did not visit every draw");

    Scc_Result result;
    result.component.assign(Four_Man_State_Count, No_Component);
    std::vector<std::uint32_t> node_stack;
    node_stack.reserve(4096);

    for (auto order = finish.rbegin(); order != finish.rend(); ++order) {
        const std::uint32_t root = *order;
        if (result.component[root] != No_Component) continue;
        const std::uint32_t component_id =
            static_cast<std::uint32_t>(result.info.size());
        result.info.push_back(Component_Info{});
        Component_Info & info = result.info.back();
        node_stack.push_back(root);
        result.component[root] = component_id;
        while (!node_stack.empty()) {
            const std::uint32_t dense = node_stack.back();
            node_stack.pop_back();
            const State4 state = graph.unrank(dense);
            ++info.size;
            info.minimum = std::min(info.minimum, dense);
            const bool detached = state.minor_king >= Regular_Squares;
            info.any_detached = info.any_detached || detached;
            info.all_detached = info.all_detached && detached;
            const std::vector<std::uint32_t> successors =
                graph.successors(state, true).internal;
            info.self_edge = info.self_edge ||
                std::binary_search(successors.begin(), successors.end(), dense);

            std::vector<std::uint32_t> parents =
                ordered_edges(graph.draw_predecessors(state), reverse_iteration);
            for (std::uint32_t parent : parents) {
                if (result.component[parent] != No_Component) continue;
                result.component[parent] = component_id;
                node_stack.push_back(parent);
            }
        }
    }

    result.component_count = result.info.size();
    for (const Component_Info & info : result.info) {
        result.largest_component = std::max(result.largest_component, info.size);
        const bool cyclic = info.size > 1 || info.self_edge;
        if (!cyclic) continue;
        ++result.cyclic_components;
        result.cyclic_members += info.size;
        if (info.all_detached) {
            ++result.detached_corner_cycles;
            result.detached_corner_cycle_members += info.size;
        }
        if (info.any_detached) {
            ++result.corner_associated_cycles;
            result.corner_associated_cycle_members += info.size;
        }
    }

    std::vector<std::uint8_t> digest_bytes;
    digest_bytes.reserve(1'852'083ULL * 8);
    auto append_u32 = [&](std::uint32_t value) {
        digest_bytes.push_back(static_cast<std::uint8_t>(value));
        digest_bytes.push_back(static_cast<std::uint8_t>(value >> 8));
        digest_bytes.push_back(static_cast<std::uint8_t>(value >> 16));
        digest_bytes.push_back(static_cast<std::uint8_t>(value >> 24));
    };
    for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
        if (wdl[dense] != Draw) continue;
        const std::uint32_t id = result.component[dense];
        if (id == No_Component || id >= result.info.size())
            throw std::logic_error("KCCK draw has no SCC");
        append_u32(dense);
        append_u32(result.info[id].minimum);
    }
    result.digest = sha256(digest_bytes.data(), digest_bytes.size());
    return result;
}

void require_scc_parity(const Scc_Result & first, const Scc_Result & second,
                        const std::vector<std::uint8_t> & wdl) {
    if (first.component_count != second.component_count ||
        first.cyclic_components != second.cyclic_components ||
        first.cyclic_members != second.cyclic_members ||
        first.largest_component != second.largest_component ||
        first.detached_corner_cycles != second.detached_corner_cycles ||
        first.detached_corner_cycle_members !=
            second.detached_corner_cycle_members ||
        first.corner_associated_cycles != second.corner_associated_cycles ||
        first.corner_associated_cycle_members !=
            second.corner_associated_cycle_members ||
        first.digest != second.digest)
        throw std::logic_error("KCCK SCC summary changed under reversed traversal");
    for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
        if (wdl[dense] != Draw) continue;
        const std::uint32_t first_min =
            first.info[first.component[dense]].minimum;
        const std::uint32_t second_min =
            second.info[second.component[dense]].minimum;
        if (first_min != second_min)
            throw std::logic_error("KCCK normalized SCC changed under reversed traversal");
    }
}

std::vector<std::uint8_t> solve_reachability(
    const Kcck_Graph & graph,
    const std::vector<std::uint8_t> & wdl,
    const Local_Result & local,
    const Scc_Result & scc) {
    std::vector<std::uint8_t> mask(Four_Man_State_Count, 0);
    std::vector<std::uint32_t> queue;
    queue.reserve(1'852'083);

    auto propagate = [&](std::uint8_t bit) {
        queue.clear();
        for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
            if (wdl[dense] != Draw) continue;
            bool seed = false;
            if (bit == Reach_Stalemate)
                seed = (local.flags[dense] & Flag_Stalemate) != 0;
            else if (bit == Reach_Capture_A)
                seed = (local.flags[dense] & Flag_Capture_A) != 0;
            else if (bit == Reach_Capture_B)
                seed = (local.flags[dense] & Flag_Capture_B) != 0;
            else {
                const Component_Info & info =
                    scc.info[scc.component[dense]];
                seed = info.size > 1 || info.self_edge;
            }
            if (seed && !(mask[dense] & bit)) {
                mask[dense] |= bit;
                queue.push_back(dense);
            }
        }
        std::size_t head = 0;
        while (head < queue.size()) {
            const std::uint32_t child = queue[head++];
            for (std::uint32_t parent :
                 graph.draw_predecessors(graph.unrank(child))) {
                if (mask[parent] & bit) continue;
                mask[parent] |= bit;
                queue.push_back(parent);
            }
        }
    };

    propagate(Reach_Stalemate);
    propagate(Reach_Capture_A);
    propagate(Reach_Capture_B);
    propagate(Reach_Cycle);
    return mask;
}

std::uint64_t solve_defender_attractor(
    const Kcck_Graph & graph,
    const std::vector<std::uint8_t> & wdl,
    const Local_Result & local,
    std::uint8_t direct_flags,
    std::uint8_t result_bit,
    std::vector<std::uint8_t> & result_mask) {
    Bit_Set attracted(Four_Man_State_Count);
    std::vector<std::uint8_t> remaining = local.draw_degree;
    std::vector<std::uint32_t> queue;
    queue.reserve(1'852'083);

    for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
        if (wdl[dense] != Draw) continue;
        if ((local.flags[dense] & direct_flags) == 0) continue;
        attracted.set(dense);
        result_mask[dense] |= result_bit;
        queue.push_back(dense);
    }

    std::size_t head = 0;
    while (head < queue.size()) {
        const std::uint32_t child = queue[head++];
        for (std::uint32_t parent :
             graph.draw_predecessors(graph.unrank(child))) {
            if (attracted.test(parent)) continue;
            const State4 parent_state = graph.unrank(parent);
            bool add = false;
            if (parent_state.turn == Minor_To_Move) {
                add = true;
            } else {
                if (remaining[parent] == 0)
                    throw std::logic_error("KCCK attractor counter underflow");
                --remaining[parent];
                add = remaining[parent] == 0;
            }
            if (add) {
                attracted.set(parent);
                result_mask[parent] |= result_bit;
                queue.push_back(parent);
            }
        }
    }
    return queue.size();
}

std::uint8_t swap_capture_bits(std::uint8_t mask,
                               std::uint8_t bit_a,
                               std::uint8_t bit_b) {
    const bool has_a = (mask & bit_a) != 0;
    const bool has_b = (mask & bit_b) != 0;
    mask &= static_cast<std::uint8_t>(~(bit_a | bit_b));
    if (has_a) mask |= bit_b;
    if (has_b) mask |= bit_a;
    return mask;
}

void verify_label_swap(const Kcck_Graph & graph,
                       const std::vector<std::uint8_t> & wdl,
                       const Local_Result & local,
                       const Scc_Result & scc,
                       const std::vector<std::uint8_t> & reach,
                       const std::vector<std::uint8_t> & attractor) {
    for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
        if (wdl[dense] != Draw) continue;
        const State4 state = graph.unrank(dense);
        const State4 swapped {
            state.rook_king, state.minor, state.minor_king, state.rook, state.turn
        };
        const std::uint32_t other = graph.rank(swapped);
        if (wdl[other] != Draw)
            throw std::logic_error("KCCK label swap left the draw population");
        const std::uint8_t expected_local = swap_capture_bits(
            local.flags[dense], Flag_Capture_A, Flag_Capture_B);
        const std::uint8_t expected_reach = swap_capture_bits(
            reach[dense], Reach_Capture_A, Reach_Capture_B);
        const std::uint8_t expected_attractor = swap_capture_bits(
            attractor[dense], Attractor_Capture_A, Attractor_Capture_B);
        if (local.flags[other] != expected_local ||
            reach[other] != expected_reach ||
            attractor[other] != expected_attractor)
            throw std::logic_error("KCCK draw classification is not label-swap invariant");
        const Component_Info & first = scc.info[scc.component[dense]];
        const Component_Info & second = scc.info[scc.component[other]];
        if (first.size != second.size ||
            first.self_edge != second.self_edge ||
            first.any_detached != second.any_detached ||
            first.all_detached != second.all_detached)
            throw std::logic_error("KCCK SCC shape is not label-swap invariant");
    }
}

struct Robust_Result {
    std::array<std::uint64_t, 4096> local_pairs{};
    std::array<std::uint64_t, 256> reach_pairs{};
    std::array<std::uint64_t, 1024> attractor_pairs{};
    std::uint64_t placements = 0;
    std::uint64_t defender_immediate_capture = 0;
    std::uint64_t either_turn_stalemate = 0;
    std::uint64_t both_records_force_terminal = 0;
};

Robust_Result analyze_robust_placements(
    const std::vector<std::uint8_t> & wdl,
    const Local_Result & local,
    const std::vector<std::uint8_t> & reach,
    const std::vector<std::uint8_t> & attractor) {
    Robust_Result result;
    for (std::uint32_t attacker = 0;
         attacker < Four_Man_State_Count; attacker += 2) {
        const std::uint32_t defender = attacker + 1;
        if (wdl[attacker] != Draw || wdl[defender] != Draw) continue;
        ++result.placements;
        const std::uint32_t local_pair =
            static_cast<std::uint32_t>(local.flags[attacker]) |
            (static_cast<std::uint32_t>(local.flags[defender]) << 6);
        const std::uint32_t reach_pair =
            static_cast<std::uint32_t>(reach[attacker]) |
            (static_cast<std::uint32_t>(reach[defender]) << 4);
        const std::uint32_t attractor_pair =
            static_cast<std::uint32_t>(attractor[attacker]) |
            (static_cast<std::uint32_t>(attractor[defender]) << 5);
        ++result.local_pairs[local_pair];
        ++result.reach_pairs[reach_pair];
        ++result.attractor_pairs[attractor_pair];
        if (local.flags[defender] & (Flag_Capture_A | Flag_Capture_B))
            ++result.defender_immediate_capture;
        if ((local.flags[attacker] | local.flags[defender]) & Flag_Stalemate)
            ++result.either_turn_stalemate;
        if ((attractor[attacker] & Attractor_Terminal) &&
            (attractor[defender] & Attractor_Terminal))
            ++result.both_records_force_terminal;
    }
    if (result.placements != 93'247 ||
        result.both_records_force_terminal != result.placements)
        throw std::logic_error("KCCK robust-placement classification changed");
    return result;
}

std::string file_sha256(const std::string & path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot open artifact for container hash");
    std::vector<std::uint8_t> bytes {
        std::istreambuf_iterator<char>(stream),
        std::istreambuf_iterator<char>()
    };
    return sha256(bytes.data(), bytes.size());
}

std::string count_array_json(const std::array<std::uint64_t, 64> & counts) {
    std::ostringstream out;
    out << '[';
    bool first = true;
    for (std::size_t mask = 0; mask < counts.size(); ++mask) {
        if (counts[mask] == 0) continue;
        if (!first) out << ',';
        first = false;
        out << "{\"mask\":" << mask << ",\"count\":" << counts[mask] << '}';
    }
    out << ']';
    return out.str();
}

std::string count_vector_json(const std::vector<std::uint64_t> & counts) {
    std::ostringstream out;
    out << '[';
    bool first = true;
    for (std::size_t mask = 0; mask < counts.size(); ++mask) {
        if (counts[mask] == 0) continue;
        if (!first) out << ',';
        first = false;
        out << "{\"mask\":" << mask << ",\"count\":" << counts[mask] << '}';
    }
    out << ']';
    return out.str();
}

std::string square_name(std::uint8_t square) {
    if (square < Regular_Squares) {
        std::string result;
        result += static_cast<char>('a' + square / 10);
        result += static_cast<char>('0' + square % 10);
        return result;
    }
    return std::string("w") +
        static_cast<char>('1' + square - Regular_Squares);
}

std::string state_json(const Kcck_Graph & graph, std::uint32_t dense) {
    const State4 state = graph.unrank(dense);
    std::ostringstream out;
    out << "{\"dense\":" << dense
        << ",\"state\":[" << static_cast<unsigned>(state.rook_king)
        << ',' << static_cast<unsigned>(state.rook)
        << ',' << static_cast<unsigned>(state.minor_king)
        << ',' << static_cast<unsigned>(state.minor)
        << ',' << static_cast<unsigned>(state.turn) << ']'
        << ",\"squares\":[\"" << square_name(state.rook_king)
        << "\",\"" << square_name(state.rook)
        << "\",\"" << square_name(state.minor_king)
        << "\",\"" << square_name(state.minor) << "\"]}";
    return out.str();
}

std::string frozen_witness_json(const Kcck_Graph & graph,
                                std::uint32_t dense,
                                const State4 & stated) {
    if (graph.rank(stated) != dense)
        throw std::logic_error("KCCK frozen witness rank changed");
    const State4 canonical = graph.unrank(dense);
    std::ostringstream out;
    out << "{\"dense\":" << dense
        << ",\"state\":[" << static_cast<unsigned>(stated.rook_king)
        << ',' << static_cast<unsigned>(stated.rook)
        << ',' << static_cast<unsigned>(stated.minor_king)
        << ',' << static_cast<unsigned>(stated.minor)
        << ',' << static_cast<unsigned>(stated.turn) << ']'
        << ",\"squares\":[\"" << square_name(stated.rook_king)
        << "\",\"" << square_name(stated.rook)
        << "\",\"" << square_name(stated.minor_king)
        << "\",\"" << square_name(stated.minor) << "\"]"
        << ",\"canonical_state\":["
        << static_cast<unsigned>(canonical.rook_king)
        << ',' << static_cast<unsigned>(canonical.rook)
        << ',' << static_cast<unsigned>(canonical.minor_king)
        << ',' << static_cast<unsigned>(canonical.minor)
        << ',' << static_cast<unsigned>(canonical.turn) << ']'
        << ",\"canonical_squares\":[\"" << square_name(canonical.rook_king)
        << "\",\"" << square_name(canonical.rook)
        << "\",\"" << square_name(canonical.minor_king)
        << "\",\"" << square_name(canonical.minor) << "\"]}";
    return out.str();
}

template <std::size_t Size>
std::string sparse_array_json(const std::array<std::uint64_t, Size> & counts) {
    std::ostringstream out;
    out << '[';
    bool first = true;
    for (std::size_t key = 0; key < counts.size(); ++key) {
        if (counts[key] == 0) continue;
        if (!first) out << ',';
        first = false;
        out << "{\"key\":" << key << ",\"count\":" << counts[key] << '}';
    }
    out << ']';
    return out.str();
}

void write_text_atomic(const std::string & path, const std::string & text) {
    const std::string temporary = path + ".tmp";
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        if (!stream) throw std::runtime_error("cannot create draw-analysis output");
        stream << text << '\n';
        if (!stream) throw std::runtime_error("failed to write draw-analysis output");
    }
    std::remove(path.c_str());
    if (std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(temporary.c_str());
        throw std::runtime_error("failed to atomically replace draw-analysis output");
    }
}

void self_test(const Kcck_Graph & graph,
               const D4Indexer & index,
               const std::vector<std::uint8_t> & wdl) {
    struct Fixture {
        State4 state;
        std::uint32_t dense;
        bool stalemate;
        bool capture_a;
        bool capture_b;
    };
    const std::array<Fixture, 7> fixtures {{
        { State4 { 1, 0, 100, 2, 1 }, 1'081'893, true, false, false },
        { State4 { 2, 10, 0, 1, 1 }, 3'369'745, false, true, false },
        { State4 { 2, 1, 0, 10, 1 }, 3'204'927, false, false, true },
        { State4 { 55, 22, 100, 77, 1 }, 25'533'851, true, false, false },
        { State4 { 0, 11, 7, 103, 0 }, 2'512, false, false, false },
        { State4 { 0, 11, 7, 103, 1 }, 2'513, false, false, false },
        { State4 { 0, 11, 66, 88, 1 }, 451, false, false, false },
    }};
    for (const Fixture & fixture : fixtures) {
        if (!graph.is_legal(fixture.state) ||
            graph.rank(fixture.state) != fixture.dense ||
            wdl[fixture.dense] != Draw)
            throw std::logic_error("KCCK draw-analysis fixture identity changed");
        const Graph_Edges edges = graph.successors(fixture.state, false);
        if ((!edges.has_move) != fixture.stalemate ||
            edges.capture_a != fixture.capture_a ||
            edges.capture_b != fixture.capture_b)
            throw std::logic_error(
                "KCCK draw-analysis fixture behavior changed at dense " +
                std::to_string(fixture.dense) + " actual=" +
                std::to_string(edges.has_move) + "," +
                std::to_string(edges.capture_a) + "," +
                std::to_string(edges.capture_b));
    }
    const State4 transition { 0, 11, 66, 88, 1 };
    const Graph_Edges transition_edges = graph.successors(transition, true);
    if (wdl[450U] != Invalid || wdl[560U] != Draw || wdl[561U] != Draw)
        throw std::logic_error("KCCK residual turn-history fixture changed");
    if (!std::binary_search(transition_edges.internal.begin(),
                            transition_edges.internal.end(), 560U))
        throw std::logic_error("KCCK residual draw transition changed");
    const State4 child = graph.unrank(560U);
    const std::vector<std::uint32_t> parents = graph.draw_predecessors(child);
    if (!std::binary_search(parents.begin(), parents.end(), 451U))
        throw std::logic_error("KCCK residual predecessor transition changed");
    (void) index;
}

void usage() {
    std::cout
        << "kcck_draw_analysis --wdl omega-kcck-wdl-v1.omtb4 "
           "[--output omega-kcck-draw-analysis-v1.json]\n";
}

} // namespace
} // namespace omega_tb4

int main(int argc, char ** argv) {
    using namespace omega_tb4;
    try {
        std::string wdl_path;
        std::string output_path;
        for (int index = 1; index < argc; ++index) {
            const std::string option = argv[index];
            auto value = [&](const char * name) {
                if (++index >= argc)
                    throw std::invalid_argument(std::string(name) + " requires a value");
                return std::string(argv[index]);
            };
            if (option == "--wdl") wdl_path = value("--wdl");
            else if (option == "--output") output_path = value("--output");
            else if (option == "--help" || option == "-h") {
                usage();
                return 0;
            } else {
                throw std::invalid_argument("unknown option: " + option);
            }
        }
        if (wdl_path.empty()) {
            usage();
            return 2;
        }

        const auto started = std::chrono::steady_clock::now();
        const FourManTable table = read_four_man_file(wdl_path);
        if (table.material != FourManMaterial::Kcck ||
            table.payload.size() != Four_Man_State_Count ||
            sha256(table.payload.data(), table.payload.size()) != Expected_Wdl_Payload ||
            file_sha256(wdl_path) != Expected_Wdl_Container ||
            table.dependency_sha256 !=
                four_man_capture_policy_sha256(FourManMaterial::Kcck))
            throw std::runtime_error("KCCK draw analysis requires the frozen WDL artifact");

        Geometry geometry;
        D4Indexer four_index(geometry, 4);
        Kcck_Graph graph(geometry, four_index, table.payload);
        self_test(graph, four_index, table.payload);

        std::cerr << "KCCK draw analysis: local flags\n";
        Local_Result local = analyze_local(graph, table.payload);
        std::cerr << "KCCK draw analysis: inverse-edge parity\n";
        verify_inverse_edges(graph, table.payload, local);
        local.draw_edge_keys.clear();
        local.draw_edge_keys.shrink_to_fit();
        std::cerr << "KCCK draw analysis: SCC forward traversal\n";
        Scc_Result scc = solve_scc(graph, table.payload, false);
        std::cerr << "KCCK draw analysis: SCC reversed traversal\n";
        Scc_Result reversed = solve_scc(graph, table.payload, true);
        require_scc_parity(scc, reversed, table.payload);
        reversed.component.clear();
        reversed.component.shrink_to_fit();
        reversed.info.clear();
        reversed.info.shrink_to_fit();

        std::cerr << "KCCK draw analysis: target/cycle reachability\n";
        const std::vector<std::uint8_t> reach =
            solve_reachability(graph, table.payload, local, scc);
        std::vector<std::uint64_t> reach_counts(16, 0);
        std::uint64_t unexplained = 0;
        for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
            if (table.payload[dense] != Draw) continue;
            ++reach_counts[reach[dense]];
            if (reach[dense] == 0) ++unexplained;
        }
        if (unexplained != 0)
            throw std::logic_error("KCCK draw reachability left unexplained records");

        std::cerr << "KCCK draw analysis: alternating attractors\n";
        std::vector<std::uint8_t> attractor(Four_Man_State_Count, 0);
        const std::uint64_t force_stalemate = solve_defender_attractor(
            graph, table.payload, local, Flag_Stalemate,
            Attractor_Stalemate, attractor);
        const std::uint64_t force_capture_a = solve_defender_attractor(
            graph, table.payload, local, Flag_Capture_A,
            Attractor_Capture_A, attractor);
        const std::uint64_t force_capture_b = solve_defender_attractor(
            graph, table.payload, local, Flag_Capture_B,
            Attractor_Capture_B, attractor);
        const std::uint64_t force_any_capture = solve_defender_attractor(
            graph, table.payload, local, Flag_Capture_A | Flag_Capture_B,
            Attractor_Any_Capture, attractor);
        const std::uint64_t force_terminal = solve_defender_attractor(
            graph, table.payload, local,
            Flag_Stalemate | Flag_Capture_A | Flag_Capture_B,
            Attractor_Terminal, attractor);
        std::vector<std::uint64_t> attractor_counts(32, 0);
        for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense)
            if (table.payload[dense] == Draw)
                ++attractor_counts[attractor[dense]];
        const std::uint64_t terminal_avoidable = local.draws - force_terminal;
        if (force_terminal != local.draws)
            throw std::logic_error("KCCK draw population escaped terminal attractor");

        std::cerr << "KCCK draw analysis: label-swap parity\n";
        verify_label_swap(
            graph, table.payload, local, scc, reach, attractor);
        std::cerr << "KCCK draw analysis: robust both-turn placements\n";
        const Robust_Result robust = analyze_robust_placements(
            table.payload, local, reach, attractor);

        std::uint32_t first_cyclic = No_Component;
        std::uint32_t largest_component = No_Component;
        for (const Component_Info & info : scc.info) {
            if ((info.size > 1 || info.self_edge) &&
                info.minimum < first_cyclic)
                first_cyclic = info.minimum;
            if (info.size == scc.largest_component &&
                info.minimum < largest_component)
                largest_component = info.minimum;
        }
        std::array<std::uint32_t, 5> attractor_witnesses;
        attractor_witnesses.fill(No_Component);
        const std::array<std::uint8_t, 5> witness_masks {{
            Attractor_Terminal,
            static_cast<std::uint8_t>(
                Attractor_Stalemate | Attractor_Terminal),
            static_cast<std::uint8_t>(
                Attractor_Capture_A | Attractor_Any_Capture |
                Attractor_Terminal),
            static_cast<std::uint8_t>(
                Attractor_Capture_B | Attractor_Any_Capture |
                Attractor_Terminal),
            static_cast<std::uint8_t>(
                Attractor_Capture_A | Attractor_Capture_B |
                Attractor_Any_Capture | Attractor_Terminal),
        }};
        for (std::uint32_t dense = 0; dense < Four_Man_State_Count; ++dense) {
            if (table.payload[dense] != Draw) continue;
            for (std::size_t index = 0; index < witness_masks.size(); ++index)
                if (attractor_witnesses[index] == No_Component &&
                    attractor[dense] == witness_masks[index])
                    attractor_witnesses[index] = dense;
        }
        for (std::uint32_t dense : attractor_witnesses)
            if (dense == No_Component)
                throw std::logic_error("KCCK attractor witness category is empty");

        std::ostringstream json;
        json << "{\"magic\":\"OMEGA-KCCK-DRAW-ANALYSIS\",\"version\":1"
             << ",\"analysis_identity\":\"" << Analysis_Identity << "\""
             << ",\"source_wdl_payload_sha256\":\"" << Expected_Wdl_Payload << "\""
             << ",\"source_wdl_container_sha256\":\"" << Expected_Wdl_Container << "\""
             << ",\"source_rules_sha256\":\""
             << four_man_rules_sha256(FourManMaterial::Kcck) << "\""
             << ",\"source_capture_policy_sha256\":\""
             << four_man_capture_policy_sha256(FourManMaterial::Kcck) << "\""
             << ",\"draw_records\":" << local.draws
             << ",\"attacker_draws\":" << local.attacker_draws
             << ",\"defender_draws\":" << local.defender_draws
             << ",\"local\":{\"stalemate\":" << local.stalemate
             << ",\"capture_a\":" << local.capture_a
             << ",\"capture_b\":" << local.capture_b
             << ",\"capture_both\":" << local.capture_both
             << ",\"detached_corner\":" << local.detached
             << ",\"both_turn_records\":" << local.both_turn_records
             << ",\"both_turn_placements\":" << local.both_turn_placements
             << ",\"in_check\":" << local.in_check
             << ",\"draw_edges\":" << local.draw_edges
             << ",\"draw_edge_digest\":\"" << local.draw_edge_digest << "\""
             << ",\"canonical_self_edges\":" << local.canonical_self_edges
             << ",\"inverse_edge_parity\":true"
             << ",\"label_swap_parity\":true"
             << ",\"combination_counts\":"
             << count_array_json(local.combinations)
             << ",\"precedence\":{\"stalemate\":" << local.precedence[0]
             << ",\"immediate_capture\":" << local.precedence[1]
             << ",\"detached_corner\":" << local.precedence[2]
             << ",\"both_turn\":" << local.precedence[3]
             << ",\"residual\":" << local.precedence[4] << "}}"
             << ",\"scc\":{\"components\":" << scc.component_count
             << ",\"cyclic_components\":" << scc.cyclic_components
             << ",\"cyclic_members\":" << scc.cyclic_members
             << ",\"largest_component\":" << scc.largest_component
             << ",\"detached_corner_cycles\":" << scc.detached_corner_cycles
             << ",\"detached_corner_cycle_members\":"
             << scc.detached_corner_cycle_members
             << ",\"corner_associated_cycles\":" << scc.corner_associated_cycles
             << ",\"corner_associated_cycle_members\":"
             << scc.corner_associated_cycle_members
             << ",\"normalized_digest\":\"" << scc.digest << "\""
             << ",\"reverse_iteration_parity\":true}"
             << ",\"reachability\":{\"combination_counts\":"
             << count_vector_json(reach_counts)
             << ",\"unexplained\":" << unexplained << "}"
             << ",\"defender_attractor\":{\"force_stalemate\":"
             << force_stalemate
             << ",\"force_capture_a\":" << force_capture_a
             << ",\"force_capture_b\":" << force_capture_b
             << ",\"force_any_capture\":" << force_any_capture
             << ",\"force_terminal_draw\":" << force_terminal
             << ",\"terminal_avoidable\":" << terminal_avoidable
             << ",\"combination_counts\":"
             << count_vector_json(attractor_counts) << "}"
             << ",\"robust_both_turn\":{\"placements\":"
             << robust.placements
             << ",\"defender_immediate_capture\":"
             << robust.defender_immediate_capture
             << ",\"either_turn_stalemate\":" << robust.either_turn_stalemate
             << ",\"both_records_force_terminal\":"
             << robust.both_records_force_terminal
             << ",\"local_pair_counts\":"
             << sparse_array_json(robust.local_pairs)
             << ",\"reach_pair_counts\":"
             << sparse_array_json(robust.reach_pairs)
             << ",\"attractor_pair_counts\":"
             << sparse_array_json(robust.attractor_pairs) << "}"
             << ",\"witnesses\":{"
             << "\"terminal_stalemate\":"
             << frozen_witness_json(
                    graph, 1'081'893U, State4 { 1, 0, 100, 2, 1 })
             << ",\"capture_a_exit\":"
             << frozen_witness_json(
                    graph, 3'369'745U, State4 { 2, 10, 0, 1, 1 })
             << ",\"capture_b_exit\":"
             << frozen_witness_json(
                    graph, 3'204'927U, State4 { 2, 1, 0, 10, 1 })
             << ",\"noncapture_detached_corner\":"
             << frozen_witness_json(
                    graph, 25'533'851U, State4 { 55, 22, 100, 77, 1 })
             << ",\"both_turn_attacker\":"
             << frozen_witness_json(
                    graph, 2'512U, State4 { 0, 11, 7, 103, 0 })
             << ",\"both_turn_defender\":"
             << frozen_witness_json(
                    graph, 2'513U, State4 { 0, 11, 7, 103, 1 })
             << ",\"residual_transition_parent\":"
             << frozen_witness_json(
                    graph, 451U, State4 { 0, 11, 66, 88, 1 })
             << ",\"residual_transition_child\":"
             << state_json(graph, 560U)
             << ",\"first_cyclic_scc\":"
             << state_json(graph, first_cyclic)
             << ",\"largest_cyclic_scc\":"
             << state_json(graph, largest_component)
             << ",\"combined_terminal_only\":"
             << state_json(graph, attractor_witnesses[0])
             << ",\"force_stalemate\":"
             << state_json(graph, attractor_witnesses[1])
             << ",\"force_capture_a\":"
             << state_json(graph, attractor_witnesses[2])
             << ",\"force_capture_b\":"
             << state_json(graph, attractor_witnesses[3])
             << ",\"force_either_labelled_capture\":"
             << state_json(graph, attractor_witnesses[4]) << "}}";

        if (!output_path.empty()) write_text_atomic(output_path, json.str());
        const double seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        std::cout << json.str() << '\n';
        std::cout << "KCCK draw analysis PASS time=" << std::fixed
                  << std::setprecision(2) << seconds << "s\n";
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "kcck_draw_analysis: " << error.what() << '\n';
        return 1;
    }
}
