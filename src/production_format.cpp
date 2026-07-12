#include "production_format.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <utility>

namespace omega_tb {
namespace production_format {

namespace {

constexpr std::size_t Header_Size = 256;
constexpr std::size_t Rules_Offset = 144;
constexpr std::size_t Payload_Hash_Offset = 176;
constexpr std::size_t Header_Hash_Offset = 208;
constexpr std::size_t Reserved_Offset = 240;
constexpr std::uint16_t Version = 1;
constexpr std::uint32_t Endian_Tag = 0x01020304U;
constexpr std::uint16_t Square_Count = 104;
constexpr std::uint8_t Turn_Count = 2;
constexpr std::uint8_t Rules_Id = 1;
constexpr std::uint32_t Index_Id = 1;
constexpr std::uint32_t Flag_Has_Dtz = 1U;
constexpr std::uint8_t Wdl_Encoding = 1;
constexpr std::uint8_t Dtz_None = 0;
constexpr std::uint8_t Dtz_Uint16_Le = 1;

constexpr std::uint8_t Piece_None = 0;
constexpr std::uint8_t Piece_King = 1;
constexpr std::uint8_t Piece_Rook = 2;
constexpr std::uint8_t Piece_Champion = 3;
constexpr std::uint8_t Role_None = 0xFF;
constexpr std::uint8_t Role_Zero = 0;
constexpr std::uint8_t Role_One = 1;

const std::array<std::uint8_t, 8> Magic {{
   'O', 'M', 'T', 'B', 'P', 'R', 'O', 'D'
}};

const char Rules_Description[] =
   "omega-104-v1;d4-first-piece-v1;historical-legality-v1;"
   "king-v1;rook-v1;champion-v1;100-ply-auto-draw-v1;"
   "insufficient-k-plus-one-nbcw-v1;wdl5-dtz16-v1";

struct Material_Spec {
   Material material;
   std::uint8_t piece_count;
   std::array<std::uint8_t, 4> pieces;
   std::array<std::uint8_t, 4> roles;
   std::uint64_t states;
   std::uint64_t legal;
};

const Material_Spec Specs[] {
   { Material::KRK, 3,
     {{ Piece_King, Piece_Rook, Piece_King, Piece_None }},
     {{ Role_Zero, Role_Zero, Role_One, Role_None }},
     273816ULL, 235033ULL },
   { Material::KCK, 3,
     {{ Piece_King, Piece_Champion, Piece_King, Piece_None }},
     {{ Role_Zero, Role_Zero, Role_One, Role_None }},
     273816ULL, 244779ULL },
   { Material::KRKC, 4,
     {{ Piece_King, Piece_Rook, Piece_King, Piece_Champion }},
     {{ Role_Zero, Role_Zero, Role_One, Role_One }},
     27594696ULL, 22607206ULL },
};

const Material_Spec * spec_for(Material material) {
   for (const Material_Spec & spec : Specs) {
      if (spec.material == material) return &spec;
   }
   return nullptr;
}

std::uint32_t rotate_right(std::uint32_t value, unsigned count) {
   return (value >> count) | (value << (32U - count));
}

class Sha256 {
public:
   Sha256() { reset(); }

   void update(const std::uint8_t * data, std::size_t size) {
      total_size_ += size;
      while (size != 0) {
         std::size_t take = std::min(size, Block_Size - buffered_);
         std::memcpy(buffer_.data() + buffered_, data, take);
         buffered_ += take;
         data += take;
         size -= take;
         if (buffered_ == Block_Size) {
            transform(buffer_.data());
            buffered_ = 0;
         }
      }
   }

   std::array<std::uint8_t, 32> finish() {
      std::uint64_t bit_size = static_cast<std::uint64_t>(total_size_) * 8ULL;
      std::uint8_t marker = 0x80;
      update(&marker, 1);
      std::uint8_t zero = 0;
      while (buffered_ != 56) update(&zero, 1);

      std::array<std::uint8_t, 8> length {{}};
      for (int index = 7; index >= 0; index--) {
         length[static_cast<std::size_t>(index)] = static_cast<std::uint8_t>(bit_size & 0xFFU);
         bit_size >>= 8;
      }
      update(length.data(), length.size());

      std::array<std::uint8_t, 32> digest {{}};
      for (std::size_t index = 0; index < state_.size(); index++) {
         digest[index * 4] = static_cast<std::uint8_t>(state_[index] >> 24);
         digest[index * 4 + 1] = static_cast<std::uint8_t>(state_[index] >> 16);
         digest[index * 4 + 2] = static_cast<std::uint8_t>(state_[index] >> 8);
         digest[index * 4 + 3] = static_cast<std::uint8_t>(state_[index]);
      }
      return digest;
   }

private:
   static constexpr std::size_t Block_Size = 64;
   std::array<std::uint32_t, 8> state_ {{}};
   std::array<std::uint8_t, Block_Size> buffer_ {{}};
   std::size_t buffered_ { 0 };
   std::size_t total_size_ { 0 };

   void reset() {
      state_ = {{
         0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
         0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
      }};
      buffer_.fill(0);
      buffered_ = 0;
      total_size_ = 0;
   }

   void transform(const std::uint8_t * block) {
      static const std::uint32_t constants[64] = {
         0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
         0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
         0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
         0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
         0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
         0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
         0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
         0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U,
      };
      std::uint32_t schedule[64] {};
      for (int index = 0; index < 16; index++) {
         std::size_t offset = static_cast<std::size_t>(index) * 4;
         schedule[index] = (static_cast<std::uint32_t>(block[offset]) << 24)
                         | (static_cast<std::uint32_t>(block[offset + 1]) << 16)
                         | (static_cast<std::uint32_t>(block[offset + 2]) << 8)
                         | static_cast<std::uint32_t>(block[offset + 3]);
      }
      for (int index = 16; index < 64; index++) {
         std::uint32_t x = schedule[index - 15];
         std::uint32_t y = schedule[index - 2];
         std::uint32_t s0 = rotate_right(x, 7) ^ rotate_right(x, 18) ^ (x >> 3);
         std::uint32_t s1 = rotate_right(y, 17) ^ rotate_right(y, 19) ^ (y >> 10);
         schedule[index] = schedule[index - 16] + s0 + schedule[index - 7] + s1;
      }

      std::uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
      std::uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
      for (int index = 0; index < 64; index++) {
         std::uint32_t s1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
         std::uint32_t choose = (e & f) ^ ((~e) & g);
         std::uint32_t temp1 = h + s1 + choose + constants[index] + schedule[index];
         std::uint32_t s0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
         std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
         std::uint32_t temp2 = s0 + majority;
         h = g; g = f; f = e; e = d + temp1;
         d = c; c = b; b = a; a = temp1 + temp2;
      }
      state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
      state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
   }
};

std::array<std::uint8_t, 32> sha256(const std::uint8_t * data, std::size_t size) {
   Sha256 hash;
   hash.update(data, size);
   return hash.finish();
}

std::array<std::uint8_t, 32> header_hash(std::array<std::uint8_t, Header_Size> header) {
   std::fill(header.begin() + Header_Hash_Offset,
             header.begin() + Header_Hash_Offset + 32, 0);
   return sha256(header.data(), header.size());
}

void put_u16(std::array<std::uint8_t, Header_Size> & data, std::size_t offset,
             std::uint16_t value) {
   data[offset] = static_cast<std::uint8_t>(value);
   data[offset + 1] = static_cast<std::uint8_t>(value >> 8);
}

void put_u32(std::array<std::uint8_t, Header_Size> & data, std::size_t offset,
             std::uint32_t value) {
   for (int index = 0; index < 4; index++)
      data[offset + static_cast<std::size_t>(index)] = static_cast<std::uint8_t>(value >> (index * 8));
}

void put_u64(std::array<std::uint8_t, Header_Size> & data, std::size_t offset,
             std::uint64_t value) {
   for (int index = 0; index < 8; index++)
      data[offset + static_cast<std::size_t>(index)] = static_cast<std::uint8_t>(value >> (index * 8));
}

std::uint16_t get_u16(const std::array<std::uint8_t, Header_Size> & data, std::size_t offset) {
   return static_cast<std::uint16_t>(data[offset])
        | static_cast<std::uint16_t>(static_cast<std::uint16_t>(data[offset + 1]) << 8);
}

std::uint32_t get_u32(const std::array<std::uint8_t, Header_Size> & data, std::size_t offset) {
   std::uint32_t value = 0;
   for (int index = 3; index >= 0; index--)
      value = (value << 8) | data[offset + static_cast<std::size_t>(index)];
   return value;
}

std::uint64_t get_u64(const std::array<std::uint8_t, Header_Size> & data, std::size_t offset) {
   std::uint64_t value = 0;
   for (int index = 7; index >= 0; index--)
      value = (value << 8) | data[offset + static_cast<std::size_t>(index)];
   return value;
}

bool equal_slice(const std::array<std::uint8_t, Header_Size> & header,
                 std::size_t offset, const std::uint8_t * data, std::size_t size) {
   return std::equal(header.begin() + offset, header.begin() + offset + size, data);
}

bool valid_code(std::uint8_t code) {
   return code <= static_cast<std::uint8_t>(Wdl::Win);
}

bool validate_payload(const Material_Spec & spec,
                      const std::vector<std::uint8_t> & wdl,
                      const std::vector<std::uint16_t> & dtz,
                      std::array<std::uint64_t, 6> & counts,
                      std::string & error) {
   if (wdl.size() != spec.states) {
      error = "WDL payload state count mismatch";
      return false;
   }
   counts.fill(0);
   for (std::uint8_t code : wdl) {
      if (!valid_code(code)) {
         error = "WDL payload contains an unsupported code";
         return false;
      }
      counts[code]++;
   }
   if (counts[static_cast<std::size_t>(Wdl::Invalid)] != spec.states - spec.legal) {
      error = "invalid-state count does not match index metadata";
      return false;
   }
   std::uint64_t legal = 0;
   for (std::size_t index = 1; index < counts.size(); index++) legal += counts[index];
   if (legal != spec.legal) {
      error = "legal-state count does not match index metadata";
      return false;
   }
   if (spec.material == Material::KCK
    && (counts[1] != 0 || counts[2] != 0 || counts[4] != 0 || counts[5] != 0)) {
      error = "KCK payload violates the insufficient-material draw policy";
      return false;
   }
   if (dtz.empty() && (counts[2] != 0 || counts[4] != 0)) {
      error = "blessed/cursed WDL codes require DTZ";
      return false;
   }
   if (!dtz.empty() && dtz.size() != spec.states) {
      error = "DTZ payload state count mismatch";
      return false;
   }
   return true;
}

std::vector<std::uint8_t> encode_dtz(const std::vector<std::uint16_t> & dtz) {
   std::vector<std::uint8_t> bytes(dtz.size() * 2);
   for (std::size_t index = 0; index < dtz.size(); index++) {
      bytes[index * 2] = static_cast<std::uint8_t>(dtz[index]);
      bytes[index * 2 + 1] = static_cast<std::uint8_t>(dtz[index] >> 8);
   }
   return bytes;
}

std::array<std::uint8_t, Header_Size> build_header(
      const Material_Spec & spec,
      const std::vector<std::uint8_t> & wdl,
      const std::vector<std::uint8_t> & dtz,
      const std::array<std::uint64_t, 6> & counts) {
   std::array<std::uint8_t, Header_Size> header {{}};
   std::copy(Magic.begin(), Magic.end(), header.begin());
   put_u16(header, 8, Version);
   put_u16(header, 10, static_cast<std::uint16_t>(Header_Size));
   put_u32(header, 12, Endian_Tag);
   header[16] = static_cast<std::uint8_t>(spec.material);
   header[17] = spec.piece_count;
   header[18] = Wdl_Encoding;
   header[19] = dtz.empty() ? Dtz_None : Dtz_Uint16_Le;
   put_u32(header, 20, dtz.empty() ? 0U : Flag_Has_Dtz);
   put_u16(header, 24, Square_Count);
   header[26] = Turn_Count;
   header[27] = Rules_Id;
   put_u32(header, 28, Index_Id);
   put_u64(header, 32, spec.states);
   put_u64(header, 40, spec.legal);
   put_u64(header, 48, Header_Size);
   put_u64(header, 56, wdl.size());
   put_u64(header, 64, dtz.empty() ? 0ULL : Header_Size + wdl.size());
   put_u64(header, 72, dtz.size());
   std::copy(spec.pieces.begin(), spec.pieces.end(), header.begin() + 80);
   std::copy(spec.roles.begin(), spec.roles.end(), header.begin() + 84);
   header[88] = Role_Zero;
   header[89] = Role_One;
   for (std::size_t index = 0; index < 6; index++) {
      header[90 + index] = static_cast<std::uint8_t>(index);
      put_u64(header, 96 + index * 8, counts[index]);
   }
   const auto rules = canonical_rules_fingerprint();
   std::copy(rules.begin(), rules.end(), header.begin() + Rules_Offset);
   Sha256 payload_hash;
   payload_hash.update(wdl.data(), wdl.size());
   if (!dtz.empty()) payload_hash.update(dtz.data(), dtz.size());
   const auto payload_digest = payload_hash.finish();
   std::copy(payload_digest.begin(), payload_digest.end(), header.begin() + Payload_Hash_Offset);
   const auto digest = header_hash(header);
   std::copy(digest.begin(), digest.end(), header.begin() + Header_Hash_Offset);
   return header;
}

std::string io_error(const char * action, const std::string & path) {
   std::ostringstream out;
   out << action << " '" << path << "'";
   if (errno != 0) out << ": " << std::strerror(errno);
   return out.str();
}

} // namespace

const char * rules_description() {
   return Rules_Description;
}

std::array<std::uint8_t, 32> canonical_rules_fingerprint() {
   return sha256(reinterpret_cast<const std::uint8_t *>(Rules_Description),
                 std::strlen(Rules_Description));
}

Load_Requirements canonical_requirements(Material material, bool require_dtz) {
   Load_Requirements result;
   result.material = material;
   result.require_dtz = require_dtz;
   result.rules_fingerprint = canonical_rules_fingerprint();
   return result;
}

std::uint64_t state_count(Material material) {
   const Material_Spec * spec = spec_for(material);
   return spec == nullptr ? 0 : spec->states;
}

std::uint64_t legal_count(Material material) {
   const Material_Spec * spec = spec_for(material);
   return spec == nullptr ? 0 : spec->legal;
}

bool write_file(const std::string & path,
                Material material,
                const std::vector<std::uint8_t> & wdl,
                const std::vector<std::uint16_t> & dtz,
                std::string & error) {
   error.clear();
   const Material_Spec * spec = spec_for(material);
   if (spec == nullptr) {
      error = "unsupported material signature";
      return false;
   }
   std::array<std::uint64_t, 6> counts {{}};
   if (!validate_payload(*spec, wdl, dtz, counts, error)) return false;
   const std::vector<std::uint8_t> dtz_bytes = encode_dtz(dtz);
   const auto header = build_header(*spec, wdl, dtz_bytes, counts);

   const std::string temporary = path + ".tmp";
   {
      std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
      if (!stream) {
         error = io_error("cannot create", temporary);
         return false;
      }
      stream.write(reinterpret_cast<const char *>(header.data()), header.size());
      stream.write(reinterpret_cast<const char *>(wdl.data()),
                   static_cast<std::streamsize>(wdl.size()));
      if (!dtz_bytes.empty()) {
         stream.write(reinterpret_cast<const char *>(dtz_bytes.data()),
                      static_cast<std::streamsize>(dtz_bytes.size()));
      }
      stream.flush();
      if (!stream) {
         error = io_error("cannot write", temporary);
         stream.close();
         std::remove(temporary.c_str());
         return false;
      }
   }

   Table verified;
   if (!read_file(temporary, canonical_requirements(material, !dtz.empty()), verified, error)) {
      std::remove(temporary.c_str());
      return false;
   }
   std::remove(path.c_str());
   if (std::rename(temporary.c_str(), path.c_str()) != 0) {
      error = io_error("cannot rename table to", path);
      std::remove(temporary.c_str());
      return false;
   }
   return true;
}

bool read_file(const std::string & path,
               const Load_Requirements & requirements,
               Table & table,
               std::string & error) {
   error.clear();
   const Material_Spec * expected = spec_for(requirements.material);
   if (expected == nullptr) {
      error = "loader requirements have an unsupported material signature";
      return false;
   }

   std::ifstream stream(path, std::ios::binary);
   if (!stream) {
      error = io_error("cannot open", path);
      return false;
   }
   std::array<std::uint8_t, Header_Size> header {{}};
   stream.read(reinterpret_cast<char *>(header.data()), header.size());
   if (stream.gcount() != static_cast<std::streamsize>(header.size())) {
      error = "truncated production header";
      return false;
   }
   if (!equal_slice(header, 0, Magic.data(), Magic.size())) {
      error = "unsupported production table magic";
      return false;
   }
   if (get_u16(header, 8) != Version || get_u16(header, 10) != Header_Size) {
      error = "unsupported production table version";
      return false;
   }
   const auto calculated_header_hash = header_hash(header);
   if (!equal_slice(header, Header_Hash_Offset, calculated_header_hash.data(), 32)) {
      error = "production header checksum mismatch";
      return false;
   }
   if (get_u32(header, 12) != Endian_Tag) {
      error = "production endianness marker mismatch";
      return false;
   }

   const Material material = static_cast<Material>(header[16]);
   const Material_Spec * spec = spec_for(material);
   if (spec == nullptr || material != requirements.material) {
      error = "production material signature mismatch";
      return false;
   }
   const std::uint32_t flags = get_u32(header, 20);
   const bool has_dtz = (flags & Flag_Has_Dtz) != 0;
   if ((flags & ~Flag_Has_Dtz) != 0) {
      error = "unsupported production table flags";
      return false;
   }
   if (header[17] != spec->piece_count || header[18] != Wdl_Encoding
    || header[19] != (has_dtz ? Dtz_Uint16_Le : Dtz_None)) {
      error = "production payload encoding metadata mismatch";
      return false;
   }
   if (requirements.require_dtz && !has_dtz) {
      error = "production table has no DTZ payload";
      return false;
   }
   if (get_u16(header, 24) != Square_Count || header[26] != Turn_Count
    || header[27] != Rules_Id || get_u32(header, 28) != Index_Id) {
      error = "production geometry/index/rules metadata mismatch";
      return false;
   }
   if (!equal_slice(header, 80, spec->pieces.data(), spec->pieces.size())
    || !equal_slice(header, 84, spec->roles.data(), spec->roles.size())) {
      error = "production labelled-piece order mismatch";
      return false;
   }
   if (header[88] != Role_Zero || header[89] != Role_One) {
      error = "production side-to-move role metadata mismatch";
      return false;
   }
   for (std::size_t index = 0; index < 6; index++) {
      if (header[90 + index] != index) {
         error = "production WDL code map mismatch";
         return false;
      }
   }
   if (std::any_of(header.begin() + Reserved_Offset, header.end(),
                   [](std::uint8_t value) { return value != 0; })) {
      error = "production reserved header bytes are nonzero";
      return false;
   }

   const std::uint64_t states = get_u64(header, 32);
   const std::uint64_t legal = get_u64(header, 40);
   const std::uint64_t wdl_offset = get_u64(header, 48);
   const std::uint64_t wdl_size = get_u64(header, 56);
   const std::uint64_t dtz_offset = get_u64(header, 64);
   const std::uint64_t dtz_size = get_u64(header, 72);
   if (states != spec->states || legal != spec->legal) {
      error = "production state-count metadata mismatch";
      return false;
   }
   if (wdl_offset != Header_Size || wdl_size != states) {
      error = "production WDL offset/size mismatch";
      return false;
   }
   const std::uint64_t expected_dtz_offset = has_dtz ? Header_Size + wdl_size : 0;
   const std::uint64_t expected_dtz_size = has_dtz ? states * 2 : 0;
   if (dtz_offset != expected_dtz_offset || dtz_size != expected_dtz_size) {
      error = "production DTZ offset/size mismatch";
      return false;
   }
   if (!equal_slice(header, Rules_Offset, requirements.rules_fingerprint.data(), 32)
    || requirements.rules_fingerprint != canonical_rules_fingerprint()) {
      error = "production rules fingerprint mismatch";
      return false;
   }

   if (wdl_size > std::numeric_limits<std::size_t>::max()
    || dtz_size > std::numeric_limits<std::size_t>::max()) {
      error = "production payload is too large for this process";
      return false;
   }
   stream.seekg(0, std::ios::end);
   const std::streamoff file_size = stream.tellg();
   const std::uint64_t expected_size = Header_Size + wdl_size + dtz_size;
   if (file_size < 0 || static_cast<std::uint64_t>(file_size) != expected_size) {
      error = "production file size mismatch";
      return false;
   }
   stream.seekg(Header_Size, std::ios::beg);

   Table loaded;
   loaded.wdl.resize(static_cast<std::size_t>(wdl_size));
   stream.read(reinterpret_cast<char *>(loaded.wdl.data()),
               static_cast<std::streamsize>(loaded.wdl.size()));
   if (!stream) {
      error = "truncated production WDL payload";
      return false;
   }
   std::vector<std::uint8_t> dtz_bytes(static_cast<std::size_t>(dtz_size));
   if (!dtz_bytes.empty()) {
      stream.read(reinterpret_cast<char *>(dtz_bytes.data()),
                  static_cast<std::streamsize>(dtz_bytes.size()));
      if (!stream) {
         error = "truncated production DTZ payload";
         return false;
      }
      loaded.dtz.resize(static_cast<std::size_t>(states));
      for (std::size_t index = 0; index < loaded.dtz.size(); index++) {
         loaded.dtz[index] = static_cast<std::uint16_t>(dtz_bytes[index * 2])
                           | static_cast<std::uint16_t>(static_cast<std::uint16_t>(dtz_bytes[index * 2 + 1]) << 8);
      }
   }

   Sha256 payload_hash;
   payload_hash.update(loaded.wdl.data(), loaded.wdl.size());
   if (!dtz_bytes.empty()) payload_hash.update(dtz_bytes.data(), dtz_bytes.size());
   const auto calculated_payload_hash = payload_hash.finish();
   if (!equal_slice(header, Payload_Hash_Offset, calculated_payload_hash.data(), 32)) {
      error = "production payload checksum mismatch";
      return false;
   }

   std::array<std::uint64_t, 6> counts {{}};
   if (!validate_payload(*spec, loaded.wdl, loaded.dtz, counts, error)) return false;
   for (std::size_t index = 0; index < counts.size(); index++) {
      if (get_u64(header, 96 + index * 8) != counts[index]) {
         error = "production WDL outcome counts mismatch";
         return false;
      }
   }

   loaded.metadata.material = material;
   loaded.metadata.state_count = states;
   loaded.metadata.legal_count = legal;
   loaded.metadata.outcome_counts = counts;
   loaded.metadata.has_dtz = has_dtz;
   std::copy(header.begin() + Rules_Offset, header.begin() + Rules_Offset + 32,
             loaded.metadata.rules_fingerprint.begin());
   std::copy(header.begin() + Payload_Hash_Offset, header.begin() + Payload_Hash_Offset + 32,
             loaded.metadata.payload_sha256.begin());
   std::copy(header.begin() + Header_Hash_Offset, header.begin() + Header_Hash_Offset + 32,
             loaded.metadata.header_sha256.begin());
   table = std::move(loaded);
   return true;
}

} // namespace production_format
} // namespace omega_tb
