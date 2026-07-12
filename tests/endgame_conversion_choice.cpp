#define DEBUG

#include <cassert>
#include <iostream>

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
   var::set("Hash", "128");
   var::set("UCI_Variant", "omega");
   var::update();
   bit::init(Omega);
   pawn::init();
   clear_pawn_table();

   // Match the reference probe: 128 MiB, one thread, one million nodes.
   tt::G_TT.set_size(int64(128) << (20 - 4));

   // Historical no-table control after 90.Rxg6 ...Kf8.  The frozen combined
   // evaluator chooses Wh1 at this node budget.  Production KRKN data later
   // showed that Wh1 does not preserve R+W versus C+N: Black can force a
   // Champion-for-Wizard trade after either candidate move.  Keep this test as
   // an A/B control for the unloaded-table behavior, not as a claim that Wh1
   // wins or should be forced when exact tables are active.
   Pos choice = pos_from_fen(
      "10/5k4/10/6R3/10/6Wc2/7n2/10/9K/10[-/-/-/-] w - - 1 92",
      Omega
   );

   Search_Input input;
   input.depth = Depth_Max;
   input.nodes = 1000000;
   input.move = true;
   input.smart = false;

   Search_Output output;
   search(output, choice, input);

   Move expected = move::from_uci("g4h1", choice);
   if (output.move != expected) {
      std::cerr << "expected g4h1, got "
                << move::to_uci(output.move, choice)
                << " at depth " << int(output.depth)
                << " score " << int(output.score)
                << " nodes " << output.node << '\n';
   }

   assert(output.node == input.nodes);
   assert(output.move == expected);
   return 0;
}
