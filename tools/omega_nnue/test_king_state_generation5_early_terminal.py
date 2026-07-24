#!/usr/bin/env python3
"""Focused branch, publication, and hostile tests for the G5 early authority."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().with_name(
    "king_state_generation5_early_terminal.py"
)
SPEC = importlib.util.spec_from_file_location("g5_early_terminal_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
early = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = early
SPEC.loader.exec_module(early)

COMPAT_PATH = MODULE_PATH.with_name(
    "king_state_match_readiness_generation5_compat_v2.py"
)
COMPAT_SPEC = importlib.util.spec_from_file_location("g5_compat_guard_tested", COMPAT_PATH)
assert COMPAT_SPEC is not None and COMPAT_SPEC.loader is not None
compat = importlib.util.module_from_spec(COMPAT_SPEC)
sys.modules[COMPAT_SPEC.name] = compat
COMPAT_SPEC.loader.exec_module(compat)


def _timestamp(seconds: int) -> str:
    value = datetime(
        2026, 7, 24, 15, microsecond=123456, tzinfo=timezone.utc
    ) + timedelta(
        seconds=seconds
    )
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(early._canonical_json(value))


class FakeTrainer(types.SimpleNamespace):
    def _verify_offline_claim(self, path: Path, **_kwargs: object) -> dict:
        return early._load_json(path, "fake offline access claim")


class EarlyFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        names = {
            "trainingRoot": "build-msvc/king-state-v5",
            "trainingPlan": "build-msvc/king-state-v5/training-plan.json",
            "validationSelection": "build-msvc/king-state-v5/validation-selection.seal.json",
            "robustnessClaim": "build-msvc/king-state-v5/.robustness.training.claim.json",
            "robustnessFailure": "build-msvc/king-state-v5/robustness.failure.json",
            "robustnessBundle": "build-msvc/king-state-v5/robustness.bundle",
            "robustnessSeal": "build-msvc/king-state-v5/robustness.seal.json",
            "offlineLifetimeClaim": "build-msvc/king-state-v5/offline/evaluation-lifetime.claim.json",
            "offlineAccessClaim": "build-msvc/king-state-v5/offline/access-claim.json",
            "offlineReport": "build-msvc/king-state-v5/offline/report.json",
            "authorityLifecycleLock": "build-msvc/king-state-v5-authority-lifecycle-v1/authority.lock",
            "terminalRoot": "build-msvc/king-state-v5-early-terminal-v1",
            "sourceSelection": "build-msvc/king-state-v5-early-terminal-v1/source-selection.seal.json",
            "sourceClosure": "build-msvc/king-state-v5-early-terminal-v1/source-closure.json",
            "compatibilityAuthorization": "build-king-state-v5/matches-color-compat-v2/sealed/match-authorization.json",
            "compatibilityClosure": "build-king-state-v5/matches-color-compat-v2/closure.json",
        }
        branches = [
            ("selection-no-eligible", "failed", ["validationSelection"], ["validation"]),
            (
                "robustness-training-failed",
                "failed",
                ["validationSelection", "robustnessFailure"],
                ["train", "validation"],
            ),
            (
                "robustness-worker-aborted",
                "aborted",
                ["validationSelection", "robustnessFailure"],
                ["validation"],
            ),
            (
                "robustness-gate-failed",
                "failed",
                ["validationSelection", "robustnessSeal"],
                ["validation"],
            ),
            (
                "heldout-gate-failed",
                "failed",
                [
                    "validationSelection",
                    "robustnessSeal",
                    "offlineLifetimeClaim",
                    "offlineAccessClaim",
                    "offlineReport",
                ],
                ["validation", "heldOut"],
            ),
            (
                "heldout-access-aborted",
                "aborted",
                [
                    "validationSelection",
                    "robustnessSeal",
                    "offlineLifetimeClaim",
                    "offlineAccessClaim",
                ],
                ["validation"],
            ),
        ]
        self.protocol = {
            "createdUtc": _timestamp(-10),
            "namespaces": names,
            "branches": [
                {
                    "terminalClass": name,
                    "promotionStatus": status,
                    "requiredEvidence": evidence,
                    "internalReplaySplits": splits,
                }
                for name, status, evidence, splits in branches
            ],
        }
        self.paths = {
            key: self.root / value for key, value in names.items()
        }
        self.paths["trainingRoot"].mkdir(parents=True)
        self.model = self.root / "G5B.nnue"
        self.model.write_bytes(b"model")
        self.selection = {
            "createdUtc": _timestamp(0),
            "winner": "G5B",
            "winnerNetwork": early._identity(self.model),
            "plan": {"path": str(self.paths["trainingPlan"]), "bytes": 1, "sha256": "1" * 64},
        }
        self.plan = {
            "selectionPath": str(self.paths["validationSelection"]),
            "outputDirectory": str(self.paths["trainingRoot"]),
            "outputs": {"robustnessSeal": str(self.paths["robustnessSeal"])},
        }
        self.profile = {}
        self.trainer = FakeTrainer()
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.object(early, "_TEST_REPOSITORY", root))
        self.stack.enter_context(mock.patch.object(early, "_TEST_PROTOCOL", self.protocol))
        self.stack.enter_context(mock.patch.object(early, "_TEST_TRAINER", self.trainer))

    def close(self) -> None:
        self.stack.close()

    def write_selection(self, *, no_eligible: bool = False) -> None:
        value = {
            "createdUtc": _timestamp(0),
            "status": (
                "closed-no-eligible-candidate"
                if no_eligible
                else "selected-validation-winner"
            ),
        }
        _write_json(self.paths["validationSelection"], value)

    def patch_selection(self, *, no_eligible: bool = False):
        selected = dict(self.selection)
        if no_eligible:
            selected = {"createdUtc": _timestamp(0), "winner": None}
        return mock.patch.object(
            early,
            "_selection_context",
            return_value=(selected, self.plan, self.profile, no_eligible),
        )

    def make_robustness(self, passed: bool) -> dict:
        self.paths["robustnessBundle"].mkdir(parents=True, exist_ok=True)
        value = {"createdUtc": _timestamp(1), "passed": passed}
        _write_json(self.paths["robustnessSeal"], value)
        return value

    def patch_robustness(self, value: dict):
        return mock.patch.object(
            early,
            "_verify_robustness_seal_any",
            return_value=(value, self.selection, self.plan, self.profile),
        )

    def make_offline(self, *, report: bool) -> None:
        _write_json(
            self.paths["offlineLifetimeClaim"], {"createdUtc": _timestamp(2)}
        )
        _write_json(
            self.paths["offlineAccessClaim"], {"createdUtc": _timestamp(3)}
        )
        if report:
            _write_json(self.paths["offlineReport"], {"createdUtc": _timestamp(4)})

    def patch_lifetime(self):
        return mock.patch.multiple(
            early,
            _verify_lifetime_claim=mock.Mock(
                side_effect=lambda path, *_args: early._load_json(path, "lifetime")
            ),
            _verify_lifetime_claim_value=mock.Mock(
                side_effect=lambda value, *_args: dict(value)
            ),
        )


class EarlyTerminalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="g5-early-test-")
        self.fixture = EarlyFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.fixture.close()
        self.temp.cleanup()

    def _publish(self, terminal_class: str) -> dict:
        f = self.fixture
        f.write_selection(no_eligible=terminal_class == "selection-no-eligible")
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            f.patch_selection(no_eligible=terminal_class == "selection-no-eligible")
        )
        operator = None
        reason = None
        if terminal_class.startswith("robustness-") and terminal_class not in {
            "robustness-gate-failed"
        }:
            _write_json(f.paths["robustnessFailure"], {"createdUtc": _timestamp(1)})
            stack.enter_context(
                mock.patch.object(
                    early,
                    "_verify_robustness_failure_any",
                    return_value=terminal_class,
                )
            )
        elif terminal_class == "robustness-gate-failed":
            robustness = f.make_robustness(False)
            stack.enter_context(f.patch_robustness(robustness))
        elif terminal_class in {"heldout-gate-failed", "heldout-access-aborted"}:
            robustness = f.make_robustness(True)
            f.make_offline(report=terminal_class == "heldout-gate-failed")
            stack.enter_context(f.patch_robustness(robustness))
            stack.enter_context(f.patch_lifetime())
            if terminal_class == "heldout-gate-failed":
                stack.enter_context(
                    mock.patch.object(
                        early,
                        "_replay_offline_report",
                        return_value=(
                            early._load_json(f.paths["offlineReport"], "offline report"),
                            robustness,
                            f.selection,
                            f.plan,
                            f.profile,
                        ),
                    )
                )
            else:
                operator = "test-operator"
                reason = "offline-evaluation-interrupted"
        result = early.publish_terminal(
            operator_id=operator, abort_reason=reason
        )
        self.assertEqual(result["promotionStatus"], (
            "aborted" if terminal_class in {"robustness-worker-aborted", "heldout-access-aborted"} else "failed"
        ))
        self.assertIsNone(result["rawModel"])
        self.assertFalse(result["healthPassed"])
        self.assertFalse(result["resultInformationRead"])
        selection = early._load_json(f.paths["sourceSelection"], "selection")
        closure = early._load_json(f.paths["sourceClosure"], "closure")
        self.assertEqual(selection["terminalClass"], terminal_class)
        self.assertEqual(closure["terminalClass"], terminal_class)
        payload = f.paths["sourceSelection"].read_text() + f.paths[
            "sourceClosure"
        ].read_text()
        for forbidden in ("metrics", "huberLoss", "predictions", "targetCp"):
            self.assertNotIn(forbidden, payload)
        stack.close()
        return result

    def test_all_six_terminal_branches_publish_and_replay(self) -> None:
        classes = [item["terminalClass"] for item in self.fixture.protocol["branches"]]
        for index, terminal_class in enumerate(classes):
            if index:
                self.tearDown()
                self.setUp()
            with self.subTest(terminal_class=terminal_class):
                self._publish(terminal_class)

    def test_publication_is_no_clobber_and_exact_inventory(self) -> None:
        self._publish("selection-no-eligible")
        with self.assertRaises(ValueError):
            early.publish_terminal()
        extra = self.fixture.paths["terminalRoot"] / "unexpected.json"
        extra.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            early.verify_terminal()

    def test_mixed_match_authority_and_partial_terminal_reject(self) -> None:
        f = self.fixture
        f.write_selection(no_eligible=True)
        with f.patch_selection(no_eligible=True):
            _write_json(f.paths["compatibilityAuthorization"], {"createdUtc": _timestamp(2)})
            with self.assertRaises(ValueError):
                early.classify_terminal()
        f.paths["compatibilityAuthorization"].unlink()
        f.paths["terminalRoot"].mkdir()
        _write_json(f.paths["sourceSelection"], {"partial": True})
        with self.assertRaises(ValueError):
            early.publish_terminal()

    def test_claim_only_abort_requires_operator_and_idle_lock(self) -> None:
        f = self.fixture
        f.write_selection()
        robustness = f.make_robustness(True)
        f.make_offline(report=False)
        with f.patch_selection(), f.patch_robustness(robustness), f.patch_lifetime():
            with self.assertRaises(ValueError):
                early.classify_terminal()
            with early._open_lifetime_lock(f.paths["offlineLifetimeClaim"]):
                with self.assertRaises(RuntimeError):
                    early.classify_terminal(
                        operator_id="operator",
                        abort_reason="offline-evaluation-interrupted",
                    )

    def test_abort_holds_lifetime_lock_through_publication_and_blocks_retry(self) -> None:
        f = self.fixture
        original = early._exclusive_json
        observed = {"selection": False}

        def guarded_write(path: Path, value: dict) -> None:
            if path == f.paths["sourceSelection"]:
                observed["selection"] = True
                with self.assertRaisesRegex(RuntimeError, "lifetime lock is active"):
                    early._open_lifetime_lock(f.paths["offlineLifetimeClaim"])
            original(path, value)

        with mock.patch.object(early, "_exclusive_json", side_effect=guarded_write):
            self._publish("heldout-access-aborted")
        self.assertTrue(observed["selection"])
        with self.assertRaisesRegex(ValueError, "blocks held-out evaluation"):
            early.run_offline_guarded()

    def test_guarded_offline_refuses_legacy_access_before_lifetime(self) -> None:
        f = self.fixture
        robustness = f.make_robustness(True)
        _write_json(f.paths["offlineAccessClaim"], {"createdUtc": _timestamp(3)})
        f.trainer._verify_robustness = mock.Mock(
            return_value=(robustness, f.selection, f.plan, f.profile)
        )
        f.trainer._offline_evaluate = mock.Mock()
        with self.assertRaisesRegex(ValueError, "predates the lifetime authority"):
            early.run_offline_guarded()
        self.assertFalse(os.path.lexists(f.paths["offlineLifetimeClaim"]))
        f.trainer._offline_evaluate.assert_not_called()

    def test_strict_heldout_chronology_rejects_retrodated_report(self) -> None:
        with self.assertRaisesRegex(ValueError, "access must strictly precede report"):
            early._verify_heldout_chronology(
                robustness={"createdUtc": _timestamp(1)},
                lifetime={"createdUtc": _timestamp(2)},
                access={"createdUtc": _timestamp(4)},
                report={"createdUtc": _timestamp(3)},
            )

    def test_heldout_chronology_requires_canonical_utc_round_trip(self) -> None:
        canonical = (
            "2026-07-24T15:00:00Z",
            "2026-07-24T15:00:00.123456Z",
        )
        for parser in (early._parse_chronology_utc, compat._parse_chronology_utc):
            for value in canonical:
                with self.subTest(parser=parser.__module__, valid=value):
                    self.assertEqual(parser(value, "synthetic UTC").tzinfo, timezone.utc)
            for value in (
                "2026-07-24T15:00:00+00:00",
                "2026-07-24T15:00:00.1Z",
                "2026-07-24T15:00:00.123Z",
                "2026-07-24T15:00:00.000000Z",
            ):
                with self.subTest(parser=parser.__module__, invalid=value):
                    with self.assertRaisesRegex(ValueError, "canonical UTC"):
                        parser(value, "synthetic UTC")
        with self.assertRaisesRegex(ValueError, "canonical UTC"):
            early._verify_heldout_chronology(
                robustness={"createdUtc": "2026-07-24T15:00:01+00:00"},
                lifetime={"createdUtc": _timestamp(2)},
                access={"createdUtc": _timestamp(3)},
                report={"createdUtc": _timestamp(4)},
            )

    def test_early_and_compatibility_tools_share_one_lifecycle_mutex(self) -> None:
        path = self.fixture.paths["authorityLifecycleLock"]
        with early._open_authority_lifecycle_lock(path):
            with self.assertRaisesRegex(RuntimeError, "lifecycle lock is active"):
                compat._open_authority_lifecycle_lock(path)

    def test_compatibility_requires_lifetime_claim_and_full_chronology(self) -> None:
        root = Path(self.temp.name) / "compat-lifecycle"
        early_protocol = root / "validation/early.json"
        lifecycle_lock = root / "build-msvc/lifecycle/authority.lock"
        terminal_root = root / "build-msvc/terminal"
        lifetime_path = root / "build-msvc/g5/offline/lifetime.json"
        access_path = root / "build-msvc/g5/offline/access.json"
        report_path = root / "build-msvc/g5/offline/report.json"
        robustness_path = root / "build-msvc/g5/robustness.json"
        names = {
            "authorityLifecycleLock": "build-msvc/lifecycle/authority.lock",
            "terminalRoot": "build-msvc/terminal",
            "offlineLifetimeClaim": "build-msvc/g5/offline/lifetime.json",
            "offlineAccessClaim": "build-msvc/g5/offline/access.json",
            "offlineReport": "build-msvc/g5/offline/report.json",
        }
        _write_json(
            early_protocol,
            {
                "schemaVersion": 1,
                "kind": compat.EARLY_PROTOCOL_KIND,
                "protocolId": compat.EARLY_PROTOCOL_ID,
                "profileId": compat.G5_PROFILE_ID,
                "namespaces": names,
            },
        )
        _write_json(robustness_path, {"createdUtc": _timestamp(1)})
        _write_json(access_path, {"createdUtc": _timestamp(3)})
        protocol_identity = compat._private_snapshot(
            early_protocol, "test early protocol"
        )[0]
        robustness_identity = compat._private_snapshot(
            robustness_path, "test robustness"
        )[0]
        access_identity = compat._private_snapshot(access_path, "test access")[0]
        _write_json(
            lifetime_path,
            {
                "schemaVersion": 1,
                "kind": compat.LIFETIME_CLAIM_KIND,
                "profileId": compat.G5_PROFILE_ID,
                "createdUtc": _timestamp(2),
                "protocol": protocol_identity,
                "robustness": robustness_identity,
                "status": "claimed-before-any-held-out-target-decode",
                "resumePolicy": "reuse this immutable claim and lifetime lock; never publish a second claim",
                "heldOutTargetRowsDecodedAtClaim": 0,
                "heldOutTargetFieldsDecodedAtClaim": 0,
                "resultInformationRead": False,
            },
        )
        policy = {
            "heldOutLifecycle": {
                "earlyTerminalProtocol": {
                    **protocol_identity,
                    "path": "validation/early.json",
                },
                "authorityLifecycleLock": names["authorityLifecycleLock"],
                "earlyTerminalRoot": names["terminalRoot"],
                "offlineLifetimeClaim": names["offlineLifetimeClaim"],
                "offlineAccessClaim": names["offlineAccessClaim"],
                "offlineReport": names["offlineReport"],
                "claimKind": compat.LIFETIME_CLAIM_KIND,
                "strictChronology": ["robustness", "lifetime", "access", "report"],
                "canonicalChronologyUtc": "python-datetime-isoformat-utc-z-round-trip",
                "guardedLauncher": "tools/omega_nnue/king_state_generation5_early_terminal.py run-offline",
                "directLegacyOfflineEvaluateCannotAuthorize": True,
            }
        }
        report = {
            "createdUtc": _timestamp(4),
            "robustness": robustness_identity,
            "accessClaim": access_identity,
        }
        patches = (
            mock.patch.object(compat, "REPO", root),
            mock.patch.object(compat, "EARLY_TERMINAL_PROTOCOL", early_protocol),
            mock.patch.object(compat, "AUTHORITY_LIFECYCLE_LOCK", lifecycle_lock),
            mock.patch.object(compat, "EARLY_TERMINAL_ROOT", terminal_root),
            mock.patch.object(compat, "OFFLINE_LIFETIME_CLAIM", lifetime_path),
            mock.patch.object(compat, "OFFLINE_ACCESS_CLAIM", access_path),
            mock.patch.object(compat, "OFFLINE_REPORT", report_path),
        )
        with ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            observed = compat._verify_heldout_lifetime_authority(policy, report)
            self.assertEqual(observed, compat._private_snapshot(lifetime_path, "claim")[0])
            noncanonical_report = dict(report)
            noncanonical_report["createdUtc"] = "2026-07-24T15:00:04+00:00"
            with self.assertRaisesRegex(ValueError, "canonical UTC"):
                compat._verify_heldout_lifetime_authority(
                    policy, noncanonical_report
                )
            lifetime_path.unlink()
            with self.assertRaises(FileNotFoundError):
                compat._verify_heldout_lifetime_authority(policy, report)
            _write_json(
                lifetime_path,
                {
                    "schemaVersion": 1,
                    "kind": compat.LIFETIME_CLAIM_KIND,
                    "profileId": compat.G5_PROFILE_ID,
                    "createdUtc": _timestamp(5),
                    "protocol": protocol_identity,
                    "robustness": robustness_identity,
                    "status": "claimed-before-any-held-out-target-decode",
                    "resumePolicy": "reuse this immutable claim and lifetime lock; never publish a second claim",
                    "heldOutTargetRowsDecodedAtClaim": 0,
                    "heldOutTargetFieldsDecodedAtClaim": 0,
                    "resultInformationRead": False,
                },
            )
            with self.assertRaisesRegex(ValueError, "robustness must strictly precede lifetime|lifetime must strictly precede access"):
                compat._verify_heldout_lifetime_authority(policy, report)

    def test_snapshot_rejects_parent_junction_or_symlink(self) -> None:
        root = Path(self.temp.name)
        target = root / "real-parent"
        target.mkdir()
        (target / "artifact.json").write_text("{}\n", encoding="utf-8")
        alias = root / "linked-parent"
        if os.name == "nt":
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(f"junctions unavailable: {created.stderr.strip()}")
        else:
            alias.symlink_to(target, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "reparse point"):
                early._snapshot(alias / "artifact.json", "junction artifact")
        finally:
            if os.path.lexists(alias):
                if os.name == "nt":
                    os.rmdir(alias)
                else:
                    alias.unlink()

    def test_forged_closure_and_hardlink_reject(self) -> None:
        self._publish("selection-no-eligible")
        f = self.fixture
        closure = early._load_json(f.paths["sourceClosure"], "closure")
        closure["retryPermitted"] = True
        f.paths["sourceClosure"].write_bytes(early._canonical_json(closure))
        with f.patch_selection(no_eligible=True):
            with self.assertRaises(ValueError):
                early.verify_terminal()
        f.paths["sourceClosure"].unlink()
        try:
            os.link(f.paths["sourceSelection"], f.paths["sourceClosure"])
        except OSError:
            self.skipTest("hardlinks unavailable")
        with f.patch_selection(no_eligible=True):
            with self.assertRaises(ValueError):
                early.verify_terminal()

    def test_color_compat_guard_rejects_any_early_root(self) -> None:
        root = Path(self.temp.name) / "guard-root"
        with mock.patch.object(compat, "EARLY_TERMINAL_ROOT", root):
            compat._reject_early_terminal_authority()
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "blocks compatibility"):
                compat._reject_early_terminal_authority()


if __name__ == "__main__":
    unittest.main(verbosity=2)
