#ifndef OMEGA_TABLEBASE_HPP
#define OMEGA_TABLEBASE_HPP

#include <array>
#include <cstdint>
#include <memory>
#include <string>

#include "production_format.hpp"

class Pos;

namespace omega_tb {

// The path option names a directory containing six checked core production
// files (KRK, KCK, KRKC, KRKN, KWKN, and KCKW) plus an optional KCCK file.
// A reload is all-or-nothing, so a bad present file cannot replace a table set
// already serving search.
const char * production_file_name(production_format::Material material);
const char * kcck_dtm_file_name();

struct Probe {
   production_format::Material material { production_format::Material::None };
   production_format::Wdl wdl { production_format::Wdl::Invalid };
   std::uint32_t index { 0 };
   bool has_dtz { false };
   bool has_dtm { false };
   std::uint16_t dtm { 0 };
};

struct Configure_Result {
   bool ok { false };
   bool disabled { false };
   bool retained_previous { false };
   std::string message;
};

class Runtime_Tablebases {
public:
   Runtime_Tablebases();

   Configure_Result configure(const std::string & directory);
   bool probe(const Pos & pos, Probe & result) const;

   bool loaded() const;
   std::string path() const;

private:
   struct Table_Set;
   std::shared_ptr<const Table_Set> p_tables;
};

extern Runtime_Tablebases G_Tablebases;

// Raw probing exposes the exact stored WDL value.  Search currently consumes
// only exact draws: without DTZ, theoretical wins/losses cannot safely claim
// conversion before Omega's automatic 100-ply draw.  This helper also refuses
// native draw, mate, and stalemate positions so a table can never override
// Senpai's terminal rules.
bool probe_search_draw(const Pos & pos, Probe * result = nullptr);

// Exact KCCK W/L is safe for search only when the companion DTM fits in the
// remaining automatic-draw budget. Native draws and terminal positions keep
// precedence. The caller remains responsible for validating any reversible
// pre-root history that is not represented in the tablebase index.
bool probe_search_exact(const Pos & pos, Probe * result = nullptr);

// Exposed for frozen native parity tests and offline diagnostics.  Squares
// use Senpai's native Omega numbering (a1..a10, b1..b10, ..., corners 100..103)
// and turn is role-relative: strong/primary side is zero.
std::uint32_t dense_index(production_format::Material material,
                          const std::array<std::uint8_t, 4> & squares,
                          std::uint8_t turn);

} // namespace omega_tb

#endif
