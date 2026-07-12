#define DEBUG

#include <cassert>
#include <string>

#include "../src/bit.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/list.hpp"
#include "../src/move.hpp"
#include "../src/pos.hpp"
#include "../src/var.hpp"

static const std::string Omega_Castling {
   "1r3k2r1/10/10/10/10/10/10/10/10/1R3K2R1[-/-/-/-]"
};

static Move find_uci(const List & list, const Pos & pos, const std::string & uci) {
   for (int i = 0; i < list.size(); i++) {
      if (move::to_uci(list[i], pos) == uci) return list[i];
   }
   return move::None;
}

static List legals(const Pos & pos) {
   List list;
   gen_legals(list, pos);
   return list;
}

static void test_omega_geometry() {

   bit::init(Omega);
   hash::init();
   var::Chess_960 = false;

   Pos white = pos_from_fen(Omega_Castling + " w KQkq - 0 1", Omega);
   List moves = legals(white);

   Move king_side = find_uci(moves, white, "f0h0");
   Move queen_side = find_uci(moves, white, "f0d0");

   assert(king_side != move::None);
   assert(queen_side != move::None);

   assert(move::is_castling(king_side));
   assert(move::from(king_side) == square_from_string("f0"));
   assert(move::to(king_side) == square_from_string("i0"));
   assert(move::castling_king_to(king_side) == square_from_string("h0"));
   assert(move::castling_rook_to(king_side) == square_from_string("g0"));

   Move parsed_king = move::from_uci("f0h0", white);
   assert(parsed_king == king_side);
   assert(move::to_uci(parsed_king, white) == "f0h0");

   Pos after_king = white.succ(parsed_king);
   assert(after_king.is_piece(square_from_string("h0"), King));
   assert(after_king.is_piece(square_from_string("g0"), Rook));
   assert(after_king.is_empty(square_from_string("f0")));
   assert(after_king.is_empty(square_from_string("i0")));
   assert(after_king.castling_rooks(White) == 0);

   assert(move::is_castling(queen_side));
   assert(move::to(queen_side) == square_from_string("b0"));
   assert(move::castling_king_to(queen_side) == square_from_string("d0"));
   assert(move::castling_rook_to(queen_side) == square_from_string("e0"));

   Move parsed_queen = move::from_uci("f0d0", white);
   assert(parsed_queen == queen_side);

   Pos after_queen = white.succ(parsed_queen);
   assert(after_queen.is_piece(square_from_string("d0"), King));
   assert(after_queen.is_piece(square_from_string("e0"), Rook));
   assert(after_queen.is_empty(square_from_string("f0")));
   assert(after_queen.is_empty(square_from_string("b0")));

   Pos black = pos_from_fen(Omega_Castling + " b KQkq - 0 1", Omega);
   List black_moves = legals(black);
   Move black_king = find_uci(black_moves, black, "f9h9");
   Move black_queen = find_uci(black_moves, black, "f9d9");

   assert(black_king != move::None);
   assert(black_queen != move::None);
   assert(move::to(black_king) == square_from_string("i9"));
   assert(move::castling_rook_to(black_king) == square_from_string("g9"));
   assert(move::to(black_queen) == square_from_string("b9"));
   assert(move::castling_rook_to(black_queen) == square_from_string("e9"));

   Pos after_black = black.succ(black_queen);
   assert(after_black.is_piece(square_from_string("d9"), King));
   assert(after_black.is_piece(square_from_string("e9"), Rook));
   assert(after_black.fullmove_number() == 2);
}

static void test_omega_legality() {

   // A rook attacks the king-side transit square g0.
   Pos transit = pos_from_fen(
      "5kr3/10/10/10/10/10/10/10/10/1R3K2R1[-/-/-/-] w KQ - 0 1", Omega
   );
   List moves = legals(transit);
   assert(find_uci(moves, transit, "f0h0") == move::None);
   assert(find_uci(moves, transit, "f0d0") != move::None);

   // Pawn diagonals count as attacks even when the attacked square is empty.
   Pos pawn = pos_from_fen(
      "5k4/10/10/10/10/10/10/10/7p2/1R3K2R1[-/-/-/-] w KQ - 0 1", Omega
   );
   moves = legals(pawn);
   assert(find_uci(moves, pawn, "f0h0") == move::None);
   assert(find_uci(moves, pawn, "f0d0") != move::None);

   // The final king square is checked separately from the transit square.
   Pos destination = pos_from_fen(
      "5k1r2/10/10/10/10/10/10/10/10/1R3K2R1[-/-/-/-] w KQ - 0 1", Omega
   );
   moves = legals(destination);
   assert(find_uci(moves, destination, "f0h0") == move::None);
   assert(find_uci(moves, destination, "f0d0") != move::None);

   // A king in check cannot castle.
   Pos checked = pos_from_fen(
      "k4r4/10/10/10/10/10/10/10/10/1R3K2R1[-/-/-/-] w KQ - 0 1", Omega
   );
   moves = legals(checked);
   assert(find_uci(moves, checked, "f0h0") == move::None);
   assert(find_uci(moves, checked, "f0d0") == move::None);

   // Omega rights are only usable by a king on its f-file home square.
   Pos displaced = pos_from_fen(
      "5k4/10/10/10/10/10/10/10/10/1R2K3R1[-/-/-/-] w KQ - 0 1", Omega
   );
   moves = legals(displaced);
   assert(find_uci(moves, displaced, "e0h0") == move::None);
   Move ordinary_king_move = find_uci(moves, displaced, "e0d0");
   assert(ordinary_king_move != move::None);
   assert(!move::is_castling(ordinary_king_move));

   // c0 blocks the queen-side rook path, but not king-side castling.
   Pos blocked = pos_from_fen(
      "5k4/10/10/10/10/10/10/10/10/1RN2K2R1[-/-/-/-] w KQ - 0 1", Omega
   );
   moves = legals(blocked);
   assert(find_uci(moves, blocked, "f0d0") == move::None);
   assert(find_uci(moves, blocked, "f0h0") != move::None);
}

static void test_standard_regression() {

   bit::init(Chess);
   hash::init();
   var::Chess_960 = false;

   Pos pos = pos_from_fen("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", Chess);
   List moves = legals(pos);

   Move king_side = find_uci(moves, pos, "e1g1");
   Move queen_side = find_uci(moves, pos, "e1c1");

   assert(king_side != move::None);
   assert(queen_side != move::None);
   assert(move::to(king_side) == square_from_string("h1"));
   assert(move::castling_king_to(king_side) == square_from_string("g1"));
   assert(move::castling_rook_to(king_side) == square_from_string("f1"));
   assert(move::to(queen_side) == square_from_string("a1"));
   assert(move::castling_king_to(queen_side) == square_from_string("c1"));
   assert(move::castling_rook_to(queen_side) == square_from_string("d1"));

   Pos after = pos.succ(move::from_uci("e1g1", pos));
   assert(after.is_piece(square_from_string("g1"), King));
   assert(after.is_piece(square_from_string("f1"), Rook));
}

int main() {
   test_omega_geometry();
   test_omega_legality();
   test_standard_regression();
   return 0;
}
