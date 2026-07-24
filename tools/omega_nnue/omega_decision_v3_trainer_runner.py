#!/usr/bin/env python3
"""Exact, result-blind numeric trainer for Omega decision corpus v3.

The Generation-6 authority launches this file with ``python -I -B`` and gives
it only the train projection, its static-HCE companion, and a frozen
architecture-4 initializer.  This runner deliberately has no validation or
held-out interface.  Its three outputs are created with O_EXCL; the actual
batch-order transcript is appended only after the corresponding Adam update
has completed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import types
from typing import Any, BinaryIO, Mapping, Sequence

import numpy as np


SCHEMA_VERSION = 1
PROFILE_ID = "king-state-v6-move-decision-v1"
LABEL_KIND = "omega-nnue-king-state-v6-decision-label"
HCE_KIND = "omega-nnue-king-state-v6-static-hce"
BATCH_KIND = "omega-nnue-king-state-v6-actual-batch-order"
HISTORY_KIND = "omega-nnue-king-state-v6-training-history"
MODE = "--train-generation6"

CANDIDATE_RECIPES: Mapping[str, Mapping[str, float]] = {
    "G6A": {"listwiseSoftmaxWeight": 0.15, "nearEqualTopSetWeight": 0.15},
    "G6B": {"listwiseSoftmaxWeight": 0.30, "nearEqualTopSetWeight": 0.30},
    "G6C": {"listwiseSoftmaxWeight": 0.45, "nearEqualTopSetWeight": 0.45},
}
OPTIMIZER_PROTOCOL: Mapping[str, Any] = {
    "optimizer": "Adam",
    "beta1": 0.9,
    "beta2": 0.999,
    "epsilon": 1e-8,
    "weightDecay": 0.0,
    "parameterLearningRateScale": 1.0,
    "epochs": 48,
    "qatEpochs": 12,
    "firstQatEpoch": 37,
    "rootsPerBatch": 64,
    "learningRate": 0.003,
    "qatLearningRateScale": 0.1,
    "qatLearningRateScaleScope": "global learning rate only",
    "checkpointRule": (
        "final epoch only; deployment health and frozen validation are external"
    ),
    "completeRootDivisibilityRequired": True,
    "batchOrderDerivation": (
        "per epoch SHA-256(seed|epoch|index), first 8 bytes little-endian, "
        "NumPy PCG64 permutation of ascending rootId"
    ),
}

LABEL_FIELDS = frozenset(
    {
        "schemaVersion",
        "kind",
        "rootId",
        "leakageComponentId",
        "split",
        "childId",
        "childOfen",
        "phase",
        "parentSideToMove",
        "deepRank",
        "deepRegretCp",
        "deepScoreCpRoot",
        "deepScoreCpChildStm",
    }
)
HCE_FIELDS = frozenset(
    {"schemaVersion", "kind", "profileId", "childId", "handcraftedCpChildStm"}
)
PHASES = frozenset(("opening", "middlegame", "late", "endgame"))
SIDES = frozenset(("w", "b"))
EXPECTED_TRAIN_ROOTS = 4096
CHILDREN_PER_ROOT = 4
SCORE_LIMIT_CP = 1_000_000
REGRET_LIMIT_CP = SCORE_LIMIT_CP * 2
RESIDUAL_TARGET_CLIP_CP = 2000.0
HUBER_NORMALIZER_CP = 100.0
HUBER_DELTA_NORMALIZED = 2.0
LISTWISE_TEMPERATURE_CP = 200.0
TOP_SET_MAX_REGRET_CP = 25

OMEGA_NNUE_RELATIVE = Path("tools/omega_nnue/omega_nnue.py")
OMEGA_NNUE_BYTES = 53_900
OMEGA_NNUE_SHA256 = "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa"
UINT64 = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _same_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and getattr(left, "st_mtime_ns", None)
        == getattr(right, "st_mtime_ns", None)
    )


def _absolute(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError(f"formal trainer path is not absolute: {path}")
    return Path(os.path.abspath(os.fspath(path)))


def _check_chain(path: Path, *, leaf_may_be_absent: bool) -> None:
    absolute = _absolute(path)
    chain = (*reversed(absolute.parents), absolute)
    for index, item in enumerate(chain):
        if leaf_may_be_absent and index == len(chain) - 1 and not os.path.lexists(item):
            continue
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"reparse path is forbidden: {item}")


def _snapshot(path: Path) -> tuple[dict[str, Any], bytes, tuple[int, int, int, int | None]]:
    safe = _absolute(path)
    _check_chain(safe, leaf_may_be_absent=False)
    before = os.lstat(safe)
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(before)
        or getattr(before, "st_nlink", 1) != 1
    ):
        raise ValueError(f"unsafe trainer input: {safe}")
    descriptor = os.open(
        safe,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    payload = bytearray()
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not _same_snapshot(before, opened):
            raise ValueError(f"trainer input changed before descriptor open: {safe}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            payload.extend(block)
            digest.update(block)
        after = os.fstat(descriptor)
        if not _same_snapshot(opened, after):
            raise ValueError(f"trainer input changed while read: {safe}")
    finally:
        os.close(descriptor)
    _check_chain(safe, leaf_may_be_absent=False)
    final = os.lstat(safe)
    if not _same_snapshot(before, final):
        raise ValueError(f"trainer input path changed during snapshot: {safe}")
    identity = {"path": str(safe), "bytes": len(payload), "sha256": digest.hexdigest()}
    private = (final.st_dev, final.st_ino, final.st_size, getattr(final, "st_mtime_ns", None))
    return identity, bytes(payload), private


def _recheck(path: Path, identity: Mapping[str, Any], private: tuple[int, int, int, int | None]) -> None:
    current, _, current_private = _snapshot(path)
    if current != dict(identity) or current_private != private:
        raise ValueError(f"trainer dependency changed during execution: {path}")


def _safe_output(path: Path) -> Path:
    safe = _absolute(path)
    _check_chain(safe.parent, leaf_may_be_absent=False)
    parent = os.lstat(safe.parent)
    if not stat.S_ISDIR(parent.st_mode):
        raise ValueError(f"trainer output parent is not a directory: {safe.parent}")
    if os.path.lexists(safe):
        raise FileExistsError(safe)
    return safe


def _open_exclusive(path: Path) -> BinaryIO:
    safe = _safe_output(path)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(safe, flags, 0o600)
    return os.fdopen(descriptor, "wb", buffering=0)


def _write_all(stream: BinaryIO, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        count = stream.write(remaining)
        if count is None or count <= 0:
            raise IOError("trainer publication made no forward progress")
        remaining = remaining[count:]


def _write_exclusive(path: Path, payload: bytes) -> dict[str, Any]:
    safe = _safe_output(path)
    with _open_exclusive(safe) as stream:
        _write_all(stream, payload)
        stream.flush()
        os.fsync(stream.fileno())
    identity, actual, _ = _snapshot(safe)
    if actual != payload:
        raise IOError(f"exclusive trainer publication changed: {safe}")
    return identity


def _strict_loads(payload: str, *, location: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{location}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"{location}: nonfinite JSON number {value}")))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"{location}: invalid JSON: {error}") from error
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is float and not math.isfinite(item):
            raise ValueError(f"{location}: nonfinite JSON number")
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return value


def _type_exact_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _type_exact_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _type_exact_equal(a, b) for a, b in zip(left, right)
        )
    return bool(left == right)


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _exact_int(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or value < low or value > high:
        raise ValueError(f"{label} is not an exact integer in {low}..{high}")
    return value


def _decode_lines(payload: bytes, *, location: str) -> list[str]:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{location}: UTF-8 BOM is forbidden")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{location}: invalid UTF-8: {error}") from error
    if not text or not text.endswith("\n"):
        raise ValueError(f"{location}: canonical JSONL must be nonempty and newline-terminated")
    lines = text.splitlines()
    if not lines or any(not line for line in lines):
        raise ValueError(f"{location}: blank JSONL rows are forbidden")
    return lines


def _load_pinned_omega_nnue() -> tuple[types.ModuleType, dict[str, Any], tuple[int, int, int, int | None]]:
    source = _repository() / OMEGA_NNUE_RELATIVE
    identity, payload, private = _snapshot(source)
    if identity["bytes"] != OMEGA_NNUE_BYTES or identity["sha256"] != OMEGA_NNUE_SHA256:
        raise ValueError("omega_nnue.py differs from the exact trainer dependency pin")
    name = "_omega_decision_v3_trainer_omega_nnue"
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
    return module, identity, private


@dataclass(frozen=True)
class Child:
    root_id: str
    component_id: str
    child_id: str
    ofen: str
    phase: str
    parent_side: str
    rank: int
    regret_cp: int
    root_score_cp: int
    child_score_cp: int
    hce_cp: int
    residual_target_cp: float
    label_document: Mapping[str, Any]


@dataclass(frozen=True)
class TrainingData:
    roots: tuple[str, ...]
    children_by_root: Mapping[str, tuple[Child, ...]]
    stm_features: np.ndarray
    opponent_features: np.ndarray
    child_index: Mapping[str, int]
    pad_feature: int


def _parse_hce(payload: bytes) -> tuple[dict[str, int], dict[str, Mapping[str, Any]]]:
    scores: dict[str, int] = {}
    documents: dict[str, Mapping[str, Any]] = {}
    for line_number, line in enumerate(_decode_lines(payload, location="train HCE"), 1):
        value = _strict_loads(line, location=f"train HCE:{line_number}")
        if not isinstance(value, dict) or set(value) != HCE_FIELDS:
            raise ValueError(f"train HCE:{line_number}: exact row schema changed")
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != SCHEMA_VERSION
            or value["kind"] != HCE_KIND
            or value["profileId"] != PROFILE_ID
            or type(value["childId"]) is not str
            or not value["childId"]
        ):
            raise ValueError(f"train HCE:{line_number}: row authority changed")
        child_id = value["childId"]
        if child_id in scores:
            raise ValueError(f"train HCE:{line_number}: duplicate childId")
        scores[child_id] = _exact_int(
            value["handcraftedCpChildStm"],
            f"train HCE:{line_number} handcraftedCpChildStm",
            -SCORE_LIMIT_CP,
            SCORE_LIMIT_CP,
        )
        documents[child_id] = value
    return scores, documents


def _parse_training_data(
    label_payload: bytes,
    hce_payload: bytes,
    omega: types.ModuleType,
    *,
    expected_roots: int = EXPECTED_TRAIN_ROOTS,
) -> tuple[TrainingData, Mapping[str, Mapping[str, Any]]]:
    hce, hce_documents = _parse_hce(hce_payload)
    raw_by_root: dict[str, list[Child]] = {}
    child_ids: set[str] = set()
    for line_number, line in enumerate(_decode_lines(label_payload, location="train labels"), 1):
        value = _strict_loads(line, location=f"train labels:{line_number}")
        if not isinstance(value, dict) or set(value) != LABEL_FIELDS:
            raise ValueError(f"train labels:{line_number}: exact row schema changed")
        if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION or value["kind"] != LABEL_KIND:
            raise ValueError(f"train labels:{line_number}: label authority changed")
        string_fields = (
            "rootId",
            "leakageComponentId",
            "split",
            "childId",
            "childOfen",
            "phase",
            "parentSideToMove",
        )
        if any(type(value[field]) is not str or not value[field] for field in string_fields):
            raise ValueError(f"train labels:{line_number}: string field changed")
        if value["split"] != "train":
            raise ValueError("trainer refuses validation or held-out target rows")
        if value["phase"] not in PHASES or value["parentSideToMove"] not in SIDES:
            raise ValueError(f"train labels:{line_number}: phase/side changed")
        child_id = value["childId"]
        if child_id in child_ids:
            raise ValueError(f"train labels:{line_number}: duplicate childId")
        child_ids.add(child_id)
        if child_id not in hce:
            raise ValueError(f"train labels:{line_number}: missing HCE cross-link")
        ofen = " ".join(value["childOfen"].split())
        if ofen != value["childOfen"]:
            raise ValueError(f"train labels:{line_number}: child OFEN is not normalized")
        tokens = ofen.split(" ")
        if len(tokens) != 6 or tokens[1] not in SIDES or tokens[1] == value["parentSideToMove"]:
            raise ValueError(f"train labels:{line_number}: child side is not opposite parent")
        rank = _exact_int(value["deepRank"], f"train labels:{line_number} deepRank", 1, 4)
        regret = _exact_int(value["deepRegretCp"], f"train labels:{line_number} deepRegretCp", 0, REGRET_LIMIT_CP)
        root_score = _exact_int(value["deepScoreCpRoot"], f"train labels:{line_number} deepScoreCpRoot", -SCORE_LIMIT_CP, SCORE_LIMIT_CP)
        child_score = _exact_int(value["deepScoreCpChildStm"], f"train labels:{line_number} deepScoreCpChildStm", -SCORE_LIMIT_CP, SCORE_LIMIT_CP)
        if root_score != -child_score:
            raise ValueError(f"train labels:{line_number}: root/child score sign changed")
        child = Child(
            root_id=value["rootId"],
            component_id=value["leakageComponentId"],
            child_id=child_id,
            ofen=ofen,
            phase=value["phase"],
            parent_side=value["parentSideToMove"],
            rank=rank,
            regret_cp=regret,
            root_score_cp=root_score,
            child_score_cp=child_score,
            hce_cp=hce[child_id],
            residual_target_cp=float(np.clip(child_score - hce[child_id], -RESIDUAL_TARGET_CLIP_CP, RESIDUAL_TARGET_CLIP_CP)),
            label_document=value,
        )
        raw_by_root.setdefault(child.root_id, []).append(child)
    if set(hce) != child_ids:
        raise ValueError("train label/HCE child inventories differ")
    if len(raw_by_root) != expected_roots:
        raise ValueError(f"train root inventory is {len(raw_by_root)}, expected {expected_roots}")
    children_by_root: dict[str, tuple[Child, ...]] = {}
    ordered_children: list[Child] = []
    for root_id in sorted(raw_by_root):
        siblings = tuple(sorted(raw_by_root[root_id], key=lambda item: item.child_id))
        if len(siblings) != CHILDREN_PER_ROOT or len({item.child_id for item in siblings}) != CHILDREN_PER_ROOT:
            raise ValueError(f"root {root_id} is not an exact four-child decision")
        first = siblings[0]
        if any(
            item.root_id != root_id
            or item.component_id != first.component_id
            or item.phase != first.phase
            or item.parent_side != first.parent_side
            for item in siblings
        ):
            raise ValueError(f"root {root_id} sibling routing changed")
        ranked = sorted(siblings, key=lambda item: item.rank)
        if [item.rank for item in ranked] != [1, 2, 3, 4] or ranked[0].regret_cp != 0:
            raise ValueError(f"root {root_id} rank authority changed")
        if any(
            left.regret_cp > right.regret_cp
            for left, right in zip(ranked, ranked[1:])
        ):
            raise ValueError(f"root {root_id} rank/regret authority changed")
        best = ranked[0].root_score_cp
        if any(best - item.root_score_cp != item.regret_cp for item in siblings):
            raise ValueError(f"root {root_id} regret/score coherence changed")
        children_by_root[root_id] = siblings
        ordered_children.extend(siblings)
    if expected_roots == EXPECTED_TRAIN_ROOTS:
        cell_counts: dict[tuple[str, str], int] = {}
        for siblings in children_by_root.values():
            key = (siblings[0].phase, siblings[0].parent_side)
            cell_counts[key] = cell_counts.get(key, 0) + 1
        expected_cells = {(phase, side) for phase in PHASES for side in SIDES}
        if set(cell_counts) != expected_cells or any(
            count != EXPECTED_TRAIN_ROOTS // len(expected_cells)
            for count in cell_counts.values()
        ):
            raise ValueError("train phase-by-root-side quota authority changed")

    architecture = omega.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL
    pad = int(omega.feature_count_for_architecture(architecture))
    stm_rows: list[tuple[int, ...]] = []
    opponent_rows: list[tuple[int, ...]] = []
    for child in ordered_children:
        white = omega.active_features(child.ofen, 0, architecture)
        black = omega.active_features(child.ofen, 1, architecture)
        stm, opponent = (white, black) if child.ofen.split(" ")[1] == "w" else (black, white)
        if any(type(index) is not int or index < 0 or index >= pad for index in (*stm, *opponent)):
            raise ValueError(f"child {child.child_id} produced an invalid feature index")
        stm_rows.append(tuple(stm))
        opponent_rows.append(tuple(opponent))
    width = max(max(map(len, stm_rows)), max(map(len, opponent_rows)))
    stm_features = np.full((len(ordered_children), width), pad, dtype=np.int32)
    opponent_features = np.full((len(ordered_children), width), pad, dtype=np.int32)
    for row, values in enumerate(stm_rows):
        stm_features[row, : len(values)] = values
    for row, values in enumerate(opponent_rows):
        opponent_features[row, : len(values)] = values
    index = {child.child_id: row for row, child in enumerate(ordered_children)}
    return TrainingData(
        roots=tuple(sorted(children_by_root)),
        children_by_root=children_by_root,
        stm_features=stm_features,
        opponent_features=opponent_features,
        child_index=index,
        pad_feature=pad,
    ), hce_documents


def _clip_round(value: np.ndarray, low: int, high: int, dtype: np.dtype[Any]) -> np.ndarray:
    return np.clip(np.rint(value), low, high).astype(dtype)


def _engine_round(value: np.ndarray) -> np.ndarray:
    return np.where(value >= 0.0, np.floor(value + 0.5), -np.floor(-value + 0.5))


@dataclass
class FloatNetwork:
    ft_bias: np.ndarray
    ft_weights: np.ndarray
    dense_bias: np.ndarray
    dense_weights: np.ndarray
    output_bias: np.ndarray
    output_weights: np.ndarray
    omega: types.ModuleType

    @classmethod
    def from_quantized(cls, network: Any, omega: types.ModuleType) -> "FloatNetwork":
        if network.architecture != omega.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL:
            raise ValueError("G6 trainer requires an architecture-4 initializer")
        result = cls(
            ft_bias=network.ft_bias.astype(np.float32),
            ft_weights=network.ft_weights.astype(np.float32),
            dense_bias=(network.dense_bias.astype(np.float32) / omega.HIDDEN_DIVISOR),
            dense_weights=(network.dense_weights.astype(np.float32) / omega.HIDDEN_DIVISOR),
            output_bias=np.asarray([network.output_bias / omega.OUTPUT_DIVISOR], dtype=np.float32),
            output_weights=(network.output_weights.astype(np.float32) / omega.OUTPUT_DIVISOR),
            omega=omega,
        )
        if result.quantize().to_bytes() != network.to_bytes():
            raise ValueError("initializer is not losslessly representable by float32 shadow parameters")
        return result

    def parameters(self) -> dict[str, np.ndarray]:
        return {
            "ft_bias": self.ft_bias,
            "ft_weights": self.ft_weights,
            "dense_bias": self.dense_bias,
            "dense_weights": self.dense_weights,
            "output_bias": self.output_bias,
            "output_weights": self.output_weights,
        }

    def quantize(self) -> Any:
        omega = self.omega
        return omega.QuantizedNetwork(
            ft_bias=_clip_round(self.ft_bias, -(1 << 15), (1 << 15) - 1, np.int16),
            ft_weights=_clip_round(self.ft_weights, -(1 << 15), (1 << 15) - 1, np.int16),
            dense_bias=_clip_round(self.dense_bias * omega.HIDDEN_DIVISOR, -(1 << 31), (1 << 31) - 1, np.int32),
            dense_weights=_clip_round(self.dense_weights * omega.HIDDEN_DIVISOR, -128, 127, np.int8),
            output_bias=int(_clip_round(self.output_bias * omega.OUTPUT_DIVISOR, -(1 << 31), (1 << 31) - 1, np.int32)[0]),
            output_weights=_clip_round(self.output_weights * omega.OUTPUT_DIVISOR, -128, 127, np.int8),
            architecture=omega.ARCHITECTURE_OMEGA_INTERACTION_RESIDUAL,
        )

    def _effective(self, qat: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, np.ndarray]:
        if not qat:
            return self.ft_bias, self.ft_weights, self.dense_bias, self.dense_weights, float(self.output_bias[0]), self.output_weights
        quantized = self.quantize()
        return (
            quantized.ft_bias.astype(np.float32),
            quantized.ft_weights.astype(np.float32),
            quantized.dense_bias.astype(np.float32) / self.omega.HIDDEN_DIVISOR,
            quantized.dense_weights.astype(np.float32) / self.omega.HIDDEN_DIVISOR,
            float(quantized.output_bias) / self.omega.OUTPUT_DIVISOR,
            quantized.output_weights.astype(np.float32) / self.omega.OUTPUT_DIVISOR,
        )

    @staticmethod
    def _accumulate(bias: np.ndarray, weights: np.ndarray, encoded: np.ndarray, pad: int) -> np.ndarray:
        result = np.repeat(bias[None, :], encoded.shape[0], axis=0)
        for row, indices in enumerate(encoded):
            real = indices[indices != pad]
            if real.size:
                result[row] += weights[real].sum(axis=0)
        return result

    def forward(self, stm: np.ndarray, opponent: np.ndarray, *, qat: bool, need_cache: bool) -> tuple[np.ndarray, Mapping[str, np.ndarray] | None]:
        ft_bias, ft_weights, dense_bias, dense_weights, output_bias, output_weights = self._effective(qat)
        pad = int(self.omega.OMEGA_INTERACTION_FEATURE_COUNT)
        stm_z = self._accumulate(ft_bias, ft_weights, stm, pad)
        opponent_z = self._accumulate(ft_bias, ft_weights, opponent, pad)
        stm_a = np.clip(stm_z, 0.0, self.omega.ACTIVATION_MAX)
        opponent_a = np.clip(opponent_z, 0.0, self.omega.ACTIVATION_MAX)
        joined = np.concatenate((stm_a, opponent_a), axis=1)
        dense_z = dense_bias + joined @ dense_weights.T
        if qat:
            dense_z = _engine_round(dense_z)
        dense_a = np.clip(dense_z, 0.0, self.omega.ACTIVATION_MAX)
        raw = output_bias + dense_a @ output_weights
        if qat:
            raw = _engine_round(raw)
        prediction = np.clip(raw, -self.omega.OMEGA_INTERACTION_RESIDUAL_LIMIT_CP, self.omega.OMEGA_INTERACTION_RESIDUAL_LIMIT_CP).astype(np.float32)
        if not need_cache:
            return prediction, None
        return prediction, {
            "stm_z": stm_z,
            "opponent_z": opponent_z,
            "joined": joined,
            "dense_z": dense_z,
            "dense_a": dense_a,
            "dense_weights": dense_weights,
            "output_weights": output_weights,
            "raw_prediction": np.asarray(raw, dtype=np.float32),
        }


def _log_softmax(logits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    maximum = float(np.max(logits))
    shifted = logits.astype(np.float64) - maximum
    log_denominator = math.log(float(np.exp(shifted).sum()))
    log_probabilities = shifted - log_denominator
    return log_probabilities, np.exp(log_probabilities)


def _batch_objective(
    predictions: np.ndarray,
    roots: Sequence[str],
    data: TrainingData,
    recipe: Mapping[str, float],
) -> tuple[float, np.ndarray]:
    if predictions.shape != (len(roots) * CHILDREN_PER_ROOT,):
        raise ValueError("batch prediction shape changed")
    gradient = np.zeros_like(predictions, dtype=np.float32)
    total = 0.0
    for root_offset, root_id in enumerate(roots):
        siblings = data.children_by_root[root_id]
        start = root_offset * CHILDREN_PER_ROOT
        prediction = predictions[start : start + CHILDREN_PER_ROOT].astype(np.float64)
        targets = np.asarray([item.residual_target_cp for item in siblings], dtype=np.float64)
        error = (prediction - targets) / HUBER_NORMALIZER_CP
        absolute = np.abs(error)
        quadratic = absolute <= HUBER_DELTA_NORMALIZED
        point_losses = np.where(quadratic, 0.5 * error * error, HUBER_DELTA_NORMALIZED * (absolute - 0.5 * HUBER_DELTA_NORMALIZED))
        point_gradient = np.where(quadratic, error, HUBER_DELTA_NORMALIZED * np.sign(error)) / HUBER_NORMALIZER_CP / CHILDREN_PER_ROOT

        regrets = np.asarray([item.regret_cp for item in siblings], dtype=np.float64)
        _, teacher = _log_softmax(-regrets / LISTWISE_TEMPERATURE_CP)
        child_totals = np.asarray([item.hce_cp for item in siblings], dtype=np.float64) + prediction
        student_logits = -child_totals / LISTWISE_TEMPERATURE_CP
        student_log, student = _log_softmax(student_logits)
        list_loss = float(-np.sum(teacher * student_log))
        list_gradient = (teacher - student) / LISTWISE_TEMPERATURE_CP

        top_mask = regrets <= TOP_SET_MAX_REGRET_CP
        top_maximum = float(np.max(student_logits[top_mask]))
        top_log_sum = top_maximum + math.log(float(np.exp(student_logits[top_mask] - top_maximum).sum()))
        all_maximum = float(np.max(student_logits))
        all_log_sum = all_maximum + math.log(float(np.exp(student_logits - all_maximum).sum()))
        top_loss = all_log_sum - top_log_sum
        conditional_top = np.zeros(CHILDREN_PER_ROOT, dtype=np.float64)
        conditional_top[top_mask] = np.exp(student_logits[top_mask] - top_log_sum)
        top_gradient = (conditional_top - student) / LISTWISE_TEMPERATURE_CP

        root_gradient = point_gradient + recipe["listwiseSoftmaxWeight"] * list_gradient + recipe["nearEqualTopSetWeight"] * top_gradient
        gradient[start : start + CHILDREN_PER_ROOT] = root_gradient.astype(np.float32) / len(roots)
        total += float(point_losses.mean()) + recipe["listwiseSoftmaxWeight"] * list_loss + recipe["nearEqualTopSetWeight"] * top_loss
    loss = total / len(roots)
    if not math.isfinite(loss) or not np.all(np.isfinite(gradient)):
        raise FloatingPointError("G6 decision objective became non-finite")
    return loss, gradient


def _backprop(
    model: FloatNetwork,
    stm: np.ndarray,
    opponent: np.ndarray,
    cache: Mapping[str, np.ndarray],
    output_gradient: np.ndarray,
    pad: int,
) -> dict[str, np.ndarray]:
    omega = model.omega
    raw = cache["raw_prediction"]
    unclamped = (raw >= -omega.OMEGA_INTERACTION_RESIDUAL_LIMIT_CP) & (raw <= omega.OMEGA_INTERACTION_RESIDUAL_LIMIT_CP)
    output_gradient = output_gradient * unclamped
    output_bias_gradient = np.asarray([output_gradient.sum()], dtype=np.float32)
    output_weights_gradient = cache["dense_a"].T @ output_gradient
    dense_activation_gradient = output_gradient[:, None] * cache["output_weights"][None, :]
    dense_mask = (cache["dense_z"] > 0.0) & (cache["dense_z"] < omega.ACTIVATION_MAX)
    dense_z_gradient = dense_activation_gradient * dense_mask
    dense_bias_gradient = dense_z_gradient.sum(axis=0)
    dense_weights_gradient = dense_z_gradient.T @ cache["joined"]
    joined_gradient = dense_z_gradient @ cache["dense_weights"]
    stm_gradient = joined_gradient[:, : omega.ACCUMULATOR_SIZE]
    opponent_gradient = joined_gradient[:, omega.ACCUMULATOR_SIZE :]
    stm_gradient *= (cache["stm_z"] > 0.0) & (cache["stm_z"] < omega.ACTIVATION_MAX)
    opponent_gradient *= (cache["opponent_z"] > 0.0) & (cache["opponent_z"] < omega.ACTIVATION_MAX)
    ft_bias_gradient = (stm_gradient + opponent_gradient).sum(axis=0)
    ft_weights_gradient = np.zeros_like(model.ft_weights, dtype=np.float32)
    for encoded, row_gradient in zip(stm, stm_gradient):
        real = encoded[encoded != pad]
        np.add.at(ft_weights_gradient, real, row_gradient)
    for encoded, row_gradient in zip(opponent, opponent_gradient):
        real = encoded[encoded != pad]
        np.add.at(ft_weights_gradient, real, row_gradient)
    gradients = {
        "ft_bias": ft_bias_gradient.astype(np.float32),
        "ft_weights": ft_weights_gradient,
        "dense_bias": dense_bias_gradient.astype(np.float32),
        "dense_weights": dense_weights_gradient.astype(np.float32),
        "output_bias": output_bias_gradient,
        "output_weights": output_weights_gradient.astype(np.float32),
    }
    if any(not np.all(np.isfinite(value)) for value in gradients.values()):
        raise FloatingPointError("G6 trainer produced a non-finite gradient")
    return gradients


class Adam:
    def __init__(self, parameters: Mapping[str, np.ndarray]) -> None:
        self.parameters = dict(parameters)
        self.first = {name: np.zeros_like(value, dtype=np.float32) for name, value in self.parameters.items()}
        self.second = {name: np.zeros_like(value, dtype=np.float32) for name, value in self.parameters.items()}
        self.steps = 0

    def step(self, gradients: Mapping[str, np.ndarray], learning_rate: float, omega: types.ModuleType) -> None:
        if set(gradients) != set(self.parameters):
            raise ValueError("optimizer gradient inventory changed")
        self.steps += 1
        beta1 = float(OPTIMIZER_PROTOCOL["beta1"])
        beta2 = float(OPTIMIZER_PROTOCOL["beta2"])
        epsilon = float(OPTIMIZER_PROTOCOL["epsilon"])
        correction1 = 1.0 - beta1**self.steps
        correction2 = 1.0 - beta2**self.steps
        for name, parameter in self.parameters.items():
            gradient = np.asarray(gradients[name], dtype=np.float32)
            first = self.first[name]
            second = self.second[name]
            first *= beta1
            first += (1.0 - beta1) * gradient
            second *= beta2
            second += (1.0 - beta2) * gradient * gradient
            parameter -= learning_rate * (first / correction1) / (np.sqrt(second / correction2) + epsilon)
        np.clip(self.parameters["ft_bias"], -(1 << 15), (1 << 15) - 1, out=self.parameters["ft_bias"])
        np.clip(self.parameters["ft_weights"], -(1 << 15), (1 << 15) - 1, out=self.parameters["ft_weights"])
        np.clip(self.parameters["dense_weights"], -128.0 / omega.HIDDEN_DIVISOR, 127.0 / omega.HIDDEN_DIVISOR, out=self.parameters["dense_weights"])
        np.clip(self.parameters["output_weights"], -128.0 / omega.OUTPUT_DIVISOR, 127.0 / omega.OUTPUT_DIVISOR, out=self.parameters["output_weights"])
        if any(not np.all(np.isfinite(value)) for value in self.parameters.values()):
            raise FloatingPointError("G6 trainer produced a non-finite parameter")


def _batch_content_sha(roots: Sequence[str], data: TrainingData, hce_documents: Mapping[str, Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for root_id in roots:
        for child in data.children_by_root[root_id]:
            digest.update(_canonical_json(child.label_document))
    for root_id in roots:
        for child in data.children_by_root[root_id]:
            digest.update(_canonical_json(hce_documents[child.child_id]))
    return digest.hexdigest()


def _identity_from_payload(path: Path, payload: bytes) -> dict[str, Any]:
    return {"path": str(_absolute(path)), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _train(
    *,
    model_id: str,
    candidate_id: str,
    purpose: str,
    seed: int,
    recipe: Mapping[str, float],
    data: TrainingData,
    hce_documents: Mapping[str, Mapping[str, Any]],
    model: FloatNetwork,
    transcript_path: Path,
) -> tuple[bytes, bytes, list[dict[str, Any]]]:
    roots_per_batch = int(OPTIMIZER_PROTOCOL["rootsPerBatch"])
    if len(data.roots) % roots_per_batch:
        raise ValueError("train roots are not complete-batch divisible")
    optimizer = Adam(model.parameters())
    transcript_digest = hashlib.sha256()
    transcript_bytes = 0
    epoch_rows: list[dict[str, Any]] = []
    with _open_exclusive(transcript_path) as transcript:
        for epoch in range(1, int(OPTIMIZER_PROTOCOL["epochs"]) + 1):
            epoch_digest = hashlib.sha256(f"{seed}|epoch|{epoch}".encode("utf-8")).digest()
            epoch_seed = int.from_bytes(epoch_digest[:8], "little", signed=False)
            permutation = np.random.default_rng(epoch_seed).permutation(len(data.roots))
            ordered = [data.roots[int(index)] for index in permutation]
            qat = epoch >= int(OPTIMIZER_PROTOCOL["firstQatEpoch"])
            learning_rate = float(OPTIMIZER_PROTOCOL["learningRate"]) * (float(OPTIMIZER_PROTOCOL["qatLearningRateScale"]) if qat else 1.0)
            for batch_index, start in enumerate(range(0, len(ordered), roots_per_batch), 1):
                batch_roots = ordered[start : start + roots_per_batch]
                batch_children = [child for root_id in batch_roots for child in data.children_by_root[root_id]]
                indices = np.asarray([data.child_index[child.child_id] for child in batch_children], dtype=np.int64)
                stm = data.stm_features[indices]
                opponent = data.opponent_features[indices]
                prediction, cache = model.forward(stm, opponent, qat=qat, need_cache=True)
                assert cache is not None
                loss, output_gradient = _batch_objective(prediction, batch_roots, data, recipe)
                if not math.isfinite(loss):
                    raise FloatingPointError("G6 batch loss became non-finite")
                gradients = _backprop(model, stm, opponent, cache, output_gradient, data.pad_feature)
                optimizer.step(gradients, learning_rate, model.omega)
                child_ids = [child.child_id for child in batch_children]
                row = {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": BATCH_KIND,
                    "profileId": PROFILE_ID,
                    "modelId": model_id,
                    "candidateId": candidate_id,
                    "purpose": purpose,
                    "seed": seed,
                    "recipe": dict(recipe),
                    "epoch": epoch,
                    "batchIndex": batch_index,
                    "optimizerStep": optimizer.steps,
                    "qatEnabled": qat,
                    "rootIds": list(batch_roots),
                    "childIds": child_ids,
                    "batchContentSha256": _batch_content_sha(batch_roots, data, hce_documents),
                }
                encoded = _canonical_json(row)
                _write_all(transcript, encoded)
                transcript_digest.update(encoded)
                transcript_bytes += len(encoded)
            epoch_rows.append(
                {
                    "epoch": epoch,
                    "qatEnabled": qat,
                    "learningRate": learning_rate,
                    "optimizerSteps": optimizer.steps,
                    "rootsSeen": len(data.roots),
                    "completeRootBatches": len(data.roots) // roots_per_batch,
                    "validationRootsDecoded": 0,
                    "heldOutRootsDecoded": 0,
                    "heldOutTargetFieldsDecoded": 0,
                }
            )
        transcript.flush()
        os.fsync(transcript.fileno())
    transcript_identity, transcript_payload, _ = _snapshot(transcript_path)
    if transcript_identity["bytes"] != transcript_bytes or transcript_identity["sha256"] != transcript_digest.hexdigest():
        raise IOError("actual batch-order transcript changed during publication")
    network_bytes = model.quantize().to_bytes()
    history = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": HISTORY_KIND,
        "profileId": PROFILE_ID,
        "modelId": model_id,
        "candidateId": candidate_id,
        "purpose": purpose,
        "seed": seed,
        "recipe": dict(recipe),
        "optimizerProtocol": dict(OPTIMIZER_PROTOCOL),
        "batchOrderSha256": transcript_digest.hexdigest(),
        "actualBatchOrderTranscript": transcript_identity,
        "epochs": epoch_rows,
        "resultInformationRead": False,
        "validationRootsDecoded": 0,
        "heldOutRootsDecoded": 0,
        "heldOutTargetFieldsDecoded": 0,
    }
    return network_bytes, _canonical_json(history), epoch_rows


def _parse_protocol_argument(text: str, *, location: str) -> Mapping[str, Any]:
    value = _strict_loads(text, location=location)
    if not isinstance(value, dict):
        raise ValueError(f"{location} is not an object")
    return value


def run(argv: Sequence[str]) -> int:
    if len(argv) != 13 or argv[0] != MODE:
        raise ValueError("exact --train-generation6 argument contract required")
    (
        _,
        model_id,
        candidate_id,
        purpose,
        seed_text,
        recipe_text,
        optimizer_text,
        corpus_text,
        hce_text,
        initializer_text,
        transcript_text,
        model_text,
        history_text,
    ) = argv
    if candidate_id not in CANDIDATE_RECIPES:
        raise ValueError("candidate id is not G6A/G6B/G6C")
    expected_model_id = candidate_id if purpose == "primary-training" else f"{candidate_id}-robustness" if purpose == "robustness-training" else None
    if model_id != expected_model_id:
        raise ValueError("model id/purpose/candidate relationship changed")
    if UINT64.fullmatch(seed_text) is None:
        raise ValueError("training seed is not a canonical unsigned 64-bit integer")
    seed = int(seed_text)
    if seed > (1 << 64) - 1:
        raise ValueError("training seed exceeds unsigned 64-bit range")
    recipe = _parse_protocol_argument(recipe_text, location="candidate recipe")
    if not _type_exact_equal(recipe, CANDIDATE_RECIPES[candidate_id]):
        raise ValueError("candidate recipe differs from frozen G6 recipe")
    optimizer_protocol = _parse_protocol_argument(optimizer_text, location="optimizer protocol")
    if not _type_exact_equal(optimizer_protocol, OPTIMIZER_PROTOCOL):
        raise ValueError("optimizer protocol differs from frozen G6 protocol")

    corpus = _absolute(Path(corpus_text))
    hce = _absolute(Path(hce_text))
    initializer = _absolute(Path(initializer_text))
    transcript = _safe_output(Path(transcript_text))
    output_model = _safe_output(Path(model_text))
    history = _safe_output(Path(history_text))
    path_strings = [str(path).casefold() for path in (corpus, hce, initializer, transcript, output_model, history)]
    if len(set(path_strings)) != len(path_strings):
        raise ValueError("trainer input/output paths are not distinct")

    omega, omega_identity, omega_private = _load_pinned_omega_nnue()
    corpus_identity, corpus_payload, corpus_private = _snapshot(corpus)
    hce_identity, hce_payload, hce_private = _snapshot(hce)
    initializer_identity, initializer_payload, initializer_private = _snapshot(initializer)
    quantized = omega.QuantizedNetwork.from_bytes(initializer_payload)
    if quantized.to_bytes() != initializer_payload:
        raise ValueError("initializer did not round-trip exactly")
    float_model = FloatNetwork.from_quantized(quantized, omega)
    data, hce_documents = _parse_training_data(corpus_payload, hce_payload, omega)
    network_payload, history_payload, _ = _train(
        model_id=model_id,
        candidate_id=candidate_id,
        purpose=purpose,
        seed=seed,
        recipe=recipe,
        data=data,
        hce_documents=hce_documents,
        model=float_model,
        transcript_path=transcript,
    )
    _write_exclusive(output_model, network_payload)
    _write_exclusive(history, history_payload)

    _recheck(corpus, corpus_identity, corpus_private)
    _recheck(hce, hce_identity, hce_private)
    _recheck(initializer, initializer_identity, initializer_private)
    _recheck(_repository() / OMEGA_NNUE_RELATIVE, omega_identity, omega_private)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
