#define DEBUG

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
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

   const omega_tb::Configure_Result loaded =
      omega_tb::G_Tablebases.configure(Fixture_Directory);
   assert(loaded.ok && !loaded.disabled && !loaded.retained_previous);
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

   // Terminal native rules precede the table: stalemate and the current
   // halfmove-clock draw gate are not replaced by a production WDL result.
   assert(omega_tb::G_Tablebases.probe(krk_stalemate, probe));
   assert(probe.wdl == fmt::Wdl::Draw);
   assert(!omega_tb::probe_search_draw(krk_stalemate));
   assert(omega_tb::G_Tablebases.probe(krk_clock_draw, probe));
   assert(!omega_tb::probe_search_draw(krk_clock_draw));

   // A failed reload keeps the fully validated table set currently visible to
   // search threads, and disabling is explicit and atomic.
   const omega_tb::Configure_Result failed =
      omega_tb::G_Tablebases.configure("omega-tablebase-directory-does-not-exist");
   assert(!failed.ok && failed.retained_previous);
   assert(omega_tb::G_Tablebases.path() == Fixture_Directory);
   assert(omega_tb::G_Tablebases.probe(krkc_draw, probe));

   const omega_tb::Configure_Result disabled = omega_tb::G_Tablebases.configure("");
   assert(disabled.ok && disabled.disabled);
   assert(!omega_tb::G_Tablebases.loaded());
   assert(!omega_tb::G_Tablebases.probe(krk_draw, probe));

   return 0;
}
