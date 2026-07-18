# KCCK exact distance-to-mate contract

## Question

The exact KCCK WDL table proves that king plus two Champions can force mate
from most legal records. This companion experiment asks:

1. how many plies mate takes when the attacker minimizes and the defender
   maximizes the distance;
2. which theoretical wins survive Omega Chess's automatic 100-ply
   no-progress rule;
3. which exact positions and optimal continuations can seed a human-readable
   two-Champion mating atlas.

The source solve remains an offline proof artifact. Its accepted result now
has a separate checked production companion and rule-safe runtime/search
integration; the immutable WDL container itself remains unchanged.

## Frozen source

The source must be a complete `OMTB4WDL` KCCK table with:

- 27,594,696 dense `D4-first-piece-v1` slots;
- payload SHA-256
  `35f9d8bcb3b283dec2f918fcc4c5d62355edc2dee3ee297e16dd42a91940678e`;
- KCCK rules SHA-256
  `ac44f4152d0c619468addc65c580c064a1e8c5bc6c31509f50243739e8f4431f`;
- capture-policy SHA-256
  `286a4e80294c4118093c8223b3414859c3a8f634ae401ee49cecb4b636c7172b`.

The labelled state remains
`attacker king, Champion A, defender king, Champion B, turn`. Draw and invalid
records have no distance.

## Metric

Distance is measured in plies from the current position to checkmate:

- a terminal checkmate loss has `DTM = 0`;
- a winning record has
  `DTM = 1 + min(DTM of losing same-family children)`;
- a nonterminal losing record has
  `DTM = 1 + max(DTM of winning same-family children)`.

Capturing either Champion exits to drawn KCK, so no decisive KCCK record has a
win-preserving zeroing capture. There are no pawns. Consequently DTZ adds no
information for this material: DTM itself measures the complete no-progress
interval to mate.

For a nonterminal position with halfmove clock `h < 100`, a theoretical win
is rule-safe exactly when:

```text
DTM <= 100 - h
```

Equality wins because Senpai gives checkmate precedence when the clock reaches
100. Native draw and repetition adjudication retain first claim at runtime;
this diagnostic table contains no repetition-history dimension.

## Companion format

`OMTB4DTM` version 1 is separate from the immutable WDL table. It stores one
little-endian unsigned 32-bit value per dense slot. `UINT32_MAX` means that
the WDL source is draw or invalid. The JSON header binds:

- index, square count, labelled order, completeness, and distance semantics;
- source WDL payload, rules, and capture-policy SHA-256 values;
- decisive and rule-budget counts, maximum DTM, encoding, and payload hash.

The accepted source artifact remains 32-bit. Production narrows it through an
exhaustive checked conversion to the explicit `OMTBDTM1` uint16 companion;
`65535` is the draw/invalid sentinel. That companion binds both the accepted
source DTM payload/container and the exact production WDL payload. It is not
stored or described as DTZ.

## Acceptance gates

1. Regenerate the exact WDL result and pass its complete Bellman verifier.
2. Seed only terminal checkmates at DTM zero.
3. Resolve all 21,786,787 decisive WDL records exactly once; leave all
   1,852,083 draws and 3,955,826 invalid records at the sentinel.
4. Re-evaluate every DTM Bellman equation from successors.
5. Require odd DTM on attacker-to-move wins and even DTM on
   defender-to-move losses.
6. Exhaustively preserve DTM under all eight D4 transforms and swapping the
   two labelled Champions.
7. Serialize, read, hash, and bind the companion to the exact source WDL.
8. Freeze the full histogram, median, p90, p95, p99, maximum witnesses, and
   counts safe at halfmove clocks 0, 20, 40, 60, 80, and 99.
9. Extract optimal continuations in a retained raw orientation and require
   DTM to fall by exactly one on every ply. Never infer moves by subtracting
   independently canonicalized states.
10. Compare curated and random KCCK legality, checks, captures, and successors
    with Senpai's native move generator before calling the table engine-ready.

The version-one sampled native gate now passes on a frozen 17,128-root corpus,
including labelled A/B captures and detached-corner cases. The corpus SHA-256
is `300f9a25f2b6592b0322e79382acd7a9e5d819a7dc8ca8d2c44598b216471729`.
The independent expected-record stream is frozen separately at
`2c1cf4bf03837b43ac58b1c5acd1fe5e15029eaa974d490e08f1f6f5e44b92ab`.
This satisfies the sampled native/independent milestone; direct three-way
comparison with the solver graph or exhaustive native parity remains a
stronger pre-production gate.

## Human-study outputs

The mating atlas should distinguish interior, regular edge, regular corner,
and detached-corner defending kings. Representative lines should cover fast,
median, percentile, and maximum-distance wins, positions near the move-count
boundary, only-move wins, and each exact draw mechanism. Position-population
counts are properties of the indexed table, not estimates of practical game
frequency or material value.
