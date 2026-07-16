# Omega exact tablebase foundation

This directory contains standalone, evaluator-independent foundations for
exact Omega Chess tablebases plus the checked conversion boundary used by
Senpai's read-only runtime probe. This branch intentionally contains **no
heuristic four-man score scaler**.

## Implemented

- Shared dense D4 rank/unrank for labelled three- and four-piece states on all
  104 Omega squares (`omega_geometry.py`).
- The retained KRKC index/count proof (`index_prototype.py`).
- A full exact theoretical-WDL retrograde solver for `KRK`.
- Rules-exact `KCK`/`KNK`/`KWK` capture policies. Senpai currently declares
  king plus one Knight, Bishop, Champion, or Wizard versus a bare king drawn
  in `Pos::is_draw()`.
- A standalone C++17 exact KRKC/KRKN/KWKN/KCKW/KCCK theoretical-WDL generator
  (`four_man_*.{hpp,cpp,ps1}`). KRKC/KRKN consume the checksummed KRK
  dependency. KWKN and diagnostic-only KCKW instead freeze their one-leaper
  capture-to-draw policies in the artifact header. Diagnostic KCCK generalizes
  ownership to two labelled, same-side Champions and freezes either Champion's
  capture as a KCK draw. All five use on-demand
  predecessors instead of retaining a reverse graph.
- A checksummed, read-only offline four-man probe in `four_man_wdl`. It accepts
  a dense index or a labelled four-piece tuple and reports WDL from the
  side-to-move perspective. KWKN order is
  `wizard_king,wizard,knight_king,knight,turn`; KCKW order is
  `champion_king,champion,wizard_king,wizard,turn`; KCCK order is
  `attacker_king,champion_a,defender_king,champion_b,turn`.
- Versioned, checksummed three-man foundation files. They are deliberately not
  yet an engine probe format.

Run every full-family test and Bellman check:

```powershell
powershell -ExecutionPolicy Bypass -File tools/omega_tb/test.ps1
```

Build the native Senpai graph oracle and compare 5,000 deterministic random
states per material family, plus corner and reported-game regressions:

```powershell
# Run from a Visual Studio Developer PowerShell.
powershell -ExecutionPolicy Bypass -File tools/omega_tb/test-native-parity.ps1
```

The parity check compares historical legality, current-side check, the native
insufficient-material result, KxR/KxC capture-to-KK edges, and every quiet
successor after mapping it through the Python D4 index. A slower complete graph
comparison is available with `-Exhaustive`.

Run the generator directly and optionally write the compact WDL files:

```powershell
python tools/omega_tb/three_man_wdl.py --material both --verify
python tools/omega_tb/three_man_wdl.py --material both --verify --output build/omega-tb
```

Verify the four-man index and exact legal-state counts:

```powershell
python tools/omega_tb/index_prototype.py --verify-counts
```

Build and exercise the C++ generator on a deterministic 100,000-slot bounded
model. States outside that prefix are explicit draw boundaries, so this is a
fast Bellman/serialization regression and not a substitute for the full table:

```powershell
powershell -ExecutionPolicy Bypass -File tools/omega_tb/four_man_test.ps1

# Also freeze completed KRKN, KWKN, KCKW, and KCCK artifacts.
powershell -ExecutionPolicy Bypass -File tools/omega_tb/four_man_test.ps1 `
  -KrknFullPath build/omega-tb/omega-krkn-wdl-v1.omtb4 `
  -KwknFullPath build/omega-tb/omega-kwkn-wdl-v1.omtb4 `
  -KckwFullPath .build-omega-tb/omega-kckw-wdl-v1.omtb4 `
  -KcckFullPath .build-omega-tb/omega-kcck-wdl-v1.omtb4
```

Generate the full KRKC, KRKN, KWKN, KCKW, and diagnostic KCCK WDL files offline:

```powershell
python tools/omega_tb/three_man_wdl.py --material krk --output build/omega-tb
powershell -ExecutionPolicy Bypass -File tools/omega_tb/four_man_build.ps1
.\.build-omega-tb\four_man_wdl.exe --material krkc --self-test --verify-counts `
  --krk build/omega-tb/omega-krk-wdl-v1.omtb3
.\.build-omega-tb\four_man_wdl.exe --material krkc --full --verify `
  --krk build/omega-tb/omega-krk-wdl-v1.omtb3 `
  --output build/omega-tb/omega-krkc-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --material krkn --self-test --verify-counts `
  --krk build/omega-tb/omega-krk-wdl-v1.omtb3
.\.build-omega-tb\four_man_wdl.exe --material krkn --full --verify `
  --krk build/omega-tb/omega-krk-wdl-v1.omtb3 `
  --output build/omega-tb/omega-krkn-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --material kwkn --self-test --verify-counts
.\.build-omega-tb\four_man_wdl.exe --material kwkn --full --verify `
  --output build/omega-tb/omega-kwkn-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --summary `
  build/omega-tb/omega-kwkn-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --material kckw --self-test --verify-counts
.\.build-omega-tb\four_man_wdl.exe --material kckw --full --verify `
  --output .build-omega-tb/omega-kckw-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --summary `
  .build-omega-tb/omega-kckw-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --material kcck --self-test --verify-counts
.\.build-omega-tb\four_man_wdl.exe --material kcck --full --verify `
  --output .build-omega-tb/omega-kcck-wdl-v1.omtb4
.\.build-omega-tb\four_man_wdl.exe --summary `
  .build-omega-tb/omega-kcck-wdl-v1.omtb4

# Probe one or more records after validating the complete container.
.\.build-omega-tb\four_man_wdl.exe --probe build/omega-tb/omega-krkc-wdl-v1.omtb4 `
  --state 101,96,45,74,0 --index 26750996
```

The full payload is 27,594,696 bytes plus one JSON header line. The generator
uses one status byte and one unresolved-successor byte per dense slot, a
four-byte FIFO entry per solved legal state in the worst case, about 6 MiB for
the D4 lookup tables, and, for the rook families, the 274 KiB KRK dependency.
Budget roughly 160 MiB
of RAM (192 MiB is a comfortable process limit) and about 55 MiB of free disk
while the checksummed file is atomically replaced. It is intentionally
single-threaded and deterministic. The first verified full solve plus Bellman
pass completed in 46.16 seconds on the development machine; KWKN completed in
41.02 seconds, KCKW in 60.81 seconds, and KCCK plus its exhaustive D4/label
swap pass in 135.41 seconds. Use
`--small 1000000` to benchmark another machine before a full build.

## Exact populations

| Family | D4 index slots | Legal | Loss | Draw | Win |
|---|---:|---:|---:|---:|---:|
| KRK | 273,816 | 235,033 | 339 | 232,962 | 1,732 |
| KCK | 273,816 | 244,779 | 0 | 244,779 | 0 |
| KRKC | 27,594,696 | 22,607,206 | 2,909 | 22,576,396 | 27,901 |
| KRKN | 27,594,696 | 23,034,346 | 2,564 | 23,005,864 | 25,918 |
| KWKN | 27,594,696 | 24,078,355 | 17,131 | 23,997,362 | 63,862 |
| KCKW | 27,594,696 | 23,651,215 | 4,647 | 23,631,170 | 15,398 |
| KCCK | 27,594,696 | 23,638,870 | 11,146,894 | 1,852,083 | 10,639,893 |

WDL is stored from the side-to-move perspective. `invalid` occupies the
historically illegal index slots. Each generated payload is one byte per D4
slot plus one JSON header line.

## Current limitations

- Runtime probing is enabled through the UCI string option
  `OmegaTablebasePath`. The directory must contain the fixed KRK, KCK, and
  KRKC, KRKN, and KWKN `OMTBPROD` filenames documented in
  `PRODUCTION_FORMAT.md`.
- Search consumes exact tablebase draws only. Theoretical win/loss records
  deliberately fall through to normal search until DTZ can account for the
  100-ply conversion boundary.
- KRK is theoretical WDL. DTZ, cursed/blessed results, and the 100-ply draw
  counter still need the next generation layer.
- Compact `OMTB3WDL`/`OMTB4WDL` files are offline verification artifacts;
  convert them to `OMTBPROD` before runtime loading.
- Indexing includes every labelled spatial placement. Historically illegal
  positions receive the reserved `invalid` code; this avoids a second sparse
  legality index.
- KCK/KNK/KWK capture results intentionally follow the current automatic
  insufficient-material policy. Changing that rule requires a new rules
  fingerprint and regeneration.
- KWKN is exact theoretical WDL, but its decisive records are diagnostic-only
  at runtime. See `KWKN_THEORY.md` for turn-stratified populations and the
  limits of interpreting WDL without DTM/DTZ.
- KCKW is an offline diagnostic only. It is not part of `OMTBPROD`, the
  all-or-nothing runtime material set, or engine evaluation. See
  `KCKW_THEORY.md` for its frozen hashes, turn/material-side populations,
  paired-placement counts, and decisive witnesses.
- KCCK is also an offline diagnostic only. Its complete WDL solve confirms
  that two Champions can mate with king support but qualifies any universal
  claim because verified drawn placements remain. See `KCCK_THEORY.md` for
  full counts, hashes, label-swap proof, and production limits.
- Native/Python parity is sampled by default to keep normal verification fast;
  use `test-native-parity.ps1 -Exhaustive` for every indexed three-man state.

See `DESIGN.md` for the retained four-man dependency plan.
