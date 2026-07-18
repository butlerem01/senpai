#define DEBUG

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <direct.h>
#include <map>
#include <string>
#include <vector>

#include "../src/attack.hpp"
#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/math.hpp"
#include "../src/omega_tablebase.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"
#include "../src/production_format.hpp"
#include "../src/var.hpp"

namespace fmt = omega_tb::production_format;

namespace {

const std::string Fixture_Directory { "omega-tablebase-runtime-fixture" };

std::string fixture_path(fmt::Material material) {
   return Fixture_Directory + "/" + omega_tb::production_file_name(material);
}

Pos make(const std::string & ofen) {
   return pos_from_fen(ofen, Omega);
}

std::vector<std::uint8_t> draw_payload(fmt::Material material) {
   std::vector<std::uint8_t> payload(
      static_cast<std::size_t>(fmt::state_count(material)),
      static_cast<std::uint8_t>(fmt::Wdl::Invalid)
   );
   std::fill(
      payload.end() - static_cast<std::ptrdiff_t>(fmt::legal_count(material)),
      payload.end(),
      static_cast<std::uint8_t>(fmt::Wdl::Draw)
   );
   return payload;
}

void set_outcomes(
   std::vector<std::uint8_t> & payload,
   const std::map<std::uint32_t, fmt::Wdl> & outcomes
) {
   for (const auto & requested : outcomes) {
      assert(requested.first < payload.size());
      if (payload[requested.first] == static_cast<std::uint8_t>(fmt::Wdl::Invalid)) {
         auto replacement = std::find(
            payload.rbegin(), payload.rend(), static_cast<std::uint8_t>(fmt::Wdl::Draw)
         );
         assert(replacement != payload.rend());
         *replacement = static_cast<std::uint8_t>(fmt::Wdl::Invalid);
         payload[requested.first] = static_cast<std::uint8_t>(fmt::Wdl::Draw);
      }
      payload[requested.first] = static_cast<std::uint8_t>(requested.second);
   }
}

void write_fixture(
   fmt::Material material,
   const std::map<std::uint32_t, fmt::Wdl> & outcomes
) {
   std::vector<std::uint8_t> payload = draw_payload(material);
   set_outcomes(payload, outcomes);
   std::string error;
   assert(fmt::write_file(fixture_path(material), material, payload, {}, error));
   assert(error.empty());
}

struct Cleanup {
   ~Cleanup() {
      omega_tb::G_Tablebases.configure("");
      const fmt::Material materials[] {
         fmt::Material::KRK, fmt::Material::KCK, fmt::Material::KRKC,
         fmt::Material::KRKN, fmt::Material::KWKN, fmt::Material::KCKW,
         fmt::Material::KCCK,
      };
      for (fmt::Material material : materials) {
         std::remove(fixture_path(material).c_str());
         std::remove((fixture_path(material) + ".tmp").c_str());
      }
      _rmdir(Fixture_Directory.c_str());
   }
};

} // namespace

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

   // Native Wizard geometry must agree square-for-square with the independent
   // coordinate rule used by the standalone KWKN solver.  This includes all
   // four detached corners, whose coordinates are (-1,-1), (10,-1),
   // (10,10), and (-1,10).
   for (int from_value = 0; from_value < square_size(); ++from_value) {
      const Square from = square_make(from_value);
      Bit expected = Bit(0);
      for (int to_value = 0; to_value < square_size(); ++to_value) {
         const Square to = square_make(to_value);
         const int df = std::abs(int(square_file(from)) - int(square_file(to)));
         const int dr = std::abs(int(square_rank(from)) - int(square_rank(to)));
         if ((df == 1 && dr == 1) || (df == 1 && dr == 3) ||
             (df == 3 && dr == 1)) {
            bit::set(expected, to);
         }
      }
      assert(bit::piece_attacks(Wizard, from) == expected);
   }
   assert(bit::piece_attacks(Wizard, square_make(100)) ==
          (bit::bit(square_make(0)) | bit::bit(square_make(2)) |
           bit::bit(square_make(20))));
   assert(bit::piece_attacks(Wizard, square_make(101)) ==
          (bit::bit(square_make(70)) | bit::bit(square_make(90)) |
           bit::bit(square_make(92))));
   assert(bit::piece_attacks(Wizard, square_make(102)) ==
          (bit::bit(square_make(79)) | bit::bit(square_make(97)) |
           bit::bit(square_make(99))));
   assert(bit::piece_attacks(Wizard, square_make(103)) ==
          (bit::bit(square_make(7)) | bit::bit(square_make(9)) |
           bit::bit(square_make(29))));

   // Frozen D4 parity case from the retained four-man proof:
   // Kw2/Rj6 versus Ke5/Ch4, rook side to move.
   const std::array<std::uint8_t, 4> reported {{ 101, 96, 45, 74 }};
   assert(omega_tb::dense_index(fmt::Material::KRKC, reported, 0) == 26750996);
   assert(omega_tb::dense_index(fmt::Material::KRKC, reported, 1) == 26750997);

   const Pos krk_draw = make(
      "10/10/10/9R/4k5/10/10/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos krk_win = make(
      "10/10/9R/10/4k5/10/10/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos kck_draw = make(
      "10/10/10/10/4k5/7C2/10/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos krkc_draw = make(
      "10/10/10/9R/4k5/7c2/10/10/10/10[-/K/-/-] w - - 0 1"
   );
   const Pos krkn_draw = make(
      "10/10/10/9R/4k5/3n6/10/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos kwkn_draw = make(
      "10/10/10/9W/4k5/3n6/10/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos kwkn_win = make(
      "10/10/10/10/10/W9/10/10/K9/n9[k/-/-/-] w - - 0 1"
   );
   const Pos kwkn_swapped_draw = make(
      "10/10/10/9w/4K5/3N6/10/1k8/10/10[-/-/-/-] b - - 0 1"
   );
   const Pos kwkn_same_side = make(
      "10/10/10/9W/4k5/3N6/10/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos kckw_draw = make(
      "10/10/10/9C/4k5/3w6/10/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos kckw_win = make(
      "10/10/10/w9/10/10/10/10/K9/C9[k/-/-/-] w - - 0 1"
   );
   const Pos kckw_loss = make(
      "w9/10/10/10/10/10/10/10/K9/C9[k/-/-/-] b - - 0 1"
   );
   const Pos kckw_swapped_draw = make(
      "10/10/10/9c/4K5/3W6/10/1k8/10/10[-/-/-/-] b - - 0 1"
   );
   const Pos kckw_same_side = make(
      "10/10/10/9C/4k5/10/3W6/1K8/10/10[-/-/-/-] w - - 0 1"
   );
   const Pos kcck_draw = make(
      "10/10/k9/10/10/10/10/10/1C8/K9[-/-/-/C] w - - 0 1"
   );
   const Pos kcck_swapped_draw = make(
      "10/10/K9/10/10/10/10/10/1c8/k9[-/-/-/c] b - - 0 1"
   );
   const Pos kcck_split_sides = make(
      "10/10/k9/10/10/10/10/10/1C8/K9[-/-/-/c] w - - 0 1"
   );
   const Pos kcck_win = make(
      "10/10/10/10/10/10/3C6/2k7/1C8/K9[-/-/-/-] w - - 0 1"
   );
   const Pos kcck_stalemate = make(
      "10/10/10/10/10/10/10/C9/K9/C9[k/-/-/-] b - - 0 1"
   );
   const Pos krk_stalemate = make(
      "R9/1K8/10/10/10/10/10/10/10/10[-/-/-/k] b - - 0 1"
   );
   const Pos krk_clock_draw = make(
      "10/10/10/9R/4k5/10/10/1K8/10/10[-/-/-/-] w - - 100 1"
   );

   assert(is_legal(krk_draw));
   assert(is_legal(krk_win));
   assert(is_legal(kck_draw));
   assert(is_legal(krkc_draw));
   assert(is_legal(krkn_draw));
   assert(is_legal(kwkn_draw));
   assert(is_legal(kwkn_win));
   assert(is_legal(kwkn_swapped_draw));
   assert(is_legal(kwkn_same_side));
   assert(is_legal(kckw_draw));
   assert(is_legal(kckw_win));
   assert(is_legal(kckw_loss));
   assert(is_legal(kckw_swapped_draw));
   assert(is_legal(kckw_same_side));
   assert(is_legal(kcck_draw));
   assert(is_legal(kcck_swapped_draw));
   assert(is_legal(kcck_split_sides));
   assert(is_legal(kcck_win));
   assert(is_legal(kcck_stalemate));
   assert(is_stalemate(kcck_stalemate));
   assert(is_legal(krk_stalemate));
   assert(is_stalemate(krk_stalemate));
   assert(!krk_stalemate.is_draw());
   assert(krk_clock_draw.is_draw());

   omega_tb::Probe probe;
   assert(!omega_tb::G_Tablebases.probe(krk_draw, probe));

   // Derive the fixture slots with the same public role normalisation used by
   // runtime probing.  The reported KRKC position exercises a detached corner.
   const std::uint32_t krk_draw_index = omega_tb::dense_index(
      fmt::Material::KRK, {{ 12, 96, 45, 0 }}, 0
   );
   const std::uint32_t krk_win_index = omega_tb::dense_index(
      fmt::Material::KRK, {{ 12, 97, 45, 0 }}, 0
   );
   const std::uint32_t kck_draw_index = omega_tb::dense_index(
      fmt::Material::KCK, {{ 12, 74, 45, 0 }}, 0
   );
   const std::uint32_t krk_stalemate_index = omega_tb::dense_index(
      fmt::Material::KRK, {{ 18, 9, 103, 0 }}, 1
   );
   const std::uint32_t krkn_draw_index = omega_tb::dense_index(
      fmt::Material::KRKN, {{ 12, 96, 45, 34 }}, 0
   );
   const std::uint32_t kwkn_draw_index = omega_tb::dense_index(
      fmt::Material::KWKN, {{ 12, 96, 45, 34 }}, 0
   );
   const std::uint32_t kwkn_win_index = omega_tb::dense_index(
      fmt::Material::KWKN, {{ 1, 4, 100, 0 }}, 0
   );
   assert(kwkn_win_index == 1143704);
   const std::uint32_t kckw_draw_index = omega_tb::dense_index(
      fmt::Material::KCKW, {{ 12, 96, 45, 34 }}, 0
   );
   const std::uint32_t kckw_win_index = omega_tb::dense_index(
      fmt::Material::KCKW, {{ 1, 0, 100, 6 }}, 0
   );
   const std::uint32_t kckw_loss_index = omega_tb::dense_index(
      fmt::Material::KCKW, {{ 1, 0, 100, 9 }}, 1
   );
   assert(kckw_win_index == 1081900);
   assert(kckw_loss_index == 1081907);
   const std::uint32_t kcck_draw_index = omega_tb::dense_index(
      fmt::Material::KCCK, {{ 0, 11, 7, 103 }}, 0
   );
   const std::uint32_t kcck_win_index = omega_tb::dense_index(
      fmt::Material::KCCK, {{ 0, 11, 22, 33 }}, 0
   );
   const std::uint32_t kcck_stalemate_index = omega_tb::dense_index(
      fmt::Material::KCCK, {{ 1, 0, 100, 2 }}, 1
   );
   assert(kcck_draw_index == 2512);
   assert(kcck_win_index == 0);
   assert(kcck_stalemate_index == 1081893);

   assert(_mkdir(Fixture_Directory.c_str()) == 0);
   Cleanup cleanup;
   write_fixture(fmt::Material::KRK, {
      { krk_draw_index, fmt::Wdl::Draw },
      { krk_win_index, fmt::Wdl::Win },
      { krk_stalemate_index, fmt::Wdl::Draw },
   });
   write_fixture(fmt::Material::KCK, {
      { kck_draw_index, fmt::Wdl::Draw },
   });
   write_fixture(fmt::Material::KRKC, {
      { 26750996, fmt::Wdl::Draw },
   });
   write_fixture(fmt::Material::KRKN, {
      { krkn_draw_index, fmt::Wdl::Draw },
   });
   write_fixture(fmt::Material::KWKN, {
      { kwkn_draw_index, fmt::Wdl::Draw },
      { kwkn_win_index, fmt::Wdl::Win },
   });
   write_fixture(fmt::Material::KCKW, {
      { kckw_draw_index, fmt::Wdl::Draw },
      { kckw_win_index, fmt::Wdl::Win },
      { kckw_loss_index, fmt::Wdl::Loss },
   });
   write_fixture(fmt::Material::KCCK, {
      { kcck_draw_index, fmt::Wdl::Draw },
      { kcck_win_index, fmt::Wdl::Win },
      { kcck_stalemate_index, fmt::Wdl::Draw },
   });

   const omega_tb::Configure_Result loaded =
      omega_tb::G_Tablebases.configure(Fixture_Directory);
   assert(loaded.ok && !loaded.disabled && !loaded.retained_previous);
   assert(loaded.message.find(", KCCK") != std::string::npos);
   assert(omega_tb::G_Tablebases.loaded());
   assert(omega_tb::G_Tablebases.path() == Fixture_Directory);

   assert(omega_tb::G_Tablebases.probe(krk_draw, probe));
   assert(probe.material == fmt::Material::KRK);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == krk_draw_index);
   assert(!probe.has_dtz);
   assert(omega_tb::probe_search_draw(krk_draw));

   // The theoretical win is visible to diagnostics, but WDL-only search must
   // not claim it under the automatic 100-ply rule.
   assert(omega_tb::G_Tablebases.probe(krk_win, probe));
   assert(probe.wdl == fmt::Wdl::Win);
   assert(!omega_tb::probe_search_draw(krk_win));

   assert(omega_tb::G_Tablebases.probe(kck_draw, probe));
   assert(probe.material == fmt::Material::KCK);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(kck_draw.is_draw());
   assert(!omega_tb::probe_search_draw(kck_draw));

   assert(omega_tb::G_Tablebases.probe(krkc_draw, probe));
   assert(probe.material == fmt::Material::KRKC);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == 26750996);
   assert(omega_tb::probe_search_draw(krkc_draw));

   assert(omega_tb::G_Tablebases.probe(krkn_draw, probe));
   assert(probe.material == fmt::Material::KRKN);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == krkn_draw_index);
   assert(omega_tb::probe_search_draw(krkn_draw));

   assert(omega_tb::G_Tablebases.probe(kwkn_draw, probe));
   assert(probe.material == fmt::Material::KWKN);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == kwkn_draw_index);
   assert(omega_tb::probe_search_draw(kwkn_draw));

   assert(omega_tb::G_Tablebases.probe(kwkn_swapped_draw, probe));
   assert(probe.material == fmt::Material::KWKN);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == kwkn_draw_index);
   assert(omega_tb::probe_search_draw(kwkn_swapped_draw));
   assert(!omega_tb::G_Tablebases.probe(kwkn_same_side, probe));

   // The exact table's decisive states remain diagnostic-only until DTZ and
   // 100-ply conversion semantics are available.
   assert(omega_tb::G_Tablebases.probe(kwkn_win, probe));
   assert(probe.material == fmt::Material::KWKN);
   assert(probe.wdl == fmt::Wdl::Win);
   assert(probe.index == kwkn_win_index);
   assert(!omega_tb::probe_search_draw(kwkn_win));

   assert(omega_tb::G_Tablebases.probe(kckw_draw, probe));
   assert(probe.material == fmt::Material::KCKW);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == kckw_draw_index);
   assert(omega_tb::probe_search_draw(kckw_draw));

   assert(omega_tb::G_Tablebases.probe(kckw_swapped_draw, probe));
   assert(probe.material == fmt::Material::KCKW);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == kckw_draw_index);
   assert(omega_tb::probe_search_draw(kckw_swapped_draw));
   assert(!omega_tb::G_Tablebases.probe(kckw_same_side, probe));

   // KCKW decisive records are likewise visible only to diagnostics. Search
   // ignores both W and L until a draw-rule-safe DTZ policy exists.
   assert(omega_tb::G_Tablebases.probe(kckw_win, probe));
   assert(probe.material == fmt::Material::KCKW);
   assert(probe.wdl == fmt::Wdl::Win);
   assert(probe.index == kckw_win_index);
   assert(!omega_tb::probe_search_draw(kckw_win));
   assert(omega_tb::G_Tablebases.probe(kckw_loss, probe));
   assert(probe.material == fmt::Material::KCKW);
   assert(probe.wdl == fmt::Wdl::Loss);
   assert(probe.index == kckw_loss_index);
   assert(!omega_tb::probe_search_draw(kckw_loss));

   // The optional seventh table recognizes same-side two-Champion material
   // for either colour. Exact draws are search-safe; decisive WDL remains
   // diagnostic-only until its DTM and remaining rule budget are integrated.
   assert(omega_tb::G_Tablebases.probe(kcck_draw, probe));
   assert(probe.material == fmt::Material::KCCK);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == kcck_draw_index);
   assert(omega_tb::probe_search_draw(kcck_draw));
   assert(omega_tb::G_Tablebases.probe(kcck_swapped_draw, probe));
   assert(probe.material == fmt::Material::KCCK);
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == kcck_draw_index);
   assert(omega_tb::probe_search_draw(kcck_swapped_draw));
   assert(!omega_tb::G_Tablebases.probe(kcck_split_sides, probe));
   assert(omega_tb::G_Tablebases.probe(kcck_win, probe));
   assert(probe.material == fmt::Material::KCCK);
   assert(probe.wdl == fmt::Wdl::Win);
   assert(probe.index == kcck_win_index);
   assert(!omega_tb::probe_search_draw(kcck_win));
   assert(omega_tb::G_Tablebases.probe(kcck_stalemate, probe));
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(probe.index == kcck_stalemate_index);
   assert(!omega_tb::probe_search_draw(kcck_stalemate));

   // Terminal native rules precede the table: stalemate and the current
   // halfmove-clock draw gate are not replaced by a production WDL result.
   assert(omega_tb::G_Tablebases.probe(krk_stalemate, probe));
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(!omega_tb::probe_search_draw(krk_stalemate));
   assert(omega_tb::G_Tablebases.probe(krk_clock_draw, probe));
   assert(!omega_tb::probe_search_draw(krk_clock_draw));

   // KCCK is optional for compatibility with existing six-table directories.
   // The mandatory KCKW table and the rest of the core remain available when
   // the optional file is absent.
   assert(std::remove(fixture_path(fmt::Material::KCCK).c_str()) == 0);
   const omega_tb::Configure_Result without_kcck =
      omega_tb::G_Tablebases.configure(Fixture_Directory);
   assert(without_kcck.ok && !without_kcck.retained_previous);
   assert(without_kcck.message.find(", KCCK") == std::string::npos);
   assert(!omega_tb::G_Tablebases.probe(kcck_draw, probe));
   assert(omega_tb::G_Tablebases.probe(kckw_draw, probe));

   // A failed reload keeps the fully validated table set currently visible to
   // search threads, and disabling is explicit and atomic.
   assert(std::remove(fixture_path(fmt::Material::KCKW).c_str()) == 0);
   const omega_tb::Configure_Result missing_kckw =
      omega_tb::G_Tablebases.configure(Fixture_Directory);
   assert(!missing_kckw.ok && missing_kckw.retained_previous);
   assert(missing_kckw.message.find("omega-kckw-wdl-v1.omtb") != std::string::npos);
   assert(omega_tb::G_Tablebases.probe(kckw_draw, probe));

   const omega_tb::Configure_Result failed =
      omega_tb::G_Tablebases.configure("omega-tablebase-directory-does-not-exist");
   assert(!failed.ok && failed.retained_previous);
   assert(omega_tb::G_Tablebases.path() == Fixture_Directory);
   assert(omega_tb::G_Tablebases.probe(krkc_draw, probe));

   const omega_tb::Configure_Result disabled = omega_tb::G_Tablebases.configure("");
   assert(disabled.ok && disabled.disabled);
   assert(!omega_tb::G_Tablebases.loaded());
   assert(!omega_tb::G_Tablebases.probe(krk_draw, probe));

   // Release gating can point this test at a converted full seven-table set.
   // The real KCKW and KCCK artifacts must retain their frozen witnesses,
   // while only exact draws are consumable by search.
   const char * full_path = std::getenv("OMEGA_FULL_TABLEBASE_PATH");
   if (full_path != nullptr && full_path[0] != '\0') {
      const omega_tb::Configure_Result full = omega_tb::G_Tablebases.configure(full_path);
      assert(full.ok && !full.disabled && !full.retained_previous);
      assert(omega_tb::G_Tablebases.probe(kckw_draw, probe));
      assert(probe.material == fmt::Material::KCKW);
      assert(probe.index == kckw_draw_index);
      assert(probe.wdl == fmt::Wdl::Draw);
      assert(omega_tb::probe_search_draw(kckw_draw));
      assert(omega_tb::G_Tablebases.probe(kckw_win, probe));
      assert(probe.index == 1081900);
      assert(probe.wdl == fmt::Wdl::Win);
      assert(!omega_tb::probe_search_draw(kckw_win));
      assert(omega_tb::G_Tablebases.probe(kckw_loss, probe));
      assert(probe.index == 1081907);
      assert(probe.wdl == fmt::Wdl::Loss);
      assert(!omega_tb::probe_search_draw(kckw_loss));
      assert(omega_tb::G_Tablebases.probe(kcck_draw, probe));
      assert(probe.material == fmt::Material::KCCK);
      assert(probe.index == 2512);
      assert(probe.wdl == fmt::Wdl::Draw);
      assert(omega_tb::probe_search_draw(kcck_draw));
      assert(omega_tb::G_Tablebases.probe(kcck_win, probe));
      assert(probe.material == fmt::Material::KCCK);
      assert(probe.index == 0);
      assert(probe.wdl == fmt::Wdl::Win);
      assert(!omega_tb::probe_search_draw(kcck_win));
      assert(omega_tb::G_Tablebases.probe(kcck_stalemate, probe));
      assert(probe.index == 1081893);
      assert(probe.wdl == fmt::Wdl::Draw);
      assert(!omega_tb::probe_search_draw(kcck_stalemate));
   }

   return 0;
}
