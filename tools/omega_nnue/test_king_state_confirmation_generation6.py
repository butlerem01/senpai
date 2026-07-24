#!/usr/bin/env python3
"""Hostile tests for the Generation-6 promotion successor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import king_state_confirmation_generation6 as g6


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_bytes(g6._canonical_json(value))
    return path.resolve()


def _time_after(value: str, microseconds: int = 1) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    return (parsed + timedelta(microseconds=microseconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


class Fixture:
    def __init__(
        self,
        root: Path,
        *,
        prior_v2: bool = False,
        register_cleanup=None,
    ) -> None:
        self.root = root.resolve()
        self.artifacts = self.root / "successor"
        self.namespace = self.root / "g6"
        self.v2 = self.root / "v2-attempts"
        self.engine = _write(self.root / "runtime/senpai.exe", b"synthetic-senpai")
        self.network = _write(self.root / "runtime/G6A.nnue", b"synthetic-g6-network")
        self.initializer = _write(self.root / "runtime/I0.nnue", b"synthetic-i0")
        self.g5_network = _write(self.root / "runtime/G5.nnue", b"synthetic-g5-network")
        self.engine_source = _write(self.root / "runtime/source.json", {"commit": "abc"})
        self.build_manifest = _write(self.root / "runtime/build.json", {"mode": "release"})
        self.runtime_manifest = _write(self.root / "runtime/runtime.json", {"files": 2})
        self.verifier_runner = _write(self.root / "runtime/verifier.py", b"# synthetic\n")
        self.verifier_options = _write(self.root / "runtime/verifier-options.json", {})

        capsule_created = "2026-07-01T00:00:00.000001Z"
        self.capsule = _write(
            self.root / "upstream/capsule.closure.json",
            {
                "schemaVersion": 1,
                "kind": g6.G6_CAPSULE_KIND,
                "profileId": g6.G6_PROFILE_ID,
                "status": "closed-pretarget-to-final-projection-lineage",
                "createdUtc": capsule_created,
                "upstreamVerifierRunner": g6.identity(self.verifier_runner),
                "upstreamVerifierOptions": g6.identity(self.verifier_options),
                "closureDeclaration": g6.CAPSULE_CLOSURE_DECLARATION,
                "resultInformationRead": False,
                "finalStageSeal": True,
            },
        )
        self.receipt = _write(
            self.namespace / "00-authority/upstream-verification.json",
            {
                "schemaVersion": 1,
                "kind": g6.G6_RECEIPT_KIND,
                "profileId": g6.G6_PROFILE_ID,
                "status": "passed-fresh-semantic-replay",
                "capsule": g6.identity(self.capsule),
                "verifierExecutable": g6.identity(Path(g6.sys.executable)),
                "verifierRunner": g6.identity(self.verifier_runner),
                "verifierOptions": g6.identity(self.verifier_options),
                "initializerAuthority": {
                    "manifest": {},
                    "selectionMode": "promoted-prior",
                    "selectedCatalogIndex": 0,
                    "selectedModel": {},
                    "catalogSourceIds": ["G5"],
                    "firstEligibleSelected": True,
                    "selectionSemanticsVerified": True,
                    "selectedPromotionHealthPassed": True,
                    "sourceClosureSemanticsVerified": True,
                    "fallbackProtocolReplayed": False,
                    "g6TargetRowsDecoded": 0,
                    "resultInformationRead": False,
                },
                "terminalAuthority": {
                    "lineage": {},
                    "routedChildren": 32,
                    "terminalChildrenExcludedBeforeRouting": True,
                    "unclassifiedChildren": 0,
                    "errorTextAcceptedAsTerminal": False,
                    "rulesSemanticsReplayed": True,
                    "completionSemanticsVerified": True,
                },
                "priorForbiddenAuthority": {
                    "catalogs": [],
                    "registry": {},
                    "requiredSourceIds": ["G3", "G4", "G5"],
                    "catalogPositions": 1,
                    "manifestsSemanticallyReplayed": True,
                    "exactPositionOverlaps": 0,
                    "conservativeSignatureOverlaps": 0,
                    "sourceArtifactOverlaps": 0,
                },
                "componentAuthority": {
                    "componentMap": {},
                    "roots": 8,
                    "components": 8,
                    "wholeComponentSplits": True,
                    "semanticsReplayed": True,
                },
                "staticHceAuthority": {
                    "claim": {},
                    "completion": {},
                    "teacherClaim": {},
                    "prelabelSeal": {},
                    "targetFreeRouting": {},
                    "engine": {},
                    "runner": {},
                    "options": {},
                    "transcript": {},
                    "inputOrderSha256": "0" * 64,
                    "rows": 32,
                    "perspective": "child stm",
                    "freshReplayMatches": True,
                    "completedBeforeTeacherClaim": True,
                    "semanticsReplayed": True,
                },
                "teacherLedgerAuthority": {
                    "claim": {},
                    "attemptLedger": {},
                    "attemptLedgerCompletion": {},
                    "completion": {},
                    "budgets": {},
                    "routedChildren": 32,
                    "attemptRecords": 32,
                    "successfulChildren": 32,
                    "rejectedChildren": 0,
                    "unresolvedChildren": 0,
                    "semanticsReplayed": True,
                },
                "projectionAuthority": {
                    "plannedProducer": {},
                    "plannedCorpusPath": "planned",
                    "plannedManifestPath": "planned-manifest",
                    "actualProducer": {},
                    "actualCorpus": {},
                    "actualManifest": {},
                    "producerMatches": True,
                    "pathsMatch": True,
                    "semanticsReplayed": True,
                },
                "resultInformationRead": False,
            },
        )
        self.authority = _write(
            self.namespace / "00-authority/materialization.manifest.json",
            {
                "kind": "omega-nnue-king-state-v6-authority-materialization-manifest",
                "upstreamCapsule": g6.identity(self.capsule),
                "upstreamVerification": g6.identity(self.receipt),
            },
        )
        self.selection = _write(
            self.namespace / "03-selection/validation-selection.json",
            {
                "schemaVersion": 1,
                "kind": g6.G6_SELECTION_KIND,
                "profileId": g6.G6_PROFILE_ID,
                "status": "selected-from-fresh-canonical-replay",
                "createdUtc": "2026-07-01T00:00:00.000010Z",
                "authorityManifest": g6.identity(self.authority),
                "selectedCandidateId": "G6A",
                "selectedModel": g6.identity(self.network),
                "resultInformationRead": False,
                "heldOutTargetRowsDecodedAtSelection": 0,
                "heldOutTargetFieldsDecodedAtSelection": 0,
            },
        )
        self.robustness = _write(
            self.namespace / "04-robustness/robustness.json",
            {
                "schemaVersion": 1,
                "kind": g6.G6_ROBUSTNESS_KIND,
                "profileId": g6.G6_PROFILE_ID,
                "status": "passed-replayed-second-seed-confirmation",
                "createdUtc": "2026-07-01T00:00:00.000020Z",
                "validationSelection": g6.identity(self.selection),
                "selectedCandidateId": "G6A",
                "primaryModel": g6.identity(self.network),
                "passedDeploymentHealth": True,
                "resultInformationRead": False,
                "heldOutTargetRowsDecodedAtSeal": 0,
                "heldOutTargetFieldsDecodedAtSeal": 0,
            },
        )
        self.g6_prereg = _write(
            self.namespace / "00-preregistration.json",
            {
                "schemaVersion": 1,
                "kind": g6.G6_PREREGISTRATION_KIND,
                "profileId": g6.G6_PROFILE_ID,
                "status": "frozen-single-lineage-before-generation6-training",
                "createdUtc": "2026-07-01T00:00:00.000005Z",
                "namespace": str(self.namespace),
                "protocol": g6.identity(g6.TRAINING_PROTOCOL_PATH),
                "upstreamVerifierExecutable": g6.identity(Path(g6.sys.executable)),
                "upstreamCapsule": g6.identity(self.capsule),
                "resultInformationRead": False,
                "heldOutTargetRowsDecodedAtFreeze": 0,
                "heldOutTargetFieldsDecodedAtFreeze": 0,
            },
        )
        self.g5_decisions = {}
        for gate in ("development", "equal-node", "equal-time"):
            self.g5_decisions[gate] = _write(
                self.root / f"g5/{gate}.json", {"gate": gate, "passed": True}
            )
        self.g5_auth = _write(
            self.root / "g5/match-authorization.json",
            {
                "schemaVersion": 1,
                "kind": "omega-nnue-king-state-v5-color-compat-authorization",
                "selectedNetwork": g6.identity(self.g5_network),
                "engine": g6.identity(self.engine),
                "runnerUpFallback": False,
                "finalStageSeal": True,
            },
        )
        self.g5_closure = _write(
            self.root / "g5/closure.json",
            {
                "schemaVersion": 1,
                "kind": "omega-nnue-king-state-v5-color-compat-closure",
                "authorization": g6.identity(self.g5_auth),
                "selectedNetwork": g6.identity(self.g5_network),
                "decisions": {
                    gate: g6.identity(path) for gate, path in self.g5_decisions.items()
                },
                "clearlySuperior": True,
                "originalArtifactsRewritten": 0,
                "finalStageSeal": True,
            },
        )
        entries = self.root / "openings/entries.jsonl"
        rows = []
        counts = {}
        for phase in g6.PHASES:
            for side in g6.SIDES:
                cell = f"{phase}:{side}"
                counts[cell] = g6.PAIRS_PER_CELL
                for index in range(g6.PAIRS_PER_CELL):
                    rows.append(
                        {
                            "schemaVersion": 1,
                            "kind": "omega-nnue-candidate-blind-opening-v1",
                            "openingId": f"{phase}-{side}-{index:03d}",
                            "phase": phase,
                            "sideToMove": side,
                            "ofen": f"synthetic-{phase}-{side}-{index}",
                            "moves": [],
                        }
                    )
        entries.parent.mkdir(parents=True, exist_ok=True)
        entries.write_bytes(b"".join(g6._canonical_json(row) for row in rows))
        self.opening_entries = entries.resolve()
        self.opening_manifest = _write(
            self.root / "openings/manifest.json",
            {
                "schemaVersion": 1,
                "kind": "omega-nnue-candidate-blind-opening-pool-v1",
                "status": "sealed-before-candidate-selection",
                "createdUtc": "2026-06-30T00:00:00.000001Z",
                "entries": g6.identity(self.opening_entries),
                "rows": len(rows),
                "cellCounts": counts,
                "candidateInformationRead": False,
                "gameResultsRead": False,
                "finalStageSeal": True,
            },
        )
        if prior_v2:
            self._write_v2_closure(1)
        self._patchers = [
            mock.patch.object(g6, "DEFAULT_V2_ATTEMPT_ROOT", self.v2),
            mock.patch.object(g6, "_run_v2_chain_verifier", return_value=None),
            mock.patch.object(g6, "_fresh_g5_incumbent_replay", side_effect=self._fake_g5_replay),
            mock.patch.object(g6, "_verify_trainer_heldout_authority", return_value=None),
            mock.patch.object(g6, "_practical_launch_adapter", return_value=self),
        ]
        for patcher in self._patchers:
            patcher.start()
            if register_cleanup is not None:
                register_cleanup(patcher.stop)
        self.deployment = self._publish_deployment()
        self.preregistration = self._publish_preregistration()

    def _fake_g5_replay(self, authorization: Path, closure: Path) -> dict:
        return {
            "authorization": g6.identity(authorization),
            "closure": g6.identity(closure),
            "engine": g6.identity(self.engine),
            "network": g6.identity(self.g5_network),
        }

    def _probe(self, engine: Path, network: Path) -> dict[str, object]:
        return {
            "uciOk": True,
            "readyOk": True,
            "requiredOptions": ["OmegaNNUEFile", "UCI_Variant", "UseOmegaNNUE"],
            "networkLoadedDiagnostic": (
                "info string Omega NNUE loaded: synthetic from " + str(network)
            ),
            "activeDiagnostic": "info string Omega NNUE evaluation active",
            "bestmove": "f1f2",
            "searchNodes": 1,
        }

    def _publish_deployment(self) -> Path:
        value = g6._publish_deployment_bundle_with_probe(
            engine=self.engine,
            network=self.network,
            g6_preregistration=self.g6_prereg,
            capsule=self.capsule,
            receipt=self.receipt,
            selection=self.selection,
            robustness=self.robustness,
            engine_source=self.engine_source,
            build_manifest=self.build_manifest,
            runtime_manifest=self.runtime_manifest,
            artifact_root=self.artifacts,
            probe_function=self._probe,
        )
        return Path(value["path"])

    def _publish_preregistration(self) -> Path:
        value = g6.publish_preregistration(
            g6_preregistration=self.g6_prereg,
            capsule=self.capsule,
            receipt=self.receipt,
            selection=self.selection,
            robustness=self.robustness,
            deployment_bundle=self.deployment,
            g5_authorization=self.g5_auth,
            g5_closure=self.g5_closure,
            opening_pool_manifest=self.opening_manifest,
            artifact_root=self.artifacts,
            v2_attempt_root=self.v2,
        )
        return Path(value["path"])

    def _write_v2_closure(self, index: int, *, outcome: str = "failed") -> Path:
        root = self.v2 / f"attempt-{index:06d}"
        path = root / "attempt-closure.json"
        value = {
            "schemaVersion": 1,
            "kind": g6.V2_CLOSURE_KIND,
            "protocol": g6.identity(
                g6.REPO / g6.PINNED_AUTHORITIES["v2ProtocolJson"][0]
            ),
            "attemptIndex": index,
            "createdUtc": f"2026-07-01T00:00:{index:02d}.000001Z",
            "implementationSeal": {"path": "unused", "bytes": 0, "sha256": "0" * 64},
            "attemptReservation": {"path": "unused", "bytes": 0, "sha256": "0" * 64},
            "candidateClaim": None,
            "jointSuiteSeal": None,
            "authorization": None,
            "decisions": {},
            "outcome": outcome,
            "reason": "synthetic-failure" if outcome != "confirmed" else "confirmed",
            "terminal": True,
            "nextAttemptAllowed": outcome != "confirmed",
            "candidateNetworkSha256": g6.identity(self.network)["sha256"],
        }
        return _write(path, value)

    def publish_heldout(
        self, *, nominate: bool = True, access_offset: int = 1
    ) -> Path:
        prereg = g6._load_json(self.preregistration, "test preregistration")
        base = prereg["createdUtc"]
        paths = {name: Path(path) for name, path in prereg["g6"]["heldoutExpectedPaths"].items()}
        access = {
            "schemaVersion": 1,
            "kind": g6.G6_HELDOUT_ACCESS_KIND,
            "status": "armed-before-first-heldout-target-decode",
            "createdUtc": _time_after(base, access_offset),
            "validationSelection": prereg["g6"]["selection"],
            "robustnessSeal": prereg["g6"]["robustness"],
            "selectedCandidateId": "G6A",
            "selectedPrimaryModel": prereg["g6"]["selectedNetwork"],
            "resultInformationRead": False,
            "heldOutTargetRowsDecodedAtArm": 0,
            "heldOutTargetFieldsDecodedAtArm": 0,
        }
        _write(paths["access"], access)
        claim = {
            "schemaVersion": 1,
            "kind": g6.G6_HELDOUT_CLAIM_KIND,
            "status": "permanently-claimed-before-first-target-decode",
            "createdUtc": _time_after(base, 2),
            "heldoutAccess": g6.identity(paths["access"]),
            "selectedCandidateId": "G6A",
            "selectedPrimaryModel": prereg["g6"]["selectedNetwork"],
            "resultInformationRead": False,
            "heldOutTargetRowsDecodedAtClaim": 0,
            "heldOutTargetFieldsDecodedAtClaim": 0,
        }
        _write(paths["claim"], claim)
        baseline = self._aggregate(top=0.50, regret=10.0, ce=1.0, huber=1.0)
        selected = self._aggregate(
            top=0.51 if nominate else 0.50,
            regret=9.0 if nominate else 10.0,
            ce=1.0,
            huber=1.0,
        )
        report = {
            "schemaVersion": 1,
            "kind": g6.G6_HELDOUT_REPORT_KIND,
            "profileId": g6.G6_PROFILE_ID,
            "status": "consumed-once-aggregate-only",
            "createdUtc": _time_after(base, 3),
            "claim": g6.identity(paths["claim"]),
            "heldoutAccess": g6.identity(paths["access"]),
            "validationSelection": prereg["g6"]["selection"],
            "robustnessSeal": prereg["g6"]["robustness"],
            "selectedCandidateId": "G6A",
            "models": {
                "I0": g6.identity(self.initializer),
                "G6A": prereg["g6"]["selectedNetwork"],
            },
            "evaluatorExecutable": {"synthetic": True},
            "evaluatorRunner": {"synthetic": True},
            "evaluatorOptions": {"synthetic": True},
            "runtimeManifest": {"synthetic": True},
            "aggregation": "equal eight cells",
            "modelMetrics": {"I0": baseline, "G6A": selected},
            "resultInformationRead": False,
            "decodedHeldOutTargetRows": 32,
            "rawRootsEmitted": False,
            "rawChildrenEmitted": False,
            "rawOfensEmitted": False,
            "predictionsEmitted": False,
            "perRootMetricsEmitted": False,
        }
        _write(paths["report"], report)
        closure = {
            "schemaVersion": 1,
            "kind": g6.G6_HELDOUT_CLOSURE_KIND,
            "profileId": g6.G6_PROFILE_ID,
            "status": "heldout-consumed-and-permanently-closed",
            "createdUtc": _time_after(base, 4),
            "preregistration": prereg["g6"]["preregistration"],
            "claim": g6.identity(paths["claim"]),
            "heldoutAccess": g6.identity(paths["access"]),
            "report": g6.identity(paths["report"]),
            "selectedCandidateId": "G6A",
            "heldOutRootInventory": {"roots": 8},
            "spentForFutureGenerations": True,
            "retryPermitted": False,
            "resultInformationRead": False,
        }
        _write(paths["closure"], closure)
        return Path(g6.publish_heldout_decision(self.preregistration)["path"])

    @staticmethod
    def _aggregate(*, top: float, regret: float, ce: float, huber: float) -> dict:
        metrics = {
            "topSetAccuracy": top,
            "meanChosenMoveRegretCp": regret,
            "listwiseCrossEntropy": ce,
            "pointwiseHuber": huber,
        }
        return {
            "decisionRoots": 8,
            "cells": {cell: {"roots": 1, **metrics} for cell in g6.CELLS},
            "macro": metrics,
        }

    def claim_and_suite(self, heldout_decision: Path) -> tuple[Path, Path, bytes]:
        entropy = b"E" * g6.ENTROPY_BYTES
        with mock.patch.object(g6.secrets, "token_bytes", return_value=entropy):
            claim = Path(
                g6.claim_practical_attempt(
                    preregistration=self.preregistration,
                    heldout_decision=heldout_decision,
                )["path"]
            )
        suite = Path(
            g6.publish_practical_suite(
                preregistration=self.preregistration, claim=claim
            )["path"]
        )
        return claim, suite, entropy

    def events(self, suite: Path, pairs: int, *, safety: int = 0, bad_diagnostic: bool = False) -> Path:
        prereg = g6._load_json(self.preregistration, "test prereg")
        suite_doc = g6._load_json(suite, "test suite")
        deployment = g6.verify_deployment_bundle(self.deployment)
        rows = []
        for entry in suite_doc["entries"][:pairs]:
            games = []
            for game_index, assignment in enumerate(
                ("candidate-white", "candidate-black"), 1
            ):
                games.append(
                    {
                        "gameIndex": game_index,
                        "assignment": assignment,
                        "candidateHalfPoints": 2,
                        "candidateEngineSha256": deployment["engine"]["sha256"],
                        "candidateNetworkSha256": deployment["network"]["sha256"],
                        "incumbentEngineSha256": prereg["g5Incumbent"]["engine"]["sha256"],
                        "incumbentNetworkSha256": prereg["g5Incumbent"]["network"]["sha256"],
                        "candidateLoadedNetworkSha256": (
                            "0" * 64 if bad_diagnostic and not rows and game_index == 1
                            else deployment["network"]["sha256"]
                        ),
                        "incumbentLoadedNetworkSha256": prereg["g5Incumbent"]["network"]["sha256"],
                        "candidateNnueActive": True,
                        "incumbentNnueActive": True,
                        "terminal": True,
                    }
                )
            rows.append(
                {
                    "schemaVersion": 1,
                    "kind": g6.EVENT_KIND,
                    "attemptIndex": 1,
                    "pairIndex": entry["pairIndex"],
                    "openingId": entry["openingId"],
                    "phase": entry["phase"],
                    "sideToMove": entry["sideToMove"],
                    "games": games,
                    "safety": {
                        name: safety if name == "engineCrashes" and not rows else 0
                        for name in g6.SAFETY_COUNTERS
                    },
                    "terminal": True,
                }
            )
        path = (
            g6._attempt_paths(self.artifacts, suite_doc["attemptIndex"])[
                "practicalExecution"
            ]
            / "practical-events.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(g6._canonical_json(row) for row in rows))
        return path.resolve()

    def verify_execution_transcript(self, **kwargs) -> dict:
        root = Path(kwargs["events"]).parent
        if {item.name for item in root.iterdir()} != {
            "practical-events.jsonl",
            "06-execution-transcript.json",
        }:
            raise ValueError("synthetic practical execution inventory changed")
        return g6._load_json(Path(kwargs["transcript"]), "synthetic execution transcript")

    def verify_incident_terminal_handoff(self, **kwargs) -> dict:
        root = Path(kwargs["handoff"]).parent
        if {item.name for item in root.iterdir()} != {
            "practical-events.jsonl",
            "98-incident-terminal-handoff.json",
        }:
            raise ValueError("synthetic incident execution inventory changed")
        return g6._load_json(Path(kwargs["handoff"]), "synthetic incident handoff")

    def publish_authenticated_practical(
        self, *, claim: Path, suite: Path, events: Path
    ) -> tuple[Path, Path]:
        """Hermetic successor test helper for an adapter-authenticated transcript."""

        prereg = g6.verify_preregistration(self.preregistration)
        claim_doc = g6.verify_candidate_claim(
            claim, preregistration=self.preregistration
        )
        suite_doc = g6.verify_practical_suite(
            suite, preregistration=self.preregistration, claim=claim
        )
        rows, observations, safety = g6._read_practical_events(
            events, prereg=prereg, claim=claim_doc, suite=suite_doc
        )
        assessment = g6._sequential_practical(
            observations, attempt_index=claim_doc["attemptIndex"]
        )
        transcript = events.parent / "06-execution-transcript.json"
        _write(
            transcript,
            {
                "schemaVersion": 1,
                "kind": g6.PRACTICAL_ADAPTER_AUTHORITY["executionTranscriptKind"],
                "status": "fully-replayed-terminal-practical-execution",
                "createdUtc": g6._utc_now(),
                "preregistration": g6.identity(self.preregistration),
                "claim": g6.identity(claim),
                "suite": g6.identity(suite),
                "events": g6.identity(events),
                "attemptIndex": claim_doc["attemptIndex"],
                "pairs": len(rows),
                "safety": safety,
                "zeroSafetyFailures": not any(safety.values()),
                "sequentialAssessment": assessment,
                "incident": None,
                "exactSuitePrefix": True,
                "allRawTranscriptsRulesReplayed": True,
                "hiddenSeedReadOrPublished": False,
                "terminal": True,
            },
        )
        decision = Path(
            g6.publish_practical_decision_from_execution_transcript(
                preregistration=self.preregistration,
                claim=claim,
                suite=suite,
                events=events,
                execution_transcript=transcript,
            )["path"]
        )
        return decision, transcript.resolve()


class Generation6SuccessorTests(unittest.TestCase):
    def fixture(self, *, prior_v2: bool = False) -> Fixture:
        temporary = tempfile.TemporaryDirectory(prefix="g6-successor-test-")
        self.addCleanup(temporary.cleanup)
        return Fixture(
            Path(temporary.name),
            prior_v2=prior_v2,
            register_cleanup=self.addCleanup,
        )

    def test_protocol_and_exhaustion_diagnostic_are_exact(self) -> None:
        protocol = g6.validate_protocol()
        self.assertEqual(protocol["protocolId"], g6.PROTOCOL_ID)
        adapter_policy = protocol["formalHceHandoff"]["launchAdapter"]
        self.assertTrue(adapter_policy["formalGateOrderStrict"])
        self.assertTrue(
            adapter_policy["bridgeRequiresTerminalDecisionAndSafetyReplay"]
        )
        adapter_relative, adapter_bytes, adapter_sha256 = g6.PINNED_AUTHORITIES[
            "hceLaunchAdapter"
        ]
        self.assertEqual(
            adapter_policy["implementation"],
            {
                "relativePath": adapter_relative,
                "bytes": adapter_bytes,
                "sha256": adapter_sha256,
            },
        )
        with self.assertRaisesRegex(ValueError, "not a diagnostic successor"):
            g6._attempt_log_threshold(378)

    def test_end_to_end_nomination_claim_suite_practical_and_handoff(self) -> None:
        fixture = self.fixture()
        heldout = fixture.publish_heldout(nominate=True)
        claim, suite, entropy = fixture.claim_and_suite(heldout)
        events = fixture.events(suite, 128)
        decision, _ = fixture.publish_authenticated_practical(
            claim=claim, suite=suite, events=events
        )
        self.assertEqual(g6._load_json(decision, "decision")["decision"], "promote")
        handoff = Path(
            g6.publish_hce_handoff(
                preregistration=fixture.preregistration,
                claim=claim,
                suite=suite,
                practical_decision=decision,
            )["path"]
        )
        verified = g6.verify_hce_handoff(
            handoff,
            preregistration=fixture.preregistration,
            claim=claim,
            suite=suite,
            practical_decision=decision,
        )
        self.assertEqual([row["gate"] for row in verified["formalGates"]], list(g6.FORMAL_HCE_GATES))
        self.assertEqual(verified["engineOptions"]["common"], g6.FORMAL_HCE_COMMON_OPTIONS)
        public = claim.read_bytes() + suite.read_bytes() + handoff.read_bytes()
        self.assertNotIn(entropy.hex().encode("ascii"), public)
        for stage in ("practical-opening-selection", *g6.FORMAL_HCE_GATES):
            raw_seed = g6._stage_key(entropy, 1, stage).hex().encode("ascii")
            self.assertNotIn(raw_seed, public)

    def test_direct_bridge_rejects_off_path_and_evidence_free_confirmed_closures(self) -> None:
        for label, canonical in (("off-path", False), ("evidence-free", True)):
            with self.subTest(label=label):
                fixture = self.fixture()
                heldout = fixture.publish_heldout(nominate=True)
                claim, suite, _ = fixture.claim_and_suite(heldout)
                events = fixture.events(suite, 128)
                practical, _ = fixture.publish_authenticated_practical(
                    claim=claim, suite=suite, events=events
                )
                handoff = Path(
                    g6.publish_hce_handoff(
                        preregistration=fixture.preregistration,
                        claim=claim,
                        suite=suite,
                        practical_decision=practical,
                    )["path"]
                )
                canonical_path = (
                    fixture.artifacts
                    / "hce-frozen-v2-adapter/attempts/attempt-000001/attempt-closure.json"
                )
                closure = canonical_path if canonical else fixture.root / "forged-v2-closure.json"
                _write(
                    closure,
                    {
                        "schemaVersion": 1,
                        "kind": g6.V2_CLOSURE_KIND,
                        "protocol": g6.identity(
                            g6.REPO / g6.PINNED_AUTHORITIES["v2ProtocolJson"][0]
                        ),
                        "attemptIndex": 1,
                        "createdUtc": "2026-07-24T00:00:00.000001Z",
                        "implementationSeal": None,
                        "attemptReservation": None,
                        "candidateClaim": g6.identity(claim),
                        "jointSuiteSeal": None,
                        "authorization": None,
                        "decisions": {
                            "development": g6.identity(practical),
                            "equal-node": None,
                            "equal-time": None,
                            "normal-start-clock": None,
                        },
                        "outcome": "confirmed",
                        "reason": "all-three-formal-hce-gates-promoted",
                        "terminal": True,
                        "nextAttemptAllowed": False,
                        "candidateNetworkSha256": g6.identity(fixture.network)[
                            "sha256"
                        ],
                    },
                )
                with self.assertRaises((ImportError, ValueError)):
                    g6.bridge_hce_attempt_closure(
                        preregistration=fixture.preregistration,
                        claim=claim,
                        suite=suite,
                        practical_decision=practical,
                        hce_handoff=handoff,
                        hce_attempt_closure=closure,
                    )
                self.assertFalse(
                    g6._attempt_paths(fixture.artifacts, 1)["closure"].exists()
                )

    def test_heldout_equal_candidate_is_rejected_without_attempt(self) -> None:
        fixture = self.fixture()
        heldout = fixture.publish_heldout(nominate=False)
        value = g6.verify_heldout_decision(
            heldout, preregistration=fixture.preregistration
        )
        self.assertEqual(value["decision"], "reject")
        with self.assertRaisesRegex(ValueError, "rejection"):
            g6.claim_practical_attempt(
                preregistration=fixture.preregistration,
                heldout_decision=heldout,
            )
        self.assertFalse((fixture.artifacts / "attempts").exists())

    def test_preregistration_refuses_existing_heldout_access(self) -> None:
        fixture = self.fixture()
        # Rebuild only the preregistration in a fresh output root after planting
        # heldout evidence in the canonical G6 namespace.
        fixture.preregistration.unlink()
        expected = g6._expected_heldout_paths(fixture.namespace)
        _write(Path(expected["access"]), {"forged": True})
        with self.assertRaisesRegex(FileExistsError, "precede G6 heldout"):
            fixture._publish_preregistration()

    def test_preregistration_is_no_clobber(self) -> None:
        fixture = self.fixture()
        before = fixture.preregistration.read_bytes()
        with self.assertRaises(FileExistsError):
            fixture._publish_preregistration()
        self.assertEqual(fixture.preregistration.read_bytes(), before)

    def test_global_attempt_snapshot_rejects_rollback(self) -> None:
        fixture = self.fixture(prior_v2=True)
        closure = fixture.v2 / "attempt-000001/attempt-closure.json"
        value = g6._load_json(closure, "v2 closure")
        closure.unlink()
        value["reason"] = "rewritten"
        _write(closure, value)
        with self.assertRaisesRegex(ValueError, "rolled back or diverged"):
            g6.verify_preregistration(fixture.preregistration)

    def test_claim_reservation_blocks_entropy_reroll(self) -> None:
        fixture = self.fixture()
        heldout = fixture.publish_heldout()
        fixture.claim_and_suite(heldout)
        with self.assertRaisesRegex(ValueError, "already active"):
            g6.claim_practical_attempt(
                preregistration=fixture.preregistration,
                heldout_decision=heldout,
            )

    def test_safety_counter_forces_terminal_failure(self) -> None:
        fixture = self.fixture()
        heldout = fixture.publish_heldout()
        claim, suite, _ = fixture.claim_and_suite(heldout)
        events = fixture.events(suite, 8, safety=1)
        decision, _ = fixture.publish_authenticated_practical(
            claim=claim, suite=suite, events=events
        )
        value = g6._load_json(decision, "safety decision")
        self.assertEqual(value["decision"], "safety-fail")
        closure = Path(
            g6.close_practical_failure(
                preregistration=fixture.preregistration,
                claim=claim,
                suite=suite,
                practical_decision=decision,
            )["path"]
        )
        self.assertTrue(g6._verify_successor_closure(closure, 1)["nextAttemptAllowed"])

    def test_forged_startup_diagnostic_is_rejected(self) -> None:
        fixture = self.fixture()
        attestation = Path(
            g6._load_json(fixture.deployment, "bundle")["startupAttestation"]["path"]
        )
        value = g6._load_json(attestation, "attestation")
        attestation.unlink()
        value["probe"]["activeDiagnostic"] = "info string handcrafted active"
        _write(attestation, value)
        with self.assertRaisesRegex(ValueError, "identity mismatch|attestation"):
            g6.verify_deployment_bundle(fixture.deployment)

    def test_forged_per_game_network_diagnostic_is_rejected(self) -> None:
        fixture = self.fixture()
        heldout = fixture.publish_heldout()
        claim, suite, _ = fixture.claim_and_suite(heldout)
        events = fixture.events(suite, 8, bad_diagnostic=True)
        with self.assertRaisesRegex(ValueError, "asset/diagnostic"):
            fixture.publish_authenticated_practical(
                claim=claim, suite=suite, events=events
            )

    def test_protocol_mutation_and_duplicate_json_key_fail(self) -> None:
        fixture = self.fixture()
        copy = fixture.root / "protocol.json"
        value = g6.validate_protocol()
        value["alphaSpending"]["familywiseAlpha"] = 0.02
        _write(copy, value)
        with self.assertRaisesRegex(ValueError, "differs"):
            g6.validate_protocol(copy, allow_noncanonical=True)
        duplicate = fixture.root / "duplicate.json"
        duplicate.write_text('{"a":1,"a":2}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            g6._load_json(duplicate, "duplicate")

    def test_preregistration_path_never_reads_heldout_report(self) -> None:
        fixture = self.fixture()
        fixture.preregistration.unlink()
        forbidden = Path(g6._expected_heldout_paths(fixture.namespace)["report"])
        original = g6._load_json

        def guarded(path: Path, label: str):
            if Path(path).resolve() == forbidden.resolve():
                raise AssertionError("heldout report read during preregistration")
            return original(path, label)

        with mock.patch.object(g6, "_load_json", side_effect=guarded):
            fixture._publish_preregistration()

    def test_legacy_jsonl_and_public_probe_injection_are_absent(self) -> None:
        with self.assertRaisesRegex(ValueError, "arbitrary practical JSONL"):
            g6.publish_practical_decision(
                preregistration=Path("prereg.json"),
                claim=Path("claim.json"),
                suite=Path("suite.json"),
                events=Path("events.jsonl"),
            )
        self.assertNotIn("_probe", inspect.signature(g6.publish_deployment_bundle).parameters)
        with self.assertRaises(TypeError):
            g6.publish_deployment_bundle(_probe=lambda *_: {})

    def test_noncanonical_v2_and_g5_authorities_fail_before_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="g6-noncanonical-") as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "canonical frozen-v2 namespace"):
                g6._scan_v2_attempts(root / "attempts")
            fake = _write(root / "fake.json", {})
            with self.assertRaisesRegex(ValueError, "noncanonical"):
                g6._fresh_g5_incumbent_replay(fake, fake)

    def test_nonempty_canonical_v2_history_runs_full_frozen_verifier(self) -> None:
        fixture = self.fixture(prior_v2=True)
        with mock.patch.object(g6, "_run_v2_chain_verifier") as replay:
            rows = g6._scan_v2_attempts(fixture.v2)
        self.assertEqual(len(rows), 1)
        replay.assert_called_once_with(1)

    def test_identity_rejects_hardlinks_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="g6-link-identity-") as temporary:
            root = Path(temporary)
            source = _write(root / "source.bin", b"authority")
            os.link(source, root / "hardlink.bin")
            with self.assertRaisesRegex(ValueError, "unique regular non-link"):
                g6.identity(source)
            target = _write(root / "target.bin", b"target")
            link = root / "symlink.bin"
            try:
                os.symlink(target, link)
            except OSError:
                return
            with self.assertRaisesRegex(ValueError, "non-link"):
                g6.identity(link)

    def test_consumed_claim_and_suite_publication_failures_advance_k(self) -> None:
        for stage in ("claim-publication", "suite-publication"):
            with self.subTest(stage=stage):
                fixture = self.fixture()
                heldout = fixture.publish_heldout()
                if stage == "claim-publication":
                    with mock.patch.object(
                        g6.secrets, "token_bytes", side_effect=RuntimeError("draw failed")
                    ):
                        with self.assertRaisesRegex(RuntimeError, "draw failed"):
                            g6.claim_practical_attempt(
                                preregistration=fixture.preregistration,
                                heldout_decision=heldout,
                            )
                else:
                    with mock.patch.object(
                        g6.secrets,
                        "token_bytes",
                        return_value=b"S" * g6.ENTROPY_BYTES,
                    ):
                        g6.claim_practical_attempt(
                            preregistration=fixture.preregistration,
                            heldout_decision=heldout,
                        )
                closure = Path(
                    g6.terminalize_consumed_publication_failure(
                        preregistration=fixture.preregistration,
                        attempt_index=1,
                        failure_stage=stage,
                    )["path"]
                )
                value = g6._verify_successor_closure(closure, 1)
                self.assertEqual(value["terminalStage"], stage)
                self.assertIsNone(value["suite"])
                self.assertIsNone(value["practicalDecision"])
                self.assertEqual(
                    g6._global_attempt_state(
                        g6.verify_preregistration(fixture.preregistration)
                    )["nextAttemptIndex"],
                    2,
                )

    def test_authenticated_incident_publishes_safety_failure_and_closure(self) -> None:
        fixture = self.fixture()
        heldout = fixture.publish_heldout()
        claim, suite, _ = fixture.claim_and_suite(heldout)
        events = fixture.events(suite, 0)
        handoff = events.parent / "98-incident-terminal-handoff.json"
        safety = {name: int(name == "assetMismatches") for name in g6.SAFETY_COUNTERS}
        _write(
            handoff,
            {
                "createdUtc": g6._utc_now(),
                "eventsPrefix": g6.identity(events),
                "attemptIndex": 1,
                "completedPairs": 0,
                "safety": safety,
                "requestedSuccessorDecision": "safety-fail",
                "requestedGlobalOutcome": "failed",
                "nextAttemptAllowed": True,
                "successorEventFabricated": False,
                "hiddenSeedReadOrPublished": False,
                "terminal": True,
            },
        )
        result = g6.publish_practical_incident_failure(
            preregistration=fixture.preregistration,
            claim=claim,
            suite=suite,
            incident_handoff=handoff,
        )
        verified = g6.verify_practical_incident_failure(
            practical_decision=Path(result["practicalDecision"]["path"]),
            attempt_closure=Path(result["attemptClosure"]["path"]),
            preregistration=fixture.preregistration,
            claim=claim,
            suite=suite,
            incident_handoff=handoff,
        )
        self.assertEqual(
            verified["practicalDecision"]["executionEvidenceMode"],
            "authenticated-incident-terminal-handoff",
        )
        self.assertEqual(verified["attemptClosure"]["outcome"], "failed")

    def test_historical_scan_replays_practical_inventory(self) -> None:
        fixture = self.fixture()
        heldout = fixture.publish_heldout()
        claim, suite, _ = fixture.claim_and_suite(heldout)
        events = fixture.events(suite, 8, safety=1)
        decision, _ = fixture.publish_authenticated_practical(
            claim=claim, suite=suite, events=events
        )
        g6.close_practical_failure(
            preregistration=fixture.preregistration,
            claim=claim,
            suite=suite,
            practical_decision=decision,
        )
        _write(events.parent / "unexpected.bin", b"forbidden")
        with self.assertRaisesRegex(ValueError, "inventory"):
            g6._global_attempt_state(g6.verify_preregistration(fixture.preregistration))

    def test_heldout_access_must_strictly_follow_successor_preregistration(self) -> None:
        fixture = self.fixture()
        with self.assertRaisesRegex(ValueError, "heldout chronology"):
            fixture.publish_heldout(access_offset=0)

    def test_heldout_decision_invokes_trainer_owned_authority_replay(self) -> None:
        fixture = self.fixture()
        with mock.patch.object(g6, "_verify_trainer_heldout_authority") as replay:
            fixture.publish_heldout()
        self.assertGreaterEqual(replay.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
