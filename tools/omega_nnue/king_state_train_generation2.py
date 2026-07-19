#!/usr/bin/env python3
"""Run protocol generation 2 on the fresh deep-HCE-v3 corpus.

The original K0/K1/K2 recipes and every numeric gate remain unchanged.
Generation 2 makes both declared corrections before its teacher labels exist:

* cross-phase leakage components are split globally and bootstrapped by their
  exact phase-incidence set;
* the planned float checkpoint is the deployment-equivalent canonical view
  produced by ``train_canonical.py``, while the optimizer shadow is retained.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence
import math
import subprocess
import sys

import king_state_train as sealed
import king_state_train_amended as phase_incidence
import king_state_v2 as prelabel
import train_canonical as canonical


REPO = sealed._repo_root()
DATA_DIR = REPO / "build-msvc" / "data-generation" / "deep-hce-v3"
OUTPUT_DIR = REPO / "build-msvc" / "king-state-v2"
CANONICAL_TRAINER = Path(__file__).with_name("train_canonical.py").resolve()
SEALED_TRAINER = Path(sealed.trainer.__file__).resolve()
PRELABEL_SEALER = Path(prelabel.__file__).resolve()
PHASE_AMENDMENT = prelabel._phase_amendment_path(REPO)
GENERATION2_AMENDMENT = prelabel._generation2_amendment_path(REPO)
PROFILE_PATH = prelabel._profile_path(REPO)


def _generation2_record() -> dict[str, Any]:
    return {
        "protocolGeneration": 2,
        "dataProfile": "deep-hce-v3",
        "profileId": prelabel.PROFILE_ID,
        "amendmentsEffectiveBeforeTeacherLabels": True,
        "candidateRecipesChanged": False,
        "promotionThresholdsChanged": False,
        "healthMetricSubject": "deployment-equivalent float checkpoint",
        "optimizerShadowPreserved": True,
        "deploymentFloatRequantizesByteIdentically": True,
        "heldOutTargetsDecoded": False,
    }


def _expected_plan_source_identities() -> dict[str, dict[str, Any]]:
    return {
        "trainer": sealed._identity(CANONICAL_TRAINER),
        "sealedTrainer": sealed._identity(SEALED_TRAINER),
        "orchestrator": sealed._identity(Path(__file__).resolve()),
        "phaseIncidenceImplementation": sealed._identity(
            Path(phase_incidence.__file__).resolve()
        ),
        "prelabelSealer": sealed._identity(PRELABEL_SEALER),
        "phaseIncidenceAmendment": sealed._identity(PHASE_AMENDMENT),
        "generation2Amendment": sealed._identity(GENERATION2_AMENDMENT),
        "v2Preregistration": sealed._identity(PROFILE_PATH),
    }


def _verify_generation2_plan_contract(
    plan: Mapping[str, Any], plan_path: Path
) -> None:
    identities = sealed._mapping(plan.get("identities"), "plan identities")
    for name, expected in _expected_plan_source_identities().items():
        if name not in identities:
            raise ValueError(
                f"generation-2 plan omits required identity {name}"
            )
        _same_pin(
            identities[name],
            expected,
            f"generation-2 plan identity {name}",
        )
    sealed._expect(
        sealed._mapping(plan.get("generation2"), "plan generation2"),
        _generation2_record(),
        "generation-2 plan contract",
    )
    canonical_data_paths = {
        "prelabelSeal": DATA_DIR / "king-state-v1-prelabel.seal.json",
        "corpus": DATA_DIR / "deep-hce-v2-residual.jsonl",
        "corpusManifest": (
            DATA_DIR / "deep-hce-v2-residual.jsonl.manifest.json"
        ),
    }
    for name, expected in canonical_data_paths.items():
        pin = sealed._mapping(
            identities.get(name), f"generation-2 plan {name}"
        )
        actual_path = Path(str(pin.get("path", ""))).resolve()
        if actual_path != expected.resolve():
            raise ValueError(
                f"generation-2 plan {name} is outside fresh deep-hce-v3"
            )
    if plan_path.resolve() != OUTPUT_DIR / "training-plan.json":
        raise ValueError(
            "generation-2 training plan is outside build-msvc/king-state-v2"
        )
    commands = sealed._mapping(plan.get("commands"), "plan commands")
    sealed._expect(
        set(commands), {"K0", "K1", "K2"}, "generation-2 candidate commands"
    )
    for candidate_id, value in commands.items():
        command = sealed._mapping(
            value, f"generation-2 command {candidate_id}"
        )
        argv = sealed._sequence(
            command.get("argv"), f"generation-2 {candidate_id} argv"
        )
        if len(argv) < 2 or Path(str(argv[1])).resolve() != CANONICAL_TRAINER:
            raise ValueError(
                f"generation-2 {candidate_id} does not use canonical trainer"
            )
        for field in ("network", "manifest", "floatCheckpoint"):
            if field in command and Path(str(command[field])).resolve().parent != OUTPUT_DIR:
                raise ValueError(
                    f"generation-2 {candidate_id} {field} escapes "
                    "build-msvc/king-state-v2"
                )
    outputs = sealed._mapping(plan.get("outputs"), "plan outputs")
    sealed._expect(
        outputs,
        {
            "selectionSeal": str(OUTPUT_DIR / "validation-selection.seal.json"),
            "offlineTest": str(OUTPUT_DIR / "offline-test.json"),
            "offlineTestAccessClaim": str(
                OUTPUT_DIR / "offline-test.json.access.json"
            ),
        },
        "generation-2 plan outputs",
    )
    audit = sealed._mapping(
        plan.get("corpusFeatureAudit"), "plan corpus feature audit"
    )
    for key, expected in {
        "groupPhasePure": False,
        "crossPhaseGroupsAllowed": True,
        "bootstrapUnit": "global leakage component groupId",
        "bootstrapStratification": "phase-incidence bitmask",
    }.items():
        sealed._expect(
            audit.get(key),
            expected,
            f"generation-2 corpus audit {key}",
        )


def _verify_declarations() -> None:
    profile, _profile_pin = prelabel._load_json(
        PROFILE_PATH, "generation-2 preregistration"
    )
    prelabel._validate_profile(profile, REPO)
    prelabel._validate_amendments(REPO)


def _verify_v2_seal(seal_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PRELABEL_SEALER),
            "verify-seal",
            "--seal",
            str(seal_path.resolve(strict=True)),
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(
            "generation-2 pre-label seal verification failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


def _same_pin(left: Any, right: Mapping[str, Any], label: str) -> None:
    actual = sealed._mapping(left, label)
    if not sealed._same_identity(actual, right):
        raise ValueError(f"{label} differs from the frozen generation-2 plan")
    sealed._verify_identity(actual, label)


def _exact_argv(value: Any, expected: Sequence[str], label: str) -> None:
    actual = sealed._sequence(value, label)
    sealed._expect(actual, list(expected), label)


def _expected_shadow_argv(
    outward_argv: Sequence[str],
) -> tuple[list[str], Path, Path]:
    expected = list(outward_argv)
    manifest_path = canonical._option(expected, "--manifest")
    float_path = canonical._option(expected, "--float-checkpoint")
    assert manifest_path is not None
    assert float_path is not None
    shadow_manifest = canonical._derived(
        manifest_path, "optimizer-shadow"
    )
    shadow_float = canonical._derived(float_path, "optimizer-shadow")
    canonical._replace_option(expected, "--manifest", shadow_manifest)
    canonical._replace_option(expected, "--float-checkpoint", shadow_float)
    return expected, shadow_manifest, shadow_float


def _verify_canonical_manifest(
    *,
    plan: Mapping[str, Any],
    candidate_id: str,
    manifest: Mapping[str, Any],
    network_path: Path,
    float_path: Path | None,
    command_override: Mapping[str, Any] | None = None,
) -> None:
    identities = sealed._mapping(plan.get("identities"), "plan identities")
    command = sealed._mapping(
        (
            command_override
            if command_override is not None
            else sealed._mapping(plan.get("commands"), "plan commands").get(
                candidate_id
            )
        ),
        f"{candidate_id} command",
    )
    outward_argv = [
        str(value)
        for value in sealed._sequence(
            command.get("rawTrainerArgv"),
            f"{candidate_id} frozen trainer argv",
        )
    ]
    canonical_wrapper = sealed._mapping(
        identities.get("trainer"), "plan canonical trainer"
    )
    underlying_trainer = sealed._mapping(
        identities.get("sealedTrainer"), "plan sealed trainer"
    )
    _same_pin(
        manifest.get("canonicalWrapper"),
        canonical_wrapper,
        f"{candidate_id} canonical wrapper",
    )
    _same_pin(
        manifest.get("sealedUnderlyingTrainer"),
        underlying_trainer,
        f"{candidate_id} sealed underlying trainer",
    )
    _exact_argv(
        manifest.get("rawArgv"),
        outward_argv,
        f"{candidate_id} outward rawArgv",
    )
    experiment = sealed._mapping(
        manifest.get("experiment"), f"{candidate_id} experiment"
    )
    _exact_argv(
        experiment.get("rawArgv"),
        outward_argv,
        f"{candidate_id} experiment outward rawArgv",
    )
    network_pin = sealed._identity(network_path)

    if candidate_id == "K0":
        if float_path is not None:
            raise ValueError("K0 unexpectedly has a float checkpoint")
        provenance = sealed._mapping(
            manifest.get("canonicalTrainerProvenance"),
            "K0 canonical trainer provenance",
        )
        sealed._expect(
            provenance.get("schemaVersion"),
            1,
            "K0 canonical provenance schema",
        )
        sealed._expect(
            provenance.get("mode"),
            "migrate-only-delegation",
            "K0 canonical provenance mode",
        )
        _same_pin(
            provenance.get("wrapper"),
            canonical_wrapper,
            "K0 provenance wrapper",
        )
        _same_pin(
            provenance.get("sealedUnderlyingTrainer"),
            underlying_trainer,
            "K0 provenance underlying trainer",
        )
        _same_pin(
            provenance.get("sourceNetwork"),
            network_pin,
            "K0 provenance source network",
        )
        _exact_argv(
            provenance.get("outwardFrozenArgv"),
            outward_argv,
            "K0 provenance outward argv",
        )
        _exact_argv(
            provenance.get("internalSubstitutedArgv"),
            outward_argv,
            "K0 provenance internal argv",
        )
        return

    if float_path is None:
        raise ValueError(f"{candidate_id} lacks its canonical float checkpoint")
    canonical_float_pin = sealed._identity(float_path)
    _same_pin(
        manifest.get("floatCheckpoint"),
        canonical_float_pin,
        f"{candidate_id} canonical float",
    )
    shadow_pin = sealed._mapping(
        manifest.get("optimizerShadowCheckpoint"),
        f"{candidate_id} optimizer shadow",
    )
    shadow_path = sealed._verify_identity(
        shadow_pin, f"{candidate_id} optimizer shadow"
    )
    shadow_manifest_pin = sealed._mapping(
        manifest.get("optimizerShadowManifest"),
        f"{candidate_id} optimizer-shadow manifest",
    )
    shadow_manifest_path = sealed._verify_identity(
        shadow_manifest_pin,
        f"{candidate_id} optimizer-shadow manifest",
    )
    (
        expected_internal_argv,
        expected_shadow_manifest,
        expected_shadow_float,
    ) = _expected_shadow_argv(outward_argv)
    if (
        shadow_path != expected_shadow_float
        or shadow_manifest_path != expected_shadow_manifest
    ):
        raise ValueError(
            f"{candidate_id} optimizer-shadow artifacts do not use the "
            "canonical derived paths"
        )
    shadow_manifest = sealed._load_json(
        shadow_manifest_path,
        f"{candidate_id} optimizer-shadow manifest",
    )
    _same_pin(
        shadow_manifest.get("floatCheckpoint"),
        shadow_pin,
        f"{candidate_id} shadow manifest checkpoint",
    )
    if shadow_path == float_path.resolve():
        raise ValueError(
            f"{candidate_id} optimizer shadow aliases canonical checkpoint"
        )
    shadow_model, decoded_shadow_pin = (
        canonical._read_lossless_float_checkpoint(
            shadow_path,
            label=f"{candidate_id} optimizer-shadow checkpoint",
        )
    )
    del shadow_model
    if not sealed._same_identity(decoded_shadow_pin, shadow_pin):
        raise ValueError(
            f"{candidate_id} structurally decoded a different optimizer shadow"
        )
    network = sealed.QuantizedNetwork.read(network_path)
    canonical_model, decoded_canonical_pin = (
        canonical._read_lossless_float_checkpoint(
            float_path,
            label=f"{candidate_id} canonical float checkpoint",
        )
    )
    if not sealed._same_identity(
        decoded_canonical_pin, canonical_float_pin
    ):
        raise ValueError(
            f"{candidate_id} structurally decoded a different canonical float"
        )
    if (
        canonical_model.quantize(network.architecture).to_bytes()
        != network.to_bytes()
    ):
        raise ValueError(
            f"{candidate_id} canonical float does not independently "
            "requantize to the exported network"
        )
    if not sealed._same_identity(
        sealed._identity(network_path), network_pin
    ):
        raise ValueError(
            f"{candidate_id} exported network changed during canonical decode"
        )

    canonicalization = sealed._mapping(
        manifest.get("deploymentFloatCanonicalization"),
        f"{candidate_id} deployment canonicalization",
    )
    for key, expected in {
        "schemaVersion": 1,
        "kind": "omega-nnue-canonical-deployment-float",
        "requantizedByteIdentical": True,
        "parameterRoundTripExact": True,
        "heldOutTargetsDecoded": False,
    }.items():
        sealed._expect(
            canonicalization.get(key),
            expected,
            f"{candidate_id} canonicalization {key}",
        )
    _same_pin(
        canonicalization.get("wrapper"),
        canonical_wrapper,
        f"{candidate_id} canonicalization wrapper",
    )
    _same_pin(
        canonicalization.get("sealedUnderlyingTrainer"),
        underlying_trainer,
        f"{candidate_id} canonicalization underlying trainer",
    )
    _same_pin(
        canonicalization.get("sourceNetwork"),
        network_pin,
        f"{candidate_id} canonicalization source network",
    )
    _same_pin(
        canonicalization.get("sourceOptimizerShadow"),
        shadow_pin,
        f"{candidate_id} canonicalization optimizer shadow",
    )
    _same_pin(
        canonicalization.get("canonicalFloat"),
        canonical_float_pin,
        f"{candidate_id} canonicalization float",
    )
    _exact_argv(
        canonicalization.get("outwardFrozenArgv"),
        outward_argv,
        f"{candidate_id} canonicalization outward argv",
    )
    internal_argv = [
        str(value)
        for value in sealed._sequence(
            canonicalization.get("internalSubstitutedArgv"),
            f"{candidate_id} canonicalization internal argv",
        )
    ]
    _exact_argv(
        internal_argv,
        expected_internal_argv,
        f"{candidate_id} exact internal substituted argv",
    )
    _exact_argv(
        shadow_manifest.get("rawArgv"),
        internal_argv,
        f"{candidate_id} shadow rawArgv",
    )
    shadow_experiment = sealed._mapping(
        shadow_manifest.get("experiment"),
        f"{candidate_id} shadow experiment",
    )
    _exact_argv(
        shadow_experiment.get("rawArgv"),
        internal_argv,
        f"{candidate_id} shadow experiment rawArgv",
    )
    legacy_penalty = canonical._legacy_quantization_penalty(
        manifest.get("quantizationPenalty")
    )
    shadow_penalty = canonical._legacy_quantization_penalty(
        manifest.get("optimizerShadowQuantizationPenalty")
    )
    raw_shadow_penalty = canonical._legacy_quantization_penalty(
        shadow_manifest.get("quantizationPenalty")
    )
    sealed._expect(
        shadow_penalty,
        legacy_penalty,
        f"{candidate_id} optimizer-shadow penalty alias",
    )
    sealed._expect(
        legacy_penalty,
        raw_shadow_penalty,
        f"{candidate_id} final/shadow penalty provenance",
    )
    deployment = sealed._mapping(
        manifest.get("deploymentFloatQuantizationPenalty"),
        f"{candidate_id} deployment-float penalty",
    )
    for key, expected in {
        "schemaVersion": 1,
        "scope": "all pinned corpus OFENs",
        "targetFieldsDecoded": 0,
        "targetFieldsEmitted": 0,
    }.items():
        sealed._expect(
            deployment.get(key),
            expected,
            f"{candidate_id} deployment penalty {key}",
        )
    semantics = str(deployment.get("semantics", ""))
    if "deployment-equivalent float" not in semantics:
        raise ValueError(
            f"{candidate_id} deployment penalty has ambiguous semantics"
        )
    expected_inputs = [identities["corpus"]]
    actual_inputs = sealed._sequence(
        deployment.get("inputs"),
        f"{candidate_id} deployment penalty inputs",
    )
    if len(actual_inputs) != 1:
        raise ValueError(
            f"{candidate_id} deployment penalty must cover one frozen corpus"
        )
    _same_pin(
        actual_inputs[0],
        expected_inputs[0],
        f"{candidate_id} deployment penalty corpus",
    )
    audit = sealed._mapping(
        plan.get("corpusFeatureAudit"), "plan corpus feature audit"
    )
    sealed._expect(
        deployment.get("samples"),
        int(audit["rows"]),
        f"{candidate_id} deployment penalty sample count",
    )
    values: dict[str, float] = {}
    for key in (
        "maeCp",
        "maxCp",
        "exactPredictionFraction",
        "floatPredictionMinCp",
        "floatPredictionMaxCp",
    ):
        value = deployment.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError(
                f"{candidate_id} deployment penalty {key} is not finite"
            )
        values[key] = float(value)
    if (
        values["maeCp"] < 0.0
        or values["maxCp"] < values["maeCp"]
        or not 0.0 <= values["exactPredictionFraction"] <= 1.0
    ):
        raise ValueError(
            f"{candidate_id} deployment penalty statistics are inconsistent"
        )
    protocol = sealed._load_json(
        Path(str(identities["protocol"]["path"])),
        "generation-2 protocol",
    )
    runtime = sealed._validate_protocol(protocol)["runtime"]
    mean_limit = float(runtime["maximumMeanFloatQuantizationPenaltyCp"])
    max_limit = float(runtime["maximumSingleFloatQuantizationPenaltyCp"])
    if values["maeCp"] > mean_limit or values["maxCp"] > max_limit:
        raise ValueError(
            f"{candidate_id} deployment-equivalent float penalty failed the "
            f"unchanged {mean_limit:g}/{max_limit:g} cp health limits"
        )


def _install_generation2() -> None:
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
    sealed._feature_corpus = phase_incidence._feature_corpus
    sealed._paired_bootstrap = phase_incidence._paired_bootstrap

    original_prelabel_context = sealed._verify_prelabel_context

    def generation2_prelabel_context(
        *,
        protocol_path: Path,
        seal_path: Path,
        initializer_path: Path,
        cpp_evaluator_path: Path,
        run_canonical_verifier: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        context = original_prelabel_context(
            protocol_path=protocol_path,
            seal_path=seal_path,
            initializer_path=initializer_path,
            cpp_evaluator_path=cpp_evaluator_path,
            run_canonical_verifier=False,
        )
        if run_canonical_verifier:
            _verify_v2_seal(seal_path)
        return context

    sealed._verify_prelabel_context = generation2_prelabel_context

    original_trainer_arguments = sealed._trainer_arguments

    def generation2_trainer_arguments(
        *args: Any, **kwargs: Any
    ) -> list[str]:
        argv = original_trainer_arguments(*args, **kwargs)
        argv[1] = str(CANONICAL_TRAINER)
        return argv

    sealed._trainer_arguments = generation2_trainer_arguments

    original_defaults = sealed._default_paths

    def generation2_defaults() -> dict[str, Path]:
        defaults = original_defaults()
        defaults.update(
            {
                "seal": DATA_DIR / "king-state-v1-prelabel.seal.json",
                "corpus": DATA_DIR / "deep-hce-v2-residual.jsonl",
                "corpus_manifest": (
                    DATA_DIR / "deep-hce-v2-residual.jsonl.manifest.json"
                ),
                "output_dir": OUTPUT_DIR,
                "plan": OUTPUT_DIR / "training-plan.json",
                "selection": OUTPUT_DIR / "validation-selection.seal.json",
                "test": OUTPUT_DIR / "offline-test.json",
            }
        )
        return defaults

    sealed._default_paths = generation2_defaults

    original_atomic_json = sealed._atomic_json

    def generation2_atomic_json(
        path: Path,
        value: Any,
        *,
        no_clobber: bool,
    ) -> None:
        if isinstance(value, dict) and value.get("kind") == sealed.PLAN_KIND:
            value = deepcopy(value)
            identities = sealed._mapping(
                value.get("identities"), "generation-2 plan identities"
            )
            identities.update(_expected_plan_source_identities())
            audit = sealed._mapping(
                value.get("corpusFeatureAudit"),
                "generation-2 corpus feature audit",
            )
            audit.update(
                {
                    "groupPhasePure": False,
                    "crossPhaseGroupsAllowed": True,
                    "bootstrapUnit": "global leakage component groupId",
                    "bootstrapStratification": "phase-incidence bitmask",
                }
            )
            value["generation2"] = _generation2_record()
        elif (
            isinstance(value, dict)
            and value.get("kind") == sealed.SELECTION_KIND
        ):
            value = deepcopy(value)
            value["orchestrator"] = sealed._identity(
                Path(__file__).resolve()
            )
            value["generation2"] = {
                "protocolGeneration": 2,
                "dataProfile": "deep-hce-v3",
                "healthMetricSubject": (
                    "deployment-equivalent float checkpoint"
                ),
                "heldOutTargetsDecoded": False,
            }
        elif (
            isinstance(value, dict)
            and value.get("kind") == sealed.TEST_REPORT_KIND
        ):
            value = deepcopy(value)
            value["generation2"] = {
                "protocolGeneration": 2,
                "dataProfile": "deep-hce-v3",
                "healthMetricSubject": (
                    "deployment-equivalent float checkpoint"
                ),
            }
        original_atomic_json(path, value, no_clobber=no_clobber)

    sealed._atomic_json = generation2_atomic_json

    original_verify_plan = sealed._verify_plan

    def generation2_verify_plan(
        path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        result = original_verify_plan(path)
        plan, _protocol, _parsed = result
        _verify_generation2_plan_contract(plan, path)
        return result

    sealed._verify_plan = generation2_verify_plan

    original_verify_candidate = sealed._verify_candidate

    def generation2_verify_candidate(
        plan: Mapping[str, Any],
        candidate_id: str,
    ) -> tuple[dict[str, Any], Path, Path, Path | None]:
        result = original_verify_candidate(plan, candidate_id)
        manifest, network_path, _manifest_path, float_path = result
        _verify_canonical_manifest(
            plan=plan,
            candidate_id=candidate_id,
            manifest=manifest,
            network_path=network_path,
            float_path=float_path,
        )
        return result

    sealed._verify_candidate = generation2_verify_candidate

    original_run_robustness = sealed._run_robustness

    def generation2_run_robustness(args: Any) -> None:
        original_run_robustness(args)
        selection, plan, _protocol, _parsed = sealed._verify_selection(
            args.selection
        )
        winner = str(selection["selectedCandidateId"])
        command = sealed._mapping(
            selection.get("robustnessCommand"),
            "generation-2 robustness command",
        )
        network_path = Path(str(command["network"])).resolve(strict=True)
        manifest_path = Path(str(command["manifest"])).resolve(strict=True)
        float_path = Path(
            str(command["floatCheckpoint"])
        ).resolve(strict=True)
        manifest = sealed._load_json(
            manifest_path, "generation-2 robustness manifest"
        )
        _verify_canonical_manifest(
            plan=plan,
            candidate_id=winner,
            manifest=manifest,
            network_path=network_path,
            float_path=float_path,
            command_override=command,
        )

    sealed._run_robustness = generation2_run_robustness

    original_offline_test = sealed._offline_test

    def generation2_offline_test(args: Any) -> dict[str, Any]:
        # Verify the amended artifact semantics before the original function
        # publishes the one-time held-out access claim.
        selection, plan, _protocol, _parsed = sealed._verify_selection(
            args.selection
        )
        winner = str(selection["selectedCandidateId"])
        command = sealed._mapping(
            selection.get("robustnessCommand"),
            "generation-2 robustness command",
        )
        network_path = Path(str(command["network"])).resolve(strict=True)
        manifest_path = Path(str(command["manifest"])).resolve(strict=True)
        float_path = Path(
            str(command["floatCheckpoint"])
        ).resolve(strict=True)
        manifest = sealed._load_json(
            manifest_path, "generation-2 robustness manifest"
        )
        _verify_canonical_manifest(
            plan=plan,
            candidate_id=winner,
            manifest=manifest,
            network_path=network_path,
            float_path=float_path,
            command_override=command,
        )
        return original_offline_test(args)

    sealed._offline_test = generation2_offline_test


def _self_test() -> None:
    _verify_declarations()
    prelabel._self_test()
    phase_incidence._self_test()
    completed = subprocess.run(
        [sys.executable, str(CANONICAL_TRAINER), "--self-test"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(
            "canonical trainer self-test failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    outward = [
        "--input",
        str(REPO / "synthetic-corpus.jsonl"),
        "--manifest",
        str(REPO / "synthetic.manifest.json"),
        "--float-checkpoint",
        str(REPO / "synthetic.float"),
        "--quiet",
    ]
    internal, shadow_manifest, shadow_float = _expected_shadow_argv(outward)
    expected = list(outward)
    expected[expected.index("--manifest") + 1] = str(
        REPO / "synthetic.manifest.optimizer-shadow.json"
    )
    expected[expected.index("--float-checkpoint") + 1] = str(
        REPO / "synthetic.optimizer-shadow.float"
    )
    if (
        internal != expected
        or shadow_manifest != Path(
            expected[expected.index("--manifest") + 1]
        )
        or shadow_float != Path(
            expected[expected.index("--float-checkpoint") + 1]
        )
    ):
        raise AssertionError("canonical shadow argv derivation changed")
    tampered = list(internal)
    tampered[-1] = "--not-quiet"
    try:
        _exact_argv(tampered, internal, "tampered internal argv")
    except ValueError:
        pass
    else:
        raise AssertionError("unrelated internal argv mutation was accepted")
    synthetic_identities: dict[str, Any] = {
        **_expected_plan_source_identities(),
        "prelabelSeal": {
            "path": str(DATA_DIR / "king-state-v1-prelabel.seal.json")
        },
        "corpus": {
            "path": str(DATA_DIR / "deep-hce-v2-residual.jsonl")
        },
        "corpusManifest": {
            "path": str(
                DATA_DIR / "deep-hce-v2-residual.jsonl.manifest.json"
            )
        },
    }
    synthetic_commands = {
        candidate_id: {
            "argv": [sys.executable, str(CANONICAL_TRAINER)],
            "network": str(OUTPUT_DIR / f"{candidate_id}.nnue"),
            "manifest": str(OUTPUT_DIR / f"{candidate_id}.manifest.json"),
            **(
                {}
                if candidate_id == "K0"
                else {
                    "floatCheckpoint": str(
                        OUTPUT_DIR / f"{candidate_id}.float"
                    )
                }
            ),
        }
        for candidate_id in ("K0", "K1", "K2")
    }
    synthetic_plan: dict[str, Any] = {
        "identities": synthetic_identities,
        "generation2": _generation2_record(),
        "commands": synthetic_commands,
        "outputs": {
            "selectionSeal": str(
                OUTPUT_DIR / "validation-selection.seal.json"
            ),
            "offlineTest": str(OUTPUT_DIR / "offline-test.json"),
            "offlineTestAccessClaim": str(
                OUTPUT_DIR / "offline-test.json.access.json"
            ),
        },
        "corpusFeatureAudit": {
            "groupPhasePure": False,
            "crossPhaseGroupsAllowed": True,
            "bootstrapUnit": "global leakage component groupId",
            "bootstrapStratification": "phase-incidence bitmask",
        },
    }
    _verify_generation2_plan_contract(
        synthetic_plan, OUTPUT_DIR / "training-plan.json"
    )
    missing_identity = deepcopy(synthetic_plan)
    del missing_identity["identities"]["trainer"]
    try:
        _verify_generation2_plan_contract(
            missing_identity, OUTPUT_DIR / "training-plan.json"
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "generation-2 plan guard accepted a missing trainer identity"
        )
    weakened_contract = deepcopy(synthetic_plan)
    del weakened_contract["generation2"]["healthMetricSubject"]
    try:
        _verify_generation2_plan_contract(
            weakened_contract, OUTPUT_DIR / "training-plan.json"
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "generation-2 plan guard accepted an incomplete amendment contract"
        )
    print("king_state_train_generation2 self-test passed", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["self-test"]:
        _self_test()
        return 0
    _verify_declarations()
    _install_generation2()
    return sealed.main(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
