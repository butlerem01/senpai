#!/usr/bin/env python3
"""Seal and verify the king-state-v1 experiment before teacher labeling.

``seal`` is deliberately a pre-label operation.  It validates the frozen
K0/K1/K2 protocol and deep-HCE-v2 declaration, refuses to run after any
teacher result or corpus artifact exists, and atomically writes a provenance
manifest for the exact training and runtime implementation.

``verify-seal`` rehashes every pinned identity and revalidates the cross-file
contracts.  Teacher results are allowed to exist by then: their absence is a
historical fact recorded by the original seal, not a condition that can be
re-created after labeling starts.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
SEAL_KIND = "omega-nnue-king-state-v1-prelabel-seal"
PROTOCOL_KIND = "omega-nnue-king-state-v1-preregistration"
DEEP_FREEZE_KIND = "omega-deep-hce-v2-freeze"
SUITE_KIND = "omega-deep-hce-static-search-suite"
GENERATION_ID = "king-state-v1-deep-hce-v2"
PHASES = ("opening", "middlegame", "late", "endgame")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Any edit to the declaration requires an explicit new sealer revision before
# labels may start.  Semantic checks below remain defense in depth.
CANONICAL_PROTOCOL_SHA256 = (
    "1dab498f8373e5b25d614172afb847362bf65384c644d6d585e131530816e1f9"
)
CANONICAL_STATIC_HCE_SHA256 = (
    "b6bdd2310901319d85464a1cb570557d"
    "fceaee0ebfefba0563c9564728c27c04"
)
CANONICAL_STATIC_HCE_BYTES = 544768

HCE_OPTIONS = {
    "Threads": "1",
    "Hash": "128",
    "Ponder": "false",
    "OwnBook": "false",
    "UCI_Chess960": "false",
    "UCI_Variant": "omega",
    "UseOmegaNNUE": "false",
}

# These files jointly define corpus construction, architecture-3 training,
# residual evaluation, UCI activation, and the regression surface that guards
# that implementation.
SOURCE_INVENTORY: dict[str, dict[str, str]] = {
    "tooling": {
        "sealer": "tools/omega_nnue/king_state_v1.py",
        "deepHceV2": "tools/omega_nnue/deep_hce_v2.py",
        "labelHce": "tools/omega_nnue/label_hce.py",
        "buildResidualTargets": "tools/omega_nnue/build_residual_targets.py",
        "train": "tools/omega_nnue/train.py",
        "kingStateTraining": "tools/omega_nnue/king_state_train.py",
        "kingStateMatches": "tools/omega_nnue/king_state_matches.py",
        "omegaNnuePython": "tools/omega_nnue/omega_nnue.py",
        "symmetryAndScreen": "tools/omega_nnue/select_screen.py",
        "omegaRootSamplerProgram": (
            "tools/omega_nnue/OmegaRootSampler/Program.cs"
        ),
        "omegaRootSamplerProject": (
            "tools/omega_nnue/OmegaRootSampler/OmegaRootSampler.csproj"
        ),
    },
    "runtime": {
        "omegaNnueCpp": "src/omega_nnue.cpp",
        "omegaNnueHpp": "src/omega_nnue.hpp",
        "evaluation": "src/eval.cpp",
        "fen": "src/fen.cpp",
        "fenHeader": "src/fen.hpp",
        "omegaEvaluation": "src/omega_eval.cpp",
        "omegaEvaluationHeader": "src/omega_eval.hpp",
        "uciMain": "src/main.cpp",
        "variables": "src/var.cpp",
        "variablesHeader": "src/var.hpp",
        "makefile": "src/Makefile",
        "msvcBuild": "build-msvc.ps1",
        "position": "src/pos.cpp",
        "positionHeader": "src/pos.hpp",
    },
    "tests": {
        "nativeOmegaNnue": "tests/omega_nnue.cpp",
        "omegaEvaluation": "tests/eval_omega.cpp",
        "uciOmegaNnue": "tests/uci_nnue.ps1",
        "msvcTestDriver": "test-msvc.ps1",
    },
}


@dataclass(frozen=True)
class CanonicalPaths:
    repo: Path
    protocol: Path
    protocol_sha256: str
    deep_freeze: Path
    output_dir: Path
    suite: Path
    frozen_teacher: Path
    source_teacher: Path
    legality_config: Path
    results: Path
    corpus: Path
    corpus_manifest: Path
    static_hce_output: Path
    residual_output: Path
    seal: Path
    initializer: Path
    static_hce_evaluator: Path
    static_hce_sha256: str
    static_hce_bytes: int
    deep_module: Path
    coordination_guard: Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _canonical_paths(
    repo: Path,
    *,
    protocol_sha256: str = CANONICAL_PROTOCOL_SHA256,
    static_hce_sha256: str = CANONICAL_STATIC_HCE_SHA256,
    static_hce_bytes: int = CANONICAL_STATIC_HCE_BYTES,
) -> CanonicalPaths:
    repo = repo.expanduser().resolve()
    output_dir = repo / "build-msvc" / "data-generation" / "deep-hce-v2"
    results = output_dir / "deep-hce-v2-results.jsonl"
    return CanonicalPaths(
        repo=repo,
        protocol=(
            repo / "validation" / "omega-nnue-king-state-v1-protocol.json"
        ),
        protocol_sha256=protocol_sha256,
        deep_freeze=output_dir / "deep-hce-v2.freeze.json",
        output_dir=output_dir,
        suite=output_dir / "deep-hce-v2-suite.json",
        frozen_teacher=output_dir / "artifacts" / "senpai-hce-v2.exe",
        source_teacher=repo / "build-msvc" / "senpai-omega-nnue.exe",
        legality_config=output_dir / "legality-validation-only.json",
        results=results,
        corpus=output_dir / "deep-hce-v2-search.jsonl",
        corpus_manifest=output_dir / "deep-hce-v2-search.manifest.json",
        static_hce_output=output_dir / "deep-hce-v2-static-hce.jsonl",
        residual_output=output_dir / "deep-hce-v2-residual.jsonl",
        seal=output_dir / "king-state-v1-prelabel.seal.json",
        initializer=(
            repo / "build-msvc" / "experimental-networks"
            / "omega-nnue-residual-v3-feature.nnue"
        ),
        static_hce_evaluator=(
            repo / ".build-msvc-tests" / "Release" / "tests"
            / "omega_nnue.exe"
        ),
        static_hce_sha256=static_hce_sha256,
        static_hce_bytes=static_hce_bytes,
        deep_module=repo / "tools" / "omega_nnue" / "deep_hce_v2.py",
        coordination_guard=Path(str(results) + ".coord.lock"),
    )


def _default_paths() -> dict[str, Path]:
    paths = _canonical_paths(_repo_root())
    return {
        "repo": paths.repo,
        "protocol": paths.protocol,
        "deep_freeze": paths.deep_freeze,
        "seal": paths.seal,
        "static_hce_evaluator": paths.static_hce_evaluator,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(path: Path, *, base: Path | None = None) -> Path:
    path = path.expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.resolve()


def _expect_path(actual: Path, expected: Path, label: str) -> None:
    actual_resolved = _resolve(actual)
    expected_resolved = _resolve(expected)
    if actual_resolved != expected_resolved:
        raise ValueError(
            f"{label}: expected canonical path {expected_resolved}, "
            f"got {actual_resolved}"
        )


def _inside(path: Path, root: Path) -> bool:
    path = _resolve(path)
    root = _resolve(root)
    return path == root or root in path.parents


def _validate_canonical_layout(paths: CanonicalPaths) -> None:
    expected = _canonical_paths(
        paths.repo,
        protocol_sha256=paths.protocol_sha256,
        static_hce_sha256=paths.static_hce_sha256,
        static_hce_bytes=paths.static_hce_bytes,
    )
    for field in (
        "protocol",
        "deep_freeze",
        "output_dir",
        "suite",
        "frozen_teacher",
        "source_teacher",
        "legality_config",
        "results",
        "corpus",
        "corpus_manifest",
        "static_hce_output",
        "residual_output",
        "seal",
        "initializer",
        "static_hce_evaluator",
        "deep_module",
        "coordination_guard",
    ):
        _expect_path(
            getattr(paths, field),
            getattr(expected, field),
            f"canonical.{field}",
        )
    if not SHA256_RE.fullmatch(paths.protocol_sha256):
        raise ValueError("canonical protocol SHA-256 is malformed")
    if not SHA256_RE.fullmatch(paths.static_hce_sha256):
        raise ValueError("canonical static HCE SHA-256 is malformed")
    if paths.static_hce_bytes <= 0:
        raise ValueError("canonical static HCE byte count is invalid")

    output_files = (
        paths.deep_freeze,
        paths.suite,
        paths.frozen_teacher,
        paths.legality_config,
        paths.results,
        paths.corpus,
        paths.corpus_manifest,
        paths.static_hce_output,
        paths.residual_output,
        paths.seal,
        paths.coordination_guard,
    )
    if not all(_inside(path, paths.output_dir) for path in output_files):
        raise ValueError("canonical output path escapes the deep-HCE directory")
    repository_files = (
        paths.protocol,
        paths.deep_freeze,
        paths.output_dir,
        paths.suite,
        paths.frozen_teacher,
        paths.source_teacher,
        paths.legality_config,
        paths.results,
        paths.corpus,
        paths.corpus_manifest,
        paths.static_hce_output,
        paths.residual_output,
        paths.seal,
        paths.initializer,
        paths.static_hce_evaluator,
        paths.deep_module,
        paths.coordination_guard,
    )
    if not all(_inside(path, paths.repo) for path in repository_files):
        raise ValueError("canonical path escapes the repository")

    named_files = {
        "protocol": paths.protocol,
        "deepFreeze": paths.deep_freeze,
        "suite": paths.suite,
        "frozenTeacher": paths.frozen_teacher,
        "sourceTeacher": paths.source_teacher,
        "legalityConfig": paths.legality_config,
        "results": paths.results,
        "corpus": paths.corpus,
        "corpusManifest": paths.corpus_manifest,
        "staticHceOutput": paths.static_hce_output,
        "residualOutput": paths.residual_output,
        "seal": paths.seal,
        "initializer": paths.initializer,
        "staticHceEvaluator": paths.static_hce_evaluator,
        "deepModule": paths.deep_module,
        "coordinationGuard": paths.coordination_guard,
    }
    by_path: dict[Path, str] = {}
    for name, path in named_files.items():
        resolved = _resolve(path)
        previous = by_path.get(resolved)
        if previous is not None:
            raise ValueError(
                f"canonical path collision: {previous} and {name} both use "
                f"{resolved}"
            )
        by_path[resolved] = name


def _real_paths_from_request(
    *,
    repo: Path,
    protocol: Path,
    deep_freeze: Path,
    seal: Path,
    static_hce_evaluator: Path,
) -> CanonicalPaths:
    real = _canonical_paths(_repo_root())
    _expect_path(repo, real.repo, "repository root")
    _expect_path(protocol, real.protocol, "protocol")
    _expect_path(deep_freeze, real.deep_freeze, "deep-HCE freeze")
    _expect_path(seal, real.seal, "seal output")
    _expect_path(
        static_hce_evaluator,
        real.static_hce_evaluator,
        "static HCE evaluator",
    )
    _validate_canonical_layout(real)
    return real


def _load_deep_api(paths: CanonicalPaths) -> Any:
    """Lazy-load the exact deep-HCE module pinned by this repository."""

    module_path = _resolve(paths.deep_module)
    spec = importlib.util.spec_from_file_location(
        "_omega_king_state_v1_deep_hce_v2", module_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import deep-HCE verifier: {module_path}")
    module = importlib.util.module_from_spec(spec)
    module_dir = str(module_path.parent)
    inserted = module_dir not in sys.path
    previous_module = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    if inserted:
        sys.path.insert(0, module_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            sys.path.remove(module_dir)
        if previous_module is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_module
    for name in (
        "verify_freeze",
        "coordination_guard_path",
        "coordination_guard",
    ):
        if not callable(getattr(module, name, None)):
            raise ValueError(
                f"deep_hce_v2.py lacks required public API {name}()"
            )
    return module


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    path = _resolve(path)
    stat = path.stat()
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "sha256": _sha256(path),
    }


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        left_path = _resolve(Path(str(left["path"])))
        right_path = _resolve(Path(str(right["path"])))
        return (
            left_path == right_path
            and int(left["bytes"]) == int(right["bytes"])
            and str(left["sha256"]).lower() == str(right["sha256"]).lower()
        )
    except (KeyError, TypeError, ValueError):
        return False


def _read_pinned_json(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _resolve(path)
    payload = path.read_bytes()
    pin = {
        "path": str(path),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }
    current = _identity(path)
    if not _same_identity(pin, current):
        raise ValueError(f"{label} changed while it was read: {path}")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object: {path}")
    return value, pin


def _atomic_json_no_clobber(path: Path, value: Any) -> None:
    """Publish complete bytes atomically without ever replacing a target.

    The temporary file and destination share a directory, so the hard-link
    publication is a single-filesystem operation.  ``os.link`` is the
    no-clobber primitive: it fails atomically when another seal already owns
    the destination.
    """

    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise FileExistsError(
                f"refusing to overwrite existing seal: {path}"
            ) from error
        os.unlink(temporary)
        temporary = ""
    except BaseException:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _required(value: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in value:
        raise ValueError(f"{label}: missing {key}")
    return value[key]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected an array")
    return value


def _parse_utc(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label}: expected an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label}: invalid timestamp {value!r}") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label}: timestamp is not UTC")
    return value


def _validate_protocol(protocol: dict[str, Any]) -> None:
    _expect(protocol.get("schemaVersion"), 1, "protocol.schemaVersion")
    _expect(protocol.get("kind"), PROTOCOL_KIND, "protocol.kind")
    _expect(protocol.get("generationId"), GENERATION_ID, "protocol.generationId")
    _parse_utc(protocol.get("declaredUtc"), "protocol.declaredUtc")

    immutability = _mapping(
        _required(protocol, "immutability", "protocol"), "protocol.immutability"
    )
    for key, expected in {
        "effectiveBeforeTeacherLabels": True,
        "candidateMatrixMayChangeAfterLabels": False,
        "validationMaySelectRecipe": True,
        "testMaySelectRecipe": False,
        "matchResultsMaySelectRecipe": False,
        "failedGenerationRequiresFreshOrbitDisjointTeacherAndMatchSuites": True,
    }.items():
        _expect(
            immutability.get(key),
            expected,
            f"protocol.immutability.{key}",
        )

    architecture = _mapping(
        _required(protocol, "architecture", "protocol"), "protocol.architecture"
    )
    _expect(architecture.get("id"), 3, "protocol.architecture.id")
    _expect(
        architecture.get("name"),
        "king-state-residual",
        "protocol.architecture.name",
    )
    initializer = _mapping(
        _required(architecture, "initializer", "protocol.architecture"),
        "protocol.architecture.initializer",
    )
    initializer_path = str(initializer.get("path", ""))
    if Path(initializer_path).name != "omega-nnue-residual-v3-feature.nnue":
        raise ValueError(
            "protocol.architecture.initializer.path is not residual-v3-feature"
        )
    initializer_hash = str(initializer.get("sha256", "")).lower()
    if not SHA256_RE.fullmatch(initializer_hash):
        raise ValueError(
            "protocol.architecture.initializer.sha256 is not a SHA-256 digest"
        )
    migration = str(initializer.get("migration", "")).lower()
    for phrase in (
        "29 king buckets",
        "copy castling",
        "dense/output",
        "en-passant",
        "clock",
        "phase",
        "zero",
    ):
        if phrase not in migration:
            raise ValueError(
                "protocol architecture migration omits required contract: "
                f"{phrase}"
            )
    if "exactly reproduce architecture-2 predictions" not in str(
        initializer.get("requiredParity", "")
    ):
        raise ValueError("protocol does not require exact K0 migration parity")

    split = _mapping(
        _required(protocol, "dataSplit", "protocol"), "protocol.dataSplit"
    )
    for key, expected in {
        "groupField": "groupId",
        "splitSeed": 4989,
        "trainPercent": 80,
        "validationPercent": 10,
        "testPercent": 10,
        "collisionPolicy": "error",
        "requiredPhaseBalanceAudit": True,
        "requiredSideToMoveBalanceAudit": True,
        "maximumRelativeDeviationFromEqualPhaseCount": 0.15,
        "maximumAbsoluteSideToMoveImbalanceFraction": 0.1,
    }.items():
        _expect(split.get(key), expected, f"protocol.dataSplit.{key}")

    preparation = _mapping(
        _required(protocol, "deepHceTeacherPreparation", "protocol"),
        "protocol.deepHceTeacherPreparation",
    )
    for key, expected in {
        "targetPairs": 4096,
        "reservePairsPerPhase": 64,
        "preflightExtraPairsPerPhase": 64,
        "preflightPairsProbed": 4608,
        "maximumPreflightRejectedPairs": 32,
        "wholePairExclusionRequired": True,
        "rejectionReasonsRecorded": True,
        "replacementOrder": (
            "frozen candidate-blind rank within the same phase"
        ),
    }.items():
        _expect(
            preparation.get(key),
            expected,
            f"protocol.deepHceTeacherPreparation.{key}",
        )
    observed = _mapping(
        _required(
            preparation,
            "observedBeforeLabels",
            "protocol.deepHceTeacherPreparation",
        ),
        "protocol.deepHceTeacherPreparation.observedBeforeLabels",
    )
    for key, expected in {
        "rejectedPairs": 18,
        "teacherLabelsStarted": False,
        "scoresReadForSelection": False,
        "maximumRejectedFractionAtCap": 32 / 4608,
    }.items():
        _expect(
            observed.get(key),
            expected,
            (
                "protocol.deepHceTeacherPreparation."
                f"observedBeforeLabels.{key}"
            ),
        )

    seeds = _mapping(
        _required(protocol, "modelSeeds", "protocol"), "protocol.modelSeeds"
    )
    _expect(seeds.get("primary"), 20260725, "protocol.modelSeeds.primary")
    _expect(seeds.get("robustness"), 20260726, "protocol.modelSeeds.robustness")

    candidates = _sequence(
        _required(protocol, "candidates", "protocol"), "protocol.candidates"
    )
    if len(candidates) != 3:
        raise ValueError("protocol.candidates must contain exactly K0, K1, K2")
    by_id: dict[str, dict[str, Any]] = {}
    for index, candidate_value in enumerate(candidates):
        candidate = _mapping(
            candidate_value, f"protocol.candidates[{index}]"
        )
        candidate_id = str(candidate.get("id", ""))
        if candidate_id in by_id:
            raise ValueError(f"duplicate protocol candidate {candidate_id!r}")
        by_id[candidate_id] = candidate
    _expect(set(by_id), {"K0", "K1", "K2"}, "protocol candidate IDs")
    _expect(by_id["K0"].get("train"), False, "protocol K0.train")
    if "learningRate" in by_id["K0"] or (
        "featureTransformerLearningRateScale" in by_id["K0"]
    ):
        raise ValueError("protocol K0 must remain an untrained epoch-zero control")
    for candidate_id, ft_scale in (("K1", 0.5), ("K2", 1.0)):
        candidate = by_id[candidate_id]
        _expect(candidate.get("train"), True, f"protocol {candidate_id}.train")
        _expect(
            candidate.get("learningRate"),
            0.003,
            f"protocol {candidate_id}.learningRate",
        )
        _expect(
            candidate.get("featureTransformerLearningRateScale"),
            ft_scale,
            f"protocol {candidate_id}.featureTransformerLearningRateScale",
        )

    common = _mapping(
        _required(protocol, "commonTraining", "protocol"),
        "protocol.commonTraining",
    )
    for key, expected in {
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
        "epochSelection": (
            "minimum quantized validation Huber loss, including epoch zero"
        ),
    }.items():
        _expect(common.get(key), expected, f"protocol.commonTraining.{key}")

    selection = _mapping(
        _required(protocol, "validationSelection", "protocol"),
        "protocol.validationSelection",
    )
    for key, expected in {
        "metric": (
            "row-weighted quantized Huber loss over clipped residual targets"
        ),
        "minimumRelativeHuberImprovementOverK0": 0.005,
        "mustLowerCpMae": True,
        "maximumPhaseMaeRegressionCp": 5,
        "requireAllHealthChecks": True,
        "winner": "lowest quantized validation Huber loss",
        "tieRelativeLoss": 0.0025,
        "tieFormula": (
            "abs(K1Loss-K2Loss)/min(K1Loss,K2Loss) <= tieRelativeLoss"
        ),
        "tieRequiresFinitePositiveLosses": True,
        "tieWinner": "K1",
        "ifNoneEligible": "fail generation without consulting test",
    }.items():
        _expect(
            selection.get(key),
            expected,
            f"protocol.validationSelection.{key}",
        )
    robustness = _mapping(
        selection.get("robustnessRun"),
        "protocol.validationSelection.robustnessRun",
    )
    _expect(
        robustness.get("recipe"),
        "selected validation recipe only",
        "protocol validation robustness recipe",
    )
    _expect(
        robustness.get("seed"),
        20260726,
        "protocol validation robustness seed",
    )
    _expect(
        robustness.get("mayReplacePrimary"),
        False,
        "protocol validation robustness replacement",
    )

    test_gate = _mapping(
        _required(protocol, "offlineTestGate", "protocol"),
        "protocol.offlineTestGate",
    )
    _expect(
        test_gate.get("unsealAfterCandidateHashRecorded"),
        True,
        "protocol offline test sealing",
    )
    _expect(
        test_gate.get("primaryMetric"),
        "phase-macro group-balanced quantized Huber loss",
        "protocol offline test primary metric",
    )
    _expect(
        test_gate.get("targetAndLoss"),
        {
            "targetField": "targetCpStm",
            "targetClipCpInclusive": [-2000, 2000],
            "predictionField": (
                "quantized residual correction in side-to-move centipawns"
            ),
            "errorCp": "predictionCp-clippedTargetCp",
            "normalizerCp": 100,
            "huberDeltaNormalized": 2,
            "huberFormula": (
                "0.5*e^2 when abs(e)<=2; 2*(abs(e)-1) otherwise, "
                "where e=errorCp/100"
            ),
            "maeFormula": "abs(errorCp)",
        },
        "protocol.offlineTestGate.targetAndLoss",
    )
    _expect(
        test_gate.get("aggregation"),
        {
            "requiredFrozenPhases": list(PHASES),
            "groupPhasePurityRequired": True,
            "rowToGroup": (
                "arithmetic mean of rows for each (phase,groupId)"
            ),
            "groupToPhase": (
                "arithmetic mean of group means within each frozen phase"
            ),
            "phaseToMetric": (
                "equal arithmetic mean of the four phase means"
            ),
            "appliesTo": ["Huber loss", "centipawn MAE"],
            "missingOrEmptyPhase": "fail gate",
        },
        "protocol.offlineTestGate.aggregation",
    )
    _expect(
        test_gate.get("bootstrap"),
        {
            "paired": True,
            "phaseStratified": True,
            "unit": "unique groupId within frozen phase",
            "sampling": (
                "within each phase sample N_phase groupIds with replacement; "
                "preserve multiplicity and use identical draws for candidate, "
                "K0, and zero residual"
            ),
            "rng": "NumPy Generator PCG64",
            "iterations": 10000,
            "oneSidedConfidence": 0.95,
            "seed": 20260727,
            "statistic": (
                "(baselineLoss-candidateLoss)/baselineLoss, computed "
                "separately for each baseline"
            ),
            "lowerBound": (
                "5th percentile via numpy.quantile(method='linear')"
            ),
            "requiredLowerBoundExclusive": 0,
        },
        "protocol.offlineTestGate.bootstrap",
    )
    _expect(
        test_gate.get("baselines"),
        {
            "K0": "migrated residual-v3 epoch-zero control",
            "zeroResidual": "plain HCE with a zero correction",
        },
        "protocol.offlineTestGate.baselines",
    )
    for key, expected in {
        "comparisonPolicy": (
            "all point-estimate and bootstrap requirements must pass "
            "separately against each baseline"
        ),
        "minimumRelativeLossImprovementAgainstEach": 0.01,
        "minimumMaeImprovementCpAgainstEach": 2,
        "requirePositiveFifthPercentileAgainstEach": True,
        "maximumPhaseMaeRegressionCp": 5,
    }.items():
        _expect(
            test_gate.get(key),
            expected,
            f"protocol.offlineTestGate.{key}",
        )
    _expect(
        test_gate.get("robustness"),
        {
            "metric": (
                "the same frozen phase-macro group-balanced quantized "
                "Huber loss"
            ),
            "mustImproveDirectionallyAgainstK0": True,
            "mustImproveDirectionallyAgainstZeroResidual": True,
            "mayReplacePrimary": False,
        },
        "protocol.offlineTestGate.robustness",
    )
    _expect(
        test_gate.get("fallbackAfterTestFailure"),
        False,
        "protocol offline test fallback",
    )

    quantization = _mapping(
        _required(protocol, "quantizationAndRuntimeGate", "protocol"),
        "protocol.quantizationAndRuntimeGate",
    )
    _expect(
        quantization,
        {
            "exactPythonCppAgreement": "whole corpus",
            "wholeCorpusParity": {
                "helperIdentity": (
                    "staticHceEvaluator pinned by pre-label seal"
                ),
                "command": "--evaluate-network-stream <network>",
                "input": (
                    "one nonempty printable-ASCII Omega OFEN per stdin line; "
                    "terminal CR is trimmed"
                ),
                "output": (
                    "one bare signed integer residual correction per "
                    "accepted input line"
                ),
                "failClosed": (
                    "nonzero exit with stderr diagnostic on load, malformed "
                    "input, non-residual network, evaluation, read, or write "
                    "failure"
                ),
                "maximumInputLineBytes": 4096,
                "exactIntegerAgreementRequiredForEveryCorpusRow": True,
            },
            "maximumMeanFloatQuantizationPenaltyCp": 2,
            "maximumSingleFloatQuantizationPenaltyCp": 10,
            "maximumSaturatedDenseUnits": 0,
            "maximumDeadDenseUnits": 8,
            "deadDenseUnitsMayExceedK0": False,
            "denseActiveFractionRange": [0.2, 0.75],
            "maximumAbsoluteCorrectionCp": 2500,
            "requireFinitePredictions": True,
            "requireExpectedFileSizeAndRoundTripHash": True,
        },
        "protocol.quantizationAndRuntimeGate",
    )

    fresh = _mapping(
        _required(protocol, "freshSuiteGeneration", "protocol"),
        "protocol.freshSuiteGeneration",
    )
    _expect(
        fresh,
        {
            "rulesOnlySamplerPolicy": {
                "deterministicPrng": "SplitMix64",
                "trajectoryPairs": 2048,
                "independentTrajectoriesPerPair": 2,
                "maxPlies": 220,
                "positionsPerPhaseAndSide": 2,
                "captureSelectionPercent": 72,
            },
            "selectorPolicy": {
                "maximumRootsPerTrajectoryPairPerSuite": 1,
                "rank": (
                    "candidate-blind deterministic hash of suite seed "
                    "and root identity"
                ),
                "exactInputUniqueWithinAndAcrossSuites": True,
                "conservativeSymmetryOrbitUniqueWithinAndAcrossSuites": True,
                "trainingAndPriorSuiteOrbitsExcluded": True,
            },
            "developmentScreenSeed": 2026071901,
            "equalNodeConfirmationSeed": 2026071902,
            "equalTimeConfirmationSeed": 2026071903,
            "configSeedEqualsSuiteSeed": True,
            "schedule": {
                "inverseArrangeForSeededDotNetRandomShuffle": True,
                "balancedPairBlockSize": 4,
                "onePairFromEachPhasePerBlock": True,
                "rootSideToMoveConstantWithinBlock": True,
                "rootSideToMoveAlternatesBetweenBlocks": True,
            },
            "requiredOrbitDisjointFrom": [
                "all training inputs",
                "all prior NNUE screens",
                "all prior NNUE confirmations",
                "the other two fresh match suites",
            ],
        },
        "protocol.freshSuiteGeneration",
    )
    suite_seeds = [
        fresh.get("developmentScreenSeed"),
        fresh.get("equalNodeConfirmationSeed"),
        fresh.get("equalTimeConfirmationSeed"),
    ]
    if len(set(suite_seeds)) != 3 or any(
        isinstance(seed, bool) or not isinstance(seed, int) for seed in suite_seeds
    ):
        raise ValueError("protocol fresh-suite seeds must be three distinct integers")

    match_execution = _mapping(
        _required(protocol, "matchExecution", "protocol"),
        "protocol.matchExecution",
    )
    _expect(
        match_execution,
        {
            "engineAssignment": {
                "candidate": "engineA",
                "control": "engineB",
                "sameExecutable": True,
                "candidateUseOmegaNNUE": True,
                "candidateOmegaNNUEFile": "exact frozen candidate network",
                "controlUseOmegaNNUE": False,
            },
            "commonEngineOptions": {
                "Threads": 1,
                "Hash": 128,
                "Ponder": False,
                "OwnBook": False,
                "UCI_Chess960": False,
                "UCI_Variant": "omega",
            },
            "repeats": 1,
            "freshProcessPerGame": True,
            "maxPlies": 300,
            "absoluteMaxPlies": 400,
            "stopGraceMs": 2000,
            "bootstrapIterations": 20000,
            "searchTimeoutMs": {
                "developmentScreen": 60000,
                "equalNode": 60000,
                "equalTime": 5000,
            },
        },
        "protocol.matchExecution",
    )

    development = _mapping(
        _required(protocol, "developmentScreen", "protocol"),
        "protocol.developmentScreen",
    )
    _expect(
        development,
        {
            "promotionEvidence": False,
            "suiteSeed": 2026071901,
            "configSeed": 2026071901,
            "roots": 32,
            "rootsPerPhase": 8,
            "rootsPerPhaseAndSideToMove": 4,
            "games": 64,
            "gamesPerRoot": 2,
            "colorOrder": "AB then BA",
            "mode": "nodes",
            "nodesPerMove": 20000,
            "threads": 1,
            "hashMiB": 128,
            "ownBook": False,
            "ponder": False,
            "minimumCandidateScore": 0.4,
            "maximumSafetyFailures": 0,
        },
        "protocol.developmentScreen",
    )

    node_gate = _mapping(
        _required(protocol, "equalNodeGate", "protocol"),
        "protocol.equalNodeGate",
    )
    time_gate = _mapping(
        _required(protocol, "equalTimeGate", "protocol"),
        "protocol.equalTimeGate",
    )
    _expect(
        node_gate,
        {
            "independentSuite": True,
            "suiteSeed": 2026071902,
            "configSeed": 2026071902,
            "maximumPairs": 128,
            "pairsPerPhase": 32,
            "pairsPerPhaseAndSideToMove": 16,
            "maximumGames": 256,
            "gamesPerPair": 2,
            "colorOrder": "AB then BA",
            "mode": "nodes",
            "nodesPerMove": 50000,
            "minimumPairsBeforeDecision": 64,
            "nullElo": 10,
            "alpha": 0.05,
            "beta": 0.1,
            "promotionEValue": 20,
            "futilityEValue": 10,
            "balancedBlockChecksOnly": True,
            "maximumSafetyFailures": 0,
        },
        "protocol.equalNodeGate",
    )
    _expect(
        time_gate,
        {
            "independentSuite": True,
            "suiteSeed": 2026071903,
            "configSeed": 2026071903,
            "maximumPairs": 128,
            "pairsPerPhase": 32,
            "pairsPerPhaseAndSideToMove": 16,
            "maximumGames": 256,
            "gamesPerPair": 2,
            "colorOrder": "AB then BA",
            "mode": "movetime",
            "moveTimeMs": 1000,
            "oneGameAtATime": True,
            "idleMachineRequired": True,
            "minimumPairsBeforeDecision": 64,
            "nullElo": 10,
            "alpha": 0.05,
            "beta": 0.1,
            "promotionEValue": 20,
            "futilityEValue": 10,
            "balancedBlockChecksOnly": True,
            "maximumSafetyFailures": 0,
            "record": ["nodes", "depth", "nps", "deadline compliance"],
            "idleAttestation": {
                "required": True,
                "kind": "omega-equal-time-idle-attestation-v1",
                "runId": (
                    "must exactly equal the frozen equal-time config runId"
                ),
                "idleMachine": True,
                "oneGameAtATime": True,
                "concurrentMatchProcesses": 1,
                "createdUtc": "required ISO-8601 UTC timestamp",
                "operator": "optional nonempty string",
            },
        },
        "protocol.equalTimeGate",
    )

    clearly = _mapping(
        _required(protocol, "clearlySuperiorDefinition", "protocol"),
        "protocol.clearlySuperiorDefinition",
    )
    _expect(
        clearly,
        {
            "offlineTestGatePasses": True,
            "developmentScreenPasses": True,
            "equalNodeDecision": "promote",
            "equalTimeDecision": "promote",
            "allSafetyGatesPass": True,
            "continueAtMaximumPairsMeans": "inconclusive, not promotion",
        },
        "protocol.clearlySuperiorDefinition",
    )


def _verify_embedded_identity(
    value: Any, label: str, *, base: Path | None = None
) -> tuple[Path, dict[str, Any]]:
    embedded = _mapping(value, label)
    try:
        path = _resolve(Path(str(embedded["path"])), base=base)
        expected = {
            "path": str(path),
            "bytes": int(embedded["bytes"]),
            "sha256": str(embedded["sha256"]).lower(),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{label}: invalid file identity") from error
    if not SHA256_RE.fullmatch(expected["sha256"]):
        raise ValueError(f"{label}: invalid SHA-256 digest")
    current = _identity(path)
    if not _same_identity(expected, current):
        raise ValueError(
            f"{label} changed: expected {expected['sha256']}, "
            f"got {current['sha256']}"
        )
    return path, current


def _teacher_artifacts(
    deep: Mapping[str, Any],
    freeze_path: Path,
    paths: CanonicalPaths,
) -> list[dict[str, Any]]:
    outputs = _mapping(
        _required(deep, "outputs", "deep freeze"), "deep freeze.outputs"
    )
    _expect_path(
        Path(str(_required(outputs, "directory", "deep freeze.outputs"))),
        paths.output_dir,
        "deep freeze.outputs.directory",
    )
    roles = (
        ("teacherResults", "results", paths.results),
        ("teacherCorpus", "corpus", paths.corpus),
        ("teacherCorpusManifest", "corpusManifest", paths.corpus_manifest),
    )
    artifacts: list[dict[str, Any]] = []
    for role, key, expected in roles:
        raw = _required(outputs, key, "deep freeze.outputs")
        path = _resolve(Path(str(raw)), base=freeze_path.parent)
        _expect_path(path, expected, f"deep freeze.outputs.{key}")
        artifacts.append({"role": role, "path": str(path), "existed": False})
    artifacts.extend(
        [
            {
                "role": "staticHceLabels",
                "path": str(_resolve(paths.static_hce_output)),
                "existed": False,
            },
            {
                "role": "residualTrainingCorpus",
                "path": str(_resolve(paths.residual_output)),
                "existed": False,
            },
        ]
    )
    return artifacts


def _coordination_record(paths: CanonicalPaths) -> dict[str, Any]:
    return {
        "path": str(_resolve(paths.coordination_guard)),
        "convention": "Path(str(results_path) + '.coord.lock')",
        "heldContinuouslyFromBeforeActivityCheckThroughPublication": True,
        "owner": "king-state-v1-prelabel-seal",
    }


def _validate_deep_freeze(
    deep: dict[str, Any],
    freeze_path: Path,
    paths: CanonicalPaths,
    verified: Mapping[str, Any],
) -> dict[str, Any]:
    verified = _mapping(verified, "deep_hce_v2.verify_freeze result")
    _expect_path(
        Path(str(_required(verified, "lockPath", "strict freeze result"))),
        paths.deep_freeze,
        "strict freeze lockPath",
    )
    _expect(
        _required(verified, "lock", "strict freeze result"),
        deep,
        "strict freeze lock object",
    )
    strict_lock_identity = _mapping(
        _required(verified, "lockIdentity", "strict freeze result"),
        "strict freeze lockIdentity",
    )
    current_lock_identity = _identity(freeze_path)
    if not _same_identity(strict_lock_identity, current_lock_identity):
        raise ValueError("strict freeze verifier returned a stale lock identity")
    _expect(
        str(_required(verified, "lockSha256", "strict freeze result")).lower(),
        current_lock_identity["sha256"],
        "strict freeze lock SHA-256",
    )
    _expect_path(
        Path(str(_required(verified, "resultsPath", "strict freeze result"))),
        paths.results,
        "strict freeze resultsPath",
    )
    _expect_path(
        Path(
            str(
                _required(
                    verified,
                    "coordinationGuardPath",
                    "strict freeze result",
                )
            )
        ),
        paths.coordination_guard,
        "strict freeze coordinationGuardPath",
    )

    _expect(deep.get("schemaVersion"), 2, "deep freeze.schemaVersion")
    _expect(deep.get("kind"), DEEP_FREEZE_KIND, "deep freeze.kind")
    _parse_utc(deep.get("createdUtc"), "deep freeze.createdUtc")

    search = _mapping(
        _required(deep, "searchContract", "deep freeze"),
        "deep freeze.searchContract",
    )
    _expect(search.get("mode"), "fixed nodes", "deep freeze search mode")
    _expect(search.get("nodes"), 100000, "deep freeze search nodes")
    _expect(
        search.get("freshProcessPerPosition"),
        True,
        "deep freeze fresh-process policy",
    )
    _expect(search.get("options"), HCE_OPTIONS, "deep freeze HCE options")
    _expect(
        search.get("targetIsLastCompletedExactIteration"),
        True,
        "deep freeze exact-iteration target",
    )

    selection_contract = _mapping(
        _required(deep, "selectionContract", "deep freeze"),
        "deep freeze.selectionContract",
    )
    for key, expected in {
        "candidateBlind": True,
        "rulesOnlySourcesUseEvaluator": False,
        "scoresReadForRanking": False,
        "gameResultsReadForRanking": False,
        "bestMovesReadForRanking": False,
        "pvsReadForRanking": False,
        "oppositeSideToMoveWithinPair": True,
        "distinctSymmetryOrbitsWithinPair": True,
        "distinctSymmetryOrbitsAcrossSuite": True,
        "phaseBalanced": True,
        "targetPairsPerPhase": 1024,
        "reservePairsPerPhase": 64,
        "preflightExtraPairsPerPhase": 64,
        "preflightWholePairExclusion": True,
        "preflightRejectedPairSanityCap": 32,
        "replacementPolicy": (
            "Within each phase, consume frozen pair order. A pair is usable "
            "only when both roots produce valid fixed-node exact-cp labels; "
            "otherwise advance to the next frozen reserve pair. Never rank "
            "or choose replacements by score value, PV, or game result."
        ),
    }.items():
        _expect(
            selection_contract.get(key),
            expected,
            f"deep freeze.selectionContract.{key}",
        )

    selection = _mapping(
        _required(deep, "selection", "deep freeze"), "deep freeze.selection"
    )
    for key, expected in {
        "selectedPairs": 4096,
        "selectedRoots": 8192,
        "frozenCandidatePairs": 4352,
        "frozenCandidateRoots": 8704,
        "preflightCandidatePairs": 4608,
        "preflightRejectedPairs": 18,
        "preflightRejectedPairSanityCap": 32,
    }.items():
        _expect(selection.get(key), expected, f"deep freeze.selection.{key}")
    rejected_root_count = selection.get("preflightRejectedRoots")
    if (
        isinstance(rejected_root_count, bool)
        or not isinstance(rejected_root_count, int)
        or not 18 <= rejected_root_count <= 36
    ):
        raise ValueError(
            "deep freeze.selection.preflightRejectedRoots must be an integer "
            "from 18 through 36"
        )
    _expect(
        selection.get("phaseCounts"),
        {phase: 2048 for phase in PHASES},
        "deep freeze selected phase counts",
    )
    _expect(
        selection.get("sideToMoveCounts"),
        {"b": 4096, "w": 4096},
        "deep freeze side-to-move counts",
    )
    validation = _mapping(
        _required(selection, "validation", "deep freeze.selection"),
        "deep freeze.selection.validation",
    )
    acceptance = _mapping(
        _required(
            validation,
            "senpaiOfenAcceptance",
            "deep freeze.selection.validation",
        ),
        "deep freeze.selection.validation.senpaiOfenAcceptance",
    )
    for key, expected in {
        "passed": True,
        "searchMode": "depth",
        "searchDepthPerPosition": 1,
        "nodesOneFalseTerminalRegressionAvoided": True,
        "syntacticScoreBearingInfoRequired": True,
        "strictTeacherCompletedDepthPolicyApplied": False,
        "candidateScoresUsedForSelection": 0,
        "positionsProbed": 9216,
        "positionsRejected": rejected_root_count,
        "pairsProbed": 4608,
        "pairsRejected": 18,
        "maximumRejectedPairs": 32,
        "extraPairsPerPhase": 64,
        "finalPairsAccepted": 4352,
        "finalPositionsAccepted": 8704,
        "finalPoolContainsRejectedRoot": False,
    }.items():
        _expect(
            acceptance.get(key),
            expected,
            (
                "deep freeze.selection.validation."
                f"senpaiOfenAcceptance.{key}"
            ),
        )
    rejected_pairs = _sequence(
        _required(
            acceptance,
            "rejectedPairs",
            "deep freeze Senpai acceptance",
        ),
        "deep freeze Senpai acceptance rejectedPairs",
    )
    if len(rejected_pairs) != 18:
        raise ValueError(
            "deep freeze Senpai acceptance must record exactly 18 rejected "
            "whole pairs"
        )
    excluded_root_ids: set[str] = set()
    direct_rejections: dict[str, str] = {}
    for index, rejected_pair_value in enumerate(rejected_pairs):
        rejected_pair = _mapping(
            rejected_pair_value,
            f"deep freeze rejectedPairs[{index}]",
        )
        roots = _sequence(
            _required(
                rejected_pair,
                "roots",
                f"deep freeze rejectedPairs[{index}]",
            ),
            f"deep freeze rejectedPairs[{index}].roots",
        )
        if len(roots) != 2:
            raise ValueError(
                f"deep freeze rejectedPairs[{index}] must record both roots"
            )
        for root_index, root_value in enumerate(roots):
            root = _mapping(
                root_value,
                (
                    f"deep freeze rejectedPairs[{index}]."
                    f"roots[{root_index}]"
                ),
            )
            root_id = str(root.get("rootId", ""))
            reason = str(root.get("reason", ""))
            direct = root.get("directProtocolRejection")
            if (
                not root_id
                or root_id in excluded_root_ids
                or not reason
                or not isinstance(direct, bool)
            ):
                raise ValueError(
                    "deep freeze rejected pair roots require unique IDs and "
                    "nonempty reasons with direct-rejection flags"
                )
            reason_hash = str(root.get("reasonSha256", "")).lower()
            if (
                not SHA256_RE.fullmatch(reason_hash)
                or hashlib.sha256(reason.encode("utf-8")).hexdigest()
                != reason_hash
            ):
                raise ValueError(
                    "deep freeze rejected pair root has an invalid reason hash"
                )
            excluded_root_ids.add(root_id)
            if direct:
                direct_rejections[root_id] = reason
            elif reason != (
                "whole-pair companion excluded after paired-root rejection"
            ):
                raise ValueError(
                    "deep freeze whole-pair companion has the wrong reason"
                )
    if (
        len(excluded_root_ids) != 36
        or len(direct_rejections) != rejected_root_count
    ):
        raise ValueError(
            "deep freeze whole-pair exclusion inventory differs from its "
            "direct rejected-root count"
        )
    raw_rejections = _sequence(
        _required(
            acceptance,
            "rejectedRoots",
            "deep freeze Senpai acceptance",
        ),
        "deep freeze Senpai acceptance rejectedRoots",
    )
    if len(raw_rejections) != rejected_root_count:
        raise ValueError(
            "deep freeze raw rejected-root inventory has the wrong length"
        )
    raw_by_id: dict[str, str] = {}
    for index, raw_value in enumerate(raw_rejections):
        raw = _mapping(
            raw_value,
            f"deep freeze raw rejectedRoots[{index}]",
        )
        root_id = str(raw.get("rootId", ""))
        reason = str(raw.get("reason", ""))
        if not root_id or root_id in raw_by_id or not reason:
            raise ValueError(
                "deep freeze raw rejected roots require unique IDs and reasons"
            )
        raw_by_id[root_id] = reason
    if raw_by_id != direct_rejections:
        raise ValueError(
            "deep freeze direct and raw rejected-root inventories differ"
        )

    frozen = _mapping(
        _required(deep, "freeze", "deep freeze"), "deep freeze.freeze"
    )
    source_engine_path, source_engine = _verify_embedded_identity(
        frozen.get("sourceEngine"), "deep freeze source HCE engine"
    )
    _expect_path(
        source_engine_path, paths.source_teacher, "deep freeze source HCE engine"
    )
    engine_path, engine = _verify_embedded_identity(
        frozen.get("engineExecutable"), "deep freeze copied HCE engine"
    )
    _expect_path(
        engine_path, paths.frozen_teacher, "deep freeze copied HCE engine"
    )
    if (
        source_engine["bytes"] != engine["bytes"]
        or source_engine["sha256"] != engine["sha256"]
    ):
        raise ValueError("deep freeze source and copied HCE engines differ")
    if source_engine_path == engine_path:
        raise ValueError("deep freeze did not copy the teacher executable")

    selector_path, selector = _verify_embedded_identity(
        frozen.get("selector"), "deep freeze selector"
    )
    _expect_path(selector_path, paths.deep_module, "deep freeze selector")
    omega_path, omega_module = _verify_embedded_identity(
        frozen.get("omegaNnueModule"), "deep freeze omega_nnue.py"
    )
    _expect_path(
        omega_path,
        paths.repo / SOURCE_INVENTORY["tooling"]["omegaNnuePython"],
        "deep freeze omega_nnue.py",
    )
    symmetry_path, symmetry = _verify_embedded_identity(
        frozen.get("symmetryModule"), "deep freeze symmetry module"
    )
    _expect_path(
        symmetry_path,
        paths.repo / SOURCE_INVENTORY["tooling"]["symmetryAndScreen"],
        "deep freeze symmetry module",
    )
    suite_path, suite_pin = _verify_embedded_identity(
        selection.get("suite"), "deep freeze teacher suite"
    )
    _expect_path(suite_path, paths.suite, "deep freeze teacher suite")
    legality_path, legality_pin = _verify_embedded_identity(
        selection.get("legalityValidationConfig"),
        "deep freeze legality-validation config",
    )
    _expect_path(
        legality_path,
        paths.legality_config,
        "deep freeze legality-validation config",
    )

    for key, expected_pin in (
        ("suiteIdentity", suite_pin),
        ("engineIdentity", engine),
    ):
        strict_pin = _mapping(
            _required(verified, key, "strict freeze result"),
            f"strict freeze {key}",
        )
        if not _same_identity(strict_pin, expected_pin):
            raise ValueError(
                f"strict freeze {key} differs from the embedded identity"
            )
    _expect_path(
        Path(str(_required(verified, "suitePath", "strict freeze result"))),
        suite_path,
        "strict freeze suitePath",
    )
    _expect_path(
        Path(str(_required(verified, "enginePath", "strict freeze result"))),
        engine_path,
        "strict freeze enginePath",
    )
    _expect(
        _required(verified, "suite", "strict freeze result"),
        json.loads(suite_path.read_text(encoding="utf-8")),
        "strict freeze suite object",
    )

    suite, stable_suite_pin = _read_pinned_json(suite_path, "teacher suite")
    if not _same_identity(suite_pin, stable_suite_pin):
        raise ValueError("teacher suite changed while the seal was prepared")
    _expect(suite.get("schemaVersion"), 1, "teacher suite.schemaVersion")
    _expect(suite.get("kind"), SUITE_KIND, "teacher suite.kind")
    for key, expected in {
        "fixedNodes": 100000,
        "targetRootPairs": 4096,
        "targetPairsPerPhase": 1024,
        "reservePairsPerPhase": 64,
        "candidateRootPairs": 4352,
    }.items():
        _expect(suite.get(key), expected, f"teacher suite.{key}")
    pairs = _sequence(suite.get("pairs"), "teacher suite.pairs")
    positions = _sequence(suite.get("positions"), "teacher suite.positions")
    if len(pairs) != 4352 or len(positions) != 8704:
        raise ValueError(
            "teacher suite pair/position lengths do not match its frozen quotas"
        )

    return {
        "teacherSuite": suite_pin,
        "frozenTeacherEngine": engine,
        "sourceTeacherEngine": source_engine,
        "teacherLegalityConfig": legality_pin,
        "deepSelector": selector,
        "deepOmegaNnueModule": omega_module,
        "deepSymmetryModule": symmetry,
        "paths": {
            "teacherSuite": str(suite_path),
            "frozenTeacherEngine": str(engine_path),
            "sourceTeacherEngine": str(source_engine_path),
            "teacherLegalityConfig": str(legality_path),
            "deepSelector": str(selector_path),
            "deepOmegaNnueModule": str(omega_path),
            "deepSymmetryModule": str(symmetry_path),
        },
        "teacherArtifacts": _teacher_artifacts(deep, freeze_path, paths),
    }


def _pin_sources(repo: Path) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for group, entries in SOURCE_INVENTORY.items():
        result[group] = {
            name: _identity(repo / relative)
            for name, relative in sorted(entries.items())
        }
    return result


def _walk_identities(
    value: Any, prefix: str = "identities"
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, dict):
        if {"path", "bytes", "sha256"}.issubset(value):
            yield prefix, value
            return
        for key in sorted(value):
            yield from _walk_identities(value[key], f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_identities(item, f"{prefix}[{index}]")


def _verify_all_pins(identities: Mapping[str, Any]) -> None:
    found = 0
    paths: dict[Path, str] = {}
    for label, pin in _walk_identities(identities):
        found += 1
        path, current = _verify_embedded_identity(pin, label)
        if not _same_identity(pin, current):
            raise ValueError(f"{label}: identity mismatch")
        previous = paths.get(path)
        if previous is not None:
            raise ValueError(
                f"identity path collision: {previous} and {label} both pin "
                f"{path}"
            )
        paths[path] = label
    if found == 0:
        raise ValueError("seal contains no file identities")


def _check_teacher_absence(artifacts: Sequence[Mapping[str, Any]]) -> None:
    existing = [
        str(artifact["path"])
        for artifact in artifacts
        if _resolve(Path(str(artifact["path"]))).exists()
    ]
    if existing:
        raise ValueError(
            "refusing to declare a pre-label seal after teacher activity; "
            "already exists: " + ", ".join(existing)
        )


def _expected_source_paths(repo: Path) -> dict[str, dict[str, str]]:
    return {
        group: {
            name: str(_resolve(repo / relative))
            for name, relative in sorted(entries.items())
        }
        for group, entries in SOURCE_INVENTORY.items()
    }


def _seal_contracts(paths: CanonicalPaths) -> dict[str, Any]:
    return {
        "candidateIds": ["K0", "K1", "K2"],
        "candidateSelectionIncluded": False,
        "canonicalProtocolSha256": paths.protocol_sha256,
        "canonicalStaticHceSha256": paths.static_hce_sha256,
        "canonicalStaticHceBytes": paths.static_hce_bytes,
        "deepHceTargetPairs": 4096,
        "deepHceTargetRoots": 8192,
        "deepHcePreflightPairsProbed": 4608,
        "deepHceObservedPrelabelRejectedPairs": 18,
        "deepHceMaximumPreflightRejectedPairs": 32,
        "deepHceWholePairExclusionRequired": True,
        "teacherNodesPerRoot": 100000,
        "splitGroupField": "groupId",
        "splitSeed": 4989,
        "splitMaximumRelativePhaseDeviation": 0.15,
        "splitMaximumAbsoluteSideToMoveImbalanceFraction": 0.1,
        "offlineBootstrapIterations": 10000,
        "offlineBootstrapSeed": 20260727,
        "freshMatchTrajectoryPairs": 2048,
        "matchBootstrapIterations": 20000,
        "labelsPermittedOnlyAfterSeal": True,
        "staticHceEvaluatorRequiredBeforeSeal": True,
        "wholeCorpusParityEvaluatorRequiredBeforeSeal": True,
        "publication": "atomic hard-link, no clobber",
    }


def _seal(
    paths: CanonicalPaths,
    *,
    created_utc: str | None = None,
    deep_api: Any | None = None,
) -> dict[str, Any]:
    _validate_canonical_layout(paths)
    api = deep_api if deep_api is not None else _load_deep_api(paths)
    api_guard_path = _resolve(api.coordination_guard_path(paths.results))
    _expect_path(
        api_guard_path,
        paths.coordination_guard,
        "deep-HCE coordination guard",
    )
    # Hashing the declaration is not an activity check.  It gives both
    # coordinators the same freeze identity in the guard record.
    lock_sha256 = _sha256(paths.deep_freeze)
    with api.coordination_guard(
        paths.results,
        owner="king-state-v1-prelabel-seal",
        lock_sha256=lock_sha256,
    ):
        if not _resolve(paths.coordination_guard).is_file():
            raise ValueError(
                "deep-HCE coordination guard did not publish its lock file"
            )

        protocol, protocol_pin = _read_pinned_json(
            paths.protocol, "protocol"
        )
        _expect(
            protocol_pin["sha256"],
            paths.protocol_sha256,
            "canonical protocol SHA-256",
        )
        _validate_protocol(protocol)

        strict = api.verify_freeze(paths.deep_freeze)
        deep, deep_pin = _read_pinned_json(
            paths.deep_freeze, "deep-HCE freeze"
        )
        deep_context = _validate_deep_freeze(
            deep, paths.deep_freeze, paths, strict
        )

        artifacts = deep_context["teacherArtifacts"]
        _check_teacher_absence(artifacts)

        initializer = _mapping(
            _mapping(
                protocol["architecture"], "protocol.architecture"
            )["initializer"],
            "protocol.architecture.initializer",
        )
        initializer_path = _resolve(
            Path(str(initializer["path"])), base=paths.protocol.parent
        )
        _expect_path(
            initializer_path,
            paths.initializer,
            "residual-v3 initializer",
        )
        initializer_pin = _identity(initializer_path)
        _expect(
            initializer_pin["sha256"],
            str(initializer["sha256"]).lower(),
            "residual-v3 initializer SHA-256",
        )

        static_hce_evaluator_pin = _identity(paths.static_hce_evaluator)
        _expect(
            static_hce_evaluator_pin["sha256"],
            paths.static_hce_sha256,
            "canonical static HCE evaluator SHA-256",
        )
        _expect(
            static_hce_evaluator_pin["bytes"],
            paths.static_hce_bytes,
            "canonical static HCE evaluator byte count",
        )
        source_pins = _pin_sources(paths.repo)
        expected_source_paths = _expected_source_paths(paths.repo)
        _expect(
            source_pins["tooling"]["deepHceV2"]["path"],
            deep_context["paths"]["deepSelector"],
            "deep freeze selector path",
        )
        _expect(
            source_pins["tooling"]["omegaNnuePython"]["path"],
            deep_context["paths"]["deepOmegaNnueModule"],
            "deep freeze omega_nnue.py path",
        )
        _expect(
            source_pins["tooling"]["symmetryAndScreen"]["path"],
            deep_context["paths"]["deepSymmetryModule"],
            "deep freeze symmetry module path",
        )
        for source_name, deep_name in (
            ("deepHceV2", "deepSelector"),
            ("omegaNnuePython", "deepOmegaNnueModule"),
            ("symmetryAndScreen", "deepSymmetryModule"),
        ):
            if not _same_identity(
                source_pins["tooling"][source_name],
                deep_context[deep_name],
            ):
                raise ValueError(f"{source_name} differs from the deep freeze")

        created = _parse_utc(
            created_utc if created_utc is not None else _utc_now(),
            "seal.createdUtc",
        )
        manifest: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": SEAL_KIND,
            "createdUtc": created,
            "generationId": GENERATION_ID,
            "declaration": {
                "effectiveBeforeTeacherLabels": True,
                "teacherArtifactsAbsentAtDeclaration": True,
                "teacherArtifacts": artifacts,
                "historicalFact": (
                    "Every listed teacher result, corpus, static-HCE label, "
                    "and residual output was absent while the shared exclusive "
                    "coordination guard was held through atomic publication. "
                    "verify-seal preserves that declaration while allowing "
                    "those artifacts to exist later."
                ),
            },
            "coordination": _coordination_record(paths),
            "contracts": _seal_contracts(paths),
            "repositoryRoot": str(_resolve(paths.repo)),
            "sourceInventory": expected_source_paths,
            "identities": {
                "protocol": protocol_pin,
                "deepHceFreeze": deep_pin,
                "teacherSuite": deep_context["teacherSuite"],
                "frozenTeacherEngine": deep_context["frozenTeacherEngine"],
                "sourceTeacherEngine": deep_context["sourceTeacherEngine"],
                "teacherLegalityConfig": deep_context[
                    "teacherLegalityConfig"
                ],
                "residualV3Initializer": initializer_pin,
                "staticHceEvaluator": static_hce_evaluator_pin,
                **source_pins,
            },
        }

        # The shared guard remains held across both final checks and the
        # single no-clobber publication.
        _verify_all_pins(manifest["identities"])
        _check_teacher_absence(artifacts)
        _atomic_json_no_clobber(paths.seal, manifest)
        return manifest


def _verify_source_inventory(seal: Mapping[str, Any], repo: Path) -> None:
    expected = _expected_source_paths(repo)
    _expect(seal.get("sourceInventory"), expected, "seal.sourceInventory")
    identities = _mapping(seal.get("identities"), "seal.identities")
    for group, entries in expected.items():
        pins = _mapping(identities.get(group), f"seal.identities.{group}")
        _expect(
            set(pins),
            set(entries),
            f"seal.identities.{group} inventory",
        )
        for name, expected_path in entries.items():
            pin = _mapping(
                pins.get(name), f"seal.identities.{group}.{name}"
            )
            _expect(
                str(_resolve(Path(str(pin.get("path", ""))))),
                expected_path,
                f"seal.identities.{group}.{name}.path",
            )


def _verify_seal(
    paths: CanonicalPaths,
    *,
    deep_api: Any | None = None,
) -> dict[str, Any]:
    _validate_canonical_layout(paths)
    seal, _ = _read_pinned_json(paths.seal, "pre-label seal")
    _expect(seal.get("schemaVersion"), SCHEMA_VERSION, "seal.schemaVersion")
    _expect(seal.get("kind"), SEAL_KIND, "seal.kind")
    _expect(seal.get("generationId"), GENERATION_ID, "seal.generationId")
    _parse_utc(seal.get("createdUtc"), "seal.createdUtc")
    _expect(
        seal.get("repositoryRoot"),
        str(_resolve(paths.repo)),
        "seal.repositoryRoot",
    )
    _expect(
        seal.get("coordination"),
        _coordination_record(paths),
        "seal.coordination",
    )
    contracts = _mapping(seal.get("contracts"), "seal.contracts")
    _expect(
        contracts,
        _seal_contracts(paths),
        "seal contracts",
    )

    declaration = _mapping(
        seal.get("declaration"), "seal.declaration"
    )
    _expect(
        declaration.get("effectiveBeforeTeacherLabels"),
        True,
        "seal pre-label effectiveness",
    )
    _expect(
        declaration.get("teacherArtifactsAbsentAtDeclaration"),
        True,
        "seal historical label absence",
    )
    declared_artifacts = _sequence(
        declaration.get("teacherArtifacts"),
        "seal.declaration.teacherArtifacts",
    )
    for index, artifact_value in enumerate(declared_artifacts):
        artifact = _mapping(
            artifact_value, f"seal.declaration.teacherArtifacts[{index}]"
        )
        _expect(
            artifact.get("existed"),
            False,
            f"seal.declaration.teacherArtifacts[{index}].existed",
        )

    identities = _mapping(seal.get("identities"), "seal.identities")
    required_top_level = {
        "protocol",
        "deepHceFreeze",
        "teacherSuite",
        "frozenTeacherEngine",
        "sourceTeacherEngine",
        "teacherLegalityConfig",
        "residualV3Initializer",
        "staticHceEvaluator",
        *SOURCE_INVENTORY.keys(),
    }
    _expect(set(identities), required_top_level, "seal identity groups")
    _verify_all_pins(identities)

    _verify_source_inventory(seal, paths.repo)

    protocol_path = Path(str(_mapping(
        identities["protocol"], "seal.identities.protocol"
    )["path"]))
    _expect_path(protocol_path, paths.protocol, "sealed protocol path")
    protocol, protocol_pin = _read_pinned_json(protocol_path, "sealed protocol")
    _expect(
        protocol_pin["sha256"],
        paths.protocol_sha256,
        "sealed canonical protocol SHA-256",
    )
    _validate_protocol(protocol)
    if not _same_identity(protocol_pin, identities["protocol"]):
        raise ValueError("sealed protocol identity is inconsistent")

    deep_path = Path(str(_mapping(
        identities["deepHceFreeze"], "seal.identities.deepHceFreeze"
    )["path"]))
    _expect_path(deep_path, paths.deep_freeze, "sealed deep-HCE freeze path")
    api = deep_api if deep_api is not None else _load_deep_api(paths)
    _expect_path(
        api.coordination_guard_path(paths.results),
        paths.coordination_guard,
        "deep-HCE coordination guard",
    )
    strict = api.verify_freeze(paths.deep_freeze)
    deep, deep_pin = _read_pinned_json(deep_path, "sealed deep-HCE freeze")
    if not _same_identity(deep_pin, identities["deepHceFreeze"]):
        raise ValueError("sealed deep-HCE freeze identity is inconsistent")
    deep_context = _validate_deep_freeze(
        deep, _resolve(deep_path), paths, strict
    )

    for name in (
        "teacherSuite",
        "frozenTeacherEngine",
        "sourceTeacherEngine",
        "teacherLegalityConfig",
    ):
        if not _same_identity(identities[name], deep_context[name]):
            raise ValueError(f"seal {name} is inconsistent with the deep freeze")

    expected_artifacts = deep_context["teacherArtifacts"]
    _expect(
        declared_artifacts,
        expected_artifacts,
        "seal historical teacher-artifact inventory",
    )

    initializer = _mapping(
        _mapping(protocol["architecture"], "protocol.architecture")["initializer"],
        "protocol initializer",
    )
    initializer_path = _resolve(
        Path(str(initializer["path"])), base=_resolve(protocol_path).parent
    )
    _expect_path(
        initializer_path, paths.initializer, "sealed residual-v3 initializer"
    )
    _expect(
        str(initializer_path),
        str(identities["residualV3Initializer"]["path"]),
        "sealed residual-v3 initializer path",
    )
    _expect(
        str(initializer["sha256"]).lower(),
        str(identities["residualV3Initializer"]["sha256"]).lower(),
        "sealed residual-v3 initializer hash",
    )
    _expect_path(
        Path(str(identities["staticHceEvaluator"]["path"])),
        paths.static_hce_evaluator,
        "sealed static HCE evaluator",
    )
    _expect(
        str(identities["staticHceEvaluator"]["sha256"]).lower(),
        paths.static_hce_sha256,
        "sealed static HCE evaluator SHA-256",
    )
    _expect(
        int(identities["staticHceEvaluator"]["bytes"]),
        paths.static_hce_bytes,
        "sealed static HCE evaluator byte count",
    )

    current = [
        {
            "role": artifact["role"],
            "path": artifact["path"],
            "existsNow": _resolve(Path(str(artifact["path"]))).exists(),
        }
        for artifact in expected_artifacts
    ]
    return {
        "seal": str(_resolve(paths.seal)),
        "generationId": GENERATION_ID,
        "teacherArtifactsAbsentAtDeclaration": True,
        "currentTeacherArtifacts": current,
        "identitiesVerified": sum(
            1 for _ in _walk_identities(identities)
        ),
    }


class _SyntheticDeepApi:
    """Minimal strict public API used only by the adversarial self-test."""

    @staticmethod
    def coordination_guard_path(results_path: Path) -> Path:
        return Path(str(_resolve(results_path)) + ".coord.lock")

    @staticmethod
    @contextmanager
    def coordination_guard(
        results_path: Path,
        owner: str,
        lock_sha256: str | None = None,
    ) -> Iterable[None]:
        guard = _SyntheticDeepApi.coordination_guard_path(results_path)
        guard.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
        except FileExistsError as error:
            raise ValueError(
                f"another coordinator owns {guard}"
            ) from error
        try:
            payload = json.dumps(
                {
                    "owner": owner,
                    "lockSha256": lock_sha256,
                },
                sort_keys=True,
            ).encode("utf-8")
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            guard.unlink(missing_ok=True)

    @staticmethod
    def verify_freeze(lock_path: Path) -> dict[str, Any]:
        lock_path = _resolve(lock_path)
        lock, lock_identity = _read_pinned_json(
            lock_path, "synthetic strict freeze"
        )
        if (
            lock.get("schemaVersion") != 2
            or lock.get("kind") != DEEP_FREEZE_KIND
        ):
            raise ValueError("synthetic strict freeze is malformed")
        selection = _mapping(lock.get("selection"), "synthetic selection")
        freeze = _mapping(lock.get("freeze"), "synthetic freeze")
        suite_path, suite_identity = _verify_embedded_identity(
            selection.get("suite"), "synthetic strict suite"
        )
        engine_path, engine_identity = _verify_embedded_identity(
            freeze.get("engineExecutable"), "synthetic strict engine"
        )
        suite, stable_suite = _read_pinned_json(
            suite_path, "synthetic strict suite"
        )
        if not _same_identity(suite_identity, stable_suite):
            raise ValueError("synthetic suite changed during verification")
        if len(_sequence(suite.get("pairs"), "synthetic suite pairs")) != 4352:
            raise ValueError("synthetic strict suite has the wrong pair count")
        if (
            len(_sequence(suite.get("positions"), "synthetic suite positions"))
            != 8704
        ):
            raise ValueError(
                "synthetic strict suite has the wrong root count"
            )
        results = _resolve(Path(str(lock["outputs"]["results"])))
        return {
            "lockPath": str(lock_path),
            "lockIdentity": lock_identity,
            "lockSha256": lock_identity["sha256"],
            "lock": lock,
            "suitePath": str(suite_path),
            "suiteIdentity": suite_identity,
            "suite": suite,
            "enginePath": str(engine_path),
            "engineIdentity": engine_identity,
            "resultsPath": str(results),
            "coordinationGuardPath": str(
                _SyntheticDeepApi.coordination_guard_path(results)
            ),
        }


def _self_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="omega-king-state-v1-self-test-"))
    try:
        real_protocol = (
            _repo_root()
            / "validation"
            / "omega-nnue-king-state-v1-protocol.json"
        )
        _expect(
            _sha256(real_protocol),
            CANONICAL_PROTOCOL_SHA256,
            "source-pinned canonical protocol SHA-256",
        )

        repo = root / "repo"
        for entries in SOURCE_INVENTORY.values():
            for relative in entries.values():
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"fixture:{relative}\n".encode("utf-8"))

        provisional = _canonical_paths(repo, protocol_sha256="0" * 64)
        provisional.initializer.parent.mkdir(parents=True, exist_ok=True)
        provisional.initializer.write_bytes(b"residual-v3-fixture\n")
        provisional.static_hce_evaluator.parent.mkdir(
            parents=True, exist_ok=True
        )
        provisional.static_hce_evaluator.write_bytes(
            b"static-hce-evaluator-fixture\n"
        )
        protocol = json.loads(real_protocol.read_text(encoding="utf-8"))
        protocol["declaredUtc"] = "2026-07-19T00:00:00Z"
        protocol["architecture"]["initializer"]["path"] = str(
            provisional.initializer
        )
        protocol["architecture"]["initializer"]["sha256"] = _sha256(
            provisional.initializer
        )
        provisional.protocol.parent.mkdir(parents=True, exist_ok=True)
        provisional.protocol.write_text(
            json.dumps(protocol, sort_keys=True), encoding="utf-8"
        )
        paths = _canonical_paths(
            repo,
            protocol_sha256=_sha256(provisional.protocol),
            static_hce_sha256=_sha256(
                provisional.static_hce_evaluator
            ),
            static_hce_bytes=provisional.static_hce_evaluator.stat().st_size,
        )
        _validate_canonical_layout(paths)

        paths.output_dir.mkdir(parents=True, exist_ok=True)
        paths.source_teacher.parent.mkdir(parents=True, exist_ok=True)
        paths.source_teacher.write_bytes(b"teacher-engine\n")
        paths.frozen_teacher.parent.mkdir(parents=True, exist_ok=True)
        paths.frozen_teacher.write_bytes(paths.source_teacher.read_bytes())
        suite = {
            "schemaVersion": 1,
            "kind": SUITE_KIND,
            "fixedNodes": 100000,
            "targetRootPairs": 4096,
            "targetPairsPerPhase": 1024,
            "reservePairsPerPhase": 64,
            "candidateRootPairs": 4352,
            "pairs": [{}] * 4352,
            "positions": [{}] * 8704,
        }
        paths.suite.write_text(json.dumps(suite), encoding="utf-8")
        paths.legality_config.write_text("{}\n", encoding="utf-8")
        synthetic_direct_reason = "synthetic pre-label rejection"
        synthetic_companion_reason = (
            "whole-pair companion excluded after paired-root rejection"
        )
        synthetic_raw_rejections = [
            {
                "rootId": f"preflight-rejected-{index}",
                "reason": synthetic_direct_reason,
            }
            for index in range(18)
        ]
        synthetic_rejected_pairs = [
            {
                "phase": PHASES[index % len(PHASES)],
                "pairRank": f"{index:064x}",
                "roots": [
                    {
                        "rootId": f"preflight-rejected-{index}",
                        "reason": synthetic_direct_reason,
                        "reasonSha256": hashlib.sha256(
                            synthetic_direct_reason.encode("utf-8")
                        ).hexdigest(),
                        "directProtocolRejection": True,
                    },
                    {
                        "rootId": f"preflight-companion-{index}",
                        "reason": synthetic_companion_reason,
                        "reasonSha256": hashlib.sha256(
                            synthetic_companion_reason.encode("utf-8")
                        ).hexdigest(),
                        "directProtocolRejection": False,
                    },
                ],
            }
            for index in range(18)
        ]
        deep = {
            "schemaVersion": 2,
            "kind": DEEP_FREEZE_KIND,
            "createdUtc": "2026-07-19T00:00:01Z",
            "selectionContract": {
                "candidateBlind": True,
                "rulesOnlySourcesUseEvaluator": False,
                "scoresReadForRanking": False,
                "gameResultsReadForRanking": False,
                "bestMovesReadForRanking": False,
                "pvsReadForRanking": False,
                "oppositeSideToMoveWithinPair": True,
                "distinctSymmetryOrbitsWithinPair": True,
                "distinctSymmetryOrbitsAcrossSuite": True,
                "phaseBalanced": True,
                "targetPairsPerPhase": 1024,
                "reservePairsPerPhase": 64,
                "preflightExtraPairsPerPhase": 64,
                "preflightWholePairExclusion": True,
                "preflightRejectedPairSanityCap": 32,
                "replacementPolicy": (
                    "Within each phase, consume frozen pair order. A pair is "
                    "usable only when both roots produce valid fixed-node "
                    "exact-cp labels; otherwise advance to the next frozen "
                    "reserve pair. Never rank or choose replacements by score "
                    "value, PV, or game result."
                ),
            },
            "searchContract": {
                "mode": "fixed nodes",
                "nodes": 100000,
                "freshProcessPerPosition": True,
                "options": HCE_OPTIONS,
                "targetIsLastCompletedExactIteration": True,
            },
            "freeze": {
                "sourceEngine": _identity(paths.source_teacher),
                "engineExecutable": _identity(paths.frozen_teacher),
                "selector": _identity(
                    repo / SOURCE_INVENTORY["tooling"]["deepHceV2"]
                ),
                "omegaNnueModule": _identity(
                    repo / SOURCE_INVENTORY["tooling"]["omegaNnuePython"]
                ),
                "symmetryModule": _identity(
                    repo / SOURCE_INVENTORY["tooling"]["symmetryAndScreen"]
                ),
            },
            "selection": {
                "selectedPairs": 4096,
                "selectedRoots": 8192,
                "frozenCandidatePairs": 4352,
                "frozenCandidateRoots": 8704,
                "preflightCandidatePairs": 4608,
                "preflightRejectedPairs": 18,
                "preflightRejectedRoots": 18,
                "preflightRejectedPairSanityCap": 32,
                "phaseCounts": {phase: 2048 for phase in PHASES},
                "sideToMoveCounts": {"b": 4096, "w": 4096},
                "validation": {
                    "senpaiOfenAcceptance": {
                        "passed": True,
                        "searchMode": "depth",
                        "searchDepthPerPosition": 1,
                        "nodesOneFalseTerminalRegressionAvoided": True,
                        "syntacticScoreBearingInfoRequired": True,
                        "strictTeacherCompletedDepthPolicyApplied": False,
                        "candidateScoresUsedForSelection": 0,
                        "positionsProbed": 9216,
                        "positionsAccepted": 9198,
                        "positionsRejected": 18,
                        "pairsProbed": 4608,
                        "pairsRejected": 18,
                        "maximumRejectedPairs": 32,
                        "extraPairsPerPhase": 64,
                        "finalPairsAccepted": 4352,
                        "finalPositionsAccepted": 8704,
                        "finalPoolContainsRejectedRoot": False,
                        "rejectedRoots": synthetic_raw_rejections,
                        "rejectedPairs": synthetic_rejected_pairs,
                    }
                },
                "suite": _identity(paths.suite),
                "legalityValidationConfig": _identity(
                    paths.legality_config
                ),
            },
            "outputs": {
                "directory": str(paths.output_dir),
                "results": str(paths.results),
                "corpus": str(paths.corpus),
                "corpusManifest": str(paths.corpus_manifest),
            },
        }
        paths.deep_freeze.write_text(
            json.dumps(deep, sort_keys=True), encoding="utf-8"
        )

        timestamp = "2026-07-19T00:00:02Z"
        # A runner that owns the exact shared guard excludes the sealer.
        with _SyntheticDeepApi.coordination_guard(
            paths.results, owner="synthetic-runner", lock_sha256="freeze"
        ):
            try:
                _seal(
                    paths,
                    created_utc=timestamp,
                    deep_api=_SyntheticDeepApi,
                )
            except ValueError as error:
                if "another coordinator" not in str(error):
                    raise
            else:
                raise AssertionError(
                    "sealer ignored the teacher runner's coordination guard"
                )
        if paths.seal.exists():
            raise AssertionError("excluded concurrent sealer published a seal")

        # Wrong roots, collisions, and containment escapes fail before work.
        try:
            _real_paths_from_request(
                repo=repo,
                protocol=paths.protocol,
                deep_freeze=paths.deep_freeze,
                seal=paths.seal,
                static_hce_evaluator=paths.static_hce_evaluator,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("non-real repository root was accepted")
        for malformed in (
            replace(paths, seal=paths.protocol),
            replace(paths, suite=root / "escape-suite.json"),
        ):
            try:
                _seal(
                    malformed,
                    created_utc=timestamp,
                    deep_api=_SyntheticDeepApi,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "canonical path collision or escape was accepted"
                )

        # Missing evaluator proves the binary must be built before sealing.
        evaluator_bytes = paths.static_hce_evaluator.read_bytes()
        paths.static_hce_evaluator.unlink()
        try:
            _seal(
                paths,
                created_utc=timestamp,
                deep_api=_SyntheticDeepApi,
            )
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("seal accepted a missing static HCE evaluator")
        paths.static_hce_evaluator.write_bytes(evaluator_bytes)

        # The source-pinned protocol digest rejects even semantically harmless
        # byte drift, while semantic validation rejects a changed K2 recipe.
        protocol_bytes = paths.protocol.read_bytes()
        paths.protocol.write_bytes(protocol_bytes + b"\n")
        try:
            _seal(
                paths,
                created_utc=timestamp,
                deep_api=_SyntheticDeepApi,
            )
        except ValueError as error:
            if "canonical protocol SHA-256" not in str(error):
                raise
        else:
            raise AssertionError("seal accepted protocol byte drift")
        paths.protocol.write_bytes(protocol_bytes)

        def reject_protocol_mutation(label: str, mutate: Any) -> None:
            bad_protocol = json.loads(protocol_bytes)
            mutate(bad_protocol)
            try:
                _validate_protocol(bad_protocol)
            except ValueError:
                return
            raise AssertionError(
                f"protocol validation accepted changed {label}"
            )

        reject_protocol_mutation(
            "K2 recipe",
            lambda value: value["candidates"][2].__setitem__(
                "featureTransformerLearningRateScale", 0.75
            ),
        )
        reject_protocol_mutation(
            "split phase-balance tolerance",
            lambda value: value["dataSplit"].__setitem__(
                "maximumRelativeDeviationFromEqualPhaseCount", 0.2
            ),
        )
        reject_protocol_mutation(
            "preflight rejection cap",
            lambda value: value["deepHceTeacherPreparation"].__setitem__(
                "maximumPreflightRejectedPairs", 33
            ),
        )
        reject_protocol_mutation(
            "validation tie denominator",
            lambda value: value["validationSelection"].__setitem__(
                "tieFormula",
                (
                    "abs(K1Loss-K2Loss)/max(K1Loss,K2Loss) "
                    "<= tieRelativeLoss"
                ),
            ),
        )
        reject_protocol_mutation(
            "offline target clip",
            lambda value: value["offlineTestGate"]["targetAndLoss"].__setitem__(
                "targetClipCpInclusive", [-2500, 2500]
            ),
        )
        reject_protocol_mutation(
            "offline group phase purity",
            lambda value: value["offlineTestGate"]["aggregation"].__setitem__(
                "groupPhasePurityRequired", False
            ),
        )
        reject_protocol_mutation(
            "offline bootstrap quantile",
            lambda value: value["offlineTestGate"]["bootstrap"].__setitem__(
                "lowerBound",
                "5th percentile via numpy.quantile(method='nearest')",
            ),
        )
        reject_protocol_mutation(
            "whole-corpus parity command",
            lambda value: value["quantizationAndRuntimeGate"][
                "wholeCorpusParity"
            ].__setitem__("command", "--evaluate-network <network>"),
        )
        reject_protocol_mutation(
            "fresh-suite trajectory count",
            lambda value: value["freshSuiteGeneration"][
                "rulesOnlySamplerPolicy"
            ].__setitem__("trajectoryPairs", 1024),
        )
        reject_protocol_mutation(
            "equal-time search timeout",
            lambda value: value["matchExecution"]["searchTimeoutMs"].__setitem__(
                "equalTime", 6000
            ),
        )
        reject_protocol_mutation(
            "idle-process attestation",
            lambda value: value["equalTimeGate"]["idleAttestation"].__setitem__(
                "concurrentMatchProcesses", 2
            ),
        )

        # The mandatory public strict verifier rejects a malformed freeze.
        freeze_bytes = paths.deep_freeze.read_bytes()
        malformed_freeze = json.loads(freeze_bytes)
        malformed_freeze["kind"] = "not-a-deep-freeze"
        paths.deep_freeze.write_text(
            json.dumps(malformed_freeze, sort_keys=True), encoding="utf-8"
        )
        try:
            _seal(
                paths,
                created_utc=timestamp,
                deep_api=_SyntheticDeepApi,
            )
        except ValueError as error:
            if "strict freeze" not in str(error):
                raise
        else:
            raise AssertionError("seal accepted a malformed deep freeze")
        paths.deep_freeze.write_bytes(freeze_bytes)
        changed_preflight = json.loads(freeze_bytes)
        changed_preflight["selectionContract"][
            "preflightRejectedPairSanityCap"
        ] = 31
        paths.deep_freeze.write_text(
            json.dumps(changed_preflight, sort_keys=True), encoding="utf-8"
        )
        try:
            _seal(
                paths,
                created_utc=timestamp,
                deep_api=_SyntheticDeepApi,
            )
        except ValueError as error:
            if "preflightRejectedPairSanityCap" not in str(error):
                raise
        else:
            raise AssertionError(
                "seal accepted a changed preflight rejection cap"
            )
        paths.deep_freeze.write_bytes(freeze_bytes)

        _seal(
            paths,
            created_utc=timestamp,
            deep_api=_SyntheticDeepApi,
        )
        first_bytes = paths.seal.read_bytes()
        report = _verify_seal(paths, deep_api=_SyntheticDeepApi)
        if (
            not report["teacherArtifactsAbsentAtDeclaration"]
            or report["identitiesVerified"] < 10
        ):
            raise AssertionError("valid seal did not verify")
        changed_contract = json.loads(first_bytes)
        changed_contract["contracts"][
            "deepHceMaximumPreflightRejectedPairs"
        ] = 31
        paths.seal.write_text(
            json.dumps(changed_contract, sort_keys=True), encoding="utf-8"
        )
        try:
            _verify_seal(paths, deep_api=_SyntheticDeepApi)
        except ValueError as error:
            if "seal contracts" not in str(error):
                raise
        else:
            raise AssertionError(
                "verify-seal accepted a changed preflight contract"
            )
        paths.seal.write_bytes(first_bytes)

        # Existing publication is never replaced, even with identical bytes.
        original_seal_hash = _sha256(paths.seal)
        try:
            _seal(
                paths,
                created_utc=timestamp,
                deep_api=_SyntheticDeepApi,
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("existing seal was overwritten")
        if _sha256(paths.seal) != original_seal_hash:
            raise AssertionError("existing seal changed during no-clobber test")

        paths.seal.unlink()
        _seal(
            paths,
            created_utc=timestamp,
            deep_api=_SyntheticDeepApi,
        )
        if first_bytes != paths.seal.read_bytes():
            raise AssertionError("seal bytes are not deterministic")

        paths.results.write_text('{"status":"ok"}\n', encoding="utf-8")
        post_label_report = _verify_seal(
            paths, deep_api=_SyntheticDeepApi
        )
        if not any(
            item["role"] == "teacherResults" and item["existsNow"]
            for item in post_label_report["currentTeacherArtifacts"]
        ):
            raise AssertionError("verify-seal did not permit current results")
        paths.seal.unlink()
        try:
            _seal(
                paths,
                created_utc=timestamp,
                deep_api=_SyntheticDeepApi,
            )
        except ValueError as error:
            if "teacher activity" not in str(error):
                raise
        else:
            raise AssertionError("seal accepted an existing teacher result")

        # Recreate the sealed declaration, then mutate a pinned source.
        paths.results.unlink()
        _seal(
            paths,
            created_utc=timestamp,
            deep_api=_SyntheticDeepApi,
        )
        for group, source_name in (
            ("tooling", "kingStateTraining"),
            ("tooling", "kingStateMatches"),
            ("tooling", "omegaRootSamplerProgram"),
            ("tests", "nativeOmegaNnue"),
        ):
            source_path = repo / SOURCE_INVENTORY[group][source_name]
            original_source = source_path.read_bytes()
            source_path.write_bytes(original_source + b"drift")
            try:
                _verify_seal(paths, deep_api=_SyntheticDeepApi)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"verify-seal accepted {source_name} source drift"
                )
            source_path.write_bytes(original_source)

        print("king_state_v1 self-test passed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    defaults = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    seal = subparsers.add_parser(
        "seal", help="write the declaration-time seal before teacher labeling"
    )
    seal.add_argument("--protocol", type=Path, default=defaults["protocol"])
    seal.add_argument("--deep-freeze", type=Path, default=defaults["deep_freeze"])
    seal.add_argument("--output", type=Path, default=defaults["seal"])
    seal.add_argument(
        "--static-hce-evaluator",
        type=Path,
        default=defaults["static_hce_evaluator"],
        help="canonical built omega_nnue evaluator used for static HCE labels",
    )
    seal.add_argument(
        "--repo-root",
        type=Path,
        default=defaults["repo"],
        help="must resolve to the repository containing this sealer",
    )

    verify = subparsers.add_parser(
        "verify-seal",
        help="rehash a seal; current teacher results are allowed to exist",
    )
    verify.add_argument("--seal", type=Path, default=defaults["seal"])

    subparsers.add_parser("self-test", help="run synthetic sealing checks")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "seal":
        paths = _real_paths_from_request(
            repo=args.repo_root,
            protocol=args.protocol,
            deep_freeze=args.deep_freeze,
            seal=args.output,
            static_hce_evaluator=args.static_hce_evaluator,
        )
        manifest = _seal(paths)
        print(
            "Pre-label seal written: "
            f"{_resolve(paths.seal)} ({manifest['generationId']})"
        )
    elif args.command == "verify-seal":
        paths = _canonical_paths(_repo_root())
        _expect_path(args.seal, paths.seal, "seal input")
        report = _verify_seal(paths)
        current = sum(
            bool(item["existsNow"])
            for item in report["currentTeacherArtifacts"]
        )
        print(
            f"Verified {report['identitiesVerified']} identities; "
            "teacher artifacts absent at declaration=true; "
            f"present now={current}."
        )
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
