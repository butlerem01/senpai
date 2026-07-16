# Exact KWKN theoretical-WDL note

## Result and scope

The completed KWKN table is an exact, full-boundary theoretical-WDL solve over
the retained `D4-first-piece-v1` index. It uses the native Omega Wizard rule
(`(1,1)`, `(1,3)`, or `(3,1)` in absolute coordinate deltas), the native
Knight rule, all four detached corners, historical legality, and the current
automatic draws for KWK and KNK after a capture. A full Bellman recheck passed.

Frozen artifact facts:

- Dense slots: 27,594,696
- Legal slots: 24,078,355
- Invalid: 3,516,341
- Loss: 17,131
- Draw: 23,997,362
- Win: 63,862
- Source payload SHA-256:
  `8aedc0255122da32dc6c5684e673b5454eee55878caefc1908b429993cd6e2b8`

WDL is always relative to the side to move. The exact turn split is:

| Side to move | Legal | Loss | Draw | Win | Decisive |
|---|---:|---:|---:|---:|---:|
| Wizard side | 11,881,140 | 12,239 | 11,853,479 | 15,422 | 27,661 |
| Knight side | 12,197,215 | 4,892 | 12,143,883 | 48,440 | 53,332 |
| Total | 24,078,355 | 17,131 | 23,997,362 | 63,862 | 80,993 |

Thus 99.6636% of legal canonical records are draws and 0.3364% are decisive.
Re-expressed by the material side that eventually wins, the Wizard side wins
20,314 canonical records and the Knight side wins 60,679. These are counts of
D4-canonical records, not a probability distribution over reachable game
positions, so they should not be read as a direct piece-value ratio. They do
show that neither leaper can generally force conversion and that the rare
decisive geometry favors the Knight side roughly three to one in this index.

## Verified decisive witnesses

The offline `--summary` pass regenerates legal moves for its witnesses and
checks their stored children, rather than classifying from the byte value
alone:

- Terminal mate: index 258,375,
  `WK=a0,W=a7,NK=w4,N=a9,turn=knight` is a checkmate loss.
- Immediate mating tactic: index 52,964,
  `WK=a0,W=g6,NK=w3,N=j9,turn=wizard` is a win with a move to terminal-loss
  child 1,002,321.
- Turn-independent Wizard win: indices 1,143,704/1,143,705,
  `WK=a1,W=a4,NK=w1,N=a0`. Wizard-to-move is Win and Knight-to-move is Loss;
  both positions are nonterminal.
- Turn-independent Knight win: indices 26,532,746/26,532,747,
  `WK=w1,W=a0,NK=c2,N=b2`. Wizard-to-move is Loss and Knight-to-move is Win;
  both positions are nonterminal.

Capturing the opposing leaper is never a decisive boundary in this solve:
both KWK and KNK are policy draws. Decisive records therefore arise from mate
nets and forced non-capture geometry, not from treating an immediate capture
as a win. The two turn-independent examples establish that decisive content is
not limited to terminal mate or one-ply tactics.

The table has no DTM or DTZ. “Turn-independent” above means the same spatial
placement is won by the same material side for either turn; it does not state
how long conversion takes. Search consequently consumes only Draw records.
Win/Loss records remain available to diagnostics but deliberately fall through
to normal search until 100-ply-aware DTZ exists.
