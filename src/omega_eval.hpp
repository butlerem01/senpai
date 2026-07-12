#ifndef OMEGA_EVAL_HPP
#define OMEGA_EVAL_HPP

#include "common.hpp"

class Pos;

namespace omega_eval {

int evaluate (const Pos & pos);
int piece_value (Piece pc);
int king_attack_quadratic (int attack_units, int attacker_count);

}

#endif
