#!/usr/bin/env python3
"""Canonical exact-pin launcher for the Omega decision-v3 verifier."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import sys
import types
from typing import Any, Sequence


VERIFIER_IMPLEMENTATION = "omega_decision_v3_verifier.py"
VERIFIER_IMPLEMENTATION_BYTES: int | None = 181_433
VERIFIER_IMPLEMENTATION_SHA256: str | None = (
    "62426e1836a770fe77e76815f44a341048865833241880d9993231f1138f7840"
)
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _runner_path() -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(Path(__file__)))))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int | None]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", None),
    )


def _snapshot(path: Path) -> tuple[dict[str, Any], bytes]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    for item in (*reversed(absolute.parents), absolute):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"verifier implementation traverses reparse path: {item}")
    before = os.lstat(absolute)
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_nlink", 1) != 1
    ):
        raise ValueError("verifier implementation is not one private regular file")
    descriptor = os.open(
        absolute,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError("verifier implementation changed before open")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
            digest.update(block)
        if _stat_key(os.fstat(descriptor)) != _stat_key(opened):
            raise ValueError("verifier implementation changed while read")
    finally:
        os.close(descriptor)
    if _stat_key(os.lstat(absolute)) != _stat_key(before):
        raise ValueError("verifier implementation path changed while read")
    payload = b"".join(chunks)
    return {
        "path": str(absolute),
        "bytes": len(payload),
        "sha256": digest.hexdigest(),
    }, payload


def _load_verifier() -> tuple[types.ModuleType, dict[str, Any]]:
    runner = _runner_path()
    runner_identity, _ = _snapshot(runner)
    if (
        type(VERIFIER_IMPLEMENTATION_BYTES) is not int
        or VERIFIER_IMPLEMENTATION_BYTES <= 0
        or type(VERIFIER_IMPLEMENTATION_SHA256) is not str
        or len(VERIFIER_IMPLEMENTATION_SHA256) != 64
    ):
        raise RuntimeError("canonical verifier implementation pin is not finalized")
    path = runner.with_name(VERIFIER_IMPLEMENTATION)
    identity, payload = _snapshot(path)
    if (
        identity["bytes"] != VERIFIER_IMPLEMENTATION_BYTES
        or identity["sha256"] != VERIFIER_IMPLEMENTATION_SHA256
    ):
        raise RuntimeError(
            "canonical verifier implementation differs from its exact pin"
        )
    name = "_omega_decision_v3_canonical_verifier"
    if name in sys.modules:
        raise RuntimeError("refusing a preloaded canonical verifier module")
    module = types.ModuleType(name)
    module.__file__ = identity["path"]
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    sys.modules[name] = module
    try:
        exec(
            compile(payload, str(path), "exec", dont_inherit=True),
            module.__dict__,
        )
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if _snapshot(path)[0] != identity:
        sys.modules.pop(name, None)
        raise ValueError("canonical verifier implementation changed while loaded")
    module.__dict__["VERIFIER_RUNNER_OVERRIDE"] = runner
    if _snapshot(runner)[0] != runner_identity:
        sys.modules.pop(name, None)
        raise ValueError("canonical verifier runner changed while loaded")
    return module, identity


def main(argv: Sequence[str] | None = None) -> int:
    runner = _runner_path()
    runner_identity, _ = _snapshot(runner)
    module, identity = _load_verifier()
    result = int(module.main(sys.argv[1:] if argv is None else argv))
    if _snapshot(Path(str(identity["path"])))[0] != identity:
        raise ValueError("canonical verifier implementation changed during execution")
    if _snapshot(runner)[0] != runner_identity:
        raise ValueError("canonical verifier runner changed during execution")
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


__all__ = [
    "VERIFIER_IMPLEMENTATION",
    "VERIFIER_IMPLEMENTATION_BYTES",
    "VERIFIER_IMPLEMENTATION_SHA256",
    "main",
]
