#include "omega_nnue.hpp"

#include <algorithm>
#include <array>
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
#include "pos.hpp"

namespace omega_nnue {

namespace {

const unsigned char Magic[8] {
   'O', 'M', 'N', 'N', 'U', 'E', '1', 0,
};

const std::uint64_t FNV_Offset { 14695981039346656037ULL };
const std::uint64_t FNV_Prime  { 1099511628211ULL };

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

} // namespace

struct Runtime_Network::Network_Data {
   std::string path;
   std::array<std::int16_t, format::Accumulator_Size> ft_bias;
   std::vector<std::int16_t> ft_weights;
   std::array<std::int32_t, format::Hidden_Size> hidden_bias;
   std::vector<std::int8_t> hidden_weights;
   std::int32_t output_bias;
   std::array<std::int8_t, format::Hidden_Size> output_weights;

   Network_Data()
      : path(),
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

int castling_feature(bool own, bool right_of_king) {
   const int relation = own ? 0 : 1;
   const int flank = right_of_king ? 1 : 0;
   return int(Occupancy_Features) + relation * 2 + flank;
}

void active_features(
   const Pos & pos,
   Side perspective,
   std::vector<int> & output
) {
   output.clear();
   output.reserve(48);

   assert(perspective == White || perspective == Black);
   if (perspective != White && perspective != Black) return;

   for (int s = 0; s < Side_Size; ++s) {
      const Side piece_side = side_make(s);

      for (int p = 0; p < Piece_Size; ++p) {
         const Piece pc = piece_make(p);

         for (Bit pieces = pos.pieces(pc, piece_side);
              pieces != 0;
              pieces = bit::rest(pieces)) {
            const int feature = piece_feature(
               pc, piece_side, bit::first(pieces), perspective
            );
            assert(feature >= 0 && feature < int(Occupancy_Features));
            if (feature >= 0) output.push_back(feature);
         }
      }
   }

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
            output.push_back(castling_feature(
               castling_side == perspective, flank != 0
            ));
         }
      }
   }
}

} // namespace format

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
   if (file_size != format::File_Bytes) {
      return failure(
         "wrong file size in " + file_name
         + " (expected " + std::to_string(format::File_Bytes)
         + ", got " + std::to_string(file_size) + ")",
         previous != nullptr
      );
   }

   input.seekg(0, std::ios::beg);
   if (!input) {
      return failure("cannot seek in " + file_name, previous != nullptr);
   }

   std::vector<unsigned char> bytes(
      static_cast<std::size_t>(format::File_Bytes)
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
   if (architecture != format::Architecture_Id) {
      return failure("unsupported architecture in " + file_name,
                     previous != nullptr);
   }
   if (squares != format::Square_Count
    || pieces != format::Piece_Count
    || features != format::Feature_Count
    || accumulator != format::Accumulator_Size
    || hidden != format::Hidden_Size
    || activation != format::Activation_Max
    || hidden_divisor != format::Hidden_Divisor
    || output_divisor != format::Output_Divisor) {
      return failure("architecture dimensions do not match PS104-128x2-32 in "
                     + file_name, previous != nullptr);
   }
   if (payload_bytes != format::Payload_Bytes) {
      return failure("invalid payload length in " + file_name,
                     previous != nullptr);
   }
   if (fnv1a(bytes, format::Header_Bytes) != payload_hash) {
      return failure("payload checksum mismatch in " + file_name,
                     previous != nullptr);
   }

   std::shared_ptr<Network_Data> next(new Network_Data());
   next->path = file_name;
   next->ft_weights.resize(
      std::size_t(format::Feature_Count) * format::Accumulator_Size
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

   std::atomic_store(
      &p_network, std::shared_ptr<const Network_Data>(next)
   );

   Configure_Result result;
   result.ok = true;
   result.message = "Omega NNUE loaded: PS104-128x2-32 from " + file_name;
   return result;
}

bool Runtime_Network::evaluate(
   const Pos & pos,
   int & side_to_move_cp
) const {
   const std::shared_ptr<const Network_Data> network =
      std::atomic_load(&p_network);
   if (network == nullptr || !variant_is_omega()) return false;

   std::array<
      std::array<std::int32_t, format::Accumulator_Size>,
      Side_Size
   > accumulator;
   // Search evaluates serially within each worker.  Reusing a thread-local
   // sparse list avoids a heap allocation at every leaf while remaining safe
   // for Senpai's independent search threads.
   static thread_local std::vector<int> features;
   if (features.capacity() < format::Square_Count + format::Castling_Features) {
      features.reserve(format::Square_Count + format::Castling_Features);
   }

   for (int s = 0; s < Side_Size; ++s) {
      for (std::size_t i = 0; i < format::Accumulator_Size; ++i) {
         accumulator[s][i] = network->ft_bias[i];
      }

      format::active_features(pos, side_make(s), features);
      for (std::size_t feature_pos = 0;
           feature_pos < features.size();
           ++feature_pos) {
         const int feature = features[feature_pos];
         assert(feature >= 0 && feature < int(format::Feature_Count));
         if (feature < 0 || feature >= int(format::Feature_Count)) continue;

         const std::size_t offset =
            std::size_t(feature) * format::Accumulator_Size;
         for (std::size_t i = 0; i < format::Accumulator_Size; ++i) {
            accumulator[s][i] += network->ft_weights[offset + i];
         }
      }
   }

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
