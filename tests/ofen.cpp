#define DEBUG

#include <cassert>
#include <string>
#include <vector>

#include "../src/bit.hpp"
#include "../src/common.hpp"
#include "../src/fen.hpp"
#include "../src/hash.hpp"
#include "../src/pos.hpp"
#include "../src/util.hpp"

static Square regular_square(char file, char rank) {
   return Square((file - 'a') * Rank_Capacity + (rank - '0'));
}

static Piece_Side at(const Ofen_Position & pos, char file, char rank) {
   return pos.piece_side[regular_square(file, rank)];
}

static void require_piece(const Ofen_Position & pos, Square sq, Piece pc, Side sd) {
   assert(pos.piece_side[sq] == piece_side_make(pc, sd));
}

static void require_bad(const std::string & s) {

   bool rejected = false;

   try {
      (void)ofen_parse(s);
   } catch (const Bad_Input &) {
      rejected = true;
   }

   assert(rejected);
}

int main() {

   // Standard chess remains the default parser and position layout.

   bit::init(Chess);
   hash::init();

   Pos chess = pos_from_fen(Start_FEN);
   assert(chess.turn() == White);
   assert(chess.count(Pawn, White) == 8);
   assert(chess.count(Pawn, Black) == 8);
   assert(chess.count(King, White) == 1);
   assert(chess.count(King, Black) == 1);
   assert(fen_variant_supported(Chess));
   assert(fen_variant_supported(Omega));

   Pos chess_state = pos_from_fen(
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b Kq e3 17 42", Chess
   );
   assert(chess_state.turn() == Black);
   assert(chess_state.has_ep(square_from_string("e3")));
   assert(chess_state.halfmove_clock() == 17);
   assert(chess_state.fullmove_number() == 42);

   // Parsing OFEN does not alter the process-wide geometry.  The parsed data
   // boundary is usable without changing the active native geometry.

   assert(variant() == Chess);
   Ofen_Position initial = ofen_parse(Omega_Start_OFEN);
   assert(variant() == Chess);
   assert(ofen_serialize(initial) == Omega_Start_OFEN);
   assert(initial.turn == White);
   assert(initial.ep_size == 0);
   assert(initial.halfmove_clock == 0);
   assert(initial.fullmove_number == 1);
   assert(initial.king_castling[White]);
   assert(initial.queen_castling[White]);
   assert(initial.king_castling[Black]);
   assert(initial.queen_castling[Black]);

   int pieces = 0;
   for (int sq = 0; sq < Square_Capacity; sq++) {
      if (initial.piece_side[sq] != Empty) pieces++;
   }
   assert(pieces == 44);

   require_piece(initial, regular_square('a', '9'), Champion, Black);
   require_piece(initial, regular_square('j', '9'), Champion, Black);
   require_piece(initial, regular_square('a', '0'), Champion, White);
   require_piece(initial, regular_square('j', '0'), Champion, White);
   require_piece(initial, Square(Regular_Square_Capacity + Corner_SW), Wizard, White);
   require_piece(initial, Square(Regular_Square_Capacity + Corner_SE), Wizard, White);
   require_piece(initial, Square(Regular_Square_Capacity + Corner_NE), Wizard, Black);
   require_piece(initial, Square(Regular_Square_Capacity + Corner_NW), Wizard, Black);

   // All OFEN state survives a round trip, including two EP landing squares.

   const std::string custom {
      "5k4/10/10/10/10/10/10/10/10/5K4[C/W/n/q] b Kq d2,d3 17 42"
   };

   Ofen_Position parsed = ofen_parse(custom);
   assert(ofen_serialize(parsed) == custom);
   assert(parsed.turn == Black);
   assert(parsed.king_castling[White]);
   assert(!parsed.queen_castling[White]);
   assert(!parsed.king_castling[Black]);
   assert(parsed.queen_castling[Black]);
   assert(parsed.ep_size == 2);
   assert(parsed.ep_square[0] == regular_square('d', '2'));
   assert(parsed.ep_square[1] == regular_square('d', '3'));
   assert(parsed.halfmove_clock == 17);
   assert(parsed.fullmove_number == 42);
   require_piece(parsed, Square(Regular_Square_Capacity + Corner_SW), Champion, White);
   require_piece(parsed, Square(Regular_Square_Capacity + Corner_SE), Wizard, White);
   require_piece(parsed, Square(Regular_Square_Capacity + Corner_NE), Knight, Black);
   require_piece(parsed, Square(Regular_Square_Capacity + Corner_NW), Queen, Black);

   const std::string empty { "10/10/10/10/10/10/10/10/10/10[-/-/-/-]" };

   const std::vector<std::string> invalid_placements {
      "10/10/10/10/10/10/10/10/10[W/W/w/w]",
      "11/10/10/10/10/10/10/10/10/10[-/-/-/-]",
      "0/10/10/10/10/10/10/10/10/10[-/-/-/-]",
      "x9/10/10/10/10/10/10/10/10/10[-/-/-/-]",
      "10/10/10/10/10/10/10/10/10/10[X/-/-/-]",
      "10/10/10/10/10/10/10/10/10/10[-/-/-/-",
      "10/10/10/10/10/10/10/10/10/10[-/-/-/-]]",
      "10/10/10/10/10/10/10/10/10/10/[-/-/-/-]",
      "10/10/10/10/10/10/10/10/10/10[-/-/-/-/]"
   };

   for (const std::string & placement : invalid_placements) {
      require_bad(placement + " w - - 0 1");
   }

   const std::vector<std::string> invalid_fields {
      empty + " x - - 0 1",
      empty + " w KK - 0 1",
      empty + " w - w1 0 1",
      empty + " w - a2,b3 0 1",
      empty + " w - a2,a2 0 1",
      empty + " w - a2,a3,a4 0 1",
      empty + " w - - -1 1",
      empty + " w - - 0 0",
      empty + " w - - 0",
      empty + " w - - 0 1 trailing"
   };

   for (const std::string & field : invalid_fields) require_bad(field);

   // Castling order is accepted but serialization is canonical.

   assert(ofen_serialize(ofen_parse(empty + " b qK - 3 9")) == empty + " b Kq - 3 9");

   // Native conversion requires explicitly initialised Omega geometry.  The
   // Native conversion still requires explicitly initialized Omega geometry.

   bool wrong_geometry_rejected = false;
   try {
      (void)pos_from_ofen(initial);
   } catch (const Bad_Input &) {
      wrong_geometry_rejected = true;
   }
   assert(wrong_geometry_rejected);

   bit::init(Omega);
   hash::init();

   bool missing_kings_rejected = false;
   try {
      (void)pos_from_fen(empty + " w - - 0 1", Omega);
   } catch (const Bad_Input &) {
      missing_kings_rejected = true;
   }
   assert(missing_kings_rejected);

   bool extra_king_rejected = false;
   try {
      (void)pos_from_fen(
         "5k4/10/10/10/10/10/10/10/5K4/5K4[-/-/-/-] w - - 0 1", Omega
      );
   } catch (const Bad_Input &) {
      extra_king_rejected = true;
   }
   assert(extra_king_rejected);

   const std::vector<std::string> non_playable_pawns {
      "5k4/10/10/10/10/10/10/10/10/5K4[P/-/-/-] w - - 0 1",
      "P4k4/10/10/10/10/10/10/10/10/5K4[-/-/-/-] w - - 0 1"
   };

   for (const std::string & value : non_playable_pawns) {
      bool rejected = false;
      try {
         (void)pos_from_fen(value, Omega);
      } catch (const Bad_Input &) {
         rejected = true;
      }
      assert(rejected);
   }

   Pos omega = pos_from_fen(Omega_Start_OFEN, Omega);
   assert(omega.turn() == White);
   assert(omega.count(Pawn, White) == 10);
   assert(omega.count(Pawn, Black) == 10);
   assert(omega.count(Champion, White) == 2);
   assert(omega.count(Champion, Black) == 2);
   assert(omega.count(Wizard, White) == 2);
   assert(omega.count(Wizard, Black) == 2);
   assert(omega.is_piece(square_from_string("w1"), Wizard));
   assert(omega.is_piece(square_from_string("w4"), Wizard));
   assert(bit::has(omega.castling_rooks(White), square_from_string("b0")));
   assert(bit::has(omega.castling_rooks(White), square_from_string("i0")));
   assert(bit::has(omega.castling_rooks(Black), square_from_string("b9")));
   assert(bit::has(omega.castling_rooks(Black), square_from_string("i9")));
   assert(omega.ep_squares() == 0);
   assert(omega.ep_sq() == Square_None);
   assert(omega.halfmove_clock() == 0);
   assert(omega.fullmove_number() == 1);

   const std::string native_state {
      "1r3k2r1/10/10/10/10/10/10/10/10/1R3K2R1[-/-/-/-] b Kq d2,d3 17 42"
   };

   Pos state = pos_from_ofen(ofen_parse(native_state));
   assert(state.turn() == Black);
   assert(state.has_ep(square_from_string("d2")));
   assert(state.has_ep(square_from_string("d3")));
   assert(bit::count(state.ep_squares()) == 2);
   assert(state.halfmove_clock() == 17);
   assert(state.ply() == 17);
   assert(state.fullmove_number() == 42);
   assert(bit::has(state.castling_rooks(White), square_from_string("i0")));
   assert(!bit::has(state.castling_rooks(White), square_from_string("b0")));
   assert(bit::has(state.castling_rooks(Black), square_from_string("b9")));
   assert(!bit::has(state.castling_rooks(Black), square_from_string("i9")));

   // Switching the tables back still leaves standard FEN construction intact.

   bit::init(Chess);
   hash::init();
   Pos chess_again = pos_from_fen(Start_FEN, Chess);
   assert(chess_again.count(Pawn, White) == 8);
   assert(chess_again.ep_sq() == Square_None);

   return 0;
}
