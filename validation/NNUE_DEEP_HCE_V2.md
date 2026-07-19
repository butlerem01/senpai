# Deep HCE v2 training-data protocol

`tools/omega_nnue/deep_hce_v2.py` prepares fresh, leakage-resistant search
targets for the Omega residual NNUE. It does not use a prior NNUE screen or
confirmation game, PV, score, or position.

The target corpus contains exactly 4,096 paired units and 8,192 static
positions. The freeze also carries 64 candidate-blind reserve pairs per phase
(4,352 candidate pairs / 8,704 candidate roots total), used in fixed order
only when an earlier pair cannot produce two valid labels:

- 1,024 target pairs / 2,048 roots in each phase;
- one genuinely different A root and B root per pair;
- opposite side to move inside every pair;
- distinct exact inputs and distinct conservative rule-preserving symmetry
  orbits inside each pair and across the frozen pool;
- one split group for the complete A/B trajectory pair and repeated launch
  ancestry;
- no terminal/drawn root, halfmove clock of 90 or more, fewer than seven
  pieces, or side reduced to its lone king;
- phase-specific source-ply windows recomputed by Python rather than trusted
  from source metadata.

The deterministic rules-only sampler uses CoreChess move legality and pinned
SplitMix64 seed `2026071802`. It never starts an engine or calls an evaluator.
Captures are favored only to obtain useful late/endgame coverage. All six
Omega promotion choices (`Q/R/B/N/C/W`) participate in deterministic move
selection. An en-passant capture has an empty destination and remains in the
ordinary move pool; ChessLib still adjudicates legality.

## 1. Generate independent legal trajectories

From `senpai-omega-nnue`:

```powershell
python .\tools\omega_nnue\deep_hce_v2.py sample
```

This builds `OmegaRootSampler`, creates 2,048 independent A/B trajectory
pairs, and atomically writes:

- `build-msvc/data-generation/deep-hce-v2-random-roots.jsonl`;
- its sampler manifest;
- a freeze pinning the seed, sampler source/project/assembly, ChessLib
  runtime, local .NET host, output, and manifest.

The sampler retains a small hash-ranked reservoir per trajectory, phase, and
side instead of retaining whole games in memory.

## 2. Freeze the deep-search suite

```powershell
python .\tools\omega_nnue\deep_hce_v2.py prepare
```

Preparation may also admit candidate-blind historical AB/BA roots. Historical
candidates independently re-pass the same piece-count, pieces-per-side,
halfmove-clock, side-to-move, and phase/ply-window checks as rules-only roots.
Runs with `UseOmegaNNUE=true` or a configured NNUE file are rejected. Scores,
results, best moves, and PVs never influence root selection.

Every exact/symmetry input found in these sources is excluded:

- all existing `build-msvc/experimental-networks` corpora;
- every formal `build-msvc/confirmation` artifact;
- all discovered `screen-omega-nnue-*` and `confirm-omega-nnue-*` runs,
  including pure-v3 and residual-v3/v5/v7;
- every frozen NNUE screen/confirmation JSON under `validation`.

The exact current `senpai-omega-nnue.exe` is copied into the freeze.
`OwnBook=false`, `UseOmegaNNUE=false`, `Threads=1`, `Hash=128`, and
`UCI_Variant=omega` are fixed.

Before writing the freeze, preparation:

1. runs OmegaMatch/CoreChess legality and terminal validation over all
   candidate roots;
2. asks frozen Senpai to complete depth 1 and requires a
   syntactically scored info line, excluding its scoreless one-legal-move fast
   path without treating the shallow score as a teacher label or using its
   value, move, or PV for selection (`go nodes 1` is not used because it can
   stop before Senpai completes a root move and falsely emit `bestmove 0000`);
3. recomputes exact/symmetry uniqueness, material phase, ply windows, clock
   bounds, Champion/Wizard presence, material and ply distributions, ancestry
   caps, split-group counts, and a clustering-aware effective-group audit.

`legality-validation-only.json` is a one-node validation fixture. Never run it
as the teacher match.

The freeze pins and later re-verifies the HCE source and copied executable,
selector modules, every historical event log, rules-only roots and companions,
the complete forbidden-artifact inventory, opening suite, validation
configuration, OmegaMatch runtime bundle, and .NET host.

Strict structural verification is available without starting a label:

```powershell
python .\tools\omega_nnue\deep_hce_v2.py verify-freeze `
  --lock .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2.freeze.json
```

## 3. Seal, then run fixed-node labels

The reviewed freeze must first be covered by the king-state-v1 pre-label seal.
The sealer and runner share the exclusive
`deep-hce-v2-results.jsonl.coord.lock`, closing the race between declaring
label absence and starting the first teacher process.

No labels should start until the freeze audit has been reviewed.

```powershell
python .\tools\omega_nnue\deep_hce_v2.py run `
  --lock .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2.freeze.json `
  --seal .\build-msvc\data-generation\deep-hce-v2\king-state-v1-prelabel.seal.json `
  --jobs 4
```

Use `--jobs 1` for one worker. Bounded batches use `--limit`, for example
`--limit 512`. Repeating the command skips the latest valid success and
resumes the next required root. Invalid mate/bound/scoreless pairs advance
into frozen reserve order; transient process failures wait for a fresh
invocation.

Each attempt is one fsynced JSONL record containing the exact freeze hash.
It also contains the exact seal path/hash. A run refuses a missing, changed,
wrong-freeze, wrong-suite, wrong-teacher, or wrong-runner seal. The shared
guard is held for the complete run.

The fixed contract is 100,000 nodes per root. A final unscored max-node line
at attempted depth A proves that A was interrupted. Every progressive score
at A is discarded; depth A-1 must have score evidence, and its last score must
be exact cp, non-bound, and non-mate. The target
corpus costs 819,200,000 nodes; using every reserve would cap it at
870,400,000. At 15k-40k NPS, the target is roughly 5.7-15.2 hours with one
worker or an ideal 1.4-3.8 hours with four before contention.

## 4. Finalize and build residual targets

```powershell
python .\tools\omega_nnue\deep_hce_v2.py finalize `
  --lock .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2.freeze.json `
  --seal .\build-msvc\data-generation\deep-hce-v2\king-state-v1-prelabel.seal.json
```

Finalization replays raw UCI output instead of trusting derived fields, keeps
the latest valid success even if a later attempt failed, and selects the first
1,024 fully valid pairs in each frozen phase order. It refuses an exhausted
reserve, short-node search, changed freeze/engine/suite/provenance, metadata
mismatch, or forbidden orbit. Entire pair/ancestry groups are unioned again
when any conservative symmetry collision is observed.

The corpus rows and manifest propagate the exact seal and its canonical static
HCE evaluator identity. Residual construction must use that sealed helper,
not an arbitrary later build.

Then label the same positions with the static HCE and subtract it:

```powershell
python .\tools\omega_nnue\label_hce.py `
  --input .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2-search.jsonl `
  --output .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2-static-hce.jsonl `
  --cpp-evaluator .\<BUILD>\omega_nnue_tests.exe

python .\tools\omega_nnue\build_residual_targets.py `
  --search-teacher .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2-search.jsonl `
  --handcrafted .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2-static-hce.jsonl `
  --output .\build-msvc\data-generation\deep-hce-v2\deep-hce-v2-residual.jsonl
```

## Self-tests

```powershell
python .\tools\omega_nnue\deep_hce_v2.py self-test
dotnet build .\tools\omega_nnue\OmegaRootSampler\OmegaRootSampler.csproj -c Release
```

The Python self-test covers phase-balanced A/B pairing, opposite side to move,
symmetry exclusion, rules-only parsing, NNUE-enabled historical-source
rejection, and rejection of mate, bound, and short fixed-node scores. It also
pins Senpai's real fixed-node pattern: an earlier exact score followed by an
unscored update at exactly 100,000 nodes. The sampler uses the same ChessLib
project as the legality referee.
