# Exact Omega WDL/DTZ design

## Rules and role normalization

Tables normalize by material role rather than colour. Three-man order is
strong-side king, role piece, bare king, side-to-move bit. KRKC order is
rook-side king, rook, Champion-side king, Champion, side-to-move bit. The
rules fingerprint covers the 104-square geometry, historical-legality test,
Champion movement, and current insufficient-material policy.

A stored placement is historically legal when the side that just moved is not
in check. The current side may be in check, so legal checkmate and stalemate
states remain representable. All D4-related placements share one dense index.

## Completed three-man dependency layer

`KRK` is solved by a conventional attractor retrograde:

1. Enumerate all 273,816 D4 slots and reserve `invalid` for illegal placements.
2. Initialize checkmates as losses and stalemates as draws.
3. Treat a bare king capturing an unprotected rook as an external K-v-K draw.
4. Generate quiet predecessors on demand rather than retaining a reverse graph.
5. A predecessor wins on its first move to a loss. It loses only after every
   successor class is a win. Unresolved cycles become draws.
6. Re-enumerate the family and verify every WDL Bellman equation.

`KCK` is all draw under the current `omega_insufficient_material()` rule. It is
still indexed and serialized so KRKC capture dependencies are explicit and a
future rules change cannot silently reuse incompatible data.

The three-man solver uses a one-byte status and one-byte unresolved-successor
counter per canonical slot. Predecessors are generated on demand, keeping the
working set small and making a full verified build practical in stock Python.

## Retained KRKC index

The 104-square board has 16 first-king D4 orbits. Ten have a trivial
stabilizer and six retain one reflection. A generic block contains
`103P3 = 1,061,106` labelled tails; a reflected block contains
`(103P3 + 11P3) / 2 = 531,048`. This yields:

| Population | States |
|---|---:|
| Raw placements with turn | 220,710,048 |
| D4-canonical placements with turn | 27,594,696 |
| Legal D4-canonical states | 22,607,206 |

The reported `Kw2/Rj6` versus `Ke5/Ch4` position is permanently fixed at dense
index `26,750,996`, protecting compatibility with the retained v1 design.

## Next dependency: KRKC

Within KRKC, quiet moves remain in-class. Captures leave the family:

- `R x C` probes the exact KRK table.
- `C x R` probes the rules-policy KCK table.

The four-man WDL pass can therefore reuse the same attractor algorithm and
on-demand predecessor strategy. It must not infer a blanket draw from the
material signature: concrete positions may already be mate or retain a forced
win.

After theoretical WDL, a separate DTZ pass must produce five-valued results
(loss, blessed loss, draw, cursed win, win). In pawnless tables captures are
the only zeroing moves; KRK without captures uses distance to mate. Runtime
probing must combine DTZ with the remaining `100 - halfmove_clock` budget.

## Production boundary not yet crossed

No search hook, UCI option, table loader, or tablebase score band is introduced
in this foundation. A later production integration needs a memory-mapped file
format, payload/rules verification, missing-table fallback, root DTZ ordering,
and multithreaded read-only probe tests.
