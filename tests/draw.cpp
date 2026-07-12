#define DEBUG

#include <cassert>
#include <string>

#include "../src/bit.hpp"
#include "../src/fen.hpp"
#include "../src/hash.hpp"
#include "../src/pos.hpp"

static Pos make(const std::string & placement) {
   return pos_from_fen(placement + " w - - 0 1", Omega);
}

int main() {
   bit::init(Omega);
   hash::init();

   const char * middle = "/10/10/10/10/10/10/10/10/";

   Pos bare = make(std::string("5k4") + middle + "5K4[-/-/-/-]");
   Pos champion = make(std::string("5k4") + middle + "C4K4[-/-/-/-]");
   Pos wizard = make(std::string("5k4") + middle + "5K4[W/-/-/-]");
   Pos rook = make(std::string("5k4") + middle + "R4K4[-/-/-/-]");
   Pos two_minors = make(std::string("5k4") + middle + "C4K4[W/-/-/-]");

   assert(bare.is_draw());
   assert(champion.is_draw());
   assert(wizard.is_draw());
   assert(!rook.is_draw());
   assert(!two_minors.is_draw());

   return 0;
}
