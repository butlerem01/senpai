# KCCK draw-mechanism analysis contract

## Purpose and boundary

The exact KCCK table contains 1,852,083 legal labelled draw records, including
93,247 labelled spatial placements that are drawn with either side to move.
Those totals establish that two Champions do not win universally, but they do
not explain why a record is drawn.

This follow-up is a diagnostic graph analysis of the already frozen
`OMTB4WDL` payload. It must not alter move generation, WDL, the source-artifact
format, production tables, runtime probing, search, evaluation, or draw
adjudication. A category is descriptive evidence, not a new boundary rule.

## Units and nonexclusive flags

Every headline number must say which unit it uses:

- **record**: a labelled spatial placement plus side to move;
- **paired placement**: the two side-to-move records for one labelled spatial
  placement, only when both are historically legal;
- **label orbit**: records or placements after swapping Champion A/B;
- **raw placement**: an unquotiented board position, used only if D4 orbit
  expansion is explicitly requested.

First report these nonexclusive flags over draw records. Their overlaps are
meaningful and must not be silently discarded:

1. `terminal-stalemate`: the side to move is not in check and has no legal
   move;
2. `immediate-capture-a`: the bare defender to move can legally capture
   Champion A, exiting to a KCK policy draw;
3. `immediate-capture-b`: the corresponding legal capture of Champion B;
4. `immediate-either-capture`: the union of the previous two flags, with a
   separate intersection count when both captures are legal;
5. `defender-detached-corner`: the bare king occupies native square
   `100..103`; this is geometry, not by itself proof of a fortress;
6. `both-turn-draw`: the opposite-turn record for the same labelled spatial
   placement is historically legal and also has WDL draw.

The A/B capture counts must be equal under the already verified exhaustive
label swap. Both-turn-draw must reconstruct exactly 93,247 paired placements
and 186,494 flagged records before any deeper analysis is accepted.

## Optional headline partition

If a single disjoint overview is useful, apply this frozen precedence to each
draw record:

1. terminal stalemate;
2. immediate either-Champion capture exit;
3. defender on a detached corner;
4. both-turn draw;
5. residual draw.

This precedence is only a presentation partition. The full nonexclusive
flags and overlap matrix remain authoritative; for example, one turn of a
both-turn-draw placement may have an immediate capture while the other does
not.

## Residual draw graph and SCC classes

Construct a directed graph containing every KCCK draw record and every legal
same-family move from one draw record to another. Preserve three types of draw
sink separately:

- terminal stalemate nodes;
- external KCK capture exits, labelled by captured Champion;
- internal draw nodes.

Run a deterministic strongly connected component decomposition on the
internal draw graph. A component is `cyclic` when it contains more than one
node or has a canonical self-edge; otherwise it is `acyclic-singleton`.
Classify nonlocal records by the draw-preserving sinks reachable from them:

- `stalemate-reachable`;
- `capture-a-reachable` and `capture-b-reachable`;
- `cycle-reachable`;
- combinations of those flags;
- `unexplained`, which must be zero after a correct traversal.

Do not call every cyclic component a fortress. A `detached-corner-cycle` is a
cyclic SCC whose defender king is detached in every member. A broader
`corner-associated-cycle` merely contains at least one such member. Components
that visit regular squares need separate witnesses before receiving a chess
interpretation.

The SCC implementation must be order-independent. Freeze the number of SCCs,
cyclic SCCs, member records, largest component size, and a stable digest over
sorted `(dense-index,component-minimum-index)` pairs. Re-run with reversed
successor iteration and require the same normalized result.

## Frozen witnesses

States are `attacker_king,champion_a,defender_king,champion_b,turn`:

Native regular squares are file-major: `file = square / 10` and
`rank = square % 10`; detached squares `100..103` are `w1..w4`. Any
human-readable witness name must be emitted by the generator's `square_name()`
helper from the numeric state. Do not hand-transcribe square names into result
artifacts.

| Mechanism | State/index | Required fact |
|---|---|---|
| terminal stalemate | `1,0,100,2,1`, index 1,081,893 | legal draw, no check, no move |
| capture A exit | `2,10,0,1,1`, index 3,369,745 | external KCK draw via A |
| capture B exit | `2,1,0,10,1`, index 3,204,927 | external KCK draw via B |
| noncapture detached-corner draw | `55,22,100,77,1`, index 25,533,851 | draw; neither Champion is adjacent to the defender |
| both-turn draw without immediate capture | `0,11,7,103,0/1`, indices 2,512/2,513 | both records draw; regular-square defender; no immediate capture |
| residual draw transition | `0,11,66,88,1`, index 451 | draw-preserving king move to index 560 |

The residual witness's opposite-turn record is historically illegal, and its
draw move reaches the paired draw at indices 560/561. It demonstrates why
`both-turn-draw` is not a complete explanation of all defender-turn draws; it
does not establish an SCC by itself.

## Acceptance gates

1. Read the exact frozen KCCK payload and require its payload, rules, capture
   policy, and complete-container hashes before analysis.
2. Reconstruct the 1,852,083 draw total and 93,247 both-turn-draw placement
   total independently from the payload.
3. Re-evaluate every local flag from legal geometry and successors; verify the
   frozen witnesses and exhaustive A/B swap invariance.
4. Prove the optional precedence counts sum to the draw total and publish the
   complete overlap matrix.
5. For SCC results, verify every internal edge stays on draw WDL, normalize
   component identities, pass reversed-iteration parity, and leave
   `unexplained = 0`.
6. Add deterministic counts/digests to the full-artifact regression only after
   all previous gates pass.

If SCC memory or verification cannot fit comfortably, stop after this
contract. Partial or sampled SCC counts must not be presented as exact.
