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
- Versioned, checksummed three-man foundation files. They are deliberately not
  yet an engine probe format.

Run every full-family test and Bellman check:

```powershell
powershell -ExecutionPolicy Bypass -File tools/omega_tb/test.ps1
```

Run the generator directly and optionally write the compact WDL files:

```powershell
python tools/omega_tb/three_man_wdl.py --material both --verify
python tools/omega_tb/three_man_wdl.py --material both --verify --output build/omega-tb
```

Verify the four-man index and exact legal-state counts:

```powershell
python tools/omega_tb/index_prototype.py --verify-counts
```

## Exact populations

| Family | D4 index slots | Legal | Loss | Draw | Win |
|---|---:|---:|---:|---:|---:|
| KRK | 273,816 | 235,033 | 339 | 232,962 | 1,732 |
| KCK | 273,816 | 244,779 | 0 | 244,779 | 0 |
| KRKC | 27,594,696 | 22,607,206 | not generated | not generated | not generated |

WDL is stored from the side-to-move perspective. `invalid` occupies the
remaining three-man index slots. The generated payload is one byte per D4
slot (273,816 bytes), plus one JSON header line.

## Current limitations

- KRKC retrograde generation and runtime probing are not implemented yet.
- KRK is theoretical WDL. DTZ, cursed/blessed results, and the 100-ply draw
  counter still need the next generation layer.
- The compact `OMTB3WDL` file is a verification artifact, not the final
  memory-mapped production format.
- Indexing includes every labelled spatial placement. Historically illegal
  positions receive the reserved `invalid` code; this avoids a second sparse
  legality index.
- KCK results intentionally follow the current automatic insufficient-material
  policy. Changing that rule requires a new rules fingerprint and regeneration.

See `DESIGN.md` for the retained KRKC dependency plan.
