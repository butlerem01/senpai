#define DEBUG

#include <cassert>

#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/hash.hpp"
#include "../src/math.hpp"
#include "../src/move.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"
#include "../src/search.hpp"
#include "../src/tt.hpp"
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
   clear_pawn_table();
   tt::G_TT.set_size(1 << 12);

   Pos pos = pos_from_fen(Omega_Start_OFEN, Omega);

   Search_Input input;
   input.depth = Depth(5);
   input.move = true;
   input.smart = false;

   Search_Output output;
   search(output, pos, input);

   assert(output.move != move::None);
   assert(output.move != move::Null);
   assert(move::pseudo_is_legal(output.move, pos));
   assert(output.depth == Depth(5));

   return 0;
}
