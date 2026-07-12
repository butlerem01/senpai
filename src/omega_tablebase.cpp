#include "omega_tablebase.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <utility>
#include <vector>

#include "bit.hpp"
#include "common.hpp"
#include "gen.hpp"
#include "list.hpp"
#include "pos.hpp"

namespace omega_tb {
namespace {

constexpr std::uint32_t Omega_Square_Count = 104;
constexpr std::uint32_t Three_Man_State_Count = 273816;
constexpr std::uint32_t Four_Man_State_Count = 27594696;

struct Coordinate {
   int x { 0 };
   int y { 0 };
};

class Geometry {
public:
   Geometry();

   const std::array<std::array<std::uint8_t, Omega_Square_Count>, 8> & transforms() const {
      return p_transforms;
   }

private:
   std::array<Coordinate, Omega_Square_Count> p_coordinates {{}};
   std::array<std::array<std::uint8_t, Omega_Square_Count>, 8> p_transforms {{}};
};

std::uint8_t square_from_coordinate(
   const std::array<Coordinate, Omega_Square_Count> & coordinates,
   int x,
   int y
) {
   for (std::uint32_t square = 0; square < Omega_Square_Count; ++square) {
      if (coordinates[square].x == x && coordinates[square].y == y) {
         return static_cast<std::uint8_t>(square);
      }
   }
   throw std::logic_error("transformed coordinate is not an Omega square");
}

Geometry::Geometry() {
   std::uint32_t square = 0;
   for (int file = 0; file < 10; ++file) {
      for (int rank = 0; rank < 10; ++rank) {
         p_coordinates[square++] = { file, rank };
      }
   }
   p_coordinates[100] = { -1, -1 };
   p_coordinates[101] = { 10, -1 };
   p_coordinates[102] = { 10, 10 };
   p_coordinates[103] = { -1, 10 };

   for (int transform = 0; transform < 8; ++transform) {
      for (std::uint32_t source = 0; source < Omega_Square_Count; ++source) {
         int x = p_coordinates[source].x;
         int y = p_coordinates[source].y;
         int rotations = transform;
         if (rotations >= 4) {
            x = 9 - x;
            rotations -= 4;
         }
         for (int i = 0; i < rotations; ++i) {
            const int old_x = x;
            x = 9 - y;
            y = old_x;
         }
         p_transforms[transform][source] = square_from_coordinate(p_coordinates, x, y);
      }
   }
}

class D4_Indexer {
public:
   D4_Indexer(const Geometry & geometry, int piece_count);

   std::uint32_t rank(const std::uint8_t * squares, std::uint8_t turn) const;
   std::uint32_t state_count() const { return p_state_count; }

private:
   struct Block {
      std::uint8_t representative { 0 };
      std::array<std::int8_t, Omega_Square_Count> base_transform {{}};
      std::uint32_t offset { 0 };
      std::array<std::uint8_t, Omega_Square_Count> to_local {{}};
      bool symmetric { false };
   };

   const Geometry & p_geometry;
   int p_piece_count { 0 };
   int p_tail_length { 0 };
   std::uint32_t p_generic_block_size { 0 };
   std::uint32_t p_symmetric_block_size { 0 };
   std::vector<Block> p_blocks;
   std::array<std::uint8_t, Omega_Square_Count> p_block_for_square {{}};
   std::vector<std::int32_t> p_symmetric_lookup;
   std::uint32_t p_state_count { 0 };

   static std::uint32_t ordered_size(int universe, int length);
   static std::uint32_t rank_ordered(const std::uint8_t * items, int universe, int length);
   static void unrank_ordered(std::uint32_t rank, int universe, int length, std::uint8_t * items);
   static std::uint8_t reflect_local(std::uint8_t local);
};

std::uint32_t D4_Indexer::ordered_size(int universe, int length) {
   std::uint32_t result = 1;
   for (int i = 0; i < length; ++i) {
      result *= static_cast<std::uint32_t>(universe - i);
   }
   return result;
}

std::uint32_t D4_Indexer::rank_ordered(
   const std::uint8_t * items,
   int universe,
   int length
) {
   std::uint8_t excluded[4] {};
   int excluded_count = 0;
   std::uint32_t rank = 0;
   int radix = universe;

   for (int i = 0; i < length; ++i) {
      int compressed = items[i];
      for (int j = 0; j < excluded_count; ++j) {
         if (excluded[j] == items[i]) {
            throw std::invalid_argument("ordered items are not distinct");
         }
         if (excluded[j] < items[i]) --compressed;
      }
      rank = rank * static_cast<std::uint32_t>(radix) +
             static_cast<std::uint32_t>(compressed);
      excluded[excluded_count++] = items[i];
      --radix;
   }
   return rank;
}

void D4_Indexer::unrank_ordered(
   std::uint32_t rank,
   int universe,
   int length,
   std::uint8_t * items
) {
   if (rank >= ordered_size(universe, length)) {
      throw std::out_of_range("ordered rank is out of range");
   }

   std::uint8_t compressed[4] {};
   for (int i = length - 1; i >= 0; --i) {
      const int radix = universe - i;
      compressed[i] = static_cast<std::uint8_t>(rank % static_cast<std::uint32_t>(radix));
      rank /= static_cast<std::uint32_t>(radix);
   }

   for (int i = 0; i < length; ++i) {
      int value = compressed[i];
      std::uint8_t selected[4] {};
      for (int j = 0; j < i; ++j) selected[j] = items[j];
      std::sort(selected, selected + i);
      for (int j = 0; j < i; ++j) {
         if (value >= selected[j]) ++value;
      }
      items[i] = static_cast<std::uint8_t>(value);
   }
}

std::uint8_t D4_Indexer::reflect_local(std::uint8_t local) {
   if (local < 11) return local;
   if (local < 57) return static_cast<std::uint8_t>(local + 46);
   return static_cast<std::uint8_t>(local - 46);
}

D4_Indexer::D4_Indexer(const Geometry & geometry, int piece_count)
   : p_geometry(geometry),
     p_piece_count(piece_count),
     p_tail_length(piece_count - 1) {
   if (piece_count != 3 && piece_count != 4) {
      throw std::invalid_argument("D4 index supports three or four labelled pieces");
   }

   p_generic_block_size = ordered_size(103, p_tail_length);
   p_symmetric_block_size =
      (p_generic_block_size + ordered_size(11, p_tail_length)) / 2;
   p_symmetric_lookup.assign(p_generic_block_size, -1);

   std::uint32_t symmetric_count = 0;
   std::uint8_t local[3] {};
   std::uint8_t reflected[3] {};
   for (std::uint32_t raw = 0; raw < p_generic_block_size; ++raw) {
      unrank_ordered(raw, 103, p_tail_length, local);
      for (int i = 0; i < p_tail_length; ++i) {
         reflected[i] = reflect_local(local[i]);
      }
      if (raw <= rank_ordered(reflected, 103, p_tail_length)) {
         p_symmetric_lookup[raw] = static_cast<std::int32_t>(symmetric_count++);
      }
   }
   if (symmetric_count != p_symmetric_block_size) {
      throw std::logic_error("C2 quotient has the wrong size");
   }

   std::array<bool, Omega_Square_Count> unseen {{}};
   unseen.fill(true);
   std::vector<std::vector<std::uint8_t>> orbits;
   for (std::uint32_t seed = 0; seed < Omega_Square_Count; ++seed) {
      if (!unseen[seed]) continue;
      std::vector<std::uint8_t> orbit;
      for (int transform = 0; transform < 8; ++transform) {
         orbit.push_back(p_geometry.transforms()[transform][seed]);
      }
      std::sort(orbit.begin(), orbit.end());
      orbit.erase(std::unique(orbit.begin(), orbit.end()), orbit.end());
      for (std::uint8_t member : orbit) unseen[member] = false;
      orbits.push_back(std::move(orbit));
   }
   std::sort(orbits.begin(), orbits.end(), [](const std::vector<std::uint8_t> & first,
                                               const std::vector<std::uint8_t> & second) {
      return first.front() < second.front();
   });
   if (orbits.size() != 16) {
      throw std::logic_error("Omega board must have sixteen D4 square orbits");
   }

   std::uint32_t offset = 0;
   for (const std::vector<std::uint8_t> & orbit : orbits) {
      Block block;
      block.base_transform.fill(-1);
      block.representative = orbit.front();

      std::vector<std::uint8_t> stabilizer;
      for (int transform = 0; transform < 8; ++transform) {
         if (p_geometry.transforms()[transform][block.representative] == block.representative) {
            stabilizer.push_back(static_cast<std::uint8_t>(transform));
         }
      }
      if (stabilizer.size() != 1 && stabilizer.size() != 2) {
         throw std::logic_error("unexpected first-square stabilizer");
      }
      block.symmetric = stabilizer.size() == 2;

      for (std::uint8_t member : orbit) {
         for (int transform = 0; transform < 8; ++transform) {
            if (p_geometry.transforms()[transform][member] == block.representative) {
               block.base_transform[member] = static_cast<std::int8_t>(transform);
               break;
            }
         }
      }

      std::vector<std::uint8_t> locals;
      if (!block.symmetric) {
         for (std::uint32_t sq = 0; sq < Omega_Square_Count; ++sq) {
            if (sq != block.representative) {
               locals.push_back(static_cast<std::uint8_t>(sq));
            }
         }
      } else {
         const std::uint8_t reflection = stabilizer[1];
         std::vector<std::uint8_t> fixed;
         std::vector<std::pair<std::uint8_t, std::uint8_t>> pairs;
         std::array<bool, Omega_Square_Count> seen {{}};
         seen[block.representative] = true;

         for (std::uint32_t sq = 0; sq < Omega_Square_Count; ++sq) {
            if (sq != block.representative &&
                p_geometry.transforms()[reflection][sq] == sq) {
               fixed.push_back(static_cast<std::uint8_t>(sq));
               seen[sq] = true;
            }
         }
         for (std::uint32_t sq = 0; sq < Omega_Square_Count; ++sq) {
            if (seen[sq]) continue;
            const std::uint8_t partner = p_geometry.transforms()[reflection][sq];
            pairs.emplace_back(
               static_cast<std::uint8_t>(std::min<std::uint32_t>(sq, partner)),
               static_cast<std::uint8_t>(std::max<std::uint32_t>(sq, partner))
            );
            seen[sq] = true;
            seen[partner] = true;
         }
         std::sort(fixed.begin(), fixed.end());
         std::sort(pairs.begin(), pairs.end());
         if (fixed.size() != 11 || pairs.size() != 46) {
            throw std::logic_error("unexpected reflection orbit structure");
         }
         locals.insert(locals.end(), fixed.begin(), fixed.end());
         for (const auto & pair : pairs) locals.push_back(pair.first);
         for (const auto & pair : pairs) locals.push_back(pair.second);
      }

      if (locals.size() != 103) {
         throw std::logic_error("local-square map has the wrong size");
      }
      for (std::uint32_t local_index = 0; local_index < locals.size(); ++local_index) {
         block.to_local[locals[local_index]] = static_cast<std::uint8_t>(local_index);
      }
      block.offset = offset;
      const std::uint8_t block_index = static_cast<std::uint8_t>(p_blocks.size());
      for (std::uint8_t member : orbit) p_block_for_square[member] = block_index;
      p_blocks.push_back(block);
      offset += block.symmetric ? p_symmetric_block_size : p_generic_block_size;
   }

   p_state_count = offset * 2;
}

std::uint32_t D4_Indexer::rank(
   const std::uint8_t * squares,
   std::uint8_t turn
) const {
   if (turn > 1) throw std::invalid_argument("invalid D4 turn");
   for (int i = 0; i < p_piece_count; ++i) {
      if (squares[i] >= Omega_Square_Count) {
         throw std::invalid_argument("invalid D4 square");
      }
      for (int j = 0; j < i; ++j) {
         if (squares[i] == squares[j]) {
            throw std::invalid_argument("D4 squares are not distinct");
         }
      }
   }

   const Block & block = p_blocks[p_block_for_square[squares[0]]];
   const int transform = block.base_transform[squares[0]];
   if (transform < 0) throw std::logic_error("missing first-square transform");

   std::uint8_t local[3] {};
   for (int i = 0; i < p_tail_length; ++i) {
      const std::uint8_t transformed =
         p_geometry.transforms()[transform][squares[i + 1]];
      local[i] = block.to_local[transformed];
   }

   std::uint32_t raw = rank_ordered(local, 103, p_tail_length);
   std::uint32_t local_rank = raw;
   if (block.symmetric) {
      std::uint8_t reflected[3] {};
      for (int i = 0; i < p_tail_length; ++i) {
         reflected[i] = reflect_local(local[i]);
      }
      raw = std::min(raw, rank_ordered(reflected, 103, p_tail_length));
      const std::int32_t canonical = p_symmetric_lookup[raw];
      if (canonical < 0) {
         throw std::logic_error("canonical C2 tuple was not indexed");
      }
      local_rank = static_cast<std::uint32_t>(canonical);
   }
   return (block.offset + local_rank) * 2 + turn;
}

struct Indexers {
   Geometry geometry;
   D4_Indexer three;
   D4_Indexer four;

   Indexers() : geometry(), three(geometry, 3), four(geometry, 4) {
      if (three.state_count() != Three_Man_State_Count ||
          four.state_count() != Four_Man_State_Count) {
         throw std::logic_error("runtime D4 index population mismatch");
      }
   }
};

const Indexers & indexers() {
   static const Indexers instance;
   return instance;
}

std::string join_path(const std::string & directory, const std::string & file) {
   if (directory.empty()) return file;
   const char last = directory.back();
   if (last == '/' || last == '\\') return directory + file;
   return directory + "/" + file;
}

struct Indexed_Position {
   production_format::Material material { production_format::Material::None };
   std::array<std::uint8_t, 4> squares {{ 0, 0, 0, 0 }};
   std::uint8_t turn { 0 };
};

bool index_position(const Pos & pos, Indexed_Position & indexed) {
   if (!variant_is_omega() || square_size() != static_cast<int>(Omega_Square_Count)) {
      return false;
   }
   if (pos.ep_squares() != 0 || pos.castling_rooks(White) != 0 ||
       pos.castling_rooks(Black) != 0) {
      return false;
   }
   if (pos.count(King, White) != 1 || pos.count(King, Black) != 1) return false;

   const int total = bit::count(pos.pieces());
   const int rooks = pos.count(Rook, White) + pos.count(Rook, Black);
   const int champions = pos.count(Champion, White) + pos.count(Champion, Black);

   if (total == 3 && rooks == 1 && champions == 0) {
      const Side strong = pos.count(Rook, White) == 1 ? White : Black;
      const Side weak = side_opp(strong);
      indexed.material = production_format::Material::KRK;
      indexed.squares[0] = static_cast<std::uint8_t>(pos.king(strong));
      indexed.squares[1] = static_cast<std::uint8_t>(bit::first(pos.pieces(Rook, strong)));
      indexed.squares[2] = static_cast<std::uint8_t>(pos.king(weak));
      indexed.turn = pos.turn() == strong ? 0 : 1;
      return true;
   }

   if (total == 3 && rooks == 0 && champions == 1) {
      const Side strong = pos.count(Champion, White) == 1 ? White : Black;
      const Side weak = side_opp(strong);
      indexed.material = production_format::Material::KCK;
      indexed.squares[0] = static_cast<std::uint8_t>(pos.king(strong));
      indexed.squares[1] = static_cast<std::uint8_t>(bit::first(pos.pieces(Champion, strong)));
      indexed.squares[2] = static_cast<std::uint8_t>(pos.king(weak));
      indexed.turn = pos.turn() == strong ? 0 : 1;
      return true;
   }

   if (total == 4 && rooks == 1 && champions == 1) {
      const Side rook_side = pos.count(Rook, White) == 1 ? White : Black;
      const Side champion_side = pos.count(Champion, White) == 1 ? White : Black;
      if (rook_side == champion_side) return false;
      indexed.material = production_format::Material::KRKC;
      indexed.squares[0] = static_cast<std::uint8_t>(pos.king(rook_side));
      indexed.squares[1] = static_cast<std::uint8_t>(bit::first(pos.pieces(Rook, rook_side)));
      indexed.squares[2] = static_cast<std::uint8_t>(pos.king(champion_side));
      indexed.squares[3] = static_cast<std::uint8_t>(
         bit::first(pos.pieces(Champion, champion_side))
      );
      indexed.turn = pos.turn() == rook_side ? 0 : 1;
      return true;
   }

   return false;
}

} // namespace

struct Runtime_Tablebases::Table_Set {
   std::string directory;
   production_format::Table krk;
   production_format::Table kck;
   production_format::Table krkc;

   const production_format::Table * table(production_format::Material material) const {
      switch (material) {
         case production_format::Material::KRK:  return &krk;
         case production_format::Material::KCK:  return &kck;
         case production_format::Material::KRKC: return &krkc;
         default:                                 return nullptr;
      }
   }
};

Runtime_Tablebases G_Tablebases;

const char * production_file_name(production_format::Material material) {
   switch (material) {
      case production_format::Material::KRK:  return "omega-krk-wdl-v1.omtb";
      case production_format::Material::KCK:  return "omega-kck-wdl-v1.omtb";
      case production_format::Material::KRKC: return "omega-krkc-wdl-v1.omtb";
      default:                                 return "";
   }
}

Runtime_Tablebases::Runtime_Tablebases() : p_tables() {
}

Configure_Result Runtime_Tablebases::configure(const std::string & requested_directory) {
   Configure_Result result;
   const std::string directory = requested_directory == "<empty>"
                               ? std::string()
                               : requested_directory;
   const std::shared_ptr<const Table_Set> previous = std::atomic_load(&p_tables);

   if (directory.empty()) {
      std::atomic_store(&p_tables, std::shared_ptr<const Table_Set>());
      result.ok = true;
      result.disabled = true;
      result.message = "Omega tablebases disabled";
      return result;
   }

   std::shared_ptr<Table_Set> next(new Table_Set());
   next->directory = directory;
   const production_format::Material materials[] {
      production_format::Material::KRK,
      production_format::Material::KCK,
      production_format::Material::KRKC,
   };

   std::uint64_t payload_bytes = 0;
   bool any_dtz = false;
   for (production_format::Material material : materials) {
      production_format::Table * table = nullptr;
      switch (material) {
         case production_format::Material::KRK:  table = &next->krk; break;
         case production_format::Material::KCK:  table = &next->kck; break;
         case production_format::Material::KRKC: table = &next->krkc; break;
         default: break;
      }

      std::string error;
      const std::string file_name = production_file_name(material);
      const std::string file_path = join_path(directory, file_name);
      bool file_loaded = false;
      if (table != nullptr) {
         try {
            file_loaded = production_format::read_file(
               file_path,
               production_format::canonical_requirements(material, false),
               *table,
               error
            );
         } catch (const std::exception & exception) {
            error = std::string("loader exception: ") + exception.what();
         } catch (...) {
            error = "unknown loader exception";
         }
      }
      if (!file_loaded) {
         result.ok = false;
         result.retained_previous = previous != nullptr;
         result.message = "Omega tablebase load failed for " + file_name + ": " + error;
         if (result.retained_previous) result.message += "; previous tables retained";
         return result;
      }
      payload_bytes += table->wdl.size();
      payload_bytes += table->dtz.size() * sizeof(std::uint16_t);
      any_dtz = any_dtz || table->metadata.has_dtz;
   }

   std::atomic_store(&p_tables, std::shared_ptr<const Table_Set>(next));
   result.ok = true;
   result.message = "Omega tablebases loaded: KRK, KCK, KRKC (" +
                    std::to_string(payload_bytes / (1024 * 1024)) + " MiB payload";
   if (any_dtz) result.message += "; DTZ payload present but not used by search";
   result.message += ")";
   return result;
}

bool Runtime_Tablebases::probe(const Pos & pos, Probe & result) const {
   const std::shared_ptr<const Table_Set> tables = std::atomic_load(&p_tables);
   if (tables == nullptr) return false;

   Indexed_Position indexed;
   if (!index_position(pos, indexed)) return false;

   const production_format::Table * table = tables->table(indexed.material);
   if (table == nullptr) return false;

   std::uint32_t index = 0;
   try {
      index = dense_index(indexed.material, indexed.squares, indexed.turn);
   } catch (const std::exception &) {
      return false;
   }
   if (index >= table->wdl.size()) return false;

   const production_format::Wdl wdl =
      static_cast<production_format::Wdl>(table->wdl[index]);
   if (wdl == production_format::Wdl::Invalid) return false;

   result.material = indexed.material;
   result.wdl = wdl;
   result.index = index;
   result.has_dtz = table->metadata.has_dtz;
   return true;
}

bool Runtime_Tablebases::loaded() const {
   return std::atomic_load(&p_tables) != nullptr;
}

std::string Runtime_Tablebases::path() const {
   const std::shared_ptr<const Table_Set> tables = std::atomic_load(&p_tables);
   return tables == nullptr ? std::string() : tables->directory;
}

bool probe_search_draw(const Pos & pos, Probe * result) {
   // Native automatic draws and repetition always have first claim.
   if (pos.is_draw()) return false;

   Probe probe;
   if (!G_Tablebases.probe(pos, probe)) return false;
   if (probe.wdl != production_format::Wdl::Draw) return false;

   // The production index also contains terminal placements.  Search must
   // still report native mate/stalemate rather than replacing them with WDL.
   List legal;
   gen_legals(legal, pos);
   if (legal.size() == 0) return false;

   if (result != nullptr) *result = probe;
   return true;
}

std::uint32_t dense_index(
   production_format::Material material,
   const std::array<std::uint8_t, 4> & squares,
   std::uint8_t turn
) {
   const Indexers & retained = indexers();
   switch (material) {
      case production_format::Material::KRK:
      case production_format::Material::KCK:
         return retained.three.rank(squares.data(), turn);
      case production_format::Material::KRKC:
         return retained.four.rank(squares.data(), turn);
      default:
         throw std::invalid_argument("material has no Omega D4 index");
   }
}

} // namespace omega_tb
