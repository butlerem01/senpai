#!/usr/bin/env python3
"""Build and verify the fixed Generation-5 prior-position catalog for G6.

The G5 decision teacher contains the reviewed lexical JSON scanner used for
prior-position catalogs, but its public builder intentionally rejects every G5
path: it was designed to construct G5's *input* catalog from G3 and G4.  This
module is the narrow generation-boundary adapter.  It authenticates the frozen
G5 pre-label closure, reuses only the reviewed lexical scanner and row
normalizer, and publishes the exact catalog schema consumed by G6 routing.

No caller chooses source files.  The seven position-bearing files are fixed by
content identity below.  Values under target/score-like keys are skipped as raw
JSON lexemes by the imported G5 scanner and are never decoded here.
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Authority modules must be authenticated before any of their code executes.
# In particular, adding this directory to sys.path and using an ordinary import
# would still trust an attacker-controlled public sys.modules entry.  Each
# module below is therefore compiled from one descriptor-stable, exactly pinned
# snapshot under a private name, with every local import it may use injected.
TOOL_DIRECTORY = Path(__file__).resolve().parent
_EXECUTION_REPARSE_POINT = 0x0400
_EXECUTION_SHA = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_EXECUTION_PREFIX = "_omega_decision_v3_prior_catalog_pinned_"
_EXECUTION_PINS: Mapping[str, tuple[str, int, str]] = {
    "routing": (
        "omega_decision_v3_routing.py",
        133_260,
        "ed327826154256137694b955aaf23ede07c91374ea665be48df55360f858e25c",
    ),
    "generation5Runtime": (
        "king_state_generation5_runtime.py",
        13_853,
        "8f183378f3c461fb2699774239c90b424b19c0b63d848a8e60a521e154163ed0",
    ),
    "deepHceV2": (
        "deep_hce_v2.py",
        148_725,
        "9e9911b0a56191e5209d6276c004cf36a94fe1530eb915baf1ad5a72d32f7702",
    ),
    "generation5PreregistrationValidator": (
        "validate_king_state_v5_preregistration.py",
        82_821,
        "36c746b9af379517ac68b1c0502e0f8444291f3a4d87a06f1cef3fe7c6016bdc",
    ),
    "generation4AbortVerifier": (
        "king_state_generation4_abort.py",
        42_170,
        "4994ef1f7586655082f02e4a0c9e98b2f5cfa834bf32502b282a0c7220f1f65a",
    ),
    "priorProjection": (
        "king_state_generation4_prior_projection.py",
        72_396,
        "59196d784defe54d3ae031d3d7f637d96adc8bf9f048038f340acbc0b851e24d",
    ),
    "generation5Teacher": (
        "omega_decision_teacher_generation5.py",
        285_726,
        "97ffde4759c3a7d037813c1225c4abf1c4c8b9380f6beeb9de6b84c6488bc414",
    ),
}


def _execution_path(filename: str) -> Path:
    if type(filename) is not str or Path(filename).name != filename:
        raise RuntimeError("authority module filename must be one fixed basename")
    path = Path(os.path.abspath(os.path.normpath(os.fspath(TOOL_DIRECTORY / filename))))
    expected_parent = Path(
        os.path.abspath(os.path.normpath(os.fspath(TOOL_DIRECTORY)))
    )
    if path.parent != expected_parent:
        raise RuntimeError("authority module escaped the fixed tool directory")
    current = path.parent
    while True:
        metadata = os.lstat(current)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or attributes & _EXECUTION_REPARSE_POINT:
            raise RuntimeError(f"authority module has a linked/reparse parent: {current}")
        if current == current.parent:
            break
        current = current.parent
    return path


def _execution_snapshot(
    filename: str, expected_bytes: int, expected_sha256: str, label: str
) -> tuple[dict[str, Any], bytes]:
    """Return exact bytes from one stable descriptor, before execution."""

    if (
        type(expected_bytes) is not int
        or expected_bytes < 1
        or type(expected_sha256) is not str
        or _EXECUTION_SHA.fullmatch(expected_sha256) is None
    ):
        raise RuntimeError(f"{label} has no finalized execution pin")
    path = _execution_path(filename)
    before = os.lstat(path)
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or attributes & _EXECUTION_REPARSE_POINT
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise RuntimeError(f"{label} must be one non-linked regular file: {path}")
    if before.st_size != expected_bytes:
        raise RuntimeError(
            f"{label} differs from its execution pin before open: expected "
            f"{expected_bytes} bytes, found {before.st_size}"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError(f"{label} changed before descriptor open")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        after_descriptor = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = os.lstat(path)
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or (
            after_descriptor.st_dev,
            after_descriptor.st_ino,
            after_descriptor.st_size,
        )
        != (before.st_dev, before.st_ino, before.st_size)
        or len(payload) != expected_bytes
    ):
        raise RuntimeError(f"{label} changed while its execution bytes were read")
    identity = {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if identity["sha256"] != expected_sha256:
        raise RuntimeError(
            f"{label} differs from its execution pin: expected {expected_sha256}, "
            f"found {identity['sha256']}"
        )
    return identity, payload


def _execute_exact_module(
    logical_name: str,
    *,
    pinned_imports: Mapping[str, types.ModuleType] | None = None,
) -> tuple[types.ModuleType, dict[str, Any]]:
    """Compile one exact snapshot under an unforgeable-by-public-import name."""

    try:
        filename, expected_bytes, expected_sha256 = _EXECUTION_PINS[logical_name]
    except KeyError as error:
        raise RuntimeError(f"unknown authority module {logical_name!r}") from error
    private_name = _PRIVATE_EXECUTION_PREFIX + logical_name
    if private_name in sys.modules:
        raise RuntimeError(f"private authority import slot was preloaded: {private_name}")
    identity, payload = _execution_snapshot(
        filename, expected_bytes, expected_sha256, f"{logical_name} authority"
    )
    module = types.ModuleType(private_name)
    module.__file__ = identity["path"]
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    original_import = builtins.__import__
    exact_imports = dict(pinned_imports or {})

    def exact_import(
        name: str,
        globals: Mapping[str, Any] | None = None,
        locals: Mapping[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if level == 0 and name in exact_imports:
            return exact_imports[name]
        return original_import(name, globals, locals, fromlist, level)

    execution_builtins = dict(vars(builtins))
    execution_builtins["__import__"] = exact_import
    module.__dict__["__builtins__"] = execution_builtins
    sys.modules[private_name] = module
    try:
        code = compile(payload, identity["path"], "exec", dont_inherit=True)
        exec(code, module.__dict__)
        if sys.modules.get(private_name) is not module:
            raise RuntimeError(f"{logical_name} replaced its private import slot")
        post_identity, _post_payload = _execution_snapshot(
            filename, expected_bytes, expected_sha256, f"{logical_name} authority"
        )
        if post_identity != identity:
            raise RuntimeError(f"{logical_name} authority changed during execution")
    except BaseException:
        if sys.modules.get(private_name) is module:
            del sys.modules[private_name]
        raise
    return module, identity


def _unavailable_transitive_module(public_name: str) -> types.ModuleType:
    """Make an irrelevant local dependency abort if a future code path touches it."""

    module = types.ModuleType(
        _PRIVATE_EXECUTION_PREFIX + "unavailable_" + public_name
    )

    def unavailable(attribute: str) -> Any:
        raise RuntimeError(
            f"unpinned transitive module {public_name!r} is unavailable in the "
            f"G5 prior-catalog authority (requested {attribute!r})"
        )

    module.__getattr__ = unavailable  # type: ignore[attr-defined]
    return module


# The runtime contract must execute before omega_nnue imports NumPy; its frozen
# verifier deliberately rejects a process in which NumPy was already loaded.
if any(name == "numpy" or name.startswith("numpy.") for name in sys.modules):
    raise RuntimeError(
        "G5 prior-catalog authority requires a fresh process before NumPy import"
    )
_runtime_contract, GENERATION5_RUNTIME_EXECUTION_IDENTITY = _execute_exact_module(
    "generation5Runtime"
)
routing, ROUTING_EXECUTION_IDENTITY = _execute_exact_module("routing")
_omega_dependency = routing._pinned_dependency("omegaNnue")
_screen_dependency = routing._pinned_dependency("selectScreen")
_omega_nnue = _omega_dependency.module
_select_screen = _screen_dependency.module
_deep_hce, DEEP_HCE_EXECUTION_IDENTITY = _execute_exact_module(
    "deepHceV2",
    pinned_imports={"omega_nnue": _omega_nnue, "select_screen": _select_screen},
)
_v5_prereg, V5_PREREG_EXECUTION_IDENTITY = _execute_exact_module(
    "generation5PreregistrationValidator",
    pinned_imports={"king_state_generation5_runtime": _runtime_contract},
)
_generation4_abort, G4_ABORT_EXECUTION_IDENTITY = _execute_exact_module(
    "generation4AbortVerifier",
    pinned_imports={
        "king_state_generation4_freeze": _unavailable_transitive_module(
            "king_state_generation4_freeze"
        ),
        "omega_nnue": _omega_nnue,
        "select_screen": _select_screen,
    },
)
_prior_projection, PRIOR_PROJECTION_EXECUTION_IDENTITY = _execute_exact_module(
    "priorProjection",
    pinned_imports={
        "deep_hce_v2": _deep_hce,
        "king_state_v3": _unavailable_transitive_module("king_state_v3"),
        "omega_nnue": _omega_nnue,
        "select_screen": _select_screen,
    },
)
g5, G5_EXECUTION_IDENTITY = _execute_exact_module(
    "generation5Teacher",
    pinned_imports={
        "king_state_generation5_runtime": _runtime_contract,
        "king_state_generation4_abort": _generation4_abort,
        "king_state_generation4_prior_projection": _prior_projection,
        "deep_hce_v2": _deep_hce,
        "omega_nnue": _omega_nnue,
        "select_screen": _select_screen,
        "validate_king_state_v5_preregistration": _v5_prereg,
    },
)


SCHEMA_VERSION = 1
MANIFEST_KIND = "omega-target-opaque-forbidden-position-catalog-manifest"
ROW_KIND = "omega-target-opaque-forbidden-position"
REPO = Path(__file__).resolve().parents[2]

G34_MANIFEST_RELATIVE = (
    "build-msvc/data-generation/omega-decision-v2/"
    "forbidden-positions.jsonl.manifest.json"
)
G34_CATALOG_RELATIVE = (
    "build-msvc/data-generation/omega-decision-v2/forbidden-positions.jsonl"
)
G5_OUTPUT_DIRECTORY_RELATIVE = "build-msvc/data-generation/g5-prior-forbidden-v1"
G5_CATALOG_RELATIVE = G5_OUTPUT_DIRECTORY_RELATIVE + "/positions.jsonl"
G5_MANIFEST_RELATIVE = G5_OUTPUT_DIRECTORY_RELATIVE + "/positions.manifest.json"

G5_PREREGISTRATION_RELATIVE = (
    "validation/omega-nnue-king-state-v5-preregistration.json"
)
G5_FINAL_FREEZE_RELATIVE = (
    "validation/omega-nnue-king-state-v5-freeze.seal.json"
)
G5_CLOSURE_RELATIVE = (
    "build-msvc/data-generation/omega-decision-v2/prelabel-freeze.seal.json"
)

# These are the already published G3+G4 authority files.  The 12,170-byte
# manifest is itself the closed semantic pin; its catalog identity is repeated
# so lineage-only verification can reject path substitution without reading
# 559 MB of rows.
G34_MANIFEST_SPEC = (
    G34_MANIFEST_RELATIVE,
    12170,
    "8795d5b4a1476d0a0c39c7aadefa9609338ffff9cacf23c7e136d10e654a110e",
)
G34_CATALOG_SPEC = (
    G34_CATALOG_RELATIVE,
    559160534,
    "80e63d110b4d745fbd0e77e72f2b799288d1fd3fc5051069746b387a39be17f0",
)
G34_POSITION_COUNT = 833321

# Exact position-source inventory in the existing G3+G4 manifest.  Its G3
# projection plus the five G4 position sources is why this manifest covers
# [G3,G4], never [G4,G5].
G34_SOURCE_SPECS: tuple[tuple[str, int, str], ...] = (
    (
        "build-msvc/data-generation/g3-prior-projection-v1/positions.jsonl",
        85617848,
        "b8fb6b30e06d7bab11d22b8ddf8251d7b6dcfebf32043a08db5c4567cbccd040",
    ),
    (
        "build-msvc/data-generation/omega-decision-v1/children.jsonl",
        149850504,
        "ff30ab9d9cc1a435f4a01f860d9cca39c73c80d65bd805f3c7a6fe036d95db2c",
    ),
    (
        "build-msvc/data-generation/omega-decision-v1/roots.jsonl",
        4814846,
        "3c7f2dfd453d0f0e9fbadb9deda0a916fd415150796866c2aec82835ea74c240",
    ),
    (
        "build-msvc/data-generation/omega-decision-v1/source/events.jsonl",
        92477081,
        "d13b23a3376085f8ffb29015d0ae14082205cf057cc905dd33b550a3ffcf50e4",
    ),
    (
        "build-msvc/data-generation/omega-decision-v1/source/openings.json",
        1477311,
        "422d1e7f77ebb45be550bb59a08ce35db50430a5fcd26182a507bf5ae4e8216f",
    ),
    (
        "build-msvc/data-generation/omega-decision-v1/source/rules-only-pool.jsonl",
        135436321,
        "5ecd1b3fb9269ff2c857da970ea9e99b19668ee602f12016c052a2f950d3fde9",
    ),
)
G34_PROJECTION_SPEC = (
    "build-msvc/data-generation/g3-prior-projection-v1/positions.manifest.json",
    674128,
    "9e73e296f27ac529086ed0b8945f71df50f048b5f8f414fb3eededf6399acc95",
)
G34_CLOSURE_SPEC = (
    "validation/omega-nnue-king-state-v4-structural-abort.seal.json",
    11315,
    "ed4421290ef52463fdd6b883ecb0972d7a99b7d9a0700c3366981e839eb928d7",
)

# role, repo-relative path, bytes, sha256.  The raw graph is the leakage
# boundary; retained roots/children are also included so artifact-level reuse is
# closed and the next generation does not have to infer the subset relation.
G5_SOURCE_SPECS: tuple[tuple[str, str, int, str], ...] = (
    (
        "sourceEvents",
        "build-msvc/data-generation/omega-decision-v2/source/events.jsonl",
        103891588,
        "9c2f5f85b8e87a0e5a48a2e47ef4d3a553a39a93cdefab60b7d72c27422d6d1a",
    ),
    (
        "sourceOpeningSuite",
        "build-msvc/data-generation/omega-decision-v2/source/openings.json",
        1654995,
        "349560a5736f729cc4c4bb653209dac9b75e93a4148e60af50741b25af59395f",
    ),
    (
        "sourceRootPool",
        "build-msvc/data-generation/omega-decision-v2/source/rules-only-pool.jsonl",
        169215179,
        "b322cad73ebfff2f904edac4319ad1ae42f5d526fbce45f884d8ef0dd6b00be7",
    ),
    (
        "rawRoots",
        "build-msvc/data-generation/omega-decision-v2/raw-roots.jsonl",
        6740868,
        "6b1f7fe5e489a6cefefcf79c0792541bf5a0db822bf4d56816e009b7bc2d0471",
    ),
    (
        "rawChildren",
        "build-msvc/data-generation/omega-decision-v2/raw-children.jsonl",
        210975453,
        "27bf427519cc18c76fe119371b62ae14f168405e3bc2ff21d9f2ae06ce514715",
    ),
    (
        "roots",
        "build-msvc/data-generation/omega-decision-v2/roots.jsonl",
        6656502,
        "e1096883c760e9717f0ec1e1a012f1ce8f7cf2939faf8cb97731e3e2eead399a",
    ),
    (
        "children",
        "build-msvc/data-generation/omega-decision-v2/children.jsonl",
        216088727,
        "52e8cb79ebe40f6dbaadcb3e25ec107bfd8f98a33ff444f86ff960a9efb207da",
    ),
)

G5_CLOSURE_SPECS: tuple[tuple[str, str, int, str], ...] = (
    (
        "g5Closure",
        G5_CLOSURE_RELATIVE,
        11380,
        "0e1878cbb2eea867d1f53dd39ea64e39dbcfabbc7bcc871457b06ddc0c1100c9",
    ),
    (
        "g5Preregistration",
        G5_PREREGISTRATION_RELATIVE,
        41620,
        "60f48b19902b62bea88bd4877d55f798e0b2d6c6751f8acd59cb8522a3fdd5ab",
    ),
    (
        "g5FinalFreeze",
        G5_FINAL_FREEZE_RELATIVE,
        12914,
        "03ff3fefc9ef4eff553f9aebdaed4f5827eeff29ef613b22ecd5b613f1a435a1",
    ),
)

# The pre-label closure is the last of the three bound G5 freezes.  A G5 prior
# catalog cannot predate it.  Callers creating a G6 plan should pass that plan's
# timestamp to ``verify_catalog_groups`` so the other side of the chronology is
# checked as well.
G5_LATEST_BOUND_FREEZE_UTC = "2026-07-23T19:54:04.576178Z"

MAX_JSON_SOURCE_BYTES = 64 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 16 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_WHOLE_SECOND_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_REPARSE_POINT = 0x0400


def _absolute(relative: str | Path) -> Path:
    path = Path(relative)
    if not path.is_absolute():
        path = REPO / path
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _identity_from_spec(spec: Sequence[Any]) -> dict[str, Any]:
    relative, byte_count, digest = spec[-3:]
    if type(relative) is not str or type(byte_count) is not int:
        raise TypeError("identity specification is malformed")
    if type(digest) is not str or _SHA.fullmatch(digest) is None:
        raise TypeError("identity specification has a malformed SHA-256")
    return {
        "path": str(_absolute(relative)),
        "bytes": byte_count,
        "sha256": digest,
    }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _utc(value: str, label: str) -> datetime:
    routing._validate_prior_timestamp(value, label)
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{label} is not UTC")
    return parsed


def _row_bytes(row: Mapping[str, Any]) -> bytes:
    # This is deliberately byte-identical to G5's reviewed _atomic_jsonl.
    return (
        json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _manifest_bytes(document: Mapping[str, Any]) -> bytes:
    # This is deliberately byte-identical to G5's reviewed _atomic_json.
    return (json.dumps(dict(document), indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _publish_owned_chunks(
    path: Path,
    chunks: Iterable[bytes],
    expected_identity: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    """No-clobber publish and return an inode-bound rollback capability."""

    expected = routing._validate_identity_record(dict(expected_identity), label)
    target = routing._safe_parent(path)
    if str(target) != expected["path"]:
        raise ValueError(f"{label} expected path differs from its fixed target")
    if target.exists():
        raise FileExistsError(f"refusing to overwrite {target}")
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_text)
    linked_inode: tuple[int, int] | None = None
    try:
        digest = hashlib.sha256()
        byte_count = 0
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            for chunk in chunks:
                if type(chunk) is not bytes:
                    raise TypeError(f"{label} publisher received a non-bytes chunk")
                stream.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_stat = os.fstat(stream.fileno())
        generated = {
            "path": str(target),
            "bytes": byte_count,
            "sha256": digest.hexdigest(),
        }
        if generated != expected or temporary_stat.st_size != byte_count:
            raise ValueError(f"{label} bytes differ from deterministic replay")
        before_link = os.lstat(temporary)
        attributes = getattr(before_link, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(before_link.st_mode)
            or attributes & _REPARSE_POINT
            or not stat.S_ISREG(before_link.st_mode)
            or before_link.st_nlink != 1
            or (before_link.st_dev, before_link.st_ino)
            != (temporary_stat.st_dev, temporary_stat.st_ino)
        ):
            raise ValueError(f"{label} temporary changed before publication")
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise FileExistsError(f"refusing to overwrite {target}") from None
        # Ownership begins when link(2) returns, before any later metadata call
        # can fail.  Retaining the already authenticated temporary inode here
        # ensures that even an injected first lstat/identity failure can clean
        # up only this invocation's newly published target.
        linked_inode = (before_link.st_dev, before_link.st_ino)
        target_stat = os.lstat(target)
        if (target_stat.st_dev, target_stat.st_ino) != linked_inode:
            raise ValueError(f"{label} publication linked an unexpected inode")
        temporary.unlink()
        after_publish = os.lstat(target)
        if (
            (after_publish.st_dev, after_publish.st_ino) != linked_inode
            or after_publish.st_nlink != 1
            or after_publish.st_size != expected["bytes"]
        ):
            raise ValueError(f"{label} changed while being published")
        return {"identity": expected, "inode": linked_inode}
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        if linked_inode is not None:
            try:
                current = os.lstat(target)
                if (current.st_dev, current.st_ino) == linked_inode:
                    target.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _owned_publication_matches(publication: Mapping[str, Any], label: str) -> bool:
    identity = publication.get("identity")
    inode = publication.get("inode")
    if type(identity) is not dict or type(inode) is not tuple or len(inode) != 2:
        return False
    try:
        expected = routing._validate_identity_record(identity, label)
        path = routing._safe_parent(Path(expected["path"]))
        before = os.lstat(path)
        attributes = getattr(before, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(before.st_mode)
            or attributes & _REPARSE_POINT
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != inode
            or before.st_size != expected["bytes"]
        ):
            return False
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        digest = hashlib.sha256()
        total = 0
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != inode:
                return False
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
            after_descriptor = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = os.lstat(path)
        return (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            and (after_descriptor.st_dev, after_descriptor.st_ino) == inode
            and total == expected["bytes"]
            and digest.hexdigest() == expected["sha256"]
        )
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return False


def _verify_owned_publication(
    publication: Mapping[str, Any], label: str
) -> dict[str, Any]:
    if not _owned_publication_matches(publication, label):
        raise ValueError(f"{label} differs from the inode-bound publication")
    return dict(publication["identity"])


def _remove_owned_publication(publication: Mapping[str, Any], label: str) -> bool:
    """Remove only the exact inode and bytes published by this invocation."""

    if not _owned_publication_matches(publication, label):
        return False
    identity = publication["identity"]
    inode = publication["inode"]
    path = Path(identity["path"])
    try:
        current = os.lstat(path)
        if (
            (current.st_dev, current.st_ino) != inode
            or current.st_nlink != 1
            or current.st_size != identity["bytes"]
        ):
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _exact_keys(value: Any, expected: Iterable[str], label: str) -> dict[str, Any]:
    names = set(expected)
    if type(value) is not dict or set(value) != names:
        actual = sorted(value) if type(value) is dict else type(value).__name__
        raise ValueError(f"{label} fields changed: expected {sorted(names)}, found {actual}")
    return value


def _producer_identity() -> dict[str, Any]:
    def bound(identity: Mapping[str, Any], label: str) -> dict[str, Any]:
        expected = dict(identity)
        actual = routing._identity(Path(expected["path"]), label)
        if actual != expected:
            raise RuntimeError(f"{label} changed after its exact execution snapshot")
        return expected

    return {
        "catalogBuilder": routing._identity(Path(__file__).resolve(), "catalog builder"),
        "g6Routing": bound(ROUTING_EXECUTION_IDENTITY, "G6 routing authority"),
        "omegaDecisionTeacher": bound(
            G5_EXECUTION_IDENTITY, "reviewed G5 lexical scanner"
        ),
        "deepHceV2": bound(
            DEEP_HCE_EXECUTION_IDENTITY, "reviewed leakage semantics"
        ),
        "omegaNnue": bound(
            _omega_dependency.identity, "Omega network/board semantics"
        ),
        "selectScreen": bound(
            _screen_dependency.identity, "Omega symmetry/phase semantics"
        ),
        "generation5Runtime": bound(
            GENERATION5_RUNTIME_EXECUTION_IDENTITY, "G5 runtime authority"
        ),
        "generation5PreregistrationValidator": bound(
            V5_PREREG_EXECUTION_IDENTITY, "G5 preregistration authority"
        ),
        "generation4AbortVerifier": bound(
            G4_ABORT_EXECUTION_IDENTITY, "G4 closure authority"
        ),
        "priorProjection": bound(
            PRIOR_PROJECTION_EXECUTION_IDENTITY, "prior-projection authority"
        ),
        "python": routing._identity(Path(sys.executable).resolve(), "Python runtime"),
    }


def _extraction_policy() -> dict[str, Any]:
    return {
        "policyId": "omega-target-opaque-lexical-ofen-g6-g5-prior-v1",
        "sourceSelection": "authenticated-fixed-generation-5-closure-only",
        "supportedSuffixes": list(g5.FORBIDDEN_SOURCE_SUFFIXES),
        "decodedStringFieldNames": sorted(
            g5.FORBIDDEN_OFEN_FIELD_NAMES
            | g5.FORBIDDEN_GAME_ID_FIELD_NAMES
            | g5.FORBIDDEN_RUN_ID_FIELD_NAMES
        ),
        "sensitiveFieldPolicy": "skip-complete-value-lexeme-without-decoding",
        "unknownScalarPolicy": "skip-value-lexeme-without-decoding",
        "nestedContainerPolicy": "recurse-except-sensitive-fields",
        "positionIdPolicy": "sha256-of-ofen-and-source-provenance",
        "g5PositionSourceRoles": [spec[0] for spec in G5_SOURCE_SPECS],
        "completeRawAndRetainedGraphs": True,
        "sourceProjectionPolicy": "none-generation-5-native-sources",
    }


def _declared_g5_context() -> dict[str, Any]:
    by_role = {
        role: _identity_from_spec((relative, byte_count, digest))
        for role, relative, byte_count, digest in G5_SOURCE_SPECS
    }
    inventory = sorted(by_role.values(), key=lambda item: item["path"].casefold())
    closure = {
        role: _identity_from_spec((relative, byte_count, digest))
        for role, relative, byte_count, digest in G5_CLOSURE_SPECS
    }
    audit = {
        **closure,
        "closurePolicy": (
            "authenticated G5 source data, complete raw graph, and exact retained "
            "derivation frozen before teacher search"
        ),
    }
    return {"byRole": by_role, "sourceInventory": inventory, "audit": audit}


def _authenticated_g5_context() -> dict[str, Any]:
    """Freshly verify the canonical G5 closure and its seven fixed sources."""

    closure_path = _absolute(G5_CLOSURE_RELATIVE)
    preregistration_path = _absolute(G5_PREREGISTRATION_RELATIVE)
    final_freeze_path = _absolute(G5_FINAL_FREEZE_RELATIVE)
    # The exact preregistration validator deliberately confirms that its runtime
    # dependency still occupies the public import slot.  Bind only that slot for
    # the duration of the validation and restore any caller value afterwards;
    # all other sibling imports remain private and injection-only.
    runtime_name = "king_state_generation5_runtime"
    missing = object()
    previous = sys.modules.get(runtime_name, missing)
    sys.modules[runtime_name] = _runtime_contract
    try:
        seal, seal_identity, profile = g5._verify_prelabel_seal(
            closure_path, preregistration_path, final_freeze_path
        )
        if sys.modules.get(runtime_name) is not _runtime_contract:
            raise RuntimeError("G5 runtime public import slot changed during validation")
    finally:
        sys.modules.pop(runtime_name, None)
        if previous is not missing:
            sys.modules[runtime_name] = previous
    declared = _declared_g5_context()
    expected_closure = declared["audit"]
    actual_closure = {
        "g5Closure": routing._identity(closure_path, "G5 pre-label closure"),
        "g5Preregistration": routing._identity(
            preregistration_path, "G5 preregistration"
        ),
        "g5FinalFreeze": routing._identity(final_freeze_path, "G5 final freeze"),
    }
    if seal_identity != actual_closure["g5Closure"]:
        raise ValueError("G5 closure changed between semantic and descriptor verification")
    for name, identity in actual_closure.items():
        if identity != expected_closure[name]:
            raise ValueError(f"{name} differs from the fixed G5 closure identity")

    identities = _exact_keys(
        seal.get("identities"),
        {
            "preregistration", "finalFreezeSeal", "rawRoots", "rawRootsManifest",
            "rawChildren", "rawChildrenManifest", "rawSamplerCompletionSeal",
            "rootFeasibility", "rootFeasibilityManifest", "rootFeasibilitySeal",
            "roots", "rootManifest", "children", "childrenManifest",
            "samplerCompletionSeal", "componentMap", "teacherEngine",
            "forbiddenCatalogs", "producer",
        },
        "G5 closure identities",
    )
    actual_by_role = {
        "sourceEvents": g5._frozen_identity(profile, "sourceEvents"),
        "sourceOpeningSuite": g5._frozen_identity(profile, "sourceOpeningSuite"),
        "sourceRootPool": g5._frozen_identity(profile, "sourceRootPool"),
        "rawRoots": identities["rawRoots"],
        "rawChildren": identities["rawChildren"],
        "roots": identities["roots"],
        "children": identities["children"],
    }
    if set(actual_by_role) != set(declared["byRole"]):
        raise ValueError("G5 position-source role inventory changed")
    for role, expected in declared["byRole"].items():
        if actual_by_role[role] != expected:
            raise ValueError(f"G5 position source {role} differs from its fixed identity")
    return declared


def _assert_regular_descriptor(path: Path, label: str) -> tuple[Path, os.stat_result, int]:
    lexical = routing._safe_parent(path)
    before = os.lstat(lexical)
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or attributes & _REPARSE_POINT
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise ValueError(f"{label} must be one non-linked regular file: {lexical}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lexical, flags)
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        os.close(descriptor)
        raise ValueError(f"{label} changed before descriptor open")
    return lexical, before, descriptor


def _finish_descriptor(
    lexical: Path,
    before: os.stat_result,
    descriptor_stat: os.stat_result,
    label: str,
) -> None:
    after = os.lstat(lexical)
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or (descriptor_stat.st_dev, descriptor_stat.st_ino, descriptor_stat.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
    ):
        raise ValueError(f"{label} changed while being scanned")


def _scan_position_source(
    expected: Mapping[str, Any], label: str
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    expected_identity = routing._validate_identity_record(dict(expected), label)
    path = Path(expected_identity["path"])
    suffix = path.name.lower()
    if not suffix.endswith(g5.FORBIDDEN_SOURCE_SUFFIXES):
        raise ValueError(f"{label} has an unsupported source suffix")
    lexical, before, descriptor = _assert_regular_descriptor(path, label)
    if str(lexical) != expected_identity["path"] or before.st_size != expected_identity["bytes"]:
        os.close(descriptor)
        raise ValueError(f"{label} differs from its declared path/size")

    digest = hashlib.sha256()
    total = 0
    observations: list[dict[str, str]] = []
    descriptor_stat: os.stat_result | None = None
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            if suffix.endswith((".jsonl", ".ndjson")):
                line_number = 0
                while True:
                    raw = stream.readline(MAX_JSONL_LINE_BYTES + 1)
                    if not raw:
                        break
                    line_number += 1
                    if len(raw) > MAX_JSONL_LINE_BYTES:
                        raise ValueError(f"{label}:{line_number} exceeds the line bound")
                    digest.update(raw)
                    total += len(raw)
                    if not raw.strip():
                        continue
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError as error:
                        raise ValueError(f"{label}:{line_number} is not UTF-8") from error
                    node = g5._extract_catalog_node(text, f"{label}:{line_number}")
                    observations.extend(node.observations)
            else:
                if before.st_size > MAX_JSON_SOURCE_BYTES:
                    raise ValueError(f"{label} exceeds the bounded JSON source size")
                payload = stream.read(MAX_JSON_SOURCE_BYTES + 1)
                if len(payload) > MAX_JSON_SOURCE_BYTES:
                    raise ValueError(f"{label} exceeds the bounded JSON source size")
                digest.update(payload)
                total = len(payload)
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError(f"{label} is not UTF-8") from error
                observations.extend(g5._extract_catalog_node(text, label).observations)
            descriptor_stat = os.fstat(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if descriptor_stat is None:
        raise AssertionError("source descriptor was not finalized")
    _finish_descriptor(lexical, before, descriptor_stat, label)
    actual = {"path": str(lexical), "bytes": total, "sha256": digest.hexdigest()}
    if actual != expected_identity:
        raise ValueError(f"{label} differs from its fixed content identity")
    if not observations:
        raise ValueError(f"{label} yielded no allowlisted Omega OFEN")
    for observation in observations:
        observation["sourceArtifactSha256"] = actual["sha256"]
    return observations, actual


def _replay_g5_catalog(
    context: Mapping[str, Any], catalog_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    inventory = context.get("sourceInventory")
    if type(inventory) is not list or len(inventory) != len(G5_SOURCE_SPECS):
        raise ValueError("G5 context has the wrong source inventory")
    expected_inventory = _declared_g5_context()["sourceInventory"]
    if inventory != expected_inventory:
        raise ValueError("G5 context source inventory differs from the fixed closure")
    observations: list[dict[str, str]] = []
    scanned: list[dict[str, Any]] = []
    for index, expected in enumerate(inventory):
        found, identity = _scan_position_source(expected, f"G5 position source {index}")
        observations.extend(found)
        scanned.append(identity)
    if scanned != inventory:
        raise ValueError("G5 position-source inventory changed during lexical replay")
    rows = g5._catalog_rows_from_observations(observations)
    if not rows:
        raise ValueError("G5 lexical replay produced an empty catalog")
    digest = hashlib.sha256()
    byte_count = 0
    for row in rows:
        if row.get("kind") != ROW_KIND or set(row) - routing.FORBIDDEN_ROW_FIELDS:
            raise ValueError("reviewed G5 row normalizer emitted an unsupported row")
        payload = _row_bytes(row)
        digest.update(payload)
        byte_count += len(payload)
    identity = {
        "path": str(_absolute(catalog_path)),
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
    }
    return rows, identity


def _catalog_schema() -> dict[str, Any]:
    return {
        "recognizedFields": sorted(routing.FORBIDDEN_ROW_FIELDS),
        "unknownFieldPolicy": "abort",
        "minimumPositionIdentity": (
            "at least one exact or conservative orbit signature per row"
        ),
    }


def _expected_g5_manifest(
    *,
    catalog_identity: Mapping[str, Any],
    context: Mapping[str, Any],
    created_utc: str,
    position_count: int,
) -> dict[str, Any]:
    inventory = list(context["sourceInventory"])
    audits = [dict(context["audit"])]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": MANIFEST_KIND,
        "targetOpaque": True,
        "createdUtc": created_utc,
        "catalog": dict(catalog_identity),
        "catalogSchema": _catalog_schema(),
        "sourceInventory": inventory,
        "sourceInventorySha256": _digest(inventory),
        "sourceProjectionManifests": [],
        "sourceProjectionManifestsSha256": _digest([]),
        "priorSourceAudits": audits,
        "priorSourceAuditsSha256": _digest(audits),
        "extractionPolicy": _extraction_policy(),
        "positionCount": position_count,
        "targetOrScoreFieldsDecoded": 0,
        "targetOrScoreFieldsEmitted": 0,
        "producer": _producer_identity(),
    }


def _fast_file_shape(identity: Mapping[str, Any], label: str) -> None:
    declared = routing._validate_identity_record(dict(identity), label)
    lexical = routing._safe_parent(Path(declared["path"]))
    metadata = os.lstat(lexical)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or attributes & _REPARSE_POINT
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size != declared["bytes"]
        or str(lexical) != declared["path"]
    ):
        raise ValueError(f"{label} path/type/size differs from its declaration")


def _coerce_manifest(
    value: Any, label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    declared: dict[str, Any] | None = None
    if type(value) is dict:
        declared = routing._validate_identity_record(value, label)
        path = Path(declared["path"])
    elif isinstance(value, (str, os.PathLike)):
        path = Path(value)
    else:
        raise ValueError(f"{label} must be a manifest path or strict identity")
    document, identity = routing._load_json(
        path,
        label,
        target_opaque=True,
        allowed_target_metadata_keys={"deepHceV2", "omegaDecisionTeacher"},
    )
    if declared is not None and identity != declared:
        raise ValueError(f"{label} differs from its supplied identity")
    return document, identity


def _verify_common_manifest(document: Mapping[str, Any], label: str) -> None:
    _exact_keys(document, routing.FORBIDDEN_MANIFEST_FIELDS, label)
    if (
        document["schemaVersion"] != SCHEMA_VERSION
        or document["kind"] != MANIFEST_KIND
        or document["targetOpaque"] is not True
        or document["targetOrScoreFieldsDecoded"] != 0
        or document["targetOrScoreFieldsEmitted"] != 0
        or document["catalogSchema"] != _catalog_schema()
    ):
        raise ValueError(f"{label} is not the strict target-opaque catalog contract")
    routing._validate_prior_timestamp(document["createdUtc"], f"{label}.createdUtc")
    for name, digest_name in (
        ("sourceInventory", "sourceInventorySha256"),
        ("sourceProjectionManifests", "sourceProjectionManifestsSha256"),
        ("priorSourceAudits", "priorSourceAuditsSha256"),
    ):
        if type(document[name]) is not list or document[digest_name] != _digest(document[name]):
            raise ValueError(f"{label} {name} digest differs")
    if type(document["positionCount"]) is not int or document["positionCount"] <= 0:
        raise ValueError(f"{label} has no positions")
    if type(document["extractionPolicy"]) is not dict:
        raise ValueError(f"{label} has no extraction policy")
    if routing._verify_embedded_identities(document["producer"], f"{label}.producer") < 1:
        raise ValueError(f"{label} producer has no content identity")


def _verify_g34_group(value: Any, *, verify_catalog_hash: bool) -> dict[str, Any]:
    document, identity = _coerce_manifest(value, "G3+G4 forbidden manifest")
    expected_manifest = _identity_from_spec(G34_MANIFEST_SPEC)
    if identity != expected_manifest:
        raise ValueError("G3+G4 forbidden manifest differs from the fixed v2 identity")
    _verify_common_manifest(document, "G3+G4 forbidden manifest")
    if document["catalog"] != _identity_from_spec(G34_CATALOG_SPEC):
        raise ValueError("G3+G4 forbidden catalog identity changed")
    if document["positionCount"] != G34_POSITION_COUNT:
        raise ValueError("G3+G4 forbidden position count changed")
    expected_sources = sorted(
        [_identity_from_spec(spec) for spec in G34_SOURCE_SPECS],
        key=lambda item: item["path"].casefold(),
    )
    if document["sourceInventory"] != expected_sources:
        raise ValueError("G3+G4 source inventory changed")
    if document["sourceProjectionManifests"] != [
        _identity_from_spec(G34_PROJECTION_SPEC)
    ]:
        raise ValueError("G3 projection manifest changed")
    audits = document["priorSourceAudits"]
    if (
        type(audits) is not list
        or len(audits) != 1
        or type(audits[0]) is not dict
        or audits[0].get("closure") != _identity_from_spec(G34_CLOSURE_SPEC)
    ):
        raise ValueError("G4 closure evidence changed")
    if verify_catalog_hash:
        actual = routing._identity(Path(document["catalog"]["path"]), "G3+G4 catalog")
        if actual != document["catalog"]:
            raise ValueError("G3+G4 catalog differs from its fixed identity")
    else:
        _fast_file_shape(document["catalog"], "G3+G4 catalog")
    return identity


def _verify_g5_lineage(
    value: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    document, identity = _coerce_manifest(value, "G5 forbidden manifest")
    expected_path = str(_absolute(G5_MANIFEST_RELATIVE))
    if identity["path"] != expected_path:
        raise ValueError("G5 forbidden manifest is outside its fixed output path")
    _verify_common_manifest(document, "G5 forbidden manifest")
    if _utc(
        document["createdUtc"], "G5 forbidden manifest createdUtc"
    ) <= _utc(G5_LATEST_BOUND_FREEZE_UTC, "latest bound G5 freeze"):
        raise ValueError("G5 forbidden manifest does not follow every bound G5 freeze")
    context = _declared_g5_context()
    if document["sourceInventory"] != context["sourceInventory"]:
        raise ValueError("G5 forbidden source inventory differs from the fixed closure")
    if document["sourceProjectionManifests"] != []:
        raise ValueError("native G5 sources must not claim a prior projection")
    if document["priorSourceAudits"] != [context["audit"]]:
        raise ValueError("G5 forbidden closure evidence changed")
    if document["extractionPolicy"] != _extraction_policy():
        raise ValueError("G5 forbidden lexical extraction policy changed")
    if document["producer"] != _producer_identity():
        raise ValueError("G5 forbidden producer identity changed")
    catalog = routing._validate_identity_record(
        document["catalog"], "G5 forbidden catalog"
    )
    if catalog["path"] != str(_absolute(G5_CATALOG_RELATIVE)):
        raise ValueError("G5 forbidden catalog is outside its fixed output path")
    _fast_file_shape(catalog, "G5 forbidden catalog")
    return document, identity, context


def canonical_catalog_groups() -> list[dict[str, Any]]:
    return [
        {
            "coveredSourceIds": ["G3", "G4"],
            "manifest": str(_absolute(G34_MANIFEST_RELATIVE)),
        },
        {
            "coveredSourceIds": ["G5"],
            "manifest": str(_absolute(G5_MANIFEST_RELATIVE)),
        },
    ]


def verify_catalog_groups(
    groups: Sequence[Mapping[str, Any]],
    *,
    full_replay: bool = True,
    plan_created_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Verify and normalize the only allowed G6 prior-catalog grouping.

    ``full_replay=True`` is the security boundary and therefore the default. It
    freshly authenticates and lexically replays all seven G5 sources, rebuilds
    the deterministic expected catalog identity/count, and compares them with
    the bound catalog.  It does not trust metadata to prove completeness.

    ``full_replay=False`` verifies lineage and file shape only.  It is exposed
    solely for diagnostics; it cannot establish catalog completeness.
    """

    if type(groups) not in (list, tuple) or len(groups) != 2:
        raise ValueError("prior catalogs must be exactly two ordered groups")
    expected_sources = (["G3", "G4"], ["G5"])
    normalized: list[dict[str, Any]] = []
    manifests: list[Any] = []
    for index, (entry, expected) in enumerate(zip(groups, expected_sources)):
        _exact_keys(entry, {"coveredSourceIds", "manifest"}, f"catalog group {index}")
        sources = entry["coveredSourceIds"]
        if type(sources) is not list or sources != expected:
            raise ValueError(
                "prior catalogs must be ordered as [G3,G4] existing v2 plus [G5]"
            )
        manifests.append(entry["manifest"])

    g34_identity = _verify_g34_group(
        manifests[0], verify_catalog_hash=full_replay
    )
    g5_document, g5_identity, declared_context = _verify_g5_lineage(manifests[1])
    if plan_created_utc is not None and _utc(
        g5_document["createdUtc"], "G5 forbidden manifest createdUtc"
    ) >= _utc(plan_created_utc, "G6 plan createdUtc"):
        raise ValueError("G5 forbidden manifest must precede the G6 plan")
    if full_replay:
        authenticated_context = _authenticated_g5_context()
        if authenticated_context != declared_context:
            raise ValueError("fresh G5 closure differs from the declared fixed context")
        rows, expected_catalog = _replay_g5_catalog(
            authenticated_context, _absolute(G5_CATALOG_RELATIVE)
        )
        if (
            g5_document["catalog"] != expected_catalog
            or g5_document["positionCount"] != len(rows)
        ):
            raise ValueError(
                "G5 forbidden catalog is incomplete or differs from fresh lexical replay"
            )
        actual_catalog = routing._identity(
            Path(expected_catalog["path"]), "G5 forbidden catalog"
        )
        if actual_catalog != expected_catalog:
            raise ValueError("G5 forbidden catalog bytes differ from fresh lexical replay")
    normalized.append(
        {"coveredSourceIds": ["G3", "G4"], "manifest": g34_identity}
    )
    normalized.append({"coveredSourceIds": ["G5"], "manifest": g5_identity})
    return normalized


def catalog_group_created_utc(
    groups: Sequence[Mapping[str, Any]], *, full_replay: bool = True
) -> str:
    """Return the verified G5 catalog time for an external plan chronology gate."""

    verify_catalog_groups(groups, full_replay=full_replay)
    document, _identity = _coerce_manifest(groups[1]["manifest"], "G5 forbidden manifest")
    return routing._validate_prior_timestamp(
        document["createdUtc"], "G5 forbidden manifest createdUtc"
    )


def _ensure_output_directory() -> Path:
    directory = _absolute(G5_OUTPUT_DIRECTORY_RELATIVE)
    parent = directory.parent
    routing._safe_parent(parent / ".g5-prior-parent-check")
    if not directory.exists():
        os.mkdir(directory)
    metadata = os.lstat(directory)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or attributes & _REPARSE_POINT
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise ValueError("G5 prior-catalog output directory is not a safe directory")
    return directory


def _full_route_verify(manifests: Sequence[Path]) -> dict[str, Any]:
    forbidden = routing._load_forbidden(list(manifests))
    try:
        return {
            "positions": forbidden.positions,
            "manifests": list(forbidden.manifests),
            "catalogs": list(forbidden.catalogs),
            "sourceArtifacts": len(forbidden.source_artifact_sha256),
        }
    finally:
        forbidden.close()


def build_catalog(*, created_utc: str) -> dict[str, Any]:
    """Build the fixed G5 catalog and perform a full post-build row replay."""

    if _WHOLE_SECOND_UTC.fullmatch(created_utc) is None:
        raise ValueError("createdUtc must be canonical UTC to whole seconds")
    routing._validate_prior_timestamp(created_utc, "G5 catalog createdUtc")
    directory = _ensure_output_directory()
    catalog_path = directory / "positions.jsonl"
    manifest_path = directory / "positions.manifest.json"
    if catalog_path.exists() or manifest_path.exists():
        existing = catalog_path if catalog_path.exists() else manifest_path
        raise FileExistsError(f"refusing to overwrite {existing}")

    context = _authenticated_g5_context()
    rows, expected_catalog = _replay_g5_catalog(context, catalog_path)
    catalog_publication: dict[str, Any] | None = None
    manifest_publication: dict[str, Any] | None = None
    published_catalog: dict[str, Any] | None = None
    published_manifest: dict[str, Any] | None = None
    try:
        catalog_publication = _publish_owned_chunks(
            catalog_path,
            (_row_bytes(row) for row in rows),
            expected_catalog,
            "published G5 catalog",
        )
        published_catalog = _verify_owned_publication(
            catalog_publication, "published G5 catalog"
        )
        if published_catalog != expected_catalog:
            raise ValueError("published G5 catalog differs from deterministic replay")
        document = _expected_g5_manifest(
            catalog_identity=published_catalog,
            context=context,
            created_utc=created_utc,
            position_count=len(rows),
        )
        manifest_payload = _manifest_bytes(document)
        expected_manifest = {
            "path": str(manifest_path),
            "bytes": len(manifest_payload),
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
        }
        manifest_publication = _publish_owned_chunks(
            manifest_path,
            (manifest_payload,),
            expected_manifest,
            "published G5 catalog manifest",
        )
        published_manifest = _verify_owned_publication(
            manifest_publication, "published G5 catalog manifest"
        )
        _verify_g5_lineage(published_manifest)
        routed = _full_route_verify([manifest_path])
        if (
            routed["positions"] != len(rows)
            or routed["manifests"] != [published_manifest]
            or routed["catalogs"] != [published_catalog]
        ):
            raise ValueError("post-build G6 routing replay differs from G5 publication")
        return published_manifest
    except BaseException:
        # Roll back only this invocation's inode-bound fresh pair.  Ownership is
        # established by the publisher before any later identity/route check can
        # fail, so a successfully linked manifest cannot be stranded merely
        # because its first post-publication identity check raised.
        if manifest_publication is not None:
            _remove_owned_publication(
                manifest_publication, "failed G5 catalog manifest"
            )
        if catalog_publication is not None:
            _remove_owned_publication(catalog_publication, "failed G5 catalog")
        raise


def verify_full() -> dict[str, Any]:
    records = verify_catalog_groups(canonical_catalog_groups(), full_replay=True)
    manifests = [Path(record["manifest"]["path"]) for record in records]
    return _full_route_verify(manifests)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--created-utc", required=True)
    verify_groups = commands.add_parser("verify-groups")
    verify_groups.add_argument(
        "--lineage-only",
        action="store_true",
        help="diagnostic only; do not establish completeness from this mode",
    )
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        identity = build_catalog(created_utc=args.created_utc)
        print(json.dumps(identity, sort_keys=True))
    elif args.command == "verify-groups":
        records = verify_catalog_groups(
            canonical_catalog_groups(), full_replay=not args.lineage_only
        )
        print(json.dumps(records, sort_keys=True))
    elif args.command == "verify":
        print(json.dumps(verify_full(), sort_keys=True))
    else:  # pragma: no cover - argparse owns the closed command set.
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
