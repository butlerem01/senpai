# King-state-v1 training and offline gate

The canonical workflow is intentionally split into validation and held-out
stages. `plan`, `run`, and `select` cannot decode split-2 targets. `test`
publishes a permanent one-time access claim before it decodes the first
held-out label.

Run these commands from the `senpai-omega-nnue` repository after the sealed
deep-HCE-v2 residual corpus has been finalized:

```powershell
python .\tools\omega_nnue\king_state_train.py plan
python .\tools\omega_nnue\king_state_train.py run --candidate K0
python .\tools\omega_nnue\king_state_train.py run --candidate K1
python .\tools\omega_nnue\king_state_train.py run --candidate K2
python .\tools\omega_nnue\king_state_train.py select
python .\tools\omega_nnue\king_state_train.py run-robustness
python .\tools\omega_nnue\king_state_train.py test
```

`run --candidate all` is equivalent to the three candidate commands, in
K0/K1/K2 order. The plan freezes the complete trainer argument vectors and
canonical artifact paths under `build-msvc/king-state-v1`.

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
Its paired bootstrap samples unique `groupId` clusters within each frozen phase,
uses the same draws for candidate and baselines, and reports the fifth
percentile of 10,000 relative-loss-improvement replicates.

Run the adversarial leakage and migration checks without training real
candidates:

```powershell
python .\tools\omega_nnue\king_state_train.py self-test
```
