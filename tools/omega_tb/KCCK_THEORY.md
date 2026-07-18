# KCCK exact WDL and distance-to-mate result

## Answer

King plus two Champions **can** force mate against a bare king in Omega
Chess, and does so from most legal KCCK records. The official
[Omega Chess strategy page](https://www.omegachess.com/strategy) lists two
Champions as mating easily. The exact WDL and DTM solves confirm both the
mating capability and the short conversion claim: the maximum is 40 plies,
the median is 18, and every theoretical win fits inside a fresh 100-ply
no-progress window. Because 1,852,083 of 23,638,870 legal labelled records are
draws, the published claim is still **confirmed and qualified**, not universal.
Here "short" is an exact optimal-distance statement; it does not establish
that the technique is easy for a human to discover or execute.

These are complete tablebase results, not samples. WDL says whether mate can
eventually be forced with perfect play; DTM says how many plies it takes when
the attacker minimizes and the defender maximizes the distance.

## Frozen result

The full `D4-first-piece-v1` table has 27,594,696 dense slots:

| Class | Records |
|---|---:|
| historically illegal | 3,955,826 |
| legal | 23,638,870 |
| side to move loses | 11,146,894 |
| draw | 1,852,083 |
| side to move wins | 10,639,893 |

Both Champions belong to the attacking side, so every decisive record is an
attacker win. Combining `win` on the attacker's turn with `loss` on the bare
king's turn gives 21,786,787 attacker-winning records, about 92.2% of the
legal labelled population. This percentage is a property of the index, not an
estimate of how often positions occur in games.

The side-to-move split is:

| Turn | Invalid | Loss | Draw | Win |
|---|---:|---:|---:|---:|
| attacker | 3,064,208 | 0 | 93,247 | 10,639,893 |
| defender | 891,618 | 11,146,894 | 1,758,836 | 0 |

For the 10,733,140 labelled spatial placements where both side-to-move records
are historically legal:

| Paired classification | Placements |
|---|---:|
| attacker wins with either side to move | 9,872,538 |
| one turn is decisive and the other drawn | 767,355 |
| drawn with either side to move | 93,247 |
| opposite sides win | 0 |

Thus the qualification is substantive: verified all-draw placements exist,
not merely an artifact of defender-to-move records. The current WDL layer
does not classify draw mechanisms; doing that requires following
draw-preserving moves, identifying capture escapes and cycles, and testing
detached-corner candidates for fortress behavior.

## Reproducibility

The complete artifact is `omega-kcck-wdl-v1.omtb4` and has:

- payload SHA-256:
  `35f9d8bcb3b283dec2f918fcc4c5d62355edc2dee3ee297e16dd42a91940678e`;
- complete-container SHA-256:
  `bff047fab9141666ae13f80db17ae2d5d5a808bdd115b131180e529bf493a244`;
- rules SHA-256:
  `ac44f4152d0c619468addc65c580c064a1e8c5bc6c31509f50243739e8f4431f`;
- capture-policy SHA-256:
  `286a4e80294c4118093c8223b3414859c3a8f634ae401ee49cecb4b636c7172b`.

The full graph passed a second Bellman-equation check. An exhaustive pass over
all records and all eight board symmetries preserved legality and WDL. An
exhaustive swap of Champion A and Champion B did the same. Collapsing the
label swap produced 13,800,384 orbits, including 926,142 draw orbits and 6,072
orbits fixed through a board symmetry.

Frozen witnesses include:

- checkmate: `AK=a1,Ca=a0,DK=w1,Cb=b1,turn=defender`
  (native state `1,0,100,11,1`, dense index 1,081,911);
- stalemate: `AK=a1,Ca=a0,DK=w1,Cb=a2,turn=defender`
  (native state `1,0,100,2,1`, dense index 1,081,893);
- deeper forced win: dense index 0,
  `AK=a0,Ca=b1,DK=c2,Cb=d3,turn=attacker`;
- nonterminal forced loss: dense index 3,
  `AK=a0,Ca=b1,DK=c2,Cb=e4,turn=defender`;
- mate in one: dense index 1,328,
  `AK=a0,Ca=b1,DK=a2,Cb=a5,turn=attacker`.

The experiment contract, ownership map, capture boundary, and concrete edge
fixtures are frozen in `KCCK_DESIGN.md`.

## Exact distance-to-mate result

The accepted `OMTB4DTM` companion resolves every one of the 21,786,787
decisive records:

| Measure | Plies |
|---|---:|
| median | 18 |
| p90 | 22 |
| p95 | 23 |
| p99 | 26 |
| maximum, attacker to move | 39 |
| maximum, defender to move | 40 |

There are no theoretical wins beyond 100 plies. KCCK has no pawn move and no
win-preserving capture: a defender capture exits to drawn KCK. DTM therefore
directly supplies the rule budget. For a nonterminal root with halfmove clock
`h < 100`, a theoretical result remains rule-safe exactly when
`DTM <= 100 - h`; mate at equality takes precedence under Senpai's current
adjudication order. Every theoretical win is safe from a fresh clock, and all
remain safe at clocks through 60.

The DTM payload SHA-256 is
`b702ce64eb13610a9d4a952d8bd9e72340e809775617c6c19b229fcc41de1748`;
the companion-container SHA-256 is
`e6f12c4eda6064df9fe81f984d5d86222eb428552507401fae48ad0eed22275c`.
The companion passed full Bellman, turn-parity, D4, Champion-label-swap,
serialization, source-binding, and raw-oriented optimal-line replay gates.

A frozen 17,128-root native/independent parity corpus also matched Senpai's
live KCCK legality, check status, exact labelled successors, both Champion
capture labels, capture-to-draw behavior, and detached-corner geometry. Its
SHA-256 is
`300f9a25f2b6592b0322e79382acd7a9e5d819a7dc8ca8d2c44598b216471729`.
The independently generated expected-record stream is separately frozen at
`2c1cf4bf03837b43ac58b1c5acd1fe5e15029eaa974d490e08f1f6f5e44b92ab`.

`KCCK_MATING_ATLAS.md` gives the full histogram, coarse terminal-mate census,
hardest roots, draw-risk cross-tabs, and a verified 39-ply
attacker-to-move line.

## Production boundary and next experiments

KCCK WDL now has a checked `OMTBPROD` conversion and an optional runtime file.
Search consumes exact draw records only, with native repetition, automatic
draw, mate, and stalemate taking precedence. Decisive W/L and the separate
DTM companion remain diagnostic-only: using them requires distance-aware
search scoring bound to the root's remaining `100 - halfmove_clock` budget.

The highest-value follow-ups are:

1. classify all 1,852,083 draw records by saving mechanism under the frozen
   `KCCK_DRAW_DESIGN.md` contract, with special focus on the 93,247
   turn-independent drawn placements;
2. extend the native/independent sample into a direct solver/native
   three-way or exhaustive graph gate;
3. expand the coarse terminal census into a diagrammed human mating-pattern
   atlas;
4. turn representative win, draw, capture-escape, and fortress-candidate
   records into engine-evaluation regressions without treating table
   populations as piece values.
