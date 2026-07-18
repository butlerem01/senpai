# Omega NNUE bootstrap trainer

This directory contains the dependency-free training and export side of
Senpai's first Omega Chess NNUE experiment. It requires Python 3.12 and NumPy;
it does not require PyTorch, ONNX, scikit-learn, or an inference library in the
engine.

The trainer targets the frozen `OMNNUE1` architecture:

- 104 native Omega squares (`a0` through `j9`, followed by `w1` through `w4`);
- eight piece types and own/enemy relations, plus four castling flags;
- a shared `1668 x 128` feature transformer;
- side-to-move and opponent clipped accumulators;
- a `256 -> 32` clipped hidden layer;
- one side-to-move centipawn output.

The exported file has a 72-byte little-endian header and a 435,620-byte
FNV-1a-protected payload. `omega_nnue.py` contains an independent integer
inference implementation and refuses wrong dimensions, truncated payloads,
and checksum failures.

## First smoke test

The synthetic smoke test trains a small material sample, exports and reloads
the quantized network, compares every prediction before and after the round
trip, verifies that a corrupted payload is rejected, exercises deterministic
cross-split collision handling, and checks hardcoded integer-inference golden
vectors (feature row order, negative weights, both signs of half-away-from-zero
output rounding, and activation saturation):

```powershell
python .\tools\omega_nnue\train.py --self-test
```

To retain the smoke network and its manifest:

```powershell
python .\tools\omega_nnue\train.py --self-test `
  --output .\build-msvc\omega-nnue-smoke.nnue
```

`--self-test` can instead overfit up to 64 supplied records:

```powershell
python .\tools\omega_nnue\train.py --self-test `
  --input ..\omega-lab\data\current\training-positions.jsonl `
  --output .\build-msvc\omega-nnue-corpus-smoke.nnue
```

## JSONL records

Every row must contain an exact six-field Omega OFEN and at least one target:

```json
{"ofen":"10/10/5k4/10/10/10/10/10/4P5/4K5[-/-/-/-] w - - 0 1","gameId":"game-17","targetCpStm":92,"outcomeStm":1.0}
```

Accepted centipawn fields, in priority order, are `targetCpStm` and
`searchCpStm`. Accepted result fields are `outcomeStm` and the existing Omega
Lab `sideToMoveScore`. All labels are from the side-to-move perspective.

Normal training assigns whole groups deterministically to train, validation,
or test from a SHA-256 hash and `--seed`. The automatic group preference is
`groupId`, `splitGroup`, `pairId`, provenance pair, `gameId`, provenance game,
source family, then `sampleId`. Use `--group-field` to require a particular
field. A missing grouping identity falls back to the exact position, which is
safe for a smoke test but is not an adequate experiment split.

After assigning whole groups, the loader hashes the exact two-perspective
feature input seen by the network. This catches positions whose OFEN strings
differ only in state the v1 network cannot observe. If an identical input
appears in more than one split, the default `--collision-policy drop` keeps
the rows in the highest-priority split (train, then validation, then test) and
drops the duplicate rows from lower-priority splits. The alternatives are
`--collision-policy error` for a strict publication run and
`--collision-policy allow` for diagnostics. Counts and examples are written
to the manifest.

This exact-input check does **not** recognize related openings, paired games,
deterministic repeats with different positions, or color/rank symmetry
families. Keeping those families together still requires correct upstream
group IDs (preferably supplied explicitly with `--group-field`). No rows are
ever reassigned away from their whole-game split.

Example:

```powershell
python .\tools\omega_nnue\train.py `
  --input ..\omega-lab\data\nnue\nnue-samples-v1.jsonl `
  --output .\build-msvc\omega-bootstrap-v1.nnue `
  --seed 20260718 --epochs 12 --qat-epochs 3
```

The adjacent manifest pins each input's absolute path, byte count, and SHA-256;
the hashes of `train.py` and `omega_nnue.py`; Python, NumPy, platform, and
available NumPy/BLAS build configuration; split and collision counts; seed;
loss settings; quantized metrics; network SHA-256 and payload FNV; and epoch
history. Training aborts if an input changes between its initial pin and
manifest creation.

## C++/Python parity

When the C++ NNUE test helper has been built, add `--cpp-evaluator` to compare
the exported network against the engine runtime on a deterministic position
set:

```powershell
python .\tools\omega_nnue\train.py --self-test `
  --output .\build-msvc\omega-nnue-smoke.nnue `
  --cpp-evaluator .\build-msvc\omega_nnue_tests.exe
```

The helper must support:

```text
<helper> --evaluate-network <network.nnue> "<six-field Omega OFEN>"
```

and emit one bare signed integer. The parity run also exports five fixed
golden networks temporarily and compares six hardcoded vectors exactly across
Python and C++. Results and the helper's path, size, and SHA-256 are recorded
under `crossRuntime`. Without `--cpp-evaluator`, Python still runs all golden
vectors and the manifest records cross-runtime status as `not-requested`.

## Evidence limitations

The current Omega Lab catalog contains many position rows but only hundreds of
underlying games. Much of it comes from related Senpai branches, paired
openings, deterministic repeats, and symmetry partners. Those rows are useful
for proving the pipeline and producing a bootstrap evaluator; they are not
independent evidence of broad Omega Chess understanding.

Before making strength claims:

1. Keep every ply of a game, AB/BA pair, opening family, deterministic repeat,
   and color-rank symmetry orbit in one split. A random position split leaks
   almost the entire game into validation.
2. Select roots without looking at engine scores or the recorded next move.
3. Treat the existing 20k-50k-node Senpai scores as low-confidence warm-start
   labels. Deep, fresh, book-disabled searches and actual game outcomes must
   anchor the corpus so the network does not merely copy shallow Senpai.
4. Keep the bishop-raid, Champion/Wizard, mating, and other frozen regression
   suites out of training.
5. Record the teacher executable hash, node budget, score bound, PV, tablebase
   provenance, and practical 100-ply safety in the upstream JSONL manifest.
6. Current non-KCCK tables lack DTZ. Their raw decisive WDL values are not
   automatically safe labels under Omega's 100-ply rule. KCCK decisive labels
   require DTM to fit the remaining halfmove budget.

The first network should therefore be described as a bootstrap candidate. Its
promotion path is engine regression testing followed by a controlled match
against the unchanged handcrafted evaluator.
