#!/usr/bin/env python3
"""Create or verify the target-blind Generation 4 final freeze.

Creation resolves every preregistration placeholder from bytes already on
disk, validates the completed profile (including every external identity),
and publishes the profile plus its adjacent seal without replacing either.
It refuses to run after any Generation 4 teacher-search artifact exists.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Mapping, Sequence

import validate_king_state_v4_preregistration as prereg


REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "validation/omega-nnue-king-state-v4-preregistration.template.json"
OUTPUT = REPO / "validation/omega-nnue-king-state-v4-preregistration.json"
SEAL = REPO / "validation/omega-nnue-king-state-v4-freeze.seal.json"
KIND = "omega-nnue-king-state-v4-final-freeze-seal"
STATUS = "target-blind-generation-4-design-frozen-before-teacher-labels"
TARGET_ARTIFACTS = (
    "build-msvc/data-generation/omega-decision-v1/shallow-results.jsonl",
    "build-msvc/data-generation/omega-decision-v1/selected-children.jsonl",
    "build-msvc/data-generation/omega-decision-v1/deep-results.jsonl",
    "build-msvc/data-generation/omega-decision-v1/decision-labels.jsonl",
    "build-msvc/king-state-v4/G4A.bundle/G4A.nnue",
    "build-msvc/king-state-v4/G4B.bundle/G4B.nnue",
    "build-msvc/king-state-v4/G4C.bundle/G4C.nnue",
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

    closure = dict(profile["generation3Closure"])
    closure_path = _repo_path(str(closure["path"]))
    profile["generation3Closure"] = _identity(
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
    return profile


def _teacher_artifacts_absent() -> None:
    present = [relative for relative in TARGET_ARTIFACTS if (REPO / relative).exists()]
    if present:
        raise ValueError(
            "Generation 4 teacher/training artifacts already exist before final freeze: "
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
            "generation4TeacherTargetsDecoded": 0,
            "generation4ValidationTargetsDecoded": 0,
            "generation4HeldOutTargetsDecoded": 0,
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
    created = str(profile["createdUtc"])
    payload = _canonical_json(profile)

    def seal_payload(profile_identity: Mapping[str, Any]) -> bytes:
        return _canonical_json(
            _expected_seal(profile, profile_identity, created_utc=created)
        )

    _publish_pair(output_path, payload, seal_path, seal_payload)
    _verify_paths(output_path, seal_path)
    print(
        f"Published Generation 4 final preregistration: {output_path} "
        f"({_sha256(output_path)})"
    )
    print(f"Published Generation 4 final-freeze seal: {seal_path}")


def _verify_paths(profile_path: Path, seal_path: Path) -> None:
    profile_path = profile_path.expanduser().resolve()
    seal_path = seal_path.expanduser().resolve()
    profile = _load_json(profile_path)
    prereg.validate_profile(profile, mode="frozen", verify_external=True)
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
    print(f"Verified Generation 4 final freeze: {args.seal.resolve()}")


def _self_test() -> None:
    value = {"z": [3, 2, 1], "a": {"b": False}}
    if _canonical_digest(value) != _canonical_digest(deepcopy(value)):
        raise AssertionError("canonical digest is unstable")
    try:
        json.loads('{"a":1,"a":2}', object_pairs_hook=_reject_duplicates)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate JSON keys were accepted")
    if _repo_path("tools/omega_nnue/king_state_generation4_freeze.py") != (
        Path(__file__).resolve()
    ):
        raise AssertionError("repo-relative path resolution changed")
    try:
        _repo_path("../escape")
    except ValueError:
        pass
    else:
        raise AssertionError("repo path escape was accepted")
    print("Generation 4 final-freeze builder self-tests passed.")


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
