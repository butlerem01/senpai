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

The header's architecture field self-describes the output meaning without
changing the frozen tensor layout:

- architecture `1` is the original absolute evaluator and replaces HCE;
- architecture `2` is a residual evaluator whose output is added to HCE.

Every existing architecture-1 `OMNNUE1` file keeps its original bytes and
runtime behavior. The engine reads the semantics from the header, not from the
filename. Draw adjudication remains ahead of either evaluator, and the final
HCE-plus-correction score passes through the normal evaluation-score clamp.

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
Use `--target-cp-clip 2000` for the initial HCE/search distillation runs so a
small number of overwhelming or tablebase-like values cannot dominate the
bootstrap network. The manifest records the threshold and clipped-row count.

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

Use `--split-seed` when a group-size-only audit chooses a split with balanced
row counts; `--seed` then remains the independent model-initialization seed.
The manifest records both. Select a split seed without inspecting scores or
outcomes.

Example:

```powershell
python .\tools\omega_nnue\train.py `
  --input ..\omega-lab\data\nnue\nnue-samples-v1.jsonl `
  --output .\build-msvc\omega-bootstrap-v1.nnue `
  --seed 20260718 --epochs 12 --qat-epochs 3
```

The default `0.01` base Adam rate is scaled by layer: `0.1` for the feature
transformer and dense bias, `0.005` for the 256-input dense matrix, and `1.0`
for the output layer. Quantization-aware epochs apply a further `0.1` global
multiplier. Without the dense-matrix scale, its coherent inputs can drive
every clipped ReLU below zero in one update, after which the strict activation
mask cannot recover. Each epoch records active/dead/saturated-unit telemetry
and training fails explicitly if all dense units die. The exported checkpoint
is the epoch with the best quantized validation loss, rather than blindly the
last epoch. Override the scale options only for controlled optimizer
experiments.

Fine-tuning starts from a previously exported, validated network with
`--initial-network <file.nnue>`. The quantized tensors are converted to
exactly representable float parameters, and both the starting file and final
network are hash-pinned in the manifest.

Normal training also writes `<network>.float`, a deterministic binary shadow
checkpoint with a payload SHA-256. Resume losslessly with
`--initial-float-checkpoint`; this preserves sub-integer parameter motion that
the deployable `.nnue` necessarily discards. Use `--initial-network` only when
no shadow checkpoint exists. The two resume options are mutually exclusive
and every checkpoint is hash-pinned and round-trip verified.

Training epochs interleave three occupancy-phase bands with negative, quiet,
and positive CP bands. This preserves one visit per sample while preventing a
long run of same-phase or same-sign positions from producing a destructive
common-mode Adam update. `--unstratified-batches` is available for an
ablation.

The adjacent manifest pins each input's absolute path, byte count, and SHA-256;
the hashes of `train.py` and `omega_nnue.py`; Python, NumPy, platform, and
available NumPy/BLAS build configuration; split and collision counts; seed;
loss settings; quantized metrics; network SHA-256 and payload FNV; and epoch
history. Training aborts if an input changes between its initial pin and
manifest creation.

Epoch history starts at epoch `0`, the quantized initial checkpoint. Model
selection compares every completed epoch with that baseline using validation
loss (or training loss when no validation split exists). If no update
improves it, `selectedEpoch` is `0`,
`selection.selectedInitialCheckpoint` is `true`, and the exported network is
byte-for-byte the initial deployable checkpoint.

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

and emit one bare signed integer. The parity run also exports six fixed
golden networks temporarily and compares seven hardcoded vectors exactly across
Python and C++. Results and the helper's path, size, and SHA-256 are recorded
under `crossRuntime`. Without `--cpp-evaluator`, Python still runs all golden
vectors and the manifest records cross-runtime status as `not-requested`.

## Handcrafted-evaluator pretraining labels

The test helper can keep one C++ process alive while it evaluates a JSONL
corpus with Senpai's exact Omega handcrafted evaluator. This is useful for
pretraining the network to reproduce the existing evaluator before mixing in
deeper search labels:

```powershell
python .\tools\omega_nnue\label_hce.py `
  --input ..\omega-lab\data\current\training-positions.jsonl `
  --output .\build-msvc\experimental-networks\omega-hce-labels-v1.jsonl `
  --cpp-evaluator .\.build-msvc-tests\Release\tests\omega_nnue.exe
```

Every input object is preserved, including its group and provenance fields,
while `targetCpStm` is replaced or added with an integer score from the
side-to-move perspective. `UseOmegaNNUE` is forced off in the helper. The
adjacent manifest pins the input snapshot, output, helper executable, and
labeling tool by SHA-256. Output and manifest files are fully staged and
fsynced before they replace older files.

Run the end-to-end protocol and atomic-write smoke test with:

```powershell
python .\tools\omega_nnue\label_hce.py --self-test `
  --cpp-evaluator .\.build-msvc-tests\Release\tests\omega_nnue.exe
```

HCE labels are a warm start, not independent evidence of strength: a network
trained only on them is being taught to imitate the baseline it must
eventually surpass.

## Residual search-teacher targets

Residual training teaches only the correction from handcrafted evaluation to
the search teacher. First run the HCE labeler on the **exact search-teacher
JSONL**, preserving its `sampleId` and position:

```powershell
python .\tools\omega_nnue\label_hce.py `
  --input .\build-msvc\experimental-networks\omega-teacher-expanded-v1.jsonl `
  --output .\build-msvc\experimental-networks\omega-teacher-expanded-hce-v1.jsonl `
  --cpp-evaluator .\.build-msvc-tests\Release\tests\omega_nnue.exe
```

Then build aligned correction labels:

```powershell
python .\tools\omega_nnue\build_residual_targets.py `
  --search-teacher .\build-msvc\experimental-networks\omega-teacher-expanded-v1.jsonl `
  --handcrafted .\build-msvc\experimental-networks\omega-teacher-expanded-hce-v1.jsonl `
  --output .\build-msvc\experimental-networks\omega-teacher-residual-v1.jsonl
```

The builder requires one-to-one unique `sampleId` coverage, equal normalized
OFEN, and an equal hash of the exact two-perspective NNUE input. It emits
`targetCpStm = searchTargetCpStm - handcraftedCpStm`, tags every row
`targetSemantics=search-minus-handcrafted`, moves absolute game-outcome labels
to the explicit `searchOutcomeStm`/`searchSideToMoveScore` fields, and writes a
content-pinned manifest. Any missing, extra, duplicate, or
position-mismatched row is a hard error.

Train and export an architecture-2 network explicitly:

```powershell
python .\tools\omega_nnue\train.py `
  --input .\build-msvc\experimental-networks\omega-teacher-residual-v1.jsonl `
  --output .\build-msvc\experimental-networks\omega-residual-v1.nnue `
  --network-semantics residual --cp-weight 1 --outcome-weight 1 `
  --seed 20260718 --epochs 12 --qat-epochs 3
```

For residual networks, the CP loss remains
`predictedCorrection - targetCorrection`. Outcome BCE instead converts
`handcraftedCpStm + predictedCorrection` to a probability and sends its
gradient into the correction network. Both scores are already from the
side-to-move perspective, so Black rows are not negated again. A positive
residual outcome weight is rejected when the selected input has no search
outcomes or when an outcome lacks a finite handcrafted baseline. Set
`--outcome-weight 0` explicitly only for a deliberately CP-only residual run.

The trainer rejects untagged or arithmetically inconsistent residual rows and
rejects an `--initial-network` whose header semantics disagree with the
selected mode. `--network-semantics absolute` remains the default for backward
compatibility. Run the alignment smoke test independently with:

```powershell
python .\tools\omega_nnue\build_residual_targets.py --self-test
```

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

## Extracting fixed-node teacher scores

OmegaMatch telemetry records every UCI `info` update. Convert its last exact
centipawn score at each successfully played ply into leakage-aware training
rows with:

```powershell
python .\tools\omega_nnue\extract_teacher.py `
  --input-root ..\match-runs\output `
  --exclude "*confirm-omega-integrated-pair-v1-96*" `
  --output .\build-msvc\experimental-networks\omega-teacher-v1.jsonl `
  --min-requested-nodes 20000 --min-depth 6 --clip-cp 2000
```

The extractor selects the newest complete retry of every game, requires the
latest scored update to be exact (a later bound or mate score is not replaced
with an older exact value), ignores failed searches, snapshots append-only
logs, and writes source hashes plus engine/search provenance to a manifest.
Split groups are keyed by the global searched opening-root position rather
than by a suite filename/hash. Exact transpositions union their complete root
groups before the duplicate rows are collapsed, preventing the retained
representative from hiding a cross-split collision. By default, rows
that are identical to the current NNUE input are collapsed: only the highest
available requested-node bucket is retained and its median exact score is
used. Pass `--keep-duplicates` only for diagnostics.

These are bootstrap teacher labels, not independent proof of strength. The
network must still pass frozen regressions and a paired fixed-node match
against the unchanged handcrafted evaluator.

For the first search-label fine-tune, cap repeated source families and
interleave phase/score bands deterministically:

```powershell
python .\tools\omega_nnue\select_teacher.py `
  --input .\build-msvc\experimental-networks\omega-teacher-strict-v1.jsonl `
  --output .\build-msvc\experimental-networks\omega-teacher-tune-v1.jsonl `
  --max-records 12000 --max-per-group 96 --seed 20260718
```

Selection is allowed to use teacher scores because this is a training subset,
not held-out evidence. The adjacent manifest pins the input/output and records
phase, score-band, and per-group coverage.
