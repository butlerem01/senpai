#!/usr/bin/env python3
"""Pinned stream/health adapter for the Generation-6 Omega evaluator.

The Generation-6 authority invokes Python with ``-I -B`` and binds this file
by exact identity.  This adapter, in turn, binds the already-frozen C++
evaluator by path, size, and SHA-256 and never accepts an evaluator path from
its caller.  Stream modes are byte-simple: one normalized six-field OFEN in,
one exact decimal integer out.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import types
from typing import Sequence

import numpy as np

HELPER_RELATIVE = Path(
    "tools/omega_nnue/frozen_runtime/king-state-v5/evaluator/omega_nnue.exe"
)
HELPER_BYTES = 978_944
HELPER_SHA256 = "271a7ef64c41e3ae79068b06ad51c9b4ba387d5ff03c101ece9dbf2a7de1ff3b"
OMEGA_NNUE_BYTES = 53_900
OMEGA_NNUE_SHA256 = "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa"
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
MAX_ABS_CP = 1_000_000
HEALTH_OFENS = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1",
    "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-] w - - 0 1",
    "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-] b - - 0 1",
    "w9/10/10/10/4k5/5K4/10/10/10/9W[-/-/-/-] w - - 12 37",
    "c9/10/10/10/4k5/5K4/10/10/10/9C[-/-/-/-] b - - 3 19",
)


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _helper() -> Path:
    return _repository() / HELPER_RELATIVE


def _snapshot(path: Path) -> tuple[dict[str, object], bytes]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    for item in (*reversed(absolute.parents), absolute):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(
            info, "st_file_attributes", 0
        ) & 0x400:
            raise ValueError(f"reparse path is forbidden: {item}")
    before = os.lstat(absolute)
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or getattr(before, "st_nlink", 1) != 1
        or getattr(before, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError(f"unsafe evaluator artifact: {absolute}")
    descriptor = os.open(
        absolute,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError(f"evaluator artifact changed before open: {absolute}")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
            digest.update(block)
        after_open = os.fstat(descriptor)
        if _stat_key(after_open) != _stat_key(opened):
            raise ValueError(f"evaluator artifact changed while read: {absolute}")
    finally:
        os.close(descriptor)
    after = os.lstat(absolute)
    if _stat_key(after) != _stat_key(before):
        raise ValueError(f"evaluator artifact path changed: {absolute}")
    payload = b"".join(chunks)
    return {
        "path": str(absolute),
        "bytes": len(payload),
        "sha256": digest.hexdigest(),
    }, payload


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int | None]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", None),
    )


def _load_pinned_omega_nnue() -> types.ModuleType:
    """Execute the exact reviewed sibling bytes, not a later pathname lookup."""

    source = Path(__file__).resolve().with_name("omega_nnue.py")
    identity, payload = _snapshot(source)
    if (
        identity["bytes"] != OMEGA_NNUE_BYTES
        or identity["sha256"] != OMEGA_NNUE_SHA256
    ):
        raise ValueError("pinned omega_nnue.py identity changed")
    name = "_omega_decision_v3_pinned_omega_nnue"
    module = types.ModuleType(name)
    module.__file__ = str(source)
    module.__package__ = ""
    sys.modules[name] = module
    try:
        code = compile(payload, str(source), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if _snapshot(source)[0] != identity:
        sys.modules.pop(name, None)
        raise ValueError("pinned omega_nnue.py changed while it was loaded")
    return module


omega_nnue = _load_pinned_omega_nnue()


def _verified_helper() -> tuple[Path, dict[str, object], bytes]:
    helper = _helper()
    identity, payload = _snapshot(helper)
    if identity["bytes"] != HELPER_BYTES or identity["sha256"] != HELPER_SHA256:
        raise ValueError("frozen Omega evaluator identity changed")
    return helper, identity, payload


def _normalized_input() -> tuple[str, ...]:
    rows: list[str] = []
    for ordinal, line in enumerate(sys.stdin, 1):
        text = line.rstrip("\r\n")
        if not text:
            raise ValueError(f"input row {ordinal} is empty")
        fields = text.split()
        if len(fields) != 6 or " ".join(fields) != text:
            raise ValueError(f"input row {ordinal} is not normalized six-field OFEN")
        if fields[1] not in ("w", "b"):
            raise ValueError(f"input row {ordinal} has an invalid side to move")
        rows.append(text)
    if not rows:
        raise ValueError("evaluator stream is empty")
    return tuple(rows)


def _write_private_snapshot(directory: Path, payload: bytes, name: str) -> Path:
    path = directory / name
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    return path


def _run_cpp(
    mode: str,
    ofens: Sequence[str],
    model: Path | None = None,
    *,
    expected_model_identity: dict[str, object] | None = None,
    expected_model_payload: bytes | None = None,
) -> list[int]:
    helper, before, helper_payload = _verified_helper()
    with tempfile.TemporaryDirectory(
        prefix="omega-decision-v3-evaluator-"
    ) as temporary_name:
        temporary = Path(temporary_name)
        private_helper = _write_private_snapshot(
            temporary, helper_payload, "omega_nnue.exe"
        )
        if _snapshot(private_helper)[1] != helper_payload:
            raise ValueError("private evaluator snapshot differs from pinned bytes")
        command = [str(private_helper), mode]
        model_before: dict[str, object] | None = None
        model_payload: bytes | None = None
        private_model: Path | None = None
        if model is not None:
            model_before, model_payload = _snapshot(model)
            if (
                expected_model_identity is not None
                and model_before != expected_model_identity
            ):
                raise ValueError("network changed before evaluator execution")
            if (
                expected_model_payload is not None
                and model_payload != expected_model_payload
            ):
                raise ValueError("network bytes changed before evaluator execution")
            private_model = _write_private_snapshot(
                temporary, model_payload, "network.nnue"
            )
            private_identity, private_payload = _snapshot(private_model)
            if private_payload != model_payload:
                raise ValueError("private network snapshot differs from claimed bytes")
            command.append(str(private_identity["path"]))
        completed = subprocess.run(
            command,
            input="".join(ofen + "\n" for ofen in ofens),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=max(120, len(ofens) // 4),
            cwd=helper.parent,
            env={},
        )
        if _snapshot(helper)[0] != before:
            raise ValueError("frozen Omega evaluator changed during execution")
        if _snapshot(private_helper)[1] != helper_payload:
            raise ValueError("private evaluator snapshot changed during execution")
        if model_before is not None:
            if _snapshot(Path(str(model_before["path"])))[0] != model_before:
                raise ValueError("network changed during evaluator execution")
            if private_model is None or _snapshot(private_model)[1] != model_payload:
                raise ValueError("private network snapshot changed during execution")
    if completed.returncode != 0 or completed.stderr:
        raise ValueError(
            "frozen Omega evaluator failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    lines = completed.stdout.splitlines()
    if len(lines) != len(ofens):
        raise ValueError(
            f"frozen Omega evaluator returned {len(lines)}/{len(ofens)} rows"
        )
    values: list[int] = []
    for ordinal, line in enumerate(lines, 1):
        if INTEGER.fullmatch(line) is None:
            raise ValueError(f"evaluator output row {ordinal} is not an exact integer")
        value = int(line)
        if value < -MAX_ABS_CP or value > MAX_ABS_CP:
            raise ValueError(f"evaluator output row {ordinal} is out of bounds")
        values.append(value)
    return values


def _feature_rows(ofens: Sequence[str], architecture: int) -> tuple[np.ndarray, np.ndarray]:
    white = [omega_nnue.active_features(ofen, 0, architecture) for ofen in ofens]
    black = [omega_nnue.active_features(ofen, 1, architecture) for ofen in ofens]
    width = max(max(map(len, white)), max(map(len, black)))
    pad = omega_nnue.feature_count_for_architecture(architecture)
    white_matrix = np.full((len(ofens), width), pad, dtype=np.int64)
    black_matrix = np.full((len(ofens), width), pad, dtype=np.int64)
    for row, features in enumerate(white):
        white_matrix[row, : len(features)] = features
    for row, features in enumerate(black):
        black_matrix[row, : len(features)] = features
    stm_white = np.asarray([ofen.split()[1] == "w" for ofen in ofens])[:, None]
    return (
        np.where(stm_white, white_matrix, black_matrix),
        np.where(stm_white, black_matrix, white_matrix),
    )


def _health(model: Path) -> dict[str, object]:
    identity, payload = _snapshot(model)
    network = omega_nnue.QuantizedNetwork.from_bytes(payload)
    round_trip = network.to_bytes() == payload
    expected_bytes = (
        network.architecture
        == omega_nnue.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
        and len(payload)
        == omega_nnue.HEADER_BYTES + omega_nnue.OMEGA_INTERACTION_PAYLOAD_BYTES
    )
    stm, opponent = _feature_rows(HEALTH_OFENS, network.architecture)
    python_values = network.predict_features(stm, opponent)
    cpp_values = np.asarray(
        _run_cpp(
            "--evaluate-network-stream",
            HEALTH_OFENS,
            model,
            expected_model_identity=identity,
            expected_model_payload=payload,
        ),
        dtype=np.int32,
    )
    if _snapshot(Path(str(identity["path"])))[0] != identity:
        raise ValueError("network changed before health publication")
    finite = bool(np.all(np.isfinite(python_values.astype(np.float64))))
    return {
        "modelSha256": identity["sha256"],
        "modelBytes": identity["bytes"],
        "finitePredictions": finite,
        "quantizationRoundTripExact": round_trip,
        "expectedNetworkBytes": expected_bytes,
        "runtimeParity": bool(np.array_equal(python_values, cpp_values)),
        "maximumAbsResidualCp": int(np.max(np.abs(python_values.astype(np.int64)))),
    }


def _self_test() -> None:
    helper, identity, _ = _verified_helper()
    values = _run_cpp("--evaluate-handcrafted-stream", HEALTH_OFENS[:3])
    if len(values) != 3 or identity["path"] != str(helper):
        raise AssertionError("pinned evaluator self-test changed")
    print("omega-decision-v3-evaluator-runner self-test: PASS")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--self-test"]:
        _self_test()
        return 0
    if arguments == ["--evaluate-handcrafted-stream"]:
        values = _run_cpp(arguments[0], _normalized_input())
        sys.stdout.write("".join(f"{value}\n" for value in values))
        return 0
    if len(arguments) == 2 and arguments[0] == "--evaluate-network-stream":
        values = _run_cpp(arguments[0], _normalized_input(), Path(arguments[1]))
        sys.stdout.write("".join(f"{value}\n" for value in values))
        return 0
    if len(arguments) == 2 and arguments[0] == "--validate-network-health":
        print(
            json.dumps(
                _health(Path(arguments[1])),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        return 0
    raise ValueError("unknown or malformed evaluator-runner invocation")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
