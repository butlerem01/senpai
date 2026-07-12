#include "production_format.hpp"

#include <array>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

namespace fmt = omega_tb::production_format;

namespace {

fmt::Material parse_material(const std::string & value) {
   if (value == "KRK") return fmt::Material::KRK;
   if (value == "KCK") return fmt::Material::KCK;
   if (value == "KRKC") return fmt::Material::KRKC;
   throw std::runtime_error("material must be KRK, KCK, or KRKC");
}

std::array<std::uint64_t, 6> parse_counts(const std::string & value) {
   std::array<std::uint64_t, 6> result {{}};
   std::istringstream input(value);
   std::string item;
   for (std::size_t index = 0; index < result.size(); ++index) {
      if (!std::getline(input, item, ',') || item.empty())
         throw std::runtime_error("--counts requires six comma-separated integers");
      std::size_t used = 0;
      result[index] = std::stoull(item, &used);
      if (used != item.size()) throw std::runtime_error("invalid integer in --counts");
   }
   if (std::getline(input, item, ','))
      throw std::runtime_error("--counts requires exactly six integers");
   return result;
}

std::string hex(const std::array<std::uint8_t, 32> & digest) {
   std::ostringstream out;
   out << std::hex << std::setfill('0');
   for (std::uint8_t byte : digest) out << std::setw(2) << unsigned(byte);
   return out.str();
}

} // namespace

int main(int argc, char ** argv) {
   try {
      std::string input;
      std::string material_name;
      std::string expected_counts_text;
      for (int index = 1; index < argc; ++index) {
         const std::string option = argv[index];
         if ((option == "--input" || option == "--material" || option == "--counts")
          && index + 1 >= argc)
            throw std::runtime_error("missing value after " + option);
         if (option == "--input") input = argv[++index];
         else if (option == "--material") material_name = argv[++index];
         else if (option == "--counts") expected_counts_text = argv[++index];
         else throw std::runtime_error("unknown option: " + option);
      }
      if (input.empty() || material_name.empty() || expected_counts_text.empty())
         throw std::runtime_error("usage: production_format_check --input FILE --material KRK|KCK|KRKC --counts i,l,bl,d,cw,w");

      const fmt::Material material = parse_material(material_name);
      const auto expected_counts = parse_counts(expected_counts_text);
      fmt::Table table;
      std::string error;
      if (!fmt::read_file(input, fmt::canonical_requirements(material), table, error))
         throw std::runtime_error(error);
      if (table.metadata.outcome_counts != expected_counts)
         throw std::runtime_error("native WDL outcome counts differ from the verified source");

      std::cout << "native OMTBPROD verified: material=" << material_name
                << " states=" << table.metadata.state_count
                << " legal=" << table.metadata.legal_count
                << " payload_sha256=" << hex(table.metadata.payload_sha256) << '\n';
      return 0;
   } catch (const std::exception & exception) {
      std::cerr << "production-format-check: " << exception.what() << '\n';
      return 1;
   }
}
