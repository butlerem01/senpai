# Frozen Generation 4 runtime

This directory is the immutable runtime bundle used by the
`king-state-v4-omega-decision-v1` experiment.  The completed preregistration
and final-freeze seal pin the byte length and SHA-256 identity of every
experiment-facing entry point.

- `engine/senpai.exe`: the HCE-only teacher when `UseOmegaNNUE=false`.
- `evaluator/omega_nnue.exe`: the independent static-HCE/NNUE stream evaluator.
- `omegamatch/`: the pinned one-ply source arena and its complete runtime bundle.
- `decision-sampler/`: the exhaustive legal-child sampler and runtime.
- `root-sampler/`: the rules-only random-trajectory sampler and runtime.

Do not rebuild or replace files in this directory after the final freeze.
Any implementation change requires a new experiment profile, namespace, and
fresh source corpus.
