#define DEBUG

#include <array>
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <string>

#include "../src/attack.hpp"
#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/list.hpp"
#include "../src/math.hpp"
#include "../src/move.hpp"
#include "../src/omega_tablebase.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"
#include "../src/score.hpp"
#include "../src/search.hpp"
#include "../src/tt.hpp"
#include "../src/var.hpp"

namespace {

namespace fmt = omega_tb::production_format;

Pos make(const std::string & ofen) {
   Pos pos = pos_from_fen(ofen, Omega);
   assert(is_legal(pos));
   return pos;
}

bool legal_has(const List & legal, Move move) {
   for (int index = 0; index < legal.size(); ++index) {
      if (legal[index] == move) return true;
   }
   return false;
}

Line require_exact(const Pos & root, int expected_dtm, bool expected_win) {
   Line pv;
   Score sc = score::None;
   assert(omega_dtm_search::root_line(root, pv, sc));
   assert(pv.size() == expected_dtm);
   assert(score::ply(sc) == expected_dtm);
   assert(expected_win ? score::is_win(sc) : score::is_loss(sc));

   omega_tb::Probe probe;
   assert(omega_tb::G_Tablebases.probe(root, probe));
   assert(probe.material == fmt::Material::KCCK);
   assert(probe.has_dtm);
   assert(probe.dtm == expected_dtm);
   assert(probe.wdl == (expected_win ? fmt::Wdl::Win : fmt::Wdl::Loss));

   std::array<Pos, Ply_Size + 1> position;
   position[0] = root;

   for (int ply = 0; ply < pv.size(); ++ply) {
      List legal;
      gen_legals(legal, position[ply]);
      assert(legal_has(legal, pv[ply]));

      position[ply + 1] = position[ply].succ(pv[ply]);

      omega_tb::Probe child;
      assert(omega_tb::G_Tablebases.probe(position[ply + 1], child));
      assert(child.has_dtm);
      assert(int(child.dtm) == expected_dtm - ply - 1);
      assert(
         (probe.wdl == fmt::Wdl::Win && child.wdl == fmt::Wdl::Loss)
       || (probe.wdl == fmt::Wdl::Loss && child.wdl == fmt::Wdl::Win)
      );
      probe = child;
   }

   assert(probe.dtm == 0);
   assert(probe.wdl == fmt::Wdl::Loss);
   assert(is_mate(position[pv.size()]));
   return pv;
}

void require_fallback(const Pos & pos) {
   Line pv;
   Score sc = Score(123);
   assert(!omega_dtm_search::root_line(pos, pv, sc));
   assert(pv.size() == 0);
   assert(sc == score::None);
}

} // namespace

int main() {
   const char * configured_path = std::getenv("OMEGA_FULL_TABLEBASE_PATH");
   if (configured_path == nullptr || *configured_path == '\0') {
      std::cout << "KCCK DTM search tests skipped: "
                   "OMEGA_FULL_TABLEBASE_PATH is not set\n";
      return 0;
   }

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
   pos::init();
   tt::G_TT.set_size(1 << 12);

   assert(score::is_win(score::win(score::Mate_Ply_Max)));
   assert(score::is_loss(score::loss(score::Mate_Ply_Max)));
   assert(score::ply(score::win(score::Mate_Ply_Max))
          == int(score::Mate_Ply_Max));

   const omega_tb::Configure_Result loaded =
      omega_tb::G_Tablebases.configure(configured_path);
   assert(loaded.ok);
   assert(loaded.message.find("KCCK+DTM") != std::string::npos);

   const Pos dtm_1 = make(
      "10/10/10/10/C9/10/10/k9/1C8/K9[-/-/-/-] w - - 0 1"
   );
   const Pos dtm_9 = make(
      "10/10/10/4C5/10/1k3C4/10/10/10/K9[-/-/-/-] w - - 0 1"
   );
   const Pos dtm_39 = make(
      "10/10/10/6k3/10/10/10/10/10/K9[C/-/C/-] w - - 0 1"
   );
   const Pos dtm_40_h60 = make(
      "10/10/10/10/5k4/10/10/10/10/K9[C/-/C/-] b - - 60 1"
   );

   const Line mate_1 = require_exact(dtm_1, 1, true);
   assert(move::to_uci(mate_1[0], dtm_1) == "a5a4");

   const Line mate_9 = require_exact(dtm_9, 9, true);
   assert(move::to_uci(mate_9[0], dtm_9) == "f4d4");

   const Line mate_39 = require_exact(dtm_39, 39, true);
   assert(move::to_uci(mate_39[0], dtm_39) == "w1b1");

   const Line mate_40 = require_exact(dtm_40_h60, 40, false);
   assert(move::to_uci(mate_40[0], dtm_40_h60) == "f5g6");

   // Equality at the automatic-draw boundary remains a mate. One clock tick
   // beyond it must fall back to ordinary search.
   const Pos dtm_40_h61 = make(
      "10/10/10/10/5k4/10/10/10/10/K9[C/-/C/-] b - - 61 1"
   );
   require_fallback(dtm_40_h61);

   const Pos dtm_39_h61 = make(
      "10/10/10/6k3/10/10/10/10/10/K9[C/-/C/-] w - - 61 1"
   );
   require_exact(dtm_39_h61, 39, true);

   const Pos dtm_39_h62 = make(
      "10/10/10/6k3/10/10/10/10/10/K9[C/-/C/-] w - - 62 1"
   );
   require_fallback(dtm_39_h62);

   const Pos dtm_1_h99 = make(
      "10/10/10/10/C9/10/10/k9/1C8/K9[-/-/-/-] w - - 99 1"
   );
   require_exact(dtm_1_h99, 1, true);

   // A line already followed in the actual game remains eligible at the next
   // engine turn when its known history is a strict DTM descent.
   std::array<Pos, 3> retained;
   retained[0] = dtm_39;
   retained[1] = retained[0].succ(mate_39[0]);
   retained[2] = retained[1].succ(mate_39[1]);
   assert(retained[2].known_reversible_plies() == 2);
   require_exact(retained[2], 37, true);

   // Find a legal attacker deviation that does not descend from DTM 9. Its
   // current position is still theoretically exact in isolation, but the
   // retained real-game history cannot be certified repetition-safe.
   omega_tb::Probe root_probe;
   assert(omega_tb::G_Tablebases.probe(dtm_9, root_probe));
   List alternatives;
   gen_legals(alternatives, dtm_9);
   bool found_unsafe_history = false;
   for (int index = 0; index < alternatives.size(); ++index) {
      std::array<Pos, 2> chain;
      chain[0] = dtm_9;
      chain[1] = chain[0].succ(alternatives[index]);
      omega_tb::Probe child;
      if (omega_tb::G_Tablebases.probe(chain[1], child)
       && child.has_dtm
       && child.wdl == fmt::Wdl::Loss
       && child.dtm >= root_probe.dtm) {
         omega_tb::Probe isolated;
         assert(omega_tb::probe_search_exact(chain[1], &isolated));
         require_fallback(chain[1]);
         found_unsafe_history = true;
         break;
      }
   }
   assert(found_unsafe_history);

   const Pos checkmate = make(
      "10/10/10/10/10/10/10/10/KC8/C9[k/-/-/-] b - - 0 1"
   );
   omega_tb::Probe terminal;
   assert(omega_tb::G_Tablebases.probe(checkmate, terminal));
   assert(terminal.has_dtm && terminal.dtm == 0);
   assert(is_mate(checkmate));
   require_fallback(checkmate);

   const Pos stalemate = make(
      "10/10/10/10/10/10/10/C9/K9/C9[k/-/-/-] b - - 0 1"
   );
   assert(is_stalemate(stalemate));
   require_fallback(stalemate);

   const Pos capture_draw = make(
      "10/10/10/10/10/10/10/K9/C9/kC8[-/-/-/-] b - - 0 1"
   );
   omega_tb::Probe draw_probe;
   assert(omega_tb::G_Tablebases.probe(capture_draw, draw_probe));
   assert(draw_probe.wdl == fmt::Wdl::Draw);
   assert(!draw_probe.has_dtm);
   require_fallback(capture_draw);

   omega_tb::G_Tablebases.configure("");
   require_fallback(dtm_9);

   // Loading the Omega files does not alter standard-chess search or produce
   // an Omega exact line in the standard geometry.
   assert(omega_tb::G_Tablebases.configure(configured_path).ok);
   var::set("UCI_Variant", "chess");
   var::update();
   bit::init(Chess);
   pawn::init();
   clear_pawn_table();
   pos::init();
   Pos chess = pos_from_fen(Start_FEN, Chess);
   require_fallback(chess);

   Search_Input input;
   input.depth = Depth(1);
   input.move = true;
   input.smart = false;
   Search_Output output;
   search(output, chess, input);
   assert(output.move != move::None);
   assert(move::pseudo_is_legal(output.move, chess));
   assert(!score::is_win_loss(output.score));

   std::cout << "KCCK DTM search, PV, clock, history, and fallback tests passed\n";
   return 0;
}
