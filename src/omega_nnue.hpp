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

// Each OMNNUE v1 architecture has a deliberately fixed size. Keeping the
// inference contract explicit lets the engine reject a Stockfish network, a
// network trained for a different Omega feature map, or a partially written
// training artifact before any weights become visible to search threads.
constexpr std::uint32_t Endian_Tag          = 0x01020304U;
constexpr std::uint32_t Format_Version      = 1U;
constexpr std::uint32_t Header_Bytes        = 72U;
// The tensor layout remains OMNNUE1 for both meanings.  Architecture 1 is
// the original absolute evaluator; architecture 2 is a correction that the
// Omega adapter adds to the handcrafted evaluation. Architecture 3 keeps the
// residual meaning but uses a king-conditioned, state-complete feature map.
constexpr std::uint32_t Architecture_Absolute = 1U;
constexpr std::uint32_t Architecture_Residual = 2U;
constexpr std::uint32_t Architecture_King_State_Residual = 3U;
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

constexpr std::uint32_t King_Bucket_Count = 29U;
constexpr std::uint32_t King_State_Occupancy_Features =
   King_Bucket_Count * Occupancy_Features;
constexpr std::uint32_t King_State_Castling_Feature_Base =
   King_State_Occupancy_Features;
constexpr std::uint32_t King_State_EP_Feature_Base =
   King_State_Castling_Feature_Base + Castling_Features;
constexpr std::uint32_t King_State_EP_Features = Square_Count;
constexpr std::uint32_t King_State_Halfmove_Feature_Base =
   King_State_EP_Feature_Base + King_State_EP_Features;
constexpr std::uint32_t King_State_Halfmove_Bins = 8U;
constexpr std::uint32_t King_State_Phase_Feature_Base =
   King_State_Halfmove_Feature_Base + King_State_Halfmove_Bins;
constexpr std::uint32_t King_State_Phase_Bins = 4U;
constexpr std::uint32_t King_State_Feature_Count =
   King_State_Phase_Feature_Base + King_State_Phase_Bins;
constexpr std::uint64_t King_State_Payload_Bytes =
     std::uint64_t(Accumulator_Size) * 2ULL
   + std::uint64_t(King_State_Feature_Count) * Accumulator_Size * 2ULL
   + std::uint64_t(Hidden_Size) * 4ULL
   + std::uint64_t(Hidden_Size) * Dense_Input_Size
   + 4ULL
   + std::uint64_t(Hidden_Size);
constexpr std::uint64_t King_State_File_Bytes =
   Header_Bytes + King_State_Payload_Bytes;

// Native Omega squares are a0..j9 in file-major order followed by the four
// detached Wizard squares SW, SE, NE, NW.  Black's perspective reflects ranks
// but not files, matching the colour/rank symmetry used by the evaluator.
int orient_square(Square sq, Side perspective);

// A shared transformer views each piece as either friendly or enemy relative
// to the requested perspective.  The result is in [0, Occupancy_Features).
int piece_feature(Piece pc, Side piece_side, Square sq, Side perspective);

// Architecture-3 helpers are public to pin the sparse input contract in
// tests and to support a future incremental accumulator implementation.
int king_bucket(Square king, Side perspective);
int king_state_piece_feature(
   Piece pc,
   Side piece_side,
   Square sq,
   Side perspective,
   int bucket
);
int halfmove_clock_bin(int halfmove_clock);
int material_phase_bin(const Pos & pos);

// Castling features follow the occupancy block and are relative to the
// perspective: own-left, own-right, enemy-left, enemy-right.
int castling_feature(bool own, bool right_of_king);

// Produces the sparse feature list for one perspective. The output is cleared
// first. Architectures 1/2 emit pieces and castling rights; architecture 3
// additionally emits exact en-passant targets, a halfmove bin, and a material
// phase.
void active_features(
   const Pos & pos,
   Side perspective,
   std::vector<int> & output,
   std::uint32_t architecture = Architecture_Absolute
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
