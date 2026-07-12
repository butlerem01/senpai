#ifndef OMEGA_TABLEBASE_HPP
#define OMEGA_TABLEBASE_HPP

#include <array>
#include <cstdint>
#include <memory>
#include <string>

#include "production_format.hpp"

class Pos;

namespace omega_tb {

// The path option names a directory containing these three checked production
// files.  A reload is all-or-nothing so a bad or incomplete directory cannot
// replace a table set which is already serving search threads.
const char * production_file_name(production_format::Material material);

struct Probe {
   production_format::Material material { production_format::Material::None };
   production_format::Wdl wdl { production_format::Wdl::Invalid };
   std::uint32_t index { 0 };
   bool has_dtz { false };
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

// Exposed for frozen native parity tests and offline diagnostics.  Squares
// use Senpai's native Omega numbering (a1..a10, b1..b10, ..., corners 100..103)
// and turn is role-relative: strong/rook side is zero.
std::uint32_t dense_index(production_format::Material material,
                          const std::array<std::uint8_t, 4> & squares,
                          std::uint8_t turn);

} // namespace omega_tb

#endif
