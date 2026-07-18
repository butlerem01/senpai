
#ifndef SCORE_HPP
#define SCORE_HPP

// includes

#include "common.hpp"
#include "libmy.hpp"

namespace score {

// constants

const Score Inf      = Score(10000);
// Search recursion and PV storage stop at 63 plies, but an exact KCCK DTM
// probe can be entered late in that search with as many as 40 plies still to
// mate. Keep those exact distances distinct from evaluation scores.
const Ply   Mate_Ply_Max = Ply(200);
const Score Eval_Inf = Inf - Score(Mate_Ply_Max) - Score(1);
const Score None     = -Inf - Score(1);

// functions

template <typename T>
inline T side(T sc, Side sd) {
   return (sd == White) ? +sc : -sc;
}

bool  is_ok (int sc);
Score win   (Ply ply);
Score loss  (Ply ply);

Score to_tt   (Score sc, Ply ply);
Score from_tt (Score sc, Ply ply);

Score clamp    (Score sc);
Score add_safe (Score sc, Score inc);

bool is_win_loss (Score sc);
bool is_win      (Score sc);
bool is_loss     (Score sc);
bool is_eval     (Score sc);

int  ply (Score sc);

}

#endif // !defined SCORE_HPP

