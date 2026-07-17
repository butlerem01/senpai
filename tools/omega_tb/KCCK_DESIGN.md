# KCCK exact-WDL experiment contract

## Question and scope

Solve king plus two labelled Champions versus a bare king under Senpai's
current Omega Chess rules. The experiment tests the published claim that two
Champions can mate with king support. It does not assume that claim as a graph
boundary, and D4-canonical record counts are not game-position probabilities
or centipawn values.

The first output is diagnostic theoretical WDL only. It does not change the
production tablebase format, the runtime material set, search, or evaluation.

## Frozen labelled state and ownership

The retained four-man state is ordered:

1. attacker king;
2. Champion A;
3. defender king;
4. Champion B;
5. side to move (`0 = attacker`, `1 = defender`).

Both Champions belong to the attacker. They stay labelled during generation,
so each physical two-Champion placement normally has two records related by
swapping A and B. The existing `D4-first-piece-v1` index canonicalizes on the
attacker king and retains the current 27,594,696 dense slots.

Historical legality means the side that just moved did not leave its own king
in check:

- kings may not overlap or be adjacent;
- with the attacker to move, the defender just moved, so neither Champion may
  attack the defender king;
- with the defender to move, the attacker just moved, and the bare defender
  has no non-king attack on the attacker king; king adjacency remains the only
  check.

On the attacker's turn, the attacker king and either labelled Champion may
move. On the defender's turn, only the defender king may move. Kings are never
captured. If the defender king legally captures either Champion, the boundary
is KCK, an automatic draw under the current engine policy. A capture is legal
only when the destination is not protected by the attacker king or the other
Champion.

The material rules identity is:

```text
omega-104-v1;d4-first-piece-v1;historical-legality-v1;kcck-theoretical-wdl;champion-a-v1;champion-b-v1;same-side-labels-v1;kck-insufficient-material-v1
```

The capture-policy identity is:

```text
either-champion-capture-to-kck-draw-v1
```

Any ownership, movement, insufficient-material, or historical-legality change
requires a new fingerprint and regenerated artifact.

## Frozen regression positions

States use `attacker_king,champion_a,defender_king,champion_b,turn` and native
square numbers:

- detached-corner mate: `1,0,100,11,1` (dense index 1,081,911);
- detached-corner stalemate: `1,0,100,2,1` (dense index 1,081,893);
- Champion A capturable to KCK: `2,10,0,1,1` (dense index 3,369,745);
- Champion B capturable to KCK: `2,1,0,10,1` (dense index 3,204,927);
- both-label quiet-move/predecessor fixture: `0,22,99,55,0`
  (dense index 11,168);
- illegal defender-history fixture: `0,22,24,55,0`, where Champion A attacks
  the defender king while the attacker is to move.

## Acceptance gates

1. Detached-corner Champion moves, attack symmetry, and every D4 transform
   agree with the retained independent geometry.
2. Both Champion labels have a verified legal quiet successor and the inverse
   predecessor edge.
3. The defender has separately verified legal captures of Champion A and
   Champion B, and both exits are draws rather than wins or losses.
4. Mate, stalemate, and illegal-history fixtures classify exactly as frozen.
5. Bounded-prefix solve, Bellman verification, serialization, inspection, and
   deterministic hash gates pass without changing any legacy family result.
6. The full solve independently freezes the legal population, resolves every
   legal record, and passes a second Bellman equation pass.
7. An exhaustive post-solve pass proves legality and WDL invariance under all
   eight D4 transforms and under swapping the two Champion labels.
8. Counts, hashes, side-to-move populations, label-paired populations, and
   terminal/immediate/deeper witnesses are recorded reproducibly.

Only after every full gate passes may the published mating claim be called
confirmed, qualified, or contradicted. The artifact remains outside production
regardless of the answer until a separate runtime decision and 100-ply-safe
distance policy exist. The accepted follow-up distance policy and companion
format are frozen separately in `KCCK_DTM_DESIGN.md`; they do not mutate this
WDL contract.
