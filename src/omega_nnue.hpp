#ifndef OMEGA_NNUE_HPP
#define OMEGA_NNUE_HPP

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "common.hpp"

class Pos;

namespace omega_nnue {

namespace format {

// OMNNUE v1 is deliberately fixed-size.  Keeping the inference contract
// explicit lets the engine reject a Stockfish network, a network trained for
// a different Omega feature map, or a partially written training artifact
// before any weights become visible to search threads.
constexpr std::uint32_t Endian_Tag          = 0x01020304U;
constexpr std::uint32_t Format_Version      = 1U;
constexpr std::uint32_t Header_Bytes        = 72U;
// The tensor layout remains OMNNUE1 for both meanings.  Architecture 1 is
// the original absolute evaluator; architecture 2 is a correction that the
// Omega adapter adds to the handcrafted evaluation.
constexpr std::uint32_t Architecture_Absolute = 1U;
constexpr std::uint32_t Architecture_Residual = 2U;
constexpr std::uint32_t Architecture_Id       = Architecture_Absolute;
constexpr std::uint32_t Square_Count        = 104U;
constexpr std::uint32_t Piece_Count         = 8U;
constexpr std::uint32_t Occupancy_Features  = 1664U;
constexpr std::uint32_t Castling_Features   = 4U;
constexpr std::uint32_t Feature_Count       = 1668U;
constexpr std::uint32_t Accumulator_Size    = 128U;
constexpr std::uint32_t Dense_Input_Size    = Accumulator_Size * 2U;
constexpr std::uint32_t Hidden_Size         = 32U;
constexpr std::uint32_t Activation_Max      = 127U;
constexpr std::uint32_t Hidden_Divisor      = 64U;
constexpr std::uint32_t Output_Divisor      = 64U;
constexpr std::uint64_t Payload_Bytes       = 435620ULL;
constexpr std::uint64_t File_Bytes          = Header_Bytes + Payload_Bytes;

// Native Omega squares are a0..j9 in file-major order followed by the four
// detached Wizard squares SW, SE, NE, NW.  Black's perspective reflects ranks
// but not files, matching the colour/rank symmetry used by the evaluator.
int orient_square(Square sq, Side perspective);

// A shared transformer views each piece as either friendly or enemy relative
// to the requested perspective.  The result is in [0, Occupancy_Features).
int piece_feature(Piece pc, Side piece_side, Square sq, Side perspective);

// Castling features follow the occupancy block and are relative to the
// perspective: own-left, own-right, enemy-left, enemy-right.
int castling_feature(bool own, bool right_of_king);

// Produces the sparse feature list for one perspective.  The output is
// cleared first and contains one feature per piece plus at most four distinct
// castling-right features.
void active_features(
   const Pos & pos,
   Side perspective,
   std::vector<int> & output
);

} // namespace format

struct Configure_Result {
   bool ok { false };
   bool disabled { false };
   bool retained_previous { false };
   std::string message;
};

class Runtime_Network {
public:
   Runtime_Network();

   // Loading is all-or-nothing.  A malformed replacement leaves the previous
   // immutable network published; an empty path explicitly unloads it.
   Configure_Result configure(const std::string & file_name);

   // Returns a score from the side-to-move perspective.  False means that no
   // network is loaded or that the active board variant is not Omega.
   bool evaluate(const Pos & pos, int & side_to_move_cp) const;

   // The three-argument form reports the output semantics from the same
   // immutable network snapshot used for inference.  This prevents a
   // reconfiguration from pairing one network's score with another network's
   // semantics.
   bool evaluate(
      const Pos & pos,
      int & side_to_move_cp,
      bool & residual_correction
   ) const;

   bool loaded() const;
   std::string path() const;

private:
   struct Network_Data;
   std::shared_ptr<const Network_Data> p_network;
};

extern Runtime_Network G_Network;

} // namespace omega_nnue

#endif // !defined OMEGA_NNUE_HPP
