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

   // Reported game after 90.Rxg6 ...Kf8.  The material-preserving Wh1 keeps
   // R+W versus C+N.  Wf5 permits ...Nf4, ...Ch3+, Rxh3, Nxh3 and liquidates
   // into the practically drawn W-versus-N ending.  Endgame knowledge must
   // not make that premature liquidation more attractive.
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
