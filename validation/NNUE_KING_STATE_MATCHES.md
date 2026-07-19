# King-state NNUE fresh match gates

`tools/omega_nnue/king_state_matches.py` implements the three fresh match
suites preregistered in
`validation/omega-nnue-king-state-v1-protocol.json`. It does not use
`confirmation_suite.py`: that older workflow derives confirmation roots from
HCE games, while this generation requires evaluator-free rules-only roots.

The tool creates all three suites in one transaction before candidate play:

| Gate | Roots/pairs | Games | Root balance | Search |
|---|---:|---:|---|---|
| development | 32 | 64 | 8/phase, 4/phase/STM | 20,000 nodes/move |
| equal-node | 128 | 256 | 32/phase, 16/phase/STM | 50,000 nodes/move |
| equal-time | 128 | 256 | 32/phase, 16/phase/STM | 1,000 ms/move |

One root is one OmegaMatch AB/BA pair. Every four-pair scheduled block has one
opening, one middlegame, one late, and one endgame root. Root side to move is
constant inside a block and alternates between blocks. The suite order is
inverse-arranged for OmegaMatch's seeded .NET shuffle, so a pair budget that is
a multiple of four ends at a balanced checkpoint.

Selection is candidate blind. It takes at most one root from a sampler
trajectory pair, excludes exact and conservative symmetry-orbit collisions
with every frozen training input and prior screen/confirmation, and makes the
three new suites mutually orbit-disjoint. The seal pins the selector and orbit
code, sampler source/runtime/output, protocol, engine, network, training
selection and corpora, exclusion inventory, OmegaMatch runtime bundle, suites,
configs, and audit.

## 1. Synthetic safety test

Run this before producing any artifacts. It exercises schedule compatibility,
leakage rejection, one-time sealing, tamper detection, development assessment,
equal-time telemetry, and all three sequential outcomes without starting an
engine.

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py self-test
```

## 2. Generate the three rules-only pools

The fixed sampler contract is SplitMix64, 2,048 trajectory pairs, two
independent trajectories per pair, 220 maximum plies, two retained positions
per phase/side, and 72% capture selection when a capture is available. The
three seeds are `2026071901`, `2026071902`, and `2026071903`.

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py sample `
  --dotnet .\.dotnet\dotnet.exe `
  --output-dir .\senpai-omega-nnue\build-msvc\king-state-v1-match-sources
```

`sample` prints and executes one isolated `dotnet build` plus three direct
sampler-assembly commands. The isolated runtime lives below the output
directory, so sampling cannot overwrite the runtime pinned by teacher-data
generation. It refuses to overwrite that runtime, a source, or a manifest.

## 3. Select and seal all match suites

Do this only after the offline gate has selected one candidate and its final
network and engine have been built. Repeat `--training-corpus` for every corpus
used by any stage. Supply the canonical training plan, validation-selection
seal, and offline report through repeated `--training-selection`. The match
sealer follows their file identities, requires the selected network and
manifest to match the candidate, and requires the candidate manifest's closed
input set to equal `--training-corpus`.

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py seal `
  --sampler-dir .\senpai-omega-nnue\build-msvc\king-state-v1-match-sources `
  --engine-executable .\senpai-omega-nnue\build-king-state-v1\senpai.exe `
  --candidate-network .\senpai-omega-nnue\build-king-state-v1\selected.nnue `
  --candidate-manifest .\senpai-omega-nnue\build-king-state-v1\selected.manifest.json `
  --training-selection .\senpai-omega-nnue\build-msvc\king-state-v1\training-plan.json `
  --training-selection .\senpai-omega-nnue\build-msvc\king-state-v1\validation-selection.seal.json `
  --training-selection .\senpai-omega-nnue\build-msvc\king-state-v1\offline-test.json `
  --training-corpus .\senpai-omega-nnue\build-msvc\data-generation\deep-hce-v2-search.jsonl `
  --omega-match-assembly .\corechess-arena\Tools\OmegaMatch\bin\Release\net10.0\OmegaMatch.dll `
  --output-dir .\senpai-omega-nnue\build-king-state-v1\matches `
  --match-output-root .\match-runs\output\king-state-v1
```

The default historical exclusion inventory covers:

- `senpai-omega-nnue/validation`;
- `senpai-omega-nnue/build-msvc/confirmation`;
- workspace `match-runs/output`;
- workspace `omega-lab/regressions`.

Use repeated `--exclude FILE_OR_DIRECTORY` for any additional prior screen,
confirmation, or position-bearing artifact. The output directory itself is
never scanned as history, so it must be a dedicated directory containing no
pre-existing JSON/JSONL. The sampler directory must be a separate tree, and
all three configured run directories must not yet exist. `seal` exclusively
creates these files and refuses to replace any of them:

- `king-state-v1-{development,equal-node,equal-time}-roots.json`;
- `king-state-v1-{development,equal-node,equal-time}-match.json`;
- `king-state-v1-matches.audit.json`;
- `king-state-v1-matches.seal.json`.

Verify the seal before every validate, run, resume, or assessment:

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py verify `
  --seal .\senpai-omega-nnue\build-king-state-v1\matches\king-state-v1-matches.seal.json
```

## 4. Validate and run

Invoke the pinned `OmegaMatch.dll` directly. Do not use `dotnet run`, which may
rebuild the frozen harness. Replace `<gate>` with `development`,
`equal-node`, or `equal-time`.

```powershell
& .\.dotnet\dotnet.exe `
  .\corechess-arena\Tools\OmegaMatch\bin\Release\net10.0\OmegaMatch.dll `
  validate `
  --config .\senpai-omega-nnue\build-king-state-v1\matches\king-state-v1-<gate>-match.json

& .\.dotnet\dotnet.exe `
  .\corechess-arena\Tools\OmegaMatch\bin\Release\net10.0\OmegaMatch.dll `
  run `
  --config .\senpai-omega-nnue\build-king-state-v1\matches\king-state-v1-<gate>-match.json `
  --pair-budget 4
```

Use `resume` instead of `run` for later balanced four-pair increments. A full
gate may be run without `--pair-budget`. OmegaMatch itself runs games
sequentially and starts fresh engine processes per game.

The development screen is not promotion evidence. It must complete all 32
pairs with zero safety failures and score at least 40%. Only then proceed to
the two formal gates.

Immediately before equal-time play, stop other CPU-heavy work, confirm that
only this one match process will run, and exclusively create the attestation:

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py attest `
  --seal .\senpai-omega-nnue\build-king-state-v1\matches\king-state-v1-matches.seal.json `
  --output .\senpai-omega-nnue\build-king-state-v1\matches\equal-time-idle-attestation.json `
  --operator $env:USERNAME
```

Creating the file is an operator attestation, not an automatic load detector.
It records the exact run ID, required `createdUtc` timestamp, idle-machine
declaration, one-game serialization, and one concurrent match process.

## 5. Assess only balanced blocks

Development:

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py assess `
  --seal .\senpai-omega-nnue\build-king-state-v1\matches\king-state-v1-matches.seal.json `
  --gate development `
  --events .\match-runs\output\king-state-v1\development\events.jsonl `
  --output .\senpai-omega-nnue\build-king-state-v1\matches\development-assessment.json
```

Equal-node:

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py assess `
  --seal .\senpai-omega-nnue\build-king-state-v1\matches\king-state-v1-matches.seal.json `
  --gate equal-node `
  --events .\match-runs\output\king-state-v1\equal-node\events.jsonl `
  --output .\senpai-omega-nnue\build-king-state-v1\matches\equal-node-assessment.json
```

Equal-time:

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\king_state_matches.py assess `
  --seal .\senpai-omega-nnue\build-king-state-v1\matches\king-state-v1-matches.seal.json `
  --gate equal-time `
  --events .\match-runs\output\king-state-v1\equal-time\events.jsonl `
  --idle-attestation .\senpai-omega-nnue\build-king-state-v1\matches\equal-time-idle-attestation.json `
  --output .\senpai-omega-nnue\build-king-state-v1\matches\equal-time-assessment.json
```

The assessor re-hashes the whole seal and run identities, reconstructs only
complete AB/BA pairs, and checks the predeclared phase order only after each
four-pair block. Its e-process uses the frozen null of +10 Elo, promotion
threshold 20, futility threshold 10, minimum 64 pairs, and maximum 128 pairs.
Crossings before the complete 64-pair checkpoint are ignored rather than
latched for later activation. At 128 pairs, `continue` becomes `inconclusive`,
never promotion. Any illegal move, illegal PV, protocol failure, time forfeit,
or abandoned attempt produces `safety-fail`.
An unfinished current attempt produces `in-progress` and withholds every
formal promote/futility decision. ArenaRunner's one extra unapplied terminal
ply on a crash, timeout, missing move, or illegal move is accepted only with a
positive safety counter, a recorded error, and no resulting position; it then
produces `safety-fail`.

For equal-time it additionally rejects an invalid attestation, overlapping
game intervals, wrong `go movetime 1000` commands, missing nodes/depth/NPS, a
harness deadline, or wall time beyond the frozen 1,000 ms plus 2,000 ms grace.
Telemetry must name the engine that actually moved and all nodes, depth, NPS,
and elapsed-time values must be finite and nonnegative. The assessment records
per-engine mean nodes, depth, NPS, wall time, and the maximum observed wall
time.

The NNUE is “clearly superior” only if the offline test, development screen,
equal-node gate, equal-time gate, and every safety/runtime gate all pass, with
both formal match decisions equal to `promote`.
