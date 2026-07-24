# OmegaHistoryRootSamplerG6

This project is the rules-only history-root producer for the Generation-6
Omega NNUE data path. It retains the deterministic SplitMix64 trajectories,
paired `ab`/`ba` provenance, phase windows, material floors, capture bias, and
selection-rank policy of `OmegaRootSampler`, but emits complete replayable
histories instead of position-only roots.

Every output JSONL row has exactly the source schema accepted by
`OmegaTerminalPreclassifierG6`:

```json
{
  "schemaVersion": 1,
  "kind": "omega-g6-history-root",
  "rootId": "random-pair-000001-ab-opening-w-ply-000006-...",
  "groupId": "random-pair-000001",
  "initialOfen": "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1",
  "moves": ["f1f2", "f8f7"],
  "plyOfenSha256": ["...", "..."]
}
```

`groupId` is the shared trajectory-pair identifier. `rootId` additionally
binds the independent trajectory flavor, sampled phase, side to move, ply,
and deterministic selection rank. `moves` is the complete lowercase
coordinate history from the canonical Omega start. Promotions always carry
one of the six Senpai-compatible suffixes `q`, `r`, `b`, `n`, `c`, or `w`.
Each hash is lowercase SHA-256 over ChessLib's normalized six-field OFEN
immediately after the corresponding ply.

Rows intentionally contain only replay authority, not a separately trusted
phase label. The later target-free routing producer must replay the row,
recompute phase from the resulting root OFEN's material count, and check the
human-readable phase embedded in `rootId`; it must never accept a phase merely
because it appears in the identifier. En-passant legality comes from ChessLib.
As in the original sampler, an en-passant move lands on an empty target and is
therefore drawn from the ordinary rather than occupied-target capture pool.

The sampler reads no game outcomes, teacher scores, candidate values, or
engine analysis. It never emits a root after checkmate, stalemate, repetition,
halfmove, or insufficient-material termination. The loaded ChessLib binary
must have SHA-256
`16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c`.

Production startup verifies the complete frozen execution closure before any
trajectory is generated. It hashes all 191 files named by the Generation-5
runtime manifest, checks that there are no extra runtime files or reparse
points, requires .NET `10.0.9`, and verifies these identities:

- runtime bundle: `0ce194480dfb9a58a59c79bf94f19cb2eb571635a00547094a9c8ff28bb5f8f8`
- runtime manifest: `c8543f22f4b353ee461f2e417c3d06ea2f05de2622fab49b789923f9944e00ee`
- `dotnet.exe`: `a5ccdc3a41d5e5c6014ff64509aed176db39f4f14caffff3dd1997f8907e94d7`
- `Newtonsoft.Json.dll`: `a28c251dfe36d881e9e2462e171441b8b0ec156fe3f452602c9149b1b9efe05b`
- `System.IO.Ports.dll`: `2767e21f384cca9004b1266ec4b71d3b8a76898594382c377c726c780aa34508`

The three application dependencies are copied on every build and must load
from beside the sampler assembly. Startup rejects .NET startup-hook,
additional-dependency, shared-store, alternate-root, roll-forward, and bundle
extraction environment overrides. The completion manifest records the exact
host, runtime manifest, core library, sampler, and dependency identities; a
production run verifies the closure again before publication.

Build with the pinned .NET 10 SDK and run the internal tests with the frozen
Generation-5 runtime host:

```powershell
& C:/Users/whate/.dotnet/dotnet.exe build `
  tools/omega_nnue/OmegaHistoryRootSamplerG6/OmegaHistoryRootSamplerG6.csproj `
  -c Release -o .build-g6-history-root-sampler

& tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/dotnet.exe `
  .build-g6-history-root-sampler/OmegaHistoryRootSamplerG6.dll --self-test
```

Generate a production pool:

```powershell
& tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/dotnet.exe `
  .build-g6-history-root-sampler/OmegaHistoryRootSamplerG6.dll `
  --output build-msvc/data-generation/g6/history-roots.jsonl `
  --seed 2026072201 `
  --trajectory-pairs 8192 `
  --max-plies 220 `
  --positions-per-phase-side 2 `
  --capture-percent 72 `
  --workers 4
```

The JSONL output is byte-identical for the same sampling options regardless
of worker count. Work is generated in bounded parallel batches and published
in pair order. The output and its adjacent `.manifest.json` are both
no-clobber. The output is moved first and the sealed manifest is moved last,
making the manifest the completion commit point; a failed publication rolls
back only artifacts created by that invocation. Output paths and their
existing parent chain may not traverse a symlink, junction, or reparse point;
hashed authority artifacts must also have exactly one hard link.
`--max-plies` must be at least 6, the first phase-window ply, and generation
aborts without publishing if it nevertheless produces zero roots.

The internal test uses worker layouts with different multi-batch boundaries,
golden SHA-256 vectors, known checkmate/stalemate/repetition/halfmove/material
terminals, mutated history hashes, both colors and all six promotions, exact
closure revalidation, publication-stage observation, injected rollback, and
output-only/manifest-only no-clobber cases.
