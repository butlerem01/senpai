#!/usr/bin/env python3
"""Freeze and verify the Python/NumPy runtime used by Generation 5.

The runtime seal is created before any Generation-5 teacher label.  Rules-only
source generation may already be running or published, but this command never
opens those positions.  It pins the Python executable, NumPy's public module
and compiled core, the NumPy build configuration, and the single-thread worker
environment.  All Python stages reverify this seal instead of merely recording
whichever runtime happens to execute them later.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
from typing import Any, Mapping, Sequence


DETERMINISTIC_WORKER_ENVIRONMENT = {
    "BLIS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "PYTHONHASHSEED": "0",
}
NUMPY_WAS_PRELOADED = "numpy" in sys.modules
for _name, _value in DETERMINISTIC_WORKER_ENVIRONMENT.items():
    os.environ[_name] = _value

import numpy as np
import numpy._core._multiarray_umath as numpy_core


REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    REPO / "validation/omega-nnue-king-state-v5-python-runtime.json"
)
SCHEMA_VERSION = 1
KIND = "omega-nnue-king-state-v5-python-runtime"
PROFILE_ID = "king-state-v5-omega-decision-v2"
STATUS = "frozen-before-generation-5-teacher-labels"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_ARTIFACTS = (
    "build-msvc/data-generation/omega-decision-v2/shallow-results.jsonl",
    "build-msvc/data-generation/omega-decision-v2/selected-children.jsonl",
    "build-msvc/data-generation/omega-decision-v2/deep-results.jsonl",
    "build-msvc/data-generation/omega-decision-v2/decision-labels.jsonl",
    "build-msvc/king-state-v5/G5A.bundle/G5A.nnue",
    "build-msvc/king-state-v5/G5B.bundle/G5B.nnue",
    "build-msvc/king-state-v5/G5C.bundle/G5C.nnue",
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


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"runtime dependency is not a file: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _verify_identity(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} is not a strict file identity")
    path_text = value.get("path")
    size = value.get("bytes")
    sha = value.get("sha256")
    if not isinstance(path_text, str) or not path_text:
        raise ValueError(f"{label}.path is invalid")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"{label}.bytes is invalid")
    if not isinstance(sha, str) or HEX64.fullmatch(sha) is None:
        raise ValueError(f"{label}.sha256 is invalid")
    actual = _identity(Path(path_text))
    expected = {"path": str(Path(path_text).expanduser().resolve()), "bytes": size,
                "sha256": sha}
    if actual != expected:
        raise ValueError(f"{label} changed: expected {expected}, found {actual}")
    return actual


def _require_environment() -> None:
    if NUMPY_WAS_PRELOADED:
        raise ValueError("NumPy was imported before the Generation-5 runtime contract")
    for name, expected in DETERMINISTIC_WORKER_ENVIRONMENT.items():
        if os.environ.get(name) != expected:
            raise ValueError(f"deterministic runtime requires {name}={expected}")


def current_runtime_record() -> dict[str, Any]:
    """Return the exact runtime record used for creation and verification."""

    _require_environment()
    executable = Path(sys.executable).resolve(strict=True)
    numpy_module = Path(str(np.__file__)).resolve(strict=True)
    numpy_core_module = Path(str(numpy_core.__file__)).resolve(strict=True)
    return {
        "runtimeTool": _identity(Path(__file__)),
        "python": {
            "executable": _identity(executable),
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "versionDetail": sys.version,
            "compiler": platform.python_compiler(),
            "byteOrder": sys.byteorder,
        },
        "numpy": {
            "version": np.__version__,
            "module": _identity(numpy_module),
            "compiledCore": _identity(numpy_core_module),
            "buildConfiguration": np.__config__.CONFIG,
        },
        "platform": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "numpyPreloadedBeforeRuntimeContract": NUMPY_WAS_PRELOADED,
        "deterministicWorkerEnvironment": dict(DETERMINISTIC_WORKER_ENVIRONMENT),
    }


def _payload(*, created_utc: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "profileId": PROFILE_ID,
        "status": STATUS,
        "createdUtc": created_utc or _utc_now(),
        "runtime": current_runtime_record(),
        "informationBoundary": {
            "generation5SourcePositionFieldsDecoded": 0,
            "generation5TeacherTargetsDecoded": 0,
            "generation5ValidationTargetsDecoded": 0,
            "generation5HeldOutTargetsDecoded": 0,
        },
        "finalStageSeal": True,
    }


def _verify_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schemaVersion", "kind", "profileId", "status", "createdUtc",
        "runtime", "informationBoundary", "finalStageSeal",
    }:
        raise ValueError("runtime manifest field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != KIND
        or value.get("profileId") != PROFILE_ID
        or value.get("status") != STATUS
        or value.get("finalStageSeal") is not True
    ):
        raise ValueError("runtime manifest identity changed")
    created = value.get("createdUtc")
    if not isinstance(created, str):
        raise ValueError("runtime manifest createdUtc is invalid")
    try:
        datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("runtime manifest createdUtc is invalid") from error
    if value.get("informationBoundary") != {
        "generation5SourcePositionFieldsDecoded": 0,
        "generation5TeacherTargetsDecoded": 0,
        "generation5ValidationTargetsDecoded": 0,
        "generation5HeldOutTargetsDecoded": 0,
    }:
        raise ValueError("runtime manifest information boundary changed")
    runtime = value.get("runtime")
    if not isinstance(runtime, Mapping) or set(runtime) != {
        "runtimeTool", "python", "numpy", "platform",
        "numpyPreloadedBeforeRuntimeContract", "deterministicWorkerEnvironment",
    }:
        raise ValueError("runtime record field inventory changed")
    python = runtime.get("python")
    if not isinstance(python, Mapping) or set(python) != {
        "executable", "implementation", "version", "versionDetail", "compiler",
        "byteOrder",
    }:
        raise ValueError("Python runtime field inventory changed")
    numpy = runtime.get("numpy")
    if not isinstance(numpy, Mapping) or set(numpy) != {
        "version", "module", "compiledCore", "buildConfiguration",
    }:
        raise ValueError("NumPy runtime field inventory changed")
    platform_record = runtime.get("platform")
    if not isinstance(platform_record, Mapping) or set(platform_record) != {
        "platform", "system", "release", "version", "machine", "processor",
    }:
        raise ValueError("platform runtime field inventory changed")
    _verify_identity(runtime["runtimeTool"], "runtime tool")
    _verify_identity(python["executable"], "Python executable")
    _verify_identity(numpy["module"], "NumPy module")
    _verify_identity(numpy["compiledCore"], "NumPy compiled core")
    if runtime.get("numpyPreloadedBeforeRuntimeContract") is not False:
        raise ValueError("runtime manifest permits NumPy preloading")
    if runtime.get("deterministicWorkerEnvironment") != DETERMINISTIC_WORKER_ENVIRONMENT:
        raise ValueError("runtime deterministic worker environment changed")
    current = current_runtime_record()
    if dict(runtime) != current:
        raise ValueError("executing Python/NumPy runtime differs from the frozen manifest")
    return dict(value)


def verify_manifest(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved != DEFAULT_OUTPUT.resolve():
        raise ValueError("runtime manifest is outside its canonical path")
    return _verify_payload(_load_json(resolved))


def _atomic_create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except BaseException:
        raise
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _create(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if resolved != DEFAULT_OUTPUT.resolve():
        raise ValueError("runtime manifest must use its preregistered path")
    present = [relative for relative in FORBIDDEN_ARTIFACTS if (REPO / relative).exists()]
    if present:
        raise ValueError(
            "Generation-5 teacher/training artifacts predate the runtime freeze: "
            + ", ".join(present)
        )
    _atomic_create(resolved, _canonical_json(_payload()))
    verify_manifest(resolved)
    print(
        f"Published Generation-5 Python/NumPy runtime manifest: {resolved} "
        f"({resolved.stat().st_size} bytes, {_sha256(resolved)})"
    )


def _self_test() -> None:
    value = _payload(created_utc="2026-07-21T00:00:00Z")
    _verify_payload(value)
    changed = deepcopy(value)
    changed["runtime"]["numpy"]["compiledCore"]["sha256"] = "0" * 64
    try:
        _verify_payload(changed)
    except ValueError:
        pass
    else:
        raise AssertionError("runtime mutation was accepted")
    try:
        json.loads('{"a":1,"a":2}', object_pairs_hook=_reject_duplicates)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate runtime JSON key was accepted")
    with tempfile.TemporaryDirectory(prefix="omega-g4-runtime-self-test-") as directory:
        target = Path(directory) / "runtime.json"
        payload = _canonical_json(value)
        _atomic_create(target, payload)
        if target.read_bytes() != payload:
            raise AssertionError("atomic runtime publication changed bytes")
        try:
            _atomic_create(target, b"replacement")
        except FileExistsError:
            pass
        else:
            raise AssertionError("runtime publication overwrote an existing seal")
    print("Generation-5 Python/NumPy runtime self-tests passed.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    subparsers.add_parser("self-test")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        _create(args.output)
    elif args.command == "verify":
        verify_manifest(args.output)
        print(f"Verified Generation-5 Python/NumPy runtime: {args.output.resolve()}")
    elif args.command == "self-test":
        _self_test()
    else:
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
