
// includes

#include <algorithm>
#include <cctype>
#include <climits>
#include <cstdlib>
#include <sstream>
#include <string>
#include <vector>

#include "bit.hpp"
#include "common.hpp"
#include "fen.hpp"
#include "libmy.hpp"
#include "pos.hpp"
#include "util.hpp"
#include "var.hpp"

// constants

const std::string Piece_Side_Char { "PpNnBbRrQqKk" };
const std::string Side_Char       { "wb" };

// prototypes

static Square fen_square (int sq);
static std::string fen_field (const std::string & s, int & i);
static Pos pos_from_chess_fen (const std::string & s);

static int          ofen_integer      (const std::string & s, int minimum);
static Piece_Side   ofen_piece_side   (char c);
static Square       ofen_square       (int file, int rank);
static Square       ofen_square       (const std::string & s);
static std::string  ofen_square_string(Square sq);
static void         ofen_parse_rank   (Ofen_Position & pos, const std::string & s, int rank);
static void         ofen_parse_corners(Ofen_Position & pos, const std::string & s);
static void         ofen_parse_castling(Ofen_Position & pos, const std::string & s);
static void         ofen_parse_ep     (Ofen_Position & pos, const std::string & s);
static char         ofen_piece_char   (Piece_Side ps);

// types

Ofen_Position::Ofen_Position() {

   for (int sq = 0; sq < Square_Capacity; sq++) piece_side[sq] = Empty;

   turn = White;

   for (int sd = 0; sd < Side_Size; sd++) {
      king_castling[sd] = false;
      queen_castling[sd] = false;
   }

   ep_square[0] = Square_None;
   ep_square[1] = Square_None;
   ep_size = 0;

   halfmove_clock = 0;
   fullmove_number = 1;
}

// functions

const std::string & start_fen(Variant variant) {

   if (variant == Chess) return Start_FEN;
   if (variant == Omega) return Omega_Start_OFEN;

   throw Bad_Input();
}

bool fen_variant_supported(Variant variant) {
   return variant == Chess || variant == Omega;
}

Ofen_Position ofen_parse(const std::string & s) {

   std::istringstream stream(s);
   std::vector<std::string> field;
   std::string token;

   while (stream >> token) field.push_back(token);
   if (field.size() != 6) throw Bad_Input();

   Ofen_Position pos;

   // piece placement: ten ranks followed by four detached corner squares

   const std::string & placement = field[0];
   std::string::size_type open = placement.find('[');

   if (open == std::string::npos || open == 0 || placement.back() != ']') throw Bad_Input();
   if (placement.find('[', open + 1) != std::string::npos) throw Bad_Input();
   if (placement.find(']', open) != placement.size() - 1) throw Bad_Input();

   std::vector<std::string> rank;
   std::string board = placement.substr(0, open);
   std::string item;
   std::istringstream rank_stream(board);

   if (std::count(board.begin(), board.end(), '/') != Rank_Capacity - 1) throw Bad_Input();
   while (std::getline(rank_stream, item, '/')) rank.push_back(item);
   if (rank.size() != Rank_Capacity) throw Bad_Input();

   for (int source_rank = 0; source_rank < Rank_Capacity; source_rank++) {
      ofen_parse_rank(pos, rank[source_rank], Rank_Capacity - 1 - source_rank);
   }

   ofen_parse_corners(pos, placement.substr(open + 1, placement.size() - open - 2));

   // remaining five OFEN fields

   if (field[1] == "w") {
      pos.turn = White;
   } else if (field[1] == "b") {
      pos.turn = Black;
   } else {
      throw Bad_Input();
   }

   ofen_parse_castling(pos, field[2]);
   ofen_parse_ep(pos, field[3]);

   pos.halfmove_clock = ofen_integer(field[4], 0);
   pos.fullmove_number = ofen_integer(field[5], 1);

   return pos;
}

std::string ofen_serialize(const Ofen_Position & pos) {

   if (pos.turn != White && pos.turn != Black) throw Bad_Input();

   std::ostringstream out;

   for (int rank = Rank_Capacity - 1; rank >= 0; rank--) {

      int empty = 0;

      for (int file = 0; file < File_Capacity; file++) {

         Piece_Side ps = pos.piece_side[ofen_square(file, rank)];

         if (ps == Empty) {
            empty++;
         } else {
            if (empty != 0) out << empty;
            out << ofen_piece_char(ps);
            empty = 0;
         }
      }

      if (empty != 0) out << empty;
      if (rank != 0) out << '/';
   }

   out << '[';
   for (int corner = 0; corner < Corner_Size; corner++) {
      if (corner != 0) out << '/';
      Piece_Side ps = pos.piece_side[Regular_Square_Capacity + corner];
      out << (ps == Empty ? '-' : ofen_piece_char(ps));
   }
   out << "] " << (pos.turn == White ? 'w' : 'b') << ' ';

   bool any_castling = false;

   if (pos.king_castling[White])  { out << 'K'; any_castling = true; }
   if (pos.queen_castling[White]) { out << 'Q'; any_castling = true; }
   if (pos.king_castling[Black])  { out << 'k'; any_castling = true; }
   if (pos.queen_castling[Black]) { out << 'q'; any_castling = true; }

   if (!any_castling) out << '-';
   out << ' ';

   if (pos.ep_size == 0) {
      out << '-';
   } else {
      if (pos.ep_size < 0 || pos.ep_size > 2) throw Bad_Input();

      if (pos.ep_size == 2) {
         int first = int(pos.ep_square[0]);
         int second = int(pos.ep_square[1]);

         if (first == second) throw Bad_Input();
         if (first < 0 || first >= Regular_Square_Capacity) throw Bad_Input();
         if (second < 0 || second >= Regular_Square_Capacity) throw Bad_Input();
         if (first / Rank_Capacity != second / Rank_Capacity) throw Bad_Input();
         if (std::abs(first % Rank_Capacity - second % Rank_Capacity) != 1) throw Bad_Input();
      }

      Square ep_square[2] { pos.ep_square[0], pos.ep_square[1] };
      if (pos.ep_size == 2 && ep_square[1] < ep_square[0]) {
         std::swap(ep_square[0], ep_square[1]);
      }

      for (int i = 0; i < pos.ep_size; i++) {
         if (i != 0) out << ',';
         out << ofen_square_string(ep_square[i]);
      }
   }

   if (pos.halfmove_clock < 0 || pos.fullmove_number < 1) throw Bad_Input();
   out << ' ' << pos.halfmove_clock << ' ' << pos.fullmove_number;

   return out.str();
}

std::string ofen_serialize(const Pos & source) {

   if (!variant_is_omega()) throw Bad_Input();

   Ofen_Position pos;

   for (int sq = 0; sq < Square_Capacity; sq++) {
      Square square = Square(sq);
      if (!source.is_empty(square)) {
         pos.piece_side[sq] = piece_side_make(source.piece(square), source.side(square));
      }
   }

   pos.turn = source.turn();

   for (int side = 0; side < Side_Size; side++) {
      Side sd = side_make(side);
      Rank home = rank_side(Rank_1, sd);
      Bit rooks = source.castling_rooks(sd);

      pos.king_castling[sd] = bit::has(rooks, square_make(File_I, home));
      pos.queen_castling[sd] = bit::has(rooks, square_make(File_B, home));
   }

   for (Bit ep = source.ep_squares(); ep != 0; ep = bit::rest(ep)) {
      if (pos.ep_size >= 2) throw Bad_Input();
      pos.ep_square[pos.ep_size++] = bit::first(ep);
   }

   pos.halfmove_clock = source.halfmove_clock();
   pos.fullmove_number = source.fullmove_number();

   return ofen_serialize(pos);
}

Pos pos_from_ofen(const Ofen_Position & source) {

   // Pos and its hash/castling helpers use the active runtime geometry.  The
   // caller must initialise Omega tables before crossing this boundary.
   if (!variant_is_omega() || bit::count(bit::Board_Squares) != Square_Capacity) throw Bad_Input();

   // Ofen_Position is intentionally public data.  Round-tripping through the
   // parser validates every field before it reaches native position storage.
   Ofen_Position pos = ofen_parse(ofen_serialize(source));

   Bit piece_side[Piece_Side_Size];
   for (int ps = 0; ps < Piece_Side_Size; ps++) piece_side[ps] = Bit(0);

   int king_count[Side_Size] { 0, 0 };

   for (int sq = 0; sq < Square_Capacity; sq++) {
      Piece_Side ps = pos.piece_side[sq];
      if (ps != Empty) {
         Piece pc = piece_side_piece(ps);
         Square square = Square(sq);

         if (pc == Pawn
          && (square_is_corner(square) || square_is_promotion(square))) {
            throw Bad_Input();
         }

         bit::set(piece_side[ps], Square(sq));
         if (pc == King) king_count[piece_side_side(ps)]++;
      }
   }

   // Search and legality code require one king per side.  Keep the standalone
   // OFEN parser lossless, but reject non-playable data at the Pos boundary so
   // malformed UCI input cannot turn into an assertion later in `go`.
   if (king_count[White] != 1 || king_count[Black] != 1) throw Bad_Input();

   Bit castling_rooks = Bit(0);

   for (int side = 0; side < Side_Size; side++) {

      Side sd = side_make(side);
      Rank rank = sd == White ? Rank_1 : Rank_10;

      // Omega rooks begin on b/i, outside the corner Champions on a/j.
      if (pos.king_castling[sd])  bit::set(castling_rooks, square_make(File_I, rank));
      if (pos.queen_castling[sd]) bit::set(castling_rooks, square_make(File_B, rank));
   }

   Bit ep_squares = Bit(0);
   for (int i = 0; i < pos.ep_size; i++) bit::set(ep_squares, pos.ep_square[i]);

   return Pos(pos.turn, piece_side, castling_rooks, ep_squares,
              pos.halfmove_clock, pos.fullmove_number);
}

Pos pos_from_fen(const std::string & s) {
   return pos_from_fen(s, Chess);
}

Pos pos_from_fen(const std::string & s, Variant variant) {

   if (variant == Chess) return pos_from_chess_fen(s);

   if (variant == Omega) return pos_from_ofen(ofen_parse(s));

   throw Bad_Input();
}

static Pos pos_from_chess_fen(const std::string & s) {

   int i = 0;

   // pieces

   if (s[i] == ' ') i++; // HACK to help parsing

   Bit piece_side[Piece_Side_Size];

   for (int ps = 0; ps < Piece_Side_Size; ps++) {
      piece_side[ps] = Bit(0);
   }

   int sq = 0;
   int run = 0;

   while (true) {

      char c = s[i++];
      if (c == '\0' || c == ' ') break;

      if (c == '/') {

         sq += run;
         run = 0;

         if (sq >= Square_Size) throw Bad_Input();

      } else if (std::isdigit(static_cast<unsigned char>(c))) { // run of empty squares

         run = run * 10 + (c - '0');

      } else { // piece

         sq += run;
         run = 0;

         if (sq >= Square_Size) throw Bad_Input();

         Piece_Side ps = Piece_Side(find(c, Piece_Side_Char));
         bit::set(piece_side[ps], fen_square(sq));
         sq += 1;
      }
   }

   if (sq + run != Square_Size) throw Bad_Input();

   // turn

   if (s[i] == ' ') i++;

   Side turn = White;
   if (s[i] != '\0') turn = side_make(find(s[i++], Side_Char));

   // castling rights

   if (s[i] == ' ') i++;

   Bit castling_rooks = Bit(0);

   while (s[i] != '\0' && s[i] != ' ') {

      char c = s[i++];
      if (c == '-') continue;

      Side sd;

      if (std::isupper(static_cast<unsigned char>(c))) {

         sd = White;

         if (c == 'K') c = 'H';
         if (c == 'Q') c = 'A';

      } else {

         sd = Black;

         if (c == 'k') c = 'h';
         if (c == 'q') c = 'a';
      }

      char file = char(std::tolower(static_cast<unsigned char>(c)));
      bit::set(castling_rooks, square_make(file_from_char(file), rank_side(Rank_1, sd)));
   }

   // en passant and move counters (legacy callers may omit these fields)

   Bit ep_squares = Bit(0);
   std::string ep = fen_field(s, i);

   if (!ep.empty() && ep != "-") bit::set(ep_squares, square_from_string(ep));

   int halfmove_clock = 0;
   int fullmove_number = 1;

   std::string halfmove = fen_field(s, i);
   std::string fullmove = fen_field(s, i);

   if (!halfmove.empty()) halfmove_clock = ofen_integer(halfmove, 0);
   if (!fullmove.empty()) fullmove_number = ofen_integer(fullmove, 1);

   // wrap up

   return Pos(turn, piece_side, castling_rooks, ep_squares,
              halfmove_clock, fullmove_number);
}

static Square fen_square(int sq) {
   int fl = sq % 8;
   int rk = sq / 8;
   return square_make(fl, 7 - rk);
}

static std::string fen_field(const std::string & s, int & i) {

   while (i < int(s.size()) && s[i] == ' ') i++;

   int begin = i;
   while (i < int(s.size()) && s[i] != ' ') i++;

   return s.substr(begin, i - begin);
}

static int ofen_integer(const std::string & s, int minimum) {

   if (s.empty()) throw Bad_Input();

   int value = 0;

   for (char c : s) {
      if (!std::isdigit(static_cast<unsigned char>(c))) throw Bad_Input();
      int digit = c - '0';
      if (value > (INT_MAX - digit) / 10) throw Bad_Input();
      value = value * 10 + digit;
   }

   if (value < minimum) throw Bad_Input();
   return value;
}

static Piece_Side ofen_piece_side(char c) {

   Side sd = std::isupper(static_cast<unsigned char>(c)) ? White : Black;
   char upper = char(std::toupper(static_cast<unsigned char>(c)));
   Piece pc;

   switch (upper) {
      case 'P' : pc = Pawn;     break;
      case 'N' : pc = Knight;   break;
      case 'B' : pc = Bishop;   break;
      case 'R' : pc = Rook;     break;
      case 'Q' : pc = Queen;    break;
      case 'K' : pc = King;     break;
      case 'C' : pc = Champion; break;
      case 'W' : pc = Wizard;   break;
      default  : throw Bad_Input();
   }

   return piece_side_make(pc, sd);
}

static Square ofen_square(int file, int rank) {

   if (file < 0 || file >= File_Capacity || rank < 0 || rank >= Rank_Capacity) throw Bad_Input();
   return Square(file * Rank_Capacity + rank);
}

static Square ofen_square(const std::string & s) {

   if (s.size() != 2) throw Bad_Input();

   char file_char = char(std::tolower(static_cast<unsigned char>(s[0])));
   int file = file_char - 'a';
   int rank = s[1] - '0';

   return ofen_square(file, rank); // detached corners are not legal EP targets
}

static std::string ofen_square_string(Square sq) {

   int value = int(sq);
   if (value < 0 || value >= Regular_Square_Capacity) throw Bad_Input();

   int file = value / Rank_Capacity;
   int rank = value % Rank_Capacity;

   std::string s;
   s += char('a' + file);
   s += char('0' + rank);
   return s;
}

static void ofen_parse_rank(Ofen_Position & pos, const std::string & s, int rank) {

   if (s.empty()) throw Bad_Input();

   int file = 0;

   for (std::string::size_type i = 0; i < s.size();) {

      char c = s[i];

      if (std::isdigit(static_cast<unsigned char>(c))) {

         std::string::size_type begin = i;
         while (i < s.size() && std::isdigit(static_cast<unsigned char>(s[i]))) i++;

         std::string run = s.substr(begin, i - begin);
         if (run[0] == '0') throw Bad_Input();

         file += ofen_integer(run, 1);
         if (file > File_Capacity) throw Bad_Input();

      } else {

         if (file >= File_Capacity) throw Bad_Input();
         pos.piece_side[ofen_square(file, rank)] = ofen_piece_side(c);
         file++;
         i++;
      }
   }

   if (file != File_Capacity) throw Bad_Input();
}

static void ofen_parse_corners(Ofen_Position & pos, const std::string & s) {

   std::vector<std::string> corner;
   std::string item;
   std::istringstream stream(s);

   if (std::count(s.begin(), s.end(), '/') != Corner_Size - 1) throw Bad_Input();
   while (std::getline(stream, item, '/')) corner.push_back(item);
   if (corner.size() != Corner_Size) throw Bad_Input();

   for (int i = 0; i < Corner_Size; i++) {
      if (corner[i] == "-") continue;
      if (corner[i].size() != 1) throw Bad_Input();
      pos.piece_side[Regular_Square_Capacity + i] = ofen_piece_side(corner[i][0]);
   }
}

static void ofen_parse_castling(Ofen_Position & pos, const std::string & s) {

   if (s == "-") return;
   if (s.empty()) throw Bad_Input();

   for (char c : s) {

      bool * right = nullptr;

      switch (c) {
         case 'K' : right = &pos.king_castling[White];   break;
         case 'Q' : right = &pos.queen_castling[White];  break;
         case 'k' : right = &pos.king_castling[Black];   break;
         case 'q' : right = &pos.queen_castling[Black];  break;
         default  : throw Bad_Input();
      }

      if (*right) throw Bad_Input();
      *right = true;
   }
}

static void ofen_parse_ep(Ofen_Position & pos, const std::string & s) {

   if (s == "-") return;
   if (s.empty()) throw Bad_Input();

   std::string::size_type comma = s.find(',');

   if (comma == std::string::npos) {
      pos.ep_square[0] = ofen_square(s);
      pos.ep_size = 1;
      return;
   }

   if (comma == 0 || comma == s.size() - 1 || s.find(',', comma + 1) != std::string::npos) throw Bad_Input();

   pos.ep_square[0] = ofen_square(s.substr(0, comma));
   pos.ep_square[1] = ofen_square(s.substr(comma + 1));
   pos.ep_size = 2;

   int first = int(pos.ep_square[0]);
   int second = int(pos.ep_square[1]);

   if (first == second) throw Bad_Input();
   if (first / Rank_Capacity != second / Rank_Capacity) throw Bad_Input();
   if (std::abs(first % Rank_Capacity - second % Rank_Capacity) != 1) throw Bad_Input();
}

static char ofen_piece_char(Piece_Side ps) {

   if (!piece_side_is_ok(ps)) throw Bad_Input();

   char c = piece_to_char(piece_side_piece(ps));
   if (piece_side_side(ps) == Black) c = char(std::tolower(static_cast<unsigned char>(c)));
   return c;
}

