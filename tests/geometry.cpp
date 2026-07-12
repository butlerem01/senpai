#define DEBUG

#include <cassert>

#include "../src/bit.hpp"
#include "../src/common.hpp"

int main() {

   // Standard chess keeps Senpai's original numbering and notation.

   assert(variant() == Chess);
   assert(file_size() == 8);
   assert(rank_size() == 8);
   assert(square_size() == 64);
   assert(square_make(File_A, Rank_1) == 0);
   assert(square_make(File_H, Rank_8) == 63);
   assert(square_to_string(square_make(File_A, Rank_1)) == "a1");
   assert(square_from_string("h8") == square_make(File_H, Rank_8));

   bit::init(Chess);
   assert(bit::count(bit::Board_Squares) == 64);
   assert(bit::Board_Squares.hi() == 0);
   assert(bit::count(bit::knight_attacks(square_from_string("a1"))) == 2);

   // Omega regular squares are file-major 0..99 and corners are 100..103.

   bit::init(Omega);
   assert(variant() == Omega);
   assert(file_size() == 10);
   assert(rank_size() == 10);
   assert(regular_square_size() == 100);
   assert(square_size() == 104);
   assert(square_make(File_A, Rank_1) == 0);
   assert(square_make(File_A, Rank_2) == 1);
   assert(square_make(File_J, Rank_10) == 99);
   assert(square_from_corner(Corner_SW) == 100);
   assert(square_from_corner(Corner_NW) == 103);
   assert(square_to_string(square_make(File_A, Rank_1)) == "a0");
   assert(square_to_string(square_make(File_J, Rank_10)) == "j9");
   assert(square_to_string(square_from_corner(Corner_SW)) == "w1");
   assert(square_from_string("w4") == square_from_corner(Corner_NW));
   assert(square_from_coordinates(-1, -1) == square_from_corner(Corner_SW));
   assert(square_from_coordinates(10, 10) == square_from_corner(Corner_NE));
   assert(square_file(square_from_corner(Corner_SW)) == -1);
   assert(square_rank(square_from_corner(Corner_NE)) == 10);

   assert(bit::count(bit::Board_Squares) == 104);
   assert(bit::count(bit::Promotion_Squares) == 20);

   // The detached squares participate in leaper and diagonal topology.

   Square a1 = square_from_string("a1");
   Square w1 = square_from_string("w1");
   assert(square_add(a1, vector_make(-1, -2)) == w1);
   assert(square_add(w1, vector_make(+1, +3)) == square_from_string("a2"));
   assert(bit::has(bit::knight_attacks(a1), w1));
   assert(bit::has(bit::piece_attacks(Bishop, w1), square_from_string("a0")));
   assert(bit::has(bit::piece_attacks(Bishop, w1), square_from_string("j9")));
   assert(bit::has(bit::piece_attacks(Bishop, w1), square_from_string("w3")));
   assert(bit::count(bit::piece_attacks(Champion, square_from_string("e4"))) == 12);
   assert(bit::count(bit::piece_attacks(Wizard, square_from_string("e4"))) == 12);
   assert(bit::count(bit::piece_attacks(Wizard, w1)) == 3);
   assert(bit::has(bit::piece_attacks(Wizard, w1), square_from_string("a0")));
   assert(bit::has(bit::piece_attacks(Wizard, w1), square_from_string("a2")));
   assert(bit::has(bit::piece_attacks(Wizard, w1), square_from_string("c0")));

   Bit blocker = bit::bit(square_from_string("a0"));
   Bit attacks = bit::bishop_attacks(w1, blocker);
   assert(bit::has(attacks, square_from_string("a0")));
   assert(!bit::has(attacks, square_from_string("b1")));

   // Omega pawns retain one-step bulk geometry and cache the three-square
   // initial path for later legal-move generation.

   Bit pawn_path = bit::pawn_moves(White, square_from_string("a1"));
   assert(bit::count(pawn_path) == 3);
   assert(bit::has(pawn_path, square_from_string("a2")));
   assert(bit::has(pawn_path, square_from_string("a3")));
   assert(bit::has(pawn_path, square_from_string("a4")));
   assert(bit::pawn_moves(White, bit::bit(square_from_string("a1"))) == bit::bit(square_from_string("a2")));

   // Reinitialising after a variant switch must not retain Omega geometry.

   bit::init(Chess);
   assert(square_size() == 64);
   assert(bit::count(bit::Board_Squares) == 64);
   assert(bit::Board_Squares.hi() == 0);
   assert(bit::count(bit::knight_attacks(square_from_string("a1"))) == 2);

   return 0;
}
