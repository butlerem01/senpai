# KWWK exact-WDL experiment contract

## Question and scope

Solve king plus two labelled Wizards versus a bare king under Senpai's current
Omega Chess rules. The official
[Omega Chess strategy page](https://www.omegachess.com/strategy) says two
Wizards cannot force mate. The experiment must test that claim rather than
encode it as a draw boundary, and must distinguish opposite-color from
same-color Wizard pairs.

The first output is diagnostic theoretical WDL only. It must not change the
production tablebase format, runtime material set, search, adjudication, or
evaluation. DTM or rule-aware DTZ remains a separate experiment.

## Frozen labelled state and ownership

Reuse the four-man order:

1. attacker king;
2. Wizard A;
3. defender king;
4. Wizard B;
5. side to move (`0 = attacker`, `1 = defender`).

Both Wizards belong to the attacker. They remain labelled while solving, so a
physical two-Wizard placement normally occurs under both A/B assignments. The
existing `D4-first-piece-v1` index still has 27,594,696 dense slots.

Historical legality and moves mirror the verified KCCK ownership model:

- all four squares are distinct and the kings are not adjacent;
- with the attacker to move, the defender just moved, so neither Wizard may
  attack the defender king;
- with the defender to move, the attacker just moved; the bare defender has no
  non-king attack on the attacker king;
- the attacker may move its king, Wizard A, or Wizard B;
- the defender may move only its king;
- a legal defender-king capture of either Wizard exits to KWK, an automatic
  draw under the current engine policy; the captured square must not be
  protected by the attacker king or the other Wizard.

The proposed material rules identity is:

```text
omega-104-v1;d4-first-piece-v1;historical-legality-v1;kwwk-theoretical-wdl;wizard-a-v1;wizard-b-v1;same-side-labels-v1;wizard-color-strata-v1;kwk-insufficient-material-v1
```

The proposed capture-policy identity is:

```text
either-wizard-capture-to-kwk-draw-v1
```

Freeze the resulting hashes only after the implementation and independent
fixture suite agree. Any ownership, movement, color, insufficient-material, or
historical-legality change requires a new fingerprint and artifact.

## Wizard colors and the D4 quotient

For a square with retained coordinate `(x,y)`, define Wizard color as
`(x + y) mod 2`. This includes the detached corners at `(-1,-1)`, `(10,-1)`,
`(10,10)`, and `(-1,10)`. Every Wizard delta preserves this bit.

Report at least three strata:

1. opposite-color Wizards;
2. both Wizards on color 0;
3. both Wizards on color 1.

There is an indexing subtlety: the full D4 quotient already identifies the
two same-color strata. With the retained transform numbering, transforms
`0,2,5,7` preserve square color and `1,3,4,6` flip it. A canonical same-color
record therefore represents raw placements in both color-0 and color-1
strata. It is incorrect to classify that record solely by the colors of its
canonical representative.

The summary pass must reconstruct **raw labelled** stratum counts by expanding
each canonical record through its distinct D4 orbit and accumulating each raw
placement once. It must then prove color-0 and color-1 legal/WDL counts equal.
The opposite-color stratum is D4-invariant. Also report labelled canonical
totals for comparison, but do not mix those units with the raw stratum totals.
Wizard A/B label-swap verification remains independent of this orbit
expansion.

## Frozen pre-solve fixtures

States use `attacker_king,wizard_a,defender_king,wizard_b,turn` and native
square numbers. Each dense index was independently ranked before solver work:

| Purpose | State | Dense index |
|---|---|---:|
| opposite-color mate | `1,0,100,3,1` | 1,081,895 |
| opposite-color stalemate | `1,3,100,4,1` | 1,123,105 |
| color-0 mate | `1,0,100,2,1` | 1,081,893 |
| color-0 stalemate | `1,4,100,6,1` | 1,143,713 |
| color-1 mate | `0,61,101,70,1` | 402,601 |
| color-1 stalemate | `1,3,100,5,1` | 1,123,107 |
| Wizard A capturable to KWK | `2,10,0,4,1` | 3,369,749 |
| Wizard B capturable to KWK | `2,4,0,10,1` | 3,246,135 |
| both-label quiet/predecessor fixture | `0,22,99,55,0` | 11,168 |
| illegal defender history | `0,22,33,55,0` | 10,506 |

The terminal fixtures prove only that mate positions exist in every color
stratum. They do not prove that an attacker can force those positions.

## Implementation sequence

1. Generalize the KCCK same-side-leaper path to a piece-kind parameter instead
   of cloning Champion-specific ownership logic.
2. Add KWWK parsing, labels, rules/capture fingerprints, independent legal
   population enumeration, and the frozen fixture tests.
3. Verify both Wizard labels have quiet successors and inverse predecessors;
   verify the defender captures each label to an external draw.
4. Run and freeze a deterministic 100,000-slot bounded solve, Bellman pass,
   serialization, inspection, probes, and payload hash without changing any
   legacy family hash.
5. Run the complete solve and a second Bellman-equation pass.
6. Exhaustively verify all eight D4 transforms and A/B label swap for legality
   and WDL.
7. Expand distinct D4 orbits to reconstruct raw opposite/color-0/color-1
   populations by turn and outcome; prove the two same-color strata equal.
8. Record aggregate and stratum hashes/counts plus terminal, immediate, deeper
   decisive, capture-escape, and fortress witnesses. If no decisive record
   exists in a stratum, say so explicitly instead of fabricating witnesses.

## Acceptance and interpretation

- Native Senpai, Python, and standalone C++ Wizard moves agree, including all
  detached-corner exits.
- Every legal record satisfies the full Bellman equation.
- Every D4 image and Wizard-label swap preserves legality and WDL.
- Raw stratum reconstruction sums to the independently enumerated raw legal
  population; color-0 and color-1 totals match exactly.
- Header counts are reconstructed from the payload and the complete-container
  checksum is frozen.
- The official claim is called confirmed, qualified, or contradicted only
  after the full solve. Existence of a composed mate is not a forced win, and
  a theoretical win does not establish conversion inside 100 plies.

The artifact remains diagnostic regardless of the result. Runtime inclusion
requires a separate stable-format decision, native role/color probes, and a
distance policy compatible with the automatic move-count draw.
