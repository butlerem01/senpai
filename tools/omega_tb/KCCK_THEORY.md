# KCCK exact theoretical-WDL result

## Answer

King plus two Champions **can** force mate against a bare king in Omega
Chess, and does so from most legal KCCK records. The official
[Omega Chess strategy page](https://www.omegachess.com/strategy) lists two
Champions as mating easily. The exact solve confirms the mating capability,
but cannot yet confirm "easily": distance was not solved, and 1,852,083 of
23,638,870 legal labelled records are draws. The published claim is therefore
**confirmed and qualified**, not contradicted.

This is a complete theoretical-WDL result, not a sample. It says whether mate
can eventually be forced with perfect play and no move-count limit. It does
not yet say how long conversion takes, how practical the win is, or whether a
win survives Omega Chess's automatic 100-ply rule.

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
not just positions where the defender happens to have an immediate saving
move. The current WDL layer does not classify draw mechanisms; doing that
requires following draw-preserving moves and identifying capture escapes,
cycles, and detached-corner fortresses.

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

- checkmate: `AK=b0,Ca=a0,DK=w2,Cb=b1,turn=defender`
  (native state `1,0,100,11,1`, dense index 1,081,911);
- stalemate: `AK=b0,Ca=a0,DK=w2,Cb=c0,turn=defender`
  (native state `1,0,100,2,1`, dense index 1,081,893);
- deeper forced win: dense index 0,
  `AK=a0,Ca=b1,DK=c2,Cb=d3,turn=attacker`;
- nonterminal forced loss: dense index 3,
  `AK=a0,Ca=b1,DK=c2,Cb=e4,turn=defender`;
- mate in one: dense index 1,328,
  `AK=a0,Ca=b1,DK=a2,Cb=a5,turn=attacker`.

The experiment contract, ownership map, capture boundary, and concrete edge
fixtures are frozen in `KCCK_DESIGN.md`.

## Production boundary and next experiments

This family remains diagnostic-only. It is not in `OMTBPROD`, the runtime
loader, search, evaluation, or automatic adjudication. Before decisive records
could guide play, add DTM or rule-aware DTZ and test the 100-ply boundary.

The highest-value follow-ups are:

1. add distance-to-mate to measure whether the nominal wins are practical;
2. classify the 93,247 turn-independent drawn placements by saving mechanism;
3. solve KWWK with separate opposite-color and same-color Wizard strata;
4. turn representative win, draw, capture-escape, and fortress records into
   engine-evaluation regressions without treating table populations as piece
   values.
