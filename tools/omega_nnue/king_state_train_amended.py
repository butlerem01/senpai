#!/usr/bin/env python3
"""Run king-state-v1 with the target-blind cross-phase cluster amendment.

The pre-label-sealed ``king_state_train.py`` remains byte-for-byte unchanged.
This wrapper verifies amendment 001, preserves the original groupId split
routing, removes only the contradictory phase-purity assertion, and replaces
the held-out bootstrap with a global leakage-component cluster bootstrap.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence
import math
import sys

import numpy as np

import king_state_train as sealed


AMENDMENT_KIND = "omega-nnue-king-state-v1-protocol-amendment"
AMENDMENT_ID = "king-state-v1-cross-phase-cluster-bootstrap"
AMENDMENT_PATH = (
    sealed._repo_root()
    / "validation"
    / "omega-nnue-king-state-v1-amendment-001.json"
)
PINNED_FILES = {
    "protocol": (
        sealed._repo_root()
        / "validation"
        / "omega-nnue-king-state-v1-protocol.json"
    ),
    "prelabelSeal": (
        sealed._repo_root()
        / "build-msvc"
        / "data-generation"
        / "deep-hce-v2"
        / "king-state-v1-prelabel.seal.json"
    ),
    "residualCorpus": (
        sealed._repo_root()
        / "build-msvc"
        / "data-generation"
        / "deep-hce-v2"
        / "deep-hce-v2-residual.jsonl"
    ),
    "residualManifest": (
        sealed._repo_root()
        / "build-msvc"
        / "data-generation"
        / "deep-hce-v2"
        / "deep-hce-v2-residual.jsonl.manifest.json"
    ),
}


def _verify_amendment() -> dict[str, Any]:
    amendment = sealed._load_json(AMENDMENT_PATH, "protocol amendment")
    sealed._expect(amendment.get("schemaVersion"), 1, "amendment schema")
    sealed._expect(amendment.get("kind"), AMENDMENT_KIND, "amendment kind")
    sealed._expect(
        amendment.get("amendmentId"), AMENDMENT_ID, "amendment id"
    )
    pins = sealed._mapping(amendment.get("pinnedInputs"), "amendment pins")
    sealed._expect(set(pins), set(PINNED_FILES), "amendment pinned inputs")
    for name, path in PINNED_FILES.items():
        expected = sealed._mapping(pins.get(name), f"amendment pin {name}")
        actual = sealed._identity(path)
        sealed._expect(
            int(expected.get("bytes", -1)),
            int(actual["bytes"]),
            f"amendment {name} bytes",
        )
        sealed._expect(
            str(expected.get("sha256", "")).lower(),
            str(actual["sha256"]).lower(),
            f"amendment {name} sha256",
        )
    contracts = sealed._mapping(
        amendment.get("amendedContracts"), "amended contracts"
    )
    sealed._expect(
        contracts.get("groupPhasePurityRequired"),
        False,
        "amended phase purity",
    )
    sealed._expect(
        contracts.get("bootstrapUnit"),
        "global leakage component groupId",
        "amended bootstrap unit",
    )
    sealed._expect(
        contracts.get("bootstrapStratification"),
        "phase-incidence set",
        "amended bootstrap stratification",
    )
    sealed._expect(
        contracts.get("minimumGroupsPerObservedStratum"),
        2,
        "minimum groups per incidence stratum",
    )
    sealed._expect(contracts.get("iterations"), 10000, "bootstrap iterations")
    sealed._expect(contracts.get("seed"), 20260727, "bootstrap seed")
    return amendment


def _feature_corpus(
    path: Path,
    *,
    split_seed: int,
    train_percent: float,
    validation_percent: float,
) -> sealed.FeatureCorpus:
    """Load features without decoding targets and allow cross-phase groups."""

    ofens: list[str] = []
    groups: list[str] = []
    phases: list[str] = []
    splits: list[int] = []
    signatures: list[str] = []
    white_rows: list[tuple[int, ...]] = []
    black_rows: list[tuple[int, ...]] = []
    stm_rows: list[bool] = []
    resolved = path.resolve(strict=True)
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{resolved}:{line_number}"
            group = sealed.trainer._selective_top_level_string(
                line, "groupId", location=location
            )
            ofen = " ".join(
                sealed.trainer._selective_top_level_string(
                    line, "ofen", location=location
                ).split()
            )
            phase = sealed.trainer._selective_top_level_string(
                line, "phase", location=location
            )
            if phase not in sealed.PHASES:
                raise ValueError(f"{location}: invalid phase {phase!r}")
            try:
                white = sealed.active_features(
                    ofen, 0, sealed.ARCHITECTURE_KING_STATE_RESIDUAL
                )
                black = sealed.active_features(
                    ofen, 1, sealed.ARCHITECTURE_KING_STATE_RESIDUAL
                )
            except ValueError as error:
                raise ValueError(
                    f"{location}: invalid Omega OFEN: {error}"
                ) from error
            side = ofen.split()[1]
            if side not in ("w", "b"):
                raise ValueError(f"{location}: invalid side to move")
            ofens.append(ofen)
            groups.append(group)
            phases.append(phase)
            splits.append(
                sealed.deterministic_split(
                    group,
                    split_seed,
                    train_percent,
                    validation_percent,
                )
            )
            signatures.append(
                sealed.nnue_input_signature(
                    white,
                    black,
                    side == "w",
                    sealed.ARCHITECTURE_KING_STATE_RESIDUAL,
                )
            )
            white_rows.append(white)
            black_rows.append(black)
            stm_rows.append(side == "w")
    if not ofens:
        raise ValueError("corpus contains no positions")
    width = max(max(map(len, white_rows)), max(map(len, black_rows)))
    white_array = np.full(
        (len(ofens), width),
        sealed.KING_STATE_FEATURE_COUNT,
        dtype=np.uint16,
    )
    black_array = np.full_like(
        white_array, sealed.KING_STATE_FEATURE_COUNT
    )
    for index, (white, black) in enumerate(zip(white_rows, black_rows)):
        white_array[index, : len(white)] = white
        black_array[index, : len(black)] = black
    return sealed.FeatureCorpus(
        ofens=tuple(ofens),
        groups=tuple(groups),
        phases=tuple(phases),
        input_signatures=tuple(signatures),
        splits=np.asarray(splits, dtype=np.int8),
        white_features=white_array,
        black_features=black_array,
        side_to_move_white=np.asarray(stm_rows, dtype=np.bool_),
    )


def _incidence_strata(
    phase_keys: Mapping[str, Sequence[str]],
) -> tuple[list[str], dict[int, list[int]]]:
    global_keys = sorted(
        {group for keys in phase_keys.values() for group in keys}
    )
    index = {group: offset for offset, group in enumerate(global_keys)}
    strata: dict[int, list[int]] = {}
    for group in global_keys:
        mask = sum(
            1 << phase_index
            for phase_index, phase in enumerate(sealed.PHASES)
            if group in phase_keys[phase]
        )
        strata.setdefault(mask, []).append(index[group])
    if any(mask <= 0 or len(indices) < 2 for mask, indices in strata.items()):
        raise ValueError(
            "every observed bootstrap phase-incidence stratum must contain "
            "at least two groupIds"
        )
    return global_keys, strata


def _draw_incidence_multiplicity(
    rng: np.random.Generator,
    *,
    group_count: int,
    strata: Mapping[int, Sequence[int]],
) -> np.ndarray:
    multiplicity = np.zeros(group_count, dtype=np.float64)
    for mask in sorted(strata):
        indices = np.asarray(strata[mask], dtype=np.int64)
        draw = rng.integers(0, len(indices), size=len(indices))
        sampled = indices[draw]
        multiplicity += np.bincount(
            sampled, minlength=group_count
        ).astype(np.float64)
    return multiplicity


def _paired_bootstrap(
    candidate: Mapping[str, Any],
    baselines: Mapping[str, Mapping[str, Any]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    """Paired phase-incidence-stratified cluster bootstrap."""

    phase_keys: dict[str, list[str]] = {}
    for phase in sealed.PHASES:
        candidate_groups = candidate["_groupLoss"][phase]
        keys = sorted(candidate_groups)
        if not keys:
            raise ValueError(f"bootstrap phase {phase} has no groups")
        candidate_values = list(candidate_groups.values())
        if not all(math.isfinite(float(value)) for value in candidate_values):
            raise ValueError(
                f"bootstrap candidate has nonfinite loss in {phase}"
            )
        phase_keys[phase] = keys
        for baseline, metrics in baselines.items():
            baseline_groups = metrics["_groupLoss"][phase]
            if set(baseline_groups) != set(keys):
                raise ValueError(
                    f"bootstrap group mismatch for {baseline} in {phase}"
                )
            values = list(baseline_groups.values())
            if (
                not values
                or not all(math.isfinite(float(value)) for value in values)
                or float(np.mean(values)) <= 0.0
            ):
                raise ValueError(
                    f"bootstrap baseline {baseline} has nonpositive or "
                    f"nonfinite loss in {phase}"
                )
    global_keys, strata = _incidence_strata(phase_keys)
    key_index = {group: index for index, group in enumerate(global_keys)}
    phase_indices = {
        phase: np.asarray(
            [key_index[group] for group in keys], dtype=np.int64
        )
        for phase, keys in phase_keys.items()
    }
    rng = np.random.Generator(np.random.PCG64(seed))
    relative = {
        baseline: np.empty(iterations, dtype=np.float64)
        for baseline in baselines
    }
    for iteration in range(iterations):
        multiplicity = _draw_incidence_multiplicity(
            rng,
            group_count=len(global_keys),
            strata=strata,
        )
        candidate_phase: list[float] = []
        baseline_phase: dict[str, list[float]] = {
            baseline: [] for baseline in baselines
        }
        for phase in sealed.PHASES:
            keys = phase_keys[phase]
            weights = multiplicity[phase_indices[phase]]
            sampled_cells = int(weights.sum())
            if sampled_cells != len(keys):
                raise AssertionError(
                    f"bootstrap changed {phase} group-cell count "
                    f"from {len(keys)} to {sampled_cells}"
                )
            candidate_values = np.asarray(
                [candidate["_groupLoss"][phase][group] for group in keys],
                dtype=np.float64,
            )
            candidate_phase.append(
                float(np.average(candidate_values, weights=weights))
            )
            for baseline, metrics in baselines.items():
                baseline_values = np.asarray(
                    [
                        metrics["_groupLoss"][phase][group]
                        for group in keys
                    ],
                    dtype=np.float64,
                )
                baseline_phase[baseline].append(
                    float(np.average(baseline_values, weights=weights))
                )
        candidate_loss = float(np.mean(candidate_phase))
        if not math.isfinite(candidate_loss):
            raise ValueError("bootstrap sampled a nonfinite candidate loss")
        for baseline in baselines:
            baseline_loss = float(np.mean(baseline_phase[baseline]))
            if not math.isfinite(baseline_loss) or baseline_loss <= 0.0:
                raise ValueError(
                    f"bootstrap baseline {baseline} sampled a nonpositive "
                    "or nonfinite phase-macro loss"
                )
            relative[baseline][iteration] = (
                baseline_loss - candidate_loss
            ) / baseline_loss
    return {
        baseline: {
            "iterations": iterations,
            "seed": seed,
            "clusterUnit": "global leakage component groupId",
            "stratification": "phase-incidence bitmask",
            "incidenceStrata": len(strata),
            "minimumGroupsPerIncidenceStratum": min(map(len, strata.values())),
            "phaseMacro": True,
            "oneSided95LowerRelativeLossImprovement": float(
                np.quantile(values, 0.05, method="linear")
            ),
        }
        for baseline, values in relative.items()
    }


def _install_amendment(amendment: Mapping[str, Any]) -> None:
    original_validate_protocol = sealed._validate_protocol

    def amended_validate_protocol(
        protocol: Mapping[str, Any],
    ) -> dict[str, Any]:
        parsed = deepcopy(original_validate_protocol(protocol))
        parsed["aggregation"]["groupPhasePurityRequired"] = False
        parsed["bootstrap"].update(
            {
                "unit": (
                    "global leakage component groupId, stratified by "
                    "phase-incidence bitmask"
                ),
                "phaseStratified": True,
                "sampling": (
                    "sample each phase-incidence stratum with replacement; "
                    "reuse one groupId multiplicity in every phase"
                ),
                "crossPhaseMultiplicityShared": True,
                "minimumGroupsPerObservedStratum": 2,
            }
        )
        return parsed

    sealed._validate_protocol = amended_validate_protocol
    sealed._feature_corpus = _feature_corpus
    sealed._paired_bootstrap = _paired_bootstrap
    original_atomic_json = sealed._atomic_json

    def amended_atomic_json(
        path: Path,
        value: Any,
        *,
        no_clobber: bool,
    ) -> None:
        if (
            isinstance(value, dict)
            and value.get("kind") == sealed.PLAN_KIND
        ):
            value = deepcopy(value)
            identities = sealed._mapping(
                value.get("identities"), "training plan identities"
            )
            identities["protocolAmendment"] = sealed._identity(AMENDMENT_PATH)
            identities["amendedOrchestrator"] = sealed._identity(
                Path(__file__)
            )
            audit = sealed._mapping(
                value.get("corpusFeatureAudit"),
                "training plan feature audit",
            )
            audit["groupPhasePure"] = False
            audit["crossPhaseGroupsAllowed"] = True
            audit["bootstrapUnit"] = "global leakage component groupId"
            audit["bootstrapStratification"] = "phase-incidence bitmask"
            value["protocolAmendment"] = {
                "amendmentId": amendment["amendmentId"],
                "preservesFrozenGroupIdSplitRouting": True,
                "changesPromotionThresholds": False,
            }
        original_atomic_json(path, value, no_clobber=no_clobber)

    sealed._atomic_json = amended_atomic_json


def _self_test() -> None:
    candidate = {
        "_groupLoss": {
            "opening": {
                "shared-a": 1.0,
                "shared-b": 1.2,
                "opening-a": 2.0,
                "opening-b": 2.2,
            },
            "middlegame": {
                "shared-a": 1.5,
                "shared-b": 1.7,
                "middle-a": 2.5,
                "middle-b": 2.7,
            },
            "late": {
                "shared-a": 2.0,
                "shared-b": 2.2,
                "late-a": 3.0,
                "late-b": 3.2,
            },
            "endgame": {
                "shared-a": 2.5,
                "shared-b": 2.7,
                "end-a": 3.5,
                "end-b": 3.7,
            },
        }
    }
    baseline = {
        "_groupLoss": {
            phase: {
                group: value + 1.0
                for group, value in candidate["_groupLoss"][phase].items()
            }
            for phase in sealed.PHASES
        }
    }
    first = _paired_bootstrap(
        candidate, {"K0": baseline}, iterations=128, seed=20260727
    )
    second = _paired_bootstrap(
        candidate, {"K0": baseline}, iterations=128, seed=20260727
    )
    if first != second:
        raise AssertionError("amended bootstrap is not deterministic")
    if (
        first["K0"]["oneSided95LowerRelativeLossImprovement"] <= 0.0
        or first["K0"]["clusterUnit"]
        != "global leakage component groupId"
        or first["K0"]["stratification"] != "phase-incidence bitmask"
    ):
        raise AssertionError("amended bootstrap smoke test failed")
    identical = _paired_bootstrap(
        candidate, {"same": candidate}, iterations=128, seed=20260727
    )
    if identical["same"]["oneSided95LowerRelativeLossImprovement"] != 0.0:
        raise AssertionError("identical-model bootstrap was not exactly zero")

    phase_keys = {
        phase: sorted(candidate["_groupLoss"][phase])
        for phase in sealed.PHASES
    }
    keys, strata = _incidence_strata(phase_keys)
    multiplicity = _draw_incidence_multiplicity(
        np.random.Generator(np.random.PCG64(7)),
        group_count=len(keys),
        strata=strata,
    )
    index = {group: offset for offset, group in enumerate(keys)}
    if (
        multiplicity[index["shared-a"]]
        + multiplicity[index["shared-b"]]
        != 2.0
    ):
        raise AssertionError("cross-phase incidence stratum size changed")
    for phase, groups in phase_keys.items():
        if sum(multiplicity[index[group]] for group in groups) != len(groups):
            raise AssertionError(
                f"phase-incidence bootstrap changed {phase} denominator"
            )

    reversed_candidate = {
        "_groupLoss": {
            phase: dict(
                reversed(list(candidate["_groupLoss"][phase].items()))
            )
            for phase in reversed(sealed.PHASES)
        }
    }
    reversed_result = _paired_bootstrap(
        reversed_candidate,
        {"K0": baseline},
        iterations=128,
        seed=20260727,
    )
    if reversed_result != first:
        raise AssertionError("bootstrap depends on mapping insertion order")

    mismatch = deepcopy(baseline)
    del mismatch["_groupLoss"]["opening"]["shared-a"]
    try:
        _paired_bootstrap(
            candidate, {"K0": mismatch}, iterations=1, seed=20260727
        )
    except ValueError:
        pass
    else:
        raise AssertionError("bootstrap accepted mismatched phase/group keys")

    singleton = deepcopy(candidate)
    for phase in sealed.PHASES:
        del singleton["_groupLoss"][phase]["shared-b"]
    try:
        _paired_bootstrap(
            singleton, {"same": singleton}, iterations=1, seed=20260727
        )
    except ValueError:
        pass
    else:
        raise AssertionError("bootstrap accepted a singleton incidence stratum")


def main(argv: Sequence[str] | None = None) -> int:
    amendment = _verify_amendment()
    _install_amendment(amendment)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["self-test"]:
        _self_test()
    return sealed.main(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
