# Production design: Omega four-man WDL/DTZ

## Scope and rules fingerprint

The first material family is `KR v KC`, normalized by material role rather than
piece colour.  The generator and engine probe must share a rules fingerprint
covering board geometry, Champion moves, the 100-ply automatic draw used by
`Pos::is_draw()`, and insufficient-material policy.  A mismatch makes the file
unloadable.

The official strategy material says that king-and-rook cannot force mate after
the defender reaches a detached Wizard square.  That statement is strategic,
not a blanket automatic-draw rule.  `KRK` must therefore be solved exactly:
some concrete positions are already mate or still win before the escape.

## Legal states and capture dependencies

A stored placement is legal exactly when the side that just moved is not in
check.  This is Senpai's `is_legal(pos)` invariant.  Thus:

- with the rook side to move, the Champion-side king may not currently be
  attacked by the rook-side king or rook;
- with the Champion side to move, the rook-side king may not currently be
  attacked by the Champion-side king or Champion.

The current side may be in check.  Checkmate and stalemate are legal terminal
states.  Castling rights and en-passant squares are absent in these pawnless
material classes.

Within a material class every move is reversible and non-capturing.  Captures
leave the class:

- `R x C` probes the previously generated `KRK` table;
- `C x R` probes `KCK`, which is a draw under Senpai's current automatic
  insufficient-material policy.

Generate predecessor positions on demand instead of storing a reverse graph.
For a child, flip the side to move and enumerate possible previous origins of
the prior mover's king and role piece.  King and Champion moves are symmetric;
rook origins are the unobstructed orthogonal ray.  Reconstruct the candidate,
validate it with the same legality function as the engine, and deduplicate its
canonical dense index.  Because a capture cannot lead to a four-piece child,
same-class predecessors never need to resurrect captured material.

## WDL retrograde

Store outcome from the side-to-move perspective.  The work arrays for all
27,594,696 canonical states are:

- status: invalid, unknown, loss, draw, win;
- remaining legal successor count (one byte is sufficient: KR has at most 26
  pseudo destinations and KC at most 20 before legality filtering);
- distance/work value (`uint16_t` initially, with overflow treated as a build
  failure rather than saturation);
- a `uint32_t` propagation queue.

Initialization:

1. Mark geometrically or historically illegal placements invalid.
2. Generate every legal move, including capture successors.
3. No legal move plus check is a loss; no legal move without check is a draw.
4. Fold known child-table capture outcomes into the successor count.  One move
   to a child loss makes the parent a win.  Child wins count as refutations;
   child draws guarantee that an otherwise unresolved parent cannot become a
   loss.

Propagation is the standard attractor algorithm.  A predecessor becomes a win
as soon as it can move to a loss.  It becomes a loss only after every legal move
has been proven to reach a win.  States left unknown at the fixed point are
draws.  Generate `KRK`, then rule-policy `KCK`, then `KRKC`.

For a separate DTM validation pass, wins take the minimum `1 + child DTM` over
losing successors while losses take the maximum over winning successors.  This
is valuable for checking terminal propagation but should not be substituted
for DTZ in the engine.

## DTZ and the 50-move rule

Use five-valued WDL, as in Syzygy-style tables:

```text
loss, blessed loss, draw, cursed win, win
```

DTZ is signed distance in plies to the next zeroing move or mate while
preserving WDL.  In these pawnless families a capture is the only zeroing move.
`KRK` has no zeroing move, so its winning distance is distance to mate.  A mate
on the 100th non-zeroing ply remains a win in Senpai because `Pos::is_draw()`
checks `is_mate()` at the boundary; a non-mating position at halfmove clock 100
is drawn.

Do not derive cursed/blessed results from a naive material score.  Run the
standard DTZ retrograde after theoretical WDL, treating a capture edge as DTZ
one and importing the five-valued result of the child table.  Quiet winning
moves minimize DTZ; the losing side maximizes it subject to WDL.  Results that
cannot reset or mate inside 100 plies become cursed wins/blessed losses.

At probe time, repetition and an already-expired halfmove clock remain the
search's responsibility.  For an unconditional win/loss, compare the probed
absolute DTZ with the remaining budget `100 - pos.halfmove_clock()`.  A
cursed-win or blessed-loss result is a rule draw under optimal play.  Keep this
logic in one tested helper; do not scatter threshold arithmetic through search.

## File format

Use one little-endian, memory-mappable file per material family:

```text
Header
  magic = "OMTB"
  format version
  rules fingerprint
  material signature and labelled-piece order
  square count = 104
  index version = D4-first-piece-v1
  state count
  WDL and DTZ offsets/lengths
  payload checksum
Payload
  packed 3-bit WDL/invalid codes
  uint16 little-endian absolute DTZ values
```

The sign of DTZ follows WDL and need not be stored twice.  Map the uncompressed
payload directly; its roughly 62.5 MiB size is small enough that adding a codec
dependency would complicate random probing for little benefit.  The generator
should write to a temporary file, flush, verify checksum and random samples,
then atomically rename it.

## Senpai integration boundary

Add new files only after the offline generator is independently verified:

```cpp
// src/omega_tb.hpp
namespace omega_tb {
enum class Wdl : int8_t { Loss = -2, BlessedLoss = -1, Draw = 0,
                          CursedWin = 1, Win = 2, Missing = 3 };
struct Probe { Wdl wdl; uint16_t dtz; bool found; };

bool load(const std::string & directory, std::string & error);
void clear();
bool eligible(const Pos & pos);
Probe probe(const Pos & pos);
Wdl with_fifty_move_rule(const Probe & result, int halfmove_clock);
}
```

Concrete engine changes would be isolated to:

- `src/omega_tb.hpp/.cpp`: mapping, signature recognition, index and probe;
- `src/var.hpp/.cpp` and `src/main.cpp`: an `OmegaTablebasePath` UCI string
  option, load diagnostics, and reload on path change;
- `src/search.cpp`: probe after native terminal/repetition checks and before TT
  or pruning; root move selection probes successors and orders equal WDL by
  DTZ;
- `src/score.hpp/.cpp` and UCI reporting: reserve a tablebase score band below
  mate scores so a tablebase win is not falsely printed as a forced mate;
- `tests/omega_tb_index.cpp`, `tests/omega_tb_probe.cpp`, and a tiny checked-in
  fixture covering only selected blocks/positions.

`eligible()` requires Omega mode, exactly the supported material signature, no
pawns, no castling rights, and no en-passant square.  Actual colours are mapped
to rook-side/Champion-side roles before indexing.

The engine must not return a blanket draw merely because the material is
`KRKC`.  A probe is exact for the concrete position, and missing/corrupt files
must fall back to normal search.

## Verification gates

1. Dense rank/unrank round trips and all eight transforms share one index.
2. Exact Burnside and legal counts match `index_prototype.py`.
3. Every generated legal edge reaches a legal child and every same-class edge
   appears in the child's predecessor set.
4. Terminal states agree with Senpai's move generator and check detector.
5. Bellman equations hold for every WDL/DTZ record after generation.
6. Random probes agree between an uncompressed reference solver and the file.
7. Transforming or colour-normalizing a position preserves WDL and DTZ.
8. The reported game position (`Kw2/Rj6` versus `Ke5/Ch4`, White to move) and
   the pre-liquidation positions are permanent regression probes.  In the
   prototype's role-relative numbering that final position is
   `State(101, 96, 45, 74, ROOK_TO_MOVE)` and has dense index `26,750,996`.
9. UCI tests cover missing path, bad checksum, wrong rules fingerprint,
   halfmove clocks 0/99/100, mate exactly at the boundary, and multithreaded
   read-only probes.
