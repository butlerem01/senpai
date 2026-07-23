# OmegaTerminalPreclassifierG6

This is the rules-only, history-aware gate in front of any Generation-6 UCI
teacher. It is deliberately bound to the frozen Generation-5 ChessLib binary
(`16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c`).
The program refuses to run if a different ChessLib assembly is loaded.

Each input JSONL record has exactly these fields:

```json
{
  "schemaVersion": 1,
  "kind": "omega-g6-history-root",
  "rootId": "root-0001",
  "groupId": "game-0001",
  "initialOfen": "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1",
  "moves": ["f1f2", "f8f7"],
  "plyOfenSha256": ["...", "..."]
}
```

`moves` is the complete coordinate history from `initialOfen` to the proposed
root. `plyOfenSha256[n]` is SHA-256 over ChessLib's normalized six-field OFEN
after move `moves[n]`. The gate replays with `DoMove(checkEndGame: false)` and
checks every hash. It also requires ChessLib's canonical recorded coordinate
to equal the input after case normalization. In particular, promotions must
carry an explicit `q`, `r`, `b`, `n`, `c`, or `w` suffix; ChessLib's implicit
queen default is rejected because it is not valid Senpai UCI history. This
gate also mirrors Senpai's playable-OFEN boundary: exactly one king of each
color is required, and pawns are forbidden on W1-W4 and ranks 0/9. This
check, including nonnegative halfmove and positive fullmove counters, runs
after initialization and after every history/child transition. This preserves
repetition state instead of reconstructing a position from its final OFEN
alone, while preventing ChessLib-only placements or counter overflows from
reaching the teacher. The manifest seals these constraints as structured
policy fields, including the exact three validation points, so downstream
jobs can verify the compatibility contract rather than infer it from prose.

For a verified root, every legal child is replayed from the same verified
history and classified in ChessLib's actual order: checkmate, stalemate, then
draw. Draws are split in ChessLib's own sub-order into repetition, halfmove,
and insufficient-material classes. The closed output classification set is:

- `root-nonterminal`
- `child-nonterminal`
- `checkmate`
- `stalemate`
- `draw-repetition`
- `draw-halfmove`
- `draw-insufficient`
- `illegal`
- `replay-failure`

A root appears in `--eligible-roots` and its children appear in
`--eligible-children` only after the root and **all** legal children have been
proved nonterminal. A mate, stalemate, draw, illegal move, or replay mismatch
rejects the entire root before any UCI process is involved. `--transcript`
retains the deterministic evidence and a domain-separated per-root transcript
hash for both accepted and rejected roots. No timestamps or filesystem paths
enter the transcript or its hash.

The self-test locks precedence with overlapping terminal conditions
(mate/halfmove, stalemate/halfmove, repetition/halfmove, and
halfmove/insufficient), checks a late per-ply hash mismatch without counting
the failed ply as verified, and confirms that replay never mutates
`Game.Ended` because `checkEndGame` is disabled.

Build and run the focused behavioral tests:

```powershell
& $HOME/.dotnet/dotnet.exe build `
  tools/omega_nnue/OmegaTerminalPreclassifierG6/OmegaTerminalPreclassifierG6.csproj `
  -c Release -o .build-g6-terminal-preclassifier

& tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/dotnet.exe `
  .build-g6-terminal-preclassifier/OmegaTerminalPreclassifierG6.dll --self-test
```

Run the gate:

```powershell
& tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/dotnet.exe `
  .build-g6-terminal-preclassifier/OmegaTerminalPreclassifierG6.dll `
  --input histories.jsonl `
  --transcript terminal-transcript.jsonl `
  --eligible-roots eligible-roots.jsonl `
  --eligible-children eligible-children.jsonl `
  --manifest terminal-preclassification.manifest.json
```

The build requires a .NET 10 SDK; the checked command selects the user-local
10.0.301 SDK instead of the older machine-wide SDK. All four outputs are
no-clobber. They are staged beside their final paths and
the manifest is moved last as the publication commit point.
