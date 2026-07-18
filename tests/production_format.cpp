#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include "production_format.hpp"

namespace fmt = omega_tb::production_format;

namespace {

std::string hex(const std::array<std::uint8_t, 32> & bytes) {
   std::ostringstream out;
   out << std::hex << std::setfill('0');
   for (std::uint8_t byte : bytes) out << std::setw(2) << unsigned(byte);
   return out.str();
}

std::vector<std::uint8_t> krk_payload() {
   std::vector<std::uint8_t> result(
      static_cast<std::size_t>(fmt::state_count(fmt::Material::KRK)),
      static_cast<std::uint8_t>(fmt::Wdl::Invalid));
   std::size_t offset = result.size() - static_cast<std::size_t>(fmt::legal_count(fmt::Material::KRK));
   std::fill_n(result.begin() + offset, 339, static_cast<std::uint8_t>(fmt::Wdl::Loss));
   offset += 339;
   std::fill_n(result.begin() + offset, 232962, static_cast<std::uint8_t>(fmt::Wdl::Draw));
   offset += 232962;
   std::fill_n(result.begin() + offset, 1732, static_cast<std::uint8_t>(fmt::Wdl::Win));
   offset += 1732;
   assert(offset == result.size());
   return result;
}

std::vector<std::uint8_t> kck_payload() {
   std::vector<std::uint8_t> result(
      static_cast<std::size_t>(fmt::state_count(fmt::Material::KCK)),
      static_cast<std::uint8_t>(fmt::Wdl::Invalid));
   std::fill(result.end() - static_cast<std::ptrdiff_t>(fmt::legal_count(fmt::Material::KCK)),
             result.end(), static_cast<std::uint8_t>(fmt::Wdl::Draw));
   return result;
}

std::vector<std::uint8_t> krkn_payload() {
   std::vector<std::uint8_t> result(
      static_cast<std::size_t>(fmt::state_count(fmt::Material::KRKN)),
      static_cast<std::uint8_t>(fmt::Wdl::Invalid));
   std::fill(result.end() - static_cast<std::ptrdiff_t>(fmt::legal_count(fmt::Material::KRKN)),
             result.end(), static_cast<std::uint8_t>(fmt::Wdl::Draw));
   return result;
}

std::vector<std::uint8_t> kwkn_payload() {
   std::vector<std::uint8_t> result(
      static_cast<std::size_t>(fmt::state_count(fmt::Material::KWKN)),
      static_cast<std::uint8_t>(fmt::Wdl::Invalid));
   std::fill(result.end() - static_cast<std::ptrdiff_t>(fmt::legal_count(fmt::Material::KWKN)),
             result.end(), static_cast<std::uint8_t>(fmt::Wdl::Draw));
   return result;
}

std::vector<std::uint8_t> kckw_payload() {
   std::vector<std::uint8_t> result(
      static_cast<std::size_t>(fmt::state_count(fmt::Material::KCKW)),
      static_cast<std::uint8_t>(fmt::Wdl::Invalid));
   std::fill(result.end() - static_cast<std::ptrdiff_t>(fmt::legal_count(fmt::Material::KCKW)),
             result.end(), static_cast<std::uint8_t>(fmt::Wdl::Draw));
   return result;
}

std::vector<std::uint8_t> kcck_payload() {
   std::vector<std::uint8_t> result(
      static_cast<std::size_t>(fmt::state_count(fmt::Material::KCCK)),
      static_cast<std::uint8_t>(fmt::Wdl::Invalid));
   std::fill(result.end() - static_cast<std::ptrdiff_t>(fmt::legal_count(fmt::Material::KCCK)),
             result.end(), static_cast<std::uint8_t>(fmt::Wdl::Draw));
   return result;
}

std::vector<std::uint8_t> read_bytes(const std::string & path) {
   std::ifstream stream(path, std::ios::binary);
   assert(stream);
   stream.seekg(0, std::ios::end);
   const std::streamoff size = stream.tellg();
   assert(size >= 0);
   stream.seekg(0, std::ios::beg);
   std::vector<std::uint8_t> bytes(static_cast<std::size_t>(size));
   stream.read(reinterpret_cast<char *>(bytes.data()), size);
   assert(stream);
   return bytes;
}

void write_bytes(const std::string & path, const std::vector<std::uint8_t> & bytes) {
   std::ofstream stream(path, std::ios::binary | std::ios::trunc);
   assert(stream);
   stream.write(reinterpret_cast<const char *>(bytes.data()),
                static_cast<std::streamsize>(bytes.size()));
   assert(stream);
}

bool contains(const std::string & value, const char * text) {
   return value.find(text) != std::string::npos;
}

struct Cleanup {
   std::vector<std::string> paths;
   ~Cleanup() {
      for (const std::string & path : paths) {
         std::remove(path.c_str());
         std::remove((path + ".tmp").c_str());
      }
   }
};

} // namespace

int main() {
   const std::string krk_path = "production-format-krk.omtb";
   const std::string kck_path = "production-format-kck.omtb";
   const std::string krkn_path = "production-format-krkn.omtb";
   const std::string kwkn_path = "production-format-kwkn.omtb";
   const std::string kckw_path = "production-format-kckw.omtb";
   const std::string kcck_path = "production-format-kcck.omtb";
   const std::string dtz_path = "production-format-krk-dtz.omtb";
   const std::string payload_corrupt = "production-format-payload-corrupt.omtb";
   const std::string header_corrupt = "production-format-header-corrupt.omtb";
   const std::string truncated = "production-format-truncated.omtb";
   Cleanup cleanup {{ krk_path, kck_path, krkn_path, kwkn_path, kckw_path, kcck_path,
                      dtz_path, payload_corrupt, header_corrupt, truncated }};

   assert(fmt::state_count(fmt::Material::KRK) == 273816);
   assert(fmt::legal_count(fmt::Material::KRK) == 235033);
   assert(fmt::state_count(fmt::Material::KCK) == 273816);
   assert(fmt::legal_count(fmt::Material::KCK) == 244779);
   assert(fmt::state_count(fmt::Material::KRKC) == 27594696);
   assert(fmt::legal_count(fmt::Material::KRKC) == 22607206);
   assert(fmt::state_count(fmt::Material::KRKN) == 27594696);
   assert(fmt::legal_count(fmt::Material::KRKN) == 23034346);
   assert(fmt::state_count(fmt::Material::KWKN) == 27594696);
   assert(fmt::legal_count(fmt::Material::KWKN) == 24078355);
   assert(fmt::state_count(fmt::Material::KCKW) == 27594696);
   assert(fmt::legal_count(fmt::Material::KCKW) == 23651215);
   assert(fmt::state_count(fmt::Material::KCCK) == 27594696);
   assert(fmt::legal_count(fmt::Material::KCCK) == 23638870);
   assert(hex(fmt::canonical_rules_fingerprint()) ==
          "cbae8f8bf3e2ab2e621a2eab8ddbec36895b3378535f9ce0ba134b72382ebf39");
   assert(hex(fmt::canonical_rules_fingerprint(fmt::Material::KRKN)) ==
          "cbae8f8bf3e2ab2e621a2eab8ddbec36895b3378535f9ce0ba134b72382ebf39");
   assert(hex(fmt::canonical_rules_fingerprint(fmt::Material::KWKN)) ==
          "67de2569c248dfb80bb308fee09fa4deebf4225fdb3880a1507b21656b204ff7");
   assert(hex(fmt::canonical_rules_fingerprint(fmt::Material::KCKW)) ==
          "f46be111c8f52a8b22777a83bd463fe2ad7368a260fed23b0fd8956d751415d7");
   assert(hex(fmt::canonical_rules_fingerprint(fmt::Material::KCCK)) ==
          "96c23dfcdec7d37442006fb52f728ad86cbbff31cb34a561854685981af90940");

   std::string error;
   const std::vector<std::uint8_t> krk = krk_payload();
   assert(fmt::write_file(krk_path, fmt::Material::KRK, krk, {}, error));
   assert(error.empty());

   fmt::Table table;
   assert(fmt::read_file(krk_path, fmt::canonical_requirements(fmt::Material::KRK), table, error));
   assert(table.metadata.material == fmt::Material::KRK);
   assert(table.metadata.state_count == krk.size());
   assert(table.metadata.legal_count == 235033);
   assert(table.metadata.outcome_counts[0] == 38783);
   assert(table.metadata.outcome_counts[1] == 339);
   assert(table.metadata.outcome_counts[2] == 0);
   assert(table.metadata.outcome_counts[3] == 232962);
   assert(table.metadata.outcome_counts[4] == 0);
   assert(table.metadata.outcome_counts[5] == 1732);
   assert(!table.metadata.has_dtz && table.dtz.empty());
   assert(table.wdl == krk);
   // Golden digests are also asserted by the Python writer/reader test.  This
   // catches any cross-language offset, endianness, or header-layout drift.
   assert(hex(table.metadata.payload_sha256) ==
          "9df95c3ab43f6c79c8ee33acd5cb860596f3cb7cbe5981cb986dc478707f5bd3");
   assert(hex(table.metadata.header_sha256) ==
          "8d661c6aaffb69c1d9c43333b384217ec4291bba2e70c9d9e636fe25db44a8df");

   std::vector<std::uint16_t> dtz(krk.size(), 0);
   dtz.back() = 7;
   assert(fmt::write_file(dtz_path, fmt::Material::KRK, krk, dtz, error));
   fmt::Table dtz_table;
   assert(fmt::read_file(dtz_path,
                         fmt::canonical_requirements(fmt::Material::KRK, true),
                         dtz_table, error));
   assert(dtz_table.metadata.has_dtz);
   assert(dtz_table.dtz.size() == krk.size());
   assert(dtz_table.dtz.back() == 7);

   const std::vector<std::uint8_t> kck = kck_payload();
   assert(fmt::write_file(kck_path, fmt::Material::KCK, kck, {}, error));
   fmt::Table kck_table;
   assert(fmt::read_file(kck_path, fmt::canonical_requirements(fmt::Material::KCK),
                         kck_table, error));
   assert(kck_table.metadata.outcome_counts[3] == 244779);

   const std::vector<std::uint8_t> krkn = krkn_payload();
   assert(fmt::write_file(krkn_path, fmt::Material::KRKN, krkn, {}, error));
   fmt::Table krkn_table;
   assert(fmt::read_file(krkn_path, fmt::canonical_requirements(fmt::Material::KRKN),
                         krkn_table, error));
   assert(krkn_table.metadata.material == fmt::Material::KRKN);
   assert(krkn_table.metadata.state_count == 27594696);
   assert(krkn_table.metadata.legal_count == 23034346);
   assert(krkn_table.metadata.outcome_counts[0] == 4560350);
   assert(krkn_table.metadata.outcome_counts[3] == 23034346);

   const std::vector<std::uint8_t> kwkn = kwkn_payload();
   assert(fmt::write_file(kwkn_path, fmt::Material::KWKN, kwkn, {}, error));
   fmt::Table kwkn_table;
   assert(fmt::read_file(kwkn_path, fmt::canonical_requirements(fmt::Material::KWKN),
                         kwkn_table, error));
   assert(kwkn_table.metadata.material == fmt::Material::KWKN);
   assert(kwkn_table.metadata.state_count == 27594696);
   assert(kwkn_table.metadata.legal_count == 24078355);
   assert(kwkn_table.metadata.outcome_counts[0] == 3516341);
   assert(kwkn_table.metadata.outcome_counts[3] == 24078355);
   assert(hex(kwkn_table.metadata.rules_fingerprint) ==
          "67de2569c248dfb80bb308fee09fa4deebf4225fdb3880a1507b21656b204ff7");

   const std::vector<std::uint8_t> kckw = kckw_payload();
   assert(fmt::write_file(kckw_path, fmt::Material::KCKW, kckw, {}, error));
   fmt::Table kckw_table;
   assert(fmt::read_file(kckw_path, fmt::canonical_requirements(fmt::Material::KCKW),
                         kckw_table, error));
   assert(kckw_table.metadata.material == fmt::Material::KCKW);
   assert(kckw_table.metadata.state_count == 27594696);
   assert(kckw_table.metadata.legal_count == 23651215);
   assert(kckw_table.metadata.outcome_counts[0] == 3943481);
   assert(kckw_table.metadata.outcome_counts[3] == 23651215);
   assert(hex(kckw_table.metadata.rules_fingerprint) ==
          "f46be111c8f52a8b22777a83bd463fe2ad7368a260fed23b0fd8956d751415d7");

   const std::vector<std::uint8_t> kcck = kcck_payload();
   assert(fmt::write_file(kcck_path, fmt::Material::KCCK, kcck, {}, error));
   fmt::Table kcck_table;
   assert(fmt::read_file(kcck_path, fmt::canonical_requirements(fmt::Material::KCCK),
                         kcck_table, error));
   assert(kcck_table.metadata.material == fmt::Material::KCCK);
   assert(kcck_table.metadata.state_count == 27594696);
   assert(kcck_table.metadata.legal_count == 23638870);
   assert(kcck_table.metadata.outcome_counts[0] == 3955826);
   assert(kcck_table.metadata.outcome_counts[3] == 23638870);
   assert(hex(kcck_table.metadata.rules_fingerprint) ==
          "96c23dfcdec7d37442006fb52f728ad86cbbff31cb34a561854685981af90940");

   // Unsupported codes are rejected before a file is written.
   std::vector<std::uint8_t> bad_code = krk;
   bad_code.back() = 0xFF;
   assert(!fmt::write_file("production-format-bad-code.omtb", fmt::Material::KRK,
                           bad_code, {}, error));
   assert(contains(error, "unsupported code"));
   std::remove("production-format-bad-code.omtb");
   std::remove("production-format-bad-code.omtb.tmp");

   // Current KCK semantics require every legal record to be a draw.
   std::vector<std::uint8_t> bad_kck = kck;
   bad_kck[bad_kck.size() - static_cast<std::size_t>(fmt::legal_count(fmt::Material::KCK))] =
      static_cast<std::uint8_t>(fmt::Wdl::Win);
   assert(!fmt::write_file("production-format-bad-kck.omtb", fmt::Material::KCK,
                           bad_kck, {}, error));
   assert(contains(error, "insufficient-material"));
   std::remove("production-format-bad-kck.omtb");
   std::remove("production-format-bad-kck.omtb.tmp");

   std::vector<std::uint8_t> bytes = read_bytes(krk_path);
   bytes.back() ^= 1;
   write_bytes(payload_corrupt, bytes);
   fmt::Table unchanged;
   unchanged.metadata.state_count = 123;
   assert(!fmt::read_file(payload_corrupt,
                          fmt::canonical_requirements(fmt::Material::KRK),
                          unchanged, error));
   assert(contains(error, "payload checksum"));
   assert(unchanged.metadata.state_count == 123);

   bytes = read_bytes(krk_path);
   bytes[40] ^= 1;
   write_bytes(header_corrupt, bytes);
   assert(!fmt::read_file(header_corrupt,
                          fmt::canonical_requirements(fmt::Material::KRK),
                          table, error));
   assert(contains(error, "header checksum"));

   bytes = read_bytes(krk_path);
   bytes.resize(256 + 10);
   write_bytes(truncated, bytes);
   assert(!fmt::read_file(truncated,
                          fmt::canonical_requirements(fmt::Material::KRK),
                          table, error));
   assert(contains(error, "file size"));

   assert(!fmt::read_file(krk_path,
                          fmt::canonical_requirements(fmt::Material::KCK),
                          table, error));
   assert(contains(error, "material signature"));

   fmt::Load_Requirements wrong_rules = fmt::canonical_requirements(fmt::Material::KRK);
   wrong_rules.rules_fingerprint.fill(0);
   assert(!fmt::read_file(krk_path, wrong_rules, table, error));
   assert(contains(error, "rules fingerprint"));

   return 0;
}
