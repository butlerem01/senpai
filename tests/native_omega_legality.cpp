#define DEBUG

#include <cassert>

#include "../src/attack.hpp"
#include "../src/bit.hpp"
#include "../src/fen.hpp"
#include "../src/hash.hpp"
#include "../src/move.hpp"
#include "../src/pos.hpp"

int main() {
   bit::init(Omega);
   hash::init();

   // A Champion's two-square orthogonal component is a jump: a1 does not
   // block the attack from a0 to a2.
   Pos champion = pos_from_fen(
      "10/10/10/10/10/10/10/k9/P9/C8K[-/-/-/-] w - - 0 1",
      Omega
   );
   Square a2 = square_from_string("a2");
   assert(has_attack(champion, White, a2));
   assert(bit::has(attacks_to(champion, White, a2, champion.pieces()),
                   square_from_string("a0")));

   // Capturing on the far target of a three-square pawn move removes the pawn
   // two ranks behind the target.  That removal exposes the black king to the
   // rook, so c3d2 is not legal.
   Pos far_ep = pos_from_fen(
      "10/10/10/10/10/k2P5R/2p7/10/10/9K[-/-/-/-] b - d2 0 1",
      Omega
   );
   Move ep = move::from_uci("c3d2", far_ep);
   assert(move::is_en_passant(ep));
   assert(move::en_passant_capture_square(ep, far_ep) == square_from_string("d4"));
   assert(!move::pseudo_is_legal(ep, far_ep));

   return 0;
}
