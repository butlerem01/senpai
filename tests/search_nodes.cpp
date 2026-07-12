#define DEBUG

#include <cassert>
#include <limits>

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

   var::set("Threads", "1");
   var::set("UCI_Variant", "omega");
   var::update();
   bit::init(Omega);
   pawn::init();
   clear_pawn_table();
   tt::G_TT.set_size(1 << 12);

   Pos pos = pos_from_fen(Omega_Start_OFEN, Omega);

   Search_Input limited;
   limited.depth = Depth_Max;
   limited.nodes = 257;
   limited.move = true;
   limited.smart = false;

   Search_Output limited_output;
   search(limited_output, pos, limited);

   assert(limited_output.node == limited.nodes);
   assert(limited_output.move != move::None);
   assert(limited_output.move != move::Null);
   assert(move::pseudo_is_legal(limited_output.move, pos));

   Search_Input unlimited;
   assert(unlimited.nodes == 0);
   unlimited.depth = Depth(3);
   unlimited.move = true;
   unlimited.smart = false;

   tt::G_TT.clear();
   Search_Output unlimited_output;
   search(unlimited_output, pos, unlimited);

   assert(unlimited_output.depth == Depth(3));
   assert(unlimited_output.node > 0);

   Search_Input wide;
   wide.nodes = std::numeric_limits<int64>::max();
   assert(wide.nodes > int64(1) << 32);

   return 0;
}
