#include <array>
#include <iostream>
#include <set>
#include <string>

#include "../../src/attack.hpp"
#include "../../src/bit.hpp"
#include "../../src/common.hpp"
#include "../../src/fen.hpp"
#include "../../src/gen.hpp"
#include "../../src/hash.hpp"
#include "../../src/list.hpp"
#include "../../src/move.hpp"
#include "../../src/pos.hpp"

namespace {

enum Role_Turn : int { Attacker_To_Move = 0, Defender_To_Move = 1 };

bool square_ok(int square) {
   return square >= 0 && square < Square_Capacity;
}

void write_error() {
   std::cout << "error" << std::endl;
}

} // namespace

int main() {
   bit::init(Omega);
   hash::init();
   pos::init();

   std::string material;
   int attacker_king;
   int champion_a;
   int defender_king;
   int champion_b;
   int role_turn;

   while (std::cin >> material >> attacker_king >> champion_a
                   >> defender_king >> champion_b >> role_turn) {
      if (material != "kcck"
       || !square_ok(attacker_king)
       || !square_ok(champion_a)
       || !square_ok(defender_king)
       || !square_ok(champion_b)
       || attacker_king == champion_a
       || attacker_king == defender_king
       || attacker_king == champion_b
       || champion_a == defender_king
       || champion_a == champion_b
       || defender_king == champion_b
       || (role_turn != Attacker_To_Move && role_turn != Defender_To_Move)) {
         write_error();
         continue;
      }

      Ofen_Position ofen;
      ofen.turn = role_turn == Attacker_To_Move ? White : Black;
      ofen.piece_side[attacker_king] = piece_side_make(King, White);
      ofen.piece_side[champion_a] = piece_side_make(Champion, White);
      ofen.piece_side[defender_king] = piece_side_make(King, Black);
      ofen.piece_side[champion_b] = piece_side_make(Champion, White);

      Pos position = pos_from_ofen(ofen);
      const bool legal = is_legal(position);
      const bool check = legal && in_check(position);
      const bool root_draw = legal && position.is_draw();
      unsigned capture_mask = 0;
      unsigned capture_draw_mask = 0;
      bool internal_error = false;
      std::set<std::array<int, 5>> successors;

      if (legal) {
         List moves;
         gen_legals(moves, position);

         for (int index = 0; index < moves.size(); ++index) {
            const Move mv = moves[index];
            const int from = int(move::from(mv));
            const int to = int(move::to(mv));
            const Pos child = position.succ(mv);

            if (!is_legal(child)
             || child.count(King, White) != 1
             || child.count(King, Black) != 1) {
               internal_error = true;
               break;
            }

            if (child.count(Champion, White) == 1) {
               unsigned captured = 0;
               if (position.turn() == Black && from == defender_king && to == champion_a) {
                  captured = 1;
               } else if (position.turn() == Black
                       && from == defender_king && to == champion_b) {
                  captured = 2;
               } else {
                  internal_error = true;
                  break;
               }
               capture_mask |= captured;
               if (child.is_draw()) capture_draw_mask |= captured;
               continue;
            }

            if (child.count(Champion, White) != 2) {
               internal_error = true;
               break;
            }

            int child_attacker_king = attacker_king;
            int child_champion_a = champion_a;
            int child_defender_king = defender_king;
            int child_champion_b = champion_b;

            if (position.turn() == White && from == attacker_king) {
               child_attacker_king = to;
            } else if (position.turn() == White && from == champion_a) {
               child_champion_a = to;
            } else if (position.turn() == White && from == champion_b) {
               child_champion_b = to;
            } else if (position.turn() == Black && from == defender_king) {
               child_defender_king = to;
            } else {
               internal_error = true;
               break;
            }

            successors.insert({{
               child_attacker_king,
               child_champion_a,
               child_defender_king,
               child_champion_b,
               child.turn() == White ? Attacker_To_Move : Defender_To_Move,
            }});
         }
      }

      if (internal_error) {
         write_error();
         continue;
      }

      std::cout << int(legal) << ' '
                << int(check) << ' '
                << int(root_draw) << ' '
                << capture_mask << ' '
                << capture_draw_mask << ' '
                << successors.size();
      for (const auto & state : successors) {
         std::cout << ' ' << state[0] << ',' << state[1] << ','
                   << state[2] << ',' << state[3] << ',' << state[4];
      }
      std::cout << std::endl;
   }

   return 0;
}
