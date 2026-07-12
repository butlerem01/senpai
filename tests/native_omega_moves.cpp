#define DEBUG

#include <cassert>
#include <string>
#include <iostream>

#include "../src/bit.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/list.hpp"
#include "../src/move.hpp"
#include "../src/pos.hpp"

static bool has_move(const List & list, const Pos & pos, const std::string & text) {
   for (int i = 0; i < list.size(); i++)
      if (move::to_uci(list[i], pos) == text) return true;
   return false;
}

static uint64 perft(const Pos & pos, int depth) {
   if (depth == 0) return 1;

   List moves;
   gen_legals(moves, pos);

   uint64 nodes = 0;
   for (int i = 0; i < moves.size(); i++) nodes += perft(pos.succ(moves[i]), depth - 1);
   return nodes;
}

int main() {
   bit::init(Omega);
   hash::init();
   pos::init();

   Pos pos = pos_from_fen(Omega_Start_OFEN, Omega);
   List moves;
   gen_legals(moves, pos);

   if (moves.size() != 40) {
      std::cerr << "initial legal moves: " << moves.size() << std::endl;
      for (int i = 0; i < moves.size(); i++) std::cerr << move::to_uci(moves[i], pos) << ' ';
      std::cerr << std::endl;
   }
   assert(moves.size() == 40);
   assert(has_move(moves, pos, "a1a4"));
   assert(has_move(moves, pos, "a0a2"));
   assert(has_move(moves, pos, "a0c2"));
   assert(has_move(moves, pos, "w1a2"));
   assert(!has_move(moves, pos, "w1a0"));

   // No opening move reaches or blocks Black's home pieces, so every one of
   // White's 40 legal moves has the same 40 Black replies.
   assert(perft(pos, 1) == 40);
   assert(perft(pos, 2) == 1600);
   assert(perft(pos, 3) == 67202);

   return 0;
}
