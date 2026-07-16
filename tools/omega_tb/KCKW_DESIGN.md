# KCKW exact-WDL experiment contract

## Question

Solve king plus Champion versus king plus Wizard under Senpai's current Omega
Chess rules.  This is a geometry experiment: it asks which side can force a
win from each legal canonical state.  It is not, by itself, a centipawn-value
estimate or a frequency model for positions reached in games.

## Frozen rules

- Labelled order: Champion-side king, Champion, Wizard-side king, Wizard,
  side to move.
- Board and moves: the same 104-square geometry and exact Champion/Wizard
  jumps used by the native Omega implementation.
- Index: the existing `D4-first-piece-v1` four-man index, canonicalized on the
  Champion square.
- Historical legality: adjacent kings are illegal; when a side is to move,
  the position is illegal if that side's opponent could have just left its
  own king in check.
- Capturing either fairy piece leaves king plus one Champion or Wizard versus
  a bare king.  Both are automatic insufficient-material draws in the current
  engine, so every such boundary transition is a draw.
- The artifact is theoretical WDL only.  It contains neither DTM nor
  100-ply-aware DTZ.

## Acceptance gates

1. A material-specific rules and capture-policy fingerprint is embedded in
   the artifact and checked on every read.
2. Exact legal-state enumeration is frozen and independently rechecked.
3. Full retrograde solving resolves every legal state and a second full pass
   verifies every Bellman equation.
4. Geometry tests cover detached corners, attack symmetry, and all D4
   transforms for both leapers.
5. The summary reports outcomes by side to move, by material-side winner, and
   turn-paired decisive placements, with nonterminal witnesses.
6. Payload and container hashes, build command, run time, and outcome counts
   are recorded reproducibly.

Until all gates pass, no engine evaluation or production tablebase set is
changed.  Even after they pass, decisive WDL records remain diagnostic until
a draw-rule-safe DTZ policy exists.

## Completion status

All six offline gates passed for the source artifact documented in
`KCKW_THEORY.md`: the exact legal population is 23,651,215, the full Bellman
recheck passed, and the summary contains turn/material-side counts plus
terminal, nonterminal, and turn-independent witnesses for both material sides.
The verified source may now be converted through the strict `OMTBPROD` bridge
and loaded as the sixth member of the atomic runtime set. Runtime search
consumes only exact draw records; decisive W/L remains diagnostic pending a
100-ply-safe DTZ policy. Engine evaluation remains unchanged.
