#include <algorithm>
#include <cmath>

#include "bit.hpp"
#include "common.hpp"
#include "omega_eval.hpp"
#include "pos.hpp"

namespace omega_eval {

namespace {

const int Phase_Max { 32 };

const int MG_Value[Piece_Size] { 100, 225, 425, 600, 1200, 0, 400, 375 };
const int EG_Value[Piece_Size] { 125, 235, 440, 625, 1225, 0, 400, 375 };

const int MG_Mobility[Piece_Size] { 0, 6, 4, 3, 2, 0, 6, 5 };
const int EG_Mobility[Piece_Size] { 0, 5, 5, 4, 2, 0, 5, 6 };

const int MG_Advance[10] { 0, 0, 2, 6, 12, 20, 32, 50, 75, 0 };
const int EG_Advance[10] { 0, 0, 5, 12, 24, 40, 62, 90, 130, 0 };
const int MG_Passed [10] { 0, 0, 4, 10, 20, 35, 55, 85, 130, 0 };
const int EG_Passed [10] { 0, 0, 8, 18, 36, 65, 105, 160, 240, 0 };

struct Eval_Score {
   int mg;
   int eg;

   Eval_Score() : mg(0), eg(0) {}

   void add(Side sd, int mg_value, int eg_value) {
      int sign = sd == White ? +1 : -1;
      mg += sign * mg_value;
      eg += sign * eg_value;
   }
};

struct Attack_Maps {
   Bit attacks[Side_Size];
   Bit double_attacks[Side_Size];
   Bit pawn_attacks[Side_Size];
   Bit piece_attacks[Square_Capacity];

   Attack_Maps() {
      for (int s = 0; s < Side_Size; s++) {
         attacks[s] = Bit(0);
         double_attacks[s] = Bit(0);
         pawn_attacks[s] = Bit(0);
      }
      for (int sq = 0; sq < Square_Capacity; sq++) piece_attacks[sq] = Bit(0);
   }
};

int relative_rank(Square sq, Side sd) {
   int rank = int(square_rank(sq));
   return sd == White ? rank : rank_size() - 1 - rank;
}

int centre_score(Square sq) {
   if (square_is_corner(sq)) return 0;

   int df = std::abs(int(square_file(sq)) * 2 - (file_size() - 1));
   int dr = std::abs(int(square_rank(sq)) * 2 - (rank_size() - 1));
   return std::max(0, 18 - df - dr);
}

Bit attacks_from(const Pos & pos, Piece pc, Side sd, Square sq) {
   if (pc == Pawn) return bit::pawn_attacks(sd, sq);
   return bit::piece_attacks(pc, sq, pos.pieces());
}

Attack_Maps make_attack_maps(const Pos & pos) {
   Attack_Maps maps;

   for (int s = 0; s < Side_Size; s++) {
      Side sd = side_make(s);

      for (int p = 0; p < Piece_Size; p++) {
         Piece pc = piece_make(p);

         for (Bit b = pos.pieces(pc, sd); b != 0; b = bit::rest(b)) {
            Square sq = bit::first(b);
            Bit attacks = attacks_from(pos, pc, sd, sq);

            maps.piece_attacks[sq] = attacks;
            maps.double_attacks[sd] |= maps.attacks[sd] & attacks;
            maps.attacks[sd] |= attacks;
            if (pc == Pawn) maps.pawn_attacks[sd] |= attacks;
         }
      }
   }

   return maps;
}

int phase(const Pos & pos) {
   int units = 0;

   for (int s = 0; s < Side_Size; s++) {
      Side sd = side_make(s);
      units += pos.count(Knight, sd);
      units += pos.count(Bishop, sd);
      units += pos.count(Champion, sd);
      units += pos.count(Wizard, sd);
      units += pos.count(Rook, sd) * 2;
      units += pos.count(Queen, sd) * 4;
   }

   return std::min(units, Phase_Max);
}

int file_count(Bit pawns, int file) {
   int count = 0;
   for (Bit b = pawns; b != 0; b = bit::rest(b)) {
      Square sq = bit::first(b);
      if (!square_is_corner(sq) && int(square_file(sq)) == file) count++;
   }
   return count;
}

bool is_passed(const Pos & pos, Square sq, Side sd) {
   int file = int(square_file(sq));
   int rank = int(square_rank(sq));
   Side xd = side_opp(sd);

   for (Bit b = pos.pawns(xd); b != 0; b = bit::rest(b)) {
      Square enemy = bit::first(b);
      int enemy_file = int(square_file(enemy));
      int enemy_rank = int(square_rank(enemy));
      int ahead = sd == White ? enemy_rank - rank : rank - enemy_rank;
      if (std::abs(enemy_file - file) <= 1 && ahead > 0) return false;
   }

   return true;
}

bool is_connected(Bit pawns, Square sq) {
   int file = int(square_file(sq));
   int rank = int(square_rank(sq));

   for (Bit b = bit::remove(pawns, sq); b != 0; b = bit::rest(b)) {
      Square other = bit::first(b);
      if (std::abs(int(square_file(other)) - file) == 1
       && std::abs(int(square_rank(other)) - rank) <= 1) {
         return true;
      }
   }

   return false;
}

bool is_isolated(Bit pawns, Square sq) {
   int file = int(square_file(sq));

   for (Bit b = bit::remove(pawns, sq); b != 0; b = bit::rest(b)) {
      if (std::abs(int(square_file(bit::first(b))) - file) == 1) return false;
   }

   return true;
}

void eval_pawns(Eval_Score & score, const Pos & pos, const Attack_Maps & maps,
                int pawn_files[Side_Size][File_Capacity]) {
   for (int s = 0; s < Side_Size; s++) {
      Side sd = side_make(s);
      Side xd = side_opp(sd);
      Bit pawns = pos.pawns(sd);

      for (int file = 0; file < file_size(); file++) {
         pawn_files[sd][file] = file_count(pawns, file);
         if (pawn_files[sd][file] > 1) {
            int extra = pawn_files[sd][file] - 1;
            score.add(sd, -14 * extra, -18 * extra);
         }
      }

      for (Bit b = pawns; b != 0; b = bit::rest(b)) {
         Square sq = bit::first(b);
         int rr = relative_rank(sq, sd);

         score.add(sd, MG_Value[Pawn] + MG_Advance[rr],
                       EG_Value[Pawn] + EG_Advance[rr]);

         if (is_passed(pos, sq, sd)) {
            score.add(sd, MG_Passed[rr], EG_Passed[rr]);
         }

         if (is_connected(pawns, sq)) score.add(sd, 8, 12);
         if (is_isolated(pawns, sq)) score.add(sd, -12, -10);
         if (bit::has(maps.pawn_attacks[sd], sq)) score.add(sd, 7, 11);

         Square front = square_from_coordinates(
            square_file(sq), square_rank(sq) + square_inc(sd)
         );
         if (front != Square_None && !pos.is_empty(front)) score.add(sd, -7, -4);

         // A home pawn's double/triple-step reserve is strategically useful.
         if (rr == 1) {
            int clear = 0;
            for (int step = 1; step <= 3; step++) {
               Square to = square_from_coordinates(
                  square_file(sq), square_rank(sq) + square_inc(sd) * step
               );
               if (to == Square_None || !pos.is_empty(to)) break;
               clear++;
            }
            score.add(sd, clear * 3, clear * 1);
         }

         // Pawn attacks on advanced pieces are durable tactical assets.
         int attacked = bit::count(bit::pawn_attacks(sd, sq) & pos.non_pawns(xd));
         score.add(sd, attacked * 8, attacked * 6);
      }
   }
}

void eval_pieces(Eval_Score & score, const Pos & pos, const Attack_Maps & maps,
                 int pawn_files[Side_Size][File_Capacity]) {
   const int centre_weight_mg[Piece_Size] { 0, 2, 1, 0, 0, 0, 2, 1 };
   const int centre_weight_eg[Piece_Size] { 0, 2, 1, 0, 0, 0, 2, 1 };

   for (int s = 0; s < Side_Size; s++) {
      Side sd = side_make(s);
      Side xd = side_opp(sd);

      for (int p = int(Knight); p < Piece_Size; p++) {
         Piece pc = piece_make(p);
         if (pc == King) continue;

         Bit pieces = pos.pieces(pc, sd);
         score.add(sd, MG_Value[pc] * bit::count(pieces),
                       EG_Value[pc] * bit::count(pieces));

         for (Bit b = pieces; b != 0; b = bit::rest(b)) {
            Square sq = bit::first(b);
            Bit mobility = maps.piece_attacks[sq] & ~pos.pieces(sd);
            Bit safe = mobility & ~maps.pawn_attacks[xd];
            int safe_count = bit::count(safe);
            int unsafe_count = bit::count(mobility) - safe_count;

            score.add(sd,
               MG_Mobility[pc] * safe_count + MG_Mobility[pc] * unsafe_count / 2,
               EG_Mobility[pc] * safe_count + EG_Mobility[pc] * unsafe_count / 2
            );

            int centre = centre_score(sq);
            score.add(sd, centre * centre_weight_mg[pc],
                          centre * centre_weight_eg[pc]);

            if (square_is_corner(sq)) {
               if (pc == Champion) score.add(sd, -60, -45);
               if (pc == Knight)   score.add(sd, -25, -18);
               if (pc == Wizard)   score.add(sd, -18, -8);
            } else {
               int rr = relative_rank(sq, sd);

               if (pc == Knight || pc == Bishop || pc == Champion || pc == Wizard) {
                  if (rr == 0) score.add(sd, -7, 0);
                  if (rr >= 2) score.add(sd, +5, +2);
               }

               if ((pc == Knight || pc == Champion || pc == Wizard || pc == Bishop)
                && rr >= 3 && rr <= 7
                && bit::has(maps.pawn_attacks[sd], sq)
                && !bit::has(maps.pawn_attacks[xd], sq)) {
                  int bonus = pc == Bishop ? 8 : 16;
                  score.add(sd, bonus, bonus);
               }

               if ((pc == Champion || pc == Wizard) && rr >= 5) {
                  score.add(sd, 10, 8); // blockade-penetrating infiltration
               }

               if (pc == Rook) {
                  int file = int(square_file(sq));
                  if (pawn_files[sd][file] == 0) score.add(sd, 12, 10);
                  if (pawn_files[sd][file] == 0 && pawn_files[xd][file] == 0) {
                     score.add(sd, 14, 12);
                  }
                  if (rr >= 6) score.add(sd, 14, 22);
               }
            }
         }
      }

      if (pos.count(Bishop, sd) >= 2) score.add(sd, 32, 48);
      if (pos.count(Champion, sd) >= 1 && pos.count(Wizard, sd) >= 1) {
         score.add(sd, 10, 8);
      }

      // Reward breadth of development rather than repeated moves by an
      // already-active unit.  The last two home units remain optional so the
      // term does not prescribe a rigid opening scheme, and it fades out with
      // the normal middlegame/endgame taper.
      score.add(sd, development_penalty(undeveloped_units(pos, sd)), 0);

      // Connected rooks retain more of their value on the expanded board.
      Bit rooks = pos.pieces(Rook, sd);
      for (Bit firsts = rooks; firsts != 0; firsts = bit::rest(firsts)) {
         Square first = bit::first(firsts);
         if (square_is_corner(first)) continue;
         for (Bit seconds = bit::rest(firsts); seconds != 0; seconds = bit::rest(seconds)) {
            Square second = bit::first(seconds);
            if (square_is_corner(second)) continue;
            if ((square_file(first) == square_file(second)
              || square_rank(first) == square_rank(second))
             && bit::line_is_empty(first, second, pos.pieces())) {
               score.add(sd, 14, 12);
            }
         }
      }
   }
}

Bit king_zone(Square king) {
   Bit zone = bit::bit(king) | bit::king_attacks(king);
   Bit ring = bit::king_attacks(king);

   for (Bit b = ring; b != 0; b = bit::rest(b)) {
      zone |= bit::king_attacks(bit::first(b));
   }

   return zone;
}

void eval_kings(Eval_Score & score, const Pos & pos, const Attack_Maps & maps,
                int pawn_files[Side_Size][File_Capacity]) {
   const int attack_weight[Piece_Size] { 1, 4, 3, 4, 6, 0, 5, 5 };

   for (int s = 0; s < Side_Size; s++) {
      Side sd = side_make(s);
      Side xd = side_opp(sd);
      Square king = pos.king(sd);
      Bit zone = king_zone(king);

      int zone_hits = bit::count(zone & maps.attacks[xd]);
      int double_hits = bit::count(zone & maps.double_attacks[xd]);
      int attack_units = 0;
      int attacker_count = 0;

      for (int p = 0; p < Piece_Size; p++) {
         Piece pc = piece_make(p);
         if (pc == King) continue;
         for (Bit b = pos.pieces(pc, xd); b != 0; b = bit::rest(b)) {
            Square sq = bit::first(b);
            int hits = bit::count(maps.piece_attacks[sq] & zone);
            if (hits != 0) {
               attacker_count++;
               attack_units += attack_weight[pc] + std::min(hits, 3) - 1;
            }
         }
      }

      int danger = zone_hits * 3 + double_hits * 5
                 + attack_units * 4
                 + king_attack_quadratic(attack_units, attacker_count);
      score.add(sd, -danger, -(zone_hits + attack_units * 2));

      if (!square_is_corner(king)) {
         int file = int(square_file(king));

         for (int df = -1; df <= 1; df++) {
            int shield_file = file + df;
            if (shield_file < 0 || shield_file >= file_size()) continue;

            if (pawn_files[sd][shield_file] == 0) {
               score.add(sd, -10, -2);
               if (pawn_files[xd][shield_file] == 0) score.add(sd, -6, 0);
            }

            for (int step = 1; step <= 2; step++) {
               Square shield = square_from_coordinates(
                  shield_file, square_rank(king) + square_inc(sd) * step
               );
               if (shield != Square_None && pos.is_piece(shield, Pawn)
                && pos.is_side(shield, sd)) {
                  score.add(sd, step == 1 ? 9 : 4, step == 1 ? 3 : 2);
               }
            }
         }

         int centre = centre_score(king);
         score.add(sd, -centre * 2, centre * 3);
      } else {
         score.add(sd, -12, -18); // sanctuary can also become a mobility trap
      }

      Bit rights = pos.castling_rooks(sd) & pos.pieces(Rook, sd);
      score.add(sd, bit::count(rights) * 10, 0);
   }
}

void eval_space_and_threats(Eval_Score & score, const Pos & pos,
                            const Attack_Maps & maps) {
   Bit centre = bit::rect(3, 3, 7, 7);

   for (int s = 0; s < Side_Size; s++) {
      Side sd = side_make(s);
      Side xd = side_opp(sd);
      Bit territory = Bit(0);

      for (int file = 0; file < file_size(); file++) {
         for (int rank = 0; rank < rank_size(); rank++) {
            Square sq = square_make(file, rank);
            if (relative_rank(sq, sd) >= 5) bit::set(territory, sq);
         }
      }

      score.add(sd, bit::count(maps.attacks[sd] & territory & pos.empties()), 0);
      score.add(sd, bit::count(maps.attacks[sd] & centre) * 2,
                    bit::count(maps.attacks[sd] & centre));

      for (int p = 0; p < Piece_Size; p++) {
         Piece pc = piece_make(p);
         if (pc == King) continue;

         for (Bit b = pos.pieces(pc, xd); b != 0; b = bit::rest(b)) {
            Square sq = bit::first(b);
            if (!bit::has(maps.attacks[sd], sq)) continue;

            int bonus = piece_value(pc) / 30;
            if (!bit::has(maps.attacks[xd], sq)) bonus += piece_value(pc) / 24;
            if (bit::has(maps.pawn_attacks[sd], sq)) bonus += piece_value(pc) / 25;
            score.add(sd, bonus, bonus);
         }
      }
   }
}

int material_scale(const Pos & pos, int value) {
   if (pos.pawns(White) != 0 || pos.pawns(Black) != 0) return value;

   for (int s = 0; s < Side_Size; s++) {
      Side attacker = side_make(s);
      Side defender = side_opp(attacker);
      if (!pos::lone_king(pos, defender)) continue;

      int knights = pos.count(Knight, attacker);
      int bishops = pos.count(Bishop, attacker);
      int rooks = pos.count(Rook, attacker);
      int queens = pos.count(Queen, attacker);
      int champions = pos.count(Champion, attacker);
      int wizards = pos.count(Wizard, attacker);
      int total = knights + bishops + rooks + queens + champions + wizards;

      if (total == 0 || (total == 1 && rooks == 0 && queens == 0)) return 0;

      if (total == 1 && rooks == 1) return value / 16; // corner escape
      if (total == 2 && wizards == 2) return value / 16;

      if (total == 2 && bishops == 1 && wizards == 1) {
         Square bishop = bit::first(pos.pieces(Bishop, attacker));
         Square wizard = bit::first(pos.pieces(Wizard, attacker));
         if (square_colour(bishop) == square_colour(wizard)) return value / 8;
      }
   }

   return value;
}

}

int undeveloped_units(const Pos & pos, Side sd) {
   int developed = 0;

   const Piece home_rank_pieces[] { Knight, Bishop, Champion };
   for (Piece pc : home_rank_pieces) {
      for (Bit b = pos.pieces(pc, sd); b != 0; b = bit::rest(b)) {
         Square sq = bit::first(b);
         if (square_is_corner(sq) || relative_rank(sq, sd) != 0) developed++;
      }
   }

   for (Bit b = pos.pieces(Wizard, sd); b != 0; b = bit::rest(b)) {
      if (!square_is_corner(bit::first(b))) developed++;
   }

   // Omega begins with eight eligible units.  Missing units do not count as
   // developed, so capturing a sleeping piece cannot refund this penalty.
   return std::max(0, 8 - developed);
}

int development_penalty(int undeveloped) {
   int bounded = std::max(0, std::min(undeveloped, 8));
   int excess = std::max(0, bounded - 2);
   return -2 * excess * excess;
}

int king_attack_quadratic(int attack_units, int attacker_count) {
   if (attacker_count <= 1) return 0;

   int value = attack_units * attack_units / 3;
   if (attacker_count == 2) value /= 2;
   return value;
}

int piece_value(Piece pc) {
   const int value[Piece_Size] { 100, 225, 425, 600, 1200, 10000, 400, 375 };
   assert(pc != Piece_None);
   return value[pc];
}

int evaluate(const Pos & pos) {
   assert(variant_is_omega());

   if (pos.is_draw()) return 0;

   Attack_Maps maps = make_attack_maps(pos);
   Eval_Score score;
   int pawn_files[Side_Size][File_Capacity] {};

   eval_pawns(score, pos, maps, pawn_files);
   eval_pieces(score, pos, maps, pawn_files);
   eval_kings(score, pos, maps, pawn_files);
   eval_space_and_threats(score, pos, maps);

   int opening = phase(pos);
   int endgame = Phase_Max - opening;
   int value = (score.mg * opening + score.eg * endgame) / Phase_Max;
   value = material_scale(pos, value);

   value += pos.turn() == White ? +10 : -10;
   return value;
}

}
