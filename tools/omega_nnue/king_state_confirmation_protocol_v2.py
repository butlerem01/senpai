#!/usr/bin/env python3
"""Frozen protocol foundation for open-ended Omega NNUE confirmation v2.

Generation 5 remains a screening and nomination experiment.  This module
defines the separate, globally alpha-spent confirmation sequence that may
support a distribution-explicit "clearly superior" claim.  Version 2 retires
the sealed-but-never-claimed v1 program and adds a candidate-blind,
normal-start-derived 60+1 clock gate.  It deliberately contains
no candidate selection, match launch, result parsing, or attempt mutation.

The protocol uses attempt indices k = 1, 2, ... and spends
beta_k = 0.01 / 2**k.  Formal decisions are made in log space against
log(100) + k * log(2), so the policy itself does not acquire a finite-float
horizon within the admissible v2 attempt range.  Rules-only source and suite
selection algorithms are fixed without a nominated candidate.  Their realized
seeds are committed from fresh claim-time OS entropy only after candidate
validation, so no confirmation suite is public before the exclusive claim
consumes its attempt index.
"""

from __future__ import annotations

import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import struct
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
PROTOCOL_ID = "omega-nnue-open-confirmation-v2"
PROTOCOL_KIND = "omega-nnue-open-confirmation-v2-protocol"
GATES = ("development", "equal-node", "equal-time", "normal-start-clock")
FORMAL_GATES = ("equal-node", "equal-time", "normal-start-clock")
PHASES = ("opening", "middlegame", "late", "endgame")
SIDES = ("w", "b")

FAMILYWISE_ALPHA_NUMERATOR = 1
FAMILYWISE_ALPHA_DENOMINATOR = 100
FIRST_ATTEMPT_INDEX = 1
G5_SCREENING_STAGE_SEEDS = (2_026_072_308, 2_026_072_309, 2_026_072_310)
DOTNET_RANDOM_SEED_MAX = (1 << 31) - 1
UINT32_MAX = (1 << 32) - 1
ENTROPY_BYTES = 32
SEED_ALGORITHM_ID = "hmac-sha256-int32-rejection-v1"
SEED_HMAC_DOMAIN = b"omega-nnue-confirmation-stage-seed-v2\x00"
ENTROPY_COMMITMENT_DOMAIN = b"omega-nnue-confirmation-entropy-commitment-v2\x00"
_IMPORTED_SECRETS_MODULE = secrets
_IMPORTED_HMAC_MODULE = hmac
_IMPORTED_HASHLIB_MODULE = hashlib
_IMPORTED_RANDOM_MODULE = random
_IMPORTED_OS_MODULE = os
_SECRETS_TOKEN_BYTES = secrets.token_bytes
_SECRETS_SYSRAND = secrets._sysrand
_SYSTEM_RANDOM_RANDBYTES = random.SystemRandom.randbytes
_RANDOM_URANDOM = random._urandom
_OS_URANDOM = os.urandom
_HMAC_NEW = hmac.new
_SHA256 = hashlib.sha256
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
MAX_ATTEMPT_INDEX = MAX_FEASIBLE_ATTEMPT_INDEX
MATERIALIZED_POWER_LIMIT = 1_000_000
THRESHOLD_DECIMAL_PRECISION = 80
THRESHOLD_DECIMAL_UPPER_GUARD = Decimal("1e-70")

REPO = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    REPO / "validation" / "omega-nnue-open-confirmation-v2-protocol.json"
)
TOOL_PATH = (
    REPO / "tools" / "omega_nnue" / "king_state_confirmation_protocol_v2.py"
)
ARTIFACT_ROOT = REPO / "build-king-state-confirmation-v2"
V1_ARTIFACT_ROOT = REPO / "build-king-state-confirmation-v1"
V1_IMPLEMENTATION_SEAL = V1_ARTIFACT_ROOT / "implementation.seal.json"
V1_RETIREMENT_SEAL = ARTIFACT_ROOT / "predecessor" / "v1-retirement.seal.json"

V1_PREDECESSOR = {
    "historicalCommit": "c7c03b0874df381d32fa9f214111545f0a3fe7fd",
    "protocolJson": {
        "bytes": 24_451,
        "sha256": "44c1139307ebce24d9cffa34f2ef5e88267ce06f8524d4bc5474666b15ef2135",
    },
    "protocolTool": {
        "bytes": 95_245,
        "sha256": "d76c20f46e52a970ff8e8171a8af067687737c739138d028750f342152b287d0",
    },
    "readiness": {
        "bytes": 209_831,
        "sha256": "a18378adb2d7ca0a41fbbc7213b346fa72797e07277d18ea6d202700fb585d25",
    },
    "orchestrator": {
        "bytes": 142_896,
        "sha256": "91bdb216e3776e131ed67ada3402fd36d34a913f6e501a207af5fb83f837e7e2",
    },
    "implementationSeal": {
        "bytes": 47_055,
        "sha256": "0bbc647cc6f8c184ad48a05098b957f858f2f1dc1621904052273b26b970e16d",
    },
}

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
    "validation/omega-nnue-king-state-v5-preregistration.json": (
        41_620,
        "60f48b19902b62bea88bd4877d55f798e0b2d6c6751f8acd59cb8522a3fdd5ab",
    ),
    "validation/omega-nnue-king-state-v5-freeze.seal.json": (
        12_914,
        "03ff3fefc9ef4eff553f9aebdaed4f5827eeff29ef613b22ecd5b613f1a435a1",
    ),
    "validation/omega-nnue-king-state-v5-color-compat-preregistration.template.json": (
        8_168,
        "0dacf0853a37d959e24c05567a974a8d85311fc1709502f1b5fb6c346b396219",
    ),
    "validation/omega-nnue-king-state-v5-color-compat-protocol.json": (
        9_543,
        "756dcbb817faa205f51073273b1749001f3acac4321b7d2c5cc9c4e35807bca4",
    ),
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
    "tools/omega_nnue/king_state_match_readiness_generation5_compat_v2.py": (
        123_792,
        "fca7346bd95bc312c4832af354e9db4abd427013dcc791cfe5c43083fa4e132a",
    ),
    "tools/omega_nnue/king_state_matches_generation5_compat_v2.py": (
        202_522,
        "3f99f67c09d9203e5358fe9fde380e9a05be47823cf658e9484427ac7ad74ef2",
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
    "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/ChessLib.dll": (
        248_832,
        "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c",
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
    "build-king-state-v5/matches/sampler/king-state-v5-development-rules-only.jsonl": (
        135_499_112,
        "a276f7cc959320da02ec43e42d200524b19ab51bbcc1144a1d208832dfb5bfbf",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-development-rules-only.jsonl.manifest.json": (
        2_462,
        "74d8c50a08a1ecf798e063a9467fb7229a9153130456e8ffe1bd786f6e291d16",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-development-rules-only.jsonl.complete.seal.json": (
        1_550,
        "2f19e160725165740037a04e1196708087f18b7971f9dc6e434ab5945849751e",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-equal-node-rules-only.jsonl": (
        135_394_413,
        "0c6edb414689b6bb1b7c045caff35c1ac354263b5f72c97400e962247fc36e18",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-equal-node-rules-only.jsonl.manifest.json": (
        2_462,
        "a3469e21fc1a63e7b17554e2b71aa18986a39a7c6022ef83471082f42c9e1b01",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-equal-node-rules-only.jsonl.complete.seal.json": (
        1_548,
        "504443fa480a31dd67e5f35c75ecbbe95ed1c450105613900822db93a4978b63",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-equal-time-rules-only.jsonl": (
        135_415_304,
        "5e17b517e37b2fd8e576fa65d842c5069cc920b9354b7b794b33af1e9e2f4aaa",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-equal-time-rules-only.jsonl.manifest.json": (
        2_461,
        "1ddf6376188690c91998c168452fe00c9517ea0c1926ef20720249754252e360",
    ),
    "build-king-state-v5/matches/sampler/king-state-v5-equal-time-rules-only.jsonl.complete.seal.json": (
        1_548,
        "98e1989ea8614f6298bb68ced781158f037158712b55b6f54418e26360b1f906",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/ChessLib.dll": (
        248_832,
        "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/ChessLib.pdb": (
        78_904,
        "c27b5a5134d57c81f0f2f7c8fbb818716157c24a1dbb5ab34074157b250e236b",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/Newtonsoft.Json.dll": (
        723_368,
        "a28c251dfe36d881e9e2462e171441b8b0ec156fe3f452602c9149b1b9efe05b",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.deps.json": (
        1_608,
        "9ade5beb57416eca211de58010fa81edd07948faed701de366e262f61aae4fc3",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.dll": (
        54_272,
        "b7b4205d6be985489aebe5d1f7c2728ddd6fdac7ffbe59fe8da63bfa2bc71495",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.exe": (
        162_816,
        "9980f4812dae96a62086a454676dfb96ef89f9942c684bc415be054da0f57f20",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.runtimeconfig.json": (
        342,
        "c230a317a54dd960bcbeb5f347f52e18dc665a26f7efda2159fced9a5ac7e097",
    ),
    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/System.IO.Ports.dll": (
        37_648,
        "2767e21f384cca9004b1266ec4b71d3b8a76898594382c377c726c780aa34508",
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
PREFIX_REPLAY_BUNDLE_SHA256 = (
    "1c0ab2019a473f63dad90c92351cfdea569fd0048c60da329eaaf62c15f03dab"
)
G5_RULES_BYTES = 248_832
G5_RULES_SHA256 = (
    "16a01414c9f486561aac0b48cebb7c485621d804a73c00803ec0aff55f572f4c"
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


def _validate_entropy(entropy: Any) -> bytes:
    if type(entropy) is not bytes or len(entropy) != ENTROPY_BYTES:
        raise ValueError(f"claim entropy must be exactly {ENTROPY_BYTES} bytes")
    return entropy


def _require_unmodified_crypto_primitives() -> None:
    if (
        secrets is not _IMPORTED_SECRETS_MODULE
        or sys.modules.get("secrets") is not _IMPORTED_SECRETS_MODULE
        or secrets.token_bytes is not _SECRETS_TOKEN_BYTES
        or secrets._sysrand is not _SECRETS_SYSRAND
        or type(_SECRETS_SYSRAND) is not random.SystemRandom
        or "randbytes" in getattr(_SECRETS_SYSRAND, "__dict__", {})
        or random is not _IMPORTED_RANDOM_MODULE
        or sys.modules.get("random") is not _IMPORTED_RANDOM_MODULE
        or random.SystemRandom.randbytes is not _SYSTEM_RANDOM_RANDBYTES
        or random._urandom is not _RANDOM_URANDOM
        or os is not _IMPORTED_OS_MODULE
        or sys.modules.get("os") is not _IMPORTED_OS_MODULE
        or os.urandom is not _OS_URANDOM
        or _RANDOM_URANDOM is not _OS_URANDOM
    ):
        raise RuntimeError("OS CSPRNG primitive was substituted")
    if (
        hmac is not _IMPORTED_HMAC_MODULE
        or sys.modules.get("hmac") is not _IMPORTED_HMAC_MODULE
        or hmac.new is not _HMAC_NEW
    ):
        raise RuntimeError("HMAC primitive was substituted")
    if (
        hashlib is not _IMPORTED_HASHLIB_MODULE
        or sys.modules.get("hashlib") is not _IMPORTED_HASHLIB_MODULE
        or hashlib.sha256 is not _SHA256
    ):
        raise RuntimeError("SHA-256 primitive was substituted")


def _validate_stage(value: Any) -> str:
    if type(value) is not str or value not in GATES:
        raise ValueError(f"unknown confirmation stage: {value!r}")
    return value


def _forbidden_seed_set(
    values: Iterable[Any], *, require_unique_history: bool = False
) -> set[int]:
    if isinstance(values, (str, bytes, bytearray)):
        raise ValueError("forbidden seeds must be an iterable of integers")
    try:
        iterator = iter(values)
    except TypeError as error:
        raise ValueError("forbidden seeds must be an iterable of integers") from error
    result = set(G5_SCREENING_STAGE_SEEDS)
    observed_history: set[int] = set()
    for value in iterator:
        if type(value) is not int or not 0 <= value <= DOTNET_RANDOM_SEED_MAX:
            raise ValueError("forbidden seed escaped nonnegative signed Int32")
        if require_unique_history and (
            value in observed_history or value in G5_SCREENING_STAGE_SEEDS
        ):
            raise ValueError("prior stage-seed history contains a collision")
        observed_history.add(value)
        result.add(value)
    return result


def _seed_hmac_message(attempt_index: int, stage: str, counter: int) -> bytes:
    return b"".join(
        (
            SEED_HMAC_DOMAIN,
            PROTOCOL_ID.encode("ascii"),
            b"\x00",
            struct.pack(">Q", attempt_index),
            b"\x00",
            stage.encode("ascii"),
            b"\x00",
            struct.pack(">I", counter),
        )
    )


def entropy_commitment(entropy: Any, attempt_index: Any) -> str:
    _require_unmodified_crypto_primitives()
    material = _validate_entropy(entropy)
    index = _validate_attempt_index(attempt_index)
    payload = b"".join(
        (
            ENTROPY_COMMITMENT_DOMAIN,
            PROTOCOL_ID.encode("ascii"),
            b"\x00",
            struct.pack(">Q", index),
            material,
        )
    )
    return _SHA256(payload).hexdigest()


def derive_stage_seed(
    entropy: Any,
    attempt_index: Any,
    stage: Any,
    forbidden_seeds: Iterable[Any],
) -> tuple[int, int]:
    """Derive one uniform nonnegative Int32 seed and rejection counter."""

    material = _validate_entropy(entropy)
    _require_unmodified_crypto_primitives()
    index = _validate_attempt_index(attempt_index)
    canonical_stage = _validate_stage(stage)
    forbidden = _forbidden_seed_set(forbidden_seeds)
    for counter in range(UINT32_MAX + 1):
        digest = _HMAC_NEW(
            material,
            _seed_hmac_message(index, canonical_stage, counter),
            _SHA256,
        ).digest()
        candidate = struct.unpack(">I", digest[:4])[0]
        if candidate <= DOTNET_RANDOM_SEED_MAX and candidate not in forbidden:
            return candidate, counter
    raise RuntimeError("stage-seed rejection counter exhausted")


def _draw_seed_bundle_with_source(
    attempt_index: Any,
    prior_attempt_seeds: Iterable[Any],
    entropy_source: Any,
) -> dict[str, Any]:
    index = _validate_attempt_index(attempt_index)
    prior = _forbidden_seed_set(
        prior_attempt_seeds, require_unique_history=True
    )
    if not callable(entropy_source):
        raise ValueError("entropy source must be callable")
    entropy = entropy_source(ENTROPY_BYTES)
    material = _validate_entropy(entropy)
    stage_seeds: dict[str, int] = {}
    counters: dict[str, int] = {}
    forbidden = set(prior)
    for stage in GATES:
        seed, counter = derive_stage_seed(material, index, stage, forbidden)
        stage_seeds[stage] = seed
        counters[stage] = counter
        forbidden.add(seed)
    return {
        "algorithmId": SEED_ALGORITHM_ID,
        "entropyCommitment": entropy_commitment(material, index),
        "stageSeeds": stage_seeds,
        "rejectionCounters": counters,
    }


def draw_seed_bundle(
    attempt_index: Any, prior_attempt_seeds: Iterable[Any]
) -> dict[str, Any]:
    """Draw once and derive the claim record; callers may never reroll k."""

    _require_unmodified_crypto_primitives()
    return _draw_seed_bundle_with_source(
        attempt_index, prior_attempt_seeds, _SECRETS_TOKEN_BYTES
    )


def validate_published_seed_bundle(
    value: Any, prior_attempt_seeds: Iterable[Any]
) -> dict[str, Any]:
    """Validate a published claim record without predicting hidden entropy."""

    record = mapping(value, "published seed derivation")
    if set(record) != {
        "algorithmId",
        "entropyCommitment",
        "stageSeeds",
        "rejectionCounters",
    }:
        raise ValueError("published seed derivation fields changed")
    if record.get("algorithmId") != SEED_ALGORITHM_ID:
        raise ValueError("published seed derivation algorithm changed")
    commitment = record.get("entropyCommitment")
    if type(commitment) is not str or HEX_256.fullmatch(commitment) is None:
        raise ValueError("entropy commitment must be lowercase SHA-256")
    seeds = mapping(record.get("stageSeeds"), "published stage seeds")
    counters = mapping(record.get("rejectionCounters"), "rejection counters")
    if set(seeds) != set(GATES) or set(counters) != set(GATES):
        raise ValueError("published seed stage inventory changed")
    forbidden = _forbidden_seed_set(
        prior_attempt_seeds, require_unique_history=True
    )
    normalized_seeds: dict[str, int] = {}
    normalized_counters: dict[str, int] = {}
    for stage in GATES:
        seed = seeds.get(stage)
        counter = counters.get(stage)
        if type(seed) is not int or not 0 <= seed <= DOTNET_RANDOM_SEED_MAX:
            raise ValueError(f"{stage} seed escaped nonnegative signed Int32")
        if seed in forbidden:
            raise ValueError(f"{stage} seed collides with excluded history")
        if type(counter) is not int or not 0 <= counter <= UINT32_MAX:
            raise ValueError(f"{stage} rejection counter escaped uint32")
        normalized_seeds[stage] = seed
        normalized_counters[stage] = counter
        forbidden.add(seed)
    return {
        "algorithmId": SEED_ALGORITHM_ID,
        "entropyCommitment": commitment,
        "stageSeeds": normalized_seeds,
        "rejectionCounters": normalized_counters,
    }


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
        "createdUtc": "2026-07-23T17:30:00Z",
        "status": "frozen replacement before any v1 or v2 candidate claim, pool, suite, match, or result access",
        "predecessorRetirement": {
            "predecessorProtocolId": "omega-nnue-open-confirmation-v1",
            "replacementReason": "v1 random-root fixed-movetime evidence did not by itself establish normal-start actual-clock practical superiority",
            "historicalCommit": V1_PREDECESSOR["historicalCommit"],
            "protocolJson": V1_PREDECESSOR["protocolJson"],
            "protocolTool": V1_PREDECESSOR["protocolTool"],
            "readiness": V1_PREDECESSOR["readiness"],
            "orchestrator": V1_PREDECESSOR["orchestrator"],
            "implementationSeal": V1_PREDECESSOR["implementationSeal"],
            "implementationSealCreatedUtc": "2026-07-23T17:11:56Z",
            "preClaim": True,
            "attemptReservations": 0,
            "candidateClaims": 0,
            "attemptsConsumed": 0,
            "poolsSuitesMatchesOrResults": 0,
            "alphaSpent": 0,
            "nextGlobalAttemptIndex": 1,
            "retirementSeal": "build-king-state-confirmation-v2/predecessor/v1-retirement.seal.json",
            "retirementSealMustProveV1RootSingleton": True,
            "retirementContinuouslyRevalidated": True,
            "v1ImplementationSealMayNotAuthorizeFutureWork": True,
        },
        "scope": {
            "generation5Role": "screening and exact candidate nomination only",
            "confirmationRole": "only this layer may authorize the distribution-explicit clearly-superior claim",
            "attemptsGlobalAcrossCandidates": True,
            "development": "safety screening only; never superiority evidence",
            "formalGates": list(FORMAL_GATES),
            "claimEstimand": "greater than +15 Elo separately on fresh phase-balanced equal-node roots, fresh phase-balanced fixed-movetime roots, and fresh normal-start-derived 60+1 clock openings",
        },
        "informationBoundary": {
            "protocolFrozenBeforeConfirmationPools": True,
            "protocolFrozenBeforeConfirmationResults": True,
            "suiteConstructionIsRulesOnly": True,
            "candidateIdentityUnavailableToPoolAndSuiteSelection": True,
            "privateHistoryProjectionPredatesAnyG5MatchLaunch": True,
            "privateHistoryProjectionPredatesAnyG5NominationResult": True,
            "privateProjectorAuditContainsSourcePathsIdentitiesAndManifests": True,
            "publicSelectorCapsuleContainsOnlyProtocolFreezeDigestCountAndOrbitFileIdentity": True,
            "selectorNeverReceivesPrivateProjectorAudit": True,
            "selectorRunsInSeparateOsProcess": True,
            "selectorReceivesOnlyPublicCapsuleOrbitProjectionAndStageSeeds": True,
            "selectorOsAccessProbeDeniesAllHistoryAndCandidateBearingRoots": True,
            "selectorWorkspaceInventoryExactBeforeAndAfterSampling": True,
            "selectorInjectedFilesDirectoriesAndLinksReject": True,
            "implementationSealPredatesCandidateClaim": True,
            "candidateClaimPredatesAllAttemptSampling": True,
            "candidateClaimIdentityUnavailableToSamplerAndSelector": True,
            "attemptSeedsUnavailableUntilExclusiveCandidateClaim": True,
            "samplerReceivesOnlySealedStageSeed": True,
            "samplerNeverReceivesCandidateClaimEntropyOrCommitment": True,
            "allFourSuitesSealedTogetherBeforeMatchAuthorization": True,
            "postSelectionFullHistoryAuditIsRejectionOnly": True,
            "postSelectionCollisionConsumesAttemptWithoutResamplingOrReselection": True,
            "postSelectionHistoryMembershipIsImmutable": True,
            "anyLaterHistoryMembershipChangeConsumesAttempt": True,
            "partialSelectorWorkspaceOrCrashConsumesAttemptWithoutRerun": True,
            "completeJointSealAndAuditResamplingIsByteIdempotent": True,
            "v1RetirementSealPredatesV2ImplementationSeal": True,
            "v2ImplementationSealPredatesAnyG5MatchLaunchOrNominationAccess": True,
            "matchResultsAccessedByProtocol": 0,
            "targetFieldsDecodedByProtocol": 0,
        },
        "namespaces": {
            "root": "build-king-state-confirmation-v2",
            "attemptDirectoryFormat": "attempt-{k:06d}",
            "attemptDirectoryMinimumDigits": 6,
            "attemptReservationFile": "attempt-reservation.json",
            "candidateClaimFile": "candidate-claim.json",
            "attemptClosureFile": "attempt-closure.json",
            "attemptChildren": [
                "post-selection-audit",
                "sealed",
                "development",
                "equal-node",
                "equal-time",
                "normal-start-clock",
            ],
            "privateProjectorAudit": "runtime/private-history-projector/projection.json",
            "publicSelectorCapsule": "runtime/public-selector-capsule/capsule.json",
            "publicExcludedOrbitProjection": "runtime/public-selector-capsule/excluded-orbits.txt",
            "selectorWorkspaceFormat": "runtime/selector-workspaces/attempt-{k:06d}",
            "selectorWorkspaceExactInventory": {
                "beforeSampling": ["sampling-intent.json"],
                "afterSamplingFiles": ["sampling-intent.json"],
                "afterSamplingDirectories": ["sampler", "sealed"],
                "samplerFilesPerGate": [
                    "{gate}-rules-only.jsonl",
                    "{gate}-rules-only.jsonl.manifest.json",
                    "{gate}-rules-only.jsonl.complete.seal.json",
                ],
                "sealedFilesPerGate": ["{gate}-suite.json"],
                "sealedJointFile": "joint-suite.seal.json",
                "unknownEntriesOrLinksAllowed": 0,
            },
            "predecessorDirectory": "predecessor",
            "predecessorRetirementFile": "v1-retirement.seal.json",
        },
        "attemptSequence": {
            "firstAttemptIndex": FIRST_ATTEMPT_INDEX,
            "maximumAdmissibleAttemptIndex": MAX_ATTEMPT_INDEX,
            "maximumAttemptIndexFromFormal512PairFeasibility": MAX_FEASIBLE_ATTEMPT_INDEX,
            "firstRejectedAttemptIndex": FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX,
            "firstRejectedAttemptReason": "statistical-cap-exhausted",
            "continuationAfterExhaustion": "requires a separately preregistered v3 with larger formal pair caps before any further attempt",
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
            "attemptReservationKind": "omega-nnue-open-confirmation-v2-attempt-reservation",
            "attemptReservationFieldInventory": [
                "schemaVersion",
                "kind",
                "protocol",
                "attemptIndex",
                "predecessorClosure",
                "createdUtc",
                "implementationSeal",
                "predecessorRetirement",
                "orchestrator",
                "g5Authorization",
                "g5Decisions",
                "g5Lineage",
                "g5Verifier",
                "g5Chronology",
                "requiredG5Decisions",
                "selectedNetwork",
                "selectedManifest",
                "engine",
                "candidateValidationComplete",
                "reservationConsumesAttempt",
                "status",
            ],
            "attemptReservationForbiddenFields": [
                "seedDerivation",
                "entropyCommitment",
                "stageSeeds",
                "rejectionCounters",
            ],
            "reservationPublishedUnderExclusiveGlobalAttemptLock": True,
            "reservationPublishedExclusivelyAfterCandidateValidationBeforeEntropyDraw": True,
            "reservationCreationConsumesAttempt": True,
            "reservationContainsNoSeedDerivation": True,
            "reservationCarriesProtocolIndexUtcImplementationPredecessorRetirementOrchestratorAndFullValidatedG5CandidateEvidence": True,
            "candidateClaimMustPinReservationIdentity": True,
            "candidateClaimReservationIdentityField": "attemptReservation",
            "candidateValidationCompletesBeforeEntropyDraw": True,
            "exclusiveAtomicClaimOperationRequired": True,
            "attemptDurablyConsumedBeforeEntropyDraw": True,
            "entropyDrawsPerAttemptIndex": 1,
            "noEntropyRerollOrAlternateClaimForSameAttempt": True,
            "postReservationCrashAbortDrawOrPublicationFailureConsumesAttempt": True,
            "failedClaimMustCloseConsumedAttemptBeforeNextIndex": True,
            "samplingFailureAfterClaimClosesAttemptAsSamplingFailure": True,
            "selectorPartialOutputMayNeverResumeOrRerun": True,
            "sealedAndPostAuditedSampleRepeatReturnsExistingSealWithoutMutation": True,
            "candidateClaimPublication": {
                "platform": "Windows NTFS",
                "staging": "delete-on-close temporary inode in the canonical attempt directory",
                "preCommit": "write complete canonical JSON, flush, then fsync the temporary file",
                "commit": "os.link no-replace hard-link creation at candidate-claim.json",
                "outcomesAfterAnyCrash": [
                    "canonical final name absent",
                    "canonical final name contains the complete fsynced claim",
                ],
                "partialFinalNamePermitted": False,
                "survivingStagingArtifactPermitted": False,
            },
        },
        "claimSeedDerivation": {
            "algorithmId": SEED_ALGORITHM_ID,
            "entropySource": "Python secrets.token_bytes backed by the operating-system CSPRNG",
            "entropyBytes": ENTROPY_BYTES,
            "entropyDrawTiming": "after candidate and predecessor validation, inside the same exclusive claim operation, after durable attempt consumption",
            "entropyDrawCount": "exactly one for each consumed attempt index",
            "rawEntropyPersistedOrPublished": False,
            "commitmentAlgorithm": "SHA-256",
            "commitmentDomainAsciiEscaped": "omega-nnue-confirmation-entropy-commitment-v2\\0",
            "commitmentMessageEncoding": "domain || protocolId ASCII || NUL || attemptIndex uint64 big-endian || 32-byte entropy",
            "hmacAlgorithm": "HMAC-SHA256",
            "hmacKey": "the single 32-byte claim entropy",
            "hmacDomainAsciiEscaped": "omega-nnue-confirmation-stage-seed-v2\\0",
            "hmacMessageEncoding": "domain || protocolId ASCII || NUL || attemptIndex uint64 big-endian || NUL || stage ASCII || NUL || rejectionCounter uint32 big-endian",
            "digestCandidateEncoding": "first four digest bytes as uint32 big-endian",
            "acceptedSeedDomain": "0 through 2147483647 inclusive (nonnegative signed Int32)",
            "rejectionRule": "reject candidate > 2147483647 or colliding with any forbidden seed; increment uint32 counter without drawing new entropy",
            "rejectionCounterStartsAt": 0,
            "rejectionCounterMaximum": UINT32_MAX,
            "stageOrder": list(GATES),
            "forbiddenSeedUnion": [
                "Generation-5 screening stage seeds 2026072308, 2026072309, 2026072310",
                "every published stage seed from every prior confirmation attempt or successor protocol",
                "earlier stage seeds derived for the current attempt",
            ],
            "candidateClaimField": "seedDerivation",
            "publishedFieldInventory": [
                "algorithmId",
                "entropyCommitment",
                "stageSeeds",
                "rejectionCounters",
            ],
            "publishedStageSeedKeys": list(GATES),
            "publishedRejectionCounterKeys": list(GATES),
            "candidateClaimPublishesCommitmentAndExactStageSeeds": True,
            "validatorPinsAlgorithmButNeverPredictsClaimSeedValues": True,
            "samplerInput": "only the authenticated sealed integer seed for its stage",
            "samplerForbiddenInputs": [
                "candidate identity",
                "candidate claim bytes",
                "raw entropy",
                "entropy commitment",
                "other-stage entropy material",
            ],
            "rerollPolicy": "no alternate entropy or seed bundle for the same attempt index under any failure mode",
            "claimWriterUsesFrozenStdlibCallableIdentities": True,
            "cryptographicPrimitiveSubstitutionMustAbortConsumedAttempt": True,
        },
        "attemptClosure": {
            "reservationIdentityRequired": True,
            "reservationMayLeadOnlyToExactClaimOrTerminalPrepublicationAbortClosure": True,
            "successfulCandidateClaimMustPinExactReservationIdentity": True,
            "candidateClaimRequiredExceptPrepublicationAbort": True,
            "candidateClaimNullableOnlyWhenOutcome": "aborted",
            "nullableCandidateClaimReason": "claim-publication-failure",
            "candidateNetworkIdentitySourceWhenClaimAbsent": "validated attempt reservation",
            "prepublicationAbortConsumesAttemptAndForbidsRetry": True,
            "nullableClosureRequiresCanonicalClaimFinalAbsent": True,
            "nullableClosureRequiresAllClaimStagingAbsent": True,
            "malformedOrPartialFinalClaimBlocksSuccessor": True,
            "nextAttemptRequiresAuthenticatedTerminalClosure": True,
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
            "noBonferroniForConjunction": "under the union null, success implies rejection of at least one true component null at beta_k",
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
            "normalStartClock": {
                "project": "tools/omega_nnue/OmegaPracticalOpeningSampler/OmegaPracticalOpeningSampler.csproj",
                "source": "tools/omega_nnue/OmegaPracticalOpeningSampler/Program.cs",
                "deterministicPrng": "SplitMix64",
                "trajectories": 8_192,
                "workers": 4,
                "officialInitialOfen": "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1",
                "targetPlies": [4, 8, 12, 16],
                "rootsPerTargetPly": 128,
                "roots": 512,
                "movePolicy": "uniform random permutation; first legal vertically colour-reflected full-move pair",
                "verticalColourReflection": {
                    "ordinarySquares": "file unchanged; rank r maps to 9-r",
                    "corners": {"w1": "w4", "w4": "w1", "w2": "w3", "w3": "w2"},
                },
                "minimumPieces": 37,
                "endpointMustBeOpeningPhase": True,
                "candidateInputs": 0,
                "engineEvaluationInputs": 0,
                "oneEndpointPerTrajectory": True,
            },
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
            "normal-start-clock": {
                "role": "formal-normal-start-practical-confirmation",
                "roots": 512,
                "rootsPerTargetPly": 128,
                "targetPlies": [4, 8, 12, 16],
                "mode": "clock",
                "initialTimeMs": 60_000,
                "incrementMs": 1_000,
                "searchTimeoutMs": 65_000,
                "initialPairBudget": 128,
                "resumePairBudget": 4,
                "minimumPairsBeforeDecision": 128,
                "maximumPairs": 512,
                "nullElo": 15,
                "promotionThresholdSource": "attempt alpha-spending log threshold",
                "futilityEValue": 20,
                "balancedBlockChecksOnly": True,
                "balancedBlockStrata": [4, 8, 12, 16],
                "oneGameAtATime": True,
                "idleMachineAttestationRequired": True,
                "zeroTimeForfeitsRequired": True,
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
            "normalStartClockBlock": "one pair from each 4,8,12,16-ply prefix stratum",
        },
        "freshness": {
            "selectedRootOrbitIntersectionMustBeZero": True,
            "crossStageSelectedRootOrbitsDisjoint": True,
            "freshRulesOnlyPoolPerStageAndAttempt": True,
            "normalStartEndpointOrbitsMustBeFresh": True,
            "normalStartProperPrefixAncestorsMayOverlapHistory": True,
            "properPrefixOverlapReason": "the official start and ordinary opening ancestors are necessarily shared; only candidate-blind selected endpoints contribute formal evidence",
            "baseProjectionSourceInventoryIsPrivateFromSelector": True,
            "baseProjectionBindsOriginalNineGeneration5SamplerArtifactsExactly": True,
            "baseProjectionRequiresAdditiveG5PreregistrationAndCandidateBlindSuiteSeal": True,
            "baseProjectionRequiresAdditiveG5AuthorizationEventsAssessmentsDecisionsAndClosureAbsent": True,
            "baseProjectionBindsPublishedAdditiveG5PreauthorizationState": True,
            "selectorCapsuleHasNoSourcePathsSourceIdentitiesOrManifests": True,
            "currentSelectorWorkspaceExcludedFromItsOwnPostSelectionScan": True,
            "allPriorSelectorWorkspacesIncludedInLaterScans": True,
            "postSelectionAuditMayAcceptOrAbortButNeverRerank": True,
            "postSelectionAuditRequiresCanonicalCompatibilityPositionHistoryProjectionAndManifest": True,
            "everyCanonicalCompatibilityPositionHistoryOrbitMustAppearInExcludedOrbitProjection": True,
            "collisionAction": "abort and consume k without resampling or reselection",
            "historyMembershipChangeAfterAuditAction": "invalidate and consume k, whether colliding or noncolliding",
            "excludedUnion": [
                "all frozen NNUE training, validation, held-out, screening, and confirmation history",
                "the complete Generation-5 decision corpus and source pool",
                "all Generation-5 raw match sampler pools and sealed suites",
                "all played Generation-5 match event, PV, and final positions",
                "all additive Generation-5 compatibility raw events, replay evidence, PVs, assessments, decisions, and final positions",
                "all prior open-confirmation pools, suites, events, PVs, and final positions",
            ],
            "futureTrainingMustExcludeAllConfirmationPositionArtifacts": True,
        },
        "nomination": {
            "candidateClaimPublishedBeforeAnyAttemptSampling": True,
            "candidateClaimRequiresPriorImplementationSeal": True,
            "candidateClaimRequiresPreG5PrivateProjectionAndImplementationSeal": True,
            "candidateInputsValidatedBeforeExclusiveEntropyDraw": True,
            "candidateClaimMustPublishSeedDerivationRecord": True,
            "candidateClaimSeedDerivationImmutable": True,
            "candidateClaimActivatesAndConsumesAttempt": True,
            "candidateClaimUnavailableToSamplerAndSelector": True,
            "matchAuthorizationOccursOnlyAfterSuiteSeal": True,
            "exactExecutableIdentityRequired": True,
            "exactNetworkIdentityRequired": True,
            "exactManifestAndScreeningLineageRequired": True,
            "candidateMustEqualSuccessfulScreenNominee": True,
            "nominationAuthorityIsAdditiveGeneration5CompatibilityClosure": True,
            "originalGeneration5ProfileFreezeToolsAndNineSamplerArtifactsRemainImmutableAncestors": True,
            "compatibilityPreauthorizationPredatesPrivateProjection": True,
            "privateProjectionPredatesImplementationSeal": True,
            "compatibilityAuthorizationAndFirstLaunchPostdateImplementationSeal": True,
            "compatibilityClosurePostdatesAllThreeSuccessfulDecisions": True,
            "candidateClaimPostdatesCompatibilityClosure": True,
            "compatibilityNamespaceInventoryBoundIntoClaimChronology": True,
            "compatibilityClosureBoundPositionHistoryProjectionAndManifestRequired": True,
            "g5LineageRequiredIdentities": [
                "compatibility template, protocol, readiness, matches, preregistration, preauthorization, suite seal, core seal, closure, position-history projection, and position-history manifest",
                "offline report and selected manifest",
                "original profile, final freeze, match protocol, readiness, core, and orchestrator",
                "all nine original development, equal-node, and equal-time sampler source/manifest/completion artifacts",
            ],
            "runnerUpFallback": False,
            "sameExecutableForCandidateAndControl": True,
            "sameNomineeRequiredAtAllFourStages": True,
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
            "normalStartClockRequiresEqualTimePromotion": True,
            "normalStartClockIdleAttestationMustPostdateEqualTimePromotion": True,
            "normalStartClockAuditRequiresExactClockCommandsDeltasAndSerializedIntervals": True,
            "rawEmitterSchemaAuthenticationRequiredForEveryGate": True,
            "rawEmitterColorTokens": ["white", "black"],
            "rawEmitterColorTokenComparison": "exact lowercase, case-sensitive",
            "assessmentColorAdapter": {
                "transformVersion": "omega-match-color-white-black-to-w-b-v1",
                "white": "w",
                "black": "b",
                "onlyFieldAllowedToChange": "Color on ply records",
                "rawAndDerivedDigestsRequired": True,
                "rawEvidenceRetained": True,
            },
            "rawEmitterSuccessfulPlyContract": {
                "bestMove": "canonical Omega coordinate move and equal to Search.BestMove",
                "san": "one nonempty unpadded printable-ASCII token without whitespace or controls",
                "postOfenRequired": True,
                "processExited": False,
                "deadlineExceeded": False,
                "pvValidationRequired": True,
                "gameResultIllegalPvsEqualsCountOfFalsePvValidation": True,
            },
            "rawEmitterSearchContract": {
                "commandMustExactlyMatchSealedNodesMovetimeOrClockMode": True,
                "wallTimeFiniteAndNonnegative": True,
                "infoRawOutputAndStandardErrorTypedLists": True,
                "successfulSearchBestMoveRequired": True,
            },
            "managedRulesReplay": {
                "contract": "ChessLib legal transcript replay",
                "allSuccessfulPliesReplayedFromAuthenticatedPreOfen": True,
                "replaySuccessfulPlyCountMustEqualScheduleAuthentication": True,
                "everyPostOfenMustEqualManagedReplay": True,
                "helperBundleSha256": PREFIX_REPLAY_BUNDLE_SHA256,
                "rulesBytes": G5_RULES_BYTES,
                "rulesSha256": G5_RULES_SHA256,
                "rulesMustBeByteEqualFrozenOmegaMatchChessLib": True,
                "dotnetHostMustEqualAuthorizedFrozenHost": True,
                "fabricatedPostOfenRejects": True,
            },
            "assessmentTerminalAdapter": {
                "transformVersion": "omega-match-process-exit-terminal-error-v1",
                "acceptedRawShape": "terminal failed-search ply with absent/null Error, Search.ProcessExited true, absent/null PostOfen, and decisive safety termination",
                "derivedError": "Authenticated OmegaMatch process exit (derived for frozen assessor compatibility).",
                "onlyFieldAllowedToChange": "Error on authenticated process-exit terminal ply",
                "rawAndDerivedDigestsRequired": True,
                "rawEvidenceRetained": True,
            },
            "assessmentViews": {
                "preparedCompatibilityViewChangedFields": ["Color", "Error"],
                "unauthorizedFieldsChanged": 0,
                "frozenCoreInput": "color-and-terminal-derived compatibility events",
                "normalStartPracticalInput": "authenticated raw events",
                "rulesReplayAlwaysUsesAuthenticatedRawEvents": True,
            },
            "everyStartMustMatchExactSealedOpeningAndSchedule": True,
            "everyResultAndPlyMustLinkToItsExactStart": True,
            "resultPlyCoverageMustBeExact": True,
            "terminalFailedPlyRequiresNonemptyErrorOrAuthenticatedProcessExitedTrue": True,
            "terminalFailedPlyRequiresAbsentPostOfenAndDecisiveSafetyTermination": True,
            "runEngineInventoryRequiresExactlyTwoUniqueObjectEntries": True,
            "runEngineEffectiveOptionsMustMatchExactly": True,
            "candidateExternalAssetMustBeExactPathBoundSingleton": True,
            "candidateAndControlExecutableWorkingDirectoryAndFileIdentityMustMatch": True,
            "candidateStartupDiagnosticsMustBindLoadedNetworkAndFinalActiveState": True,
            "controlStartupDiagnosticsMustForbidLoadedOrActiveNnueAndEndHandcrafted": True,
        },
        "promotionRule": {
            "developmentPassRequired": True,
            "equalNodePromotionRequired": True,
            "equalTimePromotionRequired": True,
            "normalStartClockPromotionRequired": True,
            "allFormalGatesUseAttemptBeta": True,
            "allFormalGatesMustPromoteInSameAttempt": True,
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
            "role": "original experiment is immutable screening ancestry; only the additive compatibility closure may nominate",
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
            "originalAuthority": {
                "immutable": True,
                "namespace": "build-king-state-v5/matches",
                "identities": {
                    "profile": _pin(
                        "validation/omega-nnue-king-state-v5-preregistration.json"
                    ),
                    "finalFreeze": _pin(
                        "validation/omega-nnue-king-state-v5-freeze.seal.json"
                    ),
                    "matchProtocol": _pin(
                        "validation/omega-nnue-king-state-v5-match-protocol.json"
                    ),
                    "matchReadiness": _pin(
                        "tools/omega_nnue/king_state_match_readiness_generation5.py"
                    ),
                    "matchCore": _pin(
                        "tools/omega_nnue/king_state_matches.py"
                    ),
                    "matchOrchestrator": _pin(
                        "tools/omega_nnue/king_state_matches_generation5.py"
                    ),
                },
                "samplerArtifacts": {
                    gate: {
                        kind: _pin(
                            "build-king-state-v5/matches/sampler/"
                            f"king-state-v5-{gate}-rules-only.jsonl{suffix}"
                        )
                        for kind, suffix in (
                            ("source", ""),
                            ("manifest", ".manifest.json"),
                            ("completionSeal", ".complete.seal.json"),
                        )
                    }
                    for gate in ("development", "equal-node", "equal-time")
                },
            },
            "compatibilityAuthority": {
                "compatibilityId": "king-state-v5-color-compat-v2",
                "namespace": "build-king-state-v5/matches-color-compat-v2",
                "positionHistoryProjection": "build-king-state-v5/matches-color-compat-v2/position-history.jsonl",
                "positionHistoryManifest": "build-king-state-v5/matches-color-compat-v2/position-history.manifest.json",
                "preauthorizationMustPrecedeBaseProjection": True,
                "authorizationAndFirstLaunchMustPostdateImplementationSeal": True,
                "closureMustPostdateAllThreeSuccessfulDecisions": True,
                "freshIsolatedVerificationRequiredAtNomination": True,
                "identities": {
                    "template": _pin(
                        "validation/omega-nnue-king-state-v5-color-compat-preregistration.template.json"
                    ),
                    "protocol": _pin(
                        "validation/omega-nnue-king-state-v5-color-compat-protocol.json"
                    ),
                    "readiness": _pin(
                        "tools/omega_nnue/king_state_match_readiness_generation5_compat_v2.py"
                    ),
                    "matches": _pin(
                        "tools/omega_nnue/king_state_matches_generation5_compat_v2.py"
                    ),
                },
                "requiredClosure": {
                    "clearlySuperior": True,
                    "development": "pass",
                    "equal-node": "promote",
                    "equal-time": "promote",
                    "originalArtifactsRewritten": 0,
                },
            },
        },
        "sharedRuntime": {
            "dotnetRuntimeVersion": "10.0.9",
            "dotnetRuntimeBundleSha256": DOTNET_RUNTIME_BUNDLE_SHA256,
            "omegaMatchBundleSha256": OMEGA_MATCH_BUNDLE_SHA256,
            "rootSamplerBundleSha256": ROOT_SAMPLER_BUNDLE_SHA256,
            "prefixReplayBundleSha256": PREFIX_REPLAY_BUNDLE_SHA256,
            "prefixReplayRulesByteEqualOmegaMatch": True,
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
                "omegaMatchRulesAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5/omegamatch/ChessLib.dll"
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
                "prefixReplayRulesAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/ChessLib.dll"
                ),
                "prefixReplayRulesSymbols": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/ChessLib.pdb"
                ),
                "prefixReplayJsonAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/Newtonsoft.Json.dll"
                ),
                "prefixReplayDeps": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.deps.json"
                ),
                "prefixReplayAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.dll"
                ),
                "prefixReplayAppHost": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.exe"
                ),
                "prefixReplayRuntimeConfig": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/OmegaOpeningPrefixReplay.runtimeconfig.json"
                ),
                "prefixReplayPortsAssembly": _pin(
                    "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay/System.IO.Ports.dll"
                ),
            },
        },
        "implementation": {
            "protocolTool": _tool_pin(),
            "v1RetirementSealRequiredBeforeImplementationSeal": True,
            "v1RetirementContinuouslyRevalidated": True,
            "privateProjectionAndImplementationSealRequiredBeforeAnyG5MatchLaunch": True,
            "privateProjectionAndImplementationSealRequiredBeforeAnyG5NominationResultAccess": True,
            "sealedCleanWorkerAcceptsNoClaimOrCandidateInputs": True,
            "sealedCleanWorkerAcceptsNoPrivateHistoryProjection": True,
            "sealedCleanWorkerInputs": [
                "public selector capsule",
                "public canonical excluded-orbit list",
                "claim-derived stage seeds without claim identity",
                "dedicated selector output workspace"
            ],
            "selectorCapsuleRejectsCandidateFieldsHiddenPathsAndUnknownKeys": True,
            "selectorWorkspaceRejectsEveryUnknownEntryAndLink": True,
            "selectorCrashOrPartialOutputPublishesSamplingFailureClosure": True,
            "selectorCompleteRepeatIsReadOnlyAndIdempotent": True,
            "postSelectionAuditPublishesBeforeCollisionAbort": True,
            "postSelectionAuditNeverTriggersSelectorRerun": True,
            "samplerSelectorSealMustPinCompleteTransitiveSourceAndRuntimeClosure": True,
            "exactGeneration5PrefixReplayBundleRequired": True,
            "prefixReplayRulesMustByteMatchFrozenOmegaMatchChessLib": True,
            "deterministicCandidateInvarianceProofRequiredBeforeG5Launch": True,
            "claimWriterMustUsePinnedDrawSeedBundleExactlyOnce": True,
            "claimWriterMustAuthenticateAllPriorPublishedStageSeeds": True,
            "claimWriterMustNeverRetryEntropyForConsumedAttempt": True,
            "readinessAndOrchestratorMustBePinnedBeforeCandidateClaim": True,
            "implementationSealRequiredBeforeCandidateClaim": True,
            "implementationSealUnavailableToCandidateAwareMutation": True,
            "plannedReadinessPath": "tools/omega_nnue/king_state_confirmation_readiness_v2.py",
            "plannedOrchestratorPath": "tools/omega_nnue/king_state_confirmation_matches_v2.py",
            "plannedPracticalModulePath": "tools/omega_nnue/king_state_confirmation_practical_v2.py",
            "plannedPracticalSamplerProject": "tools/omega_nnue/OmegaPracticalOpeningSampler/OmegaPracticalOpeningSampler.csproj",
            "plannedPracticalSamplerSource": "tools/omega_nnue/OmegaPracticalOpeningSampler/Program.cs",
            "subprocessEnvironment": {
                "policyVersion": 1,
                "construction": "new empty mapping populated only with the listed keys",
                "allowedKeys": [
                    "SystemRoot",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "DOTNET_CLI_TELEMETRY_OPTOUT",
                    "DOTNET_NOLOGO",
                    "DOTNET_MULTILEVEL_LOOKUP",
                    "DOTNET_ROOT",
                    "DOTNET_ROOT_X64",
                    "DOTNET_ROLL_FORWARD",
                    "DOTNET_EnableDiagnostics",
                    "PYTHONNOUSERSITE",
                ],
                "inheritedParentKeys": [],
                "systemRootSource": "Win32 GetWindowsDirectoryW; no environment lookup",
                "temporaryDirectorySource": "authenticated global-operation-lock parent",
                "dotnetRuntimeSource": "authenticated frozen-runtime directory",
                "allOtherParentVariablesRemoved": True,
                "candidateClaimNetworkScoreResultTargetOrSecretVariablesAccepted": 0,
            },
            "sharedG5PinnedFilesMayNotBeModified": True,
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
    runtime_root = protocol_path(
        runtime.get("root"), ".NET runtime manifest root"
    )
    if (
        runtime.get("runtimeVersion") != shared.get("dotnetRuntimeVersion")
        or runtime.get("bundleSha256") != shared.get("dotnetRuntimeBundleSha256")
        or runtime_root != resolve(dotnet_runtime.RUNTIME_ROOT)
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
    prefix = _runtime_bundle_identity(
        REPO
        / "tools/omega_nnue/frozen_runtime/king-state-v5-color-compat-v2/opening-replay",
        apphost_name="OmegaOpeningPrefixReplay.exe",
        assembly_name="OmegaOpeningPrefixReplay.dll",
    )
    if prefix["sha256"] != shared.get("prefixReplayBundleSha256"):
        raise ValueError("frozen exact-Generation-5 prefix replay bundle changed")
    omega_rules = mapping(
        identities.get("omegaMatchRulesAssembly"), "OmegaMatch rules identity"
    )
    replay_rules = mapping(
        identities.get("prefixReplayRulesAssembly"), "prefix replay rules identity"
    )
    if (
        omega_rules.get("bytes") != G5_RULES_BYTES
        or omega_rules.get("sha256") != G5_RULES_SHA256
        or replay_rules.get("bytes") != omega_rules.get("bytes")
        or replay_rules.get("sha256") != omega_rules.get("sha256")
        or shared.get("prefixReplayRulesByteEqualOmegaMatch") is not True
    ):
        raise ValueError("prefix replay rules differ from frozen OmegaMatch ChessLib")


def _verify_entropy_contract(protocol: Mapping[str, Any]) -> None:
    sequence = mapping(protocol.get("attemptSequence"), "attempt sequence")
    derivation = mapping(protocol.get("claimSeedDerivation"), "claim seed derivation")
    admissible_maximum = exact_int(
        sequence.get("maximumAdmissibleAttemptIndex"),
        "maximum admissible attempt index",
        minimum=1,
    )
    if admissible_maximum != MAX_ATTEMPT_INDEX:
        raise ValueError("maximum admissible attempt index changed")
    if derivation.get("algorithmId") != SEED_ALGORITHM_ID:
        raise ValueError("claim seed algorithm changed")
    if derivation.get("entropyBytes") != ENTROPY_BYTES:
        raise ValueError("claim entropy length changed")
    if derivation.get("rejectionCounterMaximum") != UINT32_MAX:
        raise ValueError("claim rejection counter domain changed")
    require_exact_json(derivation.get("stageOrder"), list(GATES), "seed stage order")
    if any(
        field in sequence
        for field in (
            "baseSeedFormula",
            "baseSeedAtAttempt1",
            "baseSeedStride",
            "stageSeedOffsets",
            "attempt1StageSeeds",
        )
    ):
        raise ValueError("protocol predicts an open-confirmation stage seed")


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
    predecessor = mapping(
        value.get("predecessorRetirement"), "predecessor retirement"
    )
    if predecessor.get("preClaim") is not True:
        raise ValueError("predecessor retirement is not pre-claim")
    if any(
        predecessor.get(field) != 0
        for field in (
            "attemptReservations",
            "candidateClaims",
            "attemptsConsumed",
            "poolsSuitesMatchesOrResults",
            "alphaSpent",
        )
    ):
        raise ValueError("predecessor retirement consumed confirmation evidence")
    if predecessor.get("nextGlobalAttemptIndex") != FIRST_ATTEMPT_INDEX:
        raise ValueError("v2 does not begin at the unspent global attempt index")
    if protocol_path(
        predecessor.get("retirementSeal"), "predecessor retirement seal"
    ) != resolve(V1_RETIREMENT_SEAL):
        raise ValueError("predecessor retirement seal path changed")

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
    _verify_entropy_contract(value)
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
    original_cwd = Path.cwd()
    try:
        os.chdir(TOOL_PATH.parent)
        validate_protocol()
    finally:
        os.chdir(original_cwd)

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

    # Claim-time entropy derivation, collision rejection, and namespaces.
    for attempt in range(1, MAX_ATTEMPT_INDEX + 1):
        name = attempt_directory_name(attempt)
        if parse_attempt_directory_name(name) != attempt:
            raise AssertionError("attempt directory round trip changed")
        if attempt_namespace(attempt).parent != resolve(ARTIFACT_ROOT):
            raise AssertionError("attempt namespace escaped its root")
    if MAX_ATTEMPT_INDEX != 377:
        raise AssertionError("statistical feasibility attempt boundary changed")
    fixture_entropy = bytes(range(ENTROPY_BYTES))
    fixture_bundle = {
        "algorithmId": SEED_ALGORITHM_ID,
        "entropyCommitment": "ce3db5a2e5c6c84f6c5d95333e5d8ddaaa7748af25305f7b70fa16e310142fc1",
        "stageSeeds": {
            "development": 1_228_744_267,
            "equal-node": 988_680_931,
            "equal-time": 334_760_342,
            "normal-start-clock": 1_311_040_145,
        },
        "rejectionCounters": {
            "development": 3,
            "equal-node": 4,
            "equal-time": 0,
            "normal-start-clock": 1,
        },
    }
    if entropy_commitment(fixture_entropy, 1) != fixture_bundle["entropyCommitment"]:
        raise AssertionError("entropy commitment test vector changed")
    forbidden: set[int] = set()
    derived_seeds: dict[str, int] = {}
    derived_counters: dict[str, int] = {}
    for stage in GATES:
        seed, counter = derive_stage_seed(fixture_entropy, 1, stage, forbidden)
        derived_seeds[stage] = seed
        derived_counters[stage] = counter
        forbidden.add(seed)
    if derived_seeds != fixture_bundle["stageSeeds"] or derived_counters != fixture_bundle[
        "rejectionCounters"
    ]:
        raise AssertionError("HMAC/rejection derivation test vector changed")
    if validate_published_seed_bundle(fixture_bundle, ()) != fixture_bundle:
        raise AssertionError("published seed bundle normalization changed")
    collision_seed, collision_counter = derive_stage_seed(
        fixture_entropy, 1, "development", {fixture_bundle["stageSeeds"]["development"]}
    )
    if (collision_seed, collision_counter) != (1_575_567_648, 7):
        raise AssertionError("forbidden-seed rejection changed")
    if entropy_commitment(fixture_entropy, 2) == fixture_bundle["entropyCommitment"]:
        raise AssertionError("entropy commitment lacks attempt separation")
    if derive_stage_seed(fixture_entropy, 2, "development", ())[0] == fixture_bundle[
        "stageSeeds"
    ]["development"]:
        raise AssertionError("stage seed lacks attempt separation")

    draw_calls: list[int] = []

    def fixed_token_bytes(length: int) -> bytes:
        draw_calls.append(length)
        return fixture_entropy

    drawn = _draw_seed_bundle_with_source(
        1, {fixture_bundle["stageSeeds"]["development"]}, fixed_token_bytes
    )
    if draw_calls != [ENTROPY_BYTES]:
        raise AssertionError("claim entropy was drawn more than once")
    if drawn["stageSeeds"]["development"] != collision_seed:
        raise AssertionError("collision caused an entropy reroll instead of counter advance")
    validate_published_seed_bundle(
        drawn, {fixture_bundle["stageSeeds"]["development"]}
    )

    def malformed_token_bytes(length: int) -> bytes:
        draw_calls.append(length)
        return b"x" * (ENTROPY_BYTES - 1)

    draw_calls.clear()
    try:
        _draw_seed_bundle_with_source(1, (), malformed_token_bytes)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed CSPRNG output was accepted")
    if draw_calls != [ENTROPY_BYTES]:
        raise AssertionError("malformed entropy triggered an impermissible reroll")

    # Published-record validation authenticates structure/collisions but never
    # predicts hidden claim entropy or invokes the CSPRNG.
    original_token_bytes = secrets.token_bytes
    substituted_calls: list[int] = []

    def substituted_token_bytes(length: int) -> bytes:
        substituted_calls.append(length)
        return fixture_entropy

    try:
        secrets.token_bytes = substituted_token_bytes
        try:
            draw_seed_bundle(1, ())
        except RuntimeError:
            pass
        else:
            raise AssertionError("substituted CSPRNG primitive was accepted")
    finally:
        secrets.token_bytes = original_token_bytes
    if substituted_calls:
        raise AssertionError("substituted CSPRNG primitive was invoked")
    original_sysrand = secrets._sysrand
    try:
        secrets._sysrand = object()
        try:
            draw_seed_bundle(1, ())
        except RuntimeError:
            pass
        else:
            raise AssertionError("substituted SystemRandom instance was accepted")
    finally:
        secrets._sysrand = original_sysrand

    try:
        secrets.token_bytes = lambda length: (_ for _ in ()).throw(
            AssertionError("validator attempted to predict claim entropy")
        )
        validate_published_seed_bundle(fixture_bundle, ())
    finally:
        secrets.token_bytes = original_token_bytes
    original_hmac_new = hmac.new
    try:
        hmac.new = lambda *args, **kwargs: None
        try:
            derive_stage_seed(fixture_entropy, 1, "development", ())
        except RuntimeError:
            pass
        else:
            raise AssertionError("substituted HMAC primitive was accepted")
    finally:
        hmac.new = original_hmac_new
    for malformed_entropy in (b"", b"x" * 31, b"x" * 33, bytearray(32), True):
        try:
            entropy_commitment(malformed_entropy, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed entropy structure was accepted")
    collision_record = copy.deepcopy(fixture_bundle)
    collision_record["stageSeeds"]["development"] = G5_SCREENING_STAGE_SEEDS[0]
    try:
        validate_published_seed_bundle(collision_record, ())
    except ValueError:
        pass
    else:
        raise AssertionError("Generation-5 seed collision was accepted")
    duplicate_record = copy.deepcopy(fixture_bundle)
    duplicate_record["stageSeeds"]["equal-node"] = duplicate_record["stageSeeds"][
        "development"
    ]
    try:
        validate_published_seed_bundle(duplicate_record, ())
    except ValueError:
        pass
    else:
        raise AssertionError("same-attempt stage-seed collision was accepted")
    for malformed_history in (
        [123_456, 123_456],
        [G5_SCREENING_STAGE_SEEDS[0]],
        None,
    ):
        try:
            validate_published_seed_bundle(fixture_bundle, malformed_history)
        except ValueError:
            pass
        else:
            raise AssertionError("colliding prior seed history was accepted")
    try:
        _validate_attempt_index(FIRST_STATISTICALLY_EXHAUSTED_ATTEMPT_INDEX)
    except ValueError as error:
        if "statistical-cap-exhausted" not in str(error):
            raise AssertionError("attempt exhaustion reason changed") from error
    else:
        raise AssertionError("statistically exhausted attempt was admitted")
    for invalid in (0, -1, 1.0, True, MAX_ATTEMPT_INDEX + 1):
        try:
            _validate_attempt_index(invalid)
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
                "predecessor attempt continuity",
                lambda value: value["predecessorRetirement"].__setitem__(
                    "nextGlobalAttemptIndex", 2
                ),
            ),
            (
                "predecessor singleton proof",
                lambda value: value["predecessorRetirement"].__setitem__(
                    "retirementSealMustProveV1RootSingleton", False
                ),
            ),
            (
                "normal-start actual clock mode",
                lambda value: value["stages"]["normal-start-clock"].__setitem__(
                    "mode", "moveTime"
                ),
            ),
            (
                "normal-start balanced prefix strata",
                lambda value: value["stages"]["normal-start-clock"].__setitem__(
                    "targetPlies", [4, 8, 12]
                ),
            ),
            (
                "conjunction alpha allocation",
                lambda value: value["alphaSpending"].__setitem__(
                    "noBonferroniForConjunction", "removed"
                ),
            ),
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
                "claim seed algorithm",
                lambda value: value["claimSeedDerivation"].__setitem__(
                    "algorithmId", "sha256-counter-v1"
                ),
            ),
            (
                "public fixed seed leakage",
                lambda value: value["attemptSequence"].__setitem__(
                    "attempt1StageSeeds", [1, 2, 3]
                ),
            ),
            (
                "claim entropy draw count",
                lambda value: value["attemptSequence"].__setitem__(
                    "entropyDrawsPerAttemptIndex", 2
                ),
            ),
            (
                "pre-draw reservation consumption",
                lambda value: value["attemptSequence"].__setitem__(
                    "reservationCreationConsumesAttempt", False
                ),
            ),
            (
                "exclusive global reservation lock",
                lambda value: value["attemptSequence"].__setitem__(
                    "reservationPublishedUnderExclusiveGlobalAttemptLock", False
                ),
            ),
            (
                "reservation seed leakage",
                lambda value: value["attemptSequence"].__setitem__(
                    "reservationContainsNoSeedDerivation", False
                ),
            ),
            (
                "claim entropy reroll",
                lambda value: value["attemptSequence"].__setitem__(
                    "noEntropyRerollOrAlternateClaimForSameAttempt", False
                ),
            ),
            (
                "claim entropy timing",
                lambda value: value["attemptSequence"].__setitem__(
                    "candidateValidationCompletesBeforeEntropyDraw", False
                ),
            ),
            (
                "sampler entropy isolation",
                lambda value: value["claimSeedDerivation"][
                    "samplerForbiddenInputs"
                ].pop(),
            ),
            (
                "seed collision universe",
                lambda value: value["claimSeedDerivation"][
                    "forbiddenSeedUnion"
                ].pop(),
            ),
            (
                "validator seed unpredictability",
                lambda value: value["claimSeedDerivation"].__setitem__(
                    "validatorPinsAlgorithmButNeverPredictsClaimSeedValues", False
                ),
            ),
            (
                "cryptographic primitive substitution",
                lambda value: value["claimSeedDerivation"].__setitem__(
                    "claimWriterUsesFrozenStdlibCallableIdentities", False
                ),
            ),
            (
                "prepublication abort closure",
                lambda value: value["attemptClosure"].__setitem__(
                    "nullableCandidateClaimReason", "retryable-write-failure"
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
                    "allFormalGatesMustPromoteInSameAttempt", False
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
                "pre-G5 private history projection",
                lambda value: value["informationBoundary"].__setitem__(
                    "privateHistoryProjectionPredatesAnyG5MatchLaunch", False
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
                "compatibility closure authority",
                lambda value: value["g5Screening"]["compatibilityAuthority"][
                    "requiredClosure"
                ].__setitem__("clearlySuperior", False),
            ),
            (
                "compatibility match verifier identity",
                lambda value: value["g5Screening"]["compatibilityAuthority"][
                    "identities"
                ]["matches"].__setitem__("sha256", "0" * 64),
            ),
            (
                "compatibility position-history exclusion",
                lambda value: value["freshness"].__setitem__(
                    "everyCanonicalCompatibilityPositionHistoryOrbitMustAppearInExcludedOrbitProjection",
                    False,
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
        raise SystemExit("usage: king_state_confirmation_protocol_v2.py [verify|self-test]")
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
