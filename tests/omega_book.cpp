#define DEBUG

#include <cassert>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/gen.hpp"
#include "../src/hash.hpp"
#include "../src/list.hpp"
#include "../src/math.hpp"
#include "../src/move.hpp"
#include "../src/omega_book.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"
#include "../src/search.hpp"
#include "../src/tt.hpp"
#include "../src/var.hpp"

namespace {

const std::string Good_File { "omega-book-good-test.obk" };
const std::string Bad_File { "omega-book-bad-test.obk" };

void write_lines(const std::string & path, const std::vector<std::string> & lines) {
   std::ofstream output(path.c_str(), std::ios::binary | std::ios::trunc);
   assert(output);
   for (const std::string & line : lines) output << line << "\n";
   output.close();
   assert(output);
}

std::string record(const std::string & ofen, const std::string & move, const std::string & weight) {
   return ofen + "\t" + move + "\t" + weight;
}

void write_good() {
   write_lines(Good_File, {
      "# generated test fixture",
      "senpai-omega-book-v1",
      // The highest-weight move is deliberately illegal in this position.
      // Equal legal weights must then resolve by lexical UCI order, not file order.
      record(Omega_Start_OFEN, "b0c0", "200"),
      record(Omega_Start_OFEN, "a0a9", "100"),
      record(Omega_Start_OFEN, "f1f2", "20"),
      record(Omega_Start_OFEN, "a0c2", "20"),
   });
}

void require_retained_failure(const std::vector<std::string> & lines) {
   write_lines(Bad_File, lines);
   const omega_book::Configure_Result result = omega_book::G_Book.configure(Bad_File);
   assert(!result.ok);
   assert(result.retained_previous);
   assert(omega_book::G_Book.loaded());
   assert(omega_book::G_Book.path() == Good_File);
}

struct Cleanup {
   ~Cleanup() {
      omega_book::G_Book.configure("");
      std::remove(Good_File.c_str());
      std::remove(Bad_File.c_str());
   }
};

} // namespace

int main() {
   Cleanup cleanup;

   math::init();
   bit::init(Chess);
   hash::init();
   pawn::init();
   pos::init();
   var::init();

   assert(!omega_book::G_Book.loaded());

   // Loading is independent of the currently active geometry.  UCI GUIs are
   // therefore free to send OmegaBookFile before UCI_Variant.
   write_good();
   omega_book::Configure_Result loaded = omega_book::G_Book.configure(Good_File);
   assert(loaded.ok);
   assert(!loaded.disabled);
   assert(!loaded.retained_previous);
   assert(omega_book::G_Book.loaded());
   assert(omega_book::G_Book.path() == Good_File);

   var::set("Threads", "1");
   var::set("UCI_Variant", "omega");
   var::set("OwnBook", "true");
   var::update();
   bit::init(Omega);
   pawn::init();
   clear_pawn_table();
   tt::G_TT.set_size(1 << 12);

   Pos start = pos_from_fen(Omega_Start_OFEN, Omega);
   List legal;
   gen_legals(legal, start);

   Move selected = move::None;
   assert(omega_book::G_Book.probe(start, legal, selected));
   assert(move::to_uci(selected, start) == "a0c2");
   assert(list::has(legal, selected));

   // Halfmove draw state, turn, castling, and EP remain exact.  Fullmove is
   // notation metadata and is deliberately normalised for GUI compatibility.
   Pos different_halfmove = pos_from_fen(
      "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 1 1",
      Omega
   );
   assert(!omega_book::G_Book.probe(different_halfmove, legal, selected));

   Pos different_fullmove = pos_from_fen(
      "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 37",
      Omega
   );
   assert(omega_book::G_Book.probe(different_fullmove, legal, selected));
   assert(move::to_uci(selected, different_fullmove) == "a0c2");

   Move first = move::from_uci("f1f2", start);
   Pos after = start.succ(first);
   List after_legal;
   gen_legals(after_legal, after);
   assert(!omega_book::G_Book.probe(after, after_legal, selected));

   // A malformed replacement never partially replaces the serving book.
   require_retained_failure({ "not-the-header" });
   require_retained_failure({
      "senpai-omega-book-v1",
      record("not an ofen", "a0c2", "1"),
   });
   require_retained_failure({
      "senpai-omega-book-v1",
      record(Omega_Start_OFEN, "A0c2", "1"),
   });
   require_retained_failure({
      "senpai-omega-book-v1",
      record(Omega_Start_OFEN, "a0c2", "0"),
   });
   require_retained_failure({
      "senpai-omega-book-v1",
      record(Omega_Start_OFEN, "a0c2", "1"),
      record(Omega_Start_OFEN, "a0c2", "2"),
   });
   require_retained_failure({
      "senpai-omega-book-v1",
      record(Omega_Start_OFEN, "a0c2", "1"),
      record(
         "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 37",
         "a0c2", "2"
      ),
   });

   assert(omega_book::G_Book.probe(start, legal, selected));
   assert(move::to_uci(selected, start) == "a0c2");

   // The search hook uses the book only when OwnBook is enabled.
   Search_Input book_input;
   book_input.nodes = 31;
   book_input.move = true;
   book_input.smart = false;

   Search_Output book_output;
   search(book_output, start, book_input);
   assert(book_output.move == selected);
   assert(book_output.node == 0);
   assert(book_output.depth == Depth(0));

   var::set("OwnBook", "false");
   var::update();
   tt::G_TT.clear();

   Search_Output search_output;
   search(search_output, start, book_input);
   assert(search_output.node == book_input.nodes);
   assert(list::has(legal, search_output.move));

   // A loaded Omega book is always dormant under standard-chess geometry.
   var::set("OwnBook", "true");
   var::set("UCI_Variant", "chess");
   var::update();
   bit::init(Chess);
   pawn::init();

   Pos chess = pos_from_fen(Start_FEN, Chess);
   List chess_legal;
   gen_legals(chess_legal, chess);
   assert(!omega_book::G_Book.probe(chess, chess_legal, selected));

   const omega_book::Configure_Result disabled = omega_book::G_Book.configure("<empty>");
   assert(disabled.ok);
   assert(disabled.disabled);
   assert(!omega_book::G_Book.loaded());

   return 0;
}
