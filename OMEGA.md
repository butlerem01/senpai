# Native Omega Chess support

This fork runs standard chess and Omega Chess through Senpai's native search.
It advertises:

```text
option name UCI_Variant type combo default chess var chess var omega
```

Set the option to `omega` before `position`. `position startpos` selects the
canonical 10x10 plus four-corner setup; `position fen ...` accepts OFEN. Moves
use `a0`-`j9` and detached corner coordinates `w1`-`w4`. Promotions accept
`q`, `r`, `b`, `n`, `c`, or `w`.

Implemented native rules include Champion and Wizard jumps, Knight access to
the detached corners, three-square initial pawn moves, both en-passant landing
squares, all four Omega castlings, check/evasion filtering, repetition and
50-move state, and insufficient material for a lone N/B/C/W.

The Omega evaluator is a dedicated tapered HCE. It follows the official
material ordering (`P < N < W < C < B < R < Q`) and evaluates safe mobility,
pawn structure and launch tempo, passed pawns, development, open files,
king-zone attacks, pawn shelter, castling potential, threats, and
corner-sensitive endgame material. Sparse Omega positions use conservative
search pruning so unblockable leaper tactics and quiet mating nets are retained.
The weights are still hand-tuned; standard chess continues to use Senpai's
original trained evaluator.

Current base values are `P=100`, `N=225`, `W=375`, `C=400`, `B=425`,
`R=600`, and `Q=1200` centipawns, with small tapered endgame adjustments.

The starting values are based on the [official strategy guide](https://www.omegachess.com/strategy).
Official [mating puzzles](https://www.omegachess.com/puzzles) are included as
search regressions.

## Build

On Clang/GCC:

```sh
make -C src
```

From a Visual Studio Developer PowerShell:

```powershell
./build-msvc.ps1
```

The native dual-variant executable is `build-msvc/senpai.exe`. The Makefile
also retains `src/senpai-omega`, a small independent companion implementation
used as a rule/perft oracle.

## Verification

Tests in `tests/` cover geometry, OFEN, state and hashing, castling, promotion,
near/far en passant, Champion/Wizard legality, malformed positions, draw
material, evaluator symmetry and structure, official mate-in-two positions,
and native depth-5 search. Native and companion opening perft agree at 40,
1,600, and 67,202 nodes through depths 1-3.
