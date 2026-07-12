#define DEBUG

#include <cassert>

#include "../src/bit.hpp"
#include "../src/fen.hpp"
#include "../src/game.hpp"
#include "../src/hash.hpp"
#include "../src/move.hpp"
#include "../src/pos.hpp"

int main() {

   // Standard FEN counters and the legacy single EP target remain intact.

   bit::init(Chess);
   hash::init();
   pos::init();

   Pos chess = pos_from_fen("4k3/8/8/8/3p4/8/4P3/4K3 w - - 17 42");
   assert(chess.halfmove_clock() == 17);
   assert(chess.fullmove_number() == 42);
   assert(chess.ply() == 17);

   Game game;
   game.init(chess);
   game.add_move(move::make(square_from_string("e2"), square_from_string("e4")));

   assert(game.halfmove_clock() == 0);
   assert(game.fullmove_number() == 42);
   assert(bit::count(game.ep_squares()) == 1);
   assert(game.pos().has_ep(square_from_string("e3")));
   assert(game.pos().ep_sq() == square_from_string("e3"));
   assert(game.pos().key() == hash::key(game.pos()));

   game.add_move(move::make(square_from_string("e8"), square_from_string("e7")));
   assert(game.halfmove_clock() == 1);
   assert(game.fullmove_number() == 43);
   assert(game.ep_squares() == 0);
   assert(game.pos().key() == hash::key(game.pos()));

   // A three-square Omega pawn move can expose both crossed landing squares.

   bit::init(Omega);
   hash::init();

   const std::string omega_fen {
      "5k4/10/10/10/10/2p7/2p7/10/3P6/5K4[-/-/-/-] w - - 12 7"
   };

   Pos omega = pos_from_fen(omega_fen, Omega);
   assert(omega.halfmove_clock() == 12);
   assert(omega.fullmove_number() == 7);

   Square d1 = square_from_string("d1");
   Square d2 = square_from_string("d2");
   Square d3 = square_from_string("d3");
   Square d4 = square_from_string("d4");

   Pos advanced = omega.succ(move::make(d1, d4));

   assert(advanced.turn() == Black);
   assert(advanced.halfmove_clock() == 0);
   assert(advanced.fullmove_number() == 7);
   assert(bit::count(advanced.ep_squares()) == 2);
   assert(advanced.has_ep(d2));
   assert(advanced.has_ep(d3));
   assert(advanced.ep_sq() == d2); // stable legacy first-target view
   assert(advanced.key() == hash::key(advanced));
   assert(hash::key_en_passant(d2) != hash::key_en_passant(d3));

   // Either adjacent black pawn can capture the same three-step pawn, from
   // the near or far crossed square.

   Pos far_capture = advanced.succ(move::make(square_from_string("c3"), d2, Pawn));
   assert(far_capture.is_piece(d2, Pawn));
   assert(far_capture.is_empty(d4));
   assert(far_capture.cap_sq() == d4);
   assert(far_capture.cap_to() == d2);
   assert(far_capture.halfmove_clock() == 0);
   assert(far_capture.fullmove_number() == 8);
   assert(far_capture.key() == hash::key(far_capture));

   Pos near_capture = advanced.succ(move::make(square_from_string("c4"), d3, Pawn));
   assert(near_capture.is_piece(d3, Pawn));
   assert(near_capture.is_empty(d4));
   assert(near_capture.cap_sq() == d4);
   assert(near_capture.cap_to() == d3);
   assert(near_capture.halfmove_clock() == 0);
   assert(near_capture.fullmove_number() == 8);
   assert(near_capture.key() == hash::key(near_capture));

   game.init(advanced);
   game.add_move(move::make(square_from_string("f9"), square_from_string("f8")));

   assert(game.halfmove_clock() == 1);
   assert(game.fullmove_number() == 8);
   assert(game.ep_squares() == 0);
   assert(game.pos().key() == hash::key(game.pos()));

   // Search null moves clear transient EP state but are not game moves, so the
   // FEN fullmove number is deliberately unchanged.

   Pos null_pos = advanced.null();
   assert(null_pos.ep_squares() == 0);
   assert(null_pos.halfmove_clock() == 1);
   assert(null_pos.fullmove_number() == 7);
   assert(null_pos.key() == hash::key(null_pos));

   return 0;
}
