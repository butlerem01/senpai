# Omega KRKC tablebase indexing prototype

This directory contains a standalone, evaluator-independent prototype for an
exact four-man Omega Chess tablebase with material `KR v KC`.  It does not alter
Senpai's search or playing behaviour.

Run the proof-oriented self-test and exact legal-state count with:

```powershell
python tools/omega_tb/index_prototype.py --verify-counts
```

The index orders the labelled pieces as rook-side king, rook, Champion-side
king, Champion, followed by a side-to-move bit relative to those material
roles.  Colour therefore does not need to be stored.  The 104-square Omega
board has eight dihedral symmetries, all respected by this index.

## Exact sizes

| Population | States |
|---|---:|
| Raw four-piece placements | 110,355,024 |
| Raw placements with side to move | 220,710,048 |
| D4-canonical placements | 13,797,348 |
| D4-canonical states with side to move | 27,594,696 |
| Legal raw states | 180,820,964 |
| Legal D4-canonical states | 22,607,206 |

The canonical count follows directly from Burnside's lemma:

```text
(104P4 + 2 * 12P4) / 8 = 13,797,348
```

Only the identity and two diagonal reflections contribute fixed placements.
The other five symmetries fix no square on an even 10x10 board.  Each diagonal
fixes ten regular squares and two detached Wizard squares.

For constant-time production indexing, first anchor the rook-side king.  Its
square has 16 D4 orbits: ten have a trivial stabilizer and six have a two-way
reflection stabilizer.  A generic block contains `103P3 = 1,061,106` ordered
placements.  A reflected block contains
`(103P3 + 11P3) / 2 = 531,048` placements.  The prototype implements this
construction as an exact dense rank/unrank pair.  Its shared reflected-block
lookup needs only 531,048 32-bit entries (about 2.1 MB).

An all-state production file using three packed WDL bits and a 16-bit DTZ value
would occupy 65,537,403 bytes before its header (about 62.5 MiB).  Reserving one
WDL code for an illegal state avoids a second legality index.  A byte-aligned
WDL implementation is simpler and still only about 79 MiB.

The three-man capture dependencies are much smaller:

| Material | Raw states | Legal D4-canonical states |
|---|---:|---:|
| KRK | 2,185,248 | 235,033 |
| KCK | 2,185,248 | 244,779 |

Build `KRK` before `KRKC`.  Under Senpai's current insufficient-material rule,
every `KCK` successor is immediately drawn; a generator should still encode
that as a versioned rules decision rather than silently assuming it.

See `DESIGN.md` for retrograde generation, 50-move semantics, file format, and
the proposed Senpai probe boundary.
