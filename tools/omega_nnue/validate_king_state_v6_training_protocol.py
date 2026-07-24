#!/usr/bin/env python3
"""Validate the target-blind Generation-6 training declaration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

try:
    from . import king_state_train_generation6 as generation6
except ImportError:  # Direct script execution from tools/omega_nnue.
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    import king_state_train_generation6 as generation6


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL = (
    REPO / "validation" / "omega-nnue-king-state-v6-training-protocol.json"
)


def _strict_json_loads(text: str, *, location: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{location}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{location}: non-finite JSON number {value!r}")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{location}: invalid JSON") from error


def validate_protocol(path: Path = DEFAULT_PROTOCOL) -> dict[str, Any]:
    identity, payload = generation6._snapshot_file(path)
    resolved = Path(identity["path"])
    document = _strict_json_loads(
        payload.decode("utf-8"), location=str(resolved)
    )
    expected = generation6.protocol_document()
    if not generation6._type_exact_equal(document, expected):
        raise ValueError("G6 training protocol differs from executable constants")
    primary = {
        candidate: generation6._domain_seed(
            generation6.PRIMARY_TRAINING_SEED_BASE,
            "primary-training",
            candidate,
        )
        for candidate in generation6.CANDIDATES
    }
    robustness = {
        candidate: generation6._domain_seed(
            generation6.ROBUSTNESS_TRAINING_SEED_BASE,
            "robustness-training",
            candidate,
        )
        for candidate in generation6.CANDIDATES
    }
    if len(set(primary.values()) | set(robustness.values())) != 6:
        raise ValueError("G6 primary/robustness domain-separated seeds collide")
    return {
        "status": "passed",
        "profileId": generation6.PROFILE_ID,
        "candidateRecipes": {
            candidate: dict(generation6.CANDIDATE_RECIPES[candidate])
            for candidate in generation6.CANDIDATES
        },
        "primaryTrainingSeeds": primary,
        "robustnessTrainingSeeds": robustness,
        "teacherLabelsRead": 0,
        "gameResultsRead": 0,
        "recursiveTypeExactComparison": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(json.dumps(validate_protocol(args.protocol), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
