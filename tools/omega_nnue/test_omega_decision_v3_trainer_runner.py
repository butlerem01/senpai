#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np


HERE = Path(__file__).resolve().parent
RUNNER = HERE / "omega_decision_v3_trainer_runner.py"
SPEC = importlib.util.spec_from_file_location("omega_decision_v3_trainer_runner_tested", RUNNER)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


START = "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"


def canonical(value: object) -> bytes:
    return runner._canonical_json(value)


def root_documents(root_id: str = "root-0", *, split: str = "train") -> tuple[bytes, bytes]:
    labels = []
    hce = []
    scores = (100, 90, 80, 70)
    for index, score in enumerate(scores, 1):
        child_id = f"{root_id}-child-{index}"
        labels.append(
            {
                "schemaVersion": 1,
                "kind": runner.LABEL_KIND,
                "rootId": root_id,
                "leakageComponentId": f"component-{root_id}",
                "split": split,
                "childId": child_id,
                "childOfen": START,
                "phase": "opening",
                "parentSideToMove": "b",
                "deepRank": index,
                "deepRegretCp": 100 - score,
                "deepScoreCpRoot": score,
                "deepScoreCpChildStm": -score,
            }
        )
        hce.append(
            {
                "schemaVersion": 1,
                "kind": runner.HCE_KIND,
                "profileId": runner.PROFILE_ID,
                "childId": child_id,
                "handcraftedCpChildStm": -25 + index,
            }
        )
    return b"".join(map(canonical, labels)), b"".join(map(canonical, hce))


def one_objective_data() -> tuple[runner.TrainingData, tuple[runner.Child, ...]]:
    siblings = tuple(
        runner.Child(
            root_id="r",
            component_id="c",
            child_id=f"c{index}",
            ofen=START,
            phase="opening",
            parent_side="b",
            rank=index + 1,
            regret_cp=(0, 10, 40, 90)[index],
            root_score_cp=(100, 90, 60, 10)[index],
            child_score_cp=-(100, 90, 60, 10)[index],
            hce_cp=(-20, -10, 5, 30)[index],
            residual_target_cp=(-80.0, -80.0, -65.0, -40.0)[index],
            label_document={"child": f"c{index}"},
        )
        for index in range(4)
    )
    data = runner.TrainingData(
        roots=("r",),
        children_by_root={"r": siblings},
        stm_features=np.zeros((4, 1), dtype=np.int32),
        opponent_features=np.zeros((4, 1), dtype=np.int32),
        child_index={item.child_id: index for index, item in enumerate(siblings)},
        pad_feature=1,
    )
    return data, siblings


class FakeQuantized:
    def __init__(
        self,
        *,
        ft_bias: np.ndarray,
        ft_weights: np.ndarray,
        dense_bias: np.ndarray,
        dense_weights: np.ndarray,
        output_bias: int,
        output_weights: np.ndarray,
        architecture: int,
    ) -> None:
        self.ft_bias = ft_bias
        self.ft_weights = ft_weights
        self.dense_bias = dense_bias
        self.dense_weights = dense_weights
        self.output_bias = output_bias
        self.output_weights = output_weights
        self.architecture = architecture

    def to_bytes(self) -> bytes:
        return b"".join(
            (
                b"FAKEQ1",
                np.asarray(self.ft_bias, dtype="<i2").tobytes(),
                np.asarray(self.ft_weights, dtype="<i2").tobytes(),
                np.asarray(self.dense_bias, dtype="<i4").tobytes(),
                np.asarray(self.dense_weights, dtype="i1").tobytes(),
                int(self.output_bias).to_bytes(4, "little", signed=True),
                np.asarray(self.output_weights, dtype="i1").tobytes(),
            )
        )


FAKE_OMEGA = types.SimpleNamespace(
    ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL=4,
    OMEGA_INTERACTION_FEATURE_COUNT=6,
    OMEGA_INTERACTION_RESIDUAL_LIMIT_CP=600,
    ACCUMULATOR_SIZE=3,
    HIDDEN_SIZE=2,
    ACTIVATION_MAX=127,
    HIDDEN_DIVISOR=64,
    OUTPUT_DIVISOR=64,
    QuantizedNetwork=FakeQuantized,
)


def fake_model() -> runner.FloatNetwork:
    return runner.FloatNetwork(
        ft_bias=np.asarray([12.0, 11.0, 10.0], dtype=np.float32),
        ft_weights=np.asarray(
            [
                [0.1, 0.2, -0.1],
                [0.3, -0.1, 0.2],
                [-0.2, 0.1, 0.3],
                [0.4, 0.2, -0.2],
                [0.1, -0.3, 0.2],
                [-0.2, 0.2, 0.1],
            ],
            dtype=np.float32,
        ),
        dense_bias=np.asarray([3.0, 4.0], dtype=np.float32),
        dense_weights=np.full((2, 6), 0.02, dtype=np.float32),
        output_bias=np.asarray([0.5], dtype=np.float32),
        output_weights=np.asarray([0.2, -0.1], dtype=np.float32),
        omega=FAKE_OMEGA,
    )


def tiny_training_data(root_count: int = 4) -> tuple[runner.TrainingData, dict[str, dict[str, object]]]:
    children_by_root: dict[str, tuple[runner.Child, ...]] = {}
    ordered: list[runner.Child] = []
    hce_documents: dict[str, dict[str, object]] = {}
    for root_number in range(root_count):
        root_id = f"r{root_number:02d}"
        siblings = []
        for index in range(4):
            child_id = f"{root_id}-c{index}"
            score = 100 - 15 * index
            hce = -20 + 3 * index
            label = {
                "schemaVersion": 1,
                "kind": runner.LABEL_KIND,
                "rootId": root_id,
                "leakageComponentId": f"component-{root_id}",
                "split": "train",
                "childId": child_id,
                "childOfen": START,
                "phase": "opening",
                "parentSideToMove": "b",
                "deepRank": index + 1,
                "deepRegretCp": 15 * index,
                "deepScoreCpRoot": score,
                "deepScoreCpChildStm": -score,
            }
            hce_document = {
                "schemaVersion": 1,
                "kind": runner.HCE_KIND,
                "profileId": runner.PROFILE_ID,
                "childId": child_id,
                "handcraftedCpChildStm": hce,
            }
            child = runner.Child(
                root_id=root_id,
                component_id=f"component-{root_id}",
                child_id=child_id,
                ofen=START,
                phase="opening",
                parent_side="b",
                rank=index + 1,
                regret_cp=15 * index,
                root_score_cp=score,
                child_score_cp=-score,
                hce_cp=hce,
                residual_target_cp=float(-score - hce),
                label_document=label,
            )
            siblings.append(child)
            ordered.append(child)
            hce_documents[child_id] = hce_document
        children_by_root[root_id] = tuple(siblings)
    stm = np.asarray([[index % 5, 6] for index in range(len(ordered))], dtype=np.int32)
    opponent = np.asarray([[(index + 1) % 5, 6] for index in range(len(ordered))], dtype=np.int32)
    return runner.TrainingData(
        roots=tuple(sorted(children_by_root)),
        children_by_root=children_by_root,
        stm_features=stm,
        opponent_features=opponent,
        child_index={child.child_id: index for index, child in enumerate(ordered)},
        pad_feature=6,
    ), hce_documents


class TrainerRunnerTests(unittest.TestCase):
    def test_exact_pinned_omega_nnue_loads(self) -> None:
        omega, identity, _ = runner._load_pinned_omega_nnue()
        self.assertEqual(identity["bytes"], runner.OMEGA_NNUE_BYTES)
        self.assertEqual(identity["sha256"], runner.OMEGA_NNUE_SHA256)
        self.assertEqual(omega.OMEGA_INTERACTION_FEATURE_COUNT, 48440)
        self.assertEqual(omega.OMEGA_INTERACTION_RESIDUAL_LIMIT_CP, 600)

    def test_train_only_parser_and_crosslinks(self) -> None:
        omega, _, _ = runner._load_pinned_omega_nnue()
        labels, hce = root_documents()
        data, documents = runner._parse_training_data(labels, hce, omega, expected_roots=1)
        self.assertEqual(data.roots, ("root-0",))
        self.assertEqual(len(data.children_by_root["root-0"]), 4)
        self.assertEqual(set(documents), set(data.child_index))
        altered = labels.replace(b'"split":"train"', b'"split":"validation"', 1)
        with self.assertRaisesRegex(ValueError, "refuses validation"):
            runner._parse_training_data(altered, hce, omega, expected_roots=1)

    def test_parser_rejects_rank_regret_disagreement(self) -> None:
        omega, _, _ = runner._load_pinned_omega_nnue()
        labels, hce = root_documents()
        documents = [json.loads(line) for line in labels.splitlines()]
        documents[1]["deepRank"] = 3
        documents[2]["deepRank"] = 2
        altered = b"".join(canonical(document) for document in documents)
        with self.assertRaisesRegex(ValueError, "rank/regret authority"):
            runner._parse_training_data(altered, hce, omega, expected_roots=1)
        altered = labels.replace(b'"split":"train"', b'"split":"heldOut"', 1)
        with self.assertRaisesRegex(ValueError, "refuses validation"):
            runner._parse_training_data(altered, hce, omega, expected_roots=1)

    def test_objective_gradient_matches_finite_difference(self) -> None:
        data, _ = one_objective_data()
        prediction = np.asarray([-70.0, -60.0, -30.0, 10.0], dtype=np.float32)
        loss, gradient = runner._batch_objective(prediction, ("r",), data, runner.CANDIDATE_RECIPES["G6B"])
        self.assertTrue(np.isfinite(loss))
        epsilon = 0.01
        for index in range(4):
            plus = prediction.copy()
            minus = prediction.copy()
            plus[index] += epsilon
            minus[index] -= epsilon
            plus_loss, _ = runner._batch_objective(plus, ("r",), data, runner.CANDIDATE_RECIPES["G6B"])
            minus_loss, _ = runner._batch_objective(minus, ("r",), data, runner.CANDIDATE_RECIPES["G6B"])
            numeric = (plus_loss - minus_loss) / (2.0 * epsilon)
            self.assertAlmostEqual(float(gradient[index]), numeric, places=5)

    def test_dense_and_feature_backprop_match_finite_difference(self) -> None:
        model = fake_model()
        stm = np.asarray([[0, 1, 6], [2, 6, 6]], dtype=np.int32)
        opponent = np.asarray([[3, 6, 6], [4, 5, 6]], dtype=np.int32)
        upstream = np.asarray([0.25, -0.4], dtype=np.float32)
        prediction, cache = model.forward(stm, opponent, qat=False, need_cache=True)
        assert cache is not None
        gradients = runner._backprop(model, stm, opponent, cache, upstream, 6)

        def scalar() -> float:
            value, _ = model.forward(stm, opponent, qat=False, need_cache=False)
            return float(np.dot(value.astype(np.float64), upstream.astype(np.float64)))

        epsilon = 1e-3
        probes = (("output_bias", (0,)), ("output_weights", (0,)), ("dense_bias", (0,)), ("dense_weights", (0, 0)), ("ft_bias", (0,)), ("ft_weights", (0, 0)))
        for name, index in probes:
            parameter = model.parameters()[name]
            original = float(parameter[index])
            parameter[index] = original + epsilon
            plus = scalar()
            parameter[index] = original - epsilon
            minus = scalar()
            parameter[index] = original
            numeric = (plus - minus) / (2.0 * epsilon)
            self.assertAlmostEqual(float(gradients[name][index]), numeric, delta=2e-3)

    def test_qat_forward_matches_quantized_runtime(self) -> None:
        omega, _, _ = runner._load_pinned_omega_nnue()
        features = omega.OMEGA_INTERACTION_FEATURE_COUNT
        quantized = omega.QuantizedNetwork(
            ft_bias=np.full(omega.ACCUMULATOR_SIZE, 12, dtype=np.int16),
            ft_weights=np.zeros((features, omega.ACCUMULATOR_SIZE), dtype=np.int16),
            dense_bias=np.full(omega.HIDDEN_SIZE, 64 * 5, dtype=np.int32),
            dense_weights=np.zeros((omega.HIDDEN_SIZE, omega.ACCUMULATOR_SIZE * 2), dtype=np.int8),
            output_bias=64 * 17,
            output_weights=np.zeros(omega.HIDDEN_SIZE, dtype=np.int8),
            architecture=omega.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
        )
        model = runner.FloatNetwork.from_quantized(quantized, omega)
        white = omega.active_features(START, 0, omega.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL)
        black = omega.active_features(START, 1, omega.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL)
        width = max(len(white), len(black))
        stm = np.full((1, width), features, dtype=np.int32)
        opponent = np.full((1, width), features, dtype=np.int32)
        stm[0, : len(white)] = white
        opponent[0, : len(black)] = black
        actual, _ = model.forward(stm, opponent, qat=True, need_cache=False)
        expected = quantized.predict_features(stm, opponent)
        np.testing.assert_array_equal(actual.astype(np.int32), expected)

        saturated = omega.QuantizedNetwork(
            ft_bias=quantized.ft_bias.copy(),
            ft_weights=quantized.ft_weights.copy(),
            dense_bias=quantized.dense_bias.copy(),
            dense_weights=quantized.dense_weights.copy(),
            output_bias=64 * 1000,
            output_weights=quantized.output_weights.copy(),
            architecture=omega.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
        )
        saturated_model = runner.FloatNetwork.from_quantized(saturated, omega)
        saturated_actual, cache = saturated_model.forward(
            stm, opponent, qat=True, need_cache=True
        )
        np.testing.assert_array_equal(
            saturated_actual.astype(np.int32), saturated.predict_features(stm, opponent)
        )
        self.assertEqual(int(saturated_actual[0]), 600)
        assert cache is not None
        gradients = runner._backprop(
            saturated_model,
            stm,
            opponent,
            cache,
            np.ones(1, dtype=np.float32),
            features,
        )
        self.assertEqual(float(gradients["output_bias"][0]), 0.0)

    def test_tiny_corpus_can_overfit(self) -> None:
        data, _ = tiny_training_data()
        model = fake_model()
        optimizer = runner.Adam(model.parameters())
        indices = np.arange(len(data.child_index), dtype=np.int64)
        stm = data.stm_features[indices]
        opponent = data.opponent_features[indices]

        def objective() -> float:
            prediction, _ = model.forward(stm, opponent, qat=False, need_cache=False)
            loss, _ = runner._batch_objective(
                prediction,
                data.roots,
                data,
                runner.CANDIDATE_RECIPES["G6C"],
            )
            return loss

        initial = objective()
        for _ in range(100):
            prediction, cache = model.forward(
                stm, opponent, qat=False, need_cache=True
            )
            assert cache is not None
            _, output_gradient = runner._batch_objective(
                prediction,
                data.roots,
                data,
                runner.CANDIDATE_RECIPES["G6C"],
            )
            gradients = runner._backprop(
                model, stm, opponent, cache, output_gradient, data.pad_feature
            )
            optimizer.step(gradients, 0.003, FAKE_OMEGA)
        self.assertLess(objective(), initial - 0.05)

    def test_training_is_deterministic_and_transcript_is_post_step(self) -> None:
        data, hce_documents = tiny_training_data()
        old = runner.OPTIMIZER_PROTOCOL
        runner.OPTIMIZER_PROTOCOL = {
            **dict(old),
            "epochs": 2,
            "qatEpochs": 1,
            "firstQatEpoch": 2,
            "rootsPerBatch": 2,
        }
        try:
            with tempfile.TemporaryDirectory() as directory:
                transcript = Path(directory) / "actual.jsonl"
                first_model, first_history, rows = runner._train(
                    model_id="G6A",
                    candidate_id="G6A",
                    purpose="primary-training",
                    seed=123,
                    recipe=runner.CANDIDATE_RECIPES["G6A"],
                    data=data,
                    hce_documents=hce_documents,
                    model=fake_model(),
                    transcript_path=transcript,
                )
                first_transcript = transcript.read_bytes()
                history_document = json.loads(first_history)
                self.assertEqual(
                    history_document["batchOrderSha256"],
                    hashlib.sha256(first_transcript).hexdigest(),
                )
                self.assertEqual(
                    history_document["actualBatchOrderTranscript"],
                    {
                        "path": str(transcript.resolve()),
                        "bytes": len(first_transcript),
                        "sha256": hashlib.sha256(first_transcript).hexdigest(),
                    },
                )
                with self.assertRaises(FileExistsError):
                    runner._train(
                        model_id="G6A",
                        candidate_id="G6A",
                        purpose="primary-training",
                        seed=123,
                        recipe=runner.CANDIDATE_RECIPES["G6A"],
                        data=data,
                        hce_documents=hce_documents,
                        model=fake_model(),
                        transcript_path=transcript,
                    )
                transcript.unlink()
                second_model, second_history, _ = runner._train(
                    model_id="G6A",
                    candidate_id="G6A",
                    purpose="primary-training",
                    seed=123,
                    recipe=runner.CANDIDATE_RECIPES["G6A"],
                    data=data,
                    hce_documents=hce_documents,
                    model=fake_model(),
                    transcript_path=transcript,
                )
                self.assertEqual(first_model, second_model)
                self.assertEqual(first_history, second_history)
                self.assertEqual(first_transcript, transcript.read_bytes())
                decoded = [json.loads(line) for line in first_transcript.splitlines()]
                self.assertEqual([row["optimizerStep"] for row in decoded], [1, 2, 3, 4])
                self.assertEqual(len(rows), 2)
                self.assertFalse(any(row["validationRootsDecoded"] for row in rows))
        finally:
            runner.OPTIMIZER_PROTOCOL = old

    def test_exclusive_publication_refuses_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / "artifact.bin"
            identity = runner._write_exclusive(target, b"first")
            self.assertEqual(identity["sha256"], __import__("hashlib").sha256(b"first").hexdigest())
            with self.assertRaises(FileExistsError):
                runner._write_exclusive(target, b"second")
            self.assertEqual(target.read_bytes(), b"first")

    def test_cli_rejects_noncanonical_mode_before_writes(self) -> None:
        with self.assertRaisesRegex(ValueError, "argument contract"):
            runner.run(["--other"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
