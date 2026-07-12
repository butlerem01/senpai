#define DEBUG

#include <cassert>

#include "../src/bit.hpp"
#include "../src/fen.hpp"
#include "../src/hash.hpp"
#include "../src/math.hpp"
#include "../src/move.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"
#include "../src/search.hpp"
#include "../src/var.hpp"

int main() {
   math::init();
   bit::init(Chess);
   hash::init();
   pawn::init();
   pos::init();
   var::init();

   var::set("UCI_Variant", "omega");
   var::update();
   bit::init(Omega);
   pawn::init();

   Pos start = pos_from_fen(Omega_Start_OFEN, Omega);
   assert(omega_is_development(move::from_uci("c0d2", start), start));
   assert(omega_is_development(move::from_uci("a0c2", start), start));
   assert(omega_is_development(move::from_uci("w1a2", start), start));
   assert(!omega_is_development(move::from_uci("f1f2", start), start));

   Pos open_board = pos_from_fen(
      "crnbqkbnrc/10/10/10/10/10/10/10/10/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1",
      Omega
   );
   assert(omega_is_development(move::from_uci("d0c1", open_board), open_board));

   Pos home_rank = pos_from_fen(
      "crnbqkbnrc/10/10/10/10/10/10/10/10/CR1BQKBNRC[W/W/w/w] w KQkq - 0 1",
      Omega
   );
   assert(!omega_is_development(move::from_uci("a0c0", home_rank), home_rank));

   Pos tactical = pos_from_fen(
      "crnbqkbnrc/10/10/10/10/10/10/3p6/10/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1",
      Omega
   );
   assert(!omega_is_development(move::from_uci("c0d2", tactical), tactical));

   Pos castle = pos_from_fen(
      "crnbqkbnrc/10/10/10/10/10/10/10/10/CRNBQK2RC[W/W/w/w] w KQkq - 0 1",
      Omega
   );
   assert(omega_is_development(move::from_uci("f0h0", castle), castle));

   Pos phase_boundary = pos_from_fen(
      "crnb1kbnrc/10/10/10/10/10/10/10/10/CRNB1KBNRC[W/W/w/w] w KQkq - 0 1",
      Omega
   );
   assert(pos::phase(phase_boundary) == 0.25);
   assert(omega_is_development(move::from_uci("c0d2", phase_boundary), phase_boundary));

   Pos sparse = pos_from_fen(
      "5k4/10/10/10/10/10/10/10/10/2N2K4[-/-/-/-] w - - 0 1",
      Omega
   );
   assert(!omega_is_development(move::from_uci("c0d2", sparse), sparse));

   var::set("UCI_Variant", "chess");
   var::update();
   bit::init(Chess);
   pawn::init();
   Pos chess = pos_from_fen(Start_FEN, Chess);
   assert(!omega_is_development(move::from_uci("b1c3", chess), chess));

   return 0;
}
