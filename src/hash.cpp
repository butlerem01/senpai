
// includes

#include "bit.hpp"
#include "common.hpp"
#include "hash.hpp"
#include "libmy.hpp"
#include "pos.hpp"

namespace hash {

// variables

static Key Key_Turn;
static Key Key_Piece[Side_Size][Piece_Size_2][Square_Capacity];
static Key Key_Castling[Side_Size][File_Capacity];
static Key Key_En_Passant[File_Capacity];
static Key Key_En_Passant_Square[Square_Capacity];

// functions

void init() {

   // hash keys

   Key_Turn = Key(ml::rand_int_64());

   // Generate the original 6 x 2 x 64 keys and eight file keys in the
   // original order.  Besides keeping standard-chess hashes stable, this
   // avoids needlessly perturbing transposition-table behaviour.

   const int Chess_Piece_Size { 6 };

   for (int pc = 0; pc < Chess_Piece_Size; pc++) {
      for (int sd = 0; sd < Side_Size; sd++) {
         for (int sq = 0; sq < Square_Size; sq++) {
            Key_Piece[sd][pc][sq] = Key(ml::rand_int_64());
         }
      }
   }

   for (int fl = 0; fl < File_Size; fl++) {

      Key_Castling[White][fl] = Key(ml::rand_int_64());
      Key_Castling[Black][fl] = Key(ml::rand_int_64());

      Key_En_Passant[fl] = Key(ml::rand_int_64());
   }

   // Omega-only piece/square and file keys follow the legacy sequence.

   for (int pc = 0; pc < Chess_Piece_Size; pc++) {
      for (int sd = 0; sd < Side_Size; sd++) {
         for (int sq = Square_Size; sq < Square_Capacity; sq++) {
            Key_Piece[sd][pc][sq] = Key(ml::rand_int_64());
         }
      }
   }

   for (int pc = Chess_Piece_Size; pc < Piece_Size; pc++) {
      for (int sd = 0; sd < Side_Size; sd++) {
         for (int sq = 0; sq < Square_Capacity; sq++) {
            Key_Piece[sd][pc][sq] = Key(ml::rand_int_64());
         }
      }
   }

   for (int fl = File_Size; fl < File_Capacity; fl++) {

      Key_Castling[White][fl] = Key(ml::rand_int_64());
      Key_Castling[Black][fl] = Key(ml::rand_int_64());

      Key_En_Passant[fl] = Key(ml::rand_int_64());
   }

   for (int sq = 0; sq < Square_Capacity; sq++) {
      Key_En_Passant_Square[sq] = Key(ml::rand_int_64());
   }
}

Key key(const Pos & pos) {
   return key_full(pos);
}

Key key_piece(const Pos & pos) {

   Key key = Key(0);

   // pieces

   for (int s = 0; s < Side_Size; s++) {

      Side sd = side_make(s);

      for (Bit b = pos.pieces(sd); b != 0; b = bit::rest(b)) {

         Square sq = bit::first(b);
         Piece  pc = pos.piece(sq);

         key ^= key_piece(pc, sd, sq);
      }
   }

   // turn

   key ^= key_turn(pos.turn());

   return key;
}

Key key_pawn(const Pos & pos) {

   Key key = Key(0);

   // pawns

   for (int s = 0; s < Side_Size; s++) {

      Side sd = side_make(s);

      for (Bit b = pos.pawns(sd); b != 0; b = bit::rest(b)) {

         Square sq = bit::first(b);
         Piece  pc = pos.piece(sq);

         key ^= key_piece(pc, sd, sq);
      }
   }

   return key;
}

Key key_full(const Pos & pos) {

   Key key = key_piece(pos);

   // castling

   key ^= hash::key_castling(White, pos.castling_rooks(White));
   key ^= hash::key_castling(Black, pos.castling_rooks(Black));

   // en passant

   for (Bit b = pos.ep_squares(); b != 0; b = bit::rest(b)) {
      key ^= hash::key_en_passant(bit::first(b));
   }

   return key;
}

Key key_turn() {
   return Key_Turn;
}

Key key_turn(Side sd) {
   return (sd == White) ? Key(0) : Key_Turn;
}

Key key_piece(Piece pc, Side sd, Square sq) {
   assert(pc != Piece_None);
   return Key_Piece[sd][pc][sq];
}

Key key_castling(Side sd, Bit rooks) {

   Key key = Key(0);

   for (Bit b = rooks; b != 0; b = bit::rest(b)) {
      Square sq = bit::first(b);
      key ^= Key_Castling[sd][square_file(sq)];
   }

   return key;
}

Key key_en_passant(File fl) {
   return Key_En_Passant[fl];
}

Key key_en_passant(Square sq) {
   assert(square_is_ok(sq));
   assert(!square_is_corner(sq));
   return variant_is_omega() ? Key_En_Passant_Square[sq]
                             : key_en_passant(square_file(sq));
}

int index(Key key, int mask) {
   return int(key) & mask;
}

uint32 lock(Key key) {
   return uint32(uint64(key) >> 32);
}

}

