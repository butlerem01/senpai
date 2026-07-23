#!/usr/bin/env python3
"""Frozen protocol foundation for open-ended Omega NNUE confirmation.

Generation 5 remains a screening and nomination experiment.  This module
defines the separate, globally alpha-spent confirmation sequence that may
support a project-level "clearly superior" claim.  It deliberately contains
no candidate selection, match launch, result parsing, or attempt mutation.

The protocol uses attempt indices k = 1, 2, ... and spends
beta_k = 0.01 / 2**k.  Formal decisions are made in log space against
log(100) + k * log(2), so the policy itself does not acquire a finite-float
horizon within the admissible signed-32-bit seed range.  Rules-only source and
suite generation for every attempt is fixed by the attempt index and cannot
depend on a nominated candidate.
"""

from __future__ import annotations

import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import types
from typing import Any, Iterable, Mapping, Sequence


_PREAUTH_REPO = Path(__file__).resolve().parents[2]
_PREAUTH_MODULES = {
    "tools/omega_nnue/king_state_dotnet_runtime_generation5.py": (
        12_825,
        "7c33e9f22cd6f77498caa7fa481abaf5f72ec1b805fac6a979739237a08524cb",
    ),
    "tools/omega_nnue/king_state_matches.py": (
        138_593,
        "c8e9d45452716f468023b7af1607e735a269e131c95bce510dd25849f76d9601",
    ),
    "tools/omega_nnue/omega_nnue.py": (
        53_900,
        "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa",
    ),
    "tools/omega_nnue/select_screen.py": (
        39_442,
        "304172e583b4c963718191017b4f8d2426aea325dd42337c197496ee738677ee",
    ),
}


def _preauthenticate_module(path: Path, size: int, digest: str) -> bytes:
    payload = path.read_bytes()
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise ImportError(
            f"refusing to import unauthenticated confirmation dependency: {path}"
        )
    return payload


_AUTHENTICATED_SOURCE_BYTES: dict[str, bytes] = {}
for _preauth_relative, (_preauth_size, _preauth_sha256) in _PREAUTH_MODULES.items():
    _AUTHENTICATED_SOURCE_BYTES[_preauth_relative] = _preauthenticate_module(
        _PREAUTH_REPO / _preauth_relative, _preauth_size, _preauth_sha256
    )


def _load_authenticated_module(name: str, relative: str) -> types.ModuleType:
    if name in sys.modules:
        raise ImportError(f"refusing preloaded authenticated module: {name}")
    path = (_PREAUTH_REPO / relative).resolve()
    payload = _AUTHENTICATED_SOURCE_BYTES[relative]
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = None
    sys.modules[name] = module
    try:
        exec(compile(payload, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


dotnet_runtime = _load_authenticated_module(
    "king_state_dotnet_runtime_generation5",
    "tools/omega_nnue/king_state_dotnet_runtime_generation5.py",
)
omega_module = _load_authenticated_module(
    "omega_nnue", "tools/omega_nnue/omega_nnue.py"
)
screen_module = _load_authenticated_module(
    "select_screen", "tools/omega_nnue/select_screen.py"
)
match_core = _load_authenticated_module(
    "king_state_matches", "tools/omega_nnue/king_state_matches.py"
)


SCHEMA_VERSION = 1
PROTOCOL_ID = "omega-nnue-open-confirmation-v1"
PROTOCOL_KIND = "omega-nnue-open-confirmation-v1-protocol"
GATES = ("development", "equal-node", "equal-time")
FORMAL_GATES = ("equal-node", "equal-time")
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")

FAMILYWISE_ALPHA_NUMERATOR = 1
FAMILYWISE_ALPHA_DENOMINATOR = 100
FIRST_ATTEMPT_INDEX = 1
FIRST_ATTEMPT_BASE_SEED = 2_026_072_311
ATTEMPT_SEED_STRIDE = 100
STAGE_SEED_OFFSETS = {
    "development": 0,
    "equal-node": 1,
    "equal-time": 2,
}
G5_SCREENING_STAGE_SEEDS = (2_026_072_308, 2_026_072_309, 2_026_072_310)
DOTNET_RANDOM_SEED_MAX = (1 << 31) - 1
MAX_SEED_ATTEMPT_INDEX = (
    (
        DOTNET_RANDOM_SEED_MAX
        - FIRST_ATTEMPT_BASE_SEED
        - max(STAGE_SEED_OFFSETS.values())
    )
    // ATTEMPT_SEED_STRIDE
) + 1
FORMAL_MAXIMUM_PAIRS = 512
FORMAL_NULL_ELO = 15.0
E_PROCESS_BET_FRACTIONS = (
    1.0 / 128,
    1.0 / 64,
    1.0 / 32,
    1.0 / 16,
    1.0 / 8,
    1.0 / 4,
    1.0 / 2,
    3.0 / 4,
)
MAX_FEASIBLE_ATTEMPT_INDEX = 377
FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX = 378
MAX_ATTEMPT_INDEX = min(MAX_SEED_ATTEMPT_INDEX, MAX_FEASIBLE_ATTEMPT_INDEX)
MATERIALIZED_POWER_LIMIT = 1_000_000
THRESHOLD_DECIMAL_PRECISION = 80
THRESHOLD_DECIMAL_UPPER_GUARD = Decimal("1e-70")

REPO = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    REPO / "validation" / "omega-nnue-open-confirmation-v1-protocol.json"
)
TOOL_PATH = (
    REPO / "tools" / "omega_nnue" / "king_state_confirmation_protocol_v1.py"
)
ARTIFACT_ROOT = REPO / "build-king-state-confirmation-v1"

HEX_256 = re.compile(r"^[0-9a-f]{64}$")
ATTEMPT_DIRECTORY = re.compile(r"^attempt-([0-9]{6,})$")
CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)

_IMPORTED_DOTNET_RUNTIME = dotnet_runtime
_DOTNET_VERIFY_MANIFEST = dotnet_runtime.verify_manifest
_IMPORTED_OMEGA_MODULE = omega_module
_OMEGA_PARSE_OFEN = omega_module.parse_ofen
_OMEGA_ACTIVE_FEATURES_FROM_PARSED = omega_module._active_features_from_parsed
_IMPORTED_SCREEN_MODULE = screen_module
_SCREEN_INPUT_KEYS = screen_module.input_keys
_SCREEN_OBSERVABLE_OFEN = screen_module.observable_ofen
_SCREEN_PHASE_OF = screen_module.phase_of
_IMPORTED_MATCH_CORE = match_core
_MATCH_CORE_SEQUENTIAL_GATE = match_core._sequential_gate


# These are existing, immutable Generation-5 or shared-runtime inputs.  The
# new confirmation layer reuses their bytes; it does not amend them.
PINNED_FILES: dict[str, tuple[int, str]] = {
    "validation/omega-nnue-king-state-v5-preregistration.template.json": (
        40_603,
        "a75e3bbd0845756b3594f051fa609092cb4f5eea0a2c62a30aeacb321d87c684",
    ),
    "validation/omega-nnue-king-state-v5-match-protocol.json": (
        11_307,
        "af22fe96f2e85b1a079473854c77f953f075017a9ff70ab41408c66720f9a2b4",
    ),
    "tools/omega_nnue/king_state_match_protocol_generation5.py": (
        56_249,
        "c4aad8c459263764fc71aba0648b25f453b8e522a75cc790779f6d80a37c71b2",
    ),
    "tools/omega_nnue/king_state_match_readiness_generation5.py": (
        365_185,
        "798642025d2be85d5e99171ebbf6dc404abb139340c5772e0f5ab662daf76667",
    ),
    "tools/omega_nnue/king_state_matches_generation5.py": (
        120_782,
        "6765bc5adbb24b4ba9c3557db84565c0417869c997cb0311558996a723bc4f1f",
    ),
    "tools/omega_nnue/king_state_matches.py": (
        138_593,
        "c8e9d45452716f468023b7af1607e735a269e131c95bce510dd25849f76d9601",
    ),
    "tools/omega_nnue/omega_nnue.py": (
        53_900,
        "efc55715895f32e948db35428372393c711f701f2e84c69256bdf689065422aa",
    ),
    "tools/omega_nnue/select_screen.py": (
        39_442,
        "304172e583b4c963718191017b4f8d2426aea325dd42337c197496ee738677ee",
    ),
    "tools/omega_nnue/king_state_dotnet_runtime_generation5.py": (
        12_825,
        "7c33e9f22cd6f77498caa7fa481abaf5f72ec1b805fac6a979739237a08524cb",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/dotnet.exe": (
        167_248,
        "a5ccdc3a41d5e5c6014ff64509aed176db39f4f14caffff3dd1997f8907e94d7",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime.manifest.json": (
        33_633,
        "c8543f22f4b353ee461f2e417c3d06ea2f05de2622fab49b789923f9944e00ee",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe": (
        624_128,
        "a7a3e905c1f44cc32eba622615e6f0d29d5b236735de623ca87d42ef9116b1de",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/OmegaMatch.dll": (
        166_400,
        "c51e4733c888bec97f5b1582379e16ecefe4c74537a8fabd2b0fcafd88ef2e6d",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/OmegaMatch.exe": (
        162_304,
        "996a6ea879736906a7706f3ada189677034585e9b91d33bbc95e7e420ecec9f5",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/OmegaRootSampler.dll": (
        57_856,
        "c10e9c3404f9bae9b1841bb0c5ed306ef9a984dfd9c627cf25f4e3aab40a276e",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/OmegaRootSampler.exe": (
        162_304,
        "f899efa67be2d008b8acde057a1a98a10f0ee23d7bb263b05caa3ab3b4cf2f4e",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/ChessLib.dll": (
        248_832,
        "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c",
    ),
}

DOTNET_RUNTIME_BUNDLE_SHA256 = (
    "0ce194480dfb9a58a59c79bf94f19cb2eb571635a00547094a9c8ff28bb5f8f8"
)
OMEGA_MATCH_BUNDLE_SHA256 = (
    "d3e0ebde20db71be98fc8844b053927e220f8433ed4f0897b5013a2680009874"
)
ROOT_SAMPLER_BUNDLE_SHA256 = (
    "6c7f6c0f263488ad5f9af28791899a83d34de8fd9fd874dad0c0bfd68e79196b"
)


def resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with resolve(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def protocol_path(value: Any, label: str) -> Path:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} escapes the repository")
    result = resolve(REPO / path)
    try:
        result.relative_to(REPO)
    except ValueError as error:
        raise ValueError(f"{label} escapes the repository") from error
    return result


def _relative(path: Path) -> str:
    return resolve(path).relative_to(REPO).as_posix()


def identity(path: Path, *, relative: bool = False) -> dict[str, Any]:
    path = resolve(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    stat = path.stat()
    return {
        "path": _relative(path) if relative else str(path),
        "bytes": stat.st_size,
        "sha256": sha256(path),
    }


def _pin(path: str) -> dict[str, Any]:
    size, digest = PINNED_FILES[path]
    return {"path": path, "bytes": size, "sha256": digest}


def _tool_pin() -> dict[str, Any]:
    return identity(TOOL_PATH, relative=True)


def exact_json_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON recursively without bool/int or int/float coercion."""

    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(
            exact_json_equal(actual[key], item) for key, item in expected.items()
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            exact_json_equal(left, right) for left, right in zip(actual, expected)
        )
    if type(expected) is float and (
        not math.isfinite(actual) or not math.isfinite(expected)
    ):
        return False
    return actual == expected


def require_exact_json(actual: Any, expected: Any, label: str) -> None:
    if not exact_json_equal(actual, expected):
        raise ValueError(f"{label} changed or changed JSON type")


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def strict_load(path: Path, label: str) -> dict[str, Any]:
    path = resolve(path)
    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON: {path}") from error
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def exact_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _verify_identity(value: Any, label: str) -> Path:
    record = mapping(value, label)
    if set(record) != {"path", "bytes", "sha256"}:
        raise ValueError(f"{label} identity fields changed")
    if (
        type(record.get("path")) is not str
        or not record["path"]
        or record["path"] != record["path"].strip()
        or type(record.get("bytes")) is not int
        or record["bytes"] < 0
        or type(record.get("sha256")) is not str
        or HEX_256.fullmatch(record["sha256"]) is None
    ):
        raise ValueError(f"{label} identity is malformed")
    path = Path(record["path"])
    if not path.is_absolute():
        path = protocol_path(record["path"], f"{label} path")
    actual = identity(path, relative=not Path(record["path"]).is_absolute())
    if not exact_json_equal(record, actual):
        raise ValueError(f"{label} identity changed")
    return resolve(path)


def _validate_attempt_index(value: Any) -> int:
    index = exact_int(value, "attempt index", minimum=FIRST_ATTEMPT_INDEX)
    if index > MAX_ATTEMPT_INDEX:
        raise ValueError(
            "statistical-cap-exhausted: open-confirmation-v1 admits attempts "
            f"only through {MAX_ATTEMPT_INDEX}; a separately preregistered v2 "
            "with larger formal pair caps is required"
        )
    return index


def attempt_base_seed(attempt_index: Any) -> int:
    index = _validate_attempt_index(attempt_index)
    value = FIRST_ATTEMPT_BASE_SEED + ATTEMPT_SEED_STRIDE * (index - 1)
    if not 0 <= value <= DOTNET_RANDOM_SEED_MAX:
        raise OverflowError("attempt base seed exceeds signed Int32")
    return value


def stage_seed(attempt_index: Any, gate: str) -> int:
    index = _validate_attempt_index(attempt_index)
    if type(gate) is not str or gate not in GATES:
        raise ValueError(f"unknown confirmation gate: {gate!r}")
    value = attempt_base_seed(index) + STAGE_SEED_OFFSETS[gate]
    if not 0 <= value <= DOTNET_RANDOM_SEED_MAX:
        raise OverflowError("attempt stage seed exceeds signed Int32")
    return value


def attempt_directory_name(attempt_index: Any) -> str:
    index = _validate_attempt_index(attempt_index)
    return f"attempt-{index:06d}"


def parse_attempt_directory_name(value: Any) -> int:
    if type(value) is not str:
        raise ValueError("attempt directory name must be a string")
    match = ATTEMPT_DIRECTORY.fullmatch(value)
    if match is None:
        raise ValueError("attempt directory name is not canonical")
    index = _validate_attempt_index(int(match.group(1)))
    if attempt_directory_name(index) != value:
        raise ValueError("attempt directory name has noncanonical zero padding")
    return index


def attempt_namespace(attempt_index: Any) -> Path:
    return resolve(ARTIFACT_ROOT / attempt_directory_name(attempt_index))


def beta_components(attempt_index: Any) -> dict[str, int]:
    """Return an exact symbolic beta_k = 1 / (100 * 2**k)."""

    index = _validate_attempt_index(attempt_index)
    return {
        "numerator": FAMILYWISE_ALPHA_NUMERATOR,
        "denominatorCoefficient": FAMILYWISE_ALPHA_DENOMINATOR,
        "denominatorPowerOfTwo": index,
    }


def promotion_e_value_components(attempt_index: Any) -> dict[str, int]:
    index = _validate_attempt_index(attempt_index)
    return {"coefficient": FAMILYWISE_ALPHA_DENOMINATOR, "powerOfTwo": index}


def _promotion_log_threshold_decimal_upper(index: int) -> Decimal:
    if type(index) is not int or index < FIRST_ATTEMPT_INDEX:
        raise ValueError("threshold index must be a positive integer")
    with localcontext() as context:
        context.prec = THRESHOLD_DECIMAL_PRECISION
        approximate = Decimal(FAMILYWISE_ALPHA_DENOMINATOR).ln() + Decimal(
            index
        ) * Decimal(2).ln()
        # Decimal.ln is correctly rounded in the active context.  This guard
        # is many orders larger than its accumulated rounding error for every
        # v1 feasibility index, while still negligible at binary64 scale.
        return +(approximate + THRESHOLD_DECIMAL_UPPER_GUARD)


def _conservative_promotion_log_threshold(index: int) -> float:
    upper = _promotion_log_threshold_decimal_upper(index)
    value = float(upper)
    while Decimal.from_float(value) < upper:
        value = math.nextafter(value, math.inf)
    if not math.isfinite(value):
        raise OverflowError("promotion log threshold is not finite")
    return value


def promotion_log_threshold(attempt_index: Any) -> float:
    return _conservative_promotion_log_threshold(
        _validate_attempt_index(attempt_index)
    )


def maximum_formal_log_e() -> float:
    """Maximum pinned mixture log-E in 512 pairs (all candidate wins)."""

    null_score = 1.0 / (1.0 + 10.0 ** (-FORMAL_NULL_ELO / 400.0))
    component_logs = [
        FORMAL_MAXIMUM_PAIRS
        * math.log(1.0 + (fraction / null_score) * (1.0 - null_score))
        for fraction in E_PROCESS_BET_FRACTIONS
    ]
    maximum = max(component_logs)
    return maximum + math.log(
        sum(math.exp(value - maximum) for value in component_logs)
        / len(component_logs)
    )


def materialized_beta(attempt_index: Any) -> Fraction:
    """Materialize beta for audits/tests with an explicit allocation guard."""

    index = _validate_attempt_index(attempt_index)
    if index > MATERIALIZED_POWER_LIMIT:
        raise ValueError(
            "attempt beta is intentionally symbolic beyond the materialization limit"
        )
    return Fraction(1, FAMILYWISE_ALPHA_DENOMINATOR * (1 << index))


def materialized_promotion_e_value(attempt_index: Any) -> int:
    index = _validate_attempt_index(attempt_index)
    if index > MATERIALIZED_POWER_LIMIT:
        raise ValueError(
            "promotion E-value is intentionally log-domain beyond the materialization limit"
        )
    return FAMILYWISE_ALPHA_DENOMINATOR * (1 << index)


def _runtime_bundle_identity(
    root: Path, *, apphost_name: str, assembly_name: str
) -> dict[str, Any]:
    root = resolve(root)

    def included(path: Path) -> bool:
        name = path.name.casefold()
        return (
            path.suffix.casefold() in {".dll", ".so", ".dylib"}
            or name == apphost_name.casefold()
            or name.endswith(".deps.json")
            or name.endswith(".runtimeconfig.json")
        )

    files: list[dict[str, Any]] = []
    canonical = bytearray()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file() and included(item)),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        item = identity(path)
        files.append(
            {
                "relativePath": relative,
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
        )
        canonical.extend(
            f"{relative}\t{item['bytes']}\t{item['sha256']}\n".encode("utf-8")
        )
    if not any(item["relativePath"] == apphost_name for item in files):
        raise ValueError(f"runtime bundle lacks {apphost_name}")
    if not any(item["relativePath"] == assembly_name for item in files):
        raise ValueError(f"runtime bundle lacks {assembly_name}")
    return {
        "root": str(root),
        "appHostRelativePath": apphost_name,
        "assemblyRelativePath": assembly_name,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def _expected_protocol() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROTOCOL_KIND,
        "protocolId": PROTOCOL_ID,
        "createdUtc": "2026-07-23T14:41:02Z",
        "status": "frozen before any open-confirmation candidate claim, pool, or result access",
        "scope": {
            "generation5Role": "screening and exact candidate nomination only",
            "confirmationRole": "only this layer may authorize the project-level clearly-superior claim",
            "attemptsGlobalAcrossCandidates": True,
            "development": "safety screening only; never superiority evidence",
            "formalGates": list(FORMAL_GATES),
        },
        "informationBoundary": {
            "protocolFrozenBeforeConfirmationPools": True,
            "protocolFrozenBeforeConfirmationResults": True,
            "suiteConstructionIsRulesOnly": True,
            "candidateIdentityUnavailableToPoolAndSuiteSelection": True,
            "samplerSelectorSealPredatesAnyG5MatchLaunch": True,
            "samplerSelectorSealPredatesAnyG5NominationResult": True,
            "implementationSealPredatesCandidateClaim": True,
            "candidateClaimPredatesAllAttemptSampling": True,
            "candidateClaimIdentityUnavailableToSamplerAndSelector": True,
            "allThreeSuitesSealedTogetherBeforeMatchAuthorization": True,
            "matchResultsAccessedByProtocol": 0,
            "targetFieldsDecodedByProtocol": 0,
        },
        "namespaces": {
            "root": "build-king-state-confirmation-v1",
            "attemptDirectoryFormat": "attempt-{k:06d}",
            "attemptDirectoryMinimumDigits": 6,
            "attemptChildren": [
                "sampler",
                "sealed",
                "development",
                "equal-node",
                "equal-time",
            ],
        },
        "attemptSequence": {
            "firstAttemptIndex": FIRST_ATTEMPT_INDEX,
            "maximumAdmissibleAttemptIndex": MAX_ATTEMPT_INDEX,
            "maximumAttemptIndexFromSignedInt32Seeds": MAX_SEED_ATTEMPT_INDEX,
            "maximumAttemptIndexFromFormal512PairFeasibility": MAX_FEASIBLE_ATTEMPT_INDEX,
            "firstRejectedAttemptIndex": FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX,
            "firstRejectedAttemptReason": "statistical-cap-exhausted",
            "continuationAfterExhaustion": "requires a separately preregistered v2 with larger formal pair caps before any further attempt",
            "successorProtocolFirstAttemptIndex": FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX,
            "successorMustContinueSameGlobalAlphaAndAttemptChain": True,
            "successorMayNotResetForProtocolVersionOrCandidate": True,
            "successorFirstClaimMustAuthenticateAttempt377TerminalClosure": True,
            "globalAcrossCandidates": True,
            "noGaps": True,
            "noReuse": True,
            "noConcurrentAttempts": True,
            "previousClosureRequiredAfterAttempt1": True,
            "candidateClaimPublicationConsumesAttempt": True,
            "claimedAttemptConsumedByTerminalFailureOrAbort": True,
            "baseSeedFormula": "2026072311 + 100 * (k - 1)",
            "baseSeedAtAttempt1": FIRST_ATTEMPT_BASE_SEED,
            "baseSeedStride": ATTEMPT_SEED_STRIDE,
            "seedIntegerDomain": "nonnegative signed 32-bit System.Random seed",
            "stageSeedOffsets": copy.deepcopy(STAGE_SEED_OFFSETS),
            "attempt1StageSeeds": {
                gate: stage_seed(1, gate) for gate in GATES
            },
            "g5ScreeningStageSeedsExcluded": list(G5_SCREENING_STAGE_SEEDS),
        },
        "alphaSpending": {
            "familywiseAlphaNumerator": FAMILYWISE_ALPHA_NUMERATOR,
            "familywiseAlphaDenominator": FAMILYWISE_ALPHA_DENOMINATOR,
            "attemptBetaFormula": "beta_k = 1 / (100 * 2^k), k >= 1",
            "promotionEValueFormula": "E >= 100 * 2^k",
            "promotionLogThresholdFormula": "log(E) >= log(100) + k * log(2)",
            "authoritativeComparisonDomain": "natural logarithm",
            "binary64ThresholdRounding": "conservative upward bound from 80-digit Decimal logarithms",
            "formal512PairAllWinMaximumLogE": "265.9622317806987",
            "attempt377IsReachable": True,
            "attempt378IsUnreachable": True,
            "allAdmittedAttemptsPassMaximumLogEFeasibility": True,
            "eProcessImplementationBinding": "tools/omega_nnue/king_state_matches.py::_sequential_gate",
            "eProcessBetFractions": [
                {"numerator": 1, "denominator": 128},
                {"numerator": 1, "denominator": 64},
                {"numerator": 1, "denominator": 32},
                {"numerator": 1, "denominator": 16},
                {"numerator": 1, "denominator": 8},
                {"numerator": 1, "denominator": 4},
                {"numerator": 1, "denominator": 2},
                {"numerator": 3, "denominator": 4},
            ],
            "promotionFactorFormula": "1 + (betFraction / nullScore) * (pairScore - nullScore)",
            "mixtureFormula": "equal-weight log-mean-exp across the eight fixed bet fractions",
            "pairScoreDomain": "closed interval [0,1]",
            "allWinsMaximizesEveryPromotionFactor": True,
            "readinessAndOrchestratorMustAssertExactEProcessBinding": True,
            "nullScoreFormula": "1 / (1 + 10^(-nullElo/400))",
            "inspectOnlyAtCompleteFourPhaseBlocks": True,
            "preMinimumThresholdCrossingsAreForgotten": True,
            "firstEligibleThresholdCrossingLatches": True,
            "firstAttempt": {
                "attemptIndex": 1,
                "betaNumerator": 1,
                "betaDenominator": 200,
                "promotionEValue": 200,
            },
            "sumIdentity": "sum(k=1..infinity, 1/(100*2^k)) = 0.01",
            "formalGateAllocation": "the same beta_k applies independently to each formal gate; the claim is their conjunction",
            "futilityEValue": 20,
            "futilityAnytimePValue": 0.05,
            "futilityCreatesSuperiorityEvidence": False,
        },
        "sampler": {
            "deterministicPrng": "SplitMix64",
            "trajectoryPairsPerStage": 8_192,
            "independentTrajectoriesPerPair": 2,
            "workers": 4,
            "maxPlies": 220,
            "positionsPerPhaseAndSide": 2,
            "captureSelectionPercent": 72,
            "phaseOrder": list(PHASES),
            "sideOrder": list(SIDES),
        },
        "stages": {
            "development": {
                "role": "safety-screen-only",
                "roots": 64,
                "rootsPerPhase": 16,
                "rootsPerPhaseAndSideToMove": 8,
                "mode": "nodes",
                "nodesPerMove": 30_000,
                "searchTimeoutMs": 60_000,
                "initialPairBudget": 64,
                "resumePairBudget": 4,
                "minimumPairsBeforeDecision": 64,
                "maximumPairs": 64,
                "minimumCandidateScore": 0.5,
                "contributesToSuperiorityClaim": False,
            },
            "equal-node": {
                "role": "formal-strength-confirmation",
                "roots": 512,
                "rootsPerPhase": 128,
                "rootsPerPhaseAndSideToMove": 64,
                "mode": "nodes",
                "nodesPerMove": 60_000,
                "searchTimeoutMs": 90_000,
                "initialPairBudget": 128,
                "resumePairBudget": 4,
                "minimumPairsBeforeDecision": 128,
                "maximumPairs": 512,
                "nullElo": 15,
                "promotionThresholdSource": "attempt alpha-spending log threshold",
                "futilityEValue": 20,
                "balancedBlockChecksOnly": True,
                "contributesToSuperiorityClaim": True,
            },
            "equal-time": {
                "role": "formal-practical-strength-confirmation",
                "roots": 512,
                "rootsPerPhase": 128,
                "rootsPerPhaseAndSideToMove": 64,
                "mode": "moveTime",
                "moveTimeMs": 1_000,
                "searchTimeoutMs": 5_000,
                "initialPairBudget": 128,
                "resumePairBudget": 4,
                "minimumPairsBeforeDecision": 128,
                "maximumPairs": 512,
                "nullElo": 15,
                "promotionThresholdSource": "attempt alpha-spending log threshold",
                "futilityEValue": 20,
                "balancedBlockChecksOnly": True,
                "oneGameAtATime": True,
                "idleMachineAttestationRequired": True,
                "contributesToSuperiorityClaim": True,
            },
        },
        "pairedSchedule": {
            "gamesPerRoot": 2,
            "roles": [
                {"gameSuffix": "ab", "white": "engineA", "black": "engineB"},
                {"gameSuffix": "ba", "white": "engineB", "black": "engineA"},
            ],
            "engineA": "nnue-candidate",
            "engineB": "hce-control",
            "candidatePairScore": "mean of the candidate's two colour-swapped game scores",
            "balancedPairBlockSize": 4,
            "onePairFromEachPhasePerBlock": True,
            "rootSideToMoveConstantWithinBlock": True,
            "rootSideToMoveAlternatesBetweenBlocks": True,
            "inverseArrangeForSeededDotNetRandomShuffle": True,
            "pairBudgetMustBeMultipleOf": 4,
        },
        "freshness": {
            "selectedRootOrbitIntersectionMustBeZero": True,
            "crossStageSelectedRootOrbitsDisjoint": True,
            "freshRulesOnlyPoolPerStageAndAttempt": True,
            "excludedUnion": [
                "all frozen NNUE training, validation, held-out, screening, and confirmation history",
                "the complete Generation-5 decision corpus and source pool",
                "all Generation-5 raw match sampler pools and sealed suites",
                "all played Generation-5 match event, PV, and final positions",
                "all prior open-confirmation pools, suites, events, PVs, and final positions",
            ],
            "futureTrainingMustExcludeAllConfirmationPositionArtifacts": True,
        },
        "nomination": {
            "candidateClaimPublishedBeforeAnyAttemptSampling": True,
            "candidateClaimRequiresPriorImplementationSeal": True,
            "candidateClaimRequiresPreG5SamplerSelectorSeal": True,
            "candidateClaimActivatesAndConsumesAttempt": True,
            "candidateClaimUnavailableToSamplerAndSelector": True,
            "matchAuthorizationOccursOnlyAfterSuiteSeal": True,
            "exactExecutableIdentityRequired": True,
            "exactNetworkIdentityRequired": True,
            "exactManifestAndScreeningLineageRequired": True,
            "candidateMustEqualSuccessfulScreenNominee": True,
            "runnerUpFallback": False,
            "sameExecutableForCandidateAndControl": True,
            "sameNomineeRequiredAtAllThreeStages": True,
            "candidateUseOmegaNNUE": True,
            "candidateAssetHashMustEqualNominatedNetwork": True,
            "controlUseOmegaNNUE": False,
            "controlOmegaNNUEFile": "<empty>",
            "controlExternalAssets": [],
            "candidateIdentityMayDifferBetweenAttempts": True,
            "attemptIndexNeverResetsForANewCandidate": True,
        },
        "engineConfiguration": {
            "commonEngineOptions": {
                "Threads": "1",
                "Hash": "128",
                "Ponder": "false",
                "OwnBook": "false",
                "UCI_Chess960": "false",
                "UCI_Variant": "omega",
            },
            "commonCommandLineArguments": [],
            "candidateAndControlWorkingDirectoryMustBeIdentical": True,
            "workingDirectoryMustBeCanonicalExecutableParent": True,
            "fullEngineSpecsMustBeIdenticalExcept": [
                "engineId",
                "UseOmegaNNUE",
                "OmegaNNUEFile",
                "ExternalAssets",
                "OmegaNnueActiveVerified",
                "StartupDiagnostics",
            ],
            "candidate": {
                "UseOmegaNNUE": "true",
                "OmegaNNUEFile": "exact nominated-network canonical path",
                "externalAssetsMustBeExactSingleton": True,
                "externalAssetSingletonMustEqualNominatedNetworkIdentity": True,
                "OmegaNnueActiveVerified": True,
                "startupDiagnosticsRequireExactNetworkLoadedRecord": True,
                "finalStartupDiagnostic": "info string Omega NNUE evaluation active",
            },
            "control": {
                "UseOmegaNNUE": "false",
                "OmegaNNUEFile": "<empty>",
                "emptyOptionTransmittedExplicitly": True,
                "ExternalAssets": [],
                "OmegaNnueActiveVerified": False,
                "startupDiagnosticsForbidLoadedOrActive": True,
                "finalStartupDiagnostic": "info string Omega NNUE disabled; handcrafted evaluation active",
            },
            "runRecordActivationAttestationRequired": True,
            "perGameActivationDiagnosticsAvailableFromPinnedHarness": False,
            "runRecordAttestationAuthorizesIdenticalSealedFreshLaunches": True,
            "freshLaunchStartupFailureMustBeProtocolFailure": True,
            "anySpecAssetOrDiagnosticMismatchIsSafetyFailure": True,
        },
        "execution": {
            "gateOrder": list(GATES),
            "freshProcessPerGame": True,
            "oneGameAtATime": True,
            "maximumConcurrentGames": 1,
            "repeats": 1,
            "maxPlies": 300,
            "absoluteMaxPlies": 400,
            "stopGraceMs": 2_000,
            "safetyFields": [
                "illegalMoves",
                "illegalPvs",
                "protocolFailures",
                "timeForfeits",
                "abandonedAttempts",
            ],
            "zeroSafetyFailuresRequired": True,
            "appendOnlyIntentCompletionAssessmentChain": True,
            "assessmentRequiredBetweenLaunches": True,
            "eventLogMustBeAppendOnly": True,
            "terminalDecisionBlocksRetry": True,
            "equalTimeRequiresEqualNodePromotion": True,
            "equalTimeIdleAttestationMustPostdateEqualNodePromotion": True,
            "equalTimeAuditRequiresSerializedNonoverlappingGameIntervals": True,
        },
        "promotionRule": {
            "developmentPassRequired": True,
            "equalNodePromotionRequired": True,
            "equalTimePromotionRequired": True,
            "bothFormalGatesUseAttemptBeta": True,
            "bothFormalGatesMustPromoteInSameAttempt": True,
            "zeroSafetyFailuresRequired": True,
            "maximumPairsWithoutSignal": "inconclusive",
            "failedAttemptMayNotBeRetried": True,
            "nextAttemptUsesNextGlobalIndex": True,
            "programStopsAfterFirstConfirmedSuccess": True,
            "programStopsAfterAttempt377WithoutSuccess": True,
            "attempt378AndBeyondRejectedAsStatisticalCapExhausted": True,
            "generation5ScreeningAloneIsNeverClearlySuperior": True,
        },
        "g5Screening": {
            "role": "unchanged screening and nomination authority; not open-confirmation evidence",
            "programPromotionEvidence": False,
            "requiredDecisions": {
                "development": "pass",
                "equal-node": "promote",
                "equal-time": "promote",
            },
            "zeroSafetyFailuresRequired": True,
            "identities": {
                "preregistrationTemplate": _pin(
                    "validation/omega-nnue-king-state-v5-preregistration.template.json"
                ),
                "matchProtocol": _pin(
                    "validation/omega-nnue-king-state-v5-match-protocol.json"
                ),
                "matchProtocolTool": _pin(
                    "tools/omega_nnue/king_state_match_protocol_generation5.py"
                ),
                "matchReadinessTool": _pin(
                    "tools/omega_nnue/king_state_match_readiness_generation5.py"
                ),
                "matchOrchestrator": _pin(
                    "tools/omega_nnue/king_state_matches_generation5.py"
                ),
                "frozenScreeningEngine": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/engine/senpai.exe"
                ),
            },
        },
        "sharedRuntime": {
            "dotnetRuntimeVersion": "10.0.9",
            "dotnetRuntimeBundleSha256": DOTNET_RUNTIME_BUNDLE_SHA256,
            "omegaMatchBundleSha256": OMEGA_MATCH_BUNDLE_SHA256,
            "rootSamplerBundleSha256": ROOT_SAMPLER_BUNDLE_SHA256,
            "identities": {
                "sharedMatchCore": _pin(
                    "tools/omega_nnue/king_state_matches.py"
                ),
                "omegaRulesModule": _pin(
                    "tools/omega_nnue/omega_nnue.py"
                ),
                "screenSelectionModule": _pin(
                    "tools/omega_nnue/select_screen.py"
                ),
                "dotnetRuntimeTool": _pin(
                    "tools/omega_nnue/king_state_dotnet_runtime_generation5.py"
                ),
                "dotnetHost": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime/dotnet.exe"
                ),
                "dotnetRuntimeManifest": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/dotnet-runtime.manifest.json"
                ),
                "omegaMatchAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/OmegaMatch.dll"
                ),
                "omegaMatchAppHost": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/OmegaMatch.exe"
                ),
                "rootSamplerAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/OmegaRootSampler.dll"
                ),
                "rootSamplerAppHost": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/OmegaRootSampler.exe"
                ),
                "rootSamplerRulesAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler/ChessLib.dll"
                ),
            },
        },
        "implementation": {
            "protocolTool": _tool_pin(),
            "samplerSelectorImplementationSealRequiredBeforeAnyG5MatchLaunch": True,
            "samplerSelectorImplementationSealRequiredBeforeAnyG5NominationResultAccess": True,
            "sealedCleanWorkerAcceptsNoClaimOrCandidateInputs": True,
            "samplerSelectorSealMustPinCompleteTransitiveSourceAndRuntimeClosure": True,
            "deterministicCandidateInvarianceProofRequiredBeforeG5Launch": True,
            "readinessAndOrchestratorMustBePinnedBeforeCandidateClaim": True,
            "implementationSealRequiredBeforeCandidateClaim": True,
            "implementationSealUnavailableToCandidateAwareMutation": True,
            "plannedReadinessPath": "tools/omega_nnue/king_state_confirmation_readiness_v1.py",
            "plannedOrchestratorPath": "tools/omega_nnue/king_state_confirmation_matches_v1.py",
        },
    }


def _verify_runtime_bindings(protocol: Mapping[str, Any]) -> None:
    shared = mapping(protocol.get("sharedRuntime"), "shared runtime")
    identities = mapping(shared.get("identities"), "shared runtime identities")

    for current, frozen, module_name, identity_name in (
        (omega_module, _IMPORTED_OMEGA_MODULE, "omega_nnue", "omegaRulesModule"),
        (
            screen_module,
            _IMPORTED_SCREEN_MODULE,
            "select_screen",
            "screenSelectionModule",
        ),
    ):
        if current is not frozen or sys.modules.get(module_name) is not frozen:
            raise ValueError(f"{module_name} authenticated module was substituted")
        actual_path = resolve(Path(str(getattr(frozen, "__file__", ""))))
        expected_path = protocol_path(
            identities[identity_name]["path"], f"{module_name} source path"
        )
        if actual_path != expected_path:
            raise ValueError(f"{module_name} loaded from a noncanonical path")
    if _IMPORTED_OMEGA_MODULE.parse_ofen is not _OMEGA_PARSE_OFEN:
        raise ValueError("authenticated Omega OFEN parser was substituted")
    if (
        _IMPORTED_OMEGA_MODULE._active_features_from_parsed
        is not _OMEGA_ACTIVE_FEATURES_FROM_PARSED
    ):
        raise ValueError("authenticated Omega feature extractor was substituted")
    if _IMPORTED_SCREEN_MODULE.parse_ofen is not _OMEGA_PARSE_OFEN:
        raise ValueError("screen selector OFEN-parser binding was substituted")
    if (
        _IMPORTED_SCREEN_MODULE._active_features_from_parsed
        is not _OMEGA_ACTIVE_FEATURES_FROM_PARSED
    ):
        raise ValueError("screen selector feature-extractor binding was substituted")
    if _IMPORTED_SCREEN_MODULE.input_keys is not _SCREEN_INPUT_KEYS:
        raise ValueError("authenticated screen input-key function was substituted")
    if _IMPORTED_SCREEN_MODULE.observable_ofen is not _SCREEN_OBSERVABLE_OFEN:
        raise ValueError("authenticated observable-OFEN function was substituted")
    if _IMPORTED_SCREEN_MODULE.phase_of is not _SCREEN_PHASE_OF:
        raise ValueError("authenticated phase classifier was substituted")

    if match_core is not _IMPORTED_MATCH_CORE:
        raise ValueError("shared match-core module object was substituted")
    core_module_name = getattr(_IMPORTED_MATCH_CORE, "__name__", None)
    if (
        type(core_module_name) is not str
        or sys.modules.get(core_module_name) is not _IMPORTED_MATCH_CORE
    ):
        raise ValueError("shared match-core import slot was substituted")
    if _IMPORTED_MATCH_CORE._sequential_gate is not _MATCH_CORE_SEQUENTIAL_GATE:
        raise ValueError("shared match-core e-process callable was substituted")
    if _IMPORTED_MATCH_CORE.parse_ofen is not _OMEGA_PARSE_OFEN:
        raise ValueError("shared match core OFEN parser binding was substituted")
    if _IMPORTED_MATCH_CORE.input_keys is not _SCREEN_INPUT_KEYS:
        raise ValueError("shared match core input-key binding was substituted")
    if _IMPORTED_MATCH_CORE.observable_ofen is not _SCREEN_OBSERVABLE_OFEN:
        raise ValueError("shared match core observable-OFEN binding was substituted")
    if _IMPORTED_MATCH_CORE.phase_of is not _SCREEN_PHASE_OF:
        raise ValueError("shared match core phase binding was substituted")
    core_module_path = resolve(
        Path(str(getattr(_IMPORTED_MATCH_CORE, "__file__", "")))
    )
    expected_core_path = protocol_path(
        identities["sharedMatchCore"]["path"], "shared match-core path"
    )
    if core_module_path != expected_core_path:
        raise ValueError("shared match core imported from a noncanonical path")
    if tuple(_IMPORTED_MATCH_CORE.BET_FRACTIONS) != E_PROCESS_BET_FRACTIONS:
        raise ValueError("shared match-core e-process bet fractions changed")

    if dotnet_runtime is not _IMPORTED_DOTNET_RUNTIME:
        raise ValueError(".NET runtime verifier module object was substituted")
    module_name = getattr(_IMPORTED_DOTNET_RUNTIME, "__name__", None)
    if type(module_name) is not str or sys.modules.get(module_name) is not _IMPORTED_DOTNET_RUNTIME:
        raise ValueError(".NET runtime verifier import slot was substituted")
    if _IMPORTED_DOTNET_RUNTIME.verify_manifest is not _DOTNET_VERIFY_MANIFEST:
        raise ValueError(".NET runtime verifier callable was substituted")
    module_path = resolve(Path(str(getattr(_IMPORTED_DOTNET_RUNTIME, "__file__", ""))))
    expected_module_path = protocol_path(
        identities["dotnetRuntimeTool"]["path"], ".NET runtime tool path"
    )
    if module_path != expected_module_path:
        raise ValueError(".NET runtime verifier imported from a noncanonical path")

    manifest_path = _verify_identity(
        identities["dotnetRuntimeManifest"], ".NET runtime manifest"
    )
    runtime = _DOTNET_VERIFY_MANIFEST(manifest_path)
    if (
        runtime.get("runtimeVersion") != shared.get("dotnetRuntimeVersion")
        or runtime.get("bundleSha256") != shared.get("dotnetRuntimeBundleSha256")
        or resolve(Path(str(runtime.get("root", ""))))
        != resolve(dotnet_runtime.RUNTIME_ROOT)
    ):
        raise ValueError("frozen .NET runtime bundle changed")

    omega = _runtime_bundle_identity(
        REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch",
        apphost_name="OmegaMatch.exe",
        assembly_name="OmegaMatch.dll",
    )
    if omega["sha256"] != shared.get("omegaMatchBundleSha256"):
        raise ValueError("frozen OmegaMatch bundle changed")
    sampler = _runtime_bundle_identity(
        REPO / "tools/omega_nnue/frozen_runtime/king-state-v5/root-sampler",
        apphost_name="OmegaRootSampler.exe",
        assembly_name="OmegaRootSampler.dll",
    )
    if sampler["sha256"] != shared.get("rootSamplerBundleSha256"):
        raise ValueError("frozen root-sampler bundle changed")


def _verify_seed_contract(protocol: Mapping[str, Any]) -> None:
    sequence = mapping(protocol.get("attemptSequence"), "attempt sequence")
    if ATTEMPT_SEED_STRIDE <= max(STAGE_SEED_OFFSETS.values()):
        raise AssertionError("stage seed offsets overlap the next attempt")
    if FIRST_ATTEMPT_BASE_SEED <= max(G5_SCREENING_STAGE_SEEDS):
        raise AssertionError("attempt-1 seeds collide with Generation 5")
    require_exact_json(
        sequence.get("attempt1StageSeeds"),
        {gate: stage_seed(1, gate) for gate in GATES},
        "attempt-1 stage seeds",
    )
    seed_maximum = exact_int(
        sequence.get("maximumAttemptIndexFromSignedInt32Seeds"),
        "maximum seed-compatible attempt index",
        minimum=1,
    )
    if seed_maximum != MAX_SEED_ATTEMPT_INDEX:
        raise ValueError("maximum seed-compatible attempt index changed")
    admissible_maximum = exact_int(
        sequence.get("maximumAdmissibleAttemptIndex"),
        "maximum admissible attempt index",
        minimum=1,
    )
    if admissible_maximum != MAX_ATTEMPT_INDEX:
        raise ValueError("maximum admissible attempt index changed")
    last_seed = (
        FIRST_ATTEMPT_BASE_SEED
        + ATTEMPT_SEED_STRIDE * (seed_maximum - 1)
        + max(STAGE_SEED_OFFSETS.values())
    )
    next_seed = last_seed + ATTEMPT_SEED_STRIDE
    if not last_seed <= DOTNET_RANDOM_SEED_MAX < next_seed:
        raise AssertionError("signed-Int32 seed boundary derivation changed")
    for gate in GATES:
        seed = stage_seed(admissible_maximum, gate)
        if not 0 <= seed <= DOTNET_RANDOM_SEED_MAX:
            raise AssertionError("maximum attempt seed escaped signed Int32")


def _verify_feasibility_contract(protocol: Mapping[str, Any]) -> None:
    sequence = mapping(protocol.get("attemptSequence"), "attempt sequence")
    alpha = mapping(protocol.get("alphaSpending"), "alpha spending")
    stages = mapping(protocol.get("stages"), "stages")
    if exact_int(
        sequence.get("maximumAttemptIndexFromFormal512PairFeasibility"),
        "maximum feasible attempt index",
        minimum=1,
    ) != MAX_FEASIBLE_ATTEMPT_INDEX:
        raise ValueError("maximum feasible attempt index changed")
    if exact_int(
        sequence.get("firstRejectedAttemptIndex"),
        "first statistically exhausted attempt index",
        minimum=1,
    ) != FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX:
        raise ValueError("first statistically exhausted attempt index changed")
    for gate in FORMAL_GATES:
        stage = mapping(stages.get(gate), f"{gate} stage")
        if stage.get("maximumPairs") != FORMAL_MAXIMUM_PAIRS:
            raise ValueError(f"{gate} maximum pairs changed")
        if stage.get("nullElo") != int(FORMAL_NULL_ELO):
            raise ValueError(f"{gate} null Elo changed")
    maximum_log_e = maximum_formal_log_e()
    if alpha.get("formal512PairAllWinMaximumLogE") != format(
        maximum_log_e, ".16g"
    ):
        raise ValueError("formal all-win maximum log-E identity changed")
    last_threshold = promotion_log_threshold(MAX_ATTEMPT_INDEX)
    first_exhausted_threshold = _conservative_promotion_log_threshold(
        FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX
    )
    if not last_threshold <= maximum_log_e < first_exhausted_threshold:
        raise AssertionError("formal 512-pair feasibility boundary changed")


def validate_protocol(
    path: Path = PROTOCOL_PATH, *, _allow_noncanonical_for_self_test: bool = False
) -> dict[str, Any]:
    path = resolve(path)
    if not _allow_noncanonical_for_self_test and path != resolve(PROTOCOL_PATH):
        raise ValueError("open-confirmation protocol must be the canonical frozen file")
    value = strict_load(path, "open-confirmation protocol")
    expected = _expected_protocol()
    require_exact_json(value, expected, "open-confirmation protocol")
    if CANONICAL_UTC.fullmatch(str(value.get("createdUtc", ""))) is None:
        raise ValueError("protocol createdUtc is not canonical UTC")

    # Recheck every identity from the parsed document.  Exact comparison above
    # prevents inventory substitution; these checks authenticate current bytes.
    for section_name in ("g5Screening", "sharedRuntime"):
        section = mapping(value.get(section_name), section_name)
        identities = mapping(section.get("identities"), f"{section_name} identities")
        for key, record in identities.items():
            _verify_identity(record, f"{section_name} {key}")
    implementation = mapping(value.get("implementation"), "implementation")
    tool_path = _verify_identity(implementation.get("protocolTool"), "protocol tool")
    if tool_path != resolve(TOOL_PATH):
        raise ValueError("protocol tool escaped its canonical path")

    root = protocol_path(value["namespaces"]["root"], "artifact root")
    if root != resolve(ARTIFACT_ROOT):
        raise ValueError("artifact root changed")
    _verify_seed_contract(value)
    _verify_feasibility_contract(value)
    _verify_runtime_bindings(value)
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = resolve(path)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _expect_invalid(path: Path, label: str) -> None:
    try:
        validate_protocol(path, _allow_noncanonical_for_self_test=True)
    except (OSError, ValueError):
        return
    raise AssertionError(f"protocol accepted invalid mutation: {label}")


def self_test() -> None:
    protocol = validate_protocol()

    # Exact alpha-spending identities and first-attempt requirements.
    if beta_components(1) != {
        "numerator": 1,
        "denominatorCoefficient": 100,
        "denominatorPowerOfTwo": 1,
    }:
        raise AssertionError("attempt-1 beta components changed")
    if materialized_beta(1) != Fraction(1, 200):
        raise AssertionError("attempt-1 beta is not 0.005")
    if materialized_beta(2) != Fraction(1, 400):
        raise AssertionError("attempt-2 beta is not 0.0025")
    if materialized_promotion_e_value(1) != 200:
        raise AssertionError("attempt-1 promotion E-value is not 200")
    if materialized_promotion_e_value(2) != 400:
        raise AssertionError("attempt-2 promotion E-value is not 400")
    if not math.isclose(
        promotion_log_threshold(1), math.log(200.0), rel_tol=0.0, abs_tol=1e-15
    ) or not math.isclose(
        promotion_log_threshold(2), math.log(400.0), rel_tol=0.0, abs_tol=1e-15
    ):
        raise AssertionError("promotion log threshold derivation changed")
    if not (
        promotion_log_threshold(MAX_ATTEMPT_INDEX)
        > promotion_log_threshold(MAX_ATTEMPT_INDEX - 1)
    ):
        raise AssertionError("adjacent admissible log thresholds collapsed")
    previous_threshold = -math.inf
    for attempt in range(1, MAX_ATTEMPT_INDEX + 1):
        threshold = promotion_log_threshold(attempt)
        if not threshold > previous_threshold:
            raise AssertionError("admissible log thresholds are not strictly increasing")
        if Decimal.from_float(threshold) < _promotion_log_threshold_decimal_upper(
            attempt
        ):
            raise AssertionError("promotion threshold was not rounded upward")
        previous_threshold = threshold
    maximum_log_e = maximum_formal_log_e()
    if not math.isclose(
        maximum_log_e, 265.9622317806987, rel_tol=0.0, abs_tol=1e-12
    ):
        raise AssertionError("formal all-win maximum log-E changed")
    if not (
        promotion_log_threshold(MAX_ATTEMPT_INDEX)
        <= maximum_log_e
        < _conservative_promotion_log_threshold(
            FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX
        )
    ):
        raise AssertionError("formal feasibility boundary changed")
    spent = sum((materialized_beta(k) for k in range(1, 257)), Fraction())
    remaining = Fraction(1, 100 * (1 << 256))
    if spent + remaining != Fraction(1, 100) or spent >= Fraction(1, 100):
        raise AssertionError("geometric alpha-spending identity changed")

    # Seed uniqueness, G5 separation, range bounds, and canonical namespaces.
    observed: set[int] = set(G5_SCREENING_STAGE_SEEDS)
    for attempt in range(1, MAX_ATTEMPT_INDEX + 1):
        current = {stage_seed(attempt, gate) for gate in GATES}
        if len(current) != len(GATES) or observed.intersection(current):
            raise AssertionError("confirmation stage seeds collide")
        observed.update(current)
        name = attempt_directory_name(attempt)
        if parse_attempt_directory_name(name) != attempt:
            raise AssertionError("attempt directory round trip changed")
        if attempt_namespace(attempt).parent != resolve(ARTIFACT_ROOT):
            raise AssertionError("attempt namespace escaped its root")
    if [stage_seed(1, gate) for gate in GATES] != [
        2_026_072_311,
        2_026_072_312,
        2_026_072_313,
    ]:
        raise AssertionError("attempt-1 stage seeds changed")
    if MAX_SEED_ATTEMPT_INDEX != 1_214_114:
        raise AssertionError("signed-Int32 attempt boundary changed")
    if MAX_ATTEMPT_INDEX != 377:
        raise AssertionError("statistical feasibility attempt boundary changed")
    last_seed_compatible = (
        FIRST_ATTEMPT_BASE_SEED
        + ATTEMPT_SEED_STRIDE * (MAX_SEED_ATTEMPT_INDEX - 1)
        + STAGE_SEED_OFFSETS["equal-time"]
    )
    if last_seed_compatible != 2_147_483_613:
        raise AssertionError("maximum seed-compatible stage seed changed")
    try:
        attempt_base_seed(FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX)
    except ValueError as error:
        if "statistical-cap-exhausted" not in str(error):
            raise AssertionError("attempt exhaustion reason changed") from error
    else:
        raise AssertionError("statistically exhausted attempt was admitted")
    for invalid in (0, -1, 1.0, True, MAX_ATTEMPT_INDEX + 1):
        try:
            attempt_base_seed(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid attempt index accepted: {invalid!r}")
    for invalid_name in (
        "attempt-1",
        "attempt-000000",
        "attempt-0000010",
        "attempt-+000001",
        "Attempt-000001",
    ):
        try:
            parse_attempt_directory_name(invalid_name)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid attempt directory accepted: {invalid_name}")

    with tempfile.TemporaryDirectory(prefix="omega-confirmation-protocol-") as directory:
        root = Path(directory)
        candidate = root / "protocol.json"
        _atomic_json(candidate, protocol)
        try:
            validate_protocol(candidate)
        except ValueError:
            pass
        else:
            raise AssertionError("noncanonical protocol path was accepted")
        validate_protocol(candidate, _allow_noncanonical_for_self_test=True)

        mutations: tuple[tuple[str, Any], ...] = (
            (
                "attempt beta formula",
                lambda value: value["alphaSpending"].__setitem__(
                    "attemptBetaFormula", "beta_k = 0.01"
                ),
            ),
            (
                "attempt-1 threshold",
                lambda value: value["alphaSpending"]["firstAttempt"].__setitem__(
                    "promotionEValue", 100
                ),
            ),
            (
                "statistical feasibility cap",
                lambda value: value["attemptSequence"].__setitem__(
                    "maximumAdmissibleAttemptIndex", 378
                ),
            ),
            (
                "statistical exhaustion reason",
                lambda value: value["attemptSequence"].__setitem__(
                    "firstRejectedAttemptReason", "seed-exhausted"
                ),
            ),
            (
                "threshold upward rounding",
                lambda value: value["alphaSpending"].__setitem__(
                    "binary64ThresholdRounding", "nearest"
                ),
            ),
            (
                "cross-version global spending",
                lambda value: value["attemptSequence"].__setitem__(
                    "successorMustContinueSameGlobalAlphaAndAttemptChain", False
                ),
            ),
            (
                "e-process bet fractions",
                lambda value: value["alphaSpending"]["eProcessBetFractions"][0].__setitem__(
                    "denominator", 64
                ),
            ),
            (
                "log threshold formula",
                lambda value: value["alphaSpending"].__setitem__(
                    "promotionLogThresholdFormula", "log(E) >= log(100)"
                ),
            ),
            (
                "seed stride",
                lambda value: value["attemptSequence"].__setitem__(
                    "baseSeedStride", 1
                ),
            ),
            (
                "equal-time seed offset",
                lambda value: value["attemptSequence"]["stageSeedOffsets"].__setitem__(
                    "equal-time", 1
                ),
            ),
            (
                "G5 seed exclusion",
                lambda value: value["attemptSequence"].__setitem__(
                    "g5ScreeningStageSeedsExcluded", [2_026_072_308]
                ),
            ),
            (
                "formal minimum pairs",
                lambda value: value["stages"]["equal-node"].__setitem__(
                    "minimumPairsBeforeDecision", 64
                ),
            ),
            (
                "formal maximum pairs",
                lambda value: value["stages"]["equal-time"].__setitem__(
                    "maximumPairs", 128
                ),
            ),
            (
                "resume budget",
                lambda value: value["stages"]["equal-node"].__setitem__(
                    "resumePairBudget", 8
                ),
            ),
            (
                "root count",
                lambda value: value["stages"]["equal-time"].__setitem__(
                    "roots", 256
                ),
            ),
            (
                "null Elo",
                lambda value: value["stages"]["equal-node"].__setitem__(
                    "nullElo", 0
                ),
            ),
            (
                "development evidence",
                lambda value: value["stages"]["development"].__setitem__(
                    "contributesToSuperiorityClaim", True
                ),
            ),
            (
                "engine thread parity",
                lambda value: value["engineConfiguration"][
                    "commonEngineOptions"
                ].__setitem__("Threads", "2"),
            ),
            (
                "candidate exact asset singleton",
                lambda value: value["engineConfiguration"]["candidate"].__setitem__(
                    "externalAssetsMustBeExactSingleton", False
                ),
            ),
            (
                "candidate runtime activation attestation",
                lambda value: value["engineConfiguration"]["candidate"].__setitem__(
                    "OmegaNnueActiveVerified", False
                ),
            ),
            (
                "run-level activation attestation",
                lambda value: value["engineConfiguration"].__setitem__(
                    "runRecordActivationAttestationRequired", False
                ),
            ),
            (
                "control handcrafted diagnostic",
                lambda value: value["engineConfiguration"]["control"].__setitem__(
                    "finalStartupDiagnostic", "info string Omega NNUE disabled"
                ),
            ),
            (
                "formal conjunction",
                lambda value: value["promotionRule"].__setitem__(
                    "bothFormalGatesMustPromoteInSameAttempt", False
                ),
            ),
            (
                "global attempt sequence",
                lambda value: value["attemptSequence"].__setitem__(
                    "globalAcrossCandidates", False
                ),
            ),
            (
                "candidate-blind suites",
                lambda value: value["informationBoundary"].__setitem__(
                    "candidateIdentityUnavailableToPoolAndSuiteSelection", False
                ),
            ),
            (
                "candidate claim ordering",
                lambda value: value["nomination"].__setitem__(
                    "candidateClaimPublishedBeforeAnyAttemptSampling", False
                ),
            ),
            (
                "implementation seal ordering",
                lambda value: value["informationBoundary"].__setitem__(
                    "implementationSealPredatesCandidateClaim", False
                ),
            ),
            (
                "pre-G5 sampler-selector seal",
                lambda value: value["informationBoundary"].__setitem__(
                    "samplerSelectorSealPredatesAnyG5MatchLaunch", False
                ),
            ),
            (
                "clean worker candidate inputs",
                lambda value: value["implementation"].__setitem__(
                    "sealedCleanWorkerAcceptsNoClaimOrCandidateInputs", False
                ),
            ),
            (
                "candidate-invariance proof",
                lambda value: value["implementation"].__setitem__(
                    "deterministicCandidateInvarianceProofRequiredBeforeG5Launch",
                    False,
                ),
            ),
            (
                "candidate claim implementation prerequisite",
                lambda value: value["nomination"].__setitem__(
                    "candidateClaimRequiresPriorImplementationSeal", False
                ),
            ),
            (
                "candidate-aware implementation mutation",
                lambda value: value["implementation"].__setitem__(
                    "implementationSealUnavailableToCandidateAwareMutation", False
                ),
            ),
            (
                "candidate claim isolation",
                lambda value: value["nomination"].__setitem__(
                    "candidateClaimUnavailableToSamplerAndSelector", False
                ),
            ),
            (
                "match authorization ordering",
                lambda value: value["nomination"].__setitem__(
                    "matchAuthorizationOccursOnlyAfterSuiteSeal", False
                ),
            ),
            (
                "pre-minimum crossing policy",
                lambda value: value["alphaSpending"].__setitem__(
                    "preMinimumThresholdCrossingsAreForgotten", False
                ),
            ),
            (
                "screen nominee binding",
                lambda value: value["nomination"].__setitem__(
                    "candidateMustEqualSuccessfulScreenNominee", False
                ),
            ),
            (
                "screen decision requirement",
                lambda value: value["g5Screening"]["requiredDecisions"].__setitem__(
                    "equal-time", "inconclusive"
                ),
            ),
            (
                "success closure",
                lambda value: value["promotionRule"].__setitem__(
                    "programStopsAfterFirstConfirmedSuccess", False
                ),
            ),
            (
                "history exclusion",
                lambda value: value["freshness"]["excludedUnion"].pop(),
            ),
            (
                "artifact root",
                lambda value: value["namespaces"].__setitem__(
                    "root", "build-king-state-v5/matches"
                ),
            ),
            (
                "G5 protocol identity",
                lambda value: value["g5Screening"]["identities"]["matchProtocol"].__setitem__(
                    "sha256", "0" * 64
                ),
            ),
            (
                "shared core identity",
                lambda value: value["sharedRuntime"]["identities"]["sharedMatchCore"].__setitem__(
                    "bytes", 1
                ),
            ),
            (
                "protocol tool identity",
                lambda value: value["implementation"]["protocolTool"].__setitem__(
                    "sha256", "0" * 64
                ),
            ),
            (
                "integer changed to float",
                lambda value: value["sampler"].__setitem__(
                    "trajectoryPairsPerStage", 8192.0
                ),
            ),
            (
                "integer changed to bool",
                lambda value: value["stages"]["equal-node"].__setitem__(
                    "maximumPairs", True
                ),
            ),
            (
                "boolean changed to integer",
                lambda value: value["execution"].__setitem__(
                    "oneGameAtATime", 1
                ),
            ),
            (
                "unregistered field",
                lambda value: value.__setitem__("unregistered", True),
            ),
        )
        for label, mutate in mutations:
            changed = copy.deepcopy(protocol)
            mutate(changed)
            _atomic_json(candidate, changed)
            _expect_invalid(candidate, label)

        candidate.write_text(
            '{"schemaVersion":1,"schemaVersion":1}\n', encoding="utf-8"
        )
        _expect_invalid(candidate, "duplicate JSON key")
        candidate.write_text('{"value":NaN}\n', encoding="utf-8")
        _expect_invalid(candidate, "non-finite JSON token")
        candidate.write_text("[]\n", encoding="utf-8")
        _expect_invalid(candidate, "non-object top level")

    original_core_module = match_core
    for relative, (size, digest) in _PREAUTH_MODULES.items():
        _preauthenticate_module(_PREAUTH_REPO / relative, size, digest)
    try:
        _preauthenticate_module(
            _PREAUTH_REPO / next(iter(_PREAUTH_MODULES)), 0, "0" * 64
        )
    except ImportError:
        pass
    else:
        raise AssertionError("unauthenticated dependency passed pre-import check")
    try:
        _load_authenticated_module(
            "king_state_matches", "tools/omega_nnue/king_state_matches.py"
        )
    except ImportError:
        pass
    else:
        raise AssertionError("preloaded authenticated module was accepted")

    try:
        globals()["match_core"] = object()
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted shared match-core module was accepted")
    finally:
        globals()["match_core"] = original_core_module
    original_core_callable = _IMPORTED_MATCH_CORE._sequential_gate
    try:
        _IMPORTED_MATCH_CORE._sequential_gate = lambda observations: {}
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted match-core e-process was accepted")
    finally:
        _IMPORTED_MATCH_CORE._sequential_gate = original_core_callable
    original_core_parser = _IMPORTED_MATCH_CORE.parse_ofen
    try:
        _IMPORTED_MATCH_CORE.parse_ofen = lambda text: None
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted match-core OFEN parser was accepted")
    finally:
        _IMPORTED_MATCH_CORE.parse_ofen = original_core_parser
    original_omega_parser = _IMPORTED_OMEGA_MODULE.parse_ofen
    try:
        _IMPORTED_OMEGA_MODULE.parse_ofen = lambda text: None
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted authenticated OFEN parser was accepted")
    finally:
        _IMPORTED_OMEGA_MODULE.parse_ofen = original_omega_parser
    original_screen_parser = _IMPORTED_SCREEN_MODULE.parse_ofen
    try:
        _IMPORTED_SCREEN_MODULE.parse_ofen = lambda text: None
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted screen OFEN-parser binding was accepted")
    finally:
        _IMPORTED_SCREEN_MODULE.parse_ofen = original_screen_parser
    original_screen_features = _IMPORTED_SCREEN_MODULE._active_features_from_parsed
    try:
        _IMPORTED_SCREEN_MODULE._active_features_from_parsed = lambda *args: ()
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError(
                "substituted screen feature-extractor binding was accepted"
            )
    finally:
        _IMPORTED_SCREEN_MODULE._active_features_from_parsed = (
            original_screen_features
        )
    original_phase_classifier = _IMPORTED_SCREEN_MODULE.phase_of
    try:
        _IMPORTED_SCREEN_MODULE.phase_of = lambda position: "opening"
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted phase classifier was accepted")
    finally:
        _IMPORTED_SCREEN_MODULE.phase_of = original_phase_classifier
    original_bet_fractions = _IMPORTED_MATCH_CORE.BET_FRACTIONS
    try:
        _IMPORTED_MATCH_CORE.BET_FRACTIONS = (1.0 / 2,)
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted match-core bet fractions were accepted")
    finally:
        _IMPORTED_MATCH_CORE.BET_FRACTIONS = original_bet_fractions

    original_module = dotnet_runtime
    try:
        globals()["dotnet_runtime"] = object()
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted .NET runtime module was accepted")
    finally:
        globals()["dotnet_runtime"] = original_module
    original_callable = _IMPORTED_DOTNET_RUNTIME.verify_manifest
    try:
        _IMPORTED_DOTNET_RUNTIME.verify_manifest = lambda path: {}
        try:
            validate_protocol()
        except ValueError:
            pass
        else:
            raise AssertionError("substituted .NET runtime callable was accepted")
    finally:
        _IMPORTED_DOTNET_RUNTIME.verify_manifest = original_callable


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments not in ([], ["self-test"], ["verify"]):
        raise SystemExit("usage: king_state_confirmation_protocol_v1.py [verify|self-test]")
    if arguments == ["self-test"]:
        self_test()
        print("Open-confirmation protocol self-test passed")
    else:
        value = validate_protocol()
        print(f"Open-confirmation protocol verified: {value['protocolId']}")
        print(f"Protocol SHA-256: {sha256(PROTOCOL_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
