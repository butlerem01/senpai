#define DEBUG

#include <cassert>

#include "../src/bit.hpp"
#include "../src/common.hpp"
#include "../src/eval.hpp"
#include "../src/hash.hpp"
#include "../src/move.hpp"
#include "../src/pos.hpp"

int main() {

   assert(Piece_Size == 8);
   assert(Piece_Size_2 >= Piece_Size);
   assert(Piece_Side_Size == 16);
   assert(Piece_None == 8);
   assert(Empty == 16);

   assert(piece_to_char(Champion) == 'C');
   assert(piece_to_char(Wizard) == 'W');
   assert(piece_from_char('C') == Champion);
   assert(piece_from_char('W') == Wizard);
   assert(piece_is_minor(Champion));
   assert(piece_is_minor(Wizard));

   assert(piece_side_make(Champion, White) == 12);
   assert(piece_side_make(Champion, Black) == 13);
   assert(piece_side_make(Wizard, White) == 14);
   assert(piece_side_make(Wizard, Black) == 15);
   assert(piece_side_piece(piece_side_make(Champion, Black)) == Champion);
   assert(piece_side_side(piece_side_make(Wizard, Black)) == Black);

   bit::init(Omega);
   hash::init();

   Bit piece_side[Piece_Side_Size];
   for (int ps = 0; ps < Piece_Side_Size; ps++) piece_side[ps] = Bit(0);

   Square white_king = square_from_string("f0");
   Square black_king = square_from_string("f9");
   Square champion = square_from_string("j9");
   Square wizard = square_from_string("w4");
   Square pawn = square_from_string("a8");

   bit::set(piece_side[piece_side_make(King, White)], white_king);
   bit::set(piece_side[piece_side_make(King, Black)], black_king);
   bit::set(piece_side[piece_side_make(Champion, White)], champion);
   bit::set(piece_side[piece_side_make(Wizard, Black)], wizard);
   bit::set(piece_side[piece_side_make(Pawn, White)], pawn);

   Pos pos(White, piece_side, Bit(0));

   assert(pos.count(Champion, White) == 1);
   assert(pos.count(Wizard, Black) == 1);
   assert(pos.piece(champion) == Champion);
   assert(pos.piece(wizard) == Wizard);
   assert(pos.side(champion) == White);
   assert(pos.side(wizard) == Black);
   assert(pos.is_empty(square_from_string("i9")));
   assert(bit::has(pos.pieces(Champion, White), champion));
   assert(bit::has(pos.pieces(Wizard, Black), wizard));

   assert(pos.key() == hash::key(pos));
   assert(pos.key_pawn() == hash::key_pawn(pos));
   assert(hash::key_piece(Champion, White, champion) != Key(0));
   assert(hash::key_piece(Wizard, Black, wizard) != Key(0));
   assert(hash::key_piece(Champion, White, champion)
       != hash::key_piece(Wizard, Black, wizard));

   assert(piece_mat(Knight) == Score(225));
   assert(piece_mat(Wizard) == Score(375));
   assert(piece_mat(Champion) == Score(400));
   assert(piece_mat(Bishop) == Score(425));
   assert(piece_mat(Rook) == Score(600));
   assert(piece_mat(Queen) == Score(1200));

   // The no-special-move sentinel now needs four bits because all eight
   // three-bit values are occupied by real pieces.
   Move quiet = move::make(white_king, square_from_string("f1"));
   assert(move::prom(quiet) == Piece_None);
   assert(!move::is_en_passant(quiet));
   assert(!move::is_castling(quiet));
   assert(!move::is_promotion(quiet));

   Move encoded_champion = move::make(pawn, champion, Champion);
   Move encoded_wizard = move::make(pawn, wizard, Wizard);
   assert(move::prom(encoded_champion) == Champion);
   assert(move::prom(encoded_wizard) == Wizard);

   bit::init(Chess);
   assert(piece_mat(Knight) == Score(325));
   assert(piece_mat(Bishop) == Score(325));
   assert(piece_mat(Rook) == Score(500));
   assert(piece_mat(Queen) == Score(1000));

   return 0;
}
