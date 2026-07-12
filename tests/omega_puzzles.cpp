#define DEBUG

#include <cassert>
#include <iostream>

#include "../src/attack.hpp"
#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/list.hpp"
#include "../src/math.hpp"
#include "../src/move.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"
#include "../src/score.hpp"
#include "../src/search.hpp"
#include "../src/tt.hpp"
#include "../src/var.hpp"

static void put(Ofen_Position & pos, Piece pc, Side sd, const char * coordinate) {
   Square sq = square_from_string(coordinate);
   assert(pos.piece_side[sq] == Empty);
   pos.piece_side[sq] = piece_side_make(pc, sd);
}

static Search_Output solve(const Ofen_Position & source, int depth) {
   Pos pos = pos_from_ofen(source);
   Search_Input input;
   input.depth = Depth(depth);
   input.move = true;
   input.smart = false;

   Search_Output output;
   tt::G_TT.clear();
   search(output, pos, input);
   assert(output.move != move::None);
   assert(output.move != move::Null);
   return output;
}

static bool forces_mate(const Pos & pos, Side attacker, int plies) {
   List moves;
   gen_legals(moves, pos);

   if (moves.size() == 0) return in_check(pos) && pos.turn() != attacker;
   if (plies == 0) return false;

   if (pos.turn() == attacker) {
      for (int i = 0; i < moves.size(); i++) {
         if (forces_mate(pos.succ(moves[i]), attacker, plies - 1)) return true;
      }
      return false;
   }

   for (int i = 0; i < moves.size(); i++) {
      if (!forces_mate(pos.succ(moves[i]), attacker, plies - 1)) return false;
   }
   return true;
}

static List mating_first_moves(const Pos & pos, int plies) {
   List result;
   List moves;
   gen_legals(moves, pos);
   for (int i = 0; i < moves.size(); i++) {
      if (forces_mate(pos.succ(moves[i]), pos.turn(), plies - 1)) result.add(moves[i]);
   }
   return result;
}

static void require_mating_choice(const List & mates, Move chosen, const Pos & pos) {
   if (!list::has(mates, chosen)) {
      std::cerr << "chosen " << move::to_uci(chosen, pos) << "; mating:";
      for (int i = 0; i < mates.size(); i++) {
         std::cerr << ' ' << move::to_uci(mates[i], pos);
      }
      std::cerr << '\n';
   }
   assert(list::has(mates, chosen));
}

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
   tt::G_TT.set_size(1 << 14);

   // Official Omega Chess puzzle #1: White to move and mate in two.
   Ofen_Position first;
   first.turn = White;
   put(first, King, White, "c4");
   put(first, Wizard, White, "e3");
   put(first, Rook, White, "i4");
   put(first, King, Black, "a4");
   put(first, Pawn, Black, "b4");
   Pos first_pos = pos_from_ofen(first);
   List first_mates = mating_first_moves(first_pos, 3);
   assert(first_mates.size() != 0);
   Search_Output first_result = solve(first, 3);
   require_mating_choice(first_mates, first_result.move, first_pos);

   // Official puzzle #3: White to move and mate in two.
   Ofen_Position third;
   third.turn = White;
   put(third, King, White, "c1");
   put(third, Bishop, White, "a6");
   put(third, Bishop, White, "d6");
   put(third, Champion, White, "b5");
   put(third, King, Black, "a4");
   Pos third_pos = pos_from_ofen(third);
   List third_mates = mating_first_moves(third_pos, 3);
   assert(third_mates.size() != 0);
   Search_Output third_result = solve(third, 3);
   require_mating_choice(third_mates, third_result.move, third_pos);

   return 0;
}
