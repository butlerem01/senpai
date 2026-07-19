#!/usr/bin/env python3
"""Run the sealed king-state-v1 NNUE training and offline gates.

The workflow has a hard information boundary:

* ``plan`` freezes exact K0/K1/K2 trainer argument vectors from the sealed
  protocol without decoding any target.
* ``run`` executes only those frozen vectors.
* ``select`` may decode split 0/1 labels, never split 2.  It atomically seals
  the selected network hash after validation and feature-only runtime gates.
* ``run-robustness`` executes the selected recipe with the preregistered
  robustness seed.
* ``test`` first publishes a no-clobber access claim, then decodes split 2
  exactly once and applies the preregistered offline gates.

All target-bearing aggregation is paired by row and group.  Offline Huber and
MAE are averaged first within ``(phase, groupId)``, then equally across groups
within each phase, then equally across the four frozen phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

import train as trainer
from omega_nnue import (
    ACTIVATION_MAX,
    ARCHITECTURE_KING_STATE_RESIDUAL,
    ARCHITECTURE_RESIDUAL,
    HEADER_BYTES,
    KING_STATE_FEATURE_COUNT,
    QuantizedNetwork,
    active_features,
    deterministic_split,
    nnue_input_signature,
    payload_bytes_for_architecture,
)


SCHEMA_VERSION = 1
PLAN_KIND = "omega-nnue-king-state-v1-training-plan"
SELECTION_KIND = "omega-nnue-king-state-v1-validation-selection"
TEST_ACCESS_KIND = "omega-nnue-king-state-v1-test-access-claim"
TEST_REPORT_KIND = "omega-nnue-king-state-v1-offline-test"
PROTOCOL_KIND = "omega-nnue-king-state-v1-preregistration"
PRELABEL_SEAL_KIND = "omega-nnue-king-state-v1-prelabel-seal"
PHASES = ("opening", "middlegame", "late", "endgame")
SHA256_LENGTH = 64
STREAM_MODE = "--evaluate-network-stream"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.resolve(strict=True).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"identity path is not a file: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            str(Path(str(left["path"])).resolve())
            == str(Path(str(right["path"])).resolve())
            and int(left["bytes"]) == int(right["bytes"])
            and str(left["sha256"]).lower()
            == str(right["sha256"]).lower()
        )
    except (KeyError, TypeError, ValueError):
        return False


def _verify_identity(pin: Mapping[str, Any], label: str) -> Path:
    try:
        path = Path(str(pin["path"])).resolve(strict=True)
    except (KeyError, OSError) as error:
        raise ValueError(f"{label}: invalid pinned path") from error
    actual = _identity(path)
    if not _same_identity(actual, pin):
        raise ValueError(f"{label} changed after it was pinned")
    return path


def _load_json(path: Path, label: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON: {resolved}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object: {resolved}")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _expect_number(actual: Any, expected: float, label: str) -> None:
    if (
        isinstance(actual, bool)
        or not isinstance(actual, (int, float))
        or not math.isfinite(float(actual))
        or not math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise ValueError(f"{label}: expected {expected}, got {actual!r}")


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, value: Any, *, no_clobber: bool) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(value)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if no_clobber:
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise ValueError(f"refusing to overwrite sealed file: {target}") from error
            os.unlink(temporary)
        else:
            os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _validate_protocol(protocol: Mapping[str, Any]) -> dict[str, Any]:
    _expect(protocol.get("schemaVersion"), 1, "protocol.schemaVersion")
    _expect(protocol.get("kind"), PROTOCOL_KIND, "protocol.kind")
    _expect(
        protocol.get("generationId"),
        "king-state-v1-deep-hce-v2",
        "protocol.generationId",
    )
    split = _mapping(protocol.get("dataSplit"), "protocol.dataSplit")
    for key, expected in (
        ("groupField", "groupId"),
        ("splitSeed", 4989),
        ("trainPercent", 80),
        ("validationPercent", 10),
        ("testPercent", 10),
        ("collisionPolicy", "error"),
        ("requiredPhaseBalanceAudit", True),
        ("requiredSideToMoveBalanceAudit", True),
    ):
        _expect(split.get(key), expected, f"protocol.dataSplit.{key}")
    # These tolerances are part of the declaration, not post-label choices.
    _expect_number(
        split.get("maximumRelativeDeviationFromEqualPhaseCount"),
        0.15,
        "protocol.dataSplit.maximumRelativeDeviationFromEqualPhaseCount",
    )
    _expect_number(
        split.get("maximumAbsoluteSideToMoveImbalanceFraction"),
        0.10,
        "protocol.dataSplit.maximumAbsoluteSideToMoveImbalanceFraction",
    )

    seeds = _mapping(protocol.get("modelSeeds"), "protocol.modelSeeds")
    _expect(seeds.get("primary"), 20260725, "protocol.modelSeeds.primary")
    _expect(seeds.get("robustness"), 20260726, "protocol.modelSeeds.robustness")

    candidates = _sequence(protocol.get("candidates"), "protocol.candidates")
    by_id = {
        str(_mapping(value, "protocol candidate").get("id")): _mapping(
            value, "protocol candidate"
        )
        for value in candidates
    }
    _expect(set(by_id), {"K0", "K1", "K2"}, "protocol candidate ids")
    _expect(by_id["K0"].get("train"), False, "protocol K0.train")
    for candidate_id, ft_scale in (("K1", 0.5), ("K2", 1.0)):
        candidate = by_id[candidate_id]
        _expect(candidate.get("train"), True, f"protocol {candidate_id}.train")
        _expect_number(
            candidate.get("learningRate"),
            0.003,
            f"protocol {candidate_id}.learningRate",
        )
        _expect_number(
            candidate.get("featureTransformerLearningRateScale"),
            ft_scale,
            f"protocol {candidate_id}.featureTransformerLearningRateScale",
        )

    common = _mapping(protocol.get("commonTraining"), "protocol.commonTraining")
    expected_common = {
        "networkSemantics": "king-state-residual",
        "epochs": 24,
        "qatEpochs": 4,
        "batchSize": 256,
        "denseWeightLearningRateScale": 0.01,
        "denseBiasLearningRateScale": 0.1,
        "outputLearningRateScale": 0.25,
        "qatLearningRateScale": 0.1,
        "cpWeight": 1.0,
        "outcomeWeight": 0.0,
        "targetCpClip": 2000,
        "stratifiedBatches": True,
        "strict": True,
        "cppParity": True,
    }
    for key, expected in expected_common.items():
        if isinstance(expected, float):
            _expect_number(common.get(key), expected, f"protocol.commonTraining.{key}")
        else:
            _expect(common.get(key), expected, f"protocol.commonTraining.{key}")

    selection = _mapping(
        protocol.get("validationSelection"), "protocol.validationSelection"
    )
    for key, expected in (
        ("minimumRelativeHuberImprovementOverK0", 0.005),
        ("mustLowerCpMae", True),
        ("maximumPhaseMaeRegressionCp", 5),
        ("requireAllHealthChecks", True),
        ("winner", "lowest quantized validation Huber loss"),
        ("tieRelativeLoss", 0.0025),
        ("tieWinner", "K1"),
        ("ifNoneEligible", "fail generation without consulting test"),
    ):
        if isinstance(expected, float):
            _expect_number(selection.get(key), expected, f"selection.{key}")
        else:
            _expect(selection.get(key), expected, f"selection.{key}")
    _expect(
        selection.get("tieFormula"),
        "abs(K1Loss-K2Loss)/min(K1Loss,K2Loss) <= tieRelativeLoss",
        "selection.tieFormula",
    )
    _expect(
        selection.get("tieRequiresFinitePositiveLosses"),
        True,
        "selection.tieRequiresFinitePositiveLosses",
    )

    offline = _mapping(protocol.get("offlineTestGate"), "protocol.offlineTestGate")
    target_and_loss = _mapping(
        offline.get("targetAndLoss"), "offlineTestGate.targetAndLoss"
    )
    _expect(
        target_and_loss.get("targetClipCpInclusive"),
        [-2000, 2000],
        "offlineTestGate.targetAndLoss.targetClipCpInclusive",
    )
    _expect_number(
        target_and_loss.get("normalizerCp"),
        100.0,
        "offlineTestGate.targetAndLoss.normalizerCp",
    )
    _expect_number(
        target_and_loss.get("huberDeltaNormalized"),
        2.0,
        "offlineTestGate.targetAndLoss.huberDeltaNormalized",
    )
    aggregation = _mapping(
        offline.get("aggregation"), "offlineTestGate.aggregation"
    )
    _expect(
        aggregation.get("requiredFrozenPhases"),
        list(PHASES),
        "offlineTestGate.aggregation.requiredFrozenPhases",
    )
    _expect(
        aggregation.get("groupPhasePurityRequired"),
        True,
        "offlineTestGate.aggregation.groupPhasePurityRequired",
    )
    _expect(
        aggregation.get("rowToGroup"),
        "arithmetic mean of rows for each (phase,groupId)",
        "offlineTestGate.aggregation.rowToGroup",
    )
    _expect(
        aggregation.get("groupToPhase"),
        "arithmetic mean of group means within each frozen phase",
        "offlineTestGate.aggregation.groupToPhase",
    )
    _expect(
        aggregation.get("phaseToMetric"),
        "equal arithmetic mean of the four phase means",
        "offlineTestGate.aggregation.phaseToMetric",
    )
    bootstrap = _mapping(offline.get("bootstrap"), "offlineTestGate.bootstrap")
    for key, expected in (
        ("unit", "unique groupId within frozen phase"),
        ("phaseStratified", True),
        ("paired", True),
        ("iterations", 10000),
        ("oneSidedConfidence", 0.95),
        ("seed", 20260727),
        ("rng", "NumPy Generator PCG64"),
        (
            "lowerBound",
            "5th percentile via numpy.quantile(method='linear')",
        ),
        ("requiredLowerBoundExclusive", 0),
    ):
        if isinstance(expected, float):
            _expect_number(bootstrap.get(key), expected, f"bootstrap.{key}")
        else:
            _expect(bootstrap.get(key), expected, f"bootstrap.{key}")
    baselines = _mapping(
        offline.get("baselines"), "offlineTestGate.baselines"
    )
    _expect(set(baselines), {"K0", "zeroResidual"}, "offline baseline ids")
    _expect(
        offline.get("comparisonPolicy"),
        (
            "all point-estimate and bootstrap requirements must pass "
            "separately against each baseline"
        ),
        "offlineTestGate.comparisonPolicy",
    )
    _expect_number(
        offline.get("minimumRelativeLossImprovementAgainstEach"),
        0.01,
        "offlineTestGate.minimumRelativeLossImprovementAgainstEach",
    )
    _expect_number(
        offline.get("minimumMaeImprovementCpAgainstEach"),
        2.0,
        "offlineTestGate.minimumMaeImprovementCpAgainstEach",
    )
    _expect(
        offline.get("requirePositiveFifthPercentileAgainstEach"),
        True,
        "offlineTestGate.requirePositiveFifthPercentileAgainstEach",
    )
    robustness = _mapping(
        offline.get("robustness"), "offlineTestGate.robustness"
    )
    for key, expected in (
        ("mustImproveDirectionallyAgainstK0", True),
        ("mustImproveDirectionallyAgainstZeroResidual", True),
        ("mayReplacePrimary", False),
    ):
        _expect(robustness.get(key), expected, f"offline robustness.{key}")
    _expect(
        offline.get("fallbackAfterTestFailure"),
        False,
        "offlineTestGate.fallbackAfterTestFailure",
    )
    return {
        "split": split,
        "seeds": seeds,
        "candidates": by_id,
        "common": common,
        "selection": selection,
        "offline": offline,
        "targetAndLoss": target_and_loss,
        "aggregation": aggregation,
        "bootstrap": bootstrap,
        "runtime": _mapping(
            protocol.get("quantizationAndRuntimeGate"),
            "protocol.quantizationAndRuntimeGate",
        ),
    }


def _verify_prelabel_context(
    *,
    protocol_path: Path,
    seal_path: Path,
    initializer_path: Path,
    cpp_evaluator_path: Path,
    run_canonical_verifier: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol_pin = _identity(protocol_path)
    seal_pin = _identity(seal_path)
    initializer_pin = _identity(initializer_path)
    evaluator_pin = _identity(cpp_evaluator_path)
    protocol = _load_json(protocol_path, "protocol")
    parsed = _validate_protocol(protocol)
    seal = _load_json(seal_path, "pre-label seal")
    _expect(seal.get("schemaVersion"), 1, "pre-label seal schema")
    _expect(seal.get("kind"), PRELABEL_SEAL_KIND, "pre-label seal kind")
    identities = _mapping(seal.get("identities"), "pre-label seal identities")
    for name, expected in (
        ("protocol", protocol_pin),
        ("residualV3Initializer", initializer_pin),
        ("staticHceEvaluator", evaluator_pin),
    ):
        actual = _mapping(identities.get(name), f"seal.identities.{name}")
        if not _same_identity(actual, expected):
            raise ValueError(f"pre-label seal does not pin {name}")
        _verify_identity(actual, f"sealed {name}")
    if run_canonical_verifier:
        verifier = Path(__file__).with_name("king_state_v1.py").resolve(strict=True)
        completed = subprocess.run(
            [
                sys.executable,
                str(verifier),
                "verify-seal",
                "--seal",
                str(seal_path.resolve(strict=True)),
            ],
            cwd=_repo_root(),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise ValueError(
                "canonical pre-label seal verification failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
    context = {
        "protocol": protocol_pin,
        "prelabelSeal": seal_pin,
        "initializer": initializer_pin,
        "staticHceEvaluator": evaluator_pin,
    }
    return protocol, parsed, context


def _find_output_manifest(output: Path) -> Path:
    candidates = (
        Path(str(output) + ".manifest.json"),
        output.with_suffix(".manifest.json"),
        output.with_name(output.stem + ".manifest.json"),
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                manifest = _load_json(candidate, "source manifest")
            except ValueError:
                continue
            pin = manifest.get("output")
            if isinstance(pin, dict) and _same_identity(pin, _identity(output)):
                return candidate.resolve()
    raise ValueError(f"could not find an output-pinning manifest for {output}")


def _verify_corpus_chain(
    *,
    corpus_path: Path,
    corpus_manifest_path: Path,
    seal_pin: Mapping[str, Any],
    evaluator_pin: Mapping[str, Any],
) -> dict[str, Any]:
    corpus_pin = _identity(corpus_path)
    manifest_pin = _identity(corpus_manifest_path)
    manifest = _load_json(corpus_manifest_path, "residual corpus manifest")
    seal = _load_json(Path(str(seal_pin["path"])), "pre-label seal")
    sealed_tooling = _mapping(
        _mapping(seal.get("identities"), "seal identities").get("tooling"),
        "sealed tooling",
    )
    _expect(
        manifest.get("targetSemantics"),
        "search-minus-handcrafted",
        "residual target semantics",
    )
    _expect(manifest.get("networkSemantics"), "residual", "residual network semantics")
    if not _same_identity(
        _mapping(manifest.get("output"), "residual manifest output"), corpus_pin
    ):
        raise ValueError("residual corpus manifest does not pin the corpus")
    if not _same_identity(
        _mapping(manifest.get("tool"), "residual builder identity"),
        _mapping(
            sealed_tooling.get("buildResidualTargets"),
            "sealed residual builder",
        ),
    ):
        raise ValueError("residual corpus was built by an unsealed tool")
    search_pin = _mapping(manifest.get("searchTeacher"), "search teacher identity")
    hce_pin = _mapping(manifest.get("handcraftedLabels"), "HCE label identity")
    search_path = _verify_identity(search_pin, "search teacher")
    hce_path = _verify_identity(hce_pin, "HCE labels")
    search_manifest = _load_json(
        _find_output_manifest(search_path), "search-teacher manifest"
    )
    if not _same_identity(
        _mapping(search_manifest.get("prelabelSeal"), "search pre-label seal"),
        seal_pin,
    ):
        raise ValueError("search-teacher manifest does not pin this pre-label seal")
    if not _same_identity(
        _mapping(
            search_manifest.get("staticHceEvaluator"),
            "search static-HCE evaluator",
        ),
        evaluator_pin,
    ):
        raise ValueError("search-teacher manifest does not pin the static HCE helper")
    hce_manifest = _load_json(_find_output_manifest(hce_path), "HCE label manifest")
    if not _same_identity(
        _mapping(hce_manifest.get("evaluator"), "HCE evaluator identity"),
        evaluator_pin,
    ):
        raise ValueError("HCE label manifest used a different evaluator")
    if not _same_identity(
        _mapping(hce_manifest.get("tool"), "HCE label tool identity"),
        _mapping(sealed_tooling.get("labelHce"), "sealed HCE label tool"),
    ):
        raise ValueError("HCE labels were built by an unsealed tool")
    hce_input = _mapping(hce_manifest.get("input"), "HCE label input")
    if (
        str(Path(str(hce_input.get("path", ""))).resolve())
        != str(search_path.resolve())
        or int(hce_input.get("snapshotBytes", -1)) != int(search_pin["bytes"])
        or str(hce_input.get("snapshotSha256", "")).lower()
        != str(search_pin["sha256"]).lower()
    ):
        raise ValueError("HCE labels do not cover the exact search corpus")
    return {
        "corpus": corpus_pin,
        "corpusManifest": manifest_pin,
        "searchTeacher": search_pin,
        "searchTeacherManifest": _identity(_find_output_manifest(search_path)),
        "handcraftedLabels": hce_pin,
        "handcraftedManifest": _identity(_find_output_manifest(hce_path)),
    }


@dataclass(frozen=True)
class FeatureCorpus:
    ofens: tuple[str, ...]
    groups: tuple[str, ...]
    phases: tuple[str, ...]
    input_signatures: tuple[str, ...]
    splits: np.ndarray
    white_features: np.ndarray
    black_features: np.ndarray
    side_to_move_white: np.ndarray

    @property
    def count(self) -> int:
        return len(self.ofens)

    def perspective(self, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        white = self.white_features[indices]
        black = self.black_features[indices]
        stm_white = self.side_to_move_white[indices, None]
        return np.where(stm_white, white, black), np.where(stm_white, black, white)


def _feature_corpus(
    path: Path,
    *,
    split_seed: int,
    train_percent: float,
    validation_percent: float,
) -> FeatureCorpus:
    ofens: list[str] = []
    groups: list[str] = []
    phases: list[str] = []
    splits: list[int] = []
    signatures: list[str] = []
    white_rows: list[tuple[int, ...]] = []
    black_rows: list[tuple[int, ...]] = []
    stm_rows: list[bool] = []
    group_phases: dict[str, str] = {}
    resolved = path.resolve(strict=True)
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{resolved}:{line_number}"
            # Each call only decodes the requested top-level string.  Target
            # values are skipped lexically and remain opaque.
            group = trainer._selective_top_level_string(
                line, "groupId", location=location
            )
            ofen = " ".join(
                trainer._selective_top_level_string(
                    line, "ofen", location=location
                ).split()
            )
            phase = trainer._selective_top_level_string(
                line, "phase", location=location
            )
            if phase not in PHASES:
                raise ValueError(f"{location}: invalid phase {phase!r}")
            previous = group_phases.setdefault(group, phase)
            if previous != phase:
                raise ValueError(
                    f"{location}: groupId {group!r} spans phases "
                    f"{previous!r} and {phase!r}"
                )
            try:
                white = active_features(
                    ofen, 0, ARCHITECTURE_KING_STATE_RESIDUAL
                )
                black = active_features(
                    ofen, 1, ARCHITECTURE_KING_STATE_RESIDUAL
                )
            except ValueError as error:
                raise ValueError(f"{location}: invalid Omega OFEN: {error}") from error
            side = ofen.split()[1]
            if side not in ("w", "b"):
                raise ValueError(f"{location}: invalid side to move")
            ofens.append(ofen)
            groups.append(group)
            phases.append(phase)
            splits.append(
                deterministic_split(
                    group, split_seed, train_percent, validation_percent
                )
            )
            signatures.append(
                nnue_input_signature(
                    white, black, side == "w", ARCHITECTURE_KING_STATE_RESIDUAL
                )
            )
            white_rows.append(white)
            black_rows.append(black)
            stm_rows.append(side == "w")
    if not ofens:
        raise ValueError("corpus contains no positions")
    width = max(max(map(len, white_rows)), max(map(len, black_rows)))
    white_array = np.full(
        (len(ofens), width), KING_STATE_FEATURE_COUNT, dtype=np.uint16
    )
    black_array = np.full_like(white_array, KING_STATE_FEATURE_COUNT)
    for index, (white, black) in enumerate(zip(white_rows, black_rows)):
        white_array[index, : len(white)] = white
        black_array[index, : len(black)] = black
    return FeatureCorpus(
        ofens=tuple(ofens),
        groups=tuple(groups),
        phases=tuple(phases),
        input_signatures=tuple(signatures),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=white_array,
        black_features=black_array,
        side_to_move_white=np.asarray(stm_rows, dtype=np.bool_),
    )


def _balance_audit(
    corpus: FeatureCorpus, split: int, protocol_split: Mapping[str, Any]
) -> dict[str, Any]:
    indices = np.flatnonzero(corpus.splits == split)
    if indices.size == 0:
        raise ValueError(f"split {split} contains no rows")
    phase_counts = {
        phase: sum(corpus.phases[index] == phase for index in indices)
        for phase in PHASES
    }
    if any(value == 0 for value in phase_counts.values()):
        raise ValueError(f"split {split} is missing a phase: {phase_counts}")
    expected = indices.size / len(PHASES)
    phase_deviation = max(
        abs(value - expected) / expected for value in phase_counts.values()
    )
    white = int(corpus.side_to_move_white[indices].sum())
    black = int(indices.size - white)
    stm_imbalance = abs(white - black) / indices.size
    phase_limit = float(
        protocol_split["maximumRelativeDeviationFromEqualPhaseCount"]
    )
    stm_limit = float(
        protocol_split["maximumAbsoluteSideToMoveImbalanceFraction"]
    )
    if phase_deviation > phase_limit:
        raise ValueError(
            f"split {split} phase imbalance {phase_deviation:.6f} exceeds "
            f"{phase_limit:.6f}"
        )
    if stm_imbalance > stm_limit:
        raise ValueError(
            f"split {split} side-to-move imbalance {stm_imbalance:.6f} "
            f"exceeds {stm_limit:.6f}"
        )
    return {
        "rows": int(indices.size),
        "phaseCounts": phase_counts,
        "maximumPhaseRelativeDeviationFromEqual": phase_deviation,
        "phaseLimit": phase_limit,
        "sideToMove": {"white": white, "black": black},
        "sideToMoveImbalanceFraction": stm_imbalance,
        "sideToMoveLimit": stm_limit,
        "passed": True,
    }


def _trainer_arguments(
    *,
    candidate_id: str,
    protocol: Mapping[str, Any],
    parsed: Mapping[str, Any],
    context: Mapping[str, Any],
    corpus: Path,
    corpus_manifest: Path,
    initializer: Path,
    evaluator: Path,
    output_dir: Path,
    seed: int,
    robustness: bool = False,
) -> list[str]:
    common = parsed["common"]
    split = parsed["split"]
    candidate = parsed["candidates"][candidate_id]
    suffix = "-robustness" if robustness else ""
    network = output_dir / f"{candidate_id}{suffix}.nnue"
    manifest = output_dir / f"{candidate_id}{suffix}.manifest.json"
    argv = [
        sys.executable,
        str(Path(__file__).with_name("train.py").resolve(strict=True)),
        "--input",
        str(corpus.resolve(strict=True)),
        "--output",
        str(network.resolve()),
        "--manifest",
        str(manifest.resolve()),
        "--network-semantics",
        "king-state-residual",
        "--initial-network",
        str(initializer.resolve(strict=True)),
        "--expand-residual-to-king-state",
        "--candidate-id",
        candidate_id,
        "--protocol",
        str(Path(context["protocol"]["path"])),
        "--prelabel-seal",
        str(Path(context["prelabelSeal"]["path"])),
        "--corpus-manifest",
        str(corpus_manifest.resolve(strict=True)),
        "--static-hce-evaluator",
        str(evaluator.resolve(strict=True)),
        "--cpp-evaluator",
        str(evaluator.resolve(strict=True)),
        "--seed",
        str(seed),
        "--split-seed",
        str(split["splitSeed"]),
        "--train-percent",
        str(split["trainPercent"]),
        "--validation-percent",
        str(split["validationPercent"]),
        "--group-field",
        str(split["groupField"]),
        "--collision-policy",
        str(split["collisionPolicy"]),
        "--batch-size",
        str(common["batchSize"]),
        "--cp-weight",
        str(common["cpWeight"]),
        "--outcome-weight",
        str(common["outcomeWeight"]),
        "--target-cp-clip",
        str(common["targetCpClip"]),
        "--dense-bias-learning-rate-scale",
        str(common["denseBiasLearningRateScale"]),
        "--dense-weight-learning-rate-scale",
        str(common["denseWeightLearningRateScale"]),
        "--output-learning-rate-scale",
        str(common["outputLearningRateScale"]),
        "--qat-learning-rate-scale",
        str(common["qatLearningRateScale"]),
        "--strict",
        "--quiet",
    ]
    if candidate_id == "K0":
        if robustness:
            raise ValueError("K0 has no robustness training run")
        argv.append("--migrate-only")
    else:
        argv.extend(
            [
                "--protocol-pretest",
                "--epochs",
                str(common["epochs"]),
                "--qat-epochs",
                str(common["qatEpochs"]),
                "--learning-rate",
                str(candidate["learningRate"]),
                "--feature-transformer-learning-rate-scale",
                str(candidate["featureTransformerLearningRateScale"]),
                "--float-checkpoint",
                str((output_dir / f"{candidate_id}{suffix}.float").resolve()),
            ]
        )
    return argv


def _command_record(candidate_id: str, argv: list[str]) -> dict[str, Any]:
    output_index = argv.index("--output") + 1
    manifest_index = argv.index("--manifest") + 1
    record: dict[str, Any] = {
        "candidateId": candidate_id,
        "argv": argv,
        "rawTrainerArgv": argv[2:],
        "network": str(Path(argv[output_index]).resolve()),
        "manifest": str(Path(argv[manifest_index]).resolve()),
    }
    if "--float-checkpoint" in argv:
        record["floatCheckpoint"] = str(
            Path(argv[argv.index("--float-checkpoint") + 1]).resolve()
        )
    return record


def _prepare_plan(args: argparse.Namespace) -> dict[str, Any]:
    protocol, parsed, context = _verify_prelabel_context(
        protocol_path=args.protocol,
        seal_path=args.seal,
        initializer_path=args.initializer,
        cpp_evaluator_path=args.cpp_evaluator,
        run_canonical_verifier=True,
    )
    corpus_chain = _verify_corpus_chain(
        corpus_path=args.corpus,
        corpus_manifest_path=args.corpus_manifest,
        seal_pin=context["prelabelSeal"],
        evaluator_pin=context["staticHceEvaluator"],
    )
    feature_corpus = _feature_corpus(
        args.corpus,
        split_seed=int(parsed["split"]["splitSeed"]),
        train_percent=float(parsed["split"]["trainPercent"]),
        validation_percent=float(parsed["split"]["validationPercent"]),
    )
    signature_splits: dict[str, set[int]] = {}
    for signature, split_value in zip(
        feature_corpus.input_signatures, feature_corpus.splits
    ):
        signature_splits.setdefault(signature, set()).add(int(split_value))
    cross_split = {
        signature: values
        for signature, values in signature_splits.items()
        if len(values) > 1
    }
    if cross_split:
        raise ValueError(
            f"{len(cross_split)} exact NNUE inputs cross frozen splits"
        )
    if len(signature_splits) != feature_corpus.count:
        raise ValueError(
            "deep-HCE-v2 corpus contains duplicate exact architecture-3 inputs"
        )
    validation_balance = _balance_audit(feature_corpus, 1, parsed["split"])
    test_balance = _balance_audit(feature_corpus, 2, parsed["split"])
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.plan.resolve() != output_dir / "training-plan.json":
        raise ValueError(
            "--plan must be <output-dir>/training-plan.json so later one-time "
            "seals have one canonical path"
        )
    commands = {}
    for candidate_id in ("K0", "K1", "K2"):
        argv = _trainer_arguments(
            candidate_id=candidate_id,
            protocol=protocol,
            parsed=parsed,
            context=context,
            corpus=args.corpus,
            corpus_manifest=args.corpus_manifest,
            initializer=args.initializer,
            evaluator=args.cpp_evaluator,
            output_dir=output_dir,
            seed=int(parsed["seeds"]["primary"]),
        )
        commands[candidate_id] = _command_record(candidate_id, argv)
    planned_artifacts = [
        output_dir / "validation-selection.seal.json",
        output_dir / "offline-test.json",
        output_dir / "offline-test.json.access.json",
    ]
    for command in commands.values():
        for key in ("network", "manifest", "floatCheckpoint"):
            if key in command:
                planned_artifacts.append(Path(str(command[key])))
    existing = [str(path) for path in planned_artifacts if path.exists()]
    if existing:
        raise ValueError(
            "cannot freeze a new plan over existing experiment artifacts: "
            + ", ".join(existing)
        )
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "createdUtc": _utc_now(),
        "generationId": protocol["generationId"],
        "identities": {
            **context,
            **corpus_chain,
            "trainer": _identity(Path(__file__).with_name("train.py")),
            "orchestrator": _identity(Path(__file__)),
        },
        "corpusFeatureAudit": {
            "rows": feature_corpus.count,
            "targetsDecoded": 0,
            "targetsEmitted": 0,
            "groupPhasePure": True,
            "exactInputUnique": True,
            "crossSplitExactInputCollisions": 0,
            "validation": validation_balance,
            "test": test_balance,
        },
        "commands": commands,
        "outputs": {
            "selectionSeal": str(
                output_dir / "validation-selection.seal.json"
            ),
            "offlineTest": str(output_dir / "offline-test.json"),
            "offlineTestAccessClaim": str(
                output_dir / "offline-test.json.access.json"
            ),
        },
        "selectionPolicy": {
            "labelsAvailableBeforeSelection": ["train", "validation"],
            "heldOutLabelsAvailableBeforeSelection": False,
            "candidateIds": ["K0", "K1", "K2"],
        },
    }
    _atomic_json(args.plan, plan, no_clobber=True)
    return plan


def _verify_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = _load_json(path, "training plan")
    _expect(plan.get("schemaVersion"), SCHEMA_VERSION, "plan schema")
    _expect(plan.get("kind"), PLAN_KIND, "plan kind")
    identities = _mapping(plan.get("identities"), "plan identities")
    for name, pin in identities.items():
        _verify_identity(_mapping(pin, f"plan identity {name}"), f"plan {name}")
    protocol, parsed, context = _verify_prelabel_context(
        protocol_path=Path(identities["protocol"]["path"]),
        seal_path=Path(identities["prelabelSeal"]["path"]),
        initializer_path=Path(identities["initializer"]["path"]),
        cpp_evaluator_path=Path(identities["staticHceEvaluator"]["path"]),
        run_canonical_verifier=True,
    )
    for key, value in context.items():
        if not _same_identity(value, identities[key]):
            raise ValueError(f"plan has an inconsistent {key} identity")
    chain = _verify_corpus_chain(
        corpus_path=Path(identities["corpus"]["path"]),
        corpus_manifest_path=Path(identities["corpusManifest"]["path"]),
        seal_pin=identities["prelabelSeal"],
        evaluator_pin=identities["staticHceEvaluator"],
    )
    for key, value in chain.items():
        if not _same_identity(value, identities[key]):
            raise ValueError(f"plan has an inconsistent corpus-chain identity {key}")
    commands = _mapping(plan.get("commands"), "plan commands")
    _expect(set(commands), {"K0", "K1", "K2"}, "plan candidate commands")
    output_dirs = {
        Path(str(_mapping(command, "plan command")["network"])).resolve().parent
        for command in commands.values()
    }
    if len(output_dirs) != 1:
        raise ValueError("planned candidate outputs do not share one directory")
    output_dir = next(iter(output_dirs))
    outputs = _mapping(plan.get("outputs"), "plan outputs")
    expected_outputs = {
        "selectionSeal": str(
            output_dir / "validation-selection.seal.json"
        ),
        "offlineTest": str(output_dir / "offline-test.json"),
        "offlineTestAccessClaim": str(
            output_dir / "offline-test.json.access.json"
        ),
    }
    _expect(outputs, expected_outputs, "plan canonical output paths")
    if path.resolve() != output_dir / "training-plan.json":
        raise ValueError("training plan is not at its canonical output path")
    for candidate_id, command_value in commands.items():
        command = _mapping(command_value, f"plan command {candidate_id}")
        _expect(command.get("candidateId"), candidate_id, "command candidate id")
        argv = _sequence(command.get("argv"), f"{candidate_id} argv")
        if argv[0] != sys.executable:
            raise ValueError(f"{candidate_id} uses a different Python executable")
        if Path(str(argv[1])).resolve() != Path(
            identities["trainer"]["path"]
        ).resolve():
            raise ValueError(f"{candidate_id} uses a different trainer")
        _expect(command.get("rawTrainerArgv"), argv[2:], f"{candidate_id} raw argv")
        expected_argv = _trainer_arguments(
            candidate_id=candidate_id,
            protocol=protocol,
            parsed=parsed,
            context=identities,
            corpus=Path(identities["corpus"]["path"]),
            corpus_manifest=Path(identities["corpusManifest"]["path"]),
            initializer=Path(identities["initializer"]["path"]),
            evaluator=Path(identities["staticHceEvaluator"]["path"]),
            output_dir=output_dir,
            seed=int(parsed["seeds"]["primary"]),
        )
        _expect(argv, expected_argv, f"{candidate_id} exact frozen command")
    return plan, protocol, parsed


def _verify_candidate(
    plan: Mapping[str, Any], candidate_id: str
) -> tuple[dict[str, Any], Path, Path, Path | None]:
    command = _mapping(plan["commands"][candidate_id], f"{candidate_id} command")
    network_path = Path(str(command["network"])).resolve(strict=True)
    manifest_path = Path(str(command["manifest"])).resolve(strict=True)
    manifest = _load_json(manifest_path, f"{candidate_id} trainer manifest")
    _expect(manifest.get("candidateId"), candidate_id, f"{candidate_id} manifest id")
    _expect(
        manifest.get("rawArgv"),
        command["rawTrainerArgv"],
        f"{candidate_id} frozen raw argv",
    )
    round_trip = _mapping(manifest.get("roundTrip"), f"{candidate_id} round trip")
    if str(round_trip.get("sha256", "")).lower() != _sha256(network_path):
        raise ValueError(f"{candidate_id} manifest does not pin its network")
    experiment = _mapping(manifest.get("experiment"), f"{candidate_id} experiment")
    plan_ids = _mapping(plan["identities"], "plan identities")
    for key in (
        "protocol",
        "prelabelSeal",
        "corpusManifest",
        "staticHceEvaluator",
    ):
        if not _same_identity(
            _mapping(experiment.get(key), f"{candidate_id} experiment {key}"),
            _mapping(plan_ids[key], f"plan identity {key}"),
        ):
            raise ValueError(f"{candidate_id} manifest has a different {key}")
    _expect(
        experiment.get("candidateId"),
        candidate_id,
        f"{candidate_id} experiment candidate id",
    )
    _expect(experiment.get("strict"), True, f"{candidate_id} experiment strict")
    _expect(manifest.get("strict"), True, f"{candidate_id} strict mode")
    expected_inputs = [plan_ids["corpus"]]
    actual_inputs = _sequence(manifest.get("inputs"), f"{candidate_id} inputs")
    if len(actual_inputs) != 1 or not _same_identity(
        _mapping(actual_inputs[0], f"{candidate_id} input"), expected_inputs[0]
    ):
        raise ValueError(f"{candidate_id} must pin exactly the planned corpus")
    initial = _mapping(
        manifest.get("initialNetwork"), f"{candidate_id} initializer"
    )
    if not _same_identity(initial, plan_ids["initializer"]):
        raise ValueError(f"{candidate_id} used a different initializer")
    if manifest.get("strict") is not True:
        raise ValueError(f"{candidate_id} was not strict")
    if candidate_id != "K0":
        pretest = _mapping(
            manifest.get("protocolPretest"), f"{candidate_id} pretest audit"
        )
        _expect(
            pretest.get("heldOutTargetFieldsDecoded"),
            0,
            f"{candidate_id} held-out target reads",
        )
        _expect(
            pretest.get("heldOutMetricsComputed"),
            False,
            f"{candidate_id} held-out metrics",
        )
        final = _mapping(manifest.get("finalQuantized"), f"{candidate_id} metrics")
        quant = _mapping(
            manifest.get("quantizationPenalty"), f"{candidate_id} quantization"
        )
        if "test" in final or "test" in quant:
            raise ValueError(f"{candidate_id} manifest contains held-out metrics")
    else:
        migration = _mapping(manifest.get("migrationParity"), "K0 migration parity")
        _expect(migration.get("status"), "passed", "K0 migration status")
        _expect(migration.get("scope"), "all-corpus-ofens", "K0 migration scope")
        _expect(migration.get("targetFieldsDecoded"), 0, "K0 target reads")
    float_path = (
        Path(str(command["floatCheckpoint"])).resolve(strict=True)
        if "floatCheckpoint" in command
        else None
    )
    if float_path is not None:
        pin = _mapping(manifest.get("floatCheckpoint"), f"{candidate_id} float pin")
        if not _same_identity(pin, _identity(float_path)):
            raise ValueError(f"{candidate_id} float checkpoint is not pinned")
    return manifest, network_path, manifest_path, float_path


def _run_frozen(args: argparse.Namespace) -> None:
    plan, _protocol, _parsed = _verify_plan(args.plan)
    candidate_ids = (
        ("K0", "K1", "K2") if args.candidate == "all" else (args.candidate,)
    )
    for candidate_id in candidate_ids:
        command = _mapping(plan["commands"][candidate_id], "candidate command")
        for key in ("network", "manifest", "floatCheckpoint"):
            if key in command and Path(str(command[key])).exists():
                raise ValueError(
                    f"refusing to overwrite {candidate_id} {key}: {command[key]}"
                )
        completed = subprocess.run(
            [str(value) for value in command["argv"]],
            cwd=_repo_root(),
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(
                f"{candidate_id} trainer exited {completed.returncode}"
            )
        _verify_candidate(plan, candidate_id)


@dataclass(frozen=True)
class LabeledSplit:
    indices: np.ndarray
    groups: tuple[str, ...]
    phases: tuple[str, ...]
    targets: np.ndarray


def _load_labeled_split(
    path: Path,
    feature_corpus: FeatureCorpus,
    *,
    wanted_split: int,
    target_clip: float,
) -> LabeledSplit:
    indices: list[int] = []
    groups: list[str] = []
    phases: list[str] = []
    targets: list[float] = []
    row_index = 0
    resolved = path.resolve(strict=True)
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            if row_index >= feature_corpus.count:
                raise ValueError("corpus grew after feature-only audit")
            location = f"{resolved}:{line_number}"
            group = trainer._selective_top_level_string(
                line, "groupId", location=location
            )
            if group != feature_corpus.groups[row_index]:
                raise ValueError(f"{location}: corpus ordering changed")
            split = int(feature_corpus.splits[row_index])
            if split == wanted_split:
                # Full JSON decoding occurs only after group routing proves
                # this row belongs to the explicitly authorized split.
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"{location}: selected split row is invalid JSON: {error}"
                    ) from error
                if not isinstance(record, dict):
                    raise ValueError(f"{location}: selected row is not an object")
                value = record.get("targetCpStm")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise ValueError(
                        f"{location}: targetCpStm must be a finite number"
                    )
                if record.get("targetSemantics") != "search-minus-handcrafted":
                    raise ValueError(f"{location}: wrong residual target semantics")
                indices.append(row_index)
                groups.append(group)
                phases.append(feature_corpus.phases[row_index])
                targets.append(
                    float(np.clip(float(value), -target_clip, target_clip))
                )
            row_index += 1
    if row_index != feature_corpus.count:
        raise ValueError("corpus shrank after feature-only audit")
    if not indices:
        raise ValueError(f"split {wanted_split} has no labeled rows")
    return LabeledSplit(
        indices=np.asarray(indices, dtype=np.int64),
        groups=tuple(groups),
        phases=tuple(phases),
        targets=np.asarray(targets, dtype=np.float64),
    )


def _huber(error_cp: np.ndarray) -> np.ndarray:
    normalized = error_cp / trainer.CP_NORMALIZER
    absolute = np.abs(normalized)
    delta = trainer.CP_HUBER_DELTA
    return np.where(
        absolute <= delta,
        0.5 * normalized * normalized,
        delta * (absolute - 0.5 * delta),
    )


def _row_metrics(
    labeled: LabeledSplit, predictions: np.ndarray
) -> dict[str, Any]:
    selected = predictions[labeled.indices].astype(np.float64)
    error = selected - labeled.targets
    losses = _huber(error)
    absolute = np.abs(error)
    phase = {}
    for name in PHASES:
        mask = np.asarray([value == name for value in labeled.phases])
        phase[name] = {
            "samples": int(mask.sum()),
            "huberLoss": float(losses[mask].mean()),
            "cpMae": float(absolute[mask].mean()),
        }
    return {
        "samples": int(labeled.indices.size),
        "huberLoss": float(losses.mean()),
        "cpMae": float(absolute.mean()),
        "phase": phase,
    }


def _cpp_stream_predictions(
    helper: Path, network: Path, ofens: Sequence[str]
) -> np.ndarray:
    payload = "".join(ofen + "\n" for ofen in ofens)
    completed = subprocess.run(
        [str(helper.resolve(strict=True)), STREAM_MODE, str(network.resolve(strict=True))],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise ValueError(
            "C++ network stream failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    lines = completed.stdout.splitlines()
    if len(lines) != len(ofens):
        raise ValueError(
            f"C++ stream returned {len(lines)} scores for {len(ofens)} OFENs"
        )
    values: list[int] = []
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or text.lstrip("+-").isdigit() is False:
            raise ValueError(f"C++ stream row {index + 1} is not one integer")
        values.append(int(text))
    return np.asarray(values, dtype=np.int32)


def _runtime_health(
    *,
    candidate_id: str,
    network_path: Path,
    float_path: Path | None,
    helper_path: Path,
    corpus: FeatureCorpus,
    runtime_protocol: Mapping[str, Any],
    batch_size: int,
    k0_dead_units: int | None,
) -> tuple[dict[str, Any], np.ndarray]:
    network = QuantizedNetwork.read(network_path)
    if network.architecture != ARCHITECTURE_KING_STATE_RESIDUAL:
        raise ValueError(f"{candidate_id} is not architecture 3")
    model = (
        trainer.FloatNetwork.read_checkpoint(float_path)
        if float_path is not None
        else trainer.FloatNetwork.from_quantized(network)
    )
    if model.ft_weights.shape[0] != KING_STATE_FEATURE_COUNT:
        raise ValueError(f"{candidate_id} float checkpoint has the wrong features")
    predictions = np.empty(corpus.count, dtype=np.int32)
    quant_errors: list[np.ndarray] = []
    active_cells = 0
    total_cells = 0
    ever_positive = np.zeros(trainer.HIDDEN_SIZE, dtype=np.bool_)
    ever_below_clip = np.zeros(trainer.HIDDEN_SIZE, dtype=np.bool_)
    finite_float = True
    for start in range(0, corpus.count, batch_size):
        indices = np.arange(start, min(start + batch_size, corpus.count))
        stm, opponent = corpus.perspective(indices)
        quantized = network.predict_features(stm, opponent)
        floating, cache = model.forward(
            stm, opponent, quantization_aware=False, need_cache=True
        )
        assert cache is not None
        predictions[start : start + indices.size] = quantized
        quant_errors.append(np.abs(floating.astype(np.float64) - quantized))
        dense = cache["dense_z"]
        active = (dense > 0.0) & (dense < ACTIVATION_MAX)
        active_cells += int(active.sum())
        total_cells += int(active.size)
        ever_positive |= np.any(dense > 0.0, axis=0)
        ever_below_clip |= np.any(dense < ACTIVATION_MAX, axis=0)
        finite_float = finite_float and bool(np.all(np.isfinite(floating)))
    errors = np.concatenate(quant_errors)
    cpp = _cpp_stream_predictions(helper_path, network_path, corpus.ofens)
    mismatches = int(np.count_nonzero(cpp != predictions))
    dead = int((~ever_positive).sum())
    saturated = int((~ever_below_clip).sum())
    file_bytes = network_path.stat().st_size
    expected_bytes = HEADER_BYTES + payload_bytes_for_architecture(
        ARCHITECTURE_KING_STATE_RESIDUAL
    )
    checks = {
        "pythonCppWholeCorpus": mismatches == 0,
        "meanQuantizationPenalty": float(errors.mean())
        <= float(runtime_protocol["maximumMeanFloatQuantizationPenaltyCp"]),
        "maximumQuantizationPenalty": float(errors.max())
        <= float(runtime_protocol["maximumSingleFloatQuantizationPenaltyCp"]),
        "saturatedDenseUnits": saturated
        <= int(runtime_protocol["maximumSaturatedDenseUnits"]),
        "deadDenseUnits": dead <= int(runtime_protocol["maximumDeadDenseUnits"]),
        "deadDenseUnitsVsK0": (
            True
            if k0_dead_units is None
            else (
                not bool(runtime_protocol["deadDenseUnitsMayExceedK0"])
                and dead <= k0_dead_units
            )
        ),
        "denseActiveFraction": (
            float(runtime_protocol["denseActiveFractionRange"][0])
            <= active_cells / total_cells
            <= float(runtime_protocol["denseActiveFractionRange"][1])
        ),
        "maximumAbsoluteCorrection": int(np.max(np.abs(predictions)))
        <= int(runtime_protocol["maximumAbsoluteCorrectionCp"]),
        "finitePredictions": bool(np.all(np.isfinite(predictions))) and finite_float,
        "expectedFileSize": file_bytes == expected_bytes,
        "roundTrip": QuantizedNetwork.read(network_path).to_bytes()
        == network.to_bytes(),
    }
    health = {
        "candidateId": candidate_id,
        "positions": corpus.count,
        "pythonCppMismatches": mismatches,
        "meanFloatQuantizationPenaltyCp": float(errors.mean()),
        "maximumFloatQuantizationPenaltyCp": float(errors.max()),
        "deadDenseUnits": dead,
        "saturatedDenseUnits": saturated,
        "denseActiveFraction": active_cells / total_cells,
        "maximumAbsoluteCorrectionCp": int(np.max(np.abs(predictions))),
        "fileBytes": file_bytes,
        "expectedFileBytes": expected_bytes,
        "checks": checks,
        "passed": all(checks.values()),
    }
    return health, predictions


def _choose_candidate(
    candidate_metrics: Mapping[str, Mapping[str, Any]],
    selection_protocol: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    if set(candidate_metrics) != {"K0", "K1", "K2"}:
        raise ValueError("validation selector requires exactly K0/K1/K2")
    k0 = candidate_metrics["K0"]
    k0_loss = float(k0["validation"]["huberLoss"])
    k0_mae = float(k0["validation"]["cpMae"])
    if not math.isfinite(k0_loss) or k0_loss <= 0.0:
        raise ValueError("K0 validation loss must be finite and positive")
    eligibility: dict[str, Any] = {}
    eligible: list[str] = []
    for candidate_id in ("K1", "K2"):
        value = candidate_metrics[candidate_id]
        metrics = value["validation"]
        loss = float(metrics["huberLoss"])
        mae = float(metrics["cpMae"])
        relative = (k0_loss - loss) / k0_loss
        phase_regressions = {
            phase: float(metrics["phase"][phase]["cpMae"])
            - float(k0["validation"]["phase"][phase]["cpMae"])
            for phase in PHASES
        }
        checks = {
            "finitePositiveLoss": math.isfinite(loss) and loss > 0.0,
            "minimumRelativeHuberImprovement": relative
            >= float(
                selection_protocol["minimumRelativeHuberImprovementOverK0"]
            ),
            "lowerCpMae": (
                mae < k0_mae
                if bool(selection_protocol["mustLowerCpMae"])
                else True
            ),
            "phaseMaeRegression": max(phase_regressions.values())
            <= float(selection_protocol["maximumPhaseMaeRegressionCp"]),
            "health": bool(value["health"]["passed"]),
        }
        passed = all(checks.values())
        eligibility[candidate_id] = {
            "relativeHuberImprovementOverK0": relative,
            "cpMaeImprovementOverK0": k0_mae - mae,
            "phaseMaeRegressionCp": phase_regressions,
            "checks": checks,
            "eligible": passed,
        }
        if passed:
            eligible.append(candidate_id)
    if not eligible:
        raise ValueError(
            "no validation candidate is eligible; generation fails without "
            "consulting held-out test labels"
        )
    winner = min(
        eligible,
        key=lambda candidate_id: float(
            candidate_metrics[candidate_id]["validation"]["huberLoss"]
        ),
    )
    if set(eligible) == {"K1", "K2"}:
        left = float(candidate_metrics["K1"]["validation"]["huberLoss"])
        right = float(candidate_metrics["K2"]["validation"]["huberLoss"])
        if (
            not math.isfinite(left)
            or not math.isfinite(right)
            or left <= 0.0
            or right <= 0.0
        ):
            raise ValueError(
                "validation tie formula requires finite positive K1/K2 losses"
            )
        relative_tie = abs(left - right) / min(left, right)
        if relative_tie <= float(selection_protocol["tieRelativeLoss"]):
            winner = str(selection_protocol["tieWinner"])
    else:
        relative_tie = None
    return winner, {
        "eligibility": eligibility,
        "eligibleCandidates": eligible,
        "tieFormula": (
            "abs(K1Loss-K2Loss)/min(K1Loss,K2Loss) <= tieRelativeLoss"
        ),
        "tieRelativeLossObserved": relative_tie,
        "winner": winner,
    }


def _select(args: argparse.Namespace) -> dict[str, Any]:
    plan, protocol, parsed = _verify_plan(args.plan)
    expected_selection = Path(
        str(_mapping(plan.get("outputs"), "plan outputs")["selectionSeal"])
    ).resolve()
    if args.output.resolve() != expected_selection:
        raise ValueError(
            f"selection output must be the precommitted path {expected_selection}"
        )
    if args.output.exists():
        raise ValueError(f"selection seal already exists: {args.output.resolve()}")
    corpus_path = Path(plan["identities"]["corpus"]["path"])
    feature_corpus = _feature_corpus(
        corpus_path,
        split_seed=int(parsed["split"]["splitSeed"]),
        train_percent=float(parsed["split"]["trainPercent"]),
        validation_percent=float(parsed["split"]["validationPercent"]),
    )
    validation = _load_labeled_split(
        corpus_path,
        feature_corpus,
        wanted_split=1,
        target_clip=float(parsed["common"]["targetCpClip"]),
    )
    candidate_values: dict[str, dict[str, Any]] = {}
    k0_dead: int | None = None
    for candidate_id in ("K0", "K1", "K2"):
        manifest, network_path, manifest_path, float_path = _verify_candidate(
            plan, candidate_id
        )
        health, predictions = _runtime_health(
            candidate_id=candidate_id,
            network_path=network_path,
            float_path=float_path,
            helper_path=Path(plan["identities"]["staticHceEvaluator"]["path"]),
            corpus=feature_corpus,
            runtime_protocol=parsed["runtime"],
            batch_size=int(parsed["common"]["batchSize"]),
            k0_dead_units=k0_dead,
        )
        if candidate_id == "K0":
            k0_dead = int(health["deadDenseUnits"])
            migration = _mapping(manifest.get("migrationParity"), "K0 migration")
            _expect(
                migration.get("positionsChecked"),
                feature_corpus.count,
                "K0 all-corpus parity count",
            )
        candidate_values[candidate_id] = {
            "network": _identity(network_path),
            "manifest": _identity(manifest_path),
            "floatCheckpoint": (
                None if float_path is None else _identity(float_path)
            ),
            "validation": _row_metrics(validation, predictions),
            "health": health,
        }
    if not candidate_values["K0"]["health"]["passed"]:
        raise ValueError("K0 failed the frozen runtime health gate")
    winner, decision = _choose_candidate(
        candidate_values, parsed["selection"]
    )
    selected = candidate_values[winner]
    robustness_argv = _trainer_arguments(
        candidate_id=winner,
        protocol=protocol,
        parsed=parsed,
        context=plan["identities"],
        corpus=corpus_path,
        corpus_manifest=Path(plan["identities"]["corpusManifest"]["path"]),
        initializer=Path(plan["identities"]["initializer"]["path"]),
        evaluator=Path(plan["identities"]["staticHceEvaluator"]["path"]),
        output_dir=Path(plan["commands"][winner]["network"]).parent,
        seed=int(parsed["seeds"]["robustness"]),
        robustness=True,
    )
    seal = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": SELECTION_KIND,
        "createdUtc": _utc_now(),
        "generationId": protocol["generationId"],
        "plan": _identity(args.plan),
        "protocol": plan["identities"]["protocol"],
        "prelabelSeal": plan["identities"]["prelabelSeal"],
        "corpus": plan["identities"]["corpus"],
        "orchestrator": _identity(Path(__file__)),
        "informationBoundary": {
            "trainLabelsDecoded": False,
            "validationLabelsDecoded": True,
            "heldOutLabelsDecoded": False,
            "heldOutMetricsRead": False,
            "heldOutMetricsEmitted": False,
            "featureOnlyWholeCorpusHealthAllowed": True,
        },
        "validationBalance": _balance_audit(
            feature_corpus, 1, parsed["split"]
        ),
        "candidates": candidate_values,
        "decision": decision,
        "selectedCandidateId": winner,
        "selectedNetwork": selected["network"],
        "selectedManifest": selected["manifest"],
        "robustnessCommand": _command_record(winner, robustness_argv),
        "offlineTestOutput": plan["outputs"]["offlineTest"],
        "offlineTestAccessClaim": plan["outputs"]["offlineTestAccessClaim"],
        "testAccessPermittedOnlyAfterThisHashIsRecorded": True,
    }
    _atomic_json(args.output, seal, no_clobber=True)
    return seal


def _verify_selection(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    selection = _load_json(path, "validation selection seal")
    _expect(selection.get("schemaVersion"), SCHEMA_VERSION, "selection schema")
    _expect(selection.get("kind"), SELECTION_KIND, "selection kind")
    _verify_identity(
        _mapping(selection.get("orchestrator"), "selection orchestrator"),
        "selection orchestrator",
    )
    plan_path = _verify_identity(
        _mapping(selection.get("plan"), "selection plan"), "selection plan"
    )
    plan, protocol, parsed = _verify_plan(plan_path)
    expected_selection_path = Path(
        str(_mapping(plan.get("outputs"), "plan outputs")["selectionSeal"])
    ).resolve()
    if path.resolve() != expected_selection_path:
        raise ValueError(
            f"selection seal is not at the canonical path {expected_selection_path}"
        )
    winner = str(selection.get("selectedCandidateId"))
    if winner not in ("K1", "K2"):
        raise ValueError("selection winner must be K1 or K2")
    network = _verify_identity(
        _mapping(selection.get("selectedNetwork"), "selected network"),
        "selected network",
    )
    manifest = _verify_identity(
        _mapping(selection.get("selectedManifest"), "selected manifest"),
        "selected manifest",
    )
    selected_candidates = _mapping(
        selection.get("candidates"), "selection candidates"
    )
    _expect(
        set(selected_candidates),
        {"K0", "K1", "K2"},
        "selection candidate ids",
    )
    verified: dict[str, tuple[Path, Path, Path | None]] = {}
    for candidate_id in ("K0", "K1", "K2"):
        _candidate_manifest, candidate_network, candidate_manifest_path, float_path = (
            _verify_candidate(plan, candidate_id)
        )
        record = _mapping(
            selected_candidates[candidate_id],
            f"selection candidate {candidate_id}",
        )
        if not _same_identity(
            _mapping(record.get("network"), f"{candidate_id} selected network"),
            _identity(candidate_network),
        ):
            raise ValueError(f"selection has a different {candidate_id} network")
        if not _same_identity(
            _mapping(record.get("manifest"), f"{candidate_id} selected manifest"),
            _identity(candidate_manifest_path),
        ):
            raise ValueError(f"selection has a different {candidate_id} manifest")
        if float_path is None:
            _expect(record.get("floatCheckpoint"), None, f"{candidate_id} float")
        elif not _same_identity(
            _mapping(record.get("floatCheckpoint"), f"{candidate_id} float"),
            _identity(float_path),
        ):
            raise ValueError(
                f"selection has a different {candidate_id} float checkpoint"
            )
        verified[candidate_id] = (
            candidate_network,
            candidate_manifest_path,
            float_path,
        )
    _candidate_manifest, planned_network, planned_manifest, _float = _verify_candidate(
        plan, winner
    )
    if network != planned_network or manifest != planned_manifest:
        raise ValueError("selection does not identify the frozen winning candidate")
    boundary = _mapping(
        selection.get("informationBoundary"), "selection information boundary"
    )
    _expect(boundary.get("heldOutLabelsDecoded"), False, "selection test reads")
    _expect(boundary.get("heldOutMetricsRead"), False, "selection test metrics")
    decision = _mapping(selection.get("decision"), "selection decision")
    _expect(decision.get("winner"), winner, "selection decision winner")
    winner_eligibility = _mapping(
        _mapping(decision.get("eligibility"), "selection eligibility").get(winner),
        "winner eligibility",
    )
    _expect(winner_eligibility.get("eligible"), True, "winner eligibility")
    expected_robustness_argv = _trainer_arguments(
        candidate_id=winner,
        protocol=protocol,
        parsed=parsed,
        context=plan["identities"],
        corpus=Path(plan["identities"]["corpus"]["path"]),
        corpus_manifest=Path(plan["identities"]["corpusManifest"]["path"]),
        initializer=Path(plan["identities"]["initializer"]["path"]),
        evaluator=Path(plan["identities"]["staticHceEvaluator"]["path"]),
        output_dir=planned_network.parent,
        seed=int(parsed["seeds"]["robustness"]),
        robustness=True,
    )
    _expect(
        selection.get("robustnessCommand"),
        _command_record(winner, expected_robustness_argv),
        "selection robustness command",
    )
    expected_offline = Path(str(plan["outputs"]["offlineTest"])).resolve()
    _expect(
        selection.get("offlineTestOutput"),
        str(expected_offline),
        "selection offline output",
    )
    _expect(
        selection.get("offlineTestAccessClaim"),
        str(Path(str(plan["outputs"]["offlineTestAccessClaim"])).resolve()),
        "selection offline access claim",
    )
    return selection, plan, protocol, parsed


def _run_robustness(args: argparse.Namespace) -> None:
    selection, _plan, _protocol, _parsed = _verify_selection(args.selection)
    command = _mapping(
        selection.get("robustnessCommand"), "selection robustness command"
    )
    for key in ("network", "manifest", "floatCheckpoint"):
        if key in command and Path(str(command[key])).exists():
            raise ValueError(f"refusing to overwrite robustness {key}: {command[key]}")
    completed = subprocess.run(
        [str(value) for value in command["argv"]],
        cwd=_repo_root(),
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"robustness trainer exited {completed.returncode}")
    manifest_path = Path(str(command["manifest"])).resolve(strict=True)
    manifest = _load_json(manifest_path, "robustness manifest")
    _expect(
        manifest.get("rawArgv"),
        command.get("rawTrainerArgv"),
        "robustness raw argv",
    )
    if str(manifest.get("candidateId")) != str(selection["selectedCandidateId"]):
        raise ValueError("robustness manifest used another recipe")
    if "test" in _mapping(manifest.get("finalQuantized"), "robustness metrics"):
        raise ValueError("robustness training emitted held-out metrics")


def _group_phase_metrics(
    labeled: LabeledSplit, predictions: np.ndarray
) -> dict[str, Any]:
    selected = predictions[labeled.indices].astype(np.float64)
    error = selected - labeled.targets
    loss = _huber(error)
    absolute = np.abs(error)
    phase_group_loss: dict[str, dict[str, list[float]]] = {
        phase: {} for phase in PHASES
    }
    phase_group_mae: dict[str, dict[str, list[float]]] = {
        phase: {} for phase in PHASES
    }
    for phase, group, row_loss, row_mae in zip(
        labeled.phases, labeled.groups, loss, absolute
    ):
        phase_group_loss[phase].setdefault(group, []).append(float(row_loss))
        phase_group_mae[phase].setdefault(group, []).append(float(row_mae))
    phase_values: dict[str, Any] = {}
    group_loss_means: dict[str, dict[str, float]] = {}
    group_mae_means: dict[str, dict[str, float]] = {}
    for phase in PHASES:
        if not phase_group_loss[phase]:
            raise ValueError(f"held-out split is missing phase {phase}")
        group_loss_means[phase] = {
            group: float(np.mean(values))
            for group, values in phase_group_loss[phase].items()
        }
        group_mae_means[phase] = {
            group: float(np.mean(values))
            for group, values in phase_group_mae[phase].items()
        }
        phase_values[phase] = {
            "groups": len(group_loss_means[phase]),
            "rows": sum(len(values) for values in phase_group_loss[phase].values()),
            "huberLoss": float(np.mean(list(group_loss_means[phase].values()))),
            "cpMae": float(np.mean(list(group_mae_means[phase].values()))),
        }
    return {
        "huberLoss": float(
            np.mean([phase_values[phase]["huberLoss"] for phase in PHASES])
        ),
        "cpMae": float(
            np.mean([phase_values[phase]["cpMae"] for phase in PHASES])
        ),
        "phase": phase_values,
        "_groupLoss": group_loss_means,
        "_groupMae": group_mae_means,
    }


def _public_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in metrics.items() if not str(key).startswith("_")
    }


def _paired_bootstrap(
    candidate: Mapping[str, Any],
    baselines: Mapping[str, Mapping[str, Any]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    for phase in PHASES:
        for baseline, metrics in baselines.items():
            values = list(metrics["_groupLoss"][phase].values())
            if (
                not values
                or not all(math.isfinite(float(value)) for value in values)
                or float(np.mean(values)) <= 0.0
            ):
                raise ValueError(
                    f"bootstrap baseline {baseline} has nonpositive or "
                    f"nonfinite loss in {phase}"
                )
    rng = np.random.Generator(np.random.PCG64(seed))
    relative = {
        baseline: np.empty(iterations, dtype=np.float64)
        for baseline in baselines
    }
    for iteration in range(iterations):
        candidate_phase: list[float] = []
        baseline_phase: dict[str, list[float]] = {
            baseline: [] for baseline in baselines
        }
        for phase in PHASES:
            candidate_groups = candidate["_groupLoss"][phase]
            keys = sorted(candidate_groups)
            if not keys:
                raise ValueError(f"bootstrap phase {phase} has no groups")
            for baseline, metrics in baselines.items():
                if set(metrics["_groupLoss"][phase]) != set(keys):
                    raise ValueError(
                        f"bootstrap group mismatch for {baseline} in {phase}"
                    )
            draw = rng.integers(0, len(keys), size=len(keys))
            sampled = [keys[int(index)] for index in draw]
            candidate_phase.append(
                float(np.mean([candidate_groups[group] for group in sampled]))
            )
            for baseline, metrics in baselines.items():
                baseline_phase[baseline].append(
                    float(
                        np.mean(
                            [
                                metrics["_groupLoss"][phase][group]
                                for group in sampled
                            ]
                        )
                    )
                )
        candidate_loss = float(np.mean(candidate_phase))
        for baseline in baselines:
            baseline_loss = float(np.mean(baseline_phase[baseline]))
            if not math.isfinite(baseline_loss) or baseline_loss <= 0.0:
                raise ValueError(
                    f"bootstrap baseline {baseline} sampled a nonpositive "
                    "or nonfinite phase-macro loss"
                )
            relative[baseline][iteration] = (
                baseline_loss - candidate_loss
            ) / baseline_loss
    return {
        baseline: {
            "iterations": iterations,
            "seed": seed,
            "oneSided95LowerRelativeLossImprovement": float(
                np.quantile(values, 0.05, method="linear")
            ),
        }
        for baseline, values in relative.items()
    }


def _offline_test(args: argparse.Namespace) -> dict[str, Any]:
    selection, plan, protocol, parsed = _verify_selection(args.selection)
    expected_output = Path(str(selection["offlineTestOutput"])).resolve()
    if args.output.resolve() != expected_output:
        raise ValueError(
            f"offline output must be the precommitted path {expected_output}"
        )
    access_path = Path(str(selection["offlineTestAccessClaim"])).resolve()
    if access_path != args.output.with_suffix(
        args.output.suffix + ".access.json"
    ).resolve():
        raise ValueError("selection seal has an inconsistent test-access path")
    if access_path.exists() or args.output.exists():
        raise ValueError(
            "held-out split was already claimed or evaluated; a second look is forbidden"
        )
    winner = str(selection["selectedCandidateId"])
    robustness = _mapping(
        selection.get("robustnessCommand"), "robustness command"
    )
    robustness_network = Path(str(robustness["network"])).resolve(strict=True)
    robustness_manifest_path = Path(str(robustness["manifest"])).resolve(strict=True)
    robustness_float = Path(str(robustness["floatCheckpoint"])).resolve(strict=True)
    robustness_manifest = _load_json(
        robustness_manifest_path, "robustness manifest"
    )
    _expect(
        robustness_manifest.get("rawArgv"),
        robustness.get("rawTrainerArgv"),
        "robustness raw argv",
    )
    _expect(robustness_manifest.get("candidateId"), winner, "robustness recipe")
    _expect(
        robustness_manifest.get("strict"), True, "robustness strict training"
    )
    _expect(
        robustness_manifest.get("seed"),
        parsed["seeds"]["robustness"],
        "robustness model seed",
    )
    robustness_experiment = _mapping(
        robustness_manifest.get("experiment"), "robustness experiment"
    )
    for key in (
        "protocol",
        "prelabelSeal",
        "corpusManifest",
        "staticHceEvaluator",
    ):
        if not _same_identity(
            _mapping(
                robustness_experiment.get(key),
                f"robustness experiment {key}",
            ),
            _mapping(plan["identities"][key], f"planned {key}"),
        ):
            raise ValueError(f"robustness run has a different {key}")
    _expect(
        robustness_experiment.get("strict"), True, "robustness experiment strict"
    )
    _expect(
        robustness_experiment.get("modelSeed"),
        parsed["seeds"]["robustness"],
        "robustness experiment model seed",
    )
    robustness_inputs = _sequence(
        robustness_manifest.get("inputs"), "robustness inputs"
    )
    if len(robustness_inputs) != 1 or not _same_identity(
        _mapping(robustness_inputs[0], "robustness corpus"),
        _mapping(plan["identities"]["corpus"], "planned corpus"),
    ):
        raise ValueError("robustness run did not use exactly the planned corpus")
    if not _same_identity(
        _mapping(
            robustness_manifest.get("initialNetwork"),
            "robustness initializer",
        ),
        _mapping(plan["identities"]["initializer"], "planned initializer"),
    ):
        raise ValueError("robustness run used a different initializer")
    robustness_pretest = _mapping(
        robustness_manifest.get("protocolPretest"), "robustness pretest"
    )
    _expect(
        robustness_pretest.get("heldOutTargetFieldsDecoded"),
        0,
        "robustness held-out reads",
    )
    _expect(
        robustness_pretest.get("heldOutMetricsComputed"),
        False,
        "robustness held-out metrics",
    )
    if str(
        _mapping(
            robustness_manifest.get("roundTrip"), "robustness round trip"
        ).get("sha256", "")
    ).lower() != _sha256(robustness_network):
        raise ValueError("robustness manifest does not pin its network")
    if not _same_identity(
        _mapping(
            robustness_manifest.get("floatCheckpoint"),
            "robustness float checkpoint",
        ),
        _identity(robustness_float),
    ):
        raise ValueError("robustness manifest does not pin its float checkpoint")
    if "test" in _mapping(
        robustness_manifest.get("finalQuantized"), "robustness metrics"
    ):
        raise ValueError("robustness manifest already contains test metrics")

    corpus_path = Path(plan["identities"]["corpus"]["path"])
    feature_corpus = _feature_corpus(
        corpus_path,
        split_seed=int(parsed["split"]["splitSeed"]),
        train_percent=float(parsed["split"]["trainPercent"]),
        validation_percent=float(parsed["split"]["validationPercent"]),
    )
    robustness_health, robustness_predictions = _runtime_health(
        candidate_id=winner + "-robustness",
        network_path=robustness_network,
        float_path=robustness_float,
        helper_path=Path(plan["identities"]["staticHceEvaluator"]["path"]),
        corpus=feature_corpus,
        runtime_protocol=parsed["runtime"],
        batch_size=int(parsed["common"]["batchSize"]),
        k0_dead_units=int(
            selection["candidates"]["K0"]["health"]["deadDenseUnits"]
        ),
    )
    if not robustness_health["passed"]:
        raise ValueError(
            "robustness run failed feature-only quantization/runtime gates; "
            "held-out labels remain unopened"
        )

    claim = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TEST_ACCESS_KIND,
        "createdUtc": _utc_now(),
        "selection": _identity(args.selection),
        "selectedCandidateId": winner,
        "selectedNetwork": selection["selectedNetwork"],
        "robustnessNetwork": _identity(robustness_network),
        "output": str(args.output.resolve()),
        "heldOutLabelsDecodedBeforeClaim": False,
        "secondAccessAllowed": False,
    }
    # Publication happens before _load_labeled_split is allowed to decode one
    # test target.  A crash therefore consumes the one-time authorization.
    _atomic_json(access_path, claim, no_clobber=True)

    test = _load_labeled_split(
        corpus_path,
        feature_corpus,
        wanted_split=2,
        target_clip=float(
            parsed["targetAndLoss"]["targetClipCpInclusive"][1]
        ),
    )
    networks = {
        "selected": Path(selection["selectedNetwork"]["path"]),
        "K0": Path(selection["candidates"]["K0"]["network"]["path"]),
        "robustness": robustness_network,
    }
    predictions = {"robustness": robustness_predictions}
    for name, path in networks.items():
        if name == "robustness":
            continue
        network = QuantizedNetwork.read(path)
        result = np.empty(feature_corpus.count, dtype=np.int32)
        batch_size = int(parsed["common"]["batchSize"])
        for start in range(0, feature_corpus.count, batch_size):
            indices = np.arange(
                start, min(start + batch_size, feature_corpus.count)
            )
            stm, opponent = feature_corpus.perspective(indices)
            result[start : start + indices.size] = network.predict_features(
                stm, opponent
            )
        predictions[name] = result
    predictions["zeroResidual"] = np.zeros(feature_corpus.count, dtype=np.int32)
    metrics = {
        name: _group_phase_metrics(test, value)
        for name, value in predictions.items()
    }
    bootstrap = _paired_bootstrap(
        metrics["selected"],
        {"K0": metrics["K0"], "zeroResidual": metrics["zeroResidual"]},
        iterations=int(parsed["bootstrap"]["iterations"]),
        seed=int(parsed["bootstrap"]["seed"]),
    )
    offline = parsed["offline"]
    comparisons: dict[str, Any] = {}
    comparison_passes = []
    for baseline in ("K0", "zeroResidual"):
        candidate = metrics["selected"]
        other = metrics[baseline]
        relative_loss = (
            float(other["huberLoss"]) - float(candidate["huberLoss"])
        ) / float(other["huberLoss"])
        mae_improvement = float(other["cpMae"]) - float(candidate["cpMae"])
        phase_regressions = {
            phase: float(candidate["phase"][phase]["cpMae"])
            - float(other["phase"][phase]["cpMae"])
            for phase in PHASES
        }
        checks = {
            "minimumRelativeLossImprovement": relative_loss
            >= float(offline["minimumRelativeLossImprovementAgainstEach"]),
            "minimumMaeImprovement": mae_improvement
            >= float(offline["minimumMaeImprovementCpAgainstEach"]),
            "positiveOneSidedBootstrap": (
                bootstrap[baseline][
                    "oneSided95LowerRelativeLossImprovement"
                ]
                > 0.0
                if bool(offline["requirePositiveFifthPercentileAgainstEach"])
                else True
            ),
            "maximumPhaseMaeRegression": max(phase_regressions.values())
            <= float(offline["maximumPhaseMaeRegressionCp"]),
        }
        passed = all(checks.values())
        comparisons[baseline] = {
            "relativeLossImprovement": relative_loss,
            "maeImprovementCp": mae_improvement,
            "phaseMaeRegressionCp": phase_regressions,
            "bootstrap": bootstrap[baseline],
            "checks": checks,
            "passed": passed,
        }
        comparison_passes.append(passed)
    robustness_directional = (
        float(metrics["robustness"]["huberLoss"])
        < float(metrics["K0"]["huberLoss"])
    )
    robustness_zero_directional = (
        float(metrics["robustness"]["huberLoss"])
        < float(metrics["zeroResidual"]["huberLoss"])
    )
    robustness_policy = _mapping(
        offline.get("robustness"), "offline robustness policy"
    )
    gate_passed = all(comparison_passes) and (
        robustness_directional
        if bool(robustness_policy["mustImproveDirectionallyAgainstK0"])
        else True
    ) and (
        robustness_zero_directional
        if bool(robustness_policy["mustImproveDirectionallyAgainstZeroResidual"])
        else True
    )
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": TEST_REPORT_KIND,
        "createdUtc": _utc_now(),
        "generationId": protocol["generationId"],
        "testAccessClaim": _identity(access_path),
        "selection": _identity(args.selection),
        "selectedCandidateId": winner,
        "selectedNetwork": selection["selectedNetwork"],
        "robustnessNetwork": _identity(robustness_network),
        "robustnessManifest": _identity(robustness_manifest_path),
        "robustnessFloatCheckpoint": _identity(robustness_float),
        "robustnessHealth": robustness_health,
        "testBalance": _balance_audit(feature_corpus, 2, parsed["split"]),
        "targetAndLoss": parsed["targetAndLoss"],
        "aggregation": parsed["aggregation"],
        "metrics": {
            name: _public_metrics(value) for name, value in metrics.items()
        },
        "comparisons": comparisons,
        "robustnessDirectionalImprovementOverK0": robustness_directional,
        "robustnessDirectionalImprovementOverZeroResidual": (
            robustness_zero_directional
        ),
        "passed": gate_passed,
        "fallbackAllowed": False,
        "heldOutAccessCount": 1,
    }
    _atomic_json(args.output, report, no_clobber=True)
    return report


def _group_for_split(split: int, seed: int = 4989) -> str:
    for index in range(100000):
        group = f"adversarial-group-{split}-{index}"
        if deterministic_split(group, seed, 80.0, 10.0) == split:
            return group
    raise AssertionError(f"could not construct split {split} group")


def _self_test() -> None:
    initial = (
        "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
        "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
    )
    groups = [_group_for_split(split) for split in range(3)]
    with tempfile.TemporaryDirectory(
        prefix="omega-king-state-train-self-test-"
    ) as directory:
        root = Path(directory)
        corpus = root / "corpus.jsonl"
        rows = [
            {
                "groupId": groups[0],
                "ofen": initial,
                "phase": "opening",
                "targetCpStm": 10,
                "targetSemantics": "search-minus-handcrafted",
                "searchTargetCpStm": 20,
                "handcraftedCpStm": 10,
            },
            {
                "groupId": groups[1],
                "ofen": initial.replace(" w ", " b "),
                "phase": "opening",
                "targetCpStm": 12,
                "targetSemantics": "search-minus-handcrafted",
                "searchTargetCpStm": 22,
                "handcraftedCpStm": 10,
            },
        ]
        encoded = "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        )
        # The poison target occurs before groupId.  The lexical router must
        # skip it without decoding and must not copy it to the staged input.
        poison = (
            '{"targetCpStm":"SPLIT-2-POISON","groupId":'
            + json.dumps(groups[2])
            + ',"ofen":'
            + json.dumps(initial)
            + ',"phase":"opening","targetSemantics":'
            '"search-minus-handcrafted"}\n'
        )
        corpus.write_text(encoded + poison, encoding="utf-8", newline="\n")
        temporary, filtered, audit = trainer._make_protocol_pretest_input(
            [corpus],
            split_seed=4989,
            train_percent=80.0,
            validation_percent=10.0,
            group_field="groupId",
        )
        try:
            staged = filtered[0].read_text(encoding="utf-8")
            if "SPLIT-2-POISON" in staged:
                raise AssertionError("held-out poison was copied to trainer input")
            trainer._validate_residual_targets(filtered)
            _expect(audit["heldOutTargetFieldsDecoded"], 0, "self-test target reads")
            _expect(audit["rows"]["testWithheld"], 1, "self-test held-out rows")
        finally:
            temporary.cleanup()

        # Validation-only loading succeeds despite the invalid test label;
        # explicitly requesting test must fail, proving the selector did not
        # silently inspect it.
        features = _feature_corpus(
            corpus,
            split_seed=4989,
            train_percent=80.0,
            validation_percent=10.0,
        )
        validation = _load_labeled_split(
            corpus, features, wanted_split=1, target_clip=2000.0
        )
        if validation.targets.tolist() != [12.0]:
            raise AssertionError("validation loader selected the wrong row")
        try:
            _load_labeled_split(
                corpus, features, wanted_split=2, target_clip=2000.0
            )
        except ValueError as error:
            if "targetCpStm" not in str(error):
                raise
        else:
            raise AssertionError("test poison was accepted")

        validation_fixture = {
            "K0": {
                "validation": {
                    "huberLoss": 1.0,
                    "cpMae": 100.0,
                    "phase": {
                        phase: {"cpMae": 100.0} for phase in PHASES
                    },
                },
                "health": {"passed": True},
            },
            "K1": {
                "validation": {
                    "huberLoss": 0.90,
                    "cpMae": 90.0,
                    "phase": {phase: {"cpMae": 95.0} for phase in PHASES},
                },
                "health": {"passed": True},
            },
            "K2": {
                "validation": {
                    "huberLoss": 0.901,
                    "cpMae": 89.0,
                    "phase": {phase: {"cpMae": 94.0} for phase in PHASES},
                },
                "health": {"passed": True},
            },
        }
        selection_policy = {
            "minimumRelativeHuberImprovementOverK0": 0.005,
            "mustLowerCpMae": True,
            "maximumPhaseMaeRegressionCp": 5,
            "tieRelativeLoss": 0.0025,
            "tieWinner": "K1",
        }
        winner, decision = _choose_candidate(
            validation_fixture, selection_policy
        )
        if winner != "K1" or decision["tieRelativeLossObserved"] is None:
            raise AssertionError("validation tie rule did not select K1")

        sealed = root / "one-time.json"
        _atomic_json(sealed, {"first": True}, no_clobber=True)
        first_hash = _sha256(sealed)
        try:
            _atomic_json(sealed, {"second": True}, no_clobber=True)
        except ValueError:
            pass
        else:
            raise AssertionError("no-clobber test seal was overwritten")
        if _sha256(sealed) != first_hash:
            raise AssertionError("failed no-clobber changed the first seal")

        # Exercise the real K0 migrate-only implementation against all three
        # splits.  Even the deliberately invalid test target stays opaque.
        initializer = root / "architecture-2.nnue"
        trainer._blank_quantized_network(
            architecture=ARCHITECTURE_RESIDUAL
        ).write(initializer)
        k0_output = root / "K0.nnue"
        k0_manifest = root / "K0.manifest.json"
        k0_args = argparse.Namespace(
            expand_residual_to_king_state=True,
            initial_network=initializer,
            initial_float_checkpoint=None,
            float_checkpoint=None,
            input=[corpus],
            output=k0_output,
            manifest=k0_manifest,
            protocol_pretest=False,
            cpp_evaluator=None,
            candidate_id="K0",
            raw_argv=["--migrate-only", "--candidate-id", "K0"],
            seed=20260725,
            split_seed=4989,
            strict=True,
        )
        trainer._run_migrate_only(
            k0_args,
            architecture=ARCHITECTURE_KING_STATE_RESIDUAL,
            runtime_manifest=trainer._runtime_manifest(),
            input_pins=[trainer._file_pin(corpus)],
            experiment_context=None,
            golden_cases=trainer._golden_runtime_cases(),
        )
        migrated_manifest = _load_json(k0_manifest, "K0 self-test manifest")
        _expect(
            migrated_manifest["migrationParity"]["positionsChecked"],
            3,
            "K0 self-test parity count",
        )
        _expect(
            migrated_manifest["migrationParity"]["targetFieldsDecoded"],
            0,
            "K0 self-test target reads",
        )
    print("king_state_train self-test passed", flush=True)


def _default_paths() -> dict[str, Path]:
    repo = _repo_root()
    data = repo / "build-msvc" / "data-generation" / "deep-hce-v2"
    output = repo / "build-msvc" / "king-state-v1"
    return {
        "protocol": repo / "validation" / "omega-nnue-king-state-v1-protocol.json",
        "seal": data / "king-state-v1-prelabel.seal.json",
        "corpus": data / "deep-hce-v2-residual.jsonl",
        "corpus_manifest": data / "deep-hce-v2-residual.jsonl.manifest.json",
        "initializer": (
            repo
            / "build-msvc"
            / "experimental-networks"
            / "omega-nnue-residual-v3-feature.nnue"
        ),
        "cpp_evaluator": (
            repo
            / ".build-msvc-tests"
            / "Release"
            / "tests"
            / "omega_nnue.exe"
        ),
        "output_dir": output,
        "plan": output / "training-plan.json",
        "selection": output / "validation-selection.seal.json",
        "test": output / "offline-test.json",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    defaults = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="freeze exact K0/K1/K2 commands")
    plan.add_argument("--protocol", type=Path, default=defaults["protocol"])
    plan.add_argument("--seal", type=Path, default=defaults["seal"])
    plan.add_argument("--corpus", type=Path, default=defaults["corpus"])
    plan.add_argument(
        "--corpus-manifest", type=Path, default=defaults["corpus_manifest"]
    )
    plan.add_argument("--initializer", type=Path, default=defaults["initializer"])
    plan.add_argument(
        "--cpp-evaluator", type=Path, default=defaults["cpp_evaluator"]
    )
    plan.add_argument("--output-dir", type=Path, default=defaults["output_dir"])
    plan.add_argument("--plan", type=Path, default=defaults["plan"])

    run = subparsers.add_parser("run", help="execute frozen candidate commands")
    run.add_argument("--plan", type=Path, default=defaults["plan"])
    run.add_argument("--candidate", choices=("K0", "K1", "K2", "all"), required=True)

    select = subparsers.add_parser(
        "select", help="select on validation and seal the winner hash"
    )
    select.add_argument("--plan", type=Path, default=defaults["plan"])
    select.add_argument("--output", type=Path, default=defaults["selection"])

    robustness = subparsers.add_parser(
        "run-robustness", help="run selected recipe with the frozen robustness seed"
    )
    robustness.add_argument(
        "--selection", type=Path, default=defaults["selection"]
    )

    test = subparsers.add_parser(
        "test", help="consume the one-time held-out test authorization"
    )
    test.add_argument("--selection", type=Path, default=defaults["selection"])
    test.add_argument("--output", type=Path, default=defaults["test"])

    subparsers.add_parser("self-test", help="run adversarial leakage checks")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "plan":
        plan = _prepare_plan(args)
        print(f"sealed training plan: {args.plan.resolve()}")
        for candidate_id in ("K0", "K1", "K2"):
            print(
                candidate_id + ": "
                + subprocess.list2cmdline(plan["commands"][candidate_id]["argv"])
            )
    elif args.command == "run":
        _run_frozen(args)
    elif args.command == "select":
        selection = _select(args)
        print(
            f"selected {selection['selectedCandidateId']}: "
            f"{selection['selectedNetwork']['sha256']}"
        )
        print(f"selection seal: {args.output.resolve()}")
    elif args.command == "run-robustness":
        _run_robustness(args)
    elif args.command == "test":
        report = _offline_test(args)
        print(f"offline test passed={str(report['passed']).lower()}")
        print(f"offline report: {args.output.resolve()}")
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
