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

static Move find_uci(const List & list, const Pos & pos, const std::string & uci) {
   for (int i = 0; i < list.size(); i++) {
      if (move::to_uci(list[i], pos) == uci) return list[i];
   }
   return move::None;
}

static void test_champion_leaps() {

   // Tactical generation must not treat a Champion's two-square orthogonal
   // jump as a sliding move blocked by a1.
   Pos capture = pos_from_fen(
      "5k4/10/10/10/10/10/10/n9/P9/C4K4[-/-/-/-] w - - 0 1", Omega
   );
   List moves;
   gen_captures(moves, capture);
   assert(find_uci(moves, capture, "a0a2") != move::None);

   // The Champion jumps over d1 to e2 and checks through e3 to e4.
   Pos checking = pos_from_fen(
      "10/10/10/10/10/4k5/4p5/10/3P6/2C2K4[-/-/-/-] w - - 0 1", Omega
   );
   moves.clear();
   add_checks(moves, checking);
   assert(find_uci(moves, checking, "c0e2") != move::None);

   // The same leap must be available as an evasion capture over a3.
   Pos evasion = pos_from_fen(
      "5k4/10/10/10/10/r6K2/P9/C9/10/10[-/-/-/-] w - - 0 1", Omega
   );
   gen_legals(moves, evasion);
   assert(find_uci(moves, evasion, "a2a4") != move::None);

   // A Champion's two-square check is a jump, so a1 is not an interposition
   // square between the Champion on a2 and king on a0.
   Pos no_interpose = pos_from_fen(
      "5k4/10/10/10/10/10/10/c9/1R8/K9[-/-/-/-] w - - 0 1", Omega
   );
   gen_legals(moves, no_interpose);
   assert(find_uci(moves, no_interpose, "b1a1") == move::None);
}

static void test_far_en_passant_probes() {

   // c3-d2 is the far landing square of a white d1-d4 triple move.  Removing
   // the actual pawn on d4 exposes the black king on h4 to the white rook.
   Pos illegal = pos_from_fen(
      "10/10/10/10/10/R2P3k2/2p7/10/10/5K4[-/-/-/-] b - d2 0 1", Omega
   );
   Move far = move::from_uci("c3d2", illegal);
   assert(move::is_en_passant(far));
   assert(move::en_passant_capture_square(far, illegal) == square_from_string("d4"));
   assert(!move::pseudo_is_legal(far, illegal));

   List moves;
   gen_legals(moves, illegal);
   assert(find_uci(moves, illegal, "c3d2") == move::None);

   // The same removal can expose a discovered check by the capturing side.
   Pos checking = pos_from_fen(
      "5k4/10/10/10/10/r2P3K2/2p7/10/10/10[-/-/-/-] b - d2 0 1", Omega
   );
   far = move::from_uci("c3d2", checking);
   assert(move::en_passant_capture_square(far, checking) == square_from_string("d4"));
   assert(move::pseudo_is_legal(far, checking));
   assert(move::is_check(far, checking));

   Pos after = checking.succ(far);
   assert(after.is_piece(square_from_string("d2"), Pawn));
   assert(after.is_empty(square_from_string("d4")));
   assert(after.cap_sq() == square_from_string("d4"));
}

static void test_standard_en_passant_regression() {

   bit::init(Chess);
   hash::init();

   Pos pos = pos_from_fen("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2", Chess);
   Move ep = move::from_uci("e5d6", pos);
   assert(move::is_en_passant(ep));
   assert(move::en_passant_capture_square(ep, pos) == square_from_string("d5"));
   assert(move::pseudo_is_legal(ep, pos));

   Pos after = pos.succ(ep);
   assert(after.is_piece(square_from_string("d6"), Pawn));
   assert(after.is_empty(square_from_string("d5")));
}

int main() {
   bit::init(Omega);
   hash::init();
   var::Chess_960 = false;

   test_champion_leaps();
   test_far_en_passant_probes();
   test_standard_en_passant_regression();
   return 0;
}
