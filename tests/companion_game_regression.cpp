#define main omega_companion_program
#include "../src/omega.cpp"
#undef main

#include <cassert>
#include <sstream>
#include <string>
#include <vector>

static bool has_move(const std::vector<omega::Move> & moves, const std::string & text) {
   for (std::size_t i = 0; i < moves.size(); i++)
      if (omega::move_name(moves[i]) == text) return true;
   return false;
}

int main() {
   static const char * moves[] = {
      "f1f2", "j9h7", "w1a2", "f8f7", "j0h2", "w3j7", "a2b5", "a9c7",
      "w2j2", "j7i6", "e0i4", "i6f5", "i4f4", "w4a7", "j2g3", "a7d6",
      "f4f3", "c7e5", "b5a6", "b9a9", "g3f6", "e9h6", "h2f4", "f5i4",
      "f6g9", "f9g9", "f3d3", "h6f4", "d3d6", "e8e7", "d6b8", "e5c7",
      "i1i2", "c9d7", "b8b2", "d7c5", "a6d5", "c7d7", "e1e3", "f4g5",
      "d5c4", "d7d6", "j1j3", "i4h5", "c0d2", "f7f6", "b2a3", "c5e6",
      "h0g2", "a8a5"
   };

   omega::Position pos;
   for (std::size_t i = 0; i < sizeof(moves) / sizeof(moves[0]); i++) {
      omega::Move move = omega::parse_move(pos, moves[i]);
      assert(move.from >= 0);
      pos = pos.play(move);
   }

   const std::string final_ofen =
      "r2b2knr1/2pp2pppp/4p2c2/3cnp4/p5qw2/2W7/Q3P4P/3N1PN1P1/"
      "PPPP2PP2/CR1B1KB1R1[-/-/-/-] w KQ a7,a6 0 26";
   assert(pos.ofen() == final_ofen);

   std::vector<omega::Move> legal = pos.legal();
   assert(legal.size() == 57);
   assert(has_move(legal, "a3a5"));
   assert(has_move(legal, "c4f5"));
   assert(has_move(legal, "d2f3"));

   omega::Search_Result result = omega::search_root(pos, 3);
   assert(result.move.from >= 0);
   assert(omega::move_name(result.move) == "d2f3");
   assert(result.score == 525);
   assert(result.pv.size() == 3);
   assert(omega::move_name(result.pv[0]) == "d2f3");
   assert(omega::move_name(result.pv[1]) == "g5h6");
   assert(omega::move_name(result.pv[2]) == "a3d6");

   // A failed OFEN load must not leave behind a half-parsed position.
   assert(!pos.load("not an OFEN"));
   assert(pos.ofen() == final_ofen);

   // Exercise the UCI boundary: the valid replay must report a PV and may not
   // claim that this 57-move position has no legal move.
   std::ostringstream input;
   input << "position startpos moves";
   for (std::size_t i = 0; i < sizeof(moves) / sizeof(moves[0]); i++) input << ' ' << moves[i];
   input << "\ngo depth 3\nquit\n";

   std::istringstream uci_input(input.str());
   std::ostringstream uci_output;
   std::streambuf * old_input = std::cin.rdbuf(uci_input.rdbuf());
   std::streambuf * old_output = std::cout.rdbuf(uci_output.rdbuf());
   int exit_code = omega_companion_program();
   std::cin.rdbuf(old_input);
   std::cout.rdbuf(old_output);
   std::cin.clear();
   std::cout.clear();

   assert(exit_code == 0);
   const std::string transcript = uci_output.str();
   assert(transcript.find("info depth 3 score cp 525 pv d2f3 g5h6 a3d6") != std::string::npos);
   assert(transcript.find("bestmove d2f3 ponder g5h6") != std::string::npos);
   assert(transcript.find("bestmove 0000") == std::string::npos);

   return 0;
}
