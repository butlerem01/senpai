#ifndef OMEGA_EVAL_HPP
#define OMEGA_EVAL_HPP

#include "common.hpp"

class Pos;

namespace omega_eval {

int evaluate (const Pos & pos);
int piece_value (Piece pc);
int undeveloped_units (const Pos & pos, Side sd);
int development_penalty (int undeveloped);

}

#endif
