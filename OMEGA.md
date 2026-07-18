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
./test-msvc.ps1
```

The native dual-variant executable is `build-msvc/senpai.exe`. The Makefile
also retains `src/senpai-omega`, a small independent companion implementation
used as a rule/perft oracle.

For reproducible one-thread experiments, UCI `go nodes N` accepts a positive
signed 64-bit node limit. With `Threads=1`, the final `info` output reports the
exact number of searched nodes. Searches that omit `nodes` are unchanged.

## Optional opening book

The native engine advertises these UCI options:

```text
option name OwnBook type check default false
option name OmegaBookFile type string default <empty>
```

`OwnBook=false` is the default and always searches normally. To enable the
book, set `OmegaBookFile` to a book file and set `OwnBook=true`. Setting the
file to `<empty>` disables and unloads it. An invalid replacement is rejected
atomically, so a previously loaded book remains available. Absolute file paths
are recommended.

The v1 format is UTF-8/ASCII text:

```text
senpai-omega-book-v1
<six-field OFEN><TAB><lowercase UCI move><TAB><positive integer weight>
```

Blank lines and lines beginning with `#` are ignored. Multiple moves may be
listed for one position. Senpai selects the greatest weight; equal weights are
resolved by lexical UCI move order, making the choice independent of file
order, thread count, and platform. Duplicate position/move records are an
error. Positions use canonical six-field OFEN. Placement, side to move,
castling, en-passant, and the halfmove clock match exactly. The final fullmove
number is validated but normalized for lookup because it is notation metadata,
and CoreChess and Senpai historically advance it at different times.

The parser does not change the active board geometry, so a GUI may send
`OmegaBookFile` before `UCI_Variant=omega`. At a hit, every candidate is parsed
as strict lowercase Omega UCI and matched against the canonical UCI strings of
the generated legal root move list. Illegal candidates are skipped and can
never be played. A successful hit
emits stable telemetry such as:

```text
info string omega book hit a0c2
```

The Omega book is inactive in standard chess, infinite analysis, and ponder
searches. It also has no effect when the current normalized book key is absent.

## Verification

Tests in `tests/` cover geometry, OFEN, state and hashing, castling, promotion,
near/far en passant, Champion/Wizard legality, malformed positions, draw
material, evaluator symmetry and structure, official mate-in-two positions,
native depth-5 search, deterministic opening-book selection, atomic book
replacement, and UCI book bypasses. Native and companion opening perft agree at 40,
1,600, and 67,202 nodes through depths 1-3.

An evaluator-independent exact endgame foundation lives in `tools/omega_tb`.
It provides verified KRK, KCK, KRKC, KRKN, KWKN, KCKW, and KCCK theoretical
WDL, geometry and graph parity checks, and a checksummed production format.
Set the UCI string option `OmegaTablebasePath` to a directory containing the
six fixed core production files and, for exact two-Champion draws, the
optional `omega-kcck-wdl-v1.omtb`.
Search consumes exact draws only; theoretical wins and losses fall back to
normal search until DTZ and the 100-ply conversion boundary are implemented.
