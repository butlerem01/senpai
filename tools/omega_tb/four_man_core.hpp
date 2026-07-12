#ifndef OMEGA_TB_FOUR_MAN_CORE_HPP
#define OMEGA_TB_FOUR_MAN_CORE_HPP

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace omega_tb4 {

constexpr std::uint32_t Square_Count = 104;
constexpr std::uint32_t Regular_Squares = 100;
constexpr std::uint32_t Four_Man_State_Count = 27'594'696;
constexpr std::uint32_t Four_Man_Legal_Count = 22'607'206;
constexpr std::uint32_t Three_Man_State_Count = 273'816;

constexpr std::uint8_t Invalid = 0;
constexpr std::uint8_t Unknown = 1;
constexpr std::uint8_t Loss = 2;
constexpr std::uint8_t Draw = 3;
constexpr std::uint8_t Win = 4;

constexpr std::uint8_t Rook_To_Move = 0;
constexpr std::uint8_t Champion_To_Move = 1;
constexpr std::uint8_t Strong_To_Move = 0;
constexpr std::uint8_t Weak_To_Move = 1;

struct Coordinate {
    int x = 0;
    int y = 0;
};

struct State4 {
    std::uint8_t rook_king = 0;
    std::uint8_t rook = 0;
    std::uint8_t champion_king = 0;
    std::uint8_t champion = 0;
    std::uint8_t turn = 0;

    std::array<std::uint8_t, 4> squares() const {
        return { rook_king, rook, champion_king, champion };
    }
};

struct State3 {
    std::uint8_t strong_king = 0;
    std::uint8_t piece = 0;
    std::uint8_t weak_king = 0;
    std::uint8_t turn = 0;

    std::array<std::uint8_t, 3> squares() const {
        return { strong_king, piece, weak_king };
    }
};

class Geometry {
public:
    Geometry();

    const std::array<Coordinate, Square_Count> & coordinates() const { return coordinates_; }
    const std::array<std::array<std::uint8_t, Square_Count>, 8> & transforms() const {
        return transforms_;
    }
    const std::vector<std::uint8_t> & king_moves(std::uint8_t square) const {
        return king_moves_[square];
    }
    const std::vector<std::uint8_t> & champion_moves(std::uint8_t square) const {
        return champion_moves_[square];
    }
    const std::array<std::vector<std::uint8_t>, 4> & rook_rays(std::uint8_t square) const {
        return rook_rays_[square];
    }

    bool king_attacks(std::uint8_t first, std::uint8_t second) const;
    bool champion_attacks(std::uint8_t first, std::uint8_t second) const;
    bool rook_attacks(std::uint8_t first, std::uint8_t second,
                      const std::array<std::uint8_t, 2> & blockers) const;

private:
    std::array<Coordinate, Square_Count> coordinates_{};
    std::array<std::array<std::uint8_t, Square_Count>, 8> transforms_{};
    std::array<std::vector<std::uint8_t>, Square_Count> king_moves_{};
    std::array<std::vector<std::uint8_t>, Square_Count> champion_moves_{};
    std::array<std::array<std::vector<std::uint8_t>, 4>, Square_Count> rook_rays_{};
};

class D4Indexer {
public:
    D4Indexer(const Geometry & geometry, int piece_count);

    std::uint32_t state_count() const { return state_count_; }
    std::uint32_t raw_state_count() const { return raw_state_count_; }
    int piece_count() const { return piece_count_; }

    std::uint32_t rank(const std::uint8_t * squares, std::uint8_t turn) const;
    void unrank(std::uint32_t rank, std::uint8_t * squares, std::uint8_t & turn) const;

private:
    struct Block {
        std::uint8_t representative = 0;
        std::vector<std::uint8_t> orbit;
        std::array<std::int8_t, Square_Count> base_transform{};
        std::vector<std::uint8_t> stabilizer;
        std::uint32_t offset = 0;
        std::uint32_t size = 0;
        std::array<std::uint8_t, Square_Count> to_local{};
        std::array<std::uint8_t, 103> from_local{};
        bool symmetric = false;
    };

    const Geometry & geometry_;
    int piece_count_ = 0;
    int tail_length_ = 0;
    std::uint32_t generic_block_size_ = 0;
    std::uint32_t symmetric_block_size_ = 0;
    std::vector<Block> blocks_;
    std::array<std::uint8_t, Square_Count> block_for_square_{};
    std::vector<std::uint32_t> symmetric_ranks_;
    std::vector<std::int32_t> symmetric_lookup_;
    std::uint32_t state_count_ = 0;
    std::uint32_t raw_state_count_ = 0;

    static std::uint32_t ordered_size(int universe, int length);
    static std::uint32_t rank_ordered(const std::uint8_t * items, int universe, int length);
    static void unrank_ordered(std::uint32_t rank, int universe, int length,
                               std::uint8_t * items);
    static std::uint8_t reflect_local(std::uint8_t local);
};

class KrkTable {
public:
    KrkTable(const Geometry & geometry, const D4Indexer & index);

    void load(const std::string & path);
    std::uint8_t probe(const State3 & state) const;
    bool loaded() const { return !payload_.empty(); }
    const std::string & payload_sha256() const { return payload_sha256_; }

private:
    const Geometry & geometry_;
    const D4Indexer & index_;
    std::vector<std::uint8_t> payload_;
    std::string payload_sha256_;

    bool is_legal(const State3 & state) const;
};

std::string sha256(const std::uint8_t * data, std::size_t size);
std::string sha256(const std::string & text);

std::string json_string(const std::string & header, const std::string & key);
std::uint64_t json_unsigned(const std::string & header, const std::string & key);

void write_four_man_file(const std::string & path, const std::vector<std::uint8_t> & payload,
                         std::uint64_t legal_count, bool complete,
                         const std::string & dependency_sha256);
std::vector<std::uint8_t> read_four_man_file(const std::string & path);
void inspect_four_man_file(const std::string & path);

} // namespace omega_tb4

#endif
