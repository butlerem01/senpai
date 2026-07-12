#ifndef OMEGA_EVAL_HPP
#define OMEGA_EVAL_HPP

#include "common.hpp"

class Pos;

namespace omega_eval {

int evaluate (const Pos & pos);
int piece_value (Piece pc);

}

#endif
