#!/usr/bin/env python3
"""Create or verify the target-blind Generation 5 final freeze.

Creation resolves every preregistration placeholder from bytes already on
disk, validates the completed profile (including every external identity),
and publishes the profile plus its adjacent seal without replacing either.
It refuses to run after any Generation 5 teacher-search artifact exists.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

import validate_king_state_v5_preregistration as prereg


REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "validation/omega-nnue-king-state-v5-preregistration.template.json"
OUTPUT = REPO / "validation/omega-nnue-king-state-v5-preregistration.json"
SEAL = REPO / "validation/omega-nnue-king-state-v5-freeze.seal.json"
KIND = "omega-nnue-king-state-v5-final-freeze-seal"
STATUS = "target-blind-generation-5-design-frozen-before-teacher-labels"
GENERATION4_CLOSURE_PATH = (
    "validation/omega-nnue-king-state-v4-structural-abort.seal.json"
)
GENERATION4_ABORT_VERIFIER_PATH = (
    "tools/omega_nnue/king_state_generation4_abort.py"
)
SOURCE_MATCH_COMPLETION_SEAL_PATH = (
    "build-msvc/data-generation/omega-decision-v2/source/"
    "source-match.complete.seal.json"
)
SOURCE_WRAPPER_PATH = "tools/omega_nnue/king_state_generation5_source.py"
SOURCE_DATA_INPUT_IDENTITY_FIELDS = (
    "source",
    "openingSuite",
    "sourceMatchConfig",
    "sourceRootPool",
    "sourceRootPoolManifest",
    "sourceRootPoolSeal",
)
REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS = (
    "sourceMatchHarnessAssembly",
    "rootSamplerAssembly",
    "rootSamplerChessLibAssembly",
    "sourceOpeningBuilderSource",
)
SOURCE_LAUNCH_PROVENANCE_IDENTITY_FIELDS = ("sourceMatchCompletionSeal",)
EXPECTED_SOURCE_DATA_DISJOINTNESS = {
    "policyId": "omega-source-data-input-disjointness-v1",
    "dataInputIdentityFields": list(SOURCE_DATA_INPUT_IDENTITY_FIELDS),
    "reusableInfrastructureIdentityFields": list(
        REUSABLE_SOURCE_INFRASTRUCTURE_IDENTITY_FIELDS
    ),
    "launchProvenanceIdentityFields": list(
        SOURCE_LAUNCH_PROVENANCE_IDENTITY_FIELDS
    ),
    "currentAndPriorDataInputDomainsIdentical": True,
    "priorDataInputsWithoutOfenAuthenticatedByStructuralClosure": True,
    "reusableInfrastructureContentAuthenticated": True,
    "reusableInfrastructureExcludedFromCollisionSet": True,
    "catalogSourceArtifactHashesExcludedFromDataCollisionSet": True,
    "legacyG3TransitiveArtifactHashesAuthenticatedButExcludedFromDataCollisionSet": True,
    "launchProvenanceAuthenticatedAndExcludedFromCollisionSet": True,
}
_TEACHER_OUTPUTS = (
    "build-msvc/data-generation/omega-decision-v2/shallow-results.jsonl",
    "build-msvc/data-generation/omega-decision-v2/selected-children.jsonl",
    "build-msvc/data-generation/omega-decision-v2/deep-results.jsonl",
    "build-msvc/data-generation/omega-decision-v2/decision-labels.jsonl",
)
_TEACHER_COMPANION_SUFFIXES = (
    "",
    ".lock.json",
    ".active.claim.json",
    ".manifest.json",
    ".complete.manifest.json",
)
TARGET_ARTIFACTS = tuple(
    output + suffix
    for output in _TEACHER_OUTPUTS
    for suffix in _TEACHER_COMPANION_SUFFIXES
) + (
    "build-msvc/data-generation/omega-decision-v2/component-splits.jsonl",
    "build-msvc/data-generation/omega-decision-v2/prelabel-freeze.seal.json",
    "build-msvc/king-state-v5/training-plan.json",
    "build-msvc/king-state-v5/.G5A.training.claim.json",
    "build-msvc/king-state-v5/.G5B.training.claim.json",
    "build-msvc/king-state-v5/.G5C.training.claim.json",
    "build-msvc/king-state-v5/G5A.failure.json",
    "build-msvc/king-state-v5/G5B.failure.json",
    "build-msvc/king-state-v5/G5C.failure.json",
    "build-msvc/king-state-v5/G5A.bundle",
    "build-msvc/king-state-v5/G5B.bundle",
    "build-msvc/king-state-v5/G5C.bundle",
    "build-msvc/king-state-v5/validation-selection.seal.json",
    "build-msvc/king-state-v5/.robustness.training.claim.json",
    "build-msvc/king-state-v5/robustness.failure.json",
    "build-msvc/king-state-v5/robustness.bundle",
    "build-msvc/king-state-v5/robustness.seal.json",
    "build-msvc/king-state-v5/offline/access-claim.json",
    "build-msvc/king-state-v5/offline/report.json",
)
TARGET_GLOBS = tuple(
    f"build-msvc/data-generation/omega-decision-v2/.{Path(output).name}.*.tmp"
    for output in _TEACHER_OUTPUTS
) + (
    "build-msvc/king-state-v5/.G5A.bundle-staging-*",
    "build-msvc/king-state-v5/.G5B.bundle-staging-*",
    "build-msvc/king-state-v5/.G5C.bundle-staging-*",
    "build-msvc/king-state-v5/.robustness.bundle-staging-*",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number {value!r} in {path}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicates,
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path, *, reported_path: str | None = None) -> dict[str, Any]:
    path = path.expanduser().resolve()
    stat = path.stat()
    return {
        "path": reported_path if reported_path is not None else str(path),
        "bytes": stat.st_size,
        "sha256": _sha256(path),
    }


def _repo_path(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise ValueError(f"identity path is not repo-relative POSIX: {value!r}")
    path = (REPO / Path(*pure.parts)).resolve()
    try:
        path.relative_to(REPO.resolve())
    except ValueError as error:
        raise ValueError(f"identity path escapes repository: {value!r}") from error
    return path


def _require_identity(
    profile: Mapping[str, Any], name: str, relative: str
) -> dict[str, Any]:
    identities = profile.get("finalFreezeIdentities")
    if not isinstance(identities, Mapping):
        raise ValueError("finalFreezeIdentities is malformed")
    record = identities.get(name)
    if not isinstance(record, Mapping) or set(record) != {
        "path",
        "bytes",
        "sha256",
    }:
        raise ValueError(f"final identity {name} is malformed")
    if record.get("path") != relative:
        raise ValueError(f"final identity {name} path changed")
    actual = _identity(_repo_path(relative), reported_path=relative)
    if dict(record) != actual:
        raise ValueError(f"final identity {name} content changed")
    return actual


def _validate_source_data_contract(profile: Mapping[str, Any]) -> None:
    fresh = profile.get("freshDecisionCorpus")
    source = fresh.get("source") if isinstance(fresh, Mapping) else None
    declaration = (
        source.get("sourceDataDisjointness")
        if isinstance(source, Mapping)
        else None
    )
    if declaration != EXPECTED_SOURCE_DATA_DISJOINTNESS:
        raise ValueError("source-data disjointness declaration changed")


def _validate_generation4_binding(profile: Mapping[str, Any]) -> None:
    closure_record = profile.get("generation4Closure")
    if not isinstance(closure_record, Mapping) or set(closure_record) != {
        "path",
        "bytes",
        "sha256",
    }:
        raise ValueError("Generation-4 closure identity is malformed")
    if closure_record.get("path") != GENERATION4_CLOSURE_PATH:
        raise ValueError("Generation-4 closure path changed")
    if dict(closure_record) != _identity(
        _repo_path(GENERATION4_CLOSURE_PATH),
        reported_path=GENERATION4_CLOSURE_PATH,
    ):
        raise ValueError("Generation-4 closure content changed")
    verifier = _require_identity(
        profile,
        "generation4AbortVerifierSource",
        GENERATION4_ABORT_VERIFIER_PATH,
    )
    closure = _load_json(_repo_path(GENERATION4_CLOSURE_PATH))
    if closure.get("producer") != verifier:
        raise ValueError("Generation-4 closure producer differs from final freeze")


def _frozen_python(profile: Mapping[str, Any]) -> dict[str, Any]:
    identities = profile.get("finalFreezeIdentities")
    if not isinstance(identities, Mapping):
        raise ValueError("finalFreezeIdentities is malformed")
    runtime_record = identities.get("pythonRuntimeManifest")
    if not isinstance(runtime_record, Mapping) or set(runtime_record) != {
        "path",
        "bytes",
        "sha256",
    }:
        raise ValueError("Python runtime manifest identity is malformed")
    runtime_relative = runtime_record.get("path")
    if not isinstance(runtime_relative, str):
        raise ValueError("Python runtime manifest path is malformed")
    runtime_path = _repo_path(runtime_relative)
    if dict(runtime_record) != _identity(
        runtime_path, reported_path=runtime_relative
    ):
        raise ValueError("Python runtime manifest content changed")
    manifest = _load_json(runtime_path)
    runtime = manifest.get("runtime")
    python = runtime.get("python") if isinstance(runtime, Mapping) else None
    executable = python.get("executable") if isinstance(python, Mapping) else None
    if not isinstance(executable, Mapping) or set(executable) != {
        "path",
        "bytes",
        "sha256",
    }:
        raise ValueError("frozen Python executable identity is malformed")
    executable_path = Path(str(executable["path"])).expanduser().resolve()
    actual = _identity(executable_path, reported_path=str(executable["path"]))
    if dict(executable) != actual:
        raise ValueError("frozen Python executable content changed")
    return actual


def _verify_source_match_completion(profile: Mapping[str, Any]) -> None:
    """Run the frozen source wrapper's full no-resume seal verifier."""

    paths = {
        "sourceOpeningBuilderSource": SOURCE_WRAPPER_PATH,
        "sourceMatchCompletionSeal": SOURCE_MATCH_COMPLETION_SEAL_PATH,
        "sourceMatchConfig": (
            "build-msvc/data-generation/omega-decision-v2/source/source-match.json"
        ),
        "sourceMatchHarnessAssembly": (
            "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/"
            "OmegaMatch.dll"
        ),
        "rootSamplerAssembly": (
            "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/"
            "OmegaRootSampler.dll"
        ),
        "sourceRootPool": (
            "build-msvc/data-generation/omega-decision-v2/source/"
            "rules-only-pool.jsonl"
        ),
        "sourceRootPoolManifest": (
            "build-msvc/data-generation/omega-decision-v2/source/"
            "rules-only-pool.jsonl.manifest.json"
        ),
        "sourceRootPoolSeal": (
            "build-msvc/data-generation/omega-decision-v2/source/"
            "rules-only-pool.jsonl.complete.seal.json"
        ),
        "sourceOpeningSuite": (
            "build-msvc/data-generation/omega-decision-v2/source/openings.json"
        ),
        "sourceEvents": (
            "build-msvc/data-generation/omega-decision-v2/source/events.jsonl"
        ),
    }
    pins = {
        name: _require_identity(profile, name, relative)
        for name, relative in paths.items()
    }
    decision_chesslib = _require_identity(
        profile,
        "chessLibAssembly",
        "tools/omega_nnue/frozen_runtime/king-state-v5/decision-sampler/ChessLib.dll",
    )
    root_chesslib_path = _repo_path(
        "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/ChessLib.dll"
    )
    root_chesslib = _identity(root_chesslib_path)
    if (
        root_chesslib["bytes"] != decision_chesslib["bytes"]
        or root_chesslib["sha256"] != decision_chesslib["sha256"]
    ):
        raise ValueError("root-sampler ChessLib differs from frozen decision ChessLib")
    python = _frozen_python(profile)
    wrapper_path = _repo_path(SOURCE_WRAPPER_PATH)
    arguments = [
        "verify",
        "--config",
        str(_repo_path(paths["sourceMatchConfig"])),
        "--harness",
        str(_repo_path(paths["sourceMatchHarnessAssembly"])),
        "--root-sampler",
        str(_repo_path(paths["rootSamplerAssembly"])),
        "--root-sampler-chesslib",
        str(root_chesslib_path),
        "--pool",
        str(_repo_path(paths["sourceRootPool"])),
        "--pool-manifest",
        str(_repo_path(paths["sourceRootPoolManifest"])),
        "--pool-seal",
        str(_repo_path(paths["sourceRootPoolSeal"])),
        "--completion-seal",
        str(_repo_path(paths["sourceMatchCompletionSeal"])),
    ]
    bootstrap = (
        "import runpy,sys;"
        "tool=sys.argv.pop(1);"
        "tool_dir=sys.argv.pop(1);"
        "sys.path.insert(0,tool_dir);"
        "sys.argv[0]=tool;"
        "runpy.run_path(tool,run_name='__main__')"
    )
    with tempfile.TemporaryDirectory(prefix="omega-g5-source-freeze-verify-") as directory:
        command = [
            str(python["path"]),
            "-I",
            "-B",
            "-X",
            f"pycache_prefix={Path(directory) / 'isolated-python-cache'}",
            "-c",
            bootstrap,
            str(wrapper_path),
            str(wrapper_path.parent),
            *arguments,
        ]
        environment = dict(os.environ)
        for name in tuple(environment):
            if name.upper().startswith("PYTHON"):
                environment.pop(name, None)
        environment.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "PYTHONNOUSERSITE": "1",
            }
        )
        try:
            completed = subprocess.run(
                command,
                cwd=REPO,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=3600,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError("source-match completion verification exceeded one hour") from error
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError(
            "source-match completion verifier failed: "
            + (completed.stderr.strip() or completed.stdout.strip())[-4000:]
        )
    if "Verified complete Generation 5 source match:" not in completed.stdout:
        raise ValueError("source-match verifier omitted its completion attestation")
    for name, before in pins.items():
        if _identity(
            _repo_path(paths[name]), reported_path=paths[name]
        ) != before:
            raise ValueError(f"source-match {name} changed during verification")
    if _identity(root_chesslib_path) != root_chesslib:
        raise ValueError("root-sampler ChessLib changed during verification")


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_temporary(directory: Path, name: str, payload: bytes) -> Path:
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{name}.", suffix=".tmp", dir=directory
    )
    path = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def _publish_pair(
    profile_path: Path,
    profile_payload: bytes,
    seal_path: Path,
    seal_payload_builder: Any,
) -> None:
    profile_path = profile_path.resolve()
    seal_path = seal_path.resolve()
    if profile_path.exists() or seal_path.exists():
        raise FileExistsError("refusing to replace final preregistration or seal")
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    seal_path.parent.mkdir(parents=True, exist_ok=True)
    profile_temp = _write_temporary(
        profile_path.parent, profile_path.name, profile_payload
    )
    seal_temp: Path | None = None
    profile_published = False
    seal_published = False
    try:
        profile_identity = _identity(profile_temp, reported_path=str(profile_path))
        seal_payload = seal_payload_builder(profile_identity)
        seal_temp = _write_temporary(seal_path.parent, seal_path.name, seal_payload)
        if profile_path.exists() or seal_path.exists():
            raise FileExistsError("final freeze appeared during publication")
        os.link(profile_temp, profile_path)
        profile_published = True
        os.link(seal_temp, seal_path)
        seal_published = True
        profile_temp.unlink()
        seal_temp.unlink()
    except BaseException:
        # These paths were proven absent before this invocation; remove only
        # files that this invocation linked into place.
        if seal_published:
            try:
                seal_path.unlink()
            except OSError:
                pass
        if profile_published:
            try:
                profile_path.unlink()
            except OSError:
                pass
        for temporary in (profile_temp, seal_temp):
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
        raise


def _resolve_profile(
    template: Mapping[str, Any], catalog_manifests: Sequence[Path]
) -> dict[str, Any]:
    profile = deepcopy(dict(template))
    created = _utc_now()
    profile["kind"] = prereg.FROZEN_KIND
    profile["status"] = prereg.FROZEN_STATUS
    profile["createdUtc"] = created

    closure = dict(profile["generation4Closure"])
    closure_path = _repo_path(str(closure["path"]))
    profile["generation4Closure"] = _identity(
        closure_path, reported_path=str(closure["path"])
    )

    grid = dict(profile["activationControlDevelopmentGrid"])
    grid["status"] = "sealed-before-primary-training"
    grid_seal = dict(grid["seal"])
    grid_path = _repo_path(str(grid_seal["path"]))
    grid["seal"] = _identity(grid_path, reported_path=str(grid_seal["path"]))
    profile["activationControlDevelopmentGrid"] = grid

    identities = dict(profile["finalFreezeIdentities"])
    for name, record in list(identities.items()):
        if name in {"bindingRule", "forbiddenPositionCatalogManifests"}:
            continue
        if not isinstance(record, Mapping) or set(record) != {
            "path",
            "bytes",
            "sha256",
        }:
            raise ValueError(f"malformed identity template for {name}")
        relative = str(record["path"])
        identities[name] = _identity(
            _repo_path(relative), reported_path=relative
        )
    if not catalog_manifests:
        raise ValueError("at least one forbidden-position catalog manifest is required")
    catalog_identities = []
    seen: set[str] = set()
    for catalog in catalog_manifests:
        catalog = catalog.expanduser().resolve()
        try:
            relative = catalog.relative_to(REPO.resolve()).as_posix()
        except ValueError as error:
            raise ValueError("forbidden catalog manifest must be inside the repo") from error
        if relative in seen:
            raise ValueError("duplicate forbidden catalog manifest")
        seen.add(relative)
        catalog_identities.append(_identity(catalog, reported_path=relative))
    identities["forbiddenPositionCatalogManifests"] = catalog_identities
    profile["finalFreezeIdentities"] = identities
    _validate_source_data_contract(profile)
    _validate_generation4_binding(profile)
    return profile


def _teacher_artifacts_absent(root: Path = REPO) -> None:
    present = [relative for relative in TARGET_ARTIFACTS if (root / relative).exists()]
    present.extend(
        path.relative_to(root).as_posix()
        for pattern in TARGET_GLOBS
        for path in root.glob(pattern)
    )
    present = sorted(set(present))
    if present:
        raise ValueError(
            "Generation 5 teacher/training artifacts already exist before final freeze: "
            + ", ".join(present)
        )


def _expected_seal(
    profile: Mapping[str, Any],
    profile_identity: Mapping[str, Any],
    *,
    created_utc: str,
) -> dict[str, Any]:
    final_identities = profile["finalFreezeIdentities"]
    validator = _identity(Path(prereg.__file__).resolve())
    return {
        "schemaVersion": 1,
        "kind": KIND,
        "profileId": prereg.PROFILE_ID,
        "status": STATUS,
        "createdUtc": created_utc,
        "preregistration": dict(profile_identity),
        "validator": validator,
        "finalFreezeIdentities": final_identities,
        "finalFreezeIdentitiesSha256": _canonical_digest(final_identities),
        "declaration": {
            "teacherSearchesPresentAtFreeze": False,
            "generation5TeacherTargetsDecoded": 0,
            "generation5ValidationTargetsDecoded": 0,
            "generation5HeldOutTargetsDecoded": 0,
        },
        "finalStageSeal": True,
    }


def _create(args: argparse.Namespace) -> None:
    template_path = args.template.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    seal_path = args.seal.expanduser().resolve()
    if output_path != OUTPUT.resolve() or seal_path != SEAL.resolve():
        raise ValueError("final profile and seal must use preregistered paths")
    _teacher_artifacts_absent()
    template = _load_json(template_path)
    prereg.validate_profile(template, mode="template", verify_external=False)
    profile = _resolve_profile(template, args.forbidden_catalog_manifest)
    prereg.validate_profile(profile, mode="frozen", verify_external=True)
    _verify_source_match_completion(profile)
    created = str(profile["createdUtc"])
    payload = _canonical_json(profile)

    def seal_payload(profile_identity: Mapping[str, Any]) -> bytes:
        return _canonical_json(
            _expected_seal(profile, profile_identity, created_utc=created)
        )

    _publish_pair(output_path, payload, seal_path, seal_payload)
    _verify_paths(output_path, seal_path, verify_source=False)
    print(
        f"Published Generation 5 final preregistration: {output_path} "
        f"({_sha256(output_path)})"
    )
    print(f"Published Generation 5 final-freeze seal: {seal_path}")


def _verify_paths(
    profile_path: Path, seal_path: Path, *, verify_source: bool = True
) -> None:
    profile_path = profile_path.expanduser().resolve()
    seal_path = seal_path.expanduser().resolve()
    profile = _load_json(profile_path)
    prereg.validate_profile(profile, mode="frozen", verify_external=True)
    _validate_source_data_contract(profile)
    _validate_generation4_binding(profile)
    if verify_source:
        _verify_source_match_completion(profile)
    seal = _load_json(seal_path)
    expected_keys = {
        "schemaVersion",
        "kind",
        "profileId",
        "status",
        "createdUtc",
        "preregistration",
        "validator",
        "finalFreezeIdentities",
        "finalFreezeIdentitiesSha256",
        "declaration",
        "finalStageSeal",
    }
    if set(seal) != expected_keys:
        raise ValueError("final-freeze seal field inventory changed")
    profile_identity = _identity(profile_path)
    expected = _expected_seal(
        profile, profile_identity, created_utc=str(seal.get("createdUtc"))
    )
    if seal != expected:
        raise ValueError("final-freeze seal does not exactly bind the profile")
    if seal["createdUtc"] != profile["createdUtc"]:
        raise ValueError("profile and final-freeze timestamps differ")


def _verify(args: argparse.Namespace) -> None:
    _verify_paths(args.output, args.seal)
    print(f"Verified Generation 5 final freeze: {args.seal.resolve()}")


def _self_test() -> None:
    if (
        prereg.PROFILE_ID != "king-state-v5-omega-decision-v2"
        or KIND != "omega-nnue-king-state-v5-final-freeze-seal"
        or STATUS
        != "target-blind-generation-5-design-frozen-before-teacher-labels"
    ):
        raise AssertionError("Generation 5 final-freeze identity changed")
    policy_fixture = {
        "freshDecisionCorpus": {
            "source": {
                "sourceDataDisjointness": deepcopy(
                    EXPECTED_SOURCE_DATA_DISJOINTNESS
                )
            }
        }
    }
    _validate_source_data_contract(policy_fixture)
    changed_policy = deepcopy(policy_fixture)
    changed_policy["freshDecisionCorpus"]["source"]["sourceDataDisjointness"][
        "dataInputIdentityFields"
    ][0] = "sourceMatchHarnessAssembly"
    try:
        _validate_source_data_contract(changed_policy)
    except ValueError:
        pass
    else:
        raise AssertionError("source-data identity-domain substitution was accepted")
    closure_path = _repo_path(GENERATION4_CLOSURE_PATH)
    verifier_path = _repo_path(GENERATION4_ABORT_VERIFIER_PATH)
    generation4_fixture = {
        "generation4Closure": _identity(
            closure_path, reported_path=GENERATION4_CLOSURE_PATH
        ),
        "finalFreezeIdentities": {
            "generation4AbortVerifierSource": _identity(
                verifier_path, reported_path=GENERATION4_ABORT_VERIFIER_PATH
            )
        },
    }
    _validate_generation4_binding(generation4_fixture)
    changed_generation4 = deepcopy(generation4_fixture)
    changed_generation4["finalFreezeIdentities"][
        "generation4AbortVerifierSource"
    ]["sha256"] = "0" * 64
    try:
        _validate_generation4_binding(changed_generation4)
    except ValueError:
        pass
    else:
        raise AssertionError("Generation-4 verifier substitution was accepted")
    seal_fixture = _expected_seal(
        {"finalFreezeIdentities": {}},
        {"path": "synthetic-profile.json", "bytes": 1, "sha256": "0" * 64},
        created_utc="2026-07-23T00:00:00Z",
    )
    if seal_fixture["declaration"] != {
        "teacherSearchesPresentAtFreeze": False,
        "generation5TeacherTargetsDecoded": 0,
        "generation5ValidationTargetsDecoded": 0,
        "generation5HeldOutTargetsDecoded": 0,
    }:
        raise AssertionError("Generation 5 final-freeze declaration changed")
    value = {"z": [3, 2, 1], "a": {"b": False}}
    if _canonical_digest(value) != _canonical_digest(deepcopy(value)):
        raise AssertionError("canonical digest is unstable")
    try:
        json.loads('{"a":1,"a":2}', object_pairs_hook=_reject_duplicates)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate JSON keys were accepted")
    if _repo_path("tools/omega_nnue/king_state_generation5_freeze.py") != (
        Path(__file__).resolve()
    ):
        raise AssertionError("repo-relative path resolution changed")
    try:
        _repo_path("../escape")
    except ValueError:
        pass
    else:
        raise AssertionError("repo path escape was accepted")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        profile = root / "profile.json"
        seal = root / "seal.json"
        _publish_pair(
            profile,
            b'{"profile":"g5"}\n',
            seal,
            lambda identity: _canonical_json({"preregistration": identity}),
        )
        before = (_identity(profile), _identity(seal))
        try:
            _publish_pair(
                profile,
                b'{"profile":"replacement"}\n',
                seal,
                lambda identity: _canonical_json({"preregistration": identity}),
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("no-clobber final-freeze publication succeeded")
        if (_identity(profile), _identity(seal)) != before:
            raise AssertionError("no-clobber final-freeze publication changed bytes")
        companion = (
            root
            / "build-msvc/data-generation/omega-decision-v2/"
            "shallow-results.jsonl.lock.json"
        )
        companion.parent.mkdir(parents=True)
        companion.write_text("{}\n", encoding="utf-8")
        try:
            _teacher_artifacts_absent(root)
        except ValueError as error:
            if "shallow-results.jsonl.lock.json" not in str(error):
                raise AssertionError("companion-only abort hid its artifact") from error
        else:
            raise AssertionError("companion-only teacher artifact survived freeze guard")
    print("Generation 5 final-freeze builder self-tests passed.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--template", type=Path, default=TEMPLATE)
    create.add_argument("--output", type=Path, default=OUTPUT)
    create.add_argument("--seal", type=Path, default=SEAL)
    create.add_argument(
        "--forbidden-catalog-manifest",
        type=Path,
        action="append",
        required=True,
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--output", type=Path, default=OUTPUT)
    verify.add_argument("--seal", type=Path, default=SEAL)
    subparsers.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        _create(args)
    elif args.command == "verify":
        _verify(args)
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
