# Generation 5 runtime candidate

This directory begins as a byte-identical copy of the independently audited
Generation 4 runtime.  It is the separate runtime namespace for the
`king-state-v5-omega-decision-v2` experiment.  It does not become immutable
until the Generation 5 preregistration and final-freeze seal pin every
experiment-facing entry point.

- `engine/senpai.exe`: the HCE-only teacher when `UseOmegaNNUE=false`.
- `evaluator/omega_nnue.exe`: the independent static-HCE/NNUE stream evaluator.
- `omegamatch/`: the pinned one-ply source arena and its complete runtime bundle.
- `decision-sampler/`: the exhaustive legal-child sampler and runtime.
- `root-sampler/`: the rules-only random-trajectory sampler and runtime.

Do not rebuild or replace files in this directory after the Generation 5 final freeze.
Any implementation change requires a new experiment profile, namespace, and
fresh source corpus.
