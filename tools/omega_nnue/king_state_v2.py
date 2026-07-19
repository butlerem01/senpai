#!/usr/bin/env python3
"""Seal protocol generation 2 over a fresh deep-HCE-v3 data profile.

The existing deep-HCE implementation deliberately uses v2 schema and file
names.  Its ``run`` and ``finalize`` commands also require the legacy
king-state-v1 seal kind and generation identifier.  This sealer therefore
publishes that compatibility envelope, but adds and verifies a mandatory
protocol-v2/data-v3 profile which is effective before any teacher label exists.

Unlike ``king_state_v1.py``, this wrapper does not assume one historical
preflight rejection count.  It derives the observed count from the strict
freeze, verifies the complete whole-pair rejection inventory, and only
requires that the frozen sanity cap was respected.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable, Iterator, Mapping, Sequence

import deep_hce_v2 as deep
import king_state_v1 as legacy


PROFILE_KIND = "omega-nnue-king-state-v2-preregistration"
PROFILE_ID = "king-state-v2-deep-hce-v3"
OUTPUT_DIRECTORY_NAME = "deep-hce-v3"
FREEZE_NAME = "deep-hce-v2.freeze.json"
SEAL_NAME = "king-state-v1-prelabel.seal.json"
EXPECTED_SEED = 2026071904
PHASES = ("opening", "middlegame", "late", "endgame")
COMPANION_REASON = (
    "whole-pair companion excluded after paired-root rejection"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(path: Path, *, base: Path | None = None) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() and base is not None:
        expanded = base / expanded
    return expanded.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = _resolve(path)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "bytes": stat.st_size,
        "sha256": _sha256(resolved),
    }


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            _resolve(Path(str(left["path"])))
            == _resolve(Path(str(right["path"])))
            and int(left["bytes"]) == int(right["bytes"])
            and str(left["sha256"]).lower()
            == str(right["sha256"]).lower()
        )
    except (KeyError, TypeError, ValueError):
        return False


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected an array")
    return value


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"{label}: expected an integer >= {minimum}, got {value!r}"
        )
    return value


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = _resolve(path)
    before = _identity(resolved)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON: {resolved}: {error}") from error
    after = _identity(resolved)
    if not _same_identity(before, after):
        raise ValueError(f"{label} changed while it was read: {resolved}")
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object: {resolved}")
    return value, after


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json_no_clobber(path: Path, value: Any) -> None:
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as error:
            raise ValueError(f"refusing to overwrite existing seal: {target}") from error
        os.unlink(temporary)
        temporary = ""
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _profile_path(repo: Path) -> Path:
    return repo / "validation" / "omega-nnue-king-state-v2-preregistration.json"


def _phase_amendment_path(repo: Path) -> Path:
    return (
        repo
        / "validation"
        / "omega-nnue-king-state-v1-amendment-001.json"
    )


def _generation2_amendment_path(repo: Path) -> Path:
    return (
        repo
        / "validation"
        / "omega-nnue-king-state-v2-amendment.json"
    )


def _protocol_path(repo: Path) -> Path:
    return repo / "validation" / "omega-nnue-king-state-v1-protocol.json"


def _initializer_path(repo: Path) -> Path:
    return (
        repo
        / "build-msvc"
        / "experimental-networks"
        / "omega-nnue-residual-v3-feature.nnue"
    )


def _static_hce_path(repo: Path) -> Path:
    return repo / ".build-msvc-tests" / "Release" / "tests" / "omega_nnue.exe"


def _mandatory_forbidden_roots(repo: Path) -> tuple[Path, ...]:
    data = repo / "build-msvc" / "data-generation"
    return (
        data / "deep-hce-v2",
        data / "deep-hce-v2.invalid-pre-stream-seal-20260719T0505Z",
        data / "deep-hce-v2.partial-reject18-20260719T0452Z",
        data / "deep-hce-v2.partial-stopped-20260719T0433Z",
        data / "deep-hce-v2-random-roots.jsonl",
        data / "deep-hce-v2-random-roots.jsonl.freeze.json",
        data / "deep-hce-v2-random-roots.jsonl.manifest.json",
        data / "sampler-smoke-2.jsonl",
        data / "sampler-smoke-2.jsonl.manifest.json",
        repo / "build-msvc" / "king-state-v1",
        repo / "build-msvc" / "screening-catalog-v1",
    )


def _source_inventory(repo: Path) -> dict[str, dict[str, Path]]:
    inventory = {
        group: {
            name: repo / relative
            for name, relative in entries.items()
        }
        for group, entries in legacy.SOURCE_INVENTORY.items()
    }
    inventory["tooling"].update(
        {
            "v2PrelabelSealer": Path(__file__).resolve(),
            "v2Preregistration": _profile_path(repo),
            "phaseIncidenceOrchestrator": (
                repo / "tools" / "omega_nnue" / "king_state_train_amended.py"
            ),
            "canonicalTrainer": (
                repo / "tools" / "omega_nnue" / "train_canonical.py"
            ),
            "generation2Orchestrator": (
                repo
                / "tools"
                / "omega_nnue"
                / "king_state_train_generation2.py"
            ),
            "sealedTrainer": repo / "tools" / "omega_nnue" / "train.py",
        }
    )
    inventory["declarations"] = {
        "phaseIncidenceAmendment": _phase_amendment_path(repo),
        "generation2Amendment": _generation2_amendment_path(repo),
        "generation2Preregistration": _profile_path(repo),
    }
    return inventory


def _source_paths(inventory: Mapping[str, Mapping[str, Path]]) -> dict[str, Any]:
    return {
        group: {
            name: str(_resolve(path))
            for name, path in sorted(entries.items())
        }
        for group, entries in sorted(inventory.items())
    }


def _source_pins(inventory: Mapping[str, Mapping[str, Path]]) -> dict[str, Any]:
    return {
        group: {
            name: _identity(path)
            for name, path in sorted(entries.items())
        }
        for group, entries in sorted(inventory.items())
    }


def _validate_profile(profile: Mapping[str, Any], repo: Path) -> None:
    _expect(profile.get("schemaVersion"), 1, "profile.schemaVersion")
    _expect(profile.get("kind"), PROFILE_KIND, "profile.kind")
    _expect(profile.get("profileId"), PROFILE_ID, "profile.profileId")
    _expect(profile.get("protocolGeneration"), 2, "profile protocol generation")
    _expect(profile.get("dataProfile"), "deep-hce-v3", "profile data profile")

    compatibility = _mapping(profile.get("compatibility"), "profile.compatibility")
    _expect(
        compatibility.get("protocolSha256"),
        legacy.CANONICAL_PROTOCOL_SHA256,
        "profile compatibility protocol hash",
    )
    _expect(
        compatibility.get("legacySealKind"),
        legacy.SEAL_KIND,
        "profile compatibility seal kind",
    )
    _expect(
        compatibility.get("legacyGenerationId"),
        legacy.GENERATION_ID,
        "profile compatibility generation",
    )

    fresh = _mapping(
        profile.get("freshTeacherGeneration"),
        "profile.freshTeacherGeneration",
    )
    for key, expected in {
        "directoryName": OUTPUT_DIRECTORY_NAME,
        "seed": EXPECTED_SEED,
        "fixedNodesPerRoot": 100000,
        "targetPairs": 4096,
        "targetPairsPerPhase": 1024,
        "reservePairsPerPhase": 64,
        "teacherArtifactsAbsentBeforeSeal": True,
    }.items():
        _expect(fresh.get(key), expected, f"profile fresh generation {key}")
    mandatory_values = _sequence(
        fresh.get("mandatoryForbiddenRoots"),
        "profile mandatory forbidden roots",
    )
    _expect(
        tuple(
            _resolve(Path(str(value)), base=repo)
            for value in mandatory_values
        ),
        tuple(map(_resolve, _mandatory_forbidden_roots(repo))),
        "profile mandatory forbidden roots",
    )
    _expect(
        fresh.get("mandatoryForbiddenRootCount"),
        11,
        "profile mandatory forbidden root count",
    )
    _expect(
        fresh.get("everyJsonOrJsonlUnderMandatoryRootsExactPinned"),
        True,
        "profile mandatory forbidden pin policy",
    )
    rejection = _mapping(
        fresh.get("preflightRejectionPolicy"),
        "profile preflight rejection policy",
    )
    for key in (
        "observedCountIsFreezeDerived",
        "mustNotExceedFrozenSanityCap",
        "wholePairExclusion",
        "directRejectedRootInventoryMustMatch",
    ):
        _expect(rejection.get(key), True, f"profile rejection policy {key}")
    _expect(
        rejection.get("exactObservedCountPreregistered"),
        False,
        "profile exact rejection count",
    )

    matrix = _mapping(profile.get("candidateMatrix"), "profile candidate matrix")
    _expect(matrix.get("changesOriginalProtocol"), False, "profile candidate changes")
    _expect(matrix.get("primarySeed"), 20260725, "profile primary seed")
    _expect(matrix.get("robustnessSeed"), 20260726, "profile robustness seed")
    candidates = {
        str(_mapping(item, "profile candidate").get("id")): _mapping(
            item, "profile candidate"
        )
        for item in _sequence(matrix.get("candidates"), "profile candidates")
    }
    _expect(set(candidates), {"K0", "K1", "K2"}, "profile candidate IDs")
    _expect(candidates["K0"].get("train"), False, "profile K0.train")
    for candidate_id, scale in (("K1", 0.5), ("K2", 1.0)):
        candidate = candidates[candidate_id]
        for key, expected in {
            "train": True,
            "learningRate": 0.003,
            "featureTransformerLearningRateScale": scale,
            "epochs": 24,
            "qatEpochs": 4,
        }.items():
            _expect(
                candidate.get(key),
                expected,
                f"profile {candidate_id}.{key}",
            )

    amendments = _mapping(
        profile.get("prelabelAmendments"), "profile.prelabelAmendments"
    )
    grouping = _mapping(
        amendments.get("phaseIncidenceGrouping"),
        "profile phase-incidence grouping",
    )
    _expect(
        _resolve(Path(str(grouping.get("sourceDeclaration", ""))), base=repo),
        _phase_amendment_path(repo),
        "profile phase amendment source",
    )
    for key, expected in {
        "groupPhasePurityRequired": False,
        "splitUnit": "global leakage component groupId",
        "bootstrapUnit": "global leakage component groupId",
        "bootstrapStratification": "exact phase-incidence set",
        "minimumGroupsPerObservedStratum": 2,
        "iterations": 10000,
        "seed": 20260727,
        "pairedCandidateAndBaselines": True,
        "requiredLowerBoundExclusive": 0,
    }.items():
        _expect(grouping.get(key), expected, f"profile grouping {key}")
    deployment = _mapping(
        amendments.get("canonicalDeploymentFloat"),
        "profile canonical deployment float",
    )
    _expect(
        _resolve(Path(str(deployment.get("sourceDeclaration", ""))), base=repo),
        _generation2_amendment_path(repo),
        "profile deployment amendment source",
    )
    for key, expected in {
        "productionArtifact": "exported quantized NNUE",
        "optimizerShadowPreserved": True,
        "deploymentFloatDerivedFromQuantizedNetwork": True,
        "deploymentFloatRequantizesByteIdentically": True,
        "deploymentFloatParameterRoundTripExact": True,
        "healthMetricSubject": "deployment-equivalent float checkpoint",
        "healthThresholdsChanged": False,
        "heldOutTargetsDecodedByCanonicalization": False,
        "candidateRecipesChanged": False,
    }.items():
        _expect(deployment.get(key), expected, f"profile deployment float {key}")

    matches = _mapping(
        profile.get("freshMatchSuites"), "profile fresh match suites"
    )
    for key, expected in {
        "developmentScreenSeed": 2026072001,
        "equalNodeConfirmationSeed": 2026072002,
        "equalTimeConfirmationSeed": 2026072003,
        "configSeedEqualsSuiteSeed": True,
        "orbitDisjointFromBothTrainingGenerationsAndPriorMatches": True,
    }.items():
        _expect(matches.get(key), expected, f"profile match suite {key}")

    expected_required = {
        str(path.relative_to(repo)).replace("\\", "/")
        for path in _source_inventory(repo)["tooling"].values()
        if path.name
        in {
            "train.py",
            "train_canonical.py",
            "king_state_train.py",
            "king_state_train_amended.py",
            "king_state_train_generation2.py",
            "king_state_v2.py",
        }
    }
    _expect(
        set(_sequence(profile.get("requiredPinnedTooling"), "profile required tooling")),
        expected_required,
        "profile required pinned tooling",
    )


def _validate_amendments(
    repo: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    phase, phase_pin = _load_json(
        _phase_amendment_path(repo), "phase-incidence amendment"
    )
    _expect(
        phase.get("kind"),
        "omega-nnue-king-state-v1-protocol-amendment",
        "phase amendment kind",
    )
    _expect(
        phase.get("amendmentId"),
        "king-state-v1-cross-phase-cluster-bootstrap",
        "phase amendment id",
    )
    phase_contracts = _mapping(
        phase.get("amendedContracts"), "phase amendment contracts"
    )
    for key, expected in {
        "groupPhasePurityRequired": False,
        "bootstrapUnit": "global leakage component groupId",
        "bootstrapStratification": "phase-incidence set",
        "minimumGroupsPerObservedStratum": 2,
        "iterations": 10000,
        "seed": 20260727,
        "requiredLowerBoundExclusive": 0,
    }.items():
        _expect(
            phase_contracts.get(key),
            expected,
            f"phase amendment {key}",
        )

    generation2, generation2_pin = _load_json(
        _generation2_amendment_path(repo), "generation-2 amendment"
    )
    _expect(
        generation2.get("kind"),
        "omega-nnue-king-state-v2-protocol-amendment",
        "generation-2 amendment kind",
    )
    _expect(
        generation2.get("amendmentId"),
        "king-state-v2-deployment-float-checkpoint",
        "generation-2 amendment id",
    )
    policy = _mapping(
        generation2.get("generation2DataPolicy"),
        "generation-2 data policy",
    )
    for key, expected in {
        "generation1Status": "failed",
        "trainingCorpusMayReuseGeneration1Rows": False,
        "trainingCorpusMayReuseGeneration1TeacherLabels": False,
        "samplerAndSelectorSeed": EXPECTED_SEED,
        "preLabelSealRequired": True,
        "teacherLabelsMayStartBeforeSeal": False,
    }.items():
        _expect(policy.get(key), expected, f"generation-2 data policy {key}")
    match_seeds = _mapping(
        policy.get("freshMatchSuiteSeeds"),
        "generation-2 fresh match suite seeds",
    )
    for key, expected in {
        "developmentScreen": 2026072001,
        "equalNodeConfirmation": 2026072002,
        "equalTimeConfirmation": 2026072003,
        "configSeedEqualsSuiteSeed": True,
    }.items():
        _expect(
            match_seeds.get(key),
            expected,
            f"generation-2 match seed {key}",
        )
    amended = _mapping(
        generation2.get("amendedContracts"),
        "generation-2 amended contracts",
    )
    for phrase in (
        "deployment-equivalent float checkpoint",
        "same numeric limits",
    ):
        if phrase not in str(amended.get("healthMetricSubjectChange", "")):
            raise ValueError(
                "generation-2 amendment does not explicitly predeclare the "
                f"health metric subject change: missing {phrase!r}"
            )
    for key in (
        "optimizerShadowCheckpoint",
        "deploymentFloatCheckpoint",
        "healthInput",
        "manifest",
        "informationBoundary",
    ):
        if not str(amended.get(key, "")).strip():
            raise ValueError(f"generation-2 amendment lacks {key}")
    return phase_pin, generation2_pin


def _validate_protocol(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol, pin = _load_json(_protocol_path(repo), "canonical protocol")
    _expect(
        pin["sha256"],
        legacy.CANONICAL_PROTOCOL_SHA256,
        "canonical protocol SHA-256",
    )
    legacy._validate_protocol(protocol)
    return protocol, pin


def _reason_sha(reason: str) -> str:
    return hashlib.sha256(reason.encode("utf-8")).hexdigest()


def _validate_rejections(lock: Mapping[str, Any]) -> dict[str, int]:
    contract = _mapping(lock.get("selectionContract"), "freeze selection contract")
    selection = _mapping(lock.get("selection"), "freeze selection")
    validation = _mapping(selection.get("validation"), "freeze validation")
    acceptance = _mapping(
        validation.get("senpaiOfenAcceptance"),
        "freeze Senpai acceptance",
    )
    cap = _integer(
        contract.get("preflightRejectedPairSanityCap"),
        "freeze selection-contract rejection cap",
    )
    _expect(
        _integer(
            selection.get("preflightRejectedPairSanityCap"),
            "freeze selection rejection cap",
        ),
        cap,
        "freeze rejection cap echo",
    )
    observed = _integer(
        selection.get("preflightRejectedPairs"),
        "freeze preflight rejected pairs",
    )
    if observed > cap:
        raise ValueError(
            f"freeze rejected-pair count {observed} exceeds cap {cap}"
        )
    _expect(
        _integer(
            acceptance.get("pairsRejected"),
            "freeze acceptance rejected pairs",
        ),
        observed,
        "freeze acceptance rejected pairs",
    )
    rejected_pairs = _sequence(
        acceptance.get("rejectedPairs"), "freeze rejected pairs"
    )
    _expect(len(rejected_pairs), observed, "freeze rejected pair inventory")

    direct: dict[str, str] = {}
    excluded: set[str] = set()
    for pair_index, value in enumerate(rejected_pairs):
        pair = _mapping(value, f"freeze rejectedPairs[{pair_index}]")
        phase = str(pair.get("phase", ""))
        if phase not in PHASES:
            raise ValueError(f"freeze rejectedPairs[{pair_index}] has bad phase")
        roots = _sequence(
            pair.get("roots"), f"freeze rejectedPairs[{pair_index}].roots"
        )
        if len(roots) != 2:
            raise ValueError(
                f"freeze rejectedPairs[{pair_index}] does not contain two roots"
            )
        pair_direct = 0
        for root_index, root_value in enumerate(roots):
            root = _mapping(
                root_value,
                f"freeze rejectedPairs[{pair_index}].roots[{root_index}]",
            )
            root_id = str(root.get("rootId", ""))
            reason = str(root.get("reason", ""))
            direct_flag = root.get("directProtocolRejection")
            if (
                not root_id
                or root_id in excluded
                or not reason
                or direct_flag not in (True, False)
                or str(root.get("reasonSha256", "")).lower()
                != _reason_sha(reason)
            ):
                raise ValueError(
                    f"freeze rejectedPairs[{pair_index}] has malformed root"
                )
            excluded.add(root_id)
            if direct_flag is True:
                pair_direct += 1
                direct[root_id] = reason
            elif reason != COMPANION_REASON:
                raise ValueError(
                    f"freeze rejectedPairs[{pair_index}] has a noncanonical "
                    "whole-pair companion reason"
                )
        if pair_direct not in (1, 2):
            raise ValueError(
                f"freeze rejectedPairs[{pair_index}] has no direct rejection"
            )

    rejected_roots = _integer(
        selection.get("preflightRejectedRoots"),
        "freeze preflight rejected roots",
    )
    _expect(
        _integer(
            acceptance.get("positionsRejected"),
            "freeze acceptance rejected roots",
        ),
        rejected_roots,
        "freeze rejected root count echo",
    )
    raw = _sequence(acceptance.get("rejectedRoots"), "freeze rejected roots")
    _expect(len(raw), rejected_roots, "freeze raw rejected root inventory")
    raw_by_id: dict[str, str] = {}
    for index, value in enumerate(raw):
        item = _mapping(value, f"freeze rejectedRoots[{index}]")
        root_id = str(item.get("rootId", ""))
        reason = str(item.get("reason", ""))
        if not root_id or root_id in raw_by_id or not reason:
            raise ValueError(f"freeze rejectedRoots[{index}] is malformed")
        raw_by_id[root_id] = reason
    _expect(raw_by_id, direct, "freeze direct/raw rejection inventories")
    if not observed <= rejected_roots <= observed * 2:
        raise ValueError("freeze rejected-root count is inconsistent with pairs")

    suite_pairs = _integer(
        selection.get("frozenCandidatePairs"),
        "freeze frozen candidate pairs",
        minimum=1,
    )
    _expect(
        _integer(
            acceptance.get("finalPairsAccepted"),
            "freeze final accepted pairs",
            minimum=1,
        ),
        suite_pairs,
        "freeze final accepted pairs",
    )
    _expect(
        _integer(
            acceptance.get("finalPositionsAccepted"),
            "freeze final accepted roots",
            minimum=2,
        ),
        suite_pairs * 2,
        "freeze final accepted roots",
    )
    return {
        "observedPairs": observed,
        "observedDirectRoots": rejected_roots,
        "sanityCap": cap,
    }


def _validate_freeze(
    freeze_path: Path,
    repo: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    freeze_path = _resolve(freeze_path)
    expected_dir = (
        repo / "build-msvc" / "data-generation" / OUTPUT_DIRECTORY_NAME
    ).resolve()
    _expect(freeze_path.parent, expected_dir, "generation-2 freeze directory")
    _expect(freeze_path.name, FREEZE_NAME, "generation-2 freeze filename")
    context = deep.verify_freeze(freeze_path)
    lock = _mapping(context.get("lock"), "strict freeze lock")
    suite = _mapping(context.get("suite"), "strict freeze suite")
    _expect(suite.get("seed"), EXPECTED_SEED, "fresh teacher seed")
    _expect(suite.get("fixedNodes"), 100000, "fresh teacher nodes")
    _expect(suite.get("targetRootPairs"), 4096, "fresh teacher target pairs")
    _expect(suite.get("targetPairsPerPhase"), 1024, "fresh teacher phase pairs")
    _expect(suite.get("reservePairsPerPhase"), 64, "fresh teacher reserves")
    _validate_rejections(lock)

    prior_roots = _mandatory_forbidden_roots(repo)
    missing_roots = [str(path) for path in prior_roots if not path.exists()]
    if missing_roots:
        raise FileNotFoundError(
            "mandatory prior-generation forbidden roots are absent: "
            + ", ".join(missing_roots)
        )
    freeze_record = _mapping(lock.get("freeze"), "freeze identities")
    frozen_root_values = _sequence(
        freeze_record.get("forbiddenArtifactRoots"),
        "freeze forbidden artifact roots",
    )
    frozen_roots = {
        _resolve(Path(str(value))) for value in frozen_root_values
    }
    if not set(map(_resolve, prior_roots)).issubset(frozen_roots):
        raise ValueError(
            "fresh freeze omits one or more of the 11 mandatory prior-"
            "generation forbidden roots"
        )
    forbidden = _sequence(
        freeze_record.get("forbiddenArtifacts"),
        "freeze forbidden artifacts",
    )
    frozen_by_path = {
        _resolve(Path(str(item["path"]))): _mapping(
            item, "freeze forbidden artifact"
        )
        for item in forbidden
        if isinstance(item, dict) and "path" in item
    }
    required_file_set: set[Path] = set()
    for root in prior_roots:
        resolved = _resolve(root)
        if resolved.is_file():
            if resolved.suffix.lower() not in {".json", ".jsonl"}:
                raise ValueError(
                    f"mandatory forbidden file is not JSON/JSONL: {resolved}"
                )
            required_file_set.add(resolved)
        else:
            required_file_set.update(resolved.rglob("*.json"))
            required_file_set.update(resolved.rglob("*.jsonl"))
    required_files = sorted(
        {path.resolve() for path in required_file_set}, key=str
    )
    if not required_files:
        raise ValueError("prior generation roots contain no exclusion artifacts")
    missing = [
        str(path) for path in required_files if path not in frozen_by_path
    ]
    if missing:
        raise ValueError(
            "fresh freeze omits prior-generation exclusion artifacts: "
            + ", ".join(missing)
        )
    required_pins = []
    for path in required_files:
        actual = _identity(path)
        frozen = frozen_by_path[path]
        if not _same_identity(actual, frozen):
            raise ValueError(
                f"fresh freeze has a stale prior-generation exclusion: {path}"
            )
        required_pins.append(actual)
    return context, required_pins


def _teacher_artifacts(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    lock = _mapping(context.get("lock"), "strict freeze lock")
    outputs = _mapping(lock.get("outputs"), "freeze outputs")
    output_dir = _resolve(Path(str(outputs.get("directory", ""))))
    paths = [
        ("teacherResults", Path(str(outputs["results"]))),
        ("teacherCorpus", Path(str(outputs["corpus"]))),
        ("teacherCorpusManifest", Path(str(outputs["corpusManifest"]))),
        ("staticHceLabels", output_dir / "deep-hce-v2-static-hce.jsonl"),
        (
            "staticHceManifest",
            output_dir / "deep-hce-v2-static-hce.jsonl.manifest.json",
        ),
        ("residualTrainingCorpus", output_dir / "deep-hce-v2-residual.jsonl"),
        (
            "residualManifest",
            output_dir / "deep-hce-v2-residual.jsonl.manifest.json",
        ),
    ]
    return [
        {"role": role, "path": str(_resolve(path)), "existed": False}
        for role, path in paths
    ]


def _activity_paths(context: Mapping[str, Any]) -> list[Path]:
    artifacts = [
        _resolve(Path(str(item["path"])))
        for item in _teacher_artifacts(context)
    ]
    output_dir = artifacts[0].parent
    patterns = (
        "deep-hce-v2-results.jsonl.*",
        "deep-hce-v2-search.jsonl.*",
        "deep-hce-v2-static-hce.jsonl.*",
        "deep-hce-v2-residual.jsonl.*",
    )
    guard = deep.coordination_guard_path(artifacts[0])
    discovered = {
        path.resolve()
        for pattern in patterns
        for path in output_dir.glob(pattern)
        if path.resolve() != guard.resolve()
    }
    return sorted(set(artifacts).union(discovered), key=str)


def _check_teacher_absence(context: Mapping[str, Any]) -> None:
    present = [str(path) for path in _activity_paths(context) if path.exists()]
    if present:
        raise ValueError(
            "teacher activity already exists; use a new generation directory: "
            + ", ".join(present)
        )


def _legality_identity(context: Mapping[str, Any]) -> dict[str, Any]:
    selection = _mapping(
        _mapping(context.get("lock"), "strict freeze lock").get("selection"),
        "freeze selection",
    )
    return _mapping(
        selection.get("legalityValidationConfig"),
        "freeze legality config identity",
    )


def _build_manifest(
    *,
    repo: Path,
    profile_pin: Mapping[str, Any],
    phase_amendment_pin: Mapping[str, Any],
    generation2_amendment_pin: Mapping[str, Any],
    protocol_pin: Mapping[str, Any],
    context: Mapping[str, Any],
    prior_pins: Sequence[Mapping[str, Any]],
    inventory: Mapping[str, Mapping[str, Path]],
) -> dict[str, Any]:
    lock = _mapping(context.get("lock"), "strict freeze lock")
    suite = _mapping(context.get("suite"), "strict freeze suite")
    rejection = _validate_rejections(lock)
    static_hce = _identity(_static_hce_path(repo))
    _expect(
        static_hce["sha256"],
        legacy.CANONICAL_STATIC_HCE_SHA256,
        "canonical static HCE SHA-256",
    )
    _expect(
        static_hce["bytes"],
        legacy.CANONICAL_STATIC_HCE_BYTES,
        "canonical static HCE bytes",
    )
    initializer = _identity(_initializer_path(repo))
    frozen_engine = _mapping(
        context.get("engineIdentity"), "strict frozen engine identity"
    )
    source_engine = _mapping(
        _mapping(lock.get("freeze"), "freeze identities").get("sourceEngine"),
        "source engine identity",
    )
    suite_identity = _mapping(
        context.get("suiteIdentity"), "strict suite identity"
    )
    freeze_identity = _mapping(
        context.get("lockIdentity"), "strict freeze identity"
    )
    results = _resolve(Path(str(context["resultsPath"])))
    return {
        "schemaVersion": 1,
        # Required by the existing deep-HCE and sealed trainer readers.
        "kind": legacy.SEAL_KIND,
        "generationId": legacy.GENERATION_ID,
        "createdUtc": _utc_now(),
        "repositoryRoot": str(repo),
        "profile": {
            "schemaVersion": 1,
            "kind": PROFILE_KIND,
            "profileId": PROFILE_ID,
            "protocolGeneration": 2,
            "dataProfile": "deep-hce-v3",
            "directoryName": OUTPUT_DIRECTORY_NAME,
            "preregistration": dict(profile_pin),
            "legacyEnvelopeOnly": True,
            "effectiveBeforeTeacherLabels": True,
        },
        "contracts": {
            "labelsPermittedOnlyAfterSeal": True,
            "publication": "atomic hard-link, no clobber",
            "candidateIds": ["K0", "K1", "K2"],
            "candidateSelectionIncluded": False,
            "canonicalProtocolSha256": protocol_pin["sha256"],
            "canonicalStaticHceSha256": static_hce["sha256"],
            "canonicalStaticHceBytes": static_hce["bytes"],
            "teacherNodesPerRoot": int(suite["fixedNodes"]),
            "deepHceTargetPairs": int(suite["targetRootPairs"]),
            "deepHceTargetRoots": int(suite["targetRootPairs"]) * 2,
            "deepHceMaximumPreflightRejectedPairs": rejection["sanityCap"],
            "deepHceObservedPrelabelRejectedPairs": rejection["observedPairs"],
            "deepHceObservedDirectRejectedRoots": (
                rejection["observedDirectRoots"]
            ),
            "deepHceObservedCountFreezeDerived": True,
            "deepHceWholePairExclusionRequired": True,
            "splitGroupField": "groupId",
            "crossPhaseGroupsAllowed": True,
            "bootstrapUnit": "global leakage component groupId",
            "bootstrapStratification": "exact phase-incidence set",
            "canonicalDeploymentFloatRequired": True,
            "optimizerShadowPreserved": True,
            "deploymentFloatRequantizesByteIdentically": True,
            "candidateRecipesChanged": False,
            "promotionThresholdsChanged": False,
        },
        "coordination": {
            "path": str(deep.coordination_guard_path(results)),
            "owner": "king-state-v2-prelabel-seal",
            "convention": "Path(str(results_path) + '.coord.lock')",
            "heldContinuouslyFromBeforeActivityCheckThroughPublication": True,
        },
        "declaration": {
            "effectiveBeforeTeacherLabels": True,
            "teacherArtifactsAbsentAtDeclaration": True,
            "teacherArtifacts": _teacher_artifacts(context),
            "historicalFact": (
                "Every listed teacher artifact and discovered output sidecar "
                "was absent while the exclusive coordination guard was held "
                "through no-clobber publication."
            ),
        },
        "sourceInventory": _source_paths(inventory),
        "identities": {
            "protocol": dict(protocol_pin),
            "v2Preregistration": dict(profile_pin),
            "phaseIncidenceAmendment": dict(phase_amendment_pin),
            "generation2Amendment": dict(generation2_amendment_pin),
            "deepHceFreeze": dict(freeze_identity),
            "teacherSuite": dict(suite_identity),
            "frozenTeacherEngine": dict(frozen_engine),
            "sourceTeacherEngine": dict(source_engine),
            "teacherLegalityConfig": dict(_legality_identity(context)),
            "priorGenerationExclusions": [
                dict(pin) for pin in prior_pins
            ],
            "residualV3Initializer": initializer,
            "staticHceEvaluator": static_hce,
            **_source_pins(inventory),
        },
    }


def _walk_identities(
    value: Any, label: str = "seal.identities"
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, dict):
        if {"path", "bytes", "sha256"}.issubset(value):
            yield label, value
            return
        for key in sorted(value):
            yield from _walk_identities(value[key], f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_identities(item, f"{label}[{index}]")


def _verify_all_pins(identities: Mapping[str, Any]) -> int:
    count = 0
    for label, pin in _walk_identities(identities):
        actual = _identity(Path(str(pin["path"])))
        if not _same_identity(actual, pin):
            raise ValueError(f"{label} changed after it was pinned")
        count += 1
    if count == 0:
        raise ValueError("seal pins no identities")
    return count


def _seal(freeze_path: Path, output: Path) -> dict[str, Any]:
    repo = _repo_root().resolve()
    freeze_path = _resolve(freeze_path)
    expected_output = freeze_path.with_name(SEAL_NAME)
    _expect(_resolve(output), expected_output, "generation-2 seal output")
    profile, profile_pin = _load_json(
        _profile_path(repo), "generation-2 preregistration"
    )
    _validate_profile(profile, repo)
    phase_amendment_pin, generation2_amendment_pin = _validate_amendments(repo)
    _protocol, protocol_pin = _validate_protocol(repo)
    context, prior_pins = _validate_freeze(freeze_path, repo)
    inventory = _source_inventory(repo)
    results = _resolve(Path(str(context["resultsPath"])))
    lock_sha256 = str(context["lockSha256"])
    with deep.coordination_guard(
        results,
        "king-state-v2-prelabel-seal",
        lock_sha256=lock_sha256,
    ):
        # Re-read everything after acquiring the shared exclusion primitive.
        context, prior_pins = _validate_freeze(freeze_path, repo)
        profile, profile_pin = _load_json(
            _profile_path(repo), "generation-2 preregistration"
        )
        _validate_profile(profile, repo)
        (
            phase_amendment_pin,
            generation2_amendment_pin,
        ) = _validate_amendments(repo)
        _protocol, protocol_pin = _validate_protocol(repo)
        _check_teacher_absence(context)
        manifest = _build_manifest(
            repo=repo,
            profile_pin=profile_pin,
            phase_amendment_pin=phase_amendment_pin,
            generation2_amendment_pin=generation2_amendment_pin,
            protocol_pin=protocol_pin,
            context=context,
            prior_pins=prior_pins,
            inventory=inventory,
        )
        _verify_all_pins(_mapping(manifest["identities"], "manifest identities"))
        _check_teacher_absence(context)
        _atomic_json_no_clobber(expected_output, manifest)
    return manifest


def _verify_source_inventory(
    seal: Mapping[str, Any], repo: Path
) -> None:
    inventory = _source_inventory(repo)
    _expect(
        seal.get("sourceInventory"),
        _source_paths(inventory),
        "seal source inventory",
    )
    identities = _mapping(seal.get("identities"), "seal identities")
    for group, entries in inventory.items():
        pins = _mapping(identities.get(group), f"seal identities {group}")
        _expect(set(pins), set(entries), f"seal identities {group} names")
        for name, path in entries.items():
            expected = _identity(path)
            actual = _mapping(
                pins.get(name), f"seal identities {group}.{name}"
            )
            if not _same_identity(actual, expected):
                raise ValueError(f"seal source pin changed: {group}.{name}")


def _verify_seal(seal_path: Path) -> dict[str, Any]:
    repo = _repo_root().resolve()
    seal_path = _resolve(seal_path)
    expected_dir = (
        repo / "build-msvc" / "data-generation" / OUTPUT_DIRECTORY_NAME
    ).resolve()
    _expect(seal_path.parent, expected_dir, "generation-2 seal directory")
    _expect(seal_path.name, SEAL_NAME, "generation-2 seal filename")
    seal, _seal_pin = _load_json(seal_path, "generation-2 pre-label seal")
    _expect(seal.get("schemaVersion"), 1, "seal schema")
    _expect(seal.get("kind"), legacy.SEAL_KIND, "seal compatibility kind")
    _expect(
        seal.get("generationId"),
        legacy.GENERATION_ID,
        "seal compatibility generation",
    )
    profile_record = _mapping(seal.get("profile"), "seal profile")
    _expect(profile_record.get("kind"), PROFILE_KIND, "seal profile kind")
    _expect(profile_record.get("profileId"), PROFILE_ID, "seal profile id")
    _expect(
        profile_record.get("protocolGeneration"),
        2,
        "seal protocol generation",
    )
    _expect(
        profile_record.get("dataProfile"),
        "deep-hce-v3",
        "seal data profile",
    )
    _expect(
        profile_record.get("effectiveBeforeTeacherLabels"),
        True,
        "seal profile pre-label effectiveness",
    )
    profile, profile_pin = _load_json(
        _profile_path(repo), "generation-2 preregistration"
    )
    _validate_profile(profile, repo)
    phase_amendment_pin, generation2_amendment_pin = _validate_amendments(repo)
    if not _same_identity(
        _mapping(profile_record.get("preregistration"), "seal profile pin"),
        profile_pin,
    ):
        raise ValueError("seal pins a different generation-2 preregistration")

    identities = _mapping(seal.get("identities"), "seal identities")
    count = _verify_all_pins(identities)
    _verify_source_inventory(seal, repo)
    _protocol, protocol_pin = _validate_protocol(repo)
    if not _same_identity(
        _mapping(identities.get("protocol"), "seal protocol"),
        protocol_pin,
    ):
        raise ValueError("seal pins a different canonical protocol")
    freeze_path = Path(
        str(_mapping(identities.get("deepHceFreeze"), "seal freeze")["path"])
    )
    context, prior_pins = _validate_freeze(freeze_path, repo)
    for name, expected in {
        "deepHceFreeze": context["lockIdentity"],
        "teacherSuite": context["suiteIdentity"],
        "frozenTeacherEngine": context["engineIdentity"],
        "sourceTeacherEngine": context["lock"]["freeze"]["sourceEngine"],
        "teacherLegalityConfig": _legality_identity(context),
        "residualV3Initializer": _identity(_initializer_path(repo)),
        "staticHceEvaluator": _identity(_static_hce_path(repo)),
        "v2Preregistration": profile_pin,
        "phaseIncidenceAmendment": phase_amendment_pin,
        "generation2Amendment": generation2_amendment_pin,
    }.items():
        if not _same_identity(
            _mapping(identities.get(name), f"seal {name}"), expected
        ):
            raise ValueError(f"seal {name} differs from the strict context")
    sealed_prior = _sequence(
        identities.get("priorGenerationExclusions"),
        "seal prior-generation exclusions",
    )
    if len(sealed_prior) != len(prior_pins) or any(
        not _same_identity(_mapping(actual, "sealed prior exclusion"), expected)
        for actual, expected in zip(sealed_prior, prior_pins)
    ):
        raise ValueError(
            "seal prior-generation exclusion inventory differs from freeze"
        )

    contracts = _mapping(seal.get("contracts"), "seal contracts")
    rejection = _validate_rejections(context["lock"])
    for key, expected in {
        "labelsPermittedOnlyAfterSeal": True,
        "deepHceObservedPrelabelRejectedPairs": rejection["observedPairs"],
        "deepHceObservedDirectRejectedRoots": rejection["observedDirectRoots"],
        "deepHceMaximumPreflightRejectedPairs": rejection["sanityCap"],
        "deepHceObservedCountFreezeDerived": True,
        "crossPhaseGroupsAllowed": True,
        "bootstrapUnit": "global leakage component groupId",
        "bootstrapStratification": "exact phase-incidence set",
        "canonicalDeploymentFloatRequired": True,
        "optimizerShadowPreserved": True,
        "deploymentFloatRequantizesByteIdentically": True,
        "candidateRecipesChanged": False,
        "promotionThresholdsChanged": False,
    }.items():
        _expect(contracts.get(key), expected, f"seal contract {key}")
    declaration = _mapping(seal.get("declaration"), "seal declaration")
    _expect(
        declaration.get("teacherArtifactsAbsentAtDeclaration"),
        True,
        "seal historical artifact absence",
    )
    _expect(
        declaration.get("teacherArtifacts"),
        _teacher_artifacts(context),
        "seal teacher artifact inventory",
    )
    return {
        "seal": str(seal_path),
        "profileId": PROFILE_ID,
        "identitiesVerified": count,
        "observedPreflightRejectedPairs": rejection["observedPairs"],
        "teacherArtifactsPresentNow": sum(
            _resolve(Path(str(item["path"]))).exists()
            for item in declaration["teacherArtifacts"]
        ),
    }


def _self_test() -> None:
    repo = _repo_root()
    profile, _pin = _load_json(
        _profile_path(repo), "generation-2 preregistration"
    )
    _validate_profile(profile, repo)
    _validate_amendments(repo)
    _validate_protocol(repo)
    mandatory = _mandatory_forbidden_roots(repo)
    if len(mandatory) != 11 or any(not path.exists() for path in mandatory):
        raise AssertionError(
            "mandatory prior-generation forbidden-root inventory is incomplete"
        )
    exposed = {
        item.resolve()
        for path in mandatory
        for item in (
            [path]
            if path.is_file()
            else [*path.rglob("*.json"), *path.rglob("*.jsonl")]
        )
        if item.suffix.lower() in {".json", ".jsonl"}
    }
    if not exposed:
        raise AssertionError(
            "mandatory forbidden roots expose no JSON/JSONL artifacts"
        )

    def synthetic_lock(rejected_per_pair: Sequence[int]) -> dict[str, Any]:
        rejected_pairs = []
        raw = []
        for pair_index, direct_count in enumerate(rejected_per_pair):
            roots = []
            for root_index in range(2):
                direct = root_index < direct_count
                root_id = f"pair-{pair_index}-root-{root_index}"
                reason = (
                    f"synthetic direct rejection {pair_index}-{root_index}"
                    if direct
                    else COMPANION_REASON
                )
                roots.append(
                    {
                        "rootId": root_id,
                        "reason": reason,
                        "reasonSha256": _reason_sha(reason),
                        "directProtocolRejection": direct,
                    }
                )
                if direct:
                    raw.append({"rootId": root_id, "reason": reason})
            rejected_pairs.append(
                {
                    "phase": PHASES[pair_index % len(PHASES)],
                    "roots": roots,
                }
            )
        return {
            "selectionContract": {
                "preflightRejectedPairSanityCap": 32,
            },
            "selection": {
                "preflightRejectedPairs": len(rejected_pairs),
                "preflightRejectedRoots": len(raw),
                "preflightRejectedPairSanityCap": 32,
                "frozenCandidatePairs": 4352,
                "validation": {
                    "senpaiOfenAcceptance": {
                        "pairsRejected": len(rejected_pairs),
                        "positionsRejected": len(raw),
                        "rejectedPairs": rejected_pairs,
                        "rejectedRoots": raw,
                        "finalPairsAccepted": 4352,
                        "finalPositionsAccepted": 8704,
                    }
                },
            },
        }

    for counts in ([], [1], [2], [1, 2, 1, 1, 2]):
        audit = _validate_rejections(synthetic_lock(counts))
        _expect(audit["observedPairs"], len(counts), "synthetic rejection count")
        _expect(
            audit["observedDirectRoots"],
            sum(counts),
            "synthetic direct root count",
        )
    bad = synthetic_lock([1])
    bad["selection"]["validation"]["senpaiOfenAcceptance"]["pairsRejected"] = 18
    try:
        _validate_rejections(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("dynamic rejection audit accepted a stale count")
    print("king_state_v2 self-test passed", flush=True)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repo = _repo_root()
    output_dir = (
        repo / "build-msvc" / "data-generation" / OUTPUT_DIRECTORY_NAME
    )
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser(
        "seal", help="publish the generation-2 pre-label compatibility seal"
    )
    seal.add_argument(
        "--freeze", type=Path, default=output_dir / FREEZE_NAME
    )
    seal.add_argument("--output", type=Path, default=output_dir / SEAL_NAME)
    verify = subparsers.add_parser(
        "verify-seal",
        help="rehash the generation-2 profile and every pinned identity",
    )
    verify.add_argument("--seal", type=Path, default=output_dir / SEAL_NAME)
    subparsers.add_parser("self-test", help="run target-free policy checks")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "seal":
        manifest = _seal(args.freeze, args.output)
        print(
            f"Pre-label generation-2 profile sealed: {_resolve(args.output)} "
            f"({manifest['profile']['profileId']})",
            flush=True,
        )
    elif args.command == "verify-seal":
        report = _verify_seal(args.seal)
        print(
            f"Verified {report['identitiesVerified']} identities; "
            f"observed preflight rejected pairs="
            f"{report['observedPreflightRejectedPairs']}; "
            f"teacher artifacts present now="
            f"{report['teacherArtifactsPresentNow']}.",
            flush=True,
        )
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
