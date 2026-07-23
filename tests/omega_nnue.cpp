#define DEBUG

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/list.hpp"
#include "../src/math.hpp"
#include "../src/move.hpp"
#include "../src/omega_nnue.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"
#include "../src/score.hpp"
#include "../src/var.hpp"

namespace {

namespace nn = omega_nnue;
namespace fmt = omega_nnue::format;

const std::string Good_A { "omega-nnue-good-a-test.omnnue" };
const std::string Good_B { "omega-nnue-good-b-test.omnnue" };
const std::string Good_Residual {
   "omega-nnue-good-residual-test.omnnue"
};
const std::string Good_King_State {
   "omega-nnue-good-king-state-test.omnnue"
};
const std::string Good_Omega_Interaction {
   "omega-nnue-good-omega-interaction-test.omnnue"
};
const std::string Bad_File { "omega-nnue-bad-test.omnnue" };
const std::string Missing_File { "omega-nnue-missing-test.omnnue" };

const std::size_t Ft_Bias_Offset { fmt::Header_Bytes };
const std::size_t Ft_Weight_Offset {
   Ft_Bias_Offset + fmt::Accumulator_Size * 2U
};
const std::size_t Hidden_Bias_Offset {
   Ft_Weight_Offset
   + std::size_t(fmt::Feature_Count) * fmt::Accumulator_Size * 2U
};
const std::size_t Hidden_Weight_Offset {
   Hidden_Bias_Offset + fmt::Hidden_Size * 4U
};
const std::size_t Output_Bias_Offset {
   Hidden_Weight_Offset
   + std::size_t(fmt::Hidden_Size) * fmt::Dense_Input_Size
};
const std::size_t Output_Weight_Offset { Output_Bias_Offset + 4U };

static_assert(fmt::Header_Bytes == 72U, "OMNNUE1 header size changed");
static_assert(fmt::Square_Count == 104U, "PS104 square count changed");
static_assert(fmt::Piece_Count == 8U, "PS104 piece count changed");
static_assert(fmt::Occupancy_Features == 1664U,
              "PS104 occupancy map changed");
static_assert(fmt::Castling_Features == 4U,
              "PS104 castling map changed");
static_assert(fmt::Feature_Count == 1668U, "PS104 feature count changed");
static_assert(fmt::Accumulator_Size == 128U,
              "OMNNUE1 accumulator size changed");
static_assert(fmt::Dense_Input_Size == 256U,
              "OMNNUE1 dense input size changed");
static_assert(fmt::Hidden_Size == 32U, "OMNNUE1 hidden size changed");
static_assert(fmt::Payload_Bytes == 435620ULL,
              "OMNNUE1 payload size changed");
static_assert(fmt::File_Bytes == 435692ULL, "OMNNUE1 file size changed");
static_assert(fmt::King_Bucket_Count == 29U,
              "king-state bucket count changed");
static_assert(fmt::King_State_Feature_Count == 48376U,
              "king-state feature count changed");
static_assert(fmt::King_State_Payload_Bytes == 12392868ULL,
              "king-state payload size changed");
static_assert(fmt::King_State_File_Bytes == 12392940ULL,
              "king-state file size changed");
static_assert(fmt::Architecture_Omega_Interaction_Residual == 4U,
              "Omega-interaction architecture id changed");
static_assert(fmt::Omega_Interaction_Features == 64U,
              "Omega-interaction row count changed");
static_assert(fmt::Omega_Interaction_Feature_Count == 48440U,
              "Omega-interaction feature count changed");
static_assert(fmt::Omega_Interaction_Payload_Bytes == 12409252ULL,
              "Omega-interaction payload size changed");
static_assert(fmt::Omega_Interaction_File_Bytes == 12409324ULL,
              "Omega-interaction file size changed");
static_assert(fmt::Omega_Interaction_Residual_Limit_Cp == 600,
              "Omega-interaction residual limit changed");
static_assert(Output_Weight_Offset + fmt::Hidden_Size == fmt::File_Bytes,
              "OMNNUE1 payload layout changed");

void put_u16(
   std::vector<unsigned char> & bytes,
   std::size_t offset,
   std::uint16_t value
) {
   assert(offset + 2U <= bytes.size());
   bytes[offset] = static_cast<unsigned char>(value & 0xFFU);
   bytes[offset + 1U] =
      static_cast<unsigned char>((value >> 8) & 0xFFU);
}

void put_i16(
   std::vector<unsigned char> & bytes,
   std::size_t offset,
   std::int16_t value
) {
   put_u16(bytes, offset, static_cast<std::uint16_t>(value));
}

void put_u32(
   std::vector<unsigned char> & bytes,
   std::size_t offset,
   std::uint32_t value
) {
   assert(offset + 4U <= bytes.size());
   for (int byte = 0; byte < 4; ++byte) {
      bytes[offset + std::size_t(byte)] =
         static_cast<unsigned char>((value >> (byte * 8)) & 0xFFU);
   }
}

void put_i32(
   std::vector<unsigned char> & bytes,
   std::size_t offset,
   std::int32_t value
) {
   put_u32(bytes, offset, static_cast<std::uint32_t>(value));
}

void put_u64(
   std::vector<unsigned char> & bytes,
   std::size_t offset,
   std::uint64_t value
) {
   assert(offset + 8U <= bytes.size());
   for (int byte = 0; byte < 8; ++byte) {
      bytes[offset + std::size_t(byte)] =
         static_cast<unsigned char>((value >> (byte * 8)) & 0xFFU);
   }
}

void put_i8(
   std::vector<unsigned char> & bytes,
   std::size_t offset,
   std::int8_t value
) {
   assert(offset < bytes.size());
   bytes[offset] = static_cast<unsigned char>(
      static_cast<std::uint8_t>(value)
   );
}

std::uint64_t fnv1a(
   const std::vector<unsigned char> & bytes,
   std::size_t begin
) {
   std::uint64_t hash = 14695981039346656037ULL;
   for (std::size_t i = begin; i < bytes.size(); ++i) {
      hash ^= std::uint64_t(bytes[i]);
      hash *= 1099511628211ULL;
   }
   return hash;
}

std::size_t ft_weight_offset(int feature, int lane) {
   assert(feature >= 0 && feature < int(fmt::Feature_Count));
   assert(lane >= 0 && lane < int(fmt::Accumulator_Size));
   return Ft_Weight_Offset
        + (std::size_t(feature) * fmt::Accumulator_Size
           + std::size_t(lane)) * 2U;
}

std::size_t hidden_weight_offset(int neuron, int input) {
   assert(neuron >= 0 && neuron < int(fmt::Hidden_Size));
   assert(input >= 0 && input < int(fmt::Dense_Input_Size));
   return Hidden_Weight_Offset
        + std::size_t(neuron) * fmt::Dense_Input_Size
        + std::size_t(input);
}

std::vector<unsigned char> make_fixture(
   int output_adjust_cp = 0,
   std::uint32_t architecture = fmt::Architecture_Absolute
) {
   std::vector<unsigned char> bytes(
      static_cast<std::size_t>(fmt::File_Bytes), 0
   );

   const unsigned char magic[8] {
      'O', 'M', 'N', 'N', 'U', 'E', '1', 0,
   };
   std::copy(magic, magic + 8, bytes.begin());

   put_u32(bytes, 8, fmt::Endian_Tag);
   put_u32(bytes, 12, fmt::Format_Version);
   put_u32(bytes, 16, fmt::Header_Bytes);
   put_u32(bytes, 20, architecture);
   put_u32(bytes, 24, fmt::Square_Count);
   put_u32(bytes, 28, fmt::Piece_Count);
   put_u32(bytes, 32, fmt::Feature_Count);
   put_u32(bytes, 36, fmt::Accumulator_Size);
   put_u32(bytes, 40, fmt::Hidden_Size);
   put_u32(bytes, 44, fmt::Activation_Max);
   put_u32(bytes, 48, fmt::Hidden_Divisor);
   put_u32(bytes, 52, fmt::Output_Divisor);
   put_u64(bytes, 56, fmt::Payload_Bytes);

   // The deterministic test net computes:
   //
   //   clip(STM accumulator lane 0)
   //   - clip(opponent accumulator lane 1)
   //   + output_adjust_cp
   //
   // Both dense neurons and the output use weights of exactly one divisor,
   // making the expected integer result independent of rounding subtleties.
   put_i16(bytes, Ft_Bias_Offset + 0U * 2U, 10);
   put_i16(bytes, Ft_Bias_Offset + 1U * 2U, 20);

   const int own_champion_c2 = fmt::piece_feature(
      Champion, White, square_from_string("c2"), White
   );
   const int enemy_wizard_w4 = fmt::piece_feature(
      Wizard, Black, square_from_string("w4"), White
   );
   const int own_wizard_w1 = fmt::piece_feature(
      Wizard, White, square_from_string("w1"), White
   );
   const int enemy_champion_c7 = fmt::piece_feature(
      Champion, Black, square_from_string("c7"), White
   );

   put_i16(bytes, ft_weight_offset(own_champion_c2, 0), 11);
   put_i16(bytes, ft_weight_offset(enemy_wizard_w4, 0), 13);
   put_i16(bytes, ft_weight_offset(
      fmt::castling_feature(true, false), 0
   ), 3);
   put_i16(bytes, ft_weight_offset(
      fmt::castling_feature(false, true), 0
   ), 5);

   put_i16(bytes, ft_weight_offset(own_wizard_w1, 1), 7);
   put_i16(bytes, ft_weight_offset(enemy_champion_c7, 1), 17);
   put_i16(bytes, ft_weight_offset(
      fmt::castling_feature(true, true), 1
   ), 2);
   put_i16(bytes, ft_weight_offset(
      fmt::castling_feature(false, false), 1
   ), 4);

   put_i8(bytes, hidden_weight_offset(0, 0),
          static_cast<std::int8_t>(fmt::Hidden_Divisor));
   put_i8(bytes, hidden_weight_offset(
      1, int(fmt::Accumulator_Size) + 1
   ), static_cast<std::int8_t>(fmt::Hidden_Divisor));

   put_i32(
      bytes,
      Output_Bias_Offset,
      std::int32_t(output_adjust_cp * int(fmt::Output_Divisor))
   );
   put_i8(bytes, Output_Weight_Offset + 0U,
          static_cast<std::int8_t>(fmt::Output_Divisor));
   put_i8(bytes, Output_Weight_Offset + 1U,
          static_cast<std::int8_t>(-int(fmt::Output_Divisor)));

   put_u64(bytes, 64, fnv1a(bytes, fmt::Header_Bytes));
   return bytes;
}

std::vector<unsigned char> make_king_state_fixture(int output_cp = 1) {
   std::vector<unsigned char> bytes(
      static_cast<std::size_t>(fmt::King_State_File_Bytes), 0
   );
   const unsigned char magic[8] {
      'O', 'M', 'N', 'N', 'U', 'E', '1', 0,
   };
   std::copy(magic, magic + 8, bytes.begin());
   put_u32(bytes, 8, fmt::Endian_Tag);
   put_u32(bytes, 12, fmt::Format_Version);
   put_u32(bytes, 16, fmt::Header_Bytes);
   put_u32(bytes, 20, fmt::Architecture_King_State_Residual);
   put_u32(bytes, 24, fmt::Square_Count);
   put_u32(bytes, 28, fmt::Piece_Count);
   put_u32(bytes, 32, fmt::King_State_Feature_Count);
   put_u32(bytes, 36, fmt::Accumulator_Size);
   put_u32(bytes, 40, fmt::Hidden_Size);
   put_u32(bytes, 44, fmt::Activation_Max);
   put_u32(bytes, 48, fmt::Hidden_Divisor);
   put_u32(bytes, 52, fmt::Output_Divisor);
   put_u64(bytes, 56, fmt::King_State_Payload_Bytes);

   const std::size_t output_bias =
        fmt::Header_Bytes
      + fmt::Accumulator_Size * 2U
      + std::size_t(fmt::King_State_Feature_Count)
        * fmt::Accumulator_Size * 2U
      + fmt::Hidden_Size * 4U
      + std::size_t(fmt::Hidden_Size) * fmt::Dense_Input_Size;
   put_i32(
      bytes,
      output_bias,
      std::int32_t(output_cp * int(fmt::Output_Divisor))
   );
   put_u64(bytes, 64, fnv1a(bytes, fmt::Header_Bytes));
   return bytes;
}

std::vector<unsigned char> make_omega_interaction_fixture(
   int output_cp = 1
) {
   std::vector<unsigned char> bytes(
      static_cast<std::size_t>(fmt::Omega_Interaction_File_Bytes), 0
   );
   const unsigned char magic[8] {
      'O', 'M', 'N', 'N', 'U', 'E', '1', 0,
   };
   std::copy(magic, magic + 8, bytes.begin());
   put_u32(bytes, 8, fmt::Endian_Tag);
   put_u32(bytes, 12, fmt::Format_Version);
   put_u32(bytes, 16, fmt::Header_Bytes);
   put_u32(bytes, 20, fmt::Architecture_Omega_Interaction_Residual);
   put_u32(bytes, 24, fmt::Square_Count);
   put_u32(bytes, 28, fmt::Piece_Count);
   put_u32(bytes, 32, fmt::Omega_Interaction_Feature_Count);
   put_u32(bytes, 36, fmt::Accumulator_Size);
   put_u32(bytes, 40, fmt::Hidden_Size);
   put_u32(bytes, 44, fmt::Activation_Max);
   put_u32(bytes, 48, fmt::Hidden_Divisor);
   put_u32(bytes, 52, fmt::Output_Divisor);
   put_u64(bytes, 56, fmt::Omega_Interaction_Payload_Bytes);

   const std::size_t output_bias =
        fmt::Header_Bytes
      + fmt::Accumulator_Size * 2U
      + std::size_t(fmt::Omega_Interaction_Feature_Count)
        * fmt::Accumulator_Size * 2U
      + fmt::Hidden_Size * 4U
      + std::size_t(fmt::Hidden_Size) * fmt::Dense_Input_Size;
   put_i32(
      bytes,
      output_bias,
      std::int32_t(output_cp * int(fmt::Output_Divisor))
   );
   put_u64(bytes, 64, fnv1a(bytes, fmt::Header_Bytes));
   return bytes;
}

void write_bytes(
   const std::string & path,
   const std::vector<unsigned char> & bytes
) {
   std::ofstream output(path.c_str(), std::ios::binary | std::ios::trunc);
   assert(output);
   output.write(
      reinterpret_cast<const char *>(bytes.data()),
      static_cast<std::streamsize>(bytes.size())
   );
   output.close();
   assert(output);
}

bool has(const std::vector<int> & values, int value) {
   return std::find(values.begin(), values.end(), value) != values.end();
}

std::vector<int> sorted_features(
   const Pos & pos,
   Side perspective,
   std::uint32_t architecture = fmt::Architecture_Absolute
) {
   std::vector<int> result;
   fmt::active_features(pos, perspective, result, architecture);
   std::sort(result.begin(), result.end());
   return result;
}

std::vector<int> difference(
   const std::vector<int> & left,
   const std::vector<int> & right
) {
   std::vector<int> result;
   std::set_difference(
      left.begin(), left.end(),
      right.begin(), right.end(),
      std::back_inserter(result)
   );
   return result;
}

Square rank_mirror(Square sq) {
   if (square_is_corner(sq)) {
      return square_from_corner(Corner(3 - int(square_corner(sq))));
   }
   return square_make(square_file(sq), rank_opp(square_rank(sq)));
}

Ofen_Position colour_rank_mirror(const Ofen_Position & source) {
   Ofen_Position out;
   out.turn = side_opp(source.turn);
   out.halfmove_clock = source.halfmove_clock;
   out.fullmove_number = source.fullmove_number;

   for (int s = 0; s < Side_Size; ++s) {
      const Side sd = side_make(s);
      const Side opposite = side_opp(sd);
      out.king_castling[opposite] = source.king_castling[sd];
      out.queen_castling[opposite] = source.queen_castling[sd];
   }

   for (int sq = 0; sq < Square_Capacity; ++sq) {
      const Piece_Side ps = source.piece_side[sq];
      if (ps == Empty) continue;
      out.piece_side[rank_mirror(Square(sq))] = piece_side_make(
         piece_side_piece(ps), side_opp(piece_side_side(ps))
      );
   }

   out.ep_size = source.ep_size;
   for (int i = 0; i < source.ep_size; ++i) {
      out.ep_square[i] = rank_mirror(source.ep_square[i]);
   }
   return out;
}

void put(
   Ofen_Position & pos,
   Piece pc,
   Side sd,
   const char * coordinate
) {
   const Square sq = square_from_string(coordinate);
   assert(pos.piece_side[sq] == Empty);
   pos.piece_side[sq] = piece_side_make(pc, sd);
}

Ofen_Position diagnostic_source() {
   Ofen_Position pos;
   pos.turn = White;

   put(pos, King, White, "f0");
   put(pos, Rook, White, "b0");
   put(pos, Champion, White, "c2");

   put(pos, King, Black, "f9");
   put(pos, Rook, Black, "i9");
   put(pos, Wizard, Black, "w4");

   pos.queen_castling[White] = true;
   pos.king_castling[Black] = true;
   return pos;
}

void select_variant(Variant value) {
   var::set("UCI_Variant", var::variant_to_string(value));
   var::update();
   bit::init(value);
   pawn::init();
   clear_pawn_table();
}

void initialize_omega_runtime() {
   math::init();
   bit::init(Chess);
   hash::init();
   pawn::init();
   pos::init();
   var::init();
   select_variant(Omega);
}

const std::size_t Max_Stream_OFEN_Bytes { 4096U };

bool normalize_stream_ofen(std::string & ofen) {
   if (!ofen.empty() && ofen.back() == '\r') ofen.pop_back();
   if (ofen.empty() || ofen.size() > Max_Stream_OFEN_Bytes) return false;
   for (char c : ofen) {
      const unsigned char byte = static_cast<unsigned char>(c);
      if (byte < 0x20U || byte > 0x7EU) return false;
   }
   return true;
}

bool evaluate_residual_line(
   nn::Runtime_Network & network,
   const std::string & ofen,
   int & side_to_move_cp
) {
   try {
      const Pos pos = pos_from_fen(ofen, Omega);
      bool residual_correction = false;
      return network.evaluate(
         pos, side_to_move_cp, residual_correction
      ) && residual_correction;
   } catch (const std::exception &) {
      return false;
   }
}

int evaluate_handcrafted_stream() {
   initialize_omega_runtime();
   var::set("UseOmegaNNUE", "false");
   var::update();

   std::string ofen;
   std::size_t line = 0;
   while (std::getline(std::cin, ofen)) {
      line++;
      try {
         const Pos pos = pos_from_fen(ofen, Omega);
         const int side_to_move_cp = int(eval(pos, pos.turn()));
         std::cout << side_to_move_cp << std::endl;
      } catch (const std::exception &) {
         std::cerr << "invalid Omega OFEN on input line " << line
                   << std::endl;
         return 5;
      }
   }

   if (!std::cin.eof()) {
      std::cerr << "failed while reading Omega OFEN input" << std::endl;
      return 6;
   }
   return 0;
}

int evaluate_network_stream(const std::string & network_file) {
   initialize_omega_runtime();

   nn::Runtime_Network network;
   const nn::Configure_Result loaded = network.configure(network_file);
   if (!loaded.ok) {
      std::cerr << loaded.message << std::endl;
      return 3;
   }
   int probe_cp = 0;
   bool residual_correction = false;
   const Pos probe = pos_from_fen(Omega_Start_OFEN, Omega);
   if (!network.evaluate(probe, probe_cp, residual_correction)
    || !residual_correction) {
      std::cerr << "network is not an Omega residual-correction network"
                << std::endl;
      return 4;
   }

   std::string ofen;
   std::size_t line = 0;
   while (std::getline(std::cin, ofen)) {
      line++;
      if (!normalize_stream_ofen(ofen)) {
         std::cerr << "invalid Omega OFEN text on input line " << line
                   << std::endl;
         return 5;
      }
      int side_to_move_cp = 0;
      if (!evaluate_residual_line(network, ofen, side_to_move_cp)) {
         std::cerr << "invalid Omega OFEN or non-residual network on input line "
                   << line << std::endl;
         return 4;
      }
      std::cout << side_to_move_cp << '\n';
      if (!std::cout) {
         std::cerr << "failed while writing network correction output"
                   << std::endl;
         return 7;
      }
   }

   if (!std::cin.eof()) {
      std::cerr << "failed while reading Omega OFEN input" << std::endl;
      return 6;
   }
   return 0;
}

void test_feature_map() {
   for (int sq = 0; sq < int(fmt::Square_Count); ++sq) {
      assert(fmt::orient_square(Square(sq), White) == sq);

      const int expected = sq < Regular_Square_Capacity
         ? (sq / Rank_Capacity) * Rank_Capacity
           + (Rank_Capacity - 1 - sq % Rank_Capacity)
         : Regular_Square_Capacity
           + (Corner_Size - 1 - (sq - Regular_Square_Capacity));
      assert(fmt::orient_square(Square(sq), Black) == expected);
      assert(fmt::orient_square(
         Square(fmt::orient_square(Square(sq), Black)), Black
      ) == sq);
   }

   assert(fmt::orient_square(square_from_string("a0"), Black)
          == int(square_from_string("a9")));
   assert(fmt::orient_square(square_from_string("j3"), Black)
          == int(square_from_string("j6")));
   assert(fmt::orient_square(square_from_string("w1"), Black)
          == int(square_from_string("w4")));
   assert(fmt::orient_square(square_from_string("w2"), Black)
          == int(square_from_string("w3")));

   for (int perspective = 0; perspective < Side_Size; ++perspective) {
      std::vector<bool> seen(fmt::Occupancy_Features, false);
      const Side view = side_make(perspective);

      for (int relation_side = 0; relation_side < Side_Size; ++relation_side) {
         const Side piece_side = side_make(relation_side);
         for (int pc = 0; pc < Piece_Size; ++pc) {
            for (int sq = 0; sq < int(fmt::Square_Count); ++sq) {
               const int feature = fmt::piece_feature(
                  piece_make(pc), piece_side, Square(sq), view
               );
               assert(feature >= 0
                   && feature < int(fmt::Occupancy_Features));
               assert(!seen[std::size_t(feature)]);
               seen[std::size_t(feature)] = true;
            }
         }
      }

      assert(std::find(seen.begin(), seen.end(), false) == seen.end());
   }

   // Freeze representative boundaries of the training/runtime mapping.
   assert(fmt::piece_feature(Pawn, White, square_from_string("a0"), White)
          == 0);
   assert(fmt::piece_feature(Wizard, White, square_from_string("w4"), White)
          == 831);
   assert(fmt::piece_feature(Pawn, Black, square_from_string("a0"), White)
          == 832);
   assert(fmt::piece_feature(Pawn, Black, square_from_string("a9"), Black)
          == 0);
   assert(fmt::piece_feature(Wizard, White, square_from_string("w1"), Black)
          == 1663);

   assert(fmt::castling_feature(true, false) == 1664);
   assert(fmt::castling_feature(true, true) == 1665);
   assert(fmt::castling_feature(false, false) == 1666);
   assert(fmt::castling_feature(false, true) == 1667);
}

void test_active_features() {
   const Pos start = pos_from_fen(Omega_Start_OFEN, Omega);
   const std::vector<int> white = sorted_features(start, White);
   const std::vector<int> black = sorted_features(start, Black);

   assert(white.size() == 48U); // 44 pieces plus four castling rights
   assert(std::adjacent_find(white.begin(), white.end()) == white.end());
   assert(black.size() == white.size());
   assert(std::adjacent_find(black.begin(), black.end()) == black.end());
   assert(white == black); // the initial array is colour/rank symmetric

   assert(has(white, fmt::piece_feature(
      Champion, White, square_from_string("a0"), White
   )));
   assert(has(white, fmt::piece_feature(
      Champion, White, square_from_string("j0"), White
   )));
   assert(has(white, fmt::piece_feature(
      Wizard, White, square_from_string("w1"), White
   )));
   assert(has(white, fmt::piece_feature(
      Wizard, White, square_from_string("w2"), White
   )));
   assert(has(white, fmt::piece_feature(
      Wizard, Black, square_from_string("w3"), White
   )));
   assert(has(white, fmt::piece_feature(
      Wizard, Black, square_from_string("w4"), White
   )));

   for (int feature = int(fmt::Occupancy_Features);
        feature < int(fmt::Feature_Count);
        ++feature) {
      assert(has(white, feature));
   }

   List legal;
   gen_legals(legal, start);

   const Move wizard_move = move::from_uci("w1a2", start);
   assert(list::has(legal, wizard_move));
   const Pos wizard_after = start.succ(wizard_move);
   const std::vector<int> wizard_white =
      sorted_features(wizard_after, White);

   const std::vector<int> wizard_removed = difference(white, wizard_white);
   const std::vector<int> wizard_added = difference(wizard_white, white);
   assert(wizard_removed.size() == 1U);
   assert(wizard_added.size() == 1U);
   assert(wizard_removed[0] == fmt::piece_feature(
      Wizard, White, square_from_string("w1"), White
   ));
   assert(wizard_added[0] == fmt::piece_feature(
      Wizard, White, square_from_string("a2"), White
   ));

   const Move champion_move = move::from_uci("a0c2", start);
   assert(list::has(legal, champion_move));
   const Pos champion_after = start.succ(champion_move);
   const std::vector<int> champion_white =
      sorted_features(champion_after, White);

   const std::vector<int> champion_removed =
      difference(white, champion_white);
   const std::vector<int> champion_added =
      difference(champion_white, white);
   assert(champion_removed.size() == 1U);
   assert(champion_added.size() == 1U);
   assert(champion_removed[0] == fmt::piece_feature(
      Champion, White, square_from_string("a0"), White
   ));
   assert(champion_added[0] == fmt::piece_feature(
      Champion, White, square_from_string("c2"), White
   ));

   const Ofen_Position base = ofen_parse(Omega_Start_OFEN);
   for (int mask = 0; mask < 16; ++mask) {
      Ofen_Position rights = base;
      rights.queen_castling[White] = (mask & 1) != 0;
      rights.king_castling[White] = (mask & 2) != 0;
      rights.queen_castling[Black] = (mask & 4) != 0;
      rights.king_castling[Black] = (mask & 8) != 0;
      const Pos pos = pos_from_ofen(rights);

      for (int perspective = 0; perspective < Side_Size; ++perspective) {
         const Side view = side_make(perspective);
         const std::vector<int> features = sorted_features(pos, view);
         std::vector<int> actual;
         for (int feature : features) {
            if (feature >= int(fmt::Occupancy_Features)) {
               actual.push_back(feature);
            }
         }

         std::vector<int> expected;
         const bool own_white = view == White;
         if (mask & 1) expected.push_back(fmt::castling_feature(
            own_white, false
         ));
         if (mask & 2) expected.push_back(fmt::castling_feature(
            own_white, true
         ));
         if (mask & 4) expected.push_back(fmt::castling_feature(
            !own_white, false
         ));
         if (mask & 8) expected.push_back(fmt::castling_feature(
            !own_white, true
         ));
         std::sort(expected.begin(), expected.end());
         assert(actual == expected);
      }
   }

   // A stale OFEN right without the corresponding rook is metadata, not an
   // active castling feature.
   Ofen_Position stale = base;
   stale.king_castling[White] = false;
   stale.queen_castling[White] = true;
   stale.king_castling[Black] = false;
   stale.queen_castling[Black] = false;
   stale.piece_side[square_from_string("b0")] = Empty;
   const std::vector<int> stale_features =
      sorted_features(pos_from_ofen(stale), White);
   assert(!has(stale_features, fmt::castling_feature(true, false)));
}

void test_king_state_features() {
   assert(fmt::king_bucket(square_from_string("a0"), White) == 0);
   assert(fmt::king_bucket(square_from_string("b1"), White) == 0);
   assert(fmt::king_bucket(square_from_string("c0"), White) == 5);
   assert(fmt::king_bucket(square_from_string("j9"), White) == 24);
   assert(fmt::king_bucket(square_from_string("w1"), White) == 25);
   assert(fmt::king_bucket(square_from_string("w4"), White) == 28);
   assert(fmt::king_bucket(square_from_string("a9"), Black) == 0);
   assert(fmt::king_bucket(square_from_string("w4"), Black) == 25);

   const Pos start = pos_from_fen(Omega_Start_OFEN, Omega);
   const std::vector<int> white = sorted_features(
      start, White, fmt::Architecture_King_State_Residual
   );
   const std::vector<int> black = sorted_features(
      start, Black, fmt::Architecture_King_State_Residual
   );
   assert(white.size() == 50U); // 44 pieces, 4 rights, clock, phase
   assert(white == black);
   assert(std::adjacent_find(white.begin(), white.end()) == white.end());
   const int bucket = fmt::king_bucket(start.king(White), White);
   assert(has(white, fmt::king_state_piece_feature(
      Champion, White, square_from_string("a0"), White, bucket
   )));
   for (int flank = 0; flank < 4; ++flank) {
      assert(has(
         white,
         int(fmt::King_State_Castling_Feature_Base) + flank
      ));
   }
   assert(has(white, int(fmt::King_State_Halfmove_Feature_Base)));
   assert(has(white, int(fmt::King_State_Phase_Feature_Base)));

   const Pos state = pos_from_fen(
      "5k4/10/10/10/10/10/10/10/10/4K5[-/-/-/-] "
      "w - d2,d3 74 42",
      Omega
   );
   const std::vector<int> state_white = sorted_features(
      state, White, fmt::Architecture_King_State_Residual
   );
   const std::vector<int> state_black = sorted_features(
      state, Black, fmt::Architecture_King_State_Residual
   );
   assert(has(
      state_white,
      int(fmt::King_State_EP_Feature_Base)
      + int(square_from_string("d2"))
   ));
   assert(has(
      state_white,
      int(fmt::King_State_EP_Feature_Base)
      + int(square_from_string("d3"))
   ));
   assert(has(
      state_black,
      int(fmt::King_State_EP_Feature_Base)
      + fmt::orient_square(square_from_string("d2"), Black)
   ));
   assert(has(
      state_black,
      int(fmt::King_State_EP_Feature_Base)
      + fmt::orient_square(square_from_string("d3"), Black)
   ));
   const std::pair<int, int> clock_cases[] {
      { 0, 0 }, { 1, 1 }, { 3, 1 }, { 4, 2 },
      { 15, 2 }, { 16, 3 }, { 31, 3 }, { 32, 4 },
      { 49, 4 }, { 50, 5 }, { 74, 5 }, { 75, 6 },
      { 89, 6 }, { 90, 7 }, { 99, 7 }, { 100, 7 },
   };
   for (const auto & item : clock_cases) {
      assert(fmt::halfmove_clock_bin(item.first) == item.second);
   }
   assert(has(
      state_white,
      int(fmt::King_State_Halfmove_Feature_Base) + 5
   ));
   assert(fmt::material_phase_bin(start) == 0);
   assert(fmt::material_phase_bin(state) == 3);
   assert(has(
      state_white,
      int(fmt::King_State_Phase_Feature_Base) + 3
   ));
}

void test_omega_interaction_features() {
   for (Piece pc : { Champion, Wizard }) {
      for (int from = 0; from < int(fmt::Square_Count); ++from) {
         for (int to = 0; to < int(fmt::Square_Count); ++to) {
            const int distance = fmt::empty_board_leaper_distance(
               pc, Square(from), Square(to)
            );
            assert(distance >= 0);
            assert((distance == 0) == (from == to));
            assert((distance == 1) == bit::has(
               bit::piece_attacks(pc, Square(from)), Square(to)
            ));
         }
      }
   }
   assert(fmt::empty_board_leaper_distance(
      Champion, square_from_string("a0"), square_from_string("c2")
   ) == 1);
   assert(fmt::empty_board_leaper_distance(
      Wizard, square_from_string("w1"), square_from_string("a0")
   ) == 1);
   assert(fmt::empty_board_leaper_distance(
      Wizard, square_from_string("w1"), square_from_string("a1")
   ) > int(fmt::Square_Count));

   const Pos start = pos_from_fen(Omega_Start_OFEN, Omega);
   const std::vector<int> white = sorted_features(
      start, White, fmt::Architecture_Omega_Interaction_Residual
   );
   const std::vector<int> black = sorted_features(
      start, Black, fmt::Architecture_Omega_Interaction_Residual
   );
   assert(white.size() == 66U); // architecture 3's 50 plus 16 categories
   assert(white == black);
   assert(std::adjacent_find(white.begin(), white.end()) == white.end());
   assert(has(
      white,
      int(fmt::Omega_Interaction_Development_Feature_Base) + 8
   ));
   assert(has(
      white,
      int(fmt::Omega_Interaction_Development_Feature_Base) + 9 + 8
   ));
   assert(has(
      white,
      int(fmt::Omega_Interaction_Leaper_Count_Feature_Base) + 2
   ));
   assert(has(
      white,
      int(fmt::Omega_Interaction_Leaper_Count_Feature_Base) + 5
   ));
   assert(has(
      white,
      int(fmt::Omega_Interaction_Coexistence_Feature_Base) + 1
   ));
   assert(has(
      white,
      int(fmt::Omega_Interaction_Coexistence_Feature_Base) + 3
   ));
   assert(has(
      white,
      int(fmt::Omega_Interaction_Activated_Wizard_Feature_Base)
   ));
   assert(has(
      white,
      int(fmt::Omega_Interaction_Activated_Wizard_Feature_Base) + 3
   ));

   int interaction_count = 0;
   for (int feature : white) {
      assert(feature >= 0
          && feature < int(fmt::Omega_Interaction_Feature_Count));
      if (feature >= int(fmt::King_State_Feature_Count)) {
         ++interaction_count;
      }
   }
   assert(interaction_count == 16);

   List legal;
   gen_legals(legal, start);
   const Move wizard_move = move::from_uci("w1a2", start);
   assert(list::has(legal, wizard_move));
   const std::vector<int> wizard_after = sorted_features(
      start.succ(wizard_move),
      White,
      fmt::Architecture_Omega_Interaction_Residual
   );
   assert(has(
      wizard_after,
      int(fmt::Omega_Interaction_Development_Feature_Base) + 7
   ));
   assert(!has(
      wizard_after,
      int(fmt::Omega_Interaction_Development_Feature_Base) + 8
   ));
   assert(has(
      wizard_after,
      int(fmt::Omega_Interaction_Activated_Wizard_Feature_Base) + 1
   ));
   assert(!has(
      wizard_after,
      int(fmt::Omega_Interaction_Activated_Wizard_Feature_Base)
   ));

   Ofen_Position pressure;
   pressure.turn = White;
   put(pressure, King, White, "f0");
   put(pressure, King, Black, "e2");
   put(pressure, Champion, White, "a0");
   put(pressure, Champion, White, "c2");
   const std::vector<int> pressure_features = sorted_features(
      pos_from_ofen(pressure),
      White,
      fmt::Architecture_Omega_Interaction_Residual
   );
   assert(has(
      pressure_features,
      int(fmt::Omega_Interaction_King_Distance_Feature_Base) + 1
   ));
   assert(has(
      pressure_features,
      int(fmt::Omega_Interaction_Champion_Pair_Feature_Base) + 1
   ));
}

void test_symmetry() {
   const Ofen_Position source = diagnostic_source();
   const Ofen_Position reflected = colour_rank_mirror(source);
   const Pos original = pos_from_ofen(source);
   const Pos mirrored = pos_from_ofen(reflected);

   assert(sorted_features(original, White)
          == sorted_features(mirrored, Black));
   assert(sorted_features(original, Black)
          == sorted_features(mirrored, White));
   assert(sorted_features(
      original, White, fmt::Architecture_King_State_Residual
   ) == sorted_features(
      mirrored, Black, fmt::Architecture_King_State_Residual
   ));
   assert(sorted_features(
      original, Black, fmt::Architecture_King_State_Residual
   ) == sorted_features(
      mirrored, White, fmt::Architecture_King_State_Residual
   ));
   assert(sorted_features(
      original, White, fmt::Architecture_Omega_Interaction_Residual
   ) == sorted_features(
      mirrored, Black, fmt::Architecture_Omega_Interaction_Residual
   ));
   assert(sorted_features(
      original, Black, fmt::Architecture_Omega_Interaction_Residual
   ) == sorted_features(
      mirrored, White, fmt::Architecture_Omega_Interaction_Residual
   ));
}

void require_initial_rejection(
   const std::vector<unsigned char> & bytes
) {
   write_bytes(Bad_File, bytes);
   nn::Runtime_Network network;
   const nn::Configure_Result result = network.configure(Bad_File);
   assert(!result.ok);
   assert(!result.disabled);
   assert(!result.retained_previous);
   assert(!network.loaded());
   assert(network.path().empty());
   assert(result.message.find("Omega NNUE load failed: ") == 0U);
}

void test_loader_corruption(const std::vector<unsigned char> & good) {
   std::remove(Missing_File.c_str());
   nn::Runtime_Network missing;
   const nn::Configure_Result missing_result =
      missing.configure(Missing_File);
   assert(!missing_result.ok);
   assert(!missing_result.retained_previous);
   assert(!missing.loaded());

   require_initial_rejection({});

   std::vector<unsigned char> bad = good;
   bad.pop_back();
   require_initial_rejection(bad);

   bad = good;
   bad.push_back(0);
   require_initial_rejection(bad);

   bad = good;
   bad[0] ^= 1U;
   require_initial_rejection(bad);

   const std::pair<std::size_t, std::uint32_t> wrong_u32[] {
      { 8U,  0x04030201U },
      { 12U, fmt::Format_Version + 1U },
      { 16U, fmt::Header_Bytes + 4U },
      { 20U, fmt::Architecture_Omega_Interaction_Residual + 1U },
      { 24U, 64U }, // a standard-chess network is not an Omega network
      { 28U, fmt::Piece_Count - 1U },
      { 32U, fmt::Feature_Count - 1U },
      { 36U, fmt::Accumulator_Size + 1U },
      { 40U, fmt::Hidden_Size + 1U },
      { 44U, fmt::Activation_Max + 1U },
      { 48U, fmt::Hidden_Divisor + 1U },
      { 52U, fmt::Output_Divisor + 1U },
   };

   for (const auto & mutation : wrong_u32) {
      bad = good;
      put_u32(bad, mutation.first, mutation.second);
      require_initial_rejection(bad);
   }

   bad = good;
   put_u32(bad, 32U, 0xFFFFFFFFU);
   require_initial_rejection(bad);

   bad = good;
   put_u64(bad, 56U, fmt::Payload_Bytes - 1U);
   require_initial_rejection(bad);

   bad = good;
   bad[64] ^= 1U;
   require_initial_rejection(bad);

   bad = good;
   bad[fmt::Header_Bytes + 17U] ^= 1U;
   require_initial_rejection(bad);
}

void test_loading_and_inference(
   const std::vector<unsigned char> & good_a,
   const std::vector<unsigned char> & good_b
) {
   write_bytes(Good_A, good_a);
   write_bytes(Good_B, good_b);

   const Pos original = pos_from_ofen(diagnostic_source());
   const Pos mirrored = pos_from_ofen(
      colour_rank_mirror(diagnostic_source())
   );

   nn::Runtime_Network network;
   nn::Configure_Result loaded = network.configure(Good_A);
   assert(loaded.ok);
   assert(!loaded.disabled);
   assert(!loaded.retained_previous);
   assert(loaded.message ==
          "Omega NNUE loaded: PS104-128x2-32 from " + Good_A);
   assert(network.loaded());
   assert(network.path() == Good_A);

   int raw_original = 0;
   int raw_mirrored = 0;
   bool residual_correction = true;
   assert(network.evaluate(
      original, raw_original, residual_correction
   ));
   assert(!residual_correction);
   assert(network.evaluate(mirrored, raw_mirrored));
   assert(raw_original == -8);
   assert(raw_mirrored == raw_original);

   std::vector<unsigned char> corrupt = good_a;
   corrupt.back() ^= 1U;
   write_bytes(Bad_File, corrupt);
   const nn::Configure_Result retained = network.configure(Bad_File);
   assert(!retained.ok);
   assert(retained.retained_previous);
   assert(retained.message.find("; previous network retained")
          != std::string::npos);
   assert(network.loaded());
   assert(network.path() == Good_A);
   int retained_score = 0;
   assert(network.evaluate(original, retained_score));
   assert(retained_score == raw_original);

   loaded = network.configure(Good_B);
   assert(loaded.ok);
   assert(network.path() == Good_B);
   int adjusted = 0;
   assert(network.evaluate(original, adjusted));
   assert(adjusted == raw_original + 5);

   const nn::Configure_Result disabled = network.configure("<empty>");
   assert(disabled.ok);
   assert(disabled.disabled);
   assert(!disabled.retained_previous);
   assert(disabled.message == "Omega NNUE disabled");
   assert(!network.loaded());
   assert(network.path().empty());
   assert(!network.evaluate(original, adjusted));

   // Exercise the actual eval adapter, optional fallback, score perspective,
   // and standard-chess isolation with the same known network.
   assert(nn::G_Network.configure(Good_A).ok);
   var::set("UseOmegaNNUE", "false");
   var::update();
   const int handcrafted = int(eval(original, White));

   var::set("UseOmegaNNUE", "true");
   var::update();
   const int nnue_white = int(eval(original, White));
   const int nnue_black = int(eval(original, Black));
   const int nnue_mirrored = int(eval(mirrored, White));
   assert(nnue_white == -8);
   assert(nnue_black == +8);
   assert(nnue_mirrored == +8);
   assert(score::is_eval(Score(nnue_white)));

   var::set("UseOmegaNNUE", "false");
   var::update();
   assert(int(eval(original, White)) == handcrafted);

   select_variant(Chess);
   const Pos chess = pos_from_fen(
      "4k3/2pp4/8/8/4N3/3P4/8/4K3 w - - 0 1", Chess
   );
   var::set("UseOmegaNNUE", "false");
   var::update();
   const int chess_before = int(eval(chess, White));
   var::set("UseOmegaNNUE", "true");
   var::update();
   assert(int(eval(chess, White)) == chess_before);
   int unavailable = 0;
   assert(!nn::G_Network.evaluate(chess, unavailable));

   select_variant(Omega);
   assert(int(eval(original, White)) == -8);
   assert(nn::G_Network.configure("").ok);

   // Use=true without a network is an exact handcrafted fallback.
   const int fallback = int(eval(original, White));
   var::set("UseOmegaNNUE", "false");
   var::update();
   assert(int(eval(original, White)) == fallback);
}

void test_residual_evaluation() {
   std::vector<unsigned char> residual_bytes = make_fixture(
      5, fmt::Architecture_Residual
   );
   write_bytes(Good_Residual, residual_bytes);

   const Pos original = pos_from_ofen(diagnostic_source());
   const Pos draw = pos_from_fen(
      "5k4/10/10/10/10/10/10/10/10/4K5[-/-/-/-] w - - 0 1",
      Omega
   );
   assert(draw.is_draw());

   var::set("UseOmegaNNUE", "false");
   var::update();
   const int handcrafted = int(eval(original, White));

   const nn::Configure_Result loaded =
      nn::G_Network.configure(Good_Residual);
   assert(loaded.ok);
   assert(loaded.message ==
          "Omega NNUE loaded: PS104-128x2-32 residual correction from "
          + Good_Residual);

   int correction = 0;
   bool residual_correction = false;
   assert(nn::G_Network.evaluate(
      original, correction, residual_correction
   ));
   assert(residual_correction);
   assert(correction == -3);

   var::set("UseOmegaNNUE", "true");
   var::update();
   const int combined = int(eval(original, White));
   assert(combined == handcrafted + correction);
   assert(int(eval(original, Black)) == -combined);

   // Draw adjudication is upstream of both HCE and correction.  The raw
   // network is deliberately non-zero here, so this catches an accidental
   // residual addition before the draw bypass.
   int draw_correction = 0;
   assert(nn::G_Network.evaluate(draw, draw_correction));
   assert(draw_correction != 0);
   assert(int(eval(draw, White)) == 0);
   assert(int(eval(draw, Black)) == 0);

   // An extreme but structurally valid correction must remain an evaluation
   // score rather than leaking into the search's mate-score range.
   put_i32(
      residual_bytes,
      Output_Bias_Offset,
      std::numeric_limits<std::int32_t>::max()
   );
   put_u64(
      residual_bytes,
      64,
      fnv1a(residual_bytes, fmt::Header_Bytes)
   );
   write_bytes(Good_Residual, residual_bytes);
   assert(nn::G_Network.configure(Good_Residual).ok);
   const Score safe = eval(original, White);
   assert(score::is_eval(safe));
   assert(safe == score::Eval_Inf);
}

void test_king_state_loading() {
   write_bytes(Good_King_State, make_king_state_fixture(1));
   const nn::Configure_Result loaded =
      nn::G_Network.configure(Good_King_State);
   assert(loaded.ok);
   assert(loaded.message ==
          "Omega NNUE loaded: KingPS104-state-128x2-32 "
          "residual correction from " + Good_King_State);

   const Pos original = pos_from_ofen(diagnostic_source());
   int correction = 0;
   bool residual = false;
   assert(nn::G_Network.evaluate(original, correction, residual));
   assert(residual);
   assert(correction == 1);
}

void test_omega_interaction_loading_and_clamp() {
   write_bytes(
      Good_Omega_Interaction, make_omega_interaction_fixture(1000)
   );
   nn::Configure_Result loaded =
      nn::G_Network.configure(Good_Omega_Interaction);
   assert(loaded.ok);
   assert(loaded.message ==
          "Omega NNUE loaded: KingPS104-Omega64-128x2-32 "
          "bounded residual correction from " + Good_Omega_Interaction);

   const Pos original = pos_from_ofen(diagnostic_source());
   int correction = 0;
   bool residual = false;
   assert(nn::G_Network.evaluate(original, correction, residual));
   assert(residual);
   assert(correction == fmt::Omega_Interaction_Residual_Limit_Cp);

   var::set("UseOmegaNNUE", "false");
   var::update();
   const int handcrafted = int(eval(original, White));
   var::set("UseOmegaNNUE", "true");
   var::update();
   assert(int(eval(original, White)) == handcrafted + correction);

   write_bytes(
      Good_Omega_Interaction, make_omega_interaction_fixture(-1000)
   );
   loaded = nn::G_Network.configure(Good_Omega_Interaction);
   assert(loaded.ok);
   assert(nn::G_Network.evaluate(original, correction, residual));
   assert(correction == -fmt::Omega_Interaction_Residual_Limit_Cp);

   // Architecture 3 remains byte-compatible and unbounded by architecture
   // 4's search-safety contract.
   write_bytes(Good_King_State, make_king_state_fixture(1000));
   assert(nn::G_Network.configure(Good_King_State).ok);
   assert(nn::G_Network.evaluate(original, correction, residual));
   assert(correction == 1000);
   write_bytes(Good_King_State, make_king_state_fixture(1));
}

void test_network_stream_helpers() {
   std::string valid = ofen_serialize(pos_from_ofen(diagnostic_source()));
   assert(normalize_stream_ofen(valid));

   std::string crlf = valid + "\r";
   assert(normalize_stream_ofen(crlf));
   assert(crlf == valid);

   std::string empty;
   assert(!normalize_stream_ofen(empty));
   std::string control = valid;
   control.push_back('\t');
   assert(!normalize_stream_ofen(control));
   std::string embedded_nul("valid\0suffix", 12);
   assert(!normalize_stream_ofen(embedded_nul));
   std::string oversized(Max_Stream_OFEN_Bytes + 1U, 'x');
   assert(!normalize_stream_ofen(oversized));

   nn::Runtime_Network residual;
   assert(residual.configure(Good_King_State).ok);
   int correction = 0;
   assert(evaluate_residual_line(residual, valid, correction));
   assert(correction == 1);
   assert(!evaluate_residual_line(
      residual,
      "10/10/10/10/10/10/10/10/10/10[-/-/-/-] w - - 0 1",
      correction
   ));

   nn::Runtime_Network absolute;
   assert(absolute.configure(Good_A).ok);
   assert(!evaluate_residual_line(absolute, valid, correction));

   nn::Runtime_Network unloaded;
   assert(!evaluate_residual_line(unloaded, valid, correction));
}

struct Cleanup {
   ~Cleanup() {
      nn::G_Network.configure("");
      std::remove(Good_A.c_str());
      std::remove(Good_B.c_str());
      std::remove(Good_Residual.c_str());
      std::remove(Good_King_State.c_str());
      std::remove(Good_Omega_Interaction.c_str());
      std::remove(Bad_File.c_str());
      std::remove(Missing_File.c_str());
   }
};

} // namespace

int main(int argc, char * argv[]) {
   if (argc == 3 && std::string(argv[1]) == "--write-fixture") {
      bit::init(Omega);
      write_bytes(argv[2], make_fixture());
      return 0;
   }
   if (argc == 2
    && std::string(argv[1]) == "--evaluate-handcrafted-stream") {
      return evaluate_handcrafted_stream();
   }
   if (argc == 3
    && std::string(argv[1]) == "--evaluate-network-stream") {
      return evaluate_network_stream(argv[2]);
   }
   if (argc == 5 && std::string(argv[1]) == "--dump-features") {
      initialize_omega_runtime();
      const int architecture = std::stoi(argv[2]);
      const int perspective = std::stoi(argv[3]);
      if (perspective < 0 || perspective >= Side_Size) return 4;
      const Pos pos = pos_from_fen(argv[4], Omega);
      std::vector<int> features = sorted_features(
         pos, side_make(perspective), std::uint32_t(architecture)
      );
      for (std::size_t index = 0; index < features.size(); ++index) {
         if (index != 0) std::cout << ',';
         std::cout << features[index];
      }
      std::cout << std::endl;
      return 0;
   }
   if (argc == 4 && std::string(argv[1]) == "--evaluate-network") {
      initialize_omega_runtime();

      nn::Runtime_Network network;
      const nn::Configure_Result loaded = network.configure(argv[2]);
      if (!loaded.ok) {
         std::cerr << loaded.message << std::endl;
         return 3;
      }

      const Pos pos = pos_from_fen(argv[3], Omega);
      int side_to_move_cp = 0;
      if (!network.evaluate(pos, side_to_move_cp)) return 4;
      std::cout << side_to_move_cp << std::endl;
      return 0;
   }
   if (argc != 1) return 2;

   Cleanup cleanup;

   math::init();
   bit::init(Chess);
   hash::init();
   pawn::init();
   pos::init();
   var::init();
   select_variant(Omega);

   test_feature_map();
   test_active_features();
   test_king_state_features();
   test_omega_interaction_features();
   test_symmetry();

   const std::vector<unsigned char> good_a = make_fixture();
   const std::vector<unsigned char> good_b = make_fixture(5);
   test_loader_corruption(good_a);
   test_loading_and_inference(good_a, good_b);
   test_residual_evaluation();
   test_king_state_loading();
   test_omega_interaction_loading_and_clamp();
   test_network_stream_helpers();

   return 0;
}
