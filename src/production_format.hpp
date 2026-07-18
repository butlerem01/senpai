#ifndef PRODUCTION_FORMAT_HPP
#define PRODUCTION_FORMAT_HPP

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

// Production tablebase container reader/writer. Runtime probing uses its
// checked read-only boundary; the offline writer mirrors
// tools/omega_tb/production_format.py.
namespace omega_tb {
namespace production_format {

enum class Material : std::uint8_t {
   None = 0,
   KRK = 1,
   KCK = 2,
   KRKC = 3,
   KRKN = 4,
   KWKN = 5,
   KCKW = 6,
   KCCK = 7,
};

enum class Wdl : std::uint8_t {
   Invalid = 0,
   Loss = 1,
   Blessed_Loss = 2,
   Draw = 3,
   Cursed_Win = 4,
   Win = 5,
};

struct Metadata {
   Material material { Material::None };
   std::uint64_t state_count { 0 };
   std::uint64_t legal_count { 0 };
   std::array<std::uint64_t, 6> outcome_counts {{ 0, 0, 0, 0, 0, 0 }};
   bool has_dtz { false };
   std::array<std::uint8_t, 32> rules_fingerprint {{}};
   std::array<std::uint8_t, 32> payload_sha256 {{}};
   std::array<std::uint8_t, 32> header_sha256 {{}};
};

struct Table {
   Metadata metadata;
   std::vector<std::uint8_t> wdl;
   std::vector<std::uint16_t> dtz;
};

struct Load_Requirements {
   Material material { Material::None };
   bool require_dtz { false };
   std::array<std::uint8_t, 32> rules_fingerprint {{}};
};

// KCCK DTM is deliberately a separate companion, not a DTZ payload in the
// WDL container.  UINT16_MAX is reserved for draw/invalid records; all
// decisive records contain an exact number of plies to checkmate.
constexpr std::uint16_t Dtm_No_Distance = UINT16_MAX;

struct Dtm_Metadata {
   Material material { Material::None };
   std::uint64_t state_count { 0 };
   std::uint64_t decisive_count { 0 };
   std::uint16_t maximum_dtm { 0 };
   std::array<std::uint8_t, 32> production_wdl_payload_sha256 {{}};
   std::array<std::uint8_t, 32> source_wdl_payload_sha256 {{}};
   std::array<std::uint8_t, 32> source_rules_sha256 {{}};
   std::array<std::uint8_t, 32> source_capture_policy_sha256 {{}};
   std::array<std::uint8_t, 32> source_dtm_payload_sha256 {{}};
   std::array<std::uint8_t, 32> source_dtm_container_sha256 {{}};
   std::array<std::uint8_t, 32> payload_sha256 {{}};
   std::array<std::uint8_t, 32> header_sha256 {{}};
};

struct Dtm_Table {
   Dtm_Metadata metadata;
   std::vector<std::uint16_t> dtm;
};

const char * rules_description();
const char * rules_description(Material material);
std::array<std::uint8_t, 32> canonical_rules_fingerprint();
std::array<std::uint8_t, 32> canonical_rules_fingerprint(Material material);
Load_Requirements canonical_requirements(Material material, bool require_dtz = false);

std::uint64_t state_count(Material material);
std::uint64_t legal_count(Material material);

// The writer is intended for offline generation and test fixtures.  It
// derives all semantic metadata and counts from the payload, writes through a
// temporary file, re-opens it with the production reader, then renames it.
bool write_file(const std::string & path,
                Material material,
                const std::vector<std::uint8_t> & wdl,
                const std::vector<std::uint16_t> & dtz,
                std::string & error);

// The reader validates magic/version, fixed semantic metadata, rules and
// index identity, both SHA-256 checksums, offsets/sizes, WDL codes and counts,
// and the current KCK insufficient-material policy.  On failure `table` is
// left unchanged.
bool read_file(const std::string & path,
               const Load_Requirements & requirements,
               Table & table,
               std::string & error);

// Write/read the explicit KCCK uint16 DTM companion.  The companion binds to
// the exact production WDL payload and to the frozen solver-source hashes.
// Reading also verifies the decisive/sentinel map against `wdl`.  On failure
// the output table is left unchanged.
bool write_dtm_file(const std::string & path,
                    const Table & wdl,
                    const std::vector<std::uint16_t> & dtm,
                    std::string & error);

bool read_dtm_file(const std::string & path,
                   const Table & wdl,
                   Dtm_Table & table,
                   std::string & error);

} // namespace production_format
} // namespace omega_tb

#endif
