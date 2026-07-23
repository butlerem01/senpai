# Omega NNUE v0

This branch adds the first trainable neural-evaluation path to Senpai Omega.
It is an experimental foundation, not yet a promoted strength build. The
handcrafted Omega evaluator remains the default, and it is also the automatic
fallback if a network is absent or rejected.

The v0 milestone includes:

- a fixed Omega-specific feature and binary-format contract;
- a checksummed, all-or-nothing runtime loader;
- scalar integer inference in native Omega search;
- UCI options for loading and enabling a network;
- an independent NumPy trainer, quantizer, exporter, and inference checker;
- deterministic C++ and UCI regression coverage; and
- a Python-to-C++ parity gate for exported networks.

The Generation 6 runtime uses an exact thread-local incremental accumulator
cache. A position address is only a bounded-cache lookup hint: every entry
also carries the immutable network generation and an exact position snapshot,
including all 16 `(side, piece)` bitboards, castling and en-passant state,
side to move, position key, and halfmove clock. A successful network reload is
therefore a hard cache miss, and positions whose keys alias while their clocks
differ cannot share an accumulator.

An update diffs all 16 bitboards, which covers ordinary moves, captures,
promotions, castling, en passant, and detached corners. Castling, EP, clock,
phase, and all architecture-4 categorical rows are independently recomputed
and diffed without relying on the move type. When a perspective's friendly
king changes bucket, that perspective's piece rows are rebuilt; the other
perspective still uses bitboard deltas. Null moves consequently update EP and
clock rows even though no piece changed. Only one cached parent snapshot is
considered, raw parent pointers are never dereferenced, and visible self/two-
node cycles fall back to a full refresh.

The full-refresh implementation remains the oracle and production fallback.
Targeted tests request both paths, compare every accumulator lane before dense
inference, and use the full accumulator if a diagnostic mismatch is ever
observed. The `OMNNUE1` file format, architecture-4 feature map, quantized
inference, and predictions are unchanged; SIMD remains future performance
work.

## Network

The frozen architecture is `PS104-128x2-32-CReLU`:

1. Each perspective sees every occupied square as one of eight own or enemy
   piece types over all 104 Omega squares.
2. Four additional features encode own/enemy queen- and king-side castling
   rights. A right is active only when the corresponding rook is present.
3. A shared `1668 x 128` feature transformer builds White and Black
   accumulators.
4. The side-to-move accumulator is concatenated before the opponent
   accumulator.
5. Clipped integer layers reduce `256 -> 32 -> 1` and return centipawns from
   the side-to-move perspective.

Black's square orientation mirrors ranks while preserving files. Detached
corners map `w1 <-> w4` and `w2 <-> w3`. The binary file is exactly 435,692
bytes: a 72-byte little-endian `OMNNUE1` header and a 435,620-byte payload
protected by FNV-1a-64.

The original tensor layout supports two self-described output meanings in
the header. Architecture `1` is the original absolute evaluator; all existing
v1 files retain their bytes and behavior. Architecture `2` is a correction:
the runtime adds its side-to-move output to the handcrafted Omega evaluation.
The engine obtains that meaning from the file header rather than its filename.
Draw adjudication still bypasses evaluation, and the combined score is clamped
to the normal non-mate evaluation range.

Architecture `3` is the next residual experiment,
`KingPS104-state-128x2-32-CReLU`. It keeps the same accumulator and dense
layers, but expands the sparse transformer to 48,376 rows:

- each piece-square row is conditioned on the friendly king's bucket;
- the 10x10 board supplies 25 non-overlapping 2x2 king buckets and the four
  detached corners supply four more;
- four castling features remain global (not multiplied by king bucket);
- 100 usable global rows encode every regular-board en-passant target exactly,
  including Omega's two-target state; four reserved rows keep the state
  namespace aligned with the 104-square piece map;
- one of eight halfmove-clock bins is active (`0`, `1-3`, `4-15`, `16-31`,
  `32-49`, `50-74`, `75-89`, `90+`), retaining a dedicated danger bin near
  Omega's automatic 100-ply draw; and
- one of four material-force phases is active (`24+`, `16-23`, `8-15`,
  `0-7`) using the HCE's N/B/C/W=1, R=2, Q=4 weights.

Its 12,392,868-byte payload remains in the checksummed `OMNNUE1` container.
Architecture `3` is always a residual correction; architectures `1` and `2`
remain byte-for-byte compatible.

## CoreChess

Add `build-msvc/senpai-omega-nnue.exe` as a UCI engine. Keep the established
Omega settings and add:

```text
UCI_Variant   omega
UseOmegaNNUE  false
OmegaNNUEFile <empty>
```

For an experimental network, set `OmegaNNUEFile` to its absolute path and
then set `UseOmegaNNUE=true`. The two settings may be sent in either order.
A missing or malformed first file leaves the handcrafted evaluator active. A
bad replacement retains the previously loaded network. Setting the file to
`<empty>` unloads it and returns to the handcrafted fallback.

Keep `UseOmegaNNUE=false` for normal play until a candidate has passed the
held-out tactical suites and a controlled match against the unchanged
integrated evaluator.

## Trainer

The trainer requires Python 3.12 and NumPy:

```powershell
python .\tools\omega_nnue\train.py --self-test
```

A corpus run accepts side-to-move centipawn labels (`targetCpStm` or
`searchCpStm`) and/or outcomes (`outcomeStm` or `sideToMoveScore`):

```powershell
python .\tools\omega_nnue\train.py `
  --input ..\omega-lab\data\nnue\nnue-samples-v1.jsonl `
  --output .\build-msvc\omega-nnue-v1.nnue `
  --seed 20260718 --epochs 12 --qat-epochs 3
```

For residual training, run `label_hce.py` over the exact search-teacher
corpus, align it with `build_residual_targets.py`, then pass
`--network-semantics residual`. The alignment tool requires matching unique
`sampleId`, normalized OFEN, and exact NNUE input signatures; it emits the
tagged target `searchTargetCpStm - handcraftedCpStm`. The trainer refuses
untagged residual input or an initial network with different semantics. Full
commands and the alignment self-test are documented in
`tools/omega_nnue/README.md`.

Residual CP loss fits the correction itself. Residual outcome loss uses
`handcraftedCpStm + predicted correction`, then propagates the BCE gradient
through the correction. Architecture-1 training continues to use the original
outcome fields and raw network score without a baseline.

Select the expanded input map with
`--network-semantics king-state-residual`. Dataset deduplication and split
audits hash its exact two-perspective sparse inputs, so king bucket,
en-passant targets, halfmove bin, material phase, and side to move all
participate in the signature. Fullmove number is deliberately deferred
because it has no rules or evaluation meaning after those fields are present.

Do not initialize the 48,376-row transformer randomly when an architecture-2
checkpoint is available. Pass that residual network with
`--initial-network <file>` and the explicit
`--expand-residual-to-king-state` flag. The migration copies every old
piece-square row into all 29 king buckets, keeps castling and all dense layers
exact, and zero-initializes EP/clock/phase rows. Its first architecture-3
prediction is therefore exactly the architecture-2 prediction on every OFEN;
training starts by learning bucket and state deltas rather than relearning HCE.
The trainer re-quantizes the float32 shadow and compares every tensor before
accepting the migration; an unusual int32 bias that cannot survive float32
losslessly is rejected instead of weakening that guarantee.

Splits are made by whole groups rather than individual positions. Exact NNUE
inputs that still cross split boundaries are audited; the safe default
removes the duplicate holdout rows. AB/BA pairs, opening families, repeated
games, and colour-rank symmetry orbits still need a shared upstream group ID.
Every manifest records split-collision counts, content hashes for all inputs
and trainer sources, the training environment, quantized metrics, and the
network hash.

After compiling the tests, add the C++ evaluator helper to make export/runtime
parity part of a training gate:

```powershell
python .\tools\omega_nnue\train.py --self-test --quiet `
  --cpp-evaluator .\.build-msvc-tests\Release\tests\omega_nnue.exe
```

The gate compares Python and C++ on exported positions plus fixed vectors for
feature layout, signed weights, half-away-from-zero rounding, and clipping.

## What the current data can and cannot prove

The current Omega Lab catalog is enough to verify ingestion, grouping,
training, quantization, loading, and search stability. It is not yet a clean
strength corpus. It contains related Senpai branches, paired openings,
repeated games, and only hundreds of underlying games. Outcome-only labels
also provide a weak learning signal at individual positions.

The next useful dataset should combine:

- fresh, book-disabled fixed-node teacher scores;
- actual outcomes from independent self-play;
- explicit game, pair, opening-family, repeat, and symmetry group IDs;
- exact tablebase labels only where the 100-ply rule makes them safe; and
- reserved bishop-raid, Champion/Wizard, mating, and endgame regressions that
  never enter training.

Promotion then requires evaluator regressions, rules and protocol tests,
nodes-per-second measurement, and a paired fixed-node match against the
integrated handcrafted build. A successful training loss alone is not a
strength result.
