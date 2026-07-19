# King-state training and offline gate

The canonical workflow is intentionally split into validation and held-out
stages. `plan`, `run`, and `select` cannot decode split-2 targets. `test`
publishes a permanent one-time access claim before it decodes the first
held-out label.

Generation 1 used the sealed deep-HCE-v2 residual corpus. It is retained as
historical evidence, but its validation selection failed the original
optimizer-shadow health contract and it must not be promoted or reused as a
successful generation. Its entry point was:

```powershell
python .\tools\omega_nnue\king_state_train_amended.py plan
python .\tools\omega_nnue\king_state_train_amended.py run --candidate K0
python .\tools\omega_nnue\king_state_train_amended.py run --candidate K1
python .\tools\omega_nnue\king_state_train_amended.py run --candidate K2
python .\tools\omega_nnue\king_state_train_amended.py select
python .\tools\omega_nnue\king_state_train_amended.py run-robustness
python .\tools\omega_nnue\king_state_train_amended.py test
```

The original pre-label-sealed orchestrator remains byte-for-byte unchanged.
The wrapper verifies
`validation/omega-nnue-king-state-v1-amendment-001.json` and pins both itself
and the amendment in the training plan. It never changes `groupId`, split
routing, candidate recipes, thresholds, or one-time held-out access.

Generation 2 uses a fresh, orbit-disjoint deep-HCE-v3 teacher corpus and fresh
match suites, as required after the failed generation. Before any teacher
label is generated, prepare the fresh freeze and publish the v2 pre-label seal:

```powershell
python .\tools\omega_nnue\deep_hce_v2.py verify-freeze `
  --lock .\build-msvc\data-generation\deep-hce-v3\deep-hce-v2.freeze.json
python .\tools\omega_nnue\king_state_v2.py seal `
  --freeze .\build-msvc\data-generation\deep-hce-v3\deep-hce-v2.freeze.json `
  --output .\build-msvc\data-generation\deep-hce-v3\king-state-v1-prelabel.seal.json
python .\tools\omega_nnue\king_state_v2.py verify-seal `
  --seal .\build-msvc\data-generation\deep-hce-v3\king-state-v1-prelabel.seal.json
```

The compatibility seal filename is required by the existing teacher reader;
the seal body records protocol generation 2 and data profile deep-HCE-v3.
Generation 2 also corrects the float-checkpoint artifact contract without
changing any candidate recipe or numeric gate. It preserves the optimizer
shadow separately and publishes a deployment-equivalent float view that must
requantize to the exact exported NNUE bytes:

```powershell
python .\tools\omega_nnue\train_canonical.py --self-test
python .\tools\omega_nnue\king_state_train_generation2.py plan
python .\tools\omega_nnue\king_state_train_generation2.py run --candidate K0
python .\tools\omega_nnue\king_state_train_generation2.py run --candidate K1
python .\tools\omega_nnue\king_state_train_generation2.py run --candidate K2
python .\tools\omega_nnue\king_state_train_generation2.py select
python .\tools\omega_nnue\king_state_train_generation2.py run-robustness
python .\tools\omega_nnue\king_state_train_generation2.py test
```

`run --candidate all` is equivalent to the three candidate commands, in
K0/K1/K2 order. The generation-2 plan freezes the complete trainer argument
vectors and canonical artifact paths under `build-msvc/king-state-v2`.

Key artifacts:

- `training-plan.json`: identities and exact K0/K1/K2 commands.
- `K0.nnue`: exact architecture-2 to architecture-3 migration, with parity on
  every corpus OFEN and no optimization.
- `K1.nnue`, `K2.nnue`: primary-seed candidates trained on split 0 and selected
  by split 1 only.
- `validation-selection.seal.json`: validation winner and its immutable network
  hash.
- `<winner>-robustness.nnue`: the selected recipe rerun with the frozen
  robustness seed.
- `offline-test.json.access.json`: no-clobber claim written before test access.
- `offline-test.json`: one-time, phase-macro group-balanced offline result.

The offline result must pass independently against both K0 and zero residual.
Its paired bootstrap stratifies global `groupId` leakage components by their
exact set of observed phases, reuses one sampled multiplicity for every phase
cell belonging to a component, uses identical draws for candidate and
baselines, and reports the fifth percentile of 10,000
relative-loss-improvement replicates.

Run the adversarial leakage and migration checks without training real
candidates:

```powershell
python .\tools\omega_nnue\king_state_train_amended.py self-test
python .\tools\omega_nnue\king_state_v2.py self-test
python .\tools\omega_nnue\king_state_train_generation2.py self-test
```
