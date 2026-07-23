# OmegaDecisionSampler

`OmegaDecisionSampler` is a deterministic legality bridge for decision-aligned
Omega NNUE data. It reads JSONL roots containing `rootId`, `groupId`, and
`ofen`, initializes a fresh ChessLib Omega game for every candidate move, and
atomically publishes every legal child as coordinate move plus six-field OFEN.

The output order and child IDs are stable. The adjacent manifest pins the input,
output, sampler assembly, ChessLib assembly, field mapping, and coverage. Both
the output and manifest are no-clobber.

```powershell
dotnet run --project tools/omega_nnue/OmegaDecisionSampler -c Release -- `
  --input build-msvc/data-generation/omega-decision-v1/roots.jsonl `
  --output build-msvc/data-generation/omega-decision-v1/children.jsonl

dotnet run --project tools/omega_nnue/OmegaDecisionSampler -c Release -- --self-test
python -B tools/omega_nnue/omega_decision_teacher.py self-test
```

The Python pipeline then provides `prepare-roots`, `expand`, `run-shallow`,
`select`, `run-deep`, and `finalize`. It does not train an NNUE and does not
create labels until a `run-*` command is explicitly invoked.
