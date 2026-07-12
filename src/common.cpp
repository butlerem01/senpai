
// includes

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>

#include "bit.hpp"
#include "common.hpp"
#include "libmy.hpp"
#include "util.hpp"

// constants

const std::string Piece_Char { "PNBRQKCW" };

// variables

static Variant Current_Variant { Chess };

// functions

void variant_set(Variant value) {
   assert(value == Chess || value == Omega);
   Current_Variant = value;
}

Variant variant() {
   return Current_Variant;
}

bool variant_is_omega() {
   return Current_Variant == Omega;
}

int file_size() {
   return variant_is_omega() ? File_Capacity : File_Size;
}

int rank_size() {
   return variant_is_omega() ? Rank_Capacity : Rank_Size;
}

int regular_square_size() {
   return file_size() * rank_size();
}

int square_size() {
   return regular_square_size() + (variant_is_omega() ? Corner_Size : 0);
}

bool square_is_ok(int fl, int rk) {
   return file_is_ok(fl) && rank_is_ok(rk);
}

bool square_is_ok(int sq) {
   return sq >= 0 && sq < square_size();
}

Square square_make(int fl, int rk) {
   assert(square_is_ok(fl, rk));
   return square_make(fl * rank_size() + rk); // file major for pawns
}

Square square_make(int fl, int rk, Side sd) {
   assert(square_is_ok(fl, rk));
   return square_make(fl, rank_side(Rank(rk), sd));
}

Square square_make(int sq) {
   assert(square_is_ok(sq));
   return Square(sq);
}

Square square_from_coordinates(int fl, int rk) {

   if (square_is_ok(fl, rk)) return square_make(fl, rk);
   if (!variant_is_omega()) return Square_None;

   if (fl == -1          && rk == -1)          return square_from_corner(Corner_SW);
   if (fl == file_size() && rk == -1)          return square_from_corner(Corner_SE);
   if (fl == file_size() && rk == rank_size()) return square_from_corner(Corner_NE);
   if (fl == -1          && rk == rank_size()) return square_from_corner(Corner_NW);

   return Square_None;
}

bool square_is_corner(Square sq) {
   return variant_is_omega()
       && int(sq) >= regular_square_size()
       && int(sq) < square_size();
}

Corner square_corner(Square sq) {
   assert(square_is_corner(sq));
   return Corner(int(sq) - regular_square_size());
}

Square square_from_corner(Corner corner) {
   assert(variant_is_omega());
   assert(corner >= Corner_SW && corner <= Corner_NW);
   return square_make(regular_square_size() + int(corner));
}

File square_file(Square sq) {
   assert(square_is_ok(sq));

   if (!square_is_corner(sq)) return File(int(sq) / rank_size());

   switch (square_corner(sq)) {
      case Corner_SW : return File(-1);
      case Corner_SE : return File(file_size());
      case Corner_NE : return File(file_size());
      case Corner_NW : return File(-1);
      default :        assert(false); return File(-1);
   }
}

Rank square_rank(Square sq) {
   assert(square_is_ok(sq));

   if (!square_is_corner(sq)) return Rank(int(sq) % rank_size());

   switch (square_corner(sq)) {
      case Corner_SW : return Rank(-1);
      case Corner_SE : return Rank(-1);
      case Corner_NE : return Rank(rank_size());
      case Corner_NW : return Rank(rank_size());
      default :        assert(false); return Rank(-1);
   }
}

Rank square_rank(Square sq, Side sd) {
   return rank_side(square_rank(sq), sd);
}

bool square_is_promotion(Square sq) {
   if (square_is_corner(sq)) return false;
   Rank rk = square_rank(sq);
   return rk == 0 || rk == rank_size() - 1;
}

int square_colour(Square sq) {
   return (int(square_file(sq)) + int(square_rank(sq))) & 1;
}

Inc square_inc(Side sd) { // not used externally
   return Inc(1 - sd * 2);
}

Square square_front(Square sq, Side sd) {
   Square to = square_from_coordinates(square_file(sq), square_rank(sq) + square_inc(sd));
   assert(to != Square_None);
   return to;
}

Square square_rear(Square sq, Side sd) {
   Square to = square_from_coordinates(square_file(sq), square_rank(sq) - square_inc(sd));
   assert(to != Square_None);
   return to;
}

Square square_prom(Square sq, Side sd) {
   assert(!square_is_corner(sq));
   return square_make(square_file(sq), Rank(rank_size() - 1), sd);
}

int square_dist(Square s0, Square s1) {
   return std::max(square_dist_file(s0, s1), square_dist_rank(s0, s1));
}

int square_dist_file(Square s0, Square s1) {
   return std::abs(square_file(s0) - square_file(s1));
}

int square_dist_rank(Square s0, Square s1) {
   return std::abs(square_rank(s0) - square_rank(s1));
}

std::string square_to_string(Square sq) {

   assert(square_is_ok(sq));

   if (square_is_corner(sq)) {
      std::string s { "w" };
      s += char('1' + int(square_corner(sq)));
      return s;
   }

   std::string s;
   s += file_to_char(square_file(sq));
   s += rank_to_char(square_rank(sq));

   return s;
}

bool file_is_ok(int fl) {
   return fl >= 0 && fl < file_size();
}

bool rank_is_ok(int rk) {
   return rk >= 0 && rk < rank_size();
}

File file_make(int fl) {
   assert(file_is_ok(fl));
   return File(fl);
}

Rank rank_make(int rk) {
   assert(rank_is_ok(rk));
   return Rank(rk);
}

File file_opp(File fl) {
   assert(file_is_ok(fl));
   return File(file_size() - 1 - fl);
}

Rank rank_opp(Rank rk) {
   assert(rank_is_ok(rk));
   return Rank(rank_size() - 1 - rk);
}

Rank rank_side(Rank rk, Side sd) {
   assert(rank_is_ok(rk));
   return sd == White ? rk : rank_opp(rk);
}

char file_to_char(File fl) {
   return char('a' + fl);
}

char rank_to_char(Rank rk) {
   assert(rank_is_ok(rk));
   return char((variant_is_omega() ? '0' : '1') + rk);
}

File file_from_char(char c) {

   int fl = c - 'a';
   if (!file_is_ok(fl)) throw Bad_Input();

   return File(fl);
}

Rank rank_from_char(char c) {

   int rk = c - (variant_is_omega() ? '0' : '1');
   if (!rank_is_ok(rk)) throw Bad_Input();

   return Rank(rk);
}

Square square_from_string(const std::string & s) {

   if (s.size() != 2) throw Bad_Input();

   if (variant_is_omega() && (s[0] == 'w' || s[0] == 'W')) {
      int corner = s[1] - '1';
      if (corner < 0 || corner >= Corner_Size) throw Bad_Input();
      return square_from_corner(Corner(corner));
   }

   File fl = file_from_char(s[0]);
   Rank rk = rank_from_char(s[1]);

   return square_make(fl, rk);
}

Vec vector_make(int df, int dr) {

   assert(std::abs(df) < File_Capacity);
   assert(std::abs(dr) < Rank_Capacity);

   return Vec((dr + (Rank_Capacity - 1)) * Vector_File_Size + (df + (File_Capacity - 1)));
}

Square square_add(Square from, Vec vec) {

   assert(square_is_ok(from));

   int df = int(vec) % Vector_File_Size - (File_Capacity - 1);
   int dr = int(vec) / Vector_File_Size - (Rank_Capacity - 1);

   return square_from_coordinates(square_file(from) + df, square_rank(from) + dr);
}

bool piece_is_ok(int pc) {
   return pc >= 0 && pc < Piece_Size;
}

Piece piece_make(int pc) {
   assert(piece_is_ok(pc));
   return Piece(pc);
}

bool piece_is_minor(Piece pc) {
   return pc == Knight || pc == Bishop || pc == Champion || pc == Wizard;
}

char piece_to_char(Piece pc) {
   assert(pc != Piece_None);
   return Piece_Char[pc];
}

Piece piece_from_char(char c) {
   return piece_make(find(c, Piece_Char));
}

bool side_is_ok(int sd) {
   return sd >= 0 && sd < Side_Size;
}

Side side_make(int sd) {
   assert(side_is_ok(sd));
   return Side(sd);
}

Side side_opp(Side sd) {
   return Side(sd ^ 1);
}

std::string side_to_string(Side sd) {
   return (sd == White) ? "white" : "black";
}

bool piece_side_is_ok(int ps) { // excludes Empty
   return ps >= 0 && ps < Piece_Side_Size;
}

Piece_Side piece_side_make(int ps) {
   assert(piece_side_is_ok(ps));
   return Piece_Side(ps);
}

Piece_Side piece_side_make(Piece pc, Side sd) {
   assert(pc != Piece_None);
   return piece_side_make((pc << 1) | sd);
}

Piece piece_side_piece(Piece_Side ps) {
   assert(ps != Empty);
   return piece_make(ps >> 1);
}

Side piece_side_side(Piece_Side ps) {
   assert(ps != Empty);
   return side_make(ps & 1);
}

bool flag_is_lower(Flag flag) {
   return (int(flag) & int(Flag::Lower)) != 0;
}

bool flag_is_upper(Flag flag) {
   return (int(flag) & int(Flag::Upper)) != 0;
}

bool flag_is_exact(Flag flag) {
   return flag == Flag::Exact;
}

Bit::Bit() {
   p_lo = 0;
   p_hi = 0;
}

Bit::Bit(uint64 bit) {
   p_lo = bit;
   p_hi = 0;
}

Bit::Bit(uint64 lo, uint64 hi) {
   p_lo = lo;
   p_hi = hi;
}

Bit::operator uint64() const {
   return p_lo;
}

uint64 Bit::lo() const { return p_lo; }
uint64 Bit::hi() const { return p_hi; }
bool Bit::empty() const { return p_lo == 0 && p_hi == 0; }

void Bit::operator|=(Bit b) {
   p_lo |= b.p_lo;
   p_hi |= b.p_hi;
}

void Bit::operator&=(Bit b) {
   p_lo &= b.p_lo;
   p_hi &= b.p_hi;
}

void Bit::operator^=(Bit b) {
   p_lo ^= b.p_lo;
   p_hi ^= b.p_hi;
}

