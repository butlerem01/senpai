# Two-Champion mating atlas

## What a "mating table" means

Regular chess has two related kinds of mating knowledge:

1. an exhaustive endgame tablebase, which assigns every legal material-specific
   position an exact result and distance;
2. a compressed human method, such as the rook's shrinking box or the
   bishop-and-knight W manoeuvre.

The KCCK WDL and DTM artifacts now provide the first kind for Omega Chess.
This document begins the second kind by extracting exact terminal records and
an optimal maximum-distance line. Syzygy's standard distinction between
[WDL and DTZ](https://github.com/syzygy1/tb#tablebase-files) is useful context,
but KCCK specifically needs DTM: no winning move can reset the no-progress
clock before mate.

## Exact result

The complete D4-canonical, labelled table contains:

| Quantity | Exact result |
|---|---:|
| Legal records | 23,638,870 |
| Attacker-winning records | 21,786,787 |
| Draw records | 1,852,083 |
| Terminal checkmates (`DTM 0`) | 3,352 |
| Median DTM | 18 plies |
| 90th percentile | 22 plies |
| 95th percentile | 23 plies |
| 99th percentile | 26 plies |
| Maximum, attacker to move | 39 plies |
| Maximum, defender to move | 40 plies |
| Theoretical wins over 100 plies | 0 |

The complete distance distribution is concentrated sharply:

| DTM band | Decisive records | Share |
|---|---:|---:|
| 0–10 plies | 973,075 | 4.466% |
| 11–20 plies | 16,278,329 | 74.717% |
| 21–30 plies | 4,489,831 | 20.608% |
| 31–40 plies | 45,552 | 0.209% |

The exact per-ply totals, combining attacker-to-move wins and
defender-to-move losses, are:

| DTM | Records | DTM | Records |
|---:|---:|---:|---:|
| 0 | 3,352 | 21 | 1,335,148 |
| 1 | 13,687 | 22 | 1,825,786 |
| 2 | 5,274 | 23 | 360,209 |
| 3 | 19,898 | 24 | 661,302 |
| 4 | 13,368 | 25 | 83,508 |
| 5 | 94,884 | 26 | 171,426 |
| 6 | 39,769 | 27 | 12,510 |
| 7 | 148,160 | 28 | 21,433 |
| 8 | 96,147 | 29 | 7,525 |
| 9 | 329,300 | 30 | 10,984 |
| 10 | 209,236 | 31 | 8,430 |
| 11 | 613,358 | 32 | 15,316 |
| 12 | 410,033 | 33 | 5,110 |
| 13 | 1,051,686 | 34 | 11,438 |
| 14 | 815,501 | 35 | 1,120 |
| 15 | 1,731,272 | 36 | 3,402 |
| 16 | 1,514,876 | 37 | 256 |
| 17 | 2,397,931 | 38 | 436 |
| 18 | 2,482,218 | 39 | 22 |
| 19 | 2,425,879 | 40 | 22 |
| 20 | 2,835,575 |  |  |

Thus two Champions do not merely possess theoretical mating power. With a
fresh no-progress clock, every theoretical KCCK win is rule-safe. All wins
remain safe at any halfmove clock at or below 60. At clock 80, where only
20 plies remain, 17,251,404 decisive records remain rule-safe. At clock 99,
only the 3,352 already-mated losses and 13,687 mate-in-one wins are inside the
remaining budget.

The equality boundary is exact. Independently probing the DTM-40 defender
root at halfmove 60 reports a rule-safe loss; its DTM-39 child is then reached
at halfmove 61 and remains a rule-safe win. Starting the DTM-40 root at
halfmove 61 instead reports a blessed loss, because the same child is reached
at halfmove 62 and is then a cursed win.

These are canonical labelled-record populations, not practical game
frequencies or material-value estimates.

## What makes conversion difficult

For the 10,639,893 attacker-to-move wins, distance changes strongly with
Champion deployment:

| Champions on detached corners | Records | Mean DTM | p50 | p90 | p99 | Maximum |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 9,818,608 | 16.307 | 17 | 21 | 23 | 37 |
| 1 | 810,340 | 20.788 | 21 | 25 | 31 | 37 |
| 2 | 10,945 | 25.492 | 25 | 29 | 29 | 39 |

Every maximum-distance attacker root has both Champions on detached corners.
Within this canonical table population, two detached Champions are strongly
associated with a longer conversion. The marginal comparison does not by
itself establish whether detached deployment was useful earlier in a game.

Distance is conditional on the root already being a win. Including every
legal attacker-to-move draw exposes a second, strategically important effect:

| Champions on detached corners | Winning records | Drawn records | Draw share |
|---:|---:|---:|---:|
| 0 | 9,818,608 | 34,301 | 0.348% |
| 1 | 810,340 | 55,720 | 6.434% |
| 2 | 10,945 | 3,226 | 22.765% |

The association is substantial: two detached Champions are linked not only
to the longest conversions, but also to a much larger draw share. This still
does not prove causation or identify the saving mechanism in each draw.

The bare king's location has the opposite conditional-distance pattern:

| Defender region | Records | Mean DTM | p50 | p90 | p99 | Maximum |
|---|---:|---:|---:|---:|---:|---:|
| Interior | 6,242,222 | 17.583 | 17 | 21 | 25 | 39 |
| Regular edge | 3,441,724 | 15.710 | 17 | 19 | 23 | 31 |
| Regular corner | 451,988 | 14.525 | 15 | 19 | 21 | 27 |
| Detached corner | 503,959 | 13.588 | 13 | 19 | 21 | 27 |

Interior bare-king records have the largest mean and maximum DTM, while
boundary records are shorter in this table population. This is consistent
with a classical confinement interpretation—reduce the king's usable region,
then construct the final net—but the marginal table alone does not prove that
moving toward an edge caused the reduction.

The corresponding draw shares are:

| Defender region | Winning records | Drawn records | Draw share |
|---|---:|---:|---:|
| Interior | 6,242,222 | 46,476 | 0.739% |
| Regular edge | 3,441,724 | 25,580 | 0.738% |
| Regular corner | 451,988 | 9,580 | 2.076% |
| Detached corner | 503,959 | 11,611 | 2.252% |

Thus a regular edge is consistent with faster conversion without a higher
draw share, but merely placing the king near a corner is not a complete
method. Regular and detached corners have about three times the draw share of
the interior and regular-edge strata.

Whether the attacking king itself begins on a detached corner changes the
mean only from 16.643 to 17.010 plies. Champion deployment matters much more
than the attacking king's initial region.

## Terminal mating records

The 3,352 labelled, D4-canonical terminal checkmate records collapse to 1,676
physical orbits after also identifying the two Champion labels; none is fixed
by the label swap. Their coarse region and checker-count properties divide as
follows:

| Bare-king region | One checking Champion | Two checking Champions | Total |
|---|---:|---:|---:|
| Interior | 336 | 0 | 336 |
| Regular edge | 2,046 | 58 | 2,104 |
| Regular corner | 210 | 0 | 210 |
| Detached corner | 702 | 0 | 702 |

Only 58 records, 1.73%, are double checks. Most mates use one Champion as the
checker and the other as a barrier/protector.

Further exact coarse terminal properties:

- 2,616 records, 78.04%, have the Champions mutually supporting one another.
- 2,120 records, 63.25%, have no square in the bare king's immediate move zone
  controlled by the attacking king.

The attacking king is therefore not always part of the final cage. Two
Champions can form a self-contained mating net, although king assistance can
still make the approach and many other mating configurations easier.

### A maximum-line final shell

The deterministic maximum-distance line ends at:

```text
AK=a0, Ca=b4, DK=a3, Cb=a4, defender to move

5  .  .
4  Cb Ca
3  k  .
2  .  .
1  .  .
0  K  .
   a  b
```

`Cb` on a4 checks the king on a3. The two Champions cross-protect on a4/b4.
The remaining king destinations are covered as follows:

| Defender destination | Reason unavailable |
|---|---|
| a4 | occupied by Cb and protected by Ca |
| b4 | occupied by Ca and protected by Cb |
| a2 | controlled by Cb from a4 |
| b2 | controlled by Ca from b4 |
| b3 | controlled by Ca from b4 |

The attacking king on a0 controls none of those five destinations. This is a
concrete, exact Champion-only cage.

## Longest optimal continuation

One maximum attacker-to-move root is:

```text
AK=a0, Ca=w1, DK=g6, Cb=w3, attacker to move; DTM 39
```

The following is labelled long-coordinate notation, not SAN. The attacker
chooses a shortest mate while the defender chooses maximum delay. When
several moves have the same exact distance, the extractor uses a deterministic
display tie-break.

```text
1.  Ca:w1-b1    k:g6-g7
2.  Ca:b1-d3    k:g7-h6
3.  Ca:d3-d5    k:h6-g6
4.  Ca:d5-c5    k:g6-g7
5.  Ca:c5-e7+   k:g7-h7
6.  Ca:e7-e6    k:h7-h8
7.  Ca:e6-g6    k:h8-h7
8.  Ca:g6-i8    k:h7-g7
9.  Ca:i8-i6    k:g7-f6
10. Cb:w3-i8    k:f6-e5
11. Ca:i6-g4    k:e5-d4
12. Cb:i8-g6    k:d4-e5
13. Cb:g6-e4+   k:e5-d6
14. Ca:g4-e6+   k:d6-c5
15. Cb:e4-f4    k:c5-b4
16. Cb:f4-d4+   k:b4-a4
17. Ca:e6-c4+   k:a4-a3
18. Ca:c4-b4    k:a3-a2
19. Cb:d4-c4+   k:a2-a3
20. Cb:c4-a4#
```

The maximum defender-to-move root is
`AK=a0,Ca=w1,DK=f5,Cb=w3`; its unique longest defence begins
`k:f5-g6` and enters the 39-ply line above.

This line illustrates four phases:

1. **Redeploy:** rescue Ca from w1.
2. **First barrier:** use Ca to restrict and redirect the central king while
   Cb remains remote.
3. **Second-piece entry:** bring Cb from w3 through i8 and g6; thereafter the
   Champions alternate checks and quiet barrier improvements.
4. **Edge cage:** force the king to the a-file and assemble the mutually
   protected adjacent pair.

The attacking king never moves in this particular worst-case optimal line.
That is an exact property of this line, not a claim that ignoring the king is
always best.

## Practical principles

The exact study suggests the following working technique:

1. Do not leave both Champions on detached corners once conversion begins.
2. Use one Champion to establish a barrier while the second redeploys.
3. Prefer reducing the king's region over giving purposeless checks.
4. Confinement toward a regular edge is promising, but corner proximity alone
   does not preserve a win. Recheck capture escapes and stalemate resources
   before closing the cage.
5. Near the finish, seek mutually protected Champion placements. Adjacent
   orthogonal support is particularly useful because Champions move one
   orthogonal square.
6. A common final division of labour is checker plus barrier, not double
   check.
7. Treat the deterministic principal variation as an illustration. Strategic
   frequency claims must aggregate every DTM-optimal child, not only the
   display tie-break.

## Draws are a separate problem

DTM has no value on the 1,852,083 drawn records. Existing exact totals sharpen
their interpretation:

| Turn/legal-status stratum | Records |
|---|---:|
| Defender to move in check; opposite-turn record illegal | 898,234 |
| Both turns legal; attacker-to-move twin wins | 767,355 |
| Both turns draw | 186,494 |

The last class represents 93,247 paired placements. Among the 10,733,140
placements with both turn records legal, only 0.869% draw regardless of turn.
Those are the strongest fortress candidates, but they are not proved
fortresses until the frozen draw-graph, SCC, and alternating-attractor study
is completed.

## Reproduction

The full distance table is `omega-kcck-dtm-v1.omtb4d`. Its companion CLI can
verify the source binding, probe distances, print optimal lines in retained
raw orientation, and reproduce the atlas:

```powershell
.\.build-omega-tb\four_man_wdl.exe `
  --inspect-dtm .\.build-omega-tb\omega-kcck-dtm-v1.omtb4d `
  --dtm-wdl .\.build-omega-tb\omega-kcck-wdl-v1.omtb4

.\.build-omega-tb\four_man_wdl.exe `
  --line-dtm .\.build-omega-tb\omega-kcck-dtm-v1.omtb4d `
  --dtm-wdl .\.build-omega-tb\omega-kcck-wdl-v1.omtb4 `
  --index 94094

.\.build-omega-tb\four_man_wdl.exe `
  --probe-dtm .\.build-omega-tb\omega-kcck-dtm-v1.omtb4d `
  --dtm-wdl .\.build-omega-tb\omega-kcck-wdl-v1.omtb4 `
  --halfmove 61 --index 93985 --index 94094

.\.build-omega-tb\four_man_wdl.exe `
  --atlas-dtm .\.build-omega-tb\omega-kcck-dtm-v1.omtb4d `
  --dtm-wdl .\.build-omega-tb\omega-kcck-wdl-v1.omtb4
```

The distance payload SHA-256 is
`b702ce64eb13610a9d4a952d8bd9e72340e809775617c6c19b229fcc41de1748`.
The complete companion-container SHA-256 is
`e6f12c4eda6064df9fe81f984d5d86222eb428552507401fae48ad0eed22275c`.
