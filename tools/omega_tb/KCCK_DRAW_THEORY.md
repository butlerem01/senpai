# Exact KCCK draw classification

## Scope and reproducibility

This is an offline diagnostic of the frozen two-Champion-versus-bare-king
KCCK theoretical-WDL table. It changes neither Senpai nor tablebase runtime
probing. The unit called a **record** below is one D4-canonical spatial
placement, with Champion A/B labels retained, plus side to move. A
**both-turn placement** is a labelled placement whose two historically legal
side-to-move records are both drawn.

The analyzer independently regenerates every legal move, every immediate
capture exit, and every predecessor. Its exact inputs and deterministic
outputs are:

| Identity | SHA-256 |
|---|---|
| Frozen WDL payload | `35f9d8bcb3b283dec2f918fcc4c5d62355edc2dee3ee297e16dd42a91940678e` |
| Frozen WDL container | `bff047fab9141666ae13f80db17ae2d5d5a808bdd115b131180e529bf493a244` |
| KCCK rules | `ac44f4152d0c619468addc65c580c064a1e8c5bc6c31509f50243739e8f4431f` |
| KCK capture policy | `286a4e80294c4118093c8223b3414859c3a8f634ae401ee49cecb4b636c7172b` |
| Sorted draw-edge stream | `a27f5d0e1b3bc3f28420996ad8fceefdf567eb99a3150aa9ff7bd18efe50e724` |
| Normalized SCC assignment | `2dd537502d53b53f4598d95fdc134a67a632953951a530f1f2bb71849ab55a4e` |
| Complete analysis JSON | `0bacbc4be3af566d891bfb45541d6558fb356446075c8d557c01d535f54d9a05` |

Run the complete contract gate from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  tools/omega_tb/test-kcck-draw-analysis.ps1 `
  -KcckWdlPath .build-omega-tb/omega-kcck-wdl-v1.omtb4
```

Or build and emit the JSON directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  tools/omega_tb/build-kcck-draw-analysis.ps1
.\.build-omega-tb\kcck_draw_analysis.exe `
  --wdl .build-omega-tb/omega-kcck-wdl-v1.omtb4 `
  --output .build-omega-tb/omega-kcck-draw-analysis-v1.json
```

The full pass reconstructed 1,852,083 draws and 2,173,842 internal
draw-preserving edges. The successor and independently generated predecessor
edge sets matched exactly. A second SCC pass with reversed root and edge
iteration produced the same normalized assignment, and an exhaustive
Champion A/B swap produced the same classification with A/B bits exchanged.

## Local mechanisms

The 1,852,083 draw records split by side to move into 93,247 attacker records
and 1,758,836 defender records. The local flags are intentionally
nonexclusive:

| Local fact | Draw records |
|---|---:|
| Terminal stalemate | 75,388 |
| Defender can capture Champion A immediately | 735,290 |
| Defender can capture Champion B immediately | 735,290 |
| Defender can capture either labelled Champion immediately | 1,450,317 |
| Both captures are legal | 20,263 |
| Defender king is on a detached corner | 84,773 |
| Opposite-turn record is also drawn | 186,494 |
| Defender is in check | 898,234 |

The opposite-turn flag reconstructs exactly 93,247 both-turn placements. The
A/B counts are exactly symmetric.

Using the contract's presentation precedence gives this disjoint overview:

| First applicable mechanism | Draw records |
|---|---:|
| Terminal stalemate | 75,388 |
| Immediate capture | 1,450,317 |
| Detached corner | 11,611 |
| Both-turn draw | 129,664 |
| Residual | 185,103 |

The authoritative overlap matrix uses bits `S=1`, `A=2`, `B=4`, `D=8`,
`T=16`, and `I=32` for stalemate, capture A, capture B, detached corner,
both-turn draw, and in-check:

| Flags | Records | Flags | Records |
|---|---:|---|---:|
| none | 102,228 | S | 11,698 |
| A | 295,993 | B | 295,993 |
| S+D | 61,443 | T | 129,664 |
| S+T | 108 | A+T | 16,750 |
| B+T | 16,750 | D+T | 11,611 |
| S+D+T | 2,139 | A+D+T | 4,736 |
| B+D+T | 4,736 | I | 82,875 |
| A+I | 397,494 | B+I | 397,494 |
| A+B+I | 20,263 | A+D+I | 54 |
| B+D+I | 54 |  |  |

## Internal cycles are not detached-corner fortresses

The draw-only directed graph has:

| SCC fact | Exact result |
|---|---:|
| Components | 1,640,577 |
| Acyclic singleton components | 1,638,495 |
| Cyclic components | 2,082 |
| Records in cyclic components | 213,588 |
| Largest cyclic component | 101,164 |
| Detached-corner cyclic components | 0 |
| Corner-associated cyclic components | 0 |
| Canonical self-edges | 0 |

Thus cycles exist, including one large regular-board network, but no cyclic
SCC contains even one position with the defender on a detached corner. It
would be incorrect to describe these SCCs as detached-corner fortresses.

Ordinary reverse reachability is existential: it says that some
draw-preserving continuation reaches a terminal target or a cyclic SCC, not
that the defender can force it. Its complete mask census uses `S=1`, `A=2`,
`B=4`, and `C=8` for stalemate, capture A, capture B, and a cyclic SCC:

| Reachable target combination | Records |
|---|---:|
| S | 77,032 |
| A | 687,131 |
| S+A | 5,185 |
| B | 687,131 |
| S+B | 5,185 |
| A+B | 23,015 |
| S+A+B | 328 |
| A+C | 16,640 |
| B+C | 16,640 |
| A+B+C | 7,744 |
| S+A+B+C | 326,052 |
| Unexplained | 0 |

## Defender-controlled terminal attractor

The stronger calculation is an alternating reachability game restricted to
draw-preserving play. At a defender-to-move record, one attracted successor
is enough; at an attacker-to-move record, every draw successor must already
be attracted. Immediate KCK capture exits and terminal stalemates are target
sinks.

| What the defender can force in finitely many moves | Draw records |
|---|---:|
| Stalemate specifically | 77,032 |
| Capture of Champion A specifically | 866,526 |
| Capture of Champion B specifically | 866,526 |
| Capture of either Champion | 1,712,789 |
| Capture or stalemate | **1,852,083** |
| Can avoid every terminal sink indefinitely | **0** |

The terminal-union result covers every draw record. Consequently, under this
frozen game model there is no closed, terminal-avoiding draw dominion: the
defender can force a finite capture-or-stalemate exit despite any
draw-preserving attacker choices. The SCC cycles are therefore optional
repetition routes, not compulsory fortresses. This does **not** mean every
cycle is strategically useless or that the attacker can force the exit.

The attractor classes are:

| Forceability mask | Records |
|---|---:|
| Terminal union only | 62,262 |
| Stalemate and terminal union | 77,032 |
| Capture A, either capture, and terminal union | 846,263 |
| Capture B, either capture, and terminal union | 846,263 |
| Either labelled capture, either capture, and terminal union | 20,263 |

The 62,262 union-only records are important: the defender can force *some*
terminal draw, but the attacker can prevent the defender from preselecting
stalemate, capture A, or capture B as the endpoint.

## Robust both-turn placements

All 93,247 placements drawn with either side to move lie in the terminal
attractor from both turn records. Their local defender-turn mechanisms are:

| Placement class | Placements |
|---|---:|
| Immediate capture of Champion A | 21,486 |
| Immediate capture of Champion B | 21,486 |
| Immediate capture of either Champion | 42,972 |
| Terminal stalemate | 2,247 |
| Neither immediate capture nor stalemate | 48,028 |
| Defender on a regular square | 81,636 |
| Defender on a detached corner | 11,611 |

Among the detached-corner placements, 2,139 are stalemates, 4,736 permit
capture A, and 4,736 permit capture B. None supplies a cyclic SCC.

The complete JSON also freezes the local, existential-reachability, and
attractor class of both turn records for every robust placement. Its
attractor pair cross-tab is:

| Attacker-turn class | Defender-turn class | Placements |
|---|---|---:|
| Terminal union only | Terminal union only | 414 |
| Terminal union only | Force stalemate | 894 |
| Force stalemate | Force stalemate | 1,353 |
| Terminal union only | Force capture A | 13,560 |
| Force capture A | Force capture A | 31,733 |
| Terminal union only | Force capture B | 13,560 |
| Force capture B | Force capture B | 31,733 |

## Limits on the conclusion

- The analysis is exact for D4-canonical, Champion-labelled records. It does
  not quote raw-orientation population counts or quotient Champion A/B labels.
- Capture exits inherit the frozen policy that KCK is an automatic draw.
  Changing that rule changes the graph and requires new artifacts.
- SCC and ordinary reachability alone are not game-theoretic fortress proofs.
  Only the alternating attractor accounts for opposition between attacker and
  defender choices.
- The attractor proves finite eventual reachability, but this version stores
  no minimum or maximum exit distance. It also does not model the 100-ply
  clock, DTZ, repetition claims, or practical search errors.
- This is a diagnostic of already-drawn theoretical-WDL records. It does not
  alter the exact mate results for decisive KCCK records, engine evaluation,
  search, or adjudication.
