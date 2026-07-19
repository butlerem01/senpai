# Omega NNUE experiment ledger

This ledger records promotion-grade results. A network that appears here must not
be retuned against its confirmation games, positions, principal variations, or
per-opening results.

## Residual v3 (`d81ba391cdac`)

- Date: 2026-07-18
- Candidate network SHA-256:
  `d81ba391cdacfd847b3e356a9ac439b9e3c5e0ace40b9dbbfd3d103fa197cbd1`
- Engine SHA-256:
  `24e30df0abc8e15d054193762a3e47aa415bddf585d0db929908f7934d4407d5`
- Control: the identical executable with `UseOmegaNNUE=false`
- Search: 50,000 nodes per move, one thread, 128 MB hash, fresh process per game
- Design: 64 candidate-blind roots, one AB/BA color-reversed pair per root,
  16 roots in each of opening, middlegame, late, and endgame phases
- Result: 49 wins, 19 draws, 60 losses; 45.703125% over 128 games
- Point estimate: -29.93 Elo
- Descriptive paired-bootstrap 95% interval: 39.84375% to 51.171875%
  (-71.57 to +8.14 Elo)
- Sequential promotion gate: `continue`
- Promotion e-value: 0.4664
- Futility e-value: 2.4709
- Promotion ready: false
- Safety: zero illegal moves, illegal PVs, protocol failures, and time forfeits
- Result artifact SHA-256:
  - `summary.json`: `414d22b0e4fc093741bc942543eb88a707dbfeaf38272f2b1543e1967475dff8`
  - `sequential-gate.json`: `b038d43517b8bb08118cb248dee125d3a56d7943d8eaf34f5481087fc5c7e540`
  - `manifest.json`: `7f92610514203e7a9a3b428678cde9c1a42940a518dacbd64660da06bcaf7cbb`

Conclusion: residual v3 is runtime-stable and competitive, but it is not
superior to the handcrafted evaluator. It is retired from promotion. Its
confirmation roots and their symmetry orbits are excluded from future training
and development suites.

The complete immutable run is stored under the ignored build artifact path:

`build-msvc/confirmation/residual-v3/confirmation-run`

## King-state v1 preregistration

- Declared: 2026-07-19
- Protocol SHA-256:
  `1dab498f8373e5b25d614172afb847362bf65384c644d6d585e131530816e1f9`
- Deep-HCE freeze SHA-256:
  `e0e859f2227c0e8d4b3f9b7c30b2143802bbe33541a90290073c1c5546f19262`
- Teacher suite SHA-256:
  `3fdee39e41c252b7caf516dccde70625f538de48ec4673b08972429f28df3dec`
- Static-HCE/parity helper SHA-256:
  `b6bdd2310901319d85464a1cb570557dfceaee0ebfefba0563c9564728c27c04`
- Pre-label seal SHA-256:
  `901e70b538d6348524c9b4a94493d4f57c81c826d1c0ca9c36ed13455613a2f4`
- Identities sealed: 37
- Teacher artifacts present at declaration: 0
- Frozen candidate roots: 8,704 (4,096 target A/B pairs plus 64 reserve
  pairs per phase)
- Teacher-label searches started before the seal: no

This hash is the external anchor for the ignored no-clobber seal at:

`build-msvc/data-generation/deep-hce-v2/king-state-v1-prelabel.seal.json`
