#ifndef OMEGA_EVAL_HPP
#define OMEGA_EVAL_HPP

#include "common.hpp"

class Pos;

namespace omega_eval {

int evaluate (const Pos & pos);
int piece_value (Piece pc);
int wizard_endgame_bonus (int pawn_count);
int undeveloped_units (const Pos & pos, Side sd);
int development_penalty (int undeveloped);
int king_attack_quadratic (int attack_units, int attacker_count);

}

#endif
