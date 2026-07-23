#!/usr/bin/env python3
"""Build and verify the exact minimal .NET runtime used by G5 matches.

Only the muxer, host/fxr 10.0.9, and Microsoft.NETCore.App 10.0.9 are
admitted.  The adjacent manifest is an exact file inventory: an added,
missing, or changed file invalidates the bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
RUNTIME_VERSION = "10.0.9"
KIND = "omega-nnue-king-state-v5-dotnet-runtime-bundle"
REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
RUNTIME_ROOT = (
    REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime"
)
MANIFEST_PATH = (
    REPO
    / "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime.manifest.json"
)
SOURCE_ROOT = WORKSPACE / ".dotnet"
ALLOWED_PREFIXES = (
    f"host/fxr/{RUNTIME_VERSION}/",
    f"shared/Microsoft.NETCore.App/{RUNTIME_VERSION}/",
)
CANONICAL_ROOT_RECORD = (
    "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime"
)


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    path = _resolve(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _strict_load(path: Path) -> dict[str, Any]:
    value = json.loads(
        _resolve(path).read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON token {token}")
        ),
    )
    if type(value) is not dict:
        raise ValueError("runtime manifest must be a JSON object")
    return value


def _runtime_files(root: Path) -> list[Path]:
    root = _resolve(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _file_inventory(root: Path) -> list[dict[str, Any]]:
    root = _resolve(root)
    return [
        {
            "relativePath": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in _runtime_files(root)
    ]


def _bundle_sha(files: Sequence[Mapping[str, Any]]) -> str:
    canonical = bytearray()
    for item in files:
        canonical.extend(
            (
                f"{item['relativePath']}\t{item['bytes']}\t{item['sha256']}\n"
            ).encode("utf-8")
        )
    return hashlib.sha256(canonical).hexdigest()


def _manifest_value(root: Path) -> dict[str, Any]:
    root = _resolve(root)
    files = _file_inventory(root)
    root_record = (
        CANONICAL_ROOT_RECORD if root == _resolve(RUNTIME_ROOT) else str(root)
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "runtimeVersion": RUNTIME_VERSION,
        "root": root_record,
        "dotnetHostRelativePath": "dotnet.exe",
        "includedTrees": [
            f"host/fxr/{RUNTIME_VERSION}",
            f"shared/Microsoft.NETCore.App/{RUNTIME_VERSION}",
        ],
        "excludedTrees": ["sdk", "sdk-manifests", "packs", "templates"],
        "files": files,
        "bundleSha256": _bundle_sha(files),
    }


def _validate_inventory(
    value: Mapping[str, Any], root: Path, *, require_canonical_layout: bool
) -> dict[str, Any]:
    expected_fields = {
        "schemaVersion",
        "kind",
        "runtimeVersion",
        "root",
        "dotnetHostRelativePath",
        "includedTrees",
        "excludedTrees",
        "files",
        "bundleSha256",
    }
    root = _resolve(root)
    if set(value) != expected_fields:
        raise ValueError(".NET runtime manifest field inventory changed")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != KIND
        or value.get("runtimeVersion") != RUNTIME_VERSION
        or _resolve(
            (
                REPO / Path(str(value.get("root", "")))
                if not Path(str(value.get("root", ""))).is_absolute()
                else Path(str(value.get("root", "")))
            )
        )
        != root
        or value.get("dotnetHostRelativePath") != "dotnet.exe"
        or value.get("includedTrees")
        != [
            f"host/fxr/{RUNTIME_VERSION}",
            f"shared/Microsoft.NETCore.App/{RUNTIME_VERSION}",
        ]
        or value.get("excludedTrees")
        != ["sdk", "sdk-manifests", "packs", "templates"]
    ):
        raise ValueError(".NET runtime manifest contract changed")
    files = value.get("files")
    if type(files) is not list or not files:
        raise ValueError(".NET runtime inventory is empty")
    prior = ""
    for item in files:
        if (
            type(item) is not dict
            or set(item) != {"relativePath", "bytes", "sha256"}
            or type(item.get("relativePath")) is not str
            or not item["relativePath"]
            or item["relativePath"] <= prior
            or type(item.get("bytes")) is not int
            or item["bytes"] < 0
            or type(item.get("sha256")) is not str
            or len(item["sha256"]) != 64
        ):
            raise ValueError(".NET runtime inventory record changed")
        relative = Path(item["relativePath"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(".NET runtime inventory path escapes its root")
        prior = item["relativePath"]
    actual = _file_inventory(root)
    if files != actual:
        raise ValueError(".NET runtime has a missing, extra, or changed file")
    paths = [item["relativePath"] for item in files]
    if require_canonical_layout:
        if root != _resolve(RUNTIME_ROOT):
            raise ValueError(".NET runtime root is not canonical")
        if value.get("root") != CANONICAL_ROOT_RECORD:
            raise ValueError(".NET runtime manifest root is not repo-relative")
        if paths.count("dotnet.exe") != 1 or any(
            path != "dotnet.exe"
            and not any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES)
            for path in paths
        ):
            raise ValueError(".NET runtime contains a file outside the minimal layout")
        if not any(path.startswith(ALLOWED_PREFIXES[0]) for path in paths) or not any(
            path.startswith(ALLOWED_PREFIXES[1]) for path in paths
        ):
            raise ValueError(".NET runtime is missing a required runtime tree")
    digest = _bundle_sha(files)
    if value.get("bundleSha256") != digest:
        raise ValueError(".NET runtime bundle digest changed")
    return dict(value)


def verify_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    path = _resolve(path)
    if path != _resolve(MANIFEST_PATH):
        raise ValueError(".NET runtime manifest must be canonical")
    value = _strict_load(path)
    return _validate_inventory(value, RUNTIME_ROOT, require_canonical_layout=True)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()


def refresh_manifest() -> dict[str, Any]:
    """Replace only a valid legacy/canonical manifest; never touch runtime files."""

    path = _resolve(MANIFEST_PATH)
    old = _strict_load(path)
    _validate_inventory(old, RUNTIME_ROOT, require_canonical_layout=False)
    value = _manifest_value(RUNTIME_ROOT)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        _atomic_json(temporary, value)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return verify_manifest(path)


def build() -> dict[str, Any]:
    source = _resolve(SOURCE_ROOT)
    destination = _resolve(RUNTIME_ROOT)
    manifest = _resolve(MANIFEST_PATH)
    if destination.exists() or manifest.exists():
        raise FileExistsError(destination if destination.exists() else manifest)
    required = [
        source / "dotnet.exe",
        source / f"host/fxr/{RUNTIME_VERSION}",
        source / f"shared/Microsoft.NETCore.App/{RUNTIME_VERSION}",
    ]
    if not required[0].is_file() or any(not path.is_dir() for path in required[1:]):
        raise FileNotFoundError("source .NET 10.0.9 runtime is incomplete")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copy2(required[0], destination / "dotnet.exe")
        shutil.copytree(
            required[1], destination / f"host/fxr/{RUNTIME_VERSION}"
        )
        shutil.copytree(
            required[2],
            destination / f"shared/Microsoft.NETCore.App/{RUNTIME_VERSION}",
        )
        value = _manifest_value(destination)
        _atomic_json(manifest, value)
        return verify_manifest(manifest)
    except BaseException:
        # A partial exclusive namespace is intentionally retained for forensic
        # inspection; automatic rebuild must not overwrite it.
        raise


def self_test() -> None:
    verify_manifest()
    with tempfile.TemporaryDirectory(prefix="omega-g5-dotnet-runtime-") as directory:
        root = Path(directory) / "runtime"
        (root / f"host/fxr/{RUNTIME_VERSION}").mkdir(parents=True)
        (root / f"shared/Microsoft.NETCore.App/{RUNTIME_VERSION}").mkdir(
            parents=True
        )
        (root / "dotnet.exe").write_bytes(b"host")
        (root / f"host/fxr/{RUNTIME_VERSION}/hostfxr.dll").write_bytes(b"fxr")
        framework = root / f"shared/Microsoft.NETCore.App/{RUNTIME_VERSION}/coreclr.dll"
        framework.write_bytes(b"clr")
        value = _manifest_value(root)
        _validate_inventory(value, root, require_canonical_layout=False)
        relocated = dict(value)
        relocated["root"] = "substituted/runtime"
        try:
            _validate_inventory(relocated, root, require_canonical_layout=False)
        except ValueError:
            pass
        else:
            raise AssertionError("substituted runtime root was accepted")
        framework.write_bytes(b"changed")
        try:
            _validate_inventory(value, root, require_canonical_layout=False)
        except ValueError:
            pass
        else:
            raise AssertionError("changed runtime file was accepted")
        framework.write_bytes(b"clr")
        extra = root / "extra.dll"
        extra.write_bytes(b"extra")
        try:
            _validate_inventory(value, root, require_canonical_layout=False)
        except ValueError:
            pass
        else:
            raise AssertionError("extra runtime file was accepted")
        extra.unlink()
        framework.unlink()
        try:
            _validate_inventory(value, root, require_canonical_layout=False)
        except ValueError:
            pass
        else:
            raise AssertionError("missing runtime file was accepted")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("build", "refresh-manifest", "verify", "self-test")
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "build":
        value = build()
        print(f"Frozen .NET runtime: {value['root']}")
        print(f"Bundle SHA-256: {value['bundleSha256']}")
    elif args.command == "refresh-manifest":
        value = refresh_manifest()
        print(f"Refreshed .NET runtime manifest: {MANIFEST_PATH}")
        print(f"Bundle SHA-256: {value['bundleSha256']}")
    elif args.command == "verify":
        value = verify_manifest()
        print(f"Verified .NET runtime bundle: {value['bundleSha256']}")
    else:
        self_test()
        print("Generation-5 .NET runtime self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
