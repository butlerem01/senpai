#include "omega_nnue.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <limits>
#include <memory>
#include <string>
#include <vector>

#include "bit.hpp"
#include "common.hpp"
#include "omega_eval.hpp"
#include "pos.hpp"

namespace omega_nnue {

namespace {

const unsigned char Magic[8] {
   'O', 'M', 'N', 'N', 'U', 'E', '1', 0,
};

const std::uint64_t FNV_Offset { 14695981039346656037ULL };
const std::uint64_t FNV_Prime  { 1099511628211ULL };
std::atomic<std::uint64_t> Next_Network_Generation { 1ULL };

class Byte_Reader {
public:
   Byte_Reader(const std::vector<unsigned char> & bytes, std::size_t offset)
      : p_bytes(bytes), p_offset(offset) {
   }

   bool read_u8(std::uint8_t & value) {
      if (remaining() < 1) return false;
      value = p_bytes[p_offset++];
      return true;
   }

   bool read_i8(std::int8_t & value) {
      std::uint8_t raw = 0;
      if (!read_u8(raw)) return false;
      const int decoded = raw < 0x80U ? int(raw) : int(raw) - 0x100;
      value = static_cast<std::int8_t>(decoded);
      return true;
   }

   bool read_u16(std::uint16_t & value) {
      if (remaining() < 2) return false;
      value = std::uint16_t(p_bytes[p_offset])
            | (std::uint16_t(p_bytes[p_offset + 1]) << 8);
      p_offset += 2;
      return true;
   }

   bool read_i16(std::int16_t & value) {
      std::uint16_t raw = 0;
      if (!read_u16(raw)) return false;
      const std::int32_t decoded = raw < 0x8000U
                                 ? std::int32_t(raw)
                                 : std::int32_t(raw) - 0x10000L;
      value = static_cast<std::int16_t>(decoded);
      return true;
   }

   bool read_u32(std::uint32_t & value) {
      if (remaining() < 4) return false;
      value = std::uint32_t(p_bytes[p_offset])
            | (std::uint32_t(p_bytes[p_offset + 1]) << 8)
            | (std::uint32_t(p_bytes[p_offset + 2]) << 16)
            | (std::uint32_t(p_bytes[p_offset + 3]) << 24);
      p_offset += 4;
      return true;
   }

   bool read_i32(std::int32_t & value) {
      std::uint32_t raw = 0;
      if (!read_u32(raw)) return false;
      const std::int64_t decoded = raw <= 0x7FFFFFFFU
                                 ? std::int64_t(raw)
                                 : std::int64_t(raw) - 0x100000000LL;
      value = static_cast<std::int32_t>(decoded);
      return true;
   }

   bool read_u64(std::uint64_t & value) {
      if (remaining() < 8) return false;
      value = 0;
      for (int byte = 0; byte < 8; ++byte) {
         value |= std::uint64_t(p_bytes[p_offset + byte]) << (byte * 8);
      }
      p_offset += 8;
      return true;
   }

   std::size_t offset() const {
      return p_offset;
   }

   std::size_t remaining() const {
      return p_offset <= p_bytes.size() ? p_bytes.size() - p_offset : 0;
   }

private:
   const std::vector<unsigned char> & p_bytes;
   std::size_t p_offset;
};

std::uint64_t fnv1a(
   const std::vector<unsigned char> & bytes,
   std::size_t begin
) {
   std::uint64_t hash = FNV_Offset;
   for (std::size_t i = begin; i < bytes.size(); ++i) {
      hash ^= std::uint64_t(bytes[i]);
      hash *= FNV_Prime;
   }
   return hash;
}

Configure_Result failure(
   const std::string & detail,
   bool retained_previous
) {
   Configure_Result result;
   result.retained_previous = retained_previous;
   result.message = "Omega NNUE load failed: " + detail;
   if (retained_previous) result.message += "; previous network retained";
   return result;
}

int clipped_activation(std::int64_t value) {
   if (value <= 0) return 0;
   if (value >= format::Activation_Max) return int(format::Activation_Max);
   return int(value);
}

std::int64_t divide_round(std::int64_t value, std::int64_t divisor) {
   if (value >= 0) return (value + divisor / 2) / divisor;
   return -((-value + divisor / 2) / divisor);
}

using Leaper_Distance_Row = std::array<
   std::uint8_t, format::Square_Count
>;
using Leaper_Distance_Table = std::array<
   Leaper_Distance_Row, format::Square_Count
>;

const int Champion_Deltas[12][2] {
   {+1, 0}, {-1, 0}, {0, +1}, {0, -1},
   {+2, 0}, {-2, 0}, {0, +2}, {0, -2},
   {+2, +2}, {+2, -2}, {-2, +2}, {-2, -2},
};

const int Wizard_Deltas[12][2] {
   {+1, +1}, {+1, -1}, {-1, +1}, {-1, -1},
   {+1, +3}, {+1, -3}, {-1, +3}, {-1, -3},
   {+3, +1}, {+3, -1}, {-3, +1}, {-3, -1},
};

void build_leaper_distances(
   Leaper_Distance_Table & table,
   const int deltas[12][2]
) {
   const std::uint8_t unreachable = 0xFFU;
   for (std::size_t source = 0; source < table.size(); ++source) {
      table[source].fill(unreachable);
      std::array<Square, format::Square_Count> queue;
      std::size_t head = 0;
      std::size_t tail = 0;
      table[source][source] = 0U;
      queue[tail++] = Square(source);

      while (head < tail) {
         const Square from = queue[head++];
         const std::uint8_t next = static_cast<std::uint8_t>(
            table[source][std::size_t(from)] + 1U
         );
         const int file = int(square_file(from));
         const int rank = int(square_rank(from));

         for (int index = 0; index < 12; ++index) {
            const Square to = square_from_coordinates(
               file + deltas[index][0], rank + deltas[index][1]
            );
            if (to == Square_None) continue;
            std::uint8_t & distance =
               table[source][std::size_t(to)];
            if (distance != unreachable) continue;
            distance = next;
            queue[tail++] = to;
         }
      }
      assert(tail <= queue.size());
   }
}

struct Empty_Board_Leaper_Distances {
   Leaper_Distance_Table champion;
   Leaper_Distance_Table wizard;

   Empty_Board_Leaper_Distances() : champion(), wizard() {
      // Architecture 4 is Omega-only.  Building after bit/common variant
      // initialization also pins the four detached corner coordinates.
      assert(variant_is_omega());
      build_leaper_distances(champion, Champion_Deltas);
      build_leaper_distances(wizard, Wizard_Deltas);
   }
};

const Empty_Board_Leaper_Distances & empty_board_leaper_distances() {
   static const Empty_Board_Leaper_Distances value;
   return value;
}

} // namespace

struct Runtime_Network::Network_Data {
   std::string path;
   std::uint64_t generation;
   std::uint32_t architecture;
   std::uint32_t feature_count;
   bool residual_correction;
   std::array<std::int16_t, format::Accumulator_Size> ft_bias;
   std::vector<std::int16_t> ft_weights;
   std::array<std::int32_t, format::Hidden_Size> hidden_bias;
   std::vector<std::int8_t> hidden_weights;
   std::int32_t output_bias;
   std::array<std::int8_t, format::Hidden_Size> output_weights;

   Network_Data()
      : path(),
        generation(0),
        architecture(format::Architecture_Absolute),
        feature_count(format::Feature_Count),
        residual_correction(false),
        ft_bias(),
        ft_weights(),
        hidden_bias(),
        hidden_weights(),
        output_bias(0),
        output_weights() {
   }
};

Runtime_Network G_Network;

namespace format {

int orient_square(Square sq, Side perspective) {
   const int native = int(sq);

   assert(native >= 0 && native < int(Square_Count));
   assert(perspective == White || perspective == Black);

   if (native < 0 || native >= int(Square_Count)) return -1;
   if (perspective != White && perspective != Black) return -1;
   if (perspective == White) return native;

   if (native < int(Square_Count - Corner_Size)) {
      const int file = native / Rank_Capacity;
      const int rank = native % Rank_Capacity;
      return file * Rank_Capacity + (Rank_Capacity - 1 - rank);
   }

   const int corner = native - int(Square_Count - Corner_Size);
   return int(Square_Count - Corner_Size) + (Corner_Size - 1 - corner);
}

int piece_feature(
   Piece pc,
   Side piece_side,
   Square sq,
   Side perspective
) {
   const int oriented = orient_square(sq, perspective);

   assert(pc >= Pawn && pc <= Wizard);
   assert(piece_side == White || piece_side == Black);
   assert(oriented >= 0 && oriented < int(Square_Count));

   if (pc < Pawn || pc > Wizard) return -1;
   if (piece_side != White && piece_side != Black) return -1;
   if (oriented < 0 || oriented >= int(Square_Count)) return -1;

   const int relation = piece_side == perspective ? 0 : 1;
   return ((relation * int(Piece_Count) + int(pc)) * int(Square_Count))
        + oriented;
}

int king_bucket(Square king, Side perspective) {
   const int oriented = orient_square(king, perspective);
   if (oriented < 0 || oriented >= int(Square_Count)) return -1;
   if (oriented < int(Square_Count - Corner_Size)) {
      const int file = oriented / Rank_Capacity;
      const int rank = oriented % Rank_Capacity;
      return (file / 2) * 5 + rank / 2;
   }
   return 25 + oriented - int(Square_Count - Corner_Size);
}

int king_state_piece_feature(
   Piece pc,
   Side piece_side,
   Square sq,
   Side perspective,
   int bucket
) {
   assert(bucket >= 0 && bucket < int(King_Bucket_Count));
   if (bucket < 0 || bucket >= int(King_Bucket_Count)) return -1;
   const int base = piece_feature(pc, piece_side, sq, perspective);
   if (base < 0) return -1;
   return bucket * int(Occupancy_Features) + base;
}

int halfmove_clock_bin(int halfmove_clock) {
   assert(halfmove_clock >= 0);
   if (halfmove_clock < 0) return -1;
   const int boundary[] { 1, 4, 16, 32, 50, 75, 90 };
   int bin = 0;
   for (int value : boundary) {
      if (halfmove_clock >= value) ++bin;
   }
   return bin;
}

int material_phase_bin(const Pos & pos) {
   int remaining = 0;
   for (int s = 0; s < Side_Size; ++s) {
      const Side side = side_make(s);
      remaining += pos.count(Knight, side);
      remaining += pos.count(Bishop, side);
      remaining += pos.count(Champion, side);
      remaining += pos.count(Wizard, side);
      remaining += pos.count(Rook, side) * 2;
      remaining += pos.count(Queen, side) * 4;
   }
   remaining = std::min(remaining, 32);
   if (remaining >= 24) return 0;
   if (remaining >= 16) return 1;
   if (remaining >= 8) return 2;
   return 3;
}

int empty_board_leaper_distance(Piece pc, Square from, Square to) {
   assert(pc == Champion || pc == Wizard);
   assert(int(from) >= 0 && int(from) < int(Square_Count));
   assert(int(to) >= 0 && int(to) < int(Square_Count));
   if ((pc != Champion && pc != Wizard)
    || int(from) < 0 || int(from) >= int(Square_Count)
    || int(to) < 0 || int(to) >= int(Square_Count)) {
      return int(Square_Count) + 1;
   }

   const Empty_Board_Leaper_Distances & distances =
      empty_board_leaper_distances();
   const std::uint8_t value = pc == Champion
      ? distances.champion[std::size_t(from)][std::size_t(to)]
      : distances.wizard[std::size_t(from)][std::size_t(to)];
   return value == 0xFFU ? int(Square_Count) + 1 : int(value);
}

namespace {

int leaper_count_group(Piece pc) {
   assert(pc == Champion || pc == Wizard);
   return pc == Champion ? 0 : 1;
}

int clipped_leaper_count(const Pos & pos, Piece pc, Side side) {
   return std::min(pos.count(pc, side), 2);
}

int distance_feature_bin(int distance, bool present) {
   if (!present) return 0;
   if (distance <= 1) return 1;
   if (distance == 2) return 2;
   return 3;
}

int minimum_king_distance(
   const Pos & pos,
   Piece pc,
   Side attacker,
   Square king
) {
   int best = int(Square_Count) + 1;
   for (Bit pieces = pos.pieces(pc, attacker);
        pieces != 0;
        pieces = bit::rest(pieces)) {
      best = std::min(
         best,
         empty_board_leaper_distance(pc, bit::first(pieces), king)
      );
   }
   return best;
}

bool wizard_is_activated(Square square, Side side) {
   if (!square_is_corner(square)) return true;
   const Corner corner = square_corner(square);
   return side == White
      ? corner != Corner_SW && corner != Corner_SE
      : corner != Corner_NE && corner != Corner_NW;
}

int activated_wizard_count(const Pos & pos, Side side) {
   int count = 0;
   for (Bit pieces = pos.pieces(Wizard, side);
        pieces != 0;
        pieces = bit::rest(pieces)) {
      if (wizard_is_activated(bit::first(pieces), side)) ++count;
   }
   return std::min(count, 2);
}

int champion_pair_distance_bin(const Pos & pos, Side side) {
   const Bit champions = pos.pieces(Champion, side);
   if (bit::count(champions) < 2) return 0;

   int best = int(Square_Count) + 1;
   for (Bit firsts = champions;
        firsts != 0;
        firsts = bit::rest(firsts)) {
      const Square first = bit::first(firsts);
      for (Bit seconds = bit::rest(firsts);
           seconds != 0;
           seconds = bit::rest(seconds)) {
         best = std::min(
            best,
            empty_board_leaper_distance(
               Champion, first, bit::first(seconds)
            )
         );
      }
   }
   if (best <= 1) return 1;
   if (best == 2) return 2;
   return 3;
}

void append_omega_interaction_features(
   const Pos & pos,
   Side perspective,
   std::vector<int> & output
) {
   const std::size_t begin = output.size();

   for (int relation = 0; relation < Side_Size; ++relation) {
      const Side side = relation == 0 ? perspective : side_opp(perspective);
      const int undeveloped = omega_eval::undeveloped_units(pos, side);
      assert(undeveloped >= 0 && undeveloped <= 8);
      output.push_back(
         int(Omega_Interaction_Development_Feature_Base)
         + relation * 9 + undeveloped
      );
   }

   for (int relation = 0; relation < Side_Size; ++relation) {
      const Side side = relation == 0 ? perspective : side_opp(perspective);
      for (Piece pc : { Champion, Wizard }) {
         const int group = relation * 2 + leaper_count_group(pc);
         output.push_back(
            int(Omega_Interaction_Leaper_Count_Feature_Base)
            + group * 3 + clipped_leaper_count(pos, pc, side)
         );
      }
   }

   for (int relation = 0; relation < Side_Size; ++relation) {
      const Side side = relation == 0 ? perspective : side_opp(perspective);
      const bool coexist = pos.count(Champion, side) != 0
                        && pos.count(Wizard, side) != 0;
      output.push_back(
         int(Omega_Interaction_Coexistence_Feature_Base)
         + relation * 2 + (coexist ? 1 : 0)
      );
   }

   for (int relation = 0; relation < Side_Size; ++relation) {
      const Side side = relation == 0 ? perspective : side_opp(perspective);
      const Side defender = side_opp(side);
      const Bit kings = pos.pieces(King, defender);
      assert(bit::count(kings) == 1);
      if (bit::count(kings) != 1) continue;
      const Square king = bit::first(kings);
      for (Piece pc : { Champion, Wizard }) {
         const Bit leapers = pos.pieces(pc, side);
         const int bin = distance_feature_bin(
            minimum_king_distance(pos, pc, side, king), leapers != 0
         );
         const int group = relation * 2 + leaper_count_group(pc);
         output.push_back(
            int(Omega_Interaction_King_Distance_Feature_Base)
            + group * 4 + bin
         );
      }
   }

   for (int relation = 0; relation < Side_Size; ++relation) {
      const Side side = relation == 0 ? perspective : side_opp(perspective);
      output.push_back(
         int(Omega_Interaction_Activated_Wizard_Feature_Base)
         + relation * 3 + activated_wizard_count(pos, side)
      );
   }

   for (int relation = 0; relation < Side_Size; ++relation) {
      const Side side = relation == 0 ? perspective : side_opp(perspective);
      output.push_back(
         int(Omega_Interaction_Champion_Pair_Feature_Base)
         + relation * 4 + champion_pair_distance_bin(pos, side)
      );
   }

   // Sixteen disjoint categorical groups, one active row from each.
   assert(output.size() == begin + 16U);
}

} // namespace

int castling_feature(bool own, bool right_of_king) {
   const int relation = own ? 0 : 1;
   const int flank = right_of_king ? 1 : 0;
   return int(Occupancy_Features) + relation * 2 + flank;
}

namespace {

void append_non_piece_features(
   const Pos & pos,
   Side perspective,
   std::vector<int> & output,
   std::uint32_t architecture
) {
   const bool king_state =
      architecture == Architecture_King_State_Residual
      || architecture == Architecture_Omega_Interaction_Residual;

   for (int s = 0; s < Side_Size; ++s) {
      const Side castling_side = side_make(s);
      const Bit king_bits = pos.pieces(King, castling_side);
      if (king_bits == 0) continue;

      const Square king = bit::first(king_bits);
      const int king_file = int(square_file(king));
      bool flank_seen[2] { false, false };

      // OFEN can carry a stale K/Q flag even when its home square no longer
      // contains a Rook.  Such metadata must not become an NNUE feature.
      for (Bit rooks = pos.castling_rooks(castling_side)
                     & pos.pieces(Rook, castling_side);
           rooks != 0;
           rooks = bit::rest(rooks)) {
         const Square rook = bit::first(rooks);
         const int rook_file = int(square_file(rook));
         if (rook_file == king_file) continue;
         flank_seen[rook_file > king_file ? 1 : 0] = true;
      }

      for (int flank = 0; flank < 2; ++flank) {
         if (flank_seen[flank]) {
            const int relation = castling_side == perspective ? 0 : 1;
            const int relative = relation * 2 + flank;
            output.push_back(
               king_state
               ? int(King_State_Castling_Feature_Base) + relative
               : int(Occupancy_Features) + relative
            );
         }
      }
   }

   if (king_state) {
      for (Bit ep = pos.ep_squares(); ep != 0; ep = bit::rest(ep)) {
         const Square square = bit::first(ep);
         const int oriented = orient_square(square, perspective);
         assert(oriented >= 0
             && oriented < int(Square_Count - Corner_Size));
         if (oriented >= 0
          && oriented < int(Square_Count - Corner_Size)) {
            output.push_back(int(King_State_EP_Feature_Base) + oriented);
         }
      }
      output.push_back(
         int(King_State_Halfmove_Feature_Base)
         + halfmove_clock_bin(pos.halfmove_clock())
      );
      output.push_back(
         int(King_State_Phase_Feature_Base) + material_phase_bin(pos)
      );
   }

   if (architecture == Architecture_Omega_Interaction_Residual) {
      append_omega_interaction_features(pos, perspective, output);
   }
}

void active_non_piece_features(
   const Pos & pos,
   Side perspective,
   std::vector<int> & output,
   std::uint32_t architecture
) {
   output.clear();
   output.reserve(24U);
   append_non_piece_features(pos, perspective, output, architecture);
}

} // namespace

void active_features(
   const Pos & pos,
   Side perspective,
   std::vector<int> & output,
   std::uint32_t architecture
) {
   output.clear();
   output.reserve(68);

   assert(perspective == White || perspective == Black);
   if (perspective != White && perspective != Black) return;
   assert(architecture == Architecture_Absolute
       || architecture == Architecture_Residual
       || architecture == Architecture_King_State_Residual
       || architecture == Architecture_Omega_Interaction_Residual);
   if (architecture != Architecture_Absolute
    && architecture != Architecture_Residual
    && architecture != Architecture_King_State_Residual
    && architecture != Architecture_Omega_Interaction_Residual) return;

   const bool king_state =
      architecture == Architecture_King_State_Residual
      || architecture == Architecture_Omega_Interaction_Residual;

   int bucket = -1;
   if (king_state) {
      const Bit kings = pos.pieces(King, perspective);
      assert(bit::count(kings) == 1);
      if (bit::count(kings) != 1) return;
      bucket = king_bucket(bit::first(kings), perspective);
      assert(bucket >= 0 && bucket < int(King_Bucket_Count));
   }

   for (int s = 0; s < Side_Size; ++s) {
      const Side piece_side = side_make(s);

      for (int p = 0; p < Piece_Size; ++p) {
         const Piece pc = piece_make(p);

         for (Bit pieces = pos.pieces(pc, piece_side);
              pieces != 0;
              pieces = bit::rest(pieces)) {
            const int feature =
               king_state
               ? king_state_piece_feature(
                    pc, piece_side, bit::first(pieces), perspective, bucket
                 )
               : piece_feature(
                    pc, piece_side, bit::first(pieces), perspective
                 );
            const int occupancy_limit =
               king_state
               ? int(King_State_Occupancy_Features)
               : int(Occupancy_Features);
            assert(feature >= 0 && feature < occupancy_limit);
            if (feature >= 0) output.push_back(feature);
         }
      }
   }

   append_non_piece_features(pos, perspective, output, architecture);
}

} // namespace format

Evaluation_Trace::Evaluation_Trace()
   : cache_hit(false),
     incremental_parent(false),
     full_refresh(false),
     oracle_checked(false),
     oracle_match(false),
     perspective_rebuilt { false, false },
     piece_rows_removed { 0U, 0U },
     piece_rows_added { 0U, 0U },
     categorical_rows_removed { 0U, 0U },
     categorical_rows_added { 0U, 0U } {
}

namespace {

using Accumulator = std::array<
   std::array<std::int32_t, format::Accumulator_Size>, Side_Size
>;

// Architecture 4 has at most four castling rows, two en-passant rows, one
// clock row, one phase row, and sixteen Omega interaction rows.  A little
// spare room makes a contract violation a cache miss rather than an overrun.
const std::size_t Cached_Categorical_Capacity { 32U };

struct Cached_Features {
   std::array<int, Cached_Categorical_Capacity> rows;
   std::size_t size;

   Cached_Features() : rows(), size(0U) {
   }

   bool assign(const std::vector<int> & source) {
      if (source.size() > rows.size()) {
         size = 0U;
         return false;
      }
      size = source.size();
      std::copy(source.begin(), source.end(), rows.begin());
      return true;
   }

   bool has(int feature) const {
      return std::find(rows.begin(), rows.begin() + size, feature)
          != rows.begin() + size;
   }
};

struct Accumulator_Cache_Entry {
   bool valid;
   const Pos * identity;
   std::uint64_t network_generation;
   Pos snapshot;
   Accumulator accumulator;
   Cached_Features categorical[Side_Size];
   int king_bucket[Side_Size];

   Accumulator_Cache_Entry()
      : valid(false),
        identity(nullptr),
        network_generation(0ULL),
        snapshot(),
        accumulator(),
        categorical(),
        king_bucket { -1, -1 } {
   }
};

const std::size_t Accumulator_Cache_Size { 128U };

struct Accumulator_Cache {
   std::array<Accumulator_Cache_Entry, Accumulator_Cache_Size> entry;
};

Accumulator_Cache & accumulator_cache() {
   static thread_local Accumulator_Cache cache;
   return cache;
}

std::size_t accumulator_cache_index(
   const Pos * identity,
   std::uint64_t generation
) {
   const std::uintptr_t address =
      reinterpret_cast<std::uintptr_t>(identity);
   const std::uint64_t mixed =
      std::uint64_t(address >> 4)
      ^ (generation * 0x9E3779B97F4A7C15ULL);
   return std::size_t(mixed & (Accumulator_Cache_Size - 1U));
}

Accumulator_Cache_Entry * find_cache_entry(
   const Pos * identity,
   std::uint64_t generation
) {
   Accumulator_Cache_Entry & entry = accumulator_cache().entry[
      accumulator_cache_index(identity, generation)
   ];
   if (!entry.valid
    || entry.identity != identity
    || entry.network_generation != generation) {
      return nullptr;
   }
   return &entry;
}

bool same_nnue_state(const Pos & left, const Pos & right) {
   if (left.key() != right.key()
    || left.turn() != right.turn()
    || left.halfmove_clock() != right.halfmove_clock()
    || left.ep_squares() != right.ep_squares()) {
      return false;
   }
   for (int s = 0; s < Side_Size; ++s) {
      const Side side = side_make(s);
      if (left.castling_rooks(side) != right.castling_rooks(side)) {
         return false;
      }
      for (int p = 0; p < Piece_Size; ++p) {
         const Piece piece = piece_make(p);
         if (left.pieces(piece, side) != right.pieces(piece, side)) {
            return false;
         }
      }
   }
   return true;
}

bool is_king_state_architecture(std::uint32_t architecture) {
   return architecture == format::Architecture_King_State_Residual
       || architecture == format::Architecture_Omega_Interaction_Residual;
}

int perspective_king_bucket(
   const Pos & pos,
   Side perspective,
   std::uint32_t architecture
) {
   if (!is_king_state_architecture(architecture)) return -1;
   const Bit kings = pos.pieces(King, perspective);
   assert(bit::count(kings) == 1);
   if (bit::count(kings) != 1) return -1;
   return format::king_bucket(bit::first(kings), perspective);
}

int occupancy_feature(
   Piece piece,
   Side piece_side,
   Square square,
   Side perspective,
   std::uint32_t architecture,
   int bucket
) {
   return is_king_state_architecture(architecture)
      ? format::king_state_piece_feature(
           piece, piece_side, square, perspective, bucket
        )
      : format::piece_feature(piece, piece_side, square, perspective);
}

void apply_feature_row(
   std::array<std::int32_t, format::Accumulator_Size> & accumulator,
   const std::vector<std::int16_t> & weights,
   std::uint32_t feature_count,
   int feature,
   int sign
) {
   assert(feature >= 0 && feature < int(feature_count));
   assert(sign == -1 || sign == +1);
   if (feature < 0 || feature >= int(feature_count)) return;
   const std::size_t offset =
      std::size_t(feature) * format::Accumulator_Size;
   for (std::size_t lane = 0; lane < format::Accumulator_Size; ++lane) {
      accumulator[lane] +=
         std::int32_t(sign) * std::int32_t(weights[offset + lane]);
   }
}

bool build_full_accumulator(
   const Pos & pos,
   std::uint32_t architecture,
   std::uint32_t feature_count,
   const std::array<std::int16_t, format::Accumulator_Size> & bias,
   const std::vector<std::int16_t> & weights,
   Accumulator & result,
   Cached_Features categorical[Side_Size],
   int king_bucket[Side_Size]
) {
   static thread_local std::vector<int> features;
   if (features.capacity() < Cached_Categorical_Capacity) {
      features.reserve(Cached_Categorical_Capacity);
   }

   for (int view = 0; view < Side_Size; ++view) {
      const Side perspective = side_make(view);
      for (std::size_t lane = 0;
           lane < format::Accumulator_Size;
           ++lane) {
         result[view][lane] = bias[lane];
      }
      king_bucket[view] = perspective_king_bucket(
         pos, perspective, architecture
      );
      if (is_king_state_architecture(architecture)
       && king_bucket[view] < 0) {
         return false;
      }

      for (int s = 0; s < Side_Size; ++s) {
         const Side piece_side = side_make(s);
         for (int p = 0; p < Piece_Size; ++p) {
            const Piece piece = piece_make(p);
            for (Bit pieces = pos.pieces(piece, piece_side);
                 pieces != 0;
                 pieces = bit::rest(pieces)) {
               apply_feature_row(
                  result[view], weights, feature_count,
                  occupancy_feature(
                     piece, piece_side, bit::first(pieces), perspective,
                     architecture, king_bucket[view]
                  ),
                  +1
               );
            }
         }
      }

      format::active_non_piece_features(
         pos, perspective, features, architecture
      );
      if (!categorical[view].assign(features)) return false;
      for (int feature : features) {
         apply_feature_row(
            result[view], weights, feature_count, feature, +1
         );
      }
   }
   return true;
}

bool build_incremental_accumulator(
   const Accumulator_Cache_Entry & source,
   const Pos & pos,
   std::uint32_t architecture,
   std::uint32_t feature_count,
   const std::vector<std::int16_t> & weights,
   Accumulator & result,
   Cached_Features categorical[Side_Size],
   int king_bucket[Side_Size],
   Evaluation_Trace * trace
) {
   static thread_local std::vector<int> features;
   if (features.capacity() < Cached_Categorical_Capacity) {
      features.reserve(Cached_Categorical_Capacity);
   }
   result = source.accumulator;

   for (int view = 0; view < Side_Size; ++view) {
      const Side perspective = side_make(view);
      king_bucket[view] = perspective_king_bucket(
         pos, perspective, architecture
      );
      if (is_king_state_architecture(architecture)
       && king_bucket[view] < 0) {
         return false;
      }

      const bool rebuild = is_king_state_architecture(architecture)
                        && source.king_bucket[view]
                           != king_bucket[view];
      if (trace != nullptr) trace->perspective_rebuilt[view] = rebuild;

      for (int s = 0; s < Side_Size; ++s) {
         const Side piece_side = side_make(s);
         for (int p = 0; p < Piece_Size; ++p) {
            const Piece piece = piece_make(p);
            const Bit previous = source.snapshot.pieces(piece, piece_side);
            const Bit current = pos.pieces(piece, piece_side);
            const Bit removed = rebuild ? previous : previous & ~current;
            const Bit added = rebuild ? current : current & ~previous;

            for (Bit rows = removed; rows != 0; rows = bit::rest(rows)) {
               apply_feature_row(
                  result[view], weights, feature_count,
                  occupancy_feature(
                     piece, piece_side, bit::first(rows), perspective,
                     architecture, source.king_bucket[view]
                  ),
                  -1
               );
               if (trace != nullptr) ++trace->piece_rows_removed[view];
            }
            for (Bit rows = added; rows != 0; rows = bit::rest(rows)) {
               apply_feature_row(
                  result[view], weights, feature_count,
                  occupancy_feature(
                     piece, piece_side, bit::first(rows), perspective,
                     architecture, king_bucket[view]
                  ),
                  +1
               );
               if (trace != nullptr) ++trace->piece_rows_added[view];
            }
         }
      }

      // Castling, en-passant, clock, phase, and all sixteen architecture-4
      // interaction categories are independently recomputed.  Only changed
      // rows touch the accumulator, but no move-type assumptions are made.
      format::active_non_piece_features(
         pos, perspective, features, architecture
      );
      if (!categorical[view].assign(features)) return false;
      for (std::size_t i = 0; i < source.categorical[view].size; ++i) {
         const int feature = source.categorical[view].rows[i];
         if (!categorical[view].has(feature)) {
            apply_feature_row(
               result[view], weights, feature_count, feature, -1
            );
            if (trace != nullptr) {
               ++trace->categorical_rows_removed[view];
            }
         }
      }
      for (std::size_t i = 0; i < categorical[view].size; ++i) {
         const int feature = categorical[view].rows[i];
         if (!source.categorical[view].has(feature)) {
            apply_feature_row(
               result[view], weights, feature_count, feature, +1
            );
            if (trace != nullptr) {
               ++trace->categorical_rows_added[view];
            }
         }
      }
   }
   return true;
}

bool same_accumulator(const Accumulator & left, const Accumulator & right) {
   return left == right;
}

void store_cache_entry(
   const Pos & pos,
   std::uint64_t generation,
   const Accumulator & accumulator,
   const Cached_Features categorical[Side_Size],
   const int king_bucket[Side_Size]
) {
   Accumulator_Cache_Entry & entry = accumulator_cache().entry[
      accumulator_cache_index(&pos, generation)
   ];
   entry.valid = false;
   entry.identity = &pos;
   entry.network_generation = generation;
   entry.snapshot = pos;
   entry.accumulator = accumulator;
   for (int view = 0; view < Side_Size; ++view) {
      entry.categorical[view] = categorical[view];
      entry.king_bucket[view] = king_bucket[view];
   }
   entry.valid = true;
}

} // namespace

Runtime_Network::Runtime_Network() : p_network() {
}

Configure_Result Runtime_Network::configure(
   const std::string & requested_file
) {
   const std::string file_name = requested_file == "<empty>"
                               ? std::string()
                               : requested_file;
   const std::shared_ptr<const Network_Data> previous =
      std::atomic_load(&p_network);

   if (file_name.empty()) {
      std::atomic_store(
         &p_network, std::shared_ptr<const Network_Data>()
      );

      Configure_Result result;
      result.ok = true;
      result.disabled = true;
      result.message = "Omega NNUE disabled";
      return result;
   }

   std::ifstream input(file_name.c_str(), std::ios::binary | std::ios::ate);
   if (!input) {
      return failure("cannot open " + file_name, previous != nullptr);
   }

   const std::ifstream::pos_type end = input.tellg();
   if (end < 0) {
      return failure("cannot determine the size of " + file_name,
                     previous != nullptr);
   }

   const std::uint64_t file_size = static_cast<std::uint64_t>(end);
   if (file_size != format::File_Bytes
    && file_size != format::King_State_File_Bytes
    && file_size != format::Omega_Interaction_File_Bytes) {
      return failure(
         "wrong file size in " + file_name + " (expected "
         + std::to_string(format::File_Bytes) + " or "
         + std::to_string(format::King_State_File_Bytes)
         + " or "
         + std::to_string(format::Omega_Interaction_File_Bytes)
         + ", got " + std::to_string(file_size) + ")",
         previous != nullptr
      );
   }

   input.seekg(0, std::ios::beg);
   if (!input) {
      return failure("cannot seek in " + file_name, previous != nullptr);
   }

   std::vector<unsigned char> bytes(
      static_cast<std::size_t>(file_size)
   );
   input.read(
      reinterpret_cast<char *>(bytes.data()),
      static_cast<std::streamsize>(bytes.size())
   );
   if (!input || input.gcount() != static_cast<std::streamsize>(bytes.size())) {
      return failure("read error in " + file_name, previous != nullptr);
   }
   if (input.peek() != std::char_traits<char>::eof()) {
      return failure("file changed size while loading " + file_name,
                     previous != nullptr);
   }
   if (input.bad()) {
      return failure("read error in " + file_name, previous != nullptr);
   }

   for (std::size_t i = 0; i < sizeof(Magic); ++i) {
      if (bytes[i] != Magic[i]) {
         return failure("invalid v1 magic in " + file_name,
                        previous != nullptr);
      }
   }

   Byte_Reader header(bytes, sizeof(Magic));
   std::uint32_t endian = 0;
   std::uint32_t version = 0;
   std::uint32_t header_bytes = 0;
   std::uint32_t architecture = 0;
   std::uint32_t squares = 0;
   std::uint32_t pieces = 0;
   std::uint32_t features = 0;
   std::uint32_t accumulator = 0;
   std::uint32_t hidden = 0;
   std::uint32_t activation = 0;
   std::uint32_t hidden_divisor = 0;
   std::uint32_t output_divisor = 0;
   std::uint64_t payload_bytes = 0;
   std::uint64_t payload_hash = 0;

   if (!header.read_u32(endian)
    || !header.read_u32(version)
    || !header.read_u32(header_bytes)
    || !header.read_u32(architecture)
    || !header.read_u32(squares)
    || !header.read_u32(pieces)
    || !header.read_u32(features)
    || !header.read_u32(accumulator)
    || !header.read_u32(hidden)
    || !header.read_u32(activation)
    || !header.read_u32(hidden_divisor)
    || !header.read_u32(output_divisor)
    || !header.read_u64(payload_bytes)
    || !header.read_u64(payload_hash)) {
      return failure("truncated header in " + file_name,
                     previous != nullptr);
   }

   if (header.offset() != format::Header_Bytes) {
      return failure("internal header-size mismatch", previous != nullptr);
   }
   if (endian != format::Endian_Tag) {
      return failure("unsupported byte order in " + file_name,
                     previous != nullptr);
   }
   if (version != format::Format_Version
    || header_bytes != format::Header_Bytes) {
      return failure("unsupported format version in " + file_name,
                     previous != nullptr);
   }
   if (architecture != format::Architecture_Absolute
    && architecture != format::Architecture_Residual
    && architecture != format::Architecture_King_State_Residual
    && architecture != format::Architecture_Omega_Interaction_Residual) {
      return failure("unsupported architecture in " + file_name,
                     previous != nullptr);
   }
   const bool king_state =
      architecture == format::Architecture_King_State_Residual
      || architecture == format::Architecture_Omega_Interaction_Residual;
   const bool omega_interaction =
      architecture == format::Architecture_Omega_Interaction_Residual;
   const std::uint32_t expected_features =
      omega_interaction
      ? format::Omega_Interaction_Feature_Count
      : (king_state ? format::King_State_Feature_Count
                    : format::Feature_Count);
   const std::uint64_t expected_payload =
      omega_interaction
      ? format::Omega_Interaction_Payload_Bytes
      : (king_state ? format::King_State_Payload_Bytes
                    : format::Payload_Bytes);
   const std::uint64_t expected_file =
      omega_interaction
      ? format::Omega_Interaction_File_Bytes
      : (king_state ? format::King_State_File_Bytes
                    : format::File_Bytes);
   if (file_size != expected_file) {
      return failure(
         "file size does not match architecture in " + file_name,
         previous != nullptr
      );
   }
   if (squares != format::Square_Count
    || pieces != format::Piece_Count
    || features != expected_features
    || accumulator != format::Accumulator_Size
    || hidden != format::Hidden_Size
    || activation != format::Activation_Max
    || hidden_divisor != format::Hidden_Divisor
    || output_divisor != format::Output_Divisor) {
      return failure("architecture dimensions do not match OMNNUE1 contract in "
                     + file_name, previous != nullptr);
   }
   if (payload_bytes != expected_payload) {
      return failure("invalid payload length in " + file_name,
                     previous != nullptr);
   }
   if (fnv1a(bytes, format::Header_Bytes) != payload_hash) {
      return failure("payload checksum mismatch in " + file_name,
                     previous != nullptr);
   }

   std::shared_ptr<Network_Data> next(new Network_Data());
   next->path = file_name;
   next->architecture = architecture;
   next->feature_count = expected_features;
   next->residual_correction =
      architecture == format::Architecture_Residual
      || architecture == format::Architecture_King_State_Residual
      || architecture == format::Architecture_Omega_Interaction_Residual;
   next->ft_weights.resize(
      std::size_t(expected_features) * format::Accumulator_Size
   );
   next->hidden_weights.resize(
      std::size_t(format::Hidden_Size) * format::Dense_Input_Size
   );

   Byte_Reader payload(bytes, format::Header_Bytes);

   for (std::size_t i = 0; i < next->ft_bias.size(); ++i) {
      if (!payload.read_i16(next->ft_bias[i])) {
         return failure("truncated feature bias in " + file_name,
                        previous != nullptr);
      }
   }
   for (std::size_t i = 0; i < next->ft_weights.size(); ++i) {
      if (!payload.read_i16(next->ft_weights[i])) {
         return failure("truncated feature weights in " + file_name,
                        previous != nullptr);
      }
   }
   for (std::size_t i = 0; i < next->hidden_bias.size(); ++i) {
      if (!payload.read_i32(next->hidden_bias[i])) {
         return failure("truncated hidden bias in " + file_name,
                        previous != nullptr);
      }
   }
   for (std::size_t i = 0; i < next->hidden_weights.size(); ++i) {
      if (!payload.read_i8(next->hidden_weights[i])) {
         return failure("truncated hidden weights in " + file_name,
                        previous != nullptr);
      }
   }
   if (!payload.read_i32(next->output_bias)) {
      return failure("truncated output bias in " + file_name,
                     previous != nullptr);
   }
   for (std::size_t i = 0; i < next->output_weights.size(); ++i) {
      if (!payload.read_i8(next->output_weights[i])) {
         return failure("truncated output weights in " + file_name,
                        previous != nullptr);
      }
   }

   if (payload.remaining() != 0) {
      return failure("unexpected payload data in " + file_name,
                     previous != nullptr);
   }

   // Every successfully published immutable network gets a process-unique
   // identity.  Thread-local accumulator entries from an earlier load can
   // therefore never be reused after replacement, even when the allocator
   // recycles the same Network_Data address.
   next->generation = Next_Network_Generation.fetch_add(
      1ULL, std::memory_order_relaxed
   );
   assert(next->generation != 0ULL);

   std::atomic_store(
      &p_network, std::shared_ptr<const Network_Data>(next)
   );

   Configure_Result result;
   result.ok = true;
   if (omega_interaction) {
      result.message =
         "Omega NNUE loaded: KingPS104-Omega64-128x2-32 "
         "bounded residual correction from " + file_name;
   } else if (king_state) {
      result.message =
         "Omega NNUE loaded: KingPS104-state-128x2-32 residual correction from "
         + file_name;
   } else if (next->residual_correction) {
      result.message =
         "Omega NNUE loaded: PS104-128x2-32 residual correction from "
         + file_name;
   } else {
      result.message = "Omega NNUE loaded: PS104-128x2-32 from " + file_name;
   }
   return result;
}

bool Runtime_Network::evaluate(
   const Pos & pos,
   int & side_to_move_cp
) const {
   bool residual_correction = false;
   return evaluate(pos, side_to_move_cp, residual_correction);
}

bool Runtime_Network::evaluate(
   const Pos & pos,
   int & side_to_move_cp,
   bool & residual_correction
) const {
   return evaluate_impl(
      pos, side_to_move_cp, residual_correction, false, nullptr
   );
}

bool Runtime_Network::evaluate_full_refresh(
   const Pos & pos,
   int & side_to_move_cp,
   bool & residual_correction
) const {
   return evaluate_impl(
      pos, side_to_move_cp, residual_correction, true, nullptr
   );
}

bool Runtime_Network::evaluate_with_oracle(
   const Pos & pos,
   int & side_to_move_cp,
   bool & residual_correction,
   Evaluation_Trace & trace
) const {
   return evaluate_impl(
      pos, side_to_move_cp, residual_correction, false, &trace
   );
}

bool Runtime_Network::evaluate_impl(
   const Pos & pos,
   int & side_to_move_cp,
   bool & residual_correction,
   bool force_full_refresh,
   Evaluation_Trace * trace
) const {
   if (trace != nullptr) *trace = Evaluation_Trace();
   residual_correction = false;
   const std::shared_ptr<const Network_Data> network =
      std::atomic_load(&p_network);
   if (network == nullptr || !variant_is_omega()) return false;
   residual_correction = network->residual_correction;

   Accumulator accumulator;
   Cached_Features categorical[Side_Size];
   int king_bucket[Side_Size] { -1, -1 };
   bool accumulated = false;

   if (!force_full_refresh) {
      Accumulator_Cache_Entry * current = find_cache_entry(
         &pos, network->generation
      );
      if (current != nullptr && same_nnue_state(current->snapshot, pos)) {
         accumulator = current->accumulator;
         for (int view = 0; view < Side_Size; ++view) {
            categorical[view] = current->categorical[view];
            king_bucket[view] = current->king_bucket[view];
         }
         accumulated = true;
         if (trace != nullptr) trace->cache_hit = true;
      }
   }

   if (!force_full_refresh && !accumulated) {
      const Pos * parent = pos.known_parent();

      // The updater consumes exactly one cached source snapshot and never
      // dereferences the raw parent pointer.  Self-links and the two-node
      // cycle visible from that snapshot are rejected before any delta work.
      // A source-state/address mismatch is harmless but not useful: all 16
      // bitboards and every categorical group would still be diffed exactly.
      if (parent != nullptr && parent != &pos) {
         Accumulator_Cache_Entry * source = find_cache_entry(
            parent, network->generation
         );
         const bool cycle = source != nullptr
            && (source->snapshot.known_parent() == source->identity
             || source->snapshot.known_parent() == &pos);
         if (source != nullptr && !cycle) {
            Evaluation_Trace attempted;
            Evaluation_Trace * attempted_trace =
               trace == nullptr ? nullptr : &attempted;
            accumulated = build_incremental_accumulator(
               *source, pos, network->architecture,
               network->feature_count, network->ft_weights,
               accumulator, categorical, king_bucket, attempted_trace
            );
            if (accumulated && trace != nullptr) {
               *trace = attempted;
               trace->incremental_parent = true;
            }
         }
      }
   }

   if (!accumulated) {
      if (!build_full_accumulator(
             pos, network->architecture, network->feature_count,
             network->ft_bias, network->ft_weights, accumulator,
             categorical, king_bucket
          )) {
         return false;
      }
      accumulated = true;
      if (trace != nullptr) trace->full_refresh = true;
   }

   if (trace != nullptr
    && (trace->cache_hit || trace->incremental_parent)) {
      Accumulator oracle;
      Cached_Features oracle_categorical[Side_Size];
      int oracle_king_bucket[Side_Size] { -1, -1 };
      if (!build_full_accumulator(
             pos, network->architecture, network->feature_count,
             network->ft_bias, network->ft_weights, oracle,
             oracle_categorical, oracle_king_bucket
          )) {
         return false;
      }
      trace->oracle_checked = true;
      trace->oracle_match = same_accumulator(accumulator, oracle);
      if (!trace->oracle_match) {
         accumulator = oracle;
         for (int view = 0; view < Side_Size; ++view) {
            categorical[view] = oracle_categorical[view];
            king_bucket[view] = oracle_king_bucket[view];
         }
         trace->full_refresh = true;
      }
   }

   store_cache_entry(
      pos, network->generation, accumulator, categorical, king_bucket
   );

   std::array<std::uint8_t, format::Dense_Input_Size> input;
   const Side turn = pos.turn();
   const Side opponent = side_opp(turn);

   for (std::size_t i = 0; i < format::Accumulator_Size; ++i) {
      input[i] = static_cast<std::uint8_t>(
         clipped_activation(accumulator[turn][i])
      );
      input[format::Accumulator_Size + i] =
         static_cast<std::uint8_t>(
            clipped_activation(accumulator[opponent][i])
         );
   }

   std::array<std::uint8_t, format::Hidden_Size> hidden;
   for (std::size_t j = 0; j < format::Hidden_Size; ++j) {
      std::int64_t sum = network->hidden_bias[j];
      const std::size_t offset = j * format::Dense_Input_Size;

      for (std::size_t i = 0; i < format::Dense_Input_Size; ++i) {
         sum += std::int64_t(network->hidden_weights[offset + i])
              * std::int64_t(input[i]);
      }

      hidden[j] = static_cast<std::uint8_t>(clipped_activation(
         divide_round(sum, format::Hidden_Divisor)
      ));
   }

   std::int64_t output = network->output_bias;
   for (std::size_t i = 0; i < format::Hidden_Size; ++i) {
      output += std::int64_t(network->output_weights[i])
              * std::int64_t(hidden[i]);
   }
   output = divide_round(output, format::Output_Divisor);

   if (network->architecture
       == format::Architecture_Omega_Interaction_Residual) {
      output = std::max<std::int64_t>(
         -format::Omega_Interaction_Residual_Limit_Cp,
         std::min<std::int64_t>(
            format::Omega_Interaction_Residual_Limit_Cp, output
         )
      );
   }

   if (output > std::numeric_limits<int>::max()) {
      side_to_move_cp = std::numeric_limits<int>::max();
   } else if (output < std::numeric_limits<int>::min()) {
      side_to_move_cp = std::numeric_limits<int>::min();
   } else {
      side_to_move_cp = static_cast<int>(output);
   }

   return true;
}

bool Runtime_Network::loaded() const {
   return std::atomic_load(&p_network) != nullptr;
}

std::string Runtime_Network::path() const {
   const std::shared_ptr<const Network_Data> network =
      std::atomic_load(&p_network);
   return network == nullptr ? std::string() : network->path;
}

} // namespace omega_nnue
