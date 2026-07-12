#define DEBUG

#include <cassert>
#include <string>

#include "../src/attack.hpp"
#include "../src/bit.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/list.hpp"
#include "../src/move.hpp"
#include "../src/pos.hpp"
#include "../src/sort.hpp"

static Move find_uci(const List & list, const Pos & pos, const std::string & uci) {
   for (int i = 0; i < list.size(); i++) {
      if (move::to_uci(list[i], pos) == uci) return list[i];
   }
   return move::None;
}

static void require_six_promotions(const List & list, const Pos & pos,
                                   const std::string & stem) {
   const char suffix[] { 'q', 'n', 'r', 'b', 'c', 'w' };
   for (char pc : suffix) {
      std::string uci = stem + pc;
      assert(find_uci(list, pos, uci) != move::None);
   }
   assert(find_uci(list, pos, stem) == move::None);
}

int main() {
   bit::init(Omega);
   hash::init();

   Pos white = pos_from_fen(
      "1c3k4/P9/10/10/10/10/10/10/10/5K4[-/-/-/-] w - - 0 1", Omega
   );
   List moves;
   gen_legals(moves, white);
   require_six_promotions(moves, white, "a8a9");
   require_six_promotions(moves, white, "a8b9");

   Move champion = move::from_uci("a8a9c", white);
   assert(find_uci(moves, white, "a8a9c") == champion);
   Pos champion_pos = white.succ(champion);
   assert(champion_pos.is_piece(square_from_string("a9"), Champion));
   assert(champion_pos.count(Pawn, White) == 0);

   Move wizard = move::from_uci("a8b9w", white);
   assert(find_uci(moves, white, "a8b9w") == wizard);
   assert(move_is_safe(wizard, white));
   assert(move_is_safe(move::from_uci("a8b9c", white), white));
   Pos wizard_pos = white.succ(wizard);
   assert(wizard_pos.is_piece(square_from_string("b9"), Wizard));
   assert(wizard_pos.count(Champion, Black) == 0);

   List quiet_promotions;
   const char suffix[] { 'q', 'n', 'r', 'b', 'c', 'w' };
   for (char pc : suffix) {
      quiet_promotions.add(move::from_uci(std::string("a8a9") + pc, white));
   }
   sort_mvv_lva(quiet_promotions, white);
   const char expected[] { 'q', 'r', 'b', 'c', 'w', 'n' };
   for (int i = 0; i < 6; i++) {
      assert(move::to_uci(quiet_promotions[i], white) == std::string("a8a9") + expected[i]);
   }

   Pos black = pos_from_fen(
      "5k4/10/10/10/10/10/10/10/9p/5K4[-/-/-/-] b - - 0 1", Omega
   );
   gen_legals(moves, black);
   require_six_promotions(moves, black, "j1j0");

   Move black_wizard = move::from_uci("j1j0w", black);
   Pos black_wizard_pos = black.succ(black_wizard);
   assert(black_wizard_pos.is_piece(square_from_string("j0"), Wizard));
   assert(black_wizard_pos.fullmove_number() == 2);

   return 0;
}
