#!/usr/bin/env python3
"""Hostile tests for the Generation-6 frozen-v2 HCE launch adapter."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import king_state_confirmation_generation6 as g6
import king_state_confirmation_generation6_hce_adapter as adapter
from test_king_state_confirmation_generation6 import Fixture, _write


def _publish_handoff(fixture: Fixture) -> tuple[Path, Path, Path, Path]:
    heldout = fixture.publish_heldout(nominate=True)
    claim, suite, _ = fixture.claim_and_suite(heldout)
    events = fixture.events(suite, 128)
    decision, _ = fixture.publish_authenticated_practical(
        claim=claim,
        suite=suite,
        events=events,
    )
    handoff = Path(
        g6.publish_hce_handoff(
            preregistration=fixture.preregistration,
            claim=claim,
            suite=suite,
            practical_decision=decision,
        )["path"]
    )
    return handoff, claim, suite, decision


class HceFixture(Fixture):
    """Use the successor's private hermetic-probe test publication core."""

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


class Generation6HceAdapterTests(unittest.TestCase):
    def fixture(self) -> Fixture:
        temporary = tempfile.TemporaryDirectory(prefix="g6-hce-adapter-test-")
        self.addCleanup(temporary.cleanup)
        fixture = HceFixture(
            Path(temporary.name), register_cleanup=self.addCleanup
        )
        # The HCE adapter descriptor-loads a second exact successor module.
        # Mirror the successor suite's hermetic dependencies in that copy while
        # leaving its bound verification/bridge functions and production source
        # untouched.  Successor replay still authenticates the transcript,
        # events, sequential assessment, safety, and every HCE envelope field.
        bound = adapter._bind_g6_from_preregistration(fixture.preregistration)
        patches = (
            mock.patch.object(bound, "DEFAULT_V2_ATTEMPT_ROOT", fixture.v2),
            mock.patch.object(bound, "_run_v2_chain_verifier", return_value=None),
            mock.patch.object(
                bound,
                "_fresh_g5_incumbent_replay",
                side_effect=fixture._fake_g5_replay,
            ),
            mock.patch.object(
                bound, "_verify_trainer_heldout_authority", return_value=None
            ),
            mock.patch.object(bound, "_practical_launch_adapter", return_value=fixture),
            mock.patch.object(bound, "_hce_launch_adapter", return_value=adapter),
            mock.patch.object(g6, "_hce_launch_adapter", return_value=adapter),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return fixture

    def handoff(self) -> tuple[Fixture, Path, Path, Path, Path]:
        fixture = self.fixture()
        handoff, claim, suite, decision = _publish_handoff(fixture)
        return fixture, handoff, claim, suite, decision

    def test_self_test_binds_real_frozen_launch_assess_and_clock_code(self) -> None:
        adapter.self_test()
        matches = adapter._matches()
        self.assertEqual(
            matches._launch.__module__,
            adapter.MATCHES_AUTHORITY_MODULE,
        )
        self.assertIsNot(matches._launch, adapter.launch)
        self.assertIsNot(matches._assess_command, adapter.assess)
        self.assertEqual(tuple(matches.GATES), adapter.ALL_V2_GATES)
        self.assertIs(matches._context, adapter._frozen_context)
        self.assertIs(matches._rehash_context, adapter._frozen_rehash_context)

    def test_handoff_authenticates_exact_v2_options_engine_network_and_alpha(self) -> None:
        fixture, handoff, _, _, _ = self.handoff()
        inputs = adapter._authenticate_handoff(handoff)
        value = inputs["handoffDocument"]
        self.assertEqual(value["candidateEngine"], g6.identity(fixture.engine))
        self.assertEqual(value["controlEngine"], g6.identity(fixture.engine))
        self.assertEqual(value["candidateNetwork"], g6.identity(fixture.network))
        self.assertIsNone(value["controlNetwork"])
        self.assertEqual(value["engineOptions"]["common"], g6.FORMAL_HCE_COMMON_OPTIONS)
        self.assertEqual(value["attemptIndex"], 1)
        self.assertEqual(
            value["promotionLogThreshold"], g6._attempt_log_threshold(1)
        )
        self.assertFalse(value["globalAttemptOrAlphaReset"])

    def test_forged_handoff_option_or_network_is_rejected(self) -> None:
        fixture, handoff, _, _, _ = self.handoff()
        for label, mutate in (
            (
                "options",
                lambda value: value["engineOptions"]["common"].update(
                    {"Hash": "64"}
                ),
            ),
            (
                "network",
                lambda value: value["candidateNetwork"].update(
                    {"sha256": "0" * 64}
                ),
            ),
            (
                "alpha",
                lambda value: value.update(
                    {"promotionLogThreshold": value["promotionLogThreshold"] - 1.0}
                ),
            ),
        ):
            value = g6._load_json(handoff, f"{label} source handoff")
            mutate(value)
            forged = _write(fixture.root / f"forged-{label}.json", value)
            with self.assertRaises(ValueError):
                adapter._authenticate_handoff(forged)

    def test_hmac_seed_derivation_is_committed_distinct_and_entropy_opaque(self) -> None:
        _, handoff, claim_path, _, _ = self.handoff()
        inputs = adapter._authenticate_handoff(handoff)
        seeds = adapter._stage_seeds(inputs)
        self.assertEqual(seeds, adapter._stage_seeds(inputs))
        self.assertEqual(set(seeds), set(adapter.ALL_V2_GATES))
        self.assertEqual(len(set(seeds.values())), len(adapter.ALL_V2_GATES))
        self.assertTrue(all(0 <= seed <= adapter.UINT31_MAX for seed in seeds.values()))
        claim = g6._load_json(claim_path, "test claim")
        entropy = Path(
            g6._attempt_paths(
                Path(inputs["preregistrationDocument"]["artifactRoot"]), 1
            )["entropy"]
        ).read_bytes()
        public = handoff.read_bytes() + claim_path.read_bytes()
        self.assertNotIn(entropy, public)
        for stage in ("practical-opening-selection", *adapter.FORMAL_GATES):
            self.assertNotIn(g6._stage_key(entropy, 1, stage), public)
        forged = dict(claim["stageSeedCommitments"])
        forged["equal-node"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "commitment"):
            adapter._derive_seeds_from_entropy(
                entropy, 1, forged, g6_module=g6
            )

    def test_selector_intent_and_child_command_are_candidate_and_result_blind(self) -> None:
        _, handoff, _, _, _ = self.handoff()
        inputs = adapter._authenticate_handoff(handoff)
        paths = adapter.attempt_paths(inputs["preregistrationDocument"], 1)
        seeds = adapter._stage_seeds(inputs)
        fake_capsule = {
            "path": str(Path(tempfile.gettempdir()) / "synthetic-selector-capsule.json"),
            "bytes": 1,
            "sha256": "a" * 64,
        }
        with mock.patch.object(
            adapter, "_selector_capsule_identity", return_value=fake_capsule
        ):
            intent = adapter._selector_intent_value(inputs, paths, seeds)
        serialized = adapter._canonical_json(intent).lower()
        for forbidden in (
            b"candidateengine",
            b"candidatenetwork",
            b"candidateclaim",
            b"practicaldecision",
            b"hcehandoff",
            b"events.jsonl",
            b"decision.json",
        ):
            self.assertNotIn(forbidden, serialized)
        captured: dict[str, object] = {}

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["command"] = command
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(adapter.subprocess, "run", side_effect=fake_run):
            adapter._run_selector_child(
                paths=paths, attempt_index=1, parent_lock_token="a" * 64
            )
        command = [str(item).casefold() for item in captured["command"]]
        self.assertFalse(
            any(
                token in {"--candidate", "--network", "--claim", "--handoff"}
                for token in command
            )
        )
        self.assertEqual(captured["kwargs"]["env"], {})
        self.assertIs(captured["kwargs"]["stdin"], subprocess.DEVNULL)

    def test_missing_static_selector_preflight_creates_no_adapter_output(self) -> None:
        fixture, handoff, _, _, _ = self.handoff()
        inputs = adapter._authenticate_handoff(handoff)
        roots = adapter._adapter_roots(inputs["preregistrationDocument"])
        self.assertFalse(roots["adapterRoot"].exists())
        with self.assertRaises((FileNotFoundError, ValueError)):
            adapter.prepare(handoff)
        self.assertFalse(roots["adapterRoot"].exists())
        # The already consumed G6 practical attempt remains intact and active;
        # static adapter preflight did not draw or reroll entropy.
        state = g6._global_attempt_state(inputs["preregistrationDocument"])
        self.assertEqual(state["activeAttempt"], 1)
        self.assertTrue(fixture.artifacts.exists())

    def test_development_launch_and_formal_gate_order_skips_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "three formal HCE gates"):
            adapter.launch(Path("unused"), gate="development")

        matches = adapter._matches()
        temporary = Path(tempfile.mkdtemp(prefix="g6-adapter-order-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary))
        context = SimpleNamespace(
            paths={
                "attemptClosure": temporary / "attempt-closure.json",
                "stages": {
                    gate: temporary / gate for gate in adapter.ALL_V2_GATES
                },
            }
        )
        authorization = temporary / "match-authorization.json"
        with (
            mock.patch.object(
                adapter,
                "_require_open_authorization",
                return_value={"attemptIndex": 1},
            ),
            mock.patch.object(matches, "_context", return_value=context),
        ):
            with self.assertRaisesRegex(FileNotFoundError, "equal-node decision"):
                adapter.launch(authorization, gate="equal-time")
            with self.assertRaisesRegex(FileNotFoundError, "equal-time decision"):
                adapter.launch(authorization, gate="normal-start-clock")

    def test_abort_closure_is_no_clobber_and_bridges_same_global_attempt(self) -> None:
        fixture, handoff, _, _, _ = self.handoff()
        inputs = adapter._authenticate_handoff(handoff)
        paths = adapter.attempt_paths(inputs["preregistrationDocument"], 1)
        adapter._exclusive_json(paths["reservation"], adapter._reservation_value(inputs))
        closure = Path(
            adapter._publish_abort_closure_from_inputs(
                inputs, paths, reason="operator-abort"
            )["path"]
        )
        verified = adapter.verify_attempt_closure(closure, handoff=handoff)
        self.assertEqual(verified["attemptIndex"], 1)
        self.assertEqual(verified["outcome"], "aborted")
        before = closure.read_bytes()
        with self.assertRaises(FileExistsError):
            adapter._publish_abort_closure_from_inputs(
                inputs, paths, reason="operator-abort"
            )
        self.assertEqual(closure.read_bytes(), before)
        successor_identity = adapter.bridge(closure, handoff=handoff)
        successor = Path(successor_identity["path"])
        successor_value = g6._verify_successor_closure(successor, 1)
        self.assertEqual(successor_value["outcome"], "aborted")
        self.assertEqual(successor_value["hceAttemptClosure"], g6.identity(closure))
        self.assertEqual(successor_value["candidateNetworkSha256"], g6.identity(fixture.network)["sha256"])

    def test_bridge_detects_closure_rollback_and_candidate_hash_forgery(self) -> None:
        fixture, handoff, _, _, _ = self.handoff()
        inputs = adapter._authenticate_handoff(handoff)
        paths = adapter.attempt_paths(inputs["preregistrationDocument"], 1)
        adapter._exclusive_json(paths["reservation"], adapter._reservation_value(inputs))
        closure = Path(
            adapter._publish_abort_closure_from_inputs(
                inputs, paths, reason="operator-abort"
            )["path"]
        )
        original = adapter._load_json(closure, "rollback source closure")
        closure.unlink()
        forged = copy.deepcopy(original)
        forged["candidateNetworkSha256"] = "0" * 64
        adapter._exclusive_json(closure, forged)
        with self.assertRaisesRegex(ValueError, "envelope"):
            adapter.bridge(closure, handoff=handoff)
        closure.unlink()
        adapter._exclusive_json(closure, original)
        adapter.bridge(closure, handoff=handoff)
        successor = g6._attempt_paths(fixture.artifacts, 1)["closure"]
        closure.unlink()
        with self.assertRaises((FileNotFoundError, ValueError)):
            g6._verify_successor_closure(successor, 1)

    def test_bridge_refuses_nonterminal_or_safety_inconsistent_formal_replay(self) -> None:
        cases = (
            (
                "nonterminal",
                {
                    gate: None for gate in adapter.FORMAL_GATES
                },
                None,
            ),
            (
                "safety-inconsistent",
                {
                    "equal-node": {
                        "decision": "promote",
                        "zeroSafetyFailures": True,
                    },
                    "equal-time": {
                        "decision": "safety-fail",
                        "zeroSafetyFailures": False,
                    },
                    "normal-start-clock": None,
                },
                "equal-time-safety-fail",
            ),
        )
        for label, replay, failure in cases:
            with self.subTest(label=label):
                fixture, handoff, _, _, _ = self.handoff()
                inputs = adapter._authenticate_handoff(handoff)
                paths = adapter.attempt_paths(inputs["preregistrationDocument"], 1)
                for key in (
                    "reservation",
                    "implementationSeal",
                    "jointSuiteSeal",
                    "authorization",
                ):
                    _write(paths[key], {"placeholder": key})
                if label == "safety-inconsistent":
                    for gate in ("equal-node", "equal-time"):
                        _write(
                            paths["stages"][gate] / "decision.json",
                            replay[gate],
                        )
                forged = adapter._closure_base(inputs, paths)
                forged.update(
                    {
                        "outcome": "confirmed",
                        "reason": "all-three-formal-hce-gates-promoted",
                        "nextAttemptAllowed": False,
                    }
                )
                adapter._exclusive_json(paths["attemptClosure"], forged)
                with (
                    mock.patch.object(
                        adapter,
                        "_terminal_decisions",
                        return_value=(replay, failure),
                    ) as terminal_replay,
                    self.assertRaisesRegex(ValueError, "frozen decision replay"),
                ):
                    adapter.bridge(paths["attemptClosure"], handoff=handoff)
                terminal_replay.assert_called_once()
                successor = g6._attempt_paths(fixture.artifacts, 1)["closure"]
                self.assertFalse(successor.exists())

    def test_identity_rejects_hard_linked_files(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="g6-hce-hardlink-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = _write(root / "source.json", {"source": True})
        alias = root / "alias.json"
        os.link(source, alias)
        with self.assertRaisesRegex(ValueError, "plain unlinked file"):
            adapter.identity(alias)
        with self.assertRaisesRegex(ValueError, "plain unlinked file"):
            adapter.identity(source)

    def test_identity_rejects_symbolic_links_when_supported(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="g6-hce-symlink-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = _write(root / "source.json", {"source": True})
        alias = root / "alias.json"
        try:
            os.symlink(source, alias, target_is_directory=False)
        except (NotImplementedError, OSError):
            real_lstat = os.lstat

            def symbolic_lstat(path: object, *args: object, **kwargs: object) -> object:
                if adapter._plain_path(Path(path)) == adapter._plain_path(alias):
                    return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
                return real_lstat(path, *args, **kwargs)

            with (
                mock.patch.object(adapter.os, "lstat", side_effect=symbolic_lstat),
                self.assertRaisesRegex(ValueError, "plain unlinked file"),
            ):
                adapter.identity(alias)
            return
        with self.assertRaisesRegex(ValueError, "plain unlinked file"):
            adapter.identity(alias)

    def test_identity_detects_pathname_substitution_after_hash(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="g6-hce-substitution-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = _write(root / "target.json", {"value": "target"})
        replacement = _write(root / "replacement.json", {"value": "other"})
        real_lstat = os.lstat
        target_calls = 0

        def substituted_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
            nonlocal target_calls
            if adapter._plain_path(Path(path)) == adapter._plain_path(target):
                target_calls += 1
                if target_calls > 1:
                    return real_lstat(replacement, *args, **kwargs)
            return real_lstat(path, *args, **kwargs)

        with (
            mock.patch.object(adapter, "_is_link_or_junction", return_value=False),
            mock.patch.object(adapter.os, "lstat", side_effect=substituted_lstat),
            self.assertRaisesRegex(ValueError, "substituted while hashed"),
        ):
            adapter.identity(target)
        self.assertGreaterEqual(target_calls, 2)

    def test_post_bridge_historical_replay_is_explicit_and_non_mutating(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="g6-hce-history-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        artifacts = root / "successor"
        preregistration = _write(root / "preregistration.json", {"name": "prereg"})
        claim = _write(root / "claim.json", {"name": "claim"})
        transcript = _write(root / "practical-transcript.json", {"name": "transcript"})
        practical = _write(root / "practical.json", {"name": "practical"})
        handoff = _write(root / "handoff.json", {"name": "handoff"})
        suite = _write(root / "suite.json", {"name": "suite"})
        global_reservation = _write(
            artifacts / "attempts/attempt-000001/00-reservation.json",
            {"name": "global reservation"},
        )
        inputs = {
            "preregistration": preregistration,
            "claim": claim,
            "suite": suite,
            "practicalDecision": practical,
            "handoff": handoff,
            "preregistrationDocument": {"artifactRoot": str(artifacts)},
            "claimDocument": {"reservation": adapter.identity(global_reservation)},
            "practicalDecisionDocument": {
                "executionTranscript": adapter.identity(transcript),
                "incidentHandoff": None,
            },
            "handoffDocument": {
                "attemptIndex": 1,
                "candidateNetwork": {"sha256": "a" * 64},
            },
        }
        paths = adapter.attempt_paths(inputs["preregistrationDocument"], 1)
        for key in (
            "reservation",
            "implementationSeal",
            "jointSuiteSeal",
            "authorization",
        ):
            _write(paths[key], {"name": key})
        replay = {}
        for gate in adapter.FORMAL_GATES:
            decision = {"decision": "promote", "zeroSafetyFailures": True}
            _write(paths["stages"][gate] / "decision.json", decision)
            replay[gate] = decision
        closure = adapter._closure_base(inputs, paths)
        closure.update(
            {
                "createdUtc": "2026-07-24T00:00:00.000000Z",
                "outcome": "confirmed",
                "reason": "all-three-formal-hce-gates-promoted",
                "nextAttemptAllowed": False,
            }
        )
        adapter._exclusive_json(paths["attemptClosure"], closure)

        successor_path = artifacts / "attempts/attempt-000001/06-attempt-closure.json"
        successor = {
            "schemaVersion": adapter.SCHEMA_VERSION,
            "kind": adapter.SUCCESSOR_CLOSURE_KIND,
            "status": "terminal-global-attempt-closure",
            "createdUtc": "2026-07-24T00:00:01.000000Z",
            "protocol": adapter.identity(adapter.REPO / adapter.G6_PROTOCOL_RELATIVE),
            "preregistration": adapter.identity(preregistration),
            "attemptIndex": 1,
            "terminalStage": "hce",
            "reservation": adapter.identity(global_reservation),
            "claim": adapter.identity(claim),
            "suite": adapter.identity(suite),
            "practicalDecision": adapter.identity(practical),
            "practicalExecutionTranscript": adapter.identity(transcript),
            "practicalIncidentHandoff": None,
            "hceHandoff": adapter.identity(handoff),
            "hceAttemptClosure": adapter.identity(paths["attemptClosure"]),
            "outcome": "confirmed",
            "reason": "hce-all-three-formal-hce-gates-promoted",
            "terminal": True,
            "nextAttemptAllowed": False,
            "candidateNetworkSha256": "a" * 64,
        }
        adapter._exclusive_json(successor_path, successor)

        active_state_calls = 0

        def closed_state(_: object) -> dict[str, object]:
            nonlocal active_state_calls
            active_state_calls += 1
            return {"activeAttempt": None, "closed": {1: {}}}

        fake_g6 = SimpleNamespace(
            PROTOCOL_PATH=adapter.REPO / adapter.G6_PROTOCOL_RELATIVE,
            _attempt_paths=lambda base, index: {
                "closure": Path(base).resolve()
                / "attempts"
                / f"attempt-{index:06d}"
                / "06-attempt-closure.json"
            },
            _global_attempt_state=closed_state,
        )

        def replay_terminal(_: Path) -> tuple[dict[str, object], None]:
            self.assertGreater(adapter._TERMINAL_REPLAY_DEPTH.get(), 0)
            adapter._require_active_global_attempt(inputs, 1)
            return replay, None

        with (
            mock.patch.object(adapter, "_authenticate_handoff", return_value=inputs),
            mock.patch.object(adapter, "_g6", return_value=fake_g6),
            mock.patch.object(adapter, "_terminal_decisions", side_effect=replay_terminal),
        ):
            with self.assertRaisesRegex(ValueError, "active global attempt"):
                adapter.verify_attempt_closure(paths["attemptClosure"], handoff=handoff)
            before = {
                "adapter": paths["attemptClosure"].read_bytes(),
                "successor": successor_path.read_bytes(),
            }
            verified = adapter.verify_historical_attempt_closure(
                paths["attemptClosure"], handoff=handoff
            )
            self.assertEqual(verified["outcome"], "confirmed")
            self.assertEqual(paths["attemptClosure"].read_bytes(), before["adapter"])
            self.assertEqual(successor_path.read_bytes(), before["successor"])
            self.assertEqual(adapter._HISTORICAL_REPLAY_DEPTH.get(), 0)
            with self.assertRaisesRegex(ValueError, "active global attempt"):
                adapter._require_active_global_attempt(inputs, 1)
            for label, mutate in (
                (
                    "HCE closure identity",
                    lambda value: value["hceAttemptClosure"].update(
                        {"sha256": "0" * 64}
                    ),
                ),
                (
                    "terminal stage",
                    lambda value: value.update({"terminalStage": "practical"}),
                ),
                (
                    "practical transcript",
                    lambda value: value["practicalExecutionTranscript"].update(
                        {"sha256": "0" * 64}
                    ),
                ),
                (
                    "incident handoff",
                    lambda value: value.update(
                        {"practicalIncidentHandoff": adapter.identity(handoff)}
                    ),
                ),
            ):
                with self.subTest(label=label):
                    forged = copy.deepcopy(successor)
                    mutate(forged)
                    successor_path.unlink()
                    adapter._exclusive_json(successor_path, forged)
                    with self.assertRaisesRegex(
                        ValueError, "historical successor bridge"
                    ):
                        adapter.verify_historical_attempt_closure(
                            paths["attemptClosure"], handoff=handoff
                        )
        self.assertEqual(active_state_calls, 2)

    def test_public_authority_shape_preserves_exact_formal_conjunction(self) -> None:
        _, handoff, _, _, _ = self.handoff()
        inputs = adapter._authenticate_handoff(handoff)
        paths = adapter.attempt_paths(inputs["preregistrationDocument"], 1)
        # Authority construction is tested with immutable placeholders; deep
        # suite/config/runtime replay belongs to prepare/verify_authorization.
        for key in ("reservation", "implementationSeal", "jointSuiteSeal", "coreSeal"):
            _write(paths[key], {"placeholder": key})
        for gate in adapter.ALL_V2_GATES:
            _write(paths["configs"][gate], {"placeholder": f"config-{gate}"})
            _write(paths["suites"][gate], {"placeholder": f"suite-{gate}"})
        seeds = adapter._stage_seeds(inputs)
        fake_capsule = {
            "path": str(Path(tempfile.gettempdir()) / "synthetic-selector-capsule.json"),
            "bytes": 1,
            "sha256": "a" * 64,
        }
        with mock.patch.object(
            adapter, "_selector_capsule_identity", return_value=fake_capsule
        ):
            value = adapter._authorization_value(
                inputs, paths, seeds, created_utc="2026-07-24T00:00:00Z"
            )
        self.assertEqual(value["stageOrder"], list(adapter.FORMAL_GATES))
        self.assertEqual(
            [item["gate"] for item in value["formalGates"]],
            list(adapter.FORMAL_GATES),
        )
        self.assertEqual(value["alphaSpending"]["attemptIndex"], 1)
        self.assertFalse(value["alphaSpending"]["globalAttemptOrAlphaReset"])
        self.assertEqual(value["engine"], inputs["handoffDocument"]["candidateEngine"])
        self.assertEqual(value["selectedNetwork"], inputs["handoffDocument"]["candidateNetwork"])
        self.assertEqual(value["matchResultsRead"], 0)
        self.assertFalse(value["thresholdsChangedAfterResults"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
