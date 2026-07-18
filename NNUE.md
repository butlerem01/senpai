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

The implementation intentionally recomputes both feature accumulators at each
evaluation. That keeps the first version easy to verify. Incremental
make/unmake updates and SIMD are performance work for a later milestone.

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
