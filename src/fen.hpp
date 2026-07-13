
#ifndef FEN_HPP
#define FEN_HPP

// includes

#include <string>

#include "common.hpp"
#include "libmy.hpp"

class Pos;

// types

// Parsed OFEN is a complete, lossless boundary that deliberately has no
// dependency on the process-wide geometry selected by variant_set().
struct Ofen_Position {

   Piece_Side piece_side[Square_Capacity];
   Side turn;

   bool king_castling [Side_Size];
   bool queen_castling[Side_Size];

   Square ep_square[2];
   int ep_size;

   int halfmove_clock;
   int fullmove_number;

   Ofen_Position ();
};

// constants

const std::string Start_FEN { "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" };
const std::string Omega_Start_OFEN { "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1" };

// functions

const std::string & start_fen (Variant variant);

bool fen_variant_supported (Variant variant);

Ofen_Position ofen_parse     (const std::string & s);
std::string   ofen_serialize (const Ofen_Position & pos);
std::string   ofen_serialize (const Pos & pos);
Pos           pos_from_ofen  (const Ofen_Position & pos);

Pos pos_from_fen (const std::string & s);
Pos pos_from_fen (const std::string & s, Variant variant);

#endif // !defined FEN_HPP

