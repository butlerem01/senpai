#define DEBUG

#include <cassert>

#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/hash.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"

int main() {
   bit::init(Omega);
   hash::init();
   pawn::init();
   clear_pawn_table();

   Pos pos = pos_from_fen(Omega_Start_OFEN, Omega);
   Score score = eval(pos, White);
   (void)score;

   return 0;
}
