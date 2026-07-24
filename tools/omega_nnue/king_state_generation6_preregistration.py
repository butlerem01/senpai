#!/usr/bin/env python3
"""Publish the unique Generation-6 training preregistration.

This is the only production scaffolder for ``build-msvc/king-state-v6``.
It has no output, source, runtime, or timestamp arguments.  Publication is
permitted only after the canonical decision-v3 capsule and its independently
published fresh-verification receipt exist and replay exactly under the same
Python executable that will run Generation 6.

The publisher never decodes a teacher target or a game result.  Target-bearing
artifacts remain behind the exact Generation-6 authority/verifier boundary.
The three files owned by this tool are created with descriptor-verified
``O_EXCL`` writes and are removed on a failed final verification only when the
published path still names the inode created by this invocation.

The fresh-verification receipt is independently published upstream.  This
tool does not own, delete, or serialize an inode claim for that receipt;
instead each publish/verify operation requires its canonical path, byte count,
and SHA-256 identity to remain stable from the initial snapshot through the
final return boundary.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence

try:
    from . import king_state_train_generation6 as training
except ImportError:  # Trusted sibling import for direct ``python -I -B`` use.
    _TOOL_DIRECTORY = str(
        Path(os.path.abspath(os.path.normpath(os.fspath(Path(__file__))))).parent
    )
    if _TOOL_DIRECTORY not in sys.path:
        sys.path.insert(0, _TOOL_DIRECTORY)
    import king_state_train_generation6 as training


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
PUBLICATION_KIND = "omega-nnue-king-state-v6-preregistration-publication"

_SOURCE_PATH = Path(
    os.path.abspath(os.path.normpath(os.fspath(Path(__file__).expanduser())))
)
REPO = _SOURCE_PATH.parents[2]
CANONICAL_NAMESPACE = REPO / "build-msvc" / "king-state-v6"
CANONICAL_PREREGISTRATION = CANONICAL_NAMESPACE / "00-preregistration.json"
CANONICAL_CAPSULE = (
    REPO
    / "build-msvc"
    / "data-generation"
    / "omega-decision-v3"
    / "capsule.closure.json"
)
CANONICAL_VERIFICATION_RECEIPT = (
    CANONICAL_CAPSULE.parent
    / "80-capsule"
    / "decision-v3.verification.json"
)
CANONICAL_PROTOCOL = (
    REPO / "validation" / "omega-nnue-king-state-v6-training-protocol.json"
)
CANONICAL_RUNTIME_MANIFEST = (
    REPO / "validation" / "omega-nnue-king-state-v6-python-runtime.json"
)
CANONICAL_EVALUATOR_OPTIONS = (
    REPO / "validation" / "omega-nnue-king-state-v6-evaluator-options.json"
)
CONTRACT_SOURCE = REPO / "tools" / "omega_nnue" / "king_state_train_generation6.py"
TRAINER_RUNNER = REPO / "tools" / "omega_nnue" / "omega_decision_v3_trainer_runner.py"
EVALUATOR_RUNNER = (
    REPO / "tools" / "omega_nnue" / "omega_decision_v3_evaluator_runner.py"
)
UPSTREAM_VERIFIER_RUNNER = (
    REPO / "tools" / "omega_nnue" / "verify_omega_decision_v3_upstream.py"
)
G5_EARLY_TERMINAL_PROTOCOL = (
    REPO
    / "validation"
    / "omega-nnue-king-state-v5-early-terminal-protocol.json"
)
G5_EARLY_TERMINAL_TOOL = (
    REPO
    / "tools"
    / "omega_nnue"
    / "king_state_generation5_early_terminal.py"
)
G5_COLOR_COMPAT_READINESS = (
    REPO
    / "tools"
    / "omega_nnue"
    / "king_state_match_readiness_generation5_compat_v2.py"
)
G5_COLOR_COMPAT_PROTOCOL = (
    REPO
    / "validation"
    / "omega-nnue-king-state-v5-color-compat-protocol.json"
)
G5_COLOR_COMPAT_TEMPLATE = (
    REPO
    / "validation"
    / "omega-nnue-king-state-v5-color-compat-preregistration.template.json"
)

_OWNED_OUTPUTS = (
    CANONICAL_RUNTIME_MANIFEST,
    CANONICAL_EVALUATOR_OPTIONS,
    CANONICAL_PREREGISTRATION,
)
_PUBLICATION_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "preregistration",
        "runtimeManifest",
        "evaluatorOptions",
        "upstreamVerificationReceipt",
        "resultInformationRead",
        "targetRowsDecodedByPublisher",
        "targetFieldsDecodedByPublisher",
        "gameResultsRead",
    }
)


def _require_isolated_runtime() -> None:
    if sys.flags.isolated != 1 or not sys.dont_write_bytecode:
        raise RuntimeError(
            "Generation-6 preregistration requires pinned Python -I -B"
        )
    if (
        training.SCHEMA_VERSION != SCHEMA_VERSION
        or training.PROFILE_ID != PROFILE_ID
        or training._lexical_absolute(training.DEFAULT_NAMESPACE_ROOT)
        != training._lexical_absolute(CANONICAL_NAMESPACE)
        or training._lexical_absolute(training.DEFAULT_PREREGISTRATION)
        != training._lexical_absolute(CANONICAL_PREREGISTRATION)
    ):
        raise ValueError("publisher and Generation-6 authority identities diverged")
    actual_contract = training._lexical_absolute(Path(training.__file__))
    if actual_contract != training._lexical_absolute(CONTRACT_SOURCE):
        raise ValueError("loaded Generation-6 authority is outside its canonical path")
    training._safe_existing_file(Path(__file__))


def _snapshot_json(
    path: Path, label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity, payload = training._snapshot_file(path)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    value = training._strict_json_loads(text, location=str(identity["path"]))
    if type(value) is not dict:
        raise ValueError(f"{label} is not an object")
    return identity, value


def _record_path(value: Any, label: str) -> Path:
    record = training._verify_identity_record(value, label)
    return Path(record["path"])


def _payload_identity(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": str(training._lexical_absolute(path)),
        "bytes": len(payload),
        "sha256": training._sha256_bytes(payload),
    }


def _deterministic_created_utc(*timestamps: str) -> str:
    """Return exactly one microsecond after the latest sealed predecessor."""

    latest = max(
        training._parse_timestamp(value, "preregistration predecessor createdUtc")
        for value in timestamps
    )
    return (latest + timedelta(microseconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _assert_protocol_and_sources() -> dict[str, dict[str, Any]]:
    protocol_identity, protocol = _snapshot_json(
        CANONICAL_PROTOCOL, "Generation-6 training protocol"
    )
    if not training._type_exact_equal(protocol, training.protocol_document()):
        raise ValueError("canonical Generation-6 protocol differs from current authority")
    early_policy = training.UPSTREAM_VERIFIER_OPTIONS["initializerPolicy"][
        "g5EarlyTerminalAuthority"
    ]
    compatibility_policy = early_policy["compatibilityAuthority"]
    for label, record, path in (
        ("early-terminal protocol", early_policy["protocol"], G5_EARLY_TERMINAL_PROTOCOL),
        ("early-terminal tool", early_policy["tool"], G5_EARLY_TERMINAL_TOOL),
        (
            "compatibility readiness",
            compatibility_policy["readiness"],
            G5_COLOR_COMPAT_READINESS,
        ),
        (
            "compatibility protocol",
            compatibility_policy["protocol"],
            G5_COLOR_COMPAT_PROTOCOL,
        ),
        (
            "compatibility preregistration template",
            compatibility_policy["preregistrationTemplate"],
            G5_COLOR_COMPAT_TEMPLATE,
        ),
    ):
        training._exact_keys(
            record,
            {"relativePath", "bytes", "sha256"},
            f"G5 {label} policy",
        )
        identity = training._identity(path)
        expected = {
            "relativePath": path.relative_to(REPO).as_posix(),
            "bytes": identity["bytes"],
            "sha256": identity["sha256"],
        }
        if not training._type_exact_equal(record, expected):
            raise ValueError(
                f"G5 {label} differs from its Generation-6 exact pin"
            )
    return {
        "protocol": protocol_identity,
        "contractSource": training._identity(CONTRACT_SOURCE),
        "trainerRunner": training._identity(TRAINER_RUNNER),
        "evaluatorRunner": training._identity(EVALUATOR_RUNNER),
        "upstreamVerifierRunner": training._identity(UPSTREAM_VERIFIER_RUNNER),
    }


def _authority_from_capsule(
    capsule: Mapping[str, Any],
) -> tuple[training.VerifiedAuthority, dict[str, Path]]:
    if type(capsule) is not dict:
        raise ValueError("canonical decision-v3 capsule is not an object")
    training._exact_keys(
        capsule, training.UPSTREAM_CAPSULE_FIELDS, "canonical decision-v3 capsule"
    )
    paths = {
        field: _record_path(capsule[field], f"capsule {field}")
        for field in (
            "projectedCorpus",
            "labelManifest",
            "componentMap",
            "staticHceTranscript",
            "staticHceManifest",
            "staticHceEngine",
            "staticHceRunner",
            "staticHceOptions",
            "initializerModel",
            "initializerManifest",
            "upstreamVerifierExecutable",
            "upstreamVerifierRunner",
            "upstreamVerifierOptions",
        )
    }
    authority = training.verify_training_authority(
        corpus=paths["projectedCorpus"],
        authority=training.TrainingAuthority(
            label_manifest=paths["labelManifest"],
            component_map=paths["componentMap"],
            hce_projection=paths["staticHceTranscript"],
            hce_manifest=paths["staticHceManifest"],
            hce_engine=paths["staticHceEngine"],
            hce_runner=paths["staticHceRunner"],
            hce_options=paths["staticHceOptions"],
        ),
    )
    return authority, paths


def _candidate_documents() -> tuple[
    dict[str, Any], bytes, bytes, dict[str, Any], dict[str, Any]
]:
    """Build and replay the exact documents without publishing a pathname."""

    _require_isolated_runtime()
    source_identities = _assert_protocol_and_sources()
    capsule_identity, capsule = _snapshot_json(
        CANONICAL_CAPSULE, "canonical decision-v3 capsule"
    )
    authority, capsule_paths = _authority_from_capsule(capsule)

    if capsule_paths["upstreamVerifierRunner"] != training._lexical_absolute(
        UPSTREAM_VERIFIER_RUNNER
    ):
        raise ValueError("capsule verifier runner is outside its canonical path")

    initializer = training._verify_initializer_manifest(
        capsule_paths["initializerManifest"], capsule_paths["initializerModel"]
    )
    runtime_document = training.runtime_manifest_document()
    runtime_payload = training._canonical_json(runtime_document)
    evaluator_document = training.evaluator_options_document()
    evaluator_payload = training._canonical_json(evaluator_document)

    runtime_executable = runtime_document["pythonExecutable"]
    if not training._type_exact_equal(
        runtime_executable, capsule["upstreamVerifierExecutable"]
    ):
        raise ValueError(
            "executing Python differs from the capsule's pinned verifier runtime"
        )

    created_utc = _deterministic_created_utc(
        str(capsule["createdUtc"]),
        authority.hce_created_utc,
        str(initializer["createdUtc"]),
    )
    preregistration = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": training.PREREGISTRATION_KIND,
        "profileId": PROFILE_ID,
        "status": "frozen-single-lineage-before-generation6-training",
        "createdUtc": created_utc,
        "namespace": str(training._lexical_absolute(CANONICAL_NAMESPACE)),
        "artifactPaths": dict(training.CANONICAL_ARTIFACT_PATHS),
        "protocol": dict(source_identities["protocol"]),
        "contractSource": dict(source_identities["contractSource"]),
        "upstreamCapsule": capsule_identity,
        "upstreamVerifierExecutable": dict(capsule["upstreamVerifierExecutable"]),
        "upstreamVerifierRunner": dict(capsule["upstreamVerifierRunner"]),
        "upstreamVerifierOptions": dict(capsule["upstreamVerifierOptions"]),
        "corpus": dict(capsule["projectedCorpus"]),
        "labelManifest": dict(capsule["labelManifest"]),
        "componentMap": dict(capsule["componentMap"]),
        "staticHceProjection": dict(capsule["staticHceTranscript"]),
        "staticHceManifest": dict(capsule["staticHceManifest"]),
        "staticHceEngine": dict(capsule["staticHceEngine"]),
        "staticHceRunner": dict(capsule["staticHceRunner"]),
        "staticHceOptions": dict(capsule["staticHceOptions"]),
        "trainerExecutable": dict(runtime_executable),
        "trainerRunner": dict(source_identities["trainerRunner"]),
        "runtimeManifest": _payload_identity(
            CANONICAL_RUNTIME_MANIFEST, runtime_payload
        ),
        "trainerCommandProtocol": dict(training.TRAINER_COMMAND_PROTOCOL),
        "evaluatorExecutable": dict(runtime_executable),
        "evaluatorRunner": dict(source_identities["evaluatorRunner"]),
        "evaluatorOptions": _payload_identity(
            CANONICAL_EVALUATOR_OPTIONS, evaluator_payload
        ),
        "initializerModel": dict(capsule["initializerModel"]),
        "initializerManifest": dict(capsule["initializerManifest"]),
        "optimizerProtocol": dict(training.OPTIMIZER_PROTOCOL),
        "candidateRecipes": {
            candidate: dict(training.CANDIDATE_RECIPES[candidate])
            for candidate in training.CANDIDATES
        },
        "primaryTrainingSeeds": {
            candidate: training._domain_seed(
                training.PRIMARY_TRAINING_SEED_BASE,
                "primary-training",
                candidate,
            )
            for candidate in training.CANDIDATES
        },
        "robustnessTrainingSeeds": {
            candidate: training._domain_seed(
                training.ROBUSTNESS_TRAINING_SEED_BASE,
                "robustness-training",
                candidate,
            )
            for candidate in training.CANDIDATES
        },
        "authoritySha256": authority.binding_sha256,
        "rootInventories": {
            split: dict(authority.binding["rootInventories"][split])
            for split in training.SPLITS
        },
        "phaseSideInventories": {
            split: {
                cell: dict(authority.binding["phaseSideInventories"][split][cell])
                for cell in training._CELL_KEYS
            }
            for split in training.SPLITS
        },
        "resultInformationRead": False,
        "heldOutTargetRowsDecodedAtFreeze": 0,
        "heldOutTargetFieldsDecodedAtFreeze": 0,
    }
    training._exact_keys(
        preregistration,
        training.PREREGISTRATION_FIELDS,
        "candidate Generation-6 preregistration",
    )

    # This is the trusted internal verifier boundary described by the G6
    # protocol.  The publisher itself retains no target or result values.
    _, fresh_verification = training._verify_upstream_capsule(
        CANONICAL_CAPSULE,
        authority=authority,
        preregistration=preregistration,
    )
    receipt_identity, receipt = _snapshot_json(
        CANONICAL_VERIFICATION_RECEIPT,
        "canonical decision-v3 fresh-verification receipt",
    )
    if not training._type_exact_equal(receipt, fresh_verification):
        raise ValueError(
            "canonical decision-v3 verification receipt differs from fresh replay"
        )
    return (
        preregistration,
        runtime_payload,
        evaluator_payload,
        receipt_identity,
        fresh_verification,
    )


def _preflight_absent() -> None:
    present = [str(path) for path in _OWNED_OUTPUTS if os.path.lexists(path)]
    if present:
        raise FileExistsError(
            "refusing to replace Generation-6 publication: " + ", ".join(present)
        )
    if os.path.lexists(CANONICAL_NAMESPACE):
        safe = training._safe_path(CANONICAL_NAMESPACE, regular_file=False)
        if not safe.is_dir():
            raise ValueError("canonical Generation-6 namespace is not a directory")
        with os.scandir(safe) as entries:
            if next(entries, None) is not None:
                raise ValueError("canonical Generation-6 namespace is not empty")


def _prepare_namespace(created: list[Path]) -> None:
    """Create missing canonical directories, recording each one immediately."""

    for directory in (REPO / "build-msvc", CANONICAL_NAMESPACE):
        if os.path.lexists(directory):
            safe = training._safe_path(directory, regular_file=False)
            if not safe.is_dir():
                raise ValueError(f"canonical publisher parent is not a directory: {safe}")
            continue
        parent = training._safe_path(directory.parent, regular_file=False)
        if not parent.is_dir():
            raise FileNotFoundError(f"canonical publisher parent is absent: {parent}")
        os.mkdir(directory, 0o755)
        created.append(directory)
        training._safe_path(directory, regular_file=False)


def _output_paths() -> dict[str, Path]:
    return {
        "preregistration": CANONICAL_PREREGISTRATION,
        "runtimeManifest": CANONICAL_RUNTIME_MANIFEST,
        "evaluatorOptions": CANONICAL_EVALUATOR_OPTIONS,
    }


def _recheck_output_snapshots(
    snapshots: Mapping[str, tuple[Mapping[str, Any], tuple[int, int]]]
) -> None:
    """Prove that every result path still names its intended private inode."""

    paths = _output_paths()
    if set(snapshots) != set(paths):
        raise ValueError("publisher output snapshot inventory changed")
    for field, path in paths.items():
        expected_identity, expected_inode = snapshots[field]
        actual_identity, actual_inode = training._identity_with_inode(path)
        if (
            actual_inode != expected_inode
            or not training._type_exact_equal(actual_identity, expected_identity)
        ):
            raise ValueError(f"publisher output changed before return: {field}")


def _rollback_owned(
    published: Sequence[tuple[Path, tuple[int, int]]], created_dirs: Sequence[Path]
) -> None:
    failures: list[str] = []
    for path, inode in reversed(tuple(published)):
        try:
            info = os.lstat(path)
            if (info.st_dev, info.st_ino) != inode or not stat.S_ISREG(info.st_mode):
                failures.append(f"refused changed publication path {path}")
                continue
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            failures.append(f"{path}: {error}")
    for directory in reversed(tuple(created_dirs)):
        try:
            os.rmdir(directory)
        except FileNotFoundError:
            pass
        except OSError:
            # A concurrently-created or pre-existing child is not ours.
            pass
    if failures:
        raise RuntimeError("publication rollback was incomplete: " + "; ".join(failures))


def _publication_result(
    *,
    receipt_identity: Mapping[str, Any],
    output_identities: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    paths = _output_paths()
    if set(output_identities) != set(paths):
        raise ValueError("publication result output inventory changed")
    checked: dict[str, dict[str, Any]] = {}
    for field, path in paths.items():
        identity = training._verify_identity_record(
            output_identities[field], f"publication result {field}"
        )
        if Path(identity["path"]) != training._lexical_absolute(path):
            raise ValueError(f"publication result {field} is outside its canonical path")
        checked[field] = dict(identity)
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PUBLICATION_KIND,
        "profileId": PROFILE_ID,
        "status": "published-and-freshly-verified",
        "preregistration": checked["preregistration"],
        "runtimeManifest": checked["runtimeManifest"],
        "evaluatorOptions": checked["evaluatorOptions"],
        "upstreamVerificationReceipt": dict(receipt_identity),
        "resultInformationRead": False,
        "targetRowsDecodedByPublisher": 0,
        "targetFieldsDecodedByPublisher": 0,
        "gameResultsRead": False,
    }
    training._exact_keys(result, _PUBLICATION_FIELDS, "publication result")
    return result


def _verify_publisher_paths(document: Mapping[str, Any]) -> None:
    expected = {
        "runtimeManifest": CANONICAL_RUNTIME_MANIFEST,
        "evaluatorOptions": CANONICAL_EVALUATOR_OPTIONS,
        "contractSource": CONTRACT_SOURCE,
        "trainerRunner": TRAINER_RUNNER,
        "evaluatorRunner": EVALUATOR_RUNNER,
        "upstreamCapsule": CANONICAL_CAPSULE,
        "upstreamVerifierRunner": UPSTREAM_VERIFIER_RUNNER,
    }
    for field, path in expected.items():
        actual = _record_path(document[field], f"preregistered {field}")
        if actual != training._lexical_absolute(path):
            raise ValueError(f"preregistered {field} is outside its canonical path")


def publish_canonical_preregistration() -> dict[str, Any]:
    """Publish the runtime/options/preregistration set at unique canonical paths."""

    _require_isolated_runtime()
    _preflight_absent()
    (
        preregistration,
        runtime_payload,
        evaluator_payload,
        receipt_identity,
        _,
    ) = _candidate_documents()
    preregistration_payload = training._canonical_json(preregistration)
    expected_outputs = {
        "preregistration": _payload_identity(
            CANONICAL_PREREGISTRATION, preregistration_payload
        ),
        "runtimeManifest": _payload_identity(
            CANONICAL_RUNTIME_MANIFEST, runtime_payload
        ),
        "evaluatorOptions": _payload_identity(
            CANONICAL_EVALUATOR_OPTIONS, evaluator_payload
        ),
    }

    # Repeat the absence check after the potentially long semantic replay.
    _preflight_absent()
    created_dirs: list[Path] = []
    published: list[tuple[Path, tuple[int, int]]] = []
    published_snapshots: dict[
        str, tuple[Mapping[str, Any], tuple[int, int]]
    ] = {}
    try:
        _prepare_namespace(created_dirs)
        for field, path, payload in (
            ("runtimeManifest", CANONICAL_RUNTIME_MANIFEST, runtime_payload),
            ("evaluatorOptions", CANONICAL_EVALUATOR_OPTIONS, evaluator_payload),
            ("preregistration", CANONICAL_PREREGISTRATION, preregistration_payload),
        ):
            written, inode = training._exclusive_bytes_owned(path, payload)
            expected = expected_outputs[field]
            if not training._type_exact_equal(written, expected):
                raise ValueError(f"O_EXCL publication identity changed: {field}")
            published.append((path, inode))
            published_snapshots[field] = (expected, inode)

        registry = training.verify_canonical_namespace()
        _verify_publisher_paths(registry.document)
        receipt_after = training._identity(CANONICAL_VERIFICATION_RECEIPT)
        if not training._type_exact_equal(receipt_after, receipt_identity):
            raise ValueError("decision-v3 verification receipt changed during publication")
        _recheck_output_snapshots(published_snapshots)
        result = _publication_result(
            receipt_identity=receipt_identity,
            output_identities=expected_outputs,
        )
        receipt_final = training._identity(CANONICAL_VERIFICATION_RECEIPT)
        if not training._type_exact_equal(receipt_final, receipt_identity):
            raise ValueError("decision-v3 verification receipt changed before return")
        # Keep this as the final filesystem operation before returning.  The
        # result above contains the intended identities, never opportunistic
        # re-reads of a pathname that may have been replaced after validation.
        _recheck_output_snapshots(published_snapshots)
        return result
    except BaseException as error:
        try:
            _rollback_owned(published, created_dirs)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"Generation-6 publication failed ({error}); rollback also failed"
            ) from rollback_error
        raise


def verify_canonical_preregistration() -> dict[str, Any]:
    """Freshly verify the exact canonical publication and upstream receipt."""

    _require_isolated_runtime()
    _assert_protocol_and_sources()
    registry = training.verify_canonical_namespace()
    _verify_publisher_paths(registry.document)
    receipt_identity, receipt = _snapshot_json(
        CANONICAL_VERIFICATION_RECEIPT,
        "canonical decision-v3 fresh-verification receipt",
    )
    if not training._type_exact_equal(receipt, registry.upstream_verification):
        raise ValueError(
            "canonical decision-v3 verification receipt differs from fresh replay"
        )
    verified_snapshots = {
        field: training._identity_with_inode(path)
        for field, path in _output_paths().items()
    }
    result = _publication_result(
        receipt_identity=receipt_identity,
        output_identities={
            field: identity for field, (identity, _) in verified_snapshots.items()
        },
    )
    receipt_final = training._identity(CANONICAL_VERIFICATION_RECEIPT)
    if not training._type_exact_equal(receipt_final, receipt_identity):
        raise ValueError("decision-v3 verification receipt changed before return")
    _recheck_output_snapshots(verified_snapshots)
    return result


def readiness_document() -> dict[str, Any]:
    """Report canonical prerequisites without reading target-bearing contents."""

    training._safe_existing_file(Path(__file__))
    required = {
        "capsule": CANONICAL_CAPSULE,
        "verificationReceipt": CANONICAL_VERIFICATION_RECEIPT,
        "initializerModel": CANONICAL_CAPSULE.parent
        / "40-initializer"
        / "initializer.nnue",
        "initializerManifest": CANONICAL_CAPSULE.parent
        / "40-initializer"
        / "initializer.manifest.json",
        "protocol": CANONICAL_PROTOCOL,
        "contractSource": CONTRACT_SOURCE,
        "trainerRunner": TRAINER_RUNNER,
        "evaluatorRunner": EVALUATOR_RUNNER,
        "upstreamVerifierRunner": UPSTREAM_VERIFIER_RUNNER,
        "g5EarlyTerminalProtocol": G5_EARLY_TERMINAL_PROTOCOL,
        "g5EarlyTerminalTool": G5_EARLY_TERMINAL_TOOL,
        "g5ColorCompatReadiness": G5_COLOR_COMPAT_READINESS,
        "g5ColorCompatProtocol": G5_COLOR_COMPAT_PROTOCOL,
        "g5ColorCompatTemplate": G5_COLOR_COMPAT_TEMPLATE,
    }
    present: dict[str, bool] = {}
    for name, path in required.items():
        try:
            training._safe_existing_file(path)
        except (OSError, ValueError):
            present[name] = False
        else:
            present[name] = True
    early_policy = training.UPSTREAM_VERIFIER_OPTIONS["initializerPolicy"][
        "g5EarlyTerminalAuthority"
    ]
    compatibility_policy = early_policy["compatibilityAuthority"]
    lifecycle_pins = {
        "g5EarlyTerminalProtocol": early_policy["protocol"],
        "g5EarlyTerminalTool": early_policy["tool"],
        "g5ColorCompatReadiness": compatibility_policy["readiness"],
        "g5ColorCompatProtocol": compatibility_policy["protocol"],
        "g5ColorCompatTemplate": compatibility_policy["preregistrationTemplate"],
    }
    for name, record in lifecycle_pins.items():
        if not present[name]:
            continue
        identity = training._identity(required[name])
        present[name] = (
            identity["bytes"] == record["bytes"]
            and identity["sha256"] == record["sha256"]
            and required[name].relative_to(REPO).as_posix()
            == record["relativePath"]
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "omega-nnue-king-state-v6-preregistration-readiness",
        "profileId": PROFILE_ID,
        "status": "ready-to-publish" if all(present.values()) else "awaiting-prerequisites",
        "requiredPaths": {name: str(path) for name, path in required.items()},
        "present": present,
        "ownedOutputsAbsent": not any(os.path.lexists(path) for path in _OWNED_OUTPUTS),
        "targetRowsDecoded": 0,
        "targetFieldsDecoded": 0,
        "gameResultsRead": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("readiness", "publish", "verify"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "readiness":
        result = readiness_document()
    elif args.command == "publish":
        result = publish_canonical_preregistration()
    elif args.command == "verify":
        result = verify_canonical_preregistration()
    else:  # pragma: no cover
        raise AssertionError(args.command)
    sys.stdout.buffer.write(training._canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
