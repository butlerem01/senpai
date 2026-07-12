#define DEBUG

#include <cassert>
#include "../src/bit.hpp"

int main() {
   Bit low(1);
   Bit high = low << 100;
   assert(high.lo() == 0);
   assert(high.hi() == (uint64(1) << 36));
   assert((high >> 100) == low);
   assert((high | low) != 0);
   assert(bit::count(high | low) == 2);
   assert(bit::first(high) == Square(100));
   assert(bit::rest(high | low) == high);
   return 0;
}
