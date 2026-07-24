#!/usr/bin/env python3
"""Focused tests for the pinned Generation-6 evaluator adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock
import subprocess
import sys
import tempfile
import unittest

import numpy as np

try:
    from . import omega_decision_v3_evaluator_runner as runner
    from . import omega_nnue
except ImportError:
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    import omega_decision_v3_evaluator_runner as runner
    import omega_nnue


def _network(
    architecture: int = omega_nnue.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
) -> omega_nnue.QuantizedNetwork:
    rng = np.random.default_rng(2026072400)
    features = omega_nnue.feature_count_for_architecture(architecture)
    return omega_nnue.QuantizedNetwork(
        ft_bias=np.full(omega_nnue.ACCUMULATOR_SIZE, 12, dtype=np.int16),
        ft_weights=rng.integers(
            -2,
            3,
            size=(features, omega_nnue.ACCUMULATOR_SIZE),
            dtype=np.int16,
        ),
        dense_bias=np.full(omega_nnue.HIDDEN_SIZE, 128, dtype=np.int32),
        dense_weights=rng.integers(
            -2,
            3,
            size=(omega_nnue.HIDDEN_SIZE, omega_nnue.ACCUMULATOR_SIZE * 2),
            dtype=np.int8,
        ),
        output_bias=0,
        output_weights=rng.integers(
            -2, 3, size=omega_nnue.HIDDEN_SIZE, dtype=np.int8
        ),
        architecture=architecture,
    )


class EvaluatorRunnerTests(unittest.TestCase):
    def test_self_test_and_handcrafted_stream(self) -> None:
        runner._self_test()
        direct = runner._run_cpp(
            "--evaluate-handcrafted-stream", runner.HEALTH_OFENS[:2]
        )
        self.assertEqual(len(direct), 2)

    def test_network_stream_and_health_match_independent_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "candidate.nnue"
            model.write_bytes(_network().to_bytes())
            health = runner._health(model)
            self.assertTrue(health["finitePredictions"])
            self.assertTrue(health["quantizationRoundTripExact"])
            self.assertTrue(health["expectedNetworkBytes"])
            self.assertTrue(health["runtimeParity"])
            self.assertLessEqual(health["maximumAbsResidualCp"], 600)

    def test_isolated_cli_emits_exact_contract_shapes(self) -> None:
        script = Path(runner.__file__).resolve()
        python = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "candidate.nnue"
            model.write_bytes(_network().to_bytes())
            stream = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-B",
                    str(script),
                    "--evaluate-network-stream",
                    str(model),
                ],
                input="\n".join(runner.HEALTH_OFENS[:3]) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=120,
                env={},
            )
            self.assertEqual(stream.returncode, 0, stream.stderr)
            self.assertEqual(stream.stderr, "")
            self.assertEqual(len(stream.stdout.splitlines()), 3)
            self.assertTrue(all(line.lstrip("-").isdigit() for line in stream.stdout.splitlines()))

            health_run = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-B",
                    str(script),
                    "--validate-network-health",
                    str(model),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=120,
                env={},
            )
            self.assertEqual(health_run.returncode, 0, health_run.stderr)
            self.assertEqual(health_run.stderr, "")
            value = json.loads(health_run.stdout)
            self.assertEqual(
                set(value),
                {
                    "modelSha256",
                    "modelBytes",
                    "finitePredictions",
                    "quantizationRoundTripExact",
                    "expectedNetworkBytes",
                    "runtimeParity",
                    "maximumAbsResidualCp",
                },
            )

    def test_empty_or_noncanonical_stream_is_rejected(self) -> None:
        script = Path(runner.__file__).resolve()
        python = Path(sys.executable).resolve()
        for payload in ("", "  " + runner.HEALTH_OFENS[0] + "\n"):
            with self.subTest(payload=repr(payload)):
                completed = subprocess.run(
                    [
                        str(python),
                        "-I",
                        "-B",
                        str(script),
                        "--evaluate-handcrafted-stream",
                    ],
                    input=payload,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=30,
                    env={},
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "")
                self.assertNotEqual(completed.stderr, "")

    def test_health_requires_generation6_architecture_and_single_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong_architecture = root / "king-state-v3.nnue"
            wrong_architecture.write_bytes(
                _network(omega_nnue.ARCHITECTURE_KING_STATE_RESIDUAL).to_bytes()
            )
            health = runner._health(wrong_architecture)
            self.assertFalse(health["expectedNetworkBytes"])

            canonical = root / "candidate.nnue"
            alias = root / "candidate-hardlink.nnue"
            canonical.write_bytes(_network().to_bytes())
            os.link(canonical, alias)
            with self.assertRaisesRegex(ValueError, "unsafe evaluator artifact"):
                runner._health(alias)

    def test_health_rejects_model_substitution_at_cpp_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "candidate.nnue"
            model.write_bytes(_network().to_bytes())
            replacement = _network()
            replacement.ft_weights[0, 0] += np.int16(1)
            replacement_bytes = replacement.to_bytes()
            original = runner._run_cpp

            def substitute(*args, **kwargs):
                model.write_bytes(replacement_bytes)
                return original(*args, **kwargs)

            with mock.patch.object(runner, "_run_cpp", side_effect=substitute):
                with self.assertRaisesRegex(
                    ValueError, "network changed before evaluator execution"
                ):
                    runner._health(model)

    def test_isolated_runner_rejects_tampered_python_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "omega_decision_v3_evaluator_runner.py"
            dependency = root / "omega_nnue.py"
            script.write_bytes(Path(runner.__file__).read_bytes())
            dependency.write_bytes(
                Path(runner.omega_nnue.__file__).read_bytes() + b"\n# tampered\n"
            )
            completed = subprocess.run(
                [str(Path(sys.executable).resolve()), "-I", "-B", str(script), "--self-test"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
                env={},
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertIn("pinned omega_nnue.py identity changed", completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
