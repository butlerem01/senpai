#define DEBUG

#include <cmath>
#include <iostream>
#include <string>

#include "../src/bit.hpp"
#include "../src/eval.hpp"
#include "../src/fen.hpp"
#include "../src/hash.hpp"
#include "../src/math.hpp"
#include "../src/omega_eval.hpp"
#include "../src/pawn.hpp"
#include "../src/pos.hpp"

namespace {

int Failures = 0;

void expect(bool condition, const char * message) {
   if (!condition) {
      std::cerr << "eval_omega: " << message << '\n';
      Failures++;
   }
}

void select_variant(Variant value) {
   bit::init(value);
   pawn::init();
   clear_pawn_table();
}

Square square(const char * coordinate) {
   return square_from_string(coordinate);
}

void put(Ofen_Position & pos, Piece pc, Side sd, const char * coordinate) {
   Square sq = square(coordinate);
   expect(pos.piece_side[sq] == Empty, "test position contains overlapping pieces");
   pos.piece_side[sq] = piece_side_make(pc, sd);
}

Ofen_Position bare_omega(Side turn = White) {
   Ofen_Position pos;
   pos.turn = turn;
   put(pos, King, White, "f0");
   put(pos, King, Black, "f9");
   return pos;
}

Pos make(const Ofen_Position & source) {
   return pos_from_ofen(source);
}

int white_score(const Ofen_Position & source) {
   return int(eval(make(source), White));
}

int white_piece_gain(Piece pc) {
   Ofen_Position bare = bare_omega();
   // Keep every material probe out of the insufficient-material draw class.
   // The anchor Pawns are remote from e4 and are present in both positions.
   put(bare, Pawn, White, "j1");
   put(bare, Pawn, Black, "j8");
   Ofen_Position with_piece = bare;
   put(with_piece, pc, White, "e4");
   return white_score(with_piece) - white_score(bare);
}

Square rank_mirror(Square sq) {
   if (square_is_corner(sq)) {
      // Corner order is SW, SE, NE, NW.  A north/south reflection therefore
      // maps corner n to 3 - n.
      return square_from_corner(Corner(3 - int(square_corner(sq))));
   }

   return square_make(square_file(sq), rank_opp(square_rank(sq)));
}

Ofen_Position colour_rank_mirror(const Ofen_Position & source) {
   Ofen_Position out;
   out.turn = side_opp(source.turn);
   out.halfmove_clock = source.halfmove_clock;
   out.fullmove_number = source.fullmove_number;

   for (int s = 0; s < Side_Size; s++) {
      Side sd = side_make(s);
      Side xd = side_opp(sd);
      out.king_castling[xd] = source.king_castling[sd];
      out.queen_castling[xd] = source.queen_castling[sd];
   }

   for (int sq = 0; sq < Square_Capacity; sq++) {
      Piece_Side ps = source.piece_side[sq];
      if (ps == Empty) continue;

      Piece pc = piece_side_piece(ps);
      Side sd = side_opp(piece_side_side(ps));
      out.piece_side[rank_mirror(Square(sq))] = piece_side_make(pc, sd);
   }

   out.ep_size = source.ep_size;
   for (int i = 0; i < source.ep_size; i++) {
      out.ep_square[i] = rank_mirror(source.ep_square[i]);
   }

   return out;
}

void test_colour_symmetry() {
   Ofen_Position pos = bare_omega(Black);
   put(pos, Queen, White, "d4");
   put(pos, Champion, White, "h2");
   put(pos, Pawn, White, "b3");
   put(pos, Wizard, White, "w1");
   put(pos, Rook, Black, "i7");
   put(pos, Wizard, Black, "d8");
   put(pos, Pawn, Black, "c6");

   Pos original = make(pos);
   Pos mirrored = make(colour_rank_mirror(pos));

   expect(int(eval(original, White)) == -int(eval(mirrored, White)),
          "rank reflection plus colour swap must negate the evaluation");
   expect(int(eval(original, White)) == -int(eval(original, Black)),
          "requesting the opposite perspective must negate the evaluation");
}

void test_material_ordering() {
   int pawn = white_piece_gain(Pawn);
   int knight = white_piece_gain(Knight);
   int bishop = white_piece_gain(Bishop);
   int champion = white_piece_gain(Champion);
   int wizard = white_piece_gain(Wizard);
   int rook = white_piece_gain(Rook);
   int queen = white_piece_gain(Queen);

   expect(queen > rook, "a Queen must be worth more than a Rook");
   expect(rook > bishop, "a Rook must be worth more than a Bishop");
   expect(bishop > champion,
          "a Bishop's 10x10 range must place it above a Champion");
   expect(champion > wizard,
          "a Champion must be worth more than a Wizard");
   expect(wizard > knight,
          "a Wizard must be worth more than a Knight");
   expect(knight > pawn,
          "a Knight must be worth more than a Pawn");

   // These values also drive capture ordering and exchange decisions.  Test
   // the hierarchy, not the current tuning constants.
   expect(piece_mat(Queen) > piece_mat(Rook),
          "capture ordering must rank Queen above Rook");
   expect(piece_mat(Rook) > piece_mat(Bishop),
          "capture ordering must rank Rook above Bishop");
   expect(piece_mat(Bishop) > piece_mat(Champion)
       && piece_mat(Champion) > piece_mat(Wizard)
       && piece_mat(Wizard) > piece_mat(Knight),
          "capture ordering must use the Omega minor-piece hierarchy");
}

void test_phase_bishop_value() {
   expect(omega_eval::phase_piece_value(Bishop, 32) == 425
       && omega_eval::phase_piece_value(Bishop, 24) == 433
       && omega_eval::phase_piece_value(Bishop, 16) == 442
       && omega_eval::phase_piece_value(Bishop, 8) == 451
       && omega_eval::phase_piece_value(Bishop, 0) == 460,
          "Bishop value must taper from 425 to 460 with integer truncation");
}

void test_mobility() {
   // The Knight squares e5 and f5 are mirror-equivalent on the empty 10x10
   // board.  On e5 it blocks its own Rook on e4; on f5 it does not.  Material,
   // centralisation, and Knight mobility are otherwise identical.
   Ofen_Position blocked = bare_omega();
   put(blocked, Rook, White, "e4");
   put(blocked, Knight, White, "e5");

   Ofen_Position open = bare_omega();
   put(open, Rook, White, "e4");
   put(open, Knight, White, "f5");

   expect(white_score(open) > white_score(blocked),
          "opening a long-range piece must improve the evaluation");
}

void test_pawns() {
   Ofen_Position starting = bare_omega();
   put(starting, Pawn, White, "e1");

   Ofen_Position advanced = bare_omega();
   put(advanced, Pawn, White, "e4");

   expect(white_score(advanced) > white_score(starting),
          "a safely advanced Pawn must score above the same starting Pawn");

   // d3/e3 and e3/g3 have the same material, rank sum, and aggregate distance
   // from the two central files.  Only the first pair supports one another.
   Ofen_Position connected = bare_omega();
   put(connected, Pawn, White, "d3");
   put(connected, Pawn, White, "e3");

   Ofen_Position isolated = bare_omega();
   put(isolated, Pawn, White, "e3");
   put(isolated, Pawn, White, "g3");

   expect(white_score(connected) > white_score(isolated),
          "connected Pawns must score above equally advanced isolated Pawns");

   // e2/e4 has the same total advance as d3/e3 and is slightly more central,
   // so a win by the healthy pair genuinely exercises the doubled-pawn term.
   Ofen_Position doubled = bare_omega();
   put(doubled, Pawn, White, "e2");
   put(doubled, Pawn, White, "e4");

   expect(white_score(connected) > white_score(doubled),
          "a healthy Pawn pair must score above equally advanced doubled Pawns");
}

void test_king_safety_and_castling() {
   // Both three-pawn groups are contiguous, equally advanced, and have equal
   // aggregate centrality.  Only g1/h1/i1 shields the King on h0.
   Ofen_Position shielded = bare_omega();
   shielded.piece_side[square("f0")] = Empty;
   put(shielded, King, White, "h0");
   put(shielded, Pawn, White, "g1");
   put(shielded, Pawn, White, "h1");
   put(shielded, Pawn, White, "i1");

   Ofen_Position exposed = bare_omega();
   exposed.piece_side[square("f0")] = Empty;
   put(exposed, King, White, "h0");
   put(exposed, Pawn, White, "b1");
   put(exposed, Pawn, White, "c1");
   put(exposed, Pawn, White, "d1");

   expect(white_score(shielded) > white_score(exposed),
          "a castled King's intact pawn shield must be valued");

   Ofen_Position rights = bare_omega();
   put(rights, Rook, White, "b0");
   put(rights, Rook, White, "i0");
   rights.king_castling[White] = true;
   rights.queen_castling[White] = true;

   Ofen_Position no_rights = rights;
   no_rights.king_castling[White] = false;
   no_rights.queen_castling[White] = false;

   expect(white_score(rights) > white_score(no_rights),
          "retaining usable Omega castling rights must have positive value");
}

void test_king_attack_coordination() {
   expect(omega_eval::king_attack_quadratic(9, 1) == 0,
          "a solo attacker must receive no quadratic king-danger bonus");
   expect(omega_eval::king_attack_quadratic(9, 2) == 13,
          "two attackers must receive half the quadratic king-danger bonus");
   expect(omega_eval::king_attack_quadratic(9, 3) == 27,
          "three attackers must receive the full quadratic king-danger bonus");

   Ofen_Position solo = bare_omega();
   put(solo, Pawn, White, "j1");
   put(solo, Pawn, Black, "j8");
   put(solo, Champion, White, "f5");
   put(solo, Wizard, White, "a2");

   Ofen_Position coordinated = bare_omega();
   put(coordinated, Pawn, White, "j1");
   put(coordinated, Pawn, Black, "j8");
   put(coordinated, Champion, White, "f5");
   put(coordinated, Wizard, White, "d6");

   expect(white_score(coordinated) > white_score(solo),
          "multiple credible king-zone attackers must outscore a solo raid");

   Ofen_Position mirrored = colour_rank_mirror(coordinated);
   expect(white_score(coordinated) == -white_score(mirrored),
          "coordinated king danger must preserve colour symmetry");
}

void test_corner_wizard_development() {
   Ofen_Position corner = bare_omega();
   put(corner, Pawn, White, "j1");
   put(corner, Pawn, Black, "j8");
   put(corner, Wizard, White, "w1");

   Ofen_Position developed = bare_omega();
   put(developed, Pawn, White, "j1");
   put(developed, Pawn, Black, "j8");
   put(developed, Wizard, White, "a2");

   expect(white_score(developed) > white_score(corner),
          "developing a Wizard from its corner must improve the evaluation");
}

Ofen_Position development_shell() {
   Ofen_Position pos = bare_omega();

   put(pos, Champion, White, "a0");
   put(pos, Knight, White, "c0");
   put(pos, Bishop, White, "d0");
   put(pos, Bishop, White, "g0");
   put(pos, Knight, White, "h0");
   put(pos, Champion, White, "j0");
   put(pos, Wizard, White, "w1");
   put(pos, Wizard, White, "w2");

   put(pos, Champion, Black, "a9");
   put(pos, Knight, Black, "c9");
   put(pos, Bishop, Black, "d9");
   put(pos, Bishop, Black, "g9");
   put(pos, Knight, Black, "h9");
   put(pos, Champion, Black, "j9");
   put(pos, Wizard, Black, "w3");
   put(pos, Wizard, Black, "w4");

   return pos;
}

void test_development_completion() {
   Ofen_Position home = development_shell();

   Ofen_Position one_developed = home;
   one_developed.piece_side[square("a0")] = Empty;
   put(one_developed, Champion, White, "c2");

   Ofen_Position two_developed = one_developed;
   two_developed.piece_side[square("j0")] = Empty;
   put(two_developed, Champion, White, "h2");

   expect(white_score(one_developed) > white_score(home),
          "developing a new home unit must improve army readiness");
   expect(white_score(two_developed) > white_score(one_developed),
          "developing a second distinct unit must improve army readiness");

   expect(omega_eval::development_penalty(8) == -72
       && omega_eval::development_penalty(7) == -50
       && omega_eval::development_penalty(6) == -32
       && omega_eval::development_penalty(2) == 0,
          "development completion must use the declared nonlinear curve");

   expect(omega_eval::undeveloped_units(make(home), White) == 8,
          "all eight home units must begin undeveloped");
   expect(omega_eval::undeveloped_units(make(one_developed), White) == 7,
          "the first distinct developed unit must reduce the count once");
   expect(omega_eval::undeveloped_units(make(two_developed), White) == 6,
          "the second distinct developed unit must reduce the count once");

   Ofen_Position repeated = one_developed;
   repeated.piece_side[square("c2")] = Empty;
   put(repeated, Champion, White, "e4");
   expect(omega_eval::undeveloped_units(make(repeated), White) == 7,
          "moving an active unit again must not improve army readiness");

   Ofen_Position captured_home = home;
   captured_home.piece_side[square("a0")] = Empty;
   expect(omega_eval::undeveloped_units(make(captured_home), White) == 8,
          "capturing an undeveloped unit must not refund readiness");

   Ofen_Position mirrored = colour_rank_mirror(two_developed);
   expect(white_score(two_developed) == -white_score(mirrored),
          "development completion must preserve colour symmetry");
}

void test_endgame_material_classes() {
   Ofen_Position rook = bare_omega();
   put(rook, Rook, White, "e4");

   Ofen_Position two_wizards = bare_omega();
   put(two_wizards, Wizard, White, "d4");
   put(two_wizards, Wizard, White, "f4");

   Ofen_Position two_champions = bare_omega();
   put(two_champions, Champion, White, "d4");
   put(two_champions, Champion, White, "f4");

   expect(std::abs(white_score(rook)) < 200,
          "Rook versus bare King must be scaled as corner-drawish");
   expect(std::abs(white_score(two_wizards)) < 200,
          "two Wizards versus bare King must be scaled as drawish");
   expect(white_score(two_champions) > white_score(two_wizards) + 400,
          "two Champions must retain their known mating value");

   Ofen_Position same_colour = bare_omega();
   put(same_colour, Wizard, White, "d5");
   put(same_colour, Bishop, White, "e4");

   Ofen_Position opposite_colours = bare_omega();
   put(opposite_colours, Wizard, White, "d5");
   put(opposite_colours, Bishop, White, "f4");

   expect(white_score(opposite_colours) > white_score(same_colour) + 300,
          "Bishop and Wizard must retain value when covering opposite colours");
}

int standard_reference_score() {
   const std::string fen {
      "4k3/2pp4/8/8/4N3/3P4/8/4K3 w - - 0 1"
   };
   return int(eval(pos_from_fen(fen, Chess), White));
}

} // namespace

int main() {
   math::init();
   bit::init(Chess);
   hash::init();
   pawn::init();
   clear_pawn_table();

   int standard_before = standard_reference_score();

   select_variant(Omega);
   test_colour_symmetry();
   test_material_ordering();
   test_phase_bishop_value();
   test_mobility();
   test_pawns();
   test_king_safety_and_castling();
   test_king_attack_coordination();
   test_corner_wizard_development();
   test_development_completion();
   test_endgame_material_classes();

   // Omega evaluation must not perturb the trained standard evaluator or its
   // variant-dependent geometry tables when the GUI switches back to chess.
   select_variant(Chess);
   int standard_after = standard_reference_score();
   expect(standard_after == standard_before,
          "Chess evaluation changed after an Omega variant round trip");

   if (Failures != 0) {
      std::cerr << Failures << " Omega evaluation expectation(s) failed\n";
      return 1;
   }

   return 0;
}
