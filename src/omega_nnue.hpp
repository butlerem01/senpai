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
// Architecture 4 retains the exact same inference kernel and adds a compact
// set of Omega-specific interaction categories.  Its training-time low-rank
// king parameterization is materialized into ordinary int16 feature rows
// before export, so no factor arithmetic is performed in search.
constexpr std::uint32_t Architecture_Absolute = 1U;
constexpr std::uint32_t Architecture_Residual = 2U;
constexpr std::uint32_t Architecture_King_State_Residual = 3U;
constexpr std::uint32_t Architecture_Omega_Interaction_Residual = 4U;
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

// Architecture 4 appends exactly 64 categorical rows to architecture 3.
// Every group contributes exactly one active feature per perspective.
constexpr std::uint32_t Omega_Interaction_Development_Feature_Base =
   King_State_Feature_Count;
constexpr std::uint32_t Omega_Interaction_Development_Features = 18U;
constexpr std::uint32_t Omega_Interaction_Leaper_Count_Feature_Base =
   Omega_Interaction_Development_Feature_Base
   + Omega_Interaction_Development_Features;
constexpr std::uint32_t Omega_Interaction_Leaper_Count_Features = 12U;
constexpr std::uint32_t Omega_Interaction_Coexistence_Feature_Base =
   Omega_Interaction_Leaper_Count_Feature_Base
   + Omega_Interaction_Leaper_Count_Features;
constexpr std::uint32_t Omega_Interaction_Coexistence_Features = 4U;
constexpr std::uint32_t Omega_Interaction_King_Distance_Feature_Base =
   Omega_Interaction_Coexistence_Feature_Base
   + Omega_Interaction_Coexistence_Features;
constexpr std::uint32_t Omega_Interaction_King_Distance_Features = 16U;
constexpr std::uint32_t Omega_Interaction_Activated_Wizard_Feature_Base =
   Omega_Interaction_King_Distance_Feature_Base
   + Omega_Interaction_King_Distance_Features;
constexpr std::uint32_t Omega_Interaction_Activated_Wizard_Features = 6U;
constexpr std::uint32_t Omega_Interaction_Champion_Pair_Feature_Base =
   Omega_Interaction_Activated_Wizard_Feature_Base
   + Omega_Interaction_Activated_Wizard_Features;
constexpr std::uint32_t Omega_Interaction_Champion_Pair_Features = 8U;
constexpr std::uint32_t Omega_Interaction_Features =
     Omega_Interaction_Development_Features
   + Omega_Interaction_Leaper_Count_Features
   + Omega_Interaction_Coexistence_Features
   + Omega_Interaction_King_Distance_Features
   + Omega_Interaction_Activated_Wizard_Features
   + Omega_Interaction_Champion_Pair_Features;
constexpr std::uint32_t Omega_Interaction_Feature_Count =
   King_State_Feature_Count + Omega_Interaction_Features;
constexpr std::uint64_t Omega_Interaction_Payload_Bytes =
     std::uint64_t(Accumulator_Size) * 2ULL
   + std::uint64_t(Omega_Interaction_Feature_Count)
     * Accumulator_Size * 2ULL
   + std::uint64_t(Hidden_Size) * 4ULL
   + std::uint64_t(Hidden_Size) * Dense_Input_Size
   + 4ULL
   + std::uint64_t(Hidden_Size);
constexpr std::uint64_t Omega_Interaction_File_Bytes =
   Header_Bytes + Omega_Interaction_Payload_Bytes;
constexpr int Omega_Interaction_Residual_Limit_Cp = 600;

// Native Omega squares are a0..j9 in file-major order followed by the four
// detached Wizard squares SW, SE, NE, NW.  Black's perspective reflects ranks
// but not files, matching the colour/rank symmetry used by the evaluator.
int orient_square(Square sq, Side perspective);

// A shared transformer views each piece as either friendly or enemy relative
// to the requested perspective.  The result is in [0, Occupancy_Features).
int piece_feature(Piece pc, Side piece_side, Square sq, Side perspective);

// Architecture-3 helpers are public to pin the sparse input contract in
// tests and are shared by the full and incremental accumulator paths.
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

// Empty-board distance on the native 104-square Champion or Wizard graph.
// Unreachable pairs return a value greater than Square_Count.  This helper is
// public so tests can pin the independent Python/C++ feature contract.
int empty_board_leaper_distance(Piece pc, Square from, Square to);

// Castling features follow the occupancy block and are relative to the
// perspective: own-left, own-right, enemy-left, enemy-right.
int castling_feature(bool own, bool right_of_king);

// Produces the sparse feature list for one perspective. The output is cleared
// first. Architectures 1/2 emit pieces and castling rights; architecture 3
// additionally emits exact en-passant targets, a halfmove bin, and a material
// phase. Architecture 4 appends the 64 frozen Omega interaction categories.
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

// Diagnostic surface for the exact incremental-accumulator path.  Normal
// search uses the same cache and delta updater without paying for the oracle;
// tests can request an independent full refresh and inspect which path was
// taken.  Row counts are reported per king perspective, not per piece side.
struct Evaluation_Trace {
   bool cache_hit;
   bool incremental_parent;
   bool full_refresh;
   bool oracle_checked;
   bool oracle_match;
   bool perspective_rebuilt[Side_Size];
   std::uint32_t piece_rows_removed[Side_Size];
   std::uint32_t piece_rows_added[Side_Size];
   std::uint32_t categorical_rows_removed[Side_Size];
   std::uint32_t categorical_rows_added[Side_Size];

   Evaluation_Trace();
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

   // Always rebuilds both accumulators from the sparse feature contract.  It
   // is the production fallback when a safe cached source is unavailable and
   // the independent oracle used by the incremental parity tests.
   bool evaluate_full_refresh(
      const Pos & pos,
      int & side_to_move_cp,
      bool & residual_correction
   ) const;

   // Uses the normal incremental path, then independently rebuilds both
   // accumulators.  A mismatch is reported and the full-refresh accumulator
   // is used for inference, making the diagnostic path fail safe.
   bool evaluate_with_oracle(
      const Pos & pos,
      int & side_to_move_cp,
      bool & residual_correction,
      Evaluation_Trace & trace
   ) const;

   bool loaded() const;
   std::string path() const;

private:
   struct Network_Data;
   bool evaluate_impl(
      const Pos & pos,
      int & side_to_move_cp,
      bool & residual_correction,
      bool force_full_refresh,
      Evaluation_Trace * trace
   ) const;
   std::shared_ptr<const Network_Data> p_network;
};

extern Runtime_Network G_Network;

} // namespace omega_nnue

#endif // !defined OMEGA_NNUE_HPP
