# Validation

The immediate parent is `a24e4d7` (`codex/omega-kcck-dtm-runtime-v1`).
The candidate executable used below has SHA-256
`84042a909308ff033408b8856346388b67692e180a29c74b3f8f5b98b70f2504`.

## Completed gates

- All 23 C++ Release tests passed.
- The UCI node-limit and native-book protocol tests passed.
- The full KCCK DTM protocol gate passed with the production tables, including
  exact mate distances and PVs, analysis and ponder buffering, the 100-ply
  boundary, WDL-only fallback, and standard-chess isolation.
- A one-node, 16-position Champion component panel compared the candidate with
  its immediate parent. Singleton, equal-count, and promoted 3-versus-2
  controls changed by 0 cp. Only the 2-versus-1 cases changed, by +31 cp at
  phase 9, with the identical result after colour swapping.
- The 50,000-node Champion attack floor passed both hard probes, with no
  errors. It retained the reviewed two-Champion net and mating-landing moves.
- The local relative table path loaded all seven WDL families plus KCCK DTM,
  and the DTM-1 probe returned the exact `a5a4` mate.
- The packaged v2 book loaded 229 positions and selected a legal start move.

## Completed 16-game smoke

`omega-integrated-smoke-16.json` ran eight paired openings at 10,000 nodes per
move, one thread, books off, identical table files, and fresh processes. The
integrated candidate scored 8 wins, 3 draws, and 5 losses (59.38%).

There were no illegal moves, illegal PVs, protocol failures, or time forfeits.
Thirteen games ended by checkmate, one by stalemate, one by draw adjudication,
and one at the absolute ply cap. Every game reached unequal Champion counts,
so the screen exercised positions in which the pair feature could matter.

This is a health screen, not a strength proof. The candidate's 95% score
interval is 37.5% to 81.25%, so the +8/+40 coefficient remains experimental
until the independent 96-game confirmation completes.

The complete smoke artifacts are in the workspace at:

```text
match-runs/output/smoke-omega-integrated-pair-v1-16-20260718
```

Use `omega-integrated-confirm-96.json` for the predeclared out-of-sample
confirmation.
