#include "four_man_core.hpp"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace omega_tb4 {
namespace {

constexpr const char * Three_Man_Index = "D4-first-piece-v1";
constexpr const char * Three_Man_Rules =
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "krk-theoretical-wdl;kck-insufficient-material-v1";
constexpr const char * Krkc_Rules =
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "krkc-theoretical-wdl;krk-OMTB3WDL-v1;kck-insufficient-material-v1";
constexpr const char * Krkn_Rules =
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "krkn-theoretical-wdl;krk-OMTB3WDL-v1;knk-insufficient-material-v1";
constexpr const char * Kwkn_Rules =
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "kwkn-theoretical-wdl;wizard-v1;knight-v1;"
    "kwk-insufficient-material-v1;knk-insufficient-material-v1";
constexpr const char * Kckw_Rules =
    "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
    "kckw-theoretical-wdl;champion-v1;wizard-v1;"
    "kck-insufficient-material-v1;kwk-insufficient-material-v1";

const char * four_man_rules(FourManMaterial material) {
    switch (material) {
        case FourManMaterial::Krkc: return Krkc_Rules;
        case FourManMaterial::Krkn: return Krkn_Rules;
        case FourManMaterial::Kwkn: return Kwkn_Rules;
        case FourManMaterial::Kckw: return Kckw_Rules;
    }
    throw std::logic_error("unknown four-man material rules");
}

const char * minor_name(FourManMaterial material) {
    if (material == FourManMaterial::Krkc) return "champion";
    if (material == FourManMaterial::Kckw) return "wizard";
    return "knight";
}

const char * primary_name(FourManMaterial material) {
    if (material == FourManMaterial::Kwkn) return "wizard";
    if (material == FourManMaterial::Kckw) return "champion";
    return "rook";
}

const char * primary_king_name(FourManMaterial material) {
    if (material == FourManMaterial::Kwkn) return "wizard_king";
    if (material == FourManMaterial::Kckw) return "champion_king";
    return "rook_king";
}

bool uses_capture_policy(FourManMaterial material) {
    return material == FourManMaterial::Kwkn || material == FourManMaterial::Kckw;
}

const char * capture_policy(FourManMaterial material) {
    if (material == FourManMaterial::Kwkn) return "kwk-knk-insufficient-material-v1";
    if (material == FourManMaterial::Kckw) return "kck-kwk-insufficient-material-v1";
    throw std::logic_error("material uses a KRK dependency rather than a capture policy");
}

std::uint8_t square_from_coordinate(const std::array<Coordinate, Square_Count> & coordinates,
                                    int x, int y) {
    for (std::uint32_t square = 0; square < Square_Count; ++square) {
        if (coordinates[square].x == x && coordinates[square].y == y)
            return static_cast<std::uint8_t>(square);
    }
    throw std::logic_error("transformed coordinate is not an Omega square");
}

bool distinct(const std::uint8_t * values, int count) {
    for (int i = 0; i < count; ++i) {
        if (values[i] >= Square_Count) return false;
        for (int j = 0; j < i; ++j)
            if (values[i] == values[j]) return false;
    }
    return true;
}

std::uint32_t rotate_right(std::uint32_t value, unsigned shift) {
    return (value >> shift) | (value << (32U - shift));
}

class Sha256 {
public:
    Sha256() { reset(); }

    void update(const std::uint8_t * data, std::size_t size) {
        for (std::size_t i = 0; i < size; ++i) {
            buffer_[buffer_size_++] = data[i];
            if (buffer_size_ == 64) {
                transform(buffer_.data());
                bit_count_ += 512;
                buffer_size_ = 0;
            }
        }
    }

    std::array<std::uint8_t, 32> finish() {
        std::array<std::uint8_t, 32> digest{};
        bit_count_ += static_cast<std::uint64_t>(buffer_size_) * 8;
        buffer_[buffer_size_++] = 0x80;
        if (buffer_size_ > 56) {
            while (buffer_size_ < 64) buffer_[buffer_size_++] = 0;
            transform(buffer_.data());
            buffer_size_ = 0;
        }
        while (buffer_size_ < 56) buffer_[buffer_size_++] = 0;
        for (int i = 7; i >= 0; --i)
            buffer_[buffer_size_++] = static_cast<std::uint8_t>(bit_count_ >> (i * 8));
        transform(buffer_.data());
        for (int i = 0; i < 8; ++i) {
            digest[i * 4] = static_cast<std::uint8_t>(state_[i] >> 24);
            digest[i * 4 + 1] = static_cast<std::uint8_t>(state_[i] >> 16);
            digest[i * 4 + 2] = static_cast<std::uint8_t>(state_[i] >> 8);
            digest[i * 4 + 3] = static_cast<std::uint8_t>(state_[i]);
        }
        return digest;
    }

private:
    std::array<std::uint32_t, 8> state_{};
    std::array<std::uint8_t, 64> buffer_{};
    std::size_t buffer_size_ = 0;
    std::uint64_t bit_count_ = 0;

    void reset() {
        state_ = { 0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
                   0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U };
    }

    void transform(const std::uint8_t * block) {
        static constexpr std::uint32_t constants[64] = {
            0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
            0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
            0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
            0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
            0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
            0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
            0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
            0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
        };
        std::uint32_t words[64]{};
        for (int i = 0; i < 16; ++i) {
            words[i] = (static_cast<std::uint32_t>(block[i * 4]) << 24) |
                       (static_cast<std::uint32_t>(block[i * 4 + 1]) << 16) |
                       (static_cast<std::uint32_t>(block[i * 4 + 2]) << 8) |
                       static_cast<std::uint32_t>(block[i * 4 + 3]);
        }
        for (int i = 16; i < 64; ++i) {
            const std::uint32_t s0 = rotate_right(words[i - 15], 7) ^
                                     rotate_right(words[i - 15], 18) ^ (words[i - 15] >> 3);
            const std::uint32_t s1 = rotate_right(words[i - 2], 17) ^
                                     rotate_right(words[i - 2], 19) ^ (words[i - 2] >> 10);
            words[i] = words[i - 16] + s0 + words[i - 7] + s1;
        }

        std::uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
        std::uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
        for (int i = 0; i < 64; ++i) {
            const std::uint32_t s1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
            const std::uint32_t choose = (e & f) ^ (~e & g);
            const std::uint32_t temp1 = h + s1 + choose + constants[i] + words[i];
            const std::uint32_t s0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t temp2 = s0 + majority;
            h = g; g = f; f = e; e = d + temp1;
            d = c; c = b; b = a; a = temp1 + temp2;
        }
        state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
        state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
    }
};

std::string digest_hex(const std::array<std::uint8_t, 32> & digest) {
    std::ostringstream stream;
    stream << std::hex << std::setfill('0');
    for (std::uint8_t byte : digest) stream << std::setw(2) << static_cast<unsigned>(byte);
    return stream.str();
}

std::vector<std::uint8_t> read_payload(const std::string & path, std::string & header) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot open table: " + path);
    if (!std::getline(stream, header) || header.size() > 64 * 1024)
        throw std::runtime_error("missing or oversized table header");
    return std::vector<std::uint8_t>(std::istreambuf_iterator<char>(stream),
                                     std::istreambuf_iterator<char>());
}

} // namespace

Geometry::Geometry() {
    std::uint32_t square = 0;
    for (int file = 0; file < 10; ++file)
        for (int rank = 0; rank < 10; ++rank)
            coordinates_[square++] = { file, rank };
    coordinates_[100] = { -1, -1 };
    coordinates_[101] = { 10, -1 };
    coordinates_[102] = { 10, 10 };
    coordinates_[103] = { -1, 10 };

    for (int transform = 0; transform < 8; ++transform) {
        for (std::uint32_t source = 0; source < Square_Count; ++source) {
            int x = coordinates_[source].x;
            int y = coordinates_[source].y;
            int rotations = transform;
            if (rotations >= 4) {
                x = 9 - x;
                rotations -= 4;
            }
            for (int i = 0; i < rotations; ++i) {
                const int old_x = x;
                x = 9 - y;
                y = old_x;
            }
            transforms_[transform][source] = square_from_coordinate(coordinates_, x, y);
        }
    }

    for (std::uint32_t origin = 0; origin < Square_Count; ++origin) {
        for (std::uint32_t target = 0; target < Square_Count; ++target) {
            if (origin == target) continue;
            if (king_attacks(static_cast<std::uint8_t>(origin), static_cast<std::uint8_t>(target)))
                king_moves_[origin].push_back(static_cast<std::uint8_t>(target));
            if (champion_attacks(static_cast<std::uint8_t>(origin), static_cast<std::uint8_t>(target)))
                champion_moves_[origin].push_back(static_cast<std::uint8_t>(target));
            if (knight_attacks(static_cast<std::uint8_t>(origin), static_cast<std::uint8_t>(target)))
                knight_moves_[origin].push_back(static_cast<std::uint8_t>(target));
            if (wizard_attacks(static_cast<std::uint8_t>(origin), static_cast<std::uint8_t>(target)))
                wizard_moves_[origin].push_back(static_cast<std::uint8_t>(target));
        }
        if (origin >= Regular_Squares) continue;
        const int x = coordinates_[origin].x;
        const int y = coordinates_[origin].y;
        const int directions[4][2] = { {1,0}, {-1,0}, {0,1}, {0,-1} };
        for (int direction = 0; direction < 4; ++direction) {
            int tx = x + directions[direction][0];
            int ty = y + directions[direction][1];
            while (tx >= 0 && tx < 10 && ty >= 0 && ty < 10) {
                rook_rays_[origin][direction].push_back(
                    square_from_coordinate(coordinates_, tx, ty));
                tx += directions[direction][0];
                ty += directions[direction][1];
            }
        }
    }
}

bool Geometry::king_attacks(std::uint8_t first, std::uint8_t second) const {
    const int dx = std::abs(coordinates_[first].x - coordinates_[second].x);
    const int dy = std::abs(coordinates_[first].y - coordinates_[second].y);
    return std::max(dx, dy) == 1;
}

bool Geometry::champion_attacks(std::uint8_t first, std::uint8_t second) const {
    const int dx = coordinates_[second].x - coordinates_[first].x;
    const int dy = coordinates_[second].y - coordinates_[first].y;
    return (std::abs(dx) == 1 && dy == 0) || (std::abs(dy) == 1 && dx == 0) ||
           (std::abs(dx) == 2 && dy == 0) || (std::abs(dy) == 2 && dx == 0) ||
           (std::abs(dx) == 2 && std::abs(dy) == 2);
}

bool Geometry::knight_attacks(std::uint8_t first, std::uint8_t second) const {
    const int dx = std::abs(coordinates_[second].x - coordinates_[first].x);
    const int dy = std::abs(coordinates_[second].y - coordinates_[first].y);
    return (dx == 1 && dy == 2) || (dx == 2 && dy == 1);
}

bool Geometry::wizard_attacks(std::uint8_t first, std::uint8_t second) const {
    const int dx = std::abs(coordinates_[second].x - coordinates_[first].x);
    const int dy = std::abs(coordinates_[second].y - coordinates_[first].y);
    return (dx == 1 && dy == 1) || (dx == 1 && dy == 3) || (dx == 3 && dy == 1);
}

bool Geometry::rook_attacks(std::uint8_t first, std::uint8_t second,
                            const std::array<std::uint8_t, 2> & blockers) const {
    if (first >= Regular_Squares || second >= Regular_Squares) return false;
    const Coordinate a = coordinates_[first];
    const Coordinate b = coordinates_[second];
    if (a.x != b.x && a.y != b.y) return false;
    for (std::uint8_t blocker : blockers) {
        if (blocker >= Square_Count) continue;
        const Coordinate c = coordinates_[blocker];
        if (a.x == b.x && c.x == a.x && c.y > std::min(a.y, b.y) && c.y < std::max(a.y, b.y))
            return false;
        if (a.y == b.y && c.y == a.y && c.x > std::min(a.x, b.x) && c.x < std::max(a.x, b.x))
            return false;
    }
    return true;
}

std::uint32_t D4Indexer::ordered_size(int universe, int length) {
    std::uint32_t result = 1;
    for (int i = 0; i < length; ++i) result *= static_cast<std::uint32_t>(universe - i);
    return result;
}

std::uint32_t D4Indexer::rank_ordered(const std::uint8_t * items, int universe, int length) {
    std::uint8_t excluded[4]{};
    int excluded_count = 0;
    std::uint32_t rank = 0;
    int radix = universe;
    for (int i = 0; i < length; ++i) {
        int compressed = items[i];
        for (int j = 0; j < excluded_count; ++j) {
            if (excluded[j] == items[i]) throw std::invalid_argument("ordered items are not distinct");
            if (excluded[j] < items[i]) --compressed;
        }
        rank = rank * static_cast<std::uint32_t>(radix) + static_cast<std::uint32_t>(compressed);
        excluded[excluded_count++] = items[i];
        --radix;
    }
    return rank;
}

void D4Indexer::unrank_ordered(std::uint32_t rank, int universe, int length,
                               std::uint8_t * items) {
    const std::uint32_t size = ordered_size(universe, length);
    if (rank >= size) throw std::out_of_range("ordered rank is out of range");
    std::uint8_t compressed[4]{};
    for (int i = length - 1; i >= 0; --i) {
        const int radix = universe - i;
        compressed[i] = static_cast<std::uint8_t>(rank % radix);
        rank /= static_cast<std::uint32_t>(radix);
    }
    for (int i = 0; i < length; ++i) {
        int value = compressed[i];
        std::uint8_t selected[4]{};
        for (int j = 0; j < i; ++j) selected[j] = items[j];
        std::sort(selected, selected + i);
        for (int j = 0; j < i; ++j)
            if (value >= selected[j]) ++value;
        items[i] = static_cast<std::uint8_t>(value);
    }
}

std::uint8_t D4Indexer::reflect_local(std::uint8_t local) {
    if (local < 11) return local;
    if (local < 57) return static_cast<std::uint8_t>(local + 46);
    return static_cast<std::uint8_t>(local - 46);
}

D4Indexer::D4Indexer(const Geometry & geometry, int piece_count)
    : geometry_(geometry), piece_count_(piece_count), tail_length_(piece_count - 1) {
    if (piece_count != 3 && piece_count != 4)
        throw std::invalid_argument("D4 index supports three or four labelled pieces");
    generic_block_size_ = ordered_size(103, tail_length_);
    symmetric_block_size_ = (generic_block_size_ + ordered_size(11, tail_length_)) / 2;

    if (piece_count == 4) {
        symmetric_lookup_.assign(generic_block_size_, -1);
        std::uint8_t local[3]{};
        std::uint8_t reflected[3]{};
        for (std::uint32_t raw = 0; raw < generic_block_size_; ++raw) {
            unrank_ordered(raw, 103, tail_length_, local);
            for (int i = 0; i < tail_length_; ++i) reflected[i] = reflect_local(local[i]);
            if (raw <= rank_ordered(reflected, 103, tail_length_)) {
                symmetric_lookup_[raw] = static_cast<std::int32_t>(symmetric_ranks_.size());
                symmetric_ranks_.push_back(raw);
            }
        }
    } else {
        symmetric_lookup_.assign(generic_block_size_, -1);
        std::uint8_t local[2]{};
        std::uint8_t reflected[2]{};
        for (std::uint32_t raw = 0; raw < generic_block_size_; ++raw) {
            unrank_ordered(raw, 103, tail_length_, local);
            for (int i = 0; i < tail_length_; ++i) reflected[i] = reflect_local(local[i]);
            if (raw <= rank_ordered(reflected, 103, tail_length_)) {
                symmetric_lookup_[raw] = static_cast<std::int32_t>(symmetric_ranks_.size());
                symmetric_ranks_.push_back(raw);
            }
        }
    }
    if (symmetric_ranks_.size() != symmetric_block_size_)
        throw std::logic_error("C2 quotient has the wrong size");

    std::array<bool, Square_Count> unseen{};
    unseen.fill(true);
    std::vector<std::vector<std::uint8_t>> orbits;
    for (std::uint32_t seed = 0; seed < Square_Count; ++seed) {
        if (!unseen[seed]) continue;
        std::vector<std::uint8_t> orbit;
        for (int transform = 0; transform < 8; ++transform)
            orbit.push_back(geometry_.transforms()[transform][seed]);
        std::sort(orbit.begin(), orbit.end());
        orbit.erase(std::unique(orbit.begin(), orbit.end()), orbit.end());
        for (std::uint8_t member : orbit) unseen[member] = false;
        orbits.push_back(orbit);
    }
    std::sort(orbits.begin(), orbits.end(), [](const auto & first, const auto & second) {
        return first.front() < second.front();
    });
    if (orbits.size() != 16) throw std::logic_error("Omega board must have sixteen D4 square orbits");

    std::uint32_t offset = 0;
    for (const auto & orbit : orbits) {
        Block block;
        block.base_transform.fill(-1);
        block.representative = orbit.front();
        block.orbit = orbit;
        for (int transform = 0; transform < 8; ++transform)
            if (geometry_.transforms()[transform][block.representative] == block.representative)
                block.stabilizer.push_back(static_cast<std::uint8_t>(transform));
        if (block.stabilizer.size() != 1 && block.stabilizer.size() != 2)
            throw std::logic_error("unexpected first-square stabilizer");
        block.symmetric = block.stabilizer.size() == 2;

        for (std::uint8_t member : orbit) {
            for (int transform = 0; transform < 8; ++transform) {
                if (geometry_.transforms()[transform][member] == block.representative) {
                    block.base_transform[member] = static_cast<std::int8_t>(transform);
                    break;
                }
            }
        }

        std::vector<std::uint8_t> locals;
        if (!block.symmetric) {
            for (std::uint32_t sq = 0; sq < Square_Count; ++sq)
                if (sq != block.representative) locals.push_back(static_cast<std::uint8_t>(sq));
        } else {
            const std::uint8_t reflection = block.stabilizer[1];
            std::vector<std::uint8_t> fixed;
            std::vector<std::pair<std::uint8_t, std::uint8_t>> pairs;
            std::array<bool, Square_Count> seen{};
            seen[block.representative] = true;
            for (std::uint32_t sq = 0; sq < Square_Count; ++sq) {
                if (sq != block.representative && geometry_.transforms()[reflection][sq] == sq) {
                    fixed.push_back(static_cast<std::uint8_t>(sq));
                    seen[sq] = true;
                }
            }
            for (std::uint32_t sq = 0; sq < Square_Count; ++sq) {
                if (seen[sq]) continue;
                const std::uint8_t partner = geometry_.transforms()[reflection][sq];
                pairs.emplace_back(static_cast<std::uint8_t>(std::min<std::uint32_t>(sq, partner)),
                                   static_cast<std::uint8_t>(std::max<std::uint32_t>(sq, partner)));
                seen[sq] = true;
                seen[partner] = true;
            }
            std::sort(fixed.begin(), fixed.end());
            std::sort(pairs.begin(), pairs.end());
            if (fixed.size() != 11 || pairs.size() != 46)
                throw std::logic_error("unexpected reflection orbit structure");
            locals.insert(locals.end(), fixed.begin(), fixed.end());
            for (const auto & pair : pairs) locals.push_back(pair.first);
            for (const auto & pair : pairs) locals.push_back(pair.second);
        }
        if (locals.size() != 103) throw std::logic_error("local-square map has the wrong size");
        for (std::uint32_t local = 0; local < locals.size(); ++local) {
            block.from_local[local] = locals[local];
            block.to_local[locals[local]] = static_cast<std::uint8_t>(local);
        }
        block.offset = offset;
        block.size = block.symmetric ? symmetric_block_size_ : generic_block_size_;
        const std::uint8_t block_index = static_cast<std::uint8_t>(blocks_.size());
        for (std::uint8_t member : orbit) block_for_square_[member] = block_index;
        blocks_.push_back(block);
        offset += block.size;
    }
    state_count_ = offset * 2;
    raw_state_count_ = ordered_size(Square_Count, piece_count_) * 2;
}

std::uint32_t D4Indexer::rank(const std::uint8_t * squares, std::uint8_t turn) const {
    if (turn > 1 || !distinct(squares, piece_count_))
        throw std::invalid_argument("invalid labelled D4 state");
    const Block & block = blocks_[block_for_square_[squares[0]]];
    const int transform = block.base_transform[squares[0]];
    if (transform < 0) throw std::logic_error("missing first-square transform");
    std::uint8_t local[3]{};
    for (int i = 0; i < tail_length_; ++i) {
        const std::uint8_t transformed = geometry_.transforms()[transform][squares[i + 1]];
        local[i] = block.to_local[transformed];
    }
    std::uint32_t raw = rank_ordered(local, 103, tail_length_);
    std::uint32_t local_rank = raw;
    if (block.symmetric) {
        std::uint8_t reflected[3]{};
        for (int i = 0; i < tail_length_; ++i) reflected[i] = reflect_local(local[i]);
        raw = std::min(raw, rank_ordered(reflected, 103, tail_length_));
        const std::int32_t canonical = symmetric_lookup_[raw];
        if (canonical < 0) throw std::logic_error("canonical C2 tuple was not indexed");
        local_rank = static_cast<std::uint32_t>(canonical);
    }
    return (block.offset + local_rank) * 2 + turn;
}

void D4Indexer::unrank(std::uint32_t rank_value, std::uint8_t * squares,
                       std::uint8_t & turn) const {
    if (rank_value >= state_count_) throw std::out_of_range("dense D4 rank is out of range");
    turn = static_cast<std::uint8_t>(rank_value & 1U);
    const std::uint32_t spatial = rank_value / 2;
    const Block * block = nullptr;
    for (const Block & candidate : blocks_) {
        if (spatial >= candidate.offset && spatial < candidate.offset + candidate.size) {
            block = &candidate;
            break;
        }
    }
    if (block == nullptr) throw std::logic_error("D4 block lookup failed");
    const std::uint32_t local_rank = spatial - block->offset;
    const std::uint32_t raw = block->symmetric ? symmetric_ranks_[local_rank] : local_rank;
    std::uint8_t local[3]{};
    unrank_ordered(raw, 103, tail_length_, local);
    squares[0] = block->representative;
    for (int i = 0; i < tail_length_; ++i) squares[i + 1] = block->from_local[local[i]];
}

KrkTable::KrkTable(const Geometry & geometry, const D4Indexer & index)
    : geometry_(geometry), index_(index) {
    if (index_.piece_count() != 3 || index_.state_count() != Three_Man_State_Count)
        throw std::invalid_argument("KRK dependency requires the retained three-man index");
}

void KrkTable::load(const std::string & path) {
    std::string header;
    std::vector<std::uint8_t> payload = read_payload(path, header);
    if (json_string(header, "magic") != "OMTB3WDL" || json_unsigned(header, "version") != 1)
        throw std::runtime_error("unsupported OMTB3WDL dependency");
    if (json_string(header, "material") != "KRK")
        throw std::runtime_error("four-man generation requires the KRK dependency");
    if (json_string(header, "index") != Three_Man_Index ||
        json_unsigned(header, "state_count") != Three_Man_State_Count)
        throw std::runtime_error("KRK dependency index mismatch");
    const std::string expected_rules = sha256(std::string(Three_Man_Rules));
    if (json_string(header, "rules_sha256") != expected_rules)
        throw std::runtime_error("KRK dependency rules fingerprint mismatch");
    if (payload.size() != Three_Man_State_Count)
        throw std::runtime_error("KRK dependency payload size mismatch");
    const std::string actual_sha = sha256(payload.data(), payload.size());
    if (json_string(header, "payload_sha256") != actual_sha)
        throw std::runtime_error("KRK dependency payload checksum mismatch");
    for (std::uint8_t outcome : payload)
        if (outcome != Invalid && outcome != Loss && outcome != Draw && outcome != Win)
            throw std::runtime_error("KRK dependency contains an unsupported WDL code");
    payload_ = std::move(payload);
    payload_sha256_ = actual_sha;
}

bool KrkTable::is_legal(const State3 & state) const {
    const auto squares = state.squares();
    if (state.turn > 1 || !distinct(squares.data(), 3)) return false;
    if (geometry_.king_attacks(state.strong_king, state.weak_king)) return false;
    if (state.turn == Strong_To_Move) {
        return !geometry_.rook_attacks(state.piece, state.weak_king,
                                       { state.strong_king, state.strong_king });
    }
    return true;
}

std::uint8_t KrkTable::probe(const State3 & state) const {
    if (!loaded()) throw std::runtime_error("KRK dependency is not loaded");
    if (!is_legal(state)) throw std::logic_error("illegal KRK dependency probe");
    const auto squares = state.squares();
    const std::uint8_t outcome = payload_[index_.rank(squares.data(), state.turn)];
    if (outcome == Invalid || outcome == Unknown)
        throw std::logic_error("legal KRK dependency probe has no WDL result");
    return outcome;
}

std::string sha256(const std::uint8_t * data, std::size_t size) {
    Sha256 hash;
    hash.update(data, size);
    return digest_hex(hash.finish());
}

std::string sha256(const std::string & text) {
    return sha256(reinterpret_cast<const std::uint8_t *>(text.data()), text.size());
}

std::string json_string(const std::string & header, const std::string & key) {
    const std::string token = "\"" + key + "\"";
    std::size_t position = header.find(token);
    if (position == std::string::npos) throw std::runtime_error("missing JSON header key: " + key);
    position = header.find(':', position + token.size());
    if (position == std::string::npos) throw std::runtime_error("malformed JSON header key: " + key);
    position = header.find('"', position + 1);
    if (position == std::string::npos) throw std::runtime_error("JSON header value is not a string: " + key);
    const std::size_t end = header.find('"', position + 1);
    if (end == std::string::npos) throw std::runtime_error("unterminated JSON header string: " + key);
    return header.substr(position + 1, end - position - 1);
}

std::uint64_t json_unsigned(const std::string & header, const std::string & key) {
    const std::string token = "\"" + key + "\"";
    std::size_t position = header.find(token);
    if (position == std::string::npos) throw std::runtime_error("missing JSON header key: " + key);
    position = header.find(':', position + token.size());
    if (position == std::string::npos) throw std::runtime_error("malformed JSON header key: " + key);
    ++position;
    while (position < header.size() && (header[position] == ' ' || header[position] == '\t')) ++position;
    std::size_t end = position;
    while (end < header.size() && header[end] >= '0' && header[end] <= '9') ++end;
    if (end == position) throw std::runtime_error("JSON header value is not unsigned: " + key);
    return std::stoull(header.substr(position, end - position));
}

const char * four_man_material_name(FourManMaterial material) {
    switch (material) {
        case FourManMaterial::Krkc: return "KRKC";
        case FourManMaterial::Krkn: return "KRKN";
        case FourManMaterial::Kwkn: return "KWKN";
        case FourManMaterial::Kckw: return "KCKW";
    }
    throw std::logic_error("unknown four-man material name");
}

FourManMaterial parse_four_man_material(const std::string & name) {
    if (name == "krkc" || name == "KRKC") return FourManMaterial::Krkc;
    if (name == "krkn" || name == "KRKN") return FourManMaterial::Krkn;
    if (name == "kwkn" || name == "KWKN") return FourManMaterial::Kwkn;
    if (name == "kckw" || name == "KCKW") return FourManMaterial::Kckw;
    throw std::invalid_argument("four-man material must be krkc, krkn, kwkn, or kckw");
}

void write_four_man_file(const std::string & path, const std::vector<std::uint8_t> & payload,
                         std::uint64_t legal_count, bool complete,
                         const std::string & dependency_sha256, FourManMaterial material) {
    std::uint64_t invalid = 0, loss = 0, draw = 0, win = 0;
    for (std::uint8_t outcome : payload) {
        if (outcome == Invalid) ++invalid;
        else if (outcome == Loss) ++loss;
        else if (outcome == Draw) ++draw;
        else if (outcome == Win) ++win;
        else throw std::runtime_error("four-man payload contains an unresolved WDL code");
    }
    if (legal_count != loss + draw + win)
        throw std::runtime_error("four-man legal count does not match payload outcomes");
    if (complete && payload.size() != Four_Man_State_Count)
        throw std::runtime_error("complete four-man payload has the wrong size");

    const std::string payload_sha = sha256(payload.data(), payload.size());
    const std::string rules = four_man_rules(material);
    const std::string rules_sha = sha256(rules);
    const std::string minor = minor_name(material);
    const bool capture_boundary = uses_capture_policy(material);
    std::ostringstream header;
    header << "{\"magic\":\"OMTB4WDL\",\"version\":1,\"material\":\""
           << four_man_material_name(material) << "\""
           << ",\"labelled_order\":[\"" << primary_king_name(material)
           << "\",\"" << primary_name(material) << "\",\"" << minor
           << "_king\",\"" << minor << "\",\"turn\"]"
           << ",\"index\":\"D4-first-piece-v1\",\"square_count\":104"
           << ",\"dense_state_count\":" << Four_Man_State_Count
           << ",\"state_count\":" << payload.size()
           << ",\"complete\":" << (complete ? "true" : "false")
           << ",\"boundary\":\"" << (complete ? "full" : "outside-draw-test") << "\""
           << ",\"legal_count\":" << legal_count
           << ",\"rules\":\"" << rules << "\""
           << ",\"rules_sha256\":\"" << rules_sha << "\""
           << (capture_boundary ? ",\"capture_policy_sha256\":\""
                    : ",\"krk_payload_sha256\":\"")
           << dependency_sha256 << "\""
           << ",\"codes\":{\"invalid\":0,\"loss\":2,\"draw\":3,\"win\":4}"
           << ",\"payload_sha256\":\"" << payload_sha << "\""
           << ",\"invalid_count\":" << invalid << ",\"loss_count\":" << loss
           << ",\"draw_count\":" << draw << ",\"win_count\":" << win
           << ",\"counts\":{\"invalid\":" << invalid << ",\"loss\":" << loss
           << ",\"draw\":" << draw << ",\"win\":" << win << "}}";

    const std::string temporary = path + ".tmp";
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        if (!stream) throw std::runtime_error("cannot create four-man table: " + temporary);
        stream << header.str() << '\n';
        stream.write(reinterpret_cast<const char *>(payload.data()),
                     static_cast<std::streamsize>(payload.size()));
        if (!stream) throw std::runtime_error("failed to write four-man table payload");
    }
    inspect_four_man_file(temporary);
    std::remove(path.c_str());
    if (std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(temporary.c_str());
        throw std::runtime_error("failed to atomically replace four-man table");
    }
}

FourManTable read_four_man_file(const std::string & path) {
    std::string header;
    std::vector<std::uint8_t> payload = read_payload(path, header);
    if (json_string(header, "magic") != "OMTB4WDL" || json_unsigned(header, "version") != 1)
        throw std::runtime_error("unsupported OMTB4WDL file");
    const FourManMaterial material = parse_four_man_material(json_string(header, "material"));
    if (json_string(header, "index") != "D4-first-piece-v1")
        throw std::runtime_error("four-man table metadata mismatch");
    const std::string minor = minor_name(material);
    const bool capture_boundary = uses_capture_policy(material);
    const std::string labelled_order = "\"labelled_order\":[\"" +
        std::string(primary_king_name(material)) + "\",\"" +
        std::string(primary_name(material)) + "\",\"" + minor +
        "_king\",\"" + minor + "\",\"turn\"]";
    if (header.find(labelled_order) == std::string::npos)
        throw std::runtime_error("four-man labelled-piece order mismatch");
    if (json_unsigned(header, "dense_state_count") != Four_Man_State_Count ||
        json_unsigned(header, "state_count") != payload.size())
        throw std::runtime_error("four-man table state count mismatch");
    const std::string rules = four_man_rules(material);
    if (json_string(header, "rules") != rules ||
        json_string(header, "rules_sha256") != sha256(rules))
        throw std::runtime_error("four-man rules fingerprint mismatch");
    const std::string actual_sha = sha256(payload.data(), payload.size());
    if (json_string(header, "payload_sha256") != actual_sha)
        throw std::runtime_error("four-man payload checksum mismatch");
    std::uint64_t invalid = 0, loss = 0, draw = 0, win = 0;
    for (std::uint8_t outcome : payload) {
        if (outcome != Invalid && outcome != Loss && outcome != Draw && outcome != Win)
            throw std::runtime_error("four-man payload contains an unsupported WDL code");
        if (outcome == Invalid) ++invalid;
        else if (outcome == Loss) ++loss;
        else if (outcome == Draw) ++draw;
        else if (outcome == Win) ++win;
    }
    const std::uint64_t legal = loss + draw + win;
    if (json_unsigned(header, "legal_count") != legal)
        throw std::runtime_error("four-man legal count mismatch");
    if (json_unsigned(header, "invalid_count") != invalid ||
        json_unsigned(header, "loss_count") != loss ||
        json_unsigned(header, "draw_count") != draw ||
        json_unsigned(header, "win_count") != win)
        throw std::runtime_error("four-man outcome counts mismatch");
    const std::string dependency_key = capture_boundary
        ? "capture_policy_sha256" : "krk_payload_sha256";
    const std::string dependency_sha = json_string(header, dependency_key);
    if (dependency_sha.size() != 64)
        throw std::runtime_error("four-man dependency checksum is missing");
    if (capture_boundary && dependency_sha != sha256(std::string(capture_policy(material))))
        throw std::runtime_error(std::string(four_man_material_name(material)) +
                                 " capture-policy checksum mismatch");
    return FourManTable { material, std::move(payload), dependency_sha };
}

void inspect_four_man_file(const std::string & path) {
    const FourManTable table = read_four_man_file(path);
    const std::vector<std::uint8_t> & payload = table.payload;
    std::uint64_t legal = 0;
    for (std::uint8_t outcome : payload)
        if (outcome == Loss || outcome == Draw || outcome == Win) ++legal;
    std::cout << "OMTB4WDL " << four_man_material_name(table.material)
              << " verified: states=" << payload.size()
              << " legal=" << legal << " sha256="
              << sha256(payload.data(), payload.size()) << '\n';
}

} // namespace omega_tb4
