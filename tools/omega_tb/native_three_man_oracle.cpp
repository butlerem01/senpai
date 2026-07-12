#define DEBUG

#include <algorithm>
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

enum Role_Turn : int { Strong_To_Move = 0, Weak_To_Move = 1 };

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
   int strong_king;
   int role_piece;
   int weak_king;
   int role_turn;

   while (std::cin >> material >> strong_king >> role_piece >> weak_king >> role_turn) {
      if ((material != "krk" && material != "kck")
       || !square_ok(strong_king)
       || !square_ok(role_piece)
       || !square_ok(weak_king)
       || strong_king == role_piece
       || strong_king == weak_king
       || role_piece == weak_king
       || (role_turn != Strong_To_Move && role_turn != Weak_To_Move)) {
         write_error();
         continue;
      }

      Ofen_Position ofen;
      ofen.turn = role_turn == Strong_To_Move ? White : Black;
      ofen.piece_side[strong_king] = piece_side_make(King, White);
      ofen.piece_side[role_piece] = piece_side_make(
         material == "krk" ? Rook : Champion, White
      );
      ofen.piece_side[weak_king] = piece_side_make(King, Black);

      Pos pos = pos_from_ofen(ofen);
      bool legal = is_legal(pos);
      bool check = in_check(pos);
      bool rules_draw = legal && pos.is_draw();
      bool capture_draw = false;
      std::set<std::array<int, 4>> successors;

      if (legal) {
         List moves;
         gen_legals(moves, pos);
         Piece piece = material == "krk" ? Rook : Champion;

         for (int index = 0; index < moves.size(); index++) {
            Pos child = pos.succ(moves[index]);
            if (child.pieces(piece, White) == 0) {
               // The bare king captured the role piece, leaving K versus K.
               capture_draw = true;
               continue;
            }

            std::array<int, 4> state {{
               int(child.king(White)),
               int(bit::first(child.pieces(piece, White))),
               int(child.king(Black)),
               child.turn() == White ? Strong_To_Move : Weak_To_Move,
            }};
            successors.insert(state);
         }
      }

      std::cout << int(legal) << ' '
                << int(check) << ' '
                << int(rules_draw) << ' '
                << int(capture_draw) << ' '
                << successors.size();
      for (const auto & state : successors) {
         std::cout << ' ' << state[0] << ',' << state[1] << ','
                   << state[2] << ',' << state[3];
      }
      std::cout << std::endl;
   }

   return 0;
}
