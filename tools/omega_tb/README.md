# Omega exact tablebase foundation

This directory contains standalone, evaluator-independent foundations for
exact Omega Chess tablebases. Nothing here changes Senpai's search or playing
behaviour, and this branch intentionally contains **no heuristic KRKC score
scaler**.

## Implemented

- Shared dense D4 rank/unrank for labelled three- and four-piece states on all
  104 Omega squares (`omega_geometry.py`).
- The retained KRKC index/count proof (`index_prototype.py`).
- A full exact theoretical-WDL retrograde solver for `KRK`.
- A rules-exact `KCK` policy table. Senpai currently declares king plus one
  Champion versus a bare king drawn in `Pos::is_draw()`, before move search, so
  every legal KCK record is a draw under the current rules fingerprint.
- A standalone C++17 exact KRKC theoretical-WDL generator
  (`four_man_*.{hpp,cpp,ps1}`). It consumes the checksummed KRK dependency,
  treats KCK captures according to the current automatic-draw policy, and uses
  on-demand predecessors instead of retaining a reverse graph.
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
```

Generate the full KRKC WDL file offline:

```powershell
python tools/omega_tb/three_man_wdl.py --material krk --output build/omega-tb
powershell -ExecutionPolicy Bypass -File tools/omega_tb/four_man_build.ps1
.\.build-omega-tb\four_man_wdl.exe --self-test --verify-counts `
  --krk build/omega-tb/omega-krk-wdl-v1.omtb3
.\.build-omega-tb\four_man_wdl.exe --full --verify `
  --krk build/omega-tb/omega-krk-wdl-v1.omtb3 `
  --output build/omega-tb/omega-krkc-wdl-v1.omtb4
```

The full payload is 27,594,696 bytes plus one JSON header line. The generator
uses one status byte and one unresolved-successor byte per dense slot, a
four-byte FIFO entry per solved legal state in the worst case, about 6 MiB for
the D4 lookup tables, and the 274 KiB KRK dependency. Budget roughly 160 MiB
of RAM (192 MiB is a comfortable process limit) and about 55 MiB of free disk
while the checksummed file is atomically replaced. It is intentionally
single-threaded and deterministic. Depending on CPU, a full solve plus the
optional second Bellman pass should be budgeted from several minutes to under
an hour; use `--small 1000000` to benchmark the machine before a full build.

## Exact populations

| Family | D4 index slots | Legal | Loss | Draw | Win |
|---|---:|---:|---:|---:|---:|
| KRK | 273,816 | 235,033 | 339 | 232,962 | 1,732 |
| KCK | 273,816 | 244,779 | 0 | 244,779 | 0 |
| KRKC | 27,594,696 | 22,607,206 | pending full run | pending full run | pending full run |

WDL is stored from the side-to-move perspective. `invalid` occupies the
remaining three-man index slots. The generated payload is one byte per D4
slot (273,816 bytes), plus one JSON header line.

## Current limitations

- KRKC runtime probing is not implemented yet; the C++ output remains an
  offline verification artifact.
- KRK is theoretical WDL. DTZ, cursed/blessed results, and the 100-ply draw
  counter still need the next generation layer.
- The compact `OMTB3WDL` file is a verification artifact, not the final
  memory-mapped production format.
- Indexing includes every labelled spatial placement. Historically illegal
  positions receive the reserved `invalid` code; this avoids a second sparse
  legality index.
- KCK results intentionally follow the current automatic insufficient-material
  policy. Changing that rule requires a new rules fingerprint and regeneration.
- Native/Python parity is sampled by default to keep normal verification fast;
  use `test-native-parity.ps1 -Exhaustive` for every indexed three-man state.

See `DESIGN.md` for the retained KRKC dependency plan.
