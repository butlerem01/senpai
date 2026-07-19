# Omega NNUE screening

`omega-nnue-screen-roots-v1.json` is a candidate-blind, phase-stratified
screening suite. It contains four roots in each of four material phases. Every
root is played as one AB/BA color-swapped pair by OmegaMatch.

This suite is **screen-only**. After excluding both NNUE training corpora, their
source games and opening families, frozen regression positions, symmetry
orbits, and the active integrated match, the honest local pool contained only
three new CoreChess games. Multiple roots from one game are correlated.
OmegaMatch's pair bootstrap does not model that source-game clustering, so a
positive confidence interval from these 32 games is useful triage but is not
evidence for promotion or a claim that NNUE is clearly superior.

The complete selection evidence is in
`omega-nnue-screen-roots-v1.audit.json`. It pins both training corpora and
manifests, both candidate catalogs, every regression file, the byte boundary of
the active-match snapshot, all three held-out saves, the selector and its
frozen-v1 feature module, and the generated suite. Canonical placement,
side-to-move, and effective castling features are checked under all four legal
Omega symmetries. The selector reads no candidate evaluations.

## Rebuild the supplemental held-out catalog

The current OmegaLab catalog is itself part of HCE pretraining, so it cannot
supply held-out roots. Rebuild the supplemental catalog from the six local
CoreChess saves; the selector will reject the three already present in the
current catalog and retain only source-disjoint games:

```powershell
& .\.dotnet\dotnet.exe run `
  --project .\omega-lab\src\OmegaLab.Cli\OmegaLab.Cli.csproj `
  -c Release -- catalog `
  --output .\senpai-omega-nnue\build-msvc\screening-catalog-v1 `
  --input "$HOME\Downloads\analyze purizu.ccsf" `
  --input "$HOME\Downloads\great game.ccsf" `
  --input "$HOME\Downloads\hmm.ccsf" `
  --input "$HOME\Downloads\shit.ccsf" `
  --input "$HOME\Downloads\smart.ccsf" `
  --input "$HOME\OneDrive\Documents\wow.ccsf"
```

Then rerun the exact selector command recorded by version control:

```powershell
python .\senpai-omega-nnue\tools\omega_nnue\select_screen.py `
  --training-corpus .\senpai-omega-nnue\build-msvc\experimental-networks\omega-teacher-strict-v1.jsonl `
  --training-corpus .\senpai-omega-nnue\build-msvc\experimental-networks\omega-hce-labels-v1.jsonl `
  --training-manifest .\senpai-omega-nnue\build-msvc\experimental-networks\omega-teacher-strict-v1.manifest.json `
  --training-manifest .\senpai-omega-nnue\build-msvc\experimental-networks\omega-hce-labels-v1.jsonl.manifest.json `
  --catalog .\omega-lab\data\current `
  --catalog .\senpai-omega-nnue\build-msvc\screening-catalog-v1 `
  --regressions-dir .\omega-lab\regressions `
  --active-events .\match-runs\output\confirm-omega-integrated-pair-v1-96-20260718\events.jsonl `
  --output-suite .\senpai-omega-nnue\validation\omega-nnue-screen-roots-v1.json `
  --output-audit .\senpai-omega-nnue\validation\omega-nnue-screen-roots-v1.audit.json `
  --workspace-root . --roots 16 --seed 20260718 `
  --family-plies 8 --minimum-ply-gap 5 --maximum-roots-per-game 6
```

## Instantiate and run a screen

Copy `omega-nnue-screen-v1.template.json` to a candidate-specific filename.
Replace the candidate ID, network path, and lowercase network SHA-256. Rebuild
the executable pin too if the binary changed. Both sides intentionally use the
same executable; `UseOmegaNNUE` is the only evaluation switch. Threads, hash,
book, variant, color pairing, and node budget are identical.

Validate before play:

```powershell
& .\.dotnet\dotnet.exe run `
  --project .\corechess-arena\Tools\OmegaMatch\OmegaMatch.csproj `
  -c Release -- validate --config <candidate-screen.json>
```

Validation must confirm the NNUE load/active diagnostics and reject illegal or
terminal roots. Then use `run` with the same config. Treat any illegal move,
illegal PV, protocol failure, time forfeit, or failed regression as an
automatic rejection.

## Confirmation remedy

A candidate can graduate from screening only through a new confirmation set:

1. Freeze and hash the candidate network first.
2. Generate candidate-blind source games using only the unchanged HCE control,
   book off, and a shallow fixed-node budget.
3. Before evaluating the candidate, sample at most one phase-stratified root
   from each source game.
4. Freeze the source logs, root suite, executable, selector, and all hashes.
5. Run paired AB/BA confirmation. Require no correctness regressions and a
   positive paired 95% lower confidence bound.

The candidate may not influence source generation, root selection, phase
assignment, or exclusions.
