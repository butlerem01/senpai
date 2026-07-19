# Independent Omega NNUE confirmation

`tools/omega_nnue/confirmation_suite.py` builds a promotion-quality root suite
without letting the candidate choose its own test. It is intentionally a
two-stage protocol:

1. `prepare` freezes the candidate network, the single Senpai executable used
   for both evaluators, the final candidate manifest, the exact
   `OmegaMatch.dll` and its local runtime bundle (including `ChessLib.dll`),
   every training corpus and stage/input manifest, every extra exclusion, the
   launch suites, all selector code, and the selection policy.
   It then writes source-match configurations containing two copies of the
   handcrafted evaluator only (`UseOmegaNNUE=false`, `OwnBook=false`).
2. Complete every generated source match. `select` rejects a partial, unsafe,
   pre-freeze, changed, non-HCE, book-enabled, or non-fixed-node source run. It
   excludes every training input and its rule-preserving symmetry orbit, then
   chooses exactly one root from each source AB/BA pair and at most one from
   each individual game. Phase quotas are equal.
3. `select` freezes the root suite, audit, and candidate-vs-HCE OmegaMatch
   configuration. Only after those files exist should the candidate be run.

The deterministic rank contains the precommitted seed, source run/pair/game,
source ply, and symmetry-orbit key. It does not contain the candidate hash,
either evaluator's score, the game outcome, best move, or PV. `gameResult`
records are used only to prove that every precommitted source game completed
without a safety failure.

Horizontal file reflection is included in the leakage orbit only after
castling rights are gone. With castling rights present it would move an Omega
king off its required f-file castling origin and is therefore not a
rule-preserving symmetry.

## Lightweight check

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\confirmation_suite.py self-test
```

The smoke test uses synthetic files only. It does not start an engine or
interfere with a running arena.

## Freeze and prepare source games

Do this only after the final candidate network and executable have been built.
Supply all corpora used by any training or fine-tuning stage. Include all
corpus manifests and warm-start stage manifests. `prepare` requires the final
manifest's exact `roundTrip.sha256` to equal the candidate, every direct input
to be one of the frozen corpora, every corpus to occur in a supplied manifest,
and each warm-start hash to identify exactly one matching producer stage
(`initialNetwork` to `roundTrip`, or `initialFloatCheckpoint` to
`floatCheckpoint`). Every input of every supplied schema-v2 trainer manifest
must also be a frozen corpus, so an earlier HCE-pretraining corpus cannot be
omitted from screening. This also lets the source-log reuse audit see hashes
already consumed by training. A 64-root
confirmation needs at least 64 launch openings; use a larger, diverse
candidate-blind launch pool because exact/symmetry collisions and games that
do not reach every phase are rejected.

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\confirmation_suite.py prepare `
  --candidate-network .\senpai-omega-nnue\build-msvc\experimental-networks\<candidate>.nnue `
  --candidate-manifest .\senpai-omega-nnue\build-msvc\experimental-networks\<candidate>.manifest.json `
  --engine-executable .\senpai-omega-nnue\build-msvc\senpai-omega-nnue.exe `
  --omega-match-assembly .\corechess-arena\Tools\OmegaMatch\bin\Release\net10.0\OmegaMatch.dll `
  --launch-suite .\path\to\fresh-launch-pool-a.json `
  --launch-suite .\path\to\fresh-launch-pool-b.json `
  --training-corpus .\senpai-omega-nnue\build-msvc\experimental-networks\omega-hce-labels-v1.jsonl `
  --training-corpus .\senpai-omega-nnue\build-msvc\experimental-networks\omega-teacher-strict-v1.jsonl `
  --training-corpus .\senpai-omega-nnue\build-msvc\experimental-networks\omega-teacher-tune-v1.jsonl `
  --training-manifest .\senpai-omega-nnue\build-msvc\experimental-networks\omega-hce-labels-v1.jsonl.manifest.json `
  --training-manifest .\senpai-omega-nnue\build-msvc\experimental-networks\omega-teacher-strict-v1.manifest.json `
  --training-manifest .\senpai-omega-nnue\build-msvc\experimental-networks\omega-teacher-tune-v1.manifest.json `
  --training-manifest .\senpai-omega-nnue\build-msvc\experimental-networks\<warm-start-stage>.manifest.json `
  --exclude-directory .\omega-lab\regressions `
  --exclude-position-file .\senpai-omega-nnue\validation\omega-nnue-screen-roots-v1.json `
  --output-dir .\senpai-omega-nnue\build-msvc\confirmation\<candidate-id> `
  --roots 64 --seed 20260718 --source-nodes 5000 `
  --confirmation-nodes 50000
```

The command prints each `source-configs/source-*.json`. Validate and run every
one with the pinned OmegaMatch build:

```powershell
& .\.dotnet\dotnet.exe `
  .\corechess-arena\Tools\OmegaMatch\bin\Release\net10.0\OmegaMatch.dll `
  validate --config <source-config.json>

& .\.dotnet\dotnet.exe `
  .\corechess-arena\Tools\OmegaMatch\bin\Release\net10.0\OmegaMatch.dll `
  run --config <source-config.json>
```

Do not use `--pair-budget` here: every precommitted source game must finish
before selection. Stopping when the phase pool happens to look favorable would
make the source collection outcome-dependent.

## Freeze roots and instantiate the match

After every source run is complete:

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\confirmation_suite.py select `
  --lock .\senpai-omega-nnue\build-msvc\confirmation\<candidate-id>\confirmation-freeze.lock.json
```

This produces:

- `confirmation-roots.json`: equal opening/middlegame/late/endgame roots;
- `confirmation-audit.json`: hashes, source checks, exclusion counts, and the
  exact provenance of every selected root;
- `confirmation-match.json`: the same executable on both sides, with only
  `UseOmegaNNUE` and the pinned external network differing;
- `confirmation-selection.seal.json`: an exclusive one-time seal over the
  lock, candidate, suite, audit, and match configuration.

Once the seal exists, the lock cannot be selected again or redirected to new
outputs. This prevents post-match candidate results from being used to
reselect a more favorable root set.

Validate `confirmation-match.json` before starting it. OmegaMatch must confirm
the candidate's `Omega NNUE loaded` and `Omega NNUE evaluation active`
diagnostics and independently reject illegal or terminal roots.
Invoke the same pinned `OmegaMatch.dll` directly; both source and confirmation
configs enforce its SHA-256 plus a composite hash over all local DLLs, native
runtime libraries, deps/runtimeconfig files, and the apphost before play.
Keep the published OmegaMatch directory dedicated to the harness: unrelated
runtime-shaped files there intentionally become part of the frozen bundle.
The generated config also pins the exact bytes of `confirmation-roots.json`;
validation fails if the suite is edited or replaced after the one-time seal.

The workflow enforces at least 64 roots. The default therefore produces 64
complete color-swapped pairs (128 games), matching the sequential gate's
minimum useful confirmation scale. A promotion
still requires zero illegal moves, illegal PVs, protocol failures, or time
forfeits and the predeclared sequential gate signal. The audit's independence
claim concerns test construction; it does not replace the arena's strength and
safety gates.
