# Exact Omega WDL/DTZ design

## Rules and role normalization

Tables normalize by material role rather than colour. Three-man order is
strong-side king, role piece, bare king, side-to-move bit. KRKC/KRKN order is
rook-side king, rook, minor-side king, minor, side-to-move bit. KWKN order is
Wizard-side king, Wizard, Knight-side king, Knight, side-to-move bit. The
KCKW uses Champion-side king, Champion, Wizard-side
king, Wizard, side-to-move bit. Diagnostic KCCK uses attacker king,
Champion A, defender king, Champion B, side-to-move bit; both labelled
Champions belong to the attacker. The
material-specific rules fingerprint covers the 104-square geometry,
historical-legality test, relevant leaper movement, and current
insufficient-material policy.

A stored placement is historically legal when the side that just moved is not
in check. The current side may be in check, so legal checkmate and stalemate
states remain representable. All D4-related placements share one dense index.

## Completed three-man dependency layer

`KRK` is solved by a conventional attractor retrograde:

1. Enumerate all 273,816 D4 slots and reserve `invalid` for illegal placements.
2. Initialize checkmates as losses and stalemates as draws.
3. Treat a bare king capturing an unprotected rook as an external K-v-K draw.
4. Generate quiet predecessors on demand rather than retaining a reverse graph.
5. A predecessor wins on its first move to a loss. It loses only after every
   successor class is a win. Unresolved cycles become draws.
6. Re-enumerate the family and verify every WDL Bellman equation.

`native_three_man_oracle.cpp` independently constructs the same role-normalized
positions through Senpai's `Pos`, then exports native legality, check, draw, and
legal-successor data. `native_parity.py` canonicalizes those successors with
the Python index and compares the graphs. The default deterministic sample
covers ordinary, detached-corner, invalid-history, mate, and reported-game
states; an exhaustive mode covers every three-man D4 slot.

`KCK` is all draw under the current `omega_insufficient_material()` rule.
`KNK` captures use the same current automatic-draw policy. Their explicit
rules fingerprints prevent a future policy change from reusing incompatible
four-man data.

The three-man solver uses a one-byte status and one-byte unresolved-successor
counter per canonical slot. Predecessors are generated on demand, keeping the
working set small and making a full verified build practical in stock Python.

## Retained four-man index

The 104-square board has 16 first-king D4 orbits. Ten have a trivial
stabilizer and six retain one reflection. A generic block contains
`103P3 = 1,061,106` labelled tails; a reflected block contains
`(103P3 + 11P3) / 2 = 531,048`. This yields:

| Population | States |
|---|---:|
| Raw placements with turn | 220,710,048 |
| D4-canonical placements with turn | 27,594,696 |
| Legal KRKC D4-canonical states | 22,607,206 |
| Legal KRKN D4-canonical states | 23,034,346 |
| Legal KWKN D4-canonical states | 24,078,355 |
| Legal KCKW D4-canonical states | 23,651,215 |
| Legal KCCK D4-canonical states | 23,638,870 |

The reported `Kw2/Rj6` versus `Ke5/Ch4` position is permanently fixed at dense
index `26,750,996`, protecting compatibility with the retained v1 design.

## Implemented four-man dependencies and leaper studies

Within KRKC, quiet moves remain in-class. Captures leave the family:

- `R x C` probes the exact KRK table.
- `C x R` probes the rules-policy KCK table.

KRKN uses the identical role-normalized graph construction with Knight jumps,
including jumps between the detached corners and their two adjacent regular
squares. `R x N` probes KRK, while `N x R` is a KNK draw under the current
insufficient-material policy.

KWKN uses the same role-normalized graph with the Wizard as the primary
leaper and the Knight as the opposing leaper. Wizard and Knight moves include
all detached-corner connections and commute with all eight D4 transforms.
Either side capturing the opposing leaper leaves KWK or KNK, which is an
external draw under the current automatic insufficient-material rule. KWKN
therefore has no KRK file dependency; the capture policy has its own frozen
checksum in the source artifact.

KCKW reuses the no-KRK graph boundary with the Champion as the primary leaper
and Wizard as the opposing leaper. Either capture leaves KCK or KWK, both
automatic draws under the current engine rule. Its material-specific rules and
capture-policy hashes are validated by the offline container reader and strict
production converter. See `KCKW_DESIGN.md` for its acceptance contract and
`KCKW_THEORY.md` for the frozen solve.

KCCK generalizes ownership rather than merely renaming a defender-side piece:
the attacker moves either labelled Champion, while the bare defender moves
only its king. A legal capture of either Champion exits to a KCK policy draw.
The full table passed Bellman verification plus exhaustive D4 and Champion
label-swap invariance. It confirms that two Champions can force mate from most
legal records while retaining exact drawn placements. KCCK is likewise absent
from production and evaluation. Its separate `OMTB4DTM` companion resolves all
decisive records, has maximum DTM 40, and directly settles the no-progress
budget because KCCK has no win-preserving zeroing move. See `KCCK_DESIGN.md`,
`KCCK_DTM_DESIGN.md`, `KCCK_THEORY.md`, and `KCCK_MATING_ATLAS.md`.
Any mechanism analysis of its exact draws is separately constrained by
`KCCK_DRAW_DESIGN.md`; local corner/capture flags must not be mistaken for
proved fortress classes without the residual draw-graph analysis.

The next same-side-leaper experiment is KWWK. Its contract must stratify
opposite-color and same-color Wizard pairs and reconstruct raw color-class
populations carefully because the full D4 quotient identifies the two Wizard
colors. See `KWWK_DESIGN.md`; no KWWK solver or runtime support is present yet.

The standalone C++17 four-man pass reuses the same attractor algorithm and
on-demand predecessor strategy. It does not infer a blanket draw from the
material signature: concrete positions may already be mate or retain a forced
win. Same-family children and predecessors are deduplicated after D4 ranking,
which preserves WDL semantics without storing edge multiplicity.

The full working set is bounded by a one-byte status array, a one-byte
unresolved-successor array, and a `uint32_t` FIFO whose entries are appended at
most once per legal state. Rook-side captures probe the checksummed OMTB3 KRK
dependency; minor-side captures of the rook are KCK/KNK policy draws. Quiet
predecessors are generated only when a child becomes decisive. Unresolved
cycles become draws, followed by an optional full Bellman pass.

Small mode ranks real production states but treats any same-family child beyond
the configured dense prefix as a draw boundary. It is therefore an exact solve
of that explicitly bounded model and exercises production indexing, move
generation, capture dependencies, retrograde propagation, file checksums, and
Bellman verification without allocating the full working set.

After theoretical WDL, a separate DTZ pass must produce five-valued results
(loss, blessed loss, draw, cursed win, win). In pawnless tables captures are
the only zeroing moves; KRK without captures uses distance to mate. Runtime
probing must combine DTZ with the remaining `100 - halfmove_clock` budget.

## Production runtime boundary

`OMTBPROD` provides fixed metadata, rules and payload hashes, and a strict
native reader. `OmegaTablebasePath` atomically loads KRK, KCK, KRKC, KRKN,
KWKN, and KCKW as one set; a missing or invalid replacement retains the
previous set.
KCCK remains outside this production boundary.
Runtime indexing is role-normalized and read-only. Search consumes only exact
draw records. Most win/loss records remain diagnostic until a later distance
pass can respect the automatic 100-ply boundary and provide root ordering.
KCCK now has exact DTM, no fresh-clock cursed wins, and a passing frozen
17,128-root native/independent graph-parity corpus. It still requires direct
solver/native integration coverage, history-safe runtime handling, and a
production-format decision before decisive runtime probing.
