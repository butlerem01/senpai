#include "omega_book.hpp"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <fstream>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "fen.hpp"
#include "list.hpp"
#include "move.hpp"
#include "pos.hpp"
#include "util.hpp"

namespace omega_book {

namespace {

const char Format_Header[] = "senpai-omega-book-v1";

struct Entry {
   std::string move;
   std::uint32_t weight;
};

std::string canonical_book_key(Ofen_Position pos) {
   // The FEN fullmove field is notation metadata, not chess state.  CoreChess
   // historically advances it after every ply while Senpai follows the FEN
   // convention and advances it after Black.  Normalising only this field
   // keeps books interoperable without weakening halfmove/draw-state safety.
   pos.fullmove_number = 1;
   return ofen_serialize(pos);
}

std::string canonical_book_key(const Pos & pos) {
   return canonical_book_key(ofen_parse(ofen_serialize(pos)));
}

bool coordinate_is_ok(const std::string & value) {
   if (value.size() != 2) return false;

   if (value[0] == 'w') {
      return value[1] >= '1' && value[1] <= '4';
   }

   return value[0] >= 'a' && value[0] <= 'j'
       && value[1] >= '0' && value[1] <= '9';
}

bool move_syntax_is_ok(const std::string & value) {
   if (value.size() != 4 && value.size() != 5) return false;
   if (!coordinate_is_ok(value.substr(0, 2))) return false;
   if (!coordinate_is_ok(value.substr(2, 2))) return false;

   if (value.size() == 5) {
      const char promotion = value[4];
      if (promotion != 'q' && promotion != 'r' && promotion != 'b'
       && promotion != 'n' && promotion != 'c' && promotion != 'w') {
         return false;
      }
   }

   return true;
}

bool parse_weight(const std::string & value, std::uint32_t & weight) {
   if (value.empty()) return false;

   std::uint64_t parsed = 0;
   for (char c : value) {
      if (c < '0' || c > '9') return false;
      parsed = parsed * 10 + std::uint64_t(c - '0');
      if (parsed > std::numeric_limits<std::uint32_t>::max()) return false;
   }

   if (parsed == 0) return false;
   weight = static_cast<std::uint32_t>(parsed);
   return true;
}

Configure_Result failure(
   const std::string & detail,
   bool retained_previous
) {
   Configure_Result result;
   result.retained_previous = retained_previous;
   result.message = "Omega opening book load failed: " + detail;
   if (result.retained_previous) result.message += "; previous book retained";
   return result;
}

} // namespace

struct Runtime_Book::Book_Data {
   std::string path;
   std::map<std::string, std::vector<Entry>> positions;
   std::size_t move_count { 0 };
};

Runtime_Book G_Book;

Runtime_Book::Runtime_Book() : p_book() {
}

Configure_Result Runtime_Book::configure(const std::string & requested_file) {
   const std::string file_name = requested_file == "<empty>"
                               ? std::string()
                               : requested_file;
   const std::shared_ptr<const Book_Data> previous = std::atomic_load(&p_book);

   if (file_name.empty()) {
      std::atomic_store(&p_book, std::shared_ptr<const Book_Data>());

      Configure_Result result;
      result.ok = true;
      result.disabled = true;
      result.message = "Omega opening book disabled";
      return result;
   }

   std::ifstream input(file_name.c_str(), std::ios::binary);
   if (!input) return failure("cannot open " + file_name, previous != nullptr);

   std::shared_ptr<Book_Data> next(new Book_Data());
   next->path = file_name;

   std::set<std::pair<std::string, std::string>> seen;
   bool saw_header = false;
   std::string line;
   std::size_t line_number = 0;

   try {
      while (std::getline(input, line)) {
         line_number++;
         if (!line.empty() && line.back() == '\r') line.pop_back();

         if (line_number == 1 && line.size() >= 3
          && static_cast<unsigned char>(line[0]) == 0xEF
          && static_cast<unsigned char>(line[1]) == 0xBB
          && static_cast<unsigned char>(line[2]) == 0xBF) {
            line.erase(0, 3);
         }

         if (line.empty() || line[0] == '#') continue;

         if (!saw_header) {
            if (line != Format_Header) {
               return failure(
                  "line " + std::to_string(line_number) + " has no v1 header",
                  previous != nullptr
               );
            }
            saw_header = true;
            continue;
         }

         const std::string::size_type first_tab = line.find('\t');
         const std::string::size_type second_tab = first_tab == std::string::npos
                                                  ? std::string::npos
                                                  : line.find('\t', first_tab + 1);

         if (first_tab == std::string::npos || second_tab == std::string::npos
          || line.find('\t', second_tab + 1) != std::string::npos) {
            return failure(
               "line " + std::to_string(line_number) + " must have three tab-separated fields",
               previous != nullptr
            );
         }

         const std::string source_ofen = line.substr(0, first_tab);
         const std::string move_text = line.substr(first_tab + 1, second_tab - first_tab - 1);
         const std::string weight_text = line.substr(second_tab + 1);

         if (!move_syntax_is_ok(move_text)) {
            return failure(
               "line " + std::to_string(line_number) + " has invalid Omega UCI move syntax",
               previous != nullptr
            );
         }

         std::uint32_t weight = 0;
         if (!parse_weight(weight_text, weight)) {
            return failure(
               "line " + std::to_string(line_number) + " has invalid weight",
               previous != nullptr
            );
         }

         const std::string canonical_ofen = canonical_book_key(ofen_parse(source_ofen));
         const std::pair<std::string, std::string> identity(canonical_ofen, move_text);
         if (!seen.insert(identity).second) {
            return failure(
               "line " + std::to_string(line_number) + " duplicates a position and move",
               previous != nullptr
            );
         }

         next->positions[canonical_ofen].push_back(Entry { move_text, weight });
         next->move_count++;
      }
   } catch (const Bad_Input &) {
      return failure(
         "line " + std::to_string(line_number) + " has invalid OFEN",
         previous != nullptr
      );
   } catch (const std::exception & exception) {
      return failure(
         "line " + std::to_string(line_number) + ": " + exception.what(),
         previous != nullptr
      );
   }

   if (!input.eof()) return failure("read error in " + file_name, previous != nullptr);
   if (!saw_header) return failure("missing v1 header", previous != nullptr);
   if (next->move_count == 0) return failure("book contains no moves", previous != nullptr);

   for (auto & position : next->positions) {
      std::sort(
         position.second.begin(), position.second.end(),
         [](const Entry & left, const Entry & right) {
            if (left.weight != right.weight) return left.weight > right.weight;
            return left.move < right.move;
         }
      );
   }

   std::atomic_store(&p_book, std::shared_ptr<const Book_Data>(next));

   Configure_Result result;
   result.ok = true;
   result.message = "Omega opening book loaded: "
                  + std::to_string(next->positions.size()) + " positions, "
                  + std::to_string(next->move_count) + " moves from " + file_name;
   return result;
}

bool Runtime_Book::probe(const Pos & pos, const List & legal, Move & selected) const {
   selected = move::None;
   if (!variant_is_omega()) return false;

   const std::shared_ptr<const Book_Data> book = std::atomic_load(&p_book);
   if (book == nullptr) return false;

   std::string key;
   try {
      key = canonical_book_key(pos);
   } catch (const Bad_Input &) {
      return false;
   }

   const auto position = book->positions.find(key);
   if (position == book->positions.end()) return false;

   for (const Entry & entry : position->second) {
      for (int i = 0; i < legal.size(); i++) {
         const Move candidate = legal[i];
         if (move::to_uci(candidate, pos) == entry.move) {
            selected = candidate;
            return true;
         }
      }
   }

   return false;
}

bool Runtime_Book::loaded() const {
   return std::atomic_load(&p_book) != nullptr;
}

std::string Runtime_Book::path() const {
   const std::shared_ptr<const Book_Data> book = std::atomic_load(&p_book);
   return book == nullptr ? std::string() : book->path;
}

} // namespace omega_book
