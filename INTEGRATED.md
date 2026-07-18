# Senpai 2.0 Omega Integrated 1

This build is based on the latest exact-KCCK runtime. It combines the Omega
features that survived testing:

- native 10x10 plus four-corner Omega geometry and complete move legality;
- broad-development and coordinated king-attack evaluation;
- phase-tapered Omega piece values, mobility, pawn structure, safe landing
  squares, infiltration, rook coordination, and endgame scaling;
- optional native Omega opening-book support;
- exact draw probes for the produced Omega endgame tables; and
- exact, rule-safe shortest mates for K+2 Champions versus K when the KCCK
  DTM companion is configured.

It also contains one deliberately conservative new candidate: an 8/40
centipawn Champion-pair conversion reserve. Its component-isolation panel
confirms that it affects 2-versus-1 Champion material, remains neutral for
singletons and equal pair ownership, does not stack for a promoted third
Champion, and preserves colour symmetry. Until it passes an independent
held-out match, treat the exact coefficient as experimental rather than
settled Omega theory.

Experiments that failed their held-out gates were intentionally not merged.
The retained endgame values are Knight 235, Bishop 440, Rook 625, Champion
400, and Wizard 375. The opening book remains disabled by default because its
first full-game strength test was operationally sound but not stronger.

## CoreChess settings

Add `build-msvc/senpai-omega-integrated.exe` as a UCI engine and select:

```text
UCI_Variant        omega
Threads            1
Hash               128
Ponder             false
OwnBook            false
OmegaTablebasePath omega-tablebases
```

To experiment with the current book, set `OwnBook=true` and set
`OmegaBookFile` to the current v2 book. Keep it off for the tested default.

The local Release folder is prepared for CoreChess with these relative paths:

```text
OmegaTablebasePath omega-tablebases
OmegaBookFile      omega-opening-v2.obk
```

They resolve from `build-msvc`, the engine working directory. The table files
are NTFS hard links to the verified production assets, so they remain ordinary
files without consuming a second copy on this machine. In particular, keep
`omega-kcck-wdl-v1.omtb` and `omega-kcck-dtm-v1.omtb` together to enable the
exact two-Champion result and shortest-mate play. An absolute table path also
works.
