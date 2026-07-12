#define main omega_companion_program
#include "../src/omega.cpp"
#undef main

#include <cassert>

static unsigned long long perft(const omega::Position & pos, int depth) {
   if (depth == 0) return 1;

   std::vector<omega::Move> moves = pos.legal();
   unsigned long long nodes = 0;
   for (const omega::Move & move : moves) nodes += perft(pos.play(move), depth - 1);
   return nodes;
}

int main() {
   omega::Position pos;
   assert(pos.legal().size() == 40);
   assert(perft(pos, 2) == 1600);
   assert(perft(pos, 3) == 67202);
   return 0;
}
